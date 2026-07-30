"""
git — repo change tracker for the mod protocol.

Tracks EVERY change in the mod repo (working tree + staged + untracked, with
per-file add/delete counts, branch, ahead/behind, and full diffs) and any other
repo you point it at — a local path or a GitHub repo (cloned on track).

Connect a GitHub account (a personal access token, stored off-chain in
~/.mod/git/github.json) to raise API limits, list/track your private repos and
push/pull over https. Write endpoints are gated by the shared auth module
(m.mod('auth') signed tokens): the owner can grant/revoke write or admin access
per address, all managed from the app's ACCESS tab or the CLI.

Mod protocol: null call returns info; the app + JSON API share one port
(50330) and tolerate the gateway prefix, so caddy routes /{git} (app) and
/api/git (API) from config.json.

CLI:
    m git                                  # info (what's tracked, who has access)
    m git/changes                          # ALL changes in the mod repo
    m git/changes repo=agent diff=1        # another tracked repo, with full diff
    m git/commits n=20                     # recent commits
    m git/track ~/some/checkout            # track a local repo
    m git/track owner/repo                 # clone + track a GitHub repo
    m git/connect <github_pat>             # connect your GitHub
    m git/github_repos                     # your GitHub repos (private too)
    m git/grant 0xADDR role=write          # manage access
    m git/token                            # mint a signed token for the app/API
    m git/serve                            # run the app on :50330
"""
import io
import json
import os
import re
import subprocess
import time
import contextlib
import mod as m

APP_PORT = 50330
MOD_REPO = 'mod'                        # name of the always-tracked primary repo
STATE = '~/.mod/git/state.json'         # tracked repos
GITHUB = '~/.mod/git/github.json'       # connected github account (secret, 0600)
ACCESS = '~/.mod/git/access.json'       # owner + per-address grants
CLONES = '~/.mod/git/repos'             # where tracked github repos get cloned
GH_RE = re.compile(r'(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')
MAX_DIFF = 200_000                      # chars of diff returned per request
TOKEN_TTL = 3600                        # seconds a signed token stays valid


