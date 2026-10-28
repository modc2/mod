"""The copy engine over fake targets — no chain, no wallet, no bt.

The allocator tests pin the arithmetic. These pin the wiring around it: that
a set of copies becomes a set of sized sleeves, that ONE trade list comes out
of the whole book, and that the trade tape records which sleeves paid.
"""

import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import Database  # noqa: E402
from src.engine.copier import CopyEngine  # noqa: E402
from src.engine.safety import SafetyManager  # noqa: E402


class Pos:
    def __init__(self, netuid, value_tao):
        self.netuid, self.value_tao = netuid, value_tao


class Book:
    def __init__(self, **by_netuid):
        self.positions = [Pos(int(n), v) for n, v in by_netuid.items()]
        self.total_value_tao = sum(by_netuid.values())


class FakeClient:
    """Books by address, plus a record of every stake/unstake asked for."""

    def __init__(self, books, balance=1000.0):
        self.books = books
        self.balance = balance
        self.calls = []
        self.unreadable = set()

    def get_stake_for_coldkey(self, ss58):
        if ss58 in self.unreadable:
            raise RuntimeError("rpc timeout")
        return self.books.get(ss58, Book())

    def get_balance(self, ss58):
        return self.balance

    def get_block(self):
        return 5_000_000

    def stake(self, wallet, hotkey, netuid, amount):
        self.calls.append(("stake", netuid, round(amount, 6)))
        return f"0xstake{netuid}"

    def unstake(self, wallet, hotkey, netuid, amount):
        self.calls.append(("unstake", netuid, round(amount, 6)))
        return f"0xunstake{netuid}"


def fake_wallet(ss58="5OURS"):
    return types.SimpleNamespace(
        coldkey=types.SimpleNamespace(ss58_address=ss58),
        hotkey=types.SimpleNamespace(ss58_address="5HOTKEY"),
    )


@pytest.fixture
def engine(tmp_path):
    """An engine whose safety limits are wide open — the guardrails have
    their own tests, and here they would just mask the allocation."""
    db = Database(str(tmp_path / "t.db"))
    safety = SafetyManager({
        "max_tao_per_tx": 10_000, "daily_limit_tao": 10_000,
        "min_balance_tao": 0, "cooldown_sec": 0, "max_subnets": 64,
    })
    books = {
        "5ALICE": Book(**{"1": 100.0}),              # all-in on SN1
        "5BOB": Book(**{"1": 50.0, "2": 50.0}),      # half SN1, half SN2
        "5OURS": Book(),                             # we hold nothing yet
    }
    client = FakeClient(books)
    eng = CopyEngine(client, db, safety)
    eng.set_wallet(fake_wallet())
    return eng, db, client


def add_copy(db, target, alloc, **cfg):
    base = {"our_hotkey": "5HOTKEY", "alloc_tao": alloc,
            "rebalance_threshold_pct": 1.0, "poll_interval_sec": 300}
    base.update(cfg)
    return db.insert_copy(target_ss58=target, config=base)


# ── many traders, one book ───────────────────────────────────────

def test_two_copies_produce_one_coherent_trade_list(engine):
    """40τ on Alice (all SN1) + 10τ on Bob (half SN1, half SN2) = 45τ SN1
    and 5τ SN2. Under the old per-copy loops these were two passes that each
    rewrote the whole book."""
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    add_copy(db, "5BOB", 10.0)

    eng.sync_portfolio()

    assert sorted(client.calls) == [("stake", 1, 45.0), ("stake", 2, 5.0)]


def test_resizing_one_sleeve_does_not_disturb_the_other(engine):
    """The regression that matters: Bob going 10τ -> 30τ buys 10τ more SN1
    and 10τ more SN2, and leaves Alice's 40τ of SN1 exactly where it is."""
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    bob = add_copy(db, "5BOB", 10.0)
    eng.sync_portfolio()

    # The book now holds what the first pass bought.
    client.books["5OURS"] = Book(**{"1": 45.0, "2": 5.0})
    client.calls.clear()

    cfg = dict(db.get_copy(bob)["config"])
    cfg["alloc_tao"] = 30.0
    db.update_copy_config(bob, cfg)
    eng.sync_portfolio()

    # target: Alice 40 SN1 + Bob 15/15 -> 55 SN1, 15 SN2
    assert sorted(client.calls) == [("stake", 1, 10.0), ("stake", 2, 10.0)]


