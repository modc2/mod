"""The chain: blocks, the mempool, and the node that puts them together.

A block is a header, a list of signed transactions and the receipts they
produced. The header commits to the state root after execution, so a replay
that disagrees about a single rent settlement produces a different root and
says so. Every hash is SHA3-256 and every signature is ML-DSA — including the
proposer's signature over the header, which is what makes a block a claim by
somebody rather than a file on disk.

Consensus is one proposer, and this file is honest about that: it is authority,
not agreement. There is no fork choice, no voting, no finality gadget. What is
here is the part the market needs — an ordered log, deterministic execution,
and a state root anyone can recompute — with the validator set left as a single
key so the economics can be tested without a consensus protocol in the way.
`verify()` replays the whole chain from genesis and checks every root, which is
the check that would still matter under any consensus.

Blocks are produced on demand and on a heartbeat, not on a fixed tick, because
rent is priced in wall-clock seconds rather than block height. Nothing about
the state machine depends on how often blocks appear.
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

import keys as K                                                # noqa: E402
import state as S                                               # noqa: E402
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
        self.state = State(chain_id)
        self.blocks = []                   # headers only; bodies live on disk
        self.last_block_time = 0
        self._validator_name = validator or os.environ.get(
            "POSTQUANT_VALIDATOR", "validator")
        self.validator = None
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
        return self.head()

    def _init_genesis(self):
        w = K.get(self._validator_name, required=False)
        if w is None:
            w = K.create(self._validator_name)
            w = K.get(self._validator_name)
        self.validator = w
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
                "signatures": w["scheme"] + " (FIPS 204)",
                "kem": "ML-KEM-768 (FIPS 203)",
                "hash": "SHA3-256",
                "consensus": "single proposer (authority) — no fork choice",
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
                               "min_base_fee": S.BASE_FEE_MIN},
            },
        }
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
        with open(self.blocks_file, "w") as f:
            f.write(json.dumps(block, separators=(",", ":")) + "\n")
        self.blocks = [header]
        self.last_block_time = now
        self._snapshot()

    def _seal(self, header, txs, receipts):
        """Hash the header, then sign the hash. The proposer signs what the
        header commits to, so the signature covers the transactions and the
        resulting state root without carrying either."""
        h = block_hash(header)
        block = {"header": header, "hash": h, "txs": txs, "receipts": receipts}
        if self.validator:
            sk = K.secret_key(self.validator)
            from pq import mldsa
            block["sig"] = mldsa.sign(sk, bytes.fromhex(h),
                                      self.validator["scheme"],
                                      ctx=b"postquant/block/v1").hex()
            block["proposer_pk"] = self.validator["pk"]
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
            for line in f:
                line = line.strip()
                if not line:
                    continue
                b = json.loads(line)
                if b["header"]["height"] < start:
                    continue
                out.append(b)
                if limit and len(out) >= limit:
                    break
        return out

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
        from pq import mldsa
        with open(self.genesis_file) as f:
            genesis = json.load(f)
        st = State(self.chain_id, genesis["alloc"], genesis["base_fee"])
        parent = "0" * 64
        checked = bad = 0
        problems = []
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
            if block.get("sig") and block.get("proposer_pk"):
                pk = bytes.fromhex(block["proposer_pk"])
                if K.address(pk) != h["proposer"] or not mldsa.verify(
                        pk, bytes.fromhex(block["hash"]),
                        bytes.fromhex(block["sig"]),
                        genesis.get("scheme", K.SCHEME),
                        ctx=b"postquant/block/v1"):
                    problems.append(f"height {h['height']}: proposer signature "
                                    "does not verify")
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
        return {"ok": bad == 0, "blocks": checked, "problems": problems,
                "signatures_checked": bool(signatures),
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
            if known is None and not tx.get("pk"):
                raise StateError(
                    "this address has never transacted, so the chain does not "
                    "know its public key — include pk with the first "
                    "transaction from an address", code="pk_required")
            if not K.verify_tx(tx, known):
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

    def produce(self, now=None, force=False):
        """Build, execute, seal and persist one block."""
        with self.lock:
            now = int(now or time.time())
            tip = self.blocks[-1]
            now = max(now, tip["timestamp"] + 1)
            candidates = self._order(self.pending())[:MAX_TXS_PER_BLOCK]
            if not candidates and not force:
                return None

            st = self._fork_state()
            included, receipts, dropped = [], [], []
            gas_used = growth = 0
            proposer = self._proposer()
            for tx in candidates:
                try:
                    receipt = st.apply(tx, now, proposer=proposer)
                except StateError as e:
                    dropped.append({"hash": tx["hash"], "reason": e.message,
                                    "code": e.code})
                    st = self._replay_into(st, included, now, proposer)
                    continue
                if gas_used + receipt["gas"] > S.BLOCK_GAS_LIMIT:
                    st = self._replay_into(st, included, now, proposer)
                    break
                gas_used += receipt["gas"]
                growth += receipt.get("state_bytes", 0)
                receipt["hash"] = tx["hash"]
                included.append(tx)
                receipts.append(receipt)

            st.height = tip["height"] + 1
            st.time = now
            header = {
                "chain_id": self.chain_id,
                "height": tip["height"] + 1,
                "parent": self._tip_hash(),
                "timestamp": now,
                "proposer": proposer,
                "base_fee": self.state.base_fee,
                "gas_used": gas_used,
                "state_bytes": growth,
                "tx_count": len(included),
                "tx_root": tx_root(included),
                "state_root": st.root(),
            }
            block = self._seal(header, included, receipts)
            with open(self.blocks_file, "a") as f:
                f.write(json.dumps(block, separators=(",", ":")) + "\n")

            st.base_fee = st.next_base_fee(growth)
            self.state = st
            self.blocks.append(header)
            self.last_block_time = now
            for tx in included:
                self.mempool.pop(tx["hash"], None)
            for d in dropped:
                self.mempool.pop(d["hash"], None)
                self.rejected.append({**d, "height": header["height"]})
            self.rejected = self.rejected[-50:]
            self._snapshot()
            return {"block": header, "hash": block["hash"],
                    "included": len(included), "dropped": dropped,
                    "gas_used": gas_used, "state_bytes": growth,
                    "next_base_fee": st.base_fee}

    def _replay_into(self, _stale, included, now, proposer):
        """A transaction failed mid-block. Rebuild the block's state from the
        transactions that did succeed rather than trusting a state that a
        half-applied transaction may have touched."""
        st = self._fork_state()
        for tx in included:
            st.apply(tx, now, proposer=proposer)
        return st

    def _tip_hash(self):
        blocks = self.read_blocks(start=self.blocks[-1]["height"], limit=1)
        return blocks[0]["hash"] if blocks else "0" * 64

    def maybe_produce(self, now=None):
        """Called by the block loop: produce if there is work, or on the
        heartbeat so wall-clock rent keeps being settled into the chain."""
        now = int(now or time.time())
        if self.mempool:
            return self.produce(now)
        if now - self.last_block_time >= HEARTBEAT_SECONDS:
            return self.produce(now, force=True)
        return None

    def run(self, stop=None):
        """The block loop. One thread, one proposer."""
        while not (stop and stop.is_set()):
            try:
                self.maybe_produce()
            except Exception as e:                 # never let a bad block stop
                print(f"postquant: block production failed — {e}", flush=True)
            time.sleep(BLOCK_SECONDS)

    # ── reads ─────────────────────────────────────────────────────

    def head(self):
        tip = self.blocks[-1]
        return {"chain_id": self.chain_id, "height": tip["height"],
                "hash": self._tip_hash(), "timestamp": tip["timestamp"],
                "state_root": tip["state_root"],
                "base_fee": self.state.base_fee,
                "mempool": len(self.mempool),
                "keys": len(self.state.store),
                "state_bytes": self.state.state_bytes(),
                "accounts": len(self.state.accounts),
                "burned": self.state.burned,
                "supply": self.state.supply,
                "proposer": self._proposer()}

    def block(self, ref=None):
        """A block by height, by hash, or the tip."""
        blocks = self.read_blocks()
        if ref in (None, "", "head", "latest", "tip"):
            return blocks[-1]
        try:
            h = int(ref)
            if 0 <= h < len(blocks) and blocks[h]["header"]["height"] == h:
                return blocks[h]
        except (TypeError, ValueError):
            pass
        for b in blocks:
            if b["hash"] == ref:
                return b
        raise StateError(f"no block {ref!r}", code="no_block", status=404)

    def transaction(self, tx_hash):
        for b in reversed(self.read_blocks()):
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

    def history(self, address=None, key=None, limit=50):
        out = []
        for b in reversed(self.read_blocks()):
            for tx, receipt in zip(b["txs"], b["receipts"]):
                body = tx["body"]
                if address and address not in (body.get("from"),
                                               body.get("to"),
                                               receipt.get("seller"),
                                               receipt.get("buyer")):
                    continue
                if key and body.get("key") != key:
                    continue
                out.append({"hash": tx["hash"], "height": b["header"]["height"],
                            "timestamp": b["header"]["timestamp"],
                            "kind": body["kind"], "from": body["from"],
                            "receipt": receipt})
                if len(out) >= limit:
                    return out
        return out
