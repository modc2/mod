"""
What this module promises, checked.

Three things are worth a test here and the rest is Lighthouse's business:

  * the module IS a mod — config.json and the class agree about what it can do,
    and the two services it declares are the ones it ships;
  * the store bridge fails the way it should — an unreachable store is a
    reported state, a caller the store rejects gets the store's own verdict
    (403/451) and not a flattened 502, and an upload never loses its CID
    because the registration afterwards went wrong;
  * nothing here can act IN THE STORE for a caller who did not sign — the
    store routes and tools are 401/refused without a token, and the token that
    reaches the store is always the caller's. Uploading, listing and pinning
    are open: an unsigned caller is `anon`, budgeted, and never borrows the
    box's own identity;
  * the deployment needs no pasted key — it mints one from its own wallet.
    Lighthouse itself is never called from here (the enrolment is stubbed), so
    the tests also assert the refusal is clear when minting is off.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / 'config.json').read_text())


# ── it is a mod ──────────────────────────────────────────────────────

def test_config_declares_a_mod():
    assert CONFIG['name'] == 'lighthouse'
    assert CONFIG['anchor'] == 'mod.py'
    assert CONFIG['auth'] == 'mod-protocol'
    assert CONFIG['route'] is True
    assert CONFIG['port'] != CONFIG['app_port']
    assert 'store' in CONFIG['deps']


def test_config_fns_match_the_class(core):
    """A config that lists fns the class does not have is a lie the CLI tells."""
    for fn in CONFIG['fns']:
        assert hasattr(core.Mod, fn), f'config lists {fn}, the class has no such fn'
    for fn in core.Mod.fns:
        assert fn in CONFIG['fns'], f'the class exposes {fn}, config does not list it'


def test_declared_files_exist():
    assert (ROOT / 'api' / 'api.py').is_file()
    assert (ROOT / 'app' / 'server.py').is_file()
    assert (ROOT / 'app' / 'index.html').is_file()
    assert (ROOT / 'ecosystem.config.js').is_file()


def test_endpoints_table_covers_the_routes(client):
    """Every documented endpoint is real. The reverse is allowed (aliases)."""
    routes = {r.path for r in client.app.routes}
    for path in CONFIG['endpoints']:
        assert path in routes, f'config documents {path}, the API does not serve it'


def test_state_dir_does_not_shadow_the_store_fn(core):
    """`self.store` used to be the state dir, which silently ate the store fn:
    `m lighthouse/store` handed back a PosixPath instead of the link status."""
    lh = core.Mod()
    assert callable(lh.store)
    assert isinstance(lh.state, Path)


# ── the module's own surface ─────────────────────────────────────────

def test_status_is_honest_about_the_key(core, state_dir):
    lh = core.Mod()
    status = lh.status()
    assert status['name'] == 'lighthouse'
    assert status['state_dir'] == str(state_dir)
    assert status['configured'] is bool(lh.api_key)
    assert status['needs_key'] is not bool(lh.api_key)


def test_set_key_is_written_private(core):
    lh = core.Mod()
    lh.set_key('lh-test-key')
    assert lh.api_key == 'lh-test-key'
    assert oct(lh.creds_path.stat().st_mode)[-3:] == '600'
    lh.creds_path.unlink()


def test_index_round_trip(core, address):
    lh = core.Mod()
    lh.pin('bafyTESTcid', owner=address)
    assert any(o['cid'] == 'bafyTESTcid' for o in lh.list(owner=address))
    lh.rm('bafyTESTcid')
    assert not any(o['cid'] == 'bafyTESTcid' for o in lh.list(owner=address))


# ── identity ─────────────────────────────────────────────────────────

def test_token_resolves_to_its_signer(token, address):
    import identity
    assert identity.require(token) == address
    assert identity.whoami(f'Bearer {token}') == address


def test_garbage_token_is_rejected():
    import identity
    with pytest.raises(identity.AuthError):
        identity.require('not-a-token')
    assert identity.whoami('not-a-token') is None


def test_owner_is_claimed_once_then_fixed(token, address):
    import identity
    identity.OWNER_PATH.unlink(missing_ok=True)
    assert identity.owner() is None
    assert identity.require_owner(token) == address
    assert identity.is_owner(address)
    identity.OWNER_PATH.write_text(json.dumps({'address': '0xsomebodyelse'}))
    with pytest.raises(identity.AuthError):
        identity.require_owner(token)
    identity.OWNER_PATH.unlink(missing_ok=True)


# ── the API ──────────────────────────────────────────────────────────

def test_health_and_status_need_nothing(client):
    assert client.get('/health').json()['ok'] is True
    body = client.get('/status').json()
    assert body['name'] == 'lighthouse'
    assert 'store' in body and 'url' in body['store']


def test_store_routes_need_a_signature(client):
    """The store only hears from a signer; everything that reaches it is 401
    without a token. Uploading and listing are NOT in this list on purpose."""
    for method, path in (('get', '/me'), ('get', '/store/objects'),
                         ('post', '/store/register'), ('post', '/store/mirror'),
                         ('post', '/store/terms/accept')):
        r = getattr(client, method)(path, **({'json': {'cid': 'x'}}
                                             if method == 'post' else {}))
        assert r.status_code == 401, f'{method.upper()} {path} answered {r.status_code}'


def test_a_bad_token_is_refused_not_treated_as_anonymous(client):
    for path in ('/list', '/put/text'):
        r = (client.post(path, json={'text': 'x'}) if path.endswith('text')
             else client.get(path))
        assert r.status_code != 401, f'{path} should be open to silence'
        r = (client.post(path, json={'text': 'x'}, headers={'Authorization': 'Bearer nope'})
             if path.endswith('text') else
             client.get(path, headers={'Authorization': 'Bearer nope'}))
        assert r.status_code == 401, f'{path} accepted a token that does not verify'


def test_anonymous_callers_list_and_pin_as_anon(client, core):
    assert client.post('/pin', json={'cid': 'bafyANON'}).status_code == 200
    body = client.get('/list').json()
    assert body['owner'] == core.ANON
    assert any(o['cid'] == 'bafyANON' for o in body['objects'])
    core.Mod().rm('bafyANON')


def test_upload_without_a_key_says_how_to_get_one(client, token):
    """Auto-enrolment is off in tests (conftest), so this is the refusal a
    deployment with LIGHTHOUSE_AUTO_KEY=0 and no key gives — and it must name
    both ways out."""
    r = client.post('/put', headers={'Authorization': f'Bearer {token}'},
                    files={'file': ('t.txt', b'hi')})
    assert r.status_code == 400
    assert 'mint_key' in r.json()['detail'] and 'x-lh-key' in r.json()['detail']
    # the same door, with nobody signing
    r = client.post('/put/text', json={'text': 'hi'})
    assert r.status_code == 400, 'an anonymous upload is open — it fails on the key, not the token'
    assert 'x-lh-key' in r.json()['detail']
    assert 'files.lighthouse.storage' in r.json()['detail']


def test_api_alias_is_the_same_api(client):
    """The console asks its own origin for /lighthouse/_api — same routes."""
    assert client.get('/lighthouse/_api/health').json()['ok'] is True


def test_scope_all_is_owner_only(client, token):
    import identity
    identity.OWNER_PATH.write_text(json.dumps({'address': '0xsomebodyelse'}))
    try:
        r = client.get('/list?scope=all', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 403
    finally:
        identity.OWNER_PATH.unlink(missing_ok=True)


# ── the store bridge ─────────────────────────────────────────────────

def test_unreachable_store_is_a_state_not_a_crash():
    from store_link import StoreLink
    link = StoreLink('http://127.0.0.1:9', timeout=2, activator='')  # discard port
    status = link.status('anything')
    assert status['reachable'] is False
    assert status['error']
    assert status.get('can_push') is False
    # A dead store still has a next action in it — the console renders these.
    assert status['blockers']


def test_a_down_store_reads_as_a_sentence_not_a_traceback():
    """The panel puts this string in a two-column fact row, so the raw urllib3
    retry chain ('HTTPConnectionPool… Max retries exceeded… NewConnectionError')
    is both unreadable and useless to whoever has to fix it."""
    from store_link import StoreLink
    status = StoreLink('http://127.0.0.1:9', timeout=2, activator='').status()
    error = status['error']
    for noise in ('HTTPConnectionPool', 'Max retries', 'NewConnectionError'):
        assert noise not in error
    assert 'not running' in error and 'http://127.0.0.1:9' in error


def test_store_errors_keep_their_status_code():
    from store_link import StoreError, StoreLink
    link = StoreLink('http://127.0.0.1:9', timeout=2, activator='')
    with pytest.raises(StoreError) as e:
        link.me('token')
    assert e.value.status == 503


# ── waking a slept store ─────────────────────────────────────────────
# The fleet's activator stops idle modules. We call the store on its own port,
# which the activator never sees, so without a knock a slept store looks dead
# to this module forever.

class _Reply:
    def __init__(self, status=200, text='{}', payload=None):
        self.status_code = status
        self.text = text
        self.ok = status < 400
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload


def _stub(monkeypatch, wake_reply, calls):
    """Store port refuses until a wake succeeds; records where we knocked."""
    import requests as rq
    import store_link

    def request(method, url, **kw):
        calls.append(('store', url))
        if not calls or not any(c[0] == 'woke' for c in calls):
            raise rq.ConnectionError('[Errno 111] Connection refused')
        return _Reply(payload={'ok': True})

    def get(url, **kw):
        calls.append(('activator', url))
        if wake_reply.ok:
            calls.append(('woke', url))
        return wake_reply

    monkeypatch.setattr(store_link.requests, 'request', request)
    monkeypatch.setattr(store_link.requests, 'get', get)


def test_a_slept_store_is_woken_and_the_call_retried(monkeypatch):
    from store_link import StoreLink
    calls = []
    _stub(monkeypatch, _Reply(200), calls)
    link = StoreLink('http://127.0.0.1:9', timeout=2,
                     activator='http://127.0.0.1:9999')
    assert link.health() == {'ok': True}
    knocks = [url for kind, url in calls if kind == 'activator']
    assert knocks == ['http://127.0.0.1:9999/api/store/health']


def test_waking_goes_through_the_proxy_not_the_control_plane(monkeypatch):
    """`/_activator/control` with action=wake clears the host's `actl disable`
    flag. A peer module must not override a deliberate 'keep this off', so the
    knock is an ordinary proxied request — which honours it."""
    from store_link import StoreLink
    calls = []
    _stub(monkeypatch, _Reply(200), calls)
    StoreLink('http://127.0.0.1:9', timeout=2,
              activator='http://127.0.0.1:9999').health()
    assert not [url for _, url in calls if '_activator' in url]


def test_a_host_disabled_store_stays_down_and_says_so(monkeypatch):
    from store_link import StoreError, StoreLink
    calls = []
    _stub(monkeypatch, _Reply(503, 'activator: store disabled by host\n'), calls)
    link = StoreLink('http://127.0.0.1:9', timeout=2,
                     activator='http://127.0.0.1:9999')
    with pytest.raises(StoreError) as e:
        link.health()
    assert e.value.status == 503
    assert 'turned store off' in e.value.message
    assert 'actl enable store' in e.value.message


def test_no_activator_means_no_knock(monkeypatch):
    from store_link import StoreError, StoreLink
    calls = []
    _stub(monkeypatch, _Reply(200), calls)
    with pytest.raises(StoreError) as e:
        StoreLink('http://127.0.0.1:9', timeout=2, activator='').health()
    assert not [c for c in calls if c[0] == 'activator']
    assert 'no activator' in e.value.message


def test_a_missing_token_is_refused_before_the_network():
    from store_link import StoreError, StoreLink
    with pytest.raises(StoreError) as e:
        StoreLink()._bearer('')
    assert e.value.status == 401


def test_bearer_prefix_is_tolerated():
    from store_link import StoreLink
    assert StoreLink()._bearer('Bearer abc') == StoreLink()._bearer('abc')


def test_registration_failure_never_costs_the_cid(monkeypatch):
    """The upload already happened and the bytes are pinned forever — a store
    that is down must not turn that into a failed request."""
    from api import api as api_mod
    from store_link import StoreError

    def boom(*a, **kw):
        raise StoreError('store is down', 503)

    monkeypatch.setattr(api_mod.STORE, 'register', boom)
    out = api_mod._register_after_put({'cid': 'bafyX', 'key': 'k', 'size': 1,
                                       'url': 'https://g/ipfs/bafyX'},
                                      'token', False, None)
    assert out['registered'] is False
    assert out['status'] == 503


# ── the mcp server ───────────────────────────────────────────────────
#
# The tools are a published interface: a schema that lies, or a tool that acts
# for somebody who did not sign, is worse than no server at all.

def test_every_tool_has_a_schema_a_client_can_use():
    import mcp
    for name, tool in mcp.TOOLS.items():
        assert name.startswith('lighthouse_'), f'{name} is not namespaced'
        assert len(tool['description']) > 80, f'{name} has no real description'
        schema = tool['inputSchema']
        assert schema['type'] == 'object'
        props = schema.get('properties', {})
        for arg, spec in props.items():
            assert spec.get('description'), f'{name}.{arg} is undocumented'
        for arg in schema.get('required', []):
            assert arg in props, f'{name} requires {arg} and never declares it'
        assert 'readOnlyHint' in tool['annotations'], name


def test_handshake_and_tool_list():
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '2024-11-05'}})['result']
    assert out['protocolVersion'] == '2024-11-05'       # the client's, if we speak it
    assert out['serverInfo']['name'] == 'lighthouse'
    assert 'store' in out['instructions']
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']
    assert {t['name'] for t in listed['tools']} == set(mcp.TOOLS)


def test_a_notification_gets_no_reply():
    import mcp
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_an_unknown_tool_is_an_error_result_not_a_crash():
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                      'params': {'name': 'lighthouse_nope', 'arguments': {}}})['result']
    assert out['isError'] is True
    assert 'unknown tool' in out['content'][0]['text']


def test_the_filesystem_belongs_to_stdio(token):
    """An HTTP caller shares no filesystem with the server, so a path from one
    is at best meaningless and at worst somebody else's file."""
    import mcp
    remote = mcp.Ctx(token=token, local=False)
    for name, args in (('lighthouse_get', {'cid': 'bafyX', 'out': '/tmp/x'}),
                       ('lighthouse_set_key', {'api_key': 'lh-x'}),
                       ('lighthouse_put', {'path': '/etc/hostname'})):
        with pytest.raises(mcp.Refused) as e:
            mcp.call_tool(name, args, remote)
        assert 'stdio' in str(e.value), name


