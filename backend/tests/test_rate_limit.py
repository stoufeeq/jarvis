"""Yahoo request budget — the limiter and the batch quote client.

The incident these guard against: ~11,000 provider requests/hour against
a limit near 360, an IP-level throttle, and 429s that surfaced as 75
holdings "likely delisted". Two separate defects — the volume, and the
absence of any ceiling that would have made the volume visible.

The limiter is tested against a fake Redis rather than a real one so the
suite stays hermetic. The Lua script is the part a fake cannot exercise,
so the arithmetic it encodes is pinned here against the same semantics.
"""

import asyncio

import pytest

from app.services import yahoo_quotes
from app.services.rate_limit import YahooRateLimiter


class FakeRedisWindow:
    """Minimal stand-in: a sorted set plus the registered-script hook."""

    def __init__(self):
        self.entries: list[tuple[float, str]] = []

    def register_script(self, _lua):
        async def run(keys, args):
            now, window, budget, cost, member = (
                float(args[0]), float(args[1]), int(args[2]), int(args[3]), args[4]
            )
            self.entries = [(sc, m) for sc, m in self.entries if sc > now - window]
            used = len(self.entries)
            if used + cost > budget:
                retry = window
                if self.entries:
                    retry = (min(sc for sc, _ in self.entries) + window) - now
                return [0, used, retry]
            for i in range(cost):
                self.entries.append((now, f"{member}:{i}"))
            return [1, used + cost, 0]

        return run

    async def zremrangebyscore(self, _k, _lo, _hi):
        return 0

    async def zcard(self, _k):
        return len(self.entries)

    async def aclose(self):
        pass


def _limiter(budget: int) -> tuple[YahooRateLimiter, FakeRedisWindow]:
    fake = FakeRedisWindow()
    lim = YahooRateLimiter(budget=budget)
    lim._client = fake
    lim._script = fake.register_script(None)
    lim._enabled = True
    return lim, fake


# ── The ceiling ───────────────────────────────────────────────────────


async def test_spends_up_to_the_budget_then_refuses():
    lim, _ = _limiter(5)
    for _ in range(5):
        granted, _, _ = await lim.try_acquire()
        assert granted
    granted, used, retry = await lim.try_acquire()
    assert granted is False
    assert used == 5
    assert retry > 0, "must say when a slot frees so callers can sleep exactly"


async def test_a_multi_request_cost_is_all_or_nothing():
    """A 5-chunk heatmap fetch must not get 3 slots and start anyway —
    half a heatmap cached for ten minutes is worse than none."""
    lim, _ = _limiter(10)
    assert (await lim.try_acquire(cost=8))[0] is True
    granted, used, _ = await lim.try_acquire(cost=5)
    assert granted is False
    assert used == 8, "nothing was spent on the refused request"
    assert (await lim.try_acquire(cost=2))[0] is True


async def test_cost_is_counted_not_just_the_call():
    lim, fake = _limiter(100)
    await lim.try_acquire(cost=5)
    assert len(fake.entries) == 5
    assert (await lim.usage())["used"] == 5


async def test_window_slides_so_old_requests_stop_counting():
    import time

    lim, fake = _limiter(3)
    # Three requests, timestamped just over an hour ago.
    stale = time.time() - 3601
    fake.entries = [(stale, f"old:{i}") for i in range(3)]
    granted, used, _ = await lim.try_acquire()
    assert granted is True
    assert used == 1, "expired entries were trimmed before counting"


async def test_acquire_waits_then_succeeds_when_budget_frees():
    import time

    lim, fake = _limiter(2)
    # Full, but the oldest entry leaves the window almost immediately.
    nearly_gone = time.time() - 3599.4
    fake.entries = [(nearly_gone, "a"), (nearly_gone, "b")]
    assert await lim.acquire(timeout=5.0) is True


async def test_acquire_gives_up_rather_than_proceeding_unthrottled():
    """Returning False matters: a caller that proceeds anyway is what
    turns a throttle into a longer throttle."""
    lim, fake = _limiter(1)
    import time
    fake.entries = [(time.time(), "fresh")]
    assert await lim.acquire(timeout=1.0) is False


