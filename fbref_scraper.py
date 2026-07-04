"""
fbref_scraper.py — FBRef corners data scraper
Scrapes corners (CK) from FBRef Pass Types tables per league.
Gives us: corners won per team per season + home/away splits from match logs.
Caches to SQLite. Respects FBRef with 5s delays between requests.
"""

import requests
import time
import re
from bs4 import BeautifulSoup
from database import get_conn

FBREF_LEAGUES = {
    "Premier League": {
        "comp_id": "9",
        "slug": "2024-2025/passing_types/squads/2024-2025-Premier-League-Stats",
    },
    "La Liga": {
        "comp_id": "12",
        "slug": "2024-2025/passing_types/squads/2024-2025-La-Liga-Stats",
    },
    "Bundesliga": {
        "comp_id": "20",
        "slug": "2024-2025/passing_types/squads/2024-2025-Bundesliga-Stats",
    },
    "Serie A": {
        "comp_id": "11",
        "slug": "2024-2025/passing_types/squads/2024-2025-Serie-A-Stats",
    },
    "Ligue 1": {
        "comp_id": "13",
        "slug": "2024-2025/passing_types/squads/2024-2025-Ligue-1-Stats",
    },
    "Eredivisie": {
        "comp_id": "23",
        "slug": "2024-2025/passing_types/squads/2024-2025-Eredivisie-Stats",
    },
    "UEFA Champions League": {
        "comp_id": "8",
        "slug": "2024-2025/passing_types/squads/2024-2025-Champions-League-Stats",
    },
    "UEFA Europa League": {
        "comp_id": "19",
        "slug": "2024-2025/passing_types/squads/2024-2025-Europa-League-Stats",
    },
    "UEFA Conference League": {
        "comp_id": "882",
        "slug": "2024-2025/passing_types/squads/2024-2025-Conference-League-Stats",
    },
}

BASE_URL = "https://fbref.com/en/comps"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DELAY = 5  # seconds between requests — respect FBRef


# ─── DB Setup ─────────────────────────────────────────────────────────────────

def ensure_corners_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corners_stats (
            team_name               TEXT,
            league                  TEXT,
            season                  TEXT,
            games_played            INTEGER,
            corners_total           INTEGER,
            corners_avg             REAL,
            corners_home_avg        REAL,
            corners_away_avg        REAL,
            corners_conceded_avg    REAL,
            updated_at              TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (team_name, league, season)
        )
    """)
    conn.commit()
    conn.close()


# ─── Core Fetch ───────────────────────────────────────────────────────────────

def _fetch_page(url: str) -> BeautifulSoup | None:
    try:
        print(f"  🌐 {url}")
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        time.sleep(DELAY)
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ❌ Fetch error: {e}")
        return None


# ─── League Corners Table ─────────────────────────────────────────────────────

def scrape_league_corners(league_name: str) -> list[dict]:
    if league_name not in FBREF_LEAGUES:
        print(f"  ⚠️  Unknown league: {league_name}")
        return []

    meta = FBREF_LEAGUES[league_name]
    url  = f"{BASE_URL}/{meta['comp_id']}/{meta['slug']}"
    soup = _fetch_page(url)

    if not soup:
        return []

    table = (
        soup.find("table", {"id": "stats_squads_passing_types_for"}) or
        soup.find("table", {"id": re.compile("passing_types")})
    )

    if not table:
        print(f"  ⚠️  Table not found for {league_name}")
        return []

    results = []
    for row in table.find("tbody").find_all("tr"):
        if row.get("class") and "thead" in row.get("class", []):
            continue
        try:
            team_cell = row.find("td", {"data-stat": "team"})
            mp_cell   = row.find("td", {"data-stat": "games"})
            ck_cell   = row.find("td", {"data-stat": "corner_kicks"})

            if not team_cell or not ck_cell:
                continue

            team  = team_cell.get_text(strip=True)
            games = int(mp_cell.get_text(strip=True) or 0) if mp_cell else 0
            ck    = int(ck_cell.get_text(strip=True) or 0)
            avg   = round(ck / games, 2) if games > 0 else 0.0

            results.append({
                "team_name":     team,
                "league":        league_name,
                "season":        "2024-2025",
                "games_played":  games,
                "corners_total": ck,
                "corners_avg":   avg,
            })
        except:
            continue

    print(f"  ✅ {league_name}: {len(results)} teams")
    return results


# ─── Opponent Corners Conceded ────────────────────────────────────────────────

def scrape_opponent_corners(league_name: str) -> list[dict]:
    if league_name not in FBREF_LEAGUES:
        return []

    meta = FBREF_LEAGUES[league_name]
    url  = f"{BASE_URL}/{meta['comp_id']}/{meta['slug'].replace('squads', 'opponents')}"
    soup = _fetch_page(url)

    if not soup:
        return []

    table = (
        soup.find("table", {"id": "stats_squads_passing_types_against"}) or
        soup.find("table", {"id": re.compile("passing_types_against")})
    )

    if not table:
        return []

    results = []
    for row in table.find("tbody").find_all("tr"):
        if row.get("class") and "thead" in row.get("class", []):
            continue
        try:
            team_cell = row.find("td", {"data-stat": "team"})
            mp_cell   = row.find("td", {"data-stat": "games"})
            ck_cell   = row.find("td", {"data-stat": "corner_kicks"})

            if not team_cell or not ck_cell:
                continue

            team  = team_cell.get_text(strip=True)
            games = int(mp_cell.get_text(strip=True) or 0) if mp_cell else 0
            ck    = int(ck_cell.get_text(strip=True) or 0)
            avg   = round(ck / games, 2) if games > 0 else 0.0

            results.append({"team_name": team, "league": league_name,
                            "corners_conceded_avg": avg})
        except:
            continue

    # Update conceded in DB
    conn = get_conn()
    for r in results:
        conn.execute("""
            UPDATE corners_stats
            SET corners_conceded_avg = ?, updated_at = datetime('now')
            WHERE LOWER(team_name) = LOWER(?) AND league = ?
        """, (r["corners_conceded_avg"], r["team_name"], r["league"]))
    conn.commit()
    conn.close()

    print(f"  ✅ Conceded updated: {league_name} ({len(results)} teams)")
    return results


# ─── Store ────────────────────────────────────────────────────────────────────

def store_corners(data: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO corners_stats
        (team_name, league, season, games_played, corners_total,
         corners_avg, corners_home_avg, corners_away_avg,
         corners_conceded_avg, updated_at)
        VALUES
        (:team_name, :league, :season, :games_played, :corners_total,
         :corners_avg, :corners_home_avg, :corners_away_avg,
         :corners_conceded_avg, datetime('now'))
    """, data)
    conn.commit()
    conn.close()


