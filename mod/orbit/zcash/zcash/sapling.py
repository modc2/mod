"""
Sapling: real shielded keys, addresses and note decryption, in pure Python.

What this module can do, and what it deliberately cannot:

  * derive Sapling keys from a seed the way every other Zcash wallet does
    (ZIP-32 m/32'/133'/account'), so the same seed gives the same z-address
    here, in Zashi, in Ywallet and in zcashd;
  * encode `zs1...` payment addresses, ZIP-316 unified addresses, and the
    extended spending / full viewing keys other wallets import;
  * decrypt shielded outputs -- with the incoming viewing key for notes paid
    *to* this wallet, and with the outgoing viewing key for notes this wallet
    *sent* -- recovering value, memo and recipient;
  * recompute a note's commitment and nullifier, which is what lets a scan
    tell a note it still owns from one it has already spent.

  * it cannot *create* a shielded output or spend one. Both need zk-SNARK
    proofs (Groth16 over BLS12-381) that are not feasible in pure Python.
    Spending is done by importing the extended spending key this module
    prints into a proving wallet, or by pointing ZCASH_RPC_URL at a node.

Every primitive below is pinned to the official zcash-test-vectors fixtures in
tests/test_shielded.py. If those fail, the keys are wrong -- do not ship.
"""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

try:
    from . import jubjub as J
except ImportError:                      # loaded as a top-level module
    import jubjub as J

# ── Constants ───────────────────────────────────────────────────────────────

COIN_TYPE = 133
ZIP32_PURPOSE = 32

HRP_PAYMENT_ADDRESS = "zs"
HRP_EXT_SPENDING_KEY = "secret-extended-key-main"
HRP_EXT_FULL_VIEWING_KEY = "zxviews"
HRP_UNIFIED_ADDRESS = "u"

# ZIP-316 receiver typecodes.
TYPECODE_P2PKH = 0x00
TYPECODE_P2SH = 0x01
TYPECODE_SAPLING = 0x02
TYPECODE_ORCHARD = 0x03

NOTE_PLAINTEXT_SIZE = 564
ENC_CIPHERTEXT_SIZE = 580            # 564 + 16-byte Poly1305 tag
OUT_CIPHERTEXT_SIZE = 80             # 64 + tag
MEMO_SIZE = 512

SAPLING_ACTIVATION_HEIGHT = 419200


# ── Primitives ──────────────────────────────────────────────────────────────

def prf_expand(sk: bytes, t: bytes) -> bytes:
    return hashlib.blake2b(sk + t, digest_size=64,
                           person=b"Zcash_ExpandSeed").digest()


def to_scalar(x: bytes) -> int:
    return int.from_bytes(x, "little") % J.R


def crh_ivk(ak: bytes, nk: bytes) -> int:
    h = bytearray(hashlib.blake2s(ak + nk, digest_size=32,
                                  person=b"Zcashivk").digest())
    h[31] &= 0x07                     # clamp to 251 bits
    return int.from_bytes(h, "little")


# ── FF1-AES256, radix 2 (ZIP-32 diversifier derivation) ─────────────────────

