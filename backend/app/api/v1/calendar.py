from datetime import date as date_cls, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.calendar import CalendarService

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/")
async def get_calendar(
    days_ahead: int = Query(60, ge=1, le=365),
    portfolio_only: bool = Query(False),
    types: list[str] | None = Query(None, description="Subset: earnings, ex_dividend, macro"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upcoming earnings, ex-dividend, and macro events for the user's
    watchlist + portfolio tickers, sorted by date."""
    return await CalendarService(db).upcoming_events_for_user(
        user_id=user.id,
        days_ahead=days_ahead,
        portfolio_only=portfolio_only,
        types=types,
    )


@router.post("/refresh", status_code=202)
async def refresh_calendar(
    user: User = Depends(get_current_user),
):
    """Manually dispatch a Celery task to refresh calendar events for all
    user tickers (one-shot — same task runs daily automatically)."""
    from app.workers.tasks.calendar_refresh import refresh_calendar_events
    task = refresh_calendar_events.delay()
    return {"task_id": task.id, "status": "dispatched"}


@router.get("/export.ics")
async def export_ics(
    days_ahead: int = Query(180, ge=1, le=365),
    portfolio_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export upcoming calendar events as a standards-compliant .ics file
    that imports into Google Calendar / Apple Calendar / Outlook.

    Each event becomes a full-day VEVENT. UID is stable per (ticker, type,
    date) so re-imports update rather than duplicate."""
    events = await CalendarService(db).upcoming_events_for_user(
        user_id=user.id,
        days_ahead=days_ahead,
        portfolio_only=portfolio_only,
    )
    ics_text = _build_ics(events)
    headers = {
        "Content-Disposition": 'attachment; filename="jarvis-calendar.ics"',
    }
    return Response(content=ics_text, media_type="text/calendar", headers=headers)


# ── ICS builder ─────────────────────────────────────────────────────────────


def _ics_escape(s: str) -> str:
    """Escape characters per RFC 5545 §3.3.11 (TEXT type)."""
    return (
        s.replace("\\", "\\\\")
         .replace(";", "\\;")
         .replace(",", "\\,")
         .replace("\n", "\\n")
    )


def _build_ics(events: list[dict]) -> str:
    """Render a minimal but standards-compliant iCalendar (.ics) document."""
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Jarvis//Earnings & Macro Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Jarvis Calendar",
        "X-WR-CALDESC:Upcoming earnings, ex-dividend, and macro events",
    ]

    for e in events:
        try:
            d = date_cls.fromisoformat(e["date"])
        except (ValueError, KeyError):
            continue

        # Full-day event: DTSTART = day, DTEND = next day (per RFC 5545)
        dtstart = d.strftime("%Y%m%d")
        dtend = (d + timedelta(days=1)).strftime("%Y%m%d")

        ticker = e.get("ticker") or "MARKET"
        ev_type = e.get("type", "event")
        uid = f"{ticker}-{ev_type}-{dtstart}@jarvis"

        summary = _ics_escape(e.get("title") or f"{ticker} {ev_type}")

        # Build description from details + IV info if available
        desc_parts = []
        if e.get("details"):
            desc_parts.append(str(e["details"]))
        if e.get("iv_hv_ratio") is not None:
            desc_parts.append(f"IV/HV: {e['iv_hv_ratio']:.2f}")
        if e.get("implied_move_pct") is not None:
            desc_parts.append(f"Implied move: {e['implied_move_pct']:.1f}%")
        description = _ics_escape(" · ".join(desc_parts)) if desc_parts else ""

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;VALUE=DATE:{dtstart}",
            f"DTEND;VALUE=DATE:{dtend}",
            f"SUMMARY:{summary}",
        ])
        if description:
            lines.append(f"DESCRIPTION:{description}")
        # Tag with categories for clients that support filtering
        lines.append(f"CATEGORIES:Jarvis,{ev_type}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    # RFC 5545 requires CRLF line endings
    return "\r\n".join(lines) + "\r\n"
