"""
updater.py — Result updater
Runs after matches finish, fills actuals into DB, calculates accuracy
"""

import os
from datetime import date, timedelta
from database import get_conn
from fetcher import fetch_results_for_date
from telegram import send_daily_summary, send_error_alert
from dotenv import load_dotenv

load_dotenv()


def update_results(target_date: date = None):
    """
    Fetch match results and update predictions table with actuals.
    Run this after 10PM WAT on match days.
    """
    if target_date is None:
        target_date = date.today()

    print(f"\n🔄 Updating results for {target_date}...")

    # Fetch results from API
    results = fetch_results_for_date(target_date)

    if not results:
        print("⚠️  No results returned from API")
        return

    conn = get_conn()
    updated = 0

    for result in results:
        fixture_id = result["fixture_id"]

        # Check if we have a prediction for this fixture
        pred = conn.execute("""
            SELECT * FROM predictions
            WHERE fixture_id = ? AND result_updated = 0
        """, (fixture_id,)).fetchone()

        if not pred:
            continue  # No prediction stored — skip

        pred = dict(pred)

        home_goals  = result["actual_home_goals"]
        away_goals  = result["actual_away_goals"]
        total_goals = result["actual_total_goals"]
        actual_gg   = result["actual_gg"]

        # Evaluate each pick
        corners_correct = _eval_corners(pred, result)
        gg_correct      = _eval_gg(pred, actual_gg)
        goals_correct   = _eval_goals(pred, total_goals)

        conn.execute("""
            UPDATE predictions SET
                actual_home_goals = ?,
                actual_away_goals = ?,
                actual_total_goals = ?,
                actual_gg = ?,
                corners_correct = ?,
                gg_correct = ?,
                goals_correct = ?,
                result_updated = 1,
                updated_at = datetime('now')
            WHERE fixture_id = ?
        """, (
            home_goals, away_goals, total_goals, actual_gg,
            corners_correct, gg_correct, goals_correct,
            fixture_id
        ))
        updated += 1
        print(f"  ✅ Updated: {pred['home_team']} vs {pred['away_team']} | "
              f"Corners: {_yn(corners_correct)} | GG: {_yn(gg_correct)} | Goals: {_yn(goals_correct)}")

    conn.commit()
    conn.close()

    print(f"\n✅ Results updated: {updated} prediction(s)")

    # Send daily summary to Telegram
    if updated > 0:
        stats = get_daily_accuracy(target_date)
        send_daily_summary(stats)


# ─── Evaluators ───────────────────────────────────────────────────────────────

def _eval_corners(pred: dict, result: dict) -> int | None:
    """
    Corners correct = 1/0/None
    None means no corners pick was made (didn't qualify).
    Actual corners aren't available via free API — will be None until manually entered.
    """
    if not pred.get("corners_pick"):
        return None
    if result.get("actual_corners") is None:
        return None  # Can't evaluate without corner data

    actual = result["actual_corners"]
    line   = pred.get("corners_line", 9.5)
    pick   = pred.get("corners_pick", "")

    if "OVER" in pick.upper():
        return 1 if actual > line else 0
    elif "UNDER" in pick.upper():
        return 1 if actual < line else 0
    return None


def _eval_gg(pred: dict, actual_gg: int) -> int | None:
    if not pred.get("gg_pick"):
        return None
    pick = pred["gg_pick"].upper()
    if pick == "YES":
        return 1 if actual_gg == 1 else 0
    elif pick == "NO":
        return 1 if actual_gg == 0 else 0
    return None


def _eval_goals(pred: dict, total_goals: int) -> int | None:
    if not pred.get("goals_pick"):
        return None
    pick = pred["goals_pick"].upper()
    line = pred.get("goals_line", 2.5)

    if "OVER" in pick:
        return 1 if total_goals > line else 0
    elif "UNDER" in pick:
        return 1 if total_goals < line else 0
    return None


# ─── Manual Corners Entry ─────────────────────────────────────────────────────

