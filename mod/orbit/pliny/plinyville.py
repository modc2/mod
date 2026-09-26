#!/usr/bin/env python3
"""plinyville core — the mirror logic, with no HTTP of its own.

One `Ville` object answers every question the module can be asked: what repos
elder-plinius has published, what is inside one of them, and what the plinyworld
exhibit actually does. The API server, the app server, the MCP tools and the CLI
verbs are four thin skins over this file, so none of them can drift.

Data source: the GitHub REST API. Anonymous calls get 60/hour per IP, which one
`stock` run eats alive, so a token is resolved on every call from three places in
order: $GITHUB_TOKEN/$GH_TOKEN, this module's own secret at
~/.mod/pliny/github.json, then whatever account the `git` mod already has
connected. Reading it per call (not once at import) means `m pliny/token …`
takes effect on a running server with no restart. The repo cache and the
last-update marker live in ~/.mod/pliny/state.json.

Zero third-party dependencies: urllib only, so the servers start on a bare box.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

GITHUB_USER = os.environ.get('PLINYVILLE_USER', 'elder-plinius')
HERE = os.path.dirname(os.path.abspath(__file__))
PLINYWORLD_DIR = os.path.join(HERE, 'plinyworld')
UPSTREAM_DIR = os.path.join(PLINYWORLD_DIR, 'upstream')
DEFAULT_STATE = os.path.expanduser('~/.mod/pliny/state.json')
DEFAULT_TOKEN = os.path.expanduser('~/.mod/pliny/github.json')
GIT_TOKEN = os.path.expanduser('~/.mod/git/github.json')   # the git mod's PAT store

VERSION = '0.6.0'
DESCRIPTION = ("Mirrors github.com/elder-plinius as a live repo gallery and hosts a "
               "DEFANGED fork of his elder-plinius.github.io app (a clipboard-hijack "
               "red-team PoC) as a study exhibit — no live payload.")


class GitHubError(RuntimeError):
    """A GitHub REST call failed; carries the status so callers can say 'rate limit'."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class Ville:
    """The mirror. Every public verb returns plain JSON-able data."""

    def __init__(self, state_path=None, user=None, token_path=None):
        self.state_path = os.path.expanduser(state_path or DEFAULT_STATE)
        self.token_path = os.path.expanduser(token_path or DEFAULT_TOKEN)
        self.user = user or GITHUB_USER
        self.rate = {}          # last rate-limit headers GitHub sent back

    # ── state ───────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        return self._read_json(self.state_path)

    def _save(self, st: dict):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f)
        os.replace(tmp, self.state_path)

    # ── auth ────────────────────────────────────────────────────────────────

    @staticmethod
    def _read_json(path) -> dict:
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _token(self):
        """(token, where) — the token to call GitHub with, and where it came from.

        Resolved fresh on every call so a token dropped in while a server is up is
        picked up without a restart. Env wins (a shell can override for one run),
        then this module's secret, then the `git` mod's connected account — that
        file is the fleet's GitHub PAT, so plinyville borrows it instead of asking
        for a second copy."""
        for var in ('GITHUB_TOKEN', 'GH_TOKEN'):
            val = (os.environ.get(var) or '').strip()
            if val:
                return val, '$' + var
        own = (self._read_json(self.token_path).get('token') or '').strip()
        if own:
            return own, self.token_path
        for addr, entry in (self._read_json(GIT_TOKEN).get('accounts') or {}).items():
            logins = entry.get('logins') or {}
            rec = logins.get(entry.get('active')) or next(iter(logins.values()), {})
            tok = (rec.get('token') or '').strip()
            if tok:
                return tok, 'git mod (' + (rec.get('login') or addr) + ')'
        return None, None

    def set_token(self, token: str) -> dict:
        """Validate a PAT against /user and store it 0600. A bad token is rejected
        here rather than at the next rate wall."""
        token = str(token or '').strip()
        if not token:
            raise ValueError('a github token is required — create one at '
                             'https://github.com/settings/tokens (no scopes needed '
                             'for public repos)')
        req = urllib.request.Request('https://api.github.com/user', headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'plinyville/' + VERSION,
            'Authorization': f'Bearer {token}',
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                who = json.loads(r.read().decode('utf-8', 'replace'))
                self._note_rate(r.headers)
        except urllib.error.HTTPError as e:
            raise GitHubError(f'github rejected that token ({e.code}) — check it is '
                              'not expired and was copied whole', e.code) from None
        except urllib.error.URLError as e:
            raise GitHubError(f'github unreachable: {e.reason}') from None
        os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
        tmp = self.token_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'token': token, 'login': who.get('login'), 'saved': int(time.time())}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.token_path)
        return {'ok': True, 'login': who.get('login'), 'stored': self.token_path,
                'limit': self.rate.get('limit'), 'remaining': self.rate.get('remaining')}

    def clear_token(self) -> dict:
        """Forget this module's own token (env / the git mod's are untouched)."""
        had = os.path.exists(self.token_path)
        if had:
            os.remove(self.token_path)
        tok, where = self._token()
        return {'ok': True, 'cleared': had, 'still_authenticated_via': where if tok else None}

    # ── github ──────────────────────────────────────────────────────────────

    def _note_rate(self, headers) -> dict:
        """Remember what GitHub said about the budget on the way past."""
        def num(name):
            try:
                return int(headers.get(name))
            except (TypeError, ValueError):
                return None
        self.rate = {k: v for k, v in {
            'limit': num('X-RateLimit-Limit'),
            'remaining': num('X-RateLimit-Remaining'),
            'used': num('X-RateLimit-Used'),
            'reset': num('X-RateLimit-Reset'),
            'resource': headers.get('X-RateLimit-Resource'),
            'seen': int(time.time()),
        }.items() if v is not None}
        return self.rate

    def _rate_hint(self) -> str:
        """'…, resets in 23m' — the one number that tells you whether to wait."""
        reset = self.rate.get('reset')
        if not reset:
            return ''
        left = int(reset - time.time())
        if left <= 0:
            return ' — the window has just reset, try again'
        return (f' — resets in {left // 60}m{left % 60:02d}s'
                if left >= 60 else f' — resets in {left}s')

    def _get(self, url, params=None, raw=False):
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        req = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'plinyville/' + VERSION,
        })
        tok, where = self._token()
        if tok:
            req.add_header('Authorization', f'Bearer {tok}')
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read()
                self._note_rate(r.headers)
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = json.loads(e.read().decode('utf-8', 'replace')).get('message', '')
            except Exception:
                pass
            self._note_rate(e.headers or {})
            if e.code in (403, 429) and 'rate limit' in detail.lower():
                detail = detail.split('(But here')[0].strip() + self._rate_hint()
                detail += (f' — authenticated as {where}, so this is the 5,000/hr wall'
                           if tok else
                           ' — that is the 60/hr anonymous limit; run '
                           '`m pliny/token <github_pat>` (or set $GITHUB_TOKEN) '
                           'for 5,000/hr')
            elif e.code == 401 and tok:
                detail += f' — the token from {where} was rejected; replace it'
            raise GitHubError(f'github {e.code}: {detail or e.reason}', e.code) from None
        except urllib.error.URLError as e:
            raise GitHubError(f'github unreachable: {e.reason}') from None
        return body if raw else json.loads(body.decode('utf-8', 'replace'))

    def budget(self) -> dict:
        """The live rate-limit budget. /rate_limit is free — it never costs a call."""
        tok, where = self._token()
        core = (self._get('https://api.github.com/rate_limit')
                .get('resources', {}).get('core', {}))
        reset, now = core.get('reset') or 0, int(time.time())
        return {
            'authenticated': bool(tok),
            'auth_source': where,
            'limit': core.get('limit'),
            'remaining': core.get('remaining'),
            'used': core.get('used'),
            'reset': reset,
            'resets_in': max(0, reset - now) if reset else None,
            'note': ('anonymous — 60 requests/hour per IP; one full `stock` run needs '
                     'far more. Run `m pliny/token <github_pat>` for 5,000/hr.'
                     if not tok else f'authenticated via {where}'),
        }

    def _api(self, path, params=None, raw=False):
        return self._get('https://api.github.com' + path, params, raw)

    @staticmethod
    def _fmt_repo(r: dict) -> dict:
        return {
            'name': r.get('name'),
            'full_name': r.get('full_name'),
            'description': (r.get('description') or '').strip(),
            'language': r.get('language'),
            'stars': r.get('stargazers_count', 0),
            'forks': r.get('forks_count', 0),
            'topics': r.get('topics') or [],
            'homepage': (r.get('homepage') or '').strip() or None,
            'url': r.get('html_url'),
            'default_branch': r.get('default_branch') or 'main',
            'pushed_at': r.get('pushed_at'),
            'is_fork': bool(r.get('fork')),
            'archived': bool(r.get('archived')),
        }

    def _fetch_repos(self) -> list:
        """Every public repo under the user, newest push first."""
        out, page = [], 1
        while True:
            batch = self._api(f'/users/{self.user}/repos',
                              {'per_page': 100, 'page': page, 'sort': 'pushed'})
            if not batch:
                break
            out.extend(self._fmt_repo(r) for r in batch)
            if len(batch) < 100:
                break
            page += 1
            if page > 20:                      # hard safety cap
                break
        out.sort(key=lambda r: r.get('pushed_at') or '', reverse=True)
        return out

    # ── gallery ─────────────────────────────────────────────────────────────

    def repos(self, search=None, n=500, refresh=False) -> dict:
        """The cached repo gallery. First call (or refresh=True) pulls from GitHub."""
        st = self._load()
        rows = st.get('repos')
        if not rows or refresh:
            rows = self._fetch_repos()
            st['repos'] = rows
            st['updated'] = time.time()
            self._save(st)
        if search:
            s = str(search).lower()
            rows = [r for r in rows if s in (r['name'] or '').lower()
                    or s in (r.get('description') or '').lower()
                    or any(s in t.lower() for t in r.get('topics', []))]
        return {'user': self.user, 'count': len(rows), 'updated': st.get('updated'),
                'repos': rows[:int(n)]}

    def repo(self, name) -> dict:
        """One repo's live details, straight from GitHub."""
        return self._fmt_repo(self._api(f'/repos/{self.user}/{self._safe(name)}'))

    def readme(self, name) -> dict:
        """A repo's README (decoded markdown)."""
        import base64
        data = self._api(f'/repos/{self.user}/{self._safe(name)}/readme')
        content = base64.b64decode(data.get('content', '')).decode('utf-8', 'replace')
        return {'repo': name, 'path': data.get('path'), 'markdown': content}

    def tree(self, name, path='', ref=None) -> dict:
        """List one directory of a repo (path='' is the root)."""
        p = str(path or '').strip('/')
        params = {'ref': ref} if ref else None
        data = self._api(f'/repos/{self.user}/{self._safe(name)}/contents/'
                         + urllib.parse.quote(p), params)
        if isinstance(data, dict):             # a file path was passed, not a dir
            return {'repo': name, 'path': p, 'entries': [self._fmt_entry(data)]}
        entries = [self._fmt_entry(e) for e in data]
        entries.sort(key=lambda e: (e['type'] != 'dir', e['name'].lower()))
        return {'repo': name, 'path': p, 'count': len(entries), 'entries': entries}

    @staticmethod
    def _fmt_entry(e: dict) -> dict:
        return {'name': e.get('name'), 'path': e.get('path'), 'type': e.get('type'),
                'size': e.get('size', 0), 'url': e.get('html_url')}

    MAX_FILE_BYTES = 400_000

    def file(self, name, path, ref=None) -> dict:
        """Read one file out of a repo, as text. Binary and oversized files are
        described rather than returned — this is a reading surface, not a CDN."""
        import base64
        p = str(path or '').strip('/')
        if not p:
            raise ValueError('path is required')
        params = {'ref': ref} if ref else None
        data = self._api(f'/repos/{self.user}/{self._safe(name)}/contents/'
                         + urllib.parse.quote(p), params)
        if isinstance(data, list):
            raise ValueError(f'{p} is a directory — use tree')
        size = data.get('size', 0)
        out = {'repo': name, 'path': data.get('path'), 'size': size,
               'url': data.get('html_url'), 'sha': data.get('sha')}
        if size > self.MAX_FILE_BYTES:
            out['text'] = None
            out['note'] = f'{size} bytes exceeds the {self.MAX_FILE_BYTES}-byte read cap'
            return out
        blob = base64.b64decode(data.get('content', '') or '')
        try:
            out['text'] = blob.decode('utf-8')
        except UnicodeDecodeError:
            out['text'] = None
            out['note'] = 'binary file — not decoded'
        return out

    def search(self, q, n=50) -> dict:
        """Search code across the user's repos (GitHub code search)."""
        data = self._api('/search/code', {'q': f'{q} user:{self.user}',
                                          'per_page': min(int(n), 100)})
        hits = [{'repo': (i.get('repository') or {}).get('name'),
                 'path': i.get('path'), 'url': i.get('html_url')}
                for i in data.get('items', [])]
        return {'query': q, 'total': data.get('total_count', 0), 'hits': hits}

    @staticmethod
    def _safe(name) -> str:
        """Repo names are path segments; refuse anything that could climb out."""
        n = str(name or '').strip()
        if not n or not re.fullmatch(r'[A-Za-z0-9._-]+', n) or n in ('.', '..'):
            raise ValueError(f'bad repo name: {name!r}')
        return n

    # ── the plinyworld exhibit ──────────────────────────────────────────────

    def update(self) -> dict:
        """Re-pull the repo list from GitHub AND refresh the plinyworld upstream
        snapshot (index.html + triggers.js). This is what keeps the fork current."""
        repos = self._fetch_repos()
        st = self._load()
        st['repos'] = repos
        st['updated'] = time.time()
        self._save(st)
        return {'repos': len(repos), 'updated': st['updated'],
                'plinyworld': self.refresh_plinyworld(), 'user': self.user}

    def refresh_plinyworld(self) -> dict:
        """Fetch the upstream elder-plinius.github.io files into plinyworld/upstream/.
        Non-fatal on failure — a failed pull keeps the pinned snapshot."""
        base = f'https://raw.githubusercontent.com/{self.user}/{self.user}.github.io/main'
        got = {}
        os.makedirs(UPSTREAM_DIR, exist_ok=True)
        for fn in ('index.html', 'triggers.js'):
            try:
                text = self._get(f'{base}/{fn}', raw=True).decode('utf-8', 'replace')
                with open(os.path.join(UPSTREAM_DIR, fn), 'w', encoding='utf-8') as f:
                    f.write(text)
                got[fn] = len(text)
            except Exception as e:              # noqa: BLE001 — snapshot must survive
                got[fn] = f'kept snapshot ({e})'
        try:
            commit = self._api(f'/repos/{self.user}/{self.user}.github.io/commits',
                               {'per_page': 1})
            if commit:
                with open(os.path.join(UPSTREAM_DIR, 'COMMIT'), 'w') as f:
                    f.write(commit[0]['sha'])
                got['commit'] = commit[0]['sha']
        except Exception:                       # noqa: BLE001
            pass
        return got

    def _read_upstream(self, fn) -> str:
        with open(os.path.join(UPSTREAM_DIR, fn), encoding='utf-8') as f:
            return f.read()

    def plinyworld_html(self) -> str:
        """Assemble the served plinyworld page: the upstream markup with the
        clipboard-hijack script swapped for the defanged one, plus a banner."""
        html = self._read_upstream('index.html')
        # Never load the live payload; point the page at the defanged trigger.
        html = re.sub(r'<script\s+src=["\']\.?/?triggers\.js["\']\s*>\s*</script>',
                      '<script src="./triggers.defanged.js"></script>', html)
        html = html.replace('</body>', DEFANG_BANNER + '\n</body>', 1)
        return html

    def payload_source(self) -> str:
        """The preserved upstream trigger script, verbatim. Served as text, never
        as executable JavaScript."""
        return self._read_upstream('triggers.js')

    def exhibit(self) -> dict:
        """Read the preserved payload and report what the live attack does: the
        string it writes to the clipboard, the typosquatted domains it appends,
        and how this fork neutralizes it. This is the study surface — it answers
        'what does that page do' without anyone having to run the page."""
        src = self.payload_source()
        base = re.search(r'baseText\s*=\s*"(.*?)"', src, re.S)
        # The covertLinks array only — not the harmless example.com fallback below it.
        arr = re.search(r'covertLinks\s*=\s*\[(.*?)\]', src, re.S)
        links = re.findall(r"['\"](https?://[^'\"]+)['\"]", arr.group(1) if arr else src)
        domains = sorted({urllib.parse.urlparse(u).netloc for u in links})
        try:
            commit = self._read_upstream('COMMIT').strip()
        except OSError:
            commit = None
        return {
            'what': 'clipboard hijack (pastejacking) proof-of-concept, disguised as a '
                    '"Poetic Echoes" poetry page',
            'mechanism': [
                'every nav item and poem link carries a hidden click handler '
                '(.hidden-trigger, indexed by data-index)',
                'the handler calls navigator.clipboard.writeText() on click — no '
                'copy gesture from the visitor, no visible change',
                'it writes a payload string plus one typosquatted phishing URL, '
                'falling back to a hidden <textarea> + document.execCommand("copy") '
                'where the async Clipboard API is unavailable',
                'the visitor pastes elsewhere and propagates a link they never saw',
            ],
            'clipboard_payload': base.group(1) if base else None,
            'phishing_links': links,
            'typosquatted_domains': domains,
            'upstream': f'https://github.com/{self.user}/{self.user}.github.io',
            'upstream_commit': commit,
            'defanged': {
                'served_script': 'plinyworld/triggers.defanged.js',
                'behaviour': 'clicking a trigger copies NOTHING and navigates nowhere; '
                             'it reveals inline what the live version would have copied',
                'preserved_unrun': 'plinyworld/upstream/triggers.js',
                'note': 'the served page is an exhibit, not a weapon — see plinyworld/SOURCE.md',
            },
        }

    # ── meta ────────────────────────────────────────────────────────────────

    def info(self) -> dict:
        st = self._load()
        tok, where = self._token()
        return {
            'name': 'plinyville',
            'version': VERSION,
            'description': DESCRIPTION,
            'user': self.user,
            'source': f'https://github.com/{self.user}',
            'repos_cached': len(st.get('repos', [])),
            'updated': st.get('updated'),
            'plinyworld': '/pliny/plinyworld',
            'plinyworld_note': 'DEFANGED clipboard-hijack red-team exhibit — no live '
                               'payload. See plinyworld/SOURCE.md',
            'authenticated': bool(tok),
            'auth_source': where,
            'rate_limit': (self.rate or None) if tok else
                          dict(self.rate or {}, hint='anonymous: 60 requests/hour per IP — '
                               'run `m pliny/token <github_pat>` for 5,000/hr'),
            'state': self.state_path,
            'token_file': self.token_path,
        }


# ── the defang banner injected into the served plinyworld page ──────────────

DEFANG_BANNER = """
<div style="position:fixed;top:0;left:0;right:0;z-index:99999;
  background:#161213;color:#ffd9a8;border-bottom:2px solid #d9534f;
  font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:8px 14px;text-align:center">
  ⚠ <b>plinyville exhibit</b> — this is a fork of elder-plinius.github.io, a red-team
  <b>clipboard-hijack (pastejacking) PoC</b>. It has been <b>DEFANGED</b>: clicking a link
  copies <b>nothing</b> and reveals what the live attack would have done.
  <a href="../" style="color:#7aa2ff">← back to plinyville</a>
</div>
<div style="height:38px"></div>
"""
