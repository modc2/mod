"""
Orchard: real shielded keys, addresses and note decryption, in pure Python.

The Orchard twin of `sapling.py`. What it can do, and what it deliberately
cannot:

  * derive Orchard keys from the same BIP39 seed as everything else in this
    wallet (ZIP-32 m/32'/133'/account', all hardened), so one mnemonic gives
    the same Orchard addresses here, in Zashi, in Ywallet and in zcashd;
  * encode the unified address that carries an Orchard receiver -- Orchard has
    no bech32 address of its own, a `u1...` address *is* the address -- and the
    unified full and incoming viewing keys other wallets import to watch it;
  * decrypt Orchard actions, with the incoming viewing key for notes paid *to*
    this wallet and with the outgoing viewing key for notes it sent,
    recovering value, memo and recipient;
  * recompute a note's commitment and its nullifier. Unlike Sapling's, an
    Orchard nullifier does not depend on the note's position in the commitment
    tree -- so this wallet can tell a spent note from an unspent one from the
    chain alone, with no node and no tree state.

  * it cannot *create* an Orchard action. That needs a Halo 2 proof, which is
    not feasible in pure Python. Spending is done by importing the seed into a
    proving wallet, or by pointing ZCASH_RPC_URL at a node.

The cryptography underneath is `pallas.py` (curve, group hash, Sinsemilla)
and `poseidon.py` (the nullifier PRF). Everything here is pinned to the
official zcash-test-vectors fixtures in tests/test_orchard.py: key
components, note encryption, ZIP-32 derivation and the ZIP-316 encodings. If
those fail, the keys are wrong -- do not ship.
"""

import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

try:
    from . import pallas as PA
    from . import poseidon
    from . import sapling as S
except ImportError:                      # loaded as loose modules
    import pallas as PA
    import poseidon
    import sapling as S

# ── Constants ───────────────────────────────────────────────────────────────

COIN_TYPE = 133
ZIP32_PURPOSE = 32
ZIP32_MASTER_PERSONAL = b"ZcashIP32Orchard"
ZIP32_CHILD_DOMAIN = b"\x81"
FVK_FINGERPRINT_PERSONAL = b"ZcashOrchardFVFP"

TYPECODE_ORCHARD = 0x03
HRP_UNIFIED_FVK = "uview"
HRP_UNIFIED_IVK = "uivk"

L_ORCHARD_BASE = 255                     # bits of a base field element in a commitment

NOTE_PLAINTEXT_SIZE = 564
ENC_CIPHERTEXT_SIZE = 580                # 564 + 16-byte Poly1305 tag
OUT_CIPHERTEXT_SIZE = 80                 # 64 + tag
MEMO_SIZE = 512
ACTION_SIZE = 820                        # cv, nf, rk, cmx, epk, enc, out

# NU5 -- the upgrade that activated Orchard. Nothing before it can hold a note.
NU5_ACTIVATION_HEIGHT = 1687104


class OrchardError(Exception):
    pass


# ── Hashes and commitments ──────────────────────────────────────────────────

def diversify_hash(d: bytes):
    """DiversifyHash^Orchard(d) -- the note's g_d.

    Unlike Sapling's, this never fails: on the (vanishing) chance that the
    group hash lands on the identity, the protocol substitutes the hash of the
    empty message. So every diversifier index gives a usable address.
    """
    point = PA.group_hash(b"z.cash:Orchard-gd", bytes(d))
    if point.is_identity():
        return PA.group_hash(b"z.cash:Orchard-gd", b"")
    return point


def commit_ivk(rivk: int, ak: int, nk: int) -> int:
    """CommitIvk: the incoming viewing key, as a base field element."""
    bits = PA.i2lebsp(L_ORCHARD_BASE, ak) + PA.i2lebsp(L_ORCHARD_BASE, nk)
    return PA.sinsemilla_short_commit(rivk, b"z.cash:Orchard-CommitIvk", bits)


def note_commitment(rcm: int, g_d: bytes, pk_d: bytes, value: int,
                    rho: int, psi: int):
    """NoteCommit^Orchard -- the point whose x coordinate goes on chain."""
    bits = (PA.leos2bsp(g_d) + PA.leos2bsp(pk_d)
            + PA.i2lebsp(64, value)
            + PA.i2lebsp(L_ORCHARD_BASE, rho)
            + PA.i2lebsp(L_ORCHARD_BASE, psi))
    return PA.sinsemilla_commit(rcm, b"z.cash:Orchard-NoteCommit", bits)


