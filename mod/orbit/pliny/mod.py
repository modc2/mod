"""
plinyville — a mod that mirrors elder-plinius's GitHub and *runs* his web app.

Three surfaces over one core (`plinyville.py`):

  * **api** (:50592) — the JSON mirror: repos, trees, files, READMEs, code search,
    and the exhibit report. Also hosts the **MCP** endpoint at `POST /mcp`.
  * **app** (:50593) — the browser side: the repo gallery, and the hosted
    **plinyworld** exhibit. Holds no data; proxies /api/* to the api.
  * **mcp** (`mcp.py`) — the same tools over stdio for MCP clients that prefer a
    subprocess to an HTTP endpoint, plus the **ALL** server (`POST /mcp/all`,
    `mcp.py --all`): the corpus tools *and* one tool per elder-plinius repo, so
    a client's tool list is the market itself — `pv_l1b3rt4s`, `pv_cl4r1t4s`, …

plinyworld is a fork of the site that renders at elder-plinius.github.io ("Poetic
Echoes"). That page is a red-team proof-of-concept for a clipboard-hijacking
("pastejacking") attack: its innocent-looking links silently overwrite the
visitor's clipboard with a payload plus a typosquatted phishing URL. plinyville
hosts it as an **exhibit, not a weapon** — the served page runs a DEFANGED
trigger script (plinyworld/triggers.defanged.js) that copies nothing and instead
shows, inline, what the live attack would have done. The original payload is kept
verbatim but unrun at plinyworld/upstream/triggers.js, and is only ever served as
text/plain. See plinyworld/SOURCE.md.

The MARKET turns each repo into its own mod: `install` archives a repo's tree,
README and readable files into the **store mod** (content-addressed with a real
localfs CID) and serves it at `/m/<repo>` with its own app, api and MCP server.

CLI:
    m plinyville                       # the repo gallery (JSON)
    m pliny/update                # re-pull repos + the plinyworld upstream
    m pliny/token <github_pat>    # authenticate GitHub: 60/hr → 5,000/hr
    m pliny/rate                  # what is left of the GitHub budget
    m pliny/repos search=prompt   # filter the gallery
    m pliny/repo L1B3RT4S         # one repo's details
    m pliny/tree L1B3RT4S         # walk a repo
    m pliny/file L1B3RT4S ANTHROPIC.mkd
    m pliny/market                # the MARKET — every repo as a mod
    m pliny/install L1B3RT4S      # archive one repo into the store as a mod
    m pliny/clone L1B3RT4S        # clone it over git instead (no rate limit)
    m pliny/stock                 # clone + archive the whole market, in one run
    m pliny/clones                # the clone cache: heads, sizes, what went stale
    m pliny/manifest L1B3RT4S     # one mod's manifest (app/api/mcp wiring)
    m pliny/exhibit               # what the plinyworld PoC actually does
    m pliny/serve                 # run api + app
    m pliny/mcp                   # the MCP server on stdio
    m pliny/mcp_all               # how to connect the ALL server (every repo a tool)
    m pliny/scan                  # one scan now — repos, upstream, restock, CID
    m pliny/scan_status           # up to date or stale, what changed, the CID
    m pliny/cron                  # install the daily scan (17 4 * * *)
    m pliny/deploy                # pm2 workers + Caddy routes + the daily scan
"""
import json
import os
import subprocess
import sys

import mod as m

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: `mod` above must keep resolving to the protocol.
    sys.path.append(HERE)

from clone import Cloner  # noqa: E402
from market import Market  # noqa: E402
from plinyville import DESCRIPTION, GITHUB_USER, VERSION, Ville  # noqa: E402

API_PORT = 50592
APP_PORT = 50593
RUN_DIR = '/tmp/plinyville'


