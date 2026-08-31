"""
Tests for the Orchard shielded pool.

These are consensus tests, and they are the reason it is safe for a unified
address from this module to advertise an Orchard receiver. Every primitive --
the curve, the hash into the group, Sinsemilla, Poseidon, the key derivation,
the note encryption and the ZIP-316 encodings -- is checked against the
official fixtures in zcash/zcash-test-vectors (copied into tests/vectors). If
one of them fails, addresses derived here are not the addresses the same seed
produces in Zashi or zcashd, and payments to them would land where this wallet
can never look. Do not ship past a red test in this file.

The last groups are the ones that catch real-world drift rather than bad
arithmetic: real mainnet transactions in all three serializations, and the
rule that `capabilities()` and the addresses this module hands out have to
agree about which pools it can read.
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

from zcash import (bundles, keys, orchard, pallas, poseidon,  # noqa: E402
                   sapling, shielded)

VECTORS = Path(__file__).parent / "vectors"


def load(name):
    """The fixtures start with two header rows: a source URL and the columns."""
    return json.loads((VECTORS / f"{name}.json").read_text())[2:]


def h(s):
    return bytes.fromhex(s)


def bits(msg):
    """A fixture's message, as a bit list.

    The Sinsemilla vectors write their bit strings as hex bytes that are each
    0 or 1 -- except the first, which is already a list.
    """
    return list(h(msg)) if isinstance(msg, str) else list(msg)


# ── Pallas: the curve under Orchard ─────────────────────────────────────────

def test_map_to_curve_matches_the_spec():
    for u, point in load("orchard_map_to_curve"):
        x, y = pallas.map_to_curve_simple_swu(int.from_bytes(h(u), "little"))
        encoded = bytearray(x.to_bytes(32, "little"))
        encoded[31] |= (y & 1) << 7
        assert bytes(encoded).hex() == point


def test_group_hash_matches_the_spec():
    for domain, msg, point in load("orchard_group_hash"):
        assert pallas.group_hash(h(domain), h(msg)).to_bytes().hex() == point


def test_fixed_generators_match_the_spec():
    expected = load("orchard_generators")[0]
    got = [
        pallas.spending_key_base(),
        pallas.nullifier_k_base(),
        *pallas.value_commit_bases(),
        pallas.group_hash(b"z.cash:Orchard-NoteCommit-r", b""),
        pallas.group_hash(b"z.cash:SinsemillaQ", b"z.cash:Orchard-NoteCommit-M"),
        pallas.group_hash(b"z.cash:Orchard-CommitIvk-r", b""),
        pallas.group_hash(b"z.cash:SinsemillaQ", b"z.cash:Orchard-CommitIvk-M"),
        pallas.group_hash(b"z.cash:SinsemillaQ", b"z.cash:Orchard-MerkleCRH"),
    ]
    assert [p.to_bytes().hex() for p in got] == expected


def test_sinsemilla_matches_the_spec():
    for domain, msg, point, digest in load("orchard_sinsemilla"):
        p = pallas.sinsemilla_hash_to_point(h(domain), bits(msg))
        assert p.to_bytes().hex() == point
        assert p.extract().to_bytes(32, "little").hex() == digest


def test_sinsemilla_refuses_anything_that_is_not_bits():
    # A caller that hands over "0101..." as text would otherwise get a hash of
    # nonsense, and a wrong commitment is a note nobody can find again.
    with pytest.raises(ValueError):
        pallas.sinsemilla_hash_to_point(b"z.cash:test-Sinsemilla", "0101010101")
    with pytest.raises(ValueError):
        pallas.sinsemilla_hash_to_point(b"z.cash:test-Sinsemilla", b"\x02" * 10)


def test_poseidon_matches_the_spec():
    for (x, y), out in load("orchard_poseidon_hash"):
        got = poseidon.hash(int.from_bytes(h(x), "little"),
                            int.from_bytes(h(y), "little"))
        assert got.to_bytes(32, "little").hex() == out


def test_point_encoding_roundtrips():
    p = pallas.spending_key_base() * 987654321
    assert pallas.decode_point(p.to_bytes()) == p
    assert pallas.decode_point(b"\xff" * 32) is None       # not on the curve
    assert pallas.decode_point(bytes(32)).is_identity()


# ── Keys ────────────────────────────────────────────────────────────────────

def test_key_components_against_official_vectors():
    for row in load("orchard_key_components"):
        (sk, ask, ak, nk, rivk, ivk, ovk, dk, default_d, default_pk_d,
         internal_rivk, internal_ivk, internal_ovk, internal_dk,
         note_v, note_rho, note_rseed, note_cmx, note_nf) = row
        key = orchard.SpendingKey(h(sk))
        fvk = key.fvk()
        assert key.ask.to_bytes(32, "little").hex() == ask
        assert key.ak.to_bytes(32, "little").hex() == ak
        assert key.nk.to_bytes(32, "little").hex() == nk
        assert key.rivk.to_bytes(32, "little").hex() == rivk
        assert fvk.ivk.to_bytes(32, "little").hex() == ivk
        assert fvk.ovk.hex() == ovk
        assert fvk.dk.hex() == dk
        assert fvk.diversifier(0).hex() == default_d
        assert fvk.address(0).pk_d.hex() == default_pk_d

        internal = fvk.internal()
        assert internal.rivk.to_bytes(32, "little").hex() == internal_rivk
        assert internal.ivk.to_bytes(32, "little").hex() == internal_ivk
        assert internal.ovk.hex() == internal_ovk
        assert internal.dk.hex() == internal_dk

        note = orchard.Note(h(default_d), h(default_pk_d), note_v,
                            int.from_bytes(h(note_rho), "little"),
                            h(note_rseed), b"\x00" * 512)
        assert note.cmx().hex() == note_cmx
        assert note.nullifier(key.nk).hex() == note_nf


def test_zip32_derivation_against_official_vectors():
    rows = load("orchard_zip32")
    key = orchard.ExtendedSpendingKey.master(bytes(range(32)))
    for i, (sk, chain_code, xsk, fingerprint) in enumerate(rows):
        if i:
            key = key.child(0x80000000 | i)
        assert key.data.hex() == sk
        assert key.chain_code.hex() == chain_code
        assert key.to_bytes().hex() == xsk
        assert key.fingerprint().hex() == fingerprint


def test_zip32_is_hardened_only():
    key = orchard.ExtendedSpendingKey.master(bytes(range(32)))
    with pytest.raises(orchard.OrchardError):
        key.child(0)


def test_full_viewing_key_roundtrips_through_bytes():
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    assert orchard.FullViewingKey.from_bytes(fvk.to_bytes()).ivk == fvk.ivk
    with pytest.raises(orchard.OrchardError):
        orchard.FullViewingKey.from_bytes(b"\x00" * 95)


def test_every_diversifier_index_gives_an_address():
    # Unlike Sapling, no Orchard diversifier index is unusable -- so address
    # bookkeeping never has to skip one.
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    seen = {fvk.address(i).encode() for i in range(25)}
    assert len(seen) == 25


# ── Notes ───────────────────────────────────────────────────────────────────

def test_note_decryption_against_official_vectors():
    for row in load("orchard_note_encryption"):
        (ivk_bytes, ovk, default_d, default_pk_d, value, rseed, memo, cv_net,
         rho, cmx, esk, ephemeral_key, shared_secret, k_enc, p_enc, c_enc,
         ock, op, c_out) = row
        ivk = orchard.IncomingViewingKey.from_bytes(h(ivk_bytes))

        note = orchard.decrypt_action_with_ivk(ivk.ivk, h(rho), h(cmx),
                                               h(ephemeral_key), h(c_enc))
        assert note is not None
        assert note.value == value
        assert note.d.hex() == default_d
        assert note.pk_d.hex() == default_pk_d
        assert note.memo.hex() == memo

        sent = orchard.decrypt_action_with_ovk(h(ovk), h(cv_net), h(cmx),
                                               h(rho), h(ephemeral_key),
                                               h(c_enc), h(c_out))
        assert sent is not None and sent.value == value


def test_a_note_does_not_open_with_the_wrong_key():
    row = load("orchard_note_encryption")[0]
    ivk = orchard.IncomingViewingKey.from_bytes(h(row[0]))
    stranger = (ivk.ivk + 1) % pallas.P
    assert orchard.decrypt_action_with_ivk(
        stranger, h(row[8]), h(row[9]), h(row[11]), h(row[15])) is None


def test_encrypt_then_decrypt_roundtrip():
    key = orchard.ExtendedSpendingKey.from_seed(bytes(range(32)))
    fvk = key.fvk()
    address = fvk.address(3)
    rho = (0x1234).to_bytes(32, "little")
    built = orchard.encrypt_note(address, 4_200_000, rho, memo=b"paid",
                                 ovk=fvk.ovk)

    received = orchard.decrypt_action_with_ivk(
        fvk.ivk, rho, built["cmx"], built["epk"], built["enc_ciphertext"])
    assert received is not None
    assert received.value == 4_200_000
    assert received.memo_text() == "paid"
    assert received.address.encode() == address.encode()

    sent = orchard.decrypt_action_with_ovk(
        fvk.ovk, built["cv"], built["cmx"], rho, built["epk"],
        built["enc_ciphertext"], built["out_ciphertext"])
    assert sent is not None and sent.value == 4_200_000

    # The nullifier needs no note position and no commitment tree.
    assert len(received.nullifier(fvk.nk)) == 32
    assert received.nullifier(fvk.nk) == sent.nullifier(fvk.nk)


def test_a_tampered_action_is_not_reported_as_money():
    key = orchard.ExtendedSpendingKey.from_seed(bytes(range(32)))
    fvk = key.fvk()
    rho = (7).to_bytes(32, "little")
    built = orchard.encrypt_note(fvk.address(0), 100_000, rho)
    wrong_cmx = bytes(32)
    assert orchard.decrypt_action_with_ivk(
        fvk.ivk, rho, wrong_cmx, built["epk"], built["enc_ciphertext"]) is None


# ── Addresses and unified encodings (ZIP-316) ───────────────────────────────

def test_unified_addresses_against_official_vectors():
    checked = 0
    for row in load("unified_address"):
        (p2pkh, p2sh, sapling_raw, orchard_raw, unknown_tc, unknown,
         unified, seed, account, diversifier_index) = row
        if not orchard_raw:
            continue
        raw = h(orchard_raw)
        address = orchard.Address(raw[:11], raw[11:])
        # Rows with a receiver this module does not encode (p2sh, or a
        # typecode from the future) can still be decoded, just not rebuilt.
        if unknown_tc is None and not p2sh:
            assert address.encode(
                sapling_raw=h(sapling_raw) if sapling_raw else None,
                transparent_p2pkh=h(p2pkh) if p2pkh else None) == unified
            checked += 1
        assert orchard.orchard_receiver_of(unified).raw == raw
    assert checked > 20


def test_unified_viewing_keys_against_official_vectors():
    """Every official row carries an item from the future, on purpose.

    So the encoding is checked by building the same container the fixture
    describes -- unknown typecode included -- and the decoding by reading the
    Orchard key back out of the fixture's own string.
    """
    for name, hrp, cls, decode in (
            ("unified_full_viewing_keys", orchard.HRP_UNIFIED_FVK,
             orchard.FullViewingKey, orchard.decode_unified_fvk),
            ("unified_incoming_viewing_keys", orchard.HRP_UNIFIED_IVK,
             orchard.IncomingViewingKey, orchard.decode_unified_ivk)):
        checked = 0
        for row in load(name):
            transparent, sapling_item, orchard_item, unknown_tc = row[:4]
            unknown_item, unified = row[4], row[5]
            items = []
            if transparent:
                items.append((sapling.TYPECODE_P2PKH, h(transparent)))
            if sapling_item:
                items.append((sapling.TYPECODE_SAPLING, h(sapling_item)))
            if orchard_item:
                items.append((orchard.TYPECODE_ORCHARD, h(orchard_item)))
            if unknown_tc is not None and unknown_item:
                items.append((unknown_tc, h(unknown_item)))
            assert sapling.encode_unified(hrp, items) == unified

            read = decode(unified)
            if orchard_item:
                assert read["orchard"].to_bytes().hex() == orchard_item
                assert cls.from_bytes(h(orchard_item)).to_bytes().hex() == \
                    orchard_item
                checked += 1
            if unknown_tc is not None and unknown_item:
                # An item from a future upgrade is skipped, not rejected.
                assert unknown_tc in read["unknown"]
        assert checked > 5, name


def test_unified_viewing_keys_this_module_builds_read_back():
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    sapling_fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    sapling_item = (sapling_fvk.ak + sapling_fvk.nk + sapling_fvk.ovk
                    + sapling_fvk.dk)
    uview = orchard.encode_unified_fvk(fvk, sapling_item)
    assert uview.startswith(orchard.HRP_UNIFIED_FVK + "1")
    read = orchard.decode_unified_fvk(uview)
    assert read["orchard"].to_bytes() == fvk.to_bytes()
    assert read["sapling"] == sapling_item

    uivk = orchard.encode_unified_ivk(fvk.incoming())
    assert orchard.decode_unified_ivk(uivk)["orchard"].ivk == fvk.ivk


def test_a_unified_key_is_not_readable_as_an_address():
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    uview = orchard.encode_unified_fvk(fvk)
    with pytest.raises(ValueError):
        sapling.decode_unified_address(uview)
    with pytest.raises(orchard.OrchardError):
        orchard.orchard_receiver_of(uview)


def test_address_validation_recognises_an_orchard_receiver():
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    unified = fvk.address(0).encode()
    info = keys.decode_address(unified)
    assert info["type"] == "unified"
    assert "orchard" in info["receivers"]


# ── Real transactions ───────────────────────────────────────────────────────

def raw_fixture(name):
    return (VECTORS / name).read_text().strip()


def test_parses_a_real_mainnet_v5_orchard_transaction():
    parsed = bundles.parse(raw_fixture("orchard_tx_v5.hex"))
    assert parsed.layout == "v5"
    assert len(parsed.orchard_actions) == 2
    assert parsed.orchard_value_balance == -30629470
    assert parsed.has_shielded
    for action in parsed.orchard_actions:
        assert action.looks_valid(strict=True)
    # rho of the note an action creates is the nullifier it publishes.
    assert len(set(parsed.orchard_nullifiers)) == 2


def test_parses_a_real_mainnet_v6_transaction():
    parsed = bundles.parse(raw_fixture("orchard_tx_v6.hex"))
    assert parsed.layout == "v6"
    assert len(parsed.orchard_actions) == 2
    assert parsed.orchard_action_groups == 1
    assert all(a.looks_valid(strict=True) for a in parsed.orchard_actions)


def test_parses_a_v6_transaction_with_several_action_groups():
    parsed = bundles.parse(raw_fixture("orchard_tx_v6_groups.hex"))
    assert parsed.layout == "v6"
    assert parsed.orchard_action_groups == 2
    assert len(parsed.orchard_actions) == 4
    assert [a.index for a in parsed.orchard_actions] == [0, 1, 2, 3]
    assert all(a.looks_valid(strict=True) for a in parsed.orchard_actions)


def test_a_transparent_v6_transaction_has_no_shielded_content():
    parsed = bundles.parse(raw_fixture("orchard_tx_v6_transparent.hex"))
    assert parsed.layout == "v6"
    assert not parsed.has_shielded
    assert len(parsed.vin) == 1 and len(parsed.vout) == 2


def test_a_misread_transaction_raises_instead_of_inventing_actions():
    original = h(raw_fixture("orchard_tx_v5.hex"))
    parsed = bundles.parse(original)

    # A shifted bundle: every field after it reads as something else. The
    # structural checks are what stop that being reported as somebody's note.
    epk_at = original.find(parsed.orchard_actions[0].epk)
    assert epk_at > 0
    corrupt = bytearray(original)
    corrupt[epk_at:epk_at + 32] = b"\xff" * 32     # not a Pallas point
    with pytest.raises(bundles.UnknownLayout):
        bundles.parse(bytes(corrupt))

    # And a truncated one must not be read as a shorter bundle.
    with pytest.raises(bundles.UnknownLayout):
        bundles.parse(original[:-40])


def test_a_strangers_actions_do_not_decrypt():
    parsed = bundles.parse(raw_fixture("orchard_tx_v5.hex"))
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    assert bundles.scan_actions(parsed, ivks=[fvk.ivk], ovks=[fvk.ovk]) == []


def test_scanning_finds_our_own_action_in_a_bundle():
    fvk = orchard.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    rho = (99).to_bytes(32, "little")
    built = orchard.encrypt_note(fvk.address(1), 250_000, rho, memo=b"hi",
                                 ovk=fvk.ovk)
    holder = bundles.Bundles(5, bundles.V5_VERSION_GROUP_ID, "v5")
    holder.orchard_actions = [bundles.OrchardAction(
        0, built["cv"], rho, built["cv"], built["cmx"], built["epk"],
        built["enc_ciphertext"], built["out_ciphertext"])]
    found = bundles.scan_actions(holder, ivks=[fvk.ivk], ovks=[fvk.ovk])
    assert [(f["direction"], f["note"].value) for f in found] == \
        [("incoming", 250_000)]


# ── The wallet layer ────────────────────────────────────────────────────────

MNEMONIC = ("abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about")


def test_unified_address_advertises_the_orchard_receiver():
    derived = shielded.derive_address(MNEMONIC)
    assert derived["unified_receivers"][0] == "orchard"
    typecodes = [tc for tc, _ in
                 sapling.decode_unified_address(derived["unified_address"])]
    assert orchard.TYPECODE_ORCHARD in typecodes
    assert sapling.TYPECODE_SAPLING in typecodes


def test_the_orchard_receiver_belongs_to_this_seed():
    derived = shielded.derive_address(MNEMONIC)
    receiver = orchard.orchard_receiver_of(derived["unified_address"])
    fvk = shielded.orchard_account_key(MNEMONIC).fvk()
    assert receiver.raw == fvk.address(derived["diversifier_index"]).raw


def test_the_orchard_receiver_can_be_left_out():
    derived = shielded.derive_address(MNEMONIC, orchard=False)
    assert derived["unified_receivers"] == ["sapling"]
    assert derived["orchard_receiver"] is None


def test_exported_unified_keys_scan_both_pools():
    exported = shielded.export_keys(MNEMONIC)
    keyset = shielded.keys_from_viewing_key(
        exported["unified_full_viewing_key"])
    assert keyset["fvk"] is not None
    assert keyset["orchard_fvk"] is not None
    assert keyset["orchard_fvk"].ivk.to_bytes(32, "little").hex() == \
        exported["orchard"]["incoming_viewing_key"]
    assert keyset["orchard_fvk"].address(0).encode() == \
        exported["orchard"]["default_address"]


def test_a_unified_incoming_key_carries_both_pools():
    exported = shielded.export_keys(MNEMONIC)
    items = orchard.decode_unified_ivk(
        exported["unified_incoming_viewing_key"])
    assert items["orchard"] is not None
    assert items["sapling"] is not None


def test_orchard_notes_are_marked_spent_without_a_node():
    fvk = shielded.orchard_account_key(MNEMONIC).fvk()
    rho = (5).to_bytes(32, "little")
    built = orchard.encrypt_note(fvk.address(0), 700_000, rho)
    action = bundles.OrchardAction(
        0, built["cv"], rho, built["cv"], built["cmx"], built["epk"],
        built["enc_ciphertext"], built["out_ciphertext"])
    received = shielded.scan_action_list([action], fvk, txid="a", height=1)
    nullifier = received["notes"][0]["nullifier"]

    # Nothing spends it: unspent, and said so with no tree state at all.
    unspent = shielded.summarize([received], pools=("orchard",))
    assert unspent["notes"][0]["spent"] is False
    assert unspent["unspent_zatoshi"] == 700_000
    assert unspent["spend_detection"].startswith("nullifiers")

    # A later transaction publishes that nullifier: spent.
    spend = {"txid": "b", "height": 2, "notes": [],
             "orchard_nullifiers": [nullifier]}
    after = shielded.summarize([received, spend], pools=("orchard",))
    assert after["notes"][0]["spent"] is True
    assert after["unspent_zatoshi"] == 0


def test_sapling_notes_stay_unknown_while_orchard_notes_do_not():
    fvk = shielded.orchard_account_key(MNEMONIC).fvk()
    rho = (11).to_bytes(32, "little")
    built = orchard.encrypt_note(fvk.address(0), 1_000, rho)
    action = bundles.OrchardAction(
        0, built["cv"], rho, built["cv"], built["cmx"], built["epk"],
        built["enc_ciphertext"], built["out_ciphertext"])
    scan = shielded.scan_action_list([action], fvk, txid="a", height=1)
    scan["notes"].append({"pool": "sapling", "direction": "incoming",
                          "value_zatoshi": 2_000, "nullifier": None})
    out = shielded.summarize([scan], pools=("sapling", "orchard"))
    by_pool = {n["pool"]: n["spent"] for n in out["notes"]}
    assert by_pool["orchard"] is False
    assert by_pool["sapling"] is None
    # Only what can be checked is counted as unspent.
    assert out["unspent_zatoshi"] == 1_000
    assert out["spend_detection"] == "orchard only"


def test_capabilities_and_addresses_agree_about_orchard(tmp_path, monkeypatch):
    """The claim and the address have to move together.

    A module that hands out an Orchard receiver while capabilities() says it
    cannot read Orchard is telling a sender to put money somewhere it will
    never be found again. Whichever way a future change moves, these two have
    to move together -- that is what this test is for.
    """
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "zcash_mod_under_test", os.path.join(_ROOT, "zcash", "mod.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mod = module.Mod()

    created = mod.wallet_create("orchard-capability-check", "pw")
    receivers = [tc for tc, _ in
                 sapling.decode_unified_address(created["unified_address"])]
    advertises_orchard = orchard.TYPECODE_ORCHARD in receivers
    claims_orchard = mod.capabilities()["shielded_orchard"]["read"]
    assert advertises_orchard == claims_orchard
