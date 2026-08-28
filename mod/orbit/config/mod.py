"""
config — the fleet's config, pretty.

The web face for core/config's `Config` class: a dict with attribute access
and a clean aligned tree repr. This module renders any module's config.json
(and any JSON you paste) through that repr, structured so the app can add
collapse, type colors, and click-to-copy dot-paths on top of the exact text
the library prints.

The app (zero-dependency, one port, read-only) has three views: FLEET — every
module's config rendered as the Config tree; PLAYGROUND — paste JSON, see its
tree live; ABOUT — the library's doc and source. Fleet data reuses
orbit/configs' scanner; edits stay on the configs CLI.

CLI:
    m config                        # this module's own config, as the tree
    m config/get configs            # any module's config as the Config repr
    m config/render '{"a": 1}'     # any JSON as the tree
    m config/source                 # the Config class source
    m config/serve                  # the app on :50240 (modc2.com/config)
"""
import json
import os
import subprocess
import sys

# when run as a script (serve subprocess), sys.path[0] is this dir and this
# very file would shadow the mod SDK — pin the package root ahead of it
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

import mod as m
from mod.core.config.config import Config

APP_PORT = 50240
LIB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'core', 'config', 'config.py')


class Mod:
    description = ("The config app: core/config's Config tree repr as a web "
                   "app — every fleet config.json rendered pretty, plus a "
                   "paste-any-JSON playground")

    def __init__(self, state_path=None):
        self.pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        self.state_dir = m.abspath(state_path or '~/.mod/config')
        self._fleet = None

    # --- fleet (reuses orbit/configs' scanner) ---------------------------------

    @property
    def fleet(self):
        if self._fleet is None:
            self._fleet = m.mod('configs')()
        return self._fleet

    def _id(self, config_path):
        """Stable module id from its config.json path: 'core/config',
        'orbit/configs', 'core/server/activator'…"""
        return os.path.relpath(os.path.dirname(config_path), self.pkg_root)

    def modules(self, search=None, n=500) -> dict:
        """Every fleet module (one row each, via orbit/configs) with a stable
        `id` this module's get() accepts. `search` filters name/description."""
        out = self.fleet.configs(search=search, n=n)
        for row in out['configs']:
            row['id'] = self._id(row['path'])
        return out

    def forward(self, **kwargs):
        return self.get('orbit/config', **kwargs)

    # --- render ------------------------------------------------------------------

    @staticmethod
    def _lines(cfg, indent=0, path=''):
        """The structured twin of Config._fmt: one entry per printed line,
        same grouping (leaves first, aligned) and same order, plus the type
        and dot-path the app needs for colors and click-to-copy."""
        out = []
        leaves = [(k, v) for k, v in cfg.items() if not isinstance(v, dict)]
        nested = [(k, v) for k, v in cfg.items() if isinstance(v, dict)]
        if leaves:
            w = max(len(str(k)) for k, _ in leaves)
            for k, v in leaves:
                p = f'{path}.{k}' if path else str(k)
                t = 'none' if v is None else type(v).__name__
                out.append({'i': indent, 'k': str(k), 'kp': str(k).ljust(w),
                            'v': str(v), 't': t, 'p': p})
        for k, v in nested:
            p = f'{path}.{k}' if path else str(k)
            out.append({'i': indent, 'k': str(k), 'kp': str(k), 'v': None,
                        't': 'dict', 'p': p, 'n': len(v)})
            out.extend(Mod._lines(v, indent + 1, p))
        return out

    def render(self, data=None, mod=None) -> dict:
        """A dict (or JSON string, or a module name via `mod`) as the Config
        tree: `text` is the library's exact repr, `lines` its structured twin."""
        if mod is not None:
            return self.get(mod)
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError('render wants a dict (or a JSON object string)')
        return {'text': str(Config.from_dict(data)), 'lines': self._lines(data)}

    def get(self, mod, key=None) -> dict:
        """One module's config rendered as the Config tree. `mod` is an id
        from modules() ('orbit/configs'), a module name, or a dir name; with
        two modules sharing a name, ids disambiguate."""
        want = str(mod).strip().strip('/')
        rows = self.modules(n=10000)['configs']
        hit = (next((r for r in rows if r['id'] == want), None)
               or next((r for r in rows if r['name'] == want), None)
               or next((r for r in rows if r['dir'] == want), None))
        if hit is None:
            raise FileNotFoundError(f'no module {mod!r} in the fleet')
        with open(hit['path']) as f:
            cfg = json.load(f)
        if key is not None:
            cur = cfg
            for part in str(key).split('.'):
                if not isinstance(cur, dict) or part not in cur:
                    raise KeyError(f'{key!r} not in {mod} config')
                cur = cur[part]
            cfg = cur if isinstance(cur, dict) else {key.split('.')[-1]: cur}
        return {'id': hit['id'], 'name': hit['name'], 'path': hit['path'],
                'config': cfg, 'text': str(Config.from_dict(cfg)),
                'lines': self._lines(cfg)}

    # --- library ------------------------------------------------------------------

    def source(self) -> dict:
        """The Config class: its module docstring and full source."""
        src = open(LIB_PATH).read()
        doc = ''
        if src.lstrip().startswith('"""'):
            body = src.lstrip()[3:]
            doc = body[:body.index('"""')].strip()
        return {'path': LIB_PATH, 'doc': doc, 'source': src}

    def info(self) -> dict:
        return {
            'name': 'config',
            'description': self.description,
            'lib': LIB_PATH,
            'app_port': APP_PORT,
            'url': 'https://modc2.com/config',
            'fns': ['modules', 'get', 'render', 'source', 'serve', 'kill'],
        }

    # --- web app (zero-dep, read-only) ---------------------------------------------

    def serve(self, port=APP_PORT, host='0.0.0.0', background=True):
        """Run the config app — FLEET tree browser, JSON playground, library
        doc — at / with a JSON API at /api/*. background=True detaches."""
        port = int(port)
        if background:
            self.kill(port)
            os.makedirs(self.state_dir, exist_ok=True)
            logf = open(os.path.join(self.state_dir, 'app.log'), 'w')
            env = dict(os.environ)
            env['PYTHONPATH'] = self.pkg_root + (
                ':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            proc = subprocess.Popen(
                ['python3', os.path.abspath(__file__),
                 '--port', str(port), '--host', host],
                stdout=logf, stderr=subprocess.STDOUT, env=env,
                start_new_session=True)
            with open(os.path.join(self.state_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            url = f'http://localhost:{port}'
            return {'running': True, 'pid': proc.pid, 'url': url,
                    'api': f'{url}/api/modules',
                    'log': os.path.join(self.state_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer((host, port), self._make_handler())
        print(f'config app on http://{host}:{port}')
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
                if p == '/config' or p.startswith('/config/'):
                    p = p[len('/config'):] or '/'
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
                    if u.path == '/api/modules':
                        return self._send(200, api.modules(search=q.get('search')))
                    if u.path == '/api/config':
                        return self._send(200, api.get(q['id']))
                    if u.path == '/api/source':
                        return self._send(200, api.source())
                    return self._send(404, {'error': 'not found'})
                except FileNotFoundError as e:
                    return self._send(404, {'error': str(e)})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

            def do_POST(self):
                u = urlparse(self.path)
                u = u._replace(path=self._norm(u.path))
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                    body = _json.loads(self.rfile.read(n) or b'{}')
                    if u.path == '/api/render':
                        return self._send(200, api.render(data=body.get('data')))
                    return self._send(404, {'error': 'not found'})
                except (ValueError, KeyError) as e:
                    return self._send(400, {'error': str(e)})
                except Exception as e:
                    return self._send(500, {'error': str(e)})

        return H


# --- embedded zero-dependency web UI (read-only) --------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>config · every config, pretty</title>
<style>
  :root{
    --bg:#0a0910; --panel:#131120; --panel2:#191630; --line:#241f3d;
    --line2:#332c55; --text:#efedf7; --muted:#9a92b8; --faint:#655c8a;
    --accent:#8b7cf6; --accent2:#b3a8ff; --green:#38c793; --amber:#ffb454;
    --err:#ff6b7a; --blue:#5b8cff; --r:14px;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;color:var(--text);
    font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial;
    background:
      radial-gradient(1100px 540px at 12% -8%, rgba(139,124,246,.14), transparent 60%),
      radial-gradient(900px 480px at 95% 0%, rgba(91,140,255,.08), transparent 55%),
      var(--bg);
    background-attachment:fixed}
  ::selection{background:rgba(139,124,246,.4)}
  header{position:sticky;top:0;z-index:20;
    background:linear-gradient(180deg,rgba(10,9,16,.94),rgba(10,9,16,.74));
    backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding:12px 20px}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .brand{display:flex;align-items:baseline;gap:9px;font-weight:800;font-size:17px}
  .brand .dot{color:var(--accent)}
  .sub{color:var(--muted);font-size:12px}
  .grow{flex:1}
  .seg{display:inline-flex;background:var(--panel);border:1px solid var(--line);
    border-radius:999px;padding:3px;gap:2px}
  .seg button{all:unset;cursor:pointer;padding:6px 15px;border-radius:999px;font-size:12.5px;
    font-weight:700;letter-spacing:.04em;color:var(--muted);transition:.15s}
  .seg button.on{background:var(--accent);color:#0b0817}
  main{max-width:1240px;margin:0 auto;padding:20px}
  .hide{display:none !important}

  /* fleet layout */
  .fleet{display:grid;grid-template-columns:290px 1fr;gap:16px;align-items:start}
  @media(max-width:840px){.fleet{grid-template-columns:1fr}}
  .rail{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
    overflow:hidden;position:sticky;top:76px;max-height:calc(100vh - 96px);
    display:flex;flex-direction:column}
  .rail input{all:unset;padding:11px 14px;border-bottom:1px solid var(--line);
    font-size:13px;color:var(--text);width:100%}
  .rail input::placeholder{color:var(--faint)}
  .mods{overflow-y:auto;padding:6px}
  .ghead{padding:8px 10px 3px;font-size:10.5px;font-weight:800;letter-spacing:.14em;
    color:var(--faint)}
  .mrow{display:flex;gap:9px;align-items:center;padding:7px 10px;border-radius:9px;
    cursor:pointer;font-size:13px}
  .mrow:hover{background:var(--panel2)}
  .mrow.on{background:var(--panel2);outline:1px solid var(--line2)}
  .mrow .ic{width:18px;text-align:center}
  .mrow .nm{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mrow .bad{margin-left:auto;font-size:10px;color:var(--err);font-weight:700}

  .panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
    overflow:hidden}
  .phead{display:flex;gap:12px;align-items:center;flex-wrap:wrap;
    padding:14px 18px;border-bottom:1px solid var(--line)}
  .phead .ic{font-size:22px}
  .phead .ttl{font-weight:800;font-size:16px}
  .chip{font:11.5px var(--mono);color:var(--muted);background:var(--panel2);
    border:1px solid var(--line);border-radius:999px;padding:3px 10px}
  .chip b{color:var(--accent2);font-weight:600}
  .btn{all:unset;cursor:pointer;font-size:12px;font-weight:700;color:var(--muted);
    border:1px solid var(--line);border-radius:8px;padding:5px 12px;transition:.15s}
  .btn:hover{color:var(--text);border-color:var(--line2)}
  .desc{padding:11px 18px;color:var(--muted);font-size:13px;border-bottom:1px solid var(--line)}

  /* the tree */
  .tree{font:13px/1.75 var(--mono);padding:14px 6px;overflow-x:auto}
  .ln{display:flex;white-space:pre;padding:0 12px;border-radius:6px;cursor:pointer}
  .ln:hover{background:var(--panel2)}
  .ln .car{width:16px;color:var(--faint);flex:none}
  .ln .k{color:var(--accent2)}
  .ln.dict .k{color:var(--text);font-weight:700}
  .ln .sep{color:var(--faint)}
  .v-str{color:#d7e3ff}.v-int,.v-float{color:var(--amber)}
  .v-bool{color:var(--green)}.v-none{color:var(--faint);font-style:italic}
  .v-list{color:#9ad0ff}
  .ln .cnt{color:var(--faint);font-size:11px;margin-left:8px}
  pre.raw{font:12.5px/1.6 var(--mono);margin:0;padding:16px 18px;overflow-x:auto;
    color:#d7e3ff}
  .empty{padding:40px;text-align:center;color:var(--faint)}

  /* playground */
  .play{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
  @media(max-width:840px){.play{grid-template-columns:1fr}}
  textarea{width:100%;min-height:380px;resize:vertical;background:var(--panel);
    color:#d7e3ff;border:1px solid var(--line);border-radius:var(--r);
    font:13px/1.6 var(--mono);padding:14px;outline:none}
  textarea:focus{border-color:var(--line2)}
  .perr{color:var(--err);font:12px var(--mono);padding:8px 4px;min-height:24px}

  /* about */
  .about{display:flex;flex-direction:column;gap:16px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
    padding:18px}
  .card h3{margin:0 0 8px;font-size:14px}
  .card pre{font:12.5px/1.6 var(--mono);margin:0;overflow-x:auto;color:#cfd8f0}
  .doc{font:12.5px/1.6 var(--mono);white-space:pre-wrap;color:var(--muted)}

  #toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(80px);
    background:var(--accent);color:#0b0817;font-weight:700;font-size:13px;
    padding:9px 18px;border-radius:999px;transition:.25s;z-index:50}
  #toast.on{transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><span>🌳</span><span>config<span class="dot">.</span></span>
      <span class="sub">every config, pretty — the Config tree, live</span></div>
    <div class="grow"></div>
    <div class="seg" id="tabs">
      <button data-t="fleet" class="on">FLEET</button>
      <button data-t="play">PLAYGROUND</button>
      <button data-t="about">ABOUT</button>
    </div>
  </div>
</header>
<main>
  <section id="t-fleet" class="fleet">
    <aside class="rail">
      <input id="q" placeholder="search modules…" autocomplete="off"/>
      <div class="mods" id="mods"></div>
    </aside>
    <div class="panel" id="detail"><div class="empty">loading fleet…</div></div>
  </section>

  <section id="t-play" class="play hide">
    <div>
      <textarea id="src" spellcheck="false"></textarea>
      <div class="perr" id="perr"></div>
    </div>
    <div class="panel">
      <div class="phead"><span class="ic">🌳</span><span class="ttl">tree</span>
        <div class="grow"></div><button class="btn" id="pcopy">copy text</button></div>
      <div class="tree" id="ptree"></div>
    </div>
  </section>

  <section id="t-about" class="about hide">
    <div class="card"><h3>core/config — the library this app wears</h3>
      <div class="doc" id="doc"></div></div>
    <div class="card"><h3>API</h3>
      <pre>GET  /config/api/modules?search=   fleet rows (id, name, ports…)
GET  /config/api/config?id=        one config → { config, text, lines }
POST /config/api/render {data}     any JSON object → { text, lines }
GET  /config/api/source            the Config class source</pre></div>
    <div class="card"><h3>source</h3><pre id="srccode"></pre></div>
  </section>
</main>
<div id="toast">copied</div>

<script>
const BASE = location.pathname.startsWith('/config') ? '/config' : '';
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api = p => fetch(BASE + p).then(r => r.json());

let MODS = [], CUR = null, TEXT = '', VIEW = 'tree', COLLAPSED = new Set();

// ── tabs ─────────────────────────────────────────────────────────────
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  ['fleet','play','about'].forEach(t => $('#t-' + t).classList.toggle('hide', t !== b.dataset.t));
});

// ── tree renderer ────────────────────────────────────────────────────
function treeHTML(lines, collapsed) {
  const stack = [], out = [];
  lines.forEach((l, idx) => {
    while (stack.length && stack[stack.length-1].i >= l.i) stack.pop();
    const hidden = stack.some(s => collapsed.has(s.idx));
    if (l.t === 'dict') stack.push({i: l.i, idx});
    if (hidden) return;
    const pad = '  '.repeat(l.i);
    if (l.t === 'dict') {
      const car = collapsed.has(idx) ? '▸' : '▾';
      out.push(`<div class="ln dict" data-idx="${idx}" data-p="${esc(l.p)}">` +
        `<span class="car">${car}</span><span>${pad}</span>` +
        `<span class="k">${esc(l.k)}</span><span class="cnt">${l.n} key${l.n===1?'':'s'}</span></div>`);
    } else {
      out.push(`<div class="ln" data-p="${esc(l.p)}"><span class="car"></span><span>${pad}</span>` +
        `<span class="k">${esc(l.kp)}</span><span class="sep"> : </span>` +
        `<span class="v-${l.t}">${esc(l.v)}</span></div>`);
    }
  });
  return out.join('') || '<div class="empty">empty config</div>';
}
function bindTree(el, lines, collapsed) {
  el.onclick = e => {
    const ln = e.target.closest('.ln'); if (!ln) return;
    if (ln.classList.contains('dict')) {
      const idx = +ln.dataset.idx;
      collapsed.has(idx) ? collapsed.delete(idx) : collapsed.add(idx);
      el.innerHTML = treeHTML(lines, collapsed);
    } else {
      navigator.clipboard.writeText('c.' + ln.dataset.p).then(() => toast('copied  c.' + ln.dataset.p));
    }
  };
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 1400);
}

// ── fleet ────────────────────────────────────────────────────────────
function railHTML(rows) {
  let html = '', grp = '';
  rows.forEach(r => {
    if (r.orbit !== grp) { grp = r.orbit; html += `<div class="ghead">${grp.toUpperCase()}</div>`; }
    html += `<div class="mrow${CUR === r.id ? ' on' : ''}" data-id="${esc(r.id)}">` +
      `<span class="ic">${r.icon || '▫️'}</span><span class="nm">${esc(r.name)}</span>` +
      (r.valid ? '' : '<span class="bad">bad json</span>') + `</div>`;
  });
  return html || '<div class="empty">no matches</div>';
}
async function loadMods(search) {
  const out = await api('/api/modules' + (search ? '?search=' + encodeURIComponent(search) : ''));
  MODS = out.configs;
  $('#mods').innerHTML = railHTML(MODS);
}
async function select(id) {
  CUR = id; COLLAPSED = new Set(); VIEW = 'tree';
  document.querySelectorAll('.mrow').forEach(x => x.classList.toggle('on', x.dataset.id === id));
  location.hash = id;
  const d = $('#detail');
  d.innerHTML = '<div class="empty">loading…</div>';
  let out;
  try { out = await api('/api/config?id=' + encodeURIComponent(id)); }
  catch (e) { d.innerHTML = `<div class="empty">${esc(e)}</div>`; return; }
  if (out.error) { d.innerHTML = `<div class="empty">${esc(out.error)}</div>`; return; }
  TEXT = out.text;
  const cfg = out.config, row = MODS.find(r => r.id === id) || {};
  const chips = [`<span class="chip">${esc(out.id)}/config.json</span>`];
  if (cfg.version) chips.push(`<span class="chip">v<b>${esc(cfg.version)}</b></span>`);
  for (const [k, v] of Object.entries(cfg))
    if (/(^|_)port$/.test(k) && String(v).match(/^\d+$/)) chips.push(`<span class="chip">${esc(k)} <b>${esc(v)}</b></span>`);
  d.innerHTML =
    `<div class="phead"><span class="ic">${cfg.icon || row.icon || '▫️'}</span>` +
    `<span class="ttl">${esc(out.name)}</span>${chips.join('')}<div class="grow"></div>` +
    `<button class="btn" id="vtoggle">JSON</button>` +
    `<button class="btn" id="vcopy">copy</button></div>` +
    (cfg.description ? `<div class="desc">${esc(cfg.description)}</div>` : '') +
    `<div class="tree" id="ftree">${treeHTML(out.lines, COLLAPSED)}</div>` +
    `<pre class="raw hide" id="fraw">${esc(JSON.stringify(cfg, null, 2))}</pre>`;
  bindTree($('#ftree'), out.lines, COLLAPSED);
  $('#vcopy').onclick = () => navigator.clipboard.writeText(
    VIEW === 'tree' ? TEXT : JSON.stringify(cfg, null, 2)).then(() => toast('copied ' + VIEW));
  $('#vtoggle').onclick = () => {
    VIEW = VIEW === 'tree' ? 'json' : 'tree';
    $('#vtoggle').textContent = VIEW === 'tree' ? 'JSON' : 'TREE';
    $('#ftree').classList.toggle('hide', VIEW !== 'tree');
    $('#fraw').classList.toggle('hide', VIEW !== 'json');
  };
}
$('#mods').addEventListener('click', e => {
  const r = e.target.closest('.mrow'); if (r) select(r.dataset.id);
});
let qh; $('#q').addEventListener('input', e => {
  clearTimeout(qh); qh = setTimeout(() => loadMods(e.target.value.trim()), 200);
});

// ── playground ───────────────────────────────────────────────────────
const SAMPLE = {mod: '/root/mod/mod', lib: '/root/mod',
  orbit: {orbit: '/mod/orbit', core: '/mod/core', local: '.'}};
let PLINES = [], PCOLL = new Set(), PTEXT = '';
async function renderPlay() {
  $('#perr').textContent = '';
  let data;
  try { data = JSON.parse($('#src').value); }
  catch (e) { $('#perr').textContent = String(e.message || e); return; }
  const r = await fetch(BASE + '/api/render', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({data})});
  const out = await r.json();
  if (out.error) { $('#perr').textContent = out.error; return; }
  PLINES = out.lines; PCOLL = new Set(); PTEXT = out.text;
  $('#ptree').innerHTML = treeHTML(PLINES, PCOLL);
  bindTree($('#ptree'), PLINES, PCOLL);
}
let ph; $('#src').addEventListener('input', () => { clearTimeout(ph); ph = setTimeout(renderPlay, 300); });
$('#pcopy').onclick = () => navigator.clipboard.writeText(PTEXT).then(() => toast('copied text'));

// ── about ────────────────────────────────────────────────────────────
async function loadAbout() {
  const s = await api('/api/source');
  $('#doc').textContent = s.doc;
  $('#srccode').textContent = s.source;
}

// ── boot ─────────────────────────────────────────────────────────────
(async () => {
  $('#src').value = JSON.stringify(SAMPLE, null, 2);
  renderPlay(); loadAbout();
  await loadMods();
  const want = decodeURIComponent(location.hash.slice(1)) || 'orbit/config';
  select(MODS.some(r => r.id === want) ? want : (MODS[0] || {}).id);
})();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=APP_PORT)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args()
    Mod().serve(port=args.port, host=args.host, background=False)
