"""Lazy consensus: a chain that stops when nothing is happening, and a node
that is allowed to turn off while it has stopped.

Most chains produce a block every N seconds whether or not anyone asked for
one. That is a decision about liveness, and it is paid for in a way nobody
writes down: a validator set burning CPU, disk and an electricity bill to
append empty headers, forever, so that the chain can say it is alive. This
module takes the other branch. Here a block is an answer to a transaction.
No transactions, no blocks — and a chain with no blocks to produce needs no
machine running to produce them.

WHY THIS CHAIN CAN DO IT AND MOST CANNOT
    Rent on this chain is already lazy. An entry carries `rent_from` and its
    escrow is drained by `rent_for(bytes, now - rent_from)` at the moment
    something touches it (state.py `_remaining` / `_settle`), so occupancy is
    priced off the wall clock, not off block height. Nobody has to be awake
    for a lease to run down: expiry is a timestamp comparison, and sweeping an
    expired entry is itself a transaction somebody sends when they want the
    bounty. The one thing the old heartbeat block really did was let the fee
    market cool off, and that is a function of elapsed time too — see
    `decay` below, which prices an idle gap as the empty slots it would have
    been. So the heartbeat was never load-bearing. It was a clock we were
    paying for twice.

THE THREE MODES
    hot       transactions are arriving; produce as they arrive.
    idle      nothing in the mempool; no blocks are produced at all. The tip
              is final. The loop ticks slowly, because a proposal waiting on
              votes still has to time out.
    storage   idle for PARK_SECONDS. The node writes dormant.json — tip
              height, hash, state root, and what will wake it — and declares
              itself safe to stop. A dormant chain is a directory of files
              whose last line commits to everything before it. The process
              can exit; the orbit activator wakes it on the first byte and
              the first transaction after that produces the block that
              settles the whole sleep in one step.

    Parking writes NO block. That is the point and it is also the safety
    argument: storage mode is not a chain event that a replay has to agree
    about, it is the absence of chain events. dormant.json is a convenience
    manifest, derivable from blocks.jsonl, and nothing in verify() reads it.

SCALING BACK UP
    Idle is cheap, but a chain that cannot grow out of idle is a toy. The
    `Pulse` meter below records, per block and per submitted witness, the
    thing that actually costs money on a post-quantum chain: witness
    verification. An ML-DSA-44 signature is 2420 bytes and verifies in
    milliseconds inside a wasm module; an SLH-DSA-SHAKE-128f signature is
    17088 bytes; a CAIRO-MSS composite is heavier again. So the compute
    requirement is not guessed from a tx count — it is measured verify
    milliseconds per minute, which converts to cores directly (60_000 ms of
    verification per minute saturates one core) and is multiplied by a
    headroom factor because a validator that is exactly at capacity is a
    validator that is late.

    `plan()` turns that into a requirement a human or a scheduler can act
    on: tier, cores, memory, projected disk, how many validators the tier
    wants, and — because consensus here is already modular — the exact
    pq_consensus migration that would widen the validator set to match.
    It recommends; it does not rent machines and it does not migrate a
    chain by itself. Scaling a validator set is a signed on-chain act and
    stays one.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time

import state as S

LAZY = os.environ.get("POSTQUANT_LAZY", "1").lower() not in (
    "0", "off", "false", "no")
PARK_SECONDS = int(os.environ.get("POSTQUANT_PARK_SECONDS", 900))
PARK_POLL = int(os.environ.get("POSTQUANT_PARK_POLL", 60))
HEADROOM = float(os.environ.get("POSTQUANT_SCALE_HEADROOM", 2.0))
WINDOW = int(os.environ.get("POSTQUANT_PULSE_WINDOW", 900))
MAX_DECAY_SLOTS = 4096          # the fee hits the floor long before this

# The slot the fee market measures idleness in. This is a PROTOCOL constant
# and deliberately not POSTQUANT_BLOCK_SECONDS: that one is how often a
# node's loop wakes up to look at the mempool, which is an operational knob
# an operator may set to anything. If the discount for an idle gap depended
# on it, two nodes with different settings would compute different fees for
# the same two headers and the rule would not be checkable. One is protocol,
# the other is a poll interval, and they are now separate.
SLOT_SECONDS = 5


# ── the fee market over an idle gap ───────────────────────────────

def idle_slots(parent_ts, ts, slot=SLOT_SECONDS):
    """How many fee-market slots the gap between two blocks skipped.

    Derived from two timestamps and a protocol constant, which is what makes
    the rule below checkable by anyone holding the headers.
    """
    if slot <= 0:
        return 0
    return max(0, int(ts - parent_ts) // int(slot) - 1)


def decay(fee, slots):
    """The base fee after `slots` slots in which no block was produced.

    An empty block under EIP-1559-over-state-growth drops the base fee by
    1/8 (state.next_base_fee with growth 0). A slot where no block appeared
    is a slot in which no state was added, so it prices the same: the chain
    must not charge a congestion premium for congestion that stopped
    happening while it was asleep. Integer math, applied one slot at a time,
    because that is the only way two nodes are guaranteed to agree — and it
    converges on the floor in a couple of hundred steps, so the loop is
    bounded in practice as well as formally.
    """
    fee = int(fee)
    for _ in range(min(int(slots), MAX_DECAY_SLOTS)):
        if fee <= S.BASE_FEE_MIN:
            return S.BASE_FEE_MIN
        fee = S.next_base_fee(fee, 0)
    return fee


def expected_base_fee(parent, header, slot=SLOT_SECONDS):
    """The base fee a lazily produced block must carry, from its parent.

    Two steps, both pure functions of the two headers: the ordinary per-block
    EIP-1559 response to the parent's state growth, then the idle decay for
    the slots between them. A block that declares `lazy` is checked against
    this in consensus.check_block, so the discount for sleeping is a rule and
    not a courtesy from whoever happened to be proposing — and since the
    ordinary step is in the same expression, declaring `lazy` also puts the
    per-block fee response under replay for the first time.
    """
    fee = S.next_base_fee(parent["base_fee"], parent.get("state_bytes", 0))
    return decay(fee, idle_slots(parent["timestamp"], header["timestamp"],
                                 slot))


# ── the activity meter ────────────────────────────────────────────

class Pulse:
    """What this node has actually been asked to do lately.

    Two streams, both kept as plain (timestamp, cost) samples inside a
    rolling window: witnesses verified at submit time, and blocks produced.
    Verification is timed rather than modelled — the cost of a signature on
    this chain depends on its key type and on whether its verifier is a wasm
    blob or pure python, and pretending to know that number is how capacity
    plans end up wrong by an order of magnitude.
    """

    def __init__(self, window=WINDOW):
        self.window = window
        self.lock = threading.Lock()
        self.witnesses = []        # (ts, scheme, bytes, ms)
        self.blocks = []           # (ts, txs, gas, state_bytes, ms)
        self.last_activity = int(time.time())
        self.woke_at = int(time.time())
        self.wakes = 0

    def _trim(self, now):
        floor = now - self.window
        self.witnesses = [w for w in self.witnesses if w[0] >= floor]
        self.blocks = [b for b in self.blocks if b[0] >= floor]

    def witness(self, scheme, nbytes, ms, now=None):
        now = int(now or time.time())
        with self.lock:
            self.witnesses.append((now, scheme or "?", int(nbytes), float(ms)))
            self.last_activity = now
            self._trim(now)

    def block(self, header, ms=0.0, now=None):
        now = int(now or time.time())
        with self.lock:
            self.blocks.append((now, header.get("tx_count", 0),
                                header.get("gas_used", 0),
                                header.get("state_bytes", 0), float(ms)))
            if header.get("tx_count"):
                self.last_activity = now
            self._trim(now)

    def touch(self, now=None):
        """Something happened that is not a transaction — a read that implies
        a user, a wake, an unparked proposal. Keeps a node that is being
        actively queried out of storage mode."""
        with self.lock:
            self.last_activity = int(now or time.time())

    def idle_for(self, now=None):
        return max(0, int(now or time.time()) - self.last_activity)

    def demand(self, now=None):
        """The measured load, per minute, over the window."""
        now = int(now or time.time())
        with self.lock:
            self._trim(now)
            ws, bs = list(self.witnesses), list(self.blocks)
        # The denominator is how long this node has been awake inside the
        # window, floored at a minute: a node that woke two seconds ago and
        # took one transaction has seen one transaction, not thirty a minute.
        span = float(max(60, min(self.window, now - self.woke_at)))
        per_min = 60.0 / span
        verify_ms = sum(w[3] for w in ws)
        by_scheme = {}
        for _, scheme, nbytes, ms in ws:
            s = by_scheme.setdefault(scheme, {"witnesses": 0, "bytes": 0,
                                              "ms": 0.0})
            s["witnesses"] += 1
            s["bytes"] += nbytes
            s["ms"] += ms
        for s in by_scheme.values():
            s["ms"] = round(s["ms"], 2)
            s["ms_each"] = round(s["ms"] / max(1, s["witnesses"]), 2)
        return {
            "window_seconds": self.window,
            "sampled_seconds": int(span),
            "witnesses": len(ws),
            "blocks": len(bs),
            "tx_per_min": round(len(ws) * per_min, 3),
            "blocks_per_min": round(len(bs) * per_min, 3),
            "witness_bytes_per_min": int(sum(w[2] for w in ws) * per_min),
            "verify_ms_per_min": round(verify_ms * per_min, 1),
            "state_bytes_per_min": int(sum(b[3] for b in bs) * per_min),
            "gas_per_min": int(sum(b[2] for b in bs) * per_min),
            "idle_for": self.idle_for(now),
            "by_scheme": by_scheme,
        }


# ── the tier ladder ───────────────────────────────────────────────
#
# Each rung is a floor, not a forecast: `enter` is the demand at which the
# chain belongs on that rung, and the cores/memory are the minimum a node on
# it should have even if the measured verification load is lower. The
# measured load can push a rung's requirement UP; it never pushes it below
# the floor, because a validator also has to serve reads, hold the state and
# re-verify what it is sent.

TIERS = (
    {"name": "storage", "enter_tx_per_min": None, "enter_verify_ms": None,
     "nodes": 0, "block_seconds": 0, "vcpu": 0, "ram_mb": 0,
     "note": "dormant — nothing to produce, so nothing has to be running; "
             "the tip is the commitment and the first byte wakes it"},
    {"name": "idle", "enter_tx_per_min": 0.0, "enter_verify_ms": 0.0,
     "nodes": 1, "block_seconds": 30, "vcpu": 1, "ram_mb": 512,
     "note": "awake, answering reads, producing nothing"},
    {"name": "warm", "enter_tx_per_min": 0.2, "enter_verify_ms": 200.0,
     "nodes": 1, "block_seconds": 5, "vcpu": 2, "ram_mb": 1024,
     "note": "a trickle of writes — one proposer keeps up comfortably"},
    {"name": "busy", "enter_tx_per_min": 20.0, "enter_verify_ms": 6000.0,
     "nodes": 3, "block_seconds": 5, "vcpu": 4, "ram_mb": 2048,
     "note": "sustained writes; widen the validator set so a single box is "
             "not the liveness of the chain"},
    {"name": "surge", "enter_tx_per_min": 120.0, "enter_verify_ms": 30000.0,
     "nodes": 5, "block_seconds": 2, "vcpu": 8, "ram_mb": 4096,
     "note": "verification is the bottleneck — post-quantum witnesses are "
             "kilobytes each and every node re-runs them in wasm"},
)

TIER_NAMES = [t["name"] for t in TIERS]


def tier_at(name):
    for t in TIERS:
        if t["name"] == name:
            return t
    raise KeyError(name)


def tier_for(demand, dormant=False):
    """Which rung the measured demand puts the chain on.

    Either signal promotes: a low transaction rate made of SLH-DSA witnesses
    can cost more verification than a high rate of ML-DSA-44 ones, and the
    chain should be sized for whichever of the two is binding.
    """
    if dormant:
        return tier_at("storage")
    pick = tier_at("idle")
    for t in TIERS:
        if t["enter_tx_per_min"] is None:
            continue
        if demand["tx_per_min"] >= t["enter_tx_per_min"] or \
                demand["verify_ms_per_min"] >= t["enter_verify_ms"]:
            pick = t
    return pick


def cores_for(verify_ms_per_min, headroom=HEADROOM):
    """Measured verification load as cores.

    60_000 milliseconds of verification per minute is one core, fully
    occupied. Multiply by headroom, because a proposer running at 100% of
    one core is a proposer that misses its round, and round up.
    """
    load = float(verify_ms_per_min) / 60_000.0
    return max(0, int(math.ceil(load * max(1.0, headroom))))


def disk_projection(state_bytes, log_bytes, state_bytes_per_min, days=365):
    """What the disk looks like in `days` at the current growth rate."""
    growth = max(0.0, float(state_bytes_per_min)) * 60 * 24 * days
    # The log grows faster than the state does: a block carries its witnesses
    # forever, and a 17 KiB signature is in the log even after the entry it
    # wrote has expired out of the state.
    now_bytes = int(state_bytes) + int(log_bytes)
    return {"state_bytes": int(state_bytes), "log_bytes": int(log_bytes),
            "total_gb": round(now_bytes / 1e9, 4),
            "projected_gb": round((now_bytes + growth * 3) / 1e9, 4),
            "projection_days": days,
            "why": "the log keeps every witness forever, so it grows faster "
                   "than the state it produced — projected at 3x state "
                   "growth, which is about the ratio an ML-DSA-44 write has"}


def requirements(demand, tier, state_bytes=0, log_bytes=0,
                 headroom=HEADROOM):
    """The compute requirement for one node on this tier, and for the set."""
    measured = cores_for(demand["verify_ms_per_min"], headroom)
    vcpu = max(tier["vcpu"], measured)
    nodes = tier["nodes"]
    ram = max(tier["ram_mb"],
              512 + int((state_bytes + log_bytes) / 1e6))   # state lives in RAM
    disk = disk_projection(state_bytes, log_bytes,
                           demand["state_bytes_per_min"])
    return {
        "tier": tier["name"],
        "nodes": nodes,
        "per_node": {"vcpu": vcpu, "ram_mb": ram,
                     "disk_gb": max(1, math.ceil(disk["projected_gb"]))},
        "total": {"vcpu": vcpu * nodes, "ram_mb": ram * nodes,
                  "disk_gb": max(1, math.ceil(disk["projected_gb"])) * nodes},
        "block_seconds": tier["block_seconds"],
        "verify_cores_measured": measured,
        "verify_cores_floor": tier["vcpu"],
        "headroom": headroom,
        "disk": disk,
        "note": tier["note"],
        "how_cores_were_derived":
            f"{demand['verify_ms_per_min']} ms of witness verification per "
            f"minute / 60000 ms per core-minute x {headroom} headroom = "
            f"{measured} core(s), floored at the tier minimum "
            f"{tier['vcpu']}",
    }


# ── the dormancy manifest ─────────────────────────────────────────

def manifest(node, since, reason="idle"):
    """What a stopped node leaves behind so the next thing to look at the
    directory knows what it is holding — and knows it does not need to
    trust this file, because blocks.jsonl says the same thing."""
    tip = node.blocks[-1]
    return {
        "chain_id": node.chain_id,
        "dormant": True,
        "since": int(since),
        "reason": reason,
        "height": tip["height"],
        "hash": node._tip_hash(),
        "state_root": tip["state_root"],
        "tip_timestamp": tip["timestamp"],
        "base_fee": node.state.base_fee,
        "keys": len(node.state.store),
        "state_bytes": node.state.state_bytes(),
        "safe_to_stop": True,
        "wakes_on": ["a transaction (POST /set and friends)",
                     "POST /lazy action=wake",
                     "POST /mine",
                     "the first byte, if the orbit activator is in front"],
        "on_wake": "the next block prices the whole sleep as idle slots "
                   "(lazy.decay) and settles rent per entry from rent_from — "
                   "no catch-up blocks, nothing to replay",
        "authoritative": False,
        "truth": "blocks.jsonl",
    }


def write_manifest(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)


def read_manifest(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
