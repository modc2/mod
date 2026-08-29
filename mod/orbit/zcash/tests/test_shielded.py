"""
Tests for the Sapling shielded pool.

These are consensus tests. Every primitive below is checked against the
official fixtures in zcash/zcash-test-vectors (copied into tests/vectors), so
a wrong curve constant, a wrong personalization string or a flipped byte order
fails here rather than on chain -- where it would mean handing out an address
whose payments this wallet can never find.

The last group is the one that has already caught a real bug: the public
explorer serves 32-byte shielded fields reversed, and a scanner that does not
undo that finds nothing at all and reports "no notes" instead of an error.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("ZCASH_WALLET_DIR", tempfile.mkdtemp())

from zcash import bundles, jubjub, keys, sapling, shielded, wallet  # noqa: E402

VECTORS = Path(__file__).parent / "vectors"


def load(name):
    """The fixtures start with two header rows: a source URL and the columns."""
    rows = json.loads((VECTORS / f"{name}.json").read_text())
    return rows[2:]


def h(s):
    return bytes.fromhex(s)


# ── Curve and generators ────────────────────────────────────────────────────

def test_jubjub_generators_match_the_spec():
    expected = load("sapling_generators")[0] if False else \
        json.loads((VECTORS / "sapling_generators.json").read_text())[2]
    got = [jubjub.SPENDING_KEY_BASE(), jubjub.PROVING_KEY_BASE(),
           jubjub.NULLIFIER_POSITION_BASE(), jubjub.NOTE_COMMIT_RANDOMNESS_BASE(),
           jubjub.VALUE_COMMIT_VALUE_BASE(), jubjub.VALUE_COMMIT_RANDOMNESS_BASE()] + \
          [jubjub.pedersen_generator(i) for i in range(4)]
    assert [p.bytes().hex() for p in got] == expected


def test_point_encoding_roundtrips():
    p = jubjub.SPENDING_KEY_BASE() * 12345
    assert jubjub.decode_point(p.bytes()) == p
    assert jubjub.decode_point(b"\xff" * 32) is None       # not on the curve


# ── Key components ──────────────────────────────────────────────────────────

def test_key_components_against_official_vectors():
    for row in load("sapling_key_components"):
        (sk, ask, nsk, ovk, ak, nk, ivk, default_d, default_pk_d,
         note_v, note_r, note_cmu, note_pos, note_nf) = row
        ask_i = sapling.to_scalar(sapling.prf_expand(h(sk), b"\x00"))
        nsk_i = sapling.to_scalar(sapling.prf_expand(h(sk), b"\x01"))
        assert ask_i.to_bytes(32, "little").hex() == ask
        assert nsk_i.to_bytes(32, "little").hex() == nsk
        assert sapling.prf_expand(h(sk), b"\x02")[:32].hex() == ovk

        ak_b = (jubjub.SPENDING_KEY_BASE() * ask_i).bytes()
        nk_b = (jubjub.PROVING_KEY_BASE() * nsk_i).bytes()
        assert ak_b.hex() == ak and nk_b.hex() == nk

        ivk_i = sapling.crh_ivk(ak_b, nk_b)
        assert ivk_i.to_bytes(32, "little").hex() == ivk

        g_d = jubjub.diversify_hash(h(default_d))
        assert (g_d * ivk_i).bytes().hex() == default_pk_d

        note = sapling.Note(h(default_d), h(default_pk_d), note_v,
                            int.from_bytes(h(note_r), "little"), None, b"", 1)
        assert note.cmu().hex() == note_cmu, "Pedersen note commitment"
        assert note.nullifier(h(nk), note_pos).hex() == note_nf


# ── Note encryption ─────────────────────────────────────────────────────────

def test_note_decryption_against_official_vectors():
    for row in load("sapling_note_encryption"):
        (ovk, ivk, d, pk_d, v, rcm, memo, cv, cmu, esk, epk, shared, k_enc,
         p_enc, c_enc, ock, op, c_out) = row
        note = sapling.decrypt_output_with_ivk(
            int.from_bytes(h(ivk), "little"), h(epk), h(c_enc), h(cmu))
        assert note is not None, "incoming viewing key failed to open the note"
        assert note.value == v
        assert note.d == h(d) and note.pk_d == h(pk_d)

        sent = sapling.decrypt_output_with_ovk(
            h(ovk), h(cv), h(cmu), h(epk), h(c_enc), h(c_out))
        assert sent is not None, "outgoing viewing key failed to open the note"
        assert sent.value == v


def test_a_note_does_not_open_with_the_wrong_key():
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    other = sapling.ExtendedSpendingKey.from_seed(bytes(range(1, 33))).fvk()
    out = sapling.encrypt_note(fvk.address(0), 1_000, b"hi", ovk=fvk.ovk)
    assert sapling.decrypt_output_with_ivk(
        other.ivk, out["epk"], out["enc_ciphertext"], out["cmu"]) is None
    assert sapling.decrypt_output_with_ovk(
        other.ovk, out["cv"], out["cmu"], out["epk"],
        out["enc_ciphertext"], out["out_ciphertext"]) is None


def test_encrypt_then_decrypt_roundtrip():
    fvk = sapling.ExtendedSpendingKey.from_seed(b"round trip seed 0123456789abcdef").fvk()
    addr = fvk.address(0)
    out = sapling.encrypt_note(addr, 123_450_000, "memo: gm".encode(), ovk=fvk.ovk)
    got = sapling.decrypt_output_with_ivk(fvk.ivk, out["epk"],
                                          out["enc_ciphertext"], out["cmu"])
    assert got.value == 123_450_000
    assert got.memo_text() == "memo: gm"
    assert got.address.encode() == addr.encode()
    sent = sapling.decrypt_output_with_ovk(
        fvk.ovk, out["cv"], out["cmu"], out["epk"],
        out["enc_ciphertext"], out["out_ciphertext"])
    assert sent.value == 123_450_000


# ── Addresses: ZIP-32, FF1 diversifiers, ZIP-316 ────────────────────────────

def test_unified_addresses_and_zip32_against_official_vectors():
    checked_ua = checked_zip32 = 0
    for row in load("unified_address"):
        (p2pkh, p2sh, sap, orchard, unknown_tc, unknown, addr,
         seed, account, div_index) = row
        receivers = []
        for typecode, value in ((0x00, p2pkh), (0x01, p2sh), (0x02, sap),
                                (0x03, orchard), (unknown_tc, unknown)):
            if value and typecode is not None:
                receivers.append((typecode, h(value)))
        if not any(tc in (0x02, 0x03) for tc, _ in receivers):
            continue
        assert sapling.encode_unified_address(receivers) == addr
        assert sapling.decode_unified_address(addr) == sorted(receivers)
        checked_ua += 1

        if sap and seed is not None:
            xsk = sapling.ExtendedSpendingKey.from_seed(h(seed), account)
            assert xsk.fvk().address(div_index).raw.hex() == sap
            checked_zip32 += 1
    assert checked_ua > 50 and checked_zip32 > 20


def test_payment_address_roundtrip_and_checksum():
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    addr = fvk.address(0)
    assert sapling.decode_payment_address(addr.encode()).raw == addr.raw
    with pytest.raises(ValueError):
        sapling.decode_payment_address(addr.encode()[:-1] + "q")
    with pytest.raises(ValueError):
        sapling.decode_payment_address("zs1abcdef")


def test_unified_address_never_claims_an_orchard_receiver():
    """We cannot detect Orchard payments, so we must not advertise Orchard."""
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    ua = fvk.address(0).unified(bytes(range(20)))
    typecodes = [tc for tc, _ in sapling.decode_unified_address(ua)]
    assert sapling.TYPECODE_ORCHARD not in typecodes
    assert sapling.TYPECODE_SAPLING in typecodes


def test_fresh_addresses_do_not_repeat():
    """Half of all diversifier indices are unusable; the walk must move past."""
    fvk = sapling.ExtendedSpendingKey.from_seed(b"diversify me please 0123456789ab").fvk()
    seen, index = set(), 0
    for _ in range(8):
        addr, used = fvk.address_at(index)
        assert addr.encode() not in seen
        seen.add(addr.encode())
        index = used + 1


def test_extended_keys_encode_for_other_wallets():
    xsk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32)), 0)
    assert xsk.encode().startswith("secret-extended-key-main1")
    assert xsk.encode_fvk().startswith("zxviews1")
    fvk = sapling.decode_extended_full_viewing_key(xsk.encode_fvk())
    assert fvk.ivk == xsk.fvk().ivk
    assert fvk.address(0).encode() == xsk.address(0).encode()


def test_address_validation_rejects_lookalikes():
    assert not keys.is_valid_address("zs1abcdef")
    assert not keys.is_valid_address("u1abcdef")
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    assert keys.is_valid_address(fvk.address(0).encode())


def test_unified_address_with_a_transparent_receiver_is_payable():
    """A UA that publishes a P2PKH receiver can be paid transparently."""
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    t_addr = keys.pubkey_to_address(
        keys.privkey_to_pubkey(keys.HDKey.from_seed(b"t").priv))
    ua = fvk.address(0).unified(keys.decode_address(t_addr)["hash160"])
    info = keys.decode_address(ua)
    assert info["spendable"] and info["paid_receiver"] == "p2pkh"
    assert info["transparent_address"] == t_addr
    assert keys.address_to_script(ua) == keys.address_to_script(t_addr)

    shielded_only = keys.decode_address(fvk.address(0).unified())
    assert shielded_only["spendable"] is False


# ── Transactions ────────────────────────────────────────────────────────────

def test_parses_a_real_mainnet_sapling_transaction():
    raw = (VECTORS / "sapling_tx_v4.hex").read_text().strip()
    parsed = bundles.parse(raw)
    assert parsed.layout == "v4"
    assert parsed.version == 4
    assert len(parsed.sapling_spends) == 1
    assert len(parsed.sapling_outputs) == 1
    assert parsed.value_balance == 1000          # the fee, moved out of the pool
    for out in parsed.sapling_outputs:
        assert out.looks_valid()


def test_a_stranger_s_notes_do_not_decrypt():
    raw = (VECTORS / "sapling_tx_v4.hex").read_text().strip()
    parsed = bundles.parse(raw)
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    assert bundles.scan_outputs(parsed, ivks=[fvk.ivk], ovks=[fvk.ovk]) == []


def test_a_misread_bundle_raises_instead_of_inventing_outputs():
    raw = bytearray.fromhex((VECTORS / "sapling_tx_v4.hex").read_text().strip())
    raw[-8] ^= 0xFF                               # corrupt the tail
    with pytest.raises(bundles.UnknownLayout):
        bundles.parse(bytes(raw[:-4]))


# ── The explorer's byte order ───────────────────────────────────────────────

def _explorer_row(out, height=100, txid="ab" * 32):
    """A Sapling output the way the public explorer serves it: hashes reversed."""
    return {
        "hash": txid, "block_id": height, "version": 4,
        "shielded_value_delta": -1000,
        "shielded_input_raw": [],
        "shielded_output_raw": [{
            "cv": out["cv"][::-1].hex(),
            "cmu": out["cmu"][::-1].hex(),
            "ephemeralKey": out["epk"][::-1].hex(),
            "encCiphertext": out["enc_ciphertext"].hex(),
            "outCiphertext": out["out_ciphertext"].hex(),
        }],
    }


def test_scanning_an_explorer_row_finds_the_note():
    fvk = sapling.ExtendedSpendingKey.from_seed(b"explorer order seed 123456789abc").fvk()
    out = sapling.encrypt_note(fvk.address(0), 250_000_000, b"paid", ovk=fvk.ovk)
    scan = shielded.scan_explorer_row(_explorer_row(out), fvk)
    assert len(scan["notes"]) == 1
    note = scan["notes"][0]
    assert note["value_zec"] == 2.5
    assert note["memo"] == "paid"
    assert note["direction"] == "incoming"

    summary = shielded.summarize([scan])
    assert summary["received_zec"] == 2.5
    assert summary["spend_detection"] == "unavailable"   # no positions
    assert summary["unspent_zec"] is None


def test_explorer_rows_in_the_wrong_byte_order_raise():
    """The failure mode to avoid is a silent 'no notes found'.

    A reversed ephemeral key still lands on the curve half the time, so the
    cheap structural check is not enough; the subgroup check on the first
    output is what turns a silent miss into an error.
    """
    fvk = sapling.ExtendedSpendingKey.from_seed(b"explorer order seed 123456789abc").fvk()
    out = sapling.encrypt_note(fvk.address(0), 1, b"", ovk=fvk.ovk)
    row = _explorer_row(out)
    row["shielded_output_raw"][0]["ephemeralKey"] = out["epk"].hex()   # not reversed
    with pytest.raises(bundles.UnknownLayout):
        shielded.scan_explorer_row(row, fvk)


def test_scan_reports_transactions_it_could_not_read():
    scans = [{"txid": "aa", "notes": []},
             {"txid": "bb", "notes": [], "error": "layout mismatch"}]
    out = shielded.summarize(scans)
    shielded._report_unreadable(out, scans)
    assert out["unreadable_transactions"] == 1
    assert "missed" in out["warning"]


# ── Commitment tree frontier ────────────────────────────────────────────────

def test_frontier_size_counts_filled_subtrees():
    def opt(x):
        return b"\x01" + x if x else b"\x00"
    node = bytes(32)
    # left set, right empty, parents [set, empty, set] -> 1 + 2 + 8 leaves
    blob = opt(node) + opt(None) + bytes([3]) + opt(node) + opt(None) + opt(node)
    assert shielded._frontier_size(blob) == 1 + 2 + 8


def test_frontier_parse_refuses_to_guess():
    with pytest.raises(shielded.ShieldedError):
        shielded._frontier_size(b"\x01" + bytes(32) + b"\x00" + bytes([1])
                                + b"\x00" + b"trailing")


# ── Wallet integration ──────────────────────────────────────────────────────

MNEMONIC = ("abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about")


def test_wallet_derives_a_stable_shielded_account(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    created = wallet.create("shieldtest", "pw", mnemonic=MNEMONIC, birthday=2_000_000)
    account = wallet.shielded("shieldtest")
    assert created["shielded_address"].startswith("zs1")
    assert created["unified_address"].startswith("u1")
    assert account["birthday"] == 2_000_000

    # The same words give the same z-address here as anywhere else.
    direct = shielded.derive_address(MNEMONIC, "", 0, 0)
    assert direct["address"] == created["shielded_address"]

    fresh = wallet.new_shielded_address("shieldtest", "pw", "donations")
    assert fresh["address"] != created["shielded_address"]
    assert wallet.shielded("shieldtest")["next_index"] > fresh["diversifier_index"]


def test_wallet_shielded_key_matches_the_export(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    wallet.create("exporttest", "pw", mnemonic=MNEMONIC)
    xsk = wallet.shielded_key("exporttest", "pw")
    exported = shielded.export_keys(MNEMONIC, "", 0)
    assert xsk.encode() == exported["extended_spending_key"]
    assert xsk.address(0).encode() == exported["default_address"]


def test_imported_key_wallets_say_why_they_have_no_shielded_account(tmp_path,
                                                                    monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    priv = keys.HDKey.from_seed(b"import me").priv
    wallet.import_key("watch", "pw", keys.privkey_to_wif(priv))
    with pytest.raises(wallet.WalletError, match="seed"):
        wallet.shielded_key("watch", "pw")
