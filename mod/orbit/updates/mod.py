"""
updates — a GitHub commit-feed monitor for the mod repo (and any other repos).

Tracks one or more GitHub repositories and shows their recent commits as an
"updates" feed, flagging which commits are NEW since you last looked. It ships
pre-attached to the mod repo (modc2/mod) and shows its **dev** branch history by
default; `track` any other GitHub repo to fold it into the same feed.

Data source: the GitHub REST API (anonymous, or authenticated via $GITHUB_TOKEN /
$GH_TOKEN for higher rate limits). For the local checkout it falls back to
`git log`, so the mod repo's history works even offline / rate-limited.

State (the watchlist + last-seen markers) lives in ~/.mod/updates/state.json.

CLI:
    m updates                                  # the feed (mod dev history by default)
    m updates/commits                          # mod repo, dev branch, recent commits
    m updates/commits repo=foo/bar branch=main
    m updates/track owner/repo                  # attach another GitHub repo
    m updates/track https://github.com/o/r branch=release
    m updates/untrack owner/repo
    m updates/repos                             # what's being watched
    m updates/poll                              # only NEW commits since last poll
"""
import os
import re
import subprocess
import mod as m

PRIMARY = 'modc2/mod'        # the mod repo
DEFAULT_OWNER = 'modc2'
DEFAULT_BRANCH = 'dev'       # mod repo's default-shown branch
APP_PORT = 50180
GH_RE = re.compile(r'(?:https?://github\.com/|git@github\.com:)?([^/\s]+)/([^/\s]+?)(?:\.git)?/?$')


