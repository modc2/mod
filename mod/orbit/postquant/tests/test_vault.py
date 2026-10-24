"""The vault and contract storage: off-chain bytes behind on-chain pointers.

The invariants that matter: the envelope is byte-compatible with WebCrypto
AES-GCM (a NIST vector pins the cipher), the node refuses plaintext and never
needs a key, a chain anchor is what keeps a blob, and contract code comes back
with a hash that matches its anchor.
"""

import base64
import hashlib
import os
import time

import pytest

import mcp as mcpsrv
import vault as V
from pq import aesgcm
from state import StateError


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


def b64(b):
    return base64.b64encode(b).decode()


@pytest.fixture(scope='module')
def vic():
    call('pq_wallet', action='create', name='vic')
    call('pq_faucet', wallet='vic', amount='200')
    return 'vic'


def test_aesgcm_nist_vector():
    # SP 800-38D test case 16: AES-256, 96-bit IV, with AAD.
    k = bytes.fromhex('feffe9928665731c6d6a8f9467308308'
                      'feffe9928665731c6d6a8f9467308308')
    iv = bytes.fromhex('cafebabefacedbaddecaf888')
    p = bytes.fromhex('d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d'
                      '8a318a721c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657'
                      'ba637b39')
    aad = bytes.fromhex('feedfacedeadbeeffeedfacedeadbeefabaddad2')
    c = aesgcm.encrypt(k, iv, p, aad)
    assert c[:8].hex() == '522dc1f099567d07'
    assert c[-16:].hex() == '76fc6ece0f4e1768cddf8853bb2d551b'
    assert aesgcm.decrypt(k, iv, c, aad) == p
    with pytest.raises(ValueError):
        aesgcm.decrypt(k, iv, c[:-1] + bytes([c[-1] ^ 1]), aad)


def test_seal_open_both_modes():
    blob, key = V.seal(b'hello vault', name='a.txt', type_='text/plain')
    assert blob[:4] == b'PQV1' and key
    assert len(blob) == V.HEADER + V.MIN_BUCKET + 16       # padded bucket
    meta, data = V.open_(blob, key=key)
    assert data == b'hello vault' and meta['name'] == 'a.txt'

    blob2, none = V.seal(b'secret', passphrase='correct horse', iters=2000)
    assert none is None
    assert V.open_(blob2, passphrase='correct horse')[1] == b'secret'
    with pytest.raises(V.VaultError):
        V.open_(blob2, passphrase='wrong')
    with pytest.raises(V.VaultError):                      # header is aad
        V.open_(blob[:5] + b'\1' + blob[6:], key=key)


def test_pure_python_matches_fast_path():
    # Same key/iv through both implementations: the envelope must not depend
    # on which one the client happened to have.
    k, iv = os.urandom(32), os.urandom(12)
    msg = os.urandom(3000)
    pure = aesgcm.encrypt(k, iv, msg, b'hdr')
    if V._Fast is not None:
        assert V._Fast(k).encrypt(iv, msg, b'hdr') == pure


def test_node_refuses_plaintext(vic):
    with pytest.raises(StateError) as e:
        call('pq_vault', action='put', blob=b64(b'just a text file ' * 100))
    assert e.value.code == 'not_sealed'
    fake = b'PQV1' + b'\0' * 33 + b'plain words, not ciphertext ' * 80
    with pytest.raises(StateError) as e:
        call('pq_vault', action='put', blob=b64(fake))
    assert e.value.code == 'not_sealed'


def test_put_anchor_get_roundtrip(vic):
    blob, key = V.seal(b'the board minutes', name='minutes.md')
    r = call('pq_vault', action='put', blob=b64(blob), key='vault/minutes',
             wallet=vic, hours=2)
    cid = hashlib.sha3_256(blob).hexdigest()
    assert r['cid'] == cid
    assert r['anchor']['receipt']['ok'] is True
    assert 'vault/minutes' in r['anchors']
    assert call('pq_get', key='vault/minutes')['value'] == cid

    g = call('pq_vault', action='get', key='vault/minutes')
    assert g['verified'] is True
    got = base64.b64decode(g['blob'])
    assert V.open_(got, key=key)[1] == b'the board minutes'
    listed = {b['cid']: b for b in call('pq_vault', action='list')['blobs']}
    assert listed[cid]['anchors'] == ['vault/minutes']


def test_retention_follows_the_anchor(tmp_path, monkeypatch):
    st = V.Store(str(tmp_path))
    kept, _ = V.seal(b'kept')
    lost, _ = V.seal(b'lost')
    a = st.put(kept)['cid']
    b = st.put(lost)['cid']
    monkeypatch.setattr(V, 'GRACE', 10)
    dropped = st.prune(live={a}, now=time.time() + 60)
    assert dropped == [b]
    assert st.has(a) and not st.has(b)


def test_contract_deploy_show_list(vic):
    src = '#[starknet::contract]\nmod Counter { }\n'
    r = call('pq_contract', action='deploy', name='counter', lang='cairo',
             code=src, wallet=vic, hours=2)
    assert r['key'] == 'contract/counter'
    assert r['cid'] == hashlib.sha3_256(src.encode()).hexdigest()
    s = call('pq_contract', action='show', name='counter')
    assert s['code'] == src and s['verified'] and s['proof_valid']
    assert any(c['name'] == 'counter'
               for c in call('pq_contract', action='list')['contracts'])

    wasm = b'\0asm\1\0\0\0'
    r = call('pq_contract', action='deploy', name='tiny', lang='wasm',
             code_b64=b64(wasm), wallet=vic, hours=2)
    s = call('pq_contract', action='show', name='tiny')
    assert base64.b64decode(s['code_b64']) == wasm
    with pytest.raises(StateError) as e:
        call('pq_contract', action='deploy', name='bad', lang='wasm',
             code='not wasm', wallet=vic)
    assert e.value.code == 'bad_code'


def test_contract_file_is_local_only(vic, tmp_path):
    p = tmp_path / 'c.cairo'
    p.write_text('fn main() {}')
    mcpsrv.set_remote(True)
    try:
        with pytest.raises(StateError) as e:
            call('pq_contract', action='deploy', name='x', file=str(p),
                 wallet=vic)
        assert e.value.code == 'local_only'
    finally:
        mcpsrv.set_remote(False)
