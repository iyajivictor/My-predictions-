"""
preloader.py — Off-season cache builder
Pre-loads 2025/2026 season data before the 2026/2027 season starts.

Fetches and caches:
  - Team stats for all teams in tracked leagues
  - Last 10 fixtures per team (for corners calculation)
  - Corners stats from those fixtures
  - H2H history for known fixture pairs

Run once daily during July at 100 requests/day.
Tracks progress so it resumes where it left off.

Usage:
  python preloader.py run       — run today's batch (100 req limit)
  python preloader.py status    — show cache progress
  python preloader.py teams     — just fetch team lists
"""

import os
import sys
import httpx
from datetime import date
from database import get_conn, init_db
from corners_stats import ensure_tables, fetch_fixture_corners
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("API_SPORTS_KEY", "")
API_BASE = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY}
SEASON   = 2025

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

# Daily request budget — leave 10 as buffer
REQUEST_BUDGET = 90


# ─── Request Counter ──────────────────────────────────────────────────────────

_requests_used = 0

def _get(endpoint: str, params: dict) -> dict | None:
    global _requests_used

    if _requests_used >= REQUEST_BUDGET:
        print(f"  ⚠️  Daily budget reached ({REQUEST_BUDGET}). Stopping.")
        return None

    url = f"{API_BASE}/{endpoint}"
    try:
        r = httpx.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        _requests_used += 1

        remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
        limit     = r.headers.get("x-ratelimit-requests-limit", "?")
        print(f"  📡 [{_requests_used}] {endpoint} | remaining: {remaining}/{limit}")

        return r.json()
    except Exception as e:
        print(f"  ❌ [{endpoint}]: {e}")
        return None


# ─── Progress Tracking ────────────────────────────────────────────────────────