def update_corners_manually(fixture_id: int, actual_corners: int):
    """
    Since free API doesn't provide corner stats,
    manually enter actual corners after checking results.
    """
    conn = get_conn()
    pred = conn.execute("""
        SELECT * FROM predictions WHERE fixture_id = ?
    """, (fixture_id,)).fetchone()

    if not pred:
        print(f"❌ No prediction found for fixture {fixture_id}")
        conn.close()
        return

    pred = dict(pred)
    corners_correct = None

    if pred.get("corners_pick"):
        line = pred.get("corners_line", 9.5)
        pick = pred["corners_pick"].upper()
        if "OVER" in pick:
            corners_correct = 1 if actual_corners > line else 0
        elif "UNDER" in pick:
            corners_correct = 1 if actual_corners < line else 0

    conn.execute("""
        UPDATE predictions SET
            actual_corners = ?,
            corners_correct = ?,
            updated_at = datetime('now')
        WHERE fixture_id = ?
    """, (actual_corners, corners_correct, fixture_id))
    conn.commit()
    conn.close()

    result = "✅ CORRECT" if corners_correct == 1 else "❌ WRONG" if corners_correct == 0 else "⚠️ N/A"
    print(f"Updated corners for fixture {fixture_id}: {actual_corners} corners — {result}")


# ─── Accuracy Stats ───────────────────────────────────────────────────────────

def get_daily_accuracy(target_date: date = None) -> dict:
    if target_date is None:
        target_date = date.today()

    conn = get_conn()
    rows = conn.execute("""
        SELECT corners_pick, gg_pick, goals_pick,
               corners_correct, gg_correct, goals_correct
        FROM predictions
        WHERE match_date = ? AND result_updated = 1
    """, (target_date.isoformat(),)).fetchall()
    conn.close()

    stats = {
        "corners_correct": 0, "corners_total": 0,
        "gg_correct":      0, "gg_total":      0,
        "goals_correct":   0, "goals_total":   0,
        "total_correct":   0, "total_picks":   0,
    }

    for r in rows:
        r = dict(r)
        if r["corners_pick"] and r["corners_correct"] is not None:
            stats["corners_total"] += 1
            stats["total_picks"]   += 1
            if r["corners_correct"] == 1:
                stats["corners_correct"] += 1
                stats["total_correct"]   += 1

        if r["gg_pick"] and r["gg_correct"] is not None:
            stats["gg_total"]    += 1
            stats["total_picks"] += 1
            if r["gg_correct"] == 1:
                stats["gg_correct"]    += 1
                stats["total_correct"] += 1

        if r["goals_pick"] and r["goals_correct"] is not None:
            stats["goals_total"] += 1
            stats["total_picks"] += 1
            if r["goals_correct"] == 1:
                stats["goals_correct"] += 1
                stats["total_correct"] += 1

    return stats


def get_overall_accuracy() -> dict:
    """Overall accuracy across all stored predictions."""
    conn = get_conn()
    row = conn.execute("""
        SELECT
            COUNT(CASE WHEN corners_pick IS NOT NULL AND corners_correct IS NOT NULL THEN 1 END) AS corners_total,
            SUM(CASE WHEN corners_correct = 1 THEN 1 ELSE 0 END)                                AS corners_correct,
            COUNT(CASE WHEN gg_pick IS NOT NULL AND gg_correct IS NOT NULL THEN 1 END)           AS gg_total,
            SUM(CASE WHEN gg_correct = 1 THEN 1 ELSE 0 END)                                     AS gg_correct,
            COUNT(CASE WHEN goals_pick IS NOT NULL AND goals_correct IS NOT NULL THEN 1 END)     AS goals_total,
            SUM(CASE WHEN goals_correct = 1 THEN 1 ELSE 0 END)                                  AS goals_correct
        FROM predictions WHERE result_updated = 1
    """).fetchone()
    conn.close()
    return dict(row) if row else {}


def export_ml_dataset(output_path: str = "ml_dataset.csv"):
    """Export all logged predictions as a CSV for ML training."""
    import csv
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM predictions WHERE result_updated = 1
    """).fetchall()
    conn.close()

    if not rows:
        print("⚠️  No completed predictions to export yet.")
        return

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])

    print(f"✅ ML dataset exported: {output_path} ({len(rows)} rows)")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "update":
            # python updater.py update
            update_results()

        elif cmd == "corners":
            # python updater.py corners <fixture_id> <actual_corners>
            fixture_id     = int(sys.argv[2])
            actual_corners = int(sys.argv[3])
            update_corners_manually(fixture_id, actual_corners)

        elif cmd == "accuracy":
            # python updater.py accuracy
            stats = get_overall_accuracy()
            print("\n📊 Overall Accuracy:")
            for k, v in stats.items():
                print(f"  {k}: {v}")

        elif cmd == "export":
            # python updater.py export
            export_ml_dataset()

    else:
        print("Usage:")
        print("  python updater.py update              — fetch & update today's results")
        print("  python updater.py corners <id> <n>   — manually enter corner count")
        print("  python updater.py accuracy            — print overall accuracy")
        print("  python updater.py export              — export ML dataset CSV")
