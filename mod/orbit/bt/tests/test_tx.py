"""Offline tests for bt.tx — the browser-wallet (SubWallet) signing bridge.

The live check (polkadot-js signs the node's SignerPayloadJSON, the node
verifies, finney accepts the signature) is scripts/signpayload_check.js.
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'
os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-tx-'))

from bt import tx  # noqa: E402

ALICE = '5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY'
ALICE_DOT = '15oF4uVJwmo4TdGW7VfQxNLavjCXviqxT9S1MgbjMNHr6Sp5'   # same key, prefix 0
ALICE_PUB = '0xd43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d'


def test_normalize_any_prefix_to_42():
    assert tx.normalize(ALICE) == ALICE
    assert tx.normalize(ALICE_DOT) == ALICE
    assert tx.normalize(ALICE_PUB) == ALICE
    for bad in ('', '0x1234', 'not-an-address', '0x' + 'ab' * 20):
        with pytest.raises(ValueError):
            tx.normalize(bad)


def test_signer_payload_shape():
    p = tx.signer_payload(address=ALICE, block_hash='0x' + '11' * 32, block_number=9190400,
                          era_hex='0xc501', genesis_hash='0x' + '22' * 32, method_hex='0x0503',
                          nonce=7, spec_version=470, tx_version=1,
                          signed_extensions=['CheckNonce', 'CheckMetadataHash'])
    assert p['blockNumber'] == '0x008c3c00' and p['nonce'] == '0x00000007'
    assert p['specVersion'] == '0x000001d6' and p['transactionVersion'] == '0x00000001'
    assert p['tip'] == '0x' + '0' * 32 and p['version'] == 4 and p['mode'] == 0
    p2 = tx.signer_payload(address=ALICE, block_hash='0x', block_number=1, era_hex='0x00',
                           genesis_hash='0x', method_hex='0x', nonce=0, spec_version=1,
                           tx_version=1, signed_extensions=['CheckNonce'])
    assert 'mode' not in p2


def test_split_signature():
    sig = bytes(range(64))
    assert tx.split_signature('0x' + sig.hex()) == (1, sig)
    assert tx.split_signature('0x01' + sig.hex()) == (1, sig)
    assert tx.split_signature('0x00' + sig.hex()) == (0, sig)
    assert tx.split_signature('0x02' + bytes(65).hex())[0] == 2
    with pytest.raises(ValueError):
        tx.split_signature('0x1234')
    with pytest.raises(ValueError):
        tx.split_signature('zz')


def test_verify_against_the_bytes_the_node_built():
    from bittensor_wallet import Keypair
    kp = Keypair.create_from_uri('//Alice')
    assert kp.ss58_address == ALICE
    payload = b'\x05\x03' + bytes(range(60))
    sig = kp.sign(payload)
    assert tx.verify(ALICE, payload, '0x01' + sig.hex())          # extension shape
    assert tx.verify(ALICE, payload, '0x' + sig.hex())            # bare sr25519
    assert not tx.verify(ALICE, payload + b'x', '0x01' + sig.hex())
    bob = Keypair.create_from_uri('//Bob').ss58_address
    assert not tx.verify(bob, payload, '0x01' + sig.hex())
    with pytest.raises(ValueError):
        tx.verify(ALICE, payload, '0x02' + bytes(65).hex())       # ecdsa refused


def test_limit_price():
    assert tx.limit_price_rao(0.01, 2.0, buying=True) == int(0.0102 * 1e9)
    assert tx.limit_price_rao(0.01, 2.0, buying=False) == int(0.0098 * 1e9)
    assert tx.limit_price_rao(1e-12, 50, buying=False) == 1


def test_submit_unknown_id_is_refused_before_any_chain_call():
    with pytest.raises(ValueError, match='prepare it again'):
        tx.submit('nope', '0x01' + '00' * 64)


def test_submit_refuses_a_signature_over_other_bytes():
    from bittensor_wallet import Keypair
    kp = Keypair.create_from_uri('//Alice')
    tx._pending['t1'] = {'address': ALICE, 'sign_bytes': b'what the node built',
                         'created': 9e18}
    with pytest.raises(ValueError, match='signed different bytes'):
        tx.submit('t1', '0x01' + kp.sign(b'something else').hex())
    assert 't1' in tx._pending        # still usable — nothing was broadcast


def test_history_log_roundtrip():
    p = {'address': ALICE, 'kind': 'transfer', 'call_name': 'Balances.transfer_keep_alive',
         'args': {'amount_tao': 1}, 'preview': {'action': 'Send TAO'}}
    tx._log_start('h1', p)
    tx._log_end('h1', {'ok': True, 'extrinsic_hash': '0xab', 'block': 5})
    rows = tx.history(address=ALICE_DOT)
    assert rows[0]['id'] == 'h1' and rows[0]['status'] == 'ok' and rows[0]['block'] == 5
    assert rows[0]['preview'] == {'action': 'Send TAO'}


def test_routes_are_reachable_through_the_gateway_paths():
    from fastapi.testclient import TestClient
    from bt.server import app
    c = TestClient(app)
    r = c.get('/bt/_api/tx')
    assert r.status_code == 200 and 'stake' in r.json()['kinds']
    r = c.post('/tx/prepare', json={'kind': 'stake', 'address': 'garbage'})
    assert r.status_code == 400 and 'ss58' in r.json()['error']
    r = c.post('/bt/_api/tx/submit', json={'id': 'x', 'signature': '0x00'})
    assert r.status_code == 400 and r.json()['ok'] is False
