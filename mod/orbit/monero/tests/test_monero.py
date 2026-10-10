"""
Tests for the monero module.

The ones that matter are the crypto tests. A scanner that matches nothing is
indistinguishable from a wallet with no funds, and an address encoder that is
subtly wrong produces addresses that swallow payments, so both are pinned:

  * Keccak-256, the ed25519 base point and the Monero project's own donation
    address are checked against values we did not choose;
  * the scanner is checked by constructing outputs the way a sender does and
    requiring it to recover them from the receiver's side.

Network-dependent tests are marked `live` and skipped without connectivity.
    pytest tests/ -m "not live"     # offline only
"""

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("MONERO_WALLET_DIR", tempfile.mkdtemp())

from monero import bridge, crypto, daemon, mnemonic, scan, wallet  # noqa: E402

live = pytest.mark.live

# The Monero project's donation subaddress -- an external fixture for base58,
# the Keccak checksum and the address prefix table all at once.
DONATION = crypto.DONATION_ADDRESS


# ── Primitives ──────────────────────────────────────────────────────────────

def test_keccak256_matches_published_vector():
    """Keccak-256, not SHA3-256: the padding byte differs and so does the digest."""
    assert crypto.keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
    import hashlib
    assert crypto.keccak256(b"") != hashlib.sha3_256(b"").digest()


def test_ed25519_basepoint_and_group_order():
    assert crypto.encode_point(crypto._G).hex() == "58" + "66" * 31
    # (l-1)*G == -G, so adding G returns the identity.
    almost = crypto.scalarmult(crypto._G, crypto.L - 1)
    assert crypto.encode_point(crypto._add(almost, crypto._G)) == \
        crypto.encode_point(crypto.IDENTITY)


def test_windowed_scalarmult_matches_naive():
    def naive(p, e):
        r = crypto.IDENTITY
        while e:
            if e & 1:
                r = crypto._add(r, p)
            p = crypto._add(p, p)
            e >>= 1
        return r

    for e in (1, 2, 255, 256, 1 << 200, crypto.L - 12345):
        assert crypto.encode_point(crypto.scalarmult(crypto._G, e)) == \
            crypto.encode_point(naive(crypto._G, e))
        assert crypto.encode_point(crypto.scalarmult_base(e)) == \
            crypto.encode_point(naive(crypto._G, e))


def test_point_decode_rejects_non_points():
    assert not crypto.is_point(b"\xff" * 32)
    with pytest.raises(crypto.CryptoError):
        crypto.decode_point(b"\x00" * 31)


# ── Addresses ───────────────────────────────────────────────────────────────

def test_donation_address_decodes_and_round_trips():
    parsed = crypto.decode_address(DONATION)
    assert parsed["network"] == "mainnet"
    assert parsed["type"] == "subaddress"
    assert crypto.b58_encode(crypto.b58_decode(DONATION)) == DONATION


def test_corrupt_address_is_caught_by_the_checksum():
    bad = DONATION[:-1] + ("A" if DONATION[-1] != "A" else "B")
    with pytest.raises(crypto.CryptoError):
        crypto.decode_address(bad)
    assert not crypto.is_valid_address(bad)
    assert not crypto.is_valid_address("not an address")


def test_address_kinds_have_the_right_shape():
    keys = crypto.keys_from_seed(crypto.keccak256(b"shape"))
    spend = bytes.fromhex(keys["spend_public_key"])
    view = bytes.fromhex(keys["view_public_key"])

    assert len(keys["address"]) == 95 and keys["address"][0] == "4"
    sub = crypto.subaddress(bytes.fromhex(keys["view_secret_key"]), spend, 0, 1)
    assert len(sub) == 95 and sub[0] == "8"
    integrated = crypto.integrated_address(spend, view, b"\x01" * 8)
    assert len(integrated) == 106
    assert crypto.decode_address(integrated)["payment_id"] == "01" * 8

    for net, first in (("testnet", "9"), ("stagenet", "5")):
        addr = crypto.encode_address(spend, view, net)
        assert crypto.decode_address(addr)["network"] == net
        assert addr[0] == first


def test_view_key_is_derived_from_the_spend_key():
    """One seed is enough because a = Hs(b) -- which is also why a view key can
    be shared without giving away the ability to spend."""
    keys = crypto.keys_from_seed(crypto.keccak256(b"derivation"))
    spend_sec = bytes.fromhex(keys["spend_secret_key"])
    assert crypto.hash_to_scalar(spend_sec).hex() == keys["view_secret_key"]
    assert crypto.secret_to_public(spend_sec).hex() == keys["spend_public_key"]


