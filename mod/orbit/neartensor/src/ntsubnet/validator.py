"""
Validator neuron — queries every serving miner with sampled attestation
tasks, re-verifies each answer against its OWN NEAR RPC view at the miner's
anchored block, scores correctness × freshness × latency, smooths with an
EMA across epochs, and sets weights on the chain (local file or subtensor).

One Validator works one subnet (netuid). One epoch is a plain synchronous
function (`Validator.epoch()`) so it can be invoked from the API/CLI for a
visible round; `run()` loops forever over EVERY subnet on the chain
(`PYTHONPATH=src python3 -m ntsubnet.validator`).
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import SubnetConfig
from .chain import get_chain
from .keys import get_keypair
from .near_client import NearClient
from . import protocol, reward


class Validator:
    def __init__(self, cfg=None, netuid=None):
        self.cfg = cfg or SubnetConfig.load()
        self.chain = get_chain(self.cfg, netuid)
        self.netuid = getattr(self.chain, "netuid", self.cfg.netuid)
        self.near = NearClient(self.cfg.near_rpcs, timeout=self.cfg.query_timeout)
        self.keypair = get_keypair(self.cfg, "validator")
        self.hotkey = self.keypair.ss58_address if self.keypair else "unsigned-validator"
        self.state_path = state_path(self.cfg, self.netuid)
        self.scores = self._load_scores()
        self.chain.register(self.hotkey, role="validator")

    # ── persistence ─────────────────────────────────────────────────────

    def _load_scores(self):
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return json.load(f).get("scores", {})
        return {}

    def _save_scores(self, epoch_report):
        with open(self.state_path, "w") as f:
            json.dump({"scores": self.scores, "hotkey": self.hotkey,
                       "last_epoch": epoch_report, "updated": int(time.time())}, f, indent=2)

    # ── one miner, one task ─────────────────────────────────────────────

    def query_one(self, miner, task_req):
        t0 = time.time()
        try:
            r = requests.post(f"{miner['url']}/synapse", json=task_req,
                              timeout=self.cfg.query_timeout)
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            return {"score": 0.0, "error": f"unreachable: {e}"}
        elapsed = time.time() - t0
        answer = resp.get("answer")
        if not isinstance(answer, dict):
            return {"score": 0.0, "error": "malformed answer"}

        digest = protocol.answer_digest(task_req["task"], task_req["nonce"], answer)
        sig_ok = (digest == resp.get("digest")) and protocol.verify_signature(
            resp.get("hotkey", ""), digest, resp.get("signature", ""))
        # A miner must answer as the hotkey it registered under.
        if resp.get("hotkey") != miner["hotkey"]:
            sig_ok = False
        # A miner must answer about the account the validator asked about.
        if (task_req["task"] == "account_state" and
                answer.get("account_id") != task_req["params"].get("account_id")):
            sig_ok = False

        if not sig_ok:
            return {"score": 0.0, "error": "bad signature", "task": task_req["task"]}

        try:
            truth, final_height = protocol.ground_truth(task_req, answer, self.near)
        except Exception as e:
            return {"score": None, "error": f"unverifiable: {e}"}  # skip, don't punish
        if truth is None:
            return {"score": 0.0, "error": "no verifiable anchor", "task": task_req["task"]}
        try:
            anchor_h = protocol.anchored_height(answer, self.near)
        except Exception:
            anchor_h = final_height  # freshness=0, but still score correctness+latency

        s = reward.score_response(
            truth=truth, answer=answer, anchor_height=anchor_h,
            final_height=final_height, elapsed=elapsed, signature_ok=sig_ok,
            max_lag=self.cfg.max_height_lag, latency_target=self.cfg.latency_target)
        return {"score": s, "task": task_req["task"], "elapsed": round(elapsed, 3),
                "signature_ok": sig_ok, "correct": reward.correctness(truth, answer) == 1.0}

    # ── one epoch ───────────────────────────────────────────────────────

    def epoch(self):
        miners = self.chain.miners()
        report = {"validator": self.hotkey, "network": self.cfg.network,
                  "netuid": self.netuid,
                  "miners": len(miners), "results": {}, "at": int(time.time())}
        if not miners:
            report["note"] = "no serving miners on the metagraph"
            self._save_scores(report)
            return report

        task_names = [t for t in self.chain.info()["tasks"] if t in protocol.TASKS]
        for miner in miners:
            uid = str(miner["uid"])
            hk = miner["hotkey"]
            reqs = [protocol.make_task(task_names[i % len(task_names)],
                                       near_network=self.cfg.near_network)
                    for i in range(self.cfg.sample_size)]
            # samples run in parallel: NEAR RPC round-trips dominate an epoch
            with ThreadPoolExecutor(max_workers=max(1, len(reqs))) as pool:
                results = list(pool.map(lambda r: self.query_one(miner, r), reqs))
            scored = [r["score"] for r in results if r["score"] is not None]  # None = unverifiable, skipped
            total, n = sum(scored), len(scored)
            epoch_score = total / n if n else 0.0
            if n:
                self.scores[hk] = reward.ema(self.scores.get(hk), epoch_score,
                                             self.cfg.ema_alpha)
            ema = self.scores.get(hk)
            report["results"][uid] = {"hotkey": hk,
                                      "epoch_score": round(epoch_score, 4),
                                      "ema_score": round(ema, 4) if ema is not None else None,
                                      "samples": results}

        weights = reward.normalize_weights(
            {str(m["uid"]): self.scores.get(m["hotkey"], 0.0) for m in miners})
        report["weights"] = weights
        if weights:
            try:
                report["set_weights"] = self.chain.set_weights(self.hotkey, weights)
            except Exception as e:
                report["set_weights"] = {"ok": False, "error": str(e)}
        self._save_scores(report)
        return report



def state_path(cfg, netuid):
    """Per-subnet EMA scores. The single-subnet era's file becomes netuid 0's."""
    path = os.path.join(cfg.data_dir, "subnets", f"{netuid}.validator.json")
    legacy = os.path.join(cfg.data_dir, "validator_state.json")
    if netuid == 0 and os.path.exists(legacy) and not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.replace(legacy, path)
    return path


def run(cfg=None):
    """Validate every subnet on the chain, forever."""
    cfg = cfg or SubnetConfig.load()
    vals = {}
    print(f"validator loop ({cfg.network}), epoch every {cfg.epoch_seconds}s")
    while True:
        for sn in get_chain(cfg).subnets():
            try:
                v = vals.get(sn["netuid"]) or vals.setdefault(sn["netuid"], Validator(cfg, sn["netuid"]))
                rep = v.epoch()
                print(f"netuid {sn['netuid']} ({sn['name']}): {rep['miners']} miners, "
                      f"weights={rep.get('weights', {})}")
            except Exception as e:
                print(f"netuid {sn['netuid']} epoch failed: {e}")
        time.sleep(cfg.epoch_seconds)


if __name__ == "__main__":
    run()
