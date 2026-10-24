"""
bt.git — development activity for every subnet, from the subnet's own GitHub.

Every subnet publishes a repo in its on-chain identity (subnet_identity,
mirrored into history.db's subnet_meta). A background thread asks GitHub what
is going on in each one and keeps the answer in `~/.mod/bt/git.db`, so every
read is local:

  repo     stars, forks, watchers, open/closed issues, open/merged PRs,
           license, languages, topics, created/pushed, archived, size
  commits  total on the default branch + last 7/30/90 days, the latest
           commit, distinct commit authors over 30 days, contributors
           (all-time), 52-week commit sparkline
  releases count + latest tag
  owner    the org/user's public repo count and its most recently pushed repos
  snaps    one row per repo per UTC day, so star/fork/commit growth over
           7d/30d is answered from history

Identity URLs are messy: `github.com/org` and `github.com/orgs/org/repositories`
name an org, not a repo — those resolve to the org's most-starred source repo.
`/tree/main` suffixes are dropped.

Auth: GraphQL needs a token. First of BT_GITHUB_TOKEN / GITHUB_TOKEN /
GH_TOKEN, `gh auth token`, ~/.mod/bt/github.token. With none, a REST-only
fallback (60 req/h unauthenticated) still fills the repo basics.

Env: BT_NO_GIT=1 (or BT_NO_SNAPSHOT=1) disables the thread; BT_GIT_REFRESH_SEC
(6h) = per-repo staleness; BT_GIT_BATCH (8) = repos per GraphQL query.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .history import data_dir

REFRESH_SEC = int(os.environ.get('BT_GIT_REFRESH_SEC', str(6 * 3600)))
BATCH = int(os.environ.get('BT_GIT_BATCH', '8'))
PACE_SEC = float(os.environ.get('BT_GIT_PACE_SEC', '20'))
TIMEOUT = 25
API = 'https://api.github.com'
UA = 'bt-git/1.0 (+https://modc2.com/bt)'

# ------------------------------------------------------------------ storage

_db_lock = threading.Lock()          # writers only; readers use WAL
_ready: set = set()

# every column the repos table carries, in order (netuid first)
COLS = [
    'netuid', 'url', 'repo', 'resolved_from', 'description', 'homepage',
    'stars', 'forks', 'watchers', 'open_issues', 'closed_issues', 'open_prs',
    'merged_prs', 'license', 'language', 'languages', 'topics',
    'created_at', 'pushed_at', 'archived', 'is_fork', 'size_kb',
    'default_branch', 'commits_total', 'commits_7d', 'commits_30d',
    'commits_90d', 'authors_30d', 'bot_pct_30d', 'contributors', 'last_commit_ts',
    'last_commit_msg', 'last_commit_author', 'last_commit_sha',
    'releases', 'latest_release', 'latest_release_ts', 'weekly',
    'owner', 'owner_kind', 'owner_repos', 'owner_top', 'fetched_ts', 'error',
]
JSON_COLS = {'languages', 'topics', 'weekly', 'owner_top'}


def _path() -> str:
    return os.path.join(data_dir(), 'git.db')


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    if _path() not in _ready:
        conn.execute('PRAGMA journal_mode=WAL')
        cols = ', '.join(f'{c} {"INTEGER PRIMARY KEY" if c == "netuid" else ""}'
                         for c in COLS)
        conn.executescript(f'''
        CREATE TABLE IF NOT EXISTS repos ({cols});
        CREATE TABLE IF NOT EXISTS snaps (
            netuid INTEGER NOT NULL, day TEXT NOT NULL, ts INTEGER NOT NULL,
            stars INTEGER, forks INTEGER, open_issues INTEGER,
            commits_total INTEGER, contributors INTEGER,
            PRIMARY KEY (netuid, day));
        ''')
        have = {r[1] for r in conn.execute('PRAGMA table_info(repos)')}
        for c in COLS:
            if c not in have:
                conn.execute(f'ALTER TABLE repos ADD COLUMN {c}')
        _ready.add(_path())
    return conn


# --------------------------------------------------------------------- auth

_token: Dict[str, object] = {'v': None, 'ts': 0.0}


def token() -> Optional[str]:
    """A GitHub token, re-resolved every 10 min (gh logins rotate)."""
    if time.time() - float(_token['ts']) < 600:
        return _token['v']          # type: ignore[return-value]
    t = (os.environ.get('BT_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
         or os.environ.get('GH_TOKEN'))
    if not t:
        try:
            r = subprocess.run(['gh', 'auth', 'token'], capture_output=True,
                               text=True, timeout=10)
            t = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            t = None
    if not t:
        f = os.path.join(data_dir(), 'github.token')
        if os.path.exists(f):
            t = open(f).read().strip() or None
    _token.update(v=t, ts=time.time())
    return t


# --------------------------------------------------------------------- http

_rate: Dict[str, Optional[int]] = {'remaining': None, 'reset': None}


def _req(url: str, body: Optional[Dict] = None) -> Tuple[int, Dict, object]:
    headers = {'User-Agent': UA, 'Accept': 'application/vnd.github+json'}
    t = token()
    if t:
        headers['Authorization'] = f'bearer {t}'
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status, hdrs, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, dict(e.headers or {}), e.read()
    if hdrs.get('X-RateLimit-Remaining') is not None:
        _rate['remaining'] = int(hdrs['X-RateLimit-Remaining'])
        _rate['reset'] = int(hdrs.get('X-RateLimit-Reset') or 0)
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    return status, hdrs, payload


def _gql(query: str, variables: Dict) -> Dict:
    status, _, payload = _req(f'{API}/graphql', {'query': query, 'variables': variables})
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f'github graphql {status}: {str(payload)[:200]}')
    return payload


# ------------------------------------------------------------------ parsing

_BOT = re.compile(r"\[bot\]|-bot$|^github-actions|^actions@users\.noreply|^dependabot|^renovate|"
                  r"^noreply@github\.com$", re.I)
_GH = re.compile(r'^https?://(?:www\.)?github\.com/(.+?)/?$', re.I)


def parse_url(url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """('owner', 'owner/name' | None) from any github URL in an identity.

    github.com/o/r[/tree/x][.git] -> repo; github.com/o and
    github.com/orgs/o[/repositories] -> owner only.
    """
    m = _GH.match((url or '').strip())
    if not m:
        return None, None
    parts = [p for p in m.group(1).split('/') if p]
    if parts and parts[0] == 'orgs':
        parts = parts[1:2]
    if not parts:
        return None, None
    owner = parts[0]
    if len(parts) == 1:
        return owner, None
    name = parts[1].removesuffix('.git')
    return owner, f'{owner}/{name}'


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d')


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ts(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    return int(datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
               .replace(tzinfo=timezone.utc).timestamp())


# ------------------------------------------------------------------ fetching

_OWNER_REPOS = '''repositories(first: 6, privacy: PUBLIC, isFork: false,
      orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount nodes { name stargazerCount pushedAt description } }'''

_REPO_FIELDS = '''
  nameWithOwner description url homepageUrl stargazerCount forkCount
  watchers { totalCount }
  openIssues: issues(states: OPEN) { totalCount }
  closedIssues: issues(states: CLOSED) { totalCount }
  openPRs: pullRequests(states: OPEN) { totalCount }
  mergedPRs: pullRequests(states: MERGED) { totalCount }
  createdAt pushedAt isArchived isFork diskUsage
  licenseInfo { spdxId }
  primaryLanguage { name }
  languages(first: 6, orderBy: {field: SIZE, direction: DESC}) {
    totalSize edges { size node { name color } } }
  repositoryTopics(first: 12) { nodes { topic { name } } }
  releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) {
    totalCount nodes { tagName publishedAt } }
  owner { __typename login
    ... on Organization { %s }
    ... on User { %s } }
  defaultBranchRef { name target { ... on Commit {
    all: history { totalCount }
    d7: history(since: $d7) { totalCount }
    d30: history(since: $d30, first: 100) { totalCount nodes { author { email user { login } } } }
    d90: history(since: $d90) { totalCount }
    last: history(first: 1) { nodes { oid committedDate messageHeadline
      author { name user { login } } } } } } }
''' % (_OWNER_REPOS, _OWNER_REPOS)


def resolve_owner(owner: str) -> Optional[str]:
    """An org/user URL -> its most-starred non-fork public repo (ties -> the
    most recently pushed, so an org of 0-star repos lands on the live one)."""
    q = '''query($o: String!) { repositoryOwner(login: $o) {
        repositories(first: 20, privacy: PUBLIC, isFork: false,
          orderBy: {field: STARGAZERS, direction: DESC}) {
          nodes { nameWithOwner stargazerCount pushedAt } } } }'''
    d = _gql(q, {'o': owner}).get('data') or {}
    nodes = (((d.get('repositoryOwner') or {}).get('repositories') or {}).get('nodes') or [])
    if not nodes:
        return None
    return max(nodes, key=lambda n: (n['stargazerCount'], n.get('pushedAt') or ''))['nameWithOwner']


def _row_from_gql(netuid: int, url: str, r: Dict, resolved_from: Optional[str]) -> Dict:
    hist = ((r.get('defaultBranchRef') or {}).get('target') or {})
    d30 = hist.get('d30') or {}
    who, bots = [], 0
    for n in d30.get('nodes') or []:
        a = n.get('author') or {}
        login = (a.get('user') or {}).get('login')
        if any(x and _BOT.search(x) for x in (login, a.get('email'), a.get('name'))):
            bots += 1
            who.append(None)
        else:
            who.append(login or a.get('email'))
    authors = {x for x in who if x}
    last = ((hist.get('last') or {}).get('nodes') or [{}])[0] or {}
    lang = r.get('languages') or {}
    size = lang.get('totalSize') or 0
    langs = [{'name': e['node']['name'], 'color': e['node'].get('color'),
              'pct': round(100.0 * e['size'] / size, 1) if size else None}
             for e in lang.get('edges') or []]
    rel = r.get('releases') or {}
    reln = (rel.get('nodes') or [{}])[0] or {}
    own = r.get('owner') or {}
    orep = own.get('repositories') or {}
    return {
        'netuid': netuid, 'url': url, 'repo': r['nameWithOwner'],
        'resolved_from': resolved_from, 'description': r.get('description'),
        'homepage': r.get('homepageUrl') or None,
        'stars': r.get('stargazerCount'), 'forks': r.get('forkCount'),
        'watchers': (r.get('watchers') or {}).get('totalCount'),
        'open_issues': (r.get('openIssues') or {}).get('totalCount'),
        'closed_issues': (r.get('closedIssues') or {}).get('totalCount'),
        'open_prs': (r.get('openPRs') or {}).get('totalCount'),
        'merged_prs': (r.get('mergedPRs') or {}).get('totalCount'),
        'license': (r.get('licenseInfo') or {}).get('spdxId'),
        'language': (r.get('primaryLanguage') or {}).get('name'),
        'languages': langs,
        'topics': [n['topic']['name'] for n in (r.get('repositoryTopics') or {}).get('nodes') or []],
        'created_at': _ts(r.get('createdAt')), 'pushed_at': _ts(r.get('pushedAt')),
        'archived': int(bool(r.get('isArchived'))), 'is_fork': int(bool(r.get('isFork'))),
        'size_kb': r.get('diskUsage'),
        'default_branch': (r.get('defaultBranchRef') or {}).get('name'),
        'commits_total': (hist.get('all') or {}).get('totalCount'),
        'commits_7d': (hist.get('d7') or {}).get('totalCount'),
        'commits_30d': d30.get('totalCount'),
        'commits_90d': (hist.get('d90') or {}).get('totalCount'),
        'authors_30d': len(authors),
        'bot_pct_30d': round(100.0 * bots / len(who), 1) if who else None,
        'last_commit_ts': _ts(last.get('committedDate')),
        'last_commit_msg': last.get('messageHeadline'),
        'last_commit_author': (((last.get('author') or {}).get('user') or {}).get('login')
                               or (last.get('author') or {}).get('name')),
        'last_commit_sha': (last.get('oid') or '')[:10] or None,
        'releases': rel.get('totalCount'),
        'latest_release': reln.get('tagName'),
        'latest_release_ts': _ts(reln.get('publishedAt')),
        'owner': own.get('login'),
        'owner_kind': {'Organization': 'org', 'User': 'user'}.get(own.get('__typename')),
        'owner_repos': orep.get('totalCount'),
        'owner_top': [{'name': n['name'], 'stars': n['stargazerCount'],
                       'pushed_at': _ts(n.get('pushedAt')), 'description': n.get('description')}
                      for n in orep.get('nodes') or []],
    }


def _contributors(repo: str) -> Optional[int]:
    """All-time contributor count off the Link header's last page (per_page=1)."""
    status, hdrs, payload = _req(f'{API}/repos/{repo}/contributors?per_page=1&anon=1')
    if status == 204:
        return 0
    if status != 200:
        return None
    link = hdrs.get('Link') or hdrs.get('link') or ''
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    return int(m.group(1)) if m else len(payload or [])


