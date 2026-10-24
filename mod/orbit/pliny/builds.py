#!/usr/bin/env python3
"""plinyville builds — the apps that ship as source, built here so they run.

`run.py` is honest to a fault about one class of repo: GL4SS, LEAKHUB and
P4RS3LT0NGV3 are real browser apps, but what upstream committed is a Vite
`index.html` whose only script is `/src/main.tsx`, or a template a node script
fills in. A browser cannot compile any of that, so the arcade calls them
`needs_build` and the visitor gets a card that says "read the source" about an
app that was meant to be played.

The missing step is not clever: it is `npm install && npm run build`. This file
runs it, in the repo's own clone, and lets the rest of the module carry on as
before — the build writes `dist/index.html` next to the source, `Runner.entries`
walks the same checkout it always did, finds a page whose scripts are real
JavaScript, ranks it above the unbuilt stub and serves it out of the sandbox
like any other app. No new serving path, no new sandbox hole.

What this file is careful about:

* **Nothing is built unless somebody asks.** A build is minutes of network and
  a few hundred megabytes of `node_modules`; the daily scan never triggers one.
* **`--ignore-scripts` on every install.** This is elder-plinius's dependency
  tree, and an npm lifecycle script is arbitrary code running as this user on
  this box. The one thing we do execute is the repo's own declared build.
* **Vite gets `--base ./`.** Vite's default emits `<script src="/assets/…">`,
  which is the server root — not `/api/pliny/m/<repo>/run/dist/`. A page that
  builds and then 404s its own bundle is worse than one that never built.
* **A build that would produce a broken page is refused before it starts.**
  LEAKHUB reads `VITE_CONVEX_URL` at boot: without a Convex deployment of its
  own it builds perfectly and then throws on load, so `plan()` says that
  instead, with the variable named.
* **Every build leaves a receipt** at `~/.mod/pliny/builds.json` — the commit it
  was built from, the node it used, how long it took, and on failure the tail of
  the log that says why. A build made from a commit that has since moved is
  reported stale rather than silently served.

    m pliny/build GL4SS          build it
    GET  /api/pliny/m/GL4SS/build    what it would take / what happened
    POST /api/pliny/m/GL4SS/build    do it (returns at once; poll the GET)
    GET  /api/pliny/builds           every receipt, and what else could be built
"""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time

BUILD_INDEX = os.path.expanduser(
    os.environ.get('PLINYVILLE_BUILD_INDEX', '~/.mod/pliny/builds.json'))
LOG_DIR = os.path.expanduser(
    os.environ.get('PLINYVILLE_BUILD_LOGS', '~/.mod/pliny/build-logs'))
INSTALL_TIMEOUT = int(os.environ.get('PLINYVILLE_BUILD_INSTALL_TIMEOUT', 900))
BUILD_TIMEOUT = int(os.environ.get('PLINYVILLE_BUILD_TIMEOUT', 900))
LOG_TAIL = 40

# Where a build lands. Order matters only as a tiebreak — the real test is that
# the directory holds an index.html that was written by the build we just ran.
OUT_DIRS = ('dist', 'build', 'out', '_site', 'www', 'public', 'docs', 'site')
# …except when it is also the source. `build/` is P4RS3LT0NGV3's directory of
# node build *scripts*, and `docs/`, `public/` and `site/` are checked-in pages
# in half this corpus. Those are only ever accepted when the build just wrote
# an index.html into them.
NEVER_PREEXISTING = {'build', 'public', 'docs', 'site', 'www'}

# An app that reads a deployment URL out of its bundle at boot is not something
# a build can finish. Name the variable rather than shipping a white page.
VITE_ENV_RE = re.compile(r'import\.meta\.env\.([A-Z][A-Z0-9_]*)')
ENV_FILES = ('.env', '.env.local', '.env.production', '.env.production.local')
# VITE_ vars with a sane default in the code are not blockers; these are the
# ones that are somebody's own cloud deployment.
ENV_OK = {'VITE_BASE', 'VITE_APP_TITLE', 'VITE_PUBLIC_URL'}