def ensure_progress_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preload_progress (
            key         TEXT PRIMARY KEY,
            status      TEXT DEFAULT 'pending',
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def _is_done(key: str) -> bool:
    conn  = get_conn()
    row   = conn.execute("""
        SELECT status FROM preload_progress WHERE key = ?
    """, (key,)).fetchone()
    conn.close()
    return row and row[0] == "done"


def _mark_done(key: str):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO preload_progress (key, status, updated_at)
        VALUES (?, 'done', datetime('now'))
    """, (key,))
    conn.commit()
    conn.close()


# ─── Step 1: Fetch All Teams Per League ──────────────────────────────────────

def fetch_league_teams(league_id: int) -> list[dict]:
    """
    Get all team IDs for a league using /standings endpoint.
    More reliable than /teams on the free plan.
    Falls back to /fixtures if standings not available (UCL/Europa/Conference).
    """
    key = f"teams_{league_id}"
    if _is_done(key):
        # Load from DB
        conn  = get_conn()
        rows  = conn.execute("""
            SELECT DISTINCT team_id, team_name FROM team_stats
            WHERE league_id = ? AND season = ?
        """, (league_id, SEASON)).fetchall()
        conn.close()
        if rows:
            print(f"  ✅ {LEAGUES.get(league_id)}: {len(rows)} teams (cached)")
            return [{"team_id": r[0], "team_name": r[1]} for r in rows]

    print(f"\n  🏆 Fetching teams: {LEAGUES.get(league_id)}")

    # Try standings first (works for domestic leagues)
    teams = _teams_from_standings(league_id)

    # Fall back to fixtures for European competitions
    if not teams:
        teams = _teams_from_fixtures(league_id)

    if teams:
        _mark_done(key)
        print(f"  ✅ {len(teams)} teams found")
    else:
        print(f"  ⚠️  No teams found for league {league_id}")

    return teams


def _teams_from_standings(league_id: int) -> list[dict]:
    """Extract teams from league standings table."""
    data = _get("standings", {"league": league_id, "season": SEASON})

    if not data or "response" not in data or not data["response"]:
        return []

    teams = []
    try:
        standings = data["response"][0]["league"]["standings"]
        for group in standings:
            for entry in group:
                teams.append({
                    "team_id":   entry["team"]["id"],
                    "team_name": entry["team"]["name"],
                })
    except (KeyError, IndexError, TypeError):
        return []

    return teams


def _teams_from_fixtures(league_id: int) -> list[dict]:
    """Extract unique teams from last season completed fixtures."""
    data = _get("fixtures", {
        "league": league_id,
        "season": SEASON,
        "status": "FT",
        "last":   20,
    })

    if not data or "response" not in data:
        return []

    seen  = set()
    teams = []
    for f in data["response"]:
        for side in ("home", "away"):
            tid  = f["teams"][side]["id"]
            name = f["teams"][side]["name"]
            if tid not in seen:
                seen.add(tid)
                teams.append({"team_id": tid, "team_name": name})

    return teams


# ─── Step 2: Fetch Team Stats ─────────────────────────────────────────────────

def preload_team_stats(team_id: int, league_id: int):
    """Fetch and cache team stats for 2025/2026 season."""
    key = f"stats_{team_id}_{league_id}"
    if _is_done(key):
        return

    if _requests_used >= REQUEST_BUDGET:
        return

    data = _get("teams/statistics", {
        "team":   team_id,
        "league": league_id,
        "season": SEASON,
    })

    if not data or "response" not in data:
        return

    r      = data["response"]
    goals  = r.get("goals", {})
    games  = r.get("fixtures", {})
    form   = r.get("form", "")

    played_total = games.get("played", {}).get("total", 0) or 0
    if played_total == 0:
        return

    scored_home    = goals.get("for", {}).get("average", {}).get("home", 0) or 0
    scored_away    = goals.get("for", {}).get("average", {}).get("away", 0) or 0
    scored_total   = goals.get("for", {}).get("average", {}).get("total", 0) or 0
    conceded_home  = goals.get("against", {}).get("average", {}).get("home", 0) or 0
    conceded_away  = goals.get("against", {}).get("average", {}).get("away", 0) or 0
    conceded_total = goals.get("against", {}).get("average", {}).get("total", 0) or 0

    btts_for     = goals.get("for", {}).get("total", {}).get("total", 0) or 0
    btts_against = goals.get("against", {}).get("total", {}).get("total", 0) or 0
    gg_rate      = round(min(btts_for, btts_against) / played_total, 3) if played_total else 0.0

    avg_total   = float(scored_total or 0) + float(conceded_total or 0)
    over25_rate = round(avg_total / 2.5, 3)

    cs       = r.get("clean_sheet", {})
    cs_total = cs.get("total", 0) or 0
    cs_rate  = round(cs_total / played_total, 3) if played_total else 0.0

    form_pts = sum(3 if c == "W" else 1 if c == "D" else 0
                   for c in form[-5:].upper())

    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO team_stats
        (team_id, league_id, season, team_name, games_played,
         home_played, away_played,
         goals_scored_avg, goals_conceded_avg,
         home_goals_scored_avg, home_goals_conceded_avg,
         away_goals_scored_avg, away_goals_conceded_avg,
         gg_rate, over25_rate, clean_sheet_rate,
         corners_avg, form, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, datetime('now'))
    """, (
        team_id, league_id, SEASON,
        r.get("team", {}).get("name", ""),
        played_total,
        games.get("played", {}).get("home", 0) or 0,
        games.get("played", {}).get("away", 0) or 0,
        float(scored_total or 0),   float(conceded_total or 0),
        float(scored_home or 0),    float(conceded_home or 0),
        float(scored_away or 0),    float(conceded_away or 0),
        gg_rate, over25_rate, cs_rate,
        form[-10:] if form else "",
    ))
    conn.commit()
    conn.close()

    _mark_done(key)


# ─── Step 3: Fetch Team Fixture Corners ───────────────────────────────────────