def test_a_remote_caller_never_borrows_the_box_key():
    """Without a token an HTTP context is `anon` — recorded as such, and never
    quietly handed the mod key this box signs with for the store."""
    import mcp
    anon = mcp.Ctx(local=False)
    assert anon.anonymous and anon.who() == 'anonymous'
    assert anon.address() == 'anon'
    with pytest.raises(mcp.Refused):
        anon.token()
    assert mcp.Ctx(token='abc', local=False).token() == 'abc'
    assert not mcp.Ctx(token='abc', local=False).anonymous


def test_anonymous_tools_work_and_store_tools_say_why_not(token):
    """The whole point of the keyless server: no token, and the tools that do
    not need the store still answer — the ones that do name the token."""
    import mcp
    anon = mcp.Ctx(local=False)
    out = mcp.call_tool('lighthouse_status', {}, anon)
    assert out['caller'] == 'anonymous' and 'anon' in out and 'note' in out
    mcp.call_tool('lighthouse_pin', {'cid': 'bafyMCPANON'}, anon)
    listed = mcp.call_tool('lighthouse_list', {}, anon)
    assert listed['owner'] == 'anon'
    assert any(o['cid'] == 'bafyMCPANON' for o in listed['objects'])
    for name, args in (('lighthouse_objects', {}), ('lighthouse_register', {'cid': 'x'}),
                       ('lighthouse_forget', {'cid': 'bafyMCPANON'}),
                       ('lighthouse_terms', {'accept': True})):
        with pytest.raises(mcp.Refused) as e:
            mcp.call_tool(name, args, anon)
        assert 'token' in str(e.value), name
    assert mcp.auth_of('lighthouse_put') == 'optional'
    assert mcp.auth_of('lighthouse_objects') == 'token'
    assert mcp.auth_of('lighthouse_preview') == 'none'
    assert mcp.describe()['auth']['required'] is False
    mcp.CORE.Mod().rm('bafyMCPANON')


