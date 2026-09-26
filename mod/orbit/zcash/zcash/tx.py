"""
Zcash NU5 v5 transaction construction and signing.

Serialization follows ZIP-225 (transaction version 5); the txid and the
transparent signature digest follow ZIP-244, which replaced the Bitcoin-style
double-SHA256 sighash with a tree of personalized BLAKE2b-256 digests.

Only the transparent pool is spendable here: Sapling/Orchard spends need
zk-SNARK proving. Transparent-only v5 transactions carry three zero
compactsize counts (sapling spends, sapling outputs, orchard actions).

Fees follow ZIP-317.
"""

import hashlib
import struct

try:
    from . import keys as _k
except ImportError:  # loaded as a loose module
    import keys as _k

# ── Constants ───────────────────────────────────────────────────────────────
V5_HEADER = 0x80000005            # fOverwintered | nVersion=5
V5_VERSION_GROUP_ID = 0x26A7270A  # ZIP-225
SIGHASH_ALL = 0x01
SEQUENCE_FINAL = 0xFFFFFFFF

# ZIP-317
MARGINAL_FEE = 5000
GRACE_ACTIONS = 2
P2PKH_INPUT_SIZE = 150            # bytes, signed P2PKH input
P2PKH_OUTPUT_SIZE = 34
DUST_THRESHOLD = 300              # zatoshi; below this a change output is dropped

# Default number of blocks until a transaction expires (zcashd default is 20)
DEFAULT_EXPIRY_DELTA = 40


# ── Primitives ──────────────────────────────────────────────────────────────