NODE_GLOBS = ('/nix/store/*-nodejs-*/bin/node',
              '/usr/local/n/versions/node/*/bin/node',
              os.path.expanduser('~/.nvm/versions/node/*/bin/node'))


class BuildError(RuntimeError):
    """The build ran and did not produce a page."""


ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def _tail(text, n=LOG_TAIL):
    """The end of a build log, readable: npm and vite colour their output and
    a console printing raw escape codes back at you is not a receipt."""
    text = ANSI_RE.sub('', text or '').replace('\r', '\n')
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


class Builder:
    """`npm install && npm run build`, in the clone, with receipts."""

    def __init__(self, cloner):
        self.cloner = cloner
        self._lock = threading.Lock()
        self._running = {}          # repo -> {started, step}

    # ── the toolchain on this box ───────────────────────────────────────────

    @staticmethod
    def _nodes():
        """Every node on this box, newest first.

        The system node here is 18 and half of these repos are Vite 7, which
        wants 20.19+. There is usually a newer one in the nix store that
        nothing has put on PATH, and finding it is the difference between
        "cannot build" and a working app."""
        found = {}
        which = shutil.which('node')
        cands = ([which] if which else [])
        for pat in NODE_GLOBS:
            cands += glob.glob(pat)
        for p in cands:
            if not p or p in found or not os.access(p, os.X_OK):
                continue
            try:
                r = subprocess.run([p, '-v'], capture_output=True, text=True,
                                   timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            v = (r.stdout or '').strip().lstrip('v')
            if r.returncode == 0 and v:
                found[p] = v
        out = [{'path': p, 'version': v, 'major': int(v.split('.')[0] or 0)}
               for p, v in found.items() if v.split('.')[0].isdigit()]
        out.sort(key=lambda n: [int(x) for x in re.findall(r'\d+', n['version'])],
                 reverse=True)
        return out

    @staticmethod
    def _need_major(engines) -> int:
        """The lowest node major this package will admit to working on.

        `"^20.19.0 || >=22.12.0"` means 20. We only need the floor: npm's own
        engine check is advisory, and the point of reading it is to pick the
        newest node when the system one is too old."""
        majors = [int(m) for m in re.findall(r'(?:\^|>=|~|>)\s*(\d+)',
                                             str(engines or ''))]
        return min(majors) if majors else 0

    def node_for(self, engines=None):
        nodes = self._nodes()
        if not nodes:
            return None, nodes
        need = self._need_major(engines)
        for n in nodes:
            if n['major'] >= need:
                return n, nodes
        return nodes[0], nodes           # too old for the manifest — say so later

    # ── can this repo be built, and should it ───────────────────────────────

    def plan(self, name) -> dict:
        """What building this repo would take — cheap enough for the gallery.

        Reads package.json and the source tree; runs nothing."""
        root = self.cloner.path(name)
        out = {'repo': name, 'can_build': False, 'why': None}
        pkg_path = os.path.join(root, 'package.json')
        if not os.path.isfile(pkg_path):
            out['why'] = 'no package.json — there is no build to run'
            return out
        try:
            with open(pkg_path, encoding='utf-8') as f:
                pkg = json.load(f)
        except (OSError, ValueError) as e:
            out['why'] = f'package.json will not parse ({e})'
            return out
        scripts = pkg.get('scripts') or {}
        vite = bool(glob.glob(os.path.join(root, 'vite.config.*')))
        if 'build' not in scripts and not vite:
            out['why'] = 'package.json declares no build script'
            return out
        node, nodes = self.node_for((pkg.get('engines') or {}).get('node'))
        out.update(tool='vite' if vite else 'npm',
                   script=scripts.get('build'),
                   engines=(pkg.get('engines') or {}).get('node'),
                   node=node['version'] if node else None,
                   nodes=[n['version'] for n in nodes],
                   lock=os.path.isfile(os.path.join(root, 'package-lock.json')))
        if not node:
            out['why'] = 'no node on this box to build with'
            return out
        need = self._need_major(out['engines'])
        if need and node['major'] < need:
            out['why'] = (f'this app wants node {need}+ and the newest one here is '
                          f'{node["version"]}')
            return out
        blocked = self._needs_env(root)
        if blocked:
            out['why'] = (
                'it would build and then fail on load: the bundle reads '
                + ', '.join(blocked[:3]) + ' at boot — a deployment of its own '
                'that is not part of the repo, and no .env here supplies it')
            out['needs_env'] = blocked
            return out
        out['can_build'] = True
        out['note'] = ('npm install --ignore-scripts, then '
                       + ('vite build --base ./' if vite else 'npm run build')
                       + ' — a few minutes the first time')
        return out

    @staticmethod
    def _needs_env(root) -> list:
        """Build-time variables the app reads and nothing here provides."""
        supplied = set()
        for fn in ENV_FILES:
            try:
                with open(os.path.join(root, fn), encoding='utf-8') as f:
                    for ln in f:
                        if '=' in ln and not ln.lstrip().startswith('#'):
                            supplied.add(ln.split('=', 1)[0].strip())
            except OSError:
                continue
        want, seen = set(), 0
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, 'src')):
            dirnames[:] = [d for d in dirnames if d != 'node_modules']
            for fn in filenames:
                if not fn.endswith(('.ts', '.tsx', '.js', '.jsx', '.vue', '.svelte')):
                    continue
                seen += 1
                if seen > 400:
                    break
                try:
                    with open(os.path.join(dirpath, fn), encoding='utf-8',
                              errors='replace') as f:
                        want.update(VITE_ENV_RE.findall(f.read(200_000)))
                except OSError:
                    pass
        return sorted(v for v in want
                      if v.startswith('VITE_') and v not in ENV_OK
                      and v not in supplied)

    # ── doing it ────────────────────────────────────────────────────────────

    def state(self, name) -> dict:
        """Idle, running, or the receipt of the last attempt."""
        r = self.receipt(name)
        run = self._running.get(name)
        out = {'repo': name, 'running': bool(run),
               'state': 'running' if run else ('done' if r and r.get('ok')
                                               else ('failed' if r else 'idle'))}
        if run:
            out.update(step=run['step'], seconds=round(time.time() - run['started'], 1))
        if r:
            out['receipt'] = r
            out['stale'] = self.stale(name, r)
        return out

    def stale(self, name, receipt=None) -> bool:
        """Built from a commit the clone has since moved past."""
        r = receipt or self.receipt(name)
        if not r or not r.get('ok'):
            return False
        head = self._head(name)
        return bool(head and r.get('head') and head != r['head'])

    def out_dir(self, name) -> str:
        """The built directory of a *good* build, relative to the clone. '' when
        there is nothing usable — the caller then reads the source as before."""
        r = self.receipt(name)
        if not r or not r.get('ok') or not r.get('out'):
            return ''
        full = os.path.join(self.cloner.path(name), r['out'].replace('/', os.sep))
        return r['out'] if os.path.isdir(full) else ''

    def start(self, name, force=False) -> dict:
        """Kick a build off in the background and return the state at once.

        A build is minutes long; a browser holding a POST open for four of them
        is how a console starts lying about what happened."""
        with self._lock:
            if name in self._running:
                return self.state(name)
            r = self.receipt(name)
            if r and r.get('ok') and not force and not self.stale(name, r):
                return dict(self.state(name), skipped='already built')
            plan = self.plan(name)
            if not plan.get('can_build'):
                raise BuildError(plan.get('why') or f'{name} cannot be built here')
            self._running[name] = {'started': time.time(), 'step': 'install'}
        t = threading.Thread(target=self._work, args=(name, plan), daemon=True)
        t.start()
        return self.state(name)

    def _work(self, name, plan):
        try:
            self.build(name, plan=plan, _claimed=True)
        except Exception:                                    # noqa: BLE001
            pass                                             # the receipt has it

    def build(self, name, plan=None, force=False, _claimed=False) -> dict:
        """Install, build, find the page. Returns the receipt either way."""
        root = self.cloner.path(name)
        plan = plan or self.plan(name)
        if not _claimed:
            with self._lock:
                if name in self._running:
                    raise BuildError(f'{name} is already building')
                if not plan.get('can_build'):
                    raise BuildError(plan.get('why') or f'{name} cannot be built here')
                self._running[name] = {'started': time.time(), 'step': 'install'}
        t0 = time.time()
        node = plan.get('node')
        node_path = next((n['path'] for n in self._nodes()
                          if n['version'] == node), None)
        env = dict(os.environ)
        if node_path:
            env['PATH'] = os.path.dirname(node_path) + os.pathsep + env.get('PATH', '')
        # NODE_ENV=production would be the obvious thing to set here and it is
        # exactly wrong: npm reads it and skips devDependencies, which is where
        # every one of these repos keeps vite. The build tool sets its own mode.
        env.update(CI='1', npm_config_audit='false', npm_config_fund='false',
                   npm_config_update_notifier='false', npm_config_include='dev')
        env.pop('NODE_ENV', None)
        rec = {'repo': name, 'ok': False, 'at': time.time(), 'node': node,
               'tool': plan.get('tool'), 'head': self._head(name)}
        log = []
        try:
            before = self._dirs(root)
            # npm ci is the honest install when there is a lock, but these locks
            # are as old as the commits — fall back rather than fail on drift.
            if plan.get('lock'):
                code, txt = self._run(['npm', 'ci', '--ignore-scripts',
                                       '--no-audit', '--no-fund'],
                                      root, env, INSTALL_TIMEOUT)
                log.append(txt)
                if code != 0:
                    code, txt = self._run(['npm', 'install', '--ignore-scripts',
                                           '--no-audit', '--no-fund'],
                                          root, env, INSTALL_TIMEOUT)
                    log.append(txt)
            else:
                code, txt = self._run(['npm', 'install', '--ignore-scripts',
                                       '--no-audit', '--no-fund'],
                                      root, env, INSTALL_TIMEOUT)
                log.append(txt)
            if code != 0:
                raise BuildError('npm install failed')
            self._step(name, 'build')
            # Vite by hand, for the base path: its default writes /assets/… ,
            # which is this server's root and not the app's. Skipping the
            # repo's own `tsc -b && vite build` also skips a type check that
            # fails on half these repos for reasons the page does not care about.
            argv = ['npm', 'run', 'build']
            if plan.get('tool') == 'vite':
                # the binary the install just wrote, not `npx` — npx with no
                # local copy goes to the network and then asks a question no
                # one is there to answer ("npx canceled due to missing packages")
                vite = os.path.join(root, 'node_modules', '.bin', 'vite')
                argv = ([vite] if os.path.isfile(vite) else ['npx', '--yes', 'vite']) \
                    + ['build', '--base', './']
            code, txt = self._run(argv, root, env, BUILD_TIMEOUT)
            log.append(txt)
            if code != 0:
                raise BuildError((argv[0] + ' build failed'))
            out = self._find_out(root, before, t0)
            if not out:
                raise BuildError('the build finished but wrote no index.html we '
                                 'could find — nothing to serve')
            rec.update(ok=True, out=out[0], entry=out[1])
        except BuildError as e:
            rec['error'] = str(e)
        except (OSError, subprocess.SubprocessError) as e:
            rec['error'] = f'{type(e).__name__}: {e}'
        finally:
            with self._lock:
                self._running.pop(name, None)
        rec['seconds'] = round(time.time() - t0, 1)
        full = '\n'.join(log)
        rec['log'] = _tail(full)
        rec['log_file'] = self._write_log(name, full)
        self._record(rec)
        return rec

    def _step(self, name, step):
        r = self._running.get(name)
        if r:
            r['step'] = step

    @staticmethod
    def _run(argv, cwd, env, timeout):
        try:
            r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, f'$ {" ".join(argv)}\ntimed out after {timeout}s'
        except OSError as e:
            return 127, f'$ {" ".join(argv)}\n{e}'
        return r.returncode, (f'$ {" ".join(argv)}\n' + (r.stdout or '')
                              + (r.stderr or ''))

    @staticmethod
    def _dirs(root):
        try:
            return {d for d in os.listdir(root)
                    if os.path.isdir(os.path.join(root, d))}
        except OSError:
            return set()

    @staticmethod
    def _find_out(root, before, t0):
        """Which directory did the build write, and where is its page?

        The test is not the name: it is an index.html newer than the build we
        just ran. `build/` is P4RS3LT0NGV3's directory of build *scripts* and
        `docs/` is a checked-in page in five of these repos — either is a real
        answer only if this build put a page in it."""
        for d in OUT_DIRS:
            full = os.path.join(root, d)
            if not os.path.isdir(full):
                continue
            for cand in ('index.html', 'index.htm'):
                p = os.path.join(full, cand)
                if not os.path.isfile(p):
                    continue
                if d in NEVER_PREEXISTING and d in before and \
                        os.path.getmtime(p) < t0:
                    continue
                return d, f'{d}/{cand}'
        return None

    def _head(self, name):
        try:
            r = subprocess.run(['git', 'rev-parse', 'HEAD'],
                               cwd=self.cloner.path(name), capture_output=True,
                               text=True, timeout=20)
            return (r.stdout or '').strip()[:12] if r.returncode == 0 else ''
        except (OSError, subprocess.SubprocessError):
            return ''

    @staticmethod
    def _write_log(name, text):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            p = os.path.join(LOG_DIR, re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.log')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(text[-400_000:])
            return p
        except OSError:
            return None

    # ── receipts ────────────────────────────────────────────────────────────

    def _all(self) -> dict:
        try:
            with open(BUILD_INDEX, encoding='utf-8') as f:
                return (json.load(f) or {}).get('mods') or {}
        except (OSError, ValueError):
            return {}

    def receipt(self, name):
        return self._all().get(name)

    def _record(self, rec):
        mods = self._all()
        mods[rec['repo']] = rec
        try:
            os.makedirs(os.path.dirname(BUILD_INDEX), exist_ok=True)
            tmp = BUILD_INDEX + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'updated': time.time(), 'mods': mods}, f)
            os.replace(tmp, BUILD_INDEX)
        except OSError:
            pass

    def forget(self, name) -> dict:
        """Drop the receipt and the build output — the card goes back to source."""
        mods = self._all()
        rec = mods.pop(name, None)
        if rec and rec.get('out'):
            shutil.rmtree(os.path.join(self.cloner.path(name), rec['out']),
                          ignore_errors=True)
        for d in ('node_modules',):
            shutil.rmtree(os.path.join(self.cloner.path(name), d), ignore_errors=True)
        try:
            os.makedirs(os.path.dirname(BUILD_INDEX), exist_ok=True)
            with open(BUILD_INDEX, 'w', encoding='utf-8') as f:
                json.dump({'updated': time.time(), 'mods': mods}, f)
        except OSError:
            pass
        return {'repo': name, 'forgotten': bool(rec)}

    def stamp(self, name) -> str:
        """Part of the run index's cache key: a build changes what runs."""
        r = self.receipt(name)
        return f"{r.get('at', 0):.0f}{'+' if r.get('ok') else '-'}" if r else ''

    def catalog(self, names=()) -> dict:
        """Every build receipt, and what else on the shelf could be built."""
        mods = self._all()
        built = [dict(v, stale=self.stale(k, v)) for k, v in sorted(mods.items())]
        out = {'built': sum(1 for b in built if b.get('ok')),
               'builds': built,
               'running': sorted(self._running),
               'nodes': [n['version'] for n in self._nodes()],
               'note': 'apps that ship as source, built here so they can run. '
                       'npm install runs with --ignore-scripts; only the repo\'s '
                       'own build script is executed.'}
        if names:
            out['buildable'] = [p for p in (self.plan(n) for n in names)
                                if p.get('can_build')]
        return out


if __name__ == '__main__':
    import sys
    from clone import Cloner
    b = Builder(Cloner())
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        print(json.dumps(b.build(sys.argv[2], force=True), indent=2, default=str))
    elif len(sys.argv) > 1:
        print(json.dumps(b.plan(sys.argv[1]), indent=2, default=str))
    else:
        print(json.dumps(b.catalog(), indent=2, default=str))