def test_anonymous_uploads_are_budgeted(core, monkeypatch):
    lh = core.Mod()
    budget = lh.anon_budget()
    assert budget['enabled'] and budget['owner'] == core.ANON
    with pytest.raises(PermissionError) as e:
        lh.anon_check(budget['max_bytes'] + 1)
    assert 'cap' in str(e.value)
    monkeypatch.setattr(core, 'ANON_DAILY_BYTES', 0)
    with pytest.raises(PermissionError) as e:
        lh.anon_check(1)
    assert 'off' in str(e.value)


def test_the_box_enrols_itself_with_its_own_wallet(core, monkeypatch):
    """Lighthouse's sign-in is a signed nonce; the fleet key is an Ethereum key;
    so the deployment can mint its own API key. The HTTP is stubbed — the
    signature it produces is checked to recover the box's own address."""
    from eth_account import Account
    from eth_account.messages import encode_defunct
    seen = {}

    class R:
        def __init__(self, body, text=None):
            self._body, self.text, self.status_code = body, text or json.dumps(body), 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    def get(url, params=None, **kw):
        assert url.endswith('/api/auth/get_message')
        seen['address'] = params['publicKey']
        return R('sign this, nonce=42')

    def post(url, json=None, **kw):
        assert url.endswith('/api/auth/create_api_key')
        recovered = Account.recover_message(encode_defunct(text='sign this, nonce=42'),
                                            signature=json['signedMessage'])
        assert recovered == json['publicKey'] == seen['address']
        return R({'apiKey': 'lh-minted-key'})

    monkeypatch.setattr(core.requests, 'get', get)
    monkeypatch.setattr(core.requests, 'post', post)
    monkeypatch.setattr(core.Mod, 'key_status', lambda self, **kw: {'configured': True})
    lh = core.Mod()
    out = lh.mint_key()
    assert out['minted'] is True
    assert lh.api_key == 'lh-minted-key'
    creds = json.loads(lh.creds_path.read_text())
    assert creds['source'] == 'wallet' and creds['address'] == seen['address']
    assert oct(lh.creds_path.stat().st_mode)[-3:] == '600'
    assert lh.key_source() == 'wallet'
    lh.creds_path.unlink()


