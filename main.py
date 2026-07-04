"""
main.py — Sports Predictions Engine
PythonAnywhere deployment: one daily task at 07:00 UTC
Checks the day and runs the appropriate jobs automatically.

Schedule logic:
  Tuesday  → UCL predictions + UCL acca
  Thursday → UEL/Conf predictions + UEL/Conf acca + Weekend scan
  Friday   → Weekend top-up (new fixtures only)
  Saturday → Saturday predictions
  Sunday   → Sunday predictions
  Daily    → Results updater (runs every day at 21:00 UTC via second task)
"""

import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from database import init_db, get_conn
from fetcher import fetch_fixtures_for_date, enrich_fixture
from telegram import send_error_alert
from updater import update_results
from accumulator import send_weekend_accas, send_ucl_accas, send_uel_conf_accas


# ─── Day Router ───────────────────────────────────────────────────────────────

def run_daily():
    today    = date.today()
    day_name = today.strftime("%A")

    print(f"\n{'='*50}")
    print(f"🗓  DAILY JOB — {today} ({day_name})")
    print(f"{'='*50}")

    try:
        if day_name == "Tuesday":    _tuesday_job(today)
        elif day_name == "Wednesday": _wednesday_job(today)
        elif day_name == "Thursday":  _thursday_job(today)
        elif day_name == "Friday":    _friday_job(today)
        elif day_name == "Saturday":  _saturday_job(today)
        elif day_name == "Sunday":    _sunday_job(today)
        elif day_name == "Monday":    _monday_job(today)
        else: print("📭 No jobs today")
    except Exception as e:
        print(f"❌ Daily job failed: {e}")
        send_error_alert(str(e))


def run_results():
    today = date.today()
    print(f"\n{'='*50}")
    print(f"📊 RESULTS JOB — {today}")
    print(f"{'='*50}")
    try:
        update_results(today)
    except Exception as e:
        print(f"❌ Results job failed: {e}")
        send_error_alert(str(e))


# ─── Day Jobs ─────────────────────────────────────────────────────────────────

def _tuesday_job(today: date):
    wednesday = today + timedelta(days=1)
    print("📌 UCL day — Tue+Wed fixtures")
    _run_predictions(today)
    _run_predictions(wednesday)
    send_ucl_accas()


def _wednesday_job(today: date):
    print("📌 Wednesday — predictions only")
    _run_predictions(today)


def _thursday_job(today: date):
    saturday = today + timedelta(days=2)
    sunday   = today + timedelta(days=3)
    print("📌 UEL/Conf + Weekend scan")
    _run_predictions(today)
    send_uel_conf_accas()
    _run_predictions(saturday)
    _run_predictions(sunday)
    send_weekend_accas()


def _friday_job(today: date):
    saturday = today + timedelta(days=1)
    sunday   = today + timedelta(days=2)
    print("📌 Friday top-up")
    _run_predictions(saturday)
    _run_predictions(sunday)
    send_weekend_accas(top_up=True)


def _saturday_job(today: date):
    print("📌 Saturday predictions")
    _run_predictions(today)


def _sunday_job(today: date):
    print("📌 Sunday predictions")
    _run_predictions(today)


def _monday_job(today: date):
    tuesday   = today + timedelta(days=1)
    wednesday = today + timedelta(days=2)
    print("📌 Monday — pre-fetch UCL fixtures")
    _run_predictions(tuesday)
    _run_predictions(wednesday)


# ─── Prediction Runner ────────────────────────────────────────────────────────

def _run_predictions(target_date: date):
    print(f"\n  🔍 Predictions for {target_date}...")
    fixtures = fetch_fixtures_for_date(target_date)

    if not fixtures:
        print(f"  📭 No fixtures on {target_date}")
        return

    from models import predict_fixture

    count = 0
    for fixture in fixtures:
        try:
            enriched = enrich_fixture(fixture)
            pred     = predict_fixture(enriched)
            if pred:
                _store_prediction(pred)
                count += 1
        except Exception as e:
            print(f"  ❌ {fixture.get('home_team','?')} vs "
                  f"{fixture.get('away_team','?')}: {e}")

    print(f"  ✅ {count}/{len(fixtures)} predictions stored")


