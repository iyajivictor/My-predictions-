"""
accumulator.py — Acca builder
Builds and sends accumulator bets from qualifying predictions in DB.

Acca types:
  Weekend (Thu scan, Sat/Sun fixtures): Corners + Goals
  UCL (Tue): Corners + Goals + GG
  UEL+Conference (Thu): Corners + Goals + GG

Friday top-up: new Sat/Sun fixtures only, appends to existing weekend acca
"""

import os
from datetime import date, timedelta
from database import get_conn
from telegram import send_message
from dotenv import load_dotenv

load_dotenv()

# ─── League Groups ────────────────────────────────────────────────────────────

WEEKEND_LEAGUES = {39, 140, 78, 135, 61, 88}   # Top 5 + Eredivisie
UCL_LEAGUES     = {2}                            # Champions League only
UEFA_LEAGUES    = {3, 848}                       # Europa + Conference

# ─── Thresholds ───────────────────────────────────────────────────────────────

CORNERS_MIN = 88
GOALS_MIN   = 80
GG_MIN      = 85


# ─── Date Helpers ─────────────────────────────────────────────────────────────

def _weekend_dates() -> list[str]:
    """Return Saturday and Sunday dates of the current week."""
    today = date.today()
    # Find next Saturday
    days_to_sat = (5 - today.weekday()) % 7
    if days_to_sat == 0:
        days_to_sat = 7
    saturday = today + timedelta(days=days_to_sat)
    sunday   = saturday + timedelta(days=1)
    return [saturday.isoformat(), sunday.isoformat()]


def _ucl_dates() -> list[str]:
    """Tuesday and Wednesday of current week."""
    today = date.today()
    days_to_tue = (1 - today.weekday()) % 7
    tuesday     = today + timedelta(days=days_to_tue)
    wednesday   = tuesday + timedelta(days=1)
    return [tuesday.isoformat(), wednesday.isoformat()]


def _uel_dates() -> list[str]:
    """Thursday of current week."""
    today = date.today()
    days_to_thu = (3 - today.weekday()) % 7
    thursday    = today + timedelta(days=days_to_thu)
    return [thursday.isoformat()]


# ─── DB Queries ───────────────────────────────────────────────────────────────

