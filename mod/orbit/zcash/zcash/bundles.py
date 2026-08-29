"""
Reading the shielded bundles out of a serialized Zcash transaction.

`tx.py` builds and signs transparent-only transactions; this reads the parts
of somebody else's transaction that `tx.py` refuses to make. A Sapling output
is what a shielded payment physically is -- a value commitment, a note
commitment, an ephemeral key and two ciphertexts -- and `sapling.py` turns
those into a note if one of our keys can open it.

Two serialization formats carry Sapling bundles:

  v4  (version group 0x892F2085, Sapling/Blossom/Heartwood/Canopy) -- proofs
      and signatures are interleaved with each spend and output;
  v5  (version group 0x26A7270A, ZIP-225 / NU5) -- the same data, with proofs
      and signatures moved into their own arrays after the bundle.

A newer transaction version is parsed for its transparent part and then
reported as `layout: unknown` rather than guessed at: a wrong guess would
either invent shielded outputs or, worse, quietly miss real ones. Every parse
is checked structurally (every cv and ephemeral key must be a valid Jubjub
point, every cmu a field element) so a misparse is caught rather than
returned.
"""

import struct

try:
    from . import jubjub as J
    from . import sapling as S
except ImportError:                     # loaded as loose modules
    import jubjub as J
    import sapling as S

V4_VERSION_GROUP_ID = 0x892F2085
V5_VERSION_GROUP_ID = 0x26A7270A

SPEND_V4_SIZE = 384                     # cv, anchor, nullifier, rk, proof, sig
OUTPUT_V4_SIZE = 948                    # + 192-byte proof after the ciphertexts
JOINSPLIT_SAPLING_SIZE = 1698
SPEND_V5_SIZE = 96                      # cv, nullifier, rk
OUTPUT_V5_SIZE = 756                    # cv, cmu, epk, enc(580), out(80)
ORCHARD_ACTION_SIZE = 820


class UnknownLayout(Exception):
    """The transaction version's shielded layout is not one we can read."""


def _cs(b: bytes, o: int):
    n = b[o]
    if n < 0xFD:
        return n, o + 1
    if n == 0xFD:
        return struct.unpack_from("<H", b, o + 1)[0], o + 3
    if n == 0xFE:
        return struct.unpack_from("<I", b, o + 1)[0], o + 5
    return struct.unpack_from("<Q", b, o + 1)[0], o + 9


def _transparent(b: bytes, o: int):
    vin, vout = [], []
    n, o = _cs(b, o)
    for _ in range(n):
        prev, idx = b[o:o + 32][::-1].hex(), struct.unpack_from("<I", b, o + 32)[0]
        o += 36
        ln, o = _cs(b, o)
        o += ln + 4
        vin.append({"txid": prev, "vout": idx})
    n, o = _cs(b, o)
    for _ in range(n):
        value = struct.unpack_from("<q", b, o)[0]
        o += 8
        ln, o = _cs(b, o)
        vout.append({"value_zatoshi": value, "script_pubkey": b[o:o + ln].hex()})
        o += ln
    return vin, vout, o


class SaplingOutput:
    """One Sapling output description, as serialized on chain."""

    __slots__ = ("index", "cv", "cmu", "epk", "enc_ciphertext", "out_ciphertext")

    def __init__(self, index, cv, cmu, epk, enc, out):
        self.index = index
        self.cv, self.cmu, self.epk = cv, cmu, epk
        self.enc_ciphertext, self.out_ciphertext = enc, out

    def looks_valid(self, strict: bool = False) -> bool:
        """Structural check: are these bytes really an output description?

        The cheap form asks whether the two commitments are curve points and
        the note commitment a field element -- about a 1-in-4 pass for random
        bytes, so it is a smoke test, not a proof.

        `strict` adds a prime-order subgroup check on the ephemeral key, which
        random bytes clear about one time in sixteen. It costs a scalar
        multiplication, so callers run it on one output as a canary rather
        than on every output of a long scan.
        """
        if not (J.decode_point(self.cv) is not None
                and int.from_bytes(self.cmu, "little") < J.Q
                and len(self.enc_ciphertext) == S.ENC_CIPHERTEXT_SIZE
                and len(self.out_ciphertext) == S.OUT_CIPHERTEXT_SIZE):
            return False
        epk = J.decode_point(self.epk)
        if epk is None:
            return False
        # epk = [esk] g_d, and g_d comes out of the group hash times the
        # cofactor, so a real ephemeral key is always in the prime-order
        # subgroup.
        return not strict or (epk * J.R).is_identity()

    def to_dict(self) -> dict:
        return {"index": self.index, "cmu": self.cmu.hex(),
                "ephemeral_key": self.epk.hex(), "cv": self.cv.hex()}


