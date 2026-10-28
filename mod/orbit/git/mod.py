"""
git — repo change tracker for the mod protocol.

Tracks EVERY change in the mod repo (working tree + staged + untracked, with
per-file add/delete counts, branch, ahead/behind, and full diffs) and any other
repo you point it at — a local path or a GitHub repo (cloned on track).

Commits are written by the agent module (a dependency): it reads the pending
diff and proposes the message, and this module finalizes it — staging, the
commit itself, and the push over the caller's GitHub account all stay here,
behind the same ACL. `msg` is still yours to pass whenever you want it.

Connect a GitHub account — by OAuth ("Connect with GitHub", device or browser
redirect flow) or a personal access token — and it is attached to a mod key:
every address keeps its own GitHub connection, stored off-chain in
~/.mod/git/github.json (0600), and clones/pushes run as whoever is signed in.
Write endpoints are gated by the shared auth module (m.mod('auth') signed
tokens): the owner can grant/revoke write or admin access per address, all
managed from the app's ACCESS tab or the CLI. Whoever owns the mod host (the
box's owner of record, ~/.mod/git/owner.json or the host console's) is an owner
here too — sign in on the ACCESS tab with that wallet and you can commit and
push your own changes without granting yourself anything.

A signature is only good for an hour, so signing in trades it for a SESSION
(~/.mod/git/sessions.json, 0600, hash only): one wallet prompt buys 30 days of
pushing without another one. The role is never baked into the session — every
call re-reads the ACL, so a revoke ends the sessions with it, and `m git/sessions`
/ `m git/sign_out` (or the ACCESS tab) end them by hand.

Mod protocol: null call returns info; the app + JSON API share one port
(50330) and tolerate the gateway prefix, so caddy routes /{git} (app) and
/api/git (API) from config.json.

CLI:
    m git                                  # info (what's tracked, who has access)
    m git/changes                          # ALL changes in the mod repo
    m git/changes repo=agent diff=1        # another tracked repo, with full diff
    m git/commits n=20                     # recent commits
    m git/message                          # agent-written commit message (proposal)
    m git/commit                           # stage + commit it
    m git/push                             # …and push (msg=... to write your own)
    m git/track ~/some/checkout            # track a local repo
    m git/track owner/repo                 # clone + track a GitHub repo
    m git/oauth_app <client_id> [secret]   # register your GitHub OAuth app (once)
    m git/oauth                            # connect GitHub by OAuth → your key
    m git/oauth_poll <session> wait=120    # finish the device flow
    m git/connect <github_pat>             # …or attach a token instead
    m git/github_repos                     # your GitHub repos (private too)
    m git/grant 0xADDR role=write          # manage access
    m git/token                            # mint a signed token for the app/API
    m git/session days=30                  # …trade it for a session that lasts
    m git/sessions                         # who is signed in, and until when
    m git/sign_out                         # end them (id=… for just one)
    m git/serve                            # run the app on :50330
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import subprocess
import time
import contextlib
import mod as m

APP_PORT = 50330
MOD_REPO = 'mod'                        # name of the always-tracked primary repo
STATE = '~/.mod/git/state.json'         # tracked repos
GITHUB = '~/.mod/git/github.json'       # github accounts per key (secret, 0600)
OAUTH = '~/.mod/git/oauth.json'         # github oauth app creds (secret, 0600)
PENDING = '~/.mod/git/oauth_pending.json'   # in-flight oauth handshakes (0600)
ACCESS = '~/.mod/git/access.json'       # owner + per-address grants
SESSIONS = '~/.mod/git/sessions.json'   # long-lived signed-in sessions (secret, 0600)
OWNER = '~/.mod/git/owner.json'         # who owns this box, if it is pinned for git
HOST_OWNER = '~/.mod/claude/owner.json'  # …else the host's owner of record
CLONES = '~/.mod/git/repos'             # where tracked github repos get cloned
GH_RE = re.compile(r'(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')
MAX_DIFF = 200_000                      # chars of diff returned per request
AGENT_MOD = 'agent'                     # module that writes the commit messages
AGENT_MODEL = 'anthropic/claude-sonnet-4.5'   # only if the agent has no default
AGENT_DIFF = 60_000                     # chars of diff handed to the agent
TOKEN_TTL = 3600                        # seconds a *signed* token stays valid
SESSION_PREFIX = 'gits.'                # what a session token starts with
SESSION_DAYS = 30                       # how long a session lasts by default
SESSION_MAX_DAYS = 365                  # …and the most you can ask for
SESSION_TOUCH = 300                     # only restamp `used` this often
OAUTH_SCOPE = 'repo read:org'           # what "connect with github" asks for


class Mod:
    description = ('git — tracks all changes (status, diffs, commits) in the mod repo and any '
                   'other local or GitHub repos; the agent module writes the commit message '
                   'from the diff and this module finalizes it (stage, commit, push); connect a '
                   'GitHub account and manage per-address access with signed-token grants')

    def __init__(self, path: str = None):
        self.state_path = m.abspath(STATE)
        self.github_path = m.abspath(GITHUB)
        self.oauth_path = m.abspath(OAUTH)
        self.pending_path = m.abspath(PENDING)
        self.access_path = m.abspath(ACCESS)
        self.sessions_path = m.abspath(SESSIONS)
        self.owner_path = m.abspath(OWNER)
        self.host_owner_path = m.abspath(HOST_OWNER)
        self.clones = m.abspath(CLONES)
        self._path = m.abspath(path) if path else None

    # --- plumbing -----------------------------------------------------------

    # never let a network git block on a prompt — this runs headless under pm2,
    # so an unauthenticated push must fail in a second, not hang for `timeout`
    GIT_ENV = {'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '', 'SSH_ASKPASS': '',
               'GCM_INTERACTIVE': 'never',
               'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new'}

    def _run(self, args, cwd, timeout=60, check=True, full=False):
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **self.GIT_ENV})
        if check and r.returncode != 0:
            raise RuntimeError(self._scrub((r.stderr or r.stdout or f'{args[0]} failed').strip())[:500])
        return r if full else r.stdout

    @staticmethod
    def _scrub(text: str) -> str:
        """Strip credentials out of git output — pushes run over a URL with the
        key's GitHub token embedded, and git echoes that URL back."""
        return re.sub(r'://[^@\s/]*@', '://***@', text or '')

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

    def commits(self, repo=None, n=20, branch=None, stat=True, skip=0,
                search=None, author=None) -> list:
        """Recent commits as [{hash, author, date, message, additions, deletions}].

        `stat=False` drops the per-commit diffstat: git has to diff every commit
        to produce it, which on this repo is 5 seconds versus instant — the app
        paints the list without it, then fills the +/− in. `search` greps the
        messages, `author` the authors, `skip` pages further back."""
        name, path = self._repo(repo)
        sep = '\x1f'
        fmt = sep.join(['%H', '%an', '%aI', '%s'])
        args = ['git', 'log', f'--pretty=format:{fmt}', '-n', str(int(n))]
        if stat:
            args.append('--shortstat')
        if int(skip or 0):
            args += ['--skip', str(int(skip))]
        if search:
            args += ['--regexp-ignore-case', '--grep', str(search)]
        if author:
            args += ['--regexp-ignore-case', '--author', str(author)]
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

    REV_RE = re.compile(r'^[\w][\w./^~@{}-]*$')

    @staticmethod
    def _npath(f: str) -> str:
        """numstat renders a rename as `a/{x => y}.py` — keep the new path so
        it lines up with what --name-status reports."""
        return re.sub(r'\{[^{}]*? => ([^{}]*?)\}', r'\1', f).split(' => ')[-1].strip()

    def show(self, repo=None, hash: str = 'HEAD', diff=True, file=None) -> dict:
        """One commit in full: metadata, the files it touched with +/− counts,
        and its unified diff (truncated at MAX_DIFF chars)."""
        name, path = self._repo(repo)
        rev = str(hash or 'HEAD')
        if not self.REV_RE.match(rev):
            raise ValueError(f'{rev!r} is not a revision')
        sep = '\x1f'
        meta = (self._run(['git', 'show', '--no-patch',
                           f'--pretty=format:%H{sep}%an{sep}%aI{sep}%s{sep}%b{sep}%P', rev, '--'],
                          cwd=path).split(sep) + [''] * 6)[:6]
        files = {}
        for line in self._run(['git', 'show', '--numstat', '--format=', rev, '--'],
                              cwd=path).splitlines():
            parts = line.split('\t')
            if len(parts) == 3:
                a, d, f = parts
                files[self._npath(f)] = {'file': self._npath(f), 'status': 'modified',
                                         'additions': None if a == '-' else int(a),
                                         'deletions': None if d == '-' else int(d)}
        for line in self._run(['git', 'show', '--name-status', '--format=', rev, '--'],
                              cwd=path).splitlines():
            parts = line.split('\t')
            if len(parts) >= 2 and parts[-1] in files:
                files[parts[-1]]['status'] = self._STATUS.get(parts[0][0], 'modified')
        rows = list(files.values())
        out = {'repo': name, 'hash': meta[0][:10], 'full_hash': meta[0], 'author': meta[1],
               'date': meta[2], 'subject': meta[3], 'body': meta[4].strip(),
               'parents': meta[5].split(), 'file': file,
               'total': len(rows), 'files': rows,
               'additions': sum(f['additions'] or 0 for f in rows),
               'deletions': sum(f['deletions'] or 0 for f in rows)}
        if diff:
            text = self._run(['git', 'show', '--format=', rev, '--'] + ([str(file)] if file else []),
                             cwd=path, check=False)
            out['truncated'] = len(text) > MAX_DIFF
            out['diff'] = text[:MAX_DIFF]
        return out

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

    def pull(self, repo=None, address: str = None, login: str = None) -> dict:
        name, path = self._repo(repo)
        return {'repo': name, 'pull': self._run(self._authed(['git', 'pull'], path, address, login),
                                                cwd=path, timeout=300).strip()}

    def push(self, repo=None, msg: str = None, address: str = None, files=None,
             free: bool = False, login: str = None) -> dict:
        """Stage, commit and push — over the GitHub account attached to
        `address` (the caller's key over the API). With no `msg` the agent
        module writes one from the diff. (Write-gated.)"""
        name, path = self._repo(repo)
        out = {'repo': name}
        if self.changes(repo)['clean']:
            out.update({'commit': 'nothing to commit', 'committed': False})
        else:
            out.update(self.commit(repo, msg=msg, files=files, free=free))
        r = self._run(self._authed(['git', 'push'], path, address, login), cwd=path,
                      timeout=300, check=False, full=True)
        out['push'] = self._scrub((r.stdout + r.stderr).strip())
        out['pushed'] = r.returncode == 0
        if not out['pushed']:
            hint = self._push_hint(path, address, login, out['push'])
            if hint:
                out['hint'] = hint
        return out

    # git's own wording for "you are not allowed to push here"
    AUTH_FAIL = ('authentication failed', 'could not read username', 'no anonymous write',
                 'permission denied', 'terminal prompts disabled', 'invalid username or password',
                 'repository not found')

    def _push_hint(self, path, address, login, err: str) -> str:
        """Turn git's auth refusal into the one thing the caller has to do."""
        if not any(s in (err or '').lower() for s in self.AUTH_FAIL):
            return None
        url = self._run(['git', 'remote', 'get-url', 'origin'], cwd=path, check=False).strip()
        if not url:
            return 'this repo has no origin remote — nothing to push to'
        if not url.startswith('https://'):
            return (f'origin is {url} — pushes over ssh use this box\'s ssh key, not your '
                    'GitHub connection; add the key to github.com/settings/keys')
        if self._github_token(address, login):
            return (f'{self._rec(address, login).get("login")} was refused — that account may '
                    'lack write access to this repo, or its token expired (reconnect it on the '
                    'GITHUB tab)')
        return (f'no GitHub account connected to {self._key(address)} — connect one on the '
                'GITHUB tab (or `m git/connect <github_pat>`); GitHub refuses anonymous writes')

    # --- agent-written commits ----------------------------------------------

    def commit(self, repo=None, msg: str = None, files=None, free: bool = False) -> dict:
        """Stage and commit, nothing pushed. With no `msg` the agent module
        reads the diff and writes the message. `files` limits what is staged
        (a list, or one path); everything otherwise. (Write-gated.)"""
        name, path = self._repo(repo)
        paths = [str(f) for f in ([files] if isinstance(files, str) else (files or []))]
        wrote = self.message(repo, files=paths or None, free=free) if not msg else \
            {'message': str(msg), 'by': 'caller', 'model': None}
        self._run((['git', 'add', '--'] + paths) if paths else ['git', 'add', '-A'], cwd=path)
        r = self._run(['git', 'commit', '-m', wrote['message']], cwd=path, check=False, full=True)
        if r.returncode != 0 and 'nothing to commit' not in (r.stdout + r.stderr):
            raise RuntimeError((r.stderr or r.stdout).strip()[:500])
        return {'repo': name, 'commit': (r.stdout or r.stderr).strip(),
                'committed': r.returncode == 0,
                'hash': self._run(['git', 'rev-parse', '--short', 'HEAD'], cwd=path).strip(),
                'message': wrote['message'], 'by': wrote['by'], 'model': wrote.get('model')}

    COMMIT_PROMPT = """You are writing a git commit message for this change.

Reply with the message and NOTHING else — no preamble, no code fence, no quotes.
Format: one imperative subject line under 72 chars ("add X", "fix Y", not "added"),
then, only if the change needs it, a blank line and up to 4 short "- " bullets.
Describe what the change does and why, not which files moved.

repo: {repo}  branch: {branch}
{hint}
files changed ({total}, +{additions} -{deletions}):
{files}

diff:
{diff}
"""

    def message(self, repo=None, files=None, model: str = None, hint: str = None,
                free: bool = False) -> dict:
        """Have the agent module read the pending diff and write a commit
        message for it. Proposes only — nothing is staged or committed.
        free=True runs it on the agent's free tier (no credits needed)."""
        name, _ = self._repo(repo)
        ch = self.changes(repo)
        if ch['clean']:
            raise ValueError(f'{name}: working tree is clean — nothing to describe')
        rows = [f for f in ch['files']
                if not files or f['file'] in [str(x) for x in files]]
        listing = '\n'.join(
            '  {:<9} {}{}'.format(f['status'], f['file'],
                                  '' if f['additions'] is None
                                  else '  +{} -{}'.format(f['additions'], f['deletions']))
            for f in rows[:200]) or '  (none)'
        diff = ''.join(self.diff(repo, file=f['file'])['diff'] for f in rows) \
            if files else self.diff(repo)['diff']
        prompt = self.COMMIT_PROMPT.format(
            repo=name, branch=ch['branch'], total=len(rows),
            additions=ch['additions'], deletions=ch['deletions'],
            hint=f'the author says: {hint}\n' if hint else '',
            files=listing, diff=diff[:AGENT_DIFF] or '(no textual diff — new or binary files)')
        out = {'repo': name, 'files': len(rows), 'by': 'agent', 'model': model,
               **({'free': True} if free else {})}
        try:
            ag = self._agent()
            model = model or ag.DEFAULT_MODELS.get(ag._provider, AGENT_MODEL)
            if free and hasattr(ag.model, 'free_models'):
                # resolve the free model here so the reply says which one wrote it
                model = (ag.model.free_models() or [model])[0]
            out['model'] = model
            with contextlib.redirect_stdout(io.StringIO()):
                text = ag.model.forward(prompt, stream=False, model=model,
                                        max_tokens=400, temperature=0)
            out['message'] = self._clean_message(text)
            if not out['message']:
                raise RuntimeError('the agent returned an empty message')
        except Exception as e:
            # a missing model key or a flaky provider must never block a commit
            out.update({'message': self._summarize(ch, rows), 'by': 'fallback',
                        'error': str(e)[:300]})
        return out

    def _agent(self):
        """The agent module (a dependency) — built once, reused per instance."""
        if getattr(self, '_agent_mod', None) is None:
            with contextlib.redirect_stdout(io.StringIO()):
                self._agent_mod = m.mod(AGENT_MOD)()
        return self._agent_mod

    @staticmethod
    def _clean_message(text) -> str:
        """Take the model's reply down to the commit message itself."""
        text = (text if isinstance(text, str) else str(text or '')).strip()
        text = re.sub(r'^```[\w]*\n?|\n?```$', '', text).strip()
        text = re.sub(r'^(here(?:\'s| is)[^\n:]*:|commit message:)\s*', '', text, flags=re.I)
        lines = [l.rstrip() for l in text.strip().strip('"').splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        return '\n'.join(lines).strip()[:2000]

    @staticmethod
    def _summarize(ch: dict, rows: list) -> str:
        """Deterministic message for when the agent can't be reached."""
        names = [f['file'] for f in rows] or [f['file'] for f in ch['files']]
        if len(names) == 1:
            return f'update {names[0]}'
        dirs = {n.split('/')[0] for n in names}
        where = ', '.join(sorted(dirs)[:3]) + ('…' if len(dirs) > 3 else '')
        return (f'update {len(names)} files in {where}' if len(dirs) > 1
                else f'{where}: update {len(names)} files')

    def _authed(self, args, path, address=None, login=None):
        """Inject the key's GitHub token into https github remotes for push/pull."""
        tok = self._github_token(address, login)
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

    def track(self, repo: str, name: str = None, branch: str = None,
              address: str = None, login: str = None) -> dict:
        """Track another repo. `repo` may be a local path, owner/repo, or a
        GitHub URL — GitHub repos are cloned into ~/.mod/git/repos using the
        token attached to `address` (the caller's key over the API, `login` to
        pick one of its accounts), so private repos work."""
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
                tok = self._github_token(address, login)
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

    # --- forking -------------------------------------------------------------

    def _gh_source(self, repo: str) -> tuple:
        """(owner, name) for anything forkable: owner/repo, a GitHub URL, or
        the name of a tracked repo whose origin is on GitHub."""
        repo = str(repo).strip()
        st = self._load()
        url = repo
        if repo in st['repos']:
            url = st['repos'][repo].get('url') or self.repo_url(repo) or ''
        mm = GH_RE.match(url or '')
        if not mm:
            raise ValueError(f'{repo!r} has no GitHub origin to fork — pass owner/repo, '
                             'a GitHub URL, or a tracked repo cloned from GitHub')
        return mm.group(1), mm.group(2)

    FORK_WAIT = 30                      # seconds to wait for GitHub to build the fork

    def fork(self, repo: str, name: str = None, org: str = None, track=True,
             address: str = None, login: str = None, branch: str = None) -> dict:
        """Fork a GitHub repo into the account attached to a key (or into
        `org`) and track the clone. `repo` is owner/repo, a GitHub URL, or the
        name of a tracked repo — so anything this module can see is forkable,
        the mod repo included. `login` forks with one of the key's other
        accounts without switching to it. (Write-gated.)"""
        owner, rname = self._gh_source(repo)
        body = {k: v for k, v in (('name', name), ('organization', org)) if v}
        made = self._gh_post(f'/repos/{owner}/{rname}/forks', body, address=address, login=login)
        full = made.get('full_name') or f"{org or ''}/{name or rname}"
        # GitHub builds forks asynchronously — the repo 404s until it's there
        meta, ready, deadline = made, False, time.time() + self.FORK_WAIT
        while True:
            try:
                meta, _h = self._gh_api(f'/repos/{full}', address=address, login=login)
                ready = True
                break
            except Exception:
                if time.time() >= deadline:
                    break
                time.sleep(2)
        out = {'fork': full, 'source': f'{owner}/{rname}', 'ready': ready,
               'url': meta.get('html_url') or made.get('html_url'),
               'private': meta.get('private'),
               'default_branch': meta.get('default_branch') or made.get('default_branch')}
        if track and ready:
            st = self._load()
            local = str(name or full.split('/')[-1])
            if local in st['repos'] and (st['repos'][local].get('url') or '') != out['url']:
                local = f"{full.split('/')[0]}-{local}"     # don't clobber the upstream
            out['tracked'] = self.track(full, name=local, address=address, login=login,
                                        branch=branch or out['default_branch'])
        return out

    # --- github connection (several accounts per key) ------------------------

    def _put_secret(self, path, data):
        """Write a secret file readable only by this box's user."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f)
        os.chmod(path, 0o600)

    def _accounts(self) -> dict:
        """github.json → {address: {'active': login, 'logins': {login: acct}}}.
        Migrates both older layouts on read: one flat record (pre-key) onto the
        owner's key, and one record per key (pre-multi-account) into logins."""
        gh = m.get(self.github_path, {}) or {}
        changed = False
        if gh.get('token') and 'accounts' not in gh:
            gh = {'accounts': {self._acl()['owner']: dict(gh, via=gh.get('via', 'pat'))}}
            changed = True
        accts = gh.get('accounts') or {}
        for addr, rec in list(accts.items()):
            if 'logins' not in rec:
                login = rec.get('login') or 'account'
                accts[addr] = {'active': login, 'logins': {login: rec}}
                changed = True
        if changed:
            self._put_secret(self.github_path, {'accounts': accts})
        return accts

    def _key(self, address=None) -> str:
        return str(address) if address else m.key().address

    def _rec(self, address=None, login=None) -> dict:
        """One key's GitHub account: the named login, else the active one."""
        entry = self._accounts().get(self._key(address)) or {}
        logins = entry.get('logins') or {}
        return (logins.get(str(login)) if login else logins.get(entry.get('active'))) or {}

    def _github_token(self, address=None, login=None):
        """The GitHub token to act with: the key's active account (or the one
        named by `login`), else the owner's, else $GITHUB_TOKEN."""
        rec = self._rec(address, login)
        if login and not rec:
            raise KeyError(f'{login!r} is not connected to {self._key(address)} — '
                           'connect it first (`m git/oauth`)')
        rec = rec or self._rec(self._acl()['owner'])
        return rec.get('token') or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')

    def _gh_api(self, path, token=None, params=None, address=None, login=None):
        import requests
        headers = {'Accept': 'application/vnd.github+json'}
        tok = token or self._github_token(address, login)
        if tok:
            headers['Authorization'] = f'Bearer {tok}'
        r = requests.get(f'https://api.github.com{path}', headers=headers,
                         params=params or {}, timeout=15)
        if r.status_code >= 400:
            raise self._gh_error(r, authed=bool(tok))
        return r.json(), r.headers

    @staticmethod
    def _gh_error(r, authed=True):
        """GitHub's own message beats requests' "401 Client Error for url:…" —
        and a rate-limited anonymous read has one obvious fix, so say it."""
        try:
            msg = r.json().get('message') or r.text[:200]
        except Exception:
            msg = r.text[:200]
        if r.headers.get('X-RateLimit-Remaining') == '0':
            msg = (f'github rate limit reached — ' +
                   ('wait a minute and try again' if authed else
                    'connect a GitHub account on the GitHub tab to raise it'))
            return RuntimeError(msg)
        if r.status_code in (401, 403):
            return PermissionError(f'github {r.status_code}: {msg}')
        return RuntimeError(f'github {r.status_code}: {msg}')

    def _gh_post(self, path, body=None, address=None, login=None):
        import requests
        tok = self._github_token(address, login)
        if not tok:
            raise PermissionError('no GitHub account connected — run m git/oauth '
                                  '(or m git/connect <token>)')
        r = requests.post(f'https://api.github.com{path}',
                          headers={'Accept': 'application/vnd.github+json',
                                   'Authorization': f'Bearer {tok}'},
                          json=body or {}, timeout=30)
        if r.status_code >= 400:
            raise self._gh_error(r)
        return r.json()

    def _attach(self, token: str, address=None, via: str = 'pat') -> dict:
        """Validate a GitHub token and add it to a key's accounts — a key may
        hold several, and the newest connection becomes the active one."""
        token = str(token).strip()
        user, headers = self._gh_api('/user', token=token)
        address = self._key(address)
        login = user.get('login') or 'account'
        accts = self._accounts()
        entry = accts.setdefault(address, {'active': login, 'logins': {}})
        entry['logins'][login] = {'token': token, 'login': login, 'name': user.get('name'),
                                  'scopes': (headers.get('X-OAuth-Scopes') or '').strip(),
                                  'via': via, 'connected_at': int(time.time())}
        entry['active'] = login
        self._put_secret(self.github_path, {'accounts': accts})
        return self.github(address=address)

    def connect(self, token: str, address: str = None) -> dict:
        """Attach a GitHub personal access token to a key (this box's key by
        default). The token is validated against api.github.com and stored
        off-chain in ~/.mod/git/github.json (0600) — never returned by the
        API. Prefer `m git/oauth` unless you specifically want a PAT."""
        return self._attach(token, address=address, via='pat')

    def disconnect(self, address: str = None, login: str = None) -> dict:
        """Detach a GitHub account from a key — the one named by `login`, else
        whichever is active. Any other account stays connected, and the next
        one in line takes over as active."""
        address = self._key(address)
        accts = self._accounts()
        entry = accts.get(address) or {'logins': {}}
        logins = entry.get('logins') or {}
        gone = str(login) if login else entry.get('active')
        rec = logins.pop(gone, None)
        if entry.get('active') == gone:
            entry['active'] = next(iter(logins), None)
        if not logins:
            accts.pop(address, None)
        self._put_secret(self.github_path, {'accounts': accts})
        return {'connected': bool(logins), 'key': address, 'was': (rec or {}).get('login'),
                'active': entry.get('active') if logins else None,
                'accounts': list(logins)}

    def accounts(self, address: str = None) -> dict:
        """Every GitHub account connected to a key, and which one is active.
        Tokens are never returned — only a `…tail`."""
        address = self._key(address)
        entry = self._accounts().get(address) or {}
        active = entry.get('active')
        return {'key': address, 'active': active,
                'accounts': [{'login': l, 'name': r.get('name'), 'via': r.get('via', 'pat'),
                              'scopes': r.get('scopes'), 'connected_at': r.get('connected_at'),
                              'token_tail': '…' + (r.get('token') or '')[-4:],
                              'active': l == active}
                             for l, r in (entry.get('logins') or {}).items()]}

    def switch(self, login: str, address: str = None) -> dict:
        """Make one of a key's connected GitHub accounts the active one —
        every clone, pull, push and fork then runs as that account."""
        address = self._key(address)
        accts = self._accounts()
        entry = accts.get(address) or {}
        if str(login) not in (entry.get('logins') or {}):
            raise KeyError(f'{login!r} is not connected to {address} '
                           f"(connected: {list(entry.get('logins') or {})})")
        entry['active'] = str(login)
        self._put_secret(self.github_path, {'accounts': accts})
        return self.github(address=address)

    def github(self, address: str = None, login: str = None) -> dict:
        """GitHub connection status for a key: the active account (or the one
        named by `login`), how it was connected, token scopes, the current API
        rate limit, every other account on this key and every other key with
        one. Never exposes a token itself."""
        address = self._key(address)
        accts = self._accounts()
        me = self.accounts(address)
        rec = self._rec(address, login)
        tok = rec.get('token')
        out = {'key': address, 'connected': bool(tok), 'active': me['active'],
               'accounts': me['accounts'],
               'env_token': bool(os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')),
               'keys': [{'key': a, 'login': e.get('active'),
                         'accounts': len(e.get('logins') or {}),
                         'via': ((e.get('logins') or {}).get(e.get('active')) or {}).get('via', 'pat'),
                         'connected_at': ((e.get('logins') or {}).get(e.get('active')) or {})
                         .get('connected_at')} for a, e in accts.items()]}
        if tok:
            out.update({'login': rec.get('login'), 'name': rec.get('name'),
                        'scopes': rec.get('scopes'), 'via': rec.get('via', 'pat'),
                        'connected_at': rec.get('connected_at'),
                        'token_tail': '…' + tok[-4:]})
        try:
            rate, _ = self._gh_api('/rate_limit', address=address, login=login)
            core = rate.get('resources', {}).get('core', {})
            out['rate'] = {'remaining': core.get('remaining'), 'limit': core.get('limit')}
        except Exception as e:
            out['rate'] = {'error': str(e)[:120]}
        return out

    # --- github oauth (device flow + browser redirect) -----------------------

    GH_DEVICE_URL = 'https://github.com/login/device/code'
    GH_TOKEN_URL = 'https://github.com/login/oauth/access_token'
    GH_AUTHORIZE_URL = 'https://github.com/login/oauth/authorize'
    GH_REGISTER_URL = 'https://github.com/settings/applications/new'

    def oauth_app(self, client_id: str = None, client_secret: str = None,
                  scope: str = None) -> dict:
        """Register the GitHub OAuth app this module signs people in with
        (github.com → Settings → Developer settings → OAuth Apps; tick "Enable
        Device Flow"). Credentials live off-chain in ~/.mod/git/oauth.json
        (0600) — the client secret is only needed for the browser redirect
        flow, the device flow runs on the client id alone. Call with no
        arguments to just read the status."""
        app = m.get(self.oauth_path, {}) or {}
        for k, v in (('client_id', client_id), ('client_secret', client_secret),
                     ('scope', scope)):
            if v:
                app[k] = str(v).strip()
        if client_id or client_secret or scope:
            self._put_secret(self.oauth_path, app)
        return self.oauth_status()

    def _oauth_app(self) -> dict:
        app = m.get(self.oauth_path, {}) or {}
        return {'client_id': app.get('client_id') or os.environ.get('GITHUB_CLIENT_ID'),
                'client_secret': (app.get('client_secret')
                                  or os.environ.get('GITHUB_CLIENT_SECRET')),
                'scope': app.get('scope') or OAUTH_SCOPE}

    def oauth_status(self) -> dict:
        """Is an OAuth app configured, and which flows does it unlock?"""
        app = self._oauth_app()
        return {'configured': bool(app['client_id']), 'client_id': app['client_id'],
                'device_flow': bool(app['client_id']),
                'web_flow': bool(app['client_id'] and app['client_secret']),
                'scope': app['scope'], 'register': self.GH_REGISTER_URL,
                'callback_path': '/oauth/callback',
                'pending': len(self._pending()),
                'note': 'callback URL = <this app URL>/oauth/callback'}

    def _pending(self) -> dict:
        """In-flight handshakes, expired ones swept."""
        sess = (m.get(self.pending_path, {}) or {}).get('sessions') or {}
        now = time.time()
        return {k: v for k, v in sess.items() if v.get('expires_at', 0) > now}

    def _pend(self, sid: str, rec):
        sess = self._pending()
        if rec is None:
            sess.pop(sid, None)
        else:
            sess[sid] = rec
        self._put_secret(self.pending_path, {'sessions': sess})

    def _client(self, need_secret=False) -> dict:
        app = self._oauth_app()
        if not app['client_id']:
            raise PermissionError(
                'no GitHub OAuth app configured — register one at '
                f'{self.GH_REGISTER_URL} then run `m git/oauth_app <client_id>`')
        if need_secret and not app['client_secret']:
            raise PermissionError('the redirect flow needs the OAuth app client secret — '
                                  '`m git/oauth_app <client_id> <client_secret>` '
                                  '(or use the device flow: `m git/oauth`)')
        return app

    def oauth(self, address: str = None, scope: str = None) -> dict:
        """Connect GitHub by OAuth and attach it to a key (device flow): open
        the verification URL, type the user code, then `m git/oauth_poll
        <session> wait=120`. The app polls for you."""
        import requests
        import secrets
        app = self._client()
        address = self._key(address)
        r = requests.post(self.GH_DEVICE_URL, headers={'Accept': 'application/json'},
                          data={'client_id': app['client_id'], 'scope': scope or app['scope']},
                          timeout=15)
        d = r.json()
        if d.get('error') or not d.get('device_code'):
            raise RuntimeError(f"github: {d.get('error_description') or d.get('error') or r.text[:200]}")
        sid = secrets.token_hex(8)
        interval, expires = int(d.get('interval', 5)), int(d.get('expires_in', 900))
        self._pend(sid, {'flow': 'device', 'device_code': d['device_code'], 'address': address,
                         'interval': interval, 'expires_at': time.time() + expires})
        return {'session': sid, 'key': address, 'user_code': d['user_code'],
                'verification_uri': d.get('verification_uri', 'https://github.com/login/device'),
                'interval': interval, 'expires_in': expires,
                'next': f'm git/oauth_poll {sid} wait=120'}

    def oauth_poll(self, session: str, wait: int = 0) -> dict:
        """Ask GitHub whether the device code was approved yet. wait=<seconds>
        blocks until it is (or the poll window closes). On approval the token
        is attached to the key that started the flow."""
        import requests
        app = self._client()
        sid = str(session)
        s = self._pending().get(sid)
        if not s or s.get('flow') != 'device':
            raise KeyError(f'unknown or expired oauth session {sid!r} — start one with `m git/oauth`')
        deadline = time.time() + max(0, int(wait or 0))
        while True:
            d = requests.post(self.GH_TOKEN_URL, headers={'Accept': 'application/json'},
                              data={'client_id': app['client_id'],
                                    'device_code': s['device_code'],
                                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'},
                              timeout=15).json()
            if d.get('access_token'):
                self._pend(sid, None)
                return dict(self._attach(d['access_token'], address=s['address'], via='oauth'),
                            status='connected')
            err = d.get('error')
            if err in ('authorization_pending', 'slow_down'):
                if err == 'slow_down':
                    s['interval'] = int(d.get('interval', s['interval'] + 5))
                    self._pend(sid, s)
                if time.time() >= deadline:
                    return {'status': 'pending', 'session': sid, 'key': s['address'],
                            'interval': s['interval']}
                time.sleep(s['interval'])
                continue
            self._pend(sid, None)
            raise RuntimeError(f"github: {d.get('error_description') or err or 'oauth failed'}")

    def oauth_url(self, redirect_uri: str, address: str = None, scope: str = None) -> dict:
        """Browser redirect flow: where to send someone to authorize. The
        redirect_uri must match the OAuth app's registered callback — this
        app's URL + /oauth/callback."""
        import secrets
        from urllib.parse import urlencode
        app = self._client(need_secret=True)
        address = self._key(address)
        state = secrets.token_hex(16)
        self._pend(state, {'flow': 'web', 'address': address, 'redirect_uri': str(redirect_uri),
                           'expires_at': time.time() + 900})
        q = urlencode({'client_id': app['client_id'], 'redirect_uri': str(redirect_uri),
                       'scope': scope or app['scope'], 'state': state, 'allow_signup': 'true'})
        return {'url': f'{self.GH_AUTHORIZE_URL}?{q}', 'state': state, 'key': address}

    def oauth_callback(self, code: str, state: str) -> dict:
        """Redeem the ?code GitHub sends back to the callback URL. `state` is
        what binds the new token to the key that started the flow — an unknown
        state is rejected, so nobody can graft an account onto someone's key."""
        import requests
        app = self._client(need_secret=True)
        s = self._pending().get(str(state))
        if not s or s.get('flow') != 'web':
            raise PermissionError('unknown or expired oauth state — start again from the app')
        d = requests.post(self.GH_TOKEN_URL, headers={'Accept': 'application/json'},
                          data={'client_id': app['client_id'],
                                'client_secret': app['client_secret'],
                                'code': str(code), 'redirect_uri': s.get('redirect_uri')},
                          timeout=15).json()
        self._pend(str(state), None)
        if not d.get('access_token'):
            raise RuntimeError(f"github: {d.get('error_description') or d.get('error') or 'no token'}")
        return dict(self._attach(d['access_token'], address=s['address'], via='oauth'),
                    status='connected')

    def github_repos(self, n=100, search=None, address: str = None,
                     login: str = None) -> list:
        """The repos of the account attached to a key (private included),
        newest-push first. `login` reads one of the key's other accounts."""
        if not self._github_token(address, login):
            raise PermissionError('no GitHub account connected — run m git/oauth '
                                  '(or m git/connect <token>)')
        data, _ = self._gh_api('/user/repos', params={'per_page': min(int(n), 100),
                                                      'sort': 'pushed'},
                               address=address, login=login)
        st = self._load()
        tracked_urls = {(v.get('url') or '') for v in st['repos'].values()}
        rows = [self._row(r, tracked_urls) for r in data]
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r['repo'].lower() or s in r['description'].lower()]
        return rows

    def _row(self, r: dict, tracked_urls=()) -> dict:
        """One repo, the way this module talks about repos."""
        return {'repo': r.get('full_name'), 'owner': (r.get('owner') or {}).get('login'),
                'description': (r.get('description') or '')[:220],
                'private': r.get('private'), 'fork': r.get('fork'),
                'stars': r.get('stargazers_count'), 'forks': r.get('forks_count'),
                'language': r.get('language'), 'topics': (r.get('topics') or [])[:6],
                'default_branch': r.get('default_branch'), 'pushed_at': r.get('pushed_at'),
                'updated_at': r.get('updated_at'), 'url': r.get('html_url'),
                'tracked': r.get('html_url') in tracked_urls}

    SORTS = ('stars', 'forks', 'updated', 'help-wanted-issues')

    def github_search(self, query: str, n=30, sort: str = None, language: str = None,
                      user: str = None, address: str = None, login: str = None) -> list:
        """Search ALL of GitHub for repos — not just yours — through the search
        API, then track or fork any hit. `sort` is one of stars/forks/updated
        (best match by default); `language` and `user` narrow the query, and
        GitHub's own qualifiers (`topic:mcp`, `stars:>500`, `pushed:>2026-01-01`)
        work inside `query`. Anonymous searches are allowed but capped at 10 a
        minute — connect an account and it is 30."""
        q = ' '.join(filter(None, [str(query or '').strip(),
                                   f'language:{language}' if language else '',
                                   f'user:{user}' if user else '']))
        if not q:
            raise ValueError('search what? pass a query (e.g. m git/search "mcp server")')
        if sort and str(sort) not in self.SORTS:
            raise ValueError(f'sort must be one of {self.SORTS}')
        params = {'q': q, 'per_page': max(1, min(int(n), 100))}
        if sort:
            params['sort'] = str(sort)
        data, _ = self._gh_api('/search/repositories', params=params,
                               address=address, login=login)
        tracked_urls = {(v.get('url') or '') for v in self._load()['repos'].values()}
        return [self._row(r, tracked_urls) for r in (data.get('items') or [])]

    search = github_search

    # --- access management --------------------------------------------------

    ROLES = ('write', 'admin')

    def _acl(self) -> dict:
        acl = m.get(self.access_path, {}) or {}
        if not acl.get('owner'):
            acl = {'owner': m.key().address, 'grants': acl.get('grants', {})}
            m.put(self.access_path, acl)
        acl.setdefault('grants', {})
        return acl

    def _host_owner(self):
        """Whoever owns the mod host this runs on — an owner here, no grant
        needed, so the person who owns the box can commit their own changes
        from the app by signing in with that wallet. Every module records the
        host's owner the same way (~/.mod/<mod>/owner.json), so git reads its
        own file if the box pins one and the host console's otherwise — one
        owner of record per box. $GIT_OWNER overrides both."""
        env = os.environ.get('GIT_OWNER') or os.environ.get('MOD_OWNER')
        if env:
            return env.strip()
        for path in (self.owner_path, self.host_owner_path):
            rec = m.get(path, {}) or {}
            if rec.get('owner'):
                return str(rec['owner'])
        return None

    def access(self) -> dict:
        """Who can do what: the owner (plus the mod host's owner) and every
        granted address/role. Reads are open; write ops need role write+,
        github/access management needs admin+ (owners are always admin)."""
        acl = self._acl()
        host = self._host_owner()
        return {'owner': acl['owner'], 'host_owner': host, 'grants': acl['grants'],
                'roles': {'write': ['track', 'untrack', 'pull', 'commit', 'push',
                                    'agent-written messages',
                                    'connect/disconnect your own GitHub'],
                          'admin': ['grant', 'revoke', 'oauth app', "another key's GitHub"]},
                'auth': "signed token from m.mod('auth') — mint one with `m git/token`, "
                        'or sign in with the wallet that owns this host',
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

    def set_owner(self, address: str, host: bool = False) -> dict:
        """Hand the module to another address (CLI/local only — not exposed
        over the HTTP API). host=True instead pins the mod host's owner for
        git (~/.mod/git/owner.json), which is an owner here alongside it."""
        if host:
            m.put(self.owner_path, {'owner': str(address)})
            return self.access()
        acl = self._acl()
        acl['owner'] = str(address)
        m.put(self.access_path, acl)
        return self.access()

    def token(self, data: dict = None) -> str:
        """Mint a signed auth token for this box's key — paste it into the app
        (ACCESS tab) or send it as `Authorization: Bearer <token>`. It lasts an
        hour; `session` turns it into one that lasts. A wallet
        signs its own instead: base64url of {data, time, key, signature} where
        signature is a personal_sign over the compact {"data":…,"time":…} (what
        the app's "sign in with wallet" does)."""
        with contextlib.redirect_stdout(io.StringIO()):
            return m.mod('auth')().token(data or {'mod': 'git'})

    # --- staying signed in --------------------------------------------------
    # A wallet signature is only good for an hour, which meant re-signing before
    # every push. Trade it for a session instead: prove who you are once and this
    # hands back an opaque token the app keeps. Only its hash is stored, and the
    # ROLE is never frozen into it — every call re-reads the ACL, so revoking a
    # grant (or handing the box to a new owner) kills the sessions with it.

    def _sessions(self) -> dict:
        secs = m.get(self.sessions_path, {}) or {}
        return secs if isinstance(secs, dict) else {}

    @staticmethod
    def _hash(secret: str) -> str:
        return hashlib.sha256(str(secret).encode()).hexdigest()

    def _prune(self, secs: dict) -> dict:
        now = time.time()
        return {i: s for i, s in secs.items() if float(s.get('expires') or 0) > now}

    def session(self, days: float = None, label: str = None, address: str = None) -> dict:
        """Trade the signed token you just proved yourself with for a session
        that keeps working for `days` (default 30) — so the app stops asking
        your wallet to sign before every push. `m git/sessions` lists them,
        `m git/sign_out` ends them. (Write-gated: you can only mint one for a
        key that already has access.)"""
        who = self._key(address)
        role = self._role_of(who)
        if role is None:
            raise PermissionError(f'{who} has no access here — nothing to keep signed in')
        days = max(0.01, min(float(days or SESSION_DAYS), SESSION_MAX_DAYS))
        sid, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
        now = int(time.time())
        secs = self._prune(self._sessions())
        secs[sid] = {'address': who, 'hash': self._hash(secret), 'label': str(label or 'app'),
                     'created': now, 'used': now, 'expires': int(now + days * 86400)}
        self._put_secret(self.sessions_path, secs)
        return {'token': f'{SESSION_PREFIX}{sid}.{secret}', 'id': sid, 'address': who,
                'role': role, 'days': days, 'expires': secs[sid]['expires'],
                'label': secs[sid]['label']}

    def _session_address(self, tok: str):
        """The address behind a session token — None if `tok` isn't one at all,
        PermissionError if it is one and it's dead."""
        if not tok or not str(tok).startswith(SESSION_PREFIX):
            return None
        rest = str(tok)[len(SESSION_PREFIX):]
        sid, _, secret = rest.partition('.')
        secs = self._sessions()
        rec = secs.get(sid) if secret else None
        if not rec or not hmac.compare_digest(str(rec.get('hash') or ''), self._hash(secret)):
            raise PermissionError('this session is no longer valid — sign in again')
        if float(rec.get('expires') or 0) <= time.time():
            secs.pop(sid, None)
            self._put_secret(self.sessions_path, secs)
            raise PermissionError('session expired — sign in again')
        if time.time() - float(rec.get('used') or 0) > SESSION_TOUCH:
            rec['used'] = int(time.time())
            self._put_secret(self.sessions_path, secs)
        return rec.get('address')

    def sessions(self, address: str = None) -> dict:
        """Live sessions — every one, or just `address`'s (the caller's over the
        API). Secrets never come back, only who/when/until."""
        secs = self._prune(self._sessions())
        rows = [{'id': i, 'address': v.get('address'), 'label': v.get('label'),
                 'created': v.get('created'), 'used': v.get('used'),
                 'expires': v.get('expires'), 'role': self._role_of(v.get('address'))}
                for i, v in secs.items()
                if not address or str(v.get('address') or '').lower() == str(address).lower()]
        rows.sort(key=lambda r: r.get('created') or 0, reverse=True)
        return {'address': address, 'sessions': rows, 'total': len(rows)}

    def sign_out(self, id: str = None, address: str = None) -> dict:
        """End a session: one by `id`, else all of `address`'s (the caller's
        over the API), else every session on the box. The wallet signature
        itself can't be revoked — it dies on its own within the hour."""
        secs = self._prune(self._sessions())
        who = str(address or '').lower()
        if id:
            rec = secs.get(str(id))
            if (rec and who and str(rec.get('address') or '').lower() != who
                    and self._role_of(address) not in ('admin', 'owner')):
                raise PermissionError('that session belongs to another key')
            keep = {i: v for i, v in secs.items() if i != str(id)}
        elif who:
            keep = {i: v for i, v in secs.items()
                    if str(v.get('address') or '').lower() != who}
        else:
            keep = {}
        self._put_secret(self.sessions_path, keep)
        return {'ended': len(secs) - len(keep), 'left': len(keep), 'address': address or None}

    def _role_of(self, address: str):
        """Role for an address — the module's owner and the host's owner both
        rank as owner. Compared case-insensitively: wallets sign in lowercase
        while owner records are checksummed."""
        who = str(address or '').lower()
        if not who:
            return None
        acl = self._acl()
        if who in {str(a or '').lower() for a in (acl['owner'], self._host_owner())}:
            return 'owner'
        return next((g.get('role') for a, g in acl['grants'].items()
                     if str(a).lower() == who), None)

    def _authorize(self, headers, need: str = 'write') -> dict:
        """Verify a Bearer token (shared auth module) and enforce the ACL.
        GIT_ACCESS_OPEN=1 bypasses (dev only)."""
        if os.environ.get('GIT_ACCESS_OPEN'):
            # dev mode: act as this box's key so github connections still land
            # on a real address
            return {'address': m.key().address, 'role': 'owner', 'open': True}
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        tok = raw.split('Bearer ')[-1].strip() if 'Bearer ' in raw else raw.strip()
        if not tok:
            raise PermissionError('missing Authorization: Bearer <token> '
                                  '(mint one with `m git/token`)')
        via = 'session'
        address = self._session_address(tok)
        if not address:
            via = 'token'
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    data = m.mod('auth')().verify(tok)
            except PermissionError:
                raise
            except Exception as e:
                raise PermissionError(f'invalid token: {type(e).__name__}')
            address = data.get('key')
            if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
                raise PermissionError('token expired — mint a fresh one, or keep this '
                                      'browser signed in with `session`')
        role = self._role_of(address)
        rank = {'write': 1, 'admin': 2, 'owner': 3}
        if role is None or rank[role] < rank[need if need in rank else 'write']:
            raise PermissionError(f'{address} lacks {need} access — ask the owner to '
                                  f'`m git/grant {address}`')
        return {'address': address, 'role': role, 'via': via}

    def _token_address(self, headers) -> str:
        """The address a Bearer token belongs to, ignoring the ACL — reads are
        open, but they resolve against the caller's own GitHub connection."""
        raw = (headers.get('Authorization') or headers.get('authorization') or '')
        tok = raw.split('Bearer ')[-1].strip()
        if not tok:
            return None
        try:
            addr = self._session_address(tok)
            if addr:
                return addr
        except PermissionError:
            return None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
            if abs(time.time() - float(data.get('time', 0))) > TOKEN_TTL:
                return None
            return data.get('key')
        except Exception:
            return None

    def whoami(self, headers=None) -> dict:
        """Resolve a token to (address, role) — the app uses this to sign in."""
        try:
            return dict(self._authorize(headers or {}, need='write'), ok=True)
        except PermissionError as e:
            # even without a grant, report who the token belongs to
            raw = ((headers or {}).get('Authorization') or '')
            tok = raw.split('Bearer ')[-1].strip()
            # a dead session has nothing to look up — say so instead of letting
            # the auth module fail to base64-decode it
            if tok and not tok.startswith(SESSION_PREFIX):
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
        accts = self._accounts()
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
            'github': {'keys': len(accts),
                       'logins': sorted({r.get('login') for r in accts.values() if r.get('login')}),
                       'oauth_app': bool(self._oauth_app()['client_id'])},
            # the agent module is loaded lazily (only when a message is needed),
            # so info() stays a cheap health check
            'agent': {'mod': AGENT_MOD, 'writes': 'commit messages from the diff',
                      'loaded': getattr(self, '_agent_mod', None) is not None},
            'owner': acl['owner'],
            'host_owner': self._host_owner(),
            'grants': len(acl['grants']),
            'sessions': len(self._prune(self._sessions())),
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
                  '/api/message': ('message', 'write'), '/api/commit': ('commit', 'write'),
                  '/api/connect': ('connect', 'write'), '/api/disconnect': ('disconnect', 'write'),
                  '/api/oauth/start': ('oauth', 'write'), '/api/oauth/poll': ('oauth_poll', 'write'),
                  '/api/oauth/url': ('oauth_url', 'write'),
                  '/api/oauth/app': ('oauth_app', 'admin'),
                  '/api/fork': ('fork', 'write'), '/api/switch': ('switch', 'write'),
                  '/api/grant': ('grant', 'admin'), '/api/revoke': ('revoke', 'admin'),
                  '/api/session': ('session', 'write'), '/api/signout': ('sign_out', 'write')}
        ARGS = {'track': ('repo', 'name', 'branch', 'address', 'login'), 'untrack': ('name',),
                'fork': ('repo', 'name', 'org', 'track', 'branch', 'address', 'login'),
                'switch': ('login', 'address'),
                'pull': ('repo', 'address', 'login'),
                'push': ('repo', 'msg', 'address', 'files', 'free', 'login'),
                'message': ('repo', 'files', 'model', 'hint', 'free'),
                'commit': ('repo', 'msg', 'files', 'free'),
                'connect': ('token', 'address'), 'disconnect': ('address', 'login'),
                'oauth': ('address', 'scope'), 'oauth_poll': ('session', 'wait'),
                'oauth_url': ('redirect_uri', 'address', 'scope'),
                'oauth_app': ('client_id', 'client_secret', 'scope'),
                'grant': ('address', 'role'), 'revoke': ('address',),
                'session': ('days', 'label', 'address'), 'sign_out': ('id', 'address')}
        # these act as a key: default to the caller's, admin to act as another
        AS_KEY = {'track', 'pull', 'push', 'connect', 'disconnect', 'oauth', 'oauth_url',
                  'fork', 'switch', 'session', 'sign_out'}

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

            def _key(self, q):
                """Which key this read is about: ?key=…, else the caller's."""
                return q.get('key') or git._token_address(dict(self.headers))

            def _callback(self, q):
                """GitHub's redirect lands here — redeem the code, then say so
                in plain HTML (this tab is outside the app)."""
                from html import escape
                try:
                    if q.get('error'):
                        raise PermissionError(q.get('error_description') or q['error'])
                    out = git.oauth_callback(code=q.get('code'), state=q.get('state'))
                    msg = (f"<h1>✓ connected</h1><p><b>{escape(out.get('login') or '')}</b> is now "
                           f"attached to key<br><code>{escape(str(out.get('key')))}</code></p>")
                except Exception as e:
                    msg = f"<h1 class=e>✗ not connected</h1><p>{escape(str(e))}</p>"
                return self._send(200, CALLBACK_HTML.replace('{{body}}', msg),
                                  'text/html; charset=utf-8')

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
                                                           branch=q.get('branch'),
                                                           stat=q.get('stat') != '0',
                                                           skip=int(q.get('skip', 0)),
                                                           search=q.get('search'),
                                                           author=q.get('author')))
                    if path == '/api/show':
                        return self._send(200, git.show(repo=q.get('repo'),
                                                        hash=q.get('hash', 'HEAD'),
                                                        file=q.get('file'),
                                                        diff=q.get('diff') != '0'))
                    if path == '/api/repos':
                        return self._send(200, git.repos())
                    if path == '/api/branches':
                        return self._send(200, git.branches(repo=q.get('repo')))
                    if path == '/api/github':
                        return self._send(200, git.github(address=self._key(q),
                                                          login=q.get('login')))
                    if path == '/api/accounts':
                        return self._send(200, git.accounts(address=self._key(q)))
                    if path == '/api/github/repos':
                        return self._send(200, git.github_repos(n=int(q.get('n', 100)),
                                                                search=q.get('search'),
                                                                address=self._key(q),
                                                                login=q.get('login')))
                    if path == '/api/search':
                        return self._send(200, git.github_search(
                            query=q.get('q') or q.get('query'), n=int(q.get('n', 30)),
                            sort=q.get('sort') or None, language=q.get('language') or None,
                            user=q.get('user') or None, address=self._key(q)))
                    if path == '/api/oauth':
                        return self._send(200, git.oauth_status())
                    if path == '/api/oauth/callback':
                        return self._callback(q)
                    if path == '/api/access':
                        return self._send(200, git.access())
                    if path == '/api/whoami':
                        return self._send(200, git.whoami(dict(self.headers)))
                    if path == '/api/sessions':
                        # your own sessions only — the token says which are yours
                        who = self._key(q)
                        if not who:
                            return self._send(200, {'address': None, 'sessions': [],
                                                    'total': 0})
                        return self._send(200, git.sessions(address=who))
                    return self._send(404, {'error': f'not found: {path}'})
                except PermissionError as e:
                    return self._send(403, {'error': str(e)})
                except (TypeError, ValueError, KeyError) as e:
                    return self._send(400, {'error': str(e)})
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
                    if fn_name in AS_KEY:
                        if (kwargs.get('address') and kwargs['address'] != who['address']
                                and who['role'] not in ('admin', 'owner')):
                            raise PermissionError('only an admin can act as another key')
                        kwargs.setdefault('address', who['address'])
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


