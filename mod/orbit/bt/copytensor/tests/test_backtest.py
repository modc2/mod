"""Backtest engine — the arithmetic, on fixtures, with no chain or bt.

These pin the three things that make the replay honest: contributions add up
to the basket's PnL, a book worth nothing can't invent a return, and a leg
with no data is reported instead of counted as flat.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.backtest import DUST_TAO, MAX_LEGS, backtest_basket  # noqa: E402

HOUR = 3600
T0 = 1_700_000_000


def series(*values, start=T0, step=HOUR):
    return {"series": [{"t": start + i * step, "total_tao": v}
                       for i, v in enumerate(values)]}


def source(book):
    """dict ss58 → history payload, as fetch_history."""
    def fetch(ss58, hours):
        if ss58 not in book:
            raise RuntimeError("unknown trader")
        return book[ss58]
    return fetch


def test_single_trader_return_is_its_own():
    bt = backtest_basket(
        [{"ss58": "a", "weight": 1}], 24,
        source({"a": series(100.0, 110.0)}),
    )
    assert bt["ok"]
    assert bt["stats"]["total_return_pct"] == pytest.approx(10.0)
    assert bt["stats"]["end_tao"] == pytest.approx(110.0)


def test_contributions_sum_to_the_basket_return():
    bt = backtest_basket(
        [{"ss58": "a", "weight": 3}, {"ss58": "b", "weight": 1}], 24,
        source({
            "a": series(100.0, 120.0, 110.0),
            "b": series(50.0, 45.0, 60.0),
        }),
    )
    total = bt["stats"]["total_return_pct"]
    assert sum(r["contribution_pct"] for r in bt["per_trader"]) == pytest.approx(total, abs=1e-3)
    # Weights are normalized, not taken raw.
    assert bt["per_trader"][0]["weight"] + bt["per_trader"][1]["weight"] == pytest.approx(1.0)


def test_dust_book_is_skipped_outright():
    """A book that never holds anything isn't something you can mirror."""
    bt = backtest_basket(
        [{"ss58": "dust", "weight": 1}, {"ss58": "real", "weight": 1}], 24,
        source({
            "dust": series(0.000001, 0.2),
            "real": series(100.0, 101.0),
        }),
    )
    assert [s["ss58"] for s in bt["skipped"]] == ["dust"]
    assert bt["stats"]["total_return_pct"] == pytest.approx(1.0)
    assert bt["per_trader"][0]["weight"] == pytest.approx(1.0)  # weight redistributed


def test_book_funded_mid_window_counts_only_once_it_is_real():
    """0.000001τ → 1τ is a 100,000,000% "return". That step must not reach
    the basket; the leg starts counting from the step after it's funded."""
    bt = backtest_basket(
        [{"ss58": "new", "weight": 1}, {"ss58": "real", "weight": 1}], 24,
        source({
            "new": series(0.000001, 100.0, 110.0),
            "real": series(100.0, 100.0, 100.0),
        }),
    )
    assert not bt["skipped"]
    # Step 1: only `real` is live (flat). Step 2: both, +10% and 0% → +5%.
    assert bt["stats"]["total_return_pct"] == pytest.approx(5.0)


def test_missing_history_is_reported_not_counted_flat():
    bt = backtest_basket(
        [{"ss58": "a", "weight": 1}, {"ss58": "ghost", "weight": 1}], 24,
        source({"a": series(100.0, 110.0)}),
    )
    assert bt["ok"]
    assert [s["ss58"] for s in bt["skipped"]] == ["ghost"]
    # A flat ghost would have halved this to 5%.
    assert bt["stats"]["total_return_pct"] == pytest.approx(10.0)


def test_disabled_and_zero_weight_legs_are_ignored():
    bt = backtest_basket(
        [
            {"ss58": "a", "weight": 1},
            {"ss58": "b", "weight": 1, "enabled": False},
            {"ss58": "c", "weight": 0},
        ], 24,
        source({"a": series(100.0, 110.0), "b": series(100.0, 10.0),
                "c": series(100.0, 500.0)}),
    )
    assert [r["ss58"] for r in bt["per_trader"]] == ["a"]


def test_empty_basket_is_not_an_error():
    bt = backtest_basket([], 24, source({}))
    assert bt["ok"] is False
    assert bt["curve"] == []
    assert "no enabled traders" in bt["note"]


def test_wide_basket_is_truncated_out_loud():
    book = {f"t{i}": series(100.0, 100.0 + i) for i in range(MAX_LEGS + 5)}
    bt = backtest_basket(
        [{"ss58": f"t{i}", "weight": i + 1} for i in range(MAX_LEGS + 5)],
        24, source(book),
    )
    assert bt["truncated"] == {"kept": MAX_LEGS, "dropped": 5}
    assert len(bt["per_trader"]) == MAX_LEGS


def test_collapsing_book_does_not_overflow_the_annualization():
    """A book that goes to (almost) nothing in an hour compounds to
    infinity over a year — apy comes back null rather than raising."""
    bt = backtest_basket(
        [{"ss58": "a", "weight": 1}], 24,
        source({"a": series(1000.0, DUST_TAO + 0.01)}),
    )
    assert bt["ok"]
    assert bt["stats"]["apy_pct"] is None
    assert bt["stats"]["total_return_pct"] < -99
