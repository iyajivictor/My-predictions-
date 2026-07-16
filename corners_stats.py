"""
corners_stats.py — Corners data from api-sports /fixtures/statistics
Extracts real corners data from H2H match history.
Caches permanently to SQLite — only fetches once per fixture.

Cost: ~5-6 requests per fixture pair (1 H2H + 5 stats calls)
Strategy: fetch last 5 H2H matches, cache stats forever
"""

import os
import httpx
from database import get_conn
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("API_SPORTS_KEY", "")
API_BASE = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY}

SEASON_LIVE       = 2026  # Current season (2026/2027)
SEASON_HISTORICAL = 2025  # Last season (2025/2026) — for historical cache
MAX_H2H_MATCHES   = 5  # Keep requests lean — 5 matches gives solid average


# ─── DB Setup ─────────────────────────────────────────────────────────────────

def ensure_tables():
    conn = get_conn()

    # Cache individual fixture corners
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fixture_corners (
            fixture_id          INTEGER PRIMARY KEY,
            home_team_id        INTEGER,
            away_team_id        INTEGER,
            home_corners        INTEGER,
            away_corners        INTEGER,
            total_corners       INTEGER,
            fetched_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    # Cache computed team corners averages
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_corners_avg (
            team_id             INTEGER,
            venue               TEXT,       -- 'home' or 'away'
            league_id           INTEGER,
            corners_avg         REAL,
            corners_conceded_avg REAL,
            matches_used        INTEGER,
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (team_id, venue, league_id)
        )
    """)

    conn.commit()
    conn.close()


# ─── Core API Call ────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> dict | None:
    url = f"{API_BASE}/{endpoint}"
    try:
        r = httpx.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
        limit     = r.headers.get("x-ratelimit-requests-limit", "?")
        print(f"  📡 {endpoint} | remaining: {remaining}/{limit}")
        return r.json()
    except Exception as e:
        print(f"  ❌ API error [{endpoint}]: {e}")
        return None


# ─── Fixture Statistics ───────────────────────────────────────────────────────

def fetch_fixture_corners(fixture_id: int, home_team_id: int,
                          away_team_id: int) -> dict | None:
    """
    Fetch corners for a single completed fixture.
    Checks cache first — never re-fetches the same fixture.
    """
    # Check cache
    conn = get_conn()
    row  = conn.execute("""
        SELECT * FROM fixture_corners WHERE fixture_id = ?
    """, (fixture_id,)).fetchone()
    conn.close()

    if row:
        return dict(row)

    # Fetch from API
    data = _get("fixtures/statistics", {"fixture": fixture_id})

    if not data or "response" not in data or not data["response"]:
        return None

    home_corners = 0
    away_corners = 0

    for team_stats in data["response"]:
        team_id = team_stats.get("team", {}).get("id")
        stats   = team_stats.get("statistics", [])

        corners = next(
            (s["value"] for s in stats if s["type"] == "Corner Kicks"),
            None
        )

        if corners is None:
            continue

        corners = int(corners) if corners else 0

        if team_id == home_team_id:
            home_corners = corners
        elif team_id == away_team_id:
            away_corners = corners

    result = {
        "fixture_id":    fixture_id,
        "home_team_id":  home_team_id,
        "away_team_id":  away_team_id,
        "home_corners":  home_corners,
        "away_corners":  away_corners,
        "total_corners": home_corners + away_corners,
    }

    # Store to cache
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO fixture_corners
        (fixture_id, home_team_id, away_team_id, home_corners,
         away_corners, total_corners)
        VALUES (:fixture_id, :home_team_id, :away_team_id,
                :home_corners, :away_corners, :total_corners)
    """, result)
    conn.commit()
    conn.close()

    return result


# ─── H2H Corners ─────────────────────────────────────────────────────────────

