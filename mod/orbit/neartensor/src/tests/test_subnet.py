"""
Unit tests for the dedicated Bittensor subnet layer. Everything here runs
offline: NEAR is faked, the chain is LocalChain in a temp dir. Live-network
behaviour is exercised separately via `m neartensor sn_task` / `sn_epoch`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ntsubnet import protocol, reward, consensus
from ntsubnet.chain import LocalChain, run_consensus
from ntsubnet.config import SubnetConfig
from ntsubnet.consensus.yuma import weighted_median as _weighted_median


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
    monkeypatch.setattr("ntsubnet.config.DATA_DIR", str(tmp_path))
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
    inc = run_consensus(state)        # no "consensus" field → default yuma
    # Median clip holds the pumped miner near the honest majority's view.
    assert inc["0"] > inc["1"]


def test_weighted_median():
    assert _weighted_median([(1, 0.1), (1, 0.5), (1, 0.9)]) == 0.5
    assert _weighted_median([(10, 0.1), (1, 0.9)]) == 0.1


# ── validator query_one exploit prevention ──────────────────────────────

def test_bad_sig_with_unprovable_hash_scores_zero_not_skip(cfg, monkeypatch):
    """Bad-sig response with an unprovable block_hash must score 0.0, not None."""
    from unittest.mock import patch, MagicMock
    from ntsubnet.validator import Validator

    monkeypatch.setattr("ntsubnet.validator.NearClient", lambda *a, **k: FakeNear())
    val = Validator(cfg)
    miner = {"uid": 0, "hotkey": "real-hotkey", "url": "http://fake-miner"}
    task_req = protocol.make_task("block_header")

    # "not-a-real-hash" causes FakeNear.block_by_hash to raise ValueError
    answer = {"block_hash": "not-a-real-hash", "height": 1,
              "prev_hash": "x", "epoch_id": "ep1", "timestamp_ns": 1}
    digest = protocol.answer_digest(task_req["task"], task_req["nonce"], answer)

    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {
        "answer": answer,
        "digest": digest,
        "hotkey": "wrong-hotkey",   # != miner["hotkey"] -> sig_ok=False
        "signature": "",
    }

    with patch("ntsubnet.validator.requests.post", return_value=fake_resp):
        result = val.query_one(miner, task_req)

    assert result["score"] == 0.0, f"expected 0.0 but got {result['score']!r}"
    assert result["score"] is not None  # not skipped


# ── UID recycling ───────────────────────────────────────────────────────

def test_recycled_uid_new_miner_gets_clean_ema(cfg, monkeypatch):
    """A new miner at a recycled UID must not inherit the previous miner's EMA."""
    from ntsubnet.validator import Validator

    near = FakeNear()
    monkeypatch.setattr("ntsubnet.validator.NearClient", lambda *a, **k: near)

    val = Validator(cfg)
    # Simulate a prior epoch where "old-hotkey" occupied UID 0 and built up EMA.
    val.scores["old-hotkey"] = 0.9

    chain = LocalChain(cfg)
    new_hk = "new-hotkey"
    chain.register(new_hk, role="miner")
    chain.serve(new_hk, "http://fake-miner")

    def fake_query(miner, task_req):
        answer = protocol.solve(task_req, near)
        return {"score": reward.score_response(
            truth=answer, answer=answer, anchor_height=FakeNear.HEIGHT,
            final_height=FakeNear.HEIGHT, elapsed=0.1, signature_ok=True),
            "task": task_req["task"], "elapsed": 0.1,
            "signature_ok": True, "correct": True}
    monkeypatch.setattr(val, "query_one", fake_query)

    report = val.epoch()
    uid = str(chain.metagraph()["neurons"][-1]["uid"])
    result = report["results"][uid]
    # EMA must bootstrap from None — equals raw epoch score, not the old 0.9.
    assert result["ema_score"] == pytest.approx(result["epoch_score"])
    assert result["ema_score"] != pytest.approx(0.9, abs=0.05)


# ── validator RPC outage ─────────────────────────────────────────────────

def test_rpc_outage_does_not_decay_ema(cfg, monkeypatch):
    """When all samples return score=None (unverifiable), EMA must not change."""
    from ntsubnet.validator import Validator

    monkeypatch.setattr("ntsubnet.validator.NearClient", lambda *a, **k: FakeNear())

    val = Validator(cfg)
    chain = LocalChain(cfg)
    hk = "miner-outage-test"
    chain.register(hk, role="miner")
    chain.serve(hk, "http://fake-miner")

    prior_ema = 0.75
    val.scores[hk] = prior_ema

    def fake_query_unverifiable(miner, task_req):
        return {"score": None, "error": "unverifiable: rpc down", "task": task_req["task"]}
    monkeypatch.setattr(val, "query_one", fake_query_unverifiable)

    report = val.epoch()
    assert val.scores[hk] == pytest.approx(prior_ema), \
        "EMA must not change when all samples are unverifiable"
    uid = str(chain.metagraph()["neurons"][-1]["uid"])
    assert report["results"][uid]["ema_score"] == pytest.approx(prior_ema)


