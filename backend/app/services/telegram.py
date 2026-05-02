"""
Telegram bot integration — sends triggered alerts and briefing summaries
to a user's Telegram chat.

Setup (one-time, by you):
1. Open Telegram, message @BotFather, run /newbot, follow prompts.
2. Save the HTTP API token. Set TELEGRAM_BOT_TOKEN in .env.
3. Restart the backend.

Per-user setup:
1. User opens Telegram, searches for the bot by username.
2. User sends /start to the bot.
3. Bot replies with the user's chat_id (handled by the /telegram/webhook
   endpoint or simply visible at https://api.telegram.org/bot<TOKEN>/getUpdates).
4. User pastes that chat_id into Settings → Telegram.

Once configured, alerts with "telegram" in their channels list are pushed
to the user's chat, and the daily briefing summary is auto-sent on
generation.
"""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to a Telegram chat. Returns True on success."""
    settings = get_settings()
    if not settings.telegram_configured:
        log.debug("Telegram not configured, skipping send")
        return False
    if not chat_id:
        return False

    url = API_BASE.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200 and r.json().get("ok"):
                return True
            log.warning("Telegram send failed (chat_id=%s): %s", chat_id, r.text)
    except Exception as exc:
        log.warning("Telegram send error (chat_id=%s): %s", chat_id, exc)
    return False


def alert_message(ticker: str, alert_type: str, threshold: float | None, price: float | None) -> str:
    """Format a triggered-alert Telegram message."""
    type_label = {
        "price_above": "above",
        "price_below": "below",
        "signal":      "signal triggered",
        "pnl_threshold": "P&L threshold",
    }.get(alert_type, alert_type)

    threshold_str = f"${threshold:,.2f}" if threshold is not None else "—"
    price_str = f"${price:,.2f}" if price is not None else "—"

    return (
        f"🔔 <b>{ticker}</b>\n"
        f"Alert: {type_label} {threshold_str}\n"
        f"Current: {price_str}"
    )


def briefing_message(date: str, sentiment: str, summary: str) -> str:
    """Format a daily briefing summary for Telegram."""
    sentiment_emoji = {
        "bullish":   "🟢",
        "neutral":   "⚪",
        "cautious":  "🟡",
        "bearish":   "🔴",
    }.get(sentiment.lower(), "⚪")

    return (
        f"📰 <b>Daily Briefing — {date}</b>\n"
        f"{sentiment_emoji} Sentiment: <b>{sentiment.title()}</b>\n\n"
        f"{summary}"
    )
