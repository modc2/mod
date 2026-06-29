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