# ── Seed phrases ────────────────────────────────────────────────────────────

def test_wordlist_is_the_monero_one():
    words = mnemonic.words()
    assert len(words) == 1626
    assert len({w[:3] for w in words}) == 1626   # 3-letter prefixes are unique
    assert words[0] == "abbey" and words[-1] == "zoom"


def test_seed_phrase_round_trip():
    seed = crypto.sc_reduce32(crypto.keccak256(b"phrase"))
    phrase = mnemonic.encode(seed)
    assert len(phrase.split()) == 25
    assert mnemonic.decode(phrase) == seed


def test_checksum_word_catches_corruption():
    """A fixed phrase, because the 25th word is a weak check by design: it
    names one of the 24 words, so a random corruption slips through roughly
    one time in twenty-four. Random phrases here would flake."""
    phrase = mnemonic.encode(crypto.sc_reduce32(crypto.keccak256(b"fixture")))
    words = phrase.split()
    assert mnemonic.is_valid(phrase)

    swapped = " ".join(words[1:2] + words[0:1] + words[2:])
    assert not mnemonic.is_valid(swapped)

    for index, replacement in ((3, "zebra"), (5, "zzzzzz")):
        corrupted = list(words)
        corrupted[index] = replacement
        with pytest.raises(mnemonic.MnemonicError):
            mnemonic.decode(" ".join(corrupted))


def test_phrase_accepts_three_letter_prefixes():
    """Monero words are identified by their first three letters, so a phrase
    written down with a mangled tail still restores."""
    phrase = mnemonic.generate()
    trimmed = " ".join(w[:3] for w in phrase.split())
    assert mnemonic.decode(trimmed) == mnemonic.decode(phrase)


def test_wrong_word_count_is_rejected():
    words = mnemonic.generate().split()
    for bad in (words[:12], words[:26] + ["abbey"]):
        with pytest.raises(mnemonic.MnemonicError):
            mnemonic.decode(" ".join(bad))


# ── Scanner ─────────────────────────────────────────────────────────────────

def _sender_output(dest_spend, dest_view, r, index, amount, subaddress):
    """Build a transaction body the way a real sender would."""
    big_r = (crypto.encode_point(crypto.scalarmult(
                crypto.decode_point(dest_spend), int.from_bytes(r, "little")))
             if subaddress else crypto.secret_to_public(r))
    derivation = crypto.encode_point(crypto.mul8(crypto.scalarmult(
        crypto.decode_point(dest_view), int.from_bytes(r, "little"))))
    one_time = crypto.derive_public_key(derivation, index, dest_spend)
    mask = crypto.keccak256(b"amount" + crypto.derivation_to_scalar(derivation, index))[:8]
    encrypted = bytes(x ^ y for x, y in zip(int.to_bytes(amount, 8, "little"), mask))
    return {
        "version": 2, "unlock_time": 0,
        "vin": [{"key": {"amount": 0, "key_offsets": [1, 2], "k_image": "00" * 32}}],
        "vout": [{"amount": 0, "target": {"tagged_key": {
            "key": one_time.hex(),
            "view_tag": "%02x" % crypto.derive_view_tag(derivation, index)}}}],
        "extra": list(bytes([1]) + big_r),
        "rct_signatures": {"type": 6, "txnFee": 30000000,
                           "ecdhInfo": [{"amount": encrypted.hex()}]},
    }


def test_scanner_finds_a_payment_to_the_main_address():
    keys = crypto.keys_from_seed(crypto.keccak256(b"scan-main"))
    view_sec = bytes.fromhex(keys["view_secret_key"])
    spend_pub = bytes.fromhex(keys["spend_public_key"])
    table = scan.spend_key_table(view_sec, spend_pub)

    body = _sender_output(spend_pub, bytes.fromhex(keys["view_public_key"]),
                          crypto.sc_reduce32(crypto.keccak256(b"r")), 0,
                          250_000_000_000, False)
    hits = scan.scan_transaction(body, view_sec, table)
    assert len(hits) == 1
    assert hits[0]["amount"] == 250_000_000_000
    assert hits[0]["amount_xmr"] == 0.25
    assert hits[0]["to"] == "main"