async def test_concurrent_callers_cannot_both_take_the_last_slot():
    """Three containers share the budget; a read-then-write would let
    each see 299 and proceed."""
    lim, _ = _limiter(10)
    results = await asyncio.gather(*(lim.try_acquire(cost=4) for _ in range(5)))
    granted = [r for r in results if r[0]]
    assert len(granted) == 2, "10 budget / cost 4 = 2 winners, not 3"


# ── Failure policy ────────────────────────────────────────────────────


async def test_redis_down_allows_the_call_rather_than_blocking_the_app():
    lim = YahooRateLimiter(budget=5)
    lim._enabled = True

    class Broken:
        def register_script(self, _):
            async def boom(keys, args):
                raise ConnectionError("redis gone")
            return boom

    b = Broken()
    lim._client = b
    lim._script = b.register_script(None)
    granted, _, _ = await lim.try_acquire()
    assert granted is True, "a cache outage must not take the app down"


async def test_disabled_limiter_is_a_passthrough():
    lim, fake = _limiter(1)
    lim._enabled = False
    for _ in range(50):
        assert (await lim.try_acquire())[0] is True
    assert fake.entries == []


# ── Batch quote client: the cost reduction itself ─────────────────────


def test_sp500_costs_five_requests_not_thirteen_hundred():
    from app.data.sp500 import SP500

    tickers = [s["ticker"] for s in SP500]
    assert yahoo_quotes.request_cost(tickers) == 5
    # The old path: one download request per ticker, plus 2 per fast_info.
    assert len(tickers) + 1 + len(tickers) * 2 > 1300


@pytest.mark.parametrize("n,expected", [(0, 0), (1, 1), (100, 1), (101, 2), (250, 3)])
def test_request_cost_chunking(n, expected):
    assert yahoo_quotes.request_cost([f"T{i}" for i in range(n)]) == expected


def test_duplicate_symbols_do_not_inflate_cost():
    assert yahoo_quotes.request_cost(["AAPL", "aapl", "AAPL"]) == 1


def test_normalise_maps_yahoo_fields_onto_the_quote_shape():
    row = yahoo_quotes._normalise({
        "symbol": "AAPL", "regularMarketPrice": 336.92,
        "regularMarketPreviousClose": 336.67, "regularMarketChange": 0.25,
        "regularMarketChangePercent": 0.074, "regularMarketVolume": 2339758,
        "marketCap": 4917071183872, "fiftyTwoWeekHigh": 345.34,
        "fiftyTwoWeekLow": 200.0, "averageDailyVolume10Day": 34458600,
        "currency": "USD",
    })
    assert row["ticker"] == "AAPL"
    assert row["price"] == 336.92
    assert row["previous_close"] == 336.67
    assert row["change_pct"] == pytest.approx(0.074)
    assert row["avg_volume_10d"] == 34458600
    assert row["currency"] == "USD"


def test_change_is_derived_when_yahoo_omits_it():
    """Thin names come back with both prices but no change fields."""
    row = yahoo_quotes._normalise({
        "symbol": "THIN", "regularMarketPrice": 110.0,
        "regularMarketPreviousClose": 100.0,
    })
    assert row["change"] == pytest.approx(10.0)
    assert row["change_pct"] == pytest.approx(10.0)


def test_zero_previous_close_does_not_divide_by_zero():
    row = yahoo_quotes._normalise({
        "symbol": "NEW", "regularMarketPrice": 5.0,
        "regularMarketPreviousClose": 0,
    })
    assert row["change_pct"] is None


def test_non_finite_values_become_none():
    row = yahoo_quotes._normalise({
        "symbol": "X", "regularMarketPrice": float("nan"),
        "regularMarketVolume": float("inf"), "marketCap": "not-a-number",
    })
    assert row["price"] is None
    assert row["volume"] is None
    assert row["market_cap"] is None


async def test_get_many_skips_the_fetch_when_budget_is_unavailable(monkeypatch):
    """Must return empty rather than firing the requests anyway."""
    called = []
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _false())
    monkeypatch.setattr(
        yahoo_quotes, "_fetch_chunk_sync",
        lambda syms: called.append(syms) or [],
    )
    out = await yahoo_quotes.get_many(["AAPL", "MSFT"])
    assert out == {}
    assert called == [], "no provider call may be made without budget"