def _weekly(repo: str) -> Optional[List[int]]:
    """52 weekly commit counts (oldest first). GitHub answers 202 while it
    computes the stats — keep the previous series then."""
    status, _, payload = _req(f'{API}/repos/{repo}/stats/participation')
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get('all'), list):
        return payload['all']
    return None


def _rest_basic(netuid: int, url: str, repo: str, resolved_from: Optional[str]) -> Dict:
    """No-token fallback: the repo endpoint alone."""
    status, _, r = _req(f'{API}/repos/{repo}')
    if status != 200 or not isinstance(r, dict):
        raise RuntimeError(f'github {status} for {repo}')
    owner = r.get('owner') or {}
    return {
        'netuid': netuid, 'url': url, 'repo': r['full_name'], 'resolved_from': resolved_from,
        'description': r.get('description'), 'homepage': r.get('homepage') or None,
        'stars': r.get('stargazers_count'), 'forks': r.get('forks_count'),
        'watchers': r.get('subscribers_count'), 'open_issues': r.get('open_issues_count'),
        'license': (r.get('license') or {}).get('spdx_id'), 'language': r.get('language'),
        'topics': r.get('topics') or [], 'created_at': _ts(r.get('created_at')),
        'pushed_at': _ts(r.get('pushed_at')), 'archived': int(bool(r.get('archived'))),
        'is_fork': int(bool(r.get('fork'))), 'size_kb': r.get('size'),
        'default_branch': r.get('default_branch'), 'owner': owner.get('login'),
        'owner_kind': {'Organization': 'org', 'User': 'user'}.get(owner.get('type')),
    }


