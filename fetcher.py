"""
fetcher.py — api-sports.io data fetcher
Handles: fixtures, team stats, H2H, results
Caches aggressively to stay within 100 req/day free plan
"""

import httpx
import os
from datetime import datetime, date
from database import get_conn
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("API_SPORTS_KEY", "")
API_BASE = "https://v3.football.api-sports.io"
SEASON   = 2026

LEAGUES = {
    39:  "Premier League",
    140: "La Liga",
    78:  "Bundesliga",
    135: "Serie A",
    61:  "Ligue 1",
    88:  "Eredivisie",
    2:   "UEFA Champions League",
    3:   "UEFA Europa League",
    848: "UEFA Conference League",
}

HEADERS = {"x-apisports-key": API_KEY}


# ─── Core Request ─────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> dict | None:
    url = f"{API_BASE}/{endpoint}"
    try:
        r = httpx.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
        limit     = r.headers.get("x-ratelimit-requests-limit", "?")
        print(f"  📡 {endpoint} | remaining: {remaining}/{limit}")

        _log_usage(remaining, limit)
        return data

    except Exception as e:
        print(f"  ❌ API error [{endpoint}]: {e}")
        return None


def _log_usage(remaining, limit):
    try:
        conn = get_conn()
        used = int(limit) - int(remaining) if str(remaining).isdigit() and str(limit).isdigit() else 0
        conn.execute("""
            INSERT INTO api_usage (date, requests_used, requests_limit)
            VALUES (?, ?, ?)
        """, (date.today().isoformat(), used, limit))
        conn.commit()
        conn.close()
    except:
        pass


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def fetch_fixtures_for_date(target_date: date = None) -> list[dict]:
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")
    print(f"\n📅 Fetching fixtures for {date_str}...")

    all_fixtures = []

    for league_id, league_name in LEAGUES.items():
        data = _get("fixtures", {
            "league": league_id,
            "season": SEASON,
            "date":   date_str,
        })

        if not data or "response" not in data:
            continue

        fixtures = data["response"]
        print(f"  ⚽ {league_name}: {len(fixtures)} fixture(s)")

        for f in fixtures:
            fixture = _parse_fixture(f, league_id, league_name)
            _store_fixture(fixture)
            all_fixtures.append(fixture)

    print(f"\n✅ Total: {len(all_fixtures)} fixture(s)")
    return all_fixtures


def _parse_fixture(f: dict, league_id: int, league_name: str) -> dict:
    return {
        "fixture_id":   f["fixture"]["id"],
        "match_date":   f["fixture"]["date"][:10],
        "match_time":   f["fixture"]["date"][11:16],
        "home_team":    f["teams"]["home"]["name"],
        "away_team":    f["teams"]["away"]["name"],
        "home_team_id": f["teams"]["home"]["id"],
        "away_team_id": f["teams"]["away"]["id"],
        "league":       league_name,
        "league_id":    league_id,
        "season":       SEASON,
        "status":       f["fixture"]["status"]["short"],
    }


