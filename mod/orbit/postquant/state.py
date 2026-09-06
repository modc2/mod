"""The state machine: a market in key/value space.

There is one thing to own on this chain — a key in the store — and the whole
protocol is the market for it. A key maps to a value, the value is bytes and is
usually a 32-byte hash, and holding that pair costs money for as long as you
hold it. Nothing here is a general-purpose VM; there are seven transaction
kinds and no loops, because the interesting part is the price, not the compute.

Three prices, and they are separate on purpose:

  write gas   one-time, charged when bytes enter the state. Priced per byte of
              KEY and per byte of VALUE at different rates: a key is an index
              entry the whole network carries and sorts, a value is a payload
              hanging off it, so a key byte costs 4x a value byte. A value
              declared as a hash costs less per byte still, which is the chain
              saying out loud what it wants stored — a commitment, with the
              blob somewhere else.

  witness gas one-time, charged per byte of signature and public key. On a
              post-quantum chain this is not a rounding error: an ML-DSA-44
              signature is 2420 bytes against ed25519's 64. A chain that
              charges 21000 flat for a transfer is quietly subsidising its own
              witnesses, so this one bills them.

  rent        continuous, charged per billable byte per hour against an escrow
              the writer prepays. Occupancy is a flow, not a stock: you are not
              buying a slot, you are renting it. When the escrow runs dry the
              entry expires, and anyone may sweep it and collect the bounty
              that was reserved out of the deposit for exactly that job.

Write gas is denominated in gas and settled at a base fee that floats with how
fast the state is growing — EIP-1559, but the scarce resource being metered is
state bytes rather than execution. Base fee is burned, the tip goes to the
proposer. If the store grows faster than target, writing gets more expensive
until it stops.

Everything in this file is a pure function of (state, tx, timestamp). No I/O,
no clock, no randomness — chain.py supplies all three, which is what makes a
replay reproduce the same state root.
"""

from __future__ import annotations

import hashlib
import json

# ── denomination ──────────────────────────────────────────────────
NQ = 1                              # nanoquant, the base unit
PQ = 1_000_000_000 * NQ             # the display unit
SYMBOL = "PQ"
DECIMALS = 9

# ── write gas ─────────────────────────────────────────────────────
GAS_TX_BASE = 21_000                # every transaction, whatever it does
GAS_KEY_BYTE = 400                  # a key is a permanent index entry
GAS_VALUE_BYTE = 100                # a raw value byte
GAS_VALUE_BYTE_HASH = 40            # a byte of a 32-byte commitment
GAS_WITNESS_BYTE = 3                # signature and public key bytes
GAS_ACCOUNT_NEW = 25_000            # first touch of an address
GAS_MARKET_OP = 5_000               # list / buy / fund / sweep bookkeeping

# ── rent ──────────────────────────────────────────────────────────
ENTRY_OVERHEAD = 64                 # owner, expiry, escrow, price, kind
RENT_PER_BYTE_HOUR = 1_000 * NQ     # what a byte costs to sit in the store
MIN_LEASE_SECONDS = 3600            # you may not buy less than an hour
MAX_LEASE_SECONDS = 3600 * 24 * 365 * 4
BOUNTY_BPS = 500                    # 5% of a deposit is held back as the
                                    # sweep bounty; refunded on a clean delete
BOUNTY_MARGIN = 2                   # ...but never less than this multiple of
                                    # what the sweep transaction itself costs

# ── the fee market ────────────────────────────────────────────────
BASE_FEE_MIN = 1 * NQ
BASE_FEE_INITIAL = 100 * NQ
STATE_TARGET_BYTES = 4_096          # net billable bytes per block, target
BASE_FEE_MAX_CHANGE_DEN = 8         # ±12.5% per block, as in EIP-1559

# ── limits ────────────────────────────────────────────────────────
MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 8_192             # a blob store is a different product
HASH_BYTES = 32
BLOCK_GAS_LIMIT = 30_000_000

