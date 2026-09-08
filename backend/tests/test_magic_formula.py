"""Tests for the Greenblatt Magic Formula screen.

Two things carry the weight here.

The ranking itself must be right: higher metric is a BETTER rank (lower
number), the two legs are ranked independently, and the combined score
is their sum. Getting a sign backwards would silently invert the whole
screen — it would still produce a plausible-looking ordered list, just
of the worst names instead of the best.

And the exclusions must fire. Ranking a bank on EBIT/EV is meaningless
(debt is its raw material, not a financing choice), and a company with
negative capital employed after years of buybacks produces an explosive
or negative ROCE. Both would otherwise land near the top of the list.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.magic_formula import MagicFormulaRank
from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.magic_formula import Fundamentals, MagicFormulaService

USER_ID = 1


def _f(ticker, sector="Technology", ebit=100.0, cap=200.0, ev=1000.0, error=None):
    return Fundamentals(
        ticker=ticker, company_name=ticker, sector=sector,
        ebit=ebit, capital_employed=cap, enterprise_value=ev, error=error,
    )


def _svc(db=None) -> MagicFormulaService:
    return MagicFormulaService(db) if db is not None else MagicFormulaService.__new__(MagicFormulaService)


# ── Ranking direction — the invariant a sign error would break ────────


def test_higher_roce_gets_the_better_rank():
    rows = _svc().rank([
        _f("HI", ebit=100, cap=100),    # 100%
        _f("MID", ebit=50, cap=100),    #  50%
        _f("LO", ebit=10, cap=100),     #  10%
    ])
    by = {r["ticker"]: r for r in rows}
    assert by["HI"]["rank_roce"] == 1
    assert by["MID"]["rank_roce"] == 2
    assert by["LO"]["rank_roce"] == 3


def test_higher_earnings_yield_gets_the_better_rank():
    rows = _svc().rank([
        _f("CHEAP", ebit=100, ev=500),   # 20%
        _f("MID",   ebit=100, ev=1000),  # 10%
        _f("DEAR",  ebit=100, ev=5000),  #  2%
    ])
    by = {r["ticker"]: r for r in rows}
    assert by["CHEAP"]["rank_yield"] == 1
    assert by["MID"]["rank_yield"] == 2
    assert by["DEAR"]["rank_yield"] == 3


def test_combined_rank_is_the_sum_of_both_legs():
    rows = _svc().rank([
        _f("A", ebit=100, cap=200, ev=1000),   # roce 50%,  ey 10%
        _f("B", ebit=90,  cap=300, ev=600),    # roce 30%,  ey 15%
        _f("C", ebit=50,  cap=50,  ev=2000),   # roce 100%, ey 2.5%
    ])
    for r in rows:
        assert r["combined_rank"] == r["rank_roce"] + r["rank_yield"]


def test_balanced_and_lopsided_names_can_tie():
    """The formula's defining behaviour: a quality champion that is
    expensive, a cheap name of mediocre quality, and a balanced one can
    all land on the same score. Anything that broke this would mean the
    two legs weren't being weighted equally."""
    rows = _svc().rank([
        _f("BALANCED", ebit=100, cap=200, ev=1000),  # 2nd + 2nd = 4
        _f("CHEAP",    ebit=90,  cap=300, ev=600),   # 3rd + 1st = 4
        _f("QUALITY",  ebit=50,  cap=50,  ev=2000),  # 1st + 3rd = 4
    ])
    assert {r["combined_rank"] for r in rows} == {4}


def test_ties_share_the_better_rank():
    """Two identically cheap names should both get the better position
    rather than one being arbitrarily demoted."""
    rows = _svc().rank([
        _f("T1", ebit=100, cap=100, ev=1000),
        _f("T2", ebit=100, cap=100, ev=1000),
        _f("WORSE", ebit=10, cap=1000, ev=10_000),
    ])
    by = {r["ticker"]: r for r in rows}
    assert by["T1"]["rank_roce"] == by["T2"]["rank_roce"] == 1
    assert by["WORSE"]["rank_roce"] == 3


