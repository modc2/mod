"""The chain: blocks, the mempool, and the node that puts them together.

A block is a header, a list of signed transactions and the receipts they
produced. The header commits to the state root after execution, so a replay
that disagrees about a single rent settlement produces a different root and
says so. Every hash is SHA3-256 and every signature is ML-DSA — including the
proposer's signature over the header, which is what makes a block a claim by
somebody rather than a file on disk.

Consensus is MODULAR (consensus.py): the rule for who may extend the chain
is an engine — authority, round-robin PoA, BFT quorum, proof of stake, SHA3
proof of work, or a plugin — and the chain migrates between engines with a
`migrate` field in a block header, authorised by the engine it replaces. Every
seal and every vote is a signature under one of the chain's post-quantum key
types. A chain started before this existed is a legacy authority epoch and
its blocks hash exactly as they did. What there still is not: gossip and fork
choice — one node, one log. `verify()` replays the whole chain from genesis,
rebuilds the consensus schedule from the headers, and checks every root,
seal and commit against the epoch that was live at its height.

Blocks are produced LAZILY (lazy.py): on demand, and not at all when there is
no demand. Rent is priced in wall-clock seconds rather than block height and
settles per entry at the moment something touches it, so nothing about the
state machine depends on how often blocks appear — which means a chain with an
empty mempool has nothing to produce, and a node with nothing to produce is
allowed to stop. After PARK_SECONDS of that, the node writes dormant.json and
declares itself safe to turn off; the first transaction after it comes back
produces the block that prices the whole sleep as the idle slots it was.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import consensus as CN                                         # noqa: E402
import keys as K                                                # noqa: E402
import lazy as LZ                                               # noqa: E402
import state as S                                               # noqa: E402
from pq import algos                                            # noqa: E402
from pq import complexity as CPLX                               # noqa: E402
from state import State, StateError, canonical, merkle_root, sha3  # noqa: E402

DATA_DIR = os.path.expanduser(os.environ.get("POSTQUANT_DATA_DIR",
                                             "~/.mod/postquant"))
CHAIN_ID = os.environ.get("POSTQUANT_CHAIN_ID", "postquant-1")
BLOCK_SECONDS = int(os.environ.get("POSTQUANT_BLOCK_SECONDS", 5))
HEARTBEAT_SECONDS = int(os.environ.get("POSTQUANT_HEARTBEAT", 60))
GENESIS_SUPPLY = int(os.environ.get("POSTQUANT_GENESIS_SUPPLY",
                                    1_000_000)) * S.PQ
MEMPOOL_MAX = 5_000
MAX_TXS_PER_BLOCK = 512


def chain_dir(chain_id=CHAIN_ID):
    return os.path.join(DATA_DIR, chain_id)


# ── blocks ────────────────────────────────────────────────────────


def block_hash(header) -> str:
    return sha3(b"pq-block\x00", canonical(header)).hex()


def tx_root(txs) -> str:
    return merkle_root([bytes.fromhex(t["hash"]) for t in txs]).hex()


class Node:
    """One chain, on disk, with a mempool in front of it."""

    def __init__(self, chain_id=CHAIN_ID, data_dir=None, validator=None,
                 autoload=True):
        self.chain_id = chain_id
        self.dir = data_dir or chain_dir(chain_id)
        self.blocks_file = os.path.join(self.dir, "blocks.jsonl")
        self.state_file = os.path.join(self.dir, "state.json")
        self.genesis_file = os.path.join(self.dir, "genesis.json")
        self.lock = threading.RLock()
        self.mempool = {}
        self.rejected = []                 # last few drops, for the console
        self._tx_index: dict = {}          # tx_hash -> block height, O(1) lookup
        self._block_index: dict = {}       # block_hash -> height, O(1) lookup
        self.state = State(chain_id)
        self.blocks = []                   # headers only; bodies live on disk
        self._cached_tip_hash: str | None = None
        self.last_block_time = 0
        self._validator_name = validator or os.environ.get(
            "POSTQUANT_VALIDATOR", "validator")
        self.validator = None
        self.schedule = []                 # consensus epochs, oldest first
        self.pending_migration = None      # an epoch waiting for its block
        self.proposal = None               # a block waiting for its quorum
        self.consensus_note = ""           # why the last produce() stalled
        self._round_floor = (0, 0)         # (height, first round to try)
        self.pending_file = os.path.join(self.dir, "consensus_pending.json")
        self.pulse = LZ.Pulse()            # measured demand, for the tier plan
        self.dormant = False               # storage mode: safe to turn off
        self.dormant_since = 0
        self.dormant_file = os.path.join(self.dir, "dormant.json")
        if autoload:
            self.open()

    # ── lifecycle ─────────────────────────────────────────────────

    def open(self):
        """Load an existing chain, or lay down a genesis block for a new one."""
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        if os.path.exists(self.genesis_file):
            with open(self.genesis_file) as f:
                self.genesis = json.load(f)
            self.validator = K.get(self.genesis.get("validator_name"),
                                   required=False)
            self._load()
        else:
            self._init_genesis()
        self._load_schedule()
        # A node that was parked when it stopped comes back parked. Nothing
        # is asking it for a block — if something were, it would have been
        # woken by the request that asked — so it resumes storage mode and
        # the first write unparks it.
        parked = LZ.read_manifest(self.dormant_file) if LZ.LAZY else None
        if parked and parked.get("height") == self.blocks[-1]["height"]:
            self.dormant = True
            self.dormant_since = int(parked.get("since") or time.time())
        elif parked:
            try:
                os.remove(self.dormant_file)
            except OSError:
                pass
        return self.head()

    def _load_schedule(self):
        """Epoch 0 from genesis, then every `migrate` a header carried."""
        self.schedule = [CN.genesis_epoch(self.genesis)]
        for h in self.blocks:
            if h.get("migrate"):
                self.schedule.append(h["migrate"])
        self.pending_migration = None
        if os.path.exists(self.pending_file):
            try:
                with open(self.pending_file) as f:
                    self.pending_migration = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.pending_migration = None

    def _init_genesis(self):
        w = K.get(self._validator_name, required=False)
        if w is None:
            w = K.create(self._validator_name)
            w = K.get(self._validator_name)
        self.validator = w
        engine = os.environ.get("POSTQUANT_CONSENSUS", "")
        spec = None
        if engine and engine != "legacy":
            spec = CN.make_epoch(engine, [] if engine == "pow" else
                                 [w["name"]], from_height=0)
        genesis = {
            "chain_id": self.chain_id,
            "created": int(time.time()),
            "validator_name": w["name"],
            "validator": w["address"],
            "validator_pk": w["pk"],
            "scheme": w["scheme"],
            "base_fee": S.BASE_FEE_INITIAL,
            "alloc": {w["address"]: GENESIS_SUPPLY},
            "rules": {
                "signatures": {
                    "proposer": w["scheme"],
                    "accepted": [n for n in algos.names()
                                 if algos.allowed(n)],
                    "gate": "complexity-priced — algorithms are pluggable "
                            "(pq/algos.d/) and any key type may witness, "
                            "classical included, if its best-known "
                            f"classical attack clears the 2^{CPLX.MIN_BITS} "
                            "floor and the entropy probe passes; quantum "
                            "cost is printed per scheme (pq_complexity), "
                            "not used to refuse unless POSTQUANT_REQUIRE_PQ "
                            "is set",
                },
                "kem": "ML-KEM-768 (FIPS 203)",
                "hash": "SHA3-256",
                "consensus": (f"modular — epoch 0 = {spec['engine']}"
                              if spec else
                              "single proposer (authority) — engines are "
                              "modular and migrate on chain (consensus.py)"),
                "gas": {"tx_base": S.GAS_TX_BASE, "key_byte": S.GAS_KEY_BYTE,
                        "value_byte": S.GAS_VALUE_BYTE,
                        "value_byte_hash": S.GAS_VALUE_BYTE_HASH,
                        "witness_byte": S.GAS_WITNESS_BYTE,
                        "account_new": S.GAS_ACCOUNT_NEW,
                        "market_op": S.GAS_MARKET_OP},
                "rent": {"per_byte_hour": S.RENT_PER_BYTE_HOUR,
                         "entry_overhead": S.ENTRY_OVERHEAD,
                         "min_lease_seconds": S.MIN_LEASE_SECONDS,
                         "bounty_bps": S.BOUNTY_BPS},
                "fee_market": {"target_state_bytes": S.STATE_TARGET_BYTES,
                               "max_change_denominator":
                                   S.BASE_FEE_MAX_CHANGE_DEN,
                               "min_base_fee": S.BASE_FEE_MIN,
                               "slot_seconds": LZ.SLOT_SECONDS,
                               "idle_slots": "a gap between blocks is priced "
                                             "as the empty slots it skipped; "
                                             "a block carrying lazy=1 must "
                                             "prove its fee from its parent "
                                             "header"},
                "liveness": ("lazy — no block without something to put in "
                             "it, and a node with nothing to produce may be "
                             "switched off; the tip is the commitment"
                             if LZ.LAZY else
                             f"heartbeat — an empty block every "
                             f"{HEARTBEAT_SECONDS}s"),
            },
        }
        if spec:
            genesis["consensus"] = spec
        with open(self.genesis_file, "w") as f:
            json.dump(genesis, f, indent=2)
        self.genesis = genesis

        self.state = State(self.chain_id, genesis["alloc"], genesis["base_fee"])
        now = genesis["created"]
        self.state.time = now
        header = {
            "chain_id": self.chain_id, "height": 0,
            "parent": "0" * 64, "timestamp": now,
            "proposer": w["address"], "base_fee": genesis["base_fee"],
            "gas_used": 0, "state_bytes": 0, "tx_count": 0,
            "tx_root": merkle_root([]).hex(),
            "genesis_root": sha3(canonical(genesis)).hex(),
            "state_root": self.state.root(),
        }
        block = self._seal(header, [], [])
        self._cached_tip_hash = block["hash"]
        self._block_index[block["hash"]] = 0
        with open(self.blocks_file, "w") as f:
            f.write(json.dumps(block, separators=(",", ":")) + "\n")
        self.blocks = [header]
        self.last_block_time = now
        self._snapshot()

    def _seal(self, header, txs, receipts, wallet=None):
        """Hash the header, then sign the hash. The proposer signs what the
        header commits to, so the signature covers the transactions and the
        resulting state root without carrying either."""
        h = block_hash(header)
        block = {"header": header, "hash": h, "txs": txs, "receipts": receipts}
        wallet = wallet or self.validator
        if wallet:
            algo = algos.get(wallet["scheme"])
            block["sig"] = algo.sign(K.secret_key(wallet), bytes.fromhex(h),
                                     CN.BLOCK_CONTEXT).hex()
            block["proposer_pk"] = wallet["pk"]
            block["proposer_scheme"] = wallet["scheme"]
        return block

    # ── persistence ───────────────────────────────────────────────

    def _load(self):
        """Snapshot for speed, blocks for truth. If they disagree about height,
        the snapshot is thrown away and the chain replayed — a stale snapshot
        is the one bug that would silently fork this node from its own log."""
        self.blocks = []
        heights = []
        if os.path.exists(self.blocks_file):
            with open(self.blocks_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        b = json.loads(line)
                        self.blocks.append(b["header"])
                        heights.append(b["header"]["height"])
                        self._cached_tip_hash = b["hash"]
                        self._block_index[b["hash"]] = b["header"]["height"]
                        for tx in b["txs"]:
                            self._tx_index[tx["hash"]] = b["header"]["height"]
        if not self.blocks:
            return self._init_genesis()
        snap = None
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    snap = json.load(f)
            except json.JSONDecodeError:
                snap = None
        tip = self.blocks[-1]
        if snap and snap.get("height") == tip["height"]:
            self.state = State.restore(snap)
            if self.state.root() != tip["state_root"]:
                self.state = self.replay()
        else:
            self.state = self.replay()
        self.last_block_time = tip["timestamp"]

    def _snapshot(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state.snapshot(), f, separators=(",", ":"))
        os.replace(tmp, self.state_file)

    def read_blocks(self, start=0, limit=None):
        out = []
        if not os.path.exists(self.blocks_file):
            return out
        with open(self.blocks_file) as f:
            for i, line in enumerate(f):
                if i < start:
                    continue
                line = line.strip()
                if not line:
                    continue
                b = json.loads(line)
                out.append(b)
                if limit and len(out) >= limit:
                    break
        return out

    def read_blocks_tail(self, n=10):
        """The last n blocks, oldest first. Heights are line numbers, so the
        tail is a seek by height rather than a scan of the whole log."""
        start = max(0, self.blocks[-1]["height"] - int(n) + 1)
        return self.read_blocks(start=start)

    # ── replay and audit ──────────────────────────────────────────

    def replay(self, verify_signatures=False, upto=None):
        """Re-execute the chain from genesis and return the resulting state.

        Signature checks are off by default: a transaction's witness was
        verified when it entered the mempool, and re-verifying every ML-DSA
        signature costs ~80ms each. Pass verify_signatures=True for a full
        audit — that is the run that proves the log, not just the execution.
        """
        with open(self.genesis_file) as f:
            genesis = json.load(f)
        st = State(self.chain_id, genesis["alloc"], genesis["base_fee"])
        for block in self.read_blocks():
            h = block["header"]
            if upto is not None and h["height"] > upto:
                break
            if h["height"] == 0:
                st.time = h["timestamp"]
                st.height = 0
                continue
            st.base_fee = h["base_fee"]
            now = h["timestamp"]
            for tx in block["txs"]:
                if verify_signatures:
                    known = st.accounts.get(tx["body"]["from"], {}).get("pk")
                    if not K.verify_tx(tx, known):
                        raise StateError(
                            f"block {h['height']}: transaction {tx['hash'][:12]} "
                            "does not verify", code="bad_witness")
                st.apply(tx, now, proposer=h["proposer"])
            st.height = h["height"]
            st.time = now
            # The header's state_root is taken before the fee market moves, so
            # the next base fee is applied only after the root is settled.
            st.base_fee = st.next_base_fee(h["state_bytes"])
        return st

    def verify(self, signatures=False):
        """Replay every block and check every root and every proposer
        signature. The answer a node gives about itself is worth what this
        check says it is worth."""
        with open(self.genesis_file) as f:
            genesis = json.load(f)
        st = State(self.chain_id, genesis["alloc"], genesis["base_fee"])
        parent = "0" * 64
        parent_header = None
        checked = bad = 0
        problems = []
        schedule = [CN.genesis_epoch(genesis)]
        scheme0 = genesis.get("scheme", K.SCHEME)
        for block in self.read_blocks():
            h = block["header"]
            checked += 1
            if h["parent"] != parent:
                problems.append(f"height {h['height']}: parent mismatch")
                bad += 1
            if block_hash(h) != block["hash"]:
                problems.append(f"height {h['height']}: header hash mismatch")
                bad += 1
            if block["txs"] and tx_root(block["txs"]) != h["tx_root"]:
                problems.append(f"height {h['height']}: tx root mismatch")
                bad += 1
            if h["height"] == 0:
                if block.get("sig") and not CN.seal_ok(block, scheme0):
                    problems.append("height 0: proposer signature does not "
                                    "verify")
                    bad += 1
            else:
                epoch = CN.epoch_at(schedule, h["height"])
                try:
                    found = CN.check_block(epoch, block, parent, st.balance,
                                           scheme0, parent_header)
                except StateError as e:
                    found = [e.message]
                for p in found:
                    problems.append(f"height {h['height']}: {p}")
                    bad += 1
            if h.get("migrate"):
                m = h["migrate"]
                try:
                    CN.check_epoch(m)
                    if m["from_height"] <= h["height"] or \
                            m["from_height"] <= schedule[-1]["from_height"]:
                        raise StateError("activates in the past")
                    schedule.append(m)
                except (StateError, KeyError, TypeError) as e:
                    problems.append(f"height {h['height']}: migration to "
                                    f"{m.get('engine') if isinstance(m, dict) else m}"
                                    f" is invalid — {e}")
                    bad += 1
            if h["height"] == 0:
                st.time, st.height = h["timestamp"], 0
            else:
                st.base_fee = h["base_fee"]
                for tx in block["txs"]:
                    if signatures:
                        known = st.accounts.get(tx["body"]["from"], {}).get("pk")
                        if not K.verify_tx(tx, known):
                            problems.append(
                                f"height {h['height']}: witness "
                                f"{tx['hash'][:12]} does not verify")
                            bad += 1
                    try:
                        st.apply(tx, h["timestamp"], proposer=h["proposer"])
                    except StateError as e:
                        problems.append(f"height {h['height']}: {tx['hash'][:12]}"
                                        f" no longer applies — {e}")
                        bad += 1
                st.height, st.time = h["height"], h["timestamp"]
            if st.root() != h["state_root"]:
                problems.append(f"height {h['height']}: state root mismatch")
                bad += 1
            if h["height"]:
                st.base_fee = st.next_base_fee(h["state_bytes"])
            parent = block["hash"]
            parent_header = h
        return {"ok": bad == 0, "blocks": checked, "problems": problems,
                "signatures_checked": bool(signatures),
                "epochs": len(schedule),
                "tip_root": st.root(),
                "matches_live_state": st.root() == self.state.root()}

    # ── the mempool ───────────────────────────────────────────────

    def submit(self, tx, now=None):
        """Validate a transaction's witness and shape, then queue it.

        The mempool check is deliberately strict about the witness and lenient
        about the balance: a signature that does not verify is never a
        transaction, but a balance can change before the block is built, and
        rejecting on it here would drop transactions that would have been fine.
        """
        with self.lock:
            body = tx.get("body")
            if not isinstance(body, dict):
                raise StateError("transaction needs a body", code="malformed")
            if body.get("chain_id") != self.chain_id:
                raise StateError(f"transaction is for chain "
                                 f"{body.get('chain_id')!r}, this node is "
                                 f"{self.chain_id!r}", code="wrong_chain")
            if body.get("kind") not in S.KINDS:
                raise StateError(f"kind must be one of {', '.join(S.KINDS)}",
                                 code="bad_kind")
            if not K.valid_address(body.get("from", "")):
                raise StateError(f"{body.get('from')!r} is not an address",
                                 code="bad_address")
            acct = self.state.accounts.get(body["from"], {})
            known = acct.get("pk")
            # The complexity gate, with its reasons out loud — verify_tx would
            # refuse these anyway, but "signature does not verify" is the
            # wrong error for "your key type is not allowed here".
            scheme = tx.get("scheme") or acct.get("scheme") or K.SCHEME
            gate = algos.refusal(scheme)
            if gate is not None:
                code, message, status = gate
                raise StateError(message, code=code, status=status)
            if known is None and not tx.get("pk"):
                raise StateError(
                    "this address has never transacted, so the chain does not "
                    "know its public key — include pk with the first "
                    "transaction from an address", code="pk_required")
            # Timed, not modelled: this is the one unavoidable cost of a
            # post-quantum chain (every node re-runs every witness inside its
            # key type's wasm verifier) and it is what lazy.py converts into
            # a core count when the chain is asked to scale up.
            started = time.perf_counter()
            ok = K.verify_tx(tx, known)
            self.pulse.witness(scheme, len(tx.get("sig", "")) // 2,
                               (time.perf_counter() - started) * 1000.0)
            if not ok:
                raise StateError("signature does not verify for this address",
                                 code="bad_witness", status=401)
            tx["hash"] = K.tx_hash(tx)
            if tx["hash"] in self.mempool:
                return {"queued": False, "hash": tx["hash"],
                        "reason": "already in the mempool"}
            nonce = int(body.get("nonce", 0))
            if nonce < acct.get("nonce", 0):
                raise StateError(
                    f"nonce {nonce} is already used — {body['from']} is at "
                    f"{acct.get('nonce', 0)}", code="bad_nonce")
            if len(self.mempool) >= MEMPOOL_MAX:
                raise StateError("mempool is full", code="mempool_full",
                                 status=503)
            # Dry-run it against a copy so an obviously doomed transaction is
            # refused at submit time, where the caller can still fix it.
            probe = self._fork_state()
            try:
                probe.apply(json.loads(json.dumps(tx)),
                            int(now or time.time()),
                            proposer=self._proposer())
            except StateError as e:
                if e.code not in ("bad_nonce",):     # a queued predecessor fixes those
                    raise
            self.mempool[tx["hash"]] = tx
            return {"queued": True, "hash": tx["hash"],
                    "kind": body["kind"], "from": body["from"],
                    "mempool": len(self.mempool)}

    def _fork_state(self):
        return State.restore(json.loads(json.dumps(self.state.snapshot())))

    def next_nonce(self, address):
        """The account's nonce plus whatever it already has queued, so two
        transactions sent back to back do not collide."""
        base = self.state.accounts.get(address, {}).get("nonce", 0)
        queued = sum(1 for t in self.mempool.values()
                     if t["body"]["from"] == address)
        return base + queued

    def make_tx(self, wallet, kind, max_fee=None, tip=0, nonce=None, **fields):
        """Build and sign a transaction against this node's current fee market.

        max_fee defaults to twice the base fee: the fee floats between signing
        and inclusion, and a signature cannot be adjusted afterwards, so a
        transaction that quotes the base fee exactly is one busy block away
        from being unincludable.
        """
        addr = wallet["address"]
        body = {"chain_id": self.chain_id, "kind": kind, "from": addr,
                "nonce": self.next_nonce(addr) if nonce is None else int(nonce),
                "max_fee": int(self.state.base_fee * 2 if max_fee is None
                               else max_fee),
                "tip": int(tip),
                **{k: v for k, v in fields.items() if v is not None}}
        first = self.state.accounts.get(addr, {}).get("pk") is None
        return K.sign_tx(wallet, body, include_pk=first)

    def send(self, wallet, kind, now=None, **fields):
        """Sign, submit and report. The transaction is queued, not mined —
        call produce() or let the block loop pick it up. `now` is the timestamp
        the mempool dry-run judges the transaction at; it exists because a
        sweep is only valid once an entry has actually expired."""
        tx = self.make_tx(wallet, kind, **fields)
        return {**self.submit(tx, now=now), "tx": tx}

    def _proposer(self):
        """Who would collect tips if a block were built now — the mempool's
        dry run needs a name, and a wrong guess only moves a tip in a probe."""
        try:
            epoch = self.epoch()
            if not epoch.get("legacy"):
                rnd, w = self._pick_proposer(
                    epoch, self.blocks[-1]["height"] + 1, int(time.time()),
                    self.state.balance)
                if w:
                    return w["address"]
        except StateError:
            pass
        return self.validator["address"] if self.validator else \
            self.genesis["validator"]

    def pending(self):
        return list(self.mempool.values())

    # ── block production ──────────────────────────────────────────

    def _order(self, txs):
        """Deterministic ordering: highest tip first, then a sender's own
        nonces in order, then by hash so two nodes never disagree."""
        return sorted(txs, key=lambda t: (-int(t["body"].get("tip", 0)),
                                          t["body"]["from"],
                                          int(t["body"].get("nonce", 0)),
                                          t["hash"]))

    def epoch(self, height=None):
        if height is None:
            height = self.blocks[-1]["height"] + 1
        return CN.epoch_at(self.schedule, height)

    def _local(self, address):
        w = K.get(address, required=False)
        return w if w and w["address"] == address else None

    def _miner(self, epoch):
        w = K.get(os.environ.get("POSTQUANT_MINER") or
                  (self.validator or {}).get("name"), required=False)
        if w and CN.key_refusal(w["scheme"], epoch.get("require_pq", True)):
            return None
        return w

    def _pick_proposer(self, epoch, height, now, stake):
        """(round, wallet) for the first round this node may propose in, or
        (round, None). Round r opens r*ROUND_SECONDS after the parent — the
        timeout that skips a silent proposer."""
        if epoch.get("legacy"):
            return 0, self.validator
        eng = CN.get(epoch["engine"])
        if eng.proposer(epoch, height, 0, self._tip_hash(), stake) is None \
                and not eng.needs_validators:
            return 0, self._miner(epoch)
        floor = self._round_floor[1] if self._round_floor[0] == height else 0
        elapsed = max(0, now - self.blocks[-1]["timestamp"])
        rounds = max(len(epoch["validators"]), 1) * 4
        for rnd in range(floor, floor + rounds):
            if rnd > 0 and elapsed < rnd * CN.ROUND_SECONDS:
                break
            want = eng.proposer(epoch, height, rnd, self._tip_hash(), stake)
            w = self._local(want) if want else None
            if w:
                return rnd, w
        return floor, None

    def produce(self, now=None, force=False):
        """Build, execute, seal and persist one block — or, under a voting
        engine whose quorum this node cannot meet alone, park it as a
        proposal that vote() completes."""
        with self.lock:
            if self.proposal:
                return None
            now = int(now or time.time())
            tip = self.blocks[-1]
            now = max(now, tip["timestamp"] + 1)
            height = tip["height"] + 1
            candidates = self._order(self.pending())[:MAX_TXS_PER_BLOCK]
            migrate = self.pending_migration
            if not candidates and not force and not migrate:
                return None

            epoch = self.epoch(height)
            eng = CN.get(epoch["engine"])
            pre = self.state
            # The fee this block charges. Under lazy consensus the producer
            # derives it with the SAME function the verifier re-derives it
            # with (consensus.lazy_problems), from the parent header rather
            # than from this node's running state: the slots the chain slept
            # through have to be priced as the empty blocks they would have
            # been, and a rule only the producer can evaluate is not a rule.
            base_fee = (LZ.expected_base_fee(tip, {"timestamp": now})
                        if LZ.LAZY else pre.base_fee)
            rnd, wallet = self._pick_proposer(epoch, height, now, pre.balance)
            if not epoch.get("legacy") and wallet is None:
                self.consensus_note = (
                    f"height {height}: no {eng.name} proposer key held here "
                    f"(round {rnd}) — waiting for the round timeout")
                return None
            proposer = wallet["address"] if wallet else self._proposer()

            st = self._fork_state()
            st.base_fee = base_fee
            included, receipts, dropped = [], [], []
            gas_used = growth = 0
            for tx in candidates:
                try:
                    receipt = st.apply(tx, now, proposer=proposer)
                except StateError as e:
                    dropped.append({"hash": tx["hash"], "reason": e.message,
                                    "code": e.code,
                                    "kind": tx["body"]["kind"],
                                    "from": tx["body"]["from"]})
                    st = self._replay_into(st, included, now, proposer,
                                           base_fee)
                    continue
                if gas_used + receipt["gas"] > S.BLOCK_GAS_LIMIT:
                    st = self._replay_into(st, included, now, proposer,
                                           base_fee)
                    break
                gas_used += receipt["gas"]
                growth += receipt.get("state_bytes", 0)
                receipt["hash"] = tx["hash"]
                included.append(tx)
                receipts.append(receipt)

            st.height = height
            st.time = now
            header = {
                "chain_id": self.chain_id,
                "height": height,
                "parent": self._tip_hash(),
                "timestamp": now,
                "proposer": proposer,
                "base_fee": base_fee,
                "gas_used": gas_used,
                "state_bytes": growth,
                "tx_count": len(included),
                "tx_root": tx_root(included),
                "state_root": st.root(),
            }
            if LZ.LAZY:
                # The marker is what puts this block's fee under replay: a
                # verifier seeing it re-derives base_fee from the parent and
                # refuses the block if it disagrees. Blocks produced before
                # lazy consensus existed carry no marker and are checked
                # exactly as they always were.
                header["lazy"] = 1
            if not epoch.get("legacy"):
                header.update(consensus=eng.name, epoch=CN.epoch_id(epoch),
                              round=rnd)
            if migrate:
                m = dict(migrate)
                m["from_height"] = max(int(m["from_height"]), height + 1)
                header["migrate"] = m
            header.update(eng.header_fields(epoch, header)
                          if not epoch.get("legacy") else {})
            block = self._seal(header, included, receipts, wallet)
            work = {"block": block, "st": st, "growth": growth,
                    "gas_used": gas_used, "dropped": dropped,
                    "epoch": epoch, "round": rnd, "created": int(time.time()),
                    "stake": pre.balance}
            if eng.votes and not epoch.get("legacy"):
                votes = [{"validator": v["address"],
                          "sig": CN.sign_vote(w, self.chain_id, height, rnd,
                                              block["hash"])}
                         for v in epoch["validators"]
                         for w in [self._local(v["address"])] if w]
                block["commit"] = {"round": rnd, "votes": votes}
                got, need, _ = CN.tally(epoch, votes, self.chain_id, height,
                                        rnd, block["hash"], pre.balance)
                if got < need:
                    self.proposal = work
                    self.consensus_note = (
                        f"height {height}: proposal {block['hash'][:12]} has "
                        f"{got}/{need} voting power — waiting for votes")
                    return {"proposed": True, "block": header,
                            "hash": block["hash"], "power": got,
                            "needed": need, "included": len(included),
                            "dropped": dropped, "gas_used": gas_used,
                            "state_bytes": growth,
                            "next_base_fee": self.state.base_fee}
            return self._commit(work)

    def _commit(self, work):
        block, st = work["block"], work["st"]
        header = block["header"]
        growth = work["growth"]
        self._cached_tip_hash = block["hash"]
        self._block_index[block["hash"]] = header["height"]
        with open(self.blocks_file, "a") as f:
            f.write(json.dumps(block, separators=(",", ":")) + "\n")

        st.base_fee = st.next_base_fee(growth)
        self.state = st
        self.blocks.append(header)
        self.last_block_time = header["timestamp"]
        self.pulse.block(header)
        if header.get("tx_count"):
            self.wake("a block was produced")
        self.proposal = None
        self.consensus_note = ""
        for tx in block["txs"]:
            self._tx_index[tx["hash"]] = header["height"]
            self.mempool.pop(tx["hash"], None)
        for d in work["dropped"]:
            self.mempool.pop(d["hash"], None)
            self.rejected.append({**d, "height": header["height"]})
        self.rejected = self.rejected[-50:]
        if header.get("migrate"):
            self.schedule.append(header["migrate"])
            self.pending_migration = None
            try:
                os.remove(self.pending_file)
            except FileNotFoundError:
                pass
        self._snapshot()
        return {"block": header, "hash": block["hash"],
                "included": len(block["txs"]), "dropped": work["dropped"],
                "gas_used": work["gas_used"], "state_bytes": growth,
                "next_base_fee": st.base_fee}

    # ── consensus: migrate and vote ───────────────────────────────

    def migrate(self, engine, validators=(), params=None, at=None,
                require_pq=True):
        """Queue a switch of consensus engine. It rides in the NEXT block's
        header, so the current engine's seal (and quorum) is what authorises
        it; it takes effect from height `at` (default: the block after)."""
        with self.lock:
            nxt = self.blocks[-1]["height"] + 1
            at = int(at) if at not in (None, "") else nxt + 1
            if at <= nxt:
                raise StateError(f"activation height must be > {nxt} — the "
                                 "migration needs a block of its own first",
                                 code="bad_height")
            if self.proposal:
                raise StateError("a proposal is waiting for votes — vote or "
                                 "let it time out first", code="busy",
                                 status=409)
            epoch = CN.make_epoch(engine, validators, params, at, require_pq)
            eng = CN.get(engine)
            # Liveness: refuse a schedule this node could never extend.
            if eng.needs_validators:
                held = [v for v in epoch["validators"]
                        if self._local(v["address"])]
                if not held:
                    raise StateError(
                        f"this node holds none of the {engine} validator "
                        "keys, so it could never propose again — include "
                        "at least one local wallet", code="no_local_validator")
                if engine == "pos" and not sum(
                        self.state.balance(v["address"]) for v in held):
                    raise StateError(
                        "no local validator holds stake — transfer PQ to one "
                        "first (pos weighs votes by balance)",
                        code="no_stake")
            elif self._miner(epoch) is None:
                raise StateError("no local miner key passes the validator "
                                 "key rule", code="no_local_validator")
            self.pending_migration = epoch
            with open(self.pending_file, "w") as f:
                json.dump(epoch, f)
            return epoch

    def cancel_migration(self):
        with self.lock:
            was = self.pending_migration
            self.pending_migration = None
            try:
                os.remove(self.pending_file)
            except FileNotFoundError:
                pass
            return was

    def vote(self, validator=None, sig=None, block_hash=None, wallet=None):
        """Add one validator's vote to the waiting proposal; commit once the
        quorum is met. wallet= signs with a key this node holds."""
        with self.lock:
            p = self.proposal
            if not p:
                raise StateError("no proposal is waiting for votes",
                                 code="no_proposal", status=404)
            b = p["block"]
            h = b["header"]
            if block_hash and block_hash != b["hash"]:
                raise StateError(f"stale vote: the proposal is "
                                 f"{b['hash']}", code="stale_vote",
                                 status=409)
            if wallet:
                w = K.get(wallet)
                validator = w["address"]
                sig = CN.sign_vote(w, self.chain_id, h["height"], p["round"],
                                   b["hash"])
            votes = list(b["commit"]["votes"])
            if any(v["validator"] == validator for v in votes):
                return {"accepted": False, "reason": "already voted",
                        **self.proposal_view()}
            cand = votes + [{"validator": validator, "sig": sig}]
            got, need, probs = CN.tally(p["epoch"], cand, self.chain_id,
                                        h["height"], p["round"], b["hash"],
                                        p["stake"])
            if probs:
                raise StateError(probs[-1], code="bad_vote", status=401)
            b["commit"]["votes"] = cand
            if got >= need:
                out = self._commit(p)
                return {"accepted": True, "committed": True, **out}
            return {"accepted": True, "committed": False,
                    **self.proposal_view()}

    def proposal_view(self):
        p = self.proposal
        if not p:
            return {"proposal": None}
        b = p["block"]
        h = b["header"]
        got, need, _ = CN.tally(p["epoch"], b["commit"]["votes"],
                                self.chain_id, h["height"], p["round"],
                                b["hash"], p["stake"])
        voted = {v["validator"] for v in b["commit"]["votes"]}
        return {"proposal": {
            "height": h["height"], "round": p["round"], "hash": b["hash"],
            "proposer": h["proposer"], "power": got, "needed": need,
            "tx_count": h["tx_count"],
            "vote_message": CN.vote_message(self.chain_id, h["height"],
                                            p["round"], b["hash"]).hex(),
            "vote_context": CN.VOTE_CONTEXT.decode(),
            "waiting_on": [v["address"] for v in p["epoch"]["validators"]
                           if v["address"] not in voted],
            "age": int(time.time()) - p["created"],
            "times_out_in": max(0, CN.ROUND_SECONDS -
                                (int(time.time()) - p["created"]))}}

    def _expire_proposal(self, now):
        p = self.proposal
        if p and now - p["created"] >= CN.ROUND_SECONDS:
            h = p["block"]["header"]["height"]
            self._round_floor = (h, p["round"] + 1)
            self.proposal = None
            self.consensus_note = (f"height {h}: round {p['round']} timed "
                                   "out without a quorum — next round")

    def _replay_into(self, _stale, included, now, proposer, base_fee=None):
        """A transaction failed mid-block. Rebuild the block's state from the
        transactions that did succeed rather than trusting a state that a
        half-applied transaction may have touched."""
        st = self._fork_state()
        if base_fee is not None:
            st.base_fee = base_fee
        for tx in included:
            st.apply(tx, now, proposer=proposer)
        return st

    def _tip_hash(self):
        if self._cached_tip_hash is not None:
            return self._cached_tip_hash
        blocks = self.read_blocks(start=self.blocks[-1]["height"], limit=1)
        return blocks[0]["hash"] if blocks else "0" * 64

    def maybe_produce(self, now=None):
        """Called by the block loop: produce if there is work, and otherwise
        do nothing whatsoever.

        Lazy consensus is this method. There is no tick to keep up with — if
        the mempool is empty and no migration is waiting then there is no
        block to produce, so none is produced and the tip stays where it is,
        final, for as long as nobody wants anything. What used to live here
        was a heartbeat: an empty block every HEARTBEAT_SECONDS, justified by
        rent. Rent never needed it. An entry's escrow is drained by
        `rent_for(bytes, now - rent_from)` whenever something next touches it
        (state.py `_settle`), so a lease runs down on the wall clock whether
        or not anyone is awake to watch, and expiry is a timestamp
        comparison. The only thing the heartbeat really did was let the fee
        market cool off, and produce() now prices an idle gap as the slots it
        skipped. POSTQUANT_LAZY=0 puts the heartbeat back for anyone who
        wants a chain that ticks.
        """
        now = int(now or time.time())
        with self.lock:
            self._expire_proposal(int(time.time()))
        if self.mempool or self.pending_migration:
            self.wake("work arrived")
            return self.produce(now, force=bool(self.pending_migration))
        if not LZ.LAZY and HEARTBEAT_SECONDS > 0 and \
                now - self.last_block_time >= HEARTBEAT_SECONDS:
            return self.produce(now, force=True)
        self._maybe_park(now)
        return None

    def run(self, stop=None):
        """The block loop. One thread, one proposer, and a cadence that
        follows demand: the tier's block time while the chain is awake,
        PARK_POLL once it has parked. The wait is on the stop event rather
        than a sleep, so a node that has gone quiet for an hour still shuts
        down the instant it is asked to."""
        while not (stop and stop.is_set()):
            try:
                self.maybe_produce()
            except Exception as e:                 # never let a bad block stop
                print(f"postquant: block production failed — {e}", flush=True)
            wait = self.tick_seconds()
            if stop is not None:
                stop.wait(wait)
            else:
                time.sleep(wait)

    def tick_seconds(self):
        """How long the loop should sleep before looking at the mempool
        again. A dormant node looks once a minute; a busy one looks at its
        tier's cadence. Nothing here is consensus — it is how much CPU this
        process spends waiting."""
        if self.proposal:
            return max(1, min(BLOCK_SECONDS, CN.ROUND_SECONDS // 4))
        if self.mempool or self.pending_migration:
            return 1
        if self.dormant:
            return LZ.PARK_POLL
        return self.tier()["block_seconds"] or BLOCK_SECONDS

    # ── storage mode ──────────────────────────────────────────────

    def tier(self, now=None):
        """Which rung of the ladder the measured demand puts this chain on."""
        return LZ.tier_for(self.pulse.demand(now), dormant=self.dormant)

    def _maybe_park(self, now):
        if not LZ.LAZY or self.dormant or self.proposal:
            return
        if self.pulse.idle_for(now) >= LZ.PARK_SECONDS:
            self.park()

    def park(self, reason="idle"):
        """Enter storage mode: declare this node safe to turn off.

        No block is written. Parking is the ABSENCE of chain events, which is
        exactly why it is safe — there is nothing here for a replay to agree
        about, dormant.json is a manifest for whoever finds the directory,
        and blocks.jsonl stays the only thing anyone has to trust. The
        process may now exit. It comes back on the first byte (the orbit
        activator in front of it) or the first transaction, and the block
        that follows prices the whole sleep in one step.

        Dormant means "nothing to produce", not "not answering": reads are
        served out of the snapshot the whole time, and serving them does not
        unpark the node, because answering a question is not work a validator
        has to be awake for.
        """
        with self.lock:
            if self.proposal:
                raise StateError("a proposal is waiting for votes — parking "
                                 "now would abandon it", code="not_idle")
            self.dormant = True
            self.dormant_since = int(time.time())
            doc = LZ.manifest(self, self.dormant_since, reason)
            LZ.write_manifest(self.dormant_file, doc)
            return doc

    def wake(self, reason="activity"):
        """Leave storage mode. Idempotent and cheap, because every write path
        calls it: the thing that wakes this chain is somebody using it."""
        self.pulse.touch()
        if not self.dormant:
            return None
        with self.lock:
            slept = max(0, int(time.time()) - self.dormant_since)
            self.dormant = False
            self.dormant_since = 0
            self.pulse.woke_at = int(time.time())
            self.pulse.wakes += 1
            try:
                os.remove(self.dormant_file)
            except FileNotFoundError:
                pass
            return {"woke": True, "reason": reason, "slept_seconds": slept,
                    "idle_slots_to_price": LZ.idle_slots(
                        self.blocks[-1]["timestamp"], int(time.time()))}

    def lazy_view(self, now=None):
        """Lazy consensus, storage mode and the measured demand, in one
        answer: what the chain is doing, what it is not doing, and what it
        would cost to run if the load it has been seeing kept up."""
        now = int(now or time.time())
        demand = self.pulse.demand(now)
        tier = LZ.tier_for(demand, dormant=self.dormant)
        tip = self.blocks[-1]
        slots = LZ.idle_slots(tip["timestamp"], now)
        live_fee = (LZ.expected_base_fee(tip, {"timestamp": now})
                    if LZ.LAZY else self.state.base_fee)
        return {
            "lazy": LZ.LAZY,
            "mode": ("storage" if self.dormant else
                     "hot" if self.mempool else "idle"),
            "tip": {"height": tip["height"], "timestamp": tip["timestamp"],
                    "age": max(0, now - tip["timestamp"]),
                    "state_root": tip["state_root"]},
            "blocks_not_produced": slots,
            "saved": {
                "empty_blocks": slots,
                "note": f"a heartbeat chain would have appended {slots} "
                        f"empty headers since height {tip['height']}; this "
                        f"one appended none, and the state root is the same",
            },
            "base_fee": {
                "at_tip": tip["base_fee"],
                "now": live_fee,
                "floor": S.BASE_FEE_MIN,
                "slot_seconds": LZ.SLOT_SECONDS,
                "idle_slots": slots,
                "why": "the gap is priced as the empty slots it would have "
                       "been (lazy.decay, -1/8 per slot to the floor), so a "
                       "chain is never charging a congestion premium for "
                       "congestion that stopped while it was asleep",
            },
            "rent": {
                "settles": "lazily, per entry, from rent_from on the wall "
                           "clock — no block has to exist for a lease to run "
                           "down or for an entry to become sweepable",
                "needs_blocks": False,
            },
            "storage_mode": {
                "dormant": self.dormant,
                "since": self.dormant_since or None,
                "idle_for": self.pulse.idle_for(now),
                "parks_after": LZ.PARK_SECONDS,
                "parks_in": (None if self.dormant else
                             max(0, LZ.PARK_SECONDS -
                                 self.pulse.idle_for(now))),
                "safe_to_stop": self.dormant,
                "blocked_by": ("a proposal is waiting for votes"
                               if self.proposal else None),
                "manifest": self.dormant_file,
                "wakes": self.pulse.wakes,
                "poll_seconds": LZ.PARK_POLL if self.dormant
                else self.tick_seconds(),
            },
            "demand": demand,
            "tier": tier["name"],
            "tiers": LZ.TIER_NAMES,
            "plan": self.scale_plan(now, demand, tier),
        }

    def scale_plan(self, now=None, demand=None, tier=None):
        """What this chain would need to run the load it has been seeing.

        A recommendation, and only that. Widening a validator set is a signed
        migration on this chain (pq_consensus), and renting a machine spends
        somebody's money; neither is something a block loop should do to
        itself because a counter moved.
        """
        now = int(now or time.time())
        demand = demand if demand is not None else self.pulse.demand(now)
        tier = tier or LZ.tier_for(demand, dormant=self.dormant)
        log_bytes = 0
        try:
            log_bytes = os.path.getsize(self.blocks_file)
        except OSError:
            pass
        req = LZ.requirements(demand, tier, self.state.state_bytes(),
                              log_bytes)
        epoch = self.epoch()
        have = len(epoch.get("validators") or []) or 1
        want = max(1, req["nodes"])
        out = {**req, "validators": {"have": have, "want": want,
                                     "engine": epoch["engine"]}}
        if self.dormant:
            out["recommend"] = ("nothing — the chain is dormant. Stop the "
                               "process if you want; the tip is the "
                               "commitment and the first byte wakes it")
        elif want > have:
            engine = epoch["engine"] if epoch["engine"] in ("bft", "pos") \
                else "bft"
            out["recommend"] = (
                f"{tier['name']} wants {want} validators and this chain has "
                f"{have}: pq_consensus action=migrate engine={engine} "
                f"create={want} — the switch rides in a block sealed by the "
                f"engine it replaces, so nothing forks")
            out["migration"] = {"action": "migrate", "engine": engine,
                                "create": want}
        else:
            out["recommend"] = (
                f"{tier['name']} is covered by the {have} validator(s) this "
                f"chain already has; {req['per_node']['vcpu']} vcpu and "
                f"{req['per_node']['ram_mb']} MB each")
        return out

    # ── reads ─────────────────────────────────────────────────────

    def head(self):
        tip = self.blocks[-1]
        now = int(time.time())
        return {"chain_id": self.chain_id, "height": tip["height"],
                "hash": self._tip_hash(), "timestamp": tip["timestamp"],
                "state_root": tip["state_root"],
                # Under lazy consensus the live fee is not the fee at the tip:
                # the slots the chain slept through are priced as empty ones,
                # so a quote taken now is cheaper than the tip says.
                "base_fee": (LZ.expected_base_fee(tip, {"timestamp": now})
                             if LZ.LAZY else self.state.base_fee),
                "base_fee_at_tip": tip["base_fee"],
                "lazy": LZ.LAZY,
                "mode": ("storage" if self.dormant else
                         "hot" if self.mempool else "idle"),
                "tier": self.tier()["name"],
                "idle_for": self.pulse.idle_for(now),
                "mempool": len(self.mempool),
                "keys": len(self.state.store),
                "state_bytes": self.state.state_bytes(),
                "accounts": len(self.state.accounts),
                "burned": self.state.burned,
                "supply": self.state.supply,
                "proposer": self._proposer(),
                "consensus": self.epoch()["engine"],
                "proposal": bool(self.proposal)}

    def block(self, ref=None):
        """A block by height, by hash, or the tip."""
        if ref in (None, "", "head", "latest", "tip"):
            return self.read_blocks_tail(1)[0]
        try:
            h = int(ref)
            if 0 <= h < len(self.blocks):
                result = self.read_blocks(start=h, limit=1)
                if result and result[0]["header"]["height"] == h:
                    return result[0]
        except (TypeError, ValueError):
            pass
        if ref in self._block_index:
            h = self._block_index[ref]
            result = self.read_blocks(start=h, limit=1)
            if result:
                return result[0]
        raise StateError(f"no block {ref!r}", code="no_block", status=404)

    def transaction(self, tx_hash):
        if tx_hash in self._tx_index:
            height = self._tx_index[tx_hash]
            blocks = self.read_blocks(start=height, limit=1)
            if blocks:
                b = blocks[0]
                for tx, receipt in zip(b["txs"], b["receipts"]):
                    if tx["hash"] == tx_hash:
                        return {"tx": tx, "receipt": receipt,
                                "height": b["header"]["height"],
                                "block": b["hash"], "status": "included",
                                "timestamp": b["header"]["timestamp"]}
        if tx_hash in self.mempool:
            return {"tx": self.mempool[tx_hash], "status": "pending"}
        for r in self.rejected:
            if r["hash"] == tx_hash:
                return {"status": "dropped", **r}
        raise StateError(f"no transaction {tx_hash!r}", code="no_tx", status=404)

    def history(self, address=None, key=None, limit=50, since=0):
        out = []
        for b in reversed(self.read_blocks(start=since)):
            for tx, receipt in zip(b["txs"], b["receipts"]):
                body = tx["body"]
                if address and address not in (body.get("from"),
                                               body.get("to"),
                                               receipt.get("seller"),
                                               receipt.get("buyer")):
                    continue
                if key and body.get("key") != key:
                    continue
                out.append({
                    "hash": tx["hash"], "block": b["hash"],
                    "height": b["header"]["height"],
                    "timestamp": b["header"]["timestamp"],
                    "kind": body["kind"], "from": body["from"],
                    **{k: body[k] for k in ("key", "to") if k in body},
                    "receipt": receipt,
                })
                if len(out) >= limit:
                    return out
        return out