# --- oauth redirect landing page --------------------------------------------

CALLBACK_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>git · github oauth</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0c10;color:#e9edf4;
   font:14px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;text-align:center}
 .card{border:1px solid #212836;border-radius:12px;padding:28px 34px;background:#12161e;max-width:520px}
 h1{margin:0 0 10px;font-size:20px;color:#3fb950} h1.e{color:#f85149}
 code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#8a93a6;word-break:break-all}
 a{display:inline-block;margin-top:18px;color:#1a0e02;background:#f0883e;padding:8px 16px;
   border-radius:9px;text-decoration:none;font-weight:700}
</style></head>
<body><div class="card">{{body}}<a id="back" href="/">← back to git</a></div>
<script>document.getElementById('back').href=
  location.pathname.replace(/\/(api\/)?oauth\/callback\/?$/,'')+'/';</script>
</body></html>
"""

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
    --hh:100px;   /* header height — measured at runtime, the sidebar hangs off it */
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
  input,select,textarea,button.btn{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:7px 11px;outline:none}
  input:focus,textarea:focus{border-color:var(--accent)}
  input::placeholder,textarea::placeholder{color:var(--faint)}
  .composer{display:flex;flex-direction:column;gap:8px;margin:12px 0 4px}
  .composer textarea{width:100%;resize:vertical;font:13px/1.5 var(--mono)}
  button.btn{cursor:pointer;font-weight:600;white-space:nowrap}
  button.btn:hover{background:var(--line)}
  button.btn.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));border-color:transparent;color:#1a0e02}
  button.btn.danger{color:var(--red)}
  .pill{padding:4px 11px;border-radius:999px;border:1px solid var(--line);background:var(--panel);
    color:var(--muted);cursor:pointer;font-size:12px;display:inline-flex;gap:6px;align-items:center}
  .pill.active{color:#fff;border-color:var(--accent);background:rgba(240,136,62,.15)}
  .pill .n{font-size:10px;background:var(--panel2);border-radius:99px;padding:0 6px}
  .pill.dirty .n{background:rgba(240,136,62,.25);color:var(--accent2)}
  .wrap{display:flex;align-items:flex-start}
  main{flex:1;min-width:0;max-width:1060px;margin:0 auto;padding:20px 20px 80px}
  /* commit sidebar — toggled from the header, remembered across reloads */
  aside{flex:0 0 288px;width:288px;border-right:1px solid var(--line);padding:14px 10px 60px;
    position:sticky;top:var(--hh);max-height:calc(100vh - var(--hh) - 8px);overflow:auto}
  body.noside aside{display:none}
  aside h3{margin:0 0 8px;padding:0 6px;font-size:11px;letter-spacing:.6px;color:var(--muted);
    text-transform:uppercase;display:flex;gap:8px;align-items:center}
  /* the head stays put while the log scrolls under it */
  .sidehead{position:sticky;top:-14px;z-index:1;padding:14px 0 8px;margin-top:-14px;
    background:rgba(10,12,16,.94);backdrop-filter:blur(8px)}
  .sidehead input{width:100%;font-size:12.5px;padding:5px 10px}
  .citem{padding:8px 9px;border-radius:9px;cursor:pointer;border:1px solid transparent}
  .citem:hover{background:rgba(255,255,255,.03)}
  .citem.on{background:rgba(240,136,62,.12);border-color:rgba(240,136,62,.45)}
  .citem .msg{font-size:12.5px;line-height:1.35;overflow:hidden;display:-webkit-box;
    -webkit-line-clamp:2;-webkit-box-orient:vertical}
  .citem .meta{font-size:11px;color:var(--faint);margin-top:3px;display:flex;gap:6px;flex-wrap:wrap}
  .citem.work .msg{color:var(--accent2);font-weight:600}
  .acct{cursor:pointer}
  .acct .x{color:var(--faint);font-weight:700}
  .acct .x:hover{color:var(--red)}
  #toast{position:fixed;right:18px;bottom:18px;max-width:440px;z-index:50;display:none;
    background:var(--panel2);border:1px solid var(--line2);border-radius:10px;padding:10px 14px;
    font-size:13px;white-space:pre-wrap;box-shadow:0 10px 34px rgba(0,0,0,.55)}
  #toast.err{border-color:var(--red);color:var(--red)}
  /* narrow: the sidebar becomes a drawer over the content, below the header, with a scrim */
  #scrim{display:none;position:fixed;inset:0;z-index:8;background:rgba(0,0,0,.55)}
  @media(max-width:820px){
    aside{position:fixed;left:0;top:var(--hh);height:calc(100vh - var(--hh));z-index:9;
      background:var(--bg2);max-height:none;box-shadow:0 0 40px rgba(0,0,0,.6)}
    body:not(.noside) #scrim{display:block}}
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
  .devicebox{margin-top:12px;padding:12px 14px;border:1px dashed var(--line2);border-radius:10px;
    background:rgba(240,136,62,.05)}
  .code{font-family:var(--mono);font-size:26px;font-weight:800;letter-spacing:5px;color:var(--accent2);
    margin:8px 0;cursor:pointer;user-select:all}
  button.btn[disabled]{opacity:.45;cursor:not-allowed}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand">⎇ git<span class="dot">.</span><span class="sub">change tracker · mod protocol</span></div>
    <div class="seg">
      <button id="t-changes" class="on" onclick="setView('changes')">Changes</button>
      <button id="t-repos" onclick="setView('repos')">Repos</button>
      <button id="t-search" onclick="setView('search')">Search</button>
      <button id="t-github" onclick="setView('github')">GitHub</button>
      <button id="t-access" onclick="setView('access')">Access</button>
    </div>
    <span class="grow"></span>
    <span class="pill" id="sidebtn" onclick="toggleSide()">☰ commits</span>
    <span class="sub" id="who">read-only</span>
    <button class="btn" onclick="loadAll()">↻</button>
  </div>
  <div class="row" id="repopills" style="margin-top:10px"></div>
</header>
<div id="scrim" onclick="toggleSide()"></div>
<div class="wrap">
<aside id="side"></aside>
<main>
  <div class="view on" id="v-changes"></div>
  <div class="view" id="v-commit"></div>
  <div class="view" id="v-repos"></div>
  <div class="view" id="v-search"></div>
  <div class="view" id="v-github"></div>
  <div class="view" id="v-access"></div>
</main>
</div>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const BASE=location.pathname.replace(/\/+$/,'').replace(/\/index\.html$/,'');
const api=p=>BASE+p;
const TABS=['changes','repos','search','github','access'];
let VIEW='changes', REPO=null, REPOS={}, TOKEN='', ME=null, WALLET='', EXP=0;
let SHA=null, SIDE_N=40, SIDE=true, CQ='';  // commit, how many to list, sidebar open, filter
const narrow=()=>matchMedia('(max-width:820px)').matches;
// the log is open by default on anything wide enough to hold it; on a phone it is a
// drawer over the content, so there it starts closed unless you asked for it
try{ TOKEN=localStorage.getItem('git.token')||''; WALLET=localStorage.getItem('git.wallet')||'';
     EXP=Number(localStorage.getItem('git.exp')||0);
     SIDE=narrow()?localStorage.getItem('git.commits')==='1':localStorage.getItem('git.commits')!=='0'; }catch(e){}
document.body.classList.toggle('noside',!SIDE);
// the header wraps at narrow widths — measure it so the sidebar always hangs off its real bottom
function fitHeader(){document.documentElement.style.setProperty('--hh',$('header').offsetHeight+'px')}
addEventListener('resize',fitHeader);fitHeader();

function esc(s){return (''+(s??'')).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function ago(d){if(!d)return'';const s=(Date.now()-new Date(d))/1e3;
  if(s<60)return (s|0)+'s ago';if(s<3600)return (s/60|0)+'m ago';if(s<86400)return (s/3600|0)+'h ago';
  if(s<2592000)return (s/86400|0)+'d ago';return new Date(d).toISOString().slice(0,10)}
function hdrs(json){const h=json?{'Content-Type':'application/json'}:{};if(TOKEN)h['Authorization']='Bearer '+TOKEN;return h}
async function GET(p){const r=await fetch(api(p),{headers:hdrs(false)});const j=await r.json();
  if(!r.ok)throw new Error(j.error||r.status);return j}
async function POST(p,body,noretry){
  const r=await fetch(api(p),{method:'POST',headers:hdrs(true),body:JSON.stringify(body||{})});
  const j=await r.json();
  if(!r.ok){
    // the session died (or was revoked) mid-use — re-sign once and finish the job
    // instead of dropping the user back on the Access tab
    if(!noretry&&r.status===403&&WALLET&&/expired|no longer valid|missing Authorization/i.test(j.error||'')
       &&await reauth())return POST(p,body,true);
    throw new Error(j.error||r.status);}
  return j}

function setView(v){VIEW=v;
  for(const t of TABS)$('#t-'+t).classList.toggle('on',t===v);
  for(const t of TABS.concat('commit'))$('#v-'+t).classList.toggle('on',t===v);
  if(v!=='commit'){SHA=null;paintSide();}
  load(v);}

// --- commit sidebar ----------------------------------------------------------
function toggleSide(){SIDE=!SIDE;document.body.classList.toggle('noside',!SIDE);
  $('#sidebtn').classList.toggle('active',SIDE);
  try{localStorage.setItem('git.commits',SIDE?'1':'0');}catch(e){}
  if(SIDE)loadSide();}
let LOG=[], WORK=null, LOADING=false, SEQ=0, CQT=null;
// The log paints twice: `git log` alone comes back instantly, `--shortstat` has to
// diff every commit (~5s on the mod repo) — so show the commits first and let the
// +/− counts land a moment later. SEQ drops whichever answer is no longer current.
async function loadSide(){
  const seq=++SEQ, url=s=>'/api/commits?stat='+s+'&n='+SIDE_N
    +'&repo='+encodeURIComponent(REPO||'')+(CQ?'&search='+encodeURIComponent(CQ):'');
  LOADING=true;paintSide();
  try{
    LOG=await GET(url(0));if(seq!==SEQ)return;
    LOADING=false;paintSide();
    if(!SIDE)return;                       // hidden: the count is enough, skip the stats
    const full=await GET(url(1));if(seq!==SEQ)return;
    LOG=full;paintSide();
  }catch(e){if(seq!==SEQ)return;LOG=[];LOADING=false;paintSide();
    $('#sidelist').innerHTML=`<div class="err" style="padding:14px 8px">${esc(e.message)}</div>`;}}
function mountSide(){
  $('#side').innerHTML=`<div class="sidehead">
      <h3>commits <span class="sub" id="siderepo"></span><span class="grow"></span>
        <span class="pill" id="sidecount" title="load more" onclick="moreSide()">…</span></h3>
      <input id="cq" placeholder="filter commits…" value="${esc(CQ)}" oninput="qSide(this.value)"/>
    </div><div id="sidelist"></div>`;}
function qSide(v){CQ=v.trim();clearTimeout(CQT);CQT=setTimeout(loadSide,250);}
function moreSide(){SIDE_N=SIDE_N>=400?40:SIDE_N*2;loadSide();}
function paintSide(){
  if(!$('#sidelist'))mountSide();
  const w=WORK;
  $('#siderepo').textContent=REPO||'';
  $('#sidecount').textContent=LOADING?'…':LOG.length+(LOG.length>=SIDE_N?'+':'');
  $('#sidebtn').innerHTML='☰ commits'+(LOG.length?` <span class="n">${LOG.length}</span>`:'');
  $('#sidelist').innerHTML=`${w&&!CQ?`<div class="citem work ${VIEW==='changes'?'on':''}" onclick="setView('changes')">
      <div class="msg">${w.clean?'✓ working tree clean':'● '+w.total+' uncommitted'}</div>
      <div class="meta"><span>⎇ ${esc(w.branch)}</span>${w.clean?'':`<span class="add">+${w.additions}</span><span class="del">−${w.deletions}</span>`}
        ${w.ahead?`<span>↑${w.ahead}</span>`:''}</div></div>`:''}
    ${LOG.map(c=>`<div class="citem ${SHA===c.full_hash?'on':''}" onclick="openCommit('${esc(c.full_hash)}')">
      <div class="msg">${esc(c.message)}</div>
      <div class="meta"><span class="sha">${esc(c.hash)}</span><span>${esc(c.author)}</span><span>${ago(c.date)}</span>
        ${c.additions||c.deletions?`<span class="add">+${fmtn(c.additions)}</span><span class="del">−${fmtn(c.deletions)}</span>`:''}</div>
      </div>`).join('')
      ||`<div class="sub" style="padding:8px">${LOADING?'reading the log…':(CQ?'no commit matches “'+esc(CQ)+'”':'no commits')}</div>`}`;}
function openCommit(sha){SHA=sha;if(narrow()&&SIDE)toggleSide();setView('commit');}

async function loadPills(){
  try{REPOS=await GET('/api/repos');}catch(e){REPOS={};}
  const names=Object.keys(REPOS);
  if(!REPO||!names.includes(REPO))REPO=names.includes('mod')?'mod':names[0];
  $('#repopills').innerHTML=names.map(n=>{const r=REPOS[n];
    return `<span class="pill ${REPO===n?'active':''} ${r.changes?'dirty':''}" onclick="pickRepo('${n}')">
      ${esc(n)} <span class="n">${r.error?'!':(r.changes??'?')}</span></span>`}).join('');
  fitHeader();}   // the pills row just changed the header's height
function pickRepo(n){REPO=n;SHA=null;loadPills();loadSide();load(VIEW==='commit'?'changes':VIEW);
  if(VIEW==='commit')setView('changes');}

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
      WORK={branch:c.branch,total:c.total,clean:c.clean,ahead:c.ahead,
            additions:c.additions,deletions:c.deletions};
      el.innerHTML=`<div class="card">
        <div class="stats">
          <span>⎇ <b>${esc(c.branch)}</b> @ <span class="sha">${esc(c.commit)}</span></span>
          <span><b>${c.total}</b> changed</span>
          <span class="add">+${c.additions}</span><span class="del">−${c.deletions}</span>
          ${c.ahead!=null?`<span>↑${c.ahead} ↓${c.behind}</span>`:''}
          ${c.clean?'<span class="ok">✓ clean</span>':''}
          <span class="grow"></span>
          <button class="btn" onclick="showDiff()">full diff</button>
          ${ME&&ME.ok?`<button class="btn" onclick="doPull()">pull</button>`:''}
          ${ME&&ME.ok&&c.clean?`<button class="btn ${c.ahead?'primary':''}" onclick="doPush()">push${c.ahead?' ↑'+c.ahead:''}</button>`:''}
        </div>
        ${ME&&ME.ok&&!c.clean?`<div class="composer">
          <textarea id="cmsg" rows="3" placeholder="commit message — leave it empty and the agent writes one from the diff"></textarea>
          <div class="row">
            <button class="btn" onclick="agentMsg()">✦ let the agent write it</button>
            <label class="sub" title="run it on the agent's free models"><input type="checkbox" id="cfree"/> free</label>
            <span class="sub" id="cmsginfo"></span><span class="grow"></span>
            <button class="btn" onclick="doCommit()">commit</button>
            <button class="btn primary" onclick="doPush()">commit + push</button>
          </div></div>`:''}
        ${c.files.length?`<table><tr><th>file</th><th>status</th><th>+</th><th>−</th></tr>
        ${c.files.map(f=>`<tr><td class="file" onclick="showDiff('${esc(f.file)}')">${esc(f.file)}</td>
          <td><span class="st ${f.status}">${f.status.toUpperCase()}${f.staged?' ●':''}</span></td>
          <td class="add">${f.additions??''}</td><td class="del">${f.deletions??''}</td></tr>`).join('')}
        </table>`:'<div class="empty">working tree clean</div>'}
      </div><div class="card" id="diffbox" style="display:none"></div>`;
      paintSide();
    }
    if(v==='commit'){
      const c=await GET('/api/show?repo='+encodeURIComponent(REPO||'')+'&hash='+encodeURIComponent(SHA||'HEAD'));
      SHA=c.full_hash;paintSide();
      el.innerHTML=`<div class="card">
        <div style="font-size:15px;font-weight:600">${esc(c.subject)}</div>
        ${c.body?`<pre class="diff" style="margin-top:8px;max-height:200px">${esc(c.body)}</pre>`:''}
        <div class="stats" style="margin-top:10px">
          <span class="sha">${esc(c.hash)}</span><span>${esc(c.author)}</span><span>${ago(c.date)}</span>
          <span><b>${c.total}</b> files</span><span class="add">+${c.additions}</span><span class="del">−${c.deletions}</span>
          ${c.parents.length>1?'<span class="badge private">MERGE</span>':''}
          <span class="grow"></span>
          ${c.parents[0]?`<button class="btn" onclick="openCommit('${esc(c.parents[0])}')">← parent</button>`:''}
        </div>
        ${c.files.length?`<table><tr><th>file</th><th>status</th><th>+</th><th>−</th></tr>
        ${c.files.map(f=>`<tr><td class="file" onclick="showCommitDiff('${esc(f.file)}')">${esc(f.file)}</td>
          <td><span class="st ${f.status}">${f.status.toUpperCase()}</span></td>
          <td class="add">${f.additions??''}</td><td class="del">${f.deletions??''}</td></tr>`).join('')}
        </table>`:'<div class="empty">no files</div>'}
      </div>
      <div class="card"><h3>diff${c.truncated?' · truncated':''}</h3>
        <pre class="diff" id="cdiff">${diffHtml(c.diff)||'(empty)'}</pre></div>`;
    }
    if(v==='repos'){
      const rs=await GET('/api/repos');
      el.innerHTML=`<div class="card"><h3>track a repo</h3>
        <div class="row"><input id="trk" placeholder="local path, owner/repo, or github URL" style="flex:1;min-width:240px"/>
        <input id="trkb" placeholder="branch (optional)" style="width:140px"/>
        <button class="btn primary" onclick="doTrack()">+ track</button></div>
        <div class="sub" style="margin-top:6px">GitHub repos are cloned into ~/.mod/git/repos — private ones work once GitHub is connected. Requires write access.</div></div>
        <div class="card"><h3>fork a repo</h3>
        <div class="row"><input id="frk" placeholder="owner/repo, github URL, or a tracked repo" style="flex:1;min-width:240px"/>
        <input id="frkn" placeholder="new name (optional)" style="width:170px"/>
        <input id="frko" placeholder="into org (optional)" style="width:160px"/>
        <button class="btn primary" onclick="doFork()">⑂ fork</button></div>
        <div class="sub" style="margin-top:6px">Forks with your <b>active</b> GitHub account, then tracks the clone — anything with a GitHub origin works, this repo included.</div></div>
        <div class="card"><table><tr><th>repo</th><th>branch</th><th>changes</th><th>path / url</th><th></th></tr>
        ${Object.entries(rs).map(([n,r])=>`<tr>
          <td><b>${esc(n)}</b></td><td>${esc(r.branch||'')}</td>
          <td>${r.error?`<span class="err">${esc(r.error)}</span>`:(r.clean?'<span class="ok">clean</span>':`<b>${r.changes}</b>`)}</td>
          <td class="mono">${esc(r.path||'')}${r.url?`<br><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a>`:''}</td>
          <td class="row" style="gap:6px;justify-content:flex-end">
            ${ME&&ME.ok&&r.url?`<button class="btn" onclick="doFork('${esc(n)}')">⑂ fork</button>`:''}
            ${n!=='mod'?`<button class="btn danger" onclick="doUntrack('${esc(n)}')">untrack</button>`:''}</td>
        </tr>`).join('')}</table></div>`;
    }
    if(v==='search'){
      el.innerHTML=`<div class="card"><h3>search github</h3>
        <div class="row">
          <input id="sq" placeholder="anything — name, description, topic…" style="flex:1;min-width:220px"
            value="${esc(SQ)}" onkeydown="if(event.key==='Enter')doSearch()"/>
          <input id="slang" placeholder="language" style="width:120px" value="${esc(SLANG)}"/>
          <input id="suser" placeholder="owner / org" style="width:140px" value="${esc(SUSER)}"/>
          <select id="ssort">${[['','best match'],['stars','most stars'],['updated','recently updated'],['forks','most forks']]
            .map(([v2,l])=>`<option value="${v2}">${l}</option>`).join('')}</select>
          <button class="btn primary" onclick="doSearch()">search</button></div>
        <div class="row" style="margin-top:8px"><span class="sub">try</span>
          ${['topic:mcp','language:python stars:>5000','agent framework','pushed:>2026-06-01 stars:>100']
            .map(q=>`<span class="pill" onclick="sChip('${esc(q)}')">${esc(q)}</span>`).join('')}</div>
        <div class="sub" style="margin-top:8px">Every public repo on GitHub, through its search API — then
          <b>track</b> one to clone it here and follow its changes, or <b>⑂ fork</b> it to your account.
          GitHub's qualifiers work inside the query (<span class="mono">topic:</span>,
          <span class="mono">stars:&gt;500</span>, <span class="mono">pushed:&gt;2026-01-01</span>,
          <span class="mono">org:</span>). Anonymous searches are capped at 10 a minute; connecting a
          GitHub account raises that and searches your private repos too.</div></div>
      <div id="sres"></div>`;
      $('#ssort').value=SSORT;
      paintSearch();
    }
    if(v==='github'){
      const [g,o]=await Promise.all([GET('/api/github'),GET('/api/oauth')]);
      let repos='';
      if(g.connected){try{
        const rs=await GET('/api/github/repos?n=100');
        repos=repoCard(`your repos (${rs.length})`,rs);
      }catch(e){repos=`<div class="card err">${esc(e.message)}</div>`}}
      const signedIn=!!(ME&&ME.ok), admin=signedIn&&(ME.role==='admin'||ME.role==='owner');
      const accts=g.accounts||[];
      // every account this key has connected — click one to make it active
      const chips=accts.length?`<div class="row" style="margin-bottom:12px">
          ${accts.map(a=>`<span class="pill acct ${a.active?'active':''}"
            title="${esc(a.name||'')} · ${esc(a.via)} · ${esc(a.token_tail)}" onclick="ghSwitch('${esc(a.login)}')">
            ${esc(a.login)}${a.active?' <span class="n">active</span>':''}
            <span class="x" onclick="event.stopPropagation();ghDisconnect(null,'${esc(a.login)}')">✕</span></span>`).join('')}
          ${signedIn?`<button class="btn" onclick="$('#addbox').style.display='block'">+ add account</button>`:''}
        </div>`:'';
      const connect=`<div id="addbox" style="display:${accts.length?'none':'block'}">
          <div class="sub" style="margin-bottom:10px">Authorize GitHub and it is attached to your key
            <span class="mono">${esc(g.key)}</span> — connect as many accounts as you like and switch
            between them; clones, pulls, pushes and forks run as whichever is active.
            Tokens are stored off-chain in ~/.mod/git (0600) and never leave the box.</div>
          ${!signedIn?`<div class="err" style="text-align:left;padding:0 0 10px">sign in with your key on the <b>Access</b> tab first</div>`:''}
          <div class="row">
            ${o.web_flow?`<button class="btn primary" ${signedIn?'':'disabled'} onclick="ghWeb()">Connect with GitHub</button>`:''}
            ${o.device_flow?`<button class="btn ${o.web_flow?'':'primary'}" ${signedIn?'':'disabled'} onclick="ghDevice()">${o.web_flow?'use a device code':'Connect with GitHub (device code)'}</button>`:''}
            <button class="btn" onclick="$('#patrow').style.display='flex';$('#pathint').style.display='block'">use a token instead</button>
          </div>
          <div class="row" id="patrow" style="display:none;margin-top:10px">
            <input id="pat" type="password" placeholder="ghp_… / github_pat_…" style="flex:1;min-width:260px"/>
            <button class="btn" onclick="ghConnect()">attach token</button></div>
          <div class="sub" id="pathint" style="display:none;margin-top:6px">don't have one? make a
            <a href="https://github.com/settings/tokens/new?scopes=repo,read:org&description=mod%20git" target="_blank">classic token</a>
            (scopes <span class="mono">repo</span>, <span class="mono">read:org</span>) or a
            <a href="https://github.com/settings/personal-access-tokens/new" target="_blank">fine-grained one</a>
            (repository permissions — contents: read/write, metadata: read). GitHub shows it once, so copy it before you leave.</div>
          <div id="oauthbox"></div>
          ${!o.configured?'<div class="sub" style="margin-top:8px">no OAuth app configured yet — see below</div>':''}
          ${g.env_token?'<div class="sub" style="margin-top:6px">($GITHUB_TOKEN from the environment is being used as a fallback)</div>':''}</div>`;
      const conn=chips+(g.connected?`<div class="kv">
          <span class="k">active account</span><span><b>${esc(g.login)}</b> ${esc(g.name||'')}
            <span class="badge ${g.via==='oauth'?'admin':'private'}">${esc((g.via||'pat').toUpperCase())}</span></span>
          <span class="k">attached to key</span><span class="mono">${esc(g.key)}</span>
          <span class="k">token</span><span class="mono">${esc(g.token_tail)} · scopes: ${esc(g.scopes||'(fine-grained)')}</span>
          <span class="k">api rate</span><span>${g.rate&&g.rate.remaining!=null?g.rate.remaining+' / '+g.rate.limit:esc(JSON.stringify(g.rate))}</span>
          </div><div class="row" style="margin-top:10px">
          <button class="btn danger" onclick="ghDisconnect()">disconnect ${esc(g.login)}</button></div>`:'')+connect;
      const keys=g.keys.length?`<div class="card"><h3>attached keys (${g.keys.length})</h3><table>
        <tr><th>key</th><th>github</th><th>via</th><th>since</th><th></th></tr>
        ${g.keys.map(k=>`<tr><td class="mono">${esc(k.key)}${k.key===g.key?' <span class="badge write">YOU</span>':''}</td>
          <td>${esc(k.login||'')}${k.accounts>1?` <span class="sub">+${k.accounts-1} more</span>`:''}</td>
          <td class="sub">${esc(k.via)}</td>
          <td class="sub">${k.connected_at?ago(k.connected_at*1000):''}</td>
          <td>${(k.key===g.key||admin)?`<button class="btn danger" onclick="ghDisconnect('${esc(k.key)}')">disconnect</button>`:''}</td>
        </tr>`).join('')}</table></div>`:'';
      const setup=(!o.configured||admin)?`<div class="card"><h3>oauth app${o.configured?' · configured':''}</h3>
        <div class="sub" style="margin-bottom:8px">One <a href="${esc(o.register)}" target="_blank">GitHub OAuth app</a> serves every key on this box.
          Homepage URL <span class="mono">${esc(location.origin+BASE)}</span> ·
          Authorization callback URL <span class="mono">${esc(cbUrl())}</span> · tick <b>Enable Device Flow</b>.
          Credentials are stored in ~/.mod/git/oauth.json (0600); the secret only enables the redirect flow.
          ${o.configured?`Current client id <span class="mono">${esc(o.client_id)}</span> · scope <span class="mono">${esc(o.scope)}</span>.`:''}</div>
        <div class="row"><input id="cid" placeholder="client id" style="flex:1;min-width:200px"/>
          <input id="csec" type="password" placeholder="client secret (optional)" style="flex:1;min-width:200px"/>
          <button class="btn primary" ${admin?'':'disabled'} onclick="saveApp()">save</button></div>
        ${admin?'':'<div class="sub" style="margin-top:6px">admin access required to set this</div>'}</div>`:'';
      el.innerHTML=`<div class="card"><h3>github accounts${accts.length?' ('+accts.length+')':''}</h3>${conn}</div>${keys}${setup}${repos}`;
    }
    if(v==='access'){
      const a=await GET('/api/access');
      const ses=(ME&&ME.ok)?await GET('/api/sessions').catch(()=>null):null;
      const host=a.host_owner&&a.host_owner.toLowerCase()!==(a.owner||'').toLowerCase()?a.host_owner:null;
      el.innerHTML=`<div class="card"><h3>your key</h3>
        <div class="sub" style="margin-bottom:8px">Reads are open. Writes need a signed token from the shared auth module — sign in with your wallet, or run <span class="mono">m git/token</span> on the server and paste it here. One signature is enough: it becomes a 30-day session, so committing and pushing later never reopens your wallet.</div>
        <div class="row" style="margin-bottom:8px">
          <button class="btn primary" onclick="signInWallet()">⬡ sign in with wallet</button>
          ${WALLET?`<span class="sub">last signed as <span class="mono">${esc(short(WALLET))}</span></span>`:''}
          ${TOKEN?'<span class="grow"></span><button class="btn" onclick="clearToken()">sign out</button>':''}</div>
        <div class="row"><input id="tok" type="password" placeholder="signed token" style="flex:1;min-width:260px" value="${esc(TOKEN)}"/>
        <button class="btn" onclick="saveToken()">use token</button></div>
        <div class="sub" id="tokmsg" style="margin-top:6px">${sessionMsg()}</div></div>
      ${ses&&ses.sessions.length?`<div class="card"><h3>signed-in sessions (${ses.total})</h3>
        <div class="sub" style="margin-bottom:8px">Every browser you kept signed in as <span class="mono">${esc(short(ses.address))}</span>. Ending one sends it back to a wallet signature; revoking the key's access ends them all at once.</div>
        <table><tr><th>where</th><th>last used</th><th>expires</th><th></th></tr>
        ${ses.sessions.map(x=>`<tr><td>${esc(x.label||'app')}${isSession(TOKEN)&&TOKEN.split('.')[1]===x.id?' <span class="badge private">THIS ONE</span>':''}</td>
          <td class="sub">${esc(ago((x.used||x.created)*1000))}</td>
          <td class="sub">${esc(until(x.expires)||'now')}</td>
          <td><button class="btn danger" onclick="doSignOut('${esc(x.id)}')">end</button></td></tr>`).join('')}</table>
        <div class="row" style="margin-top:10px"><button class="btn" onclick="doSignOut()">end all my sessions</button></div></div>`:''}
      <div class="card"><h3>owner${host?'s':''}</h3><div class="row"><span class="mono">${esc(a.owner)}</span><span class="badge owner">OWNER</span></div>
        ${host?`<div class="row" style="margin-top:6px"><span class="mono">${esc(host)}</span><span class="badge owner">HOST OWNER</span></div>
        <div class="sub" style="margin-top:8px">Whoever owns this mod host is an owner here too — sign in with that wallet and you can commit and push without granting yourself anything.</div>`:''}
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
  let gh='';
  try{const a=await GET('/api/accounts');
    if(a.active)gh=` · <span title="active github account (${a.accounts.length} connected)">⑂ ${esc(a.active)}</span>`;
  }catch(e){}
  $('#who').innerHTML=(ME&&ME.ok
    ?`<span class="ok">${esc(short(ME.address))} · ${esc(ME.role)}</span>`
    : `<span class="pill" onclick="setView('access')">${TOKEN?'session expired':'read-only'} · sign in</span>`)+gh;}

function saveToken(){TOKEN=$('#tok').value.trim();WALLET='';EXP=0;
  try{localStorage.setItem('git.token',TOKEN);localStorage.removeItem('git.wallet');
      localStorage.removeItem('git.exp');}catch(e){}
  // a pasted `m git/token` is good for an hour too — trade it for a session as well
  whoami().then(async()=>{if(ME&&ME.ok)await keepSignedIn('pasted token');
    await whoami();load('access')})}
function clearToken(){const dead=TOKEN;TOKEN='';WALLET='';EXP=0;
  try{localStorage.removeItem('git.token');localStorage.removeItem('git.wallet');
      localStorage.removeItem('git.exp');}catch(e){}
  if(isSession(dead))fetch(api('/api/signout'),{method:'POST',   // kill it server-side too
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+dead},body:'{}'}).catch(()=>{});
  whoami().then(()=>load('access'))}

// --- sign in with a wallet ---------------------------------------------------
// A token is base64url of {data,time,key,signature}, where the signature is an
// EIP-191 personal_sign over the compact {"data":…,"time":…} — the same bytes
// `m git/token` signs with the box key, so a wallet mints its own for its own
// address and the server verifies it statelessly. Own the host and that alone
// makes you an owner here: no grant, nothing to paste.
const eth=()=>window.ethereum;
const short=a=>a?a.slice(0,6)+'…'+a.slice(-4):'';
function b64url(o){return btoa(unescape(encodeURIComponent(JSON.stringify(o))))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')}
function tokenAge(t){try{const j=JSON.parse(decodeURIComponent(escape(
    atob(t.replace(/-/g,'+').replace(/_/g,'/')))));return Date.now()/1000-Number(j.time)}catch(e){return null}}
const isSession=t=>!!t&&t.indexOf('gits.')===0;
function until(ts){if(!ts)return'';const s=ts-Date.now()/1000;if(s<=0)return'';
  if(s<3600)return Math.round(s/60)+'m';if(s<86400)return Math.round(s/3600)+'h';
  return Math.round(s/86400)+'d'}
// sign the {data,time} bytes the server verifies — nothing is stored by this alone
async function walletToken(){const p=eth();
  if(!p)throw new Error('no wallet found — install MetaMask, or paste a token from `m git/token`');
  const accs=await p.request({method:'eth_requestAccounts'});
  const addr=String((accs||[])[0]||'').toLowerCase();
  if(!addr)throw new Error('no account selected');
  const data={mod:'git'}, time=(Date.now()/1000).toString();
  const signature=await p.request({method:'personal_sign',
    params:[JSON.stringify({data,time}),addr]});   // must match the server's sig_data
  WALLET=addr;try{localStorage.setItem('git.wallet',addr);}catch(e){}
  return b64url({data,time,key:addr,signature});}
function setToken(t,exp){TOKEN=t;EXP=exp||0;
  try{localStorage.setItem('git.token',t);
      exp?localStorage.setItem('git.exp',String(exp)):localStorage.removeItem('git.exp');}catch(e){}}
// swap the hour-long signature for a session: the whole point is that pushing
// later today, or next week, never opens the wallet again
async function keepSignedIn(label){
  try{const s=await POST('/api/session',{label:label||'app'},true);setToken(s.token,s.expires);return s}
  catch(e){return null}}
async function reauth(){if(!eth()||!WALLET)return false;
  try{setToken(await walletToken(),0);return !!await keepSignedIn('app');}catch(e){return false}}
function signInWallet(){
  act(async()=>{
    setToken(await walletToken(),0);
    await whoami();
    if(ME&&ME.ok)await keepSignedIn('app');
    await whoami();load('access');
    toast(ME&&ME.ok?'signed in as '+short(WALLET)+' · '+ME.role
        +(isSession(TOKEN)?' — staying signed in'+(until(EXP)?' for '+until(EXP):''):'')
      :(ME&&ME.error)||'signed in, but this key has no access yet',!(ME&&ME.ok));})}
function doSignOut(id){act(async()=>{await POST('/api/signout',{id:id});
  if(id&&isSession(TOKEN)&&TOKEN.split('.')[1]===id)return clearToken();
  await whoami();load('access')})}
// the session line under the sign-in row: who you are, or why you aren't
function sessionMsg(){
  if(!TOKEN)return 'not signed in';
  if(ME&&ME.ok){const age=isSession(TOKEN)?null:tokenAge(TOKEN);
    return `<span class="ok">✓ ${esc(ME.address)} · ${esc(ME.role)}</span>`+
      (isSession(TOKEN)
        ? ` <span class="sub">· signed in${until(EXP)?' for another '+until(EXP):''} — commit and push
            without signing again</span>`
        : (age!=null?` <span class="sub">· raw signature, expires in ${Math.max(0,60-Math.round(age/60))}m
            (sign in with your wallet to keep it)</span>`:''));}
  return `<span class="err">${esc((ME&&ME.error)||'not signed in')}</span>`+
    (WALLET?' <span class="sub">— sign in again and this browser stays signed in for 30 days</span>':'');}
let TT=null;
function toast(msg,err){const t=$('#toast');t.className=err?'err':'';t.textContent=msg;
  t.style.display='block';clearTimeout(TT);TT=setTimeout(()=>t.style.display='none',err?9000:6000)}
async function act(fn){try{await fn();}catch(e){toast(e.message,true)}}
async function showCommitDiff(file){
  const el=$('#cdiff');el.textContent='loading…';
  try{const c=await GET('/api/show?repo='+encodeURIComponent(REPO||'')+'&hash='+encodeURIComponent(SHA)
        +'&file='+encodeURIComponent(file));
    el.innerHTML=diffHtml(c.diff)||'(no diff — binary?)';
  }catch(e){el.innerHTML=`<span class="d">${esc(e.message)}</span>`}}
function doTrack(){act(async()=>{await POST('/api/track',{repo:$('#trk').value.trim(),branch:$('#trkb').value.trim()||null});loadPills();load('repos')})}
function trackGh(r){toast('cloning '+r+'…');
  act(async()=>{await POST('/api/track',{repo:r});
    const hit=(SRES||[]).find(x=>x.repo===r);if(hit)hit.tracked=true;   // don't re-search to grey a button
    loadPills();load(VIEW);toast('tracking '+r)})}

// --- searching github --------------------------------------------------------
// One table for every list of GitHub repos — yours on the GitHub tab, and
// anyone's from the search API. Track clones it here; fork copies it to your account.
let SQ='', SLANG='', SUSER='', SSORT='', SRES=null, SERR=null, SBUSY=false;
const fmtn=n=>n==null?'':(n>=1000?(n/1000).toFixed(n>=10000?0:1)+'k':''+n);
function repoCard(title,rows){return `<div class="card"><h3>${title}</h3>
  ${ME&&ME.ok?'':'<div class="sub" style="margin-bottom:8px">sign in on the <b>Access</b> tab to track or fork any of these</div>'}
  <table><tr><th>repo</th><th>about</th><th>pushed</th><th></th></tr>
  ${rows.map(r=>`<tr>
    <td><a href="${esc(r.url)}" target="_blank">${esc(r.repo)}</a>
      ${r.private?' <span class="badge private">PRIVATE</span>':''}
      <div class="sub">${r.stars?'★ '+fmtn(r.stars)+' · ':''}${r.language?esc(r.language)+' · ':''}${r.fork?'fork · ':''}${fmtn(r.forks||0)} forks</div></td>
    <td class="sub">${esc(r.description||'')}</td>
    <td class="sub">${ago(r.pushed_at||r.updated_at)}</td>
    <td class="row" style="gap:6px;justify-content:flex-end">
      ${ME&&ME.ok?`<button class="btn" title="fork to your github account" onclick="doFork('${esc(r.repo)}')">⑂</button>`:''}
      ${r.tracked?'<span class="ok">tracked</span>'
        :(ME&&ME.ok?`<button class="btn" onclick="trackGh('${esc(r.repo)}')">track</button>`:'')}</td>
  </tr>`).join('')}</table></div>`;}
function sChip(q){SQ=q;const i=$('#sq');if(i)i.value=q;doSearch()}
function doSearch(){
  SQ=val('#sq')||'';SLANG=val('#slang')||'';SUSER=val('#suser')||'';SSORT=$('#ssort').value;
  if(!SQ&&!SLANG&&!SUSER)return toast('search for what? a name, a topic, an org…',true);
  SBUSY=true;SERR=null;paintSearch();
  (async()=>{try{
      SRES=await GET('/api/search?n=50&q='+encodeURIComponent(SQ)
        +(SLANG?'&language='+encodeURIComponent(SLANG):'')
        +(SUSER?'&user='+encodeURIComponent(SUSER):'')+(SSORT?'&sort='+SSORT:''));
    }catch(e){SRES=null;SERR=e.message;}
    SBUSY=false;paintSearch();})();}
function paintSearch(){const el=$('#sres');if(!el)return;
  el.innerHTML=SBUSY?'<div class="empty">searching github…</div>'
    :SERR?`<div class="card"><div class="err">${esc(SERR)}</div></div>`
    :!SRES?'<div class="empty">nothing searched yet</div>'
    :(SRES.length?repoCard(`results (${SRES.length})`,SRES)
      :'<div class="empty">no repo matched — fewer words, or drop a qualifier</div>');}
function val(id){const e=$(id);return e&&e.value.trim()?e.value.trim():null}
function doFork(pre){const repo=pre||val('#frk');if(!repo)return toast('what should I fork?',true);
  toast('⑂ forking '+repo+' — github builds it, this takes a few seconds…');
  act(async()=>{const r=await POST('/api/fork',
      {repo,name:pre?null:val('#frkn'),org:pre?null:val('#frko')});
    loadPills();load(VIEW);
    toast('⑂ '+r.fork+(r.tracked?' — tracked as '+r.tracked.tracked:
      (r.ready?'':' — created, still building on github')))})}
function doUntrack(n){if(confirm('untrack '+n+'?'))act(async()=>{await POST('/api/untrack',{name:n});loadPills();load('repos')})}
function doPull(){act(async()=>{await POST('/api/pull',{repo:REPO});load('changes')})}
// commit message: whatever is in the box, or null → the agent writes one
function cmsg(){const t=$('#cmsg');return t&&t.value.trim()?t.value.trim():null}
function cfree(){const c=$('#cfree');return !!(c&&c.checked)}
function said(r){const i=$('#cmsginfo');if(i)i.innerHTML=r.by==='agent'
  ?`<span class="ok">✦ ${esc(r.model||'agent')}</span>`
  :(r.by==='fallback'?`<span class="err">agent unavailable — ${esc(r.error||'')}</span>`:'');}
function agentMsg(){const i=$('#cmsginfo');if(i)i.textContent='✦ reading the diff…';
  act(async()=>{const r=await POST('/api/message',{repo:REPO,free:cfree()});$('#cmsg').value=r.message;said(r)})}
function doCommit(){act(async()=>{const r=await POST('/api/commit',{repo:REPO,msg:cmsg(),free:cfree()});
  loadPills();load('changes');alert(`${r.hash} — ${r.message}`)})}
function doPush(){act(async()=>{const r=await POST('/api/push',{repo:REPO,msg:cmsg(),free:cfree()});
  loadPills();load('changes');
  alert((r.committed?`${r.hash} — ${r.message}\n\n`:'')+(r.push||(r.pushed?'pushed':'nothing pushed'))
    +(r.hint?`\n\n⚠ ${r.hint}`:''));
  if(r.hint&&/GitHub account/.test(r.hint))setView('github')})}
function ghConnect(){act(async()=>{await POST('/api/connect',{token:$('#pat').value.trim()});
  load('github');whoami()})}
function ghSwitch(login){act(async()=>{const g=await POST('/api/switch',{login});load('github');whoami();
  toast('now acting as '+g.login)})}
function ghDisconnect(key,login){
  if(!confirm('disconnect '+(login||'the active GitHub account')+(key?' from '+key:'')+'?'))return;
  act(async()=>{const r=await POST('/api/disconnect',Object.assign(key?{address:key}:{},login?{login}:{}));
    load('github');whoami();toast(r.was?'disconnected '+r.was:'nothing to disconnect')})}
function saveApp(){act(async()=>{await POST('/api/oauth/app',{client_id:$('#cid').value.trim(),
  client_secret:$('#csec').value.trim()||null});load('github')})}

// --- connect with github -----------------------------------------------------
function cbUrl(){return location.origin+BASE+'/oauth/callback'}
function ghWeb(){act(async()=>{const r=await POST('/api/oauth/url',{redirect_uri:cbUrl()});location.href=r.url})}
function ghDevice(){const box=$('#oauthbox');box.innerHTML='<div class="sub" style="margin-top:10px">asking github…</div>';
  act(async()=>{const s=await POST('/api/oauth/start',{});
    box.innerHTML=`<div class="devicebox">
      <div class="sub">1 · open <a href="${esc(s.verification_uri)}" target="_blank">${esc(s.verification_uri)}</a>
        &nbsp;2 · enter this code (click to copy)</div>
      <div class="code" onclick="navigator.clipboard&&navigator.clipboard.writeText('${esc(s.user_code)}')">${esc(s.user_code)}</div>
      <div class="sub" id="pollmsg">waiting for you to approve — attaching to <span class="mono">${esc(s.key)}</span></div>
    </div>`;
    pollDevice(s);})}
function pollDevice(s){const t0=Date.now();
  const tick=async()=>{
    if(VIEW!=='github'||!$('#pollmsg'))return;              // user walked away
    if(Date.now()-t0>s.expires_in*1000){$('#pollmsg').innerHTML='<span class="err">code expired — try again</span>';return}
    try{const r=await POST('/api/oauth/poll',{session:s.session});
      if(r.status==='connected'){load('github');whoami();return}
    }catch(e){$('#pollmsg').innerHTML=`<span class="err">${esc(e.message)}</span>`;return}
    setTimeout(tick,(s.interval||5)*1000);};
  setTimeout(tick,(s.interval||5)*1000);}
function doGrant(){act(async()=>{await POST('/api/grant',{address:$('#gaddr').value.trim(),role:$('#grole').value});load('access')})}
function doRevoke(a){act(async()=>{await POST('/api/revoke',{address:a});load('access')})}

async function loadAll(){await whoami();await loadPills();loadSide();load(VIEW);}
$('#sidebtn').classList.toggle('active',SIDE);
loadAll();
setInterval(()=>{if(VIEW==='changes')loadPills().then(()=>{load('changes');loadSide()})},30000);
</script>
</body>
</html>
"""
