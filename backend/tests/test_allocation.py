"""Portfolio allocation — sector / asset-type / single-name distribution.

Every number here is a percentage of a total the service computes itself,
so a single mis-summed group silently rewrites the whole page. The cases
that matter are the ones where a holding should NOT be counted (no price,
no FX rate, zero quantity, paper portfolio) — those are the ways a
percentage quietly becomes wrong rather than absent.
"""

from datetime import UTC, datetime

import pytest

from app.models.portfolio import AssetType, BrokerType, Portfolio, Position
from app.models.ticker_profile import TickerProfile
from app.services.allocation import (
    SECTOR_TO_GICS,
    UNCLASSIFIED,
    AllocationService,
    _gics,
    _sp500_sector_weights,
)


# ── Fixtures / helpers ────────────────────────────────────────────────


async def _portfolio(db, user_id: int, name="Main", broker=BrokerType.manual, ccy="USD"):
    p = Portfolio(user_id=user_id, name=name, broker=broker, currency=ccy, is_active=True)
    db.add(p)
    await db.flush()
    return p


async def _position(db, pid, ticker, qty, price, ccy="USD", asset_type=AssetType.stock):
    pos = Position(
        portfolio_id=pid, ticker=ticker, asset_type=asset_type,
        quantity=qty, avg_cost=1.0, currency=ccy, current_price=price,
        opened_at=datetime.now(UTC),
    )
    db.add(pos)
    await db.flush()
    return pos


async def _profile(db, ticker, sector, name=None, quote_type="EQUITY"):
    db.add(TickerProfile(
        ticker=ticker.upper(), sector=sector, company_name=name,
        quote_type=quote_type, fetched_at=datetime.now(UTC),
    ))
    await db.flush()


def _no_network(monkeypatch):
    """Profiles are pre-seeded in these tests; a provider call would mean
    the cache lookup missed, which is itself a failure."""
    def boom(ticker):
        raise AssertionError(f"unexpected provider fetch for {ticker}")

    from app.services.ticker_profile import TickerProfileService
    monkeypatch.setattr(TickerProfileService, "_fetch", staticmethod(boom))


def _fx(monkeypatch, rates: dict[str, float]):
    from app.services.allocation import AllocationService as A

    async def fake(self, positions, base):
        return dict(rates)

    monkeypatch.setattr(A, "_fx_rates", fake)


@pytest.fixture
async def user(make_user):
    return await make_user("alloc@example.com")


# ── Sector vocabulary reconciliation ──────────────────────────────────


def test_yfinance_sectors_map_onto_gics_names():
    """The whole benchmark comparison hinges on this: yfinance says
    "Technology", the S&P table says "Information Technology"."""
    assert _gics("Technology") == "Information Technology"
    assert _gics("Consumer Cyclical") == "Consumer Discretionary"
    assert _gics("Consumer Defensive") == "Consumer Staples"
    assert _gics("Financial Services") == "Financials"
    assert _gics("Basic Materials") == "Materials"


def test_gics_mapping_is_case_and_space_insensitive():
    assert _gics("  technology  ") == "Information Technology"


def test_unknown_sector_is_passed_through_not_dropped():
    assert _gics("Space Mining") == "Space Mining"


def test_missing_sector_becomes_unclassified():
    assert _gics(None) == UNCLASSIFIED
    assert _gics("") == UNCLASSIFIED


def test_every_mapped_sector_exists_in_the_benchmark():
    """A mapping typo would silently produce benchmark_pct=None forever."""
    bench = set(_sp500_sector_weights())
    for gics in set(SECTOR_TO_GICS.values()):
        assert gics in bench, f"{gics} missing from S&P sector weights"


def test_benchmark_weights_normalise_to_100():
    """Raw weights in sp500.py sum to ~126%, so they can't be used as
    shares without normalising."""
    w = _sp500_sector_weights()
    assert sum(w.values()) == pytest.approx(100.0, abs=0.01)


# ── Core distribution ─────────────────────────────────────────────────