# ─── DB Store ─────────────────────────────────────────────────────────────────

def _store_prediction(pred: dict):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO predictions (
                fixture_id, match_date, home_team, away_team,
                league, league_id, season,
                home_corners_avg, away_corners_avg,
                home_goals_scored_avg, home_goals_conceded_avg,
                away_goals_scored_avg, away_goals_conceded_avg,
                home_gg_rate, away_gg_rate,
                home_over25_rate, away_over25_rate,
                home_form_pts, away_form_pts,
                h2h_avg_goals, h2h_gg_rate, h2h_avg_corners, h2h_over25_rate,
                corners_score, corners_pick, corners_line,
                gg_score, gg_pick,
                goals_score, goals_pick, goals_line
            ) VALUES (
                :fixture_id, :match_date, :home_team, :away_team,
                :league, :league_id, :season,
                :home_corners_avg, :away_corners_avg,
                :home_goals_scored_avg, :home_goals_conceded_avg,
                :away_goals_scored_avg, :away_goals_conceded_avg,
                :home_gg_rate, :away_gg_rate,
                :home_over25_rate, :away_over25_rate,
                :home_form_pts, :away_form_pts,
                :h2h_avg_goals, :h2h_gg_rate, :h2h_avg_corners, :h2h_over25_rate,
                :corners_score, :corners_pick, :corners_line,
                :gg_score, :gg_pick,
                :goals_score, :goals_pick, :goals_line
            )
        """, pred)
        conn.commit()
    except Exception as e:
        print(f"  ⚠️  DB store error: {e}")
    finally:
        conn.close()


# ─── Status ───────────────────────────────────────────────────────────────────

def show_status():
    conn           = get_conn()
    total_preds    = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    updated_preds  = conn.execute("SELECT COUNT(*) FROM predictions WHERE result_updated=1").fetchone()[0]
    total_fixtures = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    team_stats     = conn.execute("SELECT COUNT(*) FROM team_stats").fetchone()[0]
    h2h_records    = conn.execute("SELECT COUNT(*) FROM h2h").fetchone()[0]
    conn.close()
    print(f"""
📊 Database Status
──────────────────
  Fixtures cached   : {total_fixtures}
  Team stats cached : {team_stats}
  H2H records       : {h2h_records}
  Predictions total : {total_preds}
  Results updated   : {updated_preds}
  Pending results   : {total_preds - updated_preds}
    """)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def print_help():
    print("""
Sports Predictions Engine — PythonAnywhere Edition
───────────────────────────────────────────────────
PythonAnywhere tasks (set these up once):
  Task 1 (07:00 UTC daily) : python main.py daily
  Task 2 (21:00 UTC daily) : python main.py results

Manual CLI:
  python main.py daily                Run today's job
  python main.py results              Update today's results
  python main.py predict 2025-05-10   Run predictions for a date
  python main.py acca weekend         Send weekend acca now
  python main.py acca topup           Send Friday top-up
  python main.py acca ucl             Send UCL acca now
  python main.py acca uel             Send UEL/Conf acca now
  python main.py scrape               Scrape FBRef corners (run weekly)
  python main.py status               Show DB stats
    """)


if __name__ == "__main__":
    init_db()
    args = sys.argv[1:]

    if not args:
        print_help()

    elif args[0] == "daily":
        run_daily()

    elif args[0] == "results":
        target = date.fromisoformat(args[1]) if len(args) > 1 else date.today()
        update_results(target)

    elif args[0] == "predict":
        target = date.fromisoformat(args[1]) if len(args) > 1 else date.today()
        _run_predictions(target)

    elif args[0] == "acca":
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "weekend":  send_weekend_accas()
        elif cmd == "topup":  send_weekend_accas(top_up=True)
        elif cmd == "ucl":    send_ucl_accas()
        elif cmd == "uel":    send_uel_conf_accas()
        else: print_help()

    elif args[0] == "scrape":
        from fbref_scraper import scrape_all_leagues
        scrape_all_leagues()

    elif args[0] == "status":
        show_status()

    else:
        print_help()
