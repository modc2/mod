"""
End-to-end mechanics of the market: subscribe → bet → settle → payout.

Every test runs against a temporary HOME so the live ledger is never touched,
and the oracle is stubbed — settlement must be exercised without depending on
a running build console.
"""
import json
import sys
import time
from pathlib import Path

import pytest

# Only the framework root goes on the path: putting the module directory
# first would shadow the framework's own `mod` package with this module's
# mod.py, and every `m.mod(...)` lookup would fail.
sys.path.insert(0, "/root/mod")

import mod as m  # noqa: E402


# Resolve the class ONCE, at import, while $HOME still points at the real
# tree — the framework locates a module by walking ~/mod, so a test that has
# already redirected HOME can no longer find anything.
Costmarket = m.mod("costmarket")


def _module_under_test():
    """The costmarket mod.py itself, for testing module-local helpers."""
    return sys.modules[Costmarket.__module__]


@pytest.fixture
def market(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cm = Costmarket()
    # No owner file in a fresh HOME ⇒ single-player mode, so owner actions
    # work without a key. That is the documented behaviour, not a bypass.
    return cm


def stub_oracle(cm, month, avg_usd, final=True, users=4):
    """Pin the settlement figure instead of reaching for the build console."""
    cm.oracle = lambda mo="": {
        "epoch": month,
        "final": final,
        "users": users,
        "avg_usd6_per_user": int(round(avg_usd * 1_000_000)),
        "avg_usd_per_user": f"{avg_usd:.2f}",
        "source": "stub",
    }


def past_month(cm, month):
    """Move an epoch's clock into the past so it can close and settle."""
    market = cm._market()
    ep = cm._ensure_epoch(month, market)
    ep["close_ts"] = int(time.time()) - 100
    ep["end_ts"] = int(time.time()) - 50
    cm._save(cm.market_path, market)
    return ep


# ── Membership ───────────────────────────────────────────────────

def test_subscription_below_the_minimum_is_refused(market):
    out = market.subscribe("0xa", amount_usd=1)
    assert "error" in out
    assert "minimum" in out["error"]


def test_subscription_becomes_the_stake_budget(market):
    out = market.subscribe("0xA", amount_usd=25)
    assert out["ok"] is True
    # Addresses are normalised, so 0xA and 0xa are one member.
    assert out["address"] == "0xa"
    assert out["stake_available_usd"] == "25.00"
    assert market.is_member("0xa")["active"] is True


def test_topping_up_adds_to_the_same_month(market):
    market.subscribe("0xa", amount_usd=10)
    out = market.subscribe("0xa", amount_usd=15)
    assert out["stake_available_usd"] == "25.00"


def test_betting_without_a_subscription_is_refused(market):
    out = market.bet("0xstranger", bucket=2, amount_usd=5)
    assert "subscribe" in out["error"]


# ── Betting ──────────────────────────────────────────────────────

def test_a_bet_spends_the_budget_and_lands_in_the_book(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=20)
    out = market.bet("0xa", bucket=2, amount_usd=8)
    assert out["ok"] is True
    assert out["stake_left_usd"] == "12.00"

    book = market.book(month)
    assert len(book["bets"]) == 1
    assert book["pool_usd"] == "8.00"
    # A parimutuel market's price IS the pool share.
    b2 = next(b for b in book["buckets"] if b["id"] == 2)
    assert b2["implied_pct"] == 100.0


def test_you_cannot_stake_more_than_you_paid_in(market):
    market.subscribe("0xa", amount_usd=10)
    out = market.bet("0xa", bucket=1, amount_usd=11)
    assert "exceeds" in out["error"]
    assert out["available_usd"] == "10.00"


def test_an_unknown_bucket_is_refused(market):
    market.subscribe("0xa", amount_usd=10)
    out = market.bet("0xa", bucket=99, amount_usd=1)
    assert "no bucket 99" in out["error"]


def test_betting_stops_at_the_midpoint_of_the_month(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=10)
    past_month(market, month)
    out = market.bet("0xa", bucket=1, amount_usd=5)
    assert "closed" in out["error"] or "awaiting" in out["error"]


def test_implied_odds_split_across_buckets(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=30)
    market.subscribe("0xb", amount_usd=10)
    market.bet("0xa", bucket=1, amount_usd=30)
    market.bet("0xb", bucket=3, amount_usd=10)
    ep = market.epoch(month)
    odds = {b["id"]: b["implied_pct"] for b in ep["buckets"]}
    assert odds[1] == 75.0
    assert odds[3] == 25.0
    # $1 in the minority bucket returns more than $1 in the crowd.
    payouts = {b["id"]: b["payout_per_usd"] for b in ep["buckets"]}
    assert payouts[3] > payouts[1]


# ── Settlement ───────────────────────────────────────────────────

def test_an_unfinished_month_cannot_settle(market):
    month = market.status()["epoch"]
    stub_oracle(market, month, 1.5)
    out = market.settle(month)
    assert "not over" in out["error"]


def test_a_non_final_oracle_reading_cannot_settle(market):
    month = market.status()["epoch"]
    past_month(market, month)
    stub_oracle(market, month, 1.5, final=False)
    out = market.settle(month)
    assert "final" in out["error"]


def test_the_pool_pays_the_winning_bucket_pro_rata(market):
    month = market.status()["epoch"]
    for who, amt in (("0xa", 30), ("0xb", 10), ("0xc", 10)):
        market.subscribe(who, amount_usd=amt)
    # Default edges: [.25, .5, 1, 2, 5, 10, 25] → bucket 3 is $1–$2.
    market.bet("0xa", bucket=3, amount_usd=30)   # right, big
    market.bet("0xb", bucket=3, amount_usd=10)   # right, small
    market.bet("0xc", bucket=6, amount_usd=10)   # wrong

    past_month(market, month)
    stub_oracle(market, month, 1.40)
    out = market.settle(month)

    assert out["ok"] is True
    assert out["winning_bucket"] == 3
    assert out["pool_usd"] == "50.00"
    assert out["fee_usd"] == "2.50"          # 5% of 50
    assert out["paid_out_usd"] == "47.50"
    # 47.50 split 30:10 → 35.625 / 11.875
    assert market.account("0xa")["balance_usd"] == "35.625"
    assert market.account("0xb")["balance_usd"] == "11.875"
    assert market.account("0xc")["balance_usd"] == "0.00"


def test_nobody_right_means_everybody_refunded(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=10)
    market.bet("0xa", bucket=0, amount_usd=10)   # under $0.25
    past_month(market, month)
    stub_oracle(market, month, 12.0)             # lands in a bucket nobody took
    out = market.settle(month)
    assert out["winners"] == 0
    assert out["fee_usd"] == "0.00"              # no windfall for the house
    assert market.account("0xa")["balance_usd"] == "10.00"


def test_the_open_ended_top_bucket_catches_everything_above(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=10)
    market.bet("0xa", bucket=7, amount_usd=10)   # "over $25"
    past_month(market, month)
    stub_oracle(market, month, 4000.0)
    out = market.settle(month)
    assert out["winning_bucket"] == 7


def test_settling_twice_is_refused(market):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=10)
    market.bet("0xa", bucket=3, amount_usd=10)
    past_month(market, month)
    stub_oracle(market, month, 1.5)
    assert market.settle(month)["ok"] is True
    assert "already settled" in market.settle(month)["error"]


