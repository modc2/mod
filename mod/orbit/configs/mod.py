"""
configs — the fleet's config manager.

Every module carries a config.json; this module is the one place to see, query,
edit, and sanity-check all of them. It scans core/ and orbit/ for configs,
lints the fleet (missing fields, name/dir mismatches, duplicate names, port
collisions — which the fleet really has), and edits any key by dot-path with an
automatic timestamped backup in ~/.mod/configs/ so every change is reversible.

The web app (zero-dependency, one port) is a read-only fleet browser: a config
card grid with search, a pretty JSON viewer, a port map with collisions
highlighted, and the lint report. Edits stay on the CLI where they're owner-run.

CLI:
    m configs                                   # every module config, one row each
    m configs/get updates                       # one module's full config
    m configs/set updates icon=📡               # set a key (dot-paths + JSON values)
    m configs/set claude urls.app=http://x      # nested key by dot-path
    m configs/unset updates urls.gateway
    m configs/lint                              # fleet-wide issues report
    m configs/ports                             # port map + collisions
    m configs/history updates                   # git log of a config
    m configs/restore updates                   # undo the last set/unset
"""
import json
import os
import re
import subprocess
import mod as m

APP_PORT = 50230
SKIP_DIRS = {'node_modules', 'target', '.git', '.next', '__pycache__', 'dist',
             'build', '.venv', 'venv'}
PORT_KEY_RE = re.compile(r'(^|_)port$')