async def test_sector_split_sums_to_100_and_weights_by_value(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 10, 100.0)   # 1000 tech
    await _position(db, p.id, "MSFT", 10, 100.0)   # 1000 tech
    await _position(db, p.id, "XOM", 10, 200.0)    # 2000 energy
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "MSFT", "Technology")
    await _profile(db, "XOM", "Energy")

    out = await AllocationService(db).compute(user.id)

    assert out["total_value"] == 4000.0
    by = {g["name"]: g for g in out["by_sector"]}
    assert by["Information Technology"]["pct"] == 50.0
    assert by["Information Technology"]["count"] == 2
    assert by["Energy"]["pct"] == 50.0
    assert sum(g["pct"] for g in out["by_sector"]) == pytest.approx(100.0)


async def test_quantity_matters_not_just_price(db, user, monkeypatch):
    """A cheap large position must outweigh an expensive small one."""
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "PENNY", 10_000, 1.0)   # 10,000
    await _position(db, p.id, "PRICEY", 1, 500.0)     # 500
    await _profile(db, "PENNY", "Energy")
    await _profile(db, "PRICEY", "Technology")

    out = await AllocationService(db).compute(user.id)
    by = {g["name"]: g["pct"] for g in out["by_sector"]}
    assert by["Energy"] > by["Information Technology"]
    assert by["Energy"] == pytest.approx(95.24, abs=0.01)


async def test_benchmark_gap_is_percentage_points(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 1000.0)
    await _profile(db, "AAPL", "Technology")

    out = await AllocationService(db).compute(user.id)
    [tech] = [g for g in out["by_sector"] if g["name"] == "Information Technology"]
    assert tech["pct"] == 100.0
    assert tech["benchmark_pct"] == pytest.approx(28.07, abs=0.01)
    assert tech["vs_benchmark_pp"] == pytest.approx(100.0 - 28.07, abs=0.01)


async def test_unclassified_is_a_visible_slice_sorted_last(db, user, monkeypatch):
    """Dropping unsectored holdings would make every other share wrong."""
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "VOO", 1, 900.0, asset_type=AssetType.etf)
    await _position(db, p.id, "AAPL", 1, 100.0)
    await _profile(db, "VOO", None, quote_type="ETF")
    await _profile(db, "AAPL", "Technology")

    out = await AllocationService(db).compute(user.id)
    names = [g["name"] for g in out["by_sector"]]
    assert UNCLASSIFIED in names
    assert names[-1] == UNCLASSIFIED, "unclassified is a data note, not the headline"
    unc = next(g for g in out["by_sector"] if g["name"] == UNCLASSIFIED)
    assert unc["pct"] == 90.0
    assert unc["benchmark_pct"] is None
    assert sum(g["pct"] for g in out["by_sector"]) == pytest.approx(100.0)


async def test_asset_type_split(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 600.0, asset_type=AssetType.stock)
    await _position(db, p.id, "VOO", 1, 400.0, asset_type=AssetType.etf)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "VOO", None)

    out = await AllocationService(db).compute(user.id)
    by = {g["name"]: g["pct"] for g in out["by_asset_type"]}
    assert by == {"stock": 60.0, "etf": 40.0}
    assert all("benchmark_pct" not in g for g in out["by_asset_type"])


# ── Aggregation across portfolios ─────────────────────────────────────


async def test_same_ticker_in_two_portfolios_is_one_holding(db, user, monkeypatch):
    _no_network(monkeypatch)
    a = await _portfolio(db, user.id, "IBKR")
    b = await _portfolio(db, user.id, "ISA")
    await _position(db, a.id, "AAPL", 1, 600.0)
    await _position(db, b.id, "AAPL", 1, 400.0)
    await _profile(db, "AAPL", "Technology")

    out = await AllocationService(db).compute(user.id)
    assert out["holdings_count"] == 1
    [h] = out["holdings"]
    assert h["value"] == 1000.0
    assert h["pct"] == 100.0
    assert h["portfolios"] == ["IBKR", "ISA"]


async def test_paper_portfolios_excluded_by_default(db, user, monkeypatch):
    _no_network(monkeypatch)
    real = await _portfolio(db, user.id, "IBKR")
    paper = await _portfolio(db, user.id, "Sandbox", broker=BrokerType.paper)
    await _position(db, real.id, "AAPL", 1, 100.0)
    await _position(db, paper.id, "TSLA", 1, 900.0)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "TSLA", "Consumer Cyclical")

    out = await AllocationService(db).compute(user.id)
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]

    with_paper = await AllocationService(db).compute(user.id, include_paper=True)
    assert {h["ticker"] for h in with_paper["holdings"]} == {"AAPL", "TSLA"}