def fetch_h2h_corners(home_team_id: int, away_team_id: int) -> dict:
    """
    Fetch corners from last N H2H matches between two teams.
    Uses /fixtures/headtohead to get fixture IDs,
    then /fixtures/statistics for each to get corners.

    Returns:
        h2h_avg_corners: average total corners across H2H matches
        h2h_home_corners_avg: avg corners for home team in these fixtures
        h2h_away_corners_avg: avg corners for away team in these fixtures
        matches_used: number of matches data was found for
    """
    # Check if already computed today
    conn = get_conn()
    cached = conn.execute("""
        SELECT * FROM h2h WHERE home_team_id = ? AND away_team_id = ?
        AND updated_at > datetime('now', '-7 days')
    """, (home_team_id, away_team_id)).fetchone()
    conn.close()

    if cached and dict(cached).get("avg_corners", 0) > 0:
        print(f"  ✅ H2H corners cached: {home_team_id} vs {away_team_id}")
        c = dict(cached)
        return {
            "h2h_avg_corners":      c["avg_corners"],
            "h2h_home_corners_avg": c["avg_corners"] / 2,
            "h2h_away_corners_avg": c["avg_corners"] / 2,
            "matches_used":         c.get("matches_used", 0),
        }

    print(f"  🔄 Fetching H2H corners: {home_team_id} vs {away_team_id}")

    # Step 1: Get H2H fixture IDs
    data = _get("fixtures/headtohead", {
        "h2h":  f"{home_team_id}-{away_team_id}",
        "last": MAX_H2H_MATCHES,
    })

    if not data or "response" not in data or not data["response"]:
        return _empty_corners()

    # Filter only completed matches
    completed = [
        f for f in data["response"]
        if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
    ][:MAX_H2H_MATCHES]

    if not completed:
        return _empty_corners()

    # Step 2: Fetch corners for each fixture
    home_corners_list  = []
    away_corners_list  = []
    total_corners_list = []

    for match in completed:
        fid      = match["fixture"]["id"]
        home_id  = match["teams"]["home"]["id"]
        away_id  = match["teams"]["away"]["id"]

        corners = fetch_fixture_corners(fid, home_id, away_id)

        if not corners or corners["total_corners"] == 0:
            continue

        # Normalise to our home/away perspective
        if home_id == home_team_id:
            home_corners_list.append(corners["home_corners"])
            away_corners_list.append(corners["away_corners"])
        else:
            # Teams were swapped in this H2H match
            home_corners_list.append(corners["away_corners"])
            away_corners_list.append(corners["home_corners"])

        total_corners_list.append(corners["total_corners"])

    if not total_corners_list:
        return _empty_corners()

    avg_total = round(sum(total_corners_list) / len(total_corners_list), 2)
    avg_home  = round(sum(home_corners_list) / len(home_corners_list), 2)
    avg_away  = round(sum(away_corners_list) / len(away_corners_list), 2)

    # Update H2H table with corners data
    conn = get_conn()
    conn.execute("""
        UPDATE h2h SET avg_corners = ?, updated_at = datetime('now')
        WHERE home_team_id = ? AND away_team_id = ?
    """, (avg_total, home_team_id, away_team_id))
    conn.commit()
    conn.close()

    print(f"  ✅ H2H corners: avg={avg_total} ({len(total_corners_list)} matches)")

    return {
        "h2h_avg_corners":      avg_total,
        "h2h_home_corners_avg": avg_home,
        "h2h_away_corners_avg": avg_away,
        "matches_used":         len(total_corners_list),
    }


# ─── Team Season Corners ──────────────────────────────────────────────────────

