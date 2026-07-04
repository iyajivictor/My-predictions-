"""
telegram.py — Telegram notification sender
Sends prediction alerts and daily summaries
"""

import httpx
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ─── Core Send ────────────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured chat."""
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Telegram not configured — check .env")
        return False

    try:
        r = httpx.post(f"{BASE_URL}/sendMessage", json={
            "chat_id":    CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10)
        r.raise_for_status()
        print(f"  ✅ Telegram sent")
        return True
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")
        return False


# ─── Prediction Alert ─────────────────────────────────────────────────────────

def format_prediction_message(pred: dict) -> str:
    """
    Format a single fixture prediction into a Telegram message.
    Only called for predictions that pass the threshold.
    """
    lines = []

    # Header
    lines.append(f"⚽ <b>{pred['home_team']} vs {pred['away_team']}</b>")
    lines.append(f"🏆 {pred['league']}")
    lines.append(f"📅 {pred['match_date']}  🕐 {pred.get('match_time', '?')} UTC")
    lines.append("")

    # Picks
    picks_added = 0

    if pred.get("corners_pick") and pred.get("corners_score", 0) >= 75:
        score  = pred["corners_score"]
        pick   = pred["corners_pick"]
        line   = pred.get("corners_line", "9.5")
        conf   = _confidence_label(score)
        lines.append(f"🔵 Corners: <b>{pick} {line}</b>  [{conf} · {score:.0f}]")
        picks_added += 1

    if pred.get("gg_pick") and pred.get("gg_score", 0) >= 75:
        score = pred["gg_score"]
        pick  = pred["gg_pick"]
        conf  = _confidence_label(score)
        lines.append(f"🟢 GG: <b>{pick}</b>  [{conf} · {score:.0f}]")
        picks_added += 1

    if pred.get("goals_pick") and pred.get("goals_score", 0) >= 75:
        score = pred["goals_score"]
        pick  = pred["goals_pick"]
        line  = pred.get("goals_line", "2.5")
        conf  = _confidence_label(score)
        lines.append(f"🟡 Goals: <b>{pick} {line}</b>  [{conf} · {score:.0f}]")
        picks_added += 1

    if picks_added == 0:
        return ""  # Nothing qualifies — don't send

    lines.append("")
    lines.append(f"📊 <i>Picks qualifying: {picks_added}/3</i>")
    lines.append("─" * 28)

    return "\n".join(lines)


def send_prediction(pred: dict) -> bool:
    """Format and send a single prediction. Returns False if nothing qualifies."""
    msg = format_prediction_message(pred)
    if not msg:
        return False
    return send_message(msg)


def send_predictions_batch(predictions: list[dict]):
    """
    Send all qualifying predictions for the day as one grouped message.
    Groups by league for cleaner formatting.
    """
    if not predictions:
        send_message("📭 <b>No qualifying predictions today.</b>")
        return

    today = date.today().strftime("%A, %d %B %Y")
    header = f"🎯 <b>EDGE FOOTBALL — {today}</b>\n{'═' * 30}\n"
    send_message(header)

    sent = 0
    for pred in predictions:
        if send_prediction(pred):
            sent += 1

    # Summary footer
    footer = f"\n✅ <b>{sent} prediction(s) sent today</b>\n<i>All picks logged to database.</i>"
    send_message(footer)


# ─── Daily Summary ────────────────────────────────────────────────────────────

def send_daily_summary(stats: dict):
    """
    Send end-of-day accuracy summary after results are updated.
    stats: { total, corners_correct, gg_correct, goals_correct, corners_total, gg_total, goals_total }
    """
    def pct(correct, total):
        return f"{round(correct/total*100)}%" if total else "N/A"

    lines = [
        "📈 <b>DAILY RESULTS SUMMARY</b>",
        f"📅 {date.today().strftime('%A, %d %B %Y')}",
        "",
        f"🔵 Corners:  {stats.get('corners_correct',0)}/{stats.get('corners_total',0)}  ({pct(stats.get('corners_correct',0), stats.get('corners_total',0))})",
        f"🟢 GG:       {stats.get('gg_correct',0)}/{stats.get('gg_total',0)}  ({pct(stats.get('gg_correct',0), stats.get('gg_total',0))})",
        f"🟡 Goals:    {stats.get('goals_correct',0)}/{stats.get('goals_total',0)}  ({pct(stats.get('goals_correct',0), stats.get('goals_total',0))})",
        "",
        f"📊 Overall:  {stats.get('total_correct',0)}/{stats.get('total_picks',0)}",
        "─" * 28,
        "<i>Results logged. ML dataset updated.</i>",
    ]

    send_message("\n".join(lines))


def send_no_fixtures_alert():
    """Notify when scheduler runs but finds no fixtures."""
    send_message("📭 <b>No fixtures today</b> across tracked leagues.")


def send_error_alert(error: str):
    """Send an error notification."""
    send_message(f"⚠️ <b>Engine Error</b>\n<code>{error[:300]}</code>")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _confidence_label(score: float) -> str:
    if score >= 88:
        return "🔥 HIGH"
    elif score >= 80:
        return "✅ MEDIUM"
    else:
        return "⚠️ LOW"


# ─── CLI Test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing Telegram connection...")
    ok = send_message("🤖 <b>Sports Engine connected.</b>\nTelegram notifications working ✅")
    if ok:
        print("✅ Message sent — check your Telegram")
    else:
        print("❌ Failed — check BOT_TOKEN and CHAT_ID in .env")
