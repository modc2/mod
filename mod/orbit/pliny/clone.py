#!/usr/bin/env python3
"""plinyville clone archiver — fill the market over git instead of the REST API.

The REST path (`market.install`) spends one API call per repo and one per file
against GitHub's **60 requests/hour** anonymous budget, so a single `stock` run
ends in `403: API rate limit` with two repos archived. Cloning does not touch
that budget at all: `git clone` (and `gh repo clone`, which is the same thing
with auth attached) goes to the git transport, and one shallow clone brings down
the whole tree in a single request.

So this module clones each `elder-plinius` repo once into a local cache
(`~/.mod/pliny/clones/<repo>`), reads the tree off disk, and hands the
result to `Market.store_bundle` — the same write path the REST archiver uses, so
the mods it produces are byte-identical in shape: they serve the same
`/m/<repo>` app, the same api and the same MCP server, and get the same real
localfs CID when `mod.py` pins them.

    m pliny/clone L1B3RT4S     # clone + archive one repo
    m pliny/stock              # clone + archive all 46, no rate wall
    m pliny/clones             # what is on disk, and how stale

`gh` is used for the clone when it is logged in (it attaches credentials, which
also lifts the *API* budget for the metadata calls); plain anonymous `git` is
the fallback and works on its own.
"""
import json
import os
import shutil
import subprocess
import time

from market import FILE_BYTES_CAP, Market
from plinyville import GitHubError, Ville

# Where the working clones live. They are a cache, not the archive: the archive
# is the bundle in the store mod, and this directory can be deleted at any time.
CLONE_ROOT = os.path.expanduser(
    os.environ.get('PLINYVILLE_CLONE_ROOT', '~/.mod/pliny/clones'))

# Cloning is local and free, so the caps here are far looser than the REST ones.
CLONE_FILES_CAP = int(os.environ.get('PLINYVILLE_CLONE_FILES_CAP', 600))
CLONE_BYTES_CAP = int(os.environ.get('PLINYVILLE_CLONE_FILE_BYTES_CAP',
                                     FILE_BYTES_CAP))
# A whole-bundle ceiling: one repo should not turn the market index into a blob.
CLONE_TOTAL_BYTES_CAP = int(os.environ.get('PLINYVILLE_CLONE_TOTAL_BYTES_CAP',
                                           12_000_000))
CLONE_TIMEOUT = int(os.environ.get('PLINYVILLE_CLONE_TIMEOUT', 300))
# Where clones come from. Overridable so the tests can clone a local repo
# instead of the internet — `git clone` takes a path as happily as a URL.
GIT_BASE = os.environ.get('PLINYVILLE_GIT_BASE', 'https://github.com')


class CloneError(RuntimeError):
    """A git/gh invocation failed. Carries the stderr git actually printed."""