# ── end-to-end offline epoch ────────────────────────────────────────────

def test_offline_epoch_scores_and_sets_weights(cfg, monkeypatch):
    """Full validator epoch against an in-process fake miner and fake NEAR."""
    from ntsubnet.validator import Validator

    near = FakeNear()
    monkeypatch.setattr("ntsubnet.validator.NearClient", lambda *a, **k: near)

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
    assert os.path.exists(os.path.join(cfg.data_dir, "subnets", "0.validator.json"))


# ── creating subnets ────────────────────────────────────────────────────

def test_genesis_exists_and_create_subnet(cfg):
    chain = LocalChain(cfg)
    assert [s["netuid"] for s in chain.subnets()] == [0]
    assert chain.info()["name"] == "neartensor"
    sn = chain.create_subnet("fast-headers", "winner", ["block_header"], owner="me")
    assert sn["netuid"] == 1 and sn["consensus"] == "winner"
    assert sn["tasks"] == ["block_header"]
    assert [s["name"] for s in chain.subnets()] == ["neartensor", "fast-headers"]
    # each subnet has its own metagraph
    LocalChain(cfg, 1).register("m1", role="miner")
    assert LocalChain(cfg, 1).info()["n"] == 1 and LocalChain(cfg, 0).info()["n"] == 0


def test_create_subnet_rejects_bad_input(cfg):
    chain = LocalChain(cfg)
    for args in [("",), ("x", "nope"), ("x", "yuma", ["nope"]), ("neartensor",)]:
        with pytest.raises(ValueError):
            chain.create_subnet(*args)
    with pytest.raises(KeyError):
        LocalChain(cfg, 7).register("m", role="miner")   # no such subnet


def test_subnet_cap(cfg):
    cfg.max_subnets = 2
    chain = LocalChain(cfg)
    chain.create_subnet("one")
    with pytest.raises(ValueError):
        chain.create_subnet("two")


def test_legacy_single_subnet_file_migrates(cfg):
    import json
    with open(os.path.join(cfg.data_dir, "subnet_chain.json"), "w") as f:
        json.dump({"netuid": 0, "block": 5, "neurons": {"m": {"uid": 0, "hotkey": "m",
                   "role": "miner", "stake": 1.0, "url": None}}, "weights": {},
                   "incentive": {}, "epochs": 5, "updated": 1}, f)
    info = LocalChain(cfg).info()
    assert info["n"] == 1 and info["epochs"] == 5 and info["consensus"] == "yuma"
    assert not os.path.exists(os.path.join(cfg.data_dir, "subnet_chain.json"))


# ── modular consensus ───────────────────────────────────────────────────

PUMP = {"v1": {"0": 0.9, "1": 0.1}, "v2": {"0": 0.9, "1": 0.1}, "v3": {"0": 0.0, "1": 1.0}}
EQUAL = {"v1": 1, "v2": 1, "v3": 1}


def test_consensus_rules_are_discovered():
    names = {r["name"] for r in consensus.available()}
    assert {"yuma", "mean", "winner"} <= names
    with pytest.raises(ValueError):
        consensus.get("nope")


def test_consensus_rules_differ_as_documented():
    y = consensus.get("yuma").run(PUMP, EQUAL)
    m = consensus.get("mean").run(PUMP, EQUAL)
    w = consensus.get("winner").run(PUMP, EQUAL)
    assert sum(y.values()) == pytest.approx(1) and sum(m.values()) == pytest.approx(1)
    assert m["1"] > y["1"]            # mean lets the pumper through, yuma clips it
    assert w == {"0": 1.0}            # winner takes all


def test_set_consensus_changes_incentive(cfg):
    chain = LocalChain(cfg)
    chain.register("v", role="validator")
    chain.set_weights("v", {"0": 0.6, "1": 0.4})
    assert chain.metagraph()["incentive"] == {"0": 0.6, "1": 0.4}
    chain.set_consensus("winner")
    out = chain.set_weights("v", {"0": 0.6, "1": 0.4})
    assert out["consensus"] == "winner" and out["incentive"] == {"0": 1.0}
    with pytest.raises(ValueError):
        chain.set_consensus("nope")


def test_validator_samples_only_subnet_tasks(cfg, monkeypatch):
    from ntsubnet.validator import Validator
    monkeypatch.setattr("ntsubnet.validator.NearClient", lambda *a, **k: FakeNear())
    LocalChain(cfg).create_subnet("gas", tasks=["gas_price"])
    val = Validator(cfg, 1)
    chain = LocalChain(cfg, 1)
    chain.register("m", role="miner")
    chain.serve("m", "http://fake")
    seen = []
    monkeypatch.setattr(val, "query_one",
                        lambda miner, req: seen.append(req["task"]) or {"score": 1.0})
    rep = val.epoch()
    assert set(seen) == {"gas_price"} and rep["netuid"] == 1
    assert os.path.exists(os.path.join(cfg.data_dir, "subnets", "1.validator.json"))