class Mod:
    description = ('Fleet config manager: scan every module config.json across '
                   'core/ and orbit/, get/set keys by dot-path with automatic '
                   'backups, lint for missing fields and port collisions')

    def __init__(self, roots=None, state_path=None):
        pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.roots = [m.abspath(r) for r in (roots or
                      [os.path.join(pkg, 'core'), os.path.join(pkg, 'orbit')])]
        self.state_dir = m.abspath(state_path or '~/.mod/configs')

    # --- fleet scan -----------------------------------------------------------

    def _walk(self):
        """Yield every config.json path under the roots (depth ≤ 3, junk dirs
        skipped)."""
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                rel = os.path.relpath(dirpath, root)
                depth = 0 if rel == '.' else rel.count(os.sep) + 1
                dirnames[:] = [] if depth >= 3 else \
                    [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
                if 'config.json' in filenames and rel != '.':
                    yield os.path.join(dirpath, 'config.json')

    def _read(self, path):
        """Parse one config file → (config dict | None, error | None)."""
        try:
            with open(path) as f:
                return json.load(f), None
        except Exception as e:
            return None, str(e)

    def _row(self, path):
        cfg, err = self._read(path)
        d = os.path.dirname(path)
        row = {
            'name': (cfg or {}).get('name') or os.path.basename(d),
            'dir': os.path.basename(d),
            'path': path,
            'orbit': 'orbit' if f'{os.sep}orbit{os.sep}' in path else 'core',
            'valid': err is None,
        }
        if err:
            row['error'] = err
            return row
        row.update({
            'version': cfg.get('version'),
            'description': (cfg.get('description') or '').strip()[:200],
            'icon': cfg.get('icon'),
            'color': cfg.get('color'),
            'ports': self._ports_of(cfg),
            'keys': len(cfg),
            'fns': len(cfg.get('fns') or []),
            'schema': bool(cfg.get('schema')),
        })
        return row

    @staticmethod
    def _ports_of(cfg) -> dict:
        """Top-level *_port keys with int-ish values, e.g. {'app_port': 50230}."""
        out = {}
        for k, v in (cfg or {}).items():
            if PORT_KEY_RE.search(k) and isinstance(v, (int, str)) and str(v).isdigit():
                out[k] = int(v)
        return out

    def configs(self, search=None, n=500) -> dict:
        """Every module config in the fleet, one summary row each. `search`
        filters on name/description/dir."""
        rows = [self._row(p) for p in self._walk()]
        if search:
            s = str(search).lower()
            rows = [r for r in rows
                    if s in r['name'].lower() or s in r['dir'].lower()
                    or s in (r.get('description') or '').lower()]
        rows.sort(key=lambda r: (r['orbit'], r['name'].lower()))
        return {'count': len(rows),
                'invalid': sum(1 for r in rows if not r['valid']),
                'configs': rows[:int(n)]}

    def forward(self, **kwargs):
        return self.configs(**kwargs)

    # --- one module -----------------------------------------------------------

    def path(self, mod) -> str:
        """Resolve a module name (or dir name) to its config.json path."""
        mod = str(mod).strip()
        fallback = None
        for p in self._walk():
            cfg, _ = self._read(p)
            if (cfg or {}).get('name') == mod:
                return p
            if os.path.basename(os.path.dirname(p)) == mod:
                fallback = fallback or p
        if fallback:
            return fallback
        raise FileNotFoundError(f'no config.json found for module {mod!r}')

    def get(self, mod, key=None):
        """A module's full config (or one key of it, dot-paths allowed)."""
        p = self.path(mod)
        cfg, err = self._read(p)
        if err:
            return {'path': p, 'error': err}
        if key is None:
            return {'path': p, 'config': cfg}
        cur = cfg
        for part in str(key).split('.'):
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(f'{key!r} not in {mod} config')
            cur = cur[part]
        return {'path': p, 'key': key, 'value': cur}

    @staticmethod
    def _coerce(value):
        """CLI values arrive as strings; parse JSON-ish ones ('8080', 'true',
        '["a"]') into real types, leave the rest as strings."""
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except Exception:
            return value

    def set(self, mod, key, value) -> dict:
        """Set one key in a module's config.json (dot-paths create nested dicts;
        JSON values are parsed). The previous file is backed up first."""
        p = self.path(mod)
        cfg, err = self._read(p)
        if err:
            raise ValueError(f'{p} is not valid JSON, fix it first: {err}')
        backup = self._backup(mod, p)
        cur = cfg
        parts = str(key).split('.')
        for part in parts[:-1]:
            if not isinstance(cur.get(part), dict):
                cur[part] = {}
            cur = cur[part]
        old = cur.get(parts[-1])
        cur[parts[-1]] = self._coerce(value)
        self._write(p, cfg)
        return {'mod': mod, 'key': key, 'old': old, 'new': cur[parts[-1]],
                'path': p, 'backup': backup}

    def unset(self, mod, key) -> dict:
        """Delete one key (dot-paths allowed) from a module's config.json,
        backing up first."""
        p = self.path(mod)
        cfg, err = self._read(p)
        if err:
            raise ValueError(f'{p} is not valid JSON, fix it first: {err}')
        cur = cfg
        parts = str(key).split('.')
        for part in parts[:-1]:
            cur = cur.get(part)
            if not isinstance(cur, dict):
                raise KeyError(f'{key!r} not in {mod} config')
        if parts[-1] not in cur:
            raise KeyError(f'{key!r} not in {mod} config')
        backup = self._backup(mod, p)
        old = cur.pop(parts[-1])
        self._write(p, cfg)
        return {'mod': mod, 'unset': key, 'old': old, 'path': p, 'backup': backup}

    @staticmethod
    def _write(path, cfg):
        with open(path, 'w') as f:
            json.dump(cfg, f, indent=4)
            f.write('\n')

    # --- backups ---------------------------------------------------------------

    def _backup(self, mod, path) -> str:
        from datetime import datetime
        bdir = os.path.join(self.state_dir, 'backups', str(mod))
        os.makedirs(bdir, exist_ok=True)
        dest = os.path.join(bdir, datetime.now().strftime('%Y%m%dT%H%M%S.%f') + '.json')
        with open(path) as src, open(dest, 'w') as out:
            out.write(src.read())
        return dest

    def backups(self, mod) -> list:
        """Backups taken before each set/unset on a module, newest first."""
        bdir = os.path.join(self.state_dir, 'backups', str(mod))
        if not os.path.isdir(bdir):
            return []
        return sorted((os.path.join(bdir, f) for f in os.listdir(bdir)
                       if f.endswith('.json')), reverse=True)

    def restore(self, mod, backup=None) -> dict:
        """Restore a module's config.json from a backup (latest by default)."""
        backs = self.backups(mod)
        if not backs:
            raise FileNotFoundError(f'no backups for {mod!r}')
        src = backup or backs[0]
        if src not in backs:
            raise FileNotFoundError(f'{src!r} is not a backup of {mod!r}')
        p = self.path(mod)
        with open(src) as f:
            cfg = json.load(f)          # refuse to restore a corrupt backup
        self._write(p, cfg)
        return {'mod': mod, 'restored_from': src, 'path': p}

    # --- lint -------------------------------------------------------------------

    def lint(self, mod=None) -> dict:
        """Fleet health report: invalid JSON, missing name/description,
        name↔directory mismatches, duplicate module names, and modules that
        claim the same port. With `mod`, only that module's issues."""
        rows = [self._row(p) for p in self._walk()]
        if mod is not None:
            rows = [r for r in rows if mod in (r['name'], r['dir'])]
        issues = []
        for r in rows:
            where = {'mod': r['name'], 'path': r['path']}
            if not r['valid']:
                issues.append(dict(where, level='error',
                                   issue=f'invalid JSON: {r["error"]}'))
                continue
            cfg, _ = self._read(r['path'])
            if not cfg.get('name'):
                issues.append(dict(where, level='warn', issue='missing "name"'))
            if not (cfg.get('description') or '').strip():
                issues.append(dict(where, level='warn', issue='missing "description"'))
            if cfg.get('name') and cfg['name'] != r['dir']:
                issues.append(dict(where, level='warn',
                                   issue=f'name {cfg["name"]!r} != directory {r["dir"]!r}'))
        # cross-module checks only make sense over the whole fleet
        if mod is None:
            seen = {}
            for r in rows:
                if r['valid']:
                    seen.setdefault(r['name'], []).append(r['path'])
            for name, paths in seen.items():
                if len(paths) > 1:
                    issues.append({'mod': name, 'level': 'error', 'path': paths[0],
                                   'issue': f'duplicate module name in {len(paths)} places: '
                                            + ', '.join(paths)})
            for port, owners in self._port_map(rows).items():
                # same module aliasing its own port (app_port == gateway_port) is fine
                mods_on_port = sorted({o['mod'] for o in owners})
                if len(mods_on_port) > 1:
                    issues.append({'mod': ', '.join(mods_on_port),
                                   'level': 'error', 'path': owners[0]['path'],
                                   'issue': f'port {port} claimed by {len(mods_on_port)} modules: '
                                            + ', '.join(f'{o["mod"]}.{o["key"]}' for o in owners)})
        issues.sort(key=lambda i: (i['level'] != 'error', i['mod']))
        return {'checked': len(rows),
                'errors': sum(1 for i in issues if i['level'] == 'error'),
                'warnings': sum(1 for i in issues if i['level'] == 'warn'),
                'issues': issues}

    @staticmethod
    def _port_map(rows) -> dict:
        ports = {}
        for r in rows:
            for k, v in (r.get('ports') or {}).items():
                ports.setdefault(v, []).append({'mod': r['name'], 'key': k,
                                                'path': r['path']})
        return ports

    def ports(self) -> dict:
        """Every port claimed in a config (*_port keys), who claims it, and
        which ports collide."""
        rows = [self._row(p) for p in self._walk()]
        pmap = self._port_map(rows)
        table = [{'port': port,
                  'owners': [f'{o["mod"]}.{o["key"]}' for o in owners],
                  'collision': len({o['mod'] for o in owners}) > 1}
                 for port, owners in sorted(pmap.items())]
        return {'count': len(table),
                'collisions': sum(1 for t in table if t['collision']),
                'ports': table}

    # --- git history ---------------------------------------------------------

    def _git(self, args, cwd):
        r = subprocess.run(['git'] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or 'git failed')
        return r.stdout

    def history(self, mod, n=10) -> list:
        """git log of a module's config.json, newest first."""
        p = self.path(mod)
        sep = '\x1f'
        out = self._git(['log', '-n', str(int(n)), f'--pretty=format:%h{sep}%an{sep}%aI{sep}%s',
                         '--follow', '--', p], cwd=os.path.dirname(p))
        rows = []
        for line in out.splitlines():
            if not line:
                continue
            sha, an, date, msg = (line.split(sep) + ['', '', '', ''])[:4]
            rows.append({'sha': sha, 'author': an, 'date': date, 'message': msg})
        return rows

    def diff(self, mod) -> str:
        """Uncommitted changes to a module's config.json."""
        p = self.path(mod)
        return self._git(['diff', 'HEAD', '--', p], cwd=os.path.dirname(p)) or '(clean)'

    # --- web app (zero-dep, read-only) ----------------------------------------

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Run the configs web app — a single-port, zero-dependency, read-only
        fleet browser (config grid + JSON viewer, port map, lint report) at /
        with a JSON API at /api/*. background=True detaches and returns the URL."""
        port = int(port)
        if background:
            self.kill(port)
            # keep logs under the state dir: a /tmp/configs dir would get
            # auto-anchored by the module tree and shadow this module
            log_dir = self.state_dir
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))
            env = dict(os.environ)
            env['PYTHONPATH'] = pkg_root + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', '-c',
                 f"import mod as m; m.mod('configs')()"
                 f".serve(port={port}, host={host!r}, background=False)"],
                stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            url = f'http://localhost:{port}'
            return {'running': True, 'pid': proc.pid, 'url': url,
                    'api': f'{url}/api/configs', 'log': os.path.join(log_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer((host, port), self._make_handler())
        print(f'configs app on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=APP_PORT):
        """Stop a running app (by pid file, then by port)."""
        killed = []
        pid_path = os.path.join(self.state_dir, 'app.pid')
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
        api = self

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
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            @staticmethod
            def _norm(p):
                # tolerate the gateway prefix whether or not Caddy strips it
                if p == '/configs' or p.startswith('/configs/'):
                    p = p[len('/configs'):] or '/'
                return p or '/'

            def do_GET(self):
                u = urlparse(self.path)
                u = u._replace(path=self._norm(u.path))
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path in ('/', '/index.html'):
                        return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                    if u.path == '/api/info':
                        return self._send(200, api.info())
                    if u.path == '/api/configs':
                        return self._send(200, api.configs(search=q.get('search')))
                    if u.path == '/api/config':
                        return self._send(200, api.get(q['mod']))
                    if u.path == '/api/ports':
                        return self._send(200, api.ports())
                    if u.path == '/api/lint':
                        return self._send(200, api.lint())
                    if u.path == '/api/history':
                        return self._send(200, api.history(q['mod'], n=int(q.get('n', 10))))
                    return self._send(404, {'error': 'not found'})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

        return H

    # --- meta -------------------------------------------------------------------

    def info(self) -> dict:
        rows = [self._row(p) for p in self._walk()]
        return {
            'name': 'configs',
            'description': self.description,
            'roots': self.roots,
            'modules': len(rows),
            'invalid': sum(1 for r in rows if not r['valid']),
            'state': self.state_dir,
            'app_port': APP_PORT,
        }


# --- embedded zero-dependency web UI (read-only) ------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>configs · fleet config manager</title>
<style>
  :root{
    --bg:#080a0f; --bg2:#0c1018; --panel:#10141e; --panel2:#161c2a; --line:#1f2636;
    --line2:#2a3346; --text:#eef1f7; --muted:#8b94a8; --faint:#5b647a;
    --accent:#38c793; --accent2:#5fe0b0; --warn:#ffb454; --err:#ff6b7a; --blue:#5b8cff;
    --r:14px;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;color:var(--text);
    font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial;
    background:
      radial-gradient(1100px 540px at 10% -8%, rgba(56,199,147,.12), transparent 60%),
      radial-gradient(900px 480px at 96% 0%, rgba(91,140,255,.09), transparent 55%),
      var(--bg);
    background-attachment:fixed}
  ::selection{background:rgba(56,199,147,.35)}
  a{color:inherit}
  header{position:sticky;top:0;z-index:20;
    background:linear-gradient(180deg,rgba(8,10,15,.92),rgba(8,10,15,.72));
    backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding:13px 22px}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .brand{display:flex;align-items:baseline;gap:9px;font-weight:800;font-size:17px}
  .brand .logo{font-size:18px}
  .brand .dot{color:var(--accent)}
  .sub{color:var(--muted);font-size:12px}
  .grow{flex:1}
  .seg{display:inline-flex;background:var(--panel);border:1px solid var(--line);
    border-radius:999px;padding:3px;gap:2px}
  .seg button{all:unset;cursor:pointer;padding:6px 16px;border-radius:999px;font-size:13px;
    font-weight:600;color:var(--muted);transition:.15s;display:flex;align-items:center;gap:7px}
  .seg button .n{font-size:11px;color:var(--faint);background:var(--panel2);
    border-radius:999px;padding:0 7px;line-height:17px}
  .seg button.on{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#04160e;
    box-shadow:0 4px 14px rgba(56,199,147,.35)}
  .seg button.on .n{color:#04160e;background:rgba(255,255,255,.35)}
  input{font:inherit;color:var(--text);background:var(--panel2);
    border:1px solid var(--line);border-radius:10px;padding:8px 12px;outline:none;min-width:220px}
  input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(56,199,147,.15)}
  input::placeholder{color:var(--faint)}
  main{max-width:1120px;margin:0 auto;padding:22px 22px 90px}
  .view{display:none}.view.on{display:block;animation:fade .25s ease}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  /* config grid */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:13px}
  .card{display:flex;flex-direction:column;gap:9px;cursor:pointer;min-height:120px;
    background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r);padding:15px;transition:.15s}
  .card:hover{border-color:var(--accent);transform:translateY(-2px);
    box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .card.bad{border-color:rgba(255,107,122,.5)}
  .card .top{display:flex;align-items:center;gap:10px}
  .card .ic{width:30px;height:30px;border-radius:9px;flex:none;display:grid;place-items:center;
    font-weight:800;color:#fff;font-size:14px}
  .card .nm{font-weight:700;font-size:15px;flex:1;min-width:0;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .card .d{color:var(--muted);font-size:12px;line-height:1.5;flex:1;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .card .foot{display:flex;align-items:center;gap:7px;margin-top:auto;flex-wrap:wrap}
  .chip{font-size:10px;font-weight:700;letter-spacing:.3px;padding:2px 8px;border-radius:999px;
    background:var(--panel2);border:1px solid var(--line);color:var(--muted)}
  .chip.core{color:var(--blue);border-color:rgba(91,140,255,.4)}
  .chip.orbit{color:var(--accent2);border-color:rgba(56,199,147,.4)}
  .chip.err{color:var(--err);border-color:rgba(255,107,122,.4)}
  .chip.port{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  /* detail overlay */
  .overlay{position:fixed;inset:0;z-index:40;display:none;align-items:flex-start;
    justify-content:center;padding:6vh 18px;background:rgba(4,6,10,.66);backdrop-filter:blur(6px)}
  .overlay.on{display:flex}
  .sheet{width:min(760px,100%);max-height:86vh;display:flex;flex-direction:column;
    background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line2);border-radius:16px;box-shadow:0 30px 90px rgba(0,0,0,.6)}
  .sheet .hd{display:flex;align-items:center;gap:11px;padding:15px 18px;
    border-bottom:1px solid var(--line)}
  .sheet .hd .nm{font-weight:800;font-size:16px;flex:1}
  .sheet .hd .x{all:unset;cursor:pointer;color:var(--muted);font-size:18px;padding:2px 8px;
    border-radius:8px}
  .sheet .hd .x:hover{color:var(--text);background:var(--panel2)}
  .sheet .path{color:var(--faint);font-size:11px;padding:8px 18px 0;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
  .sheet pre{margin:10px 18px 18px;padding:14px;overflow:auto;flex:1;
    background:#0a0d14;border:1px solid var(--line);border-radius:11px;
    font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
  .k{color:#7aa2ff}.s{color:#7ee2b8}.num{color:#ffb454}.b{color:#ff6b9d}
  /* tables */
  table{width:100%;border-collapse:collapse;background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
  th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);font-size:13px}
  th{color:var(--muted);font-size:11px;letter-spacing:.6px;text-transform:uppercase;
    background:var(--panel2)}
  tr:last-child td{border-bottom:none}
  td.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  tr.bad td{background:rgba(255,107,122,.07)}
  tr.bad td.mono{color:var(--err);font-weight:700}
  .tag{font-size:10px;font-weight:800;letter-spacing:.4px;padding:2px 8px;border-radius:6px}
  .tag.ok{color:#062611;background:var(--accent)}
  .tag.err{color:#2b0509;background:var(--err)}
  .tag.warn{color:#2b1a05;background:var(--warn)}
  /* lint list */
  .issue{display:flex;gap:12px;align-items:flex-start;padding:12px 15px;margin-bottom:10px;
    background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r)}
  .issue .body{flex:1;min-width:0}
  .issue .mod{font-weight:700}
  .issue .what{color:var(--muted);font-size:12.5px;margin-top:3px;word-break:break-word}
  .issue .where{color:var(--faint);font-size:11px;margin-top:4px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
  .empty,.errmsg{color:var(--muted);text-align:center;padding:54px 0}
  .errmsg{color:var(--err)}
  .skeleton{height:70px;border-radius:var(--r);
    background:linear-gradient(100deg,var(--panel) 30%,var(--panel2) 50%,var(--panel) 70%);
    background-size:200% 100%;animation:sh 1.2s infinite}
  @keyframes sh{to{background-position:-200% 0}}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><span class="logo">🧩</span>configs<span class="dot">.</span></div>
    <div class="seg">
      <button id="tab-configs" class="on" onclick="setView('configs')">Configs <span class="n" id="n-configs">·</span></button>
      <button id="tab-ports" onclick="setView('ports')">Ports <span class="n" id="n-ports">·</span></button>
      <button id="tab-lint" onclick="setView('lint')">Lint <span class="n" id="n-lint">·</span></button>
    </div>
    <span class="sub" id="status">loading…</span>
    <span class="grow"></span>
    <input id="q" placeholder="search modules…"/>
  </div>
</header>
<main>
  <div class="view on" id="view-configs"><div class="grid" id="configs">
    <div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div>
  <div class="view" id="view-ports"><div id="ports"><div class="skeleton"></div></div></div>
  <div class="view" id="view-lint"><div id="lint"><div class="skeleton"></div></div></div>
</main>
<div class="overlay" id="overlay" onclick="if(event.target===this)closeSheet()">
  <div class="sheet">
    <div class="hd"><div class="ic card-ic" id="s-ic"></div><div class="nm" id="s-nm"></div>
      <button class="x" onclick="closeSheet()">✕</button></div>
    <div class="path" id="s-path"></div>
    <pre id="s-json"></pre>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
const BASE = location.pathname.replace(/\/+$/,'').replace(/\/index\.html$/,'');
const api = p => BASE + p;
let VIEW='configs', ROWS=null, PORTS=null, LINT=null, Q='';

function esc(s){return (''+(s??'')).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function hue(s){let h=0;for(let i=0;i<(s||'').length;i++)h=(h*31+s.charCodeAt(i))%360;return h}
function grad(s){const h=hue(s);return `linear-gradient(135deg,hsl(${h} 70% 55%),hsl(${(h+40)%360} 70% 45%))`}
function initials(s){return (s||'?').replace(/[^a-z0-9]/gi,'').slice(0,2).toUpperCase()}

function setView(v){
  VIEW=v;
  for(const t of ['configs','ports','lint']){
    $('#tab-'+t).classList.toggle('on',v===t);
    $('#view-'+t).classList.toggle('on',v===t);
  }
  if(v==='ports'&&PORTS===null) loadPorts();
  if(v==='lint'&&LINT===null) loadLint();
  status();
}
$('#q').addEventListener('input',()=>{Q=$('#q').value;render()});

async function boot(){
  try{
    const r=await(await fetch(api('/api/configs'))).json();
    ROWS=r.configs||[];
    $('#n-configs').textContent=r.count;
    render(); status();
  }catch(e){$('#configs').innerHTML=`<div class="errmsg">${esc(e)}</div>`}
}
function status(){
  if(VIEW==='configs'&&ROWS)
    $('#status').innerHTML=`<b>${ROWS.length}</b> module configs`+
      (ROWS.filter(r=>!r.valid).length?` · <span style="color:var(--err)">${ROWS.filter(r=>!r.valid).length} invalid</span>`:'');
  if(VIEW==='ports'&&PORTS)
    $('#status').innerHTML=`<b>${PORTS.count}</b> ports claimed · `+
      (PORTS.collisions?`<span style="color:var(--err)">${PORTS.collisions} collisions</span>`:'no collisions');
  if(VIEW==='lint'&&LINT)
    $('#status').innerHTML=`checked <b>${LINT.checked}</b> · `+
      `<span style="color:var(--err)">${LINT.errors} errors</span> · `+
      `<span style="color:var(--warn)">${LINT.warnings} warnings</span>`;
}
function match(r){
  if(!Q) return true; const q=Q.toLowerCase();
  return r.name.toLowerCase().includes(q)||(r.description||'').toLowerCase().includes(q)
    ||(r.dir||'').toLowerCase().includes(q);
}
function render(){
  if(!ROWS) return;
  const rows=ROWS.filter(match);
  if(!rows.length){$('#configs').innerHTML='<div class="empty">no configs match</div>';return}
  $('#configs').innerHTML=rows.map(r=>{
    const ports=Object.values(r.ports||{});
    return `<div class="card ${r.valid?'':'bad'}" onclick="openSheet('${esc(r.name)}')">
      <div class="top">
        <div class="ic" style="background:${grad(r.name)}">${r.icon?esc(r.icon):initials(r.name)}</div>
        <div class="nm">${esc(r.name)}</div>
      </div>
      <div class="d">${esc(r.description)||'<span style="color:var(--faint)">no description</span>'}</div>
      <div class="foot">
        <span class="chip ${r.orbit}">${r.orbit.toUpperCase()}</span>
        ${r.version?`<span class="chip">v${esc(r.version)}</span>`:''}
        ${ports.map(p=>`<span class="chip port">:${p}</span>`).join('')}
        ${r.valid?'':'<span class="chip err">INVALID</span>'}
      </div>
    </div>`}).join('');
}

/* -------- detail sheet: pretty JSON -------- */
function hi(v,ind){
  const pad='  '.repeat(ind);
  if(v===null) return '<span class="b">null</span>';
  if(typeof v==='boolean') return `<span class="b">${v}</span>`;
  if(typeof v==='number') return `<span class="num">${v}</span>`;
  if(typeof v==='string') return `<span class="s">"${esc(v)}"</span>`;
  if(Array.isArray(v)){
    if(!v.length) return '[]';
    return '[\n'+v.map(x=>pad+'  '+hi(x,ind+1)).join(',\n')+'\n'+pad+']';
  }
  const ks=Object.keys(v);
  if(!ks.length) return '{}';
  return '{\n'+ks.map(k=>`${pad}  <span class="k">"${esc(k)}"</span>: ${hi(v[k],ind+1)}`).join(',\n')+'\n'+pad+'}';
}
async function openSheet(name){
  $('#s-nm').textContent=name;
  $('#s-ic').style.cssText=`width:30px;height:30px;border-radius:9px;display:grid;place-items:center;font-weight:800;color:#fff;background:${grad(name)}`;
  $('#s-ic').textContent=initials(name);
  $('#s-path').textContent='…'; $('#s-json').innerHTML='';
  $('#overlay').classList.add('on');
  try{
    const r=await(await fetch(api('/api/config?mod='+encodeURIComponent(name)))).json();
    $('#s-path').textContent=r.path||'';
    $('#s-json').innerHTML=r.error?`<span class="b">${esc(r.error)}</span>`:hi(r.config,0);
  }catch(e){$('#s-json').textContent=''+e}
}
function closeSheet(){$('#overlay').classList.remove('on')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSheet()});

/* -------- ports -------- */
async function loadPorts(){
  try{
    PORTS=await(await fetch(api('/api/ports'))).json();
    $('#ports').innerHTML=`<table><tr><th>port</th><th>claimed by</th><th></th></tr>`+
      PORTS.ports.map(p=>`<tr class="${p.collision?'bad':''}">
        <td class="mono">${p.port}</td>
        <td>${p.owners.map(esc).join(', ')}</td>
        <td>${p.collision?'<span class="tag err">COLLISION</span>':'<span class="tag ok">OK</span>'}</td>
      </tr>`).join('')+`</table>`;
    status();
  }catch(e){$('#ports').innerHTML=`<div class="errmsg">${esc(e)}</div>`}
}

/* -------- lint -------- */
async function loadLint(){
  try{
    LINT=await(await fetch(api('/api/lint'))).json();
    if(!LINT.issues.length){$('#lint').innerHTML='<div class="empty">✨ fleet is clean</div>';status();return}
    $('#lint').innerHTML=LINT.issues.map(i=>`<div class="issue">
      <span class="tag ${i.level==='error'?'err':'warn'}">${i.level.toUpperCase()}</span>
      <div class="body">
        <div class="mod">${esc(i.mod)}</div>
        <div class="what">${esc(i.issue)}</div>
        <div class="where">${esc(i.path)}</div>
      </div>
    </div>`).join('');
    status();
  }catch(e){$('#lint').innerHTML=`<div class="errmsg">${esc(e)}</div>`}
}

boot();
</script>
</body>
</html>
"""