def test_best_overall_name_wins_outright():
    rows = _svc().rank([
        _f("BEST",  ebit=100, cap=100, ev=500),    # top on both legs
        _f("MID",   ebit=50,  cap=200, ev=1000),
        _f("WORST", ebit=10,  cap=500, ev=5000),
    ])
    ranked = sorted(
        (r for r in rows if not r["excluded"]), key=lambda r: r["combined_rank"]
    )
    assert ranked[0]["ticker"] == "BEST"
    assert ranked[0]["combined_rank"] == 2   # 1st + 1st


# ── Exclusions ────────────────────────────────────────────────────────


@pytest.mark.parametrize("sector", ["Financial Services", "financial services", "Utilities"])
def test_excluded_sectors_are_dropped(sector):
    """Greenblatt excludes both. EBIT/EV is meaningless for a bank, whose
    debt is raw material rather than a financing choice."""
    rows = _svc().rank([_f("X", sector=sector), _f("OK")])
    by = {r["ticker"]: r for r in rows}
    assert by["X"]["excluded"] is True
    assert "Excluded sector" in by["X"]["exclusion_reason"]
    assert by["X"]["combined_rank"] is None
    assert by["OK"]["excluded"] is False


def test_loss_making_company_excluded():
    rows = _svc().rank([_f("LOSS", ebit=-50), _f("OK")])
    by = {r["ticker"]: r for r in rows}
    assert by["LOSS"]["excluded"] is True
    assert "Non-positive EBIT" in by["LOSS"]["exclusion_reason"]


def test_negative_capital_employed_excluded():
    """Sustained buybacks can drive equity negative. ROCE then goes
    negative or explosive, and an unguarded screen would rank such a
    name at or near the top."""
    rows = _svc().rank([_f("BUYBACK", cap=-50), _f("OK")])
    by = {r["ticker"]: r for r in rows}
    assert by["BUYBACK"]["excluded"] is True
    assert "capital employed" in by["BUYBACK"]["exclusion_reason"]


def test_missing_inputs_excluded():
    rows = _svc().rank([
        _f("NOEBIT", ebit=None),
        _f("NOCAP", cap=None),
        _f("NOEV", ev=None),
        _f("OK"),
    ])
    by = {r["ticker"]: r for r in rows}
    for t in ("NOEBIT", "NOCAP", "NOEV"):
        assert by[t]["excluded"] is True, t
        assert by[t]["combined_rank"] is None
    assert by["OK"]["excluded"] is False


def test_fetch_error_excluded_with_reason():
    rows = _svc().rank([_f("BROKEN", error="HTTPError: 404"), _f("OK")])
    by = {r["ticker"]: r for r in rows}
    assert by["BROKEN"]["excluded"] is True
    assert "Data fetch failed" in by["BROKEN"]["exclusion_reason"]


def test_excluded_names_do_not_shift_ranks_of_the_rest():
    """Exclusions must be removed BEFORE ranking. If a bank were ranked
    and then filtered out, the surviving names would carry gaps and the
    combined scores would be inflated."""
    without = _svc().rank([_f("A", ebit=100, cap=100), _f("B", ebit=50, cap=100)])
    with_junk = _svc().rank([
        _f("A", ebit=100, cap=100),
        _f("BANK", sector="Financial Services", ebit=999, cap=1),
        _f("B", ebit=50, cap=100),
        _f("LOSS", ebit=-1),
    ])
    a_without = next(r for r in without if r["ticker"] == "A")
    a_with = next(r for r in with_junk if r["ticker"] == "A")
    assert a_without["combined_rank"] == a_with["combined_rank"]


def test_all_excluded_produces_no_ranks_without_crashing(_=None):
    rows = _svc().rank([_f("BANK", sector="Financial Services"), _f("LOSS", ebit=-1)])
    assert all(r["excluded"] for r in rows)
    assert all(r["combined_rank"] is None for r in rows)