KINDS = ("set", "del", "fund", "xfer", "list", "buy", "sweep")


class StateError(Exception):
    """A transaction that cannot be applied. `code` is stable, message is not."""

    def __init__(self, message, code="invalid", status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status

    def dict(self):
        return {"error": self.message, "code": self.code}


# ── helpers ───────────────────────────────────────────────────────


def sha3(*parts: bytes) -> bytes:
    h = hashlib.sha3_256()
    for p in parts:
        h.update(p)
    return h.digest()


def canonical(obj) -> bytes:
    """The exact bytes a signature covers. Sorted keys, no whitespace, no
    floats anywhere — two nodes must never disagree about what was signed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode()


def is_hex(s, nbytes=None):
    if not isinstance(s, str):
        return False
    try:
        b = bytes.fromhex(s)
    except ValueError:
        return False
    return nbytes is None or len(b) == nbytes


def key_bytes(key: str) -> bytes:
    """Keys are UTF-8 text. Human-readable keys are the point of a namespace
    market — you cannot bid on something you cannot say."""
    return key.encode()


def billable_bytes(key: str, value_hex: str) -> int:
    return len(key_bytes(key)) + len(value_hex) // 2 + ENTRY_OVERHEAD


def sweep_gas() -> int:
    """Gas a sweep burns: a transaction, its witness, and the market op."""
    return GAS_TX_BASE + GAS_MARKET_OP + SIG_BYTES * GAS_WITNESS_BYTE


def bounty_floor(base_fee: int) -> int:
    """What a deposit must reserve for whoever eventually clears the entry.

    A bounty worth less than the gas to claim it is not a bounty, and an
    unclaimed bounty means expiry never happens and rent is decorative. So the
    floor is the cost of the sweep transaction with margin, priced at the base
    fee when the entry was written. It is a bond, not a fee: delete the entry
    yourself and it comes back whole.
    """
    return BOUNTY_MARGIN * sweep_gas() * int(base_fee)


def rent_for(nbytes: int, seconds: int) -> int:
    """Cost in nq to hold nbytes for seconds. Rounded up — the chain never
    gives away a fractional byte-hour."""
    num = nbytes * RENT_PER_BYTE_HOUR * seconds
    return -(-num // 3600)


def lease_seconds(nbytes: int, escrow: int) -> int:
    """The inverse: how long an escrow buys. Rounded down."""
    if nbytes <= 0:
        return MAX_LEASE_SECONDS
    return (escrow * 3600) // (nbytes * RENT_PER_BYTE_HOUR)


def to_pq(nq: int) -> str:
    sign = "-" if nq < 0 else ""
    nq = abs(int(nq))
    return f"{sign}{nq // PQ}.{nq % PQ:09d}"


# ── the state ─────────────────────────────────────────────────────


class State:
    """Accounts, the store, and the two market variables.

    Held as plain dicts so a snapshot is json.dumps and nothing more. Every
    mutation goes through apply(); nothing else should reach in and write.
    """

    def __init__(self, chain_id="postquant-1", genesis=None,
                 base_fee=BASE_FEE_INITIAL):
        self.chain_id = chain_id
        self.accounts = {}          # addr -> {balance, nonce, pk, scheme}
        self.store = {}             # key  -> entry
        self.base_fee = int(base_fee)
        self.height = 0
        self.time = 0               # last applied block timestamp
        self.burned = 0
        self.rent_collected = 0
        self.supply = 0
        for addr, amount in (genesis or {}).items():
            self.account(addr)["balance"] += int(amount)
            self.supply += int(amount)

    # -- accounts --

    def account(self, addr):
        a = self.accounts.get(addr)
        if a is None:
            a = self.accounts[addr] = {"balance": 0, "nonce": 0, "pk": None,
                                       "scheme": None}
        return a

    def balance(self, addr):
        return self.accounts.get(addr, {}).get("balance", 0)

    def _credit(self, addr, amount):
        if amount:
            self.account(addr)["balance"] += int(amount)

    def _debit(self, addr, amount):
        a = self.account(addr)
        if a["balance"] < amount:
            raise StateError(
                f"{addr} has {to_pq(a['balance'])} {SYMBOL}, needs "
                f"{to_pq(amount)}", code="insufficient_funds")
        a["balance"] -= int(amount)

    # -- the store --

    def entry(self, key, now=None):
        """One entry with its rent settled up to `now`, or None."""
        e = self.store.get(key)
        if e is None:
            return None
        e = dict(e)
        now = self.time if now is None else now
        e["remaining"] = self._remaining(e, now)
        e["expired"] = now >= e["expires_at"]
        e["expires_in"] = max(0, e["expires_at"] - now)
        return e

    def _remaining(self, e, now):
        """Escrow left after rent accrued since the last settlement. Lazy on
        purpose: charging every entry every block would make block time a
        function of state size."""
        elapsed = max(0, now - e["rent_from"])
        accrued = rent_for(e["bytes"], elapsed)
        return max(0, e["escrow"] - accrued)

    def _settle(self, e, now):
        """Fold accrued rent into the burn and restart the meter at `now`."""
        remaining = self._remaining(e, now)
        spent = e["escrow"] - remaining
        self.rent_collected += spent
        self.burned += spent
        self.supply -= spent
        e["escrow"] = remaining
        e["rent_from"] = now
        return remaining

    def _top_up_bounty(self, e, amount):
        """Drag an old bond up toward the current floor when someone adds
        money. Capped at half the deposit so funding an entry can never be
        swallowed whole by the bond, and skipped entirely once the bond is
        adequate — the usual case is that this returns 0."""
        short = max(0, bounty_floor(self.base_fee) - e["bounty"])
        top = min(short, amount // 2)
        e["bounty"] += top
        return top

    def _reprice(self, e):
        e["expires_at"] = e["rent_from"] + lease_seconds(e["bytes"], e["escrow"])

    def live_keys(self, now=None):
        now = self.time if now is None else now
        return [k for k, e in self.store.items() if now < e["expires_at"]]

    def state_bytes(self):
        return sum(e["bytes"] for e in self.store.values())

    # -- pricing --

    def quote(self, key, value_hex="", kind="hash", seconds=MIN_LEASE_SECONDS,
              witness_bytes=None, new_account=False, base_fee=None):
        """What a set costs, split into the three prices. This is the whole
        user-facing economics in one call — the console and the CLI both read
        it before anyone signs anything."""
        base_fee = self.base_fee if base_fee is None else int(base_fee)
        kb = len(key_bytes(key))
        vb = len(value_hex) // 2
        per_value = GAS_VALUE_BYTE_HASH if kind == "hash" else GAS_VALUE_BYTE
        wb = SIG_BYTES + PK_BYTES if witness_bytes is None else int(witness_bytes)
        gas = {
            "base": GAS_TX_BASE,
            "key": kb * GAS_KEY_BYTE,
            "value": vb * per_value,
            "witness": wb * GAS_WITNESS_BYTE,
            "account": GAS_ACCOUNT_NEW if new_account else 0,
        }
        total_gas = sum(gas.values())
        nbytes = kb + vb + ENTRY_OVERHEAD
        seconds = max(MIN_LEASE_SECONDS, int(seconds))
        lease = rent_for(nbytes, seconds)
        bounty = max(lease * BOUNTY_BPS // 10_000, bounty_floor(base_fee))
        deposit = lease + bounty
        return {
            "key": key, "key_bytes": kb, "value_bytes": vb, "value_kind": kind,
            "billable_bytes": nbytes,
            "gas": gas, "gas_total": total_gas,
            "base_fee": base_fee,
            "write_cost": total_gas * base_fee,
            "rent": {
                "seconds": seconds,
                "per_byte_hour": RENT_PER_BYTE_HOUR,
                "deposit": deposit,
                "lease": lease,
                "bounty": bounty,
                "per_day": rent_for(nbytes, 86400),
                "bounty_is": "a refundable bond, returned in full if you delete "
                             "the entry yourself and paid to whoever sweeps it "
                             "if you let it expire",
            },
            "total": total_gas * base_fee + deposit,
            "note": ("a value declared kind=hash is billed at "
                     f"{GAS_VALUE_BYTE_HASH} gas/byte against {GAS_VALUE_BYTE} "
                     "for raw bytes — the chain prices commitments below blobs"),
        }

    # ── transaction application ───────────────────────────────────

    def apply(self, tx, now, proposer=None, charge=True):
        """Apply one validated transaction. Returns a receipt.

        Raises StateError and leaves the state untouched only if it raises
        before any mutation — chain.py runs each transaction against a copy and
        commits on success, so a half-applied transaction cannot be observed.
        """
        body = tx["body"]
        kind = body["kind"]
        sender = body["from"]
        acct = self.account(sender)

        if body.get("chain_id") != self.chain_id:
            raise StateError(f"transaction is for chain "
                             f"{body.get('chain_id')!r}, this is "
                             f"{self.chain_id!r}", code="wrong_chain")
        if body["nonce"] != acct["nonce"]:
            raise StateError(
                f"nonce {body['nonce']} but {sender} is at {acct['nonce']}",
                code="bad_nonce")

        max_fee = int(body.get("max_fee", 0))
        tip = int(body.get("tip", 0))
        if charge and max_fee < self.base_fee:
            raise StateError(
                f"max_fee {max_fee} below base fee {self.base_fee} — the fee "
                "market moved; requote and resign", code="fee_too_low")
        effective = min(max_fee, self.base_fee + tip) if charge else 0
        realised_tip = max(0, effective - self.base_fee) if charge else 0

        new_account = acct["pk"] is None and tx.get("pk")
        witness = len(bytes.fromhex(tx.get("sig", ""))) + \
            (len(bytes.fromhex(tx["pk"])) if tx.get("pk") else 0)

        gas = GAS_TX_BASE + witness * GAS_WITNESS_BYTE
        if new_account:
            gas += GAS_ACCOUNT_NEW

        handler = getattr(self, f"_op_{kind}", None)
        if handler is None:
            raise StateError(f"unknown transaction kind {kind!r} — "
                             f"one of {', '.join(KINDS)}", code="bad_kind")

        # The handler returns extra gas and a per-kind receipt. It may debit
        # and credit freely; it runs after the fee check so a transaction that
        # cannot pay never reaches it.
        extra_gas, detail, growth = handler(body, sender, now)
        gas += extra_gas

        if gas > BLOCK_GAS_LIMIT:
            raise StateError(f"{gas} gas over the block limit "
                             f"{BLOCK_GAS_LIMIT}", code="gas_limit")

        fee = gas * effective
        if charge:
            self._debit(sender, fee)
            burn = gas * self.base_fee
            self.burned += burn
            self.supply -= burn
            if realised_tip and proposer:
                self._credit(proposer, gas * realised_tip)
            elif realised_tip:
                self.burned += gas * realised_tip
                self.supply -= gas * realised_tip

        if tx.get("pk") and acct["pk"] is None:
            acct["pk"] = tx["pk"]
            acct["scheme"] = tx.get("scheme")
        acct["nonce"] += 1

        return {
            "ok": True, "kind": kind, "from": sender, "gas": gas,
            "base_fee": self.base_fee, "effective_fee": effective,
            "tip": realised_tip, "fee": fee, "state_bytes": growth,
            **detail,
        }

    # -- the seven operations --

    def _op_set(self, body, sender, now):
        """Write a key. Claims it if free, owner-only if not."""
        key = body["key"]
        value = (body.get("value") or "").lower()
        kind = body.get("value_kind", "hash")
        deposit = int(body.get("deposit", 0))

        _check_key(key)
        _check_value(value, kind)

        existing = self.store.get(key)
        expired = existing is not None and now >= existing["expires_at"]
        if existing is not None and not expired and existing["owner"] != sender:
            raise StateError(
                f"{key!r} is held by {existing['owner']} until "
                f"{existing['expires_at']} — buy it, or wait for it to expire",
                code="not_owner", status=403)
        if expired:
            # An expired entry is nobody's. Reclaim it rather than making the
            # writer sweep it first; the bounty still goes to a sweeper if one
            # gets here before the new writer does.
            # Overwriting an expired entry is a sweep with a write attached,
            # so the bounty pays the writer doing the clearing — not the owner
            # who stopped paying rent.
            self._credit(sender, self._settle(existing, now) +
                         existing.get("bounty", 0))
            existing = None

        vb = len(value) // 2
        kb = len(key_bytes(key))
        nbytes = kb + vb + ENTRY_OVERHEAD
        per_value = GAS_VALUE_BYTE_HASH if kind == "hash" else GAS_VALUE_BYTE
        gas = kb * GAS_KEY_BYTE + vb * per_value

        if existing is None:
            floor = max(rent_for(nbytes, MIN_LEASE_SECONDS) * BOUNTY_BPS //
                        10_000, bounty_floor(self.base_fee))
            need = rent_for(nbytes, MIN_LEASE_SECONDS) + floor
            if deposit < need:
                raise StateError(
                    f"deposit {to_pq(deposit)} {SYMBOL} does not cover the "
                    f"{MIN_LEASE_SECONDS // 3600}h minimum lease on {nbytes} "
                    f"bytes plus the {to_pq(floor)} sweep bond — send at least "
                    f"{to_pq(need)}", code="lease_too_short")
            growth = nbytes
            bounty = max(deposit * BOUNTY_BPS // 10_000, floor)
            self._debit(sender, deposit)
            entry = {
                "key": key, "owner": sender, "value": value,
                "value_kind": kind, "bytes": nbytes,
                "escrow": deposit - bounty, "bounty": bounty,
                "rent_from": now, "expires_at": 0,
                "price": 0, "listed": False,
                "created": now, "updated": now, "writes": 1,
            }
            self._reprice(entry)
            self.store[key] = entry
        else:
            entry = existing
            self._settle(entry, now)
            if deposit:
                self._debit(sender, deposit)
                entry["escrow"] += deposit - self._top_up_bounty(entry, deposit)
            growth = max(0, nbytes - entry["bytes"])
            entry["value"] = value
            entry["value_kind"] = kind
            entry["bytes"] = nbytes
            entry["updated"] = now
            entry["writes"] += 1
            self._reprice(entry)
            if entry["expires_at"] <= now:
                raise StateError(
                    f"that value grows the entry to {nbytes} bytes and the "
                    "escrow left will not cover an hour of it — send a deposit "
                    "with the write", code="lease_too_short")

        return gas, {
            "key": key, "value": value, "value_kind": kind,
            "bytes": nbytes, "expires_at": entry["expires_at"],
            "lease_seconds": entry["expires_at"] - now,
            "escrow": entry["escrow"], "bounty": entry["bounty"],
        }, growth

    def _op_del(self, body, sender, now):
        """Give a key back. The unused rent and the bounty come back with it —
        the only way the store ever shrinks is if shrinking pays."""
        key = body["key"]
        entry = self.store.get(key)
        if entry is None:
            raise StateError(f"no entry at {key!r}", code="no_entry", status=404)
        if entry["owner"] != sender:
            raise StateError(f"{key!r} belongs to {entry['owner']}",
                             code="not_owner", status=403)
        refund = self._settle(entry, now) + entry["bounty"]
        freed = entry["bytes"]
        del self.store[key]
        self._credit(sender, refund)
        return GAS_MARKET_OP, {"key": key, "refund": refund,
                               "freed_bytes": freed}, 0

    def _op_fund(self, body, sender, now):
        """Extend anyone's lease. Public data stays up because someone keeps
        paying for it, not because the protocol forgot to charge."""
        key = body["key"]
        amount = int(body.get("deposit", 0))
        entry = self.store.get(key)
        if entry is None:
            raise StateError(f"no entry at {key!r}", code="no_entry", status=404)
        if amount <= 0:
            raise StateError("fund needs a deposit", code="bad_deposit")
        if now >= entry["expires_at"]:
            raise StateError(f"{key!r} expired at {entry['expires_at']} — "
                             "sweep it and write it again", code="expired")
        self._settle(entry, now)
        self._debit(sender, amount)
        entry["escrow"] += amount - self._top_up_bounty(entry, amount)
        self._reprice(entry)
        return GAS_MARKET_OP, {"key": key, "funded_by": sender,
                               "expires_at": entry["expires_at"],
                               "lease_seconds": entry["expires_at"] - now}, 0

    def _op_xfer(self, body, sender, now):
        to = body["to"]
        amount = int(body.get("amount", 0))
        _check_address(to)
        if amount < 0:
            raise StateError("amount must be positive", code="bad_amount")
        self._debit(sender, amount)
        self._credit(to, amount)
        return 0, {"to": to, "amount": amount}, 0

    def _op_list(self, body, sender, now):
        """Offer a key at a price. price=0 delists."""
        key = body["key"]
        price = int(body.get("price", 0))
        entry = self.store.get(key)
        if entry is None:
            raise StateError(f"no entry at {key!r}", code="no_entry", status=404)
        if entry["owner"] != sender:
            raise StateError(f"{key!r} belongs to {entry['owner']}",
                             code="not_owner", status=403)
        if now >= entry["expires_at"]:
            raise StateError(f"{key!r} has expired — nothing to sell",
                             code="expired")
        if price < 0:
            raise StateError("price must be positive", code="bad_price")
        entry["price"] = price
        entry["listed"] = price > 0
        return GAS_MARKET_OP, {"key": key, "price": price,
                               "listed": entry["listed"]}, 0

    def _op_buy(self, body, sender, now):
        """Take a listed key at its asking price. The remaining lease and the
        bounty go with it — you buy the entry, not just the name."""
        key = body["key"]
        max_price = int(body.get("max_price", 0))
        entry = self.store.get(key)
        if entry is None:
            raise StateError(f"no entry at {key!r}", code="no_entry", status=404)
        if not entry["listed"] or entry["price"] <= 0:
            raise StateError(f"{key!r} is not for sale", code="not_listed")
        if now >= entry["expires_at"]:
            raise StateError(f"{key!r} has expired — sweep it instead",
                             code="expired")
        if entry["owner"] == sender:
            raise StateError("you already own it", code="self_trade")
        price = entry["price"]
        if max_price and price > max_price:
            raise StateError(f"asking {to_pq(price)} {SYMBOL}, above your "
                             f"max_price {to_pq(max_price)}", code="price_moved")
        seller = entry["owner"]
        self._settle(entry, now)
        self._debit(sender, price)
        self._credit(seller, price)
        entry["owner"] = sender
        entry["listed"] = False
        entry["price"] = 0
        return GAS_MARKET_OP, {"key": key, "seller": seller, "buyer": sender,
                               "price": price,
                               "lease_seconds": entry["expires_at"] - now,
                               "escrow": entry["escrow"]}, 0

    def _op_sweep(self, body, sender, now):
        """Delete an expired entry and take the bounty. This is the job that
        makes rent real: without a paid reaper, expiry is a suggestion."""
        key = body["key"]
        entry = self.store.get(key)
        if entry is None:
            raise StateError(f"no entry at {key!r}", code="no_entry", status=404)
        if now < entry["expires_at"]:
            raise StateError(
                f"{key!r} is paid up for another {entry['expires_at'] - now}s",
                code="not_expired")
        self._settle(entry, now)
        bounty = entry["bounty"] + entry["escrow"]
        freed = entry["bytes"]
        owner = entry["owner"]
        del self.store[key]
        self._credit(sender, bounty)
        return GAS_MARKET_OP, {"key": key, "swept_from": owner,
                               "bounty": bounty, "freed_bytes": freed}, 0

    # ── the fee market ────────────────────────────────────────────

    def next_base_fee(self, growth_bytes):
        """EIP-1559 over state growth instead of execution gas. The scarce
        thing here is not CPU, it is every node's disk forever."""
        target = STATE_TARGET_BYTES
        fee = self.base_fee
        if growth_bytes == target:
            return fee
        if growth_bytes > target:
            delta = max(1, fee * (growth_bytes - target) //
                        target // BASE_FEE_MAX_CHANGE_DEN)
            return fee + min(delta, fee // BASE_FEE_MAX_CHANGE_DEN or 1)
        delta = fee * (target - growth_bytes) // target // BASE_FEE_MAX_CHANGE_DEN
        return max(BASE_FEE_MIN, fee - delta)

    # ── the state root ────────────────────────────────────────────

    def leaves(self):
        """Every leaf of the state tree, in the order the root commits to.
        root() and proof() both read this, so a proof can never be built
        against a different tree than the root was."""
        out = []
        for addr in sorted(self.accounts):
            a = self.accounts[addr]
            out.append(("acct", addr, sha3(
                b"acct\x00", addr.encode(),
                a["balance"].to_bytes(16, "big"),
                a["nonce"].to_bytes(8, "big"),
                sha3(bytes.fromhex(a["pk"])) if a["pk"] else b"\x00" * 32)))
        for key in sorted(self.store):
            e = self.store[key]
            out.append(("kv", key, sha3(
                b"kv\x00", key.encode(),
                sha3(bytes.fromhex(e["value"] or "")),
                e["owner"].encode(),
                e["bytes"].to_bytes(4, "big"),
                e["escrow"].to_bytes(16, "big"),
                e["bounty"].to_bytes(16, "big"),
                e["expires_at"].to_bytes(8, "big"),
                e["price"].to_bytes(16, "big"),
                (e["value_kind"] or "").encode())))
        out.append(("params", self.chain_id, sha3(
            b"params\x00", self.chain_id.encode(),
            self.base_fee.to_bytes(16, "big"),
            self.height.to_bytes(8, "big"),
            self.burned.to_bytes(16, "big"))))
        return out

    def root(self):
        """A Merkle root over every account and every entry, SHA3-256
        throughout. Hash-based commitments are what a post-quantum chain is
        allowed to keep: Grover halves the preimage exponent and nothing else
        breaks, so 256 bits of SHA3 is 128 bits of post-quantum margin."""
        return merkle_root([lf for _, _, lf in self.leaves()]).hex()

    def proof(self, key, kind="kv"):
        """A Merkle path from one entry to the state root.

        This is the point of storing hashes: a light client that knows only the
        root can be shown that a key commits to a particular digest, without
        holding the store. The proof is what makes an off-chain blob's
        on-chain commitment worth anything to someone who was not there.
        """
        leaves = self.leaves()
        index = next((i for i, (t, k, _) in enumerate(leaves)
                      if t == kind and k == key), None)
        if index is None:
            raise StateError(f"nothing at {key!r} to prove", code="no_entry",
                             status=404)
        flat = [lf for _, _, lf in leaves]
        return {"key": key, "kind": kind, "leaf": flat[index].hex(),
                "index": index, "leaves": len(flat),
                "path": merkle_path(flat, index), "root": self.root(),
                "entry": self.entry(key) if kind == "kv" else None}

    # ── snapshots ─────────────────────────────────────────────────

    def snapshot(self):
        return {"chain_id": self.chain_id, "height": self.height,
                "time": self.time, "base_fee": self.base_fee,
                "burned": self.burned, "supply": self.supply,
                "rent_collected": self.rent_collected,
                "accounts": self.accounts, "store": self.store}

    @classmethod
    def restore(cls, snap):
        s = cls(snap["chain_id"])
        s.__dict__.update({
            "height": snap["height"], "time": snap["time"],
            "base_fee": snap["base_fee"], "burned": snap["burned"],
            "supply": snap["supply"],
            "rent_collected": snap.get("rent_collected", 0),
            "accounts": snap["accounts"], "store": snap["store"]})
        return s


def merkle_path(leaves, index):
    """The sibling hashes that rebuild the root from one leaf. Mirrors
    merkle_root exactly, including the odd-leaf promotion — a path built
    against a different shape of tree is a proof of nothing."""
    path = []
    level, idx = list(leaves), index
    while len(level) > 1:
        if idx % 2 == 0:
            if idx + 1 < len(level):
                path.append({"side": "right", "hash": level[idx + 1].hex()})
        else:
            path.append({"side": "left", "hash": level[idx - 1].hex()})
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(sha3(b"\x01", level[i], level[i + 1])
                       if i + 1 < len(level) else level[i])
        level, idx = nxt, idx // 2
    return path


def check_proof(leaf, path, root) -> bool:
    """Recompute a root from a leaf and its path."""
    try:
        h = bytes.fromhex(leaf)
        for step in path:
            sib = bytes.fromhex(step["hash"])
            h = sha3(b"\x01", sib, h) if step["side"] == "left" else \
                sha3(b"\x01", h, sib)
        return h.hex() == root
    except Exception:
        return False


def merkle_root(leaves):
    """Binary Merkle over SHA3-256 with the odd leaf carried up unchanged.
    Domain-separated by position so an internal node can never be read as a
    leaf, which is the classic second-preimage hole in this shape of tree."""
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(sha3(b"\x01", level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        level = nxt
    return level[0]


# ── validation ────────────────────────────────────────────────────


def _check_key(key):
    if not isinstance(key, str) or not key:
        raise StateError("key must be a non-empty string", code="bad_key")
    kb = key_bytes(key)
    if len(kb) > MAX_KEY_BYTES:
        raise StateError(f"key is {len(kb)} bytes, limit is {MAX_KEY_BYTES}",
                         code="bad_key")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise StateError("key must not contain control characters",
                         code="bad_key")


def _check_value(value, kind):
    if kind not in ("hash", "raw"):
        raise StateError(f"value_kind must be 'hash' or 'raw', got {kind!r}",
                         code="bad_value")
    if not is_hex(value):
        raise StateError("value must be hex — the chain stores bytes, and hex "
                         "is how they cross JSON", code="bad_value")
    n = len(value) // 2
    if kind == "hash" and n != HASH_BYTES:
        raise StateError(
            f"a hash value is exactly {HASH_BYTES} bytes (SHA3-256), got {n} — "
            "use value_kind=raw for arbitrary bytes", code="bad_value")
    if n > MAX_VALUE_BYTES:
        raise StateError(
            f"value is {n} bytes, limit is {MAX_VALUE_BYTES}. Store the hash "
            "and keep the blob off-chain — that is what kind=hash is for",
            code="bad_value")


def _check_address(addr):
    if not isinstance(addr, str) or not addr.startswith("pq") or \
            not is_hex(addr[2:], 20):
        raise StateError(f"{addr!r} is not an address — expected pq + 40 hex",
                         code="bad_address")


# Filled in by keys.py at import time so quote() can price a witness without
# importing the signature scheme.
SIG_BYTES = 2420
PK_BYTES = 1312