def derive_nullifier(nk: int, rho: int, psi: int, cm) -> bytes:
    """nf = Extract([PRF^nfOrchard(nk, rho) + psi] K + cm).

    No note position, no commitment tree, no node: everything on the right
    comes from the action itself and from the note we just decrypted. This is
    the one place where Orchard is *easier* on a light wallet than Sapling.
    """
    scalar = (poseidon.hash(nk, rho) + psi) % PA.P
    return (PA.nullifier_k_base() * scalar + cm).extract().to_bytes(32, "little")


def kdf(shared_secret: bytes, ephemeral_key: bytes) -> bytes:
    return hashlib.blake2b(shared_secret + ephemeral_key, digest_size=32,
                           person=b"Zcash_OrchardKDF").digest()


def prf_ock(ovk: bytes, cv: bytes, cmx: bytes, ephemeral_key: bytes) -> bytes:
    return hashlib.blake2b(ovk + cv + cmx + ephemeral_key, digest_size=32,
                           person=b"Zcash_Orchardock").digest()


def esk_of(rseed: bytes, rho: int) -> int:
    return PA.to_scalar(S.prf_expand(rseed, b"\x04" + rho.to_bytes(32, "little")))


def rcm_of(rseed: bytes, rho: int) -> int:
    return PA.to_scalar(S.prf_expand(rseed, b"\x05" + rho.to_bytes(32, "little")))


def psi_of(rseed: bytes, rho: int) -> int:
    return PA.to_base(S.prf_expand(rseed, b"\x09" + rho.to_bytes(32, "little")))


# ── Keys ────────────────────────────────────────────────────────────────────

class SpendingKey:
    """32 bytes of ZIP-32 output, expanded into (ask, nk, rivk)."""

    __slots__ = ("data", "ask", "nk", "rivk", "ak")

    def __init__(self, data: bytes):
        if len(data) != 32:
            raise OrchardError("an Orchard spending key is 32 bytes")
        self.data = data
        self.ask = PA.to_scalar(S.prf_expand(data, b"\x06"))
        self.nk = PA.to_base(S.prf_expand(data, b"\x07"))
        self.rivk = PA.to_scalar(S.prf_expand(data, b"\x08"))
        if self.ask == 0:
            raise OrchardError("invalid Orchard spending key (ask is zero)")
        ak_point = PA.spending_key_base() * self.ask
        # ak is only the x coordinate, so the sign of ask is free; the
        # protocol pins it so that two wallets sign with the same key.
        if ak_point.to_bytes()[31] & 0x80:
            self.ask = (-self.ask) % PA.Q
        self.ak = ak_point.extract()

    def fvk(self) -> "FullViewingKey":
        return FullViewingKey(self.rivk, self.ak, self.nk)


class ExtendedSpendingKey(SpendingKey):
    """A ZIP-32 Orchard key: a spending key plus its chain code.

    Orchard's ZIP-32 is hardened-only -- there is no public derivation and so
    no non-hardened child. The path this wallet uses is the standard
    m/32'/133'/account'.
    """

    __slots__ = ("chain_code", "depth", "parent_tag", "index")

    def __init__(self, data: bytes, chain_code: bytes, depth: int = 0,
                 parent_tag: bytes = b"\x00\x00\x00\x00", index: int = 0):
        SpendingKey.__init__(self, data)
        self.chain_code = chain_code
        self.depth, self.parent_tag, self.index = depth, parent_tag, index

    @classmethod
    def master(cls, seed: bytes) -> "ExtendedSpendingKey":
        if not 32 <= len(seed) <= 252:
            raise OrchardError("seed must be between 32 and 252 bytes")
        blob = hashlib.blake2b(seed, digest_size=64,
                               person=ZIP32_MASTER_PERSONAL).digest()
        return cls(blob[:32], blob[32:])

    def child(self, index: int) -> "ExtendedSpendingKey":
        if not 0x80000000 <= index <= 0xFFFFFFFF:
            raise OrchardError("Orchard ZIP-32 derivation is hardened-only")
        blob = S.prf_expand(self.chain_code, ZIP32_CHILD_DOMAIN + self.data
                            + struct.pack("<I", index))
        return ExtendedSpendingKey(blob[:32], blob[32:], self.depth + 1,
                                   self.tag(), index)

    @classmethod
    def from_seed(cls, seed: bytes, account: int = 0) -> "ExtendedSpendingKey":
        """m/32'/133'/account' -- the same account as the Sapling key."""
        key = cls.master(seed)
        for level in (ZIP32_PURPOSE, COIN_TYPE, account):
            key = key.child(level | 0x80000000)
        return key

    def fingerprint(self) -> bytes:
        f = self.fvk()
        return hashlib.blake2b(
            f.ak.to_bytes(32, "little") + f.nk.to_bytes(32, "little")
            + f.rivk.to_bytes(32, "little"),
            digest_size=32, person=FVK_FINGERPRINT_PERSONAL).digest()

    def tag(self) -> bytes:
        return self.fingerprint()[:4]

    def to_bytes(self) -> bytes:
        """The 73-byte extended key: depth, parent tag, index, chain code, sk."""
        return (bytes([self.depth]) + self.parent_tag
                + struct.pack("<I", self.index) + self.chain_code + self.data)


