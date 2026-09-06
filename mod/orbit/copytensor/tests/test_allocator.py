"""Portfolio allocator — the arithmetic, on fixtures, with no chain.

These pin the thing the module exists for: several traders copied at once,
each with its own TAO sleeve, blended into ONE book. The engine this replaced
ran a loop per copy that drove the whole portfolio to one target's
percentages, so the tests that matter most are the ones about sleeves NOT
interfering with each other.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.allocator import (  # noqa: E402
    MIN_SLEEVE_TAO, Sleeve, blend, plan, shares_of,
)


def book(**by_netuid):
    """A trader's positions in the shape chain.client hands us."""
    return {
        "positions": [{"netuid": int(n), "value_tao": v}
                      for n, v in by_netuid.items()],
        "total_value_tao": sum(by_netuid.values()),
    }


def sleeve(copy_id, alloc, shares, **kw):
    return Sleeve(copy_id=copy_id, target_ss58=f"5{copy_id}", alloc_tao=alloc,
                  shares=shares, **kw)


# ── shapes ───────────────────────────────────────────────────────

def test_shares_are_fractions_of_their_own_book():
    s = shares_of(book(**{"1": 75.0, "2": 25.0}))
    assert s == {1: 0.75, 2: 0.25}


def test_shares_ignore_a_disagreeing_total():
    """A book read mid-update can report a total its own rows don't sum to.
    Trusting the reported total would under- or over-deploy the sleeve."""
    raw = book(**{"1": 30.0, "2": 10.0})
    raw["total_value_tao"] = 999.0
    assert shares_of(raw) == {1: 0.75, 2: 0.25}


def test_empty_book_has_no_shares():
    assert shares_of(book()) == {}
    assert shares_of({"positions": [], "total_value_tao": 0}) == {}


# ── the point: sleeves compose ───────────────────────────────────

def test_two_traders_blend_at_their_own_sizes():
    """40τ on a trader who is all-in on SN1, 10τ on one split 50/50 across
    SN1 and SN2 — the book is 45τ SN1 / 5τ SN2, not 'whoever synced last'."""
    a = sleeve("a", 40.0, {1: 1.0})
    b = sleeve("b", 10.0, {1: 0.5, 2: 0.5})
    desired = blend([a, b])
    assert desired[1] == pytest.approx(45.0)
    assert desired[2] == pytest.approx(5.0)
    assert sum(desired.values()) == pytest.approx(50.0)


def test_a_sleeve_only_moves_its_own_money():
    """Doubling B's allocation must not change what A deployed. This is the
    regression for the old engine, where every copy rewrote the whole book."""
    a = sleeve("a", 40.0, {1: 1.0})
    small = blend([a, sleeve("b", 10.0, {2: 1.0})])
    big = blend([a, sleeve("b", 20.0, {2: 1.0})])
    assert small[1] == big[1] == pytest.approx(40.0)
    assert small[2] == pytest.approx(10.0)
    assert big[2] == pytest.approx(20.0)


def test_contributors_name_who_paid_for_each_subnet():
    p = plan([sleeve("a", 40.0, {1: 1.0}), sleeve("b", 10.0, {1: 0.5, 2: 0.5})],
             current={}, free_tao=100.0)
    row = next(r for r in p.rows if r.netuid == 1)
    assert row.contributors == {"a": 40.0, "b": 5.0}


# ── the diff ─────────────────────────────────────────────────────

def test_plan_buys_the_gap_and_sells_the_excess():
    p = plan([sleeve("a", 50.0, {1: 0.5, 2: 0.5})],
             current={1: 5.0, 2: 40.0}, free_tao=50.0, threshold_pct=1.0)
    moves = {r.netuid: (r.action, round(r.amount_tao, 4)) for r in p.trades}
    assert moves[1] == ("stake", 20.0)
    assert moves[2] == ("unstake", 15.0)


def test_drift_inside_the_band_is_left_alone():
    """A 5% band on a 100τ book means nothing under 5τ moves — otherwise the
    engine churns fees chasing noise."""
    p = plan([sleeve("a", 100.0, {1: 1.0})],
             current={1: 97.0}, free_tao=10.0, threshold_pct=5.0)
    assert p.trades == []
    assert p.band_tao == pytest.approx(5.0)


def test_an_exit_is_never_left_as_a_stub():
    """A subnet nobody targets any more is sold in full even if the leftover
    is smaller than the drift band."""
    p = plan([sleeve("a", 100.0, {1: 1.0})],
             current={1: 100.0, 7: 0.4}, free_tao=0.0, threshold_pct=5.0)
    exit_row = next(r for r in p.trades if r.netuid == 7)
    assert (exit_row.action, exit_row.amount_tao) == ("unstake", 0.4)
    assert exit_row.contributors == {}


# ── honesty about money we don't have ────────────────────────────

