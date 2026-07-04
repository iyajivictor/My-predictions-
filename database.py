import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "predictions.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS fixtures (
            fixture_id      INTEGER PRIMARY KEY,
            match_date      TEXT,
            match_time      TEXT,
            home_team       TEXT,
            away_team       TEXT,
            home_team_id    INTEGER,
            away_team_id    INTEGER,
            league          TEXT,
            league_id       INTEGER,
            season          INTEGER,
            status          TEXT DEFAULT 'NS',
            fetched_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            team_id                 INTEGER,
            league_id               INTEGER,
            season                  INTEGER,
            team_name               TEXT,
            games_played            INTEGER,
            home_played             INTEGER,
            away_played             INTEGER,
            goals_scored_avg        REAL,
            goals_conceded_avg      REAL,
            home_goals_scored_avg   REAL,
            home_goals_conceded_avg REAL,
            away_goals_scored_avg   REAL,
            away_goals_conceded_avg REAL,
            gg_rate                 REAL,
            over25_rate             REAL,
            clean_sheet_rate        REAL,
            corners_avg             REAL,
            form                    TEXT,
            updated_at              TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (team_id, league_id, season)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS h2h (
            home_team_id    INTEGER,
            away_team_id    INTEGER,
            avg_goals       REAL,
            gg_rate         REAL,
            avg_corners     REAL,
            over25_rate     REAL,
            matches_used    INTEGER,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (home_team_id, away_team_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id              INTEGER UNIQUE,
            match_date              TEXT,
            home_team               TEXT,
            away_team               TEXT,
            league                  TEXT,
            league_id               INTEGER,
            season                  INTEGER,

            -- Feature inputs (ML training data later)
            home_corners_avg        REAL,
            away_corners_avg        REAL,
            home_goals_scored_avg   REAL,
            home_goals_conceded_avg REAL,
            away_goals_scored_avg   REAL,
            away_goals_conceded_avg REAL,
            home_gg_rate            REAL,
            away_gg_rate            REAL,
            home_over25_rate        REAL,
            away_over25_rate        REAL,
            home_form_pts           INTEGER,
            away_form_pts           INTEGER,
            h2h_avg_goals           REAL,
            h2h_gg_rate             REAL,
            h2h_avg_corners         REAL,
            h2h_over25_rate         REAL,

            -- Model outputs
            corners_score           REAL,
            corners_pick            TEXT,
            corners_line            REAL,
            gg_score                REAL,
            gg_pick                 TEXT,
            goals_score             REAL,
            goals_pick              TEXT,
            goals_line              REAL,

            -- Actuals (updated after match)
            actual_home_goals       INTEGER,
            actual_away_goals       INTEGER,
            actual_total_goals      INTEGER,
            actual_corners          INTEGER,
            actual_gg               INTEGER,
            corners_correct         INTEGER,
            gg_correct              INTEGER,
            goals_correct           INTEGER,
            result_updated          INTEGER DEFAULT 0,

            created_at              TEXT DEFAULT (datetime('now')),
            updated_at              TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT,
            requests_used   INTEGER,
            requests_limit  INTEGER,
            logged_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized")


if __name__ == "__main__":
    init_db()
