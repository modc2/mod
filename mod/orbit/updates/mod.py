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

            def do_GET(self):
                u = urlparse(self.path)
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
                    if u.path == '/api/commits':
                        return self._send(200, gov.commits(repo=q.get('repo'), branch=q.get('branch'),
                                                           n=int(q.get('n', 30))))
                    return self._send(404, {'error': 'not found'})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

            def do_POST(self):
                u = urlparse(self.path)
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
            'state': self.state_path,
        }


# --- embedded zero-dependency web UI ----------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>updates · commit feed</title>
<style>
  :root{
    --bg:#0b0e14; --panel:#11151f; --panel2:#161b27; --line:#222a3a;
    --text:#e6e9f0; --muted:#8b94a7; --accent:#5b8cff; --new:#ffb454; --green:#3fb950;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
  header{position:sticky;top:0;z-index:5;background:rgba(11,14,20,.85);
    backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:14px 20px}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.3px}
  h1 .dot{color:var(--accent)}
  .sub{color:var(--muted);font-size:12px}
  .grow{flex:1}
  input,button,select{font:inherit;color:var(--text);background:var(--panel2);
    border:1px solid var(--line);border-radius:8px;padding:7px 10px;outline:none}
  input:focus{border-color:var(--accent)}
  button{cursor:pointer;background:var(--panel2)}
  button:hover{border-color:var(--accent)}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
  .pill{padding:4px 10px;border-radius:999px;border:1px solid var(--line);
    background:var(--panel);color:var(--muted);cursor:pointer;font-size:12px;white-space:nowrap}
  .pill.active{color:#fff;border-color:var(--accent);background:rgba(91,140,255,.15)}
  .pill .x{margin-left:6px;opacity:.6}
  .pill .x:hover{opacity:1;color:#ff6b6b}
  main{max-width:920px;margin:0 auto;padding:18px 20px 80px}
  .feed{display:flex;flex-direction:column;gap:10px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    padding:13px 15px;display:flex;gap:13px;align-items:flex-start;transition:border-color .15s}
  .card:hover{border-color:#2c3650}
  .card.new{border-left:3px solid var(--new)}
  .msg{flex:1;min-width:0}
  .msg a{color:var(--text);text-decoration:none;font-weight:600}
  .msg a:hover{color:var(--accent)}
  .meta{color:var(--muted);font-size:12px;margin-top:3px;display:flex;gap:10px;flex-wrap:wrap}
  .repo{color:var(--accent)}
  .sha{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
  .badge{font-size:10px;font-weight:700;color:#000;background:var(--new);
    border-radius:5px;padding:1px 6px;letter-spacing:.5px}
  .empty,.err{color:var(--muted);text-align:center;padding:40px 0}
  .err{color:#ff6b6b}
  .count{color:var(--green);font-weight:600}
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>updates<span class="dot">.</span></h1>
    <span class="sub" id="status">loading…</span>
    <span class="grow"></span>
    <input id="add" placeholder="track owner/repo or github URL" style="width:260px"/>
    <button onclick="track()">+ track</button>
    <button onclick="markRead()" title="mark all as seen">mark read</button>
    <button class="primary" onclick="load()">refresh</button>
  </div>
  <div class="row" id="repos" style="margin-top:10px"></div>
</header>
<main>
  <div class="feed" id="feed"><div class="empty">loading…</div></div>
</main>
<script>
const $ = s => document.querySelector(s);
let FILTER = null;

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function ago(d){if(!d)return'';const t=new Date(d),s=(Date.now()-t)/1e3;
  if(s<60)return Math.floor(s)+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';if(s<2592000)return Math.floor(s/86400)+'d ago';
  return t.toISOString().slice(0,10)}

async function load(){
  try{
    const info = await (await fetch('./api/info')).json();
    const r = await fetch('./api/updates?n=40' + (FILTER?('&repo='+encodeURIComponent(FILTER)):''));
    const data = await r.json();
    $('#status').innerHTML = `tracking ${data.tracking.length} · ${data.count} commits · `
      + (data.new ? `<span class="count">${data.new} new</span>` : 'up to date')
      + (info.authenticated ? '' : ' · <span title="set GITHUB_TOKEN for higher limits">anon</span>');
    renderRepos(data.tracking);
    renderFeed(data.updates, data.errors);
  }catch(e){ $('#feed').innerHTML = `<div class="err">${esc(''+e)}</div>` }
}

function renderRepos(repos){
  const el = $('#repos');
  el.innerHTML = `<span class="pill ${!FILTER?'active':''}" onclick="setFilter(null)">all</span>` +
    repos.map(r=>`<span class="pill ${FILTER===r?'active':''}" onclick="setFilter('${r}')">${esc(r)}
      <span class="x" onclick="event.stopPropagation();untrack('${r}')">✕</span></span>`).join('');
}

function renderFeed(items, errors){
  let html = '';
  if(errors) for(const [r,e] of Object.entries(errors))
    html += `<div class="err">⚠ ${esc(r)}: ${esc(e)}</div>`;
  if(!items || !items.length){ $('#feed').innerHTML = html || '<div class="empty">no commits</div>'; return; }
  html += items.map(c=>`
    <div class="card ${c.new?'new':''}">
      <div class="msg">
        <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.message)||'(no message)'}</a>
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

function setFilter(r){ FILTER = r; load(); }
async function track(){
  const v = $('#add').value.trim(); if(!v) return;
  await fetch('./api/track',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({repo:v})}); $('#add').value=''; load();
}
async function untrack(r){
  if(!confirm('stop tracking '+r+'?')) return;
  await fetch('./api/untrack',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({repo:r})}); if(FILTER===r)FILTER=null; load();
}
async function markRead(){ await fetch('./api/poll?n=80'); load(); }
$('#add').addEventListener('keydown',e=>{if(e.key==='Enter')track()});
load();
setInterval(load, 60000);
</script>
</body>
</html>
"""
