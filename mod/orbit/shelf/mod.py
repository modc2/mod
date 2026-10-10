"""
shelf — the fleet's state, on one shelf.

Seventy-two modules keep their state in `~/.mod/<name>`, by convention and
without supervision. It is 31 gigabytes now. Two directories are 13G each and
nothing in the fleet can tell you which, what is in the shared store, whether
the content-addressed bytes still hash to their own names, or what could be
thrown away. This module can.

    m shelf                          # what is on the box, and the top of the pile
    m shelf/space                    # every module's state, largest first
    m shelf/usage claude             # 13G of what, exactly
    m shelf/big limit=10             # the largest individual files
    m shelf/prefixes                 # namespaces in the shared store
    m shelf/keys prefix=wasmland     # what is filed under one of them
    m shelf/read wasmland/index      # one value, secrets redacted
    m shelf/grep 0x89bc              # which record mentions this
    m shelf/verify                   # do the blobs still hash to their names
    m shelf/orphans                  # bytes nothing refers to
    m shelf/gc confirm=True          # take them (dry by default)
    m shelf/snapshot wasmland        # freeze a root under a CID
    m shelf/serve                    # API :50570, console :50571

READ-FIRST, AND LOCAL
    Everything here reads. The three verbs that write — `gc`, `restore` and
    `rm` — are dry runs until `confirm=True`, and say what they would do first.
    The server binds 127.0.0.1 and `route` is false in config.json, so the
    gateway does not publish it: this reads every module's private state, and a
    state browser on a public port is a credential exfiltrator with a console.

SECRETS ARE HANDLED ON THE READ PATH
    `~/.mod` holds HMAC secrets, owner claims, wallets and PATs next to the
    boring JSON somebody wants to look at. So redaction happens in src/redact.py
    before a value reaches a caller — not in the page, where it would be one
    curl away from irrelevant. Secret *files* are never opened at all; secret
    *fields* come back as `sha256:1f3a9c02 (64b)`, a fingerprint that answers
    the real questions — do two modules share a key, did it rotate — without
    the bytes leaving the box.

NO INDEX, NO DATABASE, NO CACHE
    Every answer is computed from the directory at the moment it is asked. A
    scan of ~46k files costs about a tenth of a second, which is cheap enough
    that correctness is affordable: this module cannot go stale and cannot
    disagree with the disk. An operator tool that lies about the thing it is
    inspecting is worse than no tool.

WHY IT IS NOT CALLED `store`
    It was, for a day, at orbit/store — and it was unreachable. Module names
    are derived from paths, and `core.tree` applies the orbits in an order that
    lets `core` overwrite `orbit`, so `m.mod('store')` resolves to core/store
    and always will. Anything built under that name is dead code. `shelf` is a
    lens over what the store holds, not a second store; core/store is still the
    one that writes.
"""
import os
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
PORT = 50570       # the API
APP_PORT = 50571   # the console

sys.path.insert(0, str(DIR))
from src import blobs, keys, redact, snapshot, space  # noqa: E402