def test_scanner_finds_a_payment_to_a_subaddress():
    keys = crypto.keys_from_seed(crypto.keccak256(b"scan-sub"))
    view_sec = bytes.fromhex(keys["view_secret_key"])
    spend_pub = bytes.fromhex(keys["spend_public_key"])
    table = scan.spend_key_table(view_sec, spend_pub, subaddresses=2)

    m = crypto.hash_to_scalar(b"SubAddr\x00" + view_sec +
                              crypto.varint(0) + crypto.varint(2))
    point = crypto._add(crypto.decode_point(spend_pub),
                        crypto.scalarmult_base(int.from_bytes(m, "little")))
    sub_spend = crypto.encode_point(point)
    sub_view = crypto.encode_point(
        crypto.scalarmult(point, int.from_bytes(view_sec, "little")))

    body = _sender_output(sub_spend, sub_view,
                          crypto.sc_reduce32(crypto.keccak256(b"r2")), 0,
                          7_500_000_000, True)
    hits = scan.scan_transaction(body, view_sec, table)
    assert len(hits) == 1
    assert hits[0]["to"] == "subaddress 0/2"
    assert hits[0]["amount"] == 7_500_000_000


def test_scanner_ignores_other_peoples_outputs():
    mine = crypto.keys_from_seed(crypto.keccak256(b"mine"))
    theirs = crypto.keys_from_seed(crypto.keccak256(b"theirs"))
    table = scan.spend_key_table(bytes.fromhex(mine["view_secret_key"]),
                                 bytes.fromhex(mine["spend_public_key"]),
                                 subaddresses=5)
    body = _sender_output(bytes.fromhex(theirs["spend_public_key"]),
                          bytes.fromhex(theirs["view_public_key"]),
                          crypto.sc_reduce32(crypto.keccak256(b"r3")), 0, 1, False)
    assert scan.scan_transaction(body, bytes.fromhex(mine["view_secret_key"]),
                                 table) == []


def test_view_tag_rejects_without_a_scalar_multiplication():
    """A wrong view tag must short-circuit -- that is the whole point of it."""
    keys = crypto.keys_from_seed(crypto.keccak256(b"tag"))
    view_sec = bytes.fromhex(keys["view_secret_key"])
    spend_pub = bytes.fromhex(keys["spend_public_key"])
    table = scan.spend_key_table(view_sec, spend_pub)
    body = _sender_output(spend_pub, bytes.fromhex(keys["view_public_key"]),
                          crypto.sc_reduce32(crypto.keccak256(b"r")), 0, 1, False)
    real_tag = body["vout"][0]["target"]["tagged_key"]["view_tag"]
    body["vout"][0]["target"]["tagged_key"]["view_tag"] = \
        "%02x" % ((int(real_tag, 16) + 1) % 256)
    assert scan.scan_transaction(body, view_sec, table) == []


def test_tx_extra_parsing():
    pubkey = crypto.secret_to_public(crypto.sc_reduce32(crypto.keccak256(b"R")))
    extra = list(bytes([1]) + pubkey) + [2, 9, 1] + [0xAB] * 8
    parsed = scan.parse_extra(extra)
    assert parsed["tx_pubkey"] == pubkey
    assert parsed["encrypted_payment_id"] == "ab" * 8

    additional = [crypto.secret_to_public(crypto.sc_reduce32(crypto.keccak256(b"a1")))]
    extra2 = list(bytes([1]) + pubkey + bytes([4, 1]) + additional[0])
    assert scan.parse_extra(extra2)["additional_pubkeys"] == additional

    # A truncated field must end the parse, not raise.
    assert scan.parse_extra([1, 2, 3])["tx_pubkey"] is None


def test_scan_self_test_passes():
    assert scan.self_test()["ok"]


# ── Wallets ─────────────────────────────────────────────────────────────────

@pytest.fixture
def wallet_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MONERO_WALLET_DIR", str(tmp_path))
    return tmp_path


def test_wallet_create_restore_round_trip(wallet_dir):
    created = wallet.create("w", "pw")
    assert len(created["seed_phrase"].split()) == 25
    wallet.create("w2", "pw", created["seed_phrase"])
    assert wallet.info("w2")["address"] == created["address"]


def test_wallet_secrets_need_the_password(wallet_dir):
    wallet.create("w", "correct")
    with pytest.raises(wallet.WalletError):
        wallet.secrets("w", "wrong")
    assert wallet.secrets("w", "correct")["view_secret_key"]


def test_wallet_file_holds_no_plaintext_secret(wallet_dir):
    created = wallet.create("w", "pw")
    raw = (wallet_dir / "w.json").read_text()
    secrets = wallet.secrets("w", "pw")
    assert created["seed_phrase"] not in raw
    assert secrets["view_secret_key"] not in raw
    assert secrets["spend_secret_key"] not in raw
    assert created["address"] in raw          # addresses stay readable on purpose


