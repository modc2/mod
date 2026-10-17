"""Sign-in: mod-protocol tokens through identity.py and the HTTP gate.

A browser wallet's token is minted here with eth_account exactly as the
console does it (EIP-191 personal_sign over JSON.stringify({data, time}));
if the protocol package is importable the CLI flavour is checked too. The
server tests start serve.py's handler on a free port with a throwaway
store and a spoofed X-Forwarded-For so loopback does not count as the box.
"""

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

os.environ.setdefault('JUDGE_XMSS_HEIGHT', '3')
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import identity  # noqa: E402

eth_account = pytest.importorskip('eth_account')
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402


def b64url(obj):
    return base64.urlsafe_b64encode(
        json.dumps(obj, separators=(',', ':')).encode()).decode().rstrip('=')


def browser_token(acct, data=None, when=None):
    data = data if data is not None else {'mod': 'judge'}
    t = str(when if when is not None else time.time())
    msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    if not sig.startswith('0x'):
        sig = '0x' + sig
    return b64url({'data': data, 'time': t, 'key': acct.address.lower(),
                   'signature': sig})


@pytest.fixture(autouse=True)
def fresh_cache():
    identity._cache.clear()
    yield
    identity._cache.clear()


def test_browser_token_names_its_signer():
    acct = Account.create()
    tok = browser_token(acct)
    assert identity.verify(tok) == acct.address.lower()
    assert identity.verify('Bearer ' + tok) == acct.address.lower()
    assert identity.whoami(tok) == acct.address.lower()
    # Second call is served from the cache and agrees.
    assert identity.verify(tok) == acct.address.lower()


def test_local_verifier_matches():
    acct = Account.create()
    env = json.loads(base64.urlsafe_b64decode(browser_token(acct) + '=='))
    assert identity._verify_local(env)['key'] == acct.address.lower()


def test_bad_tokens_are_refused():
    acct, other = Account.create(), Account.create()
    env = json.loads(base64.urlsafe_b64decode(browser_token(acct) + '=='))
    # Claiming another key than the one that signed.
    assert identity.whoami(b64url(dict(env, key=other.address.lower()))) is None
    # Changing the signed data.
    assert identity.whoami(b64url(dict(env, data={'mod': 'dns'}))) is None
    # Stale, and from the future.
    old = browser_token(acct, when=time.time() - identity.TOKEN_MAX_AGE - 60)
    assert identity.whoami(old) is None
    assert identity.whoami(browser_token(acct, when=time.time() + 10 ** 6)) is None
    # Garbage.
    assert identity.whoami('') is None and identity.whoami(None) is None
    assert identity.whoami('not-a-token') is None
    assert identity.whoami(b64url({'data': {}, 'time': '1'})) is None
    with pytest.raises(identity.AuthError):
        identity.verify('nope')


def test_cli_token_if_the_protocol_is_here():
    m = identity.protocol()
    if m is None:
        pytest.skip('mod protocol package not importable here')
    tok = m.mod('auth')().token({'mod': 'judge'})
    who = identity.verify(tok)
    assert who.startswith('0x') and who == who.lower()
    assert identity.status()['verifier'] == 'mod protocol auth'


# ── the HTTP gate ───────────────────────────────────────────────────────

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv('JUDGE_DIR', str(tmp_path))
    import serve
    serve._book = serve._shop = None
    srv = ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()
    serve._book = serve._shop = None


def call(base, fn, body=None, token=None, box=None):
    hdrs = {'Content-Type': 'application/json',
            'X-Forwarded-For': '203.0.113.9'}      # not the box
    if token:
        hdrs['Authorization'] = 'Bearer ' + token
    if box:
        hdrs['X-Judge-Token'] = box
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + '/' + fn, data, hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


RULE = {'name': 'long', 'kind': 'rule', 'min_len': 5, 'goal': 'no one-liners'}


def test_anon_reads_but_cannot_change_panels(server):
    st, who = call(server, 'auth')
    assert st == 200 and who['role'] == 'anon' and who['address'] is None
    assert not who['can_change_panels']
    st, r = call(server, 'create_panel', {'name': 'p', 'creator': 'x', 'judges': [RULE]})
    assert st == 401 and 'sign' in r['error'].lower()
    st, r = call(server, 'add_judge', {'name': 'p', 'creator': 'x', 'judge': RULE})
    assert st == 401
    st, r = call(server, 'panels')
    assert st == 200 and r['panels'] == []


def test_signed_caller_is_the_creator(server):
    alice, bob = Account.create(), Account.create()
    ta, tb = browser_token(alice), browser_token(bob)
    a, b = alice.address.lower(), bob.address.lower()

    st, who = call(server, 'whoami', token=ta)
    assert st == 200 and who == {**who, 'role': 'signed', 'address': a}

    # Whatever creator the body claims, the panel belongs to the signer.
    st, p = call(server, 'create_panel',
                 {'name': 'pr', 'creator': 'somebody-else', 'judges': [RULE]}, token=ta)
    assert st == 200 and p['creator'] == a
    assert p['judges'][0]['goal'] == 'no one-liners'
    assert set(p['judges'][0]['keys'])             # keys are public in the reply

    # Bob cannot touch alice's panel, even naming her as creator.
    st, r = call(server, 'add_judge',
                 {'name': 'pr', 'creator': a, 'judge': {'name': 'c', 'kind': 'rule'}}, token=tb)
    assert st == 200 and 'only the creator' in r['error']
    st, r = call(server, 'remove_panel', {'name': 'pr', 'creator': a}, token=tb)
    assert 'only the creator' in r['error']

    # Alice adds a judge with a goal and reads its keys.
    st, r = call(server, 'add_judge',
                 {'name': 'pr', 'judge': {'name': 'c', 'kind': 'rule', 'forbid': ['TODO'],
                                          'goal': 'no TODO markers'}}, token=ta)
    assert st == 200 and r['added'] == 'c' and r['judge']['goal'] == 'no TODO markers'
    st, k = call(server, 'judge_keys?panel=pr&judge=c')
    assert st == 200 and k['goal'] == 'no TODO markers' and k['keys']

    # Publishing is attributed to the signer too.
    st, l = call(server, 'publish_judge',
                 {'name': 'mine', 'author': 'fake', 'spec': RULE}, token=tb)
    assert st == 200 and l['author'] == b
    st, r = call(server, 'unpublish_judge', {'id': l['id'], 'author': b}, token=ta)
    assert 'only the author' in r['error']

    # A garbage bearer is anon, not an error.
    st, r = call(server, 'create_panel', {'name': 'q', 'judges': [RULE]}, token='garbage')
    assert st == 401


def test_box_token_still_passes_creator_through(server):
    import serve
    st, p = call(server, 'create_panel',
                 {'name': 'cli', 'creator': 'alice', 'judges': [RULE]}, box=serve.token())
    assert st == 200 and p['creator'] == 'alice'
    st, who = call(server, 'auth', box=serve.token())
    assert who['role'] == 'box' and who['trusted']
