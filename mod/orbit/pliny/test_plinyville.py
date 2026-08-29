#!/usr/bin/env python3
"""plinyville smoke test — all three surfaces, no network required.

The GitHub-backed routes are exercised only if api.github.com is reachable; the
checks that matter for safety (the served exhibit is defanged, the payload is
never served as script) run offline and are not allowed to skip.

    python3 test_plinyville.py          # or: pytest test_plinyville.py
"""
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import api                                       # noqa: E402
import app                                       # noqa: E402
import mcp                                       # noqa: E402
from plinyville import Ville                     # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


API_PORT, APP_PORT = _free_port(), _free_port()
os.environ['PLINYVILLE_API_URL'] = f'http://127.0.0.1:{API_PORT}'
app.API_URL = os.environ['PLINYVILLE_API_URL']


def _boot():
    threading.Thread(target=api.serve, args=(API_PORT, '127.0.0.1'), daemon=True).start()
    threading.Thread(target=app.serve, args=(APP_PORT, '127.0.0.1'), daemon=True).start()
    for _ in range(80):
        try:
            _get(f'http://127.0.0.1:{API_PORT}/')
            _get(f'http://127.0.0.1:{APP_PORT}/health')
            return
        except Exception:                        # noqa: BLE001
            import time
            time.sleep(0.05)
    raise RuntimeError('servers did not come up')


def _get(url, raw=False):
    with urllib.request.urlopen(url, timeout=20) as r:
        body = r.read()
        return (r, body) if raw else json.loads(body.decode())


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'},
                                 method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw.decode()) if raw else None


def _online():
    try:
        Ville().repo('L1B3RT4S')
        return True
    except Exception:                            # noqa: BLE001
        return False


ONLINE = None
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# ── the exhibit is defanged (offline, never skipped) ────────────────────────

@check
def test_served_page_never_loads_the_payload():
    html = Ville().plinyworld_html()
    assert 'triggers.defanged.js' in html, 'defanged script not wired in'
    assert 'src="./triggers.js"' not in html and 'src="triggers.js"' not in html, \
        'the LIVE payload is wired into the served page'
    assert 'DEFANGED' in html, 'defang banner missing'


@check
def test_payload_is_served_as_text_not_script():
    r, body = _get(f'http://127.0.0.1:{API_PORT}/payload', raw=True)
    assert r.headers['Content-Type'].startswith('text/plain'), r.headers['Content-Type']
    assert b'navigator.clipboard' in body, 'payload not preserved'
    r2, _ = _get(f'http://127.0.0.1:{APP_PORT}/plinyworld/payload', raw=True)
    assert r2.headers['Content-Type'].startswith('text/plain')
    assert r2.headers.get('X-Content-Type-Options') == 'nosniff'


@check
def test_exhibit_reports_the_attack():
    ex = _get(f'http://127.0.0.1:{API_PORT}/exhibit')
    assert 'PWNED' in (ex['clipboard_payload'] or ''), ex['clipboard_payload']
    assert len(ex['phishing_links']) == 15, ex['phishing_links']
    assert 'paypa1.com' in ex['typosquatted_domains']
    assert 'example.com' not in ex['typosquatted_domains'], 'fallback link leaked in'


@check
def test_exhibit_url_needs_the_trailing_slash():
    # Without the redirect the relative script tag 404s and the page is inert.
    op = urllib.request.build_opener(_NoRedirect)
    try:
        r = op.open(f'http://127.0.0.1:{APP_PORT}/plinyworld', timeout=10)
        code, headers = r.status, r.headers
    except urllib.error.HTTPError as e:      # not-following a redirect raises
        code, headers = e.code, e.headers
    assert code == 301 and headers['Location'].endswith('/plinyworld/'), (code, dict(headers))
    _, js = _get(f'http://127.0.0.1:{APP_PORT}/plinyworld/triggers.defanged.js', raw=True)
    assert b'writeText' not in js, 'the defanged script writes to the clipboard!'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


# ── api ─────────────────────────────────────────────────────────────────────

@check
def test_api_null_call_is_info():
    info = _get(f'http://127.0.0.1:{API_PORT}/')
    assert info['name'] == 'plinyville'
    assert info['mcp']['tools'] == len(mcp.TOOLS)


@check
def test_api_gateway_prefixes_are_stripped():
    for path in ('/api/plinyville/exhibit', '/pliny/api/exhibit', '/exhibit'):
        assert _get(f'http://127.0.0.1:{API_PORT}{path}')['phishing_links']


@check
def test_api_errors_are_typed():
    try:
        _get(f'http://127.0.0.1:{API_PORT}/repo?name=../etc')
        raise AssertionError('bad repo name was accepted')
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code


# ── app ─────────────────────────────────────────────────────────────────────

@check
def test_app_serves_the_market_and_proxies_the_api():
    _, html = _get(f'http://127.0.0.1:{APP_PORT}/', raw=True)
    assert b'plinyville' in html and b"B + '/api'" in html and b"api('/market'" in html
    _, mod_html = _get(f'http://127.0.0.1:{APP_PORT}/m/L1B3RT4S', raw=True)
    assert b'/api/m/' in mod_html and b"this mod's backend" in mod_html.lower()
    ex = _get(f'http://127.0.0.1:{APP_PORT}/api/exhibit')
    assert ex['clipboard_payload']
    ex2 = _get(f'http://127.0.0.1:{APP_PORT}/pliny/api/exhibit')   # gateway prefix
    assert ex2['clipboard_payload'] == ex['clipboard_payload']


# ── mcp ─────────────────────────────────────────────────────────────────────