async def test_single_portfolio_scope_ignores_the_others(db, user, monkeypatch):
    _no_network(monkeypatch)
    a = await _portfolio(db, user.id, "A")
    b = await _portfolio(db, user.id, "B")
    await _position(db, a.id, "AAPL", 1, 100.0)
    await _position(db, b.id, "XOM", 1, 100.0)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "XOM", "Energy")

    out = await AllocationService(db).compute(user.id, portfolio_id=a.id)
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]
    assert out["portfolio_names"] == ["A"]


async def test_a_paper_portfolio_asked_for_explicitly_is_honoured(db, user, monkeypatch):
    """include_paper gates the aggregate view; naming one directly should
    still work, or the tab would render empty for a paper portfolio."""
    _no_network(monkeypatch)
    paper = await _portfolio(db, user.id, "Sandbox", broker=BrokerType.paper)
    await _position(db, paper.id, "TSLA", 1, 100.0)
    await _profile(db, "TSLA", "Consumer Cyclical")

    out = await AllocationService(db).compute(user.id, portfolio_id=paper.id)
    assert [h["ticker"] for h in out["holdings"]] == ["TSLA"]


async def test_another_users_portfolio_is_never_counted(db, make_user, monkeypatch):
    _no_network(monkeypatch)
    me = await make_user("me@example.com")
    them = await make_user("them@example.com")
    mine = await _portfolio(db, me.id, "Mine")
    theirs = await _portfolio(db, them.id, "Theirs")
    await _position(db, mine.id, "AAPL", 1, 100.0)
    await _position(db, theirs.id, "NVDA", 1, 900.0)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "NVDA", "Technology")

    out = await AllocationService(db).compute(me.id)
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]
    assert out["total_value"] == 100.0


async def test_inactive_portfolio_excluded(db, user, monkeypatch):
    _no_network(monkeypatch)
    live = await _portfolio(db, user.id, "Live")
    dead = await _portfolio(db, user.id, "Closed")
    dead.is_active = False
    await _position(db, live.id, "AAPL", 1, 100.0)
    await _position(db, dead.id, "XOM", 1, 900.0)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "XOM", "Energy")
    await db.flush()

    out = await AllocationService(db).compute(user.id)
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]


# ── Exclusions: the ways a percentage goes quietly wrong ──────────────


async def test_position_without_a_cached_price_is_reported_not_guessed(db, user, monkeypatch):
    """Falling back to cost basis would value a stale holding at what was
    paid for it and misstate every other share on the page."""
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 100.0)
    await _position(db, p.id, "NEWCO", 1, None)
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "NEWCO", "Technology")

    out = await AllocationService(db).compute(user.id)
    assert out["total_value"] == 100.0
    assert out["unpriced_tickers"] == ["NEWCO"]
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]


async def test_zero_quantity_position_excluded(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 100.0)
    await _position(db, p.id, "SOLD", 0, 500.0)
    await _profile(db, "AAPL", "Technology")

    out = await AllocationService(db).compute(user.id)
    assert [h["ticker"] for h in out["holdings"]] == ["AAPL"]


async def test_foreign_holding_converted_at_the_fx_rate(db, user, monkeypatch):
    _no_network(monkeypatch)
    _fx(monkeypatch, {"EUR": 1.10})
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 500.0, ccy="USD")
    await _position(db, p.id, "MBG.DE", 10, 50.0, ccy="EUR")   # 500 EUR → 550 USD
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "MBG.DE", "Consumer Cyclical")

    out = await AllocationService(db).compute(user.id)
    assert out["total_value"] == pytest.approx(1050.0)
    by = {h["ticker"]: h["value"] for h in out["holdings"]}
    assert by["MBG.DE"] == pytest.approx(550.0)


async def test_missing_fx_rate_excludes_rather_than_assuming_parity(db, user, monkeypatch):
    """Treating an unconvertible holding as 1:1 silently misweights it."""
    _no_network(monkeypatch)
    _fx(monkeypatch, {})   # FX fetch failed
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 100.0, ccy="USD")
    await _position(db, p.id, "MBG.DE", 1, 100.0, ccy="EUR")
    await _profile(db, "AAPL", "Technology")
    await _profile(db, "MBG.DE", "Consumer Cyclical")

    out = await AllocationService(db).compute(user.id)
    assert out["total_value"] == 100.0
    assert out["unpriced_tickers"] == ["MBG.DE"]


