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

    # Subset, not equality: check_all also reports throttled/outage, and
    # pinning the exact dict shape makes every added counter a failure.
    assert result["checked"] == 1
    assert result["broken"] == 0
    assert result["recovered"] == 0
    assert result["outage"] is False
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


# ── Provider outage vs bad symbols ────────────────────────────────────
#
# The failure these guard against actually shipped: a provider problem
# flagged 75 symbols as "likely delisted", AAPL, MSFT and NVDA among
# them. A verdict that wrong is worse than silence — it buries the one
# symbol that really is broken and teaches the user to ignore the banner.


def _probe_err(results: dict[str, bool], error: str):
    """Like _probe, but lets the test choose the failure message — the
    error text is what separates a throttled probe from a dead symbol."""
    def fake(ticker: str):
        ok = results.get(ticker.upper(), True)
        return (True, None) if ok else (False, error)

    return patch.object(TickerHealthService, "_probe_sync", staticmethod(fake))


async def _book(db, tickers: list[str]):
    p = await _portfolio(db)
    for t in tickers:
        await _position(db, p, t)
    return p


REAL_NAMES = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "ORCL", "CRM", "AMD", "INTC", "MU", "QCOM", "VOO",
]


@pytest.mark.asyncio
async def test_total_provider_failure_flags_nothing(db):
    """The reported bug: every probe fails, so every symbol is blamed."""
    await _book(db, REAL_NAMES)

    with _probe_err({t: False for t in REAL_NAMES}, "no price and no recent history"):
        result = await TickerHealthService(db).check_all()

    assert result["outage"] is True
    assert result["broken"] == 0
    assert result["skipped"] == len(REAL_NAMES)
    assert await TickerHealthService(db).unresolvable_for_user(USER_ID) == []


@pytest.mark.asyncio
async def test_an_outage_run_writes_no_rows_at_all(db):
    """Not even last_checked_at: the run must read as 'did not happen',
    so a later real check isn't fooled into thinking it had a clean pass."""
    from sqlalchemy import func, select as sa_select

    await _book(db, REAL_NAMES)
    with _probe_err({t: False for t in REAL_NAMES}, "429 Too Many Requests"):
        await TickerHealthService(db).check_all()

    count = await db.scalar(sa_select(func.count()).select_from(TickerHealth))
    assert count == 0


@pytest.mark.asyncio
async def test_an_outage_does_not_increment_existing_counters(db):
    """A ticker sitting at 2 failures must not be pushed over the line by
    a run that proves nothing."""
    await _book(db, REAL_NAMES)
    await _health(db, "AAPL", resolves=True, consecutive_failures=2,
                  last_ok_at=datetime.now(UTC) - timedelta(days=3))

    with _probe_err({t: False for t in REAL_NAMES}, "no price and no recent history"):
        await TickerHealthService(db).check_all()

    row = await db.get(TickerHealth, "AAPL")
    assert row.consecutive_failures == 2
    assert row.resolves is True


@pytest.mark.asyncio
async def test_three_consecutive_outages_still_flag_nothing(db):
    """Repetition must not launder an outage into a verdict."""
    await _book(db, REAL_NAMES)
    for _ in range(3):
        with _probe_err({t: False for t in REAL_NAMES}, "429 Too Many Requests"):
            await TickerHealthService(db).check_all()

    assert await TickerHealthService(db).unresolvable_for_user(USER_ID) == []


@pytest.mark.asyncio
async def test_one_genuine_typo_among_healthy_names_still_flags(db):
    """The guard must not buy robustness by going blind — this is the
    case the banner exists for."""
    await _book(db, REAL_NAMES + ["NOTATICKER"])
    failing = {"NOTATICKER": False}

    svc = TickerHealthService(db)
    for _ in range(3):
        with _probe_err(failing, "no price and no recent history"):
            result = await svc.check_all()
        assert result["outage"] is False

    bad = await svc.unresolvable_for_user(USER_ID)
    assert [b["ticker"] for b in bad] == ["NOTATICKER"]


@pytest.mark.asyncio
async def test_a_minority_of_failures_is_still_judged_per_ticker(db):
    """Just under the ratio: normal path, flags land."""
    await _book(db, REAL_NAMES)
    # 7 of 15 = 47%, below the 60% threshold.
    failing = {t: False for t in REAL_NAMES[:7]}

    svc = TickerHealthService(db)
    for _ in range(3):
        with _probe_err(failing, "no price and no recent history"):
            result = await svc.check_all()
        assert result["outage"] is False

    bad = {b["ticker"] for b in await svc.unresolvable_for_user(USER_ID)}
    assert bad == set(REAL_NAMES[:7])


@pytest.mark.asyncio
async def test_a_small_set_is_never_treated_as_an_outage(db):
    """Two of three failing is 67% but means nothing — a short watchlist
    of mostly-wrong symbols must still get warned about."""
    await _book(db, ["GOOD", "BAD1", "BAD2"])
    failing = {"BAD1": False, "BAD2": False}

    svc = TickerHealthService(db)
    for _ in range(3):
        with _probe_err(failing, "no price and no recent history"):
            result = await svc.check_all()
        assert result["outage"] is False

    bad = {b["ticker"] for b in await svc.unresolvable_for_user(USER_ID)}
    assert bad == {"BAD1", "BAD2"}


# ── Transport errors are not evidence about a symbol ──────────────────


@pytest.mark.asyncio
async def test_rate_limited_probe_does_not_count_against_the_ticker(db):
    """A trickle of 429s spread over days must not accumulate into a
    verdict, even when the overall failure rate stays low."""
    await _book(db, REAL_NAMES)
    failing = {"AAPL": False}

    svc = TickerHealthService(db)
    for _ in range(5):
        with _probe_err(failing, "YFRateLimitError: 429 Too Many Requests"):
            result = await svc.check_all()
        assert result["throttled"] == 1

    row = await db.get(TickerHealth, "AAPL")
    assert row.consecutive_failures == 0
    assert row.resolves is True
    assert "429" in row.last_error, "error is still recorded for diagnosis"


@pytest.mark.parametrize("err", [
    "YFRateLimitError: 429 Too Many Requests",
    "ReadTimeout: timed out",
    "ConnectionError: Max retries exceeded",
    "SSLError: certificate verify failed",
    "HTTPError: 503 Service Temporarily Unavailable",
])
def test_transport_errors_recognised(err):
    assert TickerHealthService._is_transport_error(err) is True


@pytest.mark.parametrize("err", [
    "no price and no recent history",
    None,
    "",
])
def test_genuine_emptiness_is_not_a_transport_error(err):
    assert TickerHealthService._is_transport_error(err) is False


@pytest.mark.asyncio
async def test_empty_response_still_flags_after_three_runs(db):
    """The one error that IS evidence about the symbol keeps working."""
    await _book(db, REAL_NAMES + ["DEADCO"])

    svc = TickerHealthService(db)
    for _ in range(3):
        with _probe_err({"DEADCO": False}, "no price and no recent history"):
            await svc.check_all()

    bad = [b["ticker"] for b in await svc.unresolvable_for_user(USER_ID)]
    assert bad == ["DEADCO"]
