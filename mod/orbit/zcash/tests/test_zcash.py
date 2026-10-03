"""
Tests for the zcash module.

The important ones are the consensus tests: they check our ZIP-244 signature
digest and ZIP-225 serialization against a real mainnet transaction, so a
regression cannot silently produce transactions the network would reject.

Network-dependent tests are marked `live` and skipped without connectivity.
    pytest tests/ -m "not live"     # offline only
"""

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("ZCASH_WALLET_DIR", tempfile.mkdtemp())

from zcash import bridge, chain, evm, keys, tx, wallet  # noqa: E402

# A transparent-only v5 transaction mined on mainnet, used as a consensus
# fixture: 1 P2PKH input, 2 P2PKH outputs, no shielded bundles.
MAINNET_TXID = "d72925fd4f7dc90524cedac6d1af88ea5eb3ae8bb4f2cc263efca00dc2e37d81"

live = pytest.mark.live

# A real Sapling address (derived from a fixed seed), for the tests that check
# what happens when you try to pay one.
from zcash import sapling as _sapling  # noqa: E402

_Z_ADDRESS = _sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).address(0).encode()


# ── Base58 / addresses ──────────────────────────────────────────────────────

def test_address_roundtrip_matches_mainnet_script():
    """A real mainnet scriptPubKey must encode to the address Blockchair shows."""
    spk = bytes.fromhex("76a91429769863854a3313763ae790153f05707f18905588ac")
    addr = keys.b58check_encode(keys.P2PKH_PREFIX + spk[3:23])
    assert addr == "t1MeqnLvdRWtSho6CaqJQQHMEToqitpJFU9"
    assert keys.decode_address(addr)["script_pubkey"] == spk


def test_reject_corrupt_address():
    with pytest.raises(ValueError):
        keys.decode_address("t1MeqnLvdRWtSho6CaqJQQHMEToqitpJFU8")
    assert not keys.is_valid_address("nonsense")


def test_shielded_addresses_parse_but_are_not_spendable():
    """Real shielded addresses only -- a lookalike must be rejected outright."""
    from zcash import sapling
    fvk = sapling.ExtendedSpendingKey.from_seed(bytes(range(32))).fvk()
    z_addr = fvk.address(0).encode()
    ua = fvk.address(0).unified()
    for addr, kind in [(z_addr, "sapling"), (ua, "unified")]:
        info = keys.decode_address(addr)
        assert info["type"] == kind
        assert info["spendable"] is False
    with pytest.raises(ValueError, match="Groth16"):
        keys.address_to_script(z_addr)
    for lookalike in ("zs1abcdef", "u1abcdef"):
        assert not keys.is_valid_address(lookalike)


# ── BIP39 / BIP32 official vectors ──────────────────────────────────────────

def test_bip39_trezor_vector():
    m = keys.entropy_to_mnemonic(bytes(16))
    assert m.startswith("abandon abandon") and m.endswith("about")
    assert keys.mnemonic_to_seed(m, "TREZOR").hex() == (
        "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553"
        "1f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04")


def test_bip39_rejects_bad_checksum():
    assert not keys.validate_mnemonic("abandon " * 11 + "abandon")