def fetch_team_season_corners(team_id: int, league_id: int,
                               season: int = 2025) -> dict:  # defaults to 2025/2026 (last season)
    """
    Compute a team's corners average from their last N league matches.
    Fetches recent fixtures for the team, then stats for each.
    Caches result for 7 days.

    Returns home_corners_avg, away_corners_avg, corners_conceded_avg
    """
    ensure_tables()

    # Check cache for home
    conn    = get_conn()
    home_row = conn.execute("""
        SELECT * FROM team_corners_avg
        WHERE team_id = ? AND venue = 'home' AND league_id = ?
        AND updated_at > datetime('now', '-7 days')
    """, (team_id, league_id)).fetchone()

    away_row = conn.execute("""
        SELECT * FROM team_corners_avg
        WHERE team_id = ? AND venue = 'away' AND league_id = ?
        AND updated_at > datetime('now', '-7 days')
    """, (team_id, league_id)).fetchone()
    conn.close()

    if home_row and away_row:
        h = dict(home_row)
        a = dict(away_row)
        return {
            "corners_home_avg":      h["corners_avg"],
            "corners_away_avg":      a["corners_avg"],
            "corners_conceded_avg":  (h["corners_conceded_avg"] + a["corners_conceded_avg"]) / 2,
            "corners_avg":           (h["corners_avg"] + a["corners_avg"]) / 2,
        }

    print(f"  🔄 Fetching season corners: team {team_id} | league {league_id}")

    # Fetch last 10 completed fixtures for this team in this league
    data = _get("fixtures", {
        "team":   team_id,
        "league": league_id,
        "season": season,
        "last":   10,
        "status": "FT",
    })

    if not data or "response" not in data or not data["response"]:
        return _empty_team_corners()

    home_scored    = []
    home_conceded  = []
    away_scored    = []
    away_conceded  = []

    for match in data["response"]:
        fid     = match["fixture"]["id"]
        home_id = match["teams"]["home"]["id"]
        away_id = match["teams"]["away"]["id"]
        is_home = home_id == team_id

        corners = fetch_fixture_corners(fid, home_id, away_id)
        if not corners or corners["total_corners"] == 0:
            continue

        if is_home:
            home_scored.append(corners["home_corners"])
            home_conceded.append(corners["away_corners"])
        else:
            away_scored.append(corners["away_corners"])
            away_conceded.append(corners["home_corners"])

    # Compute averages
    h_avg   = round(sum(home_scored)   / len(home_scored),   2) if home_scored   else 5.0
    a_avg   = round(sum(away_scored)   / len(away_scored),   2) if away_scored   else 4.5
    hc_avg  = round(sum(home_conceded) / len(home_conceded), 2) if home_conceded else 4.5
    ac_avg  = round(sum(away_conceded) / len(away_conceded), 2) if away_conceded else 5.0
    con_avg = round((hc_avg + ac_avg) / 2, 2)
    overall = round((h_avg + a_avg) / 2, 2)

    # Store to cache
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO team_corners_avg
        (team_id, venue, league_id, corners_avg, corners_conceded_avg,
         matches_used, updated_at)
        VALUES (?, 'home', ?, ?, ?, ?, datetime('now'))
    """, (team_id, league_id, h_avg, hc_avg, len(home_scored)))

    conn.execute("""
        INSERT OR REPLACE INTO team_corners_avg
        (team_id, venue, league_id, corners_avg, corners_conceded_avg,
         matches_used, updated_at)
        VALUES (?, 'away', ?, ?, ?, ?, datetime('now'))
    """, (team_id, league_id, a_avg, ac_avg, len(away_scored)))

    conn.commit()
    conn.close()

    print(f"  ✅ Season corners: home={h_avg} away={a_avg} conceded={con_avg}")

    return {
        "corners_home_avg":     h_avg,
        "corners_away_avg":     a_avg,
        "corners_conceded_avg": con_avg,
        "corners_avg":          overall,
    }


# ─── Main Lookup ──────────────────────────────────────────────────────────────

def get_corners_data(home_team_id: int, away_team_id: int,
                     league_id: int, season: int = 2025) -> dict:  # defaults to 2025/2026 (last season)
    """
    Master function called by models.py for corners data.
    Returns all corners features needed by the corners model.
    Falls back to league proxies if API returns nothing.
    """
    ensure_tables()

    # Fetch team season corners
    home_c = fetch_team_season_corners(home_team_id, league_id, season)
    away_c = fetch_team_season_corners(away_team_id, league_id, season)

    # Fetch H2H corners
    h2h_c  = fetch_h2h_corners(home_team_id, away_team_id)

    return {
        # Home team corners profile
        "home_corners_avg":      home_c.get("corners_avg",      5.0),
        "home_corners_home_avg": home_c.get("corners_home_avg", 5.0),
        "home_corners_conceded": home_c.get("corners_conceded_avg", 4.5),

        # Away team corners profile
        "away_corners_avg":      away_c.get("corners_avg",      4.5),
        "away_corners_away_avg": away_c.get("corners_away_avg", 4.5),
        "away_corners_conceded": away_c.get("corners_conceded_avg", 5.0),

        # H2H corners
        "h2h_avg_corners":       h2h_c.get("h2h_avg_corners",  0.0),
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _empty_corners() -> dict:
    return {
        "h2h_avg_corners":      0.0,
        "h2h_home_corners_avg": 0.0,
        "h2h_away_corners_avg": 0.0,
        "matches_used":         0,
    }


def _empty_team_corners() -> dict:
    return {
        "corners_home_avg":     5.0,
        "corners_away_avg":     4.5,
        "corners_conceded_avg": 4.8,
        "corners_avg":          4.8,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()
    ensure_tables()

    args = sys.argv[1:]

    if args and args[0] == "h2h":
        # python corners_stats.py h2h <home_id> <away_id>
        home_id = int(args[1])
        away_id = int(args[2])
        result  = fetch_h2h_corners(home_id, away_id)
        print(f"\nH2H corners: {result}")

    elif args and args[0] == "team":
        # python corners_stats.py team <team_id> <league_id>
        team_id   = int(args[1])
        league_id = int(args[2])
        result    = fetch_team_season_corners(team_id, league_id)
        print(f"\nTeam corners: {result}")

    elif args and args[0] == "fixture":
        # python corners_stats.py fixture <fixture_id> <home_id> <away_id>
        fid     = int(args[1])
        home_id = int(args[2])
        away_id = int(args[3])
        result  = fetch_fixture_corners(fid, home_id, away_id)
        print(f"\nFixture corners: {result}")

    else:
        print("Usage:")
        print("  python corners_stats.py h2h <home_id> <away_id>")
        print("  python corners_stats.py team <team_id> <league_id>")
        print("  python corners_stats.py fixture <fixture_id> <home_id> <away_id>")