def _aes_cbc_mac(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    return (enc.update(data) + enc.finalize())[-16:]


def _bits_le(data: bytes) -> list:
    return [(b >> i) & 1 for b in data for i in range(8)]


def _bits_to_bytes_le(bits: list) -> bytes:
    return bytes(sum(bit << i for i, bit in enumerate(bits[j:j + 8]))
                 for j in range(0, len(bits), 8))


def ff1_aes256_encrypt(key: bytes, bits: list) -> list:
    """NIST SP 800-38G FF1 over radix 2 with an empty tweak.

    ZIP-32 uses this to turn a diversifier index into an 88-bit diversifier,
    so that indices can be walked without leaking how many addresses exist.
    """
    n = len(bits)
    u = n // 2
    v = n - u
    a, b = bits[:u], bits[u:]
    byte_len = (v + 7) // 8                       # b, in the spec's notation
    d = 4 * ((byte_len + 3) // 4) + 4
    p = bytes([1, 2, 1, 0, 0, 2, 10, u % 256]) + \
        n.to_bytes(4, "big") + (0).to_bytes(4, "big")
    for i in range(10):
        num_b = int("".join(str(x) for x in b), 2) if b else 0
        q = bytes((-byte_len - 1) % 16) + bytes([i]) + num_b.to_bytes(byte_len, "big")
        r = _aes_cbc_mac(key, p + q)
        s = r
        while len(s) < d:
            block = bytes(x ^ y for x, y in zip(
                r, (len(s) // 16).to_bytes(16, "big")))
            enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
            s += enc.update(block) + enc.finalize()
        s = s[:d]
        y = int.from_bytes(s, "big")
        m = u if i % 2 == 0 else v
        num_a = int("".join(str(x) for x in a), 2) if a else 0
        c = (num_a + y) % (1 << m)
        c_bits = [(c >> (m - 1 - k)) & 1 for k in range(m)]
        a, b = b, c_bits
    return a + b


def diversifier(dk: bytes, index: int) -> bytes:
    """d_j: the 11-byte diversifier at index j for diversifier key dk."""
    if not 0 <= index < (1 << 88):
        raise ValueError("diversifier index out of range")
    bits = ff1_aes256_encrypt(dk, _bits_le(index.to_bytes(11, "little")))
    return _bits_to_bytes_le(bits)


# ── Bech32 / bech32m ────────────────────────────────────────────────────────

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frm, to, pad=True):
    acc, bits, ret = 0, 0, []
    maxv = (1 << to) - 1
    for value in data:
        if value < 0 or (value >> frm):
            return None
        acc = (acc << frm) | value
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to - bits)) & maxv)
    elif bits >= frm or ((acc << (to - bits)) & maxv):
        return None
    return ret


def bech32_encode(hrp: str, data: bytes, m: bool = False) -> str:
    five = _convertbits(list(data), 8, 5)
    const = _BECH32M_CONST if m else _BECH32_CONST
    chk = _polymod(_hrp_expand(hrp) + five + [0] * 6) ^ const
    checksum = [(chk >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in five + checksum)


def bech32_decode(addr: str, m: bool = False) -> tuple:
    """-> (hrp, payload bytes). Raises ValueError on a bad string."""
    s = addr.strip()
    if s.lower() != s and s.upper() != s:
        raise ValueError("mixed case")
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):
        raise ValueError("no separator")
    hrp, body = s[:pos], s[pos + 1:]
    try:
        five = [_CHARSET.index(c) for c in body]
    except ValueError:
        raise ValueError("invalid bech32 character")
    const = _BECH32M_CONST if m else _BECH32_CONST
    if _polymod(_hrp_expand(hrp) + five) != const:
        raise ValueError("bad checksum")
    data = _convertbits(five[:-6], 5, 8, False)
    if data is None:
        raise ValueError("bad padding")
    return hrp, bytes(data)


# ── F4Jumble (ZIP-316) ──────────────────────────────────────────────────────

def _f4_h(i: int, data: bytes, length: int) -> bytes:
    return hashlib.blake2b(data, digest_size=length,
                           person=b"UA_F4Jumble_H" + bytes([i, 0, 0])).digest()


def _f4_g(i: int, data: bytes, length: int) -> bytes:
    out = b""
    j = 0
    while len(out) < length:
        out += hashlib.blake2b(
            data, digest_size=64,
            person=b"UA_F4Jumble_G" + bytes([i, j & 0xFF, j >> 8])).digest()
        j += 1
    return out[:length]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def f4jumble(message: bytes) -> bytes:
    ll = min(64, len(message) // 2)
    a, b = message[:ll], message[ll:]
    b = _xor(b, _f4_g(0, a, len(b)))
    a = _xor(a, _f4_h(0, b, len(a)))
    b = _xor(b, _f4_g(1, a, len(b)))
    a = _xor(a, _f4_h(1, b, len(a)))
    return a + b


def f4jumble_inv(message: bytes) -> bytes:
    ll = min(64, len(message) // 2)
    a, b = message[:ll], message[ll:]
    a = _xor(a, _f4_h(1, b, len(a)))
    b = _xor(b, _f4_g(1, a, len(b)))
    a = _xor(a, _f4_h(0, b, len(a)))
    b = _xor(b, _f4_g(0, a, len(b)))
    return a + b


def _compactsize(n: int) -> bytes:
    if n < 253:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:
        return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")


def _read_compactsize(body: bytes, i: int):
    if i >= len(body):
        raise ValueError("truncated compactsize")
    n = body[i]
    if n < 253:
        return n, i + 1
    width = {253: 2, 254: 4, 255: 8}[n]
    if i + 1 + width > len(body):
        raise ValueError("truncated compactsize")
    value = int.from_bytes(body[i + 1:i + 1 + width], "little")
    return value, i + 1 + width


def encode_unified_address(receivers: list) -> str:
    """ZIP-316 unified address from [(typecode, bytes), ...], lowest first."""
    if not receivers:
        raise ValueError("a unified address needs at least one receiver")
    ordered = sorted(receivers, key=lambda r: r[0])
    if not any(tc in (TYPECODE_SAPLING, TYPECODE_ORCHARD) for tc, _ in ordered):
        raise ValueError("a unified address needs a shielded receiver")
    raw = b"".join(_compactsize(tc) + _compactsize(len(data)) + data
                   for tc, data in ordered)
    padding = HRP_UNIFIED_ADDRESS.encode().ljust(16, b"\x00")
    return bech32_encode(HRP_UNIFIED_ADDRESS, f4jumble(raw + padding), m=True)


def decode_unified_address(addr: str) -> list:
    """-> [(typecode, bytes), ...]. Raises ValueError if it is not a valid UA."""
    hrp, data = bech32_decode(addr, m=True)
    if hrp != HRP_UNIFIED_ADDRESS:
        raise ValueError(f"not a unified address (hrp {hrp!r})")
    if len(data) < 48:
        raise ValueError("unified address too short")
    raw = f4jumble_inv(data)
    body, padding = raw[:-16], raw[-16:]
    if padding != hrp.encode().ljust(16, b"\x00"):
        raise ValueError("bad unified address padding")
    out, i = [], 0
    while i < len(body):
        # Typecodes and lengths are compactsize: a receiver this module has
        # never heard of still has to be skipped cleanly rather than rejected.
        tc, i = _read_compactsize(body, i)
        ln, i = _read_compactsize(body, i)
        if i + ln > len(body):
            raise ValueError("truncated receiver")
        out.append((tc, body[i:i + ln]))
        i += ln
    if not out:
        raise ValueError("unified address has no receivers")
    return out


# ── Keys ────────────────────────────────────────────────────────────────────

class FullViewingKey:
    """ak, nk, ovk (+ dk): sees every note of this account, cannot spend."""

    __slots__ = ("ak", "nk", "ovk", "dk", "_ivk")

    def __init__(self, ak: bytes, nk: bytes, ovk: bytes, dk: bytes = None):
        self.ak, self.nk, self.ovk, self.dk = ak, nk, ovk, dk
        self._ivk = None

    @property
    def ivk(self) -> int:
        if self._ivk is None:
            self._ivk = crh_ivk(self.ak, self.nk)
        return self._ivk

    def fingerprint(self) -> bytes:
        return hashlib.blake2b(self.ak + self.nk + self.ovk + (self.dk or b""),
                               digest_size=32, person=b"ZcashSaplingFVFP").digest()

    def address(self, index: int = 0) -> "PaymentAddress":
        """The payment address at (or after) diversifier index `index`."""
        return self.address_at(index)[0]

    def address_at(self, index: int = 0):
        """-> (address, index actually used).

        About half of all diversifier indices hash to no group element, so the
        search walks forward. Callers that hand out fresh addresses must
        advance past the *returned* index, or the next call gives out the same
        address again.
        """
        if self.dk is None:
            raise ValueError("this viewing key has no diversifier key")
        j = index
        while j < (1 << 88):
            d = diversifier(self.dk, j)
            g_d = J.diversify_hash(d)
            if g_d is not None:
                return PaymentAddress(d, (g_d * self.ivk).bytes()), j
            j += 1
        raise ValueError("no valid diversifier found")


class ExtendedSpendingKey:
    """A ZIP-32 Sapling extended spending key: ask, nsk, ovk, dk + chain code."""

    __slots__ = ("ask", "nsk", "ovk", "dk", "chain_code",
                 "depth", "parent_tag", "child_index")

    def __init__(self, ask, nsk, ovk, dk, chain_code,
                 depth=0, parent_tag=b"\x00" * 4, child_index=0):
        self.ask, self.nsk, self.ovk, self.dk = ask, nsk, ovk, dk
        self.chain_code = chain_code
        self.depth, self.parent_tag, self.child_index = depth, parent_tag, child_index

    # -- derivation --------------------------------------------------------

    @classmethod
    def master(cls, seed: bytes) -> "ExtendedSpendingKey":
        if not 32 <= len(seed) <= 252:
            raise ValueError("seed must be 32..252 bytes")
        i = hashlib.blake2b(seed, digest_size=64,
                            person=b"ZcashIP32Sapling").digest()
        sk_m, c_m = i[:32], i[32:]
        return cls(
            ask=to_scalar(prf_expand(sk_m, b"\x00")),
            nsk=to_scalar(prf_expand(sk_m, b"\x01")),
            ovk=prf_expand(sk_m, b"\x02")[:32],
            dk=prf_expand(sk_m, b"\x10")[:32],
            chain_code=c_m,
        )

    def child(self, index: int) -> "ExtendedSpendingKey":
        """Hardened child derivation (ZIP-32 only defines these for wallets)."""
        if index < 0x80000000:
            index += 0x80000000
        parts = (self.ask.to_bytes(32, "little") + self.nsk.to_bytes(32, "little")
                 + self.ovk + self.dk)
        i = prf_expand(self.chain_code,
                       b"\x11" + parts + index.to_bytes(4, "little"))
        i_l, i_r = i[:32], i[32:]
        return ExtendedSpendingKey(
            ask=(self.ask + to_scalar(prf_expand(i_l, b"\x13"))) % J.R,
            nsk=(self.nsk + to_scalar(prf_expand(i_l, b"\x14"))) % J.R,
            ovk=prf_expand(i_l, b"\x15" + self.ovk)[:32],
            dk=prf_expand(i_l, b"\x16" + self.dk)[:32],
            chain_code=i_r,
            depth=self.depth + 1,
            parent_tag=self.fvk().fingerprint()[:4],
            child_index=index,
        )

    @classmethod
    def from_seed(cls, seed: bytes, account: int = 0) -> "ExtendedSpendingKey":
        """m/32'/133'/account' -- the path every Zcash wallet uses."""
        return (cls.master(seed)
                .child(ZIP32_PURPOSE + 0x80000000)
                .child(COIN_TYPE + 0x80000000)
                .child(account + 0x80000000))

    # -- viewing -----------------------------------------------------------

    def fvk(self) -> FullViewingKey:
        ak = (J.SPENDING_KEY_BASE() * self.ask).bytes()
        nk = (J.PROVING_KEY_BASE() * self.nsk).bytes()
        return FullViewingKey(ak, nk, self.ovk, self.dk)

    def address(self, index: int = 0) -> "PaymentAddress":
        return self.fvk().address(index)

    # -- encoding ----------------------------------------------------------

    def encode(self) -> str:
        """`secret-extended-key-main...` -- importable into a proving wallet."""
        body = (bytes([self.depth]) + self.parent_tag
                + self.child_index.to_bytes(4, "little") + self.chain_code
                + self.ask.to_bytes(32, "little") + self.nsk.to_bytes(32, "little")
                + self.ovk + self.dk)
        return bech32_encode(HRP_EXT_SPENDING_KEY, body)

    def encode_fvk(self) -> str:
        """`zxviews...` -- a watch-only export of the same account."""
        fvk = self.fvk()
        body = (bytes([self.depth]) + self.parent_tag
                + self.child_index.to_bytes(4, "little") + self.chain_code
                + fvk.ak + fvk.nk + fvk.ovk + self.dk)
        return bech32_encode(HRP_EXT_FULL_VIEWING_KEY, body)


def decode_extended_full_viewing_key(s: str) -> FullViewingKey:
    hrp, body = bech32_decode(s)
    if hrp != HRP_EXT_FULL_VIEWING_KEY:
        raise ValueError(f"not an extended full viewing key (hrp {hrp!r})")
    if len(body) != 169:
        raise ValueError("bad extended full viewing key length")
    ak, nk, ovk, dk = body[41:73], body[73:105], body[105:137], body[137:169]
    return FullViewingKey(ak, nk, ovk, dk)


class PaymentAddress:
    """A Sapling payment address: diversifier + diversified transmission key."""

    __slots__ = ("d", "pk_d")

    def __init__(self, d: bytes, pk_d: bytes):
        if len(d) != 11 or len(pk_d) != 32:
            raise ValueError("bad Sapling address components")
        self.d, self.pk_d = d, pk_d

    @property
    def raw(self) -> bytes:
        return self.d + self.pk_d

    def g_d(self):
        g = J.diversify_hash(self.d)
        if g is None:
            raise ValueError("diversifier has no group element")
        return g

    def pk_d_point(self):
        p = J.decode_point(self.pk_d)
        if p is None:
            raise ValueError("pk_d is not a valid Jubjub point")
        return p

    def encode(self) -> str:
        return bech32_encode(HRP_PAYMENT_ADDRESS, self.raw)

    def unified(self, transparent_p2pkh: bytes = None) -> str:
        """A ZIP-316 UA carrying this Sapling receiver (+ optional t-receiver).

        No Orchard receiver is advertised: this module cannot detect Orchard
        payments, so claiming one would lose funds.
        """
        receivers = [(TYPECODE_SAPLING, self.raw)]
        if transparent_p2pkh:
            receivers.append((TYPECODE_P2PKH, transparent_p2pkh))
        return encode_unified_address(receivers)

    def __repr__(self):
        return f"PaymentAddress({self.encode()})"


def decode_payment_address(addr: str) -> PaymentAddress:
    hrp, body = bech32_decode(addr)
    if hrp != HRP_PAYMENT_ADDRESS:
        raise ValueError(f"not a Sapling payment address (hrp {hrp!r})")
    if len(body) != 43:
        raise ValueError("bad Sapling address length")
    return PaymentAddress(body[:11], body[11:])


def sapling_receiver_of(addr: str) -> PaymentAddress:
    """The Sapling receiver of a zs1 address or of a unified address."""
    a = addr.strip()
    if a.startswith(HRP_PAYMENT_ADDRESS + "1"):
        return decode_payment_address(a)
    if a.startswith(HRP_UNIFIED_ADDRESS + "1"):
        for tc, data in decode_unified_address(a):
            if tc == TYPECODE_SAPLING and len(data) == 43:
                return PaymentAddress(data[:11], data[11:])
        raise ValueError("unified address has no Sapling receiver")
    raise ValueError(f"not a shielded address: {addr!r}")


# ── Notes ───────────────────────────────────────────────────────────────────

class Note:
    """A decrypted Sapling note."""

    __slots__ = ("d", "pk_d", "value", "rcm", "rseed", "memo", "leadbyte")

    def __init__(self, d, pk_d, value, rcm, rseed, memo, leadbyte):
        self.d, self.pk_d, self.value = d, pk_d, value
        self.rcm, self.rseed, self.memo, self.leadbyte = rcm, rseed, memo, leadbyte

    @property
    def address(self) -> PaymentAddress:
        return PaymentAddress(self.d, self.pk_d)

    def commitment(self):
        """The note commitment point cm."""
        g_d = J.diversify_hash(self.d)
        if g_d is None:
            raise ValueError("note has an invalid diversifier")
        bits = ([1] * 6
                + J.int_bits(self.value, 64)
                + J.bits_of(g_d.bytes())
                + J.bits_of(self.pk_d))
        return (J.pedersen_hash_to_point(bits)
                + J.NOTE_COMMIT_RANDOMNESS_BASE() * (self.rcm % J.R))

    def cmu(self) -> bytes:
        return self.commitment().u.to_bytes(32, "little")

    def nullifier(self, nk: bytes, position: int) -> bytes:
        """nf = PRF^nfSapling(nk, rho); needs the note's position in the tree."""
        rho = J.mixing_pedersen_hash(self.commitment(), position)
        return hashlib.blake2s(nk + rho.bytes(), digest_size=32,
                               person=b"Zcash_nf").digest()

    def memo_text(self):
        """The memo as text, if it is a text memo (ZIP-302)."""
        if not self.memo or self.memo[0] == 0xF6:
            return None
        if self.memo[0] >= 0xF5:
            return None                # reserved / not a text memo
        try:
            return self.memo.rstrip(b"\x00").decode("utf-8")
        except UnicodeDecodeError:
            return None

    def to_dict(self) -> dict:
        return {
            "value_zatoshi": self.value,
            "value_zec": self.value / 1e8,
            "address": self.address.encode(),
            "memo": self.memo_text(),
            "memo_hex": self.memo.hex() if self.memo else None,
            "note_plaintext_version": self.leadbyte,
        }


def _parse_note_plaintext(p: bytes) -> Note:
    if len(p) < 52:
        raise ValueError("note plaintext too short")
    lead = p[0]
    if lead not in (0x01, 0x02):
        raise ValueError(f"unknown note plaintext version {lead:#04x}")
    d = p[1:12]
    value = int.from_bytes(p[12:20], "little")
    r = p[20:52]
    memo = p[52:52 + MEMO_SIZE]
    if lead == 0x01:
        rcm, rseed = int.from_bytes(r, "little"), None
    else:
        # ZIP-212: rcm and esk are both derived from rseed.
        rseed = r
        rcm = to_scalar(prf_expand(rseed, b"\x04"))
    return Note(d, None, value, rcm, rseed, memo, lead)


def _kdf(shared: bytes, epk: bytes) -> bytes:
    return hashlib.blake2b(shared + epk, digest_size=32,
                           person=b"Zcash_SaplingKDF").digest()


def _ka_agree(scalar: int, point) -> bytes:
    """KA.Agree(sk, P) = [h_J . sk] P, encoded."""
    return (point * (scalar * J.COFACTOR)).bytes()


def esk_of(rseed: bytes) -> int:
    return to_scalar(prf_expand(rseed, b"\x05"))


def decrypt_output_with_ivk(ivk: int, epk: bytes, enc_ciphertext: bytes,
                            cmu: bytes = None):
    """Trial-decrypt a shielded output as its recipient. -> Note or None.

    The Poly1305 tag is the real test of ownership; the ZIP-212 and commitment
    checks below then confirm the sender built the note honestly, which is what
    consensus requires of a note before it can be spent.
    """
    epk_point = J.decode_point(epk)
    if epk_point is None:
        return None
    key = _kdf(_ka_agree(ivk, epk_point), epk)
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(b"\x00" * 12, enc_ciphertext, None)
    except Exception:
        return None                    # not ours (or corrupt)
    try:
        note = _parse_note_plaintext(plaintext)
        g_d = J.diversify_hash(note.d)
        if g_d is None:
            return None
        note.pk_d = (g_d * ivk).bytes()
        if note.leadbyte == 0x02:
            # ZIP-212 binds the ephemeral key to the plaintext's own rseed.
            if (g_d * esk_of(note.rseed)).bytes() != epk:
                return None
        if cmu is not None and note.cmu() != cmu:
            return None
        return note
    except ValueError:
        return None


def value_commitment(value: int, rcv: int):
    """cv = [v] ValueCommitValueBase + [rcv] ValueCommitRandomnessBase."""
    return (J.VALUE_COMMIT_VALUE_BASE() * (value % J.R)
            + J.VALUE_COMMIT_RANDOMNESS_BASE() * (rcv % J.R))


def encrypt_note(address: PaymentAddress, value: int, memo: bytes = None,
                 ovk: bytes = None, rseed: bytes = None, rcv: int = None) -> dict:
    """Build the output description for a note paid to `address`.

    This is the whole of a Sapling output except its zk-SNARK proof: the same
    ciphertexts, ephemeral key and commitments a real wallet would put on
    chain. It exists so decryption can be tested round-trip against a note we
    built ourselves, and so a proving backend could be dropped in later
    without reimplementing note encryption.
    """
    rseed = rseed or os.urandom(32)
    rcv = os.urandom(32) if rcv is None else rcv
    rcv = to_scalar(rcv) if isinstance(rcv, bytes) else rcv
    memo = (memo or b"\xf6").ljust(MEMO_SIZE, b"\x00")[:MEMO_SIZE]

    esk = esk_of(rseed)
    g_d = address.g_d()
    epk = (g_d * esk).bytes()
    key = _kdf(_ka_agree(esk, address.pk_d_point()), epk)
    plaintext = (b"\x02" + address.d + value.to_bytes(8, "little") + rseed + memo)
    enc_ciphertext = ChaCha20Poly1305(key).encrypt(b"\x00" * 12, plaintext, None)

    note = _parse_note_plaintext(plaintext)
    note.pk_d = address.pk_d
    cmu = note.cmu()
    cv = value_commitment(value, rcv).bytes()

    out_ciphertext = None
    if ovk is not None:
        ock = hashlib.blake2b(ovk + cv + cmu + epk, digest_size=32,
                              person=b"Zcash_Derive_ock").digest()
        out_ciphertext = ChaCha20Poly1305(ock).encrypt(
            b"\x00" * 12, address.pk_d + esk.to_bytes(32, "little"), None)

    return {"cv": cv, "cmu": cmu, "epk": epk, "enc_ciphertext": enc_ciphertext,
            "out_ciphertext": out_ciphertext, "note": note}


def decrypt_output_with_ovk(ovk: bytes, cv: bytes, cmu: bytes, epk: bytes,
                            enc_ciphertext: bytes, out_ciphertext: bytes):
    """Recover a note this wallet *sent*, using its outgoing viewing key."""
    ock = hashlib.blake2b(ovk + cv + cmu + epk, digest_size=32,
                          person=b"Zcash_Derive_ock").digest()
    try:
        op = ChaCha20Poly1305(ock).decrypt(b"\x00" * 12, out_ciphertext, None)
    except Exception:
        return None
    if len(op) != 64:
        return None
    pk_d_bytes, esk_bytes = op[:32], op[32:]
    pk_d = J.decode_point(pk_d_bytes)
    if pk_d is None:
        return None
    esk = int.from_bytes(esk_bytes, "little")
    key = _kdf(_ka_agree(esk, pk_d), epk)
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(b"\x00" * 12, enc_ciphertext, None)
    except Exception:
        return None
    try:
        note = _parse_note_plaintext(plaintext)
    except ValueError:
        return None
    note.pk_d = pk_d_bytes
    if note.cmu() != cmu:
        return None
    return note
