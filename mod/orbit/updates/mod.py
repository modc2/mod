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
import json
import os
import re
import subprocess
import time
import threading
import mod as m

PRIMARY = 'modc2/mod'        # the mod repo
DEFAULT_OWNER = 'modc2'
DEFAULT_BRANCH = 'dev'       # mod repo's default-shown branch
APP_PORT = 50180
X_API = os.environ.get('UPDATES_X_API', 'http://localhost:50350')  # orbit/x — holds the X account credentials
OWNER_FILE = '~/.mod/updates/owner.json'       # {"owner": "0x…"} pins who may merge
HOST_OWNER_FILE = '~/.mod/claude/owner.json'   # …else the host's owner of record
MERGE_TOKEN_TTL = 3600                         # merge wants a token signed within the hour
TOKEN_MAX_AGE = 86_400                         # every other write: the auth mod's own default
LOOPBACK = ('127.0.0.1', '::1', '::ffff:127.0.0.1')
WRITE_PATHS = ('/api/merge_main', '/api/commit_pending', '/api/track', '/api/untrack',
               '/api/mark_posted', '/api/set_branch')
PENDING_MAX_FILE = 50_000_000                  # GitHub warns at 50 MB and rejects at 100 MB
ROOTPUSH_STATE = '~/.mod/github/root.json'     # orbit/github's hourly root push
_MERGE_LOCK = threading.Lock()
GH_RE = re.compile(r'(?:https?://github\.com/|git@github\.com:)?([^/\s]+)/([^/\s]+?)(?:\.git)?/?$')


