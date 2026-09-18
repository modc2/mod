"""sui tests.

Two halves. The offline half pins the things that must be exactly right or a
transfer is rejected — or, worse, accepted and wrong: the BCS byte layout, the
address derivation, the bech32 codec, the two different digests. None of it
touches the network, so `SUI_OFFLINE=1 python3 -m pytest` runs anywhere.

The live half checks the same rules against mainnet itself: real signatures are
re-verified, real transactions are re-hashed, and every shape of transfer this
module can build is dry-run against a node to prove the node accepts it.
"""

import base64
import json
import os
import sys
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bcs                                                    # noqa: E402
import keys as K                                              # noqa: E402
from keys import SuiError                                     # noqa: E402

OFFLINE = os.environ.get('SUI_OFFLINE')
live = pytest.mark.skipif(bool(OFFLINE), reason='SUI_OFFLINE is set')
RPC = os.environ.get('SUI_RPC', 'https://sui-rpc.publicnode.com')


def rpc(method, params=None):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params or []}).encode()
    req = urllib.request.Request(RPC, data=body, headers={
        'content-type': 'application/json', 'user-agent': 'mod-sui/tests'})
    with urllib.request.urlopen(req, timeout=40) as r:
        answer = json.loads(r.read())
    if answer.get('error'):
        raise AssertionError(answer['error'])
    return answer['result']


# ── bech32, against the BIP-173 vectors ──────────────────────────

@pytest.mark.parametrize('vector,hrp', [
    ('A12UEL5L', 'a'),
    ('a12uel5l', 'a'),
    ('abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw', 'abcdef'),
    ('split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w', 'split'),
])
def test_bech32_known_vectors(vector, hrp):
    assert K.bech32_decode(vector)[0] == hrp


def test_bech32_rejects_a_bad_checksum():
    with pytest.raises(SuiError):
        K.bech32_decode('A1G7SGD8')            # BIP-173's invalid-checksum case


def test_bech32_rejects_mixed_case():
    with pytest.raises(SuiError):
        K.bech32_decode('A12UeL5L')


def test_suiprivkey_roundtrips():
    seed = bytes(range(32))
    encoded = K.to_suiprivkey(seed)
    assert encoded.startswith('suiprivkey1')
    assert K.parse_secret(encoded) == seed


def test_a_secp256k1_key_is_refused_rather_than_misread():
    """flag||seed with a non-ed25519 flag must not be silently read as ed25519 —
    that would derive an address the caller does not control."""
    payload = base64.b64encode(bytes([K.FLAG_SECP256K1]) + bytes(32)).decode()
    with pytest.raises(SuiError, match='secp256k1'):
        K.parse_secret(payload)


def test_secret_accepts_the_keystore_shapes():
    seed = os.urandom(32)
    flagged = base64.b64encode(bytes([0]) + seed).decode()
    assert K.parse_secret(flagged) == seed             # sui.keystore entry
    assert K.parse_secret([flagged]) == seed           # a whole sui.keystore file
    assert K.parse_secret(seed.hex()) == seed
    assert K.parse_secret(K.to_suiprivkey(seed)) == seed


# ── addresses ────────────────────────────────────────────────────

def test_address_derivation_is_the_hash_not_the_key():
    seed = bytes(range(32))
    pubkey = K.pubkey_of(seed)
    assert K.address_of(pubkey) != '0x' + pubkey.hex()
    assert len(K.address_of(pubkey)) == 66


def test_normalize_pads_short_forms():
    assert K.normalize('0x2').endswith('02')
    assert len(K.normalize('0x2')) == 66
    assert K.normalize('0x2') == K.normalize(K.normalize('0x2'))
    assert K.normalize('2') == K.normalize('0x2')


def test_normalize_rejects_non_hex():
    for bad in ('0xzz', '', 'bob.sui', '0x' + 'f' * 65):
        with pytest.raises(SuiError):
            K.normalize(bad)


def test_a_digest_is_never_confused_with_an_address():
    assert K.is_digest('AwHKK3DZ32ZuiC254YrpLfgyxx9F8RQP8zFA8RDqruzU')
    assert not K.is_digest('0x2')
    assert not K.is_address('AwHKK3DZ32ZuiC254YrpLfgyxx9F8RQP8zFA8RDqruzU')


def test_base58_keeps_leading_zero_bytes():
    raw = b'\x00\x00' + os.urandom(30)
    assert K.b58decode(K.b58encode(raw)) == raw


# ── ed25519 ──────────────────────────────────────────────────────

