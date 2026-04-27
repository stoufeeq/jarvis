"""
Market session detection for US equity markets (NYSE/NASDAQ).

Determines the current session state (open / pre-market / after-hours / closed)
and the next trading day, accounting for weekends and US market holidays.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# US market holidays — full closures (half-days not included for simplicity)
US_MARKET_HOLIDAYS = {
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",
    "2027-07-05",
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",
}

PRE_MARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTER_HOURS_CLOSE = time(20, 0)


class MarketSession:
    """Snapshot of the US equity market session at a given moment."""

    def __init__(self, now: datetime | None = None):
        if now is None:
            now = datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        self.now_et = now.astimezone(ET)

    @property
    def is_holiday(self) -> bool:
        return self.now_et.strftime("%Y-%m-%d") in US_MARKET_HOLIDAYS

    @property
    def is_weekend(self) -> bool:
        return self.now_et.weekday() >= 5

    @property
    def is_trading_day(self) -> bool:
        return not (self.is_weekend or self.is_holiday)

    @property
    def state(self) -> str:
        """One of: open, pre_market, after_hours, closed_overnight,
        closed_weekend, closed_holiday."""
        if self.is_weekend:
            return "closed_weekend"
        if self.is_holiday:
            return "closed_holiday"
        t = self.now_et.time()
        if t < PRE_MARKET_OPEN:
            return "closed_overnight"
        if t < REGULAR_OPEN:
            return "pre_market"
        if t < REGULAR_CLOSE:
            return "open"
        if t < AFTER_HOURS_CLOSE:
            return "after_hours"
        return "closed_overnight"

    @property
    def is_market_open_now(self) -> bool:
        return self.state == "open"

    def next_trading_day(self) -> datetime:
        """Return the next trading day's 9:30 AM ET datetime.

        If today is a trading day and we're before the open, returns today's open.
        Otherwise looks ahead up to 10 days for the next valid session.
        """
        # If today is a trading day and we're pre-open, the next session is today
        if self.is_trading_day and self.now_et.time() < REGULAR_OPEN:
            return self.now_et.replace(hour=9, minute=30, second=0, microsecond=0)

        # Otherwise look forward
        d = self.now_et.date()
        for _ in range(10):
            d = d + timedelta(days=1)
            iso = d.strftime("%Y-%m-%d")
            if d.weekday() < 5 and iso not in US_MARKET_HOLIDAYS:
                return datetime.combine(d, time(9, 30), tzinfo=ET)
        # Fallback — shouldn't happen unless 10+ consecutive holidays
        return self.now_et + timedelta(days=10)

    def describe(self) -> str:
        """Human-readable session description, useful for AI prompts."""
        next_open = self.next_trading_day()
        next_open_str = next_open.strftime("%A %Y-%m-%d at 9:30 AM ET")

        match self.state:
            case "open":
                close_today = self.now_et.replace(hour=16, minute=0, second=0, microsecond=0)
                hours_left = (close_today - self.now_et).total_seconds() / 3600
                return f"Market is OPEN ({hours_left:.1f}h until close at 4:00 PM ET)"
            case "pre_market":
                return "Pre-market session (4:00 AM – 9:30 AM ET). Regular session opens soon."
            case "after_hours":
                return f"After-hours session (4:00 PM – 8:00 PM ET). Next regular open: {next_open_str}"
            case "closed_overnight":
                return f"Market closed overnight. Next open: {next_open_str}"
            case "closed_weekend":
                return f"Market closed for the weekend. Next open: {next_open_str}"
            case "closed_holiday":
                return f"Market closed for US holiday. Next open: {next_open_str}"
            case _:
                return f"Market state unknown. Next open: {next_open_str}"