def fetch_batch(subs: List[Dict]) -> List[Dict]:
    """Fetch every subnet in `subs` ({netuid, github}); one GraphQL round trip
    for the batch, then two cheap REST calls per repo. Never raises — a
    failing subnet comes back as {'netuid', 'error'}."""
    out: List[Dict] = []
    targets: List[Tuple[Dict, str, Optional[str]]] = []
    for s in subs:
        owner, repo = parse_url(s.get('github'))
        resolved_from = None
        try:
            if owner and not repo:
                resolved_from = s['github']
                repo = resolve_owner(owner) if token() else None
                if not repo:
                    raise RuntimeError(f'no public repos under {owner}')
            if not repo:
                raise RuntimeError('no github repo in the subnet identity')
            targets.append((s, repo, resolved_from))
        except Exception as e:
            out.append({'netuid': s['netuid'], 'url': s.get('github'), 'error': str(e)[:300]})
    if not targets:
        return out
    now = time.time()
    if not token():
        for s, repo, rf in targets:
            try:
                out.append(_rest_basic(s['netuid'], s['github'], repo, rf))
            except Exception as e:
                out.append({'netuid': s['netuid'], 'url': s.get('github'), 'repo': repo,
                            'error': str(e)[:300]})
        return out
    parts = []
    for i, (_, repo, _) in enumerate(targets):
        o, n = repo.split('/', 1)
        parts.append(f'r{i}: repository(owner: {json.dumps(o)}, name: {json.dumps(n)}) {{ {_REPO_FIELDS} }}')
    q = ('query($d7: GitTimestamp!, $d30: GitTimestamp!, $d90: GitTimestamp!) {\n'
         + '\n'.join(parts) + '\n}')
    payload = _gql(q, {'d7': _iso(now - 7 * 86400), 'd30': _iso(now - 30 * 86400),
                       'd90': _iso(now - 90 * 86400)})
    data = payload.get('data') or {}
    errs = {}
    for e in payload.get('errors') or []:
        p = (e.get('path') or [''])[0]
        errs.setdefault(p, e.get('message'))
    for i, (s, repo, rf) in enumerate(targets):
        r = data.get(f'r{i}')
        if not r:
            out.append({'netuid': s['netuid'], 'url': s.get('github'), 'repo': repo,
                        'error': errs.get(f'r{i}') or 'repository not found'})
            continue
        try:
            row = _row_from_gql(s['netuid'], s['github'], r, rf)
            row['contributors'] = _contributors(row['repo'])
            w = _weekly(row['repo'])
            if w is not None:
                row['weekly'] = w
            out.append(row)
        except Exception as e:
            out.append({'netuid': s['netuid'], 'url': s.get('github'), 'repo': repo,
                        'error': f'{type(e).__name__}: {e}'[:300]})
    return out