class Mod:
    description = ('git — tracks all changes (status, diffs, commits) in the mod repo and any '
                   'other local or GitHub repos; connect a GitHub account and manage per-address '
                   'access with signed-token grants')

    def __init__(self, path: str = None):
        self.state_path = m.abspath(STATE)
        self.github_path = m.abspath(GITHUB)
        self.access_path = m.abspath(ACCESS)
        self.clones = m.abspath(CLONES)
        self._path = m.abspath(path) if path else None

    # --- plumbing -----------------------------------------------------------

    def _run(self, args, cwd, timeout=60, check=True):
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or f'{args[0]} failed').strip()[:500])
        return r.stdout

    @property
    def mod_root(self):
        """Toplevel of the repo this module lives in — the mod repo."""
        if getattr(self, '_mod_root', None) is None:
            self._mod_root = self._run(['git', 'rev-parse', '--show-toplevel'],
                                       cwd=os.path.dirname(os.path.abspath(__file__))).strip()
        return self._mod_root

    def _load(self) -> dict:
        st = m.get(self.state_path, {})
        if not isinstance(st, dict) or not st.get('repos'):
            st = {'repos': {MOD_REPO: {'path': self.mod_root, 'url': None}}}
            self._save(st)
        return st

    def _save(self, st: dict):
        m.put(self.state_path, st)

    def _repo(self, repo=None):
        """Resolve a repo name or path to (name, abspath)."""
        st = self._load()
        if repo is None:
            repo = MOD_REPO if self._path is None else self._path
        if isinstance(repo, str) and repo in st['repos']:
            return repo, st['repos'][repo]['path']
        path = m.abspath(str(repo))
        if os.path.isdir(os.path.join(path, '.git')):
            return os.path.basename(path.rstrip('/')), path
        raise KeyError(f'{repo!r} is not a tracked repo or local checkout '
                       f"(tracked: {list(st['repos'])})")

    def is_repo(self, path: str = None) -> bool:
        path = m.abspath(path or self.mod_root)
        return os.path.isdir(os.path.join(path, '.git'))

    # --- change tracking ----------------------------------------------------

    _STATUS = {'M': 'modified', 'A': 'added', 'D': 'deleted', 'R': 'renamed',
               'C': 'copied', 'U': 'conflict', '?': 'untracked', '!': 'ignored'}

    def changes(self, repo=None, diff=False, n=500) -> dict:
        """ALL changes in a repo (the mod repo by default): every staged,
        modified, deleted, renamed and untracked file with add/delete line
        counts, plus branch, HEAD and ahead/behind. diff=True includes the
        full unified diff (truncated at MAX_DIFF chars)."""
        name, path = self._repo(repo)
        branch = self._run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=path).strip()
        commit = self._run(['git', 'rev-parse', '--short', 'HEAD'], cwd=path).strip()

        # every path git considers changed, staged or not
        files, counts = [], {}
        porcelain = self._run(['git', 'status', '--porcelain'], cwd=path)
        # line counts for tracked changes (worktree+index vs HEAD)
        numstat = self._run(['git', 'diff', 'HEAD', '--numstat'], cwd=path, check=False)
        stats = {}
        for line in numstat.splitlines():
            parts = line.split('\t')
            if len(parts) == 3:
                a, d, f = parts
                stats[f] = {'additions': None if a == '-' else int(a),
                            'deletions': None if d == '-' else int(d)}
        for line in porcelain.splitlines():
            if len(line) < 4:
                continue
            x, y, rest = line[0], line[1], line[3:]
            f = rest.split(' -> ')[-1].strip().strip('"')
            status = self._STATUS.get((x if x not in ' ?' else y), 'modified')
            if x == '?' or y == '?':
                status = 'untracked'
            row = {'file': f, 'status': status, 'staged': x not in ' ?!',
                   **stats.get(f, {'additions': None, 'deletions': None})}
            files.append(row)
            counts[status] = counts.get(status, 0) + 1
        files = files[:int(n)]

        ahead = behind = None
        try:
            lr = self._run(['git', 'rev-list', '--left-right', '--count', '@{upstream}...HEAD'],
                           cwd=path).split()
            behind, ahead = int(lr[0]), int(lr[1])
        except Exception:
            pass

        out = {
            'repo': name, 'path': path, 'branch': branch, 'commit': commit,
            'ahead': ahead, 'behind': behind,
            'clean': not files,
            'total': len(files), 'counts': counts,
            'additions': sum(s['additions'] or 0 for s in stats.values()),
            'deletions': sum(s['deletions'] or 0 for s in stats.values()),
            'files': files,
        }
        if diff:
            out['diff'] = self.diff(repo)['diff']
        return out

    status = changes

    def diff(self, repo=None, file=None, staged=False) -> dict:
        """Unified diff for a repo (vs HEAD by default; staged=True for the
        index only), optionally for one file. Truncated at MAX_DIFF chars."""
        name, path = self._repo(repo)
        args = ['git', 'diff']
        args += ['--cached'] if staged else ['HEAD']
        if file:
            args += ['--', str(file)]
        text = self._run(args, cwd=path, check=False)
        if file and not text:  # untracked file → show it as all-new
            fp = os.path.join(path, str(file))
            if os.path.isfile(fp):
                text = self._run(['git', 'diff', '--no-index', '/dev/null', str(file)],
                                 cwd=path, check=False)
        truncated = len(text) > MAX_DIFF
        return {'repo': name, 'file': file, 'truncated': truncated,
                'diff': text[:MAX_DIFF]}

    def commits(self, repo=None, n=20, branch=None) -> list:
        """Recent commits as [{hash, author, date, message, additions, deletions}]."""
        name, path = self._repo(repo)
        sep = '\x1f'
        fmt = sep.join(['%H', '%an', '%aI', '%s'])
        args = ['git', 'log', f'--pretty=format:{fmt}', '--shortstat', '-n', str(int(n))]
        if branch:
            args.insert(2, branch)
        out = self._run(args, cwd=path)
        rows, cur = [], None
        for line in out.splitlines():
            if sep in line:
                sha, an, date, msg = (line.split(sep) + [''] * 4)[:4]
                cur = {'hash': sha[:10], 'full_hash': sha, 'author': an, 'date': date,
                       'message': msg, 'additions': 0, 'deletions': 0, 'repo': name}
                rows.append(cur)
            elif cur and ('insertion' in line or 'deletion' in line or 'changed' in line):
                for num, kind in re.findall(r'(\d+) (insertion|deletion)', line):
                    cur['additions' if kind == 'insertion' else 'deletions'] = int(num)
        return rows

    history = commits

    def branches(self, repo=None) -> dict:
        name, path = self._repo(repo)
        out = self._run(['git', 'branch', '-a'], cwd=path)
        current = self._run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=path).strip()
        bs = [b.replace('*', '').strip() for b in out.splitlines() if b.strip()]
        return {'repo': name, 'current': current, 'branches': bs}

    def branch(self, repo=None) -> str:
        return self.branches(repo)['current']

    def hash(self, repo=None) -> str:
        _, path = self._repo(repo)
        return self._run(['git', 'rev-parse', 'HEAD'], cwd=path).strip()

    def repo_url(self, repo=None) -> str:
        _, path = self._repo(repo)
        out = self._run(['git', 'remote', 'get-url', 'origin'], cwd=path, check=False).strip()
        return out or None

    def pull(self, repo=None) -> dict:
        name, path = self._repo(repo)
        return {'repo': name, 'pull': self._run(self._authed(['git', 'pull'], path),
                                                cwd=path, timeout=300).strip()}

    def push(self, repo=None, msg: str = 'update') -> dict:
        """Stage everything, commit, push. (Write-gated over the API.)"""
        name, path = self._repo(repo)
        self._run(['git', 'add', '-A'], cwd=path)
        commit = self._run(['git', 'commit', '-m', msg], cwd=path, check=False).strip()
        pushed = self._run(self._authed(['git', 'push'], path), cwd=path,
                           timeout=300, check=False).strip()
        return {'repo': name, 'commit': commit, 'push': pushed}

    def _authed(self, args, path):
        """Inject the connected PAT into https github remotes for push/pull."""
        tok = self._github_token()
        url = self._run(['git', 'remote', 'get-url', 'origin'], cwd=path, check=False).strip()
        mm = GH_RE.match(url) if url.startswith('https://') else None
        if tok and mm:
            return args + [f'https://x-access-token:{tok}@github.com/{mm.group(1)}/{mm.group(2)}.git']
        return args

    # --- multi-repo watchlist ----------------------------------------------

    def repos(self) -> dict:
        """Every tracked repo with a one-line change summary."""
        st = self._load()
        out = {}
        for name, meta in st['repos'].items():
            try:
                ch = self.changes(name)
                out[name] = {'path': meta['path'], 'url': meta.get('url') or self.repo_url(name),
                             'branch': ch['branch'], 'commit': ch['commit'],
                             'clean': ch['clean'], 'changes': ch['total'],
                             'ahead': ch['ahead'], 'behind': ch['behind']}
            except Exception as e:
                out[name] = {'path': meta.get('path'), 'error': str(e)}
        return out

    def track(self, repo: str, name: str = None, branch: str = None) -> dict:
        """Track another repo. `repo` may be a local path, owner/repo, or a
        GitHub URL — GitHub repos are cloned into ~/.mod/git/repos (using the
        connected token, so private repos work)."""
        repo = str(repo).strip()
        st = self._load()
        local = m.abspath(repo)
        if os.path.isdir(os.path.join(local, '.git')):
            name = name or os.path.basename(local.rstrip('/'))
            st['repos'][name] = {'path': local, 'url': None}
        else:
            mm = GH_RE.match(repo)
            if not mm:
                raise ValueError(f'{repo!r} is neither a local repo path nor a GitHub repo')
            owner, rname = mm.group(1), mm.group(2)
            name = name or rname
            dest = os.path.join(self.clones, name)
            url = f'https://github.com/{owner}/{rname}.git'
            if not os.path.isdir(os.path.join(dest, '.git')):
                os.makedirs(self.clones, exist_ok=True)
                tok = self._github_token()
                clone_url = (f'https://x-access-token:{tok}@github.com/{owner}/{rname}.git'
                             if tok else url)
                args = ['git', 'clone', '--depth', '50']
                if branch:
                    args += ['-b', branch]
                self._run(args + [clone_url, dest], cwd=self.clones, timeout=600)
                self._run(['git', 'remote', 'set-url', 'origin', url], cwd=dest)
            st['repos'][name] = {'path': dest, 'url': f'https://github.com/{owner}/{rname}'}
        self._save(st)
        return {'tracked': name, **st['repos'][name], 'repos': list(st['repos'])}

    attach = track

    def untrack(self, name: str) -> dict:
        """Stop tracking a repo (never deletes anything on disk, and the mod
        repo itself cannot be untracked)."""
        if name == MOD_REPO:
            raise ValueError('the mod repo is always tracked')
        st = self._load()
        existed = st['repos'].pop(str(name), None)
        self._save(st)
        return {'untracked': name if existed else None, 'repos': list(st['repos'])}

    detach = untrack

    # --- github connection --------------------------------------------------

    def _github_token(self):
        gh = m.get(self.github_path, {})
        return (gh or {}).get('token') or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')

    def _gh_api(self, path, token=None, params=None):
        import requests
        headers = {'Accept': 'application/vnd.github+json'}
        tok = token or self._github_token()
        if tok:
            headers['Authorization'] = f'Bearer {tok}'
        r = requests.get(f'https://api.github.com{path}', headers=headers,
                         params=params or {}, timeout=15)
        r.raise_for_status()
        return r.json(), r.headers

    def connect(self, token: str) -> dict:
        """Connect a GitHub account with a personal access token. The token is
        validated against api.github.com and stored off-chain in
        ~/.mod/git/github.json (0600) — it is never returned by the API."""
        token = str(token).strip()
        user, headers = self._gh_api('/user', token=token)
        rec = {'token': token, 'login': user.get('login'), 'name': user.get('name'),
               'scopes': (headers.get('X-OAuth-Scopes') or '').strip(),
               'connected_at': int(time.time())}
        os.makedirs(os.path.dirname(self.github_path), exist_ok=True)
        with open(self.github_path, 'w') as f:
            json.dump(rec, f)
        os.chmod(self.github_path, 0o600)
        return self.github()

    def disconnect(self) -> dict:
        if os.path.exists(self.github_path):
            os.remove(self.github_path)
        return {'connected': False}

    def github(self) -> dict:
        """GitHub connection status: who's connected, token scopes and the
        current API rate limit. Never exposes the token itself."""
        gh = m.get(self.github_path, {}) or {}
        tok = gh.get('token')
        out = {'connected': bool(tok),
               'env_token': bool(os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN'))}
        if tok:
            out.update({'login': gh.get('login'), 'name': gh.get('name'),
                        'scopes': gh.get('scopes'), 'connected_at': gh.get('connected_at'),
                        'token_tail': '…' + tok[-4:]})
        try:
            rate, _ = self._gh_api('/rate_limit')
            core = rate.get('resources', {}).get('core', {})
            out['rate'] = {'remaining': core.get('remaining'), 'limit': core.get('limit')}
        except Exception as e:
            out['rate'] = {'error': str(e)[:120]}
        return out

    def github_repos(self, n=100, search=None) -> list:
        """The connected account's repos (private included), newest-push first."""
        if not self._github_token():
            raise PermissionError('no GitHub account connected — run m git/connect <token>')
        data, _ = self._gh_api('/user/repos', params={'per_page': min(int(n), 100),
                                                      'sort': 'pushed'})
        st = self._load()
        tracked_urls = {(v.get('url') or '') for v in st['repos'].values()}
        rows = [{'repo': r['full_name'], 'private': r['private'],
                 'default_branch': r.get('default_branch'), 'pushed_at': r.get('pushed_at'),
                 'url': r.get('html_url'), 'tracked': r.get('html_url') in tracked_urls,
                 'description': (r.get('description') or '')[:140]} for r in data]
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r['repo'].lower() or s in r['description'].lower()]
        return rows

    # --- access management --------------------------------------------------

    ROLES = ('write', 'admin')

    def _acl(self) -> dict:
        acl = m.get(self.access_path, {}) or {}
        if not acl.get('owner'):
            acl = {'owner': m.key().address, 'grants': acl.get('grants', {})}
            m.put(self.access_path, acl)
        acl.setdefault('grants', {})
        return acl

    def access(self) -> dict:
        """Who can do what: the owner plus every granted address/role. Reads
        are open; write ops need role write+, github/access management needs
        admin+ (the owner is always admin)."""
        acl = self._acl()
        return {'owner': acl['owner'], 'grants': acl['grants'],
                'roles': {'write': ['track', 'untrack', 'pull', 'push'],
                          'admin': ['connect', 'disconnect', 'grant', 'revoke']},
                'auth': "signed token from m.mod('auth') — mint one with `m git/token`",
                'open': bool(os.environ.get('GIT_ACCESS_OPEN'))}

    def grant(self, address: str, role: str = 'write') -> dict:
        """Grant an address write or admin access."""
        if role not in self.ROLES:
            raise ValueError(f'role must be one of {self.ROLES}')
        acl = self._acl()
        acl['grants'][str(address)] = {'role': role, 'granted_at': int(time.time())}
        m.put(self.access_path, acl)
        return self.access()

    def revoke(self, address: str) -> dict:
        acl = self._acl()
        acl['grants'].pop(str(address), None)
        m.put(self.access_path, acl)
        return self.access()

    def set_owner(self, address: str) -> dict:
        """Hand the module to another address (CLI/local only — not exposed
        over the HTTP API)."""
        acl = self._acl()
        acl['owner'] = str(address)
        m.put(self.access_path, acl)
        return self.access()

    def token(self, data: dict = None) -> str:
        """Mint a signed auth token for this box's key — paste it into the app
        (ACCESS tab) or send it as `Authorization: Bearer <token>`."""
        with contextlib.redirect_stdout(io.StringIO()):
            return m.mod('auth')().token(data or {'mod': 'git'})

    def _role_of(self, address: str):
        acl = self._acl()
        if address == acl['owner']:
            return 'owner'
        return (acl['grants'].get(address) or {}).get('role')

    def _authorize(self, headers, need: str = 'write') -> dict:
        """Verify a Bearer token (shared auth module) and enforce the ACL.
        GIT_ACCESS_OPEN=1 bypasses (dev only)."""
        if os.environ.get('GIT_ACCESS_OPEN'):
            return {'address': 'open', 'role': 'admin'}
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        tok = raw.split('Bearer ')[-1].strip() if 'Bearer ' in raw else raw.strip()
        if not tok:
            raise PermissionError('missing Authorization: Bearer <token> '
                                  '(mint one with `m git/token`)')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
        except PermissionError:
            raise
        except Exception as e:
            raise PermissionError(f'invalid token: {type(e).__name__}')
        address = data.get('key')
        if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
            raise PermissionError('token expired — mint a fresh one')
        role = self._role_of(address)
        rank = {'write': 1, 'admin': 2, 'owner': 3}
        if role is None or rank[role] < rank[need if need in rank else 'write']:
            raise PermissionError(f'{address} lacks {need} access — ask the owner to '
                                  f'`m git/grant {address}`')
        return {'address': address, 'role': role}

    def whoami(self, headers=None) -> dict:
        """Resolve a token to (address, role) — the app uses this to sign in."""
        try:
            return dict(self._authorize(headers or {}, need='write'), ok=True)
        except PermissionError as e:
            # even without a grant, report who the token belongs to
            raw = ((headers or {}).get('Authorization') or '')
            tok = raw.split('Bearer ')[-1].strip()
            if tok:
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        data = m.mod('auth')().verify(tok)
                    return {'ok': False, 'address': data.get('key'),
                            'role': self._role_of(data.get('key')), 'error': str(e)}
                except Exception as e2:
                    return {'ok': False, 'error': str(e2)}
            return {'ok': False, 'error': str(e)}

    # --- meta / mod protocol ------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        st = self._load()
        acl = self._acl()
        gh = m.get(self.github_path, {}) or {}
        try:
            ch = self.changes(MOD_REPO)
            mod_changes = {'branch': ch['branch'], 'commit': ch['commit'],
                           'files_changed': ch['total'], 'clean': ch['clean'],
                           'additions': ch['additions'], 'deletions': ch['deletions']}
        except Exception as e:
            mod_changes = {'error': str(e)}
        return {
            'name': 'git',
            'description': self.description,
            'mod_repo': {'path': self.mod_root, **mod_changes},
            'tracking': list(st['repos']),
            'github': {'connected': bool(gh.get('token')), 'login': gh.get('login')},
            'owner': acl['owner'],
            'grants': len(acl['grants']),
            'port': APP_PORT,
            'url': f'http://localhost:{APP_PORT}',
        }

    # --- web app (zero-dep, one port for app + api) -------------------------

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Serve the app (/) and JSON API (/api/*) on one port. background=True
        spawns a detached process and returns; False blocks."""
        port = int(port)
        if background:
            self.kill(port)
            log_dir = '/tmp/git-mod'
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))
            env = dict(os.environ)
            env['PYTHONPATH'] = root + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', '-c',
                 f"import mod as m; m.mod('git')().serve(port={port}, host={host!r}, background=False)"],
                stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            return {'running': True, 'pid': proc.pid, 'url': f'http://localhost:{port}',
                    'api': f'http://localhost:{port}/api/info',
                    'log': os.path.join(log_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer((host, port), self._make_handler())
        print(f'git app on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=APP_PORT):
        killed = []
        pid_path = '/tmp/git-mod/app.pid'
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
                if int(pid) not in killed:
                    os.kill(int(pid), 15)
                    killed.append(int(pid))
        except Exception:
            pass
        return {'killed': killed}

    PM2_NAME = 'git-app'

    def worker(self, port=APP_PORT, name=None):
        """Run the app under pm2 (auto-restart, survives logout)."""
        name = name or self.PM2_NAME
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_app.py')
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        env = dict(os.environ, GIT_APP_PORT=str(int(port)))
        r = subprocess.run(['pm2', 'start', runner, '--name', name, '--interpreter', 'python3',
                            '--cwd', root, '--time'], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        self._wait_health(int(port))
        return {'worker': name, 'port': int(port), 'running': True}

    def stop_worker(self, name=None):
        name = name or self.PM2_NAME
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': name}

    def _wait_health(self, port, tries=40):
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
        git = self

        # (endpoint → (method-name, required-role)) for POSTs
        WRITES = {'/api/track': ('track', 'write'), '/api/untrack': ('untrack', 'write'),
                  '/api/pull': ('pull', 'write'), '/api/push': ('push', 'write'),
                  '/api/connect': ('connect', 'admin'), '/api/disconnect': ('disconnect', 'admin'),
                  '/api/grant': ('grant', 'admin'), '/api/revoke': ('revoke', 'admin')}
        ARGS = {'track': ('repo', 'name', 'branch'), 'untrack': ('name',), 'pull': ('repo',),
                'push': ('repo', 'msg'), 'connect': ('token',), 'disconnect': (),
                'grant': ('address', 'role'), 'revoke': ('address',)}

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
                self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain')

            @staticmethod
            def _norm(p):
                # tolerate the gateway prefix (/git/... or bare /... after /api/git strip)
                if p == '/git' or p.startswith('/git/'):
                    p = p[len('/git'):] or '/'
                if p not in ('/', '/index.html') and not p.startswith('/api/'):
                    p = '/api' + p          # /api/git gateway strips the whole prefix
                return p or '/'

            def do_GET(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if path in ('/', '/index.html'):
                        return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                    if path == '/api/info':
                        return self._send(200, git.info())
                    if path == '/api/changes':
                        return self._send(200, git.changes(
                            repo=q.get('repo'), diff=q.get('diff') in ('1', 'true')))
                    if path == '/api/diff':
                        return self._send(200, git.diff(repo=q.get('repo'), file=q.get('file'),
                                                        staged=q.get('staged') in ('1', 'true')))
                    if path == '/api/commits':
                        return self._send(200, git.commits(repo=q.get('repo'),
                                                           n=int(q.get('n', 20)),
                                                           branch=q.get('branch')))
                    if path == '/api/repos':
                        return self._send(200, git.repos())
                    if path == '/api/branches':
                        return self._send(200, git.branches(repo=q.get('repo')))
                    if path == '/api/github':
                        return self._send(200, git.github())
                    if path == '/api/github/repos':
                        return self._send(200, git.github_repos(n=int(q.get('n', 100)),
                                                                search=q.get('search')))
                    if path == '/api/access':
                        return self._send(200, git.access())
                    if path == '/api/whoami':
                        return self._send(200, git.whoami(dict(self.headers)))
                    return self._send(404, {'error': f'not found: {path}'})
                except PermissionError as e:
                    return self._send(403, {'error': str(e)})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

            def do_POST(self):
                u = urlparse(self.path)
                path = self._norm(u.path)
                n = int(self.headers.get('Content-Length', 0) or 0)
                try:
                    body = _json.loads(self.rfile.read(n) or b'{}') if n else {}
                except Exception:
                    body = {}
                try:
                    if path not in WRITES:
                        return self._send(404, {'error': f'not found: {path}'})
                    fn_name, need = WRITES[path]
                    who = git._authorize(dict(self.headers), need=need)
                    kwargs = {k: body[k] for k in ARGS[fn_name] if body.get(k) is not None}
                    out = getattr(git, fn_name)(**kwargs)
                    if isinstance(out, dict):
                        out = dict(out, _by=who['address'])
                    return self._send(200, out)
                except PermissionError as e:
                    return self._send(403, {'error': str(e)})
                except (TypeError, ValueError, KeyError) as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

        return H


# --- embedded zero-dependency web UI ----------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>git · change tracker</title>
<style>
  :root{
    --bg:#0a0c10; --bg2:#0e1218; --panel:#12161e; --panel2:#181e28; --line:#212836;
    --line2:#2d3648; --text:#e9edf4; --muted:#8a93a6; --faint:#59637a;
    --accent:#f0883e; --accent2:#ffab70; --green:#3fb950; --red:#f85149; --blue:#58a6ff;
    --r:12px; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;color:var(--text);background:
    radial-gradient(1000px 500px at 10% -10%, rgba(240,136,62,.10), transparent 60%),
    radial-gradient(800px 420px at 95% 0%, rgba(88,166,255,.07), transparent 55%), var(--bg);
    font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
    background-attachment:fixed}
  a{color:var(--blue);text-decoration:none} a:hover{text-decoration:underline}
  header{position:sticky;top:0;z-index:10;padding:12px 20px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,rgba(10,12,16,.94),rgba(10,12,16,.75));backdrop-filter:blur(12px)}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .brand{font-weight:800;font-size:17px;display:flex;gap:8px;align-items:baseline}
  .brand .dot{color:var(--accent)}
  .sub{color:var(--muted);font-size:12px}
  .grow{flex:1}
  .seg{display:inline-flex;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:3px;gap:2px}
  .seg button{all:unset;cursor:pointer;padding:5px 14px;border-radius:999px;font-size:13px;font-weight:600;color:var(--muted)}
  .seg button.on{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#1a0e02}
  input,select,button.btn{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:7px 11px;outline:none}
  input:focus{border-color:var(--accent)}
  input::placeholder{color:var(--faint)}
  button.btn{cursor:pointer;font-weight:600;white-space:nowrap}
  button.btn:hover{background:var(--line)}
  button.btn.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));border-color:transparent;color:#1a0e02}
  button.btn.danger{color:var(--red)}
  .pill{padding:4px 11px;border-radius:999px;border:1px solid var(--line);background:var(--panel);
    color:var(--muted);cursor:pointer;font-size:12px;display:inline-flex;gap:6px;align-items:center}
  .pill.active{color:#fff;border-color:var(--accent);background:rgba(240,136,62,.15)}
  .pill .n{font-size:10px;background:var(--panel2);border-radius:99px;padding:0 6px}
  .pill.dirty .n{background:rgba(240,136,62,.25);color:var(--accent2)}
  main{max-width:1060px;margin:0 auto;padding:20px 20px 80px}
  .view{display:none}.view.on{display:block;animation:fade .2s ease}
  @keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1}}
  .card{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line);
    border-radius:var(--r);padding:14px 16px;margin-bottom:12px}
  .card h3{margin:0 0 8px;font-size:13px;letter-spacing:.6px;color:var(--muted);text-transform:uppercase}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{color:var(--faint);text-align:left;font-weight:600;padding:5px 8px;border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  td{padding:6px 8px;border-bottom:1px solid rgba(33,40,54,.5);vertical-align:top}
  tr:hover td{background:rgba(255,255,255,.02)}
  .file{font-family:var(--mono);font-size:12px;cursor:pointer;word-break:break-all}
  .file:hover{color:var(--accent2)}
  .add{color:var(--green);font-family:var(--mono)} .del{color:var(--red);font-family:var(--mono)}
  .st{font-size:10px;font-weight:700;letter-spacing:.4px;padding:2px 7px;border-radius:6px}
  .st.modified{color:#0d1f10;background:var(--accent2)}
  .st.untracked{color:#dfe6f0;background:var(--line2)}
  .st.added{color:#04140a;background:var(--green)}
  .st.deleted{color:#fff;background:var(--red)}
  .st.renamed,.st.copied{color:#04121f;background:var(--blue)}
  .stats{display:flex;gap:18px;flex-wrap:wrap;margin:2px 0 10px;color:var(--muted);font-size:13px}
  .stats b{color:var(--text)}
  pre.diff{font-family:var(--mono);font-size:12px;line-height:1.5;background:#0a0d12;border:1px solid var(--line);
    border-radius:10px;padding:12px;overflow:auto;max-height:480px;white-space:pre-wrap;word-break:break-all}
  pre.diff .a{color:var(--green)} pre.diff .d{color:var(--red)} pre.diff .h{color:var(--blue)}
  .commit{display:flex;gap:12px;align-items:flex-start;padding:10px 4px;border-bottom:1px solid rgba(33,40,54,.5)}
  .sha{font-family:var(--mono);color:var(--faint);font-size:12px}
  .empty,.err{color:var(--muted);text-align:center;padding:40px 0}
  .err{color:var(--red)}
  .ok{color:var(--green)}
  .kv{display:grid;grid-template-columns:150px 1fr;gap:6px 14px;font-size:13px}
  .kv .k{color:var(--faint)}
  .mono{font-family:var(--mono);font-size:12px;word-break:break-all}
  .badge{font-size:10px;font-weight:800;padding:2px 8px;border-radius:99px}
  .badge.owner{background:var(--accent);color:#1a0e02}
  .badge.admin{background:var(--blue);color:#04121f}
  .badge.write{background:var(--green);color:#04140a}
  .badge.private{background:var(--line2);color:var(--muted)}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand">⎇ git<span class="dot">.</span><span class="sub">change tracker · mod protocol</span></div>
    <div class="seg">
      <button id="t-changes" class="on" onclick="setView('changes')">Changes</button>
      <button id="t-commits" onclick="setView('commits')">Commits</button>
      <button id="t-repos" onclick="setView('repos')">Repos</button>
      <button id="t-github" onclick="setView('github')">GitHub</button>
      <button id="t-access" onclick="setView('access')">Access</button>
    </div>
    <span class="grow"></span>
    <span class="sub" id="who">read-only</span>
    <button class="btn" onclick="loadAll()">↻</button>
  </div>
  <div class="row" id="repopills" style="margin-top:10px"></div>
</header>
<main>
  <div class="view on" id="v-changes"></div>
  <div class="view" id="v-commits"></div>
  <div class="view" id="v-repos"></div>
  <div class="view" id="v-github"></div>
  <div class="view" id="v-access"></div>
</main>
<script>
const $=s=>document.querySelector(s);
const BASE=location.pathname.replace(/\/+$/,'').replace(/\/index\.html$/,'');
const api=p=>BASE+p;
let VIEW='changes', REPO=null, REPOS={}, TOKEN='', ME=null;
try{ TOKEN=localStorage.getItem('git.token')||''; }catch(e){}

function esc(s){return (''+(s??'')).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function ago(d){if(!d)return'';const s=(Date.now()-new Date(d))/1e3;
  if(s<60)return s|0+'s ago';if(s<3600)return (s/60|0)+'m ago';if(s<86400)return (s/3600|0)+'h ago';
  if(s<2592000)return (s/86400|0)+'d ago';return new Date(d).toISOString().slice(0,10)}
function hdrs(json){const h=json?{'Content-Type':'application/json'}:{};if(TOKEN)h['Authorization']='Bearer '+TOKEN;return h}
async function GET(p){const r=await fetch(api(p),{headers:hdrs(false)});const j=await r.json();
  if(!r.ok)throw new Error(j.error||r.status);return j}
async function POST(p,body){const r=await fetch(api(p),{method:'POST',headers:hdrs(true),body:JSON.stringify(body||{})});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}

function setView(v){VIEW=v;
  for(const t of ['changes','commits','repos','github','access']){
    $('#t-'+t).classList.toggle('on',t===v);$('#v-'+t).classList.toggle('on',t===v);}
  $('#repopills').style.display=(v==='changes'||v==='commits')?'flex':'none';
  load(v);}

async function loadPills(){
  try{REPOS=await GET('/api/repos');}catch(e){REPOS={};}
  const names=Object.keys(REPOS);
  if(!REPO||!names.includes(REPO))REPO=names.includes('mod')?'mod':names[0];
  $('#repopills').innerHTML=names.map(n=>{const r=REPOS[n];
    return `<span class="pill ${REPO===n?'active':''} ${r.changes?'dirty':''}" onclick="REPO='${n}';loadPills();load(VIEW)">
      ${esc(n)} <span class="n">${r.error?'!':(r.changes??'?')}</span></span>`}).join('');}

function diffHtml(t){return esc(t).split('\n').map(l=>{
  if(l.startsWith('+++')||l.startsWith('---')||l.startsWith('diff ')||l.startsWith('@@'))return `<span class="h">${l}</span>`;
  if(l.startsWith('+'))return `<span class="a">${l}</span>`;
  if(l.startsWith('-'))return `<span class="d">${l}</span>`;return l}).join('\n')}

async function showDiff(file){
  const el=$('#diffbox');el.style.display='block';el.innerHTML='loading…';
  try{const d=await GET('/api/diff?repo='+encodeURIComponent(REPO)+(file?'&file='+encodeURIComponent(file):''));
    el.innerHTML=`<h3>diff ${esc(file||'(all)')}${d.truncated?' · truncated':''}</h3><pre class="diff">${diffHtml(d.diff)||'(no diff — untracked or binary)'}</pre>`;
  }catch(e){el.innerHTML=`<div class="err">${esc(e.message)}</div>`}}

async function load(v){
  const el=$('#v-'+v);
  try{
    if(v==='changes'){
      const c=await GET('/api/changes?repo='+encodeURIComponent(REPO||''));
      el.innerHTML=`<div class="card">
        <div class="stats">
          <span>⎇ <b>${esc(c.branch)}</b> @ <span class="sha">${esc(c.commit)}</span></span>
          <span><b>${c.total}</b> changed</span>
          <span class="add">+${c.additions}</span><span class="del">−${c.deletions}</span>
          ${c.ahead!=null?`<span>↑${c.ahead} ↓${c.behind}</span>`:''}
          ${c.clean?'<span class="ok">✓ clean</span>':''}
          <span class="grow"></span>
          <button class="btn" onclick="showDiff()">full diff</button>
          ${ME&&ME.ok?`<button class="btn" onclick="doPull()">pull</button>
          <button class="btn primary" onclick="doPush()">commit + push</button>`:''}
        </div>
        ${c.files.length?`<table><tr><th>file</th><th>status</th><th>+</th><th>−</th></tr>
        ${c.files.map(f=>`<tr><td class="file" onclick="showDiff('${esc(f.file)}')">${esc(f.file)}</td>
          <td><span class="st ${f.status}">${f.status.toUpperCase()}${f.staged?' ●':''}</span></td>
          <td class="add">${f.additions??''}</td><td class="del">${f.deletions??''}</td></tr>`).join('')}
        </table>`:'<div class="empty">working tree clean</div>'}
      </div><div class="card" id="diffbox" style="display:none"></div>`;
    }
    if(v==='commits'){
      const cs=await GET('/api/commits?n=30&repo='+encodeURIComponent(REPO||''));
      el.innerHTML=`<div class="card">${cs.map(c=>`<div class="commit">
        <span class="sha">${esc(c.hash)}</span>
        <div style="flex:1;min-width:0"><div>${esc(c.message)}</div>
        <div class="sub">${esc(c.author)} · ${ago(c.date)} · <span class="add">+${c.additions}</span> <span class="del">−${c.deletions}</span></div></div>
        </div>`).join('')||'<div class="empty">no commits</div>'}</div>`;
    }
    if(v==='repos'){
      const rs=await GET('/api/repos');
      el.innerHTML=`<div class="card"><h3>track a repo</h3>
        <div class="row"><input id="trk" placeholder="local path, owner/repo, or github URL" style="flex:1;min-width:240px"/>
        <input id="trkb" placeholder="branch (optional)" style="width:140px"/>
        <button class="btn primary" onclick="doTrack()">+ track</button></div>
        <div class="sub" style="margin-top:6px">GitHub repos are cloned into ~/.mod/git/repos — private ones work once GitHub is connected. Requires write access.</div></div>
        <div class="card"><table><tr><th>repo</th><th>branch</th><th>changes</th><th>path / url</th><th></th></tr>
        ${Object.entries(rs).map(([n,r])=>`<tr>
          <td><b>${esc(n)}</b></td><td>${esc(r.branch||'')}</td>
          <td>${r.error?`<span class="err">${esc(r.error)}</span>`:(r.clean?'<span class="ok">clean</span>':`<b>${r.changes}</b>`)}</td>
          <td class="mono">${esc(r.path||'')}${r.url?`<br><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a>`:''}</td>
          <td>${n!=='mod'?`<button class="btn danger" onclick="doUntrack('${esc(n)}')">untrack</button>`:''}</td>
        </tr>`).join('')}</table></div>`;
    }
    if(v==='github'){
      const g=await GET('/api/github');
      let repos='';
      if(g.connected){try{
        const rs=await GET('/api/github/repos?n=100');
        repos=`<div class="card"><h3>your repos (${rs.length})</h3><table>
          <tr><th>repo</th><th></th><th>pushed</th><th></th></tr>
          ${rs.map(r=>`<tr><td><a href="${esc(r.url)}" target="_blank">${esc(r.repo)}</a>
            <div class="sub">${esc(r.description)}</div></td>
            <td>${r.private?'<span class="badge private">PRIVATE</span>':''}</td>
            <td class="sub">${ago(r.pushed_at)}</td>
            <td>${r.tracked?'<span class="ok">tracked</span>':`<button class="btn" onclick="trackGh('${esc(r.repo)}')">track</button>`}</td>
          </tr>`).join('')}</table></div>`;
      }catch(e){repos=`<div class="card err">${esc(e.message)}</div>`}}
      el.innerHTML=`<div class="card"><h3>github connection</h3>
        ${g.connected?`<div class="kv">
          <span class="k">account</span><span><b>${esc(g.login)}</b> ${esc(g.name||'')}</span>
          <span class="k">token</span><span class="mono">${esc(g.token_tail)} · scopes: ${esc(g.scopes||'(fine-grained)')}</span>
          <span class="k">api rate</span><span>${g.rate&&g.rate.remaining!=null?g.rate.remaining+' / '+g.rate.limit:esc(JSON.stringify(g.rate))}</span>
          </div><div class="row" style="margin-top:10px"><button class="btn danger" onclick="ghDisconnect()">disconnect</button></div>`
        :`<div class="sub" style="margin-bottom:8px">Connect a GitHub <b>personal access token</b> (github.com → Settings → Developer settings). It is validated, stored off-chain in ~/.mod/git (0600), never shown again, and used for private clones, pushes and higher API limits. Requires admin access.</div>
          <div class="row"><input id="pat" type="password" placeholder="ghp_… / github_pat_…" style="flex:1;min-width:260px"/>
          <button class="btn primary" onclick="ghConnect()">connect</button></div>
          ${g.env_token?'<div class="sub" style="margin-top:6px">($GITHUB_TOKEN from the environment is being used as a fallback)</div>':''}`}
      </div>${repos}`;
    }
    if(v==='access'){
      const a=await GET('/api/access');
      el.innerHTML=`<div class="card"><h3>your key</h3>
        <div class="sub" style="margin-bottom:8px">Reads are open. Writes need a signed token from the shared auth module — on the server run <span class="mono">m git/token</span> and paste it here.</div>
        <div class="row"><input id="tok" type="password" placeholder="signed token" style="flex:1;min-width:260px" value="${esc(TOKEN)}"/>
        <button class="btn primary" onclick="saveToken()">sign in</button>
        ${TOKEN?'<button class="btn" onclick="clearToken()">sign out</button>':''}</div>
        <div class="sub" id="tokmsg" style="margin-top:6px">${ME?(ME.ok?`<span class="ok">✓ ${esc(ME.address)} · ${esc(ME.role)}</span>`:`<span class="err">${esc(ME.error||'')}</span>`):''}</div></div>
      <div class="card"><h3>owner</h3><div class="row"><span class="mono">${esc(a.owner)}</span><span class="badge owner">OWNER</span></div>
        ${a.open?'<div class="err" style="text-align:left;padding:8px 0 0">⚠ GIT_ACCESS_OPEN=1 — auth is bypassed (dev mode)</div>':''}</div>
      <div class="card"><h3>grants</h3>
        <div class="row" style="margin-bottom:10px"><input id="gaddr" placeholder="0x address" style="flex:1;min-width:260px"/>
          <select id="grole"><option>write</option><option>admin</option></select>
          <button class="btn primary" onclick="doGrant()">grant</button></div>
        ${Object.keys(a.grants).length?`<table><tr><th>address</th><th>role</th><th>since</th><th></th></tr>
          ${Object.entries(a.grants).map(([addr,g])=>`<tr><td class="mono">${esc(addr)}</td>
          <td><span class="badge ${g.role}">${g.role.toUpperCase()}</span></td>
          <td class="sub">${g.granted_at?ago(g.granted_at*1000):''}</td>
          <td><button class="btn danger" onclick="doRevoke('${esc(addr)}')">revoke</button></td></tr>`).join('')}</table>`
        :'<div class="sub">no grants yet — only the owner can write</div>'}
        <div class="sub" style="margin-top:8px">write → ${a.roles.write.join(', ')} · admin → also ${a.roles.admin.join(', ')}</div></div>`;
    }
  }catch(e){el.innerHTML=`<div class="err">${esc(e.message)}</div>`}}

async function whoami(){ME=null;
  if(TOKEN){try{ME=await GET('/api/whoami');}catch(e){ME={ok:false,error:e.message}}}
  $('#who').innerHTML=ME&&ME.ok?`<span class="ok">${ME.address.slice(0,6)}…${ME.address.slice(-4)} · ${ME.role}</span>`:'read-only';}

function saveToken(){TOKEN=$('#tok').value.trim();try{localStorage.setItem('git.token',TOKEN);}catch(e){}
  whoami().then(()=>load('access'))}
function clearToken(){TOKEN='';try{localStorage.removeItem('git.token');}catch(e){}whoami().then(()=>load('access'))}
async function act(fn){try{await fn();}catch(e){alert(e.message)}}
function doTrack(){act(async()=>{await POST('/api/track',{repo:$('#trk').value.trim(),branch:$('#trkb').value.trim()||null});loadPills();load('repos')})}
function trackGh(r){act(async()=>{await POST('/api/track',{repo:r});loadPills();load('github')})}
function doUntrack(n){if(confirm('untrack '+n+'?'))act(async()=>{await POST('/api/untrack',{name:n});loadPills();load('repos')})}
function doPull(){act(async()=>{await POST('/api/pull',{repo:REPO});load('changes')})}
function doPush(){const msg=prompt('commit message','update');if(msg)act(async()=>{await POST('/api/push',{repo:REPO,msg});loadPills();load('changes')})}
function ghConnect(){act(async()=>{await POST('/api/connect',{token:$('#pat').value.trim()});load('github')})}
function ghDisconnect(){if(confirm('disconnect GitHub?'))act(async()=>{await POST('/api/disconnect',{});load('github')})}
function doGrant(){act(async()=>{await POST('/api/grant',{address:$('#gaddr').value.trim(),role:$('#grole').value});load('access')})}
function doRevoke(a){act(async()=>{await POST('/api/revoke',{address:a});load('access')})}

async function loadAll(){await whoami();await loadPills();load(VIEW);}
loadAll();
setInterval(()=>{if(VIEW==='changes')loadPills().then(()=>load('changes'))},30000);
</script>
</body>
</html>
"""
