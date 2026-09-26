"""Caching and batch behaviour of the halal screener.

Separate from test_screening_and_options.py, which pins the *verdict*
logic. This file pins the *plumbing* — and the plumbing is where the
badge silently disappeared: screen_many used to commit from inside
asyncio.gather, which raises IllegalStateChangeError on SQLAlchemy's
AsyncSession. Because the UI renders nothing when a verdict is missing,
the batch endpoint 500'd on every cold cache and the failure was
invisible. These tests make that mode loud.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models.halal_compliance import HalalCompliance, HalalStatus
from app.services.halal_screener import CACHE_TTL, HalalScreenerService


def _info(**overrides) -> dict:
    base = {
        "quoteType": "EQUITY",
        "sector": "Technology",
        "industry": "Software - Infrastructure",
        "marketCap": 1_000_000_000.0,
        "totalDebt": 100_000_000.0,
        "totalCash": 100_000_000.0,
    }
    base.update(overrides)
    return base


def _stub(svc: HalalScreenerService, calls: list[str], delay: float = 0.0, **overrides):
    """Replace the provider fetch, recording every ticker asked for.

    The sleep is load-bearing in the concurrency tests: without it the
    coroutines finish in the order they were scheduled and never
    interleave, so a session-safety bug can hide.
    """
    def fake(ticker: str) -> dict:
        calls.append(ticker)
        if delay:
            import time
            time.sleep(delay)
        return _info(**overrides)

    svc._fetch_info = staticmethod(fake)  # type: ignore[method-assign]
    return svc


# ── The regression ────────────────────────────────────────────────────


async def test_screen_many_cold_cache_returns_every_ticker(db):
    """The exact shape of the bug: >1 miss in one batch. Must not raise."""
    tickers = [f"TK{i}" for i in range(12)]
    svc = _stub(HalalScreenerService(db), [], delay=0.01)

    rows = await svc.screen_many(tickers)

    assert [r.ticker for r in rows] == tickers
    assert all(r.status is HalalStatus.compliant for r in rows)


async def test_screen_many_persists_so_a_second_call_hits_cache(db):
    calls: list[str] = []
    svc = _stub(HalalScreenerService(db), calls)

    await svc.screen_many(["AAA", "BBB"])
    assert sorted(calls) == ["AAA", "BBB"]

    calls.clear()
    rows = await HalalScreenerService(db).screen_many(["AAA", "BBB"])
    assert calls == [], "second call should be served from the cache"
    assert len(rows) == 2


async def test_screen_many_mixed_hit_and_miss(db):
    calls: list[str] = []
    await _stub(HalalScreenerService(db), calls).screen_many(["AAA"])
    calls.clear()

    svc = _stub(HalalScreenerService(db), calls, delay=0.01)
    rows = await svc.screen_many(["AAA", "BBB", "CCC"])

    assert calls == ["BBB", "CCC"] or sorted(calls) == ["BBB", "CCC"]
    assert [r.ticker for r in rows] == ["AAA", "BBB", "CCC"]


async def test_duplicate_tickers_fetched_once_and_returned_once(db):
    """Portfolio and watchlist overlap. Two writes to one PK in a single
    batch would be a unique-constraint race, not just waste."""
    calls: list[str] = []
    svc = _stub(HalalScreenerService(db), calls)

    rows = await svc.screen_many(["AAA", "aaa", "AAA"])

    assert calls == ["AAA"]
    assert [r.ticker for r in rows] == ["AAA"]


async def test_lowercase_input_is_normalised(db):
    svc = _stub(HalalScreenerService(db), [])
    [row] = await svc.screen_many(["aapl"])
    assert row.ticker == "AAPL", "UI looks rows up by uppercase ticker"


async def test_empty_input_touches_no_provider(db):
    calls: list[str] = []
    assert await _stub(HalalScreenerService(db), calls).screen_many([]) == []
    assert calls == []


# ── TTL and force ─────────────────────────────────────────────────────


async def test_ttl_outlives_the_monthly_refresh_interval(db):
    """The monthly Celery job is what renews a verdict; if the TTL were
    shorter than a month the read path would refresh it first and the
    schedule would be decorative."""
    assert CACHE_TTL > timedelta(days=31)


async def test_stale_row_is_recomputed(db):
    calls: list[str] = []
    await _stub(HalalScreenerService(db), calls).screen_many(["AAA"])

    row = await db.get(HalalCompliance, "AAA")
    row.computed_at = datetime.now(UTC) - (CACHE_TTL + timedelta(hours=1))
    await db.commit()

    calls.clear()
    await _stub(HalalScreenerService(db), calls).screen_many(["AAA"])
    assert calls == ["AAA"]


async def test_force_refetches_a_fresh_row_and_updates_verdict(db):
    calls: list[str] = []
    await _stub(HalalScreenerService(db), calls).screen_many(["AAA"])
    assert (await db.get(HalalCompliance, "AAA")).status is HalalStatus.compliant

    calls.clear()
    # Same ticker, now carrying too much debt.
    svc = _stub(HalalScreenerService(db), calls, totalDebt=500_000_000.0)
    [row] = await svc.screen_many(["AAA"], force=True)

    assert calls == ["AAA"]
    assert row.status is HalalStatus.non_compliant
    assert (await db.get(HalalCompliance, "AAA")).status is HalalStatus.non_compliant


async def test_force_updates_in_place_rather_than_inserting(db):
    from sqlalchemy import func, select

    svc = _stub(HalalScreenerService(db), [])
    await svc.screen_many(["AAA"])
    await _stub(HalalScreenerService(db), []).screen_many(["AAA"], force=True)

    count = await db.scalar(select(func.count()).select_from(HalalCompliance))
    assert count == 1


# ── refresh_all (the Celery task's entry point) ───────────────────────


async def test_refresh_all_tallies_by_verdict(db):
    calls: list[str] = []
    svc = HalalScreenerService(db)

    def fake(ticker: str) -> dict:
        calls.append(ticker)
        if ticker == "BANK":
            return _info(industry="Banks - Diversified")
        if ticker == "NODATA":
            return _info(marketCap=None)
        return _info()

    svc._fetch_info = staticmethod(fake)  # type: ignore[method-assign]
    tally = await svc.refresh_all(["GOOD", "BANK", "NODATA"])

    assert tally == {"screened": 3, "compliant": 1, "non_compliant": 1, "unknown": 1}


async def test_refresh_all_ignores_a_fresh_cache(db):
    calls: list[str] = []
    await _stub(HalalScreenerService(db), calls).screen_many(["AAA"])
    calls.clear()

    await _stub(HalalScreenerService(db), calls).refresh_all(["AAA"])
    assert calls == ["AAA"]


# ── single-ticker path ────────────────────────────────────────────────


async def test_screen_one_caches_then_serves_from_cache(db):
    calls: list[str] = []
    svc = _stub(HalalScreenerService(db), calls)

    first = await svc.screen("AAA")
    assert first.status is HalalStatus.compliant

    calls.clear()
    await HalalScreenerService(db).screen("AAA")
    assert calls == []


async def test_a_failed_fetch_is_cached_as_unknown_not_as_a_pass(db):
    svc = HalalScreenerService(db)

    def boom(ticker: str):
        raise RuntimeError("provider down")

    svc._fetch_info = staticmethod(boom)  # type: ignore[method-assign]
    [row] = await svc.screen_many(["AAA"])

    assert row.status is HalalStatus.unknown
    assert row.reason == "Data fetch failed"


# ── Through the HTTP endpoint ─────────────────────────────────────────
#
# The service-level tests above would pass even if the route were wired
# wrong. These go through the real app: routing, the repeated-param list
# binding the frontend relies on, and the response shape the badge reads.


async def test_batch_endpoint_serves_a_multi_ticker_cold_cache(auth_api, db, monkeypatch):
    """The failing request in production: several tickers, empty cache."""
    client, _ = auth_api
    monkeypatch.setattr(
        HalalScreenerService, "_fetch_info", staticmethod(lambda t: _info())
    )
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    r = await client.get("/api/v1/halal/", params=[("tickers", t) for t in tickers])

    assert r.status_code == 200, r.text
    body = r.json()
    assert [row["ticker"] for row in body] == tickers
    assert all(row["status"] == "compliant" for row in body)


async def test_batch_endpoint_response_carries_the_fields_the_badge_uses(auth_api, monkeypatch):
    client, _ = auth_api
    monkeypatch.setattr(
        HalalScreenerService, "_fetch_info",
        staticmethod(lambda t: _info(industry="Gambling")),
    )
    r = await client.get("/api/v1/halal/", params=[("tickers", "AAA")])

    assert r.status_code == 200, r.text
    [row] = r.json()
    assert row["status"] == "non_compliant"
    assert row["reason"] == "Industry: Gambling"   # rendered in the tooltip


async def test_single_ticker_endpoint(auth_api, monkeypatch):
    client, _ = auth_api
    monkeypatch.setattr(
        HalalScreenerService, "_fetch_info", staticmethod(lambda t: _info())
    )
    r = await client.get("/api/v1/halal/AAA")
    assert r.status_code == 200, r.text
    assert r.json()["ticker"] == "AAA"


async def test_batch_endpoint_requires_auth(api):
    r = await api.get("/api/v1/halal/", params=[("tickers", "AAA")])
    assert r.status_code in (401, 403)