class Mod:
    description = __doc__
    path = str(DIR)

    # ── info ─────────────────────────────────────────────────────

    def forward(self, fn: str = 'info', *args, **kwargs):
        return getattr(self, fn)(*args, **kwargs)

    def info(self):
        """The box at a glance: how much state, whose, and is it intact."""
        report = space.scan(limit=5)
        store = keys.prefixes()
        return {
            'name': 'shelf',
            'reads': space.MOD_HOME,
            'total': report['total'],
            'biggest': [{'module': r['module'], 'size': r['size'],
                         'vendor': r['vendor_size']} for r in report['modules']],
            'store': {'root': store['root'], 'keys': store.get('keys', 0),
                      'prefixes': [p['prefix'] for p in store.get('prefixes', [])]},
            'api': f'http://127.0.0.1:{PORT}',
            'app': f'http://127.0.0.1:{APP_PORT}/shelf',
            'writes': 'gc, restore and rm — all dry until confirm=True',
        }

    def health(self):
        """Is the shared store internally consistent right now."""
        report = blobs.verify()
        return {'ok': report['healthy'], 'checked': report['checked'],
                'corrupt': len(report['corrupt']),
                'duplicates': len(report['duplicates']),
                'home': os.path.isdir(space.MOD_HOME)}

    def readme(self):
        for name in ('README.md', 'readme.md'):
            path = DIR / name
            if path.exists():
                return path.read_text()
        return None

    # ── space: where the disk went ───────────────────────────────

    def space(self, limit: int = 0):
        """Every module's state directory, largest first."""
        return space.scan(limit=int(limit))

    def usage(self, module: str, depth: int = 1, limit: int = 40):
        """One module's state, broken down one level in."""
        return space.usage(module, depth=int(depth), limit=int(limit))

    def big(self, limit: int = 25, module: str = None):
        """The largest individual files — one huge file or a million small ones."""
        return space.big(limit=int(limit), module=module)

    # ── keys: reading the shared store ───────────────────────────

    def roots(self):
        """Directories this can be pointed at. The store is the shared one."""
        return {'roots': keys.roots()}

    def prefixes(self, root: str = None):
        """Top-level namespaces in a root, with what each holds."""
        return keys.prefixes(root)

    def keys(self, prefix: str = '', root: str = None, search: str = '',
             limit: int = 200, offset: int = 0):
        """List keys under a prefix. Metadata only — no value is read."""
        return keys.keys(root=root, prefix=prefix, search=search,
                         limit=int(limit), offset=int(offset))

    def read(self, key: str, root: str = None, raw: bool = False):
        """One key's value, with secrets redacted."""
        return keys.read(key, root=root, raw=bool(raw))

    def grep(self, text: str, root: str = None, prefix: str = '', limit: int = 50):
        """Find keys whose contents mention something. Secret files are skipped."""
        return keys.grep(text, root=root, prefix=prefix, limit=int(limit))

    def rm(self, key: str, root: str = None, confirm: bool = False):
        """Delete one key. Dry until confirmed, and never a secret file."""
        root_path = keys._resolve(root)
        path = keys.key2path(root_path, key)
        if not path:
            return {'key': key, 'found': False, 'deleted': False}
        if redact.sensitive_file(path):
            return {'key': key, 'deleted': False,
                    'error': 'refusing to delete a secret file from a browser'}
        size = os.path.getsize(path)
        if not confirm:
            return {'key': key, 'bytes': size, 'deleted': False,
                    'note': 'dry run — pass confirm=True to delete'}
        os.remove(path)
        return {'key': key, 'bytes': size, 'deleted': True}

    # ── blobs: is the content-addressed store telling the truth ──

    def verify(self, root: str = None, limit: int = 0):
        """Rehash every content-addressed file and report what disagrees."""
        return blobs.verify(root=root, limit=int(limit))

    def orphans(self, root: str = None):
        """Blobs no record refers to, and the bytes they hold."""
        return blobs.orphans(root=root)

    def strays(self, root: str = None):
        """The same bytes filed in two places — leftovers from an old layout."""
        return blobs.strays(root=root)

    def gc(self, root: str = None, confirm: bool = False, min_age_days: float = 1.0):
        """Report — or with confirm=True, delete — orphaned blobs."""
        return blobs.gc(root=root, confirm=bool(confirm),
                        min_age_days=float(min_age_days))

    # ── snapshots ────────────────────────────────────────────────

    def snapshot(self, root: str = None, pin: bool = True):
        """Freeze a root under a CID. Deterministic: same tree, same name."""
        return snapshot.create(root=root, pin=bool(pin))

    def inspect(self, cid: str):
        """What is inside a snapshot, without writing any of it."""
        return snapshot.inspect(cid)

    def restore(self, cid: str, root: str = None, confirm: bool = False,
                overwrite: bool = False):
        """Unpack a snapshot back into a root. Dry until confirmed."""
        return snapshot.restore(cid, root=root, confirm=bool(confirm),
                                overwrite=bool(overwrite))

    # ── serve ────────────────────────────────────────────────────

    def serve(self, no_app: bool = False, no_api: bool = False, **kw):
        """Run the API and the console under pm2, the way the fleet does.

        Two processes on two ports, matching the rest of the fleet so the
        router and `m pm/ls` see the shape they expect — even though both
        halves here are stdlib and could have been one.
        """
        started = []
        if not no_api:
            self._pm2('shelf-api', f'{DIR}/src/api/api.py', PORT)
            started.append(f'shelf-api :{PORT}')
        if not no_app:
            self._pm2('shelf-app', f'{DIR}/src/app/server.py', APP_PORT)
            started.append(f'shelf-app :{APP_PORT}')
        return {'started': started, 'app': f'http://127.0.0.1:{APP_PORT}/shelf',
                'bound': '127.0.0.1 — local by design, this reads private state'}

    def stop(self, **kw):
        for name in ('shelf-api', 'shelf-app'):
            subprocess.run(['pm2', 'delete', name], capture_output=True)
        return {'stopped': ['shelf-api', 'shelf-app']}

    def _pm2(self, name: str, script: str, port: int):
        subprocess.run(['pm2', 'delete', name], capture_output=True)
        subprocess.run(
            ['pm2', 'start', sys.executable, '--name', name, '--',
             script, '--port', str(port)],
            capture_output=True, cwd=str(DIR))

    def test(self, **kw):
        """Run the test suite."""
        out = subprocess.run([sys.executable, '-m', 'pytest', str(DIR / 'tests'), '-q'],
                             capture_output=True, text=True, cwd=str(DIR))
        return {'ok': out.returncode == 0, 'output': out.stdout[-4000:] or out.stderr[-4000:]}
