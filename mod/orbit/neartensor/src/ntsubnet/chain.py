"""
Chain abstraction — the one seam between local-first operation and mainnet.

LocalChain      file-backed subnets, one JSON file each (data/subnets/<netuid>.json,
                fcntl-locked). Anyone can create a subnet: it gets a netuid, a
                name, a task list and a consensus rule (see consensus/). Each
                subnet has its own registration, axon serving, weights and epoch.
                No wallet, no network, fully self-sustaining.

SubtensorChain  the same interface over the real bittensor SDK: burned
                registration, metagraph reads, serve_axon, set_weights on
                subtensor test/finney with the configured wallet.

Both sides speak {uid, hotkey, url, stake, incentive, weights} dicts, so the
miner and validator never know which chain they're on.
"""
import fcntl
import json
import os
import re
import time

from . import consensus
from .protocol import TASKS

GENESIS = {"name": "neartensor", "consensus": consensus.DEFAULT, "tasks": list(TASKS)}


def get_chain(cfg, netuid=None):
    if cfg.is_local:
        return LocalChain(cfg, netuid)
    return SubtensorChain(cfg)


# ── Local, file-backed ──────────────────────────────────────────────────

class LocalChain:
    def __init__(self, cfg, netuid=None):
        self.cfg = cfg
        self.netuid = cfg.netuid if netuid is None else int(netuid)
        self.dir = os.path.join(cfg.data_dir, "subnets")
        self.path = os.path.join(self.dir, f"{self.netuid}.json")
        os.makedirs(self.dir, exist_ok=True)
        _migrate_legacy(cfg.data_dir, self.dir)

    # -- storage --
    def _empty(self):
        if self.netuid != 0:          # only genesis springs into existence
            raise KeyError(f"no subnet with netuid {self.netuid}")
        return _new_state(0, created=int(time.time()), **GENESIS)

    def _read(self, f):
        f.seek(0)
        raw = f.read()
        return json.loads(raw) if raw.strip() else self._empty()

    def _mutate(self, fn):
        if self.netuid != 0 and not os.path.exists(self.path):
            raise KeyError(f"no subnet with netuid {self.netuid}")
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

    # -- subnets --
    def subnets(self):
        uids = {0} | {int(n[:-5]) for n in os.listdir(self.dir)
                      if re.fullmatch(r"\d+\.json", n)}
        return [LocalChain(self.cfg, u).info() for u in sorted(uids)]

    def create_subnet(self, name, consensus_rule=None, tasks=None, owner=""):
        name = (name or "").strip()
        if not re.fullmatch(r"[\w\- ]{1,32}", name):
            raise ValueError("name: 1-32 letters, digits, spaces, - or _")
        rule = consensus_rule or consensus.DEFAULT
        consensus.get(rule)                                   # validates
        tasks = list(tasks or TASKS)
        bad = [t for t in tasks if t not in TASKS]
        if bad:
            raise ValueError(f"unknown tasks {bad} — have: {', '.join(TASKS)}")
        with open(os.path.join(self.dir, ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.subnets()
            if len(existing) >= self.cfg.max_subnets:
                raise ValueError(f"subnet limit reached ({self.cfg.max_subnets})")
            if any(s["name"].lower() == name.lower() for s in existing):
                raise ValueError(f"a subnet named '{name}' already exists")
            netuid = max(s["netuid"] for s in existing) + 1
            state = _new_state(netuid, name=name, consensus=rule, tasks=tasks,
                               owner=owner, created=int(time.time()))
            with open(os.path.join(self.dir, f"{netuid}.json"), "w") as f:
                json.dump(state, f, indent=2)
        return LocalChain(self.cfg, netuid).info()

    def info(self):
        st = self._view()
        return {"netuid": st["netuid"], "name": st["name"], "owner": st.get("owner", ""),
                "consensus": st["consensus"], "tasks": st["tasks"],
                "created": st.get("created"), "n": len(st["neurons"]),
                "epochs": st["epochs"], "network": "local"}

    def set_consensus(self, rule):
        consensus.get(rule)
        def op(st):
            st["consensus"] = rule
            return {"ok": True, "netuid": st["netuid"], "consensus": rule}
        return self._mutate(op)

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
        return {"netuid": st["netuid"], "name": st["name"], "network": "local",
                "consensus": st["consensus"], "tasks": st["tasks"],
                "block": st["block"], "epochs": st["epochs"], "n": len(st["neurons"]),
                "neurons": sorted(st["neurons"].values(), key=lambda n: n["uid"]),
                "incentive": st["incentive"]}

    def miners(self):
        return [n for n in self.metagraph()["neurons"]
                if n["role"] == "miner" and n.get("url")]

    def set_weights(self, hotkey, weights):
        """weights: {uid(str|int): weight}. Runs the subnet's consensus rule."""
        def op(st):
            if hotkey not in st["neurons"]:
                raise KeyError(f"hotkey not registered: {hotkey}")
            st["weights"][hotkey] = {str(u): float(w) for u, w in weights.items()}
            st["block"] += 1
            st["epochs"] += 1
            st["incentive"] = run_consensus(st)
            return {"ok": True, "epoch": st["epochs"], "consensus": st["consensus"],
                    "incentive": st["incentive"]}
        return self._mutate(op)


def run_consensus(state):
    """Feed the subnet's validator weights + stakes to its consensus rule."""
    weights = {hk: w for hk, w in state["weights"].items() if hk in state["neurons"]}
    if not weights:
        return {}
    stakes = {hk: max(state["neurons"][hk].get("stake", 1.0), 1e-9) for hk in weights}
    return consensus.get(state.get("consensus", consensus.DEFAULT)).run(weights, stakes)


def _new_state(netuid, name, consensus, tasks, owner="", created=0):
    return {"netuid": netuid, "name": name, "owner": owner, "consensus": consensus,
            "tasks": tasks, "created": created, "block": 0, "neurons": {},
            "weights": {}, "incentive": {}, "epochs": 0, "updated": 0}


def _migrate_legacy(data_dir, subnets_dir):
    """Single-subnet era kept data/subnet_chain.json — it becomes netuid 0."""
    old, new = os.path.join(data_dir, "subnet_chain.json"), os.path.join(subnets_dir, "0.json")
    if os.path.exists(old) and not os.path.exists(new):
        with open(old) as f:
            st = json.load(f)
        for k, v in GENESIS.items():
            st.setdefault(k, v)
        st.update(netuid=0, owner=st.get("owner", ""), created=st.get("created", st.get("updated", 0)))
        with open(new, "w") as f:
            json.dump(st, f, indent=2)
        os.remove(old)


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

    def subnets(self):
        return [self.info()]

    def info(self):
        return {"netuid": self.cfg.netuid, "name": f"netuid {self.cfg.netuid}",
                "consensus": "yuma (subtensor)", "tasks": list(TASKS),
                "network": self.cfg.network}

    def create_subnet(self, *a, **k):
        raise NotImplementedError(
            "on subtensor, create the subnet with `btcli subnet create` (locks TAO), "
            "then put its netuid in config.json subnet.netuid")

    def set_consensus(self, rule):
        raise NotImplementedError("subtensor always runs its own Yuma consensus")

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
                "name": f"netuid {self.cfg.netuid}", "consensus": "yuma (subtensor)",
                "tasks": list(TASKS), "block": int(mg.block), "n": len(neurons), "neurons": neurons,
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
