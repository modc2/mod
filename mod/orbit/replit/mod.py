"""
replit — the Replit interface for the mod protocol.

Replit publishes no usable API: its GraphQL endpoint only accepts persisted
query hashes, `/data/repls/...` is gone, and `.zip` export is 403. So this
module does not pretend to be a Replit client. It is a *bridge*, and it only
does the four things that actually cross the boundary:

  BUNDLE   any fleet module → a Replit-runnable project. Writes `.replit`,
           `replit.nix`, `requirements.txt` and a stdlib `main.py` that
           reproduces the mod URL rule on the Repl side (POST /{fn}, a null
           POST /mod/{name} → info, GET /health). Zip it, or take the
           one-click https://replit.com/github/{owner}/{repo} URL.
  LINK     a deployed Repl → a mod remote. Null-call discovery caches the mod
           it serves and every function with its parameters, so CATALOG is the
           index of the mods running on Replit and `call` reaches any of them.
  MCP      the whole bridge as a Model Context Protocol server (mcp.py), with
           one extra tool per function per linked Repl — `repl_{remote}_{fn}`.
           An agent does not "drive the replit module": it calls the mods that
           live on Replit directly. Served on this same port at POST /mcp, or
           standalone over stdio.
  DB       Replit's key-value store over your own REPLIT_DB_URL (the one
           documented, stable, token-scoped API Replit exposes).
  IMPORT   a GitHub repo behind a Repl → a scaffolded orbit module.

State lives off-chain under ~/.mod/replit (secrets 0600), never in config.json.

Write endpoints (connect, link, bundle, import, db writes) are local-only over
HTTP: the request must come from loopback *and* carry no X-Forwarded-For, so a
gateway-proxied visitor gets a read-only console. The CLI is unrestricted.

CLI:
    m replit                          # status: bundles, remotes, db
    m replit/bundle git               # package orbit/git for Replit
    m replit/zip git                  # …and zip it
    m replit/link demo https://x.replit.dev
    m replit/catalog                  # the mods running on Replit + their fns
    m replit/call demo info           # call a fn on the linked Repl
    m replit/mcp_tools                # the MCP tool surface
    m replit/db_set hello world
    m replit/serve                    # console + MCP on :50530
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# when run as a script (the serve subprocess), sys.path[0] is this dir and this
# very file would shadow the mod SDK — pin the package root ahead of it
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

import mod as m  # noqa: E402

PORT = 50530
STATE = '~/.mod/replit'
ORBITS = ('core', 'orbit', 'mods', 'local')
SKIP_DIRS = {'__pycache__', '.git', 'node_modules', '.next', 'target', 'venv',
             '.venv', 'dist', 'build', '.pytest_cache', '.mypy_cache', 'vendor'}
SKIP_EXT = {'.pyc', '.pyo', '.so', '.o', '.a', '.zip', '.tar', '.gz', '.lock'}
MAX_FILE = 512 * 1024          # per-file cap inside a bundle
MAX_BUNDLE = 8 * 1024 * 1024   # total cap — a Repl is not a file server

# third-party imports we can name in requirements.txt; everything else is
# either stdlib or the fleet's own SDK (which is not on PyPI — see warnings)
_STDLIB = set(getattr(sys, 'stdlib_module_names', ())) | {'mod'}


class Mod:
    description = ("Replit interface for the mod protocol: bundle a module "
                   "into a Repl, call a deployed Repl as a module, browse "
                   "Replit DB, import a Repl's repo as a module")

    def __init__(self, state_path: str = None):
        self.pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))          # …/mod (holds core/, orbit/)
        self.state_dir = m.abspath(state_path or STATE)
        self.bundle_dir = os.path.join(self.state_dir, 'bundles')
        os.makedirs(self.bundle_dir, exist_ok=True)

    # ── state ────────────────────────────────────────────────────────────────

    def _path(self, name):
        return os.path.join(self.state_dir, name)

    def _read(self, name, default):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _write(self, name, data, secret=False):
        p = self._path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            json.dump(data, f, indent=2)
        if secret:
            os.chmod(p, 0o600)
        return data

    # ── templates ────────────────────────────────────────────────────────────

    def _tmpl(self, name: str, **subs) -> str:
        """Read templates/{name}.tmpl and swap __TOKEN__ placeholders. The
        generated files live outside this file on purpose: the module loader
        scans for column-0 `class ` lines, and an embedded server template
        would look like a second anchor class."""
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'templates', f'{name}.tmpl')
        with open(p) as f:
            text = f.read()
        for k, v in subs.items():
            text = text.replace(f'__{k}__', str(v))
        return text

    # ── module lookup ────────────────────────────────────────────────────────

    def _module_dir(self, module: str) -> str:
        """'git' | 'orbit/git' | an absolute path → the module's directory."""
        if not module:
            raise ValueError('module required')
        cand = []
        if os.path.isabs(module):
            cand.append(module)
        elif '/' in module:
            cand.append(os.path.join(self.pkg_root, module))
        else:
            cand += [os.path.join(self.pkg_root, o, module) for o in ORBITS]
        for c in cand:
            if os.path.isdir(c) and os.path.exists(os.path.join(c, 'config.json')):
                return os.path.realpath(c)
        raise FileNotFoundError(f'no module {module!r} (looked in {", ".join(ORBITS)})')

    def _module_id(self, path: str) -> str:
        return os.path.relpath(path, self.pkg_root)

    def modules(self, search: str = None, n: int = 500) -> dict:
        """Every fleet module that could be bundled — id, name, description."""
        rows = []
        for orbit in ORBITS:
            root = os.path.join(self.pkg_root, orbit)
            if not os.path.isdir(root):
                continue
            for entry in sorted(os.scandir(root), key=lambda e: e.name):
                if not entry.is_dir() or entry.name in SKIP_DIRS:
                    continue
                cfg_path = os.path.join(entry.path, 'config.json')
                if not os.path.exists(cfg_path):
                    continue
                try:
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                except (OSError, ValueError):
                    cfg = {}
                rows.append({
                    'id': f'{orbit}/{entry.name}',
                    'name': cfg.get('name') or entry.name,
                    'description': (cfg.get('description') or '')[:240],
                    'icon': cfg.get('icon'),
                    'color': cfg.get('color'),
                    'anchor': cfg.get('anchor') or self._anchor_name(entry.path),
                })
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r['id'].lower() or s in r['description'].lower()]
        return {'n': len(rows), 'modules': rows[:n]}

    @staticmethod
    def _anchor_name(path: str):
        for name in ('mod.py', 'agent.py', os.path.basename(path) + '.py'):
            if os.path.exists(os.path.join(path, name)):
                return name
        return None

    # ── bundle: module → Repl project ────────────────────────────────────────

    def bundle(self, module: str, name: str = None, tests: bool = False,
               force: bool = True) -> dict:
        """Package a fleet module as a Replit project under
        ~/.mod/replit/bundles/{name}. Copies the module's own files, then adds
        `.replit`, `replit.nix`, `requirements.txt` and a `main.py` that serves
        the module the way the protocol does. Returns the manifest."""
        src = self._module_dir(module)
        mid = self._module_id(src)
        name = name or os.path.basename(src)
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
            raise ValueError('bundle name must be [A-Za-z0-9._-]+')
        dst = os.path.join(self.bundle_dir, name)
        if os.path.exists(dst):
            if not force:
                raise FileExistsError(f'bundle {name} exists — pass force=True')
            shutil.rmtree(dst)
        os.makedirs(dst)

        files, total, skipped = [], 0, []
        for root, dirs, fnames in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                       and not (d == 'tests' and not tests)]
            for fn in sorted(fnames):
                sp = os.path.join(root, fn)
                rel = os.path.relpath(sp, src)
                ext = os.path.splitext(fn)[1]
                try:
                    size = os.path.getsize(sp)
                except OSError:
                    continue
                if ext in SKIP_EXT or size > MAX_FILE or total + size > MAX_BUNDLE:
                    skipped.append({'file': rel, 'bytes': size,
                                    'why': 'binary/large' if ext in SKIP_EXT else 'over cap'})
                    continue
                dp = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copy2(sp, dp)
                files.append({'file': rel, 'bytes': size})
                total += size

        anchor = self._anchor_name(src) or 'mod.py'
        try:
            with open(os.path.join(src, 'config.json')) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        reqs, warnings = self._scan_imports(src, anchor)

        gen = {
            '.replit': self._tmpl('dot.replit'),
            'replit.nix': self._tmpl('replit.nix'),
            'main.py': self._tmpl('main.py', ANCHOR=anchor, NAME=name),
            'requirements.txt': ''.join(f'{r}\n' for r in reqs),
            'REPLIT.md': self._tmpl(
                'REPLIT.md', NAME=name, MODULE=mid,
                WARNINGS=('\n'.join(f'- {w}' for w in warnings) or '- none')),
        }
        for fn, body in gen.items():
            with open(os.path.join(dst, fn), 'w') as f:
                f.write(body)
            files.append({'file': fn, 'bytes': len(body.encode()), 'generated': True})
            total += len(body.encode())

        manifest = {
            'name': name, 'module': mid, 'anchor': anchor,
            'description': cfg.get('description', '')[:400],
            'files': len(files), 'bytes': total,
            'skipped': skipped, 'requirements': reqs, 'warnings': warnings,
            'created': int(time.time()), 'path': dst,
        }
        with open(os.path.join(dst, 'mod_bundle.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        return manifest

    def _scan_imports(self, src: str, anchor: str):
        """Top-level imports across the module's .py files → (requirements,
        warnings). The fleet SDK (`import mod`) is not on PyPI, so a module
        that uses it will not boot on Replit untouched — say so, loudly."""
        found, uses_sdk = set(), False
        for root, dirs, fnames in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in fnames:
                if not fn.endswith('.py'):
                    continue
                try:
                    text = open(os.path.join(root, fn), encoding='utf-8',
                                errors='ignore').read()
                except OSError:
                    continue
                for mo in re.finditer(r'^\s*(?:from|import)\s+([A-Za-z_][\w.]*)',
                                      text, re.M):
                    top = mo.group(1).split('.')[0]
                    if top == 'mod':
                        uses_sdk = True
                    elif top not in _STDLIB and not top.startswith('_'):
                        found.add(top)
        local = {os.path.splitext(f)[0] for f in os.listdir(src)} | \
                {d for d in os.listdir(src) if os.path.isdir(os.path.join(src, d))}
        reqs = sorted(r for r in found if r not in local)
        warnings = []
        if uses_sdk:
            warnings.append(
                'This module imports the fleet SDK (`import mod`), which is not on '
                'PyPI. On Replit main.py will report the ImportError on /health '
                'instead of crashing — vendor the SDK or strip the import first.')
        if reqs:
            warnings.append(f'requirements.txt guessed from imports: {", ".join(reqs)} '
                            '— check it before deploying.')
        if not os.path.exists(os.path.join(src, anchor)):
            warnings.append(f'no anchor {anchor} in the module — main.py will 500.')
        return reqs, warnings

    def bundles(self) -> dict:
        """Every bundle built so far."""
        rows = []
        for name in sorted(os.listdir(self.bundle_dir)):
            p = os.path.join(self.bundle_dir, name, 'mod_bundle.json')
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        rows.append(json.load(f))
                except ValueError:
                    pass
        return {'n': len(rows), 'bundles': rows}

    def _bundle_dir(self, name: str) -> str:
        p = os.path.realpath(os.path.join(self.bundle_dir, name))
        if not p.startswith(os.path.realpath(self.bundle_dir) + os.sep) or \
                not os.path.isdir(p):
            raise FileNotFoundError(f'no bundle {name!r} — build it with bundle()')
        return p

    def bundle_files(self, name: str) -> dict:
        """The file tree of one bundle."""
        root = self._bundle_dir(name)
        rows = []
        for r, dirs, fnames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in sorted(fnames):
                sp = os.path.join(r, fn)
                rows.append({'file': os.path.relpath(sp, root),
                             'bytes': os.path.getsize(sp)})
        return {'name': name, 'n': len(rows),
                'files': sorted(rows, key=lambda x: x['file'])}

    def bundle_file(self, name: str, file: str) -> dict:
        """Read one file out of a bundle (text, capped)."""
        root = self._bundle_dir(name)
        p = os.path.realpath(os.path.join(root, file))
        if not p.startswith(root + os.sep):
            raise ValueError('path escapes the bundle')
        if not os.path.exists(p):
            raise FileNotFoundError(file)
        with open(p, encoding='utf-8', errors='replace') as f:
            text = f.read(MAX_FILE)
        return {'name': name, 'file': file, 'bytes': os.path.getsize(p), 'text': text}

    def zip(self, name: str, module: str = None) -> dict:
        """Zip a bundle (building it first if `module` is given) → path + size."""
        if module:
            self.bundle(module, name=name)
        root = self._bundle_dir(name)
        out = shutil.make_archive(os.path.join(self.bundle_dir, name), 'zip', root)
        return {'name': name, 'zip': out, 'bytes': os.path.getsize(out)}

    @staticmethod
    def run_url(repo: str) -> dict:
        """The one-click Run-on-Replit URL for a GitHub repo — 'owner/name' or
        any github.com URL. This is the import path Replit actually honours."""
        s = (repo or '').strip().rstrip('/')
        # accept a pasted URL with or without its scheme — 'github.com/o/n' is
        # what a browser address bar hands you
        s = re.sub(r'^(https?://)?(www\.)?github\.com/', '', s)
        s = re.sub(r'\.git$', '', s)
        parts = [p for p in s.split('/') if p]
        if len(parts) < 2:
            raise ValueError("repo must look like 'owner/name'")
        owner, repo_name = parts[0], parts[1]
        return {'repo': f'{owner}/{repo_name}',
                'url': f'https://replit.com/github/{owner}/{repo_name}'}

    # ── remotes: a deployed Repl as a mod endpoint ───────────────────────────

    def remotes(self) -> dict:
        """Linked Repls."""
        st = self._read('remotes.json', {})
        return {'n': len(st), 'remotes': [dict(name=k, **v) for k, v in sorted(st.items())]}

    def link(self, name: str, url: str, note: str = None, discover: bool = True) -> dict:
        """Register a deployed Repl URL as a callable remote, and null-call it
        once so the catalog (and the MCP tool list) knows what it exposes. A
        Repl that is asleep or not deployed yet still links — discovery just
        records the error and can be re-run later."""
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name or ''):
            raise ValueError('name must be [A-Za-z0-9._-]+')
        u = (url or '').strip().rstrip('/')
        if not u.startswith(('http://', 'https://')):
            raise ValueError('url must start with http:// or https://')
        st = self._read('remotes.json', {})
        st[name] = {'url': u, 'note': note or '', 'added': int(time.time())}
        self._write('remotes.json', st)
        if discover:
            try:
                return self.discover(name)
            except Exception as e:                   # noqa: BLE001 — link still stands
                return {'name': name, **st[name], 'ok': False, 'error': str(e)}
        return {'name': name, **st[name]}

    def unlink(self, name: str) -> dict:
        st = self._read('remotes.json', {})
        if name not in st:
            raise FileNotFoundError(f'no remote {name!r}')
        gone = st.pop(name)
        self._write('remotes.json', st)
        return {'unlinked': name, **gone}

    def _remote_url(self, name: str) -> str:
        st = self._read('remotes.json', {})
        if name in st:
            return st[name]['url']
        if (name or '').startswith(('http://', 'https://')):
            return name.rstrip('/')
        raise FileNotFoundError(f'no remote {name!r} — link it first')

    def ping(self, name: str, timeout: int = 10) -> dict:
        """Health-check a linked Repl. Repls sleep; a slow first hit is a wake."""
        url = self._remote_url(name)
        t0 = time.time()
        try:
            body, code = self._http('GET', url + '/health', timeout=timeout)
            ok = code == 200
        except Exception as e:                       # noqa: BLE001 — reported, not raised
            return {'name': name, 'url': url, 'ok': False, 'error': str(e),
                    'ms': int((time.time() - t0) * 1000)}
        return {'name': name, 'url': url, 'ok': ok, 'code': code,
                'ms': int((time.time() - t0) * 1000), 'body': body}

    def remote_info(self, name: str, timeout: int = 20) -> dict:
        """The raw null call: POST the bare module URL, which the protocol says
        returns the remote's info (name, functions, params)."""
        url = self._remote_url(name)
        info, code, err = self._null_call(url, name, timeout)
        return {'name': name, 'url': url, 'code': code, 'info': info, 'error': err}

    def _null_call(self, url: str, name: str, timeout: int):
        """Ask a Repl what it is. The protocol's null call is a bare POST; a
        module served by the generated main.py also answers GET /, and one
        mounted under its own name answers POST /mod/{name}. Try all three so
        the bridge works against Repls it did not generate."""
        info, code, err = None, None, None
        for method, path, data in (('POST', '/', {}), ('GET', '/', None),
                                   ('POST', f'/mod/{name}', {})):
            try:
                body, code = self._http(method, url + path, data=data, timeout=timeout)
            except Exception as e:                   # noqa: BLE001 — reported, not raised
                err = str(e)
                continue
            if isinstance(body, dict) and isinstance(body.get('result'), dict):
                body = body['result']                # `m serve` wraps results
            if code == 200 and isinstance(body, dict) and (body.get('fns') or body.get('name')):
                return body, code, None
            err = f'HTTP {code}: {str(body)[:200]}'
        return info, code, err

    def discover(self, name: str, timeout: int = 25) -> dict:
        """Null-call a linked Repl and cache what it exposes — the mod it
        serves, its functions and their parameters. This cache is what
        catalog() reads and what the MCP server turns into per-function tools,
        so nothing has to touch the network to list them."""
        url = self._remote_url(name)
        t0 = time.time()
        info, code, err = self._null_call(url, name, timeout)
        st = self._read('remotes.json', {})
        rec = dict(st.get(name) or {'url': url, 'note': '', 'added': int(time.time())})
        rec.update({'url': url, 'ok': info is not None, 'code': code,
                    'ms': int((time.time() - t0) * 1000),
                    'checked': int(time.time()), 'error': err})
        if info:
            rec['mod'] = info.get('name') or rec.get('mod') or name
            rec['description'] = (info.get('description') or '')[:400]
            rec['fns'] = sorted(f for f in (info.get('fns') or []) if isinstance(f, str))
            rec['params'] = info.get('params') if isinstance(info.get('params'), dict) else {}
            rec['docs'] = info.get('docs') if isinstance(info.get('docs'), dict) else {}
            rec['loaded'] = info.get('loaded', True)
            rec['remote_error'] = info.get('error')
        if name in st:                               # a bare URL is not persisted
            st[name] = rec
            self._write('remotes.json', st)
        return {'name': name, **rec}

    def repl(self, name: str, refresh: bool = False, timeout: int = 25) -> dict:
        """One Repl-hosted mod in full. Reads the cache; discovers when the
        cache is empty or `refresh` is set."""
        st = self._read('remotes.json', {})
        rec = st.get(name)
        if rec is None and (name or '').startswith(('http://', 'https://')):
            return self.discover(name, timeout=timeout)
        if rec is None:
            raise FileNotFoundError(f'no remote {name!r} — link it first')
        if refresh or 'fns' not in rec:
            return self.discover(name, timeout=timeout)
        return {'name': name, **rec}

    def catalog(self, refresh: bool = False, timeout: int = 20) -> dict:
        """Every mod reachable on Replit through this bridge, with the
        functions each one exposes — the index the whole module is for."""
        st = self._read('remotes.json', {})
        mods, total = [], 0
        for name in sorted(st):
            try:
                rec = self.repl(name, refresh=refresh, timeout=timeout)
            except Exception as e:                   # noqa: BLE001 — one bad Repl is not fatal
                rec = {'name': name, **st[name], 'ok': False, 'error': str(e)}
            fns = rec.get('fns') or []
            total += len(fns)
            mods.append({
                'name': name, 'url': rec.get('url'), 'mod': rec.get('mod'),
                'description': rec.get('description') or rec.get('note') or '',
                'fns': fns, 'params': rec.get('params') or {},
                'docs': rec.get('docs') or {},
                'ok': rec.get('ok'), 'ms': rec.get('ms'),
                'checked': rec.get('checked'), 'error': rec.get('error'),
            })
        return {'n': len(mods), 'fns': total,
                'undiscovered': [m['name'] for m in mods if not m['fns']],
                'mods': mods}

    def call(self, name: str, fn: str = None, args: dict = None,
             timeout: int = 60, **kwargs) -> dict:
        """Call a function on a linked Repl: POST {url}/{fn} with JSON kwargs,
        the same shape `m serve` exposes. No fn → the null call.

        Remote arguments go in `args` — a Repl-hosted mod is free to have a
        parameter called `name` or `timeout`, and those must not be eaten by
        this function's own signature. **kwargs stays for CLI comfort
        (`m replit/call demo hello name=world`) and loses to `args`."""
        if not fn:
            return self.remote_info(name, timeout=timeout)
        payload = dict(kwargs)
        if args:
            if not isinstance(args, dict):
                raise ValueError('args must be an object')
            payload.update(args)
        url = self._remote_url(name)
        body, code = self._http('POST', f'{url}/{fn.lstrip("/")}', data=payload,
                                timeout=timeout)
        out = {'name': name, 'fn': fn, 'code': code,
               'result': body.get('result', body) if isinstance(body, dict) else body}
        if code == 404:
            # say what the Repl *does* expose rather than just "404"
            known = (self._read('remotes.json', {}).get(name) or {}).get('fns')
            if isinstance(body, dict) and body.get('fns'):
                known = body['fns']
            if known:
                out['fns'] = known
        return out

    @staticmethod
    def _http(method: str, url: str, data=None, headers: dict = None,
              timeout: int = 30, raw: bool = False):
        h = {'User-Agent': 'mod-replit/0.1', 'Accept': 'application/json'}
        h.update(headers or {})
        payload = None
        if data is not None and method != 'GET':
            if isinstance(data, (dict, list)):
                payload = json.dumps(data).encode()
                h.setdefault('Content-Type', 'application/json')
            else:
                payload = str(data).encode()
        req = urllib.request.Request(url, data=payload, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            body, code = e.read(), e.code
        if raw:
            return body, code
        text = body.decode('utf-8', 'replace')
        try:
            return json.loads(text), code
        except ValueError:
            return text[:8000], code

    # ── Replit DB ────────────────────────────────────────────────────────────

    def connect(self, db_url: str = None, account: str = None) -> dict:
        """Attach your Replit DB. `db_url` is the REPLIT_DB_URL from a Repl
        (Secrets/shell: `echo $REPLIT_DB_URL`); it embeds a rotating token, so
        it is stored 0600 under ~/.mod/replit and never in config.json."""
        s = self._read('secrets.json', {})
        if db_url:
            u = db_url.strip().rstrip('/')
            if not u.startswith('https://'):
                raise ValueError('REPLIT_DB_URL must be an https URL')
            s['db_url'] = u
        if account:
            s['account'] = account.strip()
        s['updated'] = int(time.time())
        self._write('secrets.json', s, secret=True)
        return self.account()

    def disconnect(self) -> dict:
        """Forget the DB URL and account."""
        p = self._path('secrets.json')
        if os.path.exists(p):
            os.remove(p)
        return {'connected': False}

    def account(self) -> dict:
        """What we hold, redacted. Replit exposes no whoami endpoint we can
        reach — the account name here is whatever you typed."""
        s = self._read('secrets.json', {})
        u = s.get('db_url') or ''
        return {
            'connected': bool(u),
            'account': s.get('account'),
            'db_url': (u[:34] + '…' + u[-6:]) if len(u) > 44 else ('set' if u else None),
            'updated': s.get('updated'),
        }

    def _db(self) -> str:
        u = self._read('secrets.json', {}).get('db_url')
        if not u:
            raise ValueError('no REPLIT_DB_URL — run connect(db_url=…) first')
        return u

    def db_keys(self, prefix: str = '', n: int = 500) -> dict:
        """List keys (Replit DB returns them newline-separated)."""
        body, code = self._http(
            'GET', f'{self._db()}/keys?prefix={urllib.parse.quote(prefix or "")}')
        if code != 200:
            raise RuntimeError(f'replit db {code}: {body}')
        keys = [urllib.parse.unquote(k) for k in str(body).split('\n') if k.strip()]
        return {'n': len(keys), 'prefix': prefix, 'keys': keys[:n]}

    def db_get(self, key: str) -> dict:
        """Read one key. Values are opaque strings; JSON is parsed when it is."""
        body, code = self._http('GET', f'{self._db()}/{urllib.parse.quote(key)}')
        if code == 404 or (code == 200 and body == ''):
            return {'key': key, 'found': False, 'value': None}
        if code != 200:
            raise RuntimeError(f'replit db {code}: {body}')
        return {'key': key, 'found': True, 'value': body}

    def db_set(self, key: str, value=None) -> dict:
        """Write one key (form-encoded, as Replit DB wants)."""
        val = value if isinstance(value, str) else json.dumps(value)
        data = urllib.parse.urlencode({key: val})
        body, code = self._http(
            'POST', self._db(), data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if code not in (200, 204):
            raise RuntimeError(f'replit db {code}: {body}')
        return {'key': key, 'set': True, 'bytes': len(val)}

    def db_del(self, key: str) -> dict:
        """Delete one key."""
        body, code = self._http('DELETE', f'{self._db()}/{urllib.parse.quote(key)}')
        if code not in (200, 204, 404):
            raise RuntimeError(f'replit db {code}: {body}')
        return {'key': key, 'deleted': code in (200, 204), 'code': code}

    # ── import: a Repl's repo → a module ─────────────────────────────────────

    def import_repl(self, repo: str, name: str = None, orbit: str = 'orbit',
                    description: str = None) -> dict:
        """Clone the GitHub repo behind a Repl into the fleet as a module and
        scaffold config.json + an anchor if it has none.

        A bare replit.com/@user/slug URL cannot be fetched — Replit blocks
        anonymous export (403) — so connect that Repl to GitHub and pass the
        repo instead. That is the only import path Replit leaves open."""
        s = (repo or '').strip()
        if 'replit.com/@' in s:
            raise ValueError(
                'Replit blocks anonymous Repl export (403). In the Repl: Git → '
                'connect a GitHub repo, then import that repo URL here.')
        ref = self.run_url(s)['repo']
        name = name or ref.split('/')[1]
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
            raise ValueError('name must be [A-Za-z0-9._-]+')
        if orbit not in ORBITS:
            raise ValueError(f'orbit must be one of {ORBITS}')
        dst = os.path.join(self.pkg_root, orbit, name)
        if os.path.exists(dst):
            raise FileExistsError(f'{orbit}/{name} exists — pick another name')
        r = subprocess.run(['git', 'clone', '--depth', '1',
                            f'https://github.com/{ref}.git', dst],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f'git clone failed: {r.stderr.strip()[:400]}')

        created = []
        cfg_path = os.path.join(dst, 'config.json')
        anchor = self._anchor_name(dst)
        if not anchor:
            with open(os.path.join(dst, 'mod.py'), 'w') as f:
                f.write(self._tmpl('anchor.py', NAME=name, REPO=ref))
            anchor, _ = 'mod.py', created.append('mod.py')
        if not os.path.exists(cfg_path):
            with open(cfg_path, 'w') as f:
                json.dump({'name': name,
                           'description': description or f'imported from Replit/GitHub {ref}',
                           'version': '0.1.0', 'anchor': anchor,
                           'source': {'github': f'https://github.com/{ref}',
                                      'replit': f'https://replit.com/github/{ref}'}},
                          f, indent=4)
            created.append('config.json')
        return {'module': f'{orbit}/{name}', 'path': dst, 'repo': ref,
                'anchor': anchor, 'created': created,
                'run_on_replit': f'https://replit.com/github/{ref}',
                'next': f'm {name}'}

    # ── MCP ──────────────────────────────────────────────────────────────────

    def _mcp(self):
        """mcp.py, loaded by path and handed this instance. By path because
        `import mcp` would collide with the PyPI package of that name, and
        because mcp.py imports this file back when it runs standalone."""
        if getattr(self, '_mcp_mod', None) is None:
            import importlib.util
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp.py')
            spec = importlib.util.spec_from_file_location('replit_mcp', p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.bind(self)
            self._mcp_mod = mod
        return self._mcp_mod

    def mcp_tools(self) -> dict:
        """The MCP tool surface: the bridge's own tools plus one tool per
        function per linked Repl (`repl_{remote}_{fn}`)."""
        mcp = self._mcp()
        tools = mcp.all_tools()
        return {'n': len(tools),
                'static': sum(1 for k in tools if not k.startswith(mcp.DYN_PREFIX)),
                'dynamic': sum(1 for k in tools if k.startswith(mcp.DYN_PREFIX)),
                'endpoint': f'http://localhost:{PORT}/mcp',
                'stdio': f'python3 {os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.py")}',
                'tools': [{'name': n, 'description': t['description'],
                           'write': bool(t.get('write')),
                           'inputSchema': t['inputSchema']} for n, t in tools.items()]}

    def mcp(self, method: str = 'tools/list', params: dict = None, id: int = 1) -> dict:
        """Speak one JSON-RPC message to the MCP server from the CLI —
        `m replit/mcp tools/list`. Same dispatcher the HTTP endpoint uses."""
        return self._mcp().handle({'jsonrpc': '2.0', 'id': id, 'method': method,
                                   'params': params or {}}, local=True)

    def mcp_serve(self, port: int = PORT + 1, background: bool = True) -> dict:
        """Run MCP on its own port. Optional — the console at :50530 already
        answers POST /mcp; this is for clients that want a bare endpoint."""
        port = int(port)
        if not background:
            return self._mcp().serve_http(port)
        self.kill(port)
        log = os.path.join(self.state_dir, 'mcp.log')
        f = open(log, 'ab')
        p = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          'mcp.py'), '--http', '--port', str(port)],
            stdout=f, stderr=f, start_new_session=True)
        return {'pid': p.pid, 'port': port, 'log': log,
                'endpoint': f'http://localhost:{port}/mcp'}

    # ── info ─────────────────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status()

    def info(self) -> dict:
        cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          'config.json')))
        return {'name': 'replit', 'description': self.description,
                'version': cfg.get('version'), 'port': PORT,
                'fns': cfg.get('fns', []), 'schema': cfg.get('schema'),
                'state': self.state_dir}

    def status(self) -> dict:
        """Everything the console shows on load."""
        b, r, c = self.bundles(), self.remotes(), self.catalog()
        try:
            tools = self.mcp_tools()['n']
        except Exception:                            # noqa: BLE001 — mcp is optional
            tools = 0
        return {
            'name': 'replit', 'port': PORT,
            'bundles': b['n'], 'remotes': r['n'],
            'repl_fns': c['fns'], 'mcp_tools': tools,
            'mcp_endpoint': f'http://localhost:{PORT}/mcp',
            'account': self.account(),
            'latest_bundle': (max(b['bundles'], key=lambda x: x.get('created', 0))
                              if b['n'] else None),
            'reachable': {
                'run_on_replit': 'https://replit.com/github/{owner}/{repo}',
                'replit_db': 'https://kv.replit.com (token URL required)',
            },
            'blocked': {
                'graphql': 'replit.com/graphql — persisted query hash required (400)',
                'repl_export': 'replit.com/@user/slug.zip — 403',
                'repl_data': 'replit.com/data/repls/@user/slug — 404',
            },
        }

    # ── server ───────────────────────────────────────────────────────────────

    def serve(self, port: int = PORT, background: bool = True) -> dict:
        """Run the console + API on one port (app at /, API under /api/*)."""
        port = int(port)
        if background:
            self.kill(port)
            log = os.path.join(self.state_dir, 'server.log')
            f = open(log, 'ab')
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__),
                                  'serve', str(port)],
                                 stdout=f, stderr=f, start_new_session=True,
                                 cwd=os.path.dirname(os.path.abspath(__file__)))
            ok = self._wait(port)
            return {'pid': p.pid, 'port': port, 'healthy': ok, 'log': log,
                    'url': f'http://localhost:{port}'}
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(('0.0.0.0', port), self._handler())
        print(f'replit console on :{port}', flush=True)
        srv.serve_forever()

    def kill(self, port: int = PORT) -> dict:
        killed = []
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{int(port)} 2>/dev/null'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            try:
                os.kill(int(pid), 15)
                killed.append(int(pid))
            except OSError:
                pass
        return {'killed': killed, 'port': int(port)}

    def _wait(self, port, tries=40):
        for _ in range(tries):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/api/status', timeout=1)
                return True
            except Exception:                        # noqa: BLE001
                time.sleep(0.25)
        return False

    def _handler(self):
        api = self

        from http.server import BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json', extra=None):
                data = body if isinstance(body, bytes) else (
                    json.dumps(body, default=str).encode() if ctype == 'application/json'
                    else body.encode())
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            @property
            def _local(self):
                """Loopback *and* not proxied — a gateway visitor is read-only."""
                if self.headers.get('X-Forwarded-For'):
                    return False
                return self.client_address[0] in ('127.0.0.1', '::1', 'localhost')

            @staticmethod
            def _norm(p):
                """Tolerate every gateway shape: /replit/* (app), /api/* (api,
                prefix stripped) and /api/replit/* (both). Strip in a loop —
                the prefixes stack, and one pass leaves /api/replit/mcp as
                /replit/mcp, which matches nothing."""
                for _ in range(3):
                    if p == '/replit' or p.startswith('/replit/'):
                        p = p[len('/replit'):] or '/'
                    elif p == '/api' or p.startswith('/api/'):
                        p = p[len('/api'):] or '/'
                    else:
                        break
                return p or '/'

            def do_OPTIONS(self):
                self._send(204, b'', 'text/plain', {
                    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type'})

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                path = self._norm(u.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                try:
                    if path in ('/', '/index.html'):
                        return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                    if path == '/health':
                        return self._send(200, {'ok': True, 'mod': 'replit'})
                    if path in ('/info', '/status'):
                        return self._send(200, dict(api.status(), local=self._local))
                    if path == '/modules':
                        return self._send(200, api.modules(search=q.get('search')))
                    if path == '/bundles':
                        return self._send(200, api.bundles())
                    if path == '/bundle/files':
                        return self._send(200, api.bundle_files(q['name']))
                    if path == '/bundle/file':
                        return self._send(200, api.bundle_file(q['name'], q['file']))
                    if path == '/bundle/zip':
                        z = api.zip(q['name'])
                        with open(z['zip'], 'rb') as f:
                            blob = f.read()
                        return self._send(200, blob, 'application/zip', {
                            'Content-Disposition':
                                f'attachment; filename="{q["name"]}.zip"'})
                    if path == '/remotes':
                        return self._send(200, api.remotes())
                    if path == '/catalog':
                        return self._send(200, api.catalog(
                            refresh=q.get('refresh') in ('1', 'true', 'yes')))
                    if path == '/repl':
                        return self._send(200, api.repl(
                            q['name'], refresh=q.get('refresh') in ('1', 'true', 'yes')))
                    if path == '/mcp/tools':
                        return self._send(200, api.mcp_tools())
                    if path == '/ping':
                        return self._send(200, api.ping(q['name']))
                    if path == '/account':
                        return self._send(200, api.account())
                    if path == '/db/keys':
                        return self._send(200, api.db_keys(prefix=q.get('prefix', '')))
                    if path == '/db/get':
                        return self._send(200, api.db_get(q['key']))
                    if path == '/run_url':
                        return self._send(200, api.run_url(q['repo']))
                    return self._send(404, {'error': 'not found'})
                except KeyError as e:
                    return self._send(400, {'error': f'missing param {e}'})
                except FileNotFoundError as e:
                    return self._send(404, {'error': str(e)})
                except (ValueError, RuntimeError) as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:               # noqa: BLE001
                    return self._send(500, {'error': str(e)})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                path = self._norm(u.path)
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                    body = json.loads(self.rfile.read(n) or b'{}')
                except ValueError as e:
                    return self._send(400, {'error': f'bad json: {e}'})
                # the protocol's null call: POST the bare module URL → info
                if path in ('/', '/mod/replit') and not body:
                    return self._send(200, api.info())
                # MCP shares this port: one JSON-RPC message per POST /mcp.
                # It gates itself per-tool (reads open, writes loopback-only),
                # so it sits above the blanket write gate below.
                if path == '/mcp':
                    resp = api._mcp().handle(body, local=self._local)
                    return self._send(200 if resp else 202, resp or b'', 'application/json'
                                      if resp else 'text/plain')
                if not self._local:
                    return self._send(403, {'error': 'read-only: writes are local-only '
                                                     '(loopback, unproxied)'})
                try:
                    if path == '/bundle':
                        return self._send(200, api.bundle(
                            body['module'], name=body.get('name'),
                            tests=bool(body.get('tests'))))
                    if path == '/link':
                        return self._send(200, api.link(body['name'], body['url'],
                                                        note=body.get('note')))
                    if path == '/unlink':
                        return self._send(200, api.unlink(body['name']))
                    if path == '/discover':
                        return self._send(200, api.discover(body['name']))
                    if path == '/remote/info':
                        return self._send(200, api.remote_info(body['name']))
                    if path == '/call':
                        return self._send(200, api.call(body['name'], body.get('fn'),
                                                        args=body.get('args') or {}))
                    if path == '/connect':
                        return self._send(200, api.connect(db_url=body.get('db_url'),
                                                           account=body.get('account')))
                    if path == '/disconnect':
                        return self._send(200, api.disconnect())
                    if path == '/db/set':
                        return self._send(200, api.db_set(body['key'], body.get('value')))
                    if path == '/db/del':
                        return self._send(200, api.db_del(body['key']))
                    if path == '/import':
                        return self._send(200, api.import_repl(
                            body['repo'], name=body.get('name'),
                            orbit=body.get('orbit', 'orbit')))
                    return self._send(404, {'error': 'not found'})
                except KeyError as e:
                    return self._send(400, {'error': f'missing field {e}'})
                except FileNotFoundError as e:
                    return self._send(404, {'error': str(e)})
                except FileExistsError as e:
                    return self._send(409, {'error': str(e)})
                except (ValueError, RuntimeError) as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:               # noqa: BLE001
                    return self._send(500, {'error': str(e)})

        return H


# ── the console (app.html lives next to this file) ─────────────────────────


try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.html')) as _f:
        INDEX_HTML = _f.read()
except OSError:
    INDEX_HTML = '<!doctype html><title>replit</title><p>app.html missing</p>'


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        Mod().serve(port=int(sys.argv[2]) if len(sys.argv) > 2 else PORT,
                    background=False)
    else:
        print(json.dumps(Mod().status(), indent=2, default=str))