class Mod:
    description = DESCRIPTION

    def __init__(self, key='plinyville', state_path=None):
        self.state_path = m.abspath(state_path or '~/.mod/pliny/state.json')
        self.ville = Ville(state_path=self.state_path)
        self.mkt = Market(self.ville)
        self.cloner = Cloner(self.mkt)

    # ── the mirror (thin passthroughs to the core) ──────────────────────────

    def repos(self, search=None, n=500, refresh=False) -> dict:
        """The cached repo gallery. First call (or refresh=True) pulls from GitHub."""
        return self.ville.repos(search=search, n=n, refresh=refresh)

    def repo(self, name) -> dict:
        """One repo's live details, straight from GitHub."""
        return self.ville.repo(name)

    def readme(self, name) -> dict:
        """A repo's README (decoded markdown)."""
        return self.ville.readme(name)

    def tree(self, name, path='', ref=None) -> dict:
        """List one directory of a repo (path='' is the root)."""
        return self.ville.tree(name, path=path, ref=ref)

    def file(self, name, path, ref=None) -> dict:
        """Read one file out of a repo, as text."""
        return self.ville.file(name, path, ref=ref)

    def search(self, q, n=50) -> dict:
        """Code search across every repo of the mirrored user."""
        return self.ville.search(q, n=n)

    def update(self) -> dict:
        """Re-pull the repo list from GitHub AND refresh the plinyworld upstream."""
        return self.ville.update()

    # ── github auth ─────────────────────────────────────────────────────────

    def token(self, token=None, clear=False) -> dict:
        """Show or set the GitHub token. Anonymous GitHub allows 60 requests/hour
        per IP — `stock` alone blows through that — so a token takes the mirror to
        5,000/hr. `m pliny/token <github_pat>` validates and stores it 0600 at
        ~/.mod/pliny/github.json; a running server picks it up on the next
        call, no restart. With no argument this reports where the current token
        comes from (env, here, or the `git` mod) and what budget is left."""
        if clear:
            return self.ville.clear_token()
        if token:
            return self.ville.set_token(token)
        try:
            return self.ville.budget()
        except Exception as e:          # offline: still say what we'd authenticate with
            tok, where = self.ville._token()
            return {'authenticated': bool(tok), 'auth_source': where,
                    'token_file': self.ville.token_path, 'error': str(e)}

    def rate(self) -> dict:
        """The live GitHub rate-limit budget (checking it is itself free)."""
        return self.ville.budget()

    # ── the market: every repo as its own mod ───────────────────────────────

    def market(self, search=None, refresh=False) -> dict:
        """The plinyville MARKET — every elder-plinius repo as a mod (app+api+mcp)."""
        return self._runner().join(self.mkt.catalog(search=search, refresh=refresh))

    def _runner(self):
        from run import Runner
        return Runner(self.mkt, self.cloner)

    def run(self, name=None, refresh=False) -> dict:
        """The ARCADE — which repos are apps, not prose, and where each starts.

        With a name: whether that repo runs in a browser, its entry points, the
        sandboxed URL that serves it, and an audit of what its own scripts reach
        for. Without one: every repo that runs. A repo that cannot says why —
        source that needs a build, a corpus of prompts, a front end for a
        service that is not in the repo."""
        r = self._runner()
        return r.manifest(name, clone=True) if name else r.catalog(refresh=refresh)

    def build(self, name=None, force=False, forget=False) -> dict:
        """Build a repo that ships as source, so the arcade can run it.

        A few of these are real browser apps whose committed `index.html` is a
        Vite stub the browser cannot compile — the page loads blank and the
        card says "read the source". This runs the repo's own build inside its
        clone (`npm install --ignore-scripts`, then `vite build --base ./` or
        its declared build script) and the arcade then finds the built page and
        serves it out of the same sandbox as everything else.

        With no name: every build receipt, what is one build away, and what
        would still not work if it were built."""
        r = self._runner()
        if not name:
            return r.builds()
        if forget:
            return r.builder.forget(r.ville._safe(name))
        return r.build(name, force=force, wait=True)

    def types(self, repo=None, refresh=False) -> dict:
        """What sort of thing each repo IS — jailbreak set, leaked system prompt,
        red-team tool, browser app, tool, writing, exhibit, empty — with live
        counts. With a repo: why that one landed where it did, word by word.

        The same ids filter every listing (`m pliny/market type=jailbreak`) and
        fence the chat (`m pliny/chat "…" types=jailbreak`)."""
        return self._kinds().catalog(repo=repo, refresh=refresh)

    def _kinds(self):
        from kinds import Kinds
        return Kinds(self.mkt, self._runner())

    def chat(self, question, types=None, repo=None, model=None, session=None,
             quiet=False) -> dict:
        """Ask the corpus a question and let the CLAUDE AGENT read it for you.

        Runs the headless `claude` CLI against this module's own MCP server —
        no Bash, no file tools, no other MCP server, only the read-only pv_*
        tools — and answers with the repo and path it read it in. `types=`
        fences it: the agent's tools then refuse everything outside that type,
        so a jailbreak question cannot open anything else.

            m pliny/chat "which of these jailbreak Claude, and how" types=jailbreak
        """
        from chat import Chat
        c = Chat(self.mkt, self._kinds(), self._runner(), self.ville,
                 state_path=self.state_path)
        out = {}
        for e in c.stream(question, types=types, repo=repo, model=model,
                          session=session, owner=True):
            if e['type'] == 'tool' and not quiet:
                print('· %s %s' % (e['tool'], json.dumps(e['input'])[:110]))
            elif e['type'] == 'error':
                return {'error': e['error'], **{k: v for k, v in e.items()
                                                if k not in ('type', 'error')}}
            elif e['type'] == 'done':
                out = {k: v for k, v in e.items() if k != 'type'}
        return out

    def audit(self, name, entry=None) -> dict:
        """What one repo's page reaches for: clipboard, hosts, storage, camera,
        eval, a key left in the source. A grep, not a verdict — read it before
        you press RUN."""
        return self._runner().audit(name, entry)

    def mods(self, search=None) -> dict:
        """A short list of the market: name → installed? → wiring."""
        cat = self.mkt.catalog(search=search)
        return {'count': cat['count'], 'installed': cat['installed'],
                'mods': [{'name': x['name'], 'installed': x['installed'],
                          'app': x['app'], 'mcp': x['mcp']} for x in cat['mods']]}

    def manifest(self, name) -> dict:
        """One market mod's manifest — its wiring, meta and MCP tool list."""
        return self.mkt.mod(name)

    def install(self, name, refresh=False, via='clone') -> dict:
        """Archive one repo into the store mod as a market mod, then content-address
        it with a real localfs CID and register it through the store mod.

        `via='clone'` (the default) clones the repo and reads it off disk — one
        git request per repo and no REST budget spent. `via='api'` is the old
        path: one API call per file against 60/hr."""
        if str(via) == 'api':
            r = self.mkt.install(name, refresh=bool(refresh))
        else:
            r = self.cloner.archive(name, refresh=bool(refresh))
        r.update(self._pin(name))
        return r

    def clone(self, name=None, refresh=False) -> dict:
        """Clone repo(s) from GitHub and archive them into the store mod.

        This is `stock` by another name — the git path, spelled out. With a name
        it does one repo; with none, all of them. Cloning is what keeps the
        market fillable: `git` traffic is not charged against the 60 requests an
        hour anonymous REST calls get, so the whole corpus lands in one run.
        Clones are cached at ~/.mod/pliny/clones and re-fetched, not
        re-downloaded, on later runs."""
        if name:
            r = self.cloner.archive(name, refresh=bool(refresh))
            r.update(self._pin(name))
            return r
        return self.stock(refresh_each=bool(refresh))

    def clones(self) -> dict:
        """The clone cache: what is checked out, at which commit, how big, and
        whether the store archive still matches that commit."""
        return self.cloner.clones()

    def discover(self) -> dict:
        """Re-list the repos without spending REST calls — `gh repo list` when the
        CLI is logged in, otherwise the public repositories page. `update` does
        the same thing through the API and needs budget; this is what runs when
        there is none left. Results are merged into the gallery, never replace it."""
        return self.cloner.discover()

    def forget_clones(self, name=None) -> dict:
        """Delete the working clones (the archives in the store mod stay)."""
        return self.cloner.forget(name)

    def stock(self, limit=None, refresh_each=False, via='clone', names=None) -> dict:
        """Archive the whole market into the store, then pin every freshly-stored
        mod to localfs. The default `via='clone'` clones each repo over git, which
        no rate limit applies to, so one run takes the market from empty to full;
        `via='api'` uses the REST archiver and stops at the 60/hr wall."""
        if str(via) == 'api':
            out = self.mkt.install_all(limit=limit, skip_installed=not refresh_each)
        else:
            out = self.cloner.archive_all(names=names, limit=limit,
                                          refresh=bool(refresh_each))
        pinned = []
        for d in out.get('done', []):
            if d.get('reused'):
                continue
            try:
                pinned.append({'name': d['name'], **self._pin(d['name'])})
            except Exception as e:                        # noqa: BLE001
                pinned.append({'name': d['name'], 'pin_error': str(e)})
        out['pinned'] = pinned
        return out

    def uninstall(self, name) -> dict:
        """Remove a mod's archive from the store (the mirror is untouched)."""
        return self.mkt.uninstall(name)

    def _pin(self, name):
        """Upgrade a stored mod to a real localfs CID and rewrite the recorded id.
        Lives in scan.py so the nightly restock pins exactly the way an install
        does — one archive, one way of addressing it."""
        from scan import pin
        return pin(self.mkt, name)

    # ── the exhibit ─────────────────────────────────────────────────────────

    def exhibit(self) -> dict:
        """What the plinyworld PoC does — clipboard payload, typosquatted domains,
        mechanism — read out of the preserved-but-unrun upstream script."""
        return self.ville.exhibit()

    def payload(self) -> str:
        """The preserved upstream trigger script, verbatim, as text. Study only."""
        return self.ville.payload_source()

    def plinyworld_html(self) -> str:
        """The served exhibit page: upstream markup, defanged script, banner."""
        return self.ville.plinyworld_html()

    # ── mcp ─────────────────────────────────────────────────────────────────

    def mcp(self, http=False, port=API_PORT, all=False):
        """Run the MCP server. Default is stdio (one JSON-RPC message per line);
        http=True serves Streamable HTTP at POST /mcp — which is what the api
        server already mounts, so this is only for running it standalone.
        all=True serves the ALL registry: every repo as its own tool."""
        import mcp as _mcp
        if http:
            import api
            return api.serve(int(port))
        return _mcp.serve_stdio(all_repos=bool(all))

    def mcp_all(self) -> dict:
        """How to connect the ALL server — one MCP connection, every pliny repo
        as its own tool (pv_<repo>), on top of the corpus-wide pv_* tools."""
        import mcp as _mcp
        reg = _mcp.all_tools()
        repos = [n for n in reg if n not in _mcp.TOOLS]
        return {'server': 'plinyville-all', 'count': len(reg),
                'core': len(_mcp.TOOLS), 'repo_tools': len(repos),
                'http': f'POST http://localhost:{API_PORT}/mcp/all',
                'public': 'https://modc2.com/api/pliny/mcp/all',
                'stdio': f'python3 {os.path.join(HERE, "mcp.py")} --all',
                'claude': ('claude mcp add --transport http plinyville '
                           f'http://localhost:{API_PORT}/mcp/all'),
                'tools': repos}

    def tools(self, all=False) -> dict:
        """The MCP tool registry this module exposes. all=True adds the per-repo
        tools of the ALL server."""
        import mcp as _mcp
        reg = _mcp.all_tools() if all else _mcp.TOOLS
        return {'count': len(reg), 'tools': _mcp.tool_list(reg),
                'stdio': f'python3 {os.path.join(HERE, "mcp.py")}'
                         + (' --all' if all else ''),
                'http': f'POST http://localhost:{API_PORT}/mcp'
                        + ('/all' if all else '')}

    # ── processes ───────────────────────────────────────────────────────────

    SERVICES = (('api', 'api.py', 'PLINYVILLE_API_PORT', API_PORT),
                ('app', 'app.py', 'PLINYVILLE_APP_PORT', APP_PORT))

    def _env(self, api_port, app_port):
        return dict(os.environ,
                    PLINYVILLE_STATE=self.state_path,
                    PLINYVILLE_API_PORT=str(api_port),
                    PLINYVILLE_APP_PORT=str(app_port),
                    PLINYVILLE_API_URL=f'http://127.0.0.1:{api_port}')

    def serve(self, api_port=API_PORT, app_port=APP_PORT, host='0.0.0.0', background=True):
        """Run both servers. background=True detaches and returns the URLs;
        background=False blocks on the app and runs the api in a thread."""
        api_port, app_port = int(api_port), int(app_port)
        if not background:
            import threading
            import api as _api
            import app as _app
            os.environ.update(self._env(api_port, app_port))
            threading.Thread(target=_api.serve, args=(api_port, host), daemon=True).start()
            return _app.serve(app_port, host)
        self.kill(api_port, app_port)
        os.makedirs(RUN_DIR, exist_ok=True)
        env = self._env(api_port, app_port)
        out = {}
        for name, script, port_env, _ in self.SERVICES:
            logf = open(os.path.join(RUN_DIR, f'{name}.log'), 'w')
            proc = subprocess.Popen([sys.executable, os.path.join(HERE, script),
                                     '--port', env[port_env]],
                                    stdout=logf, stderr=subprocess.STDOUT,
                                    env=env, cwd=HERE, start_new_session=True)
            with open(os.path.join(RUN_DIR, f'{name}.pid'), 'w') as f:
                f.write(str(proc.pid))
            out[name] = {'pid': proc.pid, 'port': int(env[port_env]),
                         'log': os.path.join(RUN_DIR, f'{name}.log')}
        healthy = self._wait_health(api_port, app_port)
        return {'running': healthy, **out,
                'urls': {'app': f'http://localhost:{app_port}',
                         'api': f'http://localhost:{api_port}',
                         'mcp': f'http://localhost:{api_port}/mcp',
                         'plinyworld': f'http://localhost:{app_port}/plinyworld'}}

    def kill(self, api_port=API_PORT, app_port=APP_PORT):
        """Stop both servers (by pid file, then by port)."""
        killed = []
        for name, *_ in self.SERVICES:
            pid_path = os.path.join(RUN_DIR, f'{name}.pid')
            if os.path.exists(pid_path):
                try:
                    os.kill(int(open(pid_path).read().strip()), 15)
                    killed.append(name)
                except (OSError, ValueError):
                    pass
                try:
                    os.remove(pid_path)
                except OSError:
                    pass
        for port in (int(api_port), int(app_port)):
            try:
                pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} 2>/dev/null'],
                                      capture_output=True, text=True).stdout.split()
                for pid in pids:
                    os.kill(int(pid), 15)
                    killed.append(int(pid))
            except (OSError, ValueError):
                pass
        return {'killed': killed}

    def status(self, api_port=API_PORT, app_port=APP_PORT) -> dict:
        """Is anything actually listening, and what does it say?"""
        import json
        import urllib.request
        out = {}
        for name, port, path in (('api', int(api_port), '/'),
                                 ('app', int(app_port), '/health')):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=3) as r:
                    body = json.loads(r.read().decode())
                out[name] = {'up': True, 'port': port,
                             'version': body.get('version'), 'api': body.get('api')}
            except Exception as e:                        # noqa: BLE001
                out[name] = {'up': False, 'port': port, 'error': str(e)}
        return out

    # ── pm2 workers + gateway ───────────────────────────────────────────────

    PM2_PREFIX = 'plinyville'

    def _repo_root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

    def worker(self, api_port=API_PORT, app_port=APP_PORT):
        """Run api + app as managed pm2 workers (auto-restart, survives logout)."""
        api_port, app_port = int(api_port), int(app_port)
        env = self._env(api_port, app_port)
        started = []
        for name, script, port_env, _ in self.SERVICES:
            pm2_name = f'{self.PM2_PREFIX}-{name}'
            subprocess.run(['pm2', 'delete', pm2_name], capture_output=True, text=True)
            r = subprocess.run(
                ['pm2', 'start', os.path.join(HERE, script), '--name', pm2_name,
                 '--interpreter', sys.executable, '--cwd', HERE, '--time',
                 '--', '--port', env[port_env]],
                capture_output=True, text=True, env=env)
            if r.returncode != 0:
                raise RuntimeError(f'pm2 start {pm2_name} failed: {r.stderr or r.stdout}')
            started.append({'worker': pm2_name, 'port': int(env[port_env])})
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'workers': started, 'running': self._wait_health(api_port, app_port)}

    def stop_worker(self):
        stopped = []
        for name, *_ in self.SERVICES:
            pm2_name = f'{self.PM2_PREFIX}-{name}'
            subprocess.run(['pm2', 'delete', pm2_name], capture_output=True, text=True)
            stopped.append(pm2_name)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': stopped}

    CADDYFILE = '/etc/caddy/mod_site.caddy'

    def route_diff(self) -> dict:
        """What wiring would change in the gateway, without touching it. The
        include is generated from every module's config.json, so this is the
        honest preview: it can add or drop other modules' routes too."""
        import difflib
        gen = m.mod('caddy')().generate()
        try:
            cur = m.get_text(self.CADDYFILE)
        except Exception:                                 # noqa: BLE001
            cur = ''
        diff = list(difflib.unified_diff(cur.splitlines(), gen.splitlines(),
                                         'current', 'generated', lineterm='', n=0))
        adds = [d[1:].strip() for d in diff if d.startswith('+') and '@' in d and 'path' in d]
        drops = [d[1:].strip() for d in diff if d.startswith('-') and '@' in d and 'path' in d]
        return {'adds': adds, 'drops': drops, 'diff_lines': len(diff), 'diff': diff}

    def wire(self, api_port=API_PORT, app_port=APP_PORT, reload=True):
        """Publish the two routes the fleet convention expects — /api/plinyville
        → the api (prefix stripped), /plinyville → the app — by regenerating the
        gateway include from config.json. Both ports must already be serving:
        the generator only routes live ports. Check route_diff() first; the
        include is fleet-wide, not plinyville's alone."""
        live = self.status(api_port, app_port)
        down = [k for k, v in live.items() if not v['up']]
        if down:
            raise RuntimeError(f'not serving: {", ".join(down)} — run worker/serve first '
                               '(caddy only routes live ports)')
        out = {'wired': 'https://modc2.com/plinyville',
               'api': 'https://modc2.com/api/plinyville',
               'mcp': 'https://modc2.com/api/pliny/mcp',
               'include': self.CADDYFILE}
        out['caddy'] = m.mod('caddy')().apply(reload=reload)
        return out

    def deploy(self, api_port=API_PORT, app_port=APP_PORT, cron=True):
        """One shot: pm2 workers + Caddy routes + the daily scan → modc2.com/plinyville
        live and keeping itself current."""
        self.kill(api_port, app_port)
        worker = self.worker(api_port=api_port, app_port=app_port)
        wired = self.wire(api_port=api_port, app_port=app_port)
        cronned = None
        if cron:
            try:
                cronned = self.cron()
            except Exception as e:                        # noqa: BLE001 — never block a deploy
                cronned = {'installed': False, 'error': str(e)}
        return {'worker': worker, 'gateway': wired, 'url': wired['wired'], 'cron': cronned,
                'local': {'app': f'http://localhost:{app_port}',
                          'api': f'http://localhost:{api_port}',
                          'mcp': f'http://localhost:{api_port}/mcp'}}

    def _wait_health(self, api_port, app_port, tries=40):
        import time
        import urllib.request
        ok = set()
        for _ in range(tries):
            for port, path in ((int(api_port), '/'), (int(app_port), '/health')):
                if port in ok:
                    continue
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=1)
                    ok.add(port)
                except Exception:                         # noqa: BLE001
                    pass
            if len(ok) == 2:
                return True
            time.sleep(0.25)
        return False

    # ── registrar + meta ────────────────────────────────────────────────────

    def register(self, comment=None) -> dict:
        """Register with the registrar so plinyville shows in the modules directory."""
        # by directory name: the module is `pliny` on disk, `plinyville` in
        # every route it serves. The registrar only knows the former.
        return m.mod('registry')().reg(
            os.path.basename(HERE),
            comment=comment or 'elder-plinius mirror + market + arcade + MCP')

    # ── the daily scan ──────────────────────────────────────────────────────

    def _scanner(self):
        from scan import Scanner
        return Scanner(self.ville, self.mkt, pinner=self._pin)

    def scan(self, restock=True, register=True) -> dict:
        """One scan: re-pull the repos, re-pull the plinyworld upstream, re-archive
        any installed mod whose repo moved, and re-mint this module's own CID.
        Leaves a receipt in ~/.mod/pliny/scan.json — the app reads that
        receipt to say 'up to date'. This is what the daily cron job runs."""
        return self._scanner().run(restock=bool(restock), register=bool(register))

    def scan_status(self) -> dict:
        """The last scan's receipt: fresh or stale, what changed, and the CID."""
        return self._scanner().status()

    def cid(self, live=False) -> dict:
        """This module's own content id, as registered. The scan re-mints it, so
        the CID on the page addresses the code that is actually serving."""
        return self._scanner().cid(live=bool(live))

    def cron(self, hour=4, minute=17) -> dict:
        """Install the daily scan in crontab (default 04:17 local). Idempotent —
        one tagged line; every other crontab entry is left alone."""
        return self._scanner().cron(hour=hour, minute=minute)

    def uncron(self) -> dict:
        """Remove the daily scan from crontab. The module keeps serving; it just
        stops refreshing itself, and the header pill goes stale after a day."""
        return self._scanner().uncron()

    def forward(self, **kwargs):
        return self.repos(**kwargs) if kwargs else self.repos()

    def info(self) -> dict:
        import mcp as _mcp
        out = self.ville.info()
        idx = self.mkt._index()
        out.update({
            'version': VERSION,
            'user': GITHUB_USER,
            'ports': {'api': API_PORT, 'app': APP_PORT},
            'urls': {'app': 'https://modc2.com/plinyville',
                     'api': 'https://modc2.com/api/plinyville',
                     'mcp': 'https://modc2.com/api/pliny/mcp',
                     'mcp_all': 'https://modc2.com/api/pliny/mcp/all',
                     'market': 'https://modc2.com/plinyville'},
            'mcp_tools': len(_mcp.TOOLS),
            'mcp_all': 'POST /mcp/all — the same tools plus one per repo '
                       '(m pliny/mcp_all)',
            'market': {'mods_installed': len(idx.get('mods', {})),
                       'store': 'store mod → ~/.mod/store/plinyville',
                       'per_mod': '/pliny/m/<repo> (app+api+mcp)'},
        })
        try:
            st = self.scan_status()
            out['cid'] = st.get('cid')
            out['scan'] = {k: st[k] for k in ('state', 'label', 'up_to_date', 'last_scan',
                                              'last_scan_iso', 'age', 'next_scan_iso',
                                              'interval_hours', 'cron') if k in st}
        except Exception as e:                            # noqa: BLE001
            out['scan'] = {'state': 'unknown', 'error': str(e)}
        return out
