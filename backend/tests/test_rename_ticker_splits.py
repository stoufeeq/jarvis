"""Split arithmetic in scripts/rename_ticker.py.

The rename is a ledger rewrite, so mistakes here corrupt cost basis
silently. Pure-function tests on the adjustment layer, with the SYTA →
CHAI 1-for-4 as the motivating case.
"""

import argparse
import importlib.util
import math
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.alert import AlertType
from app.models.portfolio import AssetType, TradeAction

_spec = importlib.util.spec_from_file_location(
    "rename_ticker", Path(__file__).resolve().parents[1] / "scripts" / "rename_ticker.py"
)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


def _row(tid, qty, price, day, action=TradeAction.buy, pid=1):
    return rt.LedgerRow(
        trade_id=tid, portfolio_id=pid, action=action, quantity=qty, price=price,
        traded_at=datetime(*day, 14, 30, tzinfo=UTC), currency="USD",
        asset_type=AssetType.stock, original_quantity=qty, original_price=price,
    )


SPLIT = rt.Split(old=4, new=1, effective=date(2025, 10, 7))


def test_parse_split():
    s = rt.parse_split("4:1@2025-10-07")
    assert s == SPLIT
    assert s.factor == 0.25
    assert "reverse split" in s.label
    assert "reverse" not in rt.parse_split("1:2@2024-01-01").label


@pytest.mark.parametrize("bad", ["4-1@2025-10-07", "4:1", "0:1@2025-10-07", "4:1@yesterday"])
def test_parse_split_rejects_garbage(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        rt.parse_split(bad)


def test_pre_split_trade_rescaled_total_cost_unchanged():
    [r] = [x for x in rt.apply_splits([_row(1, 1000, 1.20, (2025, 9, 1))], [SPLIT], round_up=False)]
    assert r.quantity == 250
    assert r.price == pytest.approx(4.80)
    assert r.quantity * r.price == pytest.approx(1000 * 1.20)
    assert r.adjusted and r.original_quantity == 1000


def test_trade_on_effective_day_is_already_post_split():
    rows = rt.apply_splits(
        [_row(1, 100, 8.0, (2025, 10, 7)), _row(2, 100, 8.0, (2025, 10, 6))], [SPLIT], round_up=False
    )
    by_id = {r.trade_id: r for r in rows}
    assert not by_id[1].adjusted and by_id[1].quantity == 100
    assert by_id[2].adjusted and by_id[2].quantity == 25


def test_round_up_books_zero_cost_fraction_once_per_portfolio():
    # 1001 shares → 250.25 → issuer rounds to 251
    rows = rt.apply_splits(
        [_row(1, 601, 1.0, (2025, 9, 1)), _row(2, 400, 1.0, (2025, 9, 2))], [SPLIT], round_up=True
    )
    synth = [r for r in rows if r.trade_id is None]
    assert len(synth) == 1
    assert synth[0].price == 0
    assert synth[0].quantity == pytest.approx(0.75)
    assert synth[0].traded_at.date() == SPLIT.effective
    rebuilt = rt._rebuild_from_trades(rows)
    assert rebuilt["quantity"] == pytest.approx(251)
    # Free fraction dilutes the average, cost outlay is unchanged.
    assert rebuilt["quantity"] * rebuilt["avg_cost"] == pytest.approx(1001 * 1.0)


def test_no_rounding_row_when_holding_is_whole():
    rows = rt.apply_splits([_row(1, 1000, 1.0, (2025, 9, 1))], [SPLIT], round_up=True)
    assert all(r.trade_id is not None for r in rows)


def test_no_rounding_row_when_flat_at_split():
    rows = rt.apply_splits(
        [_row(1, 1001, 1.0, (2025, 9, 1)), _row(2, 1001, 1.5, (2025, 9, 5), TradeAction.sell)],
        [SPLIT], round_up=True,
    )
    assert all(r.trade_id is not None for r in rows)
    assert rt._rebuild_from_trades(rows) is None


def test_forward_split_never_rounds():
    rows = rt.apply_splits(
        [_row(1, 3, 100.0, (2024, 1, 1))], [rt.Split(1, 2, date(2024, 6, 1))], round_up=True
    )
    assert len(rows) == 1 and rows[0].quantity == 6 and rows[0].price == 50


def test_chained_splits_compound_and_rescale_rounding_row():
    # SYTA also did 1:10 on 2024-12-27. A 2024 buy of 1005 shares:
    #   1:10 → 100.5, rounded up to 101 (0.5 free)
    #   1:4  → 25.25, rounded up to 26 (0.75 free); the 0.5 row becomes 0.125
    first = rt.Split(10, 1, date(2024, 12, 27))
    rows = rt.apply_splits([_row(1, 1005, 0.10, (2024, 11, 1))], [SPLIT, first], round_up=True)
    orig = next(r for r in rows if r.trade_id == 1)
    assert orig.quantity == pytest.approx(1005 / 40)
    assert orig.price == pytest.approx(0.10 * 40)
    assert orig.adjustments == [first.label, SPLIT.label]
    synth = sorted((r for r in rows if r.trade_id is None), key=lambda r: r.traded_at)
    assert [r.traded_at.date() for r in synth] == [first.effective, SPLIT.effective]
    assert synth[0].quantity == pytest.approx(0.125)
    assert synth[0].adjustments == [SPLIT.label]
    assert rt._rebuild_from_trades(rows)["quantity"] == pytest.approx(26)


def test_rounding_is_per_portfolio():
    rows = rt.apply_splits(
        [_row(1, 1001, 1.0, (2025, 9, 1), pid=1), _row(2, 1002, 1.0, (2025, 9, 1), pid=2)],
        [SPLIT], round_up=True,
    )
    synth = {r.portfolio_id: r.quantity for r in rows if r.trade_id is None}
    assert synth[1] == pytest.approx(0.75)
    assert synth[2] == pytest.approx(0.5)


def test_apply_splits_does_not_mutate_input():
    src = [_row(1, 1000, 1.0, (2025, 9, 1))]
    rt.apply_splits(src, [SPLIT], round_up=True)
    assert src[0].quantity == 1000 and src[0].adjustments == []


def test_alert_threshold_rescaled_only_if_set_before_split():
    def alert(day, kind=AlertType.price_above, v=2.0):
        return SimpleNamespace(threshold_value=v, alert_type=kind,
                               created_at=datetime(*day, tzinfo=UTC))

    assert rt._alert_threshold_after_splits(alert((2025, 9, 1)), [SPLIT]) == pytest.approx(8.0)
    assert rt._alert_threshold_after_splits(alert((2025, 10, 8)), [SPLIT]) == pytest.approx(2.0)
    assert rt._alert_threshold_after_splits(
        alert((2025, 9, 1), AlertType.pnl_threshold, 15.0), [SPLIT]) == pytest.approx(15.0)
    assert rt._alert_threshold_after_splits(
        alert((2025, 9, 1), AlertType.signal, None), [SPLIT]) is None


def test_no_splits_is_a_plain_rename():
    rows = rt.apply_splits([_row(1, 7, 61.45, (2024, 3, 1))], [], round_up=True)
    assert len(rows) == 1 and not rows[0].adjusted and rows[0].quantity == 7