class Mod:
    description = ('GitHub commit-feed monitor: watch the mod repo (modc2/mod, dev branch) '
                  'and any other repos, and show recent commits flagging new ones')

    def __init__(self, key='updates', state_path=None, x_api=None):
        self.state_path = m.abspath(state_path or '~/.mod/updates/state.json')
        self.x_api = (x_api or X_API).rstrip('/')
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
        if r.status_code in (403, 429):
            rate_limited = r.headers.get('X-RateLimit-Remaining') == '0'
            if not rate_limited:
                try:
                    rate_limited = 'rate limit' in r.json().get('message', '').lower()
                except Exception:
                    pass
            if rate_limited:
                raise RuntimeError(
                    'GitHub API rate limit exceeded — set GITHUB_TOKEN or GH_TOKEN'
                    ' for higher limits (60→5000 req/hr)'
                )
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
            # first parent: lets the daily digest diff a whole day in one compare call
            'parent': (c.get('parents') or [{}])[0].get('sha', ''),
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
        fmt = sep.join(['%H', '%an', '%aI', '%P', '%s'])
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
            sha, an, date, parents, msg = (line.split(sep) + ['', '', '', '', ''])[:5]
            rows.append({'repo': repo, 'branch': branch, 'sha': sha[:8], 'full_sha': sha,
                         'author': an, 'date': date, 'message': self._short(msg),
                         'url': f'https://github.com/{repo}/commit/{sha}',
                         'parent': parents.split(' ')[0] if parents else ''})
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
            per_page = min(n, 100)
            results, page = [], 1
            while len(results) < n:
                page_data = self._api(f'/repos/{repo}/commits',
                                      {'sha': branch, 'per_page': per_page, 'page': page})
                results.extend(page_data)
                if len(page_data) < per_page:
                    break
                page += 1
            data = results[:n]
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
        and advancing each repo's last-seen marker. Markers only move for
        tracked repos viewed on their tracked branch — an ad-hoc repo= or
        branch= view is read-only (use `track` to watch a repo)."""
        st = self._load()
        n = int(n)
        if repo is not None:
            targets = [self._parse_repo(repo)]
            br = {targets[0]: branch or self._branch_of(targets[0])}
        else:
            targets = list(st['repos'])
            br = {r: (st['repos'][r].get('branch') or self._branch_of(r)) for r in targets}

        feed, errors = [], {}
        per = max(1, n if len(targets) == 1 else min(n, 15))
        for r in targets:
            try:
                cs = self.commits(r, br[r], per)
            except Exception as e:
                errors[r] = str(e)
                continue
            seen = (st['repos'].get(r) or {}).get('last_seen')
            for c in cs:
                feed.append(dict(c, new=self._is_new(cs, seen, c)))

        feed.sort(key=lambda c: c.get('date', ''), reverse=True)
        feed = feed[:n]

        if mark_seen:
            shown = {}
            for c in feed:
                r = c.get('repo')
                if r and r not in shown:
                    shown[r] = c['full_sha']
            # a view never edits the watchlist: `track` is the opt-in, so an
            # ad-hoc repo= peek stays read-only, and a tracked repo advances
            # only when viewed on its tracked branch (another branch's sha
            # would make the whole tracked feed look NEW next time)
            changed = False
            for r, sha in shown.items():
                meta = st['repos'].get(r)
                if not meta or br.get(r) != (meta.get('branch') or self._branch_of(r)):
                    continue
                meta['last_seen'] = sha
                changed = True
            if changed:
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

    # --- daily digest -------------------------------------------------------
    # The mod repo's dev branch is pushed by a bot, so every message reads
    # "root push · N files" — useless as a changelog. What actually carries the
    # signal is *which modules* the day's files landed in, so a day is rolled up
    # by path → module and rendered into a post you can paste as-is.

    AUTO_MSG = re.compile(r'^\s*(root push|auto[- ]?commit|wip\b|merge (branch|pull))', re.I)
    TWEET_MAX = 280
    TCO = 23                 # every link costs 23 chars on X, whatever its length
    DISCORD_MAX = 2000

    @staticmethod
    def _utc_day(iso: str) -> str:
        """ISO timestamp → 'YYYY-MM-DD' in UTC — one bucket per calendar day."""
        from datetime import datetime, timezone
        if not iso:
            return ''
        try:
            dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        except ValueError:
            return iso[:10]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _pretty_date(day: str) -> str:
        from datetime import date
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return day
        return f'{d.strftime("%a")} {d.strftime("%b")} {d.day}'

    @staticmethod
    def _module_of(path: str) -> str:
        """Attribute a repo path to the module that owns it."""
        seg = [s for s in (path or '').split('/') if s]
        for i, s in enumerate(seg[:-1]):
            if s == 'orbit':
                return seg[i + 1]
            if s == 'core':
                return 'core/' + seg[i + 1]
        return seg[0] if len(seg) > 1 else 'root'

    def _local_files(self, shas) -> list:
        """Paths touched by a set of commits, in one `git show` (merges show
        nothing, which is what we want — no double counting)."""
        if not shas:
            return []
        out = subprocess.run(['git', 'show', '--name-only', '--pretty=format:%x01', *shas],
                             cwd=self.toplevel, capture_output=True, text=True, timeout=90)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or 'git show failed')
        return [ln.strip() for ln in out.stdout.replace('\x01', '').splitlines() if ln.strip()]

    def _api_files(self, repo: str, base: str, head: str) -> list:
        """Paths touched between two commits — one API call for a whole day."""
        data = self._api(f'/repos/{repo}/compare/{base}...{head}')
        files = [f.get('filename', '') for f in (data.get('files') or []) if f.get('filename')]
        if len(files) >= 300:
            raise RuntimeError('GitHub compare returned ≥300 files — list is likely truncated; set GITHUB_TOKEN or use a local checkout for accurate module counts')
        return files

    def _day_files(self, repo: str, rows: list) -> list:
        """Distinct paths a day's commits touched. Local git first (free and
        offline); else one compare call from the oldest commit's parent."""
        shas = [c['full_sha'] for c in rows if c.get('full_sha')]
        if not shas:
            return []
        if self._is_local(repo):
            try:
                return sorted(set(self._local_files(shas)))
            except Exception:
                pass
        base = rows[0].get('parent') or shas[0]
        return sorted(set(self._api_files(repo, base, shas[-1])))

    def daily(self, repo=None, branch=None, days=7, n=200, files=True) -> dict:
        """Roll a branch's commits up into ONE entry per calendar day (UTC),
        each with a ready-to-paste post. Defaults to the mod repo's dev branch.
        Every day carries {date, commits, files, modules, authors, post{...}}."""
        repo = self._parse_repo(repo or PRIMARY)
        branch = branch or self._branch_of(repo)
        cs = self.commits(repo, branch, int(n))

        buckets = {}
        for c in cs:
            buckets.setdefault(self._utc_day(c.get('date')), []).append(c)

        posted = ((self._load().get('posted') or {}).get(f'{repo}@{branch}')) or {}
        out = []
        for day in sorted(buckets, reverse=True)[:int(days)]:
            rows = sorted(buckets[day], key=lambda c: c.get('date', ''))   # oldest → newest
            files_error = None
            try:
                paths = self._day_files(repo, rows) if files else []
            except Exception as e:
                paths = []
                files_error = str(e)
            counts = {}
            for p in paths:
                mod_name = self._module_of(p)
                counts[mod_name] = counts.get(mod_name, 0) + 1
            entry = {
                'date': day,
                'label': self._pretty_date(day),
                'repo': repo, 'branch': branch,
                'commits': len(rows),
                'files': len(paths),
                'authors': sorted({c['author'] for c in rows if c.get('author')}),
                'modules': [{'name': k, 'files': v} for k, v in
                            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
                'highlights': [c['message'] for c in reversed(rows)
                               if c.get('message') and not self.AUTO_MSG.match(c['message'])][:5],
                'shas': [c['sha'] for c in rows][::-1],
                'url': f'https://github.com/{repo}/commits/{branch}',
                'posted': bool(posted.get(day)),
                **({'files_error': files_error} if files_error else {}),
            }
            entry['post'] = {s: self._render(entry, s) for s in ('twitter', 'discord', 'markdown')}
            entry['chars'] = {s: self._weighted_len(entry['post'][s], entry['url'])
                              for s in ('twitter', 'discord', 'markdown')}
            out.append(entry)

        return {'repo': repo, 'branch': branch, 'tz': 'UTC', 'count': len(out),
                'today': self._today(), 'days': out}

    digest = daily

    _ACT_CACHE = {}
    ACT_TTL = 60

    def _local_activity(self, branch: str, since: str) -> list:
        """[{date, sha, files[]}] since a UTC day, in ONE `git log --name-only`
        (no content diffs — line counts cost ~100x more and every commit here
        is a bulk bot push, so files/modules are the honest measure)."""
        out = subprocess.run(
            ['git', 'log', branch, f'--since={since}T00:00:00Z', '--name-only', '--no-renames',
             '--pretty=format:\x01%H\x1f%aI'],
            cwd=self.toplevel, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or 'git log failed')
        rows = []
        for block in out.stdout.split('\x01'):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if not lines or '\x1f' not in lines[0]:
                continue
            sha, date = lines[0].split('\x1f', 1)
            rows.append({'sha': sha[:8], 'date': date, 'files': lines[1:]})
        return rows

    def activity(self, repo=None, branch=None, days=30) -> dict:
        """Changes over time: one bucket per UTC day for the last `days` days,
        zero-filled so gaps show as gaps. Each day = {date, commits, files,
        modules, top[]}. Local checkout → exact file/module counts from git;
        remote-only repos → commit counts from the API (files/modules = None)."""
        from datetime import date as _date, timedelta
        repo = self._parse_repo(repo or PRIMARY)
        branch = branch or self._branch_of(repo)
        days = max(1, min(int(days), 3660))
        key = (repo, branch, days)
        hit = self._ACT_CACHE.get(key)
        if hit and time.time() - hit[0] < self.ACT_TTL:
            return hit[1]

        today = _date.fromisoformat(self._today())
        start = today - timedelta(days=days - 1)
        buckets = {(start + timedelta(days=i)).isoformat(): {'commits': 0, 'paths': set(), 'mods': {}}
                   for i in range(days)}
        source = 'git'
        if self._is_local(repo):
            rows = self._local_activity(branch, start.isoformat())
        else:
            source = 'api'
            rows = [{'sha': c['sha'], 'date': c['date'], 'files': None}
                    for c in self.commits(repo, branch, n=min(days * 30, 1000))]
        for r in rows:
            b = buckets.get(self._utc_day(r['date']))
            if b is None:
                continue
            b['commits'] += 1
            for p in r['files'] or []:
                if p not in b['paths']:
                    b['paths'].add(p)
                    m = self._module_of(p)
                    b['mods'][m] = b['mods'].get(m, 0) + 1

        out, all_mods = [], {}
        for d in sorted(buckets):
            b = buckets[d]
            for m, k in b['mods'].items():
                all_mods[m] = all_mods.get(m, 0) + k
            top = sorted(b['mods'].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            out.append({'date': d, 'label': self._pretty_date(d), 'commits': b['commits'],
                        'files': len(b['paths']) if source == 'git' else None,
                        'modules': len(b['mods']) if source == 'git' else None,
                        'top': [{'name': k, 'files': v} for k, v in top]})
        totals = {'commits': sum(d['commits'] for d in out),
                  'active_days': sum(1 for d in out if d['commits']),
                  'files': sum(d['files'] or 0 for d in out) if source == 'git' else None,
                  'modules': len(all_mods) if source == 'git' else None}
        res = {'repo': repo, 'branch': branch, 'tz': 'UTC', 'source': source,
               'from': start.isoformat(), 'to': today.isoformat(), 'days': out, 'totals': totals,
               'top': [{'name': k, 'files': v} for k, v in
                       sorted(all_mods.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]}
        self._ACT_CACHE[key] = (time.time(), res)
        return res

    timeline = activity

    def _weighted_len(self, text: str, link: str) -> int:
        """X counts any link as 23 chars, so measure the way the box will."""
        return len(text) - len(link) + self.TCO if link and link in text else len(text)

    def _render(self, d: dict, style='twitter') -> str:
        """One day → one post. Branch is always on the first line: these feeds
        carry several branches and a digest that hides which one is a rumour."""
        repo, br, link = d['repo'], d['branch'], d['url']
        plural = lambda n, w: f'{n} {w}' + ('' if n == 1 else 's')
        stats = [plural(d['commits'], 'commit')]
        if d['files']:
            stats.append(plural(d['files'], 'file'))
        if d['modules']:
            stats.append(plural(len(d['modules']), 'module'))
        if len(d['authors']) > 1:
            stats.append(plural(len(d['authors']), 'dev'))

        if style == 'twitter':
            head = f'{repo} · {br} · {d["label"]}'
            lines = [f'▸ {x["name"]} {x["files"]}' for x in d['modules']]
            text = ''
            for keep in range(min(5, len(lines)), -1, -1):      # drop modules until it fits
                body = lines[:keep] + ([f'+{len(lines) - keep} more'] if keep < len(lines) else [])
                text = '\n'.join([head, '', ' · '.join(stats)]
                                 + ([''] + body if body else []) + ['', link])
                if self._weighted_len(text, link) <= self.TWEET_MAX:
                    break
            return text

        if style == 'discord':
            # no emoji: it renders as tofu in the app's own preview (this host
            # ships no emoji font) and the bold header carries it fine anyway
            _head = [f'**{repo} `{br}` — {d["label"]}**',
                     ' · '.join(f'`{s}`' for s in stats)]
            _footer = ['', f'<{link}>']                        # <> = no link preview card
            _mods = d['modules']
            _hi = d['highlights']

            def _build(mod_keep, hi_keep):
                p = list(_head)
                if _mods and mod_keep > 0:
                    shown = _mods[:mod_keep]
                    rest = len(_mods) - len(shown)
                    p += ['', '**modules touched**']
                    p += [f'• `{x["name"]}` — {plural(x["files"], "file")}' for x in shown]
                    if rest:
                        p.append(f'• …and {rest} more')
                if _hi:
                    shown_hi = _hi[:hi_keep]
                    rest_hi = len(_hi) - len(shown_hi)
                    p += ['', '**highlights**'] + [f'• {h}' for h in shown_hi]
                    if rest_hi:
                        p.append(f'• …and {rest_hi} more')
                p += _footer
                return '\n'.join(p)

            # drop modules until the post fits (preserving all highlights)
            for mod_keep in range(min(10, len(_mods)), -1, -1):
                text = _build(mod_keep, len(_hi))
                if len(text) <= self.DISCORD_MAX:
                    return text
            # modules exhausted — trim highlights too
            for hi_keep in range(len(_hi) - 1, -1, -1):
                text = _build(0, hi_keep)
                if len(text) <= self.DISCORD_MAX:
                    return text
            # last resort: slice (only truly irreducible content)
            return text[:self.DISCORD_MAX - 1] + '…'

        # markdown / plain — release notes, changelogs, anywhere else
        parts = [f'{repo} ({br}) — {d["date"]}', ' · '.join(stats), '']
        parts += [f'- {x["name"]}: {plural(x["files"], "file")}' for x in d['modules'][:15]]
        if len(d['modules']) > 15:
            parts.append(f'- …and {len(d["modules"]) - 15} more modules')
        if d['highlights']:
            parts += [''] + [f'- {h}' for h in d['highlights']]
        parts += ['', link]
        return '\n'.join(parts)

    def post(self, date=None, style='twitter', repo=None, branch=None,
             force=False, mark=True) -> dict:
        """THE once-a-day update, ready to paste into X or Discord.

        date: 'latest' (default — newest day with commits), 'today',
        'yesterday', or 'YYYY-MM-DD'. Runs after the first one on a given day
        come back with skip=True unless force=True, so a daily cron posts once."""
        data = self.daily(repo=repo, branch=branch, days=30, n=400)
        days = data['days']
        if not days:
            return {'skip': True, 'reason': 'no commits', 'repo': data['repo'],
                    'branch': data['branch']}
        want = (date or 'latest').strip().lower()
        if want in ('latest', 'last', ''):
            day = days[0]
        else:
            if want == 'today':
                want = self._today()
            elif want == 'yesterday':
                from datetime import date as _d, timedelta
                want = (_d.fromisoformat(self._today()) - timedelta(days=1)).isoformat()
            day = next((x for x in days if x['date'] == want), None)
            if day is None:
                return {'skip': True, 'reason': f'no commits on {want}',
                        'date': want, 'repo': data['repo'], 'branch': data['branch']}

        style = (style or 'twitter').lower()
        if style in ('x', 'tweet'):
            style = 'twitter'
        if style not in day['post']:
            style = 'markdown'
        res = {'date': day['date'], 'repo': day['repo'], 'branch': day['branch'],
               'style': style, 'text': day['post'][style], 'chars': day['chars'][style],
               'limit': self.TWEET_MAX if style == 'twitter' else self.DISCORD_MAX,
               'commits': day['commits'], 'files': day['files'],
               'modules': [x['name'] for x in day['modules'][:10]],
               'already_posted': day['posted'], 'skip': bool(day['posted']) and not force}
        if mark and not res['skip']:
            self.mark_posted(day['date'], repo=day['repo'], branch=day['branch'])
            res['marked'] = True
        return res

    tweet = post

    def paste(self, date=None, style='twitter', repo=None, branch=None,
              force=True, mark=False) -> str:
        """Just the post text, nothing around it — `m updates/paste` prints it
        as-is so you can select it out of the terminal. Defaults to force (you
        asked for it) and to NOT marking the day posted (you haven't yet)."""
        res = self.post(date=date, style=style, repo=repo, branch=branch,
                        force=force, mark=mark)
        return res.get('text') or f'no update: {res.get("reason", "nothing to post")}'

    def mark_posted(self, date=None, repo=None, branch=None, posted=True) -> dict:
        """Record that a day's update went out (what makes 'once per day' hold)."""
        repo = self._parse_repo(repo or PRIMARY)
        branch = branch or self._branch_of(repo)
        date = date or self._today()
        st = self._load()
        book = st.setdefault('posted', {}).setdefault(f'{repo}@{branch}', {})
        if posted in (False, 'false', 0, '0'):
            book.pop(date, None)
        else:
            book[date] = m.time()
        self._save(st)
        return {'repo': repo, 'branch': branch, 'date': date, 'posted': bool(book.get(date))}

    # --- the X account (via orbit/x, which owns the credentials) -------------

    def _x(self, method, path, body=None):
        """One call against the local orbit/x API. Credentials never live in
        this module — x-api reads ~/.mod/x/credentials.json per request."""
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            self.x_api + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'x-api {e.code} on {path}: {e.read().decode()[:400]}') from None
        except urllib.error.URLError as e:
            raise RuntimeError(f'x-api unreachable at {self.x_api} ({e.reason}) — '
                               'is orbit/x running?') from None

    def x_status(self) -> dict:
        """Is the X (Twitter) account connected and able to post? Proxies
        orbit/x's /auth so agents can check before calling post_to_x."""
        try:
            auth = self._x('GET', '/auth')
        except RuntimeError as e:
            return {'connected': False, 'error': str(e), 'x_api': self.x_api}
        return {'connected': True, 'x_api': self.x_api,
                'can_read': bool(auth.get('reads')), 'can_post': bool(auth.get('writes')),
                'auth': auth}

    def post_to_x(self, date=None, repo=None, branch=None, force=False,
                  dry_run=False, text=None) -> dict:
        """Build the day's digest and actually tweet it from the connected X
        account (through orbit/x). The day is marked posted only after X
        accepts it, so once-per-day still holds. dry_run shows what would be
        sent; text= tweets that instead of a digest (and marks nothing)."""
        digest = text is None
        if digest:
            res = self.post(date=date, style='twitter', repo=repo, branch=branch,
                            force=force, mark=False)
            if res.get('skip'):
                return res
            text = res['text']
        else:
            res = {'style': 'twitter', 'text': text, 'chars': self._weighted_len(text, '')}
        if dry_run:
            return {**res, 'dry_run': True, 'sent': False}
        sent = self._x('POST', '/posts', {'text': text})
        out = {**res, 'sent': True, 'x': sent}
        tweet_id = (sent.get('data') or {}).get('id') if isinstance(sent, dict) else None
        if tweet_id:
            out['url'] = f'https://x.com/i/status/{tweet_id}'
        if digest:
            self.mark_posted(res['date'], repo=res['repo'], branch=res['branch'])
            out['marked'] = True
        return out

    # --- managing the watchlist ---------------------------------------------

    def track(self, repo, branch=None) -> dict:
        """Attach a GitHub repo to the feed. `repo` may be owner/repo[@branch],
        a github URL, or (for modc2) a bare name. Branch defaults to the repo's
        default branch (dev for the mod repo)."""
        if branch is None and isinstance(repo, str) and '@' in repo.split('/')[-1]:
            repo, branch = repo.rsplit('@', 1)
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
            healthy = self._wait_health(port)
            if not healthy:
                raise RuntimeError(f'app did not start on port {port}; check {logf.name}')
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
        healthy = self._wait_health(port)
        if not healthy:
            raise RuntimeError(f'worker did not start on port {port}')
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
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain')

            @staticmethod
            def _denied(who):
                return {'ok': False, 'standing': who['standing'],
                        'error': who['error'] or 'owner only — sign in with the owner wallet'}

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
                    if u.path == '/api/whoami':
                        return self._send(200, gov.whoami(self.headers, self.client_address[0]))
                    if u.path == '/api/updates':
                        return self._send(200, gov.updates(n=int(q.get('n', 30)),
                                                           repo=q.get('repo'), mark_seen=False))
                    if u.path == '/api/poll':
                        # advances the seen-markers, so it is a write
                        who = gov.whoami(self.headers, self.client_address[0])
                        if not who['can_write']:
                            return self._send(403, self._denied(who))
                        return self._send(200, gov.poll(n=int(q.get('n', 50))))
                    if u.path == '/api/daily':
                        return self._send(200, gov.daily(repo=q.get('repo'), branch=q.get('branch'),
                                                         days=int(q.get('days', 7)),
                                                         n=int(q.get('n', 300))))
                    if u.path == '/api/activity':
                        return self._send(200, gov.activity(repo=q.get('repo'), branch=q.get('branch'),
                                                            days=int(q.get('days', 30))))
                    if u.path == '/api/post':
                        # GET never marks — the UI marks on copy, via POST
                        return self._send(200, gov.post(date=q.get('date'), style=q.get('style', 'twitter'),
                                                        repo=q.get('repo'), branch=q.get('branch'),
                                                        force=True, mark=False))
                    if u.path == '/api/pending':
                        # the list (paths, counts, sizes) is open; file contents are not
                        return self._send(200, gov.pending())
                    if u.path == '/api/pending_diff':
                        who = gov.whoami(self.headers, self.client_address[0])
                        if not who['can_write']:
                            return self._send(403, self._denied(who))
                        try:
                            return self._send(200, gov.pending_diff(q.get('path', '')))
                        except ValueError as e:
                            return self._send(404, {'error': str(e)})
                    if u.path == '/api/repos':
                        return self._send(200, gov.repos())
                    if u.path == '/api/modules':
                        return self._send(200, gov.modules(search=q.get('search'),
                                                           refresh=q.get('refresh') in ('1', 'true')))
                    if u.path == '/api/commits':
                        return self._send(200, gov.commits(repo=q.get('repo'), branch=q.get('branch'),
                                                           n=int(q.get('n', 30))))
                    if u.path == '/mcp':
                        return self._send(405, {'error': 'MCP is POST JSON-RPC',
                                                'protocol': gov.MCP_PROTOCOL})
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
                    who = gov.whoami(self.headers, self.client_address[0])
                    if u.path == '/mcp':
                        # reads are open; writes need box or the owner's token;
                        # post_to_x additionally needs the box itself
                        resp = gov._mcp_rpc(body, local=who['local'], writer=who['can_write'])
                        if resp is None:
                            return self._send(202, b'', 'text/plain')
                        return self._send(200, resp)
                    if u.path not in WRITE_PATHS:
                        return self._send(404, {'error': 'not found'})
                    if u.path == '/api/merge_main':
                        # owner only, and with a token signed within the hour;
                        # a direct loopback caller is the box itself
                        if not who['local']:
                            try:
                                gov._owner_from_token(self.headers)
                            except PermissionError as e:
                                return self._send(403, {'ok': False, 'error': str(e)})
                        return self._send(200, gov.merge_main(dry=bool(body.get('dry'))))
                    if u.path == '/api/commit_pending':
                        # commits + pushes as the box: same hour-fresh owner token as merge
                        if not who['local']:
                            try:
                                gov._owner_from_token(self.headers)
                            except PermissionError as e:
                                return self._send(403, {'ok': False, 'error': str(e)})
                        return self._send(200, gov.commit_pending(
                            paths=body.get('paths'), modules=body.get('modules'),
                            message=body.get('message'), push=body.get('push', True) is not False))
                    if not who['can_write']:
                        return self._send(403, self._denied(who))
                    if u.path == '/api/track':
                        return self._send(200, gov.track(body.get('repo'), body.get('branch')))
                    if u.path == '/api/untrack':
                        return self._send(200, gov.untrack(body.get('repo')))
                    if u.path == '/api/mark_posted':
                        return self._send(200, gov.mark_posted(body.get('date'), repo=body.get('repo'),
                                                               branch=body.get('branch'),
                                                               posted=body.get('posted', True)))
                    if u.path == '/api/set_branch':
                        return self._send(200, gov.set_branch(body.get('repo'), body.get('branch')))
                    return self._send(404, {'error': 'not found'})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

        return H

    # --- MCP server (zero-dep JSON-RPC, HTTP POST /mcp or stdio) --------------

    MCP_PROTOCOL = '2025-06-18'
    MCP_LOCAL_ONLY = {'post_to_x'}   # tweets as the owner — never over the gateway
    MCP_WRITES = {'track', 'untrack', 'set_branch', 'mark_posted', 'poll'}
    # these read by default but have a marking side effect a reader may not trigger
    MCP_MARKS = {'updates': 'mark_seen', 'post': 'mark'}

    def _mcp_tools(self) -> list:
        S = {'type': 'string'}
        I = {'type': 'integer'}
        B = {'type': 'boolean'}
        repo = {**S, 'description': 'owner/repo, a github URL, or a bare name (→ modc2/<name>)'}
        date = {**S, 'description': "'latest', 'today', 'yesterday', or YYYY-MM-DD"}

        def t(name, desc, **props):
            return {'name': name, 'description': desc,
                    'inputSchema': {'type': 'object', 'properties': props}}
        return [
            t('updates', 'Aggregated commit feed across every tracked repo, newest first, '
              'flagging commits NEW since last look (advances markers unless mark_seen=false)',
              n=I, repo=repo, branch=S, mark_seen=B),
            t('poll', 'Only commits NEW since the last poll — advances markers; for cron loops', n=I),
            t('commits', 'Recent commits for one repo+branch (local git log fallback for the checkout)',
              repo=repo, branch=S, n=I, prefer_local=B),
            t('repos', 'The watchlist: each tracked repo with its branch and latest commit'),
            t('track', 'Attach a GitHub repo to the feed', repo=repo, branch=S),
            t('untrack', 'Remove a repo from the feed', repo=repo),
            t('set_branch', 'Change which branch a tracked repo follows', repo=repo, branch=S),
            t('daily', 'Per-UTC-day rollup: commits, files, modules touched, and paste-ready '
              'post text in every style', repo=repo, branch=S, days=I, n=I),
            t('activity', 'Changes over time: zero-filled per-UTC-day series of commits, files and '
              'modules touched for the last N days (the data behind the feed chart)',
              repo=repo, branch=S, days=I),
            t('post', 'Build THE once-a-day update text without sending it anywhere '
              '(marks the day posted unless mark=false)',
              date=date, style={**S, 'enum': ['twitter', 'discord', 'markdown']},
              repo=repo, branch=S, force=B, mark=B),
            t('mark_posted', "Set or clear a day's posted flag (what makes once-per-day hold)",
              date=date, repo=repo, branch=S, posted=B),
            t('pending', 'Uncommitted work in the local checkout (what has not reached dev yet): '
              'per-module files, +/- lines, sizes, files too big for GitHub, and whether the '
              'hourly root push is stuck. files=false for the module summary only.', files=B),
            t('x_status', 'Is the X (Twitter) account connected and able to post? '
              '(proxies the local orbit/x API, which owns the credentials)'),
            t('post_to_x', "Build the day's digest and tweet it from the connected X account "
              'via orbit/x; the day is marked posted only after X accepts it. '
              'Local callers only. dry_run previews; text= tweets that instead.',
              date=date, repo=repo, branch=S, force=B, dry_run=B,
              text={**S, 'description': 'override: tweet this text instead of the digest'}),
        ]

    def _mcp_dispatch(self, name, args):
        fns = {'updates': self.updates, 'poll': self.poll, 'commits': self.commits,
               'repos': self.repos, 'track': self.track, 'untrack': self.untrack,
               'set_branch': self.set_branch, 'daily': self.daily, 'activity': self.activity,
               'post': self.post,
               'mark_posted': self.mark_posted, 'x_status': self.x_status,
               'post_to_x': self.post_to_x, 'pending': self.pending}
        spec = next(x for x in self._mcp_tools() if x['name'] == name)
        allowed = spec['inputSchema']['properties']
        return fns[name](**{k: v for k, v in (args or {}).items() if k in allowed})

    def _mcp_rpc(self, msg: dict, local=True, writer=None):
        """Answer one MCP JSON-RPC message. Returns None for notifications.
        `local` is False for requests that arrived through the gateway — those
        may read everything but cannot spend the X write rail. `writer` (default:
        `local`) is whether the caller may change state — box, or a valid
        owner token by mod-protocol auth; everyone else gets reads only."""
        writer = local if writer is None else writer
        rid = msg.get('id') if isinstance(msg, dict) else None
        method = msg.get('method') if isinstance(msg, dict) else None

        def ok(result):
            return {'jsonrpc': '2.0', 'id': rid, 'result': result}

        def err(code, text):
            return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': text}}
        if isinstance(method, str) and method.startswith('notifications/'):
            return None
        if not method:
            return err(-32600, 'invalid request: no method')
        if method == 'initialize':
            return ok({'protocolVersion': self.MCP_PROTOCOL, 'capabilities': {'tools': {}},
                       'serverInfo': {'name': 'updates', 'version': '0.2.0'}})
        if method == 'ping':
            return ok({})
        if method == 'tools/list':
            return ok({'tools': self._mcp_tools()})
        if method == 'tools/call':
            params = msg.get('params') or {}
            name = params.get('name')
            if not any(x['name'] == name for x in self._mcp_tools()):
                return err(-32602, f'unknown tool: {name}')
            if name in self.MCP_LOCAL_ONLY and not local:
                return ok({'isError': True, 'content': [{'type': 'text', 'text':
                           f'{name} is local-only: it posts as the account owner, so it '
                           'refuses callers that arrive through the gateway'}]})
            if name in self.MCP_WRITES and not writer:
                return ok({'isError': True, 'content': [{'type': 'text', 'text':
                           f'{name} changes state: owner only — send the owner\'s '
                           'mod-protocol token as Authorization: Bearer'}]})
            args = dict(params.get('arguments') or {})
            if name in self.MCP_MARKS and not writer:
                args[self.MCP_MARKS[name]] = False
            try:
                res = self._mcp_dispatch(name, args)
            except Exception as e:
                return ok({'isError': True, 'content': [{'type': 'text', 'text': str(e)}]})
            structured = res if isinstance(res, dict) else {'result': res}
            return ok({'isError': False, 'structuredContent': structured,
                       'content': [{'type': 'text', 'text': json.dumps(res, default=str)}]})
        return err(-32601, f'method not found: {method}')

    def mcp(self):
        """Serve MCP over stdio (newline-delimited JSON-RPC) — for clients that
        spawn their servers. The HTTP transport is the running app's POST /mcp."""
        import sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                print(json.dumps({'jsonrpc': '2.0', 'id': None,
                                  'error': {'code': -32700, 'message': 'parse error'}}), flush=True)
                continue
            resp = self._mcp_rpc(msg, local=True)
            if resp is not None:
                print(json.dumps(resp, default=str), flush=True)

    # --- owner-only: ship dev to main (temporary — until we leave GitHub) ------

    def owner(self) -> str:
        """The address allowed to merge: $UPDATES_OWNER, else ~/.mod/updates/owner.json,
        else the host's owner of record (~/.mod/claude/owner.json), else this box's key."""
        if os.environ.get('UPDATES_OWNER'):
            return os.environ['UPDATES_OWNER']
        for path in (OWNER_FILE, HOST_OWNER_FILE):
            rec = m.get(m.abspath(path), {}) or {}
            if rec.get('owner'):
                return str(rec['owner'])
        try:
            return m.key().address
        except Exception:
            return None

    def _signer(self, headers, max_age=TOKEN_MAX_AGE):
        """The lowercase address behind a mod-protocol `Authorization: Bearer`
        token, verified by the protocol's one auth mod (`m.mod('auth')`, the
        same class the `m` CLI mints with). None when no token was sent;
        PermissionError when one was sent and does not hold."""
        import contextlib
        import io
        import time
        raw = (headers.get('Authorization') or headers.get('authorization') or '').strip()
        tok = raw[7:].strip() if raw.lower().startswith('bearer ') else raw
        if not tok:
            return None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                data = m.mod('auth')().verify(tok)
        except Exception as e:
            raise PermissionError(f'invalid token: {type(e).__name__}')
        if abs(time.time() - float(data.get('time', 0))) > max_age:
            raise PermissionError('token expired — sign again')
        who = str(data.get('key') or '').lower()
        if not who:
            raise PermissionError('token carries no key')
        return who

    def whoami(self, headers=None, peer=None) -> dict:
        """Who is calling, by mod-protocol auth. Standings:
        box    — a direct loopback client (no proxy header): the machine itself
        owner  — a valid token signed by owner()
        signed — a valid token from anyone else (reads only)
        anon   — no token (reads only)
        Only box and owner may change state."""
        headers = headers or {}
        fwd = headers.get('X-Forwarded-For') or headers.get('X-Real-IP')
        box = not fwd and (peer in LOOPBACK if peer is not None else True)
        out = {'key': None, 'owner': self.owner(), 'error': None}
        try:
            out['key'] = self._signer(headers)
        except PermissionError as e:
            out['error'] = str(e)
        owner = str(out['owner'] or '').lower()
        if out['key'] and owner and out['key'] == owner:
            out['standing'] = 'owner'
        elif box:
            out['standing'] = 'box'
        else:
            out['standing'] = 'signed' if out['key'] else 'anon'
        out['can_write'] = out['standing'] in ('owner', 'box')
        out['local'] = box
        return out

    def _owner_from_token(self, headers, max_age=MERGE_TOKEN_TTL) -> str:
        """Verify a Bearer mod-protocol token and require it to be the owner's."""
        who = self._signer(headers, max_age=max_age)
        if not who:
            raise PermissionError('owner only — sign in with the owner wallet')
        owner = str(self.owner() or '').lower()
        if not owner or who != owner:
            raise PermissionError(f'{who} is not the owner')
        return who

    @staticmethod
    def _git(root, *args, timeout=300):
        # a dead VS Code server leaves GIT_ASKPASS pointing at a missing socket;
        # scrub it so git falls through to the stored credential
        env = {k: v for k, v in os.environ.items()
               if k not in ('GIT_ASKPASS', 'SSH_ASKPASS', 'VSCODE_GIT_ASKPASS_NODE',
                            'VSCODE_GIT_ASKPASS_MAIN', 'VSCODE_GIT_ASKPASS_EXTRA_ARGS',
                            'VSCODE_GIT_IPC_HANDLE')}
        env['GIT_TERMINAL_PROMPT'] = '0'
        try:
            r = subprocess.run(('git',) + args, cwd=root, capture_output=True,
                               text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, f'timeout after {timeout}s: git {" ".join(args)}'
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()

    def merge_main(self, source='dev', target='main', remote='origin', dry=False,
                   root=None) -> dict:
        """Merge `source` into `target` on `remote` and push — no PR, no prompts.
        Never touches the working tree (plumbing only). Order of preference:
        fast-forward -> clean merge commit -> forced merge commit whose tree is
        exactly `source` (source wins every conflict; target history is kept, so
        no force-push is needed). Never force-pushes. Only work committed on
        `source` ships."""
        root = root or self.toplevel
        if not root:
            raise RuntimeError('no git checkout found')
        with _MERGE_LOCK:
            rc, out = self._git(root, 'fetch', remote, f'+refs/heads/{source}:refs/remotes/{remote}/{source}',
                                f'+refs/heads/{target}:refs/remotes/{remote}/{target}')
            if rc:
                return {'ok': False, 'step': 'fetch', 'error': out}

            def sha(ref):
                rc, o = self._git(root, 'rev-parse', '--verify', '-q', ref + '^{commit}')
                return o if rc == 0 else None

            def ancestor(a, b):
                return self._git(root, 'merge-base', '--is-ancestor', a, b)[0] == 0

            local, upstream = sha(f'refs/heads/{source}'), sha(f'{remote}/{source}')
            tgt = sha(f'{remote}/{target}')
            src = local or upstream
            if local and upstream and local != upstream and ancestor(local, upstream):
                src = upstream               # remote is ahead of the box — ship the newer one
            if not src or not tgt:
                return {'ok': False, 'step': 'resolve',
                        'error': f'missing ref: {source if not src else remote + "/" + target}'}
            base = {'source': source, 'target': target, 'remote': remote,
                    'source_sha': src[:8], 'target_sha': tgt[:8]}
            if ancestor(src, tgt):
                return {**base, 'ok': True, 'mode': 'up-to-date', 'pushed': False,
                        'message': f'{target} already has everything on {source}'}
            ahead = int(self._git(root, 'rev-list', '--count', f'{tgt}..{src}')[1] or 0)
            if ancestor(tgt, src):
                mode, new = 'fast-forward', src
            else:
                rc, out = self._git(root, 'merge-tree', '--write-tree', tgt, src)
                if rc == 0:
                    mode, tree = 'merge', out.splitlines()[0]
                elif rc == 1:
                    mode, tree = 'forced', self._git(root, 'rev-parse', f'{src}^{{tree}}')[1]
                else:
                    return {**base, 'ok': False, 'step': 'merge-tree', 'error': out}
                msg = f'merge {source} into {target}' + (
                    f' ({source} wins conflicts)' if mode == 'forced' else '')
                rc, new = self._git(root, 'commit-tree', tree, '-p', tgt, '-p', src, '-m', msg)
                if rc:
                    return {**base, 'ok': False, 'step': 'commit-tree', 'error': new}
            base.update(mode=mode, commits=ahead, new_sha=new[:8])
            if dry:
                return {**base, 'ok': True, 'dry': True, 'pushed': False}
            refspec = f'{new}:refs/heads/{target}'
            # never --force: the new commit already has `target` as a parent, so a
            # non-fast-forward rejection means someone pushed since the fetch —
            # forcing would erase their work.
            rc, out = self._git(root, 'push', remote, refspec)
            if rc:
                if 'GH006' in out or 'protected branch' in out:
                    out = (f'GitHub refused: {target} is a protected branch (needs an approved '
                           f'pull request). Open or approve a PR {source} -> {target} on GitHub, '
                           f'or relax the branch rule.\n\n{out}')
                return {**base, 'ok': False, 'step': 'push', 'error': out}
            # keep the local target branch in step unless a worktree has it checked out
            if f'branch refs/heads/{target}' not in self._git(root, 'worktree', 'list', '--porcelain')[1]:
                self._git(root, 'update-ref', f'refs/heads/{target}', new)
            self._git(root, 'update-ref', f'refs/remotes/{remote}/{target}', new)
            return {**base, 'ok': True, 'pushed': True,
                    'message': f'{target} <- {source}: {mode}, {ahead} commit(s), now {new[:8]}'}

    # --- owner-only: uncommitted work in the checkout -> commit + push to dev --

    PENDING_STATUS = {'M': 'modified', 'A': 'added', 'D': 'deleted', 'R': 'renamed',
                      'C': 'copied', 'T': 'type changed', 'U': 'conflicted', '?': 'new'}

    def _status(self, root) -> list:
        """`git status` as rows: {path, status, staged, size}. Untracked files are
        listed one by one (-uall) because that is what a commit would add."""
        rc, _ = self._git(root, 'rev-parse', '--git-dir')
        if rc:
            raise RuntimeError('not a git checkout')
        r = subprocess.run(['git', 'status', '--porcelain=v1', '-z', '-uall'], cwd=root,
                           capture_output=True, timeout=120)
        if r.returncode:
            raise RuntimeError((r.stderr or b'').decode(errors='replace').strip() or 'git status failed')
        parts, rows, i = r.stdout.decode(errors='replace').split('\0'), [], 0
        while i < len(parts):
            ent = parts[i]
            i += 1
            if len(ent) < 4:
                continue
            xy, path, orig = ent[:2], ent[3:], None
            if 'R' in xy or 'C' in xy:          # renames carry the old path as the next field
                orig, i = parts[i], i + 1
            code = next((c for c in (xy[1], xy[0]) if c not in (' ', '!')), '?')
            full = os.path.join(root, path)
            try:
                size = os.path.getsize(full) if os.path.isfile(full) else 0
            except OSError:
                size = 0
            rows.append({'path': path, 'orig': orig, 'code': code,
                         'status': self.PENDING_STATUS.get(code, code),
                         'staged': xy[0] not in (' ', '?'), 'size': size})
        return rows

    def pending(self, root=None, files=True) -> dict:
        """Everything in the local checkout that is NOT committed yet — what the
        hourly root push (or the owner, from the Changes tab) would ship. Grouped
        by module with +/- line counts, sizes, and anything too big for GitHub."""
        root = root or self.toplevel
        if not root:
            raise RuntimeError('no git checkout found')
        rows = self._status(root)
        # +/- for tracked files in one call; new files are counted from disk
        nums = {}
        r = subprocess.run(['git', 'diff', 'HEAD', '--numstat', '-z'], cwd=root,
                           capture_output=True, timeout=120)
        for ent in r.stdout.decode(errors='replace').split('\0'):
            f = ent.split('\t')
            if len(f) == 3 and f[2]:
                nums[f[2]] = (None if f[0] == '-' else int(f[0]), None if f[1] == '-' else int(f[1]))
        mods = {}
        for x in rows:
            add, rem = nums.get(x['path'], (None, None))
            if x['code'] == '?' and 0 < x['size'] <= 2_000_000:
                try:
                    with open(os.path.join(root, x['path']), 'rb') as fh:
                        blob = fh.read()
                    add, rem = (None, None) if b'\0' in blob[:8000] else (blob.count(b'\n') + (not blob.endswith(b'\n')), 0)
                except OSError:
                    pass
            x.update(add=add, rem=rem, module=self._module_of(x['path']),
                     big=x['size'] > PENDING_MAX_FILE)
            g = mods.setdefault(x['module'], {'name': x['module'], 'files': 0, 'add': 0, 'rem': 0,
                                              'size': 0, 'big': 0, 'new': 0})
            g['files'] += 1
            g['add'] += x['add'] or 0
            g['rem'] += x['rem'] or 0
            g['size'] += x['size']
            g['big'] += x['big']
            g['new'] += x['code'] == '?'
        branch = self._git(root, 'rev-parse', '--abbrev-ref', 'HEAD')[1]
        head = self._git(root, 'rev-parse', '--short', 'HEAD')[1]
        rc, ahead = self._git(root, 'rev-list', '--count', f'origin/{branch}..HEAD')
        st = m.get(m.abspath(ROOTPUSH_STATE), {}) or {}
        last_err = st.get('last_error') or {}
        out = {
            'root': root, 'branch': branch, 'head': head,
            'ahead': int(ahead) if rc == 0 and ahead.isdigit() else None,
            'count': len(rows),
            'add': sum(x['add'] or 0 for x in rows), 'rem': sum(x['rem'] or 0 for x in rows),
            'size': sum(x['size'] for x in rows),
            'big': [x['path'] for x in rows if x['big']],
            'max_file': PENDING_MAX_FILE,
            'modules': sorted(mods.values(), key=lambda g: (-g['files'], g['name'])),
            # the hourly root push (orbit/github) — is it shipping, or stuck?
            'autopush': {'enabled': bool(st.get('enabled')), 'every': st.get('every'),
                         'last_push': st.get('last_push'),
                         'error': last_err.get('error') if (last_err.get('at') or 0) > (st.get('last_push') or 0) else None},
        }
        if files:
            out['files'] = rows
        return out

    def pending_diff(self, path, root=None, max_bytes=200_000) -> dict:
        """The uncommitted diff of ONE pending path (a new file shows as all-added).
        Only paths that `git status` lists are served — never an arbitrary file."""
        root = root or self.toplevel
        row = next((x for x in self._status(root) if x['path'] == path), None)
        if not row:
            raise ValueError(f'not a pending path: {path}')
        if row['code'] == '?':
            full = os.path.join(root, path)
            if row['size'] > max_bytes:
                return {**row, 'diff': None, 'note': f'new file, {row["size"]:,} bytes — too big to show'}
            with open(full, 'rb') as fh:
                blob = fh.read()
            if b'\0' in blob[:8000]:
                return {**row, 'diff': None, 'note': 'new binary file'}
            text = blob.decode(errors='replace')
            lines = text.splitlines()
            return {**row, 'diff': f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n'
                    + '\n'.join('+' + ln for ln in lines)}
        rc, out = self._git(root, '-c', 'core.quotepath=off', 'diff', 'HEAD', '--', path, timeout=60)
        if rc:
            raise RuntimeError(out)
        if len(out) > max_bytes:
            return {**row, 'diff': out[:max_bytes], 'note': f'diff cut at {max_bytes:,} bytes'}
        return {**row, 'diff': out or None, 'note': None if out else 'no textual change (mode or binary)'}

    def commit_pending(self, paths=None, modules=None, message=None, push=True,
                       branch=DEFAULT_BRANCH, remote='origin', root=None) -> dict:
        """Commit pending work in the checkout onto `branch` (dev) and push it.
        Pick it with `paths` and/or `modules`; neither = everything pending.
        Commits ONLY those paths — anything else that happens to be staged stays
        staged and out of this commit. Refuses files GitHub would reject, never
        force-pushes, never touches files it was not asked to commit."""
        root = root or self.toplevel
        if not root:
            raise RuntimeError('no git checkout found')
        with _MERGE_LOCK:
            cur = self._git(root, 'rev-parse', '--abbrev-ref', 'HEAD')[1]
            if branch and cur != branch:
                return {'ok': False, 'step': 'branch',
                        'error': f'the checkout is on {cur}, not {branch} — refusing to commit there'}
            rows = self._status(root)
            if isinstance(paths, str):
                paths = [p for p in paths.split(',') if p]
            if isinstance(modules, str):
                modules = [p for p in modules.split(',') if p]
            if paths or modules:
                want, mset = set(paths or ()), set(modules or ())
                pick = [x for x in rows if x['path'] in want or self._module_of(x['path']) in mset]
                unknown = want - {x['path'] for x in rows}
                if unknown:
                    return {'ok': False, 'step': 'select',
                            'error': 'not pending: ' + ', '.join(sorted(unknown)[:5])}
            else:
                pick = rows
            if not pick:
                return {'ok': False, 'step': 'select', 'error': 'nothing pending to commit'}
            big = [f"{x['path']} ({x['size'] / 1e6:.0f} MB)" for x in pick if x['size'] > PENDING_MAX_FILE]
            if big:
                return {'ok': False, 'step': 'size',
                        'error': f'{len(big)} file(s) over {PENDING_MAX_FILE // 1_000_000} MB — GitHub '
                                 'would reject the push. Leave them out (or .gitignore them): '
                                 + ', '.join(big[:5]) + (' …' if len(big) > 5 else '')}
            if os.path.exists(os.path.join(root, '.git', 'index.lock')):
                return {'ok': False, 'step': 'lock',
                        'error': 'another git process holds .git/index.lock (the hourly root push?) '
                                 '— try again in a minute'}
            spec = []
            for x in pick:
                spec.append(x['path'])
                if x.get('orig'):
                    spec.append(x['orig'])        # a rename commits its old path's removal too
            nul = '\0'.join(spec) + '\0'
            env_lit = {'GIT_LITERAL_PATHSPECS': '1'}

            def run(*args):
                env = {**os.environ, **env_lit, 'GIT_TERMINAL_PROMPT': '0'}
                r = subprocess.run(('git',) + args, cwd=root, input=nul.encode(),
                                   capture_output=True, timeout=300, env=env)
                return r.returncode, ((r.stdout or b'') + (r.stderr or b'')).decode(errors='replace').strip()
            rc, out = run('add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul')
            if rc:
                return {'ok': False, 'step': 'add', 'error': out}
            mods = sorted({self._module_of(x['path']) for x in pick})
            msg = (message or '').strip() or (
                f'owner commit · {len(pick)} file{"s" if len(pick) != 1 else ""} · '
                + ', '.join(mods[:6]) + (f' +{len(mods) - 6} more' if len(mods) > 6 else ''))
            # pathspec on commit = --only: exactly these paths, whatever else is staged
            rc, out = run('commit', '-q', '-m', msg, '--pathspec-from-file=-', '--pathspec-file-nul')
            if rc:
                return {'ok': False, 'step': 'commit', 'error': out}
            sha = self._git(root, 'rev-parse', '--short', 'HEAD')[1]
            res = {'ok': True, 'sha': sha, 'branch': cur, 'files': len(pick), 'modules': mods,
                   'message': msg, 'pushed': False}
            if not push:
                return {**res, 'summary': f'committed {sha} on {cur} ({len(pick)} files) — not pushed'}
            # never --force: a rejection means the remote moved; the commit stays local
            rc, out = self._git(root, 'push', remote, f'HEAD:refs/heads/{cur}')
            if rc:
                return {**res, 'push_error': out,
                        'summary': f'committed {sha} locally, but the push was refused — {out.splitlines()[-1] if out else ""}'}
            return {**res, 'pushed': True,
                    'summary': f'{cur} <- {sha}: {len(pick)} file(s) across {len(mods)} module(s), pushed'}

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
            'mcp': {'http': f'http://localhost:{APP_PORT}/mcp', 'stdio': 'm updates/mcp',
                    'tools': [x['name'] for x in self._mcp_tools()]},
            'x_api': self.x_api,
            'owner': self.owner(),
        }


# --- embedded zero-dependency web UI ----------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>updates · feed + daily + modules</title>
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
  .brand{display:flex;align-items:center;gap:9px;font-weight:800;font-size:17px;letter-spacing:.2px}
  .brand .logo{display:block}
  .isvg{vertical-align:-2px}
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
  .pill.arm{color:#fff;border-color:var(--pink);background:rgba(255,107,157,.18)}
  .pill .x{opacity:.5;display:inline-flex;align-items:center}
  .pill .x:hover{opacity:1;color:var(--pink)}
  .pill.arm .x{opacity:1;color:var(--pink)}
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
  /* branch chip — a digest that hides which branch it came from is a rumour */
  .br .brsvg{vertical-align:-1px;margin-right:2px}
  .br{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;font-weight:600;
    color:var(--accent2);background:rgba(91,140,255,.12);border:1px solid rgba(91,140,255,.3);
    border-radius:6px;padding:1px 7px}
  /* daily digest */
  .days{display:flex;flex-direction:column;gap:16px}
  .day{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line);
    border-radius:var(--r);padding:16px 18px;transition:.15s}
  .day:hover{border-color:var(--line2)}
  .day.pending{border-color:rgba(255,180,84,.45)}
  .day .dhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .day .date{font-size:17px;font-weight:800;letter-spacing:.2px}
  .day .iso{color:var(--faint);font-size:12px;font-family:ui-monospace,Menlo,monospace}
  .day .stats{color:var(--muted);font-size:12.5px;margin-top:7px;display:flex;gap:10px;flex-wrap:wrap}
  .day .stats b{color:var(--text)}
  .mchips{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}
  .mchip{font-size:11px;padding:3px 9px;border-radius:999px;background:var(--panel2);
    border:1px solid var(--line);color:var(--muted)}
  .mchip b{color:var(--accent2);font-weight:700}
  .hi-list{margin:10px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:3px}
  .hi-list li{font-size:12.5px;color:var(--muted);padding-left:14px;position:relative;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .hi-list li::before{content:'•';position:absolute;left:0;color:var(--faint)}
  .post{margin:13px 0 0;background:#06080d;border:1px solid var(--line);border-radius:11px;
    padding:13px 15px;white-space:pre-wrap;word-break:break-word;font:13px/1.6 ui-sans-serif,system-ui;
    color:var(--text);max-height:340px;overflow:auto}
  .dfoot{display:flex;align-items:center;gap:9px;margin-top:11px;flex-wrap:wrap}
  .chars{font-size:11.5px;color:var(--faint);font-family:ui-monospace,Menlo,monospace}
  .chars.over{color:var(--pink);font-weight:700}
  .chip.posted{color:#062611;background:rgba(63,185,80,.9)}
  .chip.pending{color:#1a1205;background:var(--new)}
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
  /* activity chart — changes over time */
  .act{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line);
    border-radius:var(--r);padding:16px 18px 12px;margin-bottom:16px;position:relative}
  .act .ahead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .act h2{margin:0;font-size:15px;font-weight:800;letter-spacing:.2px}
  .act .seg button{padding:4px 11px;font-size:12px}
  .tiles{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 4px}
  .tile{flex:1;min-width:110px;background:var(--panel2);border:1px solid var(--line);border-radius:11px;padding:8px 12px}
  .tile .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .tile .v{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
  .chart{position:relative;margin-top:8px}
  .chart svg{display:block;width:100%;overflow:visible}
  .chart .gl{stroke:var(--line);stroke-width:1}
  .chart .ax{fill:var(--faint);font:11px ui-sans-serif,system-ui;font-variant-numeric:tabular-nums}
  .chart .bar{fill:var(--accent);transition:opacity .1s}
  .chart .bar.today{fill:var(--accent2);opacity:.6}
  .chart.hov .bar{opacity:.45}.chart.hov .bar.on{opacity:1}
  .chart .clip{stroke:var(--panel);stroke-width:2}
  .tip{position:absolute;pointer-events:none;z-index:5;min-width:170px;background:var(--panel2);
    border:1px solid var(--line2);border-radius:10px;padding:9px 11px;font-size:12px;
    box-shadow:0 10px 30px rgba(0,0,0,.45);display:none}
  .tip .td{font-weight:700;margin-bottom:4px}
  .tip .tr{display:flex;justify-content:space-between;gap:14px;color:var(--muted)}
  .tip .tr b{color:var(--text);font-variant-numeric:tabular-nums}
  .tip .tm{margin-top:6px;padding-top:6px;border-top:1px solid var(--line);color:var(--muted)}
  .act details{margin-top:8px;font-size:12px;color:var(--muted)}
  .act details summary{cursor:pointer;color:var(--faint)}
  .act table{width:100%;border-collapse:collapse;margin-top:6px;font-variant-numeric:tabular-nums}
  .act td,.act th{padding:3px 8px;border-bottom:1px solid var(--line);text-align:right}
  .act td:first-child,.act th:first-child,.act td:last-child,.act th:last-child{text-align:left}
  .act .tblwrap{max-height:260px;overflow:auto}
  /* misc */
  .empty,.err{color:var(--muted);text-align:center;padding:54px 0}
  .err{color:var(--pink)}
  .count{color:var(--green);font-weight:700}
  .skeleton{height:70px;border-radius:var(--r);
    background:linear-gradient(100deg,var(--panel) 30%,var(--panel2) 50%,var(--panel) 70%);
    background-size:200% 100%;animation:sh 1.2s infinite}
  @keyframes sh{to{background-position:-200% 0}}
  /* changes — uncommitted work in the checkout */
  .pbanner{border:1px solid rgba(255,107,157,.45);background:rgba(255,107,157,.08);color:var(--text);
    border-radius:11px;padding:10px 13px;font-size:12.5px;margin-bottom:12px}
  .pbanner b{color:var(--pink)}
  .ptiles{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
  .pmods{display:flex;flex-direction:column;gap:8px}
  .pmod{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line);
    border-radius:12px;overflow:hidden}
  .pmod.big{border-color:rgba(255,107,157,.45)}
  .phead{display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;flex-wrap:wrap}
  .phead:hover{background:var(--panel2)}
  .phead .nm{font-weight:700;min-width:120px}
  .phead .caret{color:var(--faint);transition:.15s;display:inline-flex}
  .pmod.exp .caret{transform:rotate(90deg)}
  .num{font-family:ui-monospace,Menlo,monospace;font-size:12px;font-variant-numeric:tabular-nums}
  .num .a{color:var(--green)}.num .d{color:var(--pink)}
  .pfiles{border-top:1px solid var(--line);padding:4px 0;max-height:520px;overflow:auto}
  .pf{display:flex;align-items:center;gap:9px;padding:4px 14px;font-size:12.5px}
  .pf:hover{background:var(--panel2)}
  .pf .path{flex:1;min-width:0;font-family:ui-monospace,Menlo,monospace;font-size:12px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
  .pf .path:hover{color:var(--accent2)}
  .pf.big .path{color:var(--pink)}
  .st{font-size:10px;font-weight:700;letter-spacing:.3px;padding:1px 7px;border-radius:6px;
    min-width:62px;text-align:center;background:var(--panel2);color:var(--muted);border:1px solid var(--line)}
  .st.new,.st.added{color:var(--green);border-color:rgba(63,185,80,.4)}
  .st.deleted{color:var(--pink);border-color:rgba(255,107,157,.4)}
  .st.modified{color:var(--accent2);border-color:rgba(91,140,255,.4)}
  .pdiff{margin:2px 14px 8px;background:#06080d;border:1px solid var(--line);border-radius:9px;
    padding:9px 11px;font:11.5px/1.5 ui-monospace,Menlo,monospace;white-space:pre;overflow:auto;max-height:420px}
  .pdiff .a{color:var(--green)}.pdiff .d{color:var(--pink)}.pdiff .h{color:var(--accent2)}.pdiff .m{color:var(--faint)}
  input[type=checkbox]{min-width:0;width:15px;height:15px;accent-color:var(--accent);cursor:pointer;margin:0}
  .cbar{position:sticky;bottom:12px;z-index:10;margin-top:14px;display:flex;gap:9px;align-items:center;flex-wrap:wrap;
    background:rgba(16,20,30,.94);backdrop-filter:blur(10px);border:1px solid var(--line2);
    border-radius:14px;padding:10px 12px;box-shadow:0 12px 40px rgba(0,0,0,.45)}
  .cbar input[type=text]{flex:1;min-width:220px}
  /* owner-only merge */
  button.btn.merge{background:linear-gradient(180deg,#4cc864,var(--green));border-color:transparent;
    color:#04130a;box-shadow:0 4px 14px rgba(63,185,80,.3)}
  button.btn.merge:hover{filter:brightness(1.07)}
  .modal{position:fixed;inset:0;z-index:50;background:rgba(4,6,10,.7);backdrop-filter:blur(4px);
    display:grid;place-items:center}
  .modal .box{width:min(460px,92vw);background:var(--panel);border:1px solid var(--line2);
    border-radius:var(--r);padding:18px 20px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
  .modal h3{margin:0 0 8px;font-size:16px}
  .modal .body{color:var(--muted);font-size:13px;white-space:pre-wrap;word-break:break-word}
  .modal .body b{color:var(--text)}
  .modal .btns{display:flex;gap:9px;justify-content:flex-end;margin-top:16px}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><svg class="logo" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round" d="M4.9 4.9a10 10 0 0 1 14.2 0M7.8 7.8a6 6 0 0 1 8.4 0"/><circle cx="12" cy="12" r="2.2" fill="var(--accent)"/><path stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round" d="M12 14.8V20"/></svg>updates<span class="dot">.</span></div>
    <div class="seg">
      <button id="tab-feed" class="on" onclick="setView('feed')">Feed <span class="n" id="n-feed">·</span></button>
      <button id="tab-daily" onclick="setView('daily')">Daily <span class="n" id="n-daily">·</span></button>
      <button id="tab-changes" onclick="setView('changes')" title="uncommitted work in the checkout">Changes <span class="n" id="n-changes">·</span></button>
      <button id="tab-mods" onclick="setView('mods')">Modules <span class="n" id="n-mods">·</span></button>
    </div>
    <span class="sub" id="status">loading…</span>
    <span class="grow"></span>
    <span id="actions"></span>
    <span id="owner"></span>
  </div>
  <div class="row" id="filters" style="margin-top:11px"></div>
</header>
<main>
  <div class="view on" id="view-feed">
  <section class="act" id="act">
    <div class="ahead">
      <h2>Changes over time</h2><span class="sub" id="act-src"></span>
      <span class="grow"></span>
      <div class="seg" id="act-metric"></div>
      <div class="seg" id="act-range"></div>
    </div>
    <div class="tiles" id="act-tiles"></div>
    <div class="chart" id="act-chart"><div class="skeleton" style="height:200px"></div></div>
    <div class="tip" id="act-tip"></div>
    <details id="act-tbl"><summary>show data</summary><div class="tblwrap" id="act-table"></div></details>
  </section>
  <div class="feed" id="feed">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  <div class="view" id="view-daily"><div class="days" id="days">
    <div class="skeleton" style="height:180px"></div><div class="skeleton" style="height:180px"></div></div></div>
  <div class="view" id="view-changes"><div id="changes">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  <div class="view" id="view-mods"><div class="grid" id="mods">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
</main>
<script>
const $ = s => document.querySelector(s);
const BASE = location.pathname.replace(/\/+$/,'').replace(/\/index\.html$/,'');
const api = p => BASE + p;
let VIEW='feed', FILTER=null, INFO={}, MODS=null, MODQ='';
let STYLE=localStorage.getItem('updates.style')||'twitter', DAYS=7, DAILY=null, DAILY_REPO=null;
const STYLES={twitter:{label:'X / Twitter',limit:280},discord:{label:'Discord',limit:2000},markdown:{label:'Plain',limit:0}};

// branch glyph as inline SVG: this box (and plenty of others) ships no font
// that covers the git-branch character, and it would render as a tofu box.
const BR = '<svg class="brsvg" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">'
  + '<path fill="none" stroke="currentColor" stroke-width="1.6" d="M4.5 3v10M4.5 8h5a2 2 0 0 0 2-2V4.5"/>'
  + '<circle cx="4.5" cy="2.5" r="1.7" fill="currentColor"/><circle cx="4.5" cy="13.5" r="1.7" fill="currentColor"/>'
  + '<circle cx="11.5" cy="3" r="1.7" fill="currentColor"/></svg>';
// same rule for every other icon — typed glyphs (✕ ↻ ✓ ⚠ ↗) are tofu here too
const I = {
  x:'<svg class="isvg" viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg>',
  ref:'<svg class="isvg" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M13.3 9.2A5.5 5.5 0 1 1 12 4.3"/><path fill="currentColor" d="M13.9 1.6v4l-4-.6z"/></svg>',
  ok:'<svg class="isvg" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M2.5 8.5l3.5 3.5 7.5-8.5"/></svg>',
  warn:'<svg class="isvg" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" d="M8 2 1.5 13.5h13z"/><path stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M8 6.5v3.2"/><circle cx="8" cy="11.8" r=".9" fill="currentColor"/></svg>',
  ext:'<svg class="isvg" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M6 3h7v7M13 3 6.5 9.5"/></svg>'
};
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
  for(const t of ['feed','daily','changes','mods']){
    $('#tab-'+t).classList.toggle('on',v===t);
    $('#view-'+t).classList.toggle('on',v===t);
  }
  renderActions();
  $('#filters').style.display = v==='feed' ? 'flex' : 'none';
  if(v==='mods' && MODS===null) loadMods();
  if(v==='daily' && DAILY===null) loadDaily();
  if(v==='daily' && DAILY) renderDaily();
  if(v==='changes') PEND ? renderPending() : loadPending();
}

function renderActions(){
  const el=$('#actions');
  if(VIEW==='feed'){
    el.innerHTML = (canWrite() ? `<input id="add" placeholder="track owner/repo[@branch] or github URL"/>
      <button class="btn" onclick="track()">+ track</button>
      <button class="btn ghost" onclick="markRead()" title="mark all commits seen">mark read</button>` : '')
      + `<button class="btn primary" onclick="loadFeed()" title="refresh">${I.ref}</button>`;
    const a=$('#add'); if(a) a.addEventListener('keydown',e=>{if(e.key==='Enter')track()});
  } else if(VIEW==='daily'){
    el.innerHTML = `<div class="seg">`+
      Object.entries(STYLES).map(([k,v])=>
        `<button class="${STYLE===k?'on':''}" onclick="setStyle('${k}')">${v.label}</button>`).join('')+
      `</div>
      <select id="dsel" class="btn" onchange="DAYS=+this.value;loadDaily()">
        ${[7,14,30].map(d=>`<option value="${d}" ${DAYS===d?'selected':''}>last ${d} days</option>`).join('')}
      </select>
      <select id="drsel" class="btn" onchange="DAILY_REPO=this.value||null;DAILY=null;loadDaily()">
        <option value="" ${!DAILY_REPO?'selected':''}>primary</option>
        ${(INFO.tracking||TRACKING).map(r=>`<option value="${esc(r)}" ${DAILY_REPO===r?'selected':''}>${esc(r)}</option>`).join('')}
      </select>
      <button class="btn primary" onclick="loadDaily()" title="refresh">${I.ref}</button>`;
  } else if(VIEW==='changes'){
    el.innerHTML = `<input id="psearch" placeholder="filter paths…" value="${esc(PQ)}"/>
      <button class="btn primary" onclick="loadPending()" title="re-read git status">${I.ref}</button>`;
    const s=$('#psearch'); s.addEventListener('input',()=>{PQ=s.value;renderPending()});
  } else {
    el.innerHTML = `<input id="msearch" placeholder="filter modules…" value="${esc(MODQ)}"/>
      <button class="btn primary" onclick="loadMods(true)" title="re-scan registrar">${I.ref} rescan</button>`;
    const s=$('#msearch');
    s.addEventListener('input',()=>{MODQ=s.value;renderMods()});
    s.focus();
  }
}

/* ---------------- ACTIVITY (changes over time) ---------------- */
const METRICS={files:'Files changed',commits:'Commits',modules:'Modules touched'};
const RANGES=[[14,'14D'],[30,'30D'],[90,'90D'],[365,'1Y']];
let ACT=null, AMETRIC=localStorage.getItem('updates.ametric')||'files',
    ARANGE=+(localStorage.getItem('updates.arange')||30);
const fmt=n=>n==null?'–':n>=1e4?(n/1e3).toFixed(n>=1e5?0:1)+'k':n.toLocaleString();
async function loadActivity(){
  try{
    const r=await fetch(api(`/api/activity?days=${ARANGE}`+(FILTER?'&repo='+encodeURIComponent(FILTER):'')));
    const d=await r.json(); if(d.error) throw new Error(d.error);
    ACT=d; renderActivity();
  }catch(e){ $('#act-chart').innerHTML=`<div class="err" style="padding:30px 0">${esc(''+e)}</div>` }
}
function setAct(k,v){ if(k==='m'){AMETRIC=v;localStorage.setItem('updates.ametric',v);renderActivity();}
  else {ARANGE=v;localStorage.setItem('updates.arange',v);loadActivity();} }
function renderActivity(){
  const d=ACT; if(!d) return;
  const git=d.source==='git';
  if(!git && AMETRIC!=='commits') AMETRIC='commits';
  $('#act-metric').innerHTML=Object.entries(METRICS).map(([k,v])=>
    `<button class="${AMETRIC===k?'on':''}" ${!git&&k!=='commits'?'disabled style="opacity:.35;cursor:default"':''}
      onclick="setAct('m','${k}')">${v}</button>`).join('');
  $('#act-range').innerHTML=RANGES.map(([n,l])=>
    `<button class="${ARANGE===n?'on':''}" onclick="setAct('r',${n})">${l}</button>`).join('');
  $('#act-src').innerHTML=`<span class="repo">${esc(d.repo)}</span> <span class="br">${BR}${esc(d.branch)}</span> · UTC days`
    + (git?'':' · commit counts only (no local checkout)');
  const t=d.totals;
  $('#act-tiles').innerHTML=[['Commits',t.commits],['Files changed',t.files],['Modules touched',t.modules],
    ['Active days',`${t.active_days}<span class="sub"> / ${d.days.length}</span>`]]
    .map(([k,v])=>`<div class="tile"><div class="k">${k}</div><div class="v">${typeof v==='number'||v==null?fmt(v):v}</div></div>`).join('');
  drawChart();
  $('#act-table').innerHTML=`<table><tr><th>day</th><th>commits</th><th>files</th><th>modules</th><th>top modules</th></tr>`+
    d.days.slice().reverse().map(x=>`<tr><td>${x.date}</td><td>${x.commits}</td><td>${fmt(x.files)}</td><td>${fmt(x.modules)}</td>
      <td>${esc(x.top.map(m=>m.name).join(', '))}</td></tr>`).join('')+`</table>`;
}
// round tick step first so all four gridlines land on readable numbers
function niceMax(v){ const s=Math.max(1,v/4), p=Math.pow(10,Math.floor(Math.log10(s))), f=s/p;
  return 4*Math.max(1,(f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*p); }
function drawChart(){
  const d=ACT, el=$('#act-chart'); if(!d) return;
  const W=Math.max(320,el.clientWidth), H=210, L=40, R=6, T=12, B=24, iw=W-L-R, ih=H-T-B;
  const vals=d.days.map(x=>x[AMETRIC]||0), n=vals.length;
  // one huge day (a vendored tree, a mass rename) would flatten every other
  // bar to a sliver — cap the axis near the 95th percentile and mark the
  // clipped bars with a break, their real value in the tooltip
  const sorted=vals.filter(v=>v>0).sort((a,b)=>a-b);
  const raw=Math.max(1,...vals), p95=sorted.length?sorted[Math.floor((sorted.length-1)*.95)]:1;
  const cap=raw>p95*4&&sorted.length>=5 ? p95*1.25 : raw;
  const ymax=niceMax(cap), y=v=>T+ih-Math.min(v,ymax)/ymax*ih;
  const slot=iw/n, bw=Math.max(1,Math.min(28,slot-(slot>6?2:slot>2?1:0)));
  let g='';
  for(let i=0;i<=4;i++){ const v=ymax*i/4, yy=y(v);
    g+=`<line class="gl" x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}"/>`+
       `<text class="ax" x="${L-6}" y="${yy+4}" text-anchor="end">${fmt(Math.round(v))}</text>`; }
  const step=Math.ceil(n/Math.max(2,Math.floor(iw/70)));
  let bars='', labels='', hits='';
  const today=d.to;
  d.days.forEach((x,i)=>{
    const v=vals[i], cx=L+slot*i+slot/2, x0=cx-bw/2;
    if(v>0){ const top=y(v), h=T+ih-top, r=Math.min(4,bw/2,h);
      bars+=`<path class="bar${x.date===today?' today':''}" data-i="${i}" d="M${x0},${T+ih}V${top+r}Q${x0},${top} ${x0+r},${top}H${x0+bw-r}Q${x0+bw},${top} ${x0+bw},${top+r}V${T+ih}Z"/>`;
      if(v>ymax) bars+=`<line class="clip" x1="${x0-1}" x2="${x0+bw+1}" y1="${T+9}" y2="${T+5}"/>`; }
    if((n-1-i)%step===0) labels+=`<text class="ax" x="${cx}" y="${H-6}" text-anchor="middle">${x.date.slice(5).replace('-','/')}</text>`;
    hits+=`<rect x="${L+slot*i}" y="${T}" width="${slot}" height="${ih}" fill="transparent" data-i="${i}"/>`;
  });
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${METRICS[AMETRIC]} per day, last ${n} days">
    ${g}<line x1="${L}" x2="${W-R}" y1="${T+ih}" y2="${T+ih}" stroke="var(--line2)"/>${bars}${labels}<g id="act-hits">${hits}</g></svg>`;
  const tip=$('#act-tip'), box=$('#act');
  el.querySelectorAll('#act-hits rect').forEach(rc=>{
    rc.addEventListener('mousemove',ev=>{
      const i=+rc.dataset.i, x=d.days[i];
      el.classList.add('hov'); el.querySelectorAll('.bar').forEach(b=>b.classList.toggle('on',+b.dataset.i===i));
      tip.innerHTML=`<div class="td">${esc(x.label)}${x.date===today?' <span class="sub">(so far)</span>':''}</div>`+
        [['commits','Commits'],['files','Files changed'],['modules','Modules touched']].map(([k,l])=>
          x[k]==null?'':`<div class="tr"><span>${l}</span><b>${x[k].toLocaleString()}</b></div>`).join('')+
        (x.top.length?`<div class="tm">${x.top.map(m=>`${esc(m.name)} <b style="color:var(--text)">${m.files}</b>`).join(' · ')}</div>`:'');
      tip.style.display='block';
      const br=box.getBoundingClientRect(); let lx=ev.clientX-br.left+14;
      if(lx+tip.offsetWidth>br.width-8) lx=ev.clientX-br.left-tip.offsetWidth-14;
      tip.style.left=lx+'px'; tip.style.top=(ev.clientY-br.top-10)+'px';
    });
  });
  el.querySelector('svg').addEventListener('mouseleave',()=>{tip.style.display='none';el.classList.remove('hov');});
}
let _rz; addEventListener('resize',()=>{clearTimeout(_rz);_rz=setTimeout(drawChart,120)});

/* ---------------- FEED (github commits) ---------------- */
async function loadFeed(){
  try{
    INFO = await (await fetch(api('/api/info'))).json();
    const r = await fetch(api('/api/updates?n=40'+(FILTER?('&repo='+encodeURIComponent(FILTER)):'')));
    const data = await r.json();
    $('#n-feed').textContent = data.count;
    $('#status').innerHTML = `tracking <b>${data.tracking.length}</b> · ${data.count} commits · `
      + (data.new ? `<span class="count">${data.new} new</span>` : 'up to date')
      + (INFO.authenticated ? '' : ' · <span title="GitHub API is unauthenticated here — set GITHUB_TOKEN for higher rate limits. This is not your sign-in.">github: no token</span>');
    renderFilters(data.tracking);
    renderFeed(data.updates, data.errors);
    loadActivity();
  }catch(e){ $('#feed').innerHTML = `<div class="err">${esc(''+e)}</div>` }
}
let TRACKING=[], ARM=null, ARMT=null;
function renderFilters(repos){
  TRACKING=repos;
  const el=$('#filters');
  el.innerHTML = `<span class="pill ${!FILTER?'active':''}" onclick="setFilter(null)">all</span>`+
    repos.map(r=>{
      const armed = ARM===r;
      if(armed) return `<span class="pill arm" onclick="untrack('${r}')">untrack ${esc(r)}?
        <span class="x">${I.ok}</span></span>`;
      return `<span class="pill ${FILTER===r?'active':''}" onclick="setFilter('${r}')">${esc(r)}
        ${canWrite()?`<span class="x" onclick="event.stopPropagation();untrack('${r}')">${I.x}</span>`:''}</span>`;
    }).join('');
}
// bot pushes all read "root push · N files · 2026-10-04 20:49" — pull the file
// count into the meta row and drop the timestamp (the card already shows "Nm ago")
function prettyMsg(msg){
  const m=/^(.+?) · (\d+) files? · \d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?$/.exec(msg||'');
  return m ? {title:m[1], files:+m[2]} : {title:msg||'(no message)', files:null};
}
function renderFeed(items, errors){
  let html='';
  if(errors) for(const [r,e] of Object.entries(errors))
    html += `<div class="err" style="padding:10px 0">${I.warn} ${esc(r)}: ${esc(e)}</div>`;
  if(!items||!items.length){ $('#feed').innerHTML = html||'<div class="empty">no commits yet</div>'; return; }
  html += items.map(c=>{
    const p=prettyMsg(c.message);
    return `
    <div class="card ${c.new?'new':''}">
      <div class="av" style="background:${grad(c.author)}">${initials(c.author)}</div>
      <div class="msg">
        <a class="title" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>
        <div class="meta">
          <span class="repo">${esc(c.repo)}</span>
          <span class="br">${BR} ${esc(c.branch||'')}</span>
          <span class="sha">${esc(c.sha)}</span>
          ${p.files!==null?`<span><b>${p.files}</b> file${p.files===1?'':'s'}</span>`:''}
          <span>${esc(c.author)}</span>
          <span>${ago(c.date)}</span>
          ${c.new?'<span class="badge">NEW</span>':''}
        </div>
      </div>
    </div>`;
  }).join('');
  $('#feed').innerHTML = html;
}
function setFilter(r){ FILTER=r; loadFeed(); }
async function track(){
  const v=$('#add').value.trim(); if(!v) return;
  if(!await write('/api/track',{repo:v})) return; $('#add').value=''; loadFeed();
}
async function untrack(r){
  // two-click inline confirm — no native confirm() box, the armed pill reverts after 3s
  if(ARM!==r){
    ARM=r; clearTimeout(ARMT);
    ARMT=setTimeout(()=>{ARM=null;renderFilters(TRACKING)},3000);
    renderFilters(TRACKING); return;
  }
  ARM=null; clearTimeout(ARMT);
  if(!await write('/api/untrack',{repo:r})) return; if(FILTER===r)FILTER=null; loadFeed();
}
async function markRead(){ if(await write('/api/poll?n=80')) loadFeed(); }

/* ---------------- DAILY (one post per day, paste-ready) ---------------- */
function setStyle(k){ STYLE=k; localStorage.setItem('updates.style',k); renderActions(); renderDaily(); }

async function loadDaily(){
  $('#days').innerHTML='<div class="skeleton" style="height:180px"></div><div class="skeleton" style="height:180px"></div>';
  try{
    const r=await fetch(api(`/api/daily?days=${DAYS}&n=400`+(DAILY_REPO?'&repo='+encodeURIComponent(DAILY_REPO):'')));
    DAILY=await r.json();
    renderDaily();
  }catch(e){ $('#days').innerHTML=`<div class="err">${esc(''+e)}</div>`; }
}

function renderDaily(){
  if(!DAILY) return;
  const days=DAILY.days||[];
  const pending=days.filter(d=>!d.posted).length;
  $('#n-daily').textContent=pending||days.length;
  if(VIEW==='daily')
    $('#status').innerHTML=`<b>${esc(DAILY.repo)}</b> <span class="br">${BR} ${esc(DAILY.branch)}</span> · `
      +`${days.length} day${days.length===1?'':'s'} · `
      +(pending?`<span class="count">${pending} to post</span>`:'all posted')+` · UTC`;
  if(!days.length){ $('#days').innerHTML='<div class="empty">no commits in this window</div>'; return; }
  const lim=STYLES[STYLE].limit;
  $('#days').innerHTML = days.map((d,i)=>{
    const chars=(d.chars||{})[STYLE]||0, over=lim&&chars>lim;
    const mods=(d.modules||[]).slice(0,8).map(x=>`<span class="mchip">${esc(x.name)} <b>${x.files}</b></span>`).join('');
    const more=(d.modules||[]).length-8;
    return `<div class="day ${d.posted?'':'pending'}">
      <div class="dhead">
        <span class="date">${esc(d.label)}</span>
        <span class="iso">${esc(d.date)}</span>
        <span class="repo">${esc(d.repo)}</span>
        <span class="br">${BR} ${esc(d.branch)}</span>
        <span class="grow"></span>
        <span class="chip ${d.posted?'posted':'pending'}">${d.posted?'POSTED':'TO POST'}</span>
      </div>
      <div class="stats"><span><b>${d.commits}</b> commits</span>
        ${d.files_error
          ? `<span style="color:var(--faint)" title="${esc(d.files_error)}">file list unavailable</span>`
          : `<span><b>${d.files}</b> files</span><span><b>${(d.modules||[]).length}</b> modules</span>`}
        <span>${esc((d.authors||[]).join(', '))}</span></div>
      <div class="mchips">${mods}${more>0?`<span class="mchip">+${more}</span>`:''}</div>
      ${(d.highlights&&d.highlights.length)?`<ul class="hi-list">${d.highlights.map(h=>`<li>${esc(h)}</li>`).join('')}</ul>`:''}
      <pre class="post" id="post-${i}">${esc((d.post||{})[STYLE]||'')}</pre>
      <div class="dfoot">
        <button class="btn primary" onclick="copyPost(${i})" id="cp-${i}">copy for ${esc(STYLES[STYLE].label)}</button>
        <button class="btn ghost" onclick="togglePosted(${i})">${d.posted?'mark unposted':'mark posted'}</button>
        <span class="chars ${over?'over':''}">${chars}${lim?` / ${lim}`:''} chars</span>
      </div>
    </div>`;
  }).join('');
}

async function copyPost(i){
  const d=DAILY.days[i], text=(d.post||{})[STYLE]||'';
  try{ await navigator.clipboard.writeText(text); }
  catch(e){ // non-secure context: fall back to a hidden textarea
    const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
  }
  const b=$('#cp-'+i); if(b){ const t=b.innerHTML; b.innerHTML=I.ok+' copied'; setTimeout(()=>b.innerHTML=t,1400); }
  if(!d.posted && canWrite()) await togglePosted(i, true);   // copying IS posting — keeps it once a day
}

async function togglePosted(i, on){
  const d=DAILY.days[i], want = on===undefined ? !d.posted : !!on;
  if(!await write('/api/mark_posted',{date:d.date, repo:d.repo, branch:d.branch, posted:want})) return;
  d.posted=want; renderDaily();
}

/* ---------------- CHANGES (uncommitted work -> owner commits to dev) ---------------- */
// The list is public; diffs and the commit button are owner-only (server re-checks both).
let PEND=null, PQ='', POPEN=new Set(), PSEL=new Set(), PDIFF={}, PMSG='';
const enc=x=>encodeURIComponent(x).replace(/'/g,'%27');   // safe inside onclick='…'
const kb=n=>n>=1e9?(n/1e9).toFixed(1)+' GB':n>=1e6?(n/1e6).toFixed(n>=1e8?0:1)+' MB':n>=1e3?Math.round(n/1e3)+' KB':n+' B';
const pnum=(a,d)=>`<span class="num">${a==null?'bin':`<span class="a">+${fmt(a)}</span> <span class="d">&minus;${fmt(d||0)}</span>`}</span>`;
async function loadPending(){
  if(VIEW==='changes' && !PEND) $('#changes').innerHTML='<div class="skeleton"></div><div class="skeleton"></div>';
  try{
    const d=await (await fetch(api('/api/pending'))).json(); if(d.error) throw new Error(d.error);
    const first=!PEND; PEND=d;
    const live=new Set(d.files.map(f=>f.path));
    PSEL=new Set([...PSEL].filter(p=>live.has(p)));
    // default pick: everything except modules holding files GitHub would refuse —
    // a 50 MB file is almost always build output, and its siblings are too
    const bad=new Set(d.modules.filter(g=>g.big).map(g=>g.name));
    if(first) d.files.forEach(f=>{ if(!bad.has(f.module)) PSEL.add(f.path); });
    $('#n-changes').textContent=d.count>=1000?(d.count/1000).toFixed(1)+'k':d.count;
    if(VIEW==='changes') renderPending();
  }catch(e){ if(VIEW==='changes') $('#changes').innerHTML=`<div class="err">${esc(''+e)}</div>`; }
}
function pFiles(mod){ const q=PQ.toLowerCase();
  return PEND.files.filter(f=>f.module===mod && (!q||f.path.toLowerCase().includes(q))); }
function renderPending(){
  const d=PEND; if(!d) return;
  const own=canWrite(), q=PQ.toLowerCase();
  $('#status').innerHTML=`<b>${esc(d.root)}</b> <span class="br">${BR} ${esc(d.branch)}</span> <span class="sha">${esc(d.head)}</span>`
    +(d.ahead?` · <span class="count">${d.ahead} committed, not pushed</span>`:'');
  if(!d.count){ $('#changes').innerHTML=`<div class="empty">${I.ok} nothing uncommitted &mdash; ${esc(d.branch)} is clean</div>`; return; }
  const ap=d.autopush||{}; let h='';
  if(ap.error) h+=`<div class="pbanner">${I.warn} <b>hourly root push is stuck</b> &mdash; ${esc(ap.error)}`
    +(ap.last_push?` · last push ${tago(ap.last_push)}`:'')+(own?'. Commit the parts you want below, leaving out the bulk.':'')+`</div>`;
  else if(ap.enabled && ap.last_push) h+=`<div class="sub" style="margin-bottom:10px">hourly root push is on · last push ${tago(ap.last_push)} &mdash; everything here ships with the next one</div>`;
  h+=`<div class="ptiles">`+[['Files',fmt(d.count)],['Lines',`<span class="num" style="font-size:16px"><span class="a">+${fmt(d.add)}</span> <span class="d">&minus;${fmt(d.rem)}</span></span>`],
    ['Size',kb(d.size)],['Modules',d.modules.length]].concat(d.big.length?[['Too big for GitHub',`<span style="color:var(--pink)">${d.big.length}</span>`]]:[])
    .map(([k,v])=>`<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')+`</div>`;
  h+=`<div class="pmods">`;
  for(const g of d.modules){
    const fs=pFiles(g.name); if(q && !fs.length) continue;
    const all=PEND.files.filter(f=>f.module===g.name && !f.big), n=all.filter(f=>PSEL.has(f.path)).length;
    const open=POPEN.has(g.name)||(q&&fs.length<=60);
    h+=`<div class="pmod ${open?'exp':''} ${g.big?'big':''}">
      <div class="phead" onclick="pToggle('${enc(g.name)}')">
        ${own?`<input type="checkbox" ${n&&n===all.length?'checked':''} ${!all.length?'disabled':''}
          onclick="event.stopPropagation();pPickMod('${enc(g.name)}',this.checked)" data-ind="${n>0&&n<all.length?1:0}"/>`:''}
        <span class="caret"><svg viewBox="0 0 16 16" width="10" height="10"><path fill="currentColor" d="M5 3l6 5-6 5z"/></svg></span>
        <span class="nm">${esc(g.name)}</span>
        <span class="sub">${g.files} file${g.files===1?'':'s'}${g.new?` · ${g.new} new`:''}</span>
        ${pnum(g.add,g.rem)}<span class="sub">${kb(g.size)}</span>
        ${g.big?`<span class="chip pending" title="over ${kb(d.max_file)} — GitHub rejects these">${g.big} too big</span>`:''}
        <span class="grow"></span>
        ${own&&n?`<span class="sub">${n} picked</span>`:''}
      </div>`;
    if(open){
      const show=fs.slice(0,400);
      h+=`<div class="pfiles">`+show.map(f=>{const k=enc(f.path);
        return `<div class="pf ${f.big?'big':''}">
          ${own?`<input type="checkbox" ${PSEL.has(f.path)?'checked':''} ${f.big?'disabled title="too big for GitHub"':''} onchange="pPick('${k}',this.checked)"/>`:''}
          <span class="st ${esc(f.status).replace(/ /g,'-')}">${esc(f.status)}</span>
          <span class="path" title="${esc(f.path)}${own?' — click for the diff':''}" onclick="pDiff('${k}')">${esc(f.path)}${f.orig?` <span class="sub">&larr; ${esc(f.orig)}</span>`:''}</span>
          ${pnum(f.add,f.rem)}<span class="sub" style="min-width:58px;text-align:right">${kb(f.size)}</span>
        </div>${PDIFF[f.path]!==undefined?pDiffHtml(PDIFF[f.path]):''}`;}).join('')
        +(fs.length>show.length?`<div class="pf sub">+${fs.length-show.length} more &mdash; filter to narrow</div>`:'')+`</div>`;
    }
    h+=`</div>`;
  }
  h+=`</div>`;
  if(own){
    const n=PSEL.size;
    h+=`<div class="cbar">
      <input type="text" id="pmsg" placeholder="commit message (blank = owner commit · N files · modules)" value="${esc(PMSG)}" oninput="PMSG=this.value"/>
      <span class="sub"><b style="color:var(--text)">${fmt(n)}</b> of ${fmt(d.count)} picked</span>
      <button class="btn ghost" onclick="pPickAll(${n?0:1})">${n?'none':'all'}</button>
      <button class="btn merge" ${n?'':'disabled style="opacity:.4"'} onclick="pCommit()">${BR} commit &amp; push to ${esc(d.branch)}</button>
    </div>`;
  } else h+=`<div class="sub" style="margin-top:14px;text-align:center">sign in with the owner wallet to see diffs and commit these to ${esc(d.branch)}</div>`;
  $('#changes').innerHTML=h;
  document.querySelectorAll('#changes input[data-ind="1"]').forEach(c=>c.indeterminate=true);
}
function pToggle(k){ const m=decodeURIComponent(k); POPEN.has(m)?POPEN.delete(m):POPEN.add(m); renderPending(); }
function pPick(k,on){ const p=decodeURIComponent(k); on?PSEL.add(p):PSEL.delete(p); renderPending(); }
function pPickMod(k,on){ const m=decodeURIComponent(k); PEND.files.forEach(f=>{ if(f.module===m && !f.big) on?PSEL.add(f.path):PSEL.delete(f.path); }); renderPending(); }
function pPickAll(on){ PSEL=new Set(on?PEND.files.filter(f=>!f.big).map(f=>f.path):[]); renderPending(); }
function pDiffHtml(x){
  if(x===null) return `<div class="pdiff m">loading…</div>`;
  if(x.error) return `<div class="pdiff d">${esc(x.error)}</div>`;
  const body=(x.diff||'').split('\n').map(l=>{const c=l.startsWith('+++')||l.startsWith('---')?'m':l[0]==='+'?'a':l[0]==='-'?'d':l.startsWith('@@')?'h':'';
    return c?`<span class="${c}">${esc(l)}</span>`:esc(l);}).join('\n');
  return `<div class="pdiff">${x.note?`<span class="m">${esc(x.note)}</span>\n`:''}${body}</div>`;
}
async function pDiff(k){
  if(!canWrite()) return;
  const p=decodeURIComponent(k);
  if(PDIFF[p]!==undefined){ delete PDIFF[p]; return renderPending(); }
  PDIFF[p]=null; renderPending();
  try{ const r=await fetch(api('/api/pending_diff?path='+k),{headers:authHdr()}); const j=await r.json();
    PDIFF[p]=r.ok?j:{error:j.error||('HTTP '+r.status)}; }
  catch(e){ PDIFF[p]={error:''+e}; }
  renderPending();
}
async function pCommit(){
  const paths=[...PSEL], mods=[...new Set(PEND.files.filter(f=>PSEL.has(f.path)).map(f=>f.module))];
  modal(`commit &amp; push to ${esc(PEND.branch)}`,
    `<b>${fmt(paths.length)}</b> file(s) across <b>${mods.length}</b> module(s) go into one commit on `
    +`<b>${esc(PEND.branch)}</b> and are pushed to GitHub.\n${esc(mods.slice(0,12).join(', '))}${mods.length>12?` +${mods.length-12} more`:''}\n\n`
    +`Everything you did not pick stays uncommitted, untouched.`,
    [['cancel','ghost'],['commit &amp; push','merge',async()=>{
      modal('committing…','writing the commit and pushing…',[]);
      let r; try{
        await freshToken();
        const x=await fetch(api('/api/commit_pending'),{method:'POST',headers:{'Content-Type':'application/json',...authHdr()},
          body:JSON.stringify({paths, message:PMSG.trim()||null, push:true})});
        r=await x.json().catch(()=>({ok:false,error:'HTTP '+x.status}));
      }catch(e){ r={ok:false,error:e.message||''+e}; }
      if(r.ok){ PMSG=''; PSEL=new Set(); }
      modal(r.ok?(r.pushed?'committed + pushed':'committed, push refused'):'commit failed',
        esc(r.ok?(r.summary+(r.push_error?'\n\n'+r.push_error:'')):(r.error||'unknown error')),[['close','ghost']]);
      PEND=null; PDIFF={}; await loadPending(); loadFeed();
    }]]);
}

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
        <a class="open ${live?'':'off'}" href="${esc(url)}" target="_blank" rel="noopener">open ${I.ext}</a>
      </div>
    </div>`;
  }).join('');
}

/* ---------------- boot ---------------- */
renderActions();
/* ---------------- IDENTITY: mod-protocol auth ---------------- */
// One personal_sign mints a mod-protocol token: base64url({data,time,key,signature})
// over JSON.stringify({data,time}). The server verifies it with m.mod('auth') and
// decides the standing (owner / signed / anon; the box itself on loopback).
// Hiding buttons here is convenience — every write is re-checked server-side.
const TOK_KEY='updates.token';
var TOKEN=null, WHO={standing:'anon',can_write:false};
try{ TOKEN=localStorage.getItem(TOK_KEY)||null; }catch(e){}
function canWrite(){ return !!(WHO && WHO.can_write); }   // hoisted: boot calls it before WHO is set
const short=a=>a?a.slice(0,6)+'…'+a.slice(-4):'';
function tokAge(t){ try{ const j=JSON.parse(atob(t.replace(/-/g,'+').replace(/_/g,'/')));
  return Date.now()/1000-(+j.time||0); }catch(e){ return 1e9; } }
const authHdr=()=>TOKEN?{'Authorization':'Bearer '+TOKEN}:{};
async function whoami(){
  try{ WHO=await (await fetch(api('/api/whoami'),{headers:authHdr()})).json(); }catch(e){}
  if(TOKEN && WHO.error){ signOut(true); return; }   // stale/invalid token: drop it
  paintMe(); renderActions(); renderFilters(TRACKING); if(VIEW==='changes') renderPending();
}
function paintMe(){
  const el=$('#owner'), me=WHO.key;
  let h='';
  if(WHO.can_write)
    h+=`<button class="btn merge" onclick="mergeMain()" title="merge dev into main on GitHub and push">${BR} merge dev &rarr; main</button> `;
  if(me) h+=`<button class="btn ghost" onclick="signOut()" title="${esc(me)} · ${esc(WHO.standing)} — click to sign out">${esc(short(me))}${WHO.standing==='owner'?' · owner':''}</button>`;
  else if(window.ethereum) h+=`<button class="btn ghost" onclick="signIn()" title="sign in with your wallet (mod-protocol token)">sign in</button>`;
  el.innerHTML=h;
}
async function mint(){
  const [a]=await window.ethereum.request({method:'eth_requestAccounts'});
  const key=(a||'').toLowerCase(); if(!key) throw new Error('no wallet account');
  const data='updates', time=Math.floor(Date.now()/1000).toString();
  const signature=await window.ethereum.request({method:'personal_sign',params:[JSON.stringify({data,time}),key]});
  return btoa(JSON.stringify({data,time,key,signature})).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
}
async function signIn(){
  try{ TOKEN=await mint(); try{ localStorage.setItem(TOK_KEY,TOKEN); }catch(e){} }
  catch(e){ return; }
  whoami();
}
function signOut(quiet){
  TOKEN=null; WHO={standing:'anon',can_write:false}; try{ localStorage.removeItem(TOK_KEY); }catch(e){}
  if(!quiet) whoami(); else { paintMe(); renderActions(); renderFilters(TRACKING); if(VIEW==='changes') renderPending(); }
}
// every write goes through here: Bearer token attached, a 403 says why
async function write(path, body){
  const r=await fetch(api(path), body===undefined ? {headers:authHdr()} :
    {method:'POST',headers:{'Content-Type':'application/json',...authHdr()},body:JSON.stringify(body)});
  if(r.status===403){
    const j=await r.json().catch(()=>({}));
    modal('owner only',esc(j.error||'sign in with the owner wallet'),[['close','ghost']]);
    return false;
  }
  return r.ok;
}
function modal(title,body,buttons){
  document.querySelector('.modal')?.remove();
  const m=document.createElement('div'); m.className='modal';
  m.innerHTML=`<div class="box"><h3>${title}</h3><div class="body">${body}</div><div class="btns"></div></div>`;
  for(const [label,cls,fn] of buttons){
    const b=document.createElement('button'); b.className='btn '+cls; b.innerHTML=label;
    b.onclick=()=>{ if(fn) fn(); else m.remove(); }; m.querySelector('.btns').appendChild(b);
  }
  m.addEventListener('click',e=>{ if(e.target===m) m.remove(); });
  document.body.appendChild(m); return m;
}
// merge and commit push as the box: both want a token signed within the hour
async function freshToken(){
  if(WHO.standing!=='box' && (!TOKEN || tokAge(TOKEN)>3000)){
    TOKEN=await mint(); try{ localStorage.setItem(TOK_KEY,TOKEN); }catch(e){}
  }
}
async function mergeCall(dry){
  await freshToken();
  const r=await fetch(api('/api/merge_main'),{method:'POST',headers:{'Content-Type':'application/json',
    ...authHdr()},body:JSON.stringify({dry})});
  return await r.json().catch(()=>({ok:false,error:'HTTP '+r.status}));
}
const MODES={'fast-forward':'fast-forward','merge':'clean merge','forced':'forced merge (dev wins every conflict)'};
async function mergeMain(){
  modal('merge dev &rarr; main','checking…',[['cancel','ghost']]);
  let p; try{ p=await mergeCall(true); }catch(e){ p={ok:false,error:e.message||''+e}; }
  if(!p.ok) return modal('merge failed',esc(p.error||'unknown error'),[['close','ghost']]);
  if(p.mode==='up-to-date') return modal('nothing to merge',esc(p.message),[['close','ghost']]);
  modal('merge dev &rarr; main',
    `<b>${p.commits}</b> commit(s) from dev <b>${esc(p.source_sha)}</b> go onto main <b>${esc(p.target_sha)}</b>.\n`
    +`How: <b>${esc(MODES[p.mode]||p.mode)}</b>.\nOnly committed work ships; uncommitted changes wait for the next root push (or commit them from the Changes tab).`,
    [['cancel','ghost'],['merge now','merge',async()=>{
      modal('merging…','pushing to GitHub…',[]);
      let r; try{ r=await mergeCall(false); }catch(e){ r={ok:false,error:e.message||''+e}; }
      modal(r.ok?'merged':'merge failed', esc(r.ok?r.message:r.error||'unknown error'),[['close','ghost']]);
    }]]);
}

loadFeed().then(whoami); loadPending();
setInterval(()=>{ if(VIEW==='feed') loadFeed(); if(VIEW==='daily') loadDaily(); loadPending(); }, 300000);
</script>
</body>
</html>
"""
