"""
The module surface, against a live server.

The Rust suite covers the rules; this one covers the seam — that `mod.py`
hashes a commitment the server will accept, signs something it will recognise,
and that a whole round really does open, seal and settle over HTTP without
anybody nudging it. The round length is compressed to 20 seconds here, which
changes nothing about the phases: they are computed from the schedule either
way.

    python3 -m pytest tests -q          (or: m prerank/test)
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mod as prerank_mod  # noqa: E402

MICRO = 1_000_000
DAY = 30  # seconds per round, for the duration of these tests


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def binary() -> Path:
    for profile in ("release", "debug"):
        candidate = ROOT / "src" / "api" / "target" / profile / "prerank-api"
        if candidate.exists():
            return candidate
    pytest.skip("no prerank-api binary — run: cargo build --release (m prerank/build)")


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    """A server on a scratch chain, with this box as the owner."""
    from eth_account import Account

    state = tmp_path_factory.mktemp("prerank")
    port = free_port()
    owner = prerank_mod.Mod().address()
    env = {
        **os.environ,
        "PORT": str(port),
        "PRERANK_DIR": str(state),
        "PRERANK_OWNER": owner,
        "PRERANK_DAY_SECONDS": str(DAY),
        "PRERANK_REVEAL_BPS": "3000",   # reveals open 9s in
        "PRERANK_SEAL_BPS": "6000",     # grading opens 18s in, settles at 30s
        "PRERANK_QUORUM": "2",
    }
    proc = subprocess.Popen([str(binary())], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    m = prerank_mod.Mod()
    m.port = port
    os.environ["PRERANK_API"] = f"http://127.0.0.1:{port}"

    for _ in range(100):
        try:
            m.health()
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("the api never came up:\n" + proc.stdout.read().decode())

    yield m
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── the hashes three implementations have to agree on ────────────────

def test_the_commitment_hash_is_pinned():
    """Pinned in `types.rs` too. The reveal is a comparison against the Rust
    version, so these drifting apart would make every bet unopenable."""
    assert prerank_mod.commitment_hash(
        "2026-08-13", "0x00000000000000000000000000000000000000AB",
        "opus", 5_000_000, "deadbeef",
    ) == "dab3fbf2b2e4ba2b2558e1ae7851292fee965d6cc6ee5403162ac0bdb1560031"


def test_credits_convert_without_drifting():
    assert prerank_mod.round_credits(5) == 5 * MICRO
    assert prerank_mod.round_credits("2.5") == 2_500_000
    assert prerank_mod.round_credits(0.000001) == 1


# ── the surface ──────────────────────────────────────────────────────

def test_the_null_call_returns_the_card(market):
    card = market.info()
    assert card["name"] == "prerank"
    assert "bet" in card["fns"] and "verify" in card["fns"]
    assert card["live"]["cheat_proofing"], "the card states what it is claiming"


def test_health_and_a_fresh_chain_verify(market):
    assert market.health()["ok"] is True
    report = market.verify()
    assert report["ok"] is True
    assert report["chain"]["ok"] is True


def test_the_owner_is_this_box_and_a_stranger_cannot_act_as_one(market):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    assert market.status()["owner"] == market.address()

    # A stranger's perfectly valid signature over exactly the right message.
    # Nothing is wrong with it except whose it is.
    stranger = Account.from_key(b"\x09" * 32)
    signature = Account.sign_message(
        encode_defunct(text="prerank:roster:mine"), private_key=stranger.key,
    ).signature.hex()
    with pytest.raises(RuntimeError) as e:
        market._post("/roster", {
            "address": stranger.address,
            "signature": "0x" + signature.removeprefix("0x"),
            "models": ["mine"],
        })
    assert "only the owner" in str(e.value)

    # And a signature that is not a signature at all.
    with pytest.raises(RuntimeError):
        market._post("/roster", {
            "address": stranger.address, "signature": "0x" + "11" * 65,
            "models": ["mine"],
        })
    assert "mine" not in market.status()["roster"]


def test_setting_the_field_and_funding_an_account(market):
    market.roster("opus,sonnet,haiku")
    assert market.status()["roster"] == ["haiku", "opus", "sonnet"]

    market.grant("treasury", 500)
    market.grant(market.address(), 100)
    assert market.account()["balance"] == 100 * MICRO
    assert market.status()["treasury"] == 500 * MICRO


def test_metered_usage_becomes_a_weighted_position(market):
    market.meter(market.address(), "pytest")
    user = "0x00000000000000000000000000000000000000ab"
    out = market.usage(user=user, model="opus", spend=10, cost=6, id="pytest-1")
    assert out["margin"] == 4 * MICRO
    # First usage on a model: the margin is worth its face value.
    assert out["edge_units"] == 4 * MICRO

    # A second call, after opus has absorbed those credits, is worth less.
    later = market.usage(user=user, model="opus", spend=10, cost=6, id="pytest-2")
    assert later["edge_units"] < out["edge_units"]

    # And the same receipt cannot be banked twice.
    with pytest.raises(RuntimeError) as e:
        market.usage(user=user, model="opus", spend=10, cost=6, id="pytest-1")
    assert "already been posted" in str(e.value)


def test_a_bet_keeps_its_model_on_this_machine(market):
    # Round boundaries are absolute, so a test that just starts betting will
    # sooner or later land in a round that is already past its commit window.
    # Wait for one with room in it.
    round_id = _fresh_round(market)["id"]
    before = market.round(round_id)

    out = market.bet(model="opus", amount=5, round=round_id)
    assert out["ok"] is True
    assert out["model_kept_local"] == "opus"

    after = market.round(round_id)
    assert after["commitments"] == before["commitments"] + 1
    # The stake is visible and locked; the direction is not readable anywhere.
    assert after["staked_visible"] == before["staked_visible"] + 5 * MICRO
    assert after["pool"] is None
    assert all(b["units"] is None for b in after["books"])
    assert market.account()["locked"] >= 5 * MICRO

    stored = [b for b in market.bets() if b["commitment"] == out["commitment"]]
    assert stored and stored[0]["salt"], "the salt is kept here, not there"
    assert stored[0]["round"] == round_id


def test_a_full_round_opens_seals_and_settles(market):
    """The whole cadence, in compressed time and with nobody nudging it."""
    from eth_account import Account

    # Three graders: this box, which is also going to bet, and two strangers
    # holding nothing. The quorum has to come from the two strangers — the
    # box's own vote is recorded and then ignored, because a grader with a
    # position in the round it grades does not get to be the deciding one.
    market.grader(market.address(), "pytest-conflicted")
    strangers = [Account.from_key(bytes([n]) * 32) for n in (2, 3)]
    for i, who in enumerate(strangers):
        market.grader(who.address, f"pytest-{i}")

    # A round of its own, followed from the commit to the payout.
    round_id = _fresh_round(market)["id"]
    market.bet(model="opus", amount=5, round=round_id)
    balance_before = market.account()["balance"]

    _wait_for(market, round_id, "reveal")
    opened = market.reveal(round_id)
    assert any(o.get("ok") for o in opened), opened

    view = market.round(round_id)
    assert view["revealed"] >= 1
    assert view["books"], "the pools are public once the reveal starts"

    # Grading opens at the seal.
    _wait_for(market, round_id, "sealed")
    view = market.round(round_id)
    assert view["merkle_root"], "a sealed round publishes its commitment root"
    ranking = ["opus"] + [m for m in view["entrants"] if m != "opus"]

    # The box votes first, and its vote is refused a place in the count.
    assert market.attest(round_id, ranking)["counted"] is False

    # The two strangers agree, independently, and that is the quorum.
    for who in strangers:
        assert _grade(market, who, round_id, ranking)["counted"] is True

    # Settlement happens on its own when the grading window closes.
    _wait_for(market, round_id, "settled", timeout=DAY + 25)
    result = market.round(round_id)["result"]
    assert result["outcome"] == "paid"
    assert result["winner"] == "opus"
    # payouts + fee + dust is the pool, exactly.
    paid = sum(int(v) for v in result["payouts"].values())
    assert paid + result["fee"] + result["dust"] == result["total_pool"]
    assert market.account()["balance"] > balance_before
    assert market.account()["locked"] == 0


def test_the_bet_can_prove_it_was_in_the_sealed_set(market):
    mine = [b for b in market.bets() if b.get("revealed")]
    assert mine, "the round test should have left a revealed bet behind"
    proof = market.proof(mine[-1]["round"], mine[-1]["commitment"])
    assert proof["verifies"] is True
    assert proof["sealed"] is True
    assert proof["root"] == market.round(mine[-1]["round"])["merkle_root"]

    with pytest.raises(RuntimeError):
        market.proof(mine[-1]["round"], "00" * 32)


def test_the_log_still_folds_to_what_the_server_is_serving(market):
    report = market.verify()
    assert report["ok"] is True, report["problems"]
    assert report["problems"] == []
    assert report["conserved"] == report["issued"], "no credits invented or lost"

    chain = market.chain(0, 500)
    assert chain["length"] == report["events"]
    kinds = {e["kind"] for e in chain["entries"]}
    assert {"genesis", "round_opened", "committed", "revealed",
            "round_sealed", "attested", "round_settled"} <= kinds


def test_the_leaderboard_records_the_settled_round(market):
    board = market.leaderboard()
    assert board["settled_rounds"] >= 1
    winners = {row["model"]: row["wins"] for row in board["leaderboard"]}
    assert winners.get("opus", 0) >= 1


def _grade(market, account, round_id, ranking):
    """Submit a ranking as some other key — a grader that is not this box."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    digest = prerank_mod._sha256("|".join(["prerank:rank", round_id, ">".join(ranking)]))
    signature = Account.sign_message(
        encode_defunct(text=f"prerank:attest:{round_id}:{digest}"),
        private_key=account.key,
    ).signature.hex()
    return market._post("/attest", {
        "address": account.address,
        "signature": "0x" + signature.removeprefix("0x"),
        "round": round_id,
        "ranking": ranking,
    })


def _fresh_round(market, need=0.4):
    """Wait for a round that is open and has at least `need` of its commit
    window left, so a test is never racing the reveal."""
    deadline = time.time() + DAY * 3
    while time.time() < deadline:
        r = market.round()
        left = r["reveal_at"] - market.status()["now"]
        if r["phase"] == "open" and left >= (r["reveal_at"] - r["opens_at"]) * need:
            return r
        time.sleep(0.4)
    raise AssertionError("no round with room in it ever opened")


def _wait_for(market, round_id, phase, timeout=DAY + 10):
    """Wait for a round to reach a phase, without touching the clock."""
    order = ["open", "reveal", "sealed", "settled", "voided"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = market.round(round_id)["phase"]
        if order.index(current) >= order.index(phase):
            return current
        time.sleep(0.4)
    raise AssertionError(f"round {round_id} never reached {phase} (stuck at {current})")