def test_a_key_passed_to_a_tool_is_spent_not_echoed(token):
    import mcp
    out = mcp.call_tool('lighthouse_status', {'key': 'lh-secret-key'},
                        mcp.Ctx(token=token, local=False))
    assert out['key_source'] == 'call'
    assert 'lh-secret-key' not in json.dumps(out, default=str)


def test_mcp_transport_never_401s_silence(client):
    """No header, no key: every call is answered as JSON-RPC. A store tool
    without a token is an isError RESULT that names the token — a transport
    401 would send an MCP client looking for an OAuth server. A token that is
    sent and does not verify is the one thing still refused at the door."""
    def rpc(name, headers=None, **args):
        return client.post('/mcp', headers=headers or {},
                           json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': name, 'arguments': args}})
    for name in ('lighthouse_put', 'lighthouse_list', 'lighthouse_mirror',
                 'lighthouse_register', 'lighthouse_objects', 'lighthouse_status'):
        r = rpc(name, cid='x', text='x')
        assert r.status_code == 200, name
        assert 'result' in r.json(), name
    for name in ('lighthouse_mirror', 'lighthouse_register', 'lighthouse_objects'):
        out = rpc(name, cid='x').json()['result']
        assert out['isError'] is True and 'token' in out['content'][0]['text'], name
    assert rpc('lighthouse_status').json()['result']['isError'] is False
    assert rpc('lighthouse_list').json()['result']['structuredContent']['owner'] == 'anon'
    assert rpc('lighthouse_status', headers={'Authorization': 'Bearer nope'}).status_code == 401
    init = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                     'params': {'protocolVersion': '2025-06-18'}})
    assert init.status_code == 200 and 'anon' in init.json()['result']['instructions']


