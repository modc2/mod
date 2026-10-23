"""Modular consensus: the engine that decides who may extend the chain is a
plugin, and the chain can migrate from one engine to another without a fork.

An ENGINE answers three questions about a block at height h:

    who proposes it          proposer(epoch, height, round, parent, stake)
    what makes it final      quorum(epoch, stake) over the validators' votes
    what else must hold      check(...) — a PoW target, a header field

and the chain asks them through one EPOCH: {engine, from_height, params,
validators, require_pq}. The schedule is a list of epochs. Epoch 0 comes from
genesis (a chain made before this file existed is epoch 0 = "authority" with
the genesis validator, so every old block still verifies). Every later epoch
was written INTO a block header as `migrate` — signed and committed under the
rules of the epoch it replaces — so a migration is authorised by the very
consensus it ends, and `verify()` rebuilds the whole schedule from the log
instead of trusting a config file.

WHAT "POST-QUANTUM" MEANS HERE
    Every proposer seal and every vote is a signature under one of the chain's
    key types — ML-DSA, SLH-DSA, composites of them, Cairo-MSS layers — and
    validators may mix them freely. No engine depends on an elliptic curve,
    a pairing or a classical VRF: leader election is a SHA3 lottery over the
    parent hash, PoW is SHA3 hashcash. With require_pq (the default) a
    validator key must be quantum-safe and must not be few/one-time — a
    validator signs every block, and a few-time key would burn its own reuse
    budget. Grover gives a quantum miner a square-root speedup on PoW, so the
    pow engine reports difficulty/2 quantum bits; nothing breaks, it just
    costs a quantum miner less.

WHAT IS HONEST ABOUT IT
    One node holds one log. There is no gossip and no fork choice in this
    file: an engine here is the RULE for which blocks are valid, applied
    identically by produce() and by the replay audit. Validator keys this
    node holds sign locally; anybody else's vote arrives through vote() with
    a signature over the proposal, and a block whose quorum is not yet met
    waits as a proposal instead of being committed.

Engines are plugins: drop a .py with register(consensus) into
src/consensus.d/ or <data dir>/consensus/ and call consensus.register(...).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import keys as K                                                # noqa: E402
import lazy as LZ                                               # noqa: E402
from pq import algos                                            # noqa: E402
from state import StateError, canonical, sha3                   # noqa: E402

VOTE_CONTEXT = b"postquant/vote/v1"
BLOCK_CONTEXT = b"postquant/block/v1"
ROUND_SECONDS = int(os.environ.get("POSTQUANT_ROUND_SECONDS", 20))
MAX_VALIDATORS = 64
MAX_POW_BITS = 24

DATA_DIR = os.path.expanduser(os.environ.get("POSTQUANT_DATA_DIR",
                                             "~/.mod/postquant"))
PLUGIN_DIRS = [os.path.join(HERE, "consensus.d"),
               os.path.join(DATA_DIR, "consensus")]
PLUGIN_ERRORS: list = []
_LOADED: set = set()


# ── the engine interface ──────────────────────────────────────────


class Engine:
    """Subclass and register. Defaults are a single-seal engine; override
    what your consensus changes."""

    name = "engine"
    title = ""
    family = ""                     # authority | rotation | bft | stake | work
    summary = ""
    faults = ""                     # what it tolerates, in words
    needs_validators = True
    min_validators = 1
    max_validators = MAX_VALIDATORS
    votes = False                   # does a block need a quorum of votes?

    def validate(self, epoch):
        """Raise StateError if the epoch is unusable; may fill defaults."""
        n = len(epoch["validators"])
        if self.needs_validators and n < self.min_validators:
            raise StateError(f"{self.name} needs at least "
                             f"{self.min_validators} validator(s), got {n}",
                             code="bad_consensus")
        if n > self.max_validators:
            raise StateError(f"{self.name} takes at most "
                             f"{self.max_validators} validators",
                             code="bad_consensus")

    def powers(self, epoch, stake):
        """address -> voting power. stake(addr) reads the pre-block state."""
        return {v["address"]: int(v.get("power", 1))
                for v in epoch["validators"]}

    def proposer(self, epoch, height, rnd, parent, stake):
        """The address allowed to propose at (height, round), or None for
        anyone (pow)."""
        vals = epoch["validators"]
        return vals[(height + rnd) % len(vals)]["address"]

    def quorum(self, epoch, stake):
        """Voting power a block needs, or 0 when the proposer's seal is the
        whole of finality."""
        return 0

    def header_fields(self, epoch, header):
        """Extra header fields the engine writes before sealing (pow mines
        here)."""
        return {}

    def check(self, epoch, block, stake):
        """Engine-specific problems with a committed block, as strings."""
        return []

    def describe(self):
        return {"name": self.name, "title": self.title,
                "family": self.family, "summary": self.summary,
                "faults": self.faults, "votes": self.votes,
                "needs_validators": self.needs_validators,
                "min_validators": self.min_validators,
                "origin": getattr(self, "origin", "builtin")}


REGISTRY: dict = {}


def register(engine, replace=False):
    if not isinstance(engine, Engine):
        raise TypeError("register() takes an Engine instance")
    if engine.name in REGISTRY and not replace:
        raise ValueError(f"consensus engine {engine.name!r} exists")
    REGISTRY[engine.name] = engine
    return engine


def get(name) -> Engine:
    e = REGISTRY.get(name)
    if e is None:
        raise StateError(f"unknown consensus engine {name!r} — this node "
                         f"knows {', '.join(sorted(REGISTRY))}; a new one "
                         "is a .py in src/consensus.d/",
                         code="unknown_engine", status=400)
    return e


# ── builtins ──────────────────────────────────────────────────────


class Authority(Engine):
    name = "authority"
    title = "Single authority"
    family = "authority"
    summary = ("one key proposes and seals every block — the chain's "
               "original rule; finality is that key's signature")
    faults = "none: the authority going down halts the chain, and it can " \
             "reorder anything it has not yet sealed"
    max_validators = 1


class RoundRobin(Engine):
    name = "roundrobin"
    title = "Proof of authority, rotating (Aura-style)"
    family = "rotation"
    summary = ("a fixed validator set takes turns: validator "
               "(height + round) mod n proposes; a silent validator is "
               f"skipped after {ROUND_SECONDS}s by bumping the round")
    faults = "crash: liveness survives n-1 silent validators (each skip " \
             "costs a round); no byzantine safety — any one proposer seals"


def _bft_quorum(powers):
    total = sum(powers.values())
    return total * 2 // 3 + 1


class BFT(Engine):
    name = "bft"
    title = "BFT quorum (Tendermint-style)"
    family = "bft"
    summary = ("rotating proposer, and a block is final only once "
               "validators holding > 2/3 of voting power sign it — "
               "instant finality, no forks to choose between")
    faults = "byzantine: safe with f < n/3 faulty power; halts (not " \
             "forks) beyond that"
    votes = True

    def quorum(self, epoch, stake):
        return _bft_quorum(self.powers(epoch, stake))

    def describe(self):
        return {**super().describe(), "tolerates": "f = floor((n-1)/3)"}


def lottery(seed: bytes, powers: dict):
    """Stake-weighted pick by SHA3 — a hash lottery seeded by the parent block,
    not a classical VRF, so there is no curve for a quantum adversary to
    break. Predictable one block ahead, which a VRF would hide; the price of
    leaving elliptic curves out."""
    items = sorted((a, p) for a, p in powers.items() if p > 0)
    total = sum(p for _, p in items)
    if not total:
        return None
    r = int.from_bytes(sha3(b"pq-lottery\x00", seed), "big") % total
    for a, p in items:
        if r < p:
            return a
        r -= p
    return items[-1][0]


class Stake(Engine):
    name = "pos"
    title = "Proof of stake (balance-weighted BFT)"
    family = "stake"
    summary = ("voting power = each validator's PQ balance in the state "
               "before the block; the proposer is drawn by a SHA3 lottery "
               "over the parent hash weighted by stake, and > 2/3 of stake "
               "must sign")
    faults = "byzantine: safe while < 1/3 of bonded stake is faulty"
    votes = True

    def validate(self, epoch):
        super().validate(epoch)
        p = epoch.setdefault("params", {})
        p["min_stake"] = int(p.get("min_stake", 0))

    def powers(self, epoch, stake):
        floor = int(epoch.get("params", {}).get("min_stake", 0))
        out = {}
        for v in epoch["validators"]:
            s = int(stake(v["address"]))
            out[v["address"]] = s if s >= max(floor, 1) else 0
        return out

    def proposer(self, epoch, height, rnd, parent, stake):
        seed = bytes.fromhex(parent) + height.to_bytes(8, "big") + \
            rnd.to_bytes(4, "big")
        return lottery(seed, self.powers(epoch, stake))

    def quorum(self, epoch, stake):
        powers = self.powers(epoch, stake)
        if not sum(powers.values()):
            raise StateError("no validator holds stake — pos cannot elect a "
                             "proposer", code="no_stake")
        return _bft_quorum(powers)


def leading_zero_bits(b: bytes) -> int:
    n = 0
    for byte in b:
        if byte == 0:
            n += 8
            continue
        return n + 8 - byte.bit_length()
    return n


class Work(Engine):
    name = "pow"
    title = "Proof of work (SHA3 hashcash)"
    family = "work"
    summary = ("anyone may propose a block whose SHA3-256 header hash has "
               "`difficulty` leading zero bits; the miner seals it with a "
               "PQ key and collects the tips")
    faults = "economic: safe while honest hash power is the majority; " \
             "Grover halves a quantum miner's effective difficulty"
    needs_validators = False
    min_validators = 0

    def validate(self, epoch):
        super().validate(epoch)
        p = epoch.setdefault("params", {})
        d = int(p.get("difficulty", 12))
        if not 1 <= d <= MAX_POW_BITS:
            raise StateError(f"difficulty must be 1..{MAX_POW_BITS} bits",
                             code="bad_consensus")
        p["difficulty"] = d

    def powers(self, epoch, stake):
        return {}

    def proposer(self, epoch, height, rnd, parent, stake):
        return None

    def header_fields(self, epoch, header):
        import chain as C
        d = epoch["params"]["difficulty"]
        h = dict(header, difficulty=d)
        nonce = 0
        while True:
            h["pow_nonce"] = nonce
            if leading_zero_bits(bytes.fromhex(C.block_hash(h))) >= d:
                return {"difficulty": d, "pow_nonce": nonce}
            nonce += 1

    def check(self, epoch, block, stake):
        h = block["header"]
        d = epoch["params"]["difficulty"]
        out = []
        if h.get("difficulty") != d:
            out.append(f"difficulty {h.get('difficulty')} != epoch {d}")
        elif leading_zero_bits(bytes.fromhex(block["hash"])) < d:
            out.append("header hash misses the work target")
        return out

    def describe(self):
        return {**super().describe(),
                "quantum": "Grover: a quantum miner needs ~2^(d/2) hashes"}


for _e in (Authority(), RoundRobin(), BFT(), Stake(), Work()):
    register(_e)


def load_plugins():
    this = sys.modules[__name__]
    found = []
    for d in PLUGIN_DIRS:
        if os.path.isdir(d):
            found += [(fn, os.path.join(d, fn)) for fn in os.listdir(d)
                      if fn.endswith(".py") and not fn.startswith("_")]
    for fn, path in sorted(found):
        if path in _LOADED:
            continue
        _LOADED.add(path)
        try:
            spec = importlib.util.spec_from_file_location(
                f"postquant_consensus_{fn[:-3]}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            before = set(REGISTRY)
            mod.register(this)
            for n in set(REGISTRY) - before:
                REGISTRY[n].origin = path
        except Exception as e:                              # noqa: BLE001
            PLUGIN_ERRORS.append({"plugin": path,
                                  "error": f"{type(e).__name__}: {e}",
                                  "trace": traceback.format_exc(limit=3)})


load_plugins()


# ── validators and epochs ─────────────────────────────────────────


def key_refusal(scheme, require_pq=True):
    """Why a key type may not sign blocks or votes, or None."""
    if scheme == "private":
        return "private key types cannot validate — a vote must be " \
               "checkable by everyone, not only by the proposer"
    r = algos.refusal(scheme)
    if r is not None:
        return r[1]
    if require_pq:
        from pq import pqscore
        s = pqscore.score_algo(scheme)
        if not s.get("safe"):
            return f"{scheme} is {s.get('verdict')} against a quantum " \
                   "adversary (require_pq)"
        if any("few/one-time" in r for r in s.get("reasons", [])):
            return f"{scheme} is few/one-time — a validator signs every " \
                   "block and would wear the key out"
    return None


def validator_entry(v, require_pq=True):
    """A wallet name / address held locally, or {address, pk, scheme, power}."""
    if isinstance(v, str):
        w = K.get(v, required=False)
        if w is None:
            raise StateError(f"validator {v!r} is not a local wallet — pass "
                             "{address, pk, scheme} for a remote one",
                             code="bad_validator")
        v = {"address": w["address"], "pk": w["pk"], "scheme": w["scheme"],
             "name": w["name"]}
    if not isinstance(v, dict):
        raise StateError("a validator is a wallet name or an object",
                         code="bad_validator")
    try:
        pk = bytes.fromhex(v["pk"])
        scheme = v["scheme"]
    except (KeyError, ValueError, TypeError):
        raise StateError("validator needs pk (hex) and scheme",
                         code="bad_validator")
    why = key_refusal(scheme, require_pq)
    if why:
        raise StateError(f"validator {v.get('name') or v.get('address')}: "
                         f"{why}", code="bad_validator_key", status=403)
    addr = K.address(pk, scheme)
    if v.get("address") and v["address"] != addr:
        raise StateError(f"validator address {v['address']} is not "
                         f"{scheme}({v['pk'][:16]}…) = {addr}",
                         code="bad_validator")
    power = int(v.get("power", 1))
    if power < 1:
        raise StateError("validator power must be >= 1", code="bad_validator")
    out = {"address": addr, "pk": v["pk"], "scheme": scheme, "power": power}
    if v.get("name"):
        out["name"] = str(v["name"])
    return out


def make_epoch(engine, validators=(), params=None, from_height=1,
               require_pq=True):
    eng = get(engine)
    vals = [validator_entry(v, require_pq) for v in (validators or [])]
    seen = set()
    for v in vals:
        if v["address"] in seen:
            raise StateError(f"validator {v['address']} listed twice",
                             code="bad_validator")
        seen.add(v["address"])
    epoch = {"engine": eng.name, "from_height": int(from_height),
             "params": dict(params or {}), "validators": vals,
             "require_pq": bool(require_pq)}
    eng.validate(epoch)
    return epoch


def check_epoch(epoch):
    """Re-validate an epoch read back out of a header (the replay audit)."""
    again = make_epoch(epoch["engine"], epoch.get("validators", []),
                       epoch.get("params"), epoch.get("from_height", 1),
                       epoch.get("require_pq", True))
    if canonical(again) != canonical(epoch):
        raise StateError("epoch is not in canonical form",
                         code="bad_consensus")
    return again


def genesis_epoch(genesis):
    """Epoch 0. A chain that predates modular consensus is a legacy authority
    epoch: same key, same seal, and no new header fields, so its hashes are
    exactly what they always were."""
    if genesis.get("consensus"):
        return dict(genesis["consensus"], from_height=0)
    return {"engine": "authority", "from_height": 0, "params": {},
            "legacy": True, "require_pq": False,
            "validators": [{"address": genesis["validator"],
                            "pk": genesis.get("validator_pk"),
                            "scheme": genesis.get("scheme", K.SCHEME),
                            "power": 1,
                            "name": genesis.get("validator_name")}]}


def epoch_at(schedule, height):
    cur = schedule[0]
    for e in schedule:
        if e["from_height"] <= height:
            cur = e
    return cur


def epoch_id(epoch) -> str:
    return sha3(b"pq-epoch\x00", canonical(
        {k: v for k, v in epoch.items() if k != "legacy"})).hex()


# ── votes ─────────────────────────────────────────────────────────


def vote_message(chain_id, height, rnd, block_hash) -> bytes:
    return sha3(b"pq-vote\x00", canonical(
        {"chain_id": chain_id, "height": int(height), "round": int(rnd),
         "hash": block_hash}))


def sign_vote(wallet, chain_id, height, rnd, block_hash) -> str:
    return algos.get(wallet["scheme"]).sign(
        K.secret_key(wallet), vote_message(chain_id, height, rnd, block_hash),
        VOTE_CONTEXT).hex()


def vote_ok(validator, chain_id, height, rnd, block_hash, sig_hex) -> bool:
    try:
        return algos.get(validator["scheme"]).verify(
            bytes.fromhex(validator["pk"]),
            vote_message(chain_id, height, rnd, block_hash),
            bytes.fromhex(sig_hex), VOTE_CONTEXT)
    except Exception:                                       # noqa: BLE001
        return False


def tally(epoch, votes, chain_id, height, rnd, block_hash, stake):
    """(power that signed, power needed, problems). Bad or duplicate votes are
    problems, not power."""
    eng = get(epoch["engine"])
    powers = eng.powers(epoch, stake)
    need = eng.quorum(epoch, stake)
    by_addr = {v["address"]: v for v in epoch["validators"]}
    got, seen, problems = 0, set(), []
    for v in votes or []:
        a = v.get("validator")
        if a in seen:
            problems.append(f"duplicate vote from {a}")
            continue
        seen.add(a)
        val = by_addr.get(a)
        if val is None:
            problems.append(f"vote from non-validator {a}")
            continue
        if not vote_ok(val, chain_id, height, rnd, block_hash, v.get("sig")):
            problems.append(f"vote from {a} does not verify")
            continue
        got += powers.get(a, 0)
    return got, need, problems


# ── the whole-block rule ──────────────────────────────────────────


def seal_ok(block, scheme_default):
    h = block["header"]
    if not block.get("sig") or not block.get("proposer_pk"):
        return False
    scheme = block.get("proposer_scheme", scheme_default)
    try:
        pk = bytes.fromhex(block["proposer_pk"])
        return K.address(pk, scheme) == h["proposer"] and \
            algos.get(scheme).verify(pk, bytes.fromhex(block["hash"]),
                                     bytes.fromhex(block["sig"]),
                                     BLOCK_CONTEXT)
    except Exception:                                       # noqa: BLE001
        return False


def lazy_problems(block, parent_header):
    """The fee rule a block that declares `lazy` has signed up to.

    Lazy consensus skips the slots in which nothing happened, so the fee
    market has to price those slots instead of living through them
    (lazy.expected_base_fee). A block carrying `lazy: 1` is asserting that
    its base fee is exactly what that rule produces from its parent header —
    an assertion a replay can refute, which is the only kind worth making.
    Blocks without the marker, including every block produced before lazy
    consensus existed, are left exactly as they were.
    """
    h = block["header"]
    if not h.get("lazy") or not parent_header:
        return []
    want = LZ.expected_base_fee(parent_header, h)
    if h.get("base_fee") != want:
        return [f"lazy base fee is {h.get('base_fee')}, the rule says {want} "
                f"({LZ.idle_slots(parent_header['timestamp'], h['timestamp'])}"
                f" idle slots since height {parent_header['height']})"]
    return []


def check_block(epoch, block, parent_hash, stake, genesis_scheme,
                parent_header=None):
    """Every way a committed block can break its epoch's consensus rule."""
    h = block["header"]
    out = lazy_problems(block, parent_header)
    if epoch.get("legacy"):
        # The original rule: a proposer signature, checked if present.
        if block.get("sig") and not seal_ok(block, genesis_scheme):
            out.append("proposer signature does not verify")
        return out
    eng = get(epoch["engine"])
    if h.get("consensus") != eng.name:
        out.append(f"header says consensus {h.get('consensus')!r}, epoch is "
                   f"{eng.name}")
    if h.get("epoch") != epoch_id(epoch):
        out.append("header epoch id does not match the schedule")
    rnd = int(h.get("round", 0))
    want = eng.proposer(epoch, h["height"], rnd, parent_hash, stake)
    if want is not None and h["proposer"] != want:
        out.append(f"proposer {h['proposer']} is not the {eng.name} pick "
                   f"{want} for round {rnd}")
    if not seal_ok(block, genesis_scheme):
        out.append("proposer seal does not verify")
    elif want is None:
        why = key_refusal(block.get("proposer_scheme"),
                          epoch.get("require_pq", True))
        if why:
            out.append(f"proposer key: {why}")
    if eng.votes:
        got, need, probs = tally(epoch, (block.get("commit") or {}).get(
            "votes"), h["chain_id"], h["height"], rnd, block["hash"], stake)
        out += probs
        if got < need:
            out.append(f"commit has {got} of {need} voting power")
    out += eng.check(epoch, block, stake)
    return out


def catalog():
    return {"engines": [e.describe() for e in REGISTRY.values()],
            "plugin_dirs": PLUGIN_DIRS, "plugin_errors": PLUGIN_ERRORS,
            "round_seconds": ROUND_SECONDS,
            "signatures": "every seal and vote is a signature under one of "
                          "the chain's key types; require_pq demands a "
                          "quantum-safe, many-time key"}