async def _false():
    return False


async def _true():
    return True


async def test_get_many_returns_rows_keyed_by_uppercase(monkeypatch):
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", lambda syms: [
        {"symbol": s, "regularMarketPrice": 10.0, "regularMarketPreviousClose": 9.0}
        for s in syms
    ])
    out = await yahoo_quotes.get_many(["aapl", "msft"])
    assert set(out) == {"AAPL", "MSFT"}
    assert out["AAPL"]["change_pct"] == pytest.approx(100 / 9)


async def test_get_many_omits_symbols_the_provider_did_not_return(monkeypatch):
    """Absence is the signal ticker health depends on."""
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", lambda syms: [
        {"symbol": s, "regularMarketPrice": 1.0, "regularMarketPreviousClose": 1.0}
        for s in syms if s != "NOTAREAL"
    ])
    out = await yahoo_quotes.get_many(["AAPL", "NOTAREAL"])
    assert "AAPL" in out
    assert "NOTAREAL" not in out


async def test_total_failure_raises_rather_than_looking_like_no_holdings(monkeypatch):
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())

    def boom(_syms):
        raise RuntimeError("provider down")

    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", boom)
    with pytest.raises(RuntimeError, match="All 1 Yahoo quote request"):
        await yahoo_quotes.get_many(["AAPL"])


async def test_partial_chunk_failure_returns_what_succeeded(monkeypatch):
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    calls = {"n": 0}

    def flaky(syms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first chunk failed")
        return [{"symbol": s, "regularMarketPrice": 1.0,
                 "regularMarketPreviousClose": 1.0} for s in syms]

    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", flaky)
    out = await yahoo_quotes.get_many([f"T{i}" for i in range(150)])
    assert 0 < len(out) < 150


# ── Deadlines ─────────────────────────────────────────────────────────
#
# A throttled Yahoo makes yfinance retry its cookie/crumb handshake
# internally, so an HTTP timeout bounds one attempt but not the call.
# Observed live: a recheck hung with no output. In a Celery worker that
# is worse than a failure — the task never returns and beat queues the
# next run behind it.


async def test_a_hanging_chunk_times_out_instead_of_blocking(monkeypatch):
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    monkeypatch.setattr(yahoo_quotes, "CHUNK_DEADLINE", 0.2)

    def never_returns(_syms):
        import time as _t
        _t.sleep(30)
        return []

    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", never_returns)

    import time as _t
    start = _t.monotonic()
    with pytest.raises(RuntimeError, match="All 1 Yahoo quote request"):
        await yahoo_quotes.get_many(["AAPL"])
    assert _t.monotonic() - start < 5, "must not wait on the hung thread"


async def test_total_deadline_stops_further_chunks(monkeypatch):
    """A slow provider must not let 5 chunks stack into minutes."""
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    monkeypatch.setattr(yahoo_quotes, "CHUNK_DEADLINE", 0.2)
    monkeypatch.setattr(yahoo_quotes, "TOTAL_DEADLINE", 0.5)
    calls = {"n": 0}

    def slow(syms):
        calls["n"] += 1
        import time as _t
        _t.sleep(5)
        return []

    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", slow)
    import time as _t
    start = _t.monotonic()
    try:
        await yahoo_quotes.get_many([f"T{i}" for i in range(500)])
    except RuntimeError:
        pass
    elapsed = _t.monotonic() - start
    assert elapsed < 5, f"took {elapsed:.1f}s"
    assert calls["n"] < 5, "stopped before attempting every chunk"


async def test_a_fast_chunk_is_unaffected_by_the_deadline(monkeypatch):
    monkeypatch.setattr(yahoo_quotes, "spend", lambda *a, **k: _true())
    monkeypatch.setattr(yahoo_quotes, "_fetch_chunk_sync", lambda syms: [
        {"symbol": s, "regularMarketPrice": 1.0, "regularMarketPreviousClose": 1.0}
        for s in syms
    ])
    out = await yahoo_quotes.get_many([f"T{i}" for i in range(250)])
    assert len(out) == 250
