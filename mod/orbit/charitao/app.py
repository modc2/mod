"""charitao web app — zero-dependency dashboard + JSON API bridge.

Serves the dashboard UI at / and a JSON API at /api/* that calls straight into
the Mod fns. Tolerates the /charitao gateway prefix whether or not the router
strips it.
"""
import json
import subprocess
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PREFIX = '/charitao'
DIR = Path(__file__).resolve().parent
GITHUB_REPO = 'modc2/mod'
GITHUB_BRANCH = 'dev'
REPO_SUBDIR = 'mod/orbit/charitao'
GITHUB_URL = ('https://github.com/%s/tree/%s/%s'
              % (GITHUB_REPO, GITHUB_BRANCH, REPO_SUBDIR))

CODE_EXTS = {'.py', '.md', '.json'}
CODE_SKIP = {'__pycache__', '.pytest_cache', '.git', 'node_modules'}


def _code_files():
    """Browsable source files, module-relative (allowlist for /api/code)."""
    files = []
    for p in sorted(DIR.rglob('*')):
        if (p.is_file() and p.suffix in CODE_EXTS
                and not any(part in CODE_SKIP for part in p.parts)):
            files.append(str(p.relative_to(DIR)))
    return files


_git_cache = {'t': 0.0, 'data': None}


def _git_meta():
    """Branch + latest commit + last commit per file, straight from git.

    Cached 60s; returns {'error': ...} when the module isn't in a git repo
    (e.g. deployed from a tarball) so the frontend can degrade gracefully.
    """
    now = time.time()
    if _git_cache['data'] is not None and now - _git_cache['t'] < 60:
        return _git_cache['data']

    def run(*args):
        return subprocess.run(
            ['git', '-C', str(DIR), *args],
            capture_output=True, text=True, timeout=10).stdout.strip()

    try:
        branch = run('rev-parse', '--abbrev-ref', 'HEAD')
        if not branch:
            raise RuntimeError('not a git repo')
        log = run('log', '-n', '200',
                  '--format=%x01%h%x09%at%x09%an%x09%s', '--name-only', '--', '.')
        latest, files, cur = None, {}, None
        for line in log.split('\n'):
            if line.startswith('\x01'):
                short, at, author, msg = line[1:].split('\t', 3)
                cur = {'short': short, 'time': int(at),
                       'author': author, 'msg': msg}
                latest = latest or cur
            elif line.strip() and cur:
                rel = line.strip()
                if rel.startswith(REPO_SUBDIR + '/'):
                    rel = rel[len(REPO_SUBDIR) + 1:]
                files.setdefault(rel, cur)
        data = {'repo': GITHUB_REPO, 'branch': branch,
                'subdir': REPO_SUBDIR, 'latest': latest, 'files': files}
    except Exception as e:
        data = {'error': str(e)}
    _git_cache.update(t=now, data=data)
    return data


def _whitepaper():
    try:
        return (DIR / 'WHITEPAPER.md').read_text()
    except OSError:
        return '# charitao\n\nWHITEPAPER.md missing — see ' + GITHUB_URL


