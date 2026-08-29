#!/usr/bin/env python3
"""plinyville run — the repos that are apps, running under the mod.

The market already turns every elder-plinius repo into a mod you can *read*:
a tree, a README, a grep, an MCP server. But a good third of that corpus is not
prose — it is a page. GL4SS, ST3GG, ImageDefender, GLOSSOPETRAE, ENTHEA, R00TS,
LEAKHUB, the fourteen little apps inside FABLE-SHOWCASE: each one is an
index.html that runs entirely in the browser and does something. Reading their
source is not the same as watching them work.

So this module runs them. `Runner` reads a repo's **clone** (the same cache the
market archiver fills — see clone.py; the store bundle is capped at 60 text
files and has no images, so it can describe an app but not serve one), finds the
HTML entry points, and hands the bytes back for `/m/<repo>/run/<path>`.

    GET /api/plinyville/m/GL4SS/run              what it is, and what it touches
    GET /api/plinyville/m/GL4SS/run/index.html   the app itself
    GET /pliny/m/GL4SS#run                       the app, framed, in the console

**Running someone else's code is the whole risk of this file**, so:

* Every run response carries `Content-Security-Policy: sandbox allow-scripts …`
  *without* `allow-same-origin`. The document therefore gets an opaque origin
  even when opened top-level, and cannot read this box's cookies or the one
  localStorage that every mod on the host shares. The `<iframe>` in the console
  repeats the same sandbox attribute; the header is what protects the direct
  link, which an attribute cannot.
* An opaque origin makes `localStorage` *throw*, which breaks apps that never
  expected to be sandboxed, so served HTML gets a tiny shim that swaps in an
  in-memory Storage when the real one is unreachable. That, a provenance chip
  and nothing else is injected — the page is otherwise byte-for-byte upstream.
* `elder-plinius.github.io` is DEFANGED and refuses to run from here. It is the
  clipboard-hijack PoC; the sandbox does not reliably stop a copy on a user
  gesture, and serving the live payload would be publishing a working phishing
  page. Its run URL points at the neutralized exhibit at /plinyworld instead.
* Before you press RUN, `audit()` says what the entry's own scripts reach for:
  the clipboard, the network (which hosts), storage, the camera, `eval`, a key
  someone committed. It is a grep, not a proof — it is there so that pressing
  RUN is a decision rather than a reflex.
* And `health()` says whether it will work once it arrives: a merge conflict
  repaired on the way out, a script `node --check` refuses to compile, a back
  end the page expects on localhost. A dead page that serves 200 is the most
  expensive kind of "it runs".

Nothing is executed on this box: these are browser pages, served as bytes. A
repo of Python is reported as `kind: python`, not run.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from clone import Cloner
from market import BASE_PATH, Market
from plinyville import Ville

# The run index is a cache of a cache (the clones are already a cache), so it
# lives beside them and never in the store: nothing here is worth pinning.
RUN_INDEX = os.path.expanduser(
    os.environ.get('PLINYVILLE_RUN_INDEX', '~/.mod/pliny/run.json'))
ASSET_CAP = int(os.environ.get('PLINYVILLE_RUN_ASSET_CAP', 12_000_000))
WALK_CAP = int(os.environ.get('PLINYVILLE_RUN_WALK_CAP', 8000))
ENTRY_CAP = 60

# No allow-same-origin, deliberately: that one token is the difference between
# "a page in a box" and "a page with this host's storage in its hands".
SANDBOX = 'allow-scripts allow-forms allow-modals allow-pointer-lock allow-downloads'
CSP = 'sandbox ' + SANDBOX

# Repos that must never be served executable from here, and where to send the
# visitor instead. See plinyworld/SOURCE.md.
DEFANGED = {
    'elder-plinius.github.io': {
        'why': 'a live clipboard-hijack (pastejacking) PoC: its innocent links '
               'silently write typosquatted phishing URLs to your clipboard',
        'instead': BASE_PATH + '/plinyworld/',
        'instead_note': 'the same page, defanged — it copies nothing and says '
                        'inline what the live attack would have done',
    },
}

TYPES = {
    '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.map': 'application/json; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/plain; charset=utf-8', '.csv': 'text/plain; charset=utf-8',
    '.xml': 'text/xml; charset=utf-8', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
    '.ico': 'image/x-icon', '.bmp': 'image/bmp',
    '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
    '.otf': 'font/otf', '.eot': 'application/vnd.ms-fontobject',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.m4a': 'audio/mp4', '.mp4': 'video/mp4', '.webm': 'video/webm',
    '.wasm': 'application/wasm', '.pdf': 'application/pdf',
    '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json',
}
HTML_EXT = ('.html', '.htm')
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.github', '.venv', 'venv',
             # generated report trees are full of index.html files that are not
             # apps — V3SP3R's gradle test report is not something to "run".
             'reports', 'coverage', 'htmlcov', 'test-results', '.tox', '.next'}
# A source entry the browser cannot compile: the whole Vite/CRA family ships an
# index.html whose only script is <script type="module" src="/src/main.tsx">.
UNBUILT_EXT = ('.tsx', '.jsx', '.ts', '.vue', '.svelte')
STATUS_ORDER = {'ready': 0, 'needs_service': 1, 'incomplete': 2,
                'needs_build': 3, 'template': 4, 'fragment': 5}
# What to say about a page that will not run, and what to call the repo then.
NOT_READY = {
    'needs_build': ('source', 'a page that has to be built first'),
    'needs_service': ('demo', 'a front end for something running elsewhere'),
    'incomplete': ('incomplete', 'its page is missing pieces'),
    'template': ('source', 'a build template, not a built page'),
    'fragment': ('fragment', 'HTML fragments, no whole page'),
}
# Pages that exist for a runtime, not a reader: an extension's background page
# is 175 bytes with an empty body, and it should never win over its popup.
BOILERPLATE = {'background.html', 'offscreen.html', 'sandbox.html',
               'devtools.html', 'options.html', 'test.html', '404.html'}
REF_RE = re.compile(
    r'<script[^>]+src=["\']([^"\']+)|'
    r'<link[^>]+rel=["\']?stylesheet[^>]*href=["\']([^"\']+)', re.I)

# What a page reaches for. A grep over the entry and the scripts beside it —
# enough to make RUN a decision, not a proof of anything.
SIGNALS = (
    ('clipboard', r'clipboard\.writeText|execCommand\s*\(\s*[\'"]copy|'
                  r'ClipboardEvent|addEventListener\s*\(\s*[\'"]copy'),
    ('storage', r'localStorage|sessionStorage|indexedDB|document\.cookie'),
    ('network', r'\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|EventSource'),
    ('camera/mic', r'getUserMedia|getDisplayMedia'),
    ('location', r'navigator\.geolocation'),
    ('eval', r'\beval\s*\(|new\s+Function\s*\('),
    ('download', r'download\s*=|createObjectURL'),
    ('api-key-in-source', r'sk-[A-Za-z0-9]{20}|AIza[A-Za-z0-9_\-]{20}'),
)
HOST_RE = re.compile(r'https?://([A-Za-z0-9.\-]+\.[A-Za-z]{2,})')
LOCAL_RE = re.compile(r'(?:localhost|127\.0\.0\.1):(\d+)')
LOCAL_SRC_RE = re.compile(
    r'(?:src|href|data-src)=["\']https?://(?:localhost|127\.0\.0\.1):(\d+)', re.I)
BORING_HOSTS = {'github.com', 'www.github.com', 'raw.githubusercontent.com',
                'www.w3.org', 'schema.org', 'developer.mozilla.org'}
# An unresolved merge conflict is not a style problem: `<<<<<<< HEAD` is a
# syntax error, so ONE of them kills the whole script and the page renders
# perfectly while every button does nothing (R00TS shipped two of them).
# The scripts are checked with the platform's own parser when there is one:
# `node --check` is the difference between "the bytes arrived" and "the page
# works". R00TS declares `words` twice in one function — a SyntaxError that has
# nothing to do with merge markers, and one no amount of serving care fixes.
NODE = shutil.which('node')
SYNTAX_CAP = 8            # scripts parsed per repo — the entry's own, not a bundle
CONFLICT_RE = re.compile(
    r'^<{7}[^\n]*\n(.*?)^={7}[^\n]*\n.*?^>{7}[^\n]*\n?', re.M | re.S)
CONFLICT_MARK = re.compile(r'^<{7} |^>{7} ', re.M)
# A marker line on its own — the leftover of a conflict whose other half never
# made it into the commit. It holds no code, so dropping the line is the whole
# repair (see Runner._deconflict).
STRAY_MARK = re.compile(r'^(?:<{7}|>{7}|={7})(?:\s.*)?$')
REPAIRABLE_EXT = ('.js', '.mjs', '.css', '.html', '.htm')
# A page whose scripts fetch a service on this box is not "a front end for
# something running elsewhere" — it draws, and some of it works — but it is not
# whole either. Say so on the button instead of letting the panels 404 silently.
FETCH_LOCAL_RE = re.compile(
    r'(?:fetch|open|EventSource|WebSocket|axios\.\w+|url)\s*\(?\s*'
    r'[\'"`]\s*(?:https?:)?//(?:localhost|127\.0\.0\.1):(\d+)', re.I)
# The weaker read of the same fact. T3MP3ST never writes `fetch('http://…3333')`
# — it keeps the address in a settings field and fetches a variable, so the
# strict pattern saw nothing while the browser hammered localhost:3333 anyway.
# A host repeated through a page is the page's back end whatever the call site
# looks like; one lone mention in a comment is not, hence the count.
LOCAL_HOST_RE = re.compile(r'https?://(?:localhost|127\.0\.0\.1):(\d+)', re.I)
LOCAL_HOST_MIN = 2


class Runner:
    """Every repo that is an app, served under the mod — sandboxed."""

    def __init__(self, market: Market = None, cloner: Cloner = None,
                 ville: Ville = None):
        self.market = market or Market(ville)
        self.ville = self.market.ville
        self.cloner = cloner or Cloner(self.market)

    # ── discovery: which repos are pages, and where do they start ────────────

    def path(self, name) -> str:
        return self.cloner.path(self.ville._safe(name))

    def cloned(self, name) -> bool:
        return os.path.isdir(os.path.join(self.path(name), '.git'))

    def entries(self, name, clone=False) -> dict:
        """The HTML entry points of one repo, best first.

        `clone=True` fetches the repo if it is not on disk yet; the default
        answers from what is already there, because the gallery asks this
        question 47 times at once."""
        name = self.ville._safe(name)
        root = self.path(name)
        if not self.cloned(name):
            if not clone:
                return {'repo': name, 'cloned': False, 'entries': [],
                        'note': 'not cloned yet — POST install, or ask for run?clone=1'}
            self.cloner.clone(name)
            root = self.path(name)
        found, files, seen_ext = [], 0, set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                files += 1
                if files > WALK_CAP:
                    break
                ext = os.path.splitext(fn)[1].lower()
                seen_ext.add(ext)
                if ext in HTML_EXT:
                    rel = os.path.relpath(os.path.join(dirpath, fn), root)
                    found.append(rel.replace(os.sep, '/'))
            if files > WALK_CAP:
                break
        found.sort(key=self._rank)
        out = [self._entry(root, r) for r in found[:ENTRY_CAP + 20]]
        # A page that works comes before a page that only exists. Everything
        # else — depth, index.html, docs/ — is the tiebreak it always was.
        out.sort(key=lambda e: (STATUS_ORDER.get(e['status'], 9),
                                1 if os.path.basename(e['path']).lower() in BOILERPLATE
                                else 0,
                                1 if e['size'] < 600 else 0,
                                self._rank(e['path'])))
        return {'repo': name, 'cloned': True, 'entries': out[:ENTRY_CAP],
                'count': len(found), 'files': files, 'kinds': sorted(seen_ext)}

    @staticmethod
    def _rank(rel: str):
        """index.html at the root beats docs/index.html beats a stray page."""
        parts = rel.split('/')
        base = parts[-1].lower()
        return (1 if 'template' in base else 0,
                0 if base in ('index.html', 'index.htm') else 1,
                len(parts), 0 if parts[0] in ('docs', 'public', 'site', 'www', 'dist') else 1,
                rel.lower())

    def _entry(self, root: str, rel: str) -> dict:
        full = os.path.join(root, rel.replace('/', os.sep))
        try:
            size = os.path.getsize(full)
            with open(full, 'rb') as f:
                head = f.read(400_000).decode('utf-8', 'replace')
        except OSError:
            size, head = 0, ''
        m = re.search(r'<title[^>]*>(.*?)</title>', head, re.S | re.I)
        title = re.sub(r'\s+', ' ', m.group(1)).strip()[:120] if m else ''
        # The console's two faces carry a latin-1 subset and the host has no
        # emoji font, so an upstream title of "\U0001f95a STE.GG" would arrive
        # in the entry picker as two tofu boxes. Drop what cannot be drawn.
        title = ''.join(c for c in title if ord(c) < 0x100).strip(' -|')
        e = {'path': rel, 'title': title, 'size': size, 'dir': os.path.dirname(rel)}
        e.update(self._classify(root, rel, head))
        return e

    @staticmethod
    def _classify(root, rel, html) -> dict:
        """Does this page actually work as bytes?

        Half of these repos are Vite or Next apps: their index.html is a stub
        whose one script is `/src/main.tsx`, which no browser can compile. That
        page *loads* and shows nothing. Calling it runnable would be the lie
        this whole feature is built to avoid, so say `needs_build` and why."""
        d = os.path.dirname(rel)
        base = os.path.basename(rel).lower()
        if '.template.' in base:
            return {'status': 'template', 'needs': [],
                    'why': 'a template the repo\'s build step fills in, not the '
                           'page it produces'}
        if '<html' not in html.lower() and '<body' not in html.lower():
            return {'status': 'fragment', 'needs': [],
                    'why': 'an HTML fragment — another page includes it'}
        # Only a page that *loads* itself from a local service is a demo of
        # something else. Half this corpus merely mentions localhost:11434 as
        # an Ollama default in a settings field — those pages run fine, and the
        # audit says so instead.
        svc = LOCAL_SRC_RE.findall(html)
        if svc:
            return {'status': 'needs_service', 'needs': sorted(set(svc))[:4],
                    'why': 'it loads its content from localhost:' + svc[0]
                           + ', a service that is not part of this repo'}
        unbuilt, missing = [], []
        for a, b in REF_RE.findall(html):
            ref = (a or b).split('?')[0].split('#')[0].strip()
            if not ref or ref.startswith(('http://', 'https://', '//', 'data:',
                                          'blob:', 'javascript:')):
                continue
            if ref.lower().endswith(UNBUILT_EXT):
                unbuilt.append(ref)
                continue
            target = ref[1:] if ref.startswith('/') else (f'{d}/{ref}' if d else ref)
            if not os.path.isfile(os.path.join(root, target.replace('/', os.sep))):
                missing.append(ref)
        if unbuilt:
            return {'status': 'needs_build', 'needs': unbuilt[:6],
                    'why': 'the browser cannot compile ' + unbuilt[0]
                           + ' — this is source, not a built page'}
        if missing:
            return {'status': 'incomplete', 'needs': missing[:6],
                    'why': 'referenced files are not in the repo: '
                           + ', '.join(missing[:3])}
        return {'status': 'ready', 'needs': []}

    def manifest(self, name, clone=False, audit=True) -> dict:
        """Everything the RUN button needs: can it run, from where, and what
        does the thing reach for once it does."""
        name = self.ville._safe(name)
        base = f'/api{BASE_PATH}/m/{name}/run'
        d = DEFANGED.get(name)
        if d:
            return {'repo': name, 'runnable': True, 'defanged': True,
                    'kind': 'exhibit', 'run_url': d['instead'],
                    'entry': None, 'entries': [], 'source': 'exhibit',
                    'why': d['why'], 'note': d['instead_note'],
                    'app': f'{BASE_PATH}/m/{name}'}
        e = self.entries(name, clone=clone)
        out = {'repo': name, 'cloned': e['cloned'], 'defanged': False,
               'entries': e['entries'], 'count': e.get('count', 0),
               'app': f'{BASE_PATH}/m/{name}', 'api': base,
               'sandbox': SANDBOX, 'source': 'clone'}
        if not e['cloned']:
            out.update(runnable=False, kind='unknown', note=e['note'])
            return out
        ready = [x for x in e['entries'] if x['status'] == 'ready']
        if not ready:
            out.update(runnable=False, **self._why_not(e.get('kinds') or [],
                                                       e['entries']))
            return out
        entry = ready[0]['path']
        out.update(runnable=True, kind='web', entry=entry,
                   run_url=f'{base}/{entry}',
                   note='runs in the browser, sandboxed — nothing executes on this box')
        out.update(self.health(name, entry))
        if audit:
            out['audit'] = self.audit(name, entry)
        return out

    # ── does it work once it runs ───────────────────────────────────────────

    def health(self, name, entry) -> dict:
        """A page can serve 200 and still be dead. Three things kill these repos
        after the bytes arrive, and all three are visible before you press RUN:

        * an unresolved merge conflict in a script (a SyntaxError: the page
          paints and nothing responds) — we repair those on the way out, so
          this reports what was repaired rather than a warning;
        * a script that does not compile even after the repair. When the host
          has node, every script the entry pulls in is checked with the real
          parser and reported with its own message — R00TS declares `words`
          twice in one function, which no amount of careful serving fixes;
        * a script that fetches a service on somebody's localhost, which is not
          running here (T3MP3ST wants localhost:3333) — that half of the app
          will error, and the button should say so before you press it.
        """
        root = self.path(name)
        repairs, services = [], set()
        checked, unparsable = [], []
        for rel in self._audit_files(root, entry, 24):
            if not rel.lower().endswith(REPAIRABLE_EXT):
                continue
            try:
                with open(os.path.join(root, rel.replace('/', os.sep)), 'rb') as f:
                    text = f.read(600_000).decode('utf-8', 'replace')
            except OSError:
                continue
            # Ask the repair itself rather than counting markers: a file can
            # hold a whole conflict AND a stray half of one (R00TS does), and
            # what the visitor needs to know is whether the served bytes parse.
            _, n = self._deconflict(text, os.path.splitext(rel)[1].lower())
            if n:
                repairs.append({'file': rel, 'conflicts': n,
                                'fix': 'unresolved merge conflict upstream — served '
                                       'with the HEAD side kept, announced in the file'})
            elif CONFLICT_MARK.search(text):
                repairs.append({'file': rel, 'conflicts': 0, 'repaired': False,
                                'fix': 'merge-conflict markers upstream in a shape '
                                       'we will not guess at — served as-is, so this '
                                       "file's scripts will not parse"})
            services.update('localhost:' + p for p in FETCH_LOCAL_RE.findall(text))
            for port in set(LOCAL_HOST_RE.findall(text)):
                if len(LOCAL_HOST_RE.findall(text)) >= LOCAL_HOST_MIN:
                    services.add('localhost:' + port)
            # …and does what we are about to serve actually compile?
            if (rel.lower().endswith(('.js', '.mjs')) and len(checked) < SYNTAX_CAP):
                served = self._deconflict(text, os.path.splitext(rel)[1].lower())[0]
                ok, why = self._parses(served)
                if ok is not None:
                    checked.append(rel)
                if ok is False:
                    unparsable.append({'file': rel, 'error': why})
        out = {}
        if checked:
            out['scripts_parsed'] = len(checked)
        if repairs:
            out['repairs'] = repairs
        broken = [r['file'] for r in repairs if r.get('repaired') is False]
        if broken:
            out['degraded'] = True
            out['degraded_why'] = (
                'it draws, but ' + ', '.join(broken[:2]) + ' is left mid-merge '
                'upstream in a shape this module will not resolve for you — that '
                'script is a syntax error, so whatever it drives does nothing')
        if services:
            svc = sorted(services)
            out['degraded'] = True
            out['degraded_why'] = (
                'it works, but part of it talks to ' + ', '.join(svc[:2])
                + ' — a service of its own that is not running here, so those '
                  'panels will show errors')
            out['wants'] = svc[:4]
        # Last, because it is the worst of the three: a page missing its back
        # end still does half of what it promises; a page whose script will not
        # compile does none of it, and that is the line the button should read.
        if unparsable:
            out['unparsable'] = unparsable
            out['degraded'] = True
            out['degraded_why'] = (
                'it draws, but %s does not compile in a browser — %s. That is '
                'upstream\'s own bug, not the sandbox: nothing that script drives '
                'will respond.' % (unparsable[0]['file'], unparsable[0]['error']))
        return out

    @staticmethod
    def _why_not(kinds, entries=()) -> dict:
        """No page. Say what it is instead — every one of these is worth
        reading, and 'not runnable' on its own tells you nothing."""
        k = set(kinds)
        for e in entries:                     # already sorted best-first
            if e['status'] in NOT_READY:
                kind, lead = NOT_READY[e['status']]
                return {'kind': kind, 'entry': e['path'],
                        'needs_build': e['status'] in ('needs_build', 'template'),
                        'note': f'{lead} — {e["why"]}. Read the source here'}
        if k & {'.py'}:
            return {'kind': 'python',
                    'note': 'python, not a page — read it here, run it from the source'}
        if k & {'.ipynb'}:
            return {'kind': 'notebook', 'note': 'a notebook — read it here'}
        if k & {'.md', '.txt', '.mkd', '.json'}:
            return {'kind': 'text',
                    'note': 'a corpus, not a program — prompts and notes, read them here'}
        if not k:
            return {'kind': 'empty', 'note': 'the repo is empty'}
        return {'kind': 'other', 'note': 'no HTML entry point — nothing to run in a browser'}

    # ── the index: the whole corpus, cheaply ────────────────────────────────

    def index(self, refresh=False) -> dict:
        """{repo: {runnable, kind, entry, entries}} for every cloned repo,
        cached on disk and rebuilt per repo when its clone moves. The gallery
        reads this; walking 47 checkouts on every page load would not do."""
        idx = {}
        if not refresh:
            try:
                with open(RUN_INDEX, encoding='utf-8') as f:
                    idx = json.load(f) or {}
            except (OSError, json.JSONDecodeError):
                idx = {}
        mods = idx.get('mods') or {}
        out, changed = {}, False
        for name in sorted(self._clone_names()):
            stamp = self._stamp(name)
            was = mods.get(name)
            if was and not refresh and was.get('stamp') == stamp:
                out[name] = was
                continue
            m = self.manifest(name, clone=False, audit=False)
            out[name] = {'stamp': stamp, 'runnable': bool(m.get('runnable')),
                         'kind': m.get('kind'), 'entry': m.get('entry'),
                         'entries': len(m.get('entries') or []),
                         'defanged': bool(m.get('defanged')),
                         'run_url': m.get('run_url'), 'note': m.get('note'),
                         'degraded': bool(m.get('degraded')),
                         'degraded_why': m.get('degraded_why'),
                         'wants': m.get('wants') or [],
                         'repairs': m.get('repairs') or [],
                         'scripts_parsed': m.get('scripts_parsed'),
                         'unparsable': m.get('unparsable') or []}
            changed = True
        for name, d in DEFANGED.items():
            if name not in out:
                out[name] = {'stamp': 'exhibit', 'runnable': True, 'kind': 'exhibit',
                             'entry': None, 'entries': 0, 'defanged': True,
                             'run_url': d['instead'], 'note': d['instead_note']}
                changed = True
        if changed or refresh:
            self._save({'updated': time.time(), 'mods': out})
        return out

    def _clone_names(self):
        root = self.cloner.root
        try:
            return [n for n in os.listdir(root)
                    if os.path.isdir(os.path.join(root, n, '.git'))]
        except OSError:
            return []

    def _stamp(self, name) -> str:
        """Cheap 'has this checkout moved' key — a git call per repo would cost
        47 subprocesses on a cold gallery load."""
        g = os.path.join(self.path(name), '.git')
        try:
            return str(int(os.path.getmtime(g)))
        except OSError:
            return '0'

    @staticmethod
    def _save(obj):
        try:
            os.makedirs(os.path.dirname(RUN_INDEX), exist_ok=True)
            tmp = RUN_INDEX + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(obj, f)
            os.replace(tmp, RUN_INDEX)
        except OSError:
            pass

    def catalog(self, refresh=False) -> dict:
        """The arcade: every repo that runs."""
        idx = self.index(refresh=refresh)
        runs = [dict(repo=k, **v) for k, v in idx.items() if v.get('runnable')]
        runs.sort(key=lambda r: r['repo'].lower())
        for r in runs:
            r.setdefault('run_url', f'/api{BASE_PATH}/m/{r["repo"]}/run/{r["entry"]}')
            r['app'] = f'{BASE_PATH}/m/{r["repo"]}#run'
        return {'runnable': len(runs), 'cloned': len(idx), 'sandbox': SANDBOX,
                'mods': runs,
                'note': 'browser apps, served from their clone and sandboxed — '
                        'nothing runs on this box'}

    def join(self, catalog: dict) -> dict:
        """Annotate a market catalog in place with what each mod can run."""
        try:
            idx = self.index()
        except Exception:                                    # noqa: BLE001
            return catalog
        n = 0
        for m in catalog.get('mods') or []:
            e = idx.get(m.get('name')) or {}
            m['run'] = bool(e.get('runnable'))
            m['run_kind'] = e.get('kind')
            if e.get('runnable'):
                n += 1
                m['run_url'] = (e.get('run_url')
                                or f'/api{BASE_PATH}/m/{m["name"]}/run/{e.get("entry")}')
                m['run_defanged'] = bool(e.get('defanged'))
                if e.get('degraded'):
                    m['run_degraded'] = True
                    m['run_degraded_why'] = e.get('degraded_why')
                if e.get('repairs'):
                    m['run_repairs'] = e['repairs']
                if e.get('unparsable'):
                    m['run_unparsable'] = e['unparsable']
        catalog['runnable'] = n
        return catalog

    # ── the audit: what does this page reach for ────────────────────────────

    def audit(self, name, entry=None, files_cap=24) -> dict:
        """Grep the entry and the scripts around it for the things that would
        make you think twice. Cheap, honest about being shallow."""
        name = self.ville._safe(name)
        root = self.path(name)
        if not self.cloned(name):
            return {'repo': name, 'note': 'not cloned'}
        if entry is None:
            e = self.entries(name)
            entry = (e['entries'][0]['path'] if e['entries'] else None)
        if not entry:
            return {'repo': name, 'note': 'nothing to audit'}
        blobs, scanned = [], []
        for rel in self._audit_files(root, entry, files_cap):
            try:
                with open(os.path.join(root, rel.replace('/', os.sep)), 'rb') as f:
                    blobs.append(f.read(400_000).decode('utf-8', 'replace'))
                scanned.append(rel)
            except OSError:
                pass
        text = '\n'.join(blobs)
        touches = [k for k, pat in SIGNALS if re.search(pat, text, re.I)]
        hosts = sorted({h.lower() for h in HOST_RE.findall(text)} - BORING_HOSTS)
        # Not a warning: several of these are good pages whose optional back end
        # is a model server you may well be running. Say which port it wants.
        services = sorted({'localhost:' + p for p in LOCAL_RE.findall(text)})
        return {'repo': name, 'entry': entry, 'touches': touches,
                'hosts': hosts[:12], 'services': services[:6], 'files_scanned': scanned[:files_cap],
                'note': 'a grep over the entry and the scripts beside it — '
                        'a prompt to look, not a verdict'}

    @staticmethod
    def _audit_files(root, entry, cap):
        """The entry plus the js/css in its directory — where a static page
        keeps its behaviour."""
        out = [entry]
        d = os.path.dirname(entry)
        full = os.path.join(root, d.replace('/', os.sep)) if d else root
        try:
            names = sorted(os.listdir(full))
        except OSError:
            names = []
        for fn in names:
            if len(out) >= cap:
                break
            rel = (d + '/' + fn) if d else fn
            if rel != entry and os.path.splitext(fn)[1].lower() in ('.js', '.mjs', '.css'):
                if os.path.isfile(os.path.join(root, rel.replace('/', os.sep))):
                    out.append(rel)
        for sub in ('js', 'scripts', 'assets/js', 'src'):
            base = os.path.join(full, sub.replace('/', os.sep))
            if not os.path.isdir(base):
                continue
            for fn in sorted(os.listdir(base))[:8]:
                if len(out) >= cap:
                    break
                if fn.lower().endswith(('.js', '.mjs')):
                    out.append(((d + '/') if d else '') + sub + '/' + fn)
        return out

    # ── serving: bytes out of the clone, in a box ───────────────────────────

    def asset(self, name, path) -> dict:
        """One file of a running repo: {body, ctype, headers} or a redirect.

        The path is resolved inside the checkout and nowhere else — the guard
        is a realpath prefix check, not a '..' filter, because symlinks."""
        name = self.ville._safe(name)
        if name in DEFANGED:
            d = DEFANGED[name]
            raise Defanged(f'{name} is not served runnable from here: {d["why"]}. '
                           f'Run the defanged exhibit at {d["instead"]} instead.')
        root = os.path.realpath(self.path(name))
        if not os.path.isdir(os.path.join(root, '.git')):
            self.cloner.clone(name)
            root = os.path.realpath(self.path(name))
        rel = (path or '').strip('/')
        if not rel:
            m = self.manifest(name, audit=False)
            if not m.get('runnable'):
                raise ValueError(m.get('note') or f'{name} has no page to run')
            return {'redirect': m['entry']}
        if rel.split('/')[0] in SKIP_DIRS:
            raise ValueError(f'{rel} is not served')
        full = os.path.realpath(os.path.join(root, rel.replace('/', os.sep)))
        if full != root and not full.startswith(root + os.sep):
            raise ValueError('path escapes the repo')
        if os.path.isdir(full):
            for cand in ('index.html', 'index.htm'):
                if os.path.isfile(os.path.join(full, cand)):
                    return {'redirect': rel.rstrip('/') + '/' + cand}
            raise FileNotFoundError(f'{rel} is a directory with no index.html')
        if not os.path.isfile(full):
            raise FileNotFoundError(f'{rel} is not in {name}')
        size = os.path.getsize(full)
        if size > ASSET_CAP:
            raise ValueError(f'{rel} is {size} bytes — over the {ASSET_CAP} run cap')
        with open(full, 'rb') as f:
            body = f.read()
        ext = os.path.splitext(full)[1].lower()
        ctype = TYPES.get(ext, 'application/octet-stream')
        repaired = 0
        if ext in REPAIRABLE_EXT:
            text = body.decode('utf-8', 'replace')
            text, repaired = self._deconflict(text, ext)
            body = text.encode()
        if ext in HTML_EXT:
            body = self._shim(body.decode('utf-8', 'replace'), name, rel).encode()
        out = {'body': body, 'ctype': ctype, 'headers': self.headers(), 'path': rel}
        if repaired:
            out['repaired'] = repaired
        return out

    @staticmethod
    def _parses(text):
        """Does this script actually parse? None when we cannot know.

        Serving 200 OK for a file the browser will refuse to compile is the
        most expensive kind of "it runs": the page paints, every button is
        dead, and nothing in the response says so. `node --check` is the same
        parser, so when the host has node we can say it on the card instead of
        letting the visitor find out."""
        if not NODE or len(text) > 800_000:
            return None, None
        d = tempfile.mkdtemp(prefix='pliny-syntax-')
        try:
            for ext in ('.js', '.mjs'):        # classic first, then as a module
                fp = os.path.join(d, 'check' + ext)
                with open(fp, 'w', encoding='utf-8') as f:
                    f.write(text)
                try:
                    r = subprocess.run([NODE, '--check', fp], capture_output=True,
                                       text=True, timeout=20)
                except (OSError, subprocess.SubprocessError):
                    return None, None
                if r.returncode == 0:
                    return True, None
                err = next((ln.strip() for ln in (r.stderr or '').splitlines()
                            if 'Error' in ln), 'it does not parse')
            return False, err
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @staticmethod
    def _deconflict(text: str, ext: str):
        """Serve a script somebody left mid-merge.

        `<<<<<<< HEAD` is a SyntaxError, so one unresolved conflict takes the
        whole file with it and the app is a picture of itself. Keeping the HEAD
        side is what `git checkout --ours` does; it is an edit to upstream, so
        like the storage shim it is announced in the bytes we hand over.

        A marker does not have to come as a matched trio. R00TS ships one whole
        conflict *and* a lone `<<<<<<< HEAD` with nothing closing it — the
        paired pass leaves that line behind, and one leftover line is still the
        SyntaxError that kills `loadWords` and every button on the page. A bare
        marker line carries no code, so the tolerant resolution is to drop the
        line; it is counted separately and said out loud in the banner."""
        if not CONFLICT_MARK.search(text):
            return text, 0
        fixed, n = CONFLICT_RE.subn(lambda m: m.group(1), text)
        stray = 0
        if CONFLICT_MARK.search(fixed):
            kept = [ln for ln in fixed.split('\n') if not STRAY_MARK.match(ln)]
            stray = len(fixed.split('\n')) - len(kept)
            fixed = '\n'.join(kept)
        if not (n or stray) or CONFLICT_MARK.search(fixed):
            return text, 0            # not the shape we know — leave it alone
        note = (f'pliny: upstream left {n} unresolved merge conflict'
                f'{"s" if n != 1 else ""} in this file, which is a syntax error. '
                'Served with the HEAD side kept (git checkout --ours); nothing '
                'else is changed.'
                if n else 'pliny: upstream left merge-conflict markers in this '
                          'file, which is a syntax error.')
        if stray:
            note += (f' {stray} stray marker line{"s" if stray > 1 else ""} with '
                     'no conflict to close were dropped as well.')
        banner = (f'<!-- {note} -->\n' if ext in HTML_EXT else f'/* {note} */\n')
        return banner + fixed, n + stray

    @staticmethod
    def headers() -> dict:
        return {
            # The sandbox that matters: it rides the response, so it holds on a
            # direct link too, where an iframe attribute cannot reach.
            'Content-Security-Policy': CSP,
            'X-Content-Type-Options': 'nosniff',
            # An opaque origin makes the page's own fetch() of its own files
            # cross-origin. Without this, half these apps break on their data.
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
        }

    @staticmethod
    def _shim(html: str, name: str, rel: str) -> str:
        """The only edits to upstream: a Storage that survives the sandbox, and
        a chip saying whose page this is. Both are announced in the source."""
        head = ('<!-- served by the pliny mod: upstream bytes + a storage shim '
                'and a provenance chip, both marked below -->\n'
                '<script data-pliny="shim">' + STORAGE_SHIM + '</script>')
        low = html.lower()
        i = low.find('<head')
        if i != -1:
            j = html.find('>', i)
            if j != -1:
                html = html[:j + 1] + '\n' + head + html[j + 1:]
            else:
                html = head + html
        else:
            html = head + html
        chip = CHIP.replace('__NAME__', name).replace('__PATH__', rel)
        # `low` is stale the moment the head injection lands — re-lower, or the
        # chip splices itself into the middle of the page's own JavaScript.
        k = html.lower().rfind('</body>')
        return (html[:k] + chip + html[k:]) if k != -1 else html + chip


class Defanged(RuntimeError):
    """This repo is an exhibit, not something to serve executable."""


STORAGE_SHIM = (
    "(function(){function mem(){var d={};return{getItem:function(k){"
    "return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},"
    "setItem:function(k,v){d[String(k)]=String(v)},"
    "removeItem:function(k){delete d[k]},clear:function(){d={}},"
    "key:function(i){return Object.keys(d)[i]||null},"
    "get length(){return Object.keys(d).length}};}"
    "['localStorage','sessionStorage'].forEach(function(k){var ok=false;try{"
    "var s=window[k];s.setItem('__pliny','1');s.removeItem('__pliny');ok=true;}"
    "catch(e){}if(!ok){try{Object.defineProperty(window,k,{value:mem(),"
    "configurable:true,writable:true});}catch(e){}}});})();"
)

# Shown only when the page is opened on its own — inside the console's frame the
# surrounding chrome already says what this is.
CHIP = (
    '<div id="pliny-run-chip" style="position:fixed;right:0;bottom:0;z-index:2147483647;'
    'font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:#12081f;'
    'color:#cfc3e6;border:1px solid #b061ff;border-right:0;border-bottom:0;'
    'padding:4px 8px;opacity:.72;pointer-events:auto" '
    'title="__NAME__/__PATH__ served sandboxed by the pliny mod">'
    'RUN UNDER MOD &middot; elder-plinius/__NAME__</div>'
    '<script data-pliny="chip">try{if(window.top!==window.self){'
    'var c=document.getElementById("pliny-run-chip");if(c)c.remove();}}catch(e){}</script>'
)


if __name__ == '__main__':
    import sys
    r = Runner()
    if len(sys.argv) > 1:
        print(json.dumps(r.manifest(sys.argv[1], clone=True), indent=2, default=str))
    else:
        print(json.dumps(r.catalog(), indent=2, default=str))