# ------------------------------------------------------------------- ingest

def store(rows: List[Dict], now: Optional[float] = None) -> int:
    """Upsert fetched rows. An error row keeps the last good data and only
    records the error + fetch time; a missing weekly series keeps the old one."""
    now = now or time.time()
    n = 0
    with _db_lock, _connect() as c:
        for row in rows:
            old = c.execute('SELECT * FROM repos WHERE netuid = ?', (row['netuid'],)).fetchone()
            merged = dict(old) if old else {k: None for k in COLS}
            if row.get('error'):
                if not old or (row.get('url') and row['url'] != old['url']):
                    merged = {k: None for k in COLS}
                    merged.update(netuid=row['netuid'], url=row.get('url'), repo=row.get('repo'))
                merged.update(error=row['error'], fetched_ts=int(now))
            else:
                for k, v in row.items():
                    if k in JSON_COLS:
                        v = json.dumps(v)
                    if k in COLS:
                        merged[k] = v
                merged.update(error=None, fetched_ts=int(now))
                c.execute('INSERT OR REPLACE INTO snaps VALUES (?,?,?,?,?,?,?,?)',
                          (row['netuid'], _day(now), int(now), row.get('stars'),
                           row.get('forks'), row.get('open_issues'),
                           row.get('commits_total'), row.get('contributors')))
            c.execute(f'INSERT OR REPLACE INTO repos ({", ".join(COLS)}) '
                      f'VALUES ({", ".join("?" * len(COLS))})', [merged[k] for k in COLS])
            n += 1
    return n