def _get_predictions(dates: list[str], league_ids: set) -> list[dict]:
    """Pull all predictions for given dates and leagues."""
    conn      = get_conn()
    placeholders = ",".join("?" * len(dates))
    league_ph    = ",".join("?" * len(league_ids))

    rows = conn.execute(f"""
        SELECT * FROM predictions
        WHERE match_date IN ({placeholders})
        AND league_id IN ({league_ph})
        AND result_updated = 0
        ORDER BY match_date ASC, corners_score DESC
    """, (*dates, *league_ids)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_new_predictions(dates: list[str], league_ids: set,
                         known_ids: list[int]) -> list[dict]:
    """Pull only predictions not already in the known set (for top-ups)."""
    if not known_ids:
        return _get_predictions(dates, league_ids)

    conn         = get_conn()
    placeholders = ",".join("?" * len(dates))
    league_ph    = ",".join("?" * len(league_ids))
    known_ph     = ",".join("?" * len(known_ids))

    rows = conn.execute(f"""
        SELECT * FROM predictions
        WHERE match_date IN ({placeholders})
        AND league_id IN ({league_ph})
        AND fixture_id NOT IN ({known_ph})
        AND result_updated = 0
        ORDER BY match_date ASC
    """, (*dates, *league_ids, *known_ids)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_sent_fixture_ids(acca_type: str) -> list[int]:
    """
    Track which fixture IDs were already sent in a given acca type.
    Uses a simple log table.
    """
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acca_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            acca_type   TEXT,
            fixture_id  INTEGER,
            market      TEXT,
            pick        TEXT,
            score       REAL,
            sent_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    rows = conn.execute("""
        SELECT fixture_id FROM acca_log
        WHERE acca_type = ? AND DATE(sent_at) = DATE('now')
    """, (acca_type,)).fetchall()
    conn.commit()
    conn.close()
    return [r[0] for r in rows]


def _log_acca_picks(acca_type: str, picks: list[dict], market: str):
    """Log sent acca picks to prevent duplication."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acca_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            acca_type   TEXT,
            fixture_id  INTEGER,
            market      TEXT,
            pick        TEXT,
            score       REAL,
            sent_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    for p in picks:
        conn.execute("""
            INSERT INTO acca_log (acca_type, fixture_id, market, pick, score)
            VALUES (?, ?, ?, ?, ?)
        """, (acca_type, p["fixture_id"],
              market, p.get(f"{market}_pick"), p.get(f"{market}_score")))
    conn.commit()
    conn.close()


# ─── Pick Filters ─────────────────────────────────────────────────────────────

def _corners_picks(predictions: list[dict]) -> list[dict]:
    return [
        p for p in predictions
        if (p.get("corners_score") or 0) >= CORNERS_MIN
        and p.get("corners_pick") == "OVER"
    ]


def _goals_picks(predictions: list[dict]) -> list[dict]:
    return [
        p for p in predictions
        if (p.get("goals_score") or 0) >= GOALS_MIN
        and p.get("goals_pick") in ("OVER", "UNDER")
    ]


def _gg_picks(predictions: list[dict]) -> list[dict]:
    return [
        p for p in predictions
        if (p.get("gg_score") or 0) >= GG_MIN
        and p.get("gg_pick") == "YES"
    ]


# ─── Telegram Formatters ──────────────────────────────────────────────────────

def _format_acca(title: str, picks: list[dict], market: str,
                 top_up: bool = False) -> str:
    if not picks:
        return ""

    emoji = {"corners": "🔵", "goals": "🟡", "gg": "🟢"}.get(market, "⚽")
    prefix = "➕ TOP-UP — " if top_up else ""

    lines = [f"{emoji} <b>{prefix}{title}</b>", f"{'─' * 30}"]

    for i, p in enumerate(picks, 1):
        match   = f"{p['home_team']} vs {p['away_team']}"
        league  = p["league"]
        mdate   = p["match_date"]

        if market == "corners":
            pick_str = f"Corners OVER {p.get('corners_line', 9.5)}"
            score    = p.get("corners_score", 0)
        elif market == "goals":
            pick_str = f"Goals {p.get('goals_pick')} {p.get('goals_line', 2.5)}"
            score    = p.get("goals_score", 0)
        else:  # gg
            pick_str = "GG — YES"
            score    = p.get("gg_score", 0)

        lines.append(
            f"{i}. <b>{match}</b>\n"
            f"   🏆 {league} · {mdate}\n"
            f"   📌 {pick_str}  [{score:.0f}]"
        )

    lines.append(f"{'─' * 30}")
    lines.append(f"📊 <i>{len(picks)} selection(s)</i>")
    return "\n".join(lines)


# ─── Acca Senders ─────────────────────────────────────────────────────────────

def send_weekend_accas(top_up: bool = False):
    """
    Thursday: scan Sat/Sun fixtures, build corners + goals accas.
    Friday: top-up only — new fixtures not already sent.
    """
    dates      = _weekend_dates()
    acca_label = "weekend"

    if top_up:
        known_ids   = _get_sent_fixture_ids(acca_label)
        predictions = _get_new_predictions(dates, WEEKEND_LEAGUES, known_ids)
        if not predictions:
            print("📭 No new weekend fixtures to top up")
            return
    else:
        predictions = _get_predictions(dates, WEEKEND_LEAGUES)

    if not predictions:
        print("📭 No weekend predictions found")
        send_message("📭 <b>No qualifying weekend picks found yet.</b>")
        return

    prefix = "WEEKEND" if not top_up else "WEEKEND TOP-UP"

    # Header
    sat, sun = dates[0], dates[1]
    header   = f"🗓 <b>{prefix} ACCA</b>\n📅 {sat} → {sun}\n{'═' * 30}"
    send_message(header)

    # Corners acca
    c_picks = _corners_picks(predictions)
    if c_picks:
        msg = _format_acca("CORNERS ACCA", c_picks, "corners", top_up)
        send_message(msg)
        _log_acca_picks(acca_label, c_picks, "corners")

    # Goals acca
    g_picks = _goals_picks(predictions)
    if g_picks:
        msg = _format_acca("GOALS ACCA", g_picks, "goals", top_up)
        send_message(msg)
        _log_acca_picks(acca_label, g_picks, "goals")

    if not c_picks and not g_picks:
        send_message("📭 <b>No picks above threshold this weekend.</b>")

    print(f"✅ Weekend acca sent — {len(c_picks)} corners, {len(g_picks)} goals")


def send_ucl_accas():
    """
    Tuesday: scan UCL fixtures for Tue+Wed, build corners + goals + GG accas.
    """
    dates       = _ucl_dates()
    predictions = _get_predictions(dates, UCL_LEAGUES)

    if not predictions:
        print("📭 No UCL predictions found")
        return

    tue, wed = dates[0], dates[1]
    header   = f"🏆 <b>UCL ACCA</b>\n📅 {tue} → {wed}\n{'═' * 30}"
    send_message(header)

    c_picks = _corners_picks(predictions)
    g_picks = _goals_picks(predictions)
    gg_picks = _gg_picks(predictions)

    if c_picks:
        send_message(_format_acca("UCL CORNERS ACCA", c_picks, "corners"))
        _log_acca_picks("ucl", c_picks, "corners")

    if g_picks:
        send_message(_format_acca("UCL GOALS ACCA", g_picks, "goals"))
        _log_acca_picks("ucl", g_picks, "goals")

    if gg_picks:
        send_message(_format_acca("UCL GG ACCA", gg_picks, "gg"))
        _log_acca_picks("ucl", gg_picks, "gg")

    if not any([c_picks, g_picks, gg_picks]):
        send_message("📭 <b>No qualifying UCL picks this week.</b>")

    print(f"✅ UCL acca sent — {len(c_picks)} corners, {len(g_picks)} goals, {len(gg_picks)} GG")


def send_uel_conf_accas():
    """
    Thursday: scan UEL + Conference fixtures, build corners + goals + GG accas.
    Runs alongside weekend acca scan on Thursday morning.
    """
    dates       = _uel_dates()
    predictions = _get_predictions(dates, UEFA_LEAGUES)

    if not predictions:
        print("📭 No UEL/Conference predictions found")
        return

    thu      = dates[0]
    header   = f"🟠 <b>UEL + CONFERENCE ACCA</b>\n📅 {thu}\n{'═' * 30}"
    send_message(header)

    c_picks  = _corners_picks(predictions)
    g_picks  = _goals_picks(predictions)
    gg_picks = _gg_picks(predictions)

    if c_picks:
        send_message(_format_acca("UEL/CONF CORNERS ACCA", c_picks, "corners"))
        _log_acca_picks("uel_conf", c_picks, "corners")

    if g_picks:
        send_message(_format_acca("UEL/CONF GOALS ACCA", g_picks, "goals"))
        _log_acca_picks("uel_conf", g_picks, "goals")

    if gg_picks:
        send_message(_format_acca("UEL/CONF GG ACCA", gg_picks, "gg"))
        _log_acca_picks("uel_conf", gg_picks, "gg")

    if not any([c_picks, g_picks, gg_picks]):
        send_message("📭 <b>No qualifying UEL/Conference picks this week.</b>")

    print(f"✅ UEL/Conf acca sent — {len(c_picks)} corners, {len(g_picks)} goals, {len(gg_picks)} GG")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()

    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print("  python accumulator.py weekend       — send weekend acca (Thu)")
        print("  python accumulator.py topup         — send Friday top-up")
        print("  python accumulator.py ucl           — send UCL acca (Tue)")
        print("  python accumulator.py uel           — send UEL+Conf acca (Thu)")
        print("  python accumulator.py all           — send all accas")

    elif args[0] == "weekend":
        send_weekend_accas()

    elif args[0] == "topup":
        send_weekend_accas(top_up=True)

    elif args[0] == "ucl":
        send_ucl_accas()

    elif args[0] == "uel":
        send_uel_conf_accas()

    elif args[0] == "all":
        send_weekend_accas()
        send_ucl_accas()
        send_uel_conf_accas()
