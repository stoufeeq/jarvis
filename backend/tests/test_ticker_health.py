"""Tests for ticker resolution monitoring.

The whole value of this feature rests on ONE property: it must not cry
wolf. yfinance 404s transiently for large, liquid, unambiguously-listed
names — CTRA, BK, MMC, HOLX and EXAS have all failed mid-heatmap while
being perfectly real S&P constituents. A warning that fires on those
would be permanently lit and quickly ignored, which is worse than having
no warning at all.

So the tests below spend most of their effort on the failure-threshold
behaviour rather than on the happy path.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.ticker_health import TickerHealth
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.ticker_health import TickerHealthService

USER_ID = 1


# ── Fixtures ──────────────────────────────────────────────────────────


async def _portfolio(db, user_id: int = USER_ID, broker=BrokerType.manual) -> Portfolio:
    p = Portfolio(user_id=user_id, name="P", broker=broker, currency="USD")
    db.add(p)
    await db.flush()
    return p


async def _position(db, portfolio, ticker: str, qty: float = 10) -> Position:
    pos = Position(
        portfolio_id=portfolio.id, ticker=ticker,
        quantity=Decimal(str(qty)), avg_cost=Decimal("100"),
        currency="USD", opened_at=datetime.now(UTC),
    )
    db.add(pos)
    await db.flush()
    return pos


async def _watchlist(db, ticker: str, user_id: int = USER_ID) -> Watchlist:
    wl = Watchlist(user_id=user_id, name="Main")
    db.add(wl)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=wl.id, ticker=ticker))
    await db.flush()
    return wl


async def _health(db, ticker: str, **kw) -> TickerHealth:
    row = TickerHealth(ticker=ticker, **kw)
    db.add(row)
    await db.flush()
    return row


def _probe(results: dict[str, bool]):
    """Stub the provider probe. `results` maps ticker → resolves."""
    def fake(ticker: str):
        ok = results.get(ticker.upper(), True)
        return (True, None) if ok else (False, "no price and no recent history")

    return patch.object(TickerHealthService, "_probe_sync", staticmethod(fake))


# ── Which tickers get tracked ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_tracks_positions_and_watchlist(db):
    p = await _portfolio(db)
    await _position(db, p, "AAPL")
    await _watchlist(db, "MSFT")

    assert await TickerHealthService(db).tracked_tickers(USER_ID) == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_ignores_closed_positions(db):
    """A zero-quantity row is a closed position — no point warning about
    a symbol the user no longer holds."""
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=0)

    assert await TickerHealthService(db).tracked_tickers(USER_ID) == []


@pytest.mark.asyncio
async def test_scoped_to_the_requesting_user(db):
    mine = await _portfolio(db, user_id=1)
    theirs = await _portfolio(db, user_id=2)
    await _position(db, mine, "AAPL")
    await _position(db, theirs, "TSLA")

    assert await TickerHealthService(db).tracked_tickers(1) == ["AAPL"]
    assert await TickerHealthService(db).tracked_tickers(2) == ["TSLA"]


@pytest.mark.asyncio
async def test_no_user_filter_covers_everyone(db):
    """The Celery task checks every user's tickers in one pass."""
    a = await _portfolio(db, user_id=1)
    b = await _portfolio(db, user_id=2)
    await _position(db, a, "AAPL")
    await _position(db, b, "TSLA")

    assert await TickerHealthService(db).tracked_tickers(None) == ["AAPL", "TSLA"]


@pytest.mark.asyncio
async def test_deduplicates_across_sources(db):
    p = await _portfolio(db)
    await _position(db, p, "AAPL")
    await _watchlist(db, "AAPL")

    assert await TickerHealthService(db).tracked_tickers(USER_ID) == ["AAPL"]


# ── The failure threshold — the property that matters ─────────────────


@pytest.mark.asyncio
async def test_single_failure_does_not_flag(db):
    """The core anti-false-positive guarantee. One transient 404 on a
    real listing must not light the warning."""
    p = await _portfolio(db)
    await _position(db, p, "CTRA")

    with _probe({"CTRA": False}):
        result = await TickerHealthService(db).check_all()

    assert result["broken"] == 0
    row = await db.get(TickerHealth, "CTRA")
    assert row.resolves is True
    assert row.consecutive_failures == 1
    assert await TickerHealthService(db).unresolvable_for_user(USER_ID) == []


@pytest.mark.asyncio
async def test_two_failures_still_does_not_flag(db):
    p = await _portfolio(db)
    await _position(db, p, "CTRA")
    svc = TickerHealthService(db)

    with _probe({"CTRA": False}):
        await svc.check_all()
        await svc.check_all()

    row = await db.get(TickerHealth, "CTRA")
    assert row.resolves is True
    assert row.consecutive_failures == 2