class Cloner:
    """Archive repos into the market by cloning them, not by calling the API."""

    def __init__(self, market: Market = None, ville: Ville = None, root=None):
        self.mkt = market or Market(ville)
        self.ville = self.mkt.ville
        self.user = self.ville.user
        self.root = os.path.expanduser(root or CLONE_ROOT)
        self._gh = None                 # resolved lazily by gh_ready()

    # ── tools ────────────────────────────────────────────────────────────────

    @staticmethod
    def _run(argv, cwd=None, timeout=CLONE_TIMEOUT) -> str:
        try:
            p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout)
        except FileNotFoundError:
            raise CloneError(f'{argv[0]} is not installed') from None
        except subprocess.TimeoutExpired:
            raise CloneError(f'{argv[0]} timed out after {timeout}s') from None
        if p.returncode != 0:
            err = (p.stderr or p.stdout or '').strip().splitlines()
            raise CloneError((err[-1] if err else f'{argv[0]} exited {p.returncode}'))
        return p.stdout

    def gh_ready(self) -> bool:
        """Is the GitHub CLI installed *and* logged in? If so we clone through it.
        Answered once per process — `gh auth status` is a network call."""
        if self._gh is None:
            if not shutil.which('gh'):
                self._gh = False
            else:
                try:
                    self._run(['gh', 'auth', 'status'], timeout=20)
                    self._gh = True
                except CloneError:
                    self._gh = False
        return self._gh

    def tooling(self) -> dict:
        gh = self.gh_ready()
        return {'gh': bool(shutil.which('gh')), 'gh_authenticated': gh,
                'git': bool(shutil.which('git')),
                'clone_via': 'gh' if gh else 'git',
                'note': 'git transport — the 60/hr REST budget does not apply'}

    # ── the clone cache ──────────────────────────────────────────────────────

    def path(self, name) -> str:
        return os.path.join(self.root, self.ville._safe(name))

    def clone(self, name, refresh=False) -> dict:
        """Make sure `<root>/<repo>` is a shallow clone at the current HEAD.

        Already there? A `fetch --depth 1` + hard reset brings it forward, which
        is one round trip instead of a re-download. `refresh` re-clones from
        scratch (used when a previous attempt left a half-written directory)."""
        name = self.ville._safe(name)
        dest = self.path(name)
        os.makedirs(self.root, exist_ok=True)
        url = f'{GIT_BASE}/{self.user}/{name}.git'
        t0 = time.time()

        if refresh and os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)

        # gh only knows how to clone from github.com; a redirected base is git's.
        via = 'gh' if (GIT_BASE == 'https://github.com' and self.gh_ready()) else 'git'
        if os.path.isdir(os.path.join(dest, '.git')):
            via = 'git'                        # an existing clone is fetched, not re-cloned
            before = self._head(dest)
            try:
                self._run(['git', 'fetch', '--depth', '1', '--quiet', 'origin'], cwd=dest)
                branch = self._remote_head(dest)
                self._run(['git', 'reset', '--hard', '--quiet', f'origin/{branch}'],
                          cwd=dest)
                action = 'updated' if self._head(dest) != before else 'current'
            except CloneError:                 # a broken cache is not fatal — redo it
                shutil.rmtree(dest, ignore_errors=True)
                return self.clone(name, refresh=False)
        else:
            shutil.rmtree(dest, ignore_errors=True)
            if via == 'gh':
                argv = ['gh', 'repo', 'clone', f'{self.user}/{name}', dest,
                        '--', '--depth', '1', '--single-branch', '--quiet']
            else:
                argv = ['git', 'clone', '--depth', '1', '--single-branch',
                        '--quiet', url, dest]
            try:
                self._run(argv)
            except CloneError as e:
                raise CloneError(f'{name}: {e}') from None
            action = 'cloned'

        return {'name': name, 'path': dest, 'action': action,
                'head': self._head(dest), 'branch': self._branch(dest), 'via': via,
                'seconds': round(time.time() - t0, 2),
                'bytes_on_disk': self._du(dest)}

    def clones(self) -> dict:
        """What is in the clone cache: head, branch, size, and whether the store
        archive was taken from that same commit."""
        inst = self.mkt.installed()
        out = []
        for name in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else []:
            d = os.path.join(self.root, name)
            if not os.path.isdir(os.path.join(d, '.git')):
                continue
            head = self._head(d) or 'empty-repo'   # matches what archive() records
            entry = inst.get(name) or {}
            out.append({'name': name, 'head': head, 'branch': self._branch(d),
                        'bytes_on_disk': self._du(d),
                        'archived': bool(entry),
                        'archived_head': entry.get('head'),
                        'stale': bool(entry) and entry.get('head') != head})
        return {'root': self.root, 'count': len(out),
                'bytes_on_disk': sum(c['bytes_on_disk'] for c in out),
                'clones': out, **self.tooling()}

    def forget(self, name=None) -> dict:
        """Drop the working clone(s). The archives in the store mod survive —
        this only reclaims the disk the git checkouts are sitting on."""
        if name:
            d = self.path(name)
            had = os.path.isdir(d)
            shutil.rmtree(d, ignore_errors=True)
            return {'removed': [name] if had else [], 'root': self.root}
        names = sorted(os.listdir(self.root)) if os.path.isdir(self.root) else []
        shutil.rmtree(self.root, ignore_errors=True)
        return {'removed': names, 'root': self.root}

    # ── archive: clone → bundle → store mod ──────────────────────────────────

    def archive(self, name, refresh=False, files_cap=CLONE_FILES_CAP,
                byte_cap=CLONE_BYTES_CAP) -> dict:
        """Clone one repo and write it into the store as a market mod."""
        name = self.ville._safe(name)
        cl = self.clone(name, refresh=False)
        head = cl['head'] or 'empty-repo'      # a repo with no commits has no HEAD
        entry = self.mkt.installed().get(name)
        if entry and not refresh and entry.get('head') == head:
            return {**entry, 'name': name, 'reused': True, 'clone': cl}

        # Three of his repos have never been committed to. A clone of one is
        # legal but has no HEAD, so it archives as an empty mod rather than as
        # a failure — the market lists what exists, including the empties.
        tree = self._ls_tree(cl['path']) if cl['head'] else []
        files, skipped, total = {}, 0, 0
        for e in tree:
            if e['type'] != 'blob':            # submodules ('commit') hold no text
                continue
            if len(files) >= int(files_cap) or total >= CLONE_TOTAL_BYTES_CAP:
                skipped += 1
                continue
            if not self.mkt._texty(e['path']) or e['size'] > int(byte_cap):
                skipped += 1
                continue
            text = self._read(os.path.join(cl['path'], e['path']))
            if text is None:                   # binary despite the extension
                skipped += 1
                continue
            files[e['path']] = text
            total += len(text)

        out = self.mkt.store_bundle(
            name, meta=self._meta(name, cl), branch=cl['branch'],
            readme=self._readme(cl['path'], files), tree=tree, files=files,
            skipped=skipped, source='git-clone', head=head)
        out['clone'] = cl
        return out

    def archive_all(self, names=None, limit=None, refresh=False, sleep=0.0) -> dict:
        """Clone and archive the whole market. Nothing here is rate-limited, so
        unlike the REST `install_all` this does not stop at a wall — a repo that
        fails is recorded and the run carries on."""
        if names:
            todo = [self.ville._safe(n) for n in
                    (names.split(',') if isinstance(names, str) else names)]
        else:
            todo = [r['name'] for r in self.ville.repos(n=500)['repos']]
        if limit:
            todo = todo[:int(limit)]

        done, failed, t0 = [], [], time.time()
        for name in todo:
            try:
                r = self.archive(name, refresh=refresh)
                done.append({'name': name, 'reused': bool(r.get('reused')),
                             'files_stored': r.get('files_stored'),
                             'head': (r.get('clone') or {}).get('head'),
                             'action': (r.get('clone') or {}).get('action')})
            except (CloneError, GitHubError, OSError, ValueError) as e:
                failed.append({'name': name, 'error': f'{type(e).__name__}: {e}'})
            if sleep:
                time.sleep(float(sleep))
        return {'total': len(todo), 'installed': len(done), 'failed': len(failed),
                'seconds': round(time.time() - t0, 1),
                'done': done, 'errors': failed, **self.tooling()}

    # ── discovery without the REST budget ────────────────────────────────────

    def discover(self, save=True) -> dict:
        """Re-list the user's repos *without* spending REST calls.

        `repos(refresh=1)` pulls the gallery from `/users/<user>/repos`, which is
        the same 60/hr budget the archiver used to burn — so when it is spent the
        market cannot even learn that a new repo exists. Two ways around that:
        `gh repo list` when the CLI is logged in (its own, much larger budget),
        and otherwise the public repositories *page*, which is HTML and costs the
        API nothing. Either way the result is merged into the cached gallery —
        rows already there keep their fields, new repos are added — so a scrape
        can only ever add to what the API previously told us."""
        rows, source = None, None
        if self.gh_ready():
            try:
                rows, source = self._gh_list(), 'gh'
            except (CloneError, ValueError):
                rows = None
        if not rows:
            rows, source = self._html_list(), 'github-html'

        st = self.ville._load()
        known = {r['name']: r for r in (st.get('repos') or [])}
        added = []
        for r in rows:
            cur = known.get(r['name'])
            if cur is None:
                known[r['name']] = r
                added.append(r['name'])
            else:                              # overlay only what we actually read
                for k, v in r.items():
                    if v not in (None, '', [], 0) or cur.get(k) in (None, ''):
                        cur[k] = v
        merged = sorted(known.values(),
                        key=lambda r: r.get('pushed_at') or '', reverse=True)
        if save:
            st['repos'] = merged
            st['updated'] = time.time()
            st['repos_source'] = source
            self.ville._save(st)
        return {'source': source, 'count': len(merged), 'seen': len(rows),
                'added': added, 'saved': bool(save),
                'note': 'no REST calls were spent'}

    def _gh_list(self) -> list:
        out = self._run(['gh', 'repo', 'list', self.user, '--limit', '500', '--json',
                         'name,description,primaryLanguage,stargazerCount,forkCount,'
                         'repositoryTopics,url,defaultBranchRef,pushedAt,isFork,'
                         'isArchived,homepageUrl'], timeout=60)
        rows = []
        for r in json.loads(out or '[]'):
            rows.append({
                'name': r.get('name'),
                'full_name': f"{self.user}/{r.get('name')}",
                'description': (r.get('description') or '').strip(),
                'language': (r.get('primaryLanguage') or {}).get('name'),
                'stars': r.get('stargazerCount', 0),
                'forks': r.get('forkCount', 0),
                'topics': [t.get('name') for t in (r.get('repositoryTopics') or [])
                           if t.get('name')],
                'homepage': (r.get('homepageUrl') or '').strip() or None,
                'url': r.get('url'),
                'default_branch': (r.get('defaultBranchRef') or {}).get('name') or 'main',
                'pushed_at': r.get('pushedAt'),
                'is_fork': bool(r.get('isFork')),
                'archived': bool(r.get('isArchived')),
            })
        return [r for r in rows if r['name']]

    def _html_list(self, pages=10) -> list:
        """Parse the public ?tab=repositories page. Read as one block per repo so
        a repo with no description cannot shift every later field by one."""
        import re
        import urllib.error
        import urllib.request
        rows, page = [], 1
        while page <= int(pages):
            url = (f'https://github.com/{self.user}?tab=repositories&page={page}')
            req = urllib.request.Request(url, headers={
                'User-Agent': 'plinyville (github.com/elder-plinius mirror)',
                'Accept': 'text/html'})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode('utf-8', 'replace')
            except urllib.error.HTTPError as e:
                raise GitHubError(f'github page {page}: {e.code}', e.code) from None
            except urllib.error.URLError as e:
                raise GitHubError(f'github unreachable: {e.reason}') from None

            chunks = html.split('itemprop="name codeRepository"')[1:]
            if not chunks:
                break
            for i, chunk in enumerate(chunks):
                block = chunk if i == len(chunks) - 1 else chunk
                name = re.search(r'>\s*([^<>\s][^<>]*?)\s*</a>', block)
                if not name:
                    continue
                name = name.group(1)
                desc = re.search(r'itemprop="description">\s*(.*?)\s*</p>', block, re.S)
                lang = re.search(r'itemprop="programmingLanguage">\s*([^<]+?)\s*<', block)
                star = re.search(re.escape(f'/{self.user}/{name}/stargazers') +
                                 r'".*?>\s*([\d,\.k]+)\s*<', block, re.S)
                when = re.search(r'<relative-time[^>]*datetime="([^"]+)"', block)
                rows.append({
                    'name': name,
                    'full_name': f'{self.user}/{name}',
                    'description': self._untag(desc.group(1)) if desc else '',
                    'language': lang.group(1) if lang else None,
                    'stars': self._num(star.group(1)) if star else 0,
                    'forks': 0,
                    'topics': [],
                    'homepage': None,
                    'url': f'https://github.com/{self.user}/{name}',
                    'default_branch': 'main',
                    'pushed_at': when.group(1) if when else None,
                    'is_fork': 'Forked from' in block[:4000],
                    'archived': False,
                })
            if f'page={page + 1}' not in html:
                break
            page += 1
        seen, uniq = set(), []
        for r in rows:                          # the page repeats pinned repos
            if r['name'] in seen:
                continue
            seen.add(r['name'])
            uniq.append(r)
        return uniq

    @staticmethod
    def _untag(s) -> str:
        import html as _html
        import re
        return _html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()

    @staticmethod
    def _num(s) -> int:
        s = str(s).replace(',', '').strip().lower()
        try:
            return int(float(s[:-1]) * 1000) if s.endswith('k') else int(float(s))
        except ValueError:
            return 0

    # ── reading a checkout ───────────────────────────────────────────────────

    def _ls_tree(self, path) -> list:
        """The recursive blob list, straight from git — same shape the REST tree
        endpoint returns (path/type/size/sha), so bundles stay interchangeable."""
        out = self._run(['git', 'ls-tree', '-r', '-l', 'HEAD'], cwd=path)
        tree = []
        for line in out.splitlines():
            head, _, rel = line.partition('\t')
            bits = head.split()
            if len(bits) < 4 or not rel:
                continue
            _mode, kind, sha, size = bits[0], bits[1], bits[2], bits[3]
            try:
                size = int(size)
            except ValueError:
                size = 0
            tree.append({'path': rel.strip('"'), 'type': kind, 'size': size,
                         'sha': sha})
        return tree

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding='utf-8') as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def _readme(self, path, files) -> str:
        """The repo's README, preferring one already read into the bundle."""
        for cand in ('README.md', 'readme.md', 'README.MD', 'Readme.md',
                     'README', 'README.txt', 'README.rst', 'README.mkd'):
            if cand in files:
                return files[cand]
            text = self._read(os.path.join(path, cand))
            if text is not None:
                return text
        return None

    def _meta(self, name, cl) -> dict:
        """Repo metadata for the manifest. The cached gallery row is used first —
        it is free — and GitHub is only asked if this repo is not in it (and even
        then a failure is survivable: the clone is what we are archiving)."""
        for r in self.ville.repos(n=500)['repos']:
            if r['name'] == name:
                return {**r, 'default_branch': cl['branch'] or r.get('default_branch')}
        try:
            return self.ville.repo(name)
        except (GitHubError, OSError):
            return {'name': name, 'full_name': f'{self.user}/{name}',
                    'description': '', 'url': f'https://github.com/{self.user}/{name}',
                    'default_branch': cl['branch'], 'stars': 0, 'topics': [],
                    'meta_source': 'clone-only (github unavailable)'}

    # ── git one-liners ───────────────────────────────────────────────────────

    def _head(self, path) -> str:
        try:
            return self._run(['git', 'rev-parse', 'HEAD'], cwd=path).strip()
        except CloneError:
            return ''

    def _branch(self, path) -> str:
        try:
            b = self._run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=path).strip()
            return b if b and b != 'HEAD' else self._remote_head(path)
        except CloneError:
            return 'main'

    def _remote_head(self, path) -> str:
        """The branch origin points at — a repo whose default is `master` (or
        anything else) must not be reset onto a `main` that does not exist."""
        try:
            ref = self._run(['git', 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD'],
                            cwd=path).strip()
            if ref.startswith('origin/'):
                return ref[len('origin/'):]
        except CloneError:
            pass
        try:
            for line in self._run(['git', 'branch', '-r'], cwd=path).splitlines():
                b = line.strip()
                if b.startswith('origin/') and '->' not in b:
                    return b[len('origin/'):]
        except CloneError:
            pass
        return 'HEAD'

    @staticmethod
    def _du(path) -> int:
        total = 0
        for root, _dirs, names in os.walk(path):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                except OSError:
                    pass
        return total


if __name__ == '__main__':                      # tiny CLI: python3 clone.py [repo…]
    import sys
    c = Cloner()
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if args:
        print(json.dumps({a: c.archive(a, refresh='--refresh' in sys.argv[1:])
                          .get('files_stored') for a in args}, indent=2))
    else:
        print(json.dumps(c.archive_all(refresh='--refresh' in sys.argv[1:]), indent=2))