def subnets() -> List[Dict]:
    from . import news
    return news.subnets()


def refresh(netuid: Optional[int] = None) -> Dict:
    """Fetch now: one subnet, or (netuid omitted) every subnet in batches."""
    subs = [s for s in subnets() if s['netuid'] != 0]
    if netuid is not None:
        subs = [s for s in subs if s['netuid'] == netuid]
        if not subs:
            raise ValueError(f'unknown subnet {netuid} — the market indexer has not seen it yet')
    t0 = time.time()
    rows: List[Dict] = []
    for i in range(0, len(subs), BATCH):
        rows += fetch_batch(subs[i:i + BATCH])
    store(rows)
    ok = [r for r in rows if not r.get('error')]
    res = {'fetched': len(ok), 'errors': [{'netuid': r['netuid'], 'error': r['error']}
                                          for r in rows if r.get('error')],
           'ms': int((time.time() - t0) * 1000), 'rate_remaining': _rate['remaining']}
    if netuid is not None:
        res['repo'] = repo(netuid)
    return res


# -------------------------------------------------------------------- reads

def _decode(r: sqlite3.Row) -> Dict:
    d = dict(r)
    for k in JSON_COLS:
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = None
    return d


def _growth(c: sqlite3.Connection, now: float) -> Dict[int, Dict]:
    """stars/forks/commits gained over 7d and 30d, from the daily snaps."""
    out: Dict[int, Dict] = {}
    for label, days in (('7d', 7), ('30d', 30)):
        cut = _day(now - days * 86400)
        for netuid, stars, forks, commits in c.execute(
                'SELECT s.netuid, s.stars, s.forks, s.commits_total FROM snaps s '
                'JOIN (SELECT netuid, MAX(day) d FROM snaps WHERE day <= ? GROUP BY netuid) b '
                'ON s.netuid = b.netuid AND s.day = b.d', (cut,)):
            out.setdefault(netuid, {})[label] = (stars, forks, commits)
    return out