def preload_team_corners(team_id: int, league_id: int):
    """
    Fetch last 10 completed fixtures for a team,
    then pull corners stats for each.
    """
    key = f"corners_{team_id}_{league_id}"
    if _is_done(key):
        return

    if _requests_used >= REQUEST_BUDGET:
        return

    # Fetch last 10 completed fixtures
    data = _get("fixtures", {
        "team":   team_id,
        "league": league_id,
        "season": SEASON,
        "last":   10,
        "status": "FT",
    })

    if not data or "response" not in data:
        _mark_done(key)
        return

    fixtures = data["response"]

    for match in fixtures:
        if _requests_used >= REQUEST_BUDGET:
            return

        fid     = match["fixture"]["id"]
        home_id = match["teams"]["home"]["id"]
        away_id = match["teams"]["away"]["id"]

        # Skip if already cached
        conn = get_conn()
        exists = conn.execute("""
            SELECT 1 FROM fixture_corners WHERE fixture_id = ?
        """, (fid,)).fetchone()
        conn.close()

        if exists:
            continue

        fetch_fixture_corners(fid, home_id, away_id)

    _mark_done(key)


# ─── Main Run ─────────────────────────────────────────────────────────────────

def run():
    """
    Daily pre-loader run. Processes as many teams as possible
    within the 90 request budget.
    """
    init_db()
    ensure_tables()
    ensure_progress_table()

    print(f"\n{'='*50}")
    print(f"🔄 PRE-LOADER — {date.today()}")
    print(f"   Season: {SEASON}/{SEASON+1} (historical cache)")
    print(f"   Budget: {REQUEST_BUDGET} requests")
    print(f"{'='*50}\n")

    for league_id, league_name in LEAGUES.items():
        if _requests_used >= REQUEST_BUDGET:
            print(f"\n⚠️  Budget reached — stopping at {league_name}")
            break

        print(f"\n🏆 {league_name}")

        # Get teams in this league
        teams = fetch_league_teams(league_id)
        if not teams:
            continue

        for team in teams:
            if _requests_used >= REQUEST_BUDGET:
                break

            team_id   = team["team_id"]
            team_name = team["team_name"]

            stats_done   = _is_done(f"stats_{team_id}_{league_id}")
            corners_done = _is_done(f"corners_{team_id}_{league_id}")

            if stats_done and corners_done:
                continue

            print(f"\n  👕 {team_name}")

            if not stats_done:
                preload_team_stats(team_id, league_id)

            if not corners_done and _requests_used < REQUEST_BUDGET:
                preload_team_corners(team_id, league_id)

    print(f"\n{'='*50}")
    print(f"✅ Done — {_requests_used} requests used today")
    show_status()


# ─── Status ───────────────────────────────────────────────────────────────────

def show_status():
    conn = get_conn()

    total_teams    = conn.execute("""
        SELECT COUNT(DISTINCT team_id) FROM team_stats WHERE season = ?
    """, (SEASON,)).fetchone()[0]

    total_corners  = conn.execute("""
        SELECT COUNT(*) FROM fixture_corners
    """).fetchone()[0]

    done_stats     = conn.execute("""
        SELECT COUNT(*) FROM preload_progress
        WHERE key LIKE 'stats_%' AND status = 'done'
    """).fetchone()[0]

    done_corners   = conn.execute("""
        SELECT COUNT(*) FROM preload_progress
        WHERE key LIKE 'corners_%' AND status = 'done'
    """).fetchone()[0]

    conn.close()

    print(f"""
📊 Pre-load Status
──────────────────
  Teams cached      : {total_teams}
  Team stats done   : {done_stats}
  Corners done      : {done_corners}
  Fixture corners   : {total_corners} matches cached
    """)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "run":
        run()
    elif args[0] == "status":
        init_db()
        ensure_tables()
        ensure_progress_table()
        show_status()
    elif args[0] == "teams":
        init_db()
        ensure_progress_table()
        for league_id in LEAGUES:
            fetch_league_teams(league_id)
    else:
        print("Usage:")
        print("  python preloader.py run      — run today's batch")
        print("  python preloader.py status   — show progress")
        print("  python preloader.py teams    — fetch team lists only")