class FullViewingKey:
    """(rivk, ak, nk): sees every note of this account, and cannot spend."""

    __slots__ = ("rivk", "ak", "nk", "dk", "ovk", "_ivk")

    def __init__(self, rivk: int, ak: int, nk: int):
        self.rivk, self.ak, self.nk = rivk, ak, nk
        blob = S.prf_expand(rivk.to_bytes(32, "little"),
                            b"\x82" + ak.to_bytes(32, "little")
                            + nk.to_bytes(32, "little"))
        self.dk, self.ovk = blob[:32], blob[32:]
        self._ivk = None

    @property
    def ivk(self) -> int:
        if self._ivk is None:
            self._ivk = commit_ivk(self.rivk, self.ak, self.nk)
            if self._ivk == 0:
                raise OrchardError("this key commits to a zero ivk")
        return self._ivk

    def internal(self) -> "FullViewingKey":
        """The internal (change) key of ZIP-32: same ak and nk, new rivk."""
        rivk = PA.to_scalar(S.prf_expand(
            self.rivk.to_bytes(32, "little"),
            b"\x83" + self.ak.to_bytes(32, "little")
            + self.nk.to_bytes(32, "little")))
        return FullViewingKey(rivk, self.ak, self.nk)

    def diversifier(self, index: int = 0) -> bytes:
        return S.diversifier(self.dk, index)

    def address(self, index: int = 0) -> "Address":
        """Every Orchard diversifier index yields an address -- no skipping."""
        d = self.diversifier(index)
        pk_d = (diversify_hash(d) * self.ivk).to_bytes()
        return Address(d, pk_d)

    def to_bytes(self) -> bytes:
        """ak || nk || rivk -- the Orchard receiver of a unified FVK."""
        return (self.ak.to_bytes(32, "little") + self.nk.to_bytes(32, "little")
                + self.rivk.to_bytes(32, "little"))

    @classmethod
    def from_bytes(cls, blob: bytes) -> "FullViewingKey":
        if len(blob) != 96:
            raise OrchardError("an Orchard full viewing key is 96 bytes")
        ak = int.from_bytes(blob[:32], "little")
        nk = int.from_bytes(blob[32:64], "little")
        rivk = int.from_bytes(blob[64:], "little")
        if ak >= PA.P or nk >= PA.P or rivk >= PA.Q:
            raise OrchardError("full viewing key is out of range")
        return cls(rivk, ak, nk)

    def incoming(self) -> "IncomingViewingKey":
        return IncomingViewingKey(self.dk, self.ivk)


class IncomingViewingKey:
    """(dk, ivk): finds notes paid to this account, and nothing else."""

    __slots__ = ("dk", "ivk")

    def __init__(self, dk: bytes, ivk: int):
        self.dk, self.ivk = dk, ivk

    def address(self, index: int = 0) -> "Address":
        d = S.diversifier(self.dk, index)
        return Address(d, (diversify_hash(d) * self.ivk).to_bytes())

    def to_bytes(self) -> bytes:
        return self.dk + self.ivk.to_bytes(32, "little")

    @classmethod
    def from_bytes(cls, blob: bytes) -> "IncomingViewingKey":
        if len(blob) != 64:
            raise OrchardError("an Orchard incoming viewing key is 64 bytes")
        ivk = int.from_bytes(blob[32:], "little")
        if ivk >= PA.P or ivk == 0:
            raise OrchardError("incoming viewing key is out of range")
        return cls(blob[:32], ivk)