def make_handler(mod):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, default=str).encode() if ctype == 'application/json'
                else body.encode())
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        @staticmethod
        def _norm(p):
            if p == PREFIX or p.startswith(PREFIX + '/'):
                p = p[len(PREFIX):] or '/'
            return p or '/'

        def do_GET(self):
            u = urlparse(self.path)
            path = self._norm(u.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if path in ('/', '/index.html'):
                    return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                if path in ('/whitepaper', '/whitepaper/'):
                    return self._send(200, DOC_HTML, 'text/html; charset=utf-8')
                if path in ('/code', '/code/'):
                    return self._send(200, DOC_HTML, 'text/html; charset=utf-8')
                if path == '/api/whitepaper':
                    return self._send(200, {'markdown': _whitepaper(),
                                            'github': GITHUB_URL})
                if path == '/api/code':
                    f = q.get('f')
                    if not f:
                        return self._send(200, {'files': _code_files(),
                                                'github': GITHUB_URL})
                    if f not in _code_files():
                        return self._send(404, {'error': 'no such file'})
                    text = (DIR / f).read_text()
                    if q.get('raw'):
                        return self._send(200, text,
                                          'text/plain; charset=utf-8')
                    return self._send(200, {'path': f, 'content': text,
                                            'size': (DIR / f).stat().st_size})
                if path == '/api/git':
                    return self._send(200, _git_meta())
                if path == '/api/info':
                    return self._send(200, mod.status())
                if path == '/api/status':
                    return self._send(200, mod.status())
                if path == '/api/charities':
                    return self._send(200, mod.charities(
                        verified_only=q.get('verified_only') in ('1', 'true')))
                if path == '/api/leaderboard':
                    return self._send(200, mod.leaderboard())
                if path == '/api/results':
                    ep = q.get('epoch')
                    return self._send(200, mod.results(epoch=ep) or {})
                if path == '/api/projection':
                    return self._send(200, mod.projection(
                        donation=float(q.get('donation', 1.0)),
                        emission=float(q['emission']) if q.get('emission') else None))
                if path == '/api/suggest':
                    return self._send(200, mod.suggest_donation(
                        expected_donors=int(q.get('donors', 1))))
                return self._send(404, {'error': 'not found'})
            except Exception as e:
                return self._send(500, {'error': str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            path = self._norm(u.path)
            n = int(self.headers.get('Content-Length', 0) or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}') if n else {}
            except Exception:
                body = {}
            try:
                if path == '/api/simulate':
                    return self._send(200, mod.simulate(
                        n_miners=int(body.get('n_miners', 3)),
                        epochs=int(body.get('epochs', 1)),
                        amount=float(body['amount']) if body.get('amount') else None))
                if path == '/api/donate':
                    return self._send(200, mod.donate(
                        uid=int(body.get('uid', 0)),
                        amount=float(body.get('amount', 0.1)),
                        charity=body.get('charity')))
                if path == '/api/epoch':
                    return self._send(200, mod.epoch())
                if path == '/api/add_charity':
                    return self._send(200, mod.add_charity(
                        id=body['id'], name=body['name'], address=body['address'],
                        cause=body.get('cause', ''), url=body.get('url', ''),
                        links=body.get('links'), audits=body.get('audits'),
                        verified=bool(body.get('verified', False))))
                if path == '/api/flag_charity':
                    return self._send(200, mod.flag_charity(
                        id=body['id'], legit=body.get('legit')))
                if path == '/api/reset':
                    return self._send(200, mod.reset())
                return self._send(404, {'error': 'not found'})
            except Exception as e:
                return self._send(500, {'error': str(e)})

    return H


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>charitao · proof-of-donation subnet</title>
<style>
  :root{
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --surface-2:#222220;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,.10);
    --series:#d55181; --good:#0ca30c; --warn:#fab219;
    --brand:#e8618c; --r:14px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--page);color:var(--ink);
    font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
  .wrap{max-width:1060px;margin:0 auto;padding:28px 20px 64px}
  header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
  h1{font-size:22px;margin:0;letter-spacing:.2px;display:flex;align-items:center;gap:9px}
  h1 .logo{width:22px;height:22px;flex:none;fill:var(--brand);
    filter:drop-shadow(0 0 8px rgba(232,97,140,.45))}
  .sub{color:var(--ink-2);margin:0 0 22px;max-width:72ch}
  .pill{font-size:11px;letter-spacing:.6px;color:var(--brand);
    border:1px solid var(--ring);border-radius:999px;padding:2px 10px}
  nav.top{margin-left:auto}
  nav.top a{color:var(--ink-2);text-decoration:none;font-size:12px;
    letter-spacing:.6px;text-transform:uppercase;margin-left:16px}
  nav.top a:hover{color:var(--brand)}
  .formula{color:var(--muted)}
  .formula b{color:var(--ink-2);font-weight:600}

  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:10px;margin-bottom:22px}
  .tile{background:var(--surface);border:1px solid var(--ring);
    border-radius:var(--r);padding:14px 16px}
  .tile .k{font-size:11px;letter-spacing:.6px;text-transform:uppercase;
    color:var(--muted);margin-bottom:6px}
  .tile .k .sym{text-transform:none}
  .tile .v{font-size:24px;font-weight:650;line-height:1.15}
  .tile .v small{font-size:13px;color:var(--muted);font-weight:400}
  .tile .v.good{color:var(--good)}

  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:840px){.grid{grid-template-columns:1fr}}
  .card{background:var(--surface);border:1px solid var(--ring);
    border-radius:var(--r);padding:18px 18px 16px;margin-bottom:14px}
  .card h2{font-size:13px;letter-spacing:.6px;text-transform:uppercase;
    color:var(--ink-2);margin:0 0 12px;font-weight:600}
  .card.full{grid-column:1/-1}

  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th{font-size:11px;text-transform:uppercase;letter-spacing:.5px;
    color:var(--muted);font-weight:500;text-align:right;padding:6px 8px;
    border-bottom:1px solid var(--grid)}
  th:first-child{text-align:left}
  td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--grid);
    color:var(--ink-2)}
  td:first-child{text-align:left;color:var(--ink)}
  tr:last-child td{border-bottom:0}
  tr:hover td{background:var(--surface-2)}
  .barcell{width:34%}
  .bar{display:flex;align-items:center;gap:8px;justify-content:flex-end}
  .bar i{display:block;height:10px;background:var(--series);
    border-radius:0 4px 4px 0;min-width:2px}
  .bar span{color:var(--ink-2);font-size:12px;min-width:64px;text-align:left}
  .profit{color:var(--good)}
  .empty{color:var(--muted);padding:14px 4px;font-size:13px}

  .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
  label{font-size:12px;color:var(--muted)}
  input{background:var(--page);border:1px solid var(--ring);color:var(--ink);
    border-radius:8px;padding:7px 10px;font:inherit;width:110px;
    font-variant-numeric:tabular-nums}
  input:focus{outline:1px solid var(--brand);border-color:var(--brand)}
  input.wide{width:170px}
  button{background:var(--brand);border:0;color:#140a0e;font:inherit;
    font-weight:600;border-radius:8px;padding:8px 16px;cursor:pointer}
  button:hover{filter:brightness(1.08)}
  button.ghost{background:transparent;color:var(--ink-2);
    border:1px solid var(--ring)}
  button.ghost:hover{background:var(--surface-2);filter:none}
  button:disabled{opacity:.5;cursor:default}

  .proj{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));
    gap:8px;margin-top:6px}
  .proj .cell{background:var(--surface-2);border-radius:10px;padding:10px 12px}
  .proj .k{font-size:10px;letter-spacing:.5px;text-transform:uppercase;
    color:var(--muted)}
  .proj .v{font-size:17px;font-weight:650;font-variant-numeric:tabular-nums}
  .proj .v.good{color:var(--good)}

  .chgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
  .chcard{background:var(--surface-2);border:1px solid var(--ring);
    border-radius:12px;padding:14px 15px 12px;display:flex;flex-direction:column;gap:8px}
  .chhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .cname{font-weight:650;font-size:15px}
  .ccause{color:var(--ink-2);font-size:12.5px;margin-top:-4px}
  .caddr{color:var(--muted);font-family:ui-monospace,Menlo,monospace;
    font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .badge{font-size:11px;border-radius:999px;padding:1px 9px;border:1px solid var(--ring)}
  .badge.ok{color:var(--good)}
  .badge.no{color:var(--warn)}
  .legit{font-size:11px;font-weight:650;border-radius:999px;padding:2px 10px;
    margin-left:auto;cursor:help;white-space:nowrap}
  .legit.l-legit{color:#0d0d0d;background:var(--good)}
  .legit.l-probably{color:var(--warn);border:1px solid var(--warn)}
  .legit.l-dyor{color:var(--warn);border:1px dashed var(--warn)}
  .legit.l-flagged{color:#fff;background:#c23b3b}
  .csec{font-size:10px;letter-spacing:.6px;text-transform:uppercase;
    color:var(--muted);margin-top:2px}
  .caudits{margin:0;padding:0;list-style:none;font-size:12.5px}
  .caudits li{padding:1px 0}
  .caudits a{color:var(--ink-2);text-decoration:none;border-bottom:1px dotted var(--muted)}
  .caudits a:hover{color:var(--ink);border-bottom-color:var(--ink)}
  .caudits .yr{color:var(--muted);font-variant-numeric:tabular-nums;margin-right:6px}
  .cnone{color:var(--muted);font-size:12px;font-style:italic}
  .clinks{display:flex;gap:6px;flex-wrap:wrap}
  .clinks a{font-size:11.5px;color:var(--ink-2);border:1px solid var(--ring);
    border-radius:999px;padding:2px 10px;text-decoration:none}
  .clinks a:hover{background:var(--surface);color:var(--ink);border-color:var(--brand)}
  .cfoot{display:flex;gap:10px;color:var(--muted);font-size:11px;
    margin-top:auto;padding-top:4px;border-top:1px solid var(--grid)}

  #toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
    background:var(--surface-2);border:1px solid var(--ring);color:var(--ink);
    border-radius:10px;padding:10px 18px;font-size:13px;opacity:0;
    transition:opacity .25s;pointer-events:none;max-width:80vw}
  #toast.show{opacity:1}
  footer{color:var(--muted);font-size:12px;margin-top:26px}
  footer code{color:var(--ink-2)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>charitao</h1>
    <span class="pill" id="mode">LOCAL SIM</span>
    <nav class="top" id="topnav"></nav>
  </header>
  <p class="sub">Proof-of-donation Bittensor subnet — miners donate TAO to
    whitelisted charities, validators verify on chain and set weights so
    <span class="formula">payout = donation / <b>ρ</b></span> at a fixed,
    guaranteed margin.</p>

  <div class="tiles" id="tiles"></div>

  <div class="grid">
    <div class="card">
      <h2>Projection — what a donation earns</h2>
      <div class="row">
        <label>donation τ <input id="pj-amt" type="number" step="0.1" min="0" value="1"></label>
        <button class="ghost" id="pj-go">Project</button>
      </div>
      <div class="proj" id="pj-out"></div>
    </div>

    <div class="card">
      <h2>Actions (local sim)</h2>
      <div class="row">
        <label>miners <input id="sim-n" type="number" min="1" value="3" style="width:64px"></label>
        <label>epochs <input id="sim-e" type="number" min="1" value="2" style="width:64px"></label>
        <button id="sim-go">Simulate</button>
      </div>
      <div class="row">
        <label>uid <input id="don-uid" type="number" min="0" value="0" style="width:64px"></label>
        <label>amount τ <input id="don-amt" type="number" step="0.05" min="0" value="0.2" style="width:84px"></label>
        <button class="ghost" id="don-go">Donate</button>
        <button class="ghost" id="ep-go">Run epoch</button>
        <button class="ghost" id="rst-go">Reset sim</button>
      </div>
      <div class="proj" id="ep-out"></div>
    </div>

    <div class="card full">
      <h2>Leaderboard</h2>
      <div id="lb"></div>
    </div>

    <div class="card full">
      <h2>Charity whitelist</h2>
      <div class="chgrid" id="chars"></div>
      <div class="row" style="margin-top:14px">
        <input class="wide" id="ch-id" placeholder="id">
        <input class="wide" id="ch-name" placeholder="name">
        <input class="wide" id="ch-addr" placeholder="address">
        <input class="wide" id="ch-url" placeholder="website (optional)">
        <button class="ghost" id="ch-go">Add charity</button>
      </div>
    </div>
  </div>

  <footer>CLI: <code>m charitao</code> · <code>m charitao/simulate</code> ·
    <code>m charitao/projection donation=1</code> · state in <code>~/.mod/charitao/</code></footer>
</div>
<div id="toast"></div>

<script>
const BASE = location.pathname.replace(/\/(index\.html)?$/,'');
document.getElementById('topnav').innerHTML =
  `<a href="${BASE}/whitepaper">Whitepaper</a>` +
  `<a href="${BASE}/code">Code</a>` +
  `<a href="https://github.com/modc2/mod/tree/dev/mod/orbit/charitao" target="_blank" rel="noopener">GitHub ↗</a>`;
const api = p => fetch(BASE + '/api/' + p).then(r => r.json());
const post = (p, body) => fetch(BASE + '/api/' + p, {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})})
  .then(r => r.json());
const $ = id => document.getElementById(id);
const fmt = (x, d=4) => Number(x).toLocaleString('en-US',
  {maximumFractionDigits:d, minimumFractionDigits:0});

let toastT;
function toast(msg){
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(()=>t.classList.remove('show'), 3500);
}

function tile(k, v, cls=''){
  return `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
}

async function loadStatus(){
  const s = await api('status');
  $('mode').textContent = s.local ? 'LOCAL SIM' : 'LIVE · ' + (s.network||'');
  $('tiles').innerHTML =
    tile('Epoch', s.epoch) +
    tile('Profit margin', '+' + fmt(s.margin_pct,2) + '<small> %</small>', 'good') +
    tile('Coverage ratio <span class="sym">ρ</span>', fmt(s.coverage_ratio,3)) +
    tile('Epoch emission', fmt(s.epoch_emission) + '<small> τ</small>') +
    tile('Carryover', fmt(s.carryover) + '<small> τ</small>') +
    tile('Charities', s.charities) +
    tile('Miners bound', s.miners_bound);
}

async function loadLeaderboard(){
  const rows = await api('leaderboard');
  if(!rows.length){
    $('lb').innerHTML = '<div class="empty">No miners yet — run a simulation or donate.</div>';
    return;
  }
  const max = Math.max(...rows.map(r => r.projected_payout), 1e-12);
  $('lb').innerHTML = `<table>
    <thead><tr><th>uid</th><th>donated τ</th><th>credited τ</th>
    <th>profit τ</th><th class="barcell">projected payout τ</th></tr></thead>
    <tbody>` + rows.map(r => `<tr>
      <td>${r.uid}</td>
      <td>${fmt(r.donated)}</td>
      <td>${fmt(r.credited)}</td>
      <td class="profit">+${fmt(r.projected_profit)}</td>
      <td class="barcell"><div class="bar">
        <i style="width:${Math.max(2, r.projected_payout/max*100)}%"></i>
        <span>${fmt(r.projected_payout)}</span></div></td>
    </tr>`).join('') + '</tbody></table>';
}

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const link = (label, url) =>
  `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;

function charityCard(c){
  const lg = c.legitimacy || {};
  const verdict = `<span class="legit l-${esc(lg.status||'dyor')}"
    title="${esc((lg.reasons||[]).join(' · '))}">${esc(lg.label||'?')}</span>`;
  const audits = (c.audits||[]).length
    ? `<ul class="caudits">` + c.audits.map(a => `<li>` +
        (a.year ? `<span class="yr">${esc(a.year)}</span>` : '') +
        (a.url ? link(a.label||'audit', a.url) : esc(a.label||'audit')) +
      `</li>`).join('') + `</ul>`
    : `<div class="cnone">no audits on file</div>`;
  const links = [
    ...(c.url ? [{label:'website', url:c.url}] : []),
    ...(c.links||[]),
  ];
  return `<div class="chcard">
    <div class="chhead">
      <span class="cname">${esc(c.name||c.id)}</span>
      <span class="badge ${c.verified?'ok':'no'}">${c.verified?'✓ verified':'unverified'}</span>
      ${verdict}
    </div>
    ${c.cause?`<div class="ccause">${esc(c.cause)}</div>`:''}
    <div class="caddr" title="${esc(c.address)}">${esc(c.address)}</div>
    <div class="csec">Audits &amp; financials</div>
    ${audits}
    <div class="csec">Info &amp; evaluators</div>
    ${links.length
      ? `<div class="clinks">` + links.map(l => link(l.label||l.url, l.url)).join('') + `</div>`
      : `<div class="cnone">no links — do your own research</div>`}
    <div class="cfoot">
      <span>network: ${esc(c.network||'?')}</span>
      ${c.added_at?`<span>added ${new Date(c.added_at*1000).toISOString().slice(0,10)}</span>`:''}
    </div>
  </div>`;
}

async function loadCharities(){
  const cs = await api('charities');
  $('chars').innerHTML = cs.length ? cs.map(charityCard).join('')
    : '<div class="empty">No charities whitelisted yet.</div>';
}

async function project(){
  const p = await api('projection?donation=' + (parseFloat($('pj-amt').value)||0));
  if(p.error) return toast(p.error);
  $('pj-out').innerHTML =
    `<div class="cell"><div class="k">payout</div><div class="v">${fmt(p.payout)} τ</div></div>
     <div class="cell"><div class="k">profit</div><div class="v good">+${fmt(p.profit)} τ</div></div>
     <div class="cell"><div class="k">margin</div><div class="v">${fmt(p.margin_pct,2)} %</div></div>
     <div class="cell"><div class="k">epochs to settle</div><div class="v">${p.epochs_to_settle}</div></div>`;
}

function showEpoch(r){
  if(!r || r.error) return toast(r && r.error || 'no result');
  const credited = Object.values(r.credited||{}).reduce((a,b)=>a+ +b, 0);
  $('ep-out').innerHTML =
    `<div class="cell"><div class="k">epoch</div><div class="v">${r.epoch}</div></div>
     <div class="cell"><div class="k">budget</div><div class="v">${fmt(r.budget)} τ</div></div>
     <div class="cell"><div class="k">credited</div><div class="v">${fmt(credited)} τ</div></div>
     <div class="cell"><div class="k">carryover</div><div class="v">${fmt(r.carryover)} τ</div></div>`;
}

async function refresh(){
  try{
    await Promise.all([loadStatus(), loadLeaderboard(), loadCharities()]);
  }catch(e){ toast('backend unreachable: ' + e.message); }
}

$('pj-go').onclick = project;
$('pj-amt').addEventListener('change', project);
$('sim-go').onclick = async () => {
  const r = await post('simulate', {n_miners: +$('sim-n').value, epochs: +$('sim-e').value});
  if(r.error) return toast(r.error);
  toast(`Simulated ${r.epochs_run} epoch(s) · ${r.miners} miners · ${fmt(r.per_miner_donation)} τ each`);
  if(r.last_epoch) showEpoch(r.last_epoch);
  refresh();
};
$('don-go').onclick = async () => {
  const r = await post('donate', {uid: +$('don-uid').value, amount: +$('don-amt').value});
  if(r.error) return toast(r.error);
  toast(`uid ${$('don-uid').value} donated ${$('don-amt').value} τ (credited next epoch)`);
  refresh();
};
$('ep-go').onclick = async () => { showEpoch(await post('epoch')); refresh(); };
$('rst-go').onclick = async () => {
  if(!confirm('Wipe local sim state?')) return;
  await post('reset'); toast('Sim state wiped'); $('ep-out').innerHTML=''; refresh();
};
$('ch-go').onclick = async () => {
  const [id, name, addr] = [$('ch-id').value.trim(), $('ch-name').value.trim(), $('ch-addr').value.trim()];
  if(!id || !name || !addr) return toast('id, name and address are required');
  const r = await post('add_charity', {id, name, address: addr, verified: true});
  if(r.error) return toast(r.error);
  toast(`Charity "${name}" added + verified`);
  $('ch-id').value = $('ch-name').value = $('ch-addr').value = '';
  refresh();
};

refresh(); project();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""




# /whitepaper and /code share this shell. /code is a GitHub-style repo
# browser (file tree + Go-to-file filter, last-commit-per-file metadata from
# /api/git, syntax highlighting, #f=path&L=n deep links, raw/copy/blob
# actions, rendered README landing) backed by /api/code + /api/git.
DOC_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>charitao · docs</title>
<style>
  :root{
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --surface-2:#222220;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --ring:rgba(255,255,255,.10);
    --good:#0ca30c; --brand:#e8618c; --r:14px;
    --tok-k:#ff7b72; --tok-s:#a5d6ff; --tok-c:#8b949e; --tok-n:#79c0ff;
    --tok-f:#d2a8ff; --tok-d:#d2a8ff; --tok-b:#ffa657; --tok-j:#7ee787;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--page);color:var(--ink);
    font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:28px 20px 64px}
  body[data-page="code"] .wrap{max-width:1180px}
  header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  h1.brand{font-size:22px;margin:0;display:flex;align-items:center;gap:9px}
  h1.brand .logo{width:22px;height:22px;flex:none;fill:var(--brand);
    filter:drop-shadow(0 0 8px rgba(232,97,140,.45))}
  nav{margin-left:auto}
  nav a{color:var(--ink-2);text-decoration:none;font-size:12px;
    letter-spacing:.6px;text-transform:uppercase;margin-left:16px}
  nav a:hover,nav a.on{color:var(--brand)}
  svg.oct{width:16px;height:16px;fill:currentColor;flex:none;vertical-align:-3px}

  .doc{background:var(--surface);border:1px solid var(--ring);
    border-radius:var(--r);padding:26px 30px}
  .doc.embed{border:0;border-radius:0;background:transparent;padding:22px 28px;max-width:860px}
  .doc h1{font-size:24px;margin:0 0 4px;line-height:1.25}
  .doc h2{font-size:18px;margin:28px 0 8px;padding-top:14px;border-top:1px solid var(--grid)}
  .doc h3{font-size:15px;margin:20px 0 6px;color:var(--ink-2)}
  .doc p,.doc li{color:var(--ink-2)}
  .doc strong{color:var(--ink)}
  .doc a{color:var(--brand)}
  .doc code{background:var(--surface-2);border-radius:5px;padding:1px 6px;
    font:12.5px var(--mono);color:var(--ink)}
  .doc pre{background:var(--page);border:1px solid var(--grid);border-radius:10px;
    padding:14px 16px;overflow:auto}
  .doc pre code{background:none;padding:0;display:block;line-height:1.55}
  .doc blockquote{margin:0;padding:2px 16px;border-left:3px solid var(--brand);color:var(--muted)}
  .doc table{border-collapse:collapse;margin:10px 0;font-variant-numeric:tabular-nums}
  .doc th,.doc td{border:1px solid var(--grid);padding:6px 12px;text-align:left;color:var(--ink-2)}
  .doc th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  .doc hr{border:0;border-top:1px solid var(--grid);margin:22px 0}

  /* ---- GitHub-style repo browser ---- */
  .repo{border:1px solid var(--ring);border-radius:12px;overflow:hidden;
    background:var(--surface)}
  .rhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
    padding:12px 16px;border-bottom:1px solid var(--grid);font-size:14px}
  .rhead .oct{color:var(--muted)}
  .rhead a.rrepo{color:var(--ink);font-weight:650;text-decoration:none}
  .rhead a.rrepo:hover{color:var(--brand)}
  .rsep{color:var(--muted)}
  .rpath{color:var(--ink-2);font-family:var(--mono);font-size:13px}
  .rbranch{display:inline-flex;align-items:center;gap:6px;margin-left:8px;
    font:12px var(--mono);color:var(--ink-2);background:var(--surface-2);
    border:1px solid var(--ring);border-radius:999px;padding:3px 12px}
  .ghbtn{margin-left:auto;display:inline-flex;align-items:center;gap:7px;
    color:var(--ink);text-decoration:none;font-size:12.5px;font-weight:600;
    background:var(--surface-2);border:1px solid var(--ring);
    border-radius:8px;padding:6px 13px}
  .ghbtn:hover{border-color:var(--brand);color:var(--brand)}
  .cbar{display:flex;align-items:center;gap:9px;padding:9px 16px;
    background:var(--surface-2);border-bottom:1px solid var(--grid);
    font-size:12.5px;color:var(--ink-2);min-width:0}
  .cbar .oct{color:var(--muted)}
  .cauthor{font-weight:650;color:var(--ink)}
  .cmsg{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
  .chash{font-family:var(--mono);color:var(--muted);text-decoration:none}
  .chash:hover{color:var(--brand)}
  .ctime{color:var(--muted);white-space:nowrap}

  .cols{display:grid;grid-template-columns:252px minmax(0,1fr);height:76vh;min-height:480px}
  .tree{border-right:1px solid var(--grid);overflow:auto;padding:10px 0 14px;
    font-size:13px}
  .tfindbox{padding:0 10px 8px}
  .tfind{width:100%;background:var(--page);border:1px solid var(--ring);
    color:var(--ink);border-radius:8px;padding:6px 10px;font:12.5px var(--mono)}
  .tfind:focus{outline:1px solid var(--brand);border-color:var(--brand)}
  .drow,.frow{display:flex;align-items:center;gap:7px;padding:4px 10px;
    cursor:pointer;color:var(--ink-2);text-decoration:none;user-select:none;
    white-space:nowrap}
  .drow:hover,.frow:hover{background:var(--surface-2);color:var(--ink)}
  .drow .oct.chev{width:12px;height:12px;transition:transform .12s;color:var(--muted)}
  .dir[data-open="0"] > .drow .chev{transform:rotate(0deg)}
  .dir[data-open="1"] > .drow .chev{transform:rotate(90deg)}
  .dir[data-open="0"] > .dkids{display:none}
  .drow .oct.fold{color:#7aa7ff}
  .frow .oct{color:var(--muted);width:14px;height:14px}
  .frow.on{background:var(--surface-2);color:var(--ink);
    box-shadow:inset 2px 0 0 var(--brand)}
  .frow.on .oct{color:var(--brand)}

  .pane{overflow:auto;background:var(--page)}
  .fhead{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:10px;
    flex-wrap:wrap;padding:10px 16px;background:var(--surface);
    border-bottom:1px solid var(--grid)}
  .fname{font:13px var(--mono);color:var(--ink)}
  .fname .dirpart{color:var(--muted)}
  .fmeta{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
  .facts{margin-left:auto;display:flex;gap:6px;align-items:center}
  .fbtn{display:inline-flex;align-items:center;gap:6px;background:transparent;
    border:1px solid var(--ring);color:var(--ink-2);font:12px inherit;
    font-family:inherit;border-radius:7px;padding:4px 10px;cursor:pointer;
    text-decoration:none}
  .fbtn:hover{color:var(--ink);border-color:var(--brand)}
  .fbtn .oct{width:13px;height:13px}
  .seg{display:inline-flex;border:1px solid var(--ring);border-radius:7px;overflow:hidden}
  .seg button{background:transparent;border:0;color:var(--muted);font:12px inherit;
    padding:4px 11px;cursor:pointer}
  .seg button.on{background:var(--surface-2);color:var(--ink)}
  .fcommit{display:flex;align-items:center;gap:8px;padding:7px 16px;
    font-size:12px;color:var(--muted);border-bottom:1px solid var(--grid);
    background:var(--surface);min-width:0}
  .fcommit .cmsg{color:var(--ink-2)}

  .src{padding:10px 0 24px;font:12.5px/1.6 var(--mono)}
  .src .ln{display:flex}
  .src .ln:hover{background:var(--surface-2)}
  .src .ln.hl{background:rgba(232,97,140,.13);box-shadow:inset 2px 0 0 var(--brand)}
  .src .n{color:var(--muted);text-align:right;min-width:52px;padding:0 14px 0 0;
    user-select:none;flex:none;cursor:pointer;text-decoration:none}
  .src .n:hover{color:var(--brand)}
  .src .c{white-space:pre;color:var(--ink-2);padding-right:20px}
  .tok-k{color:var(--tok-k)} .tok-s{color:var(--tok-s)}
  .tok-c{color:var(--tok-c);font-style:italic} .tok-n{color:var(--tok-n)}
  .tok-f{color:var(--tok-f)} .tok-d{color:var(--tok-d)}
  .tok-b{color:var(--tok-b)} .tok-j{color:var(--tok-j)}
  .muted{color:var(--muted)}
  @media(max-width:860px){
    .cols{grid-template-columns:1fr;height:auto}
    .tree{max-height:240px;border-right:0;border-bottom:1px solid var(--grid)}
    .pane{max-height:70vh}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 class="brand"><svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>charitao</h1>
    <nav id="nav"></nav>
  </header>
  <div id="body" class="muted">loading…</div>
</div>
<script>
const PAGE = /\/code\/?$/.test(location.pathname) ? 'code' : 'whitepaper';
document.body.dataset.page = PAGE;
const BASE = location.pathname.replace(/\/(whitepaper|code)\/?$/,'');
const api = p => fetch(BASE + '/api/' + p).then(r => r.json());
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const $ = id => document.getElementById(id);

const OCT = {
  github:'M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.27-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z',
  branch:'M9.5 3.25a2.25 2.25 0 1 1 3 2.122V6A2.5 2.5 0 0 1 10 8.5H6a1 1 0 0 0-1 1v1.128a2.251 2.251 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.5 0v1.836A2.493 2.493 0 0 1 6 7h4a1 1 0 0 0 1-1v-.628A2.25 2.25 0 0 1 9.5 3.25Zm-6 0a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Zm8.25-.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM4.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z',
  commit:'M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 0-1.5h3.32a4.002 4.002 0 0 1 7.86 0h3.32a.75.75 0 0 1 0 1.5h-3.32ZM8 10.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z',
  folder:'M1.75 1A1.75 1.75 0 0 0 0 2.75v10.5C0 14.216.784 15 1.75 15h12.5A1.75 1.75 0 0 0 16 13.25v-8.5A1.75 1.75 0 0 0 14.25 3H7.5a.25.25 0 0 1-.2-.1l-.9-1.2C6.07 1.26 5.55 1 5 1H1.75Z',
  file:'M2 1.75C2 .784 2.784 0 3.75 0h6.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0 1 13.25 16h-9.5A1.75 1.75 0 0 1 2 14.25V1.75Zm1.75-.25a.25.25 0 0 0-.25.25v12.5c0 .138.112.25.25.25h9.5a.25.25 0 0 0 .25-.25V6h-2.75A1.75 1.75 0 0 1 9 4.25V1.5H3.75Zm6.75.062V4.25c0 .138.112.25.25.25h2.688l-.011-.013-2.914-2.914-.013-.011Z',
  chev:'M6.22 3.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 8 6.22 4.28a.75.75 0 0 1 0-1.06Z',
  copy:'M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5Zm5-5C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5Z',
  out:'M3.75 2h3.5a.75.75 0 0 1 0 1.5h-3.5a.25.25 0 0 0-.25.25v8.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25v-3.5a.75.75 0 0 1 1.5 0v3.5A1.75 1.75 0 0 1 12.25 14h-8.5A1.75 1.75 0 0 1 2 12.25v-8.5C2 2.784 2.784 2 3.75 2Zm6.854-1h4.146a.25.25 0 0 1 .25.25v4.146a.25.25 0 0 1-.427.177L13.03 4.03 9.28 7.78a.751.751 0 0 1-1.06-1.06l3.75-3.75-1.543-1.543A.25.25 0 0 1 10.604 1Z'};
const ic = (n, cls) => `<svg class="oct ${cls||''}" viewBox="0 0 16 16" aria-hidden="true"><path d="${OCT[n]}"/></svg>`;

document.getElementById('nav').innerHTML =
  `<a href="${BASE || '/'}">Dashboard</a>` +
  `<a href="${BASE}/whitepaper" class="${PAGE==='whitepaper'?'on':''}">Whitepaper</a>` +
  `<a href="${BASE}/code" class="${PAGE==='code'?'on':''}">Code</a>` +
  `<a href="https://github.com/modc2/mod/tree/dev/mod/orbit/charitao" target="_blank" rel="noopener">GitHub ↗</a>`;

function inline(s){
  return esc(s)
    .replace(/`([^`]+)`/g, (_,c) => '<code>' + c + '</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

function md(src){
  const out = [];
  const lines = src.split('\n');
  let i = 0, list = null, para = [];
  const flushP = () => { if(para.length){ out.push('<p>'+inline(para.join(' '))+'</p>'); para=[]; } };
  const flushL = () => { if(list){ out.push('</'+list+'>'); list=null; } };
  while(i < lines.length){
    const l = lines[i];
    if(/^```/.test(l)){
      flushP(); flushL();
      const buf = []; i++;
      while(i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push('<pre><code>'+esc(buf.join('\n'))+'</code></pre>');
      continue;
    }
    if(/^\|/.test(l)){
      flushP(); flushL();
      const rows = [];
      while(i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.replace(/^\||\|$/g,'').split('|').map(c=>inline(c.trim()));
      let html = '<table><tr>' + cells(rows[0]).map(c=>'<th>'+c+'</th>').join('') + '</tr>';
      for(let r = (/^\|[\s:-]+\|/.test(rows[1]||'') ? 2 : 1); r < rows.length; r++)
        html += '<tr>' + cells(rows[r]).map(c=>'<td>'+c+'</td>').join('') + '</tr>';
      out.push(html + '</table>');
      continue;
    }
    let m;
    if((m = l.match(/^(#{1,4})\s+(.*)/))){
      flushP(); flushL();
      out.push('<h'+m[1].length+'>'+inline(m[2])+'</h'+m[1].length+'>');
    } else if(/^\s*([-*])\s+/.test(l) || /^\s*\d+\.\s+/.test(l)){
      flushP();
      const kind = /^\s*\d+\./.test(l) ? 'ol' : 'ul';
      if(list !== kind){ flushL(); out.push('<'+kind+'>'); list = kind; }
      let item = l.replace(/^\s*([-*]|\d+\.)\s+/, '');
      while(i+1 < lines.length && /^\s{2,}\S/.test(lines[i+1]) &&
            !/^\s*([-*]|\d+\.)\s+/.test(lines[i+1])) item += ' ' + lines[++i].trim();
      out.push('<li>'+inline(item)+'</li>');
    } else if(/^>\s?/.test(l)){
      flushP(); flushL();
      out.push('<blockquote>'+inline(l.replace(/^>\s?/,''))+'</blockquote>');
    } else if(/^\s*(---+|\*\*\*+)\s*$/.test(l)){
      flushP(); flushL(); out.push('<hr/>');
    } else if(!l.trim()){
      flushP(); flushL();
    } else {
      para.push(l.trim());
    }
    i++;
  }
  flushP(); flushL();
  return out.join('\n');
}

/* ---- syntax highlighting: tokenize whole file, then emit per line so
   multi-line strings keep their color across line breaks ---- */
const PY_RE = new RegExp(
  '("{3}[\\s\\S]*?(?:"{3}|$)' + "|'{3}[\\s\\S]*?(?:'{3}|$)" +
  '|"(?:\\\\.|[^"\\\\\\n])*"' + "|'(?:\\\\.|[^'\\\\\\n])*'" +
  '|#[^\\n]*|@[A-Za-z_][\\w.]*' +
  '|\\b\\d+(?:\\.\\d+)?(?:e[+-]?\\d+)?j?\\b' +
  '|\\b(?:def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|global|nonlocal|assert|del|not|and|or|in|is|async|await)\\b' +
  '|\\b(?:None|True|False|self|cls)\\b)', 'g');
const pyCls = t =>
  t[0]==='#' ? 'c' : /^["']/.test(t) ? 's' : t[0]==='@' ? 'd' :
  /^\d/.test(t) ? 'n' : /^(None|True|False|self|cls)$/.test(t) ? 'b' : 'k';

const JSON_RE = /("(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/g;
const jsonCls = (t, src, end) =>
  t[0]==='"' ? (/^\s*:/.test(src.slice(end, end+4)) ? 'j' : 's')
  : /^[tfn]/.test(t) ? 'b' : 'n';

const MD_RE = /(^#{1,6}[^\n]*$|`[^`\n]+`|^```[^\n]*$)/gm;
const mdCls = t => t[0]==='#' ? 'k' : 's';

function tokenize(src, re, classify){
  const toks = []; let last = 0, m;
  re.lastIndex = 0;
  while((m = re.exec(src))){
    if(m.index > last) toks.push([src.slice(last, m.index), null]);
    toks.push([m[0], classify(m[0], src, m.index + m[0].length)]);
    last = m.index + m[0].length;
    if(m.index === re.lastIndex) re.lastIndex++;
  }
  if(last < src.length) toks.push([src.slice(last), null]);
  return toks;
}

function nameAfterDefClass(toks){
  for(let i = 0; i < toks.length; i++){
    if(toks[i][1]==='k' && /^(def|class)$/.test(toks[i][0])
       && toks[i+1] && !toks[i+1][1]){
      const t = toks[i+1][0], m = t.match(/^(\s+)([A-Za-z_]\w*)/);
      if(m) toks.splice(i+1, 1,
        [m[1], null], [m[2], 'f'], [t.slice(m[0].length), null]);
    }
  }
  return toks;
}

function hlLines(src, lang){
  let toks;
  if(lang === 'py') toks = nameAfterDefClass(tokenize(src, PY_RE, pyCls));
  else if(lang === 'json') toks = tokenize(src, JSON_RE, jsonCls);
  else if(lang === 'md') toks = tokenize(src, MD_RE, mdCls);
  else toks = [[src, null]];
  const lines = [[]];
  for(const [txt, c] of toks){
    txt.split('\n').forEach((p, i) => {
      if(i) lines.push([]);
      if(p) lines[lines.length-1].push([p, c]);
    });
  }
  return lines.map(segs => segs.map(([p, c]) =>
    c ? `<span class="tok-${c}">${esc(p)}</span>` : esc(p)).join(''));
}

/* ---- code page state ---- */
const cache = {}, GH = {};
let GIT = null, cur = {f: null, mode: 'code'};
const ext = f => f.endsWith('.py') ? 'py' : f.endsWith('.json') ? 'json'
  : f.endsWith('.md') ? 'md' : 'txt';
const fmtSize = b => b < 1024 ? b + ' B' : (b/1024).toFixed(1) + ' KB';
function rel(ts){
  const s = Date.now()/1000 - ts;
  if(s < 90) return 'just now';
  if(s < 5400) return Math.round(s/60) + ' min ago';
  if(s < 172800) return Math.round(s/3600) + ' hours ago';
  if(s < 2592000) return Math.round(s/86400) + ' days ago';
  return new Date(ts*1000).toISOString().slice(0,10);
}

function treeHTML(files){
  const node = () => ({d:{}, f:[]});
  const root = node();
  for(const f of files){
    const parts = f.split('/');
    let n = root;
    for(let i = 0; i < parts.length-1; i++)
      n = n.d[parts[i]] || (n.d[parts[i]] = node());
    n.f.push({name: parts[parts.length-1], path: f});
  }
  const render = (n, depth) =>
    Object.keys(n.d).sort().map(dir =>
      `<div class="dir" data-open="1">
        <div class="drow" style="padding-left:${10+depth*14}px">
          ${ic('chev','chev')}${ic('folder','fold')}<span>${dir}</span></div>
        <div class="dkids">${render(n.d[dir], depth+1)}</div>
      </div>`).join('') +
    n.f.sort((a,b) => a.name.localeCompare(b.name)).map(fl =>
      `<a class="frow" data-f="${fl.path}"
         style="padding-left:${28+depth*14}px">${ic('file')}<span>${fl.name}</span></a>`).join('');
  return render(root, 0);
}

function parseHash(){
  const m = location.hash.match(/#f=([^&]+)(?:&L=(\d+))?/);
  return m ? {f: decodeURIComponent(m[1]), line: m[2] ? +m[2] : null} : null;
}

async function openFile(f, line, mode){
  const files = GH.files || [];
  if(!files.includes(f)) return;
  if(!cache[f]) cache[f] = await api('code?f=' + encodeURIComponent(f));
  const r = cache[f];
  if(r.error){ $('pane').innerHTML = `<div class="doc embed">${esc(r.error)}</div>`; return; }
  cur.f = f;
  cur.mode = mode || (ext(f)==='md' && !line ? 'preview' : 'code');
  document.querySelectorAll('.frow').forEach(el =>
    el.classList.toggle('on', el.dataset.f === f));
  const nLines = r.content.replace(/\n$/,'').split('\n').length;
  const dir = f.includes('/') ? f.slice(0, f.lastIndexOf('/')+1) : '';
  const name = f.slice(dir.length);
  const blob = `https://github.com/${GIT&&GIT.repo||'modc2/mod'}/blob/${GIT&&GIT.branch||'dev'}/${(GIT&&GIT.subdir)||'mod/orbit/charitao'}/${f}`;
  const raw = `${BASE}/api/code?f=${encodeURIComponent(f)}&raw=1`;
  const isMd = ext(f)==='md';
  const fc = GIT && GIT.files && GIT.files[f];
  $('pane').innerHTML =
    `<div class="fhead">
      <span class="fname"><span class="dirpart">${esc(dir)}</span>${esc(name)}</span>
      <span class="fmeta">${nLines} lines · ${fmtSize(r.size||r.content.length)}</span>
      <span class="facts">
        ${isMd ? `<span class="seg">
          <button id="seg-p" class="${cur.mode==='preview'?'on':''}">Preview</button>
          <button id="seg-c" class="${cur.mode==='code'?'on':''}">Code</button></span>` : ''}
        <button class="fbtn" id="copybtn">${ic('copy')}Copy</button>
        <a class="fbtn" href="${raw}" target="_blank" rel="noopener">Raw</a>
        <a class="fbtn" href="${blob}" target="_blank" rel="noopener">${ic('github')}GitHub</a>
      </span>
    </div>` +
    (fc ? `<div class="fcommit">${ic('commit')}<span class="cmsg">${esc(fc.msg)}</span>
      <a class="chash" href="https://github.com/${GIT.repo}/commit/${fc.short}"
        target="_blank" rel="noopener">${fc.short}</a>
      <span class="ctime">${rel(fc.time)}</span></div>` : '') +
    (cur.mode==='preview'
      ? `<div class="doc embed">${md(r.content)}</div>`
      : `<div class="src" id="src">` +
        hlLines(r.content.replace(/\n$/,''), ext(f)).map((h, i) =>
          `<div class="ln${i+1===line?' hl':''}" id="L${i+1}">` +
          `<a class="n" data-n="${i+1}">${i+1}</a><span class="c">${h||' '}</span></div>`
        ).join('') + `</div>`);
  document.title = 'charitao · ' + f;
  if(isMd){
    const go = m2 => { openFile(f, null, m2); };
    const p = $('seg-p'), c2 = $('seg-c');
    if(p) p.onclick = () => go('preview');
    if(c2) c2.onclick = () => go('code');
  }
  $('copybtn').onclick = async () => {
    try{ await navigator.clipboard.writeText(r.content);
      $('copybtn').innerHTML = ic('copy') + 'Copied!';
      setTimeout(() => { $('copybtn').innerHTML = ic('copy') + 'Copy'; }, 1200);
    }catch(e){}
  };
  const src = $('src');
  if(src) src.addEventListener('click', e => {
    const a = e.target.closest('a.n');
    if(!a) return;
    history.replaceState(null, '', '#f=' + encodeURIComponent(f) + '&L=' + a.dataset.n);
    document.querySelectorAll('.ln.hl').forEach(el => el.classList.remove('hl'));
    a.parentElement.classList.add('hl');
  });
  if(line){
    const el = $('L' + line);
    if(el) el.scrollIntoView({block: 'center'});
  } else $('pane').scrollTop = 0;
}

async function showCode(){
  const [r, git] = await Promise.all([api('code'), api('git').catch(() => null)]);
  GH.files = r.files; GIT = git && !git.error ? git : null;
  const el = $('body');
  el.className = '';
  const latest = GIT && GIT.latest;
  el.innerHTML =
   `<div class="repo">
      <div class="rhead">
        ${ic('github')}
        <a class="rrepo" href="https://github.com/${GIT&&GIT.repo||'modc2/mod'}"
          target="_blank" rel="noopener">${GIT&&GIT.repo||'modc2/mod'}</a>
        <span class="rsep">/</span>
        <span class="rpath">${(GIT&&GIT.subdir)||'mod/orbit/charitao'}</span>
        <span class="rbranch">${ic('branch')}${GIT&&GIT.branch||'dev'}</span>
        <a class="ghbtn" href="${r.github}" target="_blank" rel="noopener">
          Open on GitHub ${ic('out')}</a>
      </div>` +
      (latest ? `<div class="cbar">${ic('commit')}
        <span class="cauthor">${esc(latest.author)}</span>
        <span class="cmsg">${esc(latest.msg)}</span>
        <a class="chash" href="https://github.com/${GIT.repo}/commit/${latest.short}"
          target="_blank" rel="noopener">${latest.short}</a>
        <span class="ctime">${rel(latest.time)}</span></div>` : '') +
     `<div class="cols">
        <aside class="tree">
          <div class="tfindbox"><input class="tfind" id="tfind"
            placeholder="Go to file…" spellcheck="false"></div>
          <div id="treebody">${treeHTML(r.files)}</div>
        </aside>
        <section class="pane" id="pane">
          <div class="doc embed muted">pick a file</div>
        </section>
      </div>
    </div>`;
  document.title = 'charitao · code';
  document.querySelectorAll('.drow').forEach(d => d.onclick = () => {
    const dir = d.parentElement;
    dir.dataset.open = dir.dataset.open === '1' ? '0' : '1';
  });
  document.querySelectorAll('.frow').forEach(a => a.onclick = () => {
    location.hash = 'f=' + encodeURIComponent(a.dataset.f);
  });
  $('tfind').oninput = e => {
    const v = e.target.value.trim().toLowerCase();
    document.querySelectorAll('.frow').forEach(a =>
      a.style.display = !v || a.dataset.f.toLowerCase().includes(v) ? '' : 'none');
    document.querySelectorAll('.dir').forEach(d => {
      if(v) d.dataset.open = '1';
      const any = [...d.querySelectorAll('.frow')].some(a => a.style.display !== 'none');
      d.style.display = any ? '' : 'none';
    });
  };
  const h = parseHash();
  if(h) openFile(h.f, h.line);
  else if(r.files.includes('README.md')) openFile('README.md');
  else if(r.files.length) openFile(r.files[0]);
  window.addEventListener('hashchange', () => {
    const h2 = parseHash();
    if(h2 && (h2.f !== cur.f || h2.line)) openFile(h2.f, h2.line);
  });
}

async function showWhitepaper(){
  const r = await api('whitepaper');
  $('body').innerHTML = '<div class="doc">' + md(r.markdown) + '</div>';
  document.title = 'charitao · whitepaper';
}

(PAGE === 'code' ? showCode() : showWhitepaper()).catch(e => {
  $('body').textContent = 'backend unreachable: ' + e.message;
});
</script>
</body>
</html>
"""