def compact_size(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def with_size(b: bytes) -> bytes:
    return compact_size(len(b)) + b


def blake2b32(data: bytes, person: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=32, person=person).digest()


# ── Transaction parts ───────────────────────────────────────────────────────

class TxIn:
    """A transparent input. `value` and `script_pubkey` come from the prevout
    and are required by ZIP-244 (they are committed to by the signature)."""

    __slots__ = ("txid", "vout", "value", "script_pubkey", "sequence", "script_sig")

    def __init__(self, txid: str, vout: int, value: int, script_pubkey: bytes,
                 sequence: int = SEQUENCE_FINAL):
        self.txid = txid
        self.vout = vout
        self.value = int(value)
        self.script_pubkey = script_pubkey
        self.sequence = sequence
        self.script_sig = b""

    @property
    def prevout_bytes(self) -> bytes:
        # txids are displayed big-endian, serialized little-endian
        return bytes.fromhex(self.txid)[::-1] + struct.pack("<I", self.vout)

    def serialize(self) -> bytes:
        return self.prevout_bytes + with_size(self.script_sig) + struct.pack("<I", self.sequence)


class TxOut:
    __slots__ = ("value", "script_pubkey")

    def __init__(self, value: int, script_pubkey: bytes):
        self.value = int(value)
        self.script_pubkey = script_pubkey

    @classmethod
    def to_address(cls, addr: str, value: int) -> "TxOut":
        return cls(value, _k.address_to_script(addr))

    def serialize(self) -> bytes:
        return struct.pack("<q", self.value) + with_size(self.script_pubkey)


class Transaction:
    """A transparent-only Zcash v5 transaction."""

    def __init__(self, consensus_branch_id: int, expiry_height: int = 0, lock_time: int = 0):
        self.consensus_branch_id = consensus_branch_id
        self.expiry_height = expiry_height
        self.lock_time = lock_time
        self.vin = []
        self.vout = []

    # ── ZIP-244 digest tree ────────────────────────────────────────────────

    def _header_digest(self) -> bytes:
        return blake2b32(
            struct.pack("<IIIII", V5_HEADER, V5_VERSION_GROUP_ID,
                        self.consensus_branch_id, self.lock_time, self.expiry_height),
            b"ZTxIdHeadersHash")

    def _prevouts_digest(self) -> bytes:
        return blake2b32(b"".join(i.prevout_bytes for i in self.vin), b"ZTxIdPrevoutHash")

    def _sequence_digest(self) -> bytes:
        return blake2b32(b"".join(struct.pack("<I", i.sequence) for i in self.vin),
                         b"ZTxIdSequencHash")

    def _outputs_digest(self) -> bytes:
        return blake2b32(b"".join(o.serialize() for o in self.vout), b"ZTxIdOutputsHash")

    def _amounts_digest(self) -> bytes:
        return blake2b32(b"".join(struct.pack("<q", i.value) for i in self.vin),
                         b"ZTxTrAmountsHash")

    def _scriptpubkeys_digest(self) -> bytes:
        return blake2b32(b"".join(with_size(i.script_pubkey) for i in self.vin),
                         b"ZTxTrScriptsHash")

    def _transparent_txid_digest(self) -> bytes:
        if not self.vin and not self.vout:
            return blake2b32(b"", b"ZTxIdTranspaHash")
        return blake2b32(
            self._prevouts_digest() + self._sequence_digest() + self._outputs_digest(),
            b"ZTxIdTranspaHash")

    def _sapling_digest(self) -> bytes:
        return blake2b32(b"", b"ZTxIdSaplingHash")   # no Sapling bundle

    def _orchard_digest(self) -> bytes:
        return blake2b32(b"", b"ZTxIdOrchardHash")   # no Orchard bundle

    def txid(self) -> str:
        digest = blake2b32(
            self._header_digest() + self._transparent_txid_digest()
            + self._sapling_digest() + self._orchard_digest(),
            b"ZcashTxHash_" + struct.pack("<I", self.consensus_branch_id))
        return digest[::-1].hex()

    def signature_digest(self, index: int, hash_type: int = SIGHASH_ALL) -> bytes:
        """ZIP-244 §S.2 signature digest for transparent input `index`."""
        if hash_type != SIGHASH_ALL:
            raise NotImplementedError("only SIGHASH_ALL is supported")
        txin = self.vin[index]
        txin_digest = blake2b32(
            txin.prevout_bytes
            + struct.pack("<q", txin.value)
            + with_size(txin.script_pubkey)
            + struct.pack("<I", txin.sequence),
            b"Zcash___TxInHash")
        transparent = blake2b32(
            bytes([hash_type])
            + self._prevouts_digest()
            + self._amounts_digest()
            + self._scriptpubkeys_digest()
            + self._sequence_digest()
            + self._outputs_digest()
            + txin_digest,
            b"ZTxIdTranspaHash")
        return blake2b32(
            self._header_digest() + transparent
            + self._sapling_digest() + self._orchard_digest(),
            b"ZcashTxHash_" + struct.pack("<I", self.consensus_branch_id))

    # ── Signing ────────────────────────────────────────────────────────────

    def sign_input(self, index: int, privkey: bytes, compressed: bool = True):
        """Sign a P2PKH input in place."""
        pubkey = _k.privkey_to_pubkey(privkey, compressed)
        expected = _k.p2pkh_script(_k.hash160(pubkey))
        if self.vin[index].script_pubkey != expected:
            raise ValueError(
                f"key does not control input {index}: "
                f"expected {expected.hex()}, prevout pays {self.vin[index].script_pubkey.hex()}")
        sig = _k.sign_digest(privkey, self.signature_digest(index)) + bytes([SIGHASH_ALL])
        self.vin[index].script_sig = with_size(sig) + with_size(pubkey)

    def verify_input(self, index: int) -> bool:
        """Verify an already-populated P2PKH script_sig against our sighash."""
        script_sig = self.vin[index].script_sig
        if not script_sig:
            return False
        sig_len = script_sig[0]
        sig = script_sig[1:1 + sig_len]
        pk_len = script_sig[1 + sig_len]
        pubkey = script_sig[2 + sig_len:2 + sig_len + pk_len]
        hash_type = sig[-1]
        return _k.verify_digest(pubkey, self.signature_digest(index, hash_type), sig[:-1])

    # ── Serialization ──────────────────────────────────────────────────────

    def serialize(self) -> bytes:
        out = struct.pack("<IIIII", V5_HEADER, V5_VERSION_GROUP_ID,
                          self.consensus_branch_id, self.lock_time, self.expiry_height)
        out += compact_size(len(self.vin)) + b"".join(i.serialize() for i in self.vin)
        out += compact_size(len(self.vout)) + b"".join(o.serialize() for o in self.vout)
        out += b"\x00"   # nSpendsSapling
        out += b"\x00"   # nOutputsSapling
        out += b"\x00"   # nActionsOrchard
        return out

    def hex(self) -> str:
        return self.serialize().hex()

    @property
    def fee(self) -> int:
        return sum(i.value for i in self.vin) - sum(o.value for o in self.vout)


# ── ZIP-317 fees ────────────────────────────────────────────────────────────

def conventional_fee(n_in: int, n_out: int) -> int:
    """ZIP-317 conventional fee for a transparent-only transaction."""
    logical_actions = max(n_in, n_out)
    return MARGINAL_FEE * max(GRACE_ACTIONS, logical_actions)


def estimate_size(n_in: int, n_out: int) -> int:
    return 20 + 3 + len(compact_size(n_in)) + len(compact_size(n_out)) \
        + n_in * P2PKH_INPUT_SIZE + n_out * P2PKH_OUTPUT_SIZE


# ── Coin selection & build ──────────────────────────────────────────────────

def select_coins(utxos: list, target: int, n_out: int) -> tuple:
    """Greedy largest-first selection. Returns (chosen, fee, change).

    `utxos` are dicts with at least value/txid/vout/script_pubkey.
    Raises ValueError when funds are insufficient, reporting the shortfall.
    """
    ordered = sorted(utxos, key=lambda u: -int(u["value"]))
    chosen, total = [], 0
    for u in ordered:
        chosen.append(u)
        total += int(u["value"])
        # try with a change output, then without
        fee_with = conventional_fee(len(chosen), n_out + 1)
        if total >= target + fee_with:
            change = total - target - fee_with
            if change >= DUST_THRESHOLD:
                return chosen, fee_with, change
        fee_without = conventional_fee(len(chosen), n_out)
        if total >= target + fee_without:
            return chosen, total - target, 0
    available = sum(int(u["value"]) for u in utxos)
    needed = target + conventional_fee(max(len(utxos), 1), n_out + 1)
    raise ValueError(
        f"insufficient funds: need {needed} zatoshi "
        f"({needed / 1e8:.8f} ZEC) including fee, have {available} "
        f"({available / 1e8:.8f} ZEC)")


def build_transaction(utxos: list, outputs: list, change_address: str,
                      consensus_branch_id: int, expiry_height: int,
                      fee: int = None) -> tuple:
    """Build an unsigned v5 transaction.

    `outputs` is a list of (address, zatoshi). Returns (Transaction, meta).
    """
    if not outputs:
        raise ValueError("no outputs specified")
    for addr, value in outputs:
        if value <= 0:
            raise ValueError(f"output to {addr} must be positive")
        _k.address_to_script(addr)  # raises for shielded/unsupported targets

    target = sum(v for _, v in outputs)
    chosen, sel_fee, change = select_coins(utxos, target, len(outputs))
    if fee is not None:
        # caller pinned the fee; recompute change against it
        total = sum(int(u["value"]) for u in chosen)
        change = total - target - fee
        if change < 0:
            raise ValueError(f"pinned fee {fee} exceeds available change")
        if change < DUST_THRESHOLD:
            fee, change = fee + change, 0
        sel_fee = fee

    tx = Transaction(consensus_branch_id, expiry_height)
    for u in chosen:
        spk = u["script_pubkey"]
        tx.vin.append(TxIn(u["txid"], int(u["vout"]), int(u["value"]),
                           bytes.fromhex(spk) if isinstance(spk, str) else spk))
    for addr, value in outputs:
        tx.vout.append(TxOut.to_address(addr, value))
    if change > 0:
        tx.vout.append(TxOut.to_address(change_address, change))

    meta = {
        "inputs": len(tx.vin),
        "outputs": len(tx.vout),
        "sent_zatoshi": target,
        "fee_zatoshi": sel_fee,
        "change_zatoshi": change,
        "estimated_size": estimate_size(len(tx.vin), len(tx.vout)),
        "expiry_height": expiry_height,
    }
    return tx, meta


# ── Parsing (used to verify against on-chain transactions) ──────────────────

def parse_v5(raw: bytes) -> "Transaction":
    """Parse a transparent-only v5 transaction. Raises on shielded bundles."""
    def cs(buf, off):
        n = buf[off]
        if n < 0xFD:
            return n, off + 1
        if n == 0xFD:
            return struct.unpack_from("<H", buf, off + 1)[0], off + 3
        if n == 0xFE:
            return struct.unpack_from("<I", buf, off + 1)[0], off + 5
        return struct.unpack_from("<Q", buf, off + 1)[0], off + 9

    header, vgid, branch, lock, expiry = struct.unpack_from("<IIIII", raw, 0)
    if header != V5_HEADER or vgid != V5_VERSION_GROUP_ID:
        raise ValueError(f"not a v5 transaction (header {header:#x}, vgid {vgid:#x})")
    tx = Transaction(branch, expiry, lock)
    off = 20
    n_in, off = cs(raw, off)
    for _ in range(n_in):
        prev_txid = raw[off:off + 32][::-1].hex()
        vout_idx = struct.unpack_from("<I", raw, off + 32)[0]
        off += 36
        slen, off = cs(raw, off)
        script_sig = raw[off:off + slen]
        off += slen
        seq = struct.unpack_from("<I", raw, off)[0]
        off += 4
        # value/script_pubkey are not in the serialized tx; caller fills them in
        ti = TxIn(prev_txid, vout_idx, 0, b"", seq)
        ti.script_sig = script_sig
        tx.vin.append(ti)
    n_out, off = cs(raw, off)
    for _ in range(n_out):
        value = struct.unpack_from("<q", raw, off)[0]
        off += 8
        slen, off = cs(raw, off)
        tx.vout.append(TxOut(value, raw[off:off + slen]))
        off += slen
    n_ss, off = cs(raw, off)
    n_so, off = cs(raw, off)
    n_oa, off = cs(raw, off)
    if n_ss or n_so or n_oa:
        raise ValueError("transaction has shielded bundles; not supported")
    if off != len(raw):
        raise ValueError(f"trailing bytes after parse ({off} of {len(raw)})")
    return tx