# ── Addresses ───────────────────────────────────────────────────────────────

class Address:
    """An Orchard payment address: diversifier + transmission key.

    There is no bech32 encoding of an Orchard address on its own. ZIP-316
    made the unified address the only way to write one down, which is why
    `encode()` here returns a `u1...` string.
    """

    __slots__ = ("d", "pk_d")

    def __init__(self, d: bytes, pk_d: bytes):
        if len(d) != 11 or len(pk_d) != 32:
            raise OrchardError("bad Orchard address components")
        self.d, self.pk_d = d, pk_d

    @property
    def raw(self) -> bytes:
        return self.d + self.pk_d

    def g_d(self):
        return diversify_hash(self.d)

    def pk_d_point(self):
        point = PA.decode_point(self.pk_d)
        if point is None:
            raise OrchardError("pk_d is not a valid Pallas point")
        return point

    def encode(self, sapling_raw: bytes = None,
               transparent_p2pkh: bytes = None) -> str:
        receivers = [(TYPECODE_ORCHARD, self.raw)]
        if sapling_raw:
            receivers.append((S.TYPECODE_SAPLING, sapling_raw))
        if transparent_p2pkh:
            receivers.append((S.TYPECODE_P2PKH, transparent_p2pkh))
        return S.encode_unified_address(receivers)

    unified = encode

    def __repr__(self):
        return f"OrchardAddress({self.encode()})"


def orchard_receiver_of(addr: str) -> Address:
    """The Orchard receiver of a unified address."""
    a = addr.strip()
    if not a.startswith(S.HRP_UNIFIED_ADDRESS + "1"):
        raise OrchardError("an Orchard address is always a unified address")
    for tc, data in S.decode_unified_address(a):
        if tc == TYPECODE_ORCHARD and len(data) == 43:
            return Address(data[:11], data[11:])
    raise OrchardError("unified address has no Orchard receiver")


# ── Unified viewing keys (ZIP-316) ──────────────────────────────────────────

def encode_unified_fvk(orchard_fvk: FullViewingKey = None,
                       sapling_fvk_bytes: bytes = None,
                       transparent_bytes: bytes = None) -> str:
    """A `uview1...` key: everything needed to watch an account, spend nothing."""
    items = []
    if orchard_fvk is not None:
        items.append((TYPECODE_ORCHARD, orchard_fvk.to_bytes()))
    if sapling_fvk_bytes:
        items.append((S.TYPECODE_SAPLING, sapling_fvk_bytes))
    if transparent_bytes:
        items.append((S.TYPECODE_P2PKH, transparent_bytes))
    if not items:
        raise OrchardError("a unified full viewing key needs a key in it")
    return S.encode_unified(HRP_UNIFIED_FVK, items)


def encode_unified_ivk(orchard_ivk: IncomingViewingKey = None,
                       sapling_ivk_bytes: bytes = None,
                       transparent_bytes: bytes = None) -> str:
    """A `uivk1...` key: finds incoming notes, and cannot see what was sent."""
    items = []
    if orchard_ivk is not None:
        items.append((TYPECODE_ORCHARD, orchard_ivk.to_bytes()))
    if sapling_ivk_bytes:
        items.append((S.TYPECODE_SAPLING, sapling_ivk_bytes))
    if transparent_bytes:
        items.append((S.TYPECODE_P2PKH, transparent_bytes))
    if not items:
        raise OrchardError("a unified incoming viewing key needs a key in it")
    return S.encode_unified(HRP_UNIFIED_IVK, items)


def decode_unified_fvk(key: str) -> dict:
    """-> {'orchard': FullViewingKey|None, 'sapling': bytes|None, ...}"""
    out = {"orchard": None, "sapling": None, "transparent": None, "unknown": []}
    for tc, data in S.decode_unified(HRP_UNIFIED_FVK, key):
        if tc == TYPECODE_ORCHARD:
            out["orchard"] = FullViewingKey.from_bytes(data)
        elif tc == S.TYPECODE_SAPLING:
            out["sapling"] = data
        elif tc == S.TYPECODE_P2PKH:
            out["transparent"] = data
        else:
            out["unknown"].append(tc)
    return out