def _store_fixture(fixture: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO fixtures
        (fixture_id, match_date, match_time, home_team, away_team,
         home_team_id, away_team_id, league, league_id, season, status)
        VALUES (:fixture_id, :match_date, :match_time, :home_team, :away_team,
                :home_team_id, :away_team_id, :league, :league_id, :season, :status)
    """, fixture)
    conn.commit()
    conn.close()


def get_fixtures_from_db(target_date: date = None) -> list[dict]:
    if target_date is None:
        target_date = date.today()
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM fixtures WHERE match_date = ?
    """, (target_date.isoformat(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Team Stats ───────────────────────────────────────────────────────────────

def fetch_team_stats(team_id: int, league_id: int, season: int = SEASON) -> dict | None:
    cached = _get_cached_team_stats(team_id, league_id, season)
    if cached:
        return cached

    print(f"  🔄 Fetching stats: team {team_id} | league {league_id}")
    data = _get("teams/statistics", {
        "team":   team_id,
        "league": league_id,
        "season": season,
    })

    if not data or "response" not in data:
        return None

    stats = _parse_team_stats(data["response"], team_id, league_id, season)
    _store_team_stats(stats)
    return stats


def _get_cached_team_stats(team_id: int, league_id: int, season: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM team_stats
        WHERE team_id = ? AND league_id = ? AND season = ?
        AND updated_at > datetime('now', '-7 days')
    """, (team_id, league_id, season)).fetchone()
    conn.close()
    return dict(row) if row else None


def _parse_team_stats(r: dict, team_id: int, league_id: int, season: int) -> dict:
    goals  = r.get("goals", {})
    games  = r.get("fixtures", {})
    form   = r.get("form", "")

    played_home  = games.get("played", {}).get("home", 0) or 0
    played_away  = games.get("played", {}).get("away", 0) or 0
    played_total = games.get("played", {}).get("total", 0) or 0

    scored_home  = goals.get("for", {}).get("average", {}).get("home", 0) or 0
    scored_away  = goals.get("for", {}).get("average", {}).get("away", 0) or 0
    scored_total = goals.get("for", {}).get("average", {}).get("total", 0) or 0

    conceded_home  = goals.get("against", {}).get("average", {}).get("home", 0) or 0
    conceded_away  = goals.get("against", {}).get("average", {}).get("away", 0) or 0
    conceded_total = goals.get("against", {}).get("average", {}).get("total", 0) or 0

    btts_for     = goals.get("for", {}).get("total", {}).get("total", 0) or 0
    btts_against = goals.get("against", {}).get("total", {}).get("total", 0) or 0
    gg_rate = round(min(btts_for, btts_against) / played_total, 3) if played_total else 0.0

    avg_total   = float(scored_total or 0) + float(conceded_total or 0)
    over25_rate = round(avg_total / 2.5, 3)

    cs      = r.get("clean_sheet", {})
    cs_total = cs.get("total", 0) or 0
    cs_rate  = round(cs_total / played_total, 3) if played_total else 0.0

    form_pts = _calc_form_pts(form[-5:] if len(form) >= 5 else form)

    return {
        "team_id":                 team_id,
        "league_id":               league_id,
        "season":                  season,
        "team_name":               r.get("team", {}).get("name", ""),
        "games_played":            played_total,
        "home_played":             played_home,
        "away_played":             played_away,
        "goals_scored_avg":        float(scored_total or 0),
        "goals_conceded_avg":      float(conceded_total or 0),
        "home_goals_scored_avg":   float(scored_home or 0),
        "home_goals_conceded_avg": float(conceded_home or 0),
        "away_goals_scored_avg":   float(scored_away or 0),
        "away_goals_conceded_avg": float(conceded_away or 0),
        "gg_rate":                 gg_rate,
        "over25_rate":             over25_rate,
        "clean_sheet_rate":        cs_rate,
        "corners_avg":             0.0,
        "form":                    form[-10:] if form else "",
        "form_pts":                form_pts,
    }


def _calc_form_pts(form_str: str) -> int:
    pts = 0
    for ch in form_str.upper():
        if ch == "W":   pts += 3
        elif ch == "D": pts += 1
    return pts


def _store_team_stats(stats: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO team_stats
        (team_id, league_id, season, team_name, games_played,
         home_played, away_played, goals_scored_avg, goals_conceded_avg,
         home_goals_scored_avg, home_goals_conceded_avg,
         away_goals_scored_avg, away_goals_conceded_avg,
         gg_rate, over25_rate, clean_sheet_rate, corners_avg, form, updated_at)
        VALUES
        (:team_id, :league_id, :season, :team_name, :games_played,
         :home_played, :away_played, :goals_scored_avg, :goals_conceded_avg,
         :home_goals_scored_avg, :home_goals_conceded_avg,
         :away_goals_scored_avg, :away_goals_conceded_avg,
         :gg_rate, :over25_rate, :clean_sheet_rate, :corners_avg, :form, datetime('now'))
    """, stats)
    conn.commit()
    conn.close()


# ─── H2H ──────────────────────────────────────────────────────────────────────

def fetch_h2h(home_team_id: int, away_team_id: int) -> dict | None:
    cached = _get_cached_h2h(home_team_id, away_team_id)
    if cached:
        return cached

    print(f"  🔄 Fetching H2H: {home_team_id} vs {away_team_id}")
    data = _get("fixtures/headtohead", {
        "h2h":  f"{home_team_id}-{away_team_id}",
        "last": 10,
    })

    if not data or "response" not in data:
        return None

    h2h = _parse_h2h(data["response"], home_team_id, away_team_id)
    _store_h2h(h2h)
    return h2h


def _get_cached_h2h(home_team_id: int, away_team_id: int) -> dict | None:
    conn = get_conn()
    row  = conn.execute("""
        SELECT * FROM h2h WHERE home_team_id = ? AND away_team_id = ?
    """, (home_team_id, away_team_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def _parse_h2h(matches: list, home_team_id: int, away_team_id: int) -> dict:
    if not matches:
        return {"home_team_id": home_team_id, "away_team_id": away_team_id,
                "avg_goals": 0.0, "gg_rate": 0.0, "avg_corners": 0.0,
                "over25_rate": 0.0, "matches_used": 0}

    total_goals = gg_count = over25_count = count = 0

    for m in matches:
        status = m.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("FT", "AET", "PEN"):
            continue

        hg    = m["goals"]["home"] or 0
        ag    = m["goals"]["away"] or 0
        total = hg + ag

        total_goals += total
        if hg > 0 and ag > 0: gg_count    += 1
        if total > 2:          over25_count += 1
        count += 1

    if count == 0:
        return {"home_team_id": home_team_id, "away_team_id": away_team_id,
                "avg_goals": 0.0, "gg_rate": 0.0, "avg_corners": 0.0,
                "over25_rate": 0.0, "matches_used": 0}

    return {
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "avg_goals":    round(total_goals / count, 2),
        "gg_rate":      round(gg_count / count, 3),
        "avg_corners":  0.0,
        "over25_rate":  round(over25_count / count, 3),
        "matches_used": count,
    }


def _store_h2h(h2h: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO h2h
        (home_team_id, away_team_id, avg_goals, gg_rate, avg_corners,
         over25_rate, matches_used, updated_at)
        VALUES
        (:home_team_id, :away_team_id, :avg_goals, :gg_rate, :avg_corners,
         :over25_rate, :matches_used, datetime('now'))
    """, h2h)
    conn.commit()
    conn.close()


# ─── Results ──────────────────────────────────────────────────────────────────

def fetch_results_for_date(target_date: date = None) -> list[dict]:
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")
    print(f"\n📊 Fetching results for {date_str}...")

    results = []

    for league_id in LEAGUES:
        data = _get("fixtures", {
            "league": league_id,
            "season": SEASON,
            "date":   date_str,
            "status": "FT",
        })

        if not data or "response" not in data:
            continue

        for f in data["response"]:
            hg = f["goals"]["home"] or 0
            ag = f["goals"]["away"] or 0
            results.append({
                "fixture_id":        f["fixture"]["id"],
                "actual_home_goals": hg,
                "actual_away_goals": ag,
                "actual_total_goals": hg + ag,
                "actual_gg":         1 if hg > 0 and ag > 0 else 0,
                "status":            f["fixture"]["status"]["short"],
            })

    print(f"✅ Results: {len(results)} finished matches")
    return results


# ─── Enrich ───────────────────────────────────────────────────────────────────

def enrich_fixture(fixture: dict) -> dict:
    home_stats = fetch_team_stats(fixture["home_team_id"], fixture["league_id"])
    away_stats = fetch_team_stats(fixture["away_team_id"], fixture["league_id"])
    h2h        = fetch_h2h(fixture["home_team_id"], fixture["away_team_id"])

    return {**fixture, "home_stats": home_stats,
            "away_stats": away_stats, "h2h": h2h}


# ─── API Status ───────────────────────────────────────────────────────────────

def check_api_status() -> dict | None:
    data = _get("status", {})
    if data and "response" in data:
        s = data["response"]
        req = s.get("requests", {})
        print(f"\n📊 API — {req.get('current','?')}/{req.get('limit_day','?')} requests used today")
        return s
    return None


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from database import init_db
    init_db()
    check_api_status()
    fixtures = fetch_fixtures_for_date()
    if fixtures:
        print(f"\n🔍 Enriching first fixture...")
        enriched = enrich_fixture(fixtures[0])
        print(f"   Home stats : {enriched['home_stats'] is not None}")
        print(f"   Away stats : {enriched['away_stats'] is not None}")
        print(f"   H2H        : {enriched['h2h'] is not None}")