def dev_score(r: Dict, now: Optional[float] = None) -> Optional[float]:
    """0-100 activity score: recent human commits (bot share of the newest
    100 commits discounted), distinct authors, freshness,
    community (stars/forks/contributors), log-scaled so one mega-repo can't
    flatten the rest. A heuristic for sorting, not a verdict."""
    if r.get('repo') is None or r.get('stars') is None:
        return None
    now = now or time.time()
    lg = lambda x, cap: min(math.log1p(max(x or 0, 0)) / math.log1p(cap), 1.0)
    fresh = 0.0
    if r.get('last_commit_ts') or r.get('pushed_at'):
        age_d = (now - (r.get('last_commit_ts') or r.get('pushed_at'))) / 86400
        fresh = max(0.0, 1.0 - age_d / 90)
    human = (r.get('commits_30d') or 0) * (1 - (r.get('bot_pct_30d') or 0) / 100)
    s = (30 * lg(human, 300) + 20 * lg(r.get('authors_30d'), 25)
         + 15 * fresh + 15 * lg(r.get('commits_90d'), 800)
         + 10 * lg(r.get('stars'), 2000) + 5 * lg(r.get('contributors'), 100)
         + 5 * lg(r.get('forks'), 500))
    if r.get('archived'):
        s *= 0.3
    return round(s, 1)


def _finish(d: Dict, growth: Dict[int, Dict], now: float) -> Dict:
    for label in ('7d', '30d'):
        base = growth.get(d['netuid'], {}).get(label)
        for i, k in enumerate(('stars', 'forks', 'commits')):
            cur = d.get('commits_total' if k == 'commits' else k)
            d[f'{k}_gain_{label}'] = (cur - base[i]) if base and cur is not None and base[i] is not None else None
    d['dev_score'] = dev_score(d, now)
    d['age_days'] = int((now - d['created_at']) / 86400) if d.get('created_at') else None
    d['days_since_commit'] = (round((now - (d.get('last_commit_ts') or d.get('pushed_at'))) / 86400, 1)
                              if (d.get('last_commit_ts') or d.get('pushed_at')) else None)
    return d


def repo(netuid: int) -> Optional[Dict]:
    """One subnet's repo stats (None if never fetched)."""
    now = time.time()
    with _connect() as c:
        r = c.execute('SELECT * FROM repos WHERE netuid = ?', (netuid,)).fetchone()
        if not r:
            return None
        g = _growth(c, now)
        hist = [dict(x) for x in c.execute(
            'SELECT day, stars, forks, open_issues, commits_total, contributors '
            'FROM snaps WHERE netuid = ? ORDER BY day DESC LIMIT 90', (netuid,))][::-1]
    d = _finish(_decode(r), g, now)
    d['history'] = hist
    return d