def test_the_tape_records_who_paid_for_each_move(engine):
    eng, db, client = engine
    a = add_copy(db, "5ALICE", 40.0)
    b = add_copy(db, "5BOB", 10.0)
    eng.sync_portfolio()

    sn1 = next(t for t in db.get_trades() if t["netuid"] == 1)
    assert sn1["contributors"] == {a: 40.0, b: 5.0}
    # Filed under the sleeve that paid the most, but visible from either.
    assert sn1["copy_id"] == a
    assert any(t["netuid"] == 1 for t in db.get_trades(copy_id=b))


def test_paused_copies_are_out_of_the_blend(engine):
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    b = add_copy(db, "5BOB", 10.0)
    db.update_copy(b, status="paused")

    eng.sync_portfolio()

    assert client.calls == [("stake", 1, 40.0)]


def test_a_deleted_copy_is_sold_out_of_the_book(engine):
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    b = add_copy(db, "5BOB", 10.0)
    eng.sync_portfolio()

    client.books["5OURS"] = Book(**{"1": 45.0, "2": 5.0})
    client.calls.clear()
    db.delete_copy(b)
    eng.sync_portfolio()

    # Alice alone wants 40τ of SN1 and nothing else.
    assert sorted(client.calls) == [("unstake", 1, 5.0), ("unstake", 2, 5.0)]


def test_an_unreadable_target_trades_nothing(engine):
    """One RPC blip must not liquidate the sleeve it can't see."""
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    add_copy(db, "5BOB", 10.0)
    client.books["5OURS"] = Book(**{"1": 45.0, "2": 5.0})
    client.unreadable.add("5BOB")

    eng.sync_portfolio()

    assert client.calls == []
    assert eng.plan_portfolio().blocked


def test_no_active_copies_trades_nothing(engine):
    eng, db, client = engine
    client.books["5OURS"] = Book(**{"1": 45.0})
    eng.sync_portfolio()
    assert client.calls == []


# ── sizing is honest about the wallet ────────────────────────────

def test_sleeves_are_scaled_to_what_the_wallet_can_back(engine):
    """Ask for 150τ with 100τ available and both sleeves fill at two thirds —
    the plan says so rather than filling Alice and starving Bob."""
    eng, db, client = engine
    client.balance = 100.0
    add_copy(db, "5ALICE", 100.0)
    add_copy(db, "5BOB", 50.0)

    plan = eng.plan_portfolio()

    assert plan.scale == pytest.approx(2 / 3)
    assert plan.desired[1] == pytest.approx(100 * 2 / 3 + 25 * 2 / 3)
    assert any("scaled" in n for n in plan.notes)


def test_plan_is_a_pure_read(engine):
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    eng.plan_portfolio()
    assert client.calls == []
    assert db.get_trades() == []


def test_the_plan_is_what_gets_executed(engine):
    """The dry run and the live pass must not be two code paths that can
    drift apart."""
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)
    add_copy(db, "5BOB", 10.0)

    planned = {(r.action, r.netuid, round(r.amount_tao, 6))
               for r in eng.plan_portfolio().trades}
    eng.sync_portfolio()

    assert planned == set(client.calls)


# ── safety still guards execution ────────────────────────────────

def test_per_tx_cap_shrinks_the_trade_and_its_receipt(engine):
    """Safety caps the size that goes out; the contributor split has to be
    scaled with it or the tape claims money that never moved."""
    eng, db, client = engine
    eng.safety.max_tao_per_tx = 10.0
    a = add_copy(db, "5ALICE", 40.0)
    eng.sync_portfolio()

    assert client.calls == [("stake", 1, 10.0)]
    sn1 = next(t for t in db.get_trades() if t["netuid"] == 1)
    assert sn1["contributors"] == {a: 10.0}


def test_a_failed_trade_is_recorded_not_swallowed(engine):
    eng, db, client = engine
    add_copy(db, "5ALICE", 40.0)

    def boom(*a, **k):
        raise RuntimeError("insufficient balance")
    client.stake = boom

    results = eng.sync_portfolio()

    assert [r.status for r in results] == ["failed"]
    tape = db.get_trades()
    assert tape[0]["status"] == "failed"
    assert "insufficient balance" in tape[0]["error"]