async def test_no_positions_returns_an_empty_shape_not_an_error(db, user, monkeypatch):
    _no_network(monkeypatch)
    await _portfolio(db, user.id)
    out = await AllocationService(db).compute(user.id)
    assert out["total_value"] == 0.0
    assert out["by_sector"] == []
    assert out["concentration"]["effective_holdings"] == 0.0


async def test_no_portfolios_at_all(db, user, monkeypatch):
    _no_network(monkeypatch)
    out = await AllocationService(db).compute(user.id)
    assert out["holdings_count"] == 0


# ── Concentration ─────────────────────────────────────────────────────


async def test_effective_holdings_reflects_imbalance_not_row_count(db, user, monkeypatch):
    """Four equal positions → 4. One dominant position → close to 1."""
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    for t in ("A", "B", "C", "D"):
        await _position(db, p.id, t, 1, 250.0)
        await _profile(db, t, "Technology")

    out = await AllocationService(db).compute(user.id)
    assert out["concentration"]["effective_holdings"] == pytest.approx(4.0, abs=0.05)
    assert out["concentration"]["top_1_pct"] == 25.0


async def test_one_dominant_position_collapses_effective_holdings(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "BIG", 1, 9100.0)
    await _profile(db, "BIG", "Technology")
    for t in ("A", "B", "C"):
        await _position(db, p.id, t, 1, 300.0)
        await _profile(db, t, "Energy")

    c = (await AllocationService(db).compute(user.id))["concentration"]
    assert c["top_1_pct"] == 91.0
    assert c["effective_holdings"] < 1.3
    assert c["hhi"] > 8000


async def test_top_n_are_cumulative_and_ordered(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    for i, v in enumerate([400.0, 300.0, 200.0, 100.0]):
        await _position(db, p.id, f"T{i}", 1, v)
        await _profile(db, f"T{i}", "Technology")

    c = (await AllocationService(db).compute(user.id))["concentration"]
    assert c["top_1_pct"] == 40.0
    assert c["top_3_pct"] == 90.0
    assert c["top_5_pct"] == 100.0   # fewer than 5 holdings — caps, no crash
    assert c["top_10_pct"] == 100.0


async def test_holdings_sorted_largest_first(db, user, monkeypatch):
    _no_network(monkeypatch)
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "SMALL", 1, 10.0)
    await _position(db, p.id, "BIG", 1, 90.0)
    await _profile(db, "SMALL", "Energy")
    await _profile(db, "BIG", "Technology")

    out = await AllocationService(db).compute(user.id)
    assert [h["ticker"] for h in out["holdings"]] == ["BIG", "SMALL"]


# ── Endpoint ──────────────────────────────────────────────────────────


async def test_allocation_endpoint_is_reachable_not_swallowed_by_id_route(
    auth_api, db, monkeypatch
):
    """"/allocation" sits beside "/{portfolio_id}"; if it were declared
    after it, FastAPI would try to parse "allocation" as an int and 422."""
    _no_network(monkeypatch)
    client, user = auth_api
    p = await _portfolio(db, user.id)
    await _position(db, p.id, "AAPL", 1, 100.0)
    await _profile(db, "AAPL", "Technology")

    r = await client.get("/api/v1/portfolios/allocation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_value"] == 100.0
    assert body["by_sector"][0]["name"] == "Information Technology"


async def test_allocation_endpoint_requires_auth(api):
    r = await api.get("/api/v1/portfolios/allocation")
    assert r.status_code in (401, 403)


async def test_allocation_endpoint_rejects_another_users_portfolio(
    auth_api, db, make_user, monkeypatch
):
    _no_network(monkeypatch)
    client, _ = auth_api
    other = await make_user("other@example.com")
    theirs = await _portfolio(db, other.id, "Theirs")

    r = await client.get(f"/api/v1/portfolios/allocation?portfolio_id={theirs.id}")
    assert r.status_code == 403


async def test_allocation_endpoint_404s_on_a_missing_portfolio(auth_api, monkeypatch):
    _no_network(monkeypatch)
    client, _ = auth_api
    r = await client.get("/api/v1/portfolios/allocation?portfolio_id=999999")
    assert r.status_code == 404