SORTS = {'dev_score', 'stars', 'forks', 'commits_7d', 'commits_30d', 'commits_90d',
         'authors_30d', 'contributors', 'open_issues', 'open_prs', 'merged_prs',
         'releases', 'pushed_at', 'last_commit_ts', 'stars_gain_7d', 'stars_gain_30d',
         'commits_total', 'created_at', 'netuid'}


def board(sort_by: str = 'dev_score', limit: int = 0, weekly: bool = True) -> Dict:
    """Every subnet's dev activity in one table, sorted."""
    if sort_by not in SORTS:
        raise ValueError(f'sort_by must be one of {", ".join(sorted(SORTS))}')
    now = time.time()
    with _connect() as c:
        rows = c.execute('SELECT * FROM repos').fetchall()
        g = _growth(c, now)
    out = []
    for r in rows:
        d = _finish(_decode(r), g, now)
        if not weekly:
            d.pop('weekly', None)
        d.pop('owner_top', None)
        out.append(d)
    rev = sort_by != 'netuid'
    out.sort(key=lambda d: (d.get(sort_by) is not None, d.get(sort_by) or 0), reverse=rev)
    known = {s['netuid'] for s in subnets() if s['netuid'] != 0}
    with_repo = [d for d in out if d.get('repo') and not d.get('error')]
    return {
        'sort_by': sort_by, 'count': len(out),
        'subnets_known': len(known),
        'with_repo': len(with_repo),
        'no_repo': sorted(known - {d['netuid'] for d in with_repo}),
        'active_7d': sum(1 for d in with_repo if (d.get('commits_7d') or 0) > 0),
        'commits_30d_total': sum(d.get('commits_30d') or 0 for d in with_repo),
        'rows': out[:limit] if limit else out,
    }


def status() -> Dict:
    with _connect() as c:
        n = c.execute('SELECT COUNT(*), SUM(error IS NULL), MAX(fetched_ts), MIN(fetched_ts) '
                      'FROM repos').fetchone()
        errs = [dict(r) for r in c.execute(
            'SELECT netuid, url, error, fetched_ts FROM repos WHERE error IS NOT NULL '
            'ORDER BY netuid')]
    return {'repos': n[0], 'ok': n[1] or 0, 'newest_fetch': n[2], 'oldest_fetch': n[3],
            'authenticated': bool(token()), 'rate_remaining': _rate['remaining'],
            'rate_reset': _rate['reset'], 'refresh_sec': REFRESH_SEC, 'batch': BATCH,
            'errors': errs, 'running': _thread is not None and _thread.is_alive()}


# ---------------------------------------------------------------- background

def _due(subs: List[Dict]) -> List[Dict]:
    with _connect() as c:
        last = {r[0]: (r[1] or 0, r[2]) for r in c.execute(
            'SELECT netuid, fetched_ts, url FROM repos')}
    due = []
    for s in subs:
        ts, url = last.get(s['netuid'], (0, None))
        if s['netuid'] == 0:
            continue
        if time.time() - ts > REFRESH_SEC or (url or None) != (s.get('github') or None):
            due.append((ts, s))
    due.sort(key=lambda x: x[0])
    return [s for _, s in due]


_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _loop() -> None:
    _stop.wait(30)                   # let the market indexer warm subnet_meta first
    while not _stop.is_set():
        try:
            due = _due(subnets())
            if due:
                if _rate['remaining'] is not None and _rate['remaining'] < 200:
                    _stop.wait(max(60, (_rate['reset'] or 0) - time.time()))
                    continue
                store(fetch_batch(due[:BATCH]))
        except Exception:
            pass
        _stop.wait(PACE_SEC)


def start() -> None:
    global _thread
    if os.environ.get('BT_NO_GIT') == '1' or os.environ.get('BT_NO_SNAPSHOT') == '1':
        return
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='bt-git', daemon=True)
        _thread.start()
