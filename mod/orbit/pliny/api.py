#!/usr/bin/env python3
"""plinyville api — the JSON surface and the MCP endpoint, on one port.

Every route is a thin call into the same `Ville` the MCP tools use, so the app,
the CLI and an agent all get the same answer to the same question. Zero
third-party dependencies — stdlib http.server only.

    python3 api.py [--port 50592]

Routes (also reachable under the gateway's /api/plinyville prefix):

    GET  /                 info (the null call)
    GET  /repos            ?search=&limit=&refresh=1
    GET  /repo             ?name=
    GET  /readme           ?name=
    GET  /tree             ?name=&path=&ref=
    GET  /file             ?name=&path=&ref=
    GET  /search           ?q=&limit=
    GET  /exhibit          what the defanged plinyworld PoC would do
    GET  /payload          the preserved upstream script, as text/plain
    GET  /tools            the MCP tool registry (?all=1 for the ALL server's)
    GET  /run              the arcade: every repo that is an app, not a corpus
    GET  /m/<repo>/run     can this repo run, from which entry, and what it touches
    GET  /m/<repo>/run/…   the app itself, sandboxed (see run.py)
    POST /update           re-pull repos + the plinyworld snapshot (GET works too)
    GET  /status           the daily scan receipt: freshness + the module's CID
    POST /scan             run the daily scan now
    POST /mcp              MCP JSON-RPC 2.0 (Streamable HTTP) — the corpus tools
    POST /mcp/all          the same, plus one tool per elder-plinius repo
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mcp                                              # noqa: E402
from chat import Chat, ChatError                         # noqa: E402
from chat import available as chat_available             # noqa: E402
from clone import CloneError, Cloner                     # noqa: E402
from kinds import TYPE_IDS, Kinds                        # noqa: E402
from market import Market                                # noqa: E402
from plinyville import DESCRIPTION, GitHubError, Ville  # noqa: E402
from run import SANDBOX as RUN_SANDBOX                    # noqa: E402
from run import Defanged, Runner                         # noqa: E402
from scan import Scanner                                 # noqa: E402

PORT = int(os.environ.get('PLINYVILLE_API_PORT', 50592))
BASE = os.environ.get('PLINYVILLE_BASE_PATH', '/pliny')
STATE = os.environ.get('PLINYVILLE_STATE') or None

# Prefixes the gateway may leave on the path, longest first. The module's
# directory and CLI name moved from `plinyville` to `pliny`, but its routes,
# store bundles and served JSON still say plinyville everywhere — including the
# endpoint list this api hands out. Both names have to resolve, or half the
# advertised routes 404.
_ALIASES = tuple(dict.fromkeys([BASE, '/plinyville', '/pliny']))
_PREFIXES = tuple([p for b in _ALIASES for p in (f'/api{b}', f'{b}/api')]
                  + ['/api'] + list(_ALIASES))


def info():
    return {
        'name': 'plinyville',
        'version': mcp.version(),
        'what': DESCRIPTION,
        'user': Ville(STATE).user,
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS),
                'all': {'endpoint': 'POST /mcp/all', 'stdio': 'python3 mcp.py --all',
                        'what': 'the corpus tools plus one tool per elder-plinius '
                                'repo — the whole market on one MCP connection'}},
        'exhibit': {'url': f'{BASE}/plinyworld',
                    'note': 'DEFANGED clipboard-hijack red-team PoC — the served page '
                            'copies nothing. See GET /exhibit or plinyworld/SOURCE.md'},
        'endpoints': {
            'GET /repos': 'the repo gallery — search, limit, refresh',
            'GET /repo': 'name= — one repo, live from GitHub',
            'GET /readme': 'name= — decoded README markdown',
            'GET /tree': 'name=&path=&ref= — list a directory in a repo',
            'GET /file': 'name=&path=&ref= — read one file as text',
            'GET /search': 'q=&limit= — code search across the user',
            'GET /rate': 'the GitHub rate-limit budget left, and how we authenticate',
            'GET /exhibit': 'what the plinyworld PoC does, read from the unrun payload',
            'GET /payload': 'the preserved upstream trigger script, as text/plain',
            'GET /tools': 'the MCP tool registry (?all=1 = every repo as a tool)',
            'GET /types': 'the taxonomy — what sort of thing each repo is '
                          '(jailbreak, system-prompt, redteam, app, tool, writing, '
                          'exhibit, empty) with live counts; ?repo= for one repo\'s '
                          'evidence. Pass ?type= to /repos, /market and /run',
            'GET /chat': 'the chat agent card (also /.well-known/agent.json)',
            'POST /chat': '{question, types?, repo?, model?, session?} — the Claude '
                          'agent reads this corpus through the module\'s own MCP '
                          'tools and answers with the paths it read. `types` fences '
                          'it: a jailbreak-scoped question cannot open anything else',
            'POST /chat/stream': 'the same, as server-sent events — every tool call '
                                 'as it happens',
            'GET /market': 'the MARKET — every repo as its own mod (app+api+mcp)',
            'POST /market/install': 'name= (or name=* for all) — archive a repo into '
                                    'the store as a mod, by cloning it (via=api for '
                                    'the rate-limited REST archiver)',
            'GET /clones': 'the clone cache — what is checked out, at which commit, '
                           'and whether the archive still matches it',
            'GET /discover': 're-list the repos without spending REST budget '
                             '(gh, else the public repositories page)',
            'GET /run': 'the ARCADE — every repo that is a browser app, where it '
                        'starts, and what it reaches for',
            'GET /m/<repo>': 'one mod: manifest + wiring',
            'GET /m/<repo>/run': 'can this repo run in a browser, from which entry, '
                                 'and what its own scripts touch — or why it cannot',
            'GET /m/<repo>/run/<path>': "the repo's own app, served from its clone "
                                        'with an opaque origin (CSP sandbox, no '
                                        'allow-same-origin). Nothing runs on the box',
            'GET /m/<repo>/audit': 'clipboard, hosts, storage, camera, eval, a key '
                                   'left in the source — read it before you run it',
            'GET /m/<repo>/{readme,tree,file,search}': "one mod's api (from the store)",
            'POST /m/<repo>/mcp': "one mod's own MCP server",
            'POST /update': 're-pull repos + the plinyworld upstream snapshot',
            'GET /status': 'the daily scan receipt — fresh or stale, what changed, '
                           "and this module's own CID",
            'POST /scan': 'run the daily scan now (the cron job runs this once a day)',
            'POST /mcp': 'MCP JSON-RPC 2.0 — the corpus tools',
            'POST /mcp/all': 'MCP JSON-RPC 2.0 — every elder-plinius repo as its own tool',
        },
        'market': {'url': f'{BASE}/market', 'note': 'every elder-plinius repo, '
                   'archived into the store mod and served as its own mod'},
        'types': {'url': f'/api{BASE}/types', 'ids': TYPE_IDS,
                  'note': 'every listing takes ?type=; the chat takes the same ids '
                          'and the fence is on the tools, not in the prompt'},
        'chat': {'url': f'/api{BASE}/chat', 'card': f'/api{BASE}/.well-known/agent.json',
                 'agent': 'claude (Claude Code, headless) over this module\'s own '
                          'MCP server — it can only read this corpus',
                 'available': chat_available().get('ok', False)},
        'run': {'url': f'{BASE}/run', 'sandbox': RUN_SANDBOX,
                'note': 'a third of these repos are browser apps, not prose. They run '
                        'under the mod at /m/<repo>/run — sandboxed into an opaque '
                        'origin that cannot reach this host, audited before you press '
                        'play, and never for the pastejacking PoC'},
    }


def _norm(path: str) -> str:
    for pre in _PREFIXES:
        if path == pre:
            return '/'
        if path.startswith(pre + '/'):
            return path[len(pre):] or '/'
    return path or '/'


def _split_mod(p):
    """'/m/L1B3RT4S/tree' -> ('L1B3RT4S', '/tree'); '/m/L1B3RT4S' -> (name, '')."""
    rest = p[len('/m/'):]
    name, _, sub = rest.partition('/')
    return name, ('/' + sub if sub else '')


def _handler():
    ville = Ville(STATE)
    market = Market(ville)
    cloner = Cloner(market)
    scanner = Scanner(ville, market)
    runner = Runner(market, cloner)
    kinds = Kinds(market, runner)
    chat = Chat(market, kinds, runner, ville, state_path=STATE)

    def archive(name, refresh=False, via='clone'):
        """Install one repo. The clone archiver is the default because the REST
        one runs out of budget after a couple of repos; `via=api` opts back in."""
        if str(via) == 'api':
            return market.install(name, refresh=bool(refresh))
        return cloner.archive(name, refresh=bool(refresh))

    class H(BaseHTTPRequestHandler):
        server_version = 'plinyville/' + mcp.version()

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='application/json', headers=None):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, default=str).encode()
                if ctype.startswith('application/json') else body.encode())
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            sent = set()
            for k, v in (headers or {}).items():
                self.send_header(k, v)
                sent.add(k.lower())
            # Send each CORS header ONCE. A run asset carries its own
            # Access-Control-Allow-Origin, and a response with the header twice
            # is *invalid* to a browser ("contains multiple values") — which is
            # exactly the case that matters here, because a sandboxed page has
            # an opaque origin and every `<script type=module>` it loads from
            # its own repo is a CORS request. GLOSSOPETRAE died on this.
            for k, v in (('Access-Control-Allow-Origin', '*'),
                         ('Access-Control-Allow-Headers', '*'),
                         ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')):
                if k.lower() not in sent:
                    self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            p = _norm(u.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                if p in ('/', '/info'):
                    return self._send(200, info())
                if p == '/repos':
                    return self._send(200, self._repos(q))
                if p == '/repo':
                    return self._send(200, ville.repo(q.get('name')))
                if p == '/readme':
                    return self._send(200, ville.readme(q.get('name')))
                if p == '/tree':
                    return self._send(200, ville.tree(q.get('name'), q.get('path') or '',
                                                      q.get('ref')))
                if p == '/file':
                    return self._send(200, ville.file(q.get('name'), q.get('path'),
                                                      q.get('ref')))
                if p == '/search':
                    return self._send(200, ville.search(q.get('q') or q.get('query'),
                                                        n=q.get('limit') or 50))
                if p == '/rate':
                    return self._send(200, ville.budget())
                if p == '/exhibit':
                    return self._send(200, ville.exhibit())
                if p == '/payload':
                    # text/plain, never application/javascript: readable, never runnable.
                    return self._send(200, ville.payload_source(),
                                      'text/plain; charset=utf-8')
                if p == '/tools':
                    reg = mcp.all_tools() if q.get('all') in ('1', 'true') else None
                    return self._send(200, {'tools': mcp.tool_list(reg)})
                if p == '/market':
                    # every card also says whether that repo is a thing you can
                    # run and what sort of thing it is, so the gallery can offer
                    # RUN and the type pills without 47 more calls
                    return self._send(200, self._typed(kinds.join(runner.join(
                        market.catalog(
                            search=q.get('search'),
                            refresh=q.get('refresh') in ('1', 'true')))), q))
                if p in ('/run', '/arcade'):
                    cat = runner.catalog(refresh=q.get('refresh') in ('1', 'true'))
                    return self._send(200, self._typed(cat, q, key='repo',
                                                       count='runnable'))
                if p in ('/types', '/kinds'):
                    # the taxonomy with counts — or one repo's receipts
                    return self._send(200, kinds.catalog(
                        repo=q.get('repo') or q.get('name'),
                        refresh=q.get('refresh') in ('1', 'true')))
                if p in ('/chat', '/.well-known/agent.json', '/card'):
                    return self._send(200, chat.card(BASE))
                if p == '/clones':
                    return self._send(200, cloner.clones())
                if p == '/discover':
                    return self._send(200, cloner.discover())
                if p.startswith('/m/'):
                    return self._mod_get(p, q)
                if p == '/update':
                    return self._send(200, ville.update())
                if p in ('/status', '/scan'):
                    # The freshness receipt the header pill reads: is the mirror
                    # up to date, when does the next daily scan land, and the CID.
                    return self._send(200, scanner.status())
                if p in ('/mcp/all', '/all/mcp'):
                    tl = mcp.tool_list(mcp.all_tools())
                    return self._send(405, {'error': 'POST a JSON-RPC 2.0 message to '
                                            '/mcp/all', 'count': len(tl),
                                            'tools': [t['name'] for t in tl]})
                if p == '/mcp':
                    return self._send(405, {'error': 'POST a JSON-RPC 2.0 message to /mcp',
                                            'all': 'POST /mcp/all — every repo as a tool',
                                            'tools': [t['name'] for t in mcp.tool_list()]})
                return self._send(404, {'error': f'no route {p}',
                                        'routes': list(info()['endpoints'])})
            except GitHubError as e:
                return self._send(502 if not e.status else e.status, {'error': str(e)})
            except ValueError as e:
                return self._send(400, {'error': str(e)})
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        def _typed_repos(self, out, q):
            """The same `?type=` filter, over /repos' own shape."""
            want = kinds.parse(q.get('type') or q.get('types'))
            if not want:
                return out
            keep = set(kinds.filter([r.get('name') for r in out.get('repos') or []],
                                    want))
            out['repos'] = [r for r in out['repos'] if r.get('name') in keep]
            out['count'] = len(out['repos'])
            out['type'] = sorted(want)
            return out

        def _typed(self, cat, q, key='name', count='count'):
            """`?type=` on a listing. Unknown ids raise (400) rather than
            quietly returning everything — a filter that lies is worse than
            no filter."""
            want = kinds.parse(q.get('type') or q.get('types'))
            if not want:
                return cat
            names = kinds.filter([m.get(key) for m in cat.get('mods') or []], want)
            keep = set(names)
            cat['mods'] = [m for m in cat['mods'] if m.get(key) in keep]
            cat[count] = len(cat['mods'])
            cat['type'] = sorted(want)
            return cat

        def _mod_get(self, p, q):
            """/m/<repo>/… — one market mod's api, served from the store archive."""
            name, sub = _split_mod(p)
            try:
                if sub in ('', '/', '/info'):
                    return self._send(200, market.repo_info(name))
                if sub == '/manifest':
                    return self._send(200, market.mod(name))
                if sub == '/readme':
                    return self._send(200, market.repo_readme(name))
                if sub == '/tree':
                    return self._send(200, market.repo_tree(name, q.get('path') or ''))
                if sub == '/file':
                    return self._send(200, market.repo_file(
                        name, q.get('path'), q.get('ref')))
                if sub == '/search':
                    return self._send(200, market.repo_search(
                        name, q.get('q') or q.get('query'), n=q.get('limit') or 50))
                if sub == '/content':
                    return self._send(200, market.content(name))
                if sub == '/tools':
                    return self._send(200, {'tools': mcp.tool_list(mcp.repo_tools(name))})
                if sub == '/mcp':
                    return self._send(405, {'error': 'POST a JSON-RPC 2.0 message to '
                                            f'/m/{name}/mcp',
                                            'tools': [t['name'] for t in
                                                      mcp.tool_list(mcp.repo_tools(name))]})
                if sub == '/run':
                    return self._send(200, runner.manifest(
                        name, clone=q.get('clone') in ('1', 'true')))
                if sub == '/run/' or sub.startswith('/run/'):
                    return self._run_asset(p, name, sub[len('/run/'):])
                if sub in ('/types', '/kinds'):
                    return self._send(200, kinds.catalog(repo=name))
                if sub == '/audit':
                    return self._send(200, runner.audit(name, q.get('entry')))
                if sub == '/install':
                    return self._send(200, archive(name, via=q.get('via') or 'clone'))
                return self._send(404, {'error': f'no route /m/{name}{sub}'})
            except GitHubError as e:
                return self._send(502 if not e.status else e.status, {'error': str(e)})
            except ValueError as e:
                return self._send(400, {'error': str(e)})
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        def _run_asset(self, p, name, rel):
            """One file of a running repo. The interesting header is the CSP:
            it sandboxes the document itself, so the protection holds on a
            direct link and not only inside our iframe."""
            try:
                # percent-decoded here, not in the router: assets have spaces in
                # their names. The escape guard downstream is a realpath prefix
                # check, which an encoded '..' cannot walk past either.
                r = runner.asset(name, urllib.parse.unquote(rel))
            except Defanged as e:
                return self._send(403, {'error': str(e),
                                        'run': runner.manifest(name)})
            except FileNotFoundError as e:
                return self._send(404, {'error': str(e)})
            except (CloneError, GitHubError) as e:
                return self._send(502, {'error': str(e)})
            except ValueError as e:
                return self._send(400, {'error': str(e), 'run': runner.manifest(name)})
            if r.get('redirect'):
                # An absolute path in this server's own space; the app rewrites
                # it back into the browser's space when it proxies.
                loc = p.split('/run')[0] + '/run/' + r['redirect']
                return self._send(302, b'', 'text/plain', {'Location': loc})
            return self._send(200, r['body'], r['ctype'], r['headers'])

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            p = _norm(u.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(n) if n else b''
            # /m/<repo>/mcp — one mod's MCP server; other /m/<repo>/* POSTs = install
            if p.startswith('/m/'):
                name, sub = _split_mod(p)
                if sub == '/mcp':
                    return self._mcp_post(raw, lambda b: mcp.handle_repo(name, b))
                try:
                    body = json.loads(raw.decode('utf-8') or '{}') if raw else {}
                    if sub == '/install':
                        return self._send(200, archive(
                            name, refresh=bool(body.get('refresh')),
                            via=q.get('via') or body.get('via') or 'clone'))
                    if sub in ('', '/', '/uninstall'):
                        return self._send(200, archive(name))
                    return self._send(404, {'error': f'no route POST /m/{name}{sub}'})
                except (GitHubError, ValueError) as e:
                    return self._send(getattr(e, 'status', None) or 400, {'error': str(e)})
                except Exception as e:                   # noqa: BLE001
                    return self._send(500, {'error': f'{type(e).__name__}: {e}'})
            if p == '/market/install':
                try:
                    body = json.loads(raw.decode('utf-8') or '{}') if raw else {}
                    name = q.get('name') or body.get('name')
                    via = q.get('via') or body.get('via') or 'clone'
                    if name == '*' or body.get('all'):
                        if str(via) == 'api':
                            return self._send(200, market.install_all(
                                limit=q.get('limit') or body.get('limit')))
                        return self._send(200, cloner.archive_all(
                            limit=q.get('limit') or body.get('limit'),
                            refresh=bool(body.get('refresh'))))
                    return self._send(200, archive(
                        name, refresh=bool(body.get('refresh')), via=via))
                except (GitHubError, CloneError, ValueError) as e:
                    return self._send(getattr(e, 'status', None) or 400, {'error': str(e)})
                except Exception as e:                   # noqa: BLE001
                    return self._send(500, {'error': f'{type(e).__name__}: {e}'})
            if p in ('/chat', '/ask', '/chat/stream', '/chat/sse'):
                return self._chat(p, raw, q)
            if p in ('/mcp/all', '/all/mcp'):
                return self._mcp_post(raw, mcp.handle_all)
            if p == '/mcp':
                return self._mcp_post(raw, mcp.handle)
            if p == '/update':
                try:
                    return self._send(200, ville.update())
                except GitHubError as e:
                    return self._send(502, {'error': str(e)})
            if p in ('/scan', '/status'):
                # Run the daily scan now. It never raises — a failed scan comes
                # back as a failed receipt, which is what the page then shows.
                rec = scanner.run()
                return self._send(200, {'scan': rec, 'status': scanner.status()})
            return self._send(404, {'error': f'no route POST {p}'})

        # ── the chat: the Claude agent, reading this corpus ──────────────

        def _chat(self, p, raw, q):
            """POST /chat answers; POST /chat/stream narrates.

            Both run the same generator in chat.py — the streaming one just
            hands each step to the browser as it lands, because an agent that
            spends forty seconds reading should be *visibly* reading."""
            try:
                body = json.loads(raw.decode('utf-8') or '{}') if raw else {}
            except json.JSONDecodeError:
                return self._send(400, {'error': 'send JSON: {"question": "…"}'})
            if not isinstance(body, dict):
                return self._send(400, {'error': 'send a JSON object'})
            args = dict(
                question=body.get('question') or body.get('q') or q.get('q') or '',
                types=body.get('types') or body.get('type') or q.get('type'),
                repo=body.get('repo') or q.get('repo'),
                model=body.get('model') or q.get('model'),
                session=body.get('session') or q.get('session'),
                ip=self.client_address[0] if self.client_address else None,
                owner=self._is_owner())
            stream = p.endswith(('/stream', '/sse')) or body.get('stream')
            if not stream:
                try:
                    return self._send(200, chat.ask(**args))
                except ChatError as e:
                    return self._send(e.status, {'error': str(e), **e.extra})
                except ValueError as e:
                    return self._send(400, {'error': str(e)})
            # SSE. One event per step, flushed immediately: a proxy that buffers
            # this turns a live console back into a spinner, hence X-Accel-*.
            self.send_response(200)
            for k, v in (('Content-Type', 'text/event-stream; charset=utf-8'),
                         ('Cache-Control', 'no-cache, no-transform'),
                         ('Connection', 'close'), ('X-Accel-Buffering', 'no'),
                         ('Access-Control-Allow-Origin', '*')):
                self.send_header(k, v)
            self.end_headers()
            try:
                for ev in chat.stream(**args):
                    self.wfile.write(
                        b'data: ' + json.dumps(ev, default=str).encode() + b'\n\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return                      # the reader closed the tab; fine
            except Exception as e:                       # noqa: BLE001
                try:
                    self.wfile.write(b'data: ' + json.dumps(
                        {'type': 'error', 'error': f'{type(e).__name__}: {e}'}
                    ).encode() + b'\n\n')
                except OSError:
                    pass

        def _is_owner(self):
            """The owner's own token lifts the hourly allowance. A missing or
            wrong one is not an error — it is just a guest, with a smaller
            allowance."""
            secret = os.environ.get('PLINYVILLE_CHAT_TOKEN')
            if not secret:
                return False
            got = (self.headers.get('Authorization') or '').strip()
            return got == 'Bearer ' + secret

        def _repos(self, q):
            """The gallery. A refresh is the one read that can hit the 60/hr wall
            (it is a REST list), and the page's own refresh button is what asks
            for it — so when GitHub says 403 the list is re-read off the public
            repositories page instead of coming back as a red error."""
            refresh = q.get('refresh') in ('1', 'true')
            try:
                return self._typed_repos(
                    ville.repos(search=q.get('search'),
                                n=q.get('limit') or 500, refresh=refresh), q)
            except GitHubError as e:
                if not refresh or e.status not in (403, 429):
                    raise
                found = cloner.discover()          # no REST calls
                out = ville.repos(search=q.get('search'), n=q.get('limit') or 500)
                out['refreshed_via'] = found.get('source')
                out['rest_error'] = str(e)
                return self._typed_repos(out, q)

        def _mcp_post(self, raw, handler):
            """Run a JSON-RPC 2.0 request (single or batch) through `handler`."""
            try:
                body = json.loads(raw.decode('utf-8') or '{}')
            except Exception:                            # noqa: BLE001
                return self._send(400, {'jsonrpc': '2.0', 'id': None,
                                        'error': {'code': -32700, 'message': 'parse error'}})
            if isinstance(body, list):                   # JSON-RPC batch
                out = [r for r in (handler(b) for b in body) if r is not None]
                return self._send(200 if out else 202, out or b'', 'application/json')
            resp = handler(body)
            return self._send(200 if resp is not None else 202,
                              resp if resp is not None else b'', 'application/json')

    return H


def serve(port=PORT, host='0.0.0.0'):
    httpd = ThreadingHTTPServer((host, int(port)), _handler())
    print(f'plinyville api on http://{host}:{port}  (mcp: POST /mcp, '
          f'{len(mcp.TOOLS)} tools)', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