def decode_unified_ivk(key: str) -> dict:
    out = {"orchard": None, "sapling": None, "transparent": None, "unknown": []}
    for tc, data in S.decode_unified(HRP_UNIFIED_IVK, key):
        if tc == TYPECODE_ORCHARD:
            out["orchard"] = IncomingViewingKey.from_bytes(data)
        elif tc == S.TYPECODE_SAPLING:
            out["sapling"] = data
        elif tc == S.TYPECODE_P2PKH:
            out["transparent"] = data
        else:
            out["unknown"].append(tc)
    return out


# ── Notes ───────────────────────────────────────────────────────────────────

class Note:
    """A decrypted Orchard note."""

    __slots__ = ("d", "pk_d", "value", "rho", "rseed", "memo", "leadbyte")

    def __init__(self, d, pk_d, value, rho, rseed, memo, leadbyte=0x02):
        self.d, self.pk_d, self.value = d, pk_d, value
        self.rho, self.rseed, self.memo, self.leadbyte = rho, rseed, memo, leadbyte

    @property
    def address(self) -> Address:
        return Address(self.d, self.pk_d)

    @property
    def rcm(self) -> int:
        return rcm_of(self.rseed, self.rho)

    @property
    def psi(self) -> int:
        return psi_of(self.rseed, self.rho)

    def commitment(self):
        return note_commitment(self.rcm, diversify_hash(self.d).to_bytes(),
                               self.pk_d, self.value, self.rho, self.psi)

    def cmx(self) -> bytes:
        return self.commitment().extract().to_bytes(32, "little")

    def nullifier(self, nk: int) -> bytes:
        """The note's nullifier -- computable from the note alone."""
        return derive_nullifier(nk, self.rho, self.psi, self.commitment())

    def memo_text(self):
        """The memo as text, if it is a text memo (ZIP-302)."""
        if not self.memo or self.memo[0] == 0xF6:
            return None
        if self.memo[0] >= 0xF5:
            return None                 # reserved / not a text memo
        try:
            return self.memo.rstrip(b"\x00").decode("utf-8")
        except UnicodeDecodeError:
            return None

    def to_dict(self) -> dict:
        return {
            "pool": "orchard",
            "value_zatoshi": self.value,
            "value_zec": self.value / 1e8,
            "address": self.address.encode(),
            "memo": self.memo_text(),
            "memo_hex": self.memo.hex() if self.memo else None,
            "note_plaintext_version": self.leadbyte,
        }


def _parse_note_plaintext(p: bytes, rho: int) -> Note:
    if len(p) != NOTE_PLAINTEXT_SIZE:
        raise ValueError("Orchard note plaintext has the wrong length")
    lead = p[0]
    if lead != 0x02:
        raise ValueError(f"unknown note plaintext version {lead:#04x}")
    value = int.from_bytes(p[12:20], "little")
    return Note(p[1:12], None, value, rho, p[20:52], p[52:564], lead)


def _finish(note: Note, cmx: bytes, epk: bytes, ivk_check=None):
    """The consensus checks every decrypted note has to pass."""
    try:
        g_d = diversify_hash(note.d)
        if ivk_check is not None and (g_d * ivk_check).to_bytes() != epk:
            return None
        if note.cmx() != cmx:
            return None
    except ValueError:
        # An exceptional case in Sinsemilla means this is not a well-formed
        # note; consensus would have rejected it, so neither do we claim it.
        return None
    return note


def decrypt_action_with_ivk(ivk: int, rho: bytes, cmx: bytes, epk: bytes,
                            enc_ciphertext: bytes):
    """Trial-decrypt an action as its recipient. -> Note or None.

    The Poly1305 tag is what actually says "this note is yours"; the checks
    afterwards confirm the sender built it the way consensus requires, so a
    malformed note is never reported as money.
    """
    epk_point = PA.decode_point(epk)
    if epk_point is None or epk_point.is_identity():
        return None
    key = kdf((epk_point * ivk).to_bytes(), epk)
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(b"\x00" * 12,
                                                  enc_ciphertext, None)
    except Exception:
        return None                      # not ours (or corrupt)
    try:
        rho_int = int.from_bytes(rho, "little")
        note = _parse_note_plaintext(plaintext, rho_int)
        g_d = diversify_hash(note.d)
        note.pk_d = (g_d * ivk).to_bytes()
        # ZIP-212: the ephemeral key must be the one the plaintext's own
        # rseed produces, so a sender cannot smuggle in a note we could not
        # later prove we own.
        if (g_d * esk_of(note.rseed, rho_int)).to_bytes() != epk:
            return None
        return _finish(note, cmx, epk)
    except ValueError:
        return None