def test_empty_input():
    assert _svc().rank([]) == []


# ── Derived values ────────────────────────────────────────────────────


def test_ratios_computed_correctly():
    rows = _svc().rank([_f("X", ebit=150.0, cap=600.0, ev=1500.0)])
    r = rows[0]
    assert r["roce"] == pytest.approx(0.25)            # 150/600
    assert r["earnings_yield"] == pytest.approx(0.10)  # 150/1500


# ── Universe assembly ─────────────────────────────────────────────────


async def _portfolio(db, broker=BrokerType.manual) -> Portfolio:
    p = Portfolio(user_id=USER_ID, name="P", broker=broker, currency="USD")
    db.add(p)
    await db.flush()
    return p


async def _position(db, portfolio, ticker, qty=10):
    db.add(Position(
        portfolio_id=portfolio.id, ticker=ticker,
        quantity=Decimal(str(qty)), avg_cost=Decimal("100"),
        currency="USD", opened_at=datetime.now(UTC),
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_universe_includes_sp500_plus_holdings_and_watchlist(db):
    p = await _portfolio(db)
    await _position(db, p, "MBG.DE")          # not in the index
    wl = Watchlist(user_id=USER_ID, name="Main")
    db.add(wl)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=wl.id, ticker="QUBT"))
    await db.flush()

    universe = await MagicFormulaService(db).build_universe()

    assert "AAPL" in universe          # from the S&P list
    assert "MBG.DE" in universe        # holding
    assert "QUBT" in universe          # watchlist
    assert len(universe) == len(set(universe))   # deduped


@pytest.mark.asyncio
async def test_universe_excludes_paper_positions(db):
    """Paper holdings are strategy artefacts, not names the user chose
    to follow, so they must not pull extra tickers into the screen."""
    paper = await _portfolio(db, broker=BrokerType.paper)
    await _position(db, paper, "ZZPAPERONLY")

    universe = await MagicFormulaService(db).build_universe()
    assert "ZZPAPERONLY" not in universe


# ── Read path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_screen_empty_before_first_run(db):
    out = await MagicFormulaService(db).get_screen()
    assert out["results"] == []
    assert out["computed_at"] is None


@pytest.mark.asyncio
async def test_get_screen_orders_by_combined_rank_and_tallies_exclusions(db):
    now = datetime.now(UTC)
    for tk, combined, excl, reason in [
        ("BEST", 4, False, None),
        ("MID", 20, False, None),
        ("WORST", 90, False, None),
        ("BANK", None, True, "Excluded sector: Financial Services"),
        ("UTIL", None, True, "Excluded sector: Utilities"),
        ("LOSS", None, True, "Non-positive EBIT"),
    ]:
        db.add(MagicFormulaRank(
            ticker=tk, combined_rank=combined, excluded=excl,
            exclusion_reason=reason, computed_at=now,
            ebit=Decimal("100"), enterprise_value=Decimal("1000"),
        ))
    await db.flush()

    out = await MagicFormulaService(db).get_screen(limit=10)

    assert [r["ticker"] for r in out["results"]] == ["BEST", "MID", "WORST"]
    assert out["ranked"] == 3
    assert out["universe"] == 6
    tally = {e["reason"]: e["count"] for e in out["exclusions"]}
    assert tally["Excluded sector"] == 2      # collapsed across sectors
    assert tally["Non-positive EBIT"] == 1


@pytest.mark.asyncio
async def test_get_screen_respects_limit(db):
    now = datetime.now(UTC)
    for i in range(10):
        db.add(MagicFormulaRank(
            ticker=f"T{i:02d}", combined_rank=i + 1, excluded=False, computed_at=now,
        ))
    await db.flush()

    out = await MagicFormulaService(db).get_screen(limit=3)
    assert [r["ticker"] for r in out["results"]] == ["T00", "T01", "T02"]