@pytest.mark.asyncio
async def test_third_consecutive_failure_flags(db):
    p = await _portfolio(db)
    await _position(db, p, "MBGD")
    svc = TickerHealthService(db)

    with _probe({"MBGD": False}):
        for _ in range(TickerHealthService.FAILURE_THRESHOLD):
            result = await svc.check_all()

    assert result["broken"] == 1
    row = await db.get(TickerHealth, "MBGD")
    assert row.resolves is False
    assert row.consecutive_failures == 3

    flagged = await svc.unresolvable_for_user(USER_ID)
    assert [f["ticker"] for f in flagged] == ["MBGD"]
    assert flagged[0]["never_resolved"] is True


@pytest.mark.asyncio
async def test_one_success_resets_the_counter(db):
    """Two bad days then a good one must return the ticker to a clean
    slate — otherwise intermittent flakiness accumulates to a false flag
    over weeks."""
    p = await _portfolio(db)
    await _position(db, p, "BK")
    svc = TickerHealthService(db)

    with _probe({"BK": False}):
        await svc.check_all()
        await svc.check_all()
    assert (await db.get(TickerHealth, "BK")).consecutive_failures == 2

    with _probe({"BK": True}):
        await svc.check_all()

    row = await db.get(TickerHealth, "BK")
    assert row.consecutive_failures == 0
    assert row.resolves is True
    assert row.last_ok_at is not None


@pytest.mark.asyncio
async def test_alternating_failures_never_flag(db):
    """Fail, pass, fail, pass … is exactly the flaky-provider pattern and
    must never trip the warning no matter how long it runs."""
    p = await _portfolio(db)
    await _position(db, p, "MMC")
    svc = TickerHealthService(db)

    for _ in range(6):
        with _probe({"MMC": False}):
            await svc.check_all()
        with _probe({"MMC": True}):
            await svc.check_all()

    row = await db.get(TickerHealth, "MMC")
    assert row.resolves is True
    assert row.consecutive_failures == 0


@pytest.mark.asyncio
async def test_recovery_clears_the_flag(db):
    """A renamed ticker that starts resolving again should stop warning
    without manual intervention."""
    p = await _portfolio(db)
    await _position(db, p, "XYZ")
    svc = TickerHealthService(db)

    with _probe({"XYZ": False}):
        for _ in range(3):
            await svc.check_all()
    assert (await db.get(TickerHealth, "XYZ")).resolves is False

    with _probe({"XYZ": True}):
        result = await svc.check_all()

    assert result["recovered"] == 1
    assert (await db.get(TickerHealth, "XYZ")).resolves is True
    assert await svc.unresolvable_for_user(USER_ID) == []


@pytest.mark.asyncio
async def test_healthy_ticker_stays_healthy(db):
    p = await _portfolio(db)
    await _position(db, p, "AAPL")

    with _probe({"AAPL": True}):
        result = await TickerHealthService(db).check_all()

    assert result == {"checked": 1, "broken": 0, "recovered": 0}
    assert (await db.get(TickerHealth, "AAPL")).resolves is True


# ── never_resolved vs previously-worked ───────────────────────────────


@pytest.mark.asyncio
async def test_never_resolved_distinguishes_typo_from_delisting(db):
    """A symbol that never worked is probably a typo; one that used to
    work is probably delisted or renamed. Different fixes, so the UI
    needs to tell them apart."""
    p = await _portfolio(db)
    await _position(db, p, "TYPO")
    await _position(db, p, "OLDCO")

    # OLDCO resolved once, a while back.
    await _health(
        db, "OLDCO", resolves=False, consecutive_failures=5,
        last_ok_at=datetime.now(UTC) - timedelta(days=30),
    )
    await _health(db, "TYPO", resolves=False, consecutive_failures=9, last_ok_at=None)

    flagged = {f["ticker"]: f for f in
               await TickerHealthService(db).unresolvable_for_user(USER_ID)}

    assert flagged["TYPO"]["never_resolved"] is True
    assert flagged["OLDCO"]["never_resolved"] is False
    assert flagged["OLDCO"]["last_ok_at"] is not None


@pytest.mark.asyncio
async def test_unresolvable_scoped_to_user(db):
    """One user's broken ticker must not appear in another's warning."""
    mine = await _portfolio(db, user_id=1)
    theirs = await _portfolio(db, user_id=2)
    await _position(db, mine, "AAPL")
    await _position(db, theirs, "BADTKR")
    await _health(db, "BADTKR", resolves=False, consecutive_failures=5)

    assert await TickerHealthService(db).unresolvable_for_user(1) == []
    assert len(await TickerHealthService(db).unresolvable_for_user(2)) == 1


@pytest.mark.asyncio
async def test_unresolvable_ignores_untracked_tickers(db):
    """A broken row for something the user no longer holds must not
    surface — otherwise the banner never clears after a cleanup."""
    p = await _portfolio(db)
    await _position(db, p, "AAPL")
    await _health(db, "SOLDOFF", resolves=False, consecutive_failures=9)

    assert await TickerHealthService(db).unresolvable_for_user(USER_ID) == []


@pytest.mark.asyncio
async def test_check_all_with_no_tickers_is_a_noop(db):
    assert await TickerHealthService(db).check_all() == {
        "checked": 0, "broken": 0, "recovered": 0,
    }