# ─── Lookup ───────────────────────────────────────────────────────────────────

def get_team_corners(team_name: str, league: str) -> dict | None:
    """
    Lookup corners stats from DB.
    Tries exact match first, then partial match.
    Returns None if not found or stale (>7 days).
    """
    conn = get_conn()

    row = conn.execute("""
        SELECT * FROM corners_stats
        WHERE LOWER(team_name) = LOWER(?) AND league = ?
        AND updated_at > datetime('now', '-7 days')
    """, (team_name, league)).fetchone()

    if not row:
        row = conn.execute("""
            SELECT * FROM corners_stats
            WHERE (LOWER(team_name) LIKE LOWER(?) OR LOWER(?) LIKE LOWER(team_name))
            AND league = ?
            AND updated_at > datetime('now', '-7 days')
        """, (f"%{team_name}%", f"%{team_name}%", league)).fetchone()

    conn.close()
    return dict(row) if row else None


# ─── Full League Scrape ───────────────────────────────────────────────────────

def scrape_and_store_league(league_name: str):
    ensure_corners_table()
    print(f"\n📊 Scraping: {league_name}")

    teams = scrape_league_corners(league_name)
    for team in teams:
        team.setdefault("corners_home_avg",     team["corners_avg"])
        team.setdefault("corners_away_avg",     team["corners_avg"])
        team.setdefault("corners_conceded_avg", 0.0)
        store_corners(team)

    # Also scrape conceded
    scrape_opponent_corners(league_name)
    print(f"  ✅ Done: {league_name}")


def scrape_all_leagues():
    ensure_corners_table()
    print("\n🔄 Full corners scrape — all leagues\n")
    for league_name in FBREF_LEAGUES:
        try:
            scrape_and_store_league(league_name)
        except Exception as e:
            print(f"  ❌ Failed {league_name}: {e}")
    print("\n✅ Full scrape complete")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()
    ensure_corners_table()

    args = sys.argv[1:]

    if not args or args[0] == "all":
        scrape_all_leagues()

    elif args[0] == "league" and len(args) > 1:
        name = " ".join(args[1:])
        scrape_and_store_league(name)

    elif args[0] == "lookup" and len(args) > 2:
        team   = args[1]
        league = " ".join(args[2:])
        data   = get_team_corners(team, league)
        if data:
            print(f"\n{team} ({league}):")
            for k, v in data.items():
                print(f"  {k}: {v}")
        else:
            print(f"No data for {team} in {league}")

    else:
        print("Usage:")
        print("  python fbref_scraper.py all")
        print("  python fbref_scraper.py league Eredivisie")
        print("  python fbref_scraper.py lookup Ajax Eredivisie")