def test_underfunding_scales_every_sleeve_equally():
    """Asking for 100τ with 50τ behind it fills both sleeves at half, rather
    than funding the first trader and silently starving the second."""
    p = plan([sleeve("a", 75.0, {1: 1.0}), sleeve("b", 25.0, {2: 1.0})],
             current={}, free_tao=50.0, threshold_pct=1.0)
    assert p.scale == pytest.approx(0.5)
    assert p.desired[1] == pytest.approx(37.5)
    assert p.desired[2] == pytest.approx(12.5)
    assert any("scaled" in n for n in p.notes)


def test_staked_value_counts_as_deployable():
    """Money already staked can be moved between subnets, so a fully-invested
    book is not 'underfunded' just because its free balance is zero."""
    p = plan([sleeve("a", 100.0, {2: 1.0})],
             current={1: 100.0}, free_tao=0.0, threshold_pct=1.0)
    assert p.scale == pytest.approx(1.0)
    assert p.deployable_tao == pytest.approx(100.0)


def test_min_balance_is_reserved_from_free_tao():
    p = plan([sleeve("a", 50.0, {1: 1.0})],
             current={}, free_tao=50.0, min_balance_tao=10.0, threshold_pct=1.0)
    assert p.deployable_tao == pytest.approx(40.0)
    assert p.scale == pytest.approx(0.8)


# ── sleeves that can't be trusted this pass ──────────────────────

def test_an_unreadable_target_blocks_the_whole_pass():
    """A failed read must never look like 'they sold everything'.

    B's 50τ is invisible this pass, so the blend would understate the target
    by exactly that much and the diff would sell B's half of the book — then
    buy it back once the read recovered. The pass holds instead.
    """
    good = sleeve("a", 50.0, {1: 1.0})
    bad = sleeve("b", 50.0, {}, stale=True, error="rpc timeout")
    p = plan([good, bad], current={1: 50.0, 2: 50.0}, free_tao=0.0,
             threshold_pct=1.0)
    assert not bad.live
    assert p.trades == []
    assert p.blocked and "unreadable" in p.blocked
    assert any("rpc timeout" in n for n in p.notes)


def test_a_readable_pass_still_trades():
    """The guard above must not be so broad that nothing ever executes."""
    p = plan([sleeve("a", 50.0, {1: 1.0}), sleeve("b", 50.0, {2: 1.0})],
             current={1: 100.0}, free_tao=0.0, threshold_pct=1.0)
    assert p.blocked is None
    assert {r.netuid for r in p.trades} == {1, 2}


def test_dust_sleeve_is_reported_not_deployed():
    p = plan([sleeve("a", MIN_SLEEVE_TAO / 2, {1: 1.0})],
             current={}, free_tao=10.0)
    assert p.trades == []
    assert any("floor" in n for n in p.notes)


def test_a_target_holding_nothing_is_idle_not_an_error():
    p = plan([sleeve("a", 10.0, {})], current={}, free_tao=10.0)
    assert p.desired == {}
    assert any("holds nothing" in n for n in p.notes)


# ── guardrails ───────────────────────────────────────────────────

def test_denylist_keeps_a_subnet_out_of_the_target_book():
    p = plan([sleeve("a", 100.0, {1: 0.5, 2: 0.5})],
             current={}, free_tao=200.0, threshold_pct=1.0, subnet_denylist=[2])
    assert 2 not in p.desired
    assert p.desired[1] == pytest.approx(50.0)


def test_allowlist_drops_everything_else():
    p = plan([sleeve("a", 100.0, {1: 0.5, 2: 0.3, 3: 0.2})],
             current={}, free_tao=200.0, threshold_pct=1.0, subnet_allowlist=[1])
    assert set(p.desired) == {1}


def test_max_subnets_defers_the_smallest_moves():
    shares = {n: 0.1 for n in range(1, 11)}
    p = plan([sleeve("a", 100.0, shares)], current={}, free_tao=200.0,
             threshold_pct=0.1, max_subnets=3)
    assert len(p.trades) == 3
    assert any("deferred" in n for n in p.notes)


def test_no_copies_is_not_an_instruction_to_sell():
    """Deleting every copy leaves your stake where it is. Only an explicit
    zero allocation (below) means 'get me out'."""
    p = plan([], current={1: 10.0}, free_tao=5.0)
    assert p.requested_tao == 0
    assert p.trades == []
    assert p.blocked and "nothing is targeted" in p.blocked


def test_zeroing_a_lone_sleeve_does_exit_the_position():
    """The counterpart: a copy that still exists but is allocated 0τ is a
    deliberate 'stop following them', and the position is sold."""
    p = plan([sleeve("a", 0.0, {1: 1.0})], current={1: 10.0}, free_tao=0.0)
    assert p.blocked is None
    assert [(r.action, r.amount_tao) for r in p.trades] == [("unstake", 10.0)]