@check
def test_mcp_handshake_and_registry():
    init = _post(f'http://127.0.0.1:{API_PORT}/mcp',
                 {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['serverInfo']['name'] == 'plinyville'
    assert init['result']['protocolVersion'] == '2025-06-18'
    tools = _post(f'http://127.0.0.1:{API_PORT}/mcp',
                  {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert {t['name'] for t in tools} == set(mcp.TOOLS)
    for t in tools:
        assert t['description'] and t['inputSchema']['type'] == 'object'


@check
def test_mcp_notification_gets_no_body():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert _post(f'http://127.0.0.1:{API_PORT}/mcp',
                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


@check
def test_mcp_tool_failure_is_a_result_not_a_transport_error():
    r = _post(f'http://127.0.0.1:{API_PORT}/mcp',
              {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
               'params': {'name': 'pv_file', 'arguments': {'name': 'L1B3RT4S'}}})['result']
    assert r['isError'] and 'path' in r['content'][0]['text']
    unknown = _post(f'http://127.0.0.1:{API_PORT}/mcp',
                    {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                     'params': {'name': 'nope', 'arguments': {}}})['result']
    assert unknown['isError']


@check
def test_mcp_exhibit_tool_matches_the_api():
    r = _post(f'http://127.0.0.1:{API_PORT}/mcp',
              {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
               'params': {'name': 'pv_exhibit', 'arguments': {}}})['result']
    assert r['structuredContent'] == _get(f'http://127.0.0.1:{API_PORT}/exhibit')


# ── the market (offline, on a temp store) ───────────────────────────────────

@check
def test_market_archives_a_mod_into_the_store_and_reads_it_offline():
    import tempfile
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-store-')
    orig = _market.STORE_ROOT
    _market.STORE_ROOT = tmp
    try:
        mk = _market.Market()
        # a synthetic repo — no network: seed the archive directly, then read it back.
        bundle = {
            'repo': 'DEMO', 'user': 'elder-plinius',
            'meta': {'name': 'DEMO', 'description': 'demo', 'stars': 1,
                     'url': 'https://github.com/elder-plinius/DEMO',
                     'default_branch': 'main', 'language': 'Python', 'topics': []},
            'default_branch': 'main', 'readme': '# DEMO\nhello',
            'tree': [{'path': 'README.md', 'type': 'blob', 'size': 12, 'sha': 'x'},
                     {'path': 'src/a.py', 'type': 'blob', 'size': 9, 'sha': 'y'}],
            'files': {'README.md': '# DEMO\nhello', 'src/a.py': 'print(42)'},
            'files_total': 2, 'files_stored': 2, 'archived_at': 0,
        }
        cid = mk._cid(bundle)
        mk._store_put('mods/DEMO/content', bundle)
        mk._store_put('mods/DEMO/manifest',
                      mk._build_manifest('DEMO', bundle['meta'], bundle, cid))
        idx = mk._index()
        idx.setdefault('mods', {})['DEMO'] = {'cid': cid, 'files_stored': 2}
        mk._save_index(idx)

        assert mk.is_installed('DEMO')
        # the mod reads offline from the store, never GitHub
        assert mk.repo_readme('DEMO')['source'] == 'store'
        root = mk.repo_tree('DEMO')
        names = {e['name']: e['type'] for e in root['entries']}
        assert names == {'README.md': 'file', 'src': 'dir'}, names
        sub = mk.repo_tree('DEMO', 'src')
        assert sub['entries'][0]['name'] == 'a.py' and sub['entries'][0]['stored']
        assert mk.repo_file('DEMO', 'src/a.py')['text'] == 'print(42)'
        assert mk.repo_search('DEMO', 'print')['count'] == 1
        man = mk.mod('DEMO')
        assert man['name'] == 'pv-demo' and man['mcp'].endswith('/m/DEMO/mcp')
    finally:
        _market.STORE_ROOT = orig


@check
def test_per_mod_mcp_server_is_scoped_to_one_repo():
    import tempfile
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-store-')
    orig = _market.STORE_ROOT
    _market.STORE_ROOT = tmp
    try:
        mk = _market.Market()
        bundle = {'repo': 'DEMO', 'user': 'elder-plinius',
                  'meta': {'name': 'DEMO', 'description': 'd', 'default_branch': 'main'},
                  'default_branch': 'main', 'readme': 'hi', 'tree': [], 'files': {},
                  'files_total': 0, 'files_stored': 0, 'archived_at': 0}
        mk._store_put('mods/DEMO/content', bundle)
        mk._store_put('mods/DEMO/manifest',
                      mk._build_manifest('DEMO', bundle['meta'], bundle, mk._cid(bundle)))
        mk._save_index({'mods': {'DEMO': {'cid': 'x'}}})

        tools = mcp.repo_tools('DEMO')
        assert set(tools) == {f'demo_{s}' for s in
                              ('info', 'readme', 'tree', 'file', 'search', 'install',
                               'run')}
        init = mcp.handle_repo('DEMO',
                               {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                'params': {}})['result']
        assert init['serverInfo']['name'] == 'plinyville/DEMO'
        r = mcp.handle_repo('DEMO',
                            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                             'params': {'name': 'demo_readme', 'arguments': {}}})['result']
        assert not r['isError'] and 'hi' in r['content'][0]['text']
    finally:
        _market.STORE_ROOT = orig


@check
def test_all_server_is_one_tool_per_repo_plus_the_corpus_tools():
    """The ALL server: every repo shows up as its own tool, reads offline, and
    still carries the corpus-wide pv_* tools."""
    import tempfile
    import market as _market
    from plinyville import Ville as _Ville
    tmp = tempfile.mkdtemp(prefix='pv-store-')
    orig, orig_repos = _market.STORE_ROOT, _Ville.repos
    _market.STORE_ROOT = tmp
    _Ville.repos = lambda self, search=None, n=500, refresh=False: {'repos': [
        {'name': 'DEMO', 'description': 'demo', 'stars': 3, 'language': 'Python'},
        {'name': 'OTHER-THING', 'description': '', 'stars': 0}]}
    try:
        mk = _market.Market()
        bundle = {'repo': 'DEMO', 'user': 'elder-plinius',
                  'meta': {'name': 'DEMO', 'description': 'demo', 'default_branch': 'main'},
                  'default_branch': 'main', 'readme': '# DEMO',
                  'tree': [{'path': 'a.py', 'type': 'blob', 'size': 9, 'sha': 'y'}],
                  'files': {'a.py': 'print(42)'},
                  'files_total': 1, 'files_stored': 1, 'archived_at': 0}
        mk._store_put('mods/DEMO/content', bundle)
        mk._save_index({'mods': {'DEMO': {'cid': 'x', 'files_stored': 1}}})

        reg = mcp.all_tools()
        assert {'pv_demo', 'pv_other-thing'} <= set(reg), sorted(reg)
        assert set(mcp.TOOLS) <= set(reg)               # the corpus tools ride along
        assert 'archived offline' in reg['pv_demo']['description']
        assert 'live from github' in reg['pv_other-thing']['description']

        init = mcp.handle_all({'jsonrpc': '2.0', 'id': 1,
                               'method': 'initialize', 'params': {}})['result']
        assert init['serverInfo']['name'] == 'plinyville-all'

        def call(args):
            return mcp.handle_all({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                   'params': {'name': 'pv_demo', 'arguments': args}})['result']

        assert '# DEMO' in call({})['content'][0]['text']            # op defaults to readme
        assert 'print(42)' in call({'op': 'file', 'path': 'a.py'})['content'][0]['text']
        assert call({'op': 'search', 'query': 'print'})['structuredContent']['count'] == 1
        # a missing argument and a bad op are typed results, not transport errors
        assert call({'op': 'file'})['isError']
        assert call({'op': 'nope'})['isError']
    finally:
        _market.STORE_ROOT, _Ville.repos = orig, orig_repos


@check
def test_all_server_answers_over_http():
    tl = _post(f'http://127.0.0.1:{API_PORT}/mcp/all',
               {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    names = {t['name'] for t in tl}
    assert 'pv_market' in names and len(names) > len(mcp.TOOLS), len(names)
    # the same registry backs GET /tools?all=1
    assert len(_get(f'http://127.0.0.1:{API_PORT}/tools?all=1')['tools']) == len(tl)


@check
def test_market_http_routes_answer_without_network():
    cat = _get(f'http://127.0.0.1:{API_PORT}/market')
    assert cat['market'] == 'plinyville' and 'mods' in cat
    # an un-archived mod cannot be grepped offline → a typed 400, not a crash
    try:
        _get(f'http://127.0.0.1:{API_PORT}/m/__nope__/search?q=x')
        raise AssertionError('offline search on a missing mod should 400')
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
    # its MCP endpoint advertises the repo-scoped tools on GET
    reg = _get(f'http://127.0.0.1:{API_PORT}/m/L1B3RT4S/tools')
    assert any(t['name'].startswith('l1b3rt4s_') for t in reg['tools']), reg


@check
def test_market_tools_are_registered_on_the_module_mcp():
    names = set(mcp.TOOLS)
    assert {'pv_market', 'pv_install'} <= names, names
    # the module-level MCP still answers the null market list without network:
    r = mcp.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/list'})['result']['tools']
    assert 'pv_market' in {t['name'] for t in r}


# ── github-backed (skipped offline) ─────────────────────────────────────────

@check
def test_mirror_reads_github():
    if not ONLINE:
        return 'skipped (github unreachable)'
    repos = _get(f'http://127.0.0.1:{API_PORT}/repos?limit=5')
    assert repos['count'] and len(repos['repos']) <= 5
    tree = _get(f'http://127.0.0.1:{API_PORT}/tree?name=L1B3RT4S')
    assert tree['entries'], tree
    one = _get(f'http://127.0.0.1:{API_PORT}/file?name=L1B3RT4S&path=README.md')
    assert one['text'], one
    via_mcp = _post(f'http://127.0.0.1:{API_PORT}/mcp',
                    {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
                     'params': {'name': 'pv_repos', 'arguments': {'limit': 5}}})['result']
    assert not via_mcp['isError'] and via_mcp['structuredContent']['repos']
    return None


# ── github auth resolution (offline, never skipped) ─────────────────────────

@check
def test_token_resolution_order():
    """env beats the module secret beats the git mod's account — and the file is
    read per call, so dropping a token in reaches a server that is already up."""
    import tempfile

    import plinyville as pv
    with tempfile.TemporaryDirectory() as d:
        tok_path = os.path.join(d, 'github.json')
        v = Ville(state_path=os.path.join(d, 'state.json'), token_path=tok_path)
        saved_env = {k: os.environ.pop(k, None) for k in ('GITHUB_TOKEN', 'GH_TOKEN')}
        saved_git = pv.GIT_TOKEN
        pv.GIT_TOKEN = os.path.join(d, 'git.json')
        try:
            assert v._token() == (None, None), 'anonymous should stay anonymous'

            with open(pv.GIT_TOKEN, 'w') as f:                  # the git mod's PAT
                json.dump({'accounts': {'0xabc': {'active': 'someone', 'logins': {
                    'someone': {'token': 'gh_from_git', 'login': 'someone'}}}}}, f)
            tok, where = v._token()
            assert tok == 'gh_from_git' and 'git mod' in where, (tok, where)

            with open(tok_path, 'w') as f:                      # our own secret wins
                json.dump({'token': 'gh_from_file'}, f)
            assert v._token()[0] == 'gh_from_file', v._token()

            os.environ['GITHUB_TOKEN'] = 'gh_from_env'          # env wins over both
            assert v._token() == ('gh_from_env', '$GITHUB_TOKEN'), v._token()
        finally:
            pv.GIT_TOKEN = saved_git
            os.environ.pop('GITHUB_TOKEN', None)
            for k, val in saved_env.items():
                if val is not None:
                    os.environ[k] = val


@check
def test_rate_limit_error_says_how_to_fix_it():
    """A 403 must name the wall it hit and the command that raises it — the old
    message sent you looking for an env var on a server you'd have to restart."""
    import tempfile

    import plinyville as pv
    with tempfile.TemporaryDirectory() as d:
        v = Ville(state_path=os.path.join(d, 'state.json'),
                  token_path=os.path.join(d, 'github.json'))
        saved_env = {k: os.environ.pop(k, None) for k in ('GITHUB_TOKEN', 'GH_TOKEN')}
        saved_git, pv.GIT_TOKEN = pv.GIT_TOKEN, os.path.join(d, 'git.json')
        reset = int(time.time()) + 1800

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 403, 'rate limit exceeded',
                {'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '0',
                 'X-RateLimit-Reset': str(reset)},
                io.BytesIO(json.dumps({'message': 'API rate limit exceeded for 1.2.3.4. '
                                       '(But here is the good news: …)'}).encode()))

        saved_open, urllib.request.urlopen = urllib.request.urlopen, boom
        try:
            try:
                v.repos(refresh=True)
            except pv.GitHubError as e:
                msg = str(e)
            else:
                raise AssertionError('a 403 should have surfaced')
        finally:
            urllib.request.urlopen = saved_open
            pv.GIT_TOKEN = saved_git
            for k, val in saved_env.items():
                if val is not None:
                    os.environ[k] = val
    assert 'm pliny/token' in msg, msg
    assert 'resets in 29m' in msg or 'resets in 30m' in msg, msg
    assert '60/hr anonymous' in msg, msg
    assert v.rate['remaining'] == 0 and v.rate['limit'] == 60, v.rate
    return 'anonymous 403 explains the wall and the fix'


@check
def test_scan_records_what_changed_and_stays_fresh():
    """The daily scan is the only thing keeping this mirror honest, so its receipt
    has to say three things without the network: what moved, whether that makes
    the corpus current, and which CID served it. GitHub is stubbed — this is
    about the bookkeeping, not the fetch."""
    import tempfile

    from scan import Scanner
    with tempfile.TemporaryDirectory() as d:
        v = Ville(state_path=os.path.join(d, 'state.json'),
                  token_path=os.path.join(d, 'github.json'))
        rows = [{'name': 'L1B3RT4S', 'pushed_at': '2026-01-01T00:00:00Z'},
                {'name': 'CL4R1TAS', 'pushed_at': '2026-01-01T00:00:00Z'}]
        v._save({'repos': rows, 'updated': time.time()})

        after = [{'name': 'L1B3RT4S', 'pushed_at': '2026-08-01T00:00:00Z'},   # moved
                 {'name': 'GLOSSOPETRAE', 'pushed_at': '2026-08-01T00:00:00Z'}]  # added
        v._fetch_repos = lambda: after                     # CL4R1TAS drops out
        v.refresh_plinyworld = lambda: {'index.html': 10, 'commit': 'deadbeef'}

        s = Scanner(v, state_path=os.path.join(d, 'scan.json'))
        s.mkt.installed = lambda: {}                       # nothing archived to restock
        s.mint_cid = lambda register=True: 'QmScanTestCid00000000000000000000000000000'
        rec = s.run()

        assert rec['ok'], rec
        assert rec['added'] == ['GLOSSOPETRAE'], rec
        assert rec['removed'] == ['CL4R1TAS'], rec
        assert rec['moved'] == ['L1B3RT4S'], rec

        st = s.status()
        assert st['up_to_date'] and st['state'] == 'ok', st
        assert st['cid'] == 'QmScanTestCid00000000000000000000000000000', st
        assert st['changes'] == 3 and st['repos'] == 2, st
        assert st['next_scan'] > st['last_scan'], st
        if st['next_scan_source'] == 'cron':      # the installed job, not last+24h
            hh = int(st['cron']['schedule'].split()[1])
            assert time.localtime(st['next_scan']).tm_hour == hh, st
        else:
            assert st['next_scan'] - st['last_scan'] == 24 * 3600, st

        # a receipt older than the interval + slack is stale, not "up to date"
        raw = json.load(open(os.path.join(d, 'scan.json')))
        raw['last']['at'] -= 40 * 3600
        json.dump(raw, open(os.path.join(d, 'scan.json'), 'w'))
        assert Scanner(v, state_path=os.path.join(d, 'scan.json')).status()['state'] == 'stale'
    return 'added/removed/moved booked; freshness expires with the interval'


@check
def test_a_failed_scan_is_a_receipt_not_an_exception():
    """cron has nowhere to raise to. A GitHub outage must leave a receipt that
    says the scan failed — silence would let the page keep claiming freshness."""
    import tempfile

    import plinyville as pv
    from scan import Scanner
    with tempfile.TemporaryDirectory() as d:
        v = Ville(state_path=os.path.join(d, 'state.json'),
                  token_path=os.path.join(d, 'github.json'))

        def boom():
            raise pv.GitHubError('github 403: API rate limit exceeded', 403)
        v._fetch_repos = boom

        s = Scanner(v, state_path=os.path.join(d, 'scan.json'))
        # A rate wall alone is survivable — the scan re-lists off the page — so
        # to test the failure receipt the fallback has to be gone too.
        import clone as _clone
        real = _clone.Cloner.discover
        _clone.Cloner.discover = lambda self, save=True: (_ for _ in ()).throw(
            pv.GitHubError('github page 1: 429', 429))
        try:
            rec = s.run(register=False)                    # must not raise
        finally:
            _clone.Cloner.discover = real
        assert rec['ok'] is False and '403' in rec['error'], rec
        assert '429' in rec['discover_error'], rec
        st = s.status(cid=False)
        assert st['state'] == 'failed' and not st['up_to_date'], st
        assert '403' in st['error'], st
    return 'a failed scan reports failure instead of aging quietly'


@check
def test_a_rate_walled_scan_finishes_off_the_page():
    """The nightly job runs behind whatever else spent the 60/hr budget. Losing
    the REST list must not lose the scan: it re-lists off the public page and
    still books what changed."""
    import tempfile

    import clone as _clone
    import plinyville as pv
    from scan import Scanner
    with tempfile.TemporaryDirectory() as d:
        v = Ville(state_path=os.path.join(d, 'state.json'),
                  token_path=os.path.join(d, 'github.json'))
        v._save({'repos': [{'name': 'L1B3RT4S', 'pushed_at': '2026-01-01T00:00:00Z'}]})
        v._fetch_repos = lambda: (_ for _ in ()).throw(
            pv.GitHubError('github 403: API rate limit exceeded', 403))
        v.refresh_plinyworld = lambda: {'index.html': 1}

        def fake_discover(self, save=True):        # what the page would have said
            st = v._load()
            st['repos'] = [{'name': 'L1B3RT4S', 'pushed_at': '2026-02-02T00:00:00Z'},
                           {'name': 'NEW-ONE', 'pushed_at': '2026-02-02T00:00:00Z'}]
            v._save(st)
            return {'source': 'github-html', 'count': 2, 'added': ['NEW-ONE']}

        real = _clone.Cloner.discover
        _clone.Cloner.discover = fake_discover
        try:
            s = Scanner(v, state_path=os.path.join(d, 'scan.json'))
            rec = s.run(restock=False, register=False)
        finally:
            _clone.Cloner.discover = real

        assert rec['ok'] and rec['repos_source'] == 'github-html', rec
        assert '403' in rec['rest_error'], rec
        assert rec['added'] == ['NEW-ONE'] and rec['moved'] == ['L1B3RT4S'], rec
        assert s.status(cid=False)['up_to_date'], s.status(cid=False)
    return 'a spent REST budget downgrades the scan, it does not fail it'


@check
def test_cron_line_runs_this_scanner_and_leaves_other_entries_alone():
    """The installed line has to be runnable as-is (absolute interpreter, absolute
    script — cron has no PATH and no cwd) and tagged, so install/remove touches
    only our own entry."""
    from scan import CRON_TAG, Scanner
    s = Scanner()
    line = s.cron_line(hour=4, minute=17)
    assert line.startswith('17 4 * * *'), line
    assert line.endswith(CRON_TAG), line
    words = line.split()
    interp = next(w for w in words if os.path.basename(w).startswith('python'))
    script = next(w for w in words if w.endswith('scan.py'))
    assert os.path.isabs(interp) and os.path.isabs(script), line
    assert os.path.isfile(interp) and os.path.isfile(script), line
    assert '--run' in line and '>>' in line, line

    keep = ['0 0 * * 0 /some/other/job.sh', '17 4 * * * old ' + CRON_TAG]
    assert [ln for ln in keep if CRON_TAG not in ln] == ['0 0 * * 0 /some/other/job.sh']
    return 'one tagged, self-contained crontab line'


@check
def test_status_route_answers_offline():
    """The header pill reads GET /status on every page load; it must answer from
    the receipt on disk, with no GitHub call in the path."""
    j = _get(f'http://127.0.0.1:{API_PORT}/status')
    for k in ('state', 'label', 'up_to_date', 'age', 'interval_hours', 'cron', 'repos'):
        assert k in j, (k, sorted(j))
    assert j['state'] in ('ok', 'stale', 'failed', 'never'), j['state']
    # the app proxies it under its own origin — that is what the page fetches
    a = _get(f'http://127.0.0.1:{APP_PORT}/api/status')
    assert a['state'] == j['state'], (a['state'], j['state'])
    return f"status via api and app: {j['state']}"


@check
def test_status_is_an_mcp_tool_too():
    """An agent asking 'is this current?' should not have to scrape the page."""
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': 'pv_status', 'arguments': {}}})
    body = json.loads(r['result']['content'][0]['text'])
    assert 'up_to_date' in body and 'state' in body, sorted(body)
    return 'pv_status returns the same receipt as GET /status'


# ── the clone archiver (offline: it clones a repo made right here) ──────────


def _git(argv, cwd):
    import subprocess
    p = subprocess.run(['git'] + argv, cwd=cwd, capture_output=True, text=True)
    assert p.returncode == 0, f'git {argv}: {p.stderr}'
    return p.stdout


def _fixture_repo(base, name, files, commit=True):
    """A real git repo on disk, standing in for github.com/<user>/<name>."""
    d = os.path.join(base, 'elder-plinius', name + '.git')
    os.makedirs(d)
    _git(['init', '-q', '-b', 'main'], d)
    _git(['config', 'user.email', 't@t'], d)
    _git(['config', 'user.name', 'test'], d)
    for path, blob in files.items():
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = 'wb' if isinstance(blob, bytes) else 'w'
        with open(full, mode) as f:
            f.write(blob)
    if commit:
        _git(['add', '-A'], d)
        _git(['commit', '-qm', 'one'], d)
    return d


def _cloner(tmp, repos=('DEMO',)):
    """A Cloner pointed at a local git base, a temp store and a temp gallery."""
    import clone as _clone
    import market as _market
    _market.STORE_ROOT = os.path.join(tmp, 'store')
    _clone.GIT_BASE = os.path.join(tmp, 'remote')
    state = os.path.join(tmp, 'state.json')
    with open(state, 'w') as f:
        json.dump({'repos': [{'name': n, 'description': 'demo', 'stars': 1,
                              'topics': [], 'language': 'Python',
                              'url': f'https://github.com/elder-plinius/{n}',
                              'default_branch': 'main'} for n in repos]}, f)
    return _clone.Cloner(ville=Ville(state_path=state),
                         root=os.path.join(tmp, 'clones'))


@check
def test_clone_archiver_fills_the_store_without_calling_the_api():
    """The whole point: an archive built over git, spending no REST budget. The
    mod it produces has to be indistinguishable from a REST-built one — same
    bundle, same offline reads, same manifest."""
    import tempfile
    import clone as _clone
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-clone-')
    store, base = _market.STORE_ROOT, _clone.GIT_BASE
    try:
        _fixture_repo(os.path.join(tmp, 'remote'), 'DEMO', {
            'README.md': '# DEMO\nhello',
            'src/a.py': 'print(42)\n',
            'logo.png': b'\x89PNG\r\n\x1a\n\x00\x01binary',
        })
        c = _cloner(tmp)
        # if it touched GitHub this would explode: there is no token and no need
        c.ville._api = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('the clone archiver must not call the REST API'))

        r = c.archive('DEMO')
        assert r['source'] == 'git-clone' and r['installed']
        assert len(r['head']) == 40, r['head']
        assert r['files_stored'] == 2, r          # the png is tree-only
        mk = c.mkt
        assert mk.is_installed('DEMO')
        assert mk.repo_readme('DEMO')['source'] == 'store'
        assert mk.repo_file('DEMO', 'src/a.py')['text'] == 'print(42)\n'
        assert mk.repo_search('DEMO', 'print')['count'] == 1
        assert {e['path'] for e in mk.content('DEMO')['tree']} == {
            'README.md', 'src/a.py', 'logo.png'}
        assert mk.mod('DEMO')['mcp'].endswith('/m/DEMO/mcp')
        return 'cloned, archived and read back with zero API calls'
    finally:
        _market.STORE_ROOT, _clone.GIT_BASE = store, base


@check
def test_a_second_run_is_free_until_the_repo_moves():
    """Re-archiving is keyed on the commit, so `stock` over 46 repos is cheap to
    re-run and a repo that moved is the only one rebuilt."""
    import tempfile
    import clone as _clone
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-clone-')
    store, base = _market.STORE_ROOT, _clone.GIT_BASE
    try:
        remote = _fixture_repo(os.path.join(tmp, 'remote'), 'DEMO',
                               {'README.md': '# one'})
        c = _cloner(tmp)
        first = c.archive('DEMO')
        again = c.archive('DEMO')
        assert again.get('reused') and again['head'] == first['head']

        with open(os.path.join(remote, 'NEW.md'), 'w') as f:
            f.write('# two')
        _git(['add', '-A'], remote)
        _git(['commit', '-qm', 'two'], remote)

        moved = c.archive('DEMO')
        assert not moved.get('reused') and moved['head'] != first['head']
        assert 'NEW.md' in c.mkt.content('DEMO')['files']
        stale = [x for x in c.clones()['clones'] if x['stale']]
        assert not stale, stale
        return 'unchanged repos are skipped; a moved one is re-archived'
    finally:
        _market.STORE_ROOT, _clone.GIT_BASE = store, base


@check
def test_a_repo_with_no_commits_archives_as_an_empty_mod():
    """Three of his repos have never been committed to. They are still repos, so
    they get a (empty) mod rather than a failed install."""
    import tempfile
    import clone as _clone
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-clone-')
    store, base = _market.STORE_ROOT, _clone.GIT_BASE
    try:
        _fixture_repo(os.path.join(tmp, 'remote'), 'EMPTY', {}, commit=False)
        c = _cloner(tmp, repos=('EMPTY',))
        r = c.archive('EMPTY')
        assert r['installed'] and r['files_stored'] == 0 and r['head'] == 'empty-repo'
        assert c.mkt.repo_tree('EMPTY')['entries'] == []
        assert c.archive('EMPTY').get('reused')      # and it stays quiet after
        return 'an empty repo is an empty mod, not an error'
    finally:
        _market.STORE_ROOT, _clone.GIT_BASE = store, base


@check
def test_discovery_reads_the_repo_page_and_only_ever_adds():
    """The listing is the last thing that needed REST budget. Parsed off the
    public page it must keep repos aligned with their descriptions (a repo with
    none must not steal the next one's) and must never drop what we already knew."""
    import tempfile
    import clone as _clone
    import market as _market
    tmp = tempfile.mkdtemp(prefix='pv-clone-')
    store, base = _market.STORE_ROOT, _clone.GIT_BASE
    page = '''
    <a href="/elder-plinius/L1B3RT4S" itemprop="name codeRepository"> L1B3RT4S </a>
      <p itemprop="description">TOTALLY <em>LEGAL</em> &amp; FREE</p>
      <span itemprop="programmingLanguage">Python</span>
      <a href="/elder-plinius/L1B3RT4S/stargazers" class="x"> 12.4k </a>
      <relative-time datetime="2026-08-01T00:00:00Z"></relative-time>
    <a href="/elder-plinius/NODESC" itemprop="name codeRepository"> NODESC </a>
      <a href="/elder-plinius/NODESC/stargazers" class="x"> 7 </a>
    '''
    try:
        c = _cloner(tmp, repos=('L1B3RT4S',))
        c.ville._save({'repos': [{'name': 'L1B3RT4S', 'topics': ['jailbreak'],
                                  'forks': 9, 'description': 'from the api'}]})

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # _html_list imports urllib.request inside itself; the module object is
        # shared, so patching the function here is what it will call.
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Resp(page.encode())
        # …and the *page* is what this test is about: on a box where the gh CLI
        # happens to be logged in, discover() would take that path instead and
        # never touch the fixture.
        real_ready, c.gh_ready = c.gh_ready, lambda: False
        try:
            found = c.discover()
        finally:
            urllib.request.urlopen = real
            c.gh_ready = real_ready

        assert found['source'] == 'github-html' and found['added'] == ['NODESC']
        rows = {r['name']: r for r in c.ville.repos()['repos']}
        assert rows['L1B3RT4S']['description'] == 'TOTALLY LEGAL & FREE'
        assert rows['L1B3RT4S']['stars'] == 12400
        assert rows['L1B3RT4S']['topics'] == ['jailbreak']   # api-only field kept
        assert rows['L1B3RT4S']['forks'] == 9
        assert rows['NODESC']['description'] == ''           # not the neighbour's
        assert rows['NODESC']['stars'] == 7
        return 'the page listing adds repos and never overwrites what we knew'
    finally:
        _market.STORE_ROOT, _clone.GIT_BASE = store, base


# ── RUN: the repos that are apps ────────────────────────────────────────────

_APP_FILES = {
    'index.html': ('<!doctype html><html><head><title>Demo App</title>'
                   '<link rel="stylesheet" href="style.css"></head><body>'
                   '<h1>demo</h1>\n'
                   "<script>var MARKER='do-not-splice';function f(){"
                   "switch(x){case 'bottom-center': return 1;}}</script>\n"
                   '<script src="app.js"></script></body></html>'),
    'app.js': 'console.log("hi"); localStorage.setItem("k","v");',
    'style.css': 'body{color:red}',
    'assets/pic.png': b'\x89PNG\r\n\x1a\n' + b'0' * 40,
    'docs/index.html': '<!doctype html><html><head><title>Docs</title></head>'
                       '<body>docs</body></html>',
}
_SRC_FILES = {
    'index.html': ('<!doctype html><html><head><title>Vite App</title></head>'
                   '<body><div id="root"></div>'
                   '<script type="module" src="/src/main.tsx"></script></body></html>'),
    'src/main.tsx': 'export default function App(){return null}',
    'package.json': '{"name":"src-app"}',
}
_PY_FILES = {'main.py': 'print("hello")', 'README.md': '# a script'}


def _runner(tmp):
    """A Runner over three fixture repos cloned from a local git base."""
    import run as _run
    cl = _cloner(tmp, repos=('DEMO_APP', 'DEMO_SRC', 'DEMO_PY'))
    for name, files in (('DEMO_APP', _APP_FILES), ('DEMO_SRC', _SRC_FILES),
                        ('DEMO_PY', _PY_FILES)):
        _fixture_repo(os.path.join(tmp, 'remote'), name, files)
        cl.clone(name)
    _run.RUN_INDEX = os.path.join(tmp, 'run.json')
    return _run.Runner(cl.mkt, cl)


@check
def test_run_finds_the_page_and_serves_it_unaltered_but_shimmed():
    """The one edit to upstream is announced: a storage shim in the head and a
    chip at the end. Everything between them has to arrive byte-identical —
    an injection that lands mid-script would break the app it is hosting."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        m = r.manifest('DEMO_APP')
        assert m['runnable'] and m['kind'] == 'web', m
        assert m['entry'] == 'index.html', m['entry']
        assert m['entries'][0]['title'] == 'Demo App'
        page = r.asset('DEMO_APP', 'index.html')['body'].decode()
        assert page.count('data-pliny="shim"') == 1
        assert page.count('data-pliny="chip"') == 1
        # the regression: the chip index must be taken on the *modified* page
        assert "var MARKER='do-not-splice';" in page
        assert "case 'bottom-center': return 1;" in page
        assert page.rstrip().endswith('</html>')
        # a non-HTML asset is passed through untouched, with its own type
        png = r.asset('DEMO_APP', 'assets/pic.png')
        assert png['body'].startswith(b'\x89PNG'), png['body'][:8]
        assert png['ctype'] == 'image/png'
        return f"{m['count']} pages, entry {m['entry']}"


@check
def test_run_responses_are_sandboxed_without_same_origin():
    """The header is the whole protection: one origin is shared by every mod on
    this host, so a run page must never be able to reach it."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        h = _runner(tmp).asset('DEMO_APP', 'index.html')['headers']
        csp = h['Content-Security-Policy']
        assert csp.startswith('sandbox '), csp
        assert 'allow-scripts' in csp
        assert 'allow-same-origin' not in csp, 'that one token undoes the sandbox'
        assert h['X-Content-Type-Options'] == 'nosniff'
        return csp


@check
def test_run_refuses_to_leave_the_checkout():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        for bad in ('../../../../etc/passwd', '.git/config', 'docs/../../etc/hosts'):
            try:
                r.asset('DEMO_APP', bad)
            except (ValueError, FileNotFoundError) as e:
                assert 'escape' in str(e) or 'not served' in str(e) or 'not in' in str(e), e
            else:
                raise AssertionError(f'{bad} was served')
        return 'traversal, .git and absolute paths all refused'


@check
def test_a_directory_redirects_to_its_index():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        assert r.asset('DEMO_APP', 'docs')['redirect'] == 'docs/index.html'
        assert r.asset('DEMO_APP', '')['redirect'] == 'index.html'
        return 'docs -> docs/index.html'


@check
def test_source_that_needs_a_build_is_not_called_runnable():
    """A Vite index.html loads /src/main.tsx, which no browser can compile. It
    renders a blank page, so calling it runnable would be a lie."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        m = r.manifest('DEMO_SRC')
        assert not m['runnable'], m
        assert m['kind'] == 'source' and m['needs_build'], m
        assert 'main.tsx' in m['note'], m['note']
        py = r.manifest('DEMO_PY')
        assert not py['runnable'] and py['kind'] == 'python', py
        return m['note'][:60]


@check
def test_the_audit_says_what_the_page_reaches_for():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        a = _runner(tmp).audit('DEMO_APP')
        assert 'storage' in a['touches'], a
        assert 'app.js' in a['files_scanned'], a
        return ', '.join(a['touches'])


@check
def test_the_pastejacking_poc_is_never_served_runnable():
    """The safety line this whole feature has to hold: the one repo that is a
    live clipboard-hijack does not run from here, it points at the defanged
    exhibit instead."""
    import run as _run
    r = _run.Runner()
    m = r.manifest('elder-plinius.github.io')
    assert m['defanged'] and m['kind'] == 'exhibit', m
    assert m['run_url'].endswith('/plinyworld/'), m['run_url']
    try:
        r.asset('elder-plinius.github.io', 'index.html')
    except _run.Defanged as e:
        assert 'clipboard' in str(e), e
        return m['run_url']
    raise AssertionError('the live payload was served')


@check
def test_run_over_http_keeps_the_sandbox_through_the_app_proxy():
    """The app proxies the bytes; if it drops the CSP on the way the page is
    suddenly on this host's origin. Also: a redirect must come back to the
    browser, not be followed inside the proxy, or every relative path breaks."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        import clone as _clone
        _clone.CLONE_ROOT = r.cloner.root
        state = r.ville.state_path
        old_state, old_url = api.STATE, app.API_URL
        port = _free_port()
        api.STATE = state
        threading.Thread(target=api.serve, args=(port, '127.0.0.1'),
                         daemon=True).start()
        for _ in range(80):
            try:
                _get(f'http://127.0.0.1:{port}/')
                break
            except Exception:                    # noqa: BLE001
                time.sleep(0.05)
        app.API_URL = f'http://127.0.0.1:{port}'
        try:
            base = f'http://127.0.0.1:{APP_PORT}/m/DEMO_APP/run'
            res, body = _get(base + '/index.html', raw=True)
            assert 'sandbox' in res.headers['Content-Security-Policy']
            assert 'allow-same-origin' not in res.headers['Content-Security-Policy']
            assert res.headers['Content-Type'].startswith('text/html')
            assert b'do-not-splice' in body
            png = _get(base + '/assets/pic.png', raw=True)[1]
            assert png.startswith(b'\x89PNG')
            # the redirect is forwarded, and rewritten into the browser's space
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                opener.open(base + '/docs')
            except urllib.error.HTTPError as e:
                assert e.code == 302, e.code
                assert e.headers['Location'] == '/m/DEMO_APP/run/docs/index.html', \
                    e.headers['Location']
            else:
                raise AssertionError('the directory did not redirect')
            man = _get(base)
            assert man['runnable'] and man['entry'] == 'index.html'
            arcade = _get(f'http://127.0.0.1:{port}/run')
            assert any(x['repo'] == 'DEMO_APP' for x in arcade['mods']), arcade
            return f"{arcade['runnable']} runnable over http"
        finally:
            api.STATE, app.API_URL = old_state, old_url


@check
def test_the_market_card_says_whether_a_repo_runs():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        cat = r.join(r.market.catalog())
        by = {m['name']: m for m in cat['mods']}
        assert by['DEMO_APP']['run'] is True, by['DEMO_APP']
        assert by['DEMO_SRC']['run'] is False, by['DEMO_SRC']
        assert cat['runnable'] == 1, cat['runnable']
        return 'run flag on every card'


@check
def test_run_is_an_mcp_tool_on_all_three_servers():
    reg = mcp.tool_list()
    assert 'pv_run' in [t['name'] for t in reg], [t['name'] for t in reg]
    assert 'run' in mcp.REPO_OPS
    per = [t['name'] for t in mcp.tool_list(mcp.repo_tools('ST3GG'))]
    assert 'st3gg_run' in per, per
    return f'pv_run + {len(per)} per-repo tools'


# ── the taxonomy: what sort of thing each repo is ───────────────────────────

def _kinds(tmp):
    """A Kinds over the three fixture repos, with its cache inside tmp."""
    import kinds as _kinds_mod
    r = _runner(tmp)
    k = _kinds_mod.Kinds(r.market, r, index_path=os.path.join(tmp, 'kinds.json'))
    return k, r


@check
def test_types_are_measured_and_show_their_evidence():
    """A type with no evidence is a type nobody should filter on, so every one
    carries the word that produced it and where it was found."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        k, _ = _kinds(tmp)
        cat = k.catalog()
        ids = [t['id'] for t in cat['types']]
        assert ids == ['jailbreak', 'system-prompt', 'redteam', 'app', 'tool',
                       'writing', 'exhibit', 'empty'], ids
        one = k.catalog(repo='DEMO_APP')
        assert 'app' in one['types'], one
        # the run kind is a measurement, and it says so rather than pretending
        # a word in the README put it there
        ev = [w['in'] for w in one['why']['app']]
        assert 'run' in ev or 'files' in ev or 'name' in ev, one['why']
        return f"{len(ids)} types, {cat['repos']} repos classified"


@check
def test_an_unknown_type_is_refused_not_ignored():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        k, _ = _kinds(tmp)
        assert k.parse('app,jailbreak') == {'app', 'jailbreak'}
        try:
            k.parse('jailbrake')
        except ValueError as e:
            assert 'unknown type' in str(e) and 'jailbreak' in str(e), e
            return 'a filter that cannot lie'
        raise AssertionError('a typo silently returned everything')


@check
def test_type_filter_is_and_across_types():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        k, _ = _kinds(tmp)
        names = sorted(k.index())
        apps = k.filter(names, 'app')
        assert 'DEMO_APP' in apps, apps
        assert 'tool' in k.types_of('DEMO_PY'), k.types_of('DEMO_PY')
        both = k.filter(names, 'app,empty')
        assert both == [], both        # AND, not OR: nothing is both
        assert k.filter(names, 'empty') == [], 'no fixture repo is empty'
        return f'{len(apps)} apps of {len(names)}'


@check
def test_the_scope_fence_lives_on_the_tool_not_in_the_prompt():
    """chat.py fences a question by starting the MCP server with
    PLINYVILLE_SCOPE. The refusal has to come from the tool, because a sentence
    in a system prompt is a request and this is not."""
    import importlib
    old = os.environ.get('PLINYVILLE_SCOPE')
    os.environ['PLINYVILLE_SCOPE'] = 'L1B3RT4S'
    try:
        scoped = importlib.reload(mcp)
        try:
            scoped.TOOLS['pv_readme']['handler']({'name': 'ENTHEA'})
        except ValueError as e:
            assert 'out of scope' in str(e), e
        else:
            raise AssertionError('a scoped server read outside its scope')
        # and a filtered-to-nothing list says so, so nobody reports "not found"
        out = {'repos': [{'name': 'ENTHEA'}]}
        kept = scoped._scope_keep(out['repos'], out=out)
        assert kept == [] and out['hidden_by_scope'] == 1, out
        assert 'out of scope' in out['scope_note'], out
        return 'the tool refuses, not the model'
    finally:
        if old is None:
            os.environ.pop('PLINYVILLE_SCOPE', None)
        else:
            os.environ['PLINYVILLE_SCOPE'] = old
        importlib.reload(mcp)


@check
def test_types_filter_every_listing_over_http():
    base = f'http://127.0.0.1:{API_PORT}'
    cat = _get(base + '/types')
    assert cat['types'] and cat['repos'] >= 0, cat
    for route, key in (('/market', 'mods'), ('/run', 'mods'), ('/repos', 'repos')):
        try:
            got = _get(base + route + '?type=app')
        except urllib.error.HTTPError as e:
            raise AssertionError(f'{route}?type=app: {e.code} {e.read()[:300]}')
        assert 'error' not in got, got
        assert got.get('type') in (['app'], 'app', None), got.get('type')
        assert all('app' in (x.get('types') or ['app']) for x in got.get(key) or []), route
    try:
        _get(base + '/repos?type=nope')
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
        assert 'unknown type' in json.loads(e.read())['error']
    else:
        raise AssertionError('an unknown type filtered nothing and said nothing')
    return 'type= on /repos, /market, /run'


# ── the chat: the claude agent, fenced to this corpus ───────────────────────

@check
def test_chat_card_is_an_agent_protocol_card():
    for path in ('/chat', '/.well-known/agent.json'):
        card = _get(f'http://127.0.0.1:{API_PORT}' + path)
        assert card['protocol'] == 'agent/1.0', card
        assert card['name'] == 'pliny-chat'
        assert 'pv_search' in card['agent']['tools'], card['agent']
        # the tools it may call are read-only: nothing that writes is listed
        assert 'pv_install' not in card['agent']['tools'], card['agent']
        assert card['limits']['per_hour_per_ip'] >= 1
    return 'agent/1.0 on two paths'


@check
def test_chat_refuses_before_it_spends_anything():
    """Every refusal (no question, an unknown type, a bad model) has to happen
    before a model session is started — an empty box should not cost money."""
    import chat as _chat
    c = _chat.Chat(None, None, None)
    out = list(c.stream('', owner=True))
    assert out[-1]['type'] == 'error' and out[-1]['status'] == 400, out
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        k, r = _kinds(tmp)
        c = _chat.Chat(r.market, k, r)
        bad = list(c.stream('hello', model='gpt-4', owner=True))
        assert bad[-1]['status'] == 400 and 'model' in bad[-1]['error'], bad
        junk = list(c.stream('hello', types=['jailbrake'], owner=True))
        assert junk[-1]['status'] == 400, junk
        # a scope with nothing in it is a 404, not an unfenced question
        empty = list(c.stream('hello', types=['exhibit'], owner=True))
        assert empty[-1]['status'] in (404, 503), empty
    return 'four refusals, no session started'


@check
def test_chat_scope_lists_the_repos_the_agent_may_read():
    import tempfile
    import chat as _chat
    with tempfile.TemporaryDirectory() as tmp:
        k, r = _kinds(tmp)
        c = _chat.Chat(r.market, k, r)
        sc = c.scope(types=['app'])
        assert 'DEMO_APP' in sc['repos'], sc
        cmd = c._cmd('q', sc, 'sonnet', None)
        cfg = json.loads(cmd[cmd.index('--mcp-config') + 1])
        env = cfg['mcpServers']['pliny']['env']
        assert env['PLINYVILLE_SCOPE'] == ','.join(sc['repos']), env
        # the agent gets this module's tools and nothing else
        assert '--strict-mcp-config' in cmd and cmd[cmd.index('--tools') + 1] == ''
        assert '--restricted' in cmd
        allowed = cmd[cmd.index('--allowedTools') + 1]
        assert allowed.startswith('mcp__pliny__pv_') and 'Bash' not in allowed
        return 'fenced to ' + ','.join(sc['repos'])


@check
def test_chat_rate_limit_counts_per_address():
    import chat as _chat
    old, _chat.RATE_PER_HOUR = _chat.RATE_PER_HOUR, 2
    _chat._HITS.clear()
    try:
        assert _chat._rate('1.2.3.4') == 1
        assert _chat._rate('1.2.3.4') == 0
        assert _chat._rate('1.2.3.4') == -1
        assert _chat._rate('5.6.7.8') == 1        # a different caller is fresh
        return '2/hour, per address'
    finally:
        _chat.RATE_PER_HOUR = old
        _chat._HITS.clear()


# ── does the page that arrives actually work ────────────────────────────────

@check
def test_a_stray_conflict_marker_is_repaired_too():
    """R00TS ships one whole conflict AND a lone `<<<<<<< HEAD`. The paired
    pass leaves the second one behind, and one marker line is still the syntax
    error that kills every button on the page."""
    import run as _run
    src = ('a();\n<<<<<<< HEAD\nkeep();\n=======\ndrop();\n>>>>>>> abc123\n'
           'b();\n<<<<<<< HEAD\nc();\n')
    fixed, n = _run.Runner._deconflict(src, '.js')
    assert n == 2, n
    assert 'keep();' in fixed and 'drop();' not in fixed, fixed
    assert '<<<<<<<' not in fixed and 'c();' in fixed, fixed
    assert fixed.startswith('/* pliny:') and 'stray marker' in fixed, fixed[:120]
    return 'conflict + stray, both announced'


@check
def test_a_script_that_will_not_compile_is_reported_not_hidden():
    """Serving 200 for a file the browser refuses to compile is the expensive
    kind of "it runs": the page paints and every button is dead."""
    import run as _run
    if not _run.NODE:
        return 'no node on this host — skipped'
    ok, why = _run.Runner._parses('function f(a){const a=1;}')
    assert ok is False and 'already been declared' in (why or ''), why
    assert _run.Runner._parses('const a = 1;')[0] is True
    assert _run.Runner._parses('export const a = 1;')[0] is True
    return 'node --check backs the arcade card'


def main():
    global ONLINE
    _boot()
    ONLINE = _online()
    if not ONLINE:
        print('! github unreachable — network-backed checks will skip')
    failed = 0
    for fn in CHECKS:
        try:
            note = fn()
        except Exception as e:                   # noqa: BLE001
            failed += 1
            print(f'FAIL {fn.__name__}: {type(e).__name__}: {e}')
        else:
            print(f'ok   {fn.__name__}{" — " + note if note else ""}')
    print(f'\n{len(CHECKS) - failed}/{len(CHECKS)} passed')
    return 1 if failed else 0


# pytest picks the test_* functions up directly; they need the servers first.
def setup_module(_module=None):
    global ONLINE
    _boot()
    ONLINE = _online()


if __name__ == '__main__':
    sys.exit(main())