def explorer_hash(hex_str: str) -> bytes:
    """Undo the explorer's display order for a 32-byte field.

    Blockchair prints 32-byte fields the way it prints txids -- reversed --
    but leaves the ciphertexts alone. Feeding a reversed value commitment to
    the curve gives a point that is not on it, and feeding a reversed
    ephemeral key to note decryption silently finds nothing at all, which is
    the worse of the two failures.
    """
    return bytes.fromhex(hex_str)[::-1]


def outputs_from_explorer(rows: list) -> list:
    """Build SaplingOutputs from an explorer's decoded `shielded_output_raw`.

    The public explorer already splits each output description into its
    fields, which is how a scan can cover a height range without downloading
    every transaction: one request carries a hundred transactions' worth of
    ciphertexts. The result is validated exactly like a parsed one.
    """
    out = []
    for i, row in enumerate(rows or []):
        try:
            o = SaplingOutput(
                i,
                explorer_hash(row["cv"]), explorer_hash(row["cmu"]),
                explorer_hash(row["ephemeralKey"]),
                bytes.fromhex(row["encCiphertext"]),
                bytes.fromhex(row["outCiphertext"]))
        except (KeyError, ValueError) as e:
            raise UnknownLayout(f"explorer output {i} is malformed: {e}")
        # The first output of each transaction is the canary for a changed
        # byte order: without it a reversed ephemeral key just decrypts to
        # nothing, and the scan reports "no notes" instead of a problem.
        if not o.looks_valid(strict=(i == 0)):
            raise UnknownLayout(
                f"explorer output {i} is not a valid Sapling output "
                f"(byte order or schema changed)")
        out.append(o)
    return out


class Bundles:
    """The shielded content of one transaction."""

    def __init__(self, version, version_group_id, layout):
        self.version = version
        self.version_group_id = version_group_id
        self.layout = layout
        self.vin, self.vout = [], []
        self.sapling_spends = []          # nullifiers, hex
        self.sapling_outputs = []         # SaplingOutput
        self.orchard_actions = 0
        self.value_balance = 0

    @property
    def has_shielded(self) -> bool:
        return bool(self.sapling_spends or self.sapling_outputs
                    or self.orchard_actions)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "layout": self.layout,
            "transparent_inputs": len(self.vin),
            "transparent_outputs": len(self.vout),
            "sapling_spends": len(self.sapling_spends),
            "sapling_outputs": len(self.sapling_outputs),
            "orchard_actions": self.orchard_actions,
            "value_balance_zatoshi": self.value_balance,
            "shielded": self.has_shielded,
        }


def parse(raw) -> Bundles:
    """Parse a serialized transaction into its transparent and Sapling parts."""
    b = bytes.fromhex(raw) if isinstance(raw, str) else raw
    if len(b) < 20:
        raise UnknownLayout("transaction too short")
    header = struct.unpack_from("<I", b, 0)[0]
    overwintered = bool(header & 0x80000000)
    version = header & 0x7FFFFFFF
    if not overwintered:
        raise UnknownLayout(f"pre-Overwinter transaction version {version}")
    vgid = struct.unpack_from("<I", b, 4)[0]

    if vgid == V4_VERSION_GROUP_ID:
        out = _parse_v4(b, version, vgid)
    elif vgid == V5_VERSION_GROUP_ID:
        out = _parse_v5(b, version, vgid)
    else:
        out = Bundles(version, vgid, "unknown")
        try:
            out.vin, out.vout, _ = _transparent(b, 20)
        except (IndexError, struct.error):
            pass
        return out

    bad = [o.index for o in out.sapling_outputs if not o.looks_valid()]
    if bad:
        raise UnknownLayout(
            f"sapling outputs {bad} do not decode as valid bundle entries; "
            f"the v{version} layout was misread")
    return out