class Mod:
    description = ('GitHub commit-feed monitor: watch the mod repo (modc2/mod, dev branch) '
                  'and any other repos, and show recent commits flagging new ones')

    def __init__(self, key='updates', state_path=None):
        self.state_path = m.abspath(state_path or '~/.mod/updates/state.json')
        self._toplevel = None

    # --- watchlist state ----------------------------------------------------

    def _load(self) -> dict:
        st = m.get(self.state_path, {})
        if not st.get('repos'):
            st = {'repos': {PRIMARY: {'branch': DEFAULT_BRANCH, 'last_seen': None}}}
            self._save(st)
        return st

    def _save(self, st: dict):
        m.put(self.state_path, st)

    @staticmethod
    def _parse_repo(s: str) -> str:
        """Normalize 'owner/repo', a github URL, or a bare 'repo' to 'owner/repo'."""
        s = (s or PRIMARY).strip()
        if '/' not in s and ':' not in s:
            return f'{DEFAULT_OWNER}/{s}'
        match = GH_RE.match(s)
        if not match:
            raise ValueError(f'cannot parse repo: {s!r} (use owner/repo or a github URL)')
        return f'{match.group(1)}/{match.group(2)}'

    def _branch_of(self, repo: str) -> str:
        """Tracked branch for a repo; else dev for the mod repo, else the repo's
        GitHub default branch, else 'main'."""
        st = self._load()
        if repo in st['repos'] and st['repos'][repo].get('branch'):
            return st['repos'][repo]['branch']
        if repo == PRIMARY:
            return DEFAULT_BRANCH
        try:
            return self._api(f'/repos/{repo}').get('default_branch') or 'main'
        except Exception:
            return 'main'

    # --- github / local git -------------------------------------------------

    def _api(self, path: str, params=None):
        import requests
        headers = {'Accept': 'application/vnd.github+json'}
        tok = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if tok:
            headers['Authorization'] = f'Bearer {tok}'
        r = requests.get(f'https://api.github.com{path}', headers=headers,
                         params=params or {}, timeout=15)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _short(msg: str) -> str:
        return (msg or '').strip().splitlines()[0] if msg else ''

    def _fmt_api(self, c: dict, repo: str, branch: str) -> dict:
        commit = c.get('commit', {})
        author = commit.get('author', {}) or {}
        return {
            'repo': repo, 'branch': branch,
            'sha': c.get('sha', '')[:8], 'full_sha': c.get('sha', ''),
            'author': author.get('name', ''), 'date': author.get('date', ''),
            'message': self._short(commit.get('message', '')),
            'url': c.get('html_url', f'https://github.com/{repo}/commit/{c.get("sha", "")}'),
        }

    @property
    def toplevel(self):
        if self._toplevel is None:
            try:
                self._toplevel = subprocess.run(
                    ['git', 'rev-parse', '--show-toplevel'],
                    cwd=os.path.dirname(__file__), capture_output=True, text=True, timeout=10
                ).stdout.strip()
            except Exception:
                self._toplevel = ''
        return self._toplevel

    def _is_local(self, repo: str) -> bool:
        """True if `repo` is the GitHub remote of the local checkout."""
        if not self.toplevel:
            return False
        try:
            url = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                                 cwd=self.toplevel, capture_output=True, text=True, timeout=10).stdout
            return self._parse_repo(url.strip()) == repo if url.strip() else False
        except Exception:
            return False

    def _local_commits(self, branch: str, n: int):
        sep = '\x1f'
        fmt = sep.join(['%H', '%an', '%aI', '%s'])
        out = subprocess.run(
            ['git', 'log', branch, f'--pretty=format:{fmt}', '-n', str(n)],
            cwd=self.toplevel, capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or 'git log failed')
        rows = []
        repo = self._parse_repo(
            subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=self.toplevel,
                           capture_output=True, text=True).stdout.strip())
        for line in out.stdout.splitlines():
            if not line:
                continue
            sha, an, date, msg = (line.split(sep) + ['', '', '', ''])[:4]
            rows.append({'repo': repo, 'branch': branch, 'sha': sha[:8], 'full_sha': sha,
                         'author': an, 'date': date, 'message': self._short(msg),
                         'url': f'https://github.com/{repo}/commit/{sha}'})
        return rows

    # --- commits / history --------------------------------------------------

    def commits(self, repo=None, branch=None, n=20, prefer_local=False) -> list:
        """Recent commits for a repo+branch. Defaults to the mod repo's dev
        branch. Uses the GitHub API; falls back to local `git log` for the
        checked-out repo (or when prefer_local=True)."""
        repo = self._parse_repo(repo or PRIMARY)
        branch = branch or self._branch_of(repo)
        n = int(n)
        if prefer_local and self._is_local(repo):
            return self._local_commits(branch, n)
        try:
            data = self._api(f'/repos/{repo}/commits', {'sha': branch, 'per_page': n})
            return [self._fmt_api(c, repo, branch) for c in data]
        except Exception:
            if self._is_local(repo):
                return self._local_commits(branch, n)
            raise

    history = commits

    # --- the updates feed ---------------------------------------------------

    def updates(self, n=20, repo=None, branch=None, mark_seen=True) -> dict:
        """The updates feed. With `repo`, shows that repo's history (mod dev by
        default). Otherwise aggregates the latest commits across every tracked
        repo, newest first, flagging commits that are NEW since you last looked
        and advancing each repo's last-seen marker."""
        st = self._load()
        n = int(n)
        if repo is not None:
            targets = [self._parse_repo(repo)]
            br = {targets[0]: branch or self._branch_of(targets[0])}
        else:
            targets = list(st['repos'])
            br = {r: (st['repos'][r].get('branch') or self._branch_of(r)) for r in targets}

        feed, errors, newest = [], {}, {}
        per = max(1, n if len(targets) == 1 else min(n, 15))
        for r in targets:
            try:
                cs = self.commits(r, br[r], per)
            except Exception as e:
                errors[r] = str(e)
                continue
            seen = (st['repos'].get(r) or {}).get('last_seen')
            if cs:
                newest[r] = cs[0]['full_sha']
            for c in cs:
                feed.append(dict(c, new=self._is_new(cs, seen, c)))

        feed.sort(key=lambda c: c.get('date', ''), reverse=True)
        feed = feed[:n]

        if mark_seen:
            for r, sha in newest.items():
                st['repos'].setdefault(r, {'branch': br[r]})['last_seen'] = sha
            self._save(st)

        return {
            'tracking': targets,
            'count': len(feed),
            'new': sum(1 for c in feed if c.get('new')),
            'updates': feed,
            **({'errors': errors} if errors else {}),
        }

    show = updates

    @staticmethod
    def _is_new(commits_list, last_seen, commit) -> bool:
        """A commit is new if it appears before last_seen in the list (or
        last_seen isn't present at all → whole page is new)."""
        if last_seen is None:
            return True
        shas = [c['full_sha'] for c in commits_list]
        if last_seen not in shas:
            return True
        return shas.index(commit['full_sha']) < shas.index(last_seen)

    def poll(self, n=30) -> dict:
        """Return ONLY commits that are new since the last poll across all tracked
        repos, advancing the markers. Handy for a cron/loop monitor."""
        res = self.updates(n=n, mark_seen=True)
        new = [c for c in res['updates'] if c.get('new')]
        return {'new': len(new), 'updates': new,
                **({'errors': res['errors']} if res.get('errors') else {})}

    # --- managing the watchlist ---------------------------------------------

    def track(self, repo, branch=None) -> dict:
        """Attach a GitHub repo to the feed. `repo` may be owner/repo, a github
        URL, or (for modc2) a bare name. Branch defaults to the repo's default
        branch (dev for the mod repo)."""
        repo = self._parse_repo(repo)
        branch = branch or self._branch_of(repo)
        st = self._load()
        st['repos'][repo] = {'branch': branch,
                             'last_seen': (st['repos'].get(repo) or {}).get('last_seen')}
        self._save(st)
        return {'tracked': repo, 'branch': branch, 'repos': list(st['repos'])}

    attach = track

    def untrack(self, repo) -> dict:
        repo = self._parse_repo(repo)
        st = self._load()
        existed = st['repos'].pop(repo, None) is not None
        self._save(st)
        return {'untracked': repo if existed else None, 'repos': list(st['repos'])}

    detach = untrack

    def set_branch(self, repo, branch) -> dict:
        """Change which branch a tracked repo follows."""
        repo = self._parse_repo(repo)
        st = self._load()
        if repo not in st['repos']:
            raise KeyError(f'{repo} is not tracked; track it first')
        st['repos'][repo]['branch'] = branch
        st['repos'][repo]['last_seen'] = None  # re-baseline on branch switch
        self._save(st)
        return {'repo': repo, 'branch': branch}

    def repos(self) -> dict:
        """The watchlist with each repo's branch and latest commit."""
        st = self._load()
        out = {}
        for r, meta in st['repos'].items():
            br = meta.get('branch') or self._branch_of(r)
            try:
                latest = self.commits(r, br, 1)
                latest = latest[0] if latest else None
            except Exception as e:
                latest = {'error': str(e)}
            out[r] = {'branch': br, 'last_seen': meta.get('last_seen'), 'latest': latest}
        return out

    # --- registrar integration ----------------------------------------------

    MODULES_TTL = 90          # seconds to cache the (heavy) registry walk
    _MODULES_CACHE = '~/.mod/updates/modules_cache.json'

    def _registry(self):
        """Lazily bind the registrar (core/registry)."""
        if getattr(self, '_reg', None) is None:
            self._reg = m.mod('registry')()
        return self._reg

    @staticmethod
    def _split_url(url):
        """Pull (app, api) out of a registry url field, which may be a dict
        {api,app}, a bare string, or None."""
        if isinstance(url, dict):
            return url.get('app'), url.get('api')
        if isinstance(url, str) and url:
            return url, None
        return None, None

    def _scan_modules(self) -> list:
        """Walk the registrar once and flatten each module into a launcher row:
        name, owner key, registered?, live app/api URL, gateway path, blurb."""
        reg = self._registry()
        rows = []
        for x in reg.mods(n=500):
            name = x.get('name')
            if not name:
                continue
            app, api = self._split_url(x.get('url'))
            desc = x.get('desc')
            if not desc:                      # registered mods lack desc; pull from config
                try:
                    cfg = m.config(name)
                    desc = cfg.get('description') if isinstance(cfg, dict) else None
                except Exception:
                    desc = None
            rows.append({
                'name': name,
                'key': x.get('key'),
                'registered': bool(x.get('cid')),
                'cid': x.get('cid'),
                'updated': x.get('updated'),
                'app': app, 'api': api,
                'path': '/' + name,            # gateway-relative (modc2.com/<name>)
                'desc': (desc or '').strip()[:160],
            })
        # registered first, then alphabetical
        rows.sort(key=lambda r: (not r['registered'], r['name'].lower()))
        return rows

    def modules(self, search=None, n=300, refresh=False) -> dict:
        """Modules known to the registrar, each with its live URL so the feed
        doubles as a launcher. The registry walk is heavy, so results are cached
        for MODULES_TTL seconds (pass refresh=True to force a re-scan)."""
        cache = m.abspath(self._MODULES_CACHE)
        rows = None
        if not refresh:
            cached = m.get(cache, None)            # dict-wrapped (m.put won't persist bare lists)
            if isinstance(cached, dict) and (m.time() - cached.get('ts', 0)) < self.MODULES_TTL:
                rows = cached.get('rows')
        if rows is None:
            rows = self._scan_modules()
            m.put(cache, {'rows': rows, 'ts': m.time()})
        if search:
            s = search.lower()
            rows = [r for r in rows
                    if s in r['name'].lower() or s in (r.get('desc') or '').lower()]
        return {
            'count': len(rows),
            'registered': sum(1 for r in rows if r.get('registered')),
            'modules': rows[:int(n)],
        }

    def register(self, comment=None) -> dict:
        """Register this module with the registrar (so `updates` shows up in the
        modules directory it serves). Thin pass-through to registry.reg."""
        return self._registry().reg('updates', comment=comment or 'updates feed')

    # --- web app (zero-dep) -------------------------------------------------

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Run the updates web app — a single-port, zero-dependency server that
        serves the feed UI at / and a JSON API at /api/*. background=True spawns
        it as a detached process and returns the URL; False blocks (serve_forever)."""
        port = int(port)
        if background:
            self.kill(port)
            log_dir = m.abspath(f'/tmp/updates')
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            mod_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # .../mod
            env = dict(os.environ)
            env['PYTHONPATH'] = mod_root + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', '-c',
                 f"import mod as m; m.mod('updates')(state_path={self.state_path!r})"
                 f".serve(port={port}, host={host!r}, background=False)"],
                stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            url = f'http://localhost:{port}'
            return {'running': True, 'pid': proc.pid, 'url': url,
                    'api': f'{url}/api/updates', 'log': os.path.join(log_dir, 'app.log')}
        # blocking mode
        from http.server import ThreadingHTTPServer
        handler = self._make_handler()
        httpd = ThreadingHTTPServer((host, port), handler)
        print(f'updates app on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=APP_PORT):
        """Stop a running app (by pid file, then by port)."""
        killed = []
        pid_path = m.abspath('/tmp/updates/app.pid')
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 15)
                killed.append(pid)
            except Exception:
                pass
            try:
                os.remove(pid_path)
            except OSError:
                pass
        try:
            out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{int(port)} 2>/dev/null'],
                                 capture_output=True, text=True).stdout.split()
            for pid in out:
                os.kill(int(pid), 15)
                killed.append(int(pid))
        except Exception:
            pass
        return {'killed': killed}

    # --- background worker (pm2) + gateway wiring ---------------------------

    PM2_NAME = 'updates-app'

    def _repo_root(self):
        # the dir that contains the `mod` package (so `import mod` works)
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    def worker(self, port=APP_PORT, name=None):
        """Run the app as a managed pm2 background worker (auto-restart, survives
        logout). Idempotent: replaces any existing worker of the same name."""
        name = name or self.PM2_NAME
        port = int(port)
        runner = os.path.join(os.path.dirname(__file__), 'run_app.py')
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        env = dict(os.environ, UPDATES_PORT=str(port), UPDATES_STATE=self.state_path)
        r = subprocess.run(
            ['pm2', 'start', runner, '--name', name, '--interpreter', 'python3',
             '--cwd', self._repo_root(), '--time'],
            capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        self._wait_health(port)
        return {'worker': name, 'port': port, 'running': True}

    def stop_worker(self, name=None):
        name = name or self.PM2_NAME
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': name}

    CADDYFILE = '/etc/caddy/Caddyfile'

    def wire(self, port=APP_PORT, caddyfile=None, host_block='modc2.com', reload=True):
        """Add (idempotently) a Caddy route so modc2.com/updates → the app, and
        reload the gateway. Inserts the block at the top of the host block so it
        wins over the catch-all."""
        port = int(port)
        path = caddyfile or self.CADDYFILE
        block = (
            "    @updates_app path /updates /updates/*\n"
            "    handle @updates_app {\n"
            "        uri strip_prefix /updates\n"
            "        reverse_proxy {$PM2_HOST:localhost}:%d\n"
            "    }\n" % port)
        src = m.get_text(path)
        # drop any prior updates block (matcher + its handle{} body)
        src = re.sub(r'[ \t]*@updates_app[^\n]*\n[ \t]*handle @updates_app \{.*?\n[ \t]*\}\n',
                     '', src, flags=re.S)
        anchor = host_block + ' {\n'
        if anchor not in src:
            raise RuntimeError(f'{host_block} block not found in {path}')
        src = src.replace(anchor, anchor + block, 1)
        m.put_text(path, src)
        out = {'wired': f'https://{host_block}/updates', 'caddyfile': path, 'port': port}
        if reload:
            out['reload'] = self._reload_caddy(path)
        return out

    def _reload_caddy(self, path):
        r = subprocess.run(['caddy', 'reload', '--config', path, '--adapter', 'caddyfile'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return 'ok'
        r2 = subprocess.run(['systemctl', 'reload', 'caddy'], capture_output=True, text=True)
        return 'ok (systemctl)' if r2.returncode == 0 else f'FAILED: {r.stderr or r2.stderr}'

    def deploy(self, port=APP_PORT):
        """One shot: start the pm2 background worker AND wire the Caddy route.
        This is what makes modc2.com/updates live and self-healing."""
        self.kill(port)               # clear any ad-hoc foreground server on the port
        worker = self.worker(port=port)
        wired = self.wire(port=port)
        return {'worker': worker, 'gateway': wired,
                'url': wired['wired'], 'local': f'http://localhost:{port}'}

    def _wait_health(self, port, tries=40):
        import time
        import urllib.request
        for _ in range(tries):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/api/info', timeout=1)
                return True
            except Exception:
                time.sleep(0.25)
        return False

    def _make_handler(self):
        import json as _json
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import urlparse, parse_qs
        gov = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else (
                    _json.dumps(body, default=str).encode() if ctype == 'application/json'
                    else body.encode())
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain')

            @staticmethod
            def _norm(p):
                # tolerate the gateway prefix whether or not Caddy strips it
                if p == '/updates' or p.startswith('/updates/'):
                    p = p[len('/updates'):] or '/'
                return p or '/'

            def do_GET(self):
                u = urlparse(self.path)
                u = u._replace(path=self._norm(u.path))
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path in ('/', '/index.html'):
                        return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                    if u.path == '/api/info':
                        return self._send(200, gov.info())
                    if u.path == '/api/updates':
                        return self._send(200, gov.updates(n=int(q.get('n', 30)),
                                                           repo=q.get('repo'), mark_seen=False))
                    if u.path == '/api/poll':
                        return self._send(200, gov.poll(n=int(q.get('n', 50))))
                    if u.path == '/api/repos':
                        return self._send(200, gov.repos())
                    if u.path == '/api/modules':
                        return self._send(200, gov.modules(search=q.get('search'),
                                                           refresh=q.get('refresh') in ('1', 'true')))
                    if u.path == '/api/commits':
                        return self._send(200, gov.commits(repo=q.get('repo'), branch=q.get('branch'),
                                                           n=int(q.get('n', 30))))
                    return self._send(404, {'error': 'not found'})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

            def do_POST(self):
                u = urlparse(self.path)
                u = u._replace(path=self._norm(u.path))
                n = int(self.headers.get('Content-Length', 0) or 0)
                try:
                    body = _json.loads(self.rfile.read(n) or b'{}') if n else {}
                except Exception:
                    body = {}
                try:
                    if u.path == '/api/track':
                        return self._send(200, gov.track(body.get('repo'), body.get('branch')))
                    if u.path == '/api/untrack':
                        return self._send(200, gov.untrack(body.get('repo')))
                    if u.path == '/api/set_branch':
                        return self._send(200, gov.set_branch(body.get('repo'), body.get('branch')))
                    return self._send(404, {'error': 'not found'})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

        return H

    # --- meta ---------------------------------------------------------------

    def forward(self, **kwargs):
        return self.updates(**kwargs) if kwargs else self.updates()

    def info(self) -> dict:
        st = self._load()
        return {
            'name': 'updates',
            'description': self.description,
            'primary': PRIMARY,
            'default_branch': DEFAULT_BRANCH,
            'tracking': list(st['repos']),
            'authenticated': bool(os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')),
            'registrar': 'registry',
            'state': self.state_path,
        }


# --- embedded zero-dependency web UI ----------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>updates · feed + modules</title>
<style>
  :root{
    --bg:#080a0f; --bg2:#0c1018; --panel:#10141e; --panel2:#161c2a; --line:#1f2636;
    --line2:#2a3346; --text:#eef1f7; --muted:#8b94a8; --faint:#5b647a;
    --accent:#5b8cff; --accent2:#7aa2ff; --new:#ffb454; --green:#3fb950; --pink:#ff6b9d;
    --r:14px;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;color:var(--text);
    font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial;
    background:
      radial-gradient(1100px 540px at 12% -8%, rgba(91,140,255,.14), transparent 60%),
      radial-gradient(900px 480px at 96% 0%, rgba(255,107,157,.08), transparent 55%),
      var(--bg);
    background-attachment:fixed}
  a{color:inherit}
  ::selection{background:rgba(91,140,255,.35)}
  /* --- header --- */
  header{position:sticky;top:0;z-index:20;
    background:linear-gradient(180deg,rgba(8,10,15,.92),rgba(8,10,15,.72));
    backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding:13px 22px}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .brand{display:flex;align-items:baseline;gap:9px;font-weight:800;font-size:17px;letter-spacing:.2px}
  .brand .logo{font-size:18px}
  .brand .dot{color:var(--accent)}
  .sub{color:var(--muted);font-size:12px}
  .grow{flex:1}
  /* segmented control */
  .seg{display:inline-flex;background:var(--panel);border:1px solid var(--line);
    border-radius:999px;padding:3px;gap:2px}
  .seg button{all:unset;cursor:pointer;padding:6px 16px;border-radius:999px;font-size:13px;
    font-weight:600;color:var(--muted);transition:.15s;display:flex;align-items:center;gap:7px}
  .seg button .n{font-size:11px;color:var(--faint);background:var(--panel2);
    border-radius:999px;padding:0 7px;line-height:17px}
  .seg button.on{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#fff;
    box-shadow:0 4px 14px rgba(91,140,255,.35)}
  .seg button.on .n{color:#fff;background:rgba(255,255,255,.2)}
  /* inputs */
  input,button.btn,select{font:inherit;color:var(--text);background:var(--panel2);
    border:1px solid var(--line);border-radius:10px;padding:8px 12px;outline:none}
  input{min-width:200px}
  input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,140,255,.15)}
  input::placeholder{color:var(--faint)}
  button.btn{cursor:pointer;font-weight:600;transition:.15s;white-space:nowrap}
  button.btn:hover{border-color:var(--line2);background:var(--line)}
  button.btn.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));
    border-color:transparent;color:#fff;box-shadow:0 4px 14px rgba(91,140,255,.3)}
  button.btn.primary:hover{filter:brightness(1.07)}
  button.btn.ghost{background:transparent}
  /* repo pills */
  .pill{padding:5px 11px;border-radius:999px;border:1px solid var(--line);
    background:var(--panel);color:var(--muted);cursor:pointer;font-size:12px;white-space:nowrap;
    display:inline-flex;align-items:center;gap:6px;transition:.15s}
  .pill:hover{border-color:var(--line2);color:var(--text)}
  .pill.active{color:#fff;border-color:var(--accent);background:rgba(91,140,255,.18)}
  .pill .x{opacity:.5;font-size:11px}
  .pill .x:hover{opacity:1;color:var(--pink)}
  /* layout */
  main{max-width:1080px;margin:0 auto;padding:22px 22px 90px}
  .view{display:none}.view.on{display:block;animation:fade .25s ease}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  /* feed cards */
  .feed{display:flex;flex-direction:column;gap:11px}
  .card{position:relative;background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;
    display:flex;gap:14px;align-items:flex-start;transition:.15s;overflow:hidden}
  .card:hover{border-color:var(--line2);transform:translateY(-1px)}
  .card.new::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
    background:linear-gradient(180deg,var(--new),#ff8a3d)}
  .av{flex:none;width:34px;height:34px;border-radius:9px;display:grid;place-items:center;
    font-weight:800;font-size:13px;color:#fff;background:linear-gradient(135deg,#3a4566,#222a3a)}
  .msg{flex:1;min-width:0}
  .msg .title{font-weight:600;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    text-decoration:none}
  .msg .title:hover{color:var(--accent2)}
  .meta{color:var(--muted);font-size:12px;margin-top:5px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
  .repo{color:var(--accent2);font-weight:600}
  .sha{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--faint)}
  .badge{font-size:10px;font-weight:800;color:#1a1205;background:var(--new);
    border-radius:6px;padding:2px 7px;letter-spacing:.4px}
  /* module grid */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(248px,1fr));gap:13px}
  .mod{display:flex;flex-direction:column;gap:9px;background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r);padding:15px;transition:.15s;min-height:118px}
  .mod:hover{border-color:var(--accent);transform:translateY(-2px);
    box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .mod .top{display:flex;align-items:center;gap:10px}
  .mod .ic{width:30px;height:30px;border-radius:9px;flex:none;display:grid;place-items:center;
    font-weight:800;color:#fff}
  .mod .nm{font-weight:700;font-size:15px;letter-spacing:.2px;flex:1;min-width:0;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mod .d{color:var(--muted);font-size:12px;line-height:1.5;flex:1;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .mod .foot{display:flex;align-items:center;gap:8px;margin-top:auto}
  .chip{font-size:10px;font-weight:700;letter-spacing:.3px;padding:2px 8px;border-radius:999px}
  .chip.reg{color:#062611;background:rgba(63,185,80,.9)}
  .chip.local{color:var(--muted);background:var(--panel2);border:1px solid var(--line)}
  .open{margin-left:auto;font-size:12px;font-weight:700;color:var(--accent2);text-decoration:none;
    display:inline-flex;align-items:center;gap:5px;padding:5px 11px;border-radius:9px;
    border:1px solid var(--line);transition:.15s}
  .open:hover{border-color:var(--accent);background:rgba(91,140,255,.14)}
  .open.off{color:var(--faint);pointer-events:none;border-style:dashed}
  /* misc */
  .empty,.err{color:var(--muted);text-align:center;padding:54px 0}
  .err{color:var(--pink)}
  .count{color:var(--green);font-weight:700}
  .skeleton{height:70px;border-radius:var(--r);
    background:linear-gradient(100deg,var(--panel) 30%,var(--panel2) 50%,var(--panel) 70%);
    background-size:200% 100%;animation:sh 1.2s infinite}
  @keyframes sh{to{background-position:-200% 0}}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><span class="logo">📡</span>updates<span class="dot">.</span></div>
    <div class="seg">
      <button id="tab-feed" class="on" onclick="setView('feed')">Feed <span class="n" id="n-feed">·</span></button>
      <button id="tab-mods" onclick="setView('mods')">Modules <span class="n" id="n-mods">·</span></button>
    </div>
    <span class="sub" id="status">loading…</span>
    <span class="grow"></span>
    <span id="actions"></span>
  </div>
  <div class="row" id="filters" style="margin-top:11px"></div>
</header>
<main>
  <div class="view on" id="view-feed"><div class="feed" id="feed">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  <div class="view" id="view-mods"><div class="grid" id="mods">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
</main>
<script>
const $ = s => document.querySelector(s);
const BASE = location.pathname.replace(/\/+$/,'').replace(/\/index\.html$/,'');
const api = p => BASE + p;
let VIEW='feed', FILTER=null, INFO={}, MODS=null, MODQ='';

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function ago(d){if(!d)return'';const t=new Date(d),s=(Date.now()-t)/1e3;
  if(s<60)return Math.floor(s)+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';if(s<2592000)return Math.floor(s/86400)+'d ago';
  return t.toISOString().slice(0,10)}
function tago(sec){return sec?ago(new Date(sec*1000).toISOString()):''}
// deterministic color from a string
function hue(s){let h=0;for(let i=0;i<(s||'').length;i++)h=(h*31+s.charCodeAt(i))%360;return h}
function grad(s){const h=hue(s);return `linear-gradient(135deg,hsl(${h} 70% 55%),hsl(${(h+40)%360} 70% 45%))`}
function initials(s){return (s||'?').replace(/[^a-z0-9]/gi,'').slice(0,2).toUpperCase()}

function setView(v){
  VIEW=v;
  $('#tab-feed').classList.toggle('on',v==='feed');
  $('#tab-mods').classList.toggle('on',v==='mods');
  $('#view-feed').classList.toggle('on',v==='feed');
  $('#view-mods').classList.toggle('on',v==='mods');
  renderActions();
  if(v==='feed'){$('#filters').style.display='flex';}
  else {$('#filters').style.display='none'; if(MODS===null) loadMods();}
}

function renderActions(){
  const el=$('#actions');
  if(VIEW==='feed'){
    el.innerHTML = `<input id="add" placeholder="track owner/repo or github URL"/>
      <button class="btn" onclick="track()">+ track</button>
      <button class="btn ghost" onclick="markRead()" title="mark all commits seen">mark read</button>
      <button class="btn primary" onclick="loadFeed()">↻</button>`;
    const a=$('#add'); a.addEventListener('keydown',e=>{if(e.key==='Enter')track()});
  } else {
    el.innerHTML = `<input id="msearch" placeholder="filter modules…" value="${esc(MODQ)}"/>
      <button class="btn primary" onclick="loadMods(true)" title="re-scan registrar">↻ rescan</button>`;
    const s=$('#msearch');
    s.addEventListener('input',()=>{MODQ=s.value;renderMods()});
    s.focus();
  }
}

/* ---------------- FEED (github commits) ---------------- */
async function loadFeed(){
  try{
    INFO = await (await fetch(api('/api/info'))).json();
    const r = await fetch(api('/api/updates?n=40'+(FILTER?('&repo='+encodeURIComponent(FILTER)):'')));
    const data = await r.json();
    $('#n-feed').textContent = data.count;
    $('#status').innerHTML = `tracking <b>${data.tracking.length}</b> · ${data.count} commits · `
      + (data.new ? `<span class="count">${data.new} new</span>` : 'up to date')
      + (INFO.authenticated ? '' : ' · <span title="set GITHUB_TOKEN for higher limits">anon</span>');
    renderFilters(data.tracking);
    renderFeed(data.updates, data.errors);
  }catch(e){ $('#feed').innerHTML = `<div class="err">${esc(''+e)}</div>` }
}
function renderFilters(repos){
  const el=$('#filters');
  el.innerHTML = `<span class="pill ${!FILTER?'active':''}" onclick="setFilter(null)">all</span>`+
    repos.map(r=>`<span class="pill ${FILTER===r?'active':''}" onclick="setFilter('${r}')">${esc(r)}
      <span class="x" onclick="event.stopPropagation();untrack('${r}')">✕</span></span>`).join('');
}
function renderFeed(items, errors){
  let html='';
  if(errors) for(const [r,e] of Object.entries(errors))
    html += `<div class="err" style="padding:10px 0">⚠ ${esc(r)}: ${esc(e)}</div>`;
  if(!items||!items.length){ $('#feed').innerHTML = html||'<div class="empty">no commits yet</div>'; return; }
  html += items.map(c=>`
    <div class="card ${c.new?'new':''}">
      <div class="av" style="background:${grad(c.author)}">${initials(c.author)}</div>
      <div class="msg">
        <a class="title" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.message)||'(no message)'}</a>
        <div class="meta">
          <span class="repo">${esc(c.repo)}</span>
          <span>${esc(c.branch||'')}</span>
          <span class="sha">${esc(c.sha)}</span>
          <span>${esc(c.author)}</span>
          <span>${ago(c.date)}</span>
          ${c.new?'<span class="badge">NEW</span>':''}
        </div>
      </div>
    </div>`).join('');
  $('#feed').innerHTML = html;
}
function setFilter(r){ FILTER=r; loadFeed(); }
async function track(){
  const v=$('#add').value.trim(); if(!v) return;
  await fetch(api('/api/track'),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({repo:v})}); $('#add').value=''; loadFeed();
}
async function untrack(r){
  if(!confirm('stop tracking '+r+'?')) return;
  await fetch(api('/api/untrack'),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({repo:r})}); if(FILTER===r)FILTER=null; loadFeed();
}
async function markRead(){ await fetch(api('/api/poll?n=80')); loadFeed(); }

/* ---------------- MODULES (registrar) ---------------- */
async function loadMods(refresh){
  $('#mods').innerHTML='<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  try{
    const r=await fetch(api('/api/modules'+(refresh?'?refresh=1':'')));
    const data=await r.json();
    MODS=data.modules||[];
    $('#n-mods').textContent=data.count;
    renderMods(data);
  }catch(e){ $('#mods').innerHTML=`<div class="err">${esc(''+e)}</div>`; }
}
function renderMods(data){
  if(!MODS){return;}
  const q=MODQ.toLowerCase();
  const rows=MODS.filter(m=>!q||m.name.toLowerCase().includes(q)||(m.desc||'').toLowerCase().includes(q));
  const reg=MODS.filter(m=>m.registered).length;
  if(VIEW==='mods')
    $('#status').innerHTML=`<b>${MODS.length}</b> modules · <span class="count">${reg} registered</span> via registrar`;
  if(!rows.length){ $('#mods').innerHTML='<div class="empty">no modules match</div>'; return; }
  $('#mods').innerHTML = rows.map(m=>{
    const url = m.path; // gateway-relative; works behind modc2.com/<name>
    const live = !!(m.app||m.path);
    return `<div class="mod">
      <div class="top">
        <div class="ic" style="background:${grad(m.name)}">${initials(m.name)}</div>
        <div class="nm">${esc(m.name)}</div>
      </div>
      <div class="d">${esc(m.desc)||'<span style="color:var(--faint)">no description</span>'}</div>
      <div class="foot">
        <span class="chip ${m.registered?'reg':'local'}">${m.registered?'REGISTERED':'LOCAL'}</span>
        ${m.updated?`<span class="sub">${tago(m.updated)}</span>`:''}
        <a class="open ${live?'':'off'}" href="${esc(url)}" target="_blank" rel="noopener">open ↗</a>
      </div>
    </div>`;
  }).join('');
}

/* ---------------- boot ---------------- */
renderActions();
loadFeed();
setInterval(()=>{ if(VIEW==='feed') loadFeed(); }, 60000);
</script>
</body>
</html>
"""