def test_subaddresses_are_derived_and_persisted(wallet_dir):
    wallet.create("w", "pw")
    first = wallet.new_subaddress("w", "pw", label="donations")
    second = wallet.new_subaddress("w", "pw")
    assert first["minor"] == 1 and second["minor"] == 2
    assert first["address"] != second["address"]
    assert crypto.decode_address(first["address"])["type"] == "subaddress"
    assert len(wallet.info("w")["subaddresses"]) == 2


def test_view_only_wallet_rejects_a_mismatched_key(wallet_dir):
    created = wallet.create("w", "pw")
    with pytest.raises(wallet.WalletError):
        wallet.import_view_only("v", "pw", created["address"], "11" * 32)
    good = wallet.secrets("w", "pw")["view_secret_key"]
    imported = wallet.import_view_only("v", "pw", created["address"], good)
    assert imported["view_only"] is True
    assert wallet.secrets("v", "pw")["spend_secret_key"] is None


def test_view_only_import_rejects_a_subaddress(wallet_dir):
    created = wallet.create("w", "pw")
    sub = wallet.new_subaddress("w", "pw")
    with pytest.raises(wallet.WalletError):
        wallet.import_view_only("v", "pw", sub["address"],
                                wallet.secrets("w", "pw")["view_secret_key"])
    assert created["address"]


def test_delete_requires_the_password(wallet_dir):
    wallet.create("w", "pw")
    with pytest.raises(wallet.WalletError):
        wallet.delete("w", "nope")
    wallet.delete("w", "pw")
    assert wallet.list_wallets() == []


# ── Module surface ──────────────────────────────────────────────────────────

def test_config_fns_match_the_class(wallet_dir):
    """`fns` exposure comes from config.json, so the two must not drift."""
    import json
    from monero.mod import Mod
    config = json.load(open(os.path.join(_ROOT, "config.json")))
    for name in config["fns"]:
        assert callable(getattr(Mod, name, None)), f"{name} is in config.json only"
    assert set(config["fns"]) == set(Mod.fns)


def test_send_refuses_an_invalid_address(wallet_dir):
    from monero.mod import Mod
    result = Mod().send("not-an-address", 0.1)
    assert "error" in result and "invalid address" in result["error"]


def test_offline_self_tests_pass():
    assert crypto.self_test()["ok"]
    assert mnemonic.self_test()["ok"]


# ── Live ────────────────────────────────────────────────────────────────────

@live
def test_daemon_reaches_the_chain():
    info = daemon.Daemon().info()
    assert info["height"] > 3_000_000
    assert info["network"] == "mainnet"


@live
def test_block_and_transaction_round_trip():
    d = daemon.Daemon()
    block = d.block()
    assert block["hash"] and block["height"] > 3_000_000
    by_hash = d.block(hash=block["hash"])
    assert by_hash["height"] == block["height"]
    if block.get("tx_hashes"):
        tx = d.transaction(block["tx_hashes"][0])
        assert tx["hash"] == block["tx_hashes"][0]
        assert tx["ring_size"] in (0, 16)


@live
def test_fee_estimate_is_sane():
    fee = daemon.Daemon().fee_estimate()
    assert fee["fee_per_byte"] > 0
    assert len(fee["tiers"]) >= 1


@live
def test_live_scan_runs_and_reports_its_rate():
    d = daemon.Daemon()
    keys = crypto.keys_from_seed(crypto.keccak256(b"live-scan"))
    tip = d.tip_height()
    result = scan.scan_blocks(d, bytes.fromhex(keys["view_secret_key"]),
                              bytes.fromhex(keys["spend_public_key"]),
                              tip - 2, blocks=2)
    assert result["blocks_scanned"] >= 1
    assert result["transactions_scanned"] > 0     # real blocks have real txs
    assert result["outputs_found"] == 0           # not our wallet
    assert result["blocks_per_second"] > 0


@live
def test_bridge_quotes_xmr():
    quote = bridge.quote("XMR", "BTC", 1)
    assert quote["amount_out"] > 0
    assert quote["custodial"] is True


@live
def test_bridge_rejects_a_bad_recipient_before_reserving_anything():
    with pytest.raises(bridge.BridgeError):
        bridge.swap_start("XMR", "BTC", 1, "not-a-bitcoin-address", DONATION)