def test_rounding_dust_goes_to_the_largest_winner_not_nowhere(market):
    month = market.status()["epoch"]
    # Three equal winners on a pool that doesn't divide evenly.
    for who in ("0xa", "0xb", "0xc"):
        market.subscribe(who, amount_usd=10)
        market.bet(who, bucket=3, amount_usd=10)
    past_month(market, month)
    stub_oracle(market, month, 1.5)
    out = market.settle(month)

    paid = sum(
        int(round(float(market.account(w)["balance_usd"]) * 1_000_000))
        for w in ("0xa", "0xb", "0xc")
    )
    expected = 30 * 1_000_000 - int(round(float(out["fee_usd"]) * 1_000_000))
    assert paid == expected  # every micro-dollar accounted for


# ── Ledger ───────────────────────────────────────────────────────

def test_withdrawal_cannot_exceed_the_balance(market):
    market.subscribe("0xa", amount_usd=10)
    out = market.withdraw("0xa", amount_usd=5)
    assert "error" in out


def test_leaderboard_ranks_by_net_not_by_volume(market):
    month = market.status()["epoch"]
    market.subscribe("0xwinner", amount_usd=10)
    market.subscribe("0xwhale", amount_usd=100)
    market.bet("0xwinner", bucket=3, amount_usd=10)
    market.bet("0xwhale", bucket=6, amount_usd=100)
    past_month(market, month)
    stub_oracle(market, month, 1.5)
    market.settle(month)

    board = market.leaderboard()
    assert board[0]["address"] == "0xwinner"
    assert board[0]["hit_rate"] == 100.0
    assert board[-1]["address"] == "0xwhale"
    assert board[-1]["net_usd"].startswith("-")


def test_buckets_are_frozen_once_money_is_on_the_table(market):
    month = market.status()["epoch"]
    assert market.set_buckets(month, [1, 2, 3])["ok"] is True
    market.subscribe("0xa", amount_usd=10)
    market.bet("0xa", bucket=1, amount_usd=10)
    out = market.set_buckets(month, [5, 10])
    assert "already has bets" in out["error"]


def test_ownership_is_claim_once_then_rotate_only_by_the_owner(market):
    assert market.set_owner("0xowner")["ok"] is True
    assert market.is_owner("0xowner") is True
    assert market.is_owner("0xsomeone") is False
    assert "error" in market.set_owner("0xthief")
    assert market.set_owner("0xnew", key="0xowner")["ok"] is True


def test_owner_only_actions_are_gated_once_an_owner_exists(market):
    month = market.status()["epoch"]
    market.set_owner("0xowner")
    past_month(market, month)
    stub_oracle(market, month, 1.5)
    assert "owner only" in market.settle(month)["error"]
    assert market.settle(month, key="0xowner")["ok"] is True


def test_month_bounds_match_the_oracles_calendar(market):
    _month_bounds = _module_under_test()._month_bounds

    start, end, days = _month_bounds("2026-02")
    assert days == 28
    assert end - start == 28 * 86400
    start, end, days = _month_bounds("2028-02")
    assert days == 29


def test_state_survives_a_restart(market, tmp_path, monkeypatch):
    month = market.status()["epoch"]
    market.subscribe("0xa", amount_usd=10)
    market.bet("0xa", bucket=2, amount_usd=4)

    fresh = Costmarket()
    assert fresh.book(month)["pool_usd"] == "4.00"
    assert fresh.account("0xa")["membership"]["stake_available_usd"] == "6.00"
