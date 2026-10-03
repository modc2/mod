"""
Chain abstraction — the one seam between local-first operation and mainnet.

LocalChain      file-backed metagraph (data/subnet_chain.json, fcntl-locked):
                registration, axon serving, weights, and a Yuma-lite epoch
                (stake-weighted median-clipped consensus → incentive) all on
                this box. No wallet, no network, fully self-sustaining.

SubtensorChain  the same interface over the real bittensor SDK: burned
                registration, metagraph reads, serve_axon, set_weights on
                subtensor test/finney with the configured wallet.

Both sides speak {uid, hotkey, url, stake, incentive, weights} dicts, so the
miner and validator never know which chain they're on.
"""
import fcntl
import json
import os
import time


def get_chain(cfg):
    if cfg.is_local:
        return LocalChain(cfg)
    return SubtensorChain(cfg)


# ── Local, file-backed ──────────────────────────────────────────────────

class LocalChain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = os.path.join(cfg.data_dir, "subnet_chain.json")

    # -- storage --
    def _empty(self):
        return {"netuid": self.cfg.netuid, "block": 0, "neurons": {},
                "weights": {}, "incentive": {}, "epochs": 0, "updated": 0}

    def _read(self, f):
        f.seek(0)
        raw = f.read()
        return json.loads(raw) if raw.strip() else self._empty()

    def _mutate(self, fn):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            state = self._read(f)
            out = fn(state)
            state["updated"] = int(time.time())
            f.seek(0)
            f.truncate()
            json.dump(state, f, indent=2)
        return out

    def _view(self):
        if not os.path.exists(self.path):
            return self._empty()
        with open(self.path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            return self._read(f)

    # -- chain interface --
    def register(self, hotkey, role="miner", stake=1.0):
        def op(st):
            if hotkey in st["neurons"]:
                return st["neurons"][hotkey]
            uid = len(st["neurons"])
            st["neurons"][hotkey] = {"uid": uid, "hotkey": hotkey, "role": role,
                                     "stake": float(stake), "url": None,
                                     "registered_at": int(time.time())}
            return st["neurons"][hotkey]
        return self._mutate(op)

    def serve(self, hotkey, url):
        def op(st):
            n = st["neurons"].get(hotkey)
            if not n:
                raise KeyError(f"hotkey not registered: {hotkey}")
            n["url"] = url
            return n
        return self._mutate(op)

    def metagraph(self):
        st = self._view()
        return {"netuid": st["netuid"], "network": "local", "block": st["block"],
                "epochs": st["epochs"], "n": len(st["neurons"]),
                "neurons": sorted(st["neurons"].values(), key=lambda n: n["uid"]),
                "incentive": st["incentive"]}

    def miners(self):
        return [n for n in self.metagraph()["neurons"]
                if n["role"] == "miner" and n.get("url")]

    def set_weights(self, hotkey, weights):
        """weights: {uid(str|int): weight}. Triggers a Yuma-lite epoch."""
        def op(st):
            if hotkey not in st["neurons"]:
                raise KeyError(f"hotkey not registered: {hotkey}")
            st["weights"][hotkey] = {str(u): float(w) for u, w in weights.items()}
            st["block"] += 1
            st["epochs"] += 1
            st["incentive"] = _yuma_lite(st)
            return {"ok": True, "epoch": st["epochs"], "incentive": st["incentive"]}
        return self._mutate(op)


def _yuma_lite(state):
    """
    Stake-weighted, median-clipped consensus. For each miner uid, take every
    validator's weight, clip each at the stake-weighted median (so a lone
    validator can't pump a miner above what the majority attests), then
    average by validator stake. Normalized to sum 1.
    """
    validators = [(hk, w) for hk, w in state["weights"].items()
                  if hk in state["neurons"]]
    if not validators:
        return {}
    stakes = {hk: max(state["neurons"][hk].get("stake", 1.0), 1e-9)
              for hk, _ in validators}
    uids = sorted({u for _, w in validators for u in w})
    raw = {}
    for uid in uids:
        pairs = [(stakes[hk], w.get(uid, 0.0)) for hk, w in validators]
        med = _weighted_median(pairs)
        total_stake = sum(s for s, _ in pairs)
        raw[uid] = sum(s * min(v, med) for s, v in pairs) / total_stake
    total = sum(raw.values())
    return {u: v / total for u, v in raw.items()} if total > 0 else {}


def _weighted_median(pairs):
    """pairs: [(stake, value)] → value at 50% of cumulative stake."""
    pairs = sorted(pairs, key=lambda p: p[1])
    half = sum(s for s, _ in pairs) / 2.0
    acc = 0.0
    for s, v in pairs:
        acc += s
        if acc >= half:
            return v
    return pairs[-1][1] if pairs else 0.0


# ── Real subtensor via the bittensor SDK ────────────────────────────────

class SubtensorChain:
    def __init__(self, cfg):
        self.cfg = cfg
        self._st = None
        self._wallet = None

    @property
    def st(self):
        if self._st is None:
            import bittensor as bt
            self._st = bt.subtensor(network=self.cfg.network)
        return self._st

    @property
    def wallet(self):
        if self._wallet is None:
            import bittensor as bt
            self._wallet = bt.wallet(name=self.cfg.wallet_name,
                                     hotkey=self.cfg.wallet_hotkey)
        return self._wallet

    def register(self, hotkey=None, role="miner", stake=None):
        ok = self.st.burned_register(wallet=self.wallet, netuid=self.cfg.netuid)
        return {"ok": bool(ok), "netuid": self.cfg.netuid,
                "hotkey": self.wallet.hotkey.ss58_address}

    def serve(self, hotkey=None, url=None):
        import bittensor as bt
        host, port = url.rsplit(":", 1)
        host = host.split("://")[-1]
        ok = self.st.serve_axon(
            netuid=self.cfg.netuid,
            axon=bt.axon(wallet=self.wallet, external_ip=host, external_port=int(port)),
        )
        return {"ok": bool(ok), "url": url}

    def metagraph(self):
        mg = self.st.metagraph(netuid=self.cfg.netuid)
        neurons = []
        for uid in range(mg.n.item() if hasattr(mg.n, "item") else int(mg.n)):
            ax = mg.axons[uid]
            neurons.append({
                "uid": uid,
                "hotkey": mg.hotkeys[uid],
                "role": "validator" if bool(mg.validator_permit[uid]) else "miner",
                "stake": float(mg.S[uid]),
                "url": f"http://{ax.ip}:{ax.port}" if ax.is_serving else None,
                "incentive": float(mg.I[uid]),
            })
        return {"netuid": self.cfg.netuid, "network": self.cfg.network,
                "block": int(mg.block), "n": len(neurons), "neurons": neurons,
                "incentive": {str(n["uid"]): n["incentive"] for n in neurons}}

    def miners(self):
        return [n for n in self.metagraph()["neurons"]
                if n["role"] == "miner" and n.get("url")]

    def set_weights(self, hotkey=None, weights=None):
        uids = [int(u) for u in weights]
        vals = [float(weights[u]) for u in weights]
        ok, msg = self.st.set_weights(wallet=self.wallet, netuid=self.cfg.netuid,
                                      uids=uids, weights=vals,
                                      wait_for_inclusion=True)
        return {"ok": bool(ok), "message": str(msg)}
