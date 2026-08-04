"""
encrypt tests.

Two layers:
  * sandbox + vault unit tests — no store, no server, always run.
  * an end-to-end pass over the live API (bring circuit → encrypt → store →
    open → download → delete), skipped when the API or the store is not up.

Run:  pytest mod/orbit/encrypt/test -q
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest
import requests

MODULE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_DIR.parent.parent.parent
for p in (str(REPO_ROOT), str(MODULE_DIR)):
    if p not in sys.path:
        sys.path.append(p)

from encryptor import sandbox                                  # noqa: E402
from encryptor.sandbox import CircuitError                     # noqa: E402
from encryptor.vault import Vault                              # noqa: E402

CONFIG = json.loads((MODULE_DIR / 'config.json').read_text())
API = os.environ.get('ENCRYPT_API', CONFIG['urls']['api'])
STORE = os.environ.get('ENCRYPT_STORE_URL', CONFIG['store_url'])
AES = (MODULE_DIR / 'circuits' / 'aes_gcm.py').read_text()
CHACHA = (MODULE_DIR / 'circuits' / 'chacha_poly.py').read_text()

# A circuit's id is the sha256 of its source, so uploading a reference circuit
# verbatim would land on the *same row* a user already has — and the cleanup at
# the end of each test would delete it out from under them. Fixtures get a
# marker line so they are always distinct circuits.
def fixture(source: str, tag: str) -> str:
    return source + f'\n# encrypt test fixture: {tag}\n'


# ── sandbox ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('source', [AES, CHACHA], ids=['aes_gcm', 'chacha_poly'])
def test_reference_circuits_roundtrip(source):
    ct = sandbox.transform(source, 'encrypt', b'attack at dawn', b'passphrase')
    assert ct != b'attack at dawn'
    assert sandbox.transform(source, 'decrypt', ct, b'passphrase') == b'attack at dawn'


@pytest.mark.parametrize('source', [AES, CHACHA], ids=['aes_gcm', 'chacha_poly'])
def test_wrong_key_is_rejected(source):
    ct = sandbox.transform(source, 'encrypt', b'attack at dawn', b'right')
    with pytest.raises(CircuitError):
        sandbox.transform(source, 'decrypt', ct, b'wrong')


def test_selftest_rejects_a_passthrough_circuit():
    with pytest.raises(CircuitError, match='no-op'):
        sandbox.selftest('def encrypt(d, k, p): return d\n'
                         'def decrypt(d, k, p): return d\n', b'k')


def test_selftest_rejects_a_broken_roundtrip():
    with pytest.raises(CircuitError, match='roundtrip'):
        sandbox.selftest('def encrypt(d, k, p): return d[::-1] + b"x"\n'
                         'def decrypt(d, k, p): return d\n', b'k')


def test_selftest_rejects_a_circuit_without_the_contract():
    with pytest.raises(CircuitError, match='callable decrypt'):
        sandbox.selftest('def encrypt(d, k, p): return d\n', b'k')


def test_circuit_cannot_write_files(tmp_path):
    target = tmp_path / 'escape.txt'
    with pytest.raises(CircuitError):
        sandbox.selftest(
            f'def encrypt(d, k, p):\n'
            f'    open({str(target)!r}, "w").write("nope")\n'
            f'    return d + b"1"\n'
            f'def decrypt(d, k, p): return d[:-1]\n', b'k')
    assert not target.exists()


def test_circuit_timeout_is_enforced():
    with pytest.raises(CircuitError, match='timed out|cpu|no output'):
        sandbox.selftest('def encrypt(d, k, p):\n'
                         '    while True: pass\n'
                         'def decrypt(d, k, p): return d\n', b'k',
                         limits={'timeout': 3, 'cpu_seconds': 2})


def test_sandbox_reports_what_it_actually_enforces():
    caps = sandbox.capabilities()
    assert set(caps) >= {'network_isolated', 'drops_privileges', 'runs_as_root'}


# ── vault ────────────────────────────────────────────────────────────

def test_vault_keeps_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('ENCRYPT_DIR', str(tmp_path))
    v = Vault()
    row = v.add_circuit(b'def encrypt(d,k,p): return d\n', name='x', owner='0xABC')
    assert row['owner'] == '0xabc'
    assert v.source(row['id']).startswith('def encrypt')

    v.add_message({'id': 'm1', 'owner': '0xabc', 'circuit': row['id'], 'cid': 'Qm1',
                   'bytes': 10, 'created_at': int(time.time())})
    assert v.circuit_in_use(row['id']) == 1
    blob = (tmp_path / 'messages.json').read_text()
    assert 'passphrase' not in blob and 'key' not in json.loads(blob)['m1']

    assert v.rm_message('m1') and v.rm_circuit(row['id'])
    assert v.circuits() == {} and v.messages() == {}


# ── live api ─────────────────────────────────────────────────────────

def _up(url: str) -> bool:
    try:
        return requests.get(f'{url}/health', timeout=3).ok
    except requests.RequestException:
        return False


live = pytest.mark.skipif(not (_up(API) and _up(STORE)),
                          reason=f'needs the encrypt api ({API}) and the store ({STORE}) running')


@pytest.fixture(scope='module')
def token():
    import mod as m
    name = os.environ.get('ENCRYPT_KEY')
    auth = m.mod('auth')(key=name, crypto_type='ecdsa') if name else m.mod('auth')(crypto_type='ecdsa')
    return auth.token({'scope': 'encrypt-test'})


@pytest.fixture
def call(token):
    def _call(method, path, expect=200, **kw):
        r = requests.request(method, f'{API}{path}',
                             headers={'Authorization': f'Bearer {token}'}, timeout=60, **kw)
        assert r.status_code == expect, f'{method} {path} → {r.status_code}: {r.text[:300]}'
        return r
    return _call


@live
def test_auth_is_required():
    assert requests.get(f'{API}/circuits', timeout=10).status_code == 401
    assert requests.get(f'{API}/circuits', timeout=10,
                        headers={'Authorization': 'Bearer garbage'}).status_code == 401


@live
def test_full_lifecycle(call, token):
    """Bring a circuit, encrypt, read it back, delete everything."""
    circuit = call('POST', '/circuits', json={'source': fixture(CHACHA, 'chacha'), 'name': 'test_chacha'}).json()
    assert circuit['selftest']['ok'] and circuit['cid']

    secret = 'the eagle lands at 0400'
    msg = call('POST', '/messages', json={'circuit': circuit['id'], 'key': 'hunter2',
                                          'text': secret, 'label': 'test-note'}).json()
    assert msg['cid'] and msg['bytes'] > 0

    listed = call('GET', '/messages').json()['messages']
    assert any(row['id'] == msg['id'] for row in listed)

    assert call('POST', f'/messages/{msg["id"]}/open',
                json={'key': 'hunter2'}).json()['text'] == secret
    call('POST', f'/messages/{msg["id"]}/open', json={'key': 'nope'}, expect=400)

    ciphertext = call('GET', f'/messages/{msg["id"]}/download').content
    assert len(ciphertext) == msg['bytes'] and secret.encode() not in ciphertext

    # The ciphertext really is in the store, under the caller's own identity.
    auth = {'Authorization': f'Bearer {token}'}
    assert requests.get(f'{STORE}/get', params={'cid': msg['cid']},
                        headers=auth, timeout=30).content == ciphertext

    # Delete reaches the store, and reports honestly what the store did: the
    # backend currently keeps the bytes after /rm, and we say so instead of
    # claiming they are gone.
    removed = call('DELETE', f'/messages/{msg["id"]}').json()
    still_there = requests.get(f'{STORE}/get', params={'cid': msg['cid']},
                               headers=auth, timeout=30).ok
    if removed['store_removed'] is True:
        assert not still_there
    else:
        assert 'still serves' in removed['store_removed'] \
            or 'could not be verified' in removed['store_removed']
    call('GET', f'/messages/{msg["id"]}', expect=404)

    call('DELETE', f'/circuits/{circuit["id"]}')
    call('GET', f'/circuits/{circuit["id"]}', expect=404)


@live
def test_binary_payload_survives_the_roundtrip(call):
    circuit = call('POST', '/circuits', json={'source': fixture(AES, 'aes'), 'name': 'test_aes'}).json()
    blob = bytes(range(256)) * 4
    msg = call('POST', '/messages', json={'circuit': circuit['id'], 'key': 'k',
                                          'data_b64': base64.b64encode(blob).decode(),
                                          'label': 'test-binary'}).json()
    out = call('POST', f'/messages/{msg["id"]}/open', json={'key': 'k'}).json()
    assert base64.b64decode(out['data_b64']) == blob
    call('DELETE', f'/messages/{msg["id"]}')
    call('DELETE', f'/circuits/{circuit["id"]}')


@live
def test_burn_after_read_deletes_itself(call):
    circuit = call('POST', '/circuits', json={'source': fixture(CHACHA, 'burn'), 'name': 'test_burn'}).json()
    msg = call('POST', '/messages', json={'circuit': circuit['id'], 'key': 'k',
                                          'text': 'read once', 'burn': True,
                                          'label': 'test-burn'}).json()
    opened = call('POST', f'/messages/{msg["id"]}/open', json={'key': 'k'}).json()
    assert opened['text'] == 'read once' and opened['burned'] is True
    call('GET', f'/messages/{msg["id"]}', expect=404)
    call('DELETE', f'/circuits/{circuit["id"]}')


@live
def test_a_circuit_in_use_is_not_deletable_by_accident(call):
    circuit = call('POST', '/circuits', json={'source': fixture(CHACHA, 'inuse'), 'name': 'test_inuse'}).json()
    msg = call('POST', '/messages', json={'circuit': circuit['id'], 'key': 'k',
                                          'text': 'still needed', 'label': 'test-inuse'}).json()
    call('DELETE', f'/circuits/{circuit["id"]}', expect=403)
    call('DELETE', f'/messages/{msg["id"]}')
    call('DELETE', f'/circuits/{circuit["id"]}')


@live
def test_a_shared_circuit_installs_from_its_cid(call):
    """The pin is a real handoff: install by CID, and the installed source is
    byte-identical to what was uploaded."""
    circuit = call('POST', '/circuits', json={'source': fixture(AES, 'shared'), 'name': 'test_shared',
                                              'public': True}).json()
    installed = call('POST', '/circuits/install',
                     json={'cid': circuit['cid'], 'name': 'test_installed'}).json()
    assert installed['sha256'] == circuit['sha256']
    assert call('GET', f'/circuits/{installed["id"]}/source').text == fixture(AES, 'shared')
    call('DELETE', f'/circuits/{circuit["id"]}')


@live
def test_attach_registers_a_client_encrypted_blob(call, token):
    """The mode where the server never sees a key: the caller stores the
    ciphertext themselves and encrypt only tracks the CID."""
    ciphertext = b'\x00opaque-to-the-server\xff'
    put = requests.post(f'{STORE}/put', headers={'Authorization': f'Bearer {token}'},
                        files={'file': ('test-attached.enc', ciphertext)},
                        data={'backend': 'localfs', 'public': 'false'}, timeout=60)
    assert put.ok, put.text[:300]
    cid = next(v['cid'] for v in put.json()['results'].values()
               if isinstance(v, dict) and v.get('cid'))

    msg = call('POST', '/messages/attach', json={'cid': cid, 'label': 'test-attached'}).json()
    assert msg['mode'] == 'client' and msg['circuit'] is None
    assert call('GET', f'/messages/{msg["id"]}/download').content == ciphertext
    # No circuit means nothing to decrypt with — say so rather than guess.
    call('POST', f'/messages/{msg["id"]}/open', json={'key': 'k'}, expect=404)
    call('DELETE', f'/messages/{msg["id"]}')


@live
def test_bad_circuits_are_refused(call):
    call('POST', '/circuits', json={'source': 'def encrypt(d,k,p): return d\n'
                                              'def decrypt(d,k,p): return d\n',
                                    'name': 'test_noop'}, expect=400)
    call('POST', '/circuits', json={'source': 'not python at all !!!', 'name': 'test_junk'},
         expect=400)