def decrypt_action_with_ovk(ovk: bytes, cv: bytes, cmx: bytes, rho: bytes,
                            epk: bytes, enc_ciphertext: bytes,
                            out_ciphertext: bytes):
    """Recover a note this wallet *sent*, from the outgoing ciphertext."""
    try:
        op = ChaCha20Poly1305(prf_ock(ovk, cv, cmx, epk)).decrypt(
            b"\x00" * 12, out_ciphertext, None)
    except Exception:
        return None
    pk_d_bytes, esk_bytes = op[:32], op[32:64]
    pk_d = PA.decode_point(pk_d_bytes)
    if pk_d is None or pk_d.to_bytes() != pk_d_bytes:
        return None
    esk = int.from_bytes(esk_bytes, "little")
    if esk >= PA.Q:
        return None
    try:
        plaintext = ChaCha20Poly1305(kdf((pk_d * esk).to_bytes(), epk)).decrypt(
            b"\x00" * 12, enc_ciphertext, None)
    except Exception:
        return None
    try:
        rho_int = int.from_bytes(rho, "little")
        note = _parse_note_plaintext(plaintext, rho_int)
        note.pk_d = pk_d_bytes
        if esk_of(note.rseed, rho_int) != esk:
            return None
        return _finish(note, cmx, epk, ivk_check=esk)
    except ValueError:
        return None


def encrypt_note(address: Address, value: int, rho: bytes, memo: bytes = None,
                 ovk: bytes = None, rseed: bytes = None, rcv: int = None) -> dict:
    """Build the encrypted half of an Orchard action paying `address`.

    Everything a real action carries except its Halo 2 proof and its
    signatures: the two ciphertexts, the ephemeral key, the value commitment
    and cmx, all built exactly as a spending wallet would build them. `rho` is
    the nullifier of the note the action spends, which is what a real bundle
    supplies and what binds this note to its position in the chain.

    It cannot make a *valid* action -- no proof -- so nothing here can be
    broadcast. It exists so decryption can be tested against a note we built
    ourselves, and so a proving backend could be dropped in later without
    reimplementing note encryption.
    """
    rseed = rseed or os.urandom(32)
    rcv = os.urandom(32) if rcv is None else rcv
    rcv = PA.to_scalar(rcv) if isinstance(rcv, bytes) else rcv
    memo = (memo or b"\xf6").ljust(MEMO_SIZE, b"\x00")[:MEMO_SIZE]
    rho_int = int.from_bytes(rho, "little")
    if rho_int >= PA.P:
        raise OrchardError("rho is not a Pallas base field element")

    esk = esk_of(rseed, rho_int)
    g_d = diversify_hash(address.d)
    epk = (g_d * esk).to_bytes()
    key = kdf((address.pk_d_point() * esk).to_bytes(), epk)
    plaintext = (b"\x02" + address.d + value.to_bytes(8, "little")
                 + rseed + memo)
    enc_ciphertext = ChaCha20Poly1305(key).encrypt(b"\x00" * 12, plaintext, None)

    note = _parse_note_plaintext(plaintext, rho_int)
    note.pk_d = address.pk_d
    cmx = note.cmx()
    cv = PA.value_commitment(value, rcv).to_bytes()

    out_ciphertext = None
    if ovk is not None:
        out_ciphertext = ChaCha20Poly1305(prf_ock(ovk, cv, cmx, epk)).encrypt(
            b"\x00" * 12, address.pk_d + esk.to_bytes(32, "little"), None)

    return {"cv": cv, "cmx": cmx, "epk": epk, "rho": rho,
            "enc_ciphertext": enc_ciphertext, "out_ciphertext": out_ciphertext,
            "note": note}