def test_the_schema_is_served_without_a_token(client):
    doc = client.get('/mcp').json()
    assert doc['count'] == len(client.get('/mcp/tools').json()['tools'])
    assert doc['config']['http']['mcpServers']['lighthouse']['url'].endswith('/mcp')
    assert 'jsonrpc' in doc['protocol']


def test_the_advertised_url_is_the_one_the_caller_reached(client):
    """Behind the gateway, on a bare port and through the console's proxy are
    three different urls for one server; a config block naming the wrong one is
    a client that never connects."""
    def url(path, **headers):
        return (client.get(path, headers=headers)
                .json()['http']['mcpServers']['lighthouse']['url'])

    assert url('/mcp/config').endswith('/mcp')
    assert url('/lighthouse/_api/mcp/config').endswith('/lighthouse/_api/mcp')
    # Caddy strips /lighthouse/api and never says so, so the prefix has to be
    # put back — a forwarded host means the gateway's own route, not this port.
    assert url('/mcp/config', **{'x-forwarded-host': 'modc2.com',
                                 'x-forwarded-proto': 'https'}) \
        == 'https://modc2.com/lighthouse/api/mcp'


def test_batching_is_refused_in_words(client):
    r = client.post('/mcp', json=[{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}])
    assert r.status_code == 400
    assert 'batching' in r.json()['detail']


def test_config_documents_the_tools_it_ships():
    import mcp
    assert CONFIG['mcp']['tools'] == list(mcp.TOOLS)
    assert set(CONFIG['mcp']['local_only']) >= {'lighthouse_get', 'lighthouse_set_key'}
    for name in mcp.TOOLS:
        assert name in (ROOT / 'README.md').read_text(), f'{name} is undocumented'


# ── against a live store, when there is one ──────────────────────────

def test_live_store_link_reports_the_caller(store_up, token, address):
    if not store_up:
        pytest.skip('no store module listening')
    from store_link import StoreLink
    status = StoreLink().status(token)
    assert status['reachable'] is True
    assert status['address'] == address
    # An unknown test address is exactly the case the blockers list exists for.
    assert status['can_push'] is (not status['blockers'])


def test_live_store_refuses_an_unknown_address(client, store_up, token):
    if not store_up:
        pytest.skip('no store module listening')
    r = client.post('/store/register', json={'cid': 'bafyNOPE'},
                    headers={'Authorization': f'Bearer {token}'})
    # The store's own verdict comes through: 403 not whitelisted, 451 no terms,
    # 200 if this box happens to trust the test key. Never a flattened 502.
    assert r.status_code in (200, 403, 451), r.text