def test_pure_python_ed25519_matches_rfc8032():
    """RFC 8032 test vector 1. The pure path is the fallback when no crypto
    library is installed, and a wrong signature there is a silent loss."""
    seed = bytes.fromhex(
        '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
    assert K._pure_pubkey(seed).hex() == (
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    assert K._pure_sign(seed, b'').hex() == (
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
        '5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')


def test_the_fast_backend_agrees_with_the_reference_one():
    seed, message = os.urandom(32), b'sui'
    assert K.pubkey_of(seed) == K._pure_pubkey(seed)
    assert K.sign(seed, message) == K._pure_sign(seed, message)


# ── BCS ──────────────────────────────────────────────────────────

def test_uleb128():
    assert bcs.uleb128(0) == b'\x00'
    assert bcs.uleb128(127) == b'\x7f'
    assert bcs.uleb128(128) == b'\x80\x01'
    assert bcs.uleb128(300) == b'\xac\x02'


def test_an_address_has_no_length_prefix_but_a_digest_does():
    """These two fixed 32-byte fields sit next to each other in an ObjectRef and
    are encoded differently. Swapping them shifts every following byte."""
    address = bcs.address('0x2')
    digest = bcs.object_digest(K.b58encode(bytes(range(32))))
    assert len(address) == 32 and address[-1] == 2
    assert len(digest) == 33 and digest[0] == 0x20


def test_pure_arguments_are_wrapped_in_a_byte_vector():
    assert bcs.pure(bcs.u64(1)) == b'\x00' + b'\x08' + bcs.u64(1)


def test_u64_refuses_to_silently_truncate():
    with pytest.raises(SuiError):
        bcs.u64(2 ** 64)
    with pytest.raises(SuiError):
        bcs.u64(-1)


def test_transaction_data_layout():
    """The whole envelope, byte for byte: version, kind, sender, gas, expiry."""
    sender = '0x' + '11' * 32
    tx = bcs.transaction_data(
        sender, [bcs.pure(bcs.u64(7))], [bcs.transfer_objects([bcs.GAS_COIN],
                                                              bcs.Input(0))],
        [('0x' + '22' * 32, 5, K.b58encode(bytes(32)))], 1000, 2000)
    assert tx[0] == 0                       # TransactionData::V1
    assert tx[1] == 0                       # TransactionKind::ProgrammableTransaction
    assert tx.endswith(bcs.u64(1000) + bcs.u64(2000) + b'\x00')   # price, budget, no expiry
    assert bytes.fromhex('11' * 32) in tx and bytes.fromhex('22' * 32) in tx


def test_the_two_digests_are_different_functions():
    """The signing hash covers the intent prefix; the transaction digest covers
    a literal "TransactionData::" instead. Using one for the other produces a
    valid-looking hash that matches nothing."""
    tx = b'\x00' * 40
    assert bcs.signing_digest(tx) != K.b58decode(bcs.digest_of(tx))
    assert bcs.signing_digest(tx) == K.blake2b(b'\x00\x00\x00' + tx)


def test_synthetic_address_balance_coins_are_not_spendable():
    """Sui reports address-accumulator balances through suix_getCoins with a
    synthetic digest. Passing one as an input fails the whole transaction."""
    synthetic = {'digest': K.b58encode(bytes(16) + b'\xac' * 16), 'balance': '100',
                 'coinObjectId': '0x1', 'version': '1'}
    real = {'digest': K.b58encode(os.urandom(32)), 'balance': '50',
            'coinObjectId': '0x2', 'version': '1'}
    assert bcs.is_synthetic(synthetic) and not bcs.is_synthetic(real)
    assert bcs.spendable([synthetic, real]) == [real]


def test_spendable_sorts_biggest_first():
    coins = [{'digest': K.b58encode(os.urandom(32)), 'balance': str(b)}
             for b in (5, 100, 20)]
    assert [int(c['balance']) for c in bcs.spendable(coins)] == [100, 20, 5]


# ── live: the chain agrees with all of the above ─────────────────

@live
def test_real_signatures_verify_against_our_derivation():
    """Pull signed transactions off mainnet and re-derive everything: the
    sender's address from the public key in its own signature, the transaction
    digest from the raw bytes, and the signature itself over the signing hash.
    If any rule here were wrong, none of these would match."""
    rows = rpc('suix_queryTransactionBlocks',
               [{'options': {'showRawInput': True, 'showInput': True}}, None, 12, True])
    checked = 0
    for row in rows['data']:
        sender = row['transaction']['data']['sender']
        # System transactions (consensus prologue, checkpoints) are unsigned and
        # sent by 0x0. Their trailing bytes can coincidentally look like a
        # signature, so they are excluded by sender rather than by layout.
        if K.normalize(sender) == K.normalize('0x0'):
            continue
        raw = base64.b64decode(row['rawTransaction'])
        if raw[0] != 1 or raw[-99] != 1 or raw[-98] != 0x61 or raw[-97] != 0:
            continue                        # multisig or zkLogin
        signature, pubkey = raw[-96:-32], raw[-32:]
        tx_bytes = raw[1:-99][3:]
        assert K.address_of(pubkey) == K.normalize(sender)
        assert bcs.digest_of(tx_bytes) == row['digest']
        try:
            from nacl.signing import VerifyKey
            VerifyKey(pubkey).verify(bcs.signing_digest(tx_bytes), signature)
        except ImportError:
            pass
        checked += 1
    assert checked >= 1, 'no ed25519 transactions in the sample'


@live
@pytest.mark.parametrize('shape', ['sui', 'coin_split', 'coin_merge', 'coin_whole'])
def test_the_node_accepts_every_transfer_shape_we_build(shape):
    """Build a real transfer for a funded mainnet address and dry-run it. The
    node deserializes the BCS strictly, so a success here means the byte layout
    is right — not merely plausible."""
    from bcs import build_transfer, spendable
    gas_price = int(rpc('suix_getReferenceGasPrice'))
    recipient = '0x' + '0f' * 32
    rows = rpc('suix_queryTransactionBlocks',
               [{'options': {'showBalanceChanges': True}}, None, 50, True])
    holders = {}
    for tx in rows['data']:
        for change in (tx.get('balanceChanges') or []):
            owner = change.get('owner') or {}
            if isinstance(owner, dict) and owner.get('AddressOwner'):
                holders.setdefault(change['coinType'], []).append(owner['AddressOwner'])

    def coins_of(address, coin_type):
        return spendable(rpc('suix_getCoins', [address, coin_type, None, 20])['data'])

    for coin_type, addresses in holders.items():
        want_sui = shape == 'sui'
        if want_sui != (coin_type == bcs.SUI_TYPE):
            continue
        for address in addresses[:10]:
            gas = coins_of(address, bcs.SUI_TYPE)
            if not gas or sum(int(c['balance']) for c in gas) < 60_000_000:
                continue
            if shape == 'sui':
                tx = build_transfer(address, recipient, 1_000_000, gas[:2],
                                    gas_price, 50_000_000)
            else:
                coins = coins_of(address, coin_type)
                if not coins or sum(int(c['balance']) for c in coins) < 2:
                    continue
                if shape == 'coin_merge' and len(coins) < 2:
                    continue
                take = coins[:3] if shape == 'coin_merge' else coins[:1]
                held = sum(int(c['balance']) for c in take)
                amount = held if shape == 'coin_whole' else max(1, held // 2)
                tx = build_transfer(address, recipient, amount, gas[:2], gas_price,
                                    50_000_000, coins=take)
            effects = rpc('sui_dryRunTransactionBlock',
                          [base64.b64encode(tx).decode()])['effects']
            assert effects['status']['status'] == 'success', effects['status']
            return
    pytest.skip(f'no funded mainnet address in the sample for {shape}')


@live
def test_what_identifies_the_framework_package():
    from chain import Client
    answer = Client().what('0x2')
    assert answer['kind'] == 'package'
    assert answer['known_as'] == 'Sui framework'


@live
def test_what_says_unused_rather_than_guessing():
    from chain import Client
    answer = Client().what('0x' + 'ab' * 32)
    assert answer['kind'] == 'unused'


@live
def test_a_coin_with_no_market_is_null_and_not_zero():
    """The invariant, not the upstream's uptime: an unknown price is null and
    says so. Reading a missing price as zero is how a portfolio silently loses
    a position, so that must never happen — including when the price feed is
    throttling us, which it does."""
    from chain import Client
    c = Client()
    answer = c.price('SUI')
    priced = answer['prices'][0]
    if priced['priced']:
        assert priced['usd'] > 0
    else:
        assert priced['usd'] is None
        assert answer.get('warnings'), 'an unpriced coin must explain itself'

    unknown = Client().prices([bcs.SUI_TYPE.replace('0x2', '0x' + 'cd' * 32)])
    assert unknown == {}, 'a coin with no market must be absent, not zero'


@live
def test_transfer_refuses_an_amount_the_sender_does_not_have():
    """The failure has to arrive before anything is signed."""
    from chain import Client
    seed = os.urandom(32)
    with pytest.raises(SuiError) as failure:
        Client().transfer('0x' + '0f' * 32, 1000,
                          secret=K.to_suiprivkey(seed))
    assert 'not enough' in str(failure.value).lower()


@live
def test_the_rpc_pool_reports_which_endpoint_answered():
    from chain import Client
    assert Client().status()['rpc'].startswith('http')