def _parse_v4(b: bytes, version: int, vgid: int) -> Bundles:
    out = Bundles(version, vgid, "v4")
    try:
        out.vin, out.vout, o = _transparent(b, 8)
        o += 8                                   # lock_time, nExpiryHeight
        out.value_balance = struct.unpack_from("<q", b, o)[0]
        o += 8
        n_spends, o = _cs(b, o)
        for i in range(n_spends):
            out.sapling_spends.append(b[o + 64:o + 96].hex())   # nullifier
            o += SPEND_V4_SIZE
        n_outputs, o = _cs(b, o)
        for i in range(n_outputs):
            out.sapling_outputs.append(SaplingOutput(
                i, b[o:o + 32], b[o + 32:o + 64], b[o + 64:o + 96],
                b[o + 96:o + 676], b[o + 676:o + 756]))
            o += OUTPUT_V4_SIZE
        n_js, o = _cs(b, o)
        if n_js:
            o += n_js * JOINSPLIT_SAPLING_SIZE + 32 + 64
        if n_spends or n_outputs:
            o += 64                              # bindingSig
    except (IndexError, struct.error) as e:
        raise UnknownLayout(f"truncated v4 transaction: {e}")
    if o != len(b):
        raise UnknownLayout(
            f"v4 parse consumed {o} of {len(b)} bytes -- layout mismatch")
    return out


def _parse_v5(b: bytes, version: int, vgid: int) -> Bundles:
    out = Bundles(version, vgid, "v5")
    try:
        out.vin, out.vout, o = _transparent(b, 20)
        n_spends, o = _cs(b, o)
        spends_at = o
        o += n_spends * SPEND_V5_SIZE
        n_outputs, o = _cs(b, o)
        for i in range(n_outputs):
            out.sapling_outputs.append(SaplingOutput(
                i, b[o:o + 32], b[o + 32:o + 64], b[o + 64:o + 96],
                b[o + 96:o + 676], b[o + 676:o + 756]))
            o += OUTPUT_V5_SIZE
        for i in range(n_spends):
            base = spends_at + i * SPEND_V5_SIZE
            out.sapling_spends.append(b[base + 32:base + 64].hex())
        if n_spends or n_outputs:
            out.value_balance = struct.unpack_from("<q", b, o)[0]
            o += 8
        if n_spends:
            o += 32                              # anchorSapling
        o += n_spends * 192                      # spend proofs
        o += n_spends * 64                       # spend auth sigs
        o += n_outputs * 192                     # output proofs
        if n_spends or n_outputs:
            o += 64                              # bindingSig
        n_actions, o = _cs(b, o)
        out.orchard_actions = n_actions
        if n_actions:
            o += n_actions * ORCHARD_ACTION_SIZE + 1 + 8 + 32
            size_proofs, o = _cs(b, o)
            o += size_proofs + n_actions * 64 + 64
    except (IndexError, struct.error) as e:
        raise UnknownLayout(f"truncated v5 transaction: {e}")
    if o != len(b):
        raise UnknownLayout(
            f"v5 parse consumed {o} of {len(b)} bytes -- layout mismatch")
    return out


# ── Trial decryption ────────────────────────────────────────────────────────

def scan_outputs(bundles: Bundles, ivks: list = None, ovks: list = None) -> list:
    """Try every viewing key against every Sapling output in a transaction.

    Returns [{output, direction, note}]. `incoming` means a key of ours is the
    recipient; `outgoing` means we are the sender and recovered the note from
    the out ciphertext.
    """
    found = []
    for o in bundles.sapling_outputs:
        for ivk in (ivks or []):
            note = S.decrypt_output_with_ivk(ivk, o.epk, o.enc_ciphertext, o.cmu)
            if note is not None:
                found.append({"output": o, "direction": "incoming", "note": note,
                              "ivk": ivk})
                break
        else:
            for ovk in (ovks or []):
                note = S.decrypt_output_with_ovk(
                    ovk, o.cv, o.cmu, o.epk, o.enc_ciphertext, o.out_ciphertext)
                if note is not None:
                    found.append({"output": o, "direction": "outgoing",
                                  "note": note, "ivk": None})
                    break
    return found
