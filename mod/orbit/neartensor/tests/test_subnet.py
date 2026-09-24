"""
Unit tests for the dedicated Bittensor subnet layer. Everything here runs
offline: NEAR is faked, the chain is LocalChain in a temp dir. Live-network
behaviour is exercised separately via `m neartensor sn_task` / `sn_epoch`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subnet import protocol, reward
from subnet.chain import LocalChain, _yuma_lite, _weighted_median
from subnet.config import SubnetConfig


class FakeNear:
    """Deterministic stand-in for NearClient: a 3-block chain, one account."""
    HEIGHT = 1000

    def _header(self, height):
        return {"height": height, "hash": f"hash{height}",
                "prev_hash": f"hash{height - 1}", "epoch_id": "ep1",
                "timestamp_nanosec": str(height * 10**9)}

    def final_block(self):
        return {"header": self._header(self.HEIGHT)}

    def block_by_hash(self, block_hash):
        height = int(block_hash.replace("hash", ""))
        return {"header": self._header(height)}

    def gas_price(self, block_hash=None):
        return {"gas_price": "100000000"}

    def view_account(self, account_id, block_hash=None):
        return {"amount": "5" + "0" * 24, "locked": "0", "storage_usage": 182}

    def final_height(self):
        return self.HEIGHT


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr("subnet.config.DATA_DIR", str(tmp_path))
    return SubnetConfig.load({"network": "local", "near_rpcs": ["http://fake"]})


# ── protocol ────────────────────────────────────────────────────────────

def test_make_task_shapes():
    for name in protocol.TASKS:
        t = protocol.make_task(name)
        assert t["task"] == name and t["nonce"] and t["spec"] == protocol.SPEC_VERSION
    assert protocol.make_task("account_state")["params"]["account_id"]
    with pytest.raises(ValueError):
        protocol.make_task("nope")


def test_digest_binds_task_nonce_answer():
    ans = {"height": 1, "block_hash": "h"}
    d = protocol.answer_digest("block_header", "n1", ans)
    assert d == protocol.answer_digest("block_header", "n1", dict(ans))
    assert d != protocol.answer_digest("block_header", "n2", ans)
    assert d != protocol.answer_digest("gas_price", "n1", ans)
    assert d != protocol.answer_digest("block_header", "n1", {**ans, "height": 2})


def test_solve_and_ground_truth_agree_offline():
    near = FakeNear()
    for name in protocol.TASKS:
        req = protocol.make_task(name, account_id="alice.testnet")
        answer = protocol.solve(req, near)
        assert set(protocol.TASKS[name]) <= set(answer)
        truth, final_h = protocol.ground_truth(req, answer, near)
        assert truth == answer and final_h == FakeNear.HEIGHT
        assert protocol.anchored_height(answer, near) == FakeNear.HEIGHT


def test_ground_truth_catches_lies():
    near = FakeNear()
    req = protocol.make_task("block_header")
    answer = protocol.solve(req, near)
    answer["prev_hash"] = "forged"
    truth, _ = protocol.ground_truth(req, answer, near)
    assert truth != answer
    assert reward.correctness(truth, answer) == 0.0


# ── reward ──────────────────────────────────────────────────────────────

def test_correctness_gates_everything():
    assert reward.score_response(truth={"a": 1}, answer={"a": 2}, anchor_height=100,
                                 final_height=100, elapsed=0.1, signature_ok=True) == 0.0
    assert reward.score_response(truth={"a": 1}, answer={"a": 1}, anchor_height=100,
                                 final_height=100, elapsed=0.1, signature_ok=False) == 0.0


def test_perfect_answer_scores_one():
    s = reward.score_response(truth={"a": 1}, answer={"a": 1}, anchor_height=100,
                              final_height=100, elapsed=0.1, signature_ok=True)
    assert s == pytest.approx(1.0)


def test_freshness_and_latency_decay():
    fresh = reward.freshness(100, 100, max_lag=30)
    stale = reward.freshness(100, 115, max_lag=30)
    dead = reward.freshness(100, 200, max_lag=30)
    assert fresh == 1.0 and 0 < stale < 1 and dead == 0.0
    assert reward.latency_credit(1.0, 2.0) == 1.0
    assert 0 < reward.latency_credit(4.0, 2.0) < 1


def test_ema_and_normalize():
    assert reward.ema(None, 0.8, 0.3) == 0.8
    assert reward.ema(1.0, 0.0, 0.3) == pytest.approx(0.7)
    w = reward.normalize_weights({"0": 0.6, "1": 0.2, "2": 0.0})
    assert sum(w.values()) == pytest.approx(1.0) and "2" not in w
    assert reward.normalize_weights({"0": 0.0}) == {}


# ── LocalChain / Yuma-lite ──────────────────────────────────────────────

def test_localchain_register_serve_weights(cfg):
    chain = LocalChain(cfg)
    m0 = chain.register("miner-A", role="miner")
    chain.register("miner-A", role="miner")           # idempotent
    v0 = chain.register("val-1", role="validator", stake=10)
    assert m0["uid"] == 0 and v0["uid"] == 1
    assert chain.miners() == []                       # not serving yet
    chain.serve("miner-A", "http://127.0.0.1:50184")
    assert chain.miners()[0]["url"] == "http://127.0.0.1:50184"
    out = chain.set_weights("val-1", {"0": 1.0})
    assert out["ok"] and out["incentive"] == {"0": 1.0}
    mg = chain.metagraph()
    assert mg["n"] == 2 and mg["epochs"] == 1


def test_yuma_lite_clips_lone_pumper():
    # Three equal-stake validators; one tries to pump miner uid 1.
    state = {
        "neurons": {"v1": {"stake": 1}, "v2": {"stake": 1}, "v3": {"stake": 1}},
        "weights": {
            "v1": {"0": 0.9, "1": 0.1},
            "v2": {"0": 0.9, "1": 0.1},
            "v3": {"0": 0.0, "1": 1.0},   # pumper
        },
    }
    inc = _yuma_lite(state)
    # Median clip holds the pumped miner near the honest majority's view.
    assert inc["0"] > inc["1"]


def test_weighted_median():
    assert _weighted_median([(1, 0.1), (1, 0.5), (1, 0.9)]) == 0.5
    assert _weighted_median([(10, 0.1), (1, 0.9)]) == 0.1


# ── end-to-end offline epoch ────────────────────────────────────────────

def test_offline_epoch_scores_and_sets_weights(cfg, monkeypatch):
    """Full validator epoch against an in-process fake miner and fake NEAR."""
    from subnet.validator import Validator

    near = FakeNear()
    monkeypatch.setattr("subnet.validator.NearClient", lambda *a, **k: near)

    val = Validator(cfg)
    chain = LocalChain(cfg)
    miner_hk = "fake-miner-hotkey"
    chain.register(miner_hk, role="miner")
    chain.serve(miner_hk, "http://fake-miner")

    def fake_query(miner, task_req):        # honest miner, no HTTP
        answer = protocol.solve(task_req, near)
        return {"score": reward.score_response(
            truth=answer, answer=answer, anchor_height=FakeNear.HEIGHT,
            final_height=FakeNear.HEIGHT, elapsed=0.1, signature_ok=True),
            "task": task_req["task"], "elapsed": 0.1,
            "signature_ok": True, "correct": True}
    monkeypatch.setattr(val, "query_one", fake_query)

    report = val.epoch()
    uid = str(chain.metagraph()["neurons"][-1]["uid"])
    assert report["results"][uid]["epoch_score"] == pytest.approx(1.0)
    assert report["weights"] == {uid: 1.0}
    assert report["set_weights"]["ok"]
    assert os.path.exists(os.path.join(cfg.data_dir, "validator_state.json"))