def test_bip32_official_vector_1():
    master = keys.HDKey.from_seed(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    assert master.priv.hex() == \
        "e8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35"
    # hardened and non-hardened derivation
    assert master.derive_path("m/0'/1/2'/2/1000000000").priv.hex() == \
        "471b76e389e528d6de6d816857e012c5455051cad6660850e58372a6c3e6e7c8"


def test_wif_roundtrip():
    hd = keys.derive_account(keys.mnemonic_to_seed(keys.generate_mnemonic()), 0, 0, 0)
    priv, compressed = keys.wif_to_privkey(hd.wif())
    assert priv == hd.priv and compressed


def test_signing_is_deterministic():
    priv = keys.HDKey.from_seed(b"determinism").priv
    digest = keys.sha256d(b"zcash")
    assert keys.sign_digest(priv, digest) == keys.sign_digest(priv, digest)


# ── Keccak / EIP-55 ─────────────────────────────────────────────────────────

def test_keccak_and_eip55():
    assert evm.keccak256(b"").hex() == \
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    checksummed = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert evm.to_checksum_address(checksummed.lower()) == checksummed
    assert evm.is_valid_evm_address(checksummed)
    # flipping the last character's case breaks the checksum
    assert not evm.is_valid_evm_address(checksummed[:-1] + "D")


# ── Transaction construction ────────────────────────────────────────────────

def _synthetic_wallet_tx(branch_id=0x37A5165B, n_out=1):
    priv = keys.HDKey.from_seed(b"tx-fixture").priv
    addr = keys.pubkey_to_address(keys.privkey_to_pubkey(priv))
    utxos = [{"txid": "ab" * 32, "vout": 0, "value": 10 * 10**8,
              "script_pubkey": keys.address_to_script(addr).hex()}]
    outputs = [(addr, 10**8)] * n_out
    transaction, meta = tx.build_transaction(utxos, outputs, addr, branch_id, 1_000_000)
    return priv, addr, transaction, meta


def test_sign_and_verify_own_transaction():
    priv, _, transaction, _ = _synthetic_wallet_tx()
    transaction.sign_input(0, priv)
    assert transaction.verify_input(0)


def test_serialization_round_trips():
    priv, _, transaction, _ = _synthetic_wallet_tx()
    transaction.sign_input(0, priv)
    reparsed = tx.parse_v5(bytes.fromhex(transaction.hex()))
    assert reparsed.serialize() == transaction.serialize()
    assert reparsed.txid() == transaction.txid()


def test_signing_with_the_wrong_key_is_refused():
    _, _, transaction, _ = _synthetic_wallet_tx()
    with pytest.raises(ValueError, match="does not control"):
        transaction.sign_input(0, keys.HDKey.from_seed(b"someone-else").priv)


def test_zip317_fee():
    assert tx.conventional_fee(1, 2) == 10_000     # grace floor
    assert tx.conventional_fee(1, 1) == 10_000
    assert tx.conventional_fee(5, 3) == 25_000     # 5 logical actions


def test_change_below_dust_is_folded_into_the_fee():
    addr = keys.pubkey_to_address(
        keys.privkey_to_pubkey(keys.HDKey.from_seed(b"dust").priv))
    spk = keys.address_to_script(addr).hex()
    # leave less than DUST_THRESHOLD over after amount + fee
    value = 10**8 + tx.conventional_fee(1, 1) + 100
    utxos = [{"txid": "cd" * 32, "vout": 0, "value": value, "script_pubkey": spk}]
    transaction, meta = tx.build_transaction(
        utxos, [(addr, 10**8)], addr, 0x37A5165B, 1_000)
    assert meta["change_zatoshi"] == 0
    assert len(transaction.vout) == 1
    assert transaction.fee == meta["fee_zatoshi"]


def test_insufficient_funds_reports_the_shortfall():
    addr = keys.pubkey_to_address(
        keys.privkey_to_pubkey(keys.HDKey.from_seed(b"poor").priv))
    utxos = [{"txid": "ef" * 32, "vout": 0, "value": 1000,
              "script_pubkey": keys.address_to_script(addr).hex()}]
    with pytest.raises(ValueError, match="insufficient funds"):
        tx.build_transaction(utxos, [(addr, 10**8)], addr, 0x37A5165B, 1_000)


def test_cannot_build_an_output_to_a_shielded_address():
    addr = keys.pubkey_to_address(
        keys.privkey_to_pubkey(keys.HDKey.from_seed(b"shield").priv))
    utxos = [{"txid": "11" * 32, "vout": 0, "value": 10**9,
              "script_pubkey": keys.address_to_script(addr).hex()}]
    with pytest.raises(ValueError):
        tx.build_transaction(utxos, [(_Z_ADDRESS, 1000)], addr, 0x37A5165B, 1_000)


# ── Wallet ──────────────────────────────────────────────────────────────────

def test_wallet_lifecycle_and_encryption(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    created = wallet.create("w", "pw", addresses=2)
    assert len(created["mnemonic"].split()) == 24
    assert len(created["addresses"]) == 2

    # the mnemonic must not be readable without the password
    raw = (tmp_path / "w.json").read_text()
    assert created["mnemonic"] not in raw
    with pytest.raises(wallet.WalletError, match="wrong password"):
        wallet.reveal("w", "not-the-password")
    assert wallet.reveal("w", "pw")["mnemonic"] == created["mnemonic"]


def test_wallet_restore_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    a = wallet.create("a", "pw")
    b = wallet.create("b", "pw2", mnemonic=a["mnemonic"])
    assert a["addresses"][0] == b["addresses"][0]
    assert b["mnemonic"] is None      # never echoed back on restore


def test_private_keys_cover_every_address(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    wallet.create("w", "pw", addresses=3)
    wallet.import_key("w", "pw", keys.privkey_to_wif(os.urandom(32)))
    derived = wallet.private_keys("w", "pw")
    assert set(derived) == {e["address"] for e in wallet.addresses("w")}


def test_duplicate_wallet_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    wallet.create("w", "pw")
    with pytest.raises(wallet.WalletError, match="already exists"):
        wallet.create("w", "pw")


def test_invalid_mnemonic_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path))
    with pytest.raises(wallet.WalletError, match="invalid BIP39"):
        wallet.create("w", "pw", mnemonic="not actually a mnemonic at all")


# ── Bridge argument handling (offline) ──────────────────────────────────────

def test_base_units_do_not_use_floats():
    assert bridge.to_base_units("12.3456789", 6) == "12345678"   # truncates, no rounding
    assert bridge.to_base_units(1.5, 8) == "150000000"


# ── Live network tests ──────────────────────────────────────────────────────

@live
def test_mainnet_transaction_reproduces_txid_and_signature():
    """The consensus test: our ZIP-244 digests must match a mined transaction."""
    import requests
    c = chain.Chain()
    raw = bytes.fromhex(c.raw_transaction(MAINNET_TXID))
    parsed = tx.parse_v5(raw)

    assert parsed.serialize() == raw, "serialization is not byte-exact"
    assert parsed.txid() == MAINNET_TXID, "ZIP-244 txid mismatch"

    # fill in each prevout, then verify the real signature against our sighash
    for txin in parsed.vin:
        body = requests.get(
            f"https://api.blockchair.com/zcash/raw/transaction/{txin.txid}",
            timeout=30).json()["data"][txin.txid]["decoded_raw_transaction"]
        out = body["vout"][txin.vout]
        txin.value = out["valueSat"]
        txin.script_pubkey = bytes.fromhex(out["scriptPubKey"]["hex"])
    assert parsed.verify_input(0), "ZIP-244 signature digest mismatch"


@live
def test_consensus_branch_id_is_discovered_from_chain():
    c = chain.Chain()
    assert c.consensus_branch_id() > 0
    assert c.tip_height() > 3_000_000


@live
def test_utxo_fetch_uses_the_paired_limit():
    """A bare limit=0 would silently return an empty utxo set."""
    c = chain.Chain()
    funded = "t1VtnnhTYhmADh7L2uKU3Sev7GscBHT6HfE"   # Maya inbound, always funded
    balance = c.balance(funded)
    if balance["balance_zatoshi"] > 0:
        assert c.utxos(funded), "address has a balance but reported no utxos"


@live
def test_bridge_quotes_zec_to_ethereum():
    q = bridge.quote("ZEC", "ETH", 1,
                     "0x742d35cc6634c0532925a3b844bc454e4438f44e",
                     "t1MeqnLvdRWtSho6CaqJQQHMEToqitpJFU9", dry=True)
    assert float(q["amount_out"]) > 0
    assert q["deposit_address"] is None      # dry quotes reserve nothing


@live
def test_bridge_rejects_a_mistyped_evm_recipient():
    with pytest.raises(bridge.BridgeError, match="EIP-55|not a valid"):
        bridge.quote("ZEC", "ETH", 1,
                     "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD",
                     "t1MeqnLvdRWtSho6CaqJQQHMEToqitpJFU9", dry=True)
