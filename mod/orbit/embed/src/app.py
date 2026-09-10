"""The console. Standard library only — no build step, no node_modules.

    python3 src/app.py --port 50621 --api http://127.0.0.1:50620

Serves one page at /embed and proxies /embed/_api/* to the API, so the browser
talks to a single origin and there is no CORS to arrange.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = os.environ.get('EMBED_API', 'http://127.0.0.1:50620')
BASE = '/embed'

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>embed — small models, made smaller</title>
<style>
  :root {
    --bg:#080a0f; --panel:#0e1119; --line:#1c2130; --ink:#c9d1e0; --dim:#6b7688;
    --accent:#8aa2ff; --good:#4ade80; --warn:#fbbf24; --bad:#f87171;
    --mono:'SF Mono',ui-monospace,'JetBrains Mono',Menlo,Consolas,monospace;
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--ink); font-family:var(--mono);
         font-size:13px; line-height:1.6 }
  header { padding:18px 24px; border-bottom:1px solid var(--line);
           display:flex; align-items:baseline; gap:14px; flex-wrap:wrap }
  h1 { margin:0; font-size:16px; letter-spacing:.14em; color:#fff }
  h1 b { color:var(--accent) }
  .tag { color:var(--dim) }
  nav { display:flex; gap:2px; padding:0 24px; border-bottom:1px solid var(--line);
        overflow-x:auto }
  nav button { background:none; border:none; border-bottom:2px solid transparent;
               color:var(--dim); font:inherit; letter-spacing:.12em; padding:12px 16px;
               cursor:pointer }
  nav button.on { color:var(--accent); border-bottom-color:var(--accent) }
  main { padding:24px; max-width:1180px }
  section { display:none } section.on { display:block }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:16px 18px; margin-bottom:16px }
  .card h2 { margin:0 0 4px; font-size:13px; letter-spacing:.1em; color:#fff }
  .card p { margin:0 0 12px; color:var(--dim) }
  table { width:100%; border-collapse:collapse; font-size:12px }
  th { text-align:right; color:var(--dim); font-weight:400; padding:6px 10px;
       border-bottom:1px solid var(--line); letter-spacing:.06em }
  th:first-child, td:first-child { text-align:left }
  td { text-align:right; padding:6px 10px; border-bottom:1px solid #141824 }
  tr:hover td { background:#121724 }
  input, select, button.go { background:#0b0e15; color:var(--ink); font:inherit;
        border:1px solid var(--line); border-radius:6px; padding:8px 11px }
  input { min-width:280px } button.go { cursor:pointer; color:var(--accent) }
  button.go:hover { border-color:var(--accent) }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:14px }
  .bar { display:inline-block; height:8px; background:var(--accent); border-radius:2px;
         vertical-align:middle; opacity:.8 }
  .good { color:var(--good) } .warn { color:var(--warn) } .bad { color:var(--bad) }
  .dim { color:var(--dim) }
  pre { background:#0b0e15; border:1px solid var(--line); border-radius:6px; padding:12px;
        overflow:auto; font-size:12px; color:#aab6c8; max-height:520px }
  .hit { border-left:2px solid var(--line); padding:6px 0 6px 12px; margin-bottom:8px }
  .hit b { color:#fff; font-weight:500 } .score { color:var(--accent) }
  .note { color:var(--dim); font-size:12px; margin-top:10px; line-height:1.7 }
</style></head><body>

<header>
  <h1><b>embed</b> small models, made smaller</h1>
  <span class="tag">the compression is easy — the honest part is measuring what it cost</span>
</header>

<nav>
  <button class="on" data-tab="compress">COMPRESS</button>
  <button data-tab="models">MODELS</button>
  <button data-tab="search">SEARCH</button>
  <button data-tab="lessons">LESSONS</button>
  <button data-tab="info">INFO</button>
</nav>

<main>
  <section id="compress" class="on">
    <div class="card">
      <h2>EVERY METHOD, SCORED ON THE TASK</h2>
      <p>Compress the model each way, then run the model's own task on each result.</p>
      <div class="row">
        <select id="sweep-model"></select>
        <button class="go" onclick="sweep()">run the sweep</button>
        <span id="sweep-status" class="dim"></span>
      </div>
      <div id="sweep-out"></div>
    </div>
    <div class="card">
      <h2>ONE TENSOR, EVERY METHOD</h2>
      <p>The arithmetic on its own — bytes and error, no graph and no task.</p>
      <div class="row">
        <select id="cmp-model"></select>
        <button class="go" onclick="compare()">compare</button>
      </div>
      <div id="cmp-out"></div>
    </div>
  </section>

  <section id="models">
    <div class="card">
      <h2>THE ZOO</h2>
      <p>Two models built here from numpy in a couple of seconds, and one real
         transformer you can pull if you want something with mass.</p>
      <div id="zoo"></div>
    </div>
    <div class="card">
      <h2>INSIDE THE FILE</h2>
      <div class="row">
        <select id="graph-model"></select>
        <button class="go" onclick="graph()">read the graph</button>
      </div>
      <div id="graph-out"></div>
    </div>
  </section>

  <section id="search">
    <div class="card">
      <h2>SEARCH, AT TWO PRECISIONS</h2>
      <p>The same query against the float model and a compressed copy. Where the
         two disagree, the float model's margin was small to begin with.</p>
      <div class="row">
        <input id="q" value="why add an index to a table">
        <select id="method">
          <option>float16</option><option>int8</option>
          <option>int8-per-channel</option><option selected>int4-sim</option>
        </select>
        <button class="go" onclick="search()">search</button>
      </div>
      <div id="search-out"></div>
    </div>
    <div class="card">
      <h2>CLASSIFY</h2>
      <div class="row">
        <input id="t" value="the book is clever but ultimately mean">
        <button class="go" onclick="classify()">classify</button>
      </div>
      <div id="class-out"></div>
    </div>
  </section>

  <section id="lessons">
    <div class="card">
      <h2>THE LESSONS</h2>
      <p>Five scripts, in order. Run them with <code>python3 examples/&lt;file&gt;</code>
         or <code>m embed/examples run=01</code>.</p>
      <div id="lessons-out"></div>
    </div>
  </section>

  <section id="info">
    <div class="card"><h2>WHAT THIS IS</h2><div id="info-out"></div></div>
  </section>
</main>

<script>
const API = location.pathname.replace(/\/$/, '') + '/_api';
const get = (p) => fetch(API + p).then(r => r.json());
const bytes = (n) => n == null ? '—' : (n / 1024).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + ' KB';
const el = (id) => document.getElementById(id);

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('section').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); el(b.dataset.tab).classList.add('on');
});

async function boot() {
  const models = await get('/models');
  const runnable = models.filter(m => m.name !== 'minilm');
  for (const id of ['sweep-model', 'cmp-model', 'graph-model']) {
    el(id).innerHTML = models.map(m => `<option>${m.name}</option>`).join('');
    if (id !== 'graph-model') el(id).innerHTML = runnable.map(m => `<option>${m.name}</option>`).join('');
  }
  el('zoo').innerHTML = `<table><tr><th>model</th><th>kind</th><th>what it is</th>
    <th>on disk</th></tr>` + models.map(m => `<tr><td><b>${m.name}</b></td>
    <td class="dim">${m.kind}</td><td class="dim" style="text-align:left">${m.about}</td>
    <td>${m.present ? bytes(m.bytes) : '<span class="dim">not built</span>'}</td></tr>`
  ).join('') + '</table>';
  el('info-out').innerHTML = '<pre>' + JSON.stringify(await get('/'), null, 2) + '</pre>';
  const lessons = await get('/examples');
  el('lessons-out').innerHTML = lessons.map(l =>
    `<div class="hit"><b>${l.file}</b><br><span class="dim">${l.title}</span></div>`).join('');
  sweep();
}

async function sweep() {
  const name = el('sweep-model').value;
  el('sweep-status').textContent = 'compressing and scoring…';
  const r = await get('/sweep?name=' + name);
  const metric = 'top1_accuracy' in r.results[0] ? 'top1_accuracy' : 'accuracy';
  const label = metric === 'top1_accuracy' ? 'retrieval top-1' : 'accuracy';
  const biggest = Math.max(...r.results.map(x => x.file_bytes));
  el('sweep-status').textContent = '';
  el('sweep-out').innerHTML = `<table>
    <tr><th>method</th><th>file</th><th></th><th>gzipped</th><th>worst weight error</th>
        <th>${label}</th><th>agreement with float32</th></tr>` +
    r.results.map(x => {
      const agree = x.agreement_with_float ?? 1;
      const cls = agree === 1 ? 'good' : agree >= 0.9 ? 'warn' : 'bad';
      return `<tr><td>${x.method}</td><td>${bytes(x.file_bytes)}</td>
        <td style="width:120px"><span class="bar" style="width:${100 * x.file_bytes / biggest}px"></span></td>
        <td class="dim">${bytes(x.gzip_bytes)}</td>
        <td>${(100 * x.worst_tensor_rel_rmse).toFixed(2)}%</td>
        <td>${(x[metric] ?? 0).toFixed(3)}</td>
        <td class="${cls}">${agree.toFixed(3)}</td></tr>`;
    }).join('') + '</table>' +
    `<div class="note">Agreement is the strict metric: the share of answers identical
     to the float32 model's. Accuracy can hold steady while answers swap underneath it
     — on sent-mlp, int4 keeps accuracy within half a point while changing twelve
     answers.<br>int4-sim's <i>file</i> is int8-sized because ONNX has no 4-bit tensor
     before opset 21; its gzip column is the honest measure of what 4 bits would cost.</div>`;
}

async function compare() {
  const r = await get('/compare?name=' + el('cmp-model').value);
  el('cmp-out').innerHTML = `<div class="dim" style="margin-bottom:8px">tensor
    <b>${r.tensor}</b> — ${r.shape.join(' × ')}, ${r.parameters.toLocaleString()} parameters</div>
    <table><tr><th>method</th><th>stored</th><th>ratio</th><th>rel_rmse</th><th>cosine</th></tr>` +
    r.methods.map(m => `<tr><td>${m.method}${m.per_channel ? ' <span class="dim">per-channel</span>' : ''}</td>
      <td>${bytes(m.stored_bytes)}</td><td>${m.ratio}×</td>
      <td>${(100 * m.error.rel_rmse).toFixed(3)}%</td>
      <td class="dim">${m.error.cosine.toFixed(6)}</td></tr>`).join('') + '</table>';
}

async function graph() {
  const r = await get('/models/' + el('graph-model').value);
  const s = r.summary;
  el('graph-out').innerHTML = `<table><tr><th>op</th><th>inputs → outputs</th></tr>` +
    r.nodes.map(n => `<tr><td>${n.op}</td>
      <td class="dim" style="text-align:left">${n.inputs.join(', ')} → ${n.outputs.join(', ')}</td></tr>`
    ).join('') + `</table><div class="note">${s.parameters.toLocaleString()} parameters,
    ${bytes(s.weight_bytes)} of weights, opset ${s.opset}.
    ${r.ops_missing_here.length ? '<span class="warn">This module\'s numpy runtime does not implement: '
      + r.ops_missing_here.join(', ') + ' — it can still compress the file, it just cannot run it.</span>'
      : 'Every op in this graph runs in src/runtime.py.'}</div>`;
}

async function search() {
  const q = encodeURIComponent(el('q').value), m = el('method').value;
  const [full, small] = await Promise.all([
    get(`/search?query=${q}&top=3`), get(`/search?query=${q}&top=3&method=${m}`)]);
  const render = (r, title) => `<div style="flex:1;min-width:280px">
    <div class="dim" style="margin-bottom:6px">${title}</div>` + r.results.map(h =>
    `<div class="hit"><span class="score">${h.score.toFixed(3)}</span> ${h.text}</div>`).join('') + '</div>';
  const same = full.results[0].index === small.results[0].index;
  el('search-out').innerHTML = `<div style="display:flex;gap:24px;flex-wrap:wrap">
    ${render(full, 'float32')}${render(small, m)}</div>
    <div class="note ${same ? 'good' : 'bad'}">${same
      ? 'Same top answer. The margin here was wider than the noise the quantizer added.'
      : 'Different top answer — this query was a close call in the float model too.'}</div>`;
}

async function classify() {
  const t = encodeURIComponent(el('t').value);
  const [full, small] = await Promise.all([
    get(`/classify?text=${t}`), get(`/classify?text=${t}&method=int4-sim`)]);
  const row = (r, title) => `<tr><td>${title}</td><td><b>${r.label}</b></td>
    <td>${r.probabilities.positive.toFixed(4)}</td><td>${r.margin.toFixed(4)}</td></tr>`;
  el('class-out').innerHTML = `<table><tr><th>model</th><th>label</th>
    <th>p(positive)</th><th>margin</th></tr>${row(full, 'float32')}${row(small, 'int4-sim')}</table>
    <div class="note">A small margin means the model is nearly undecided — those are the
    rows compression flips first.</div>`;
}

boot();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    api = API

    def do_GET(self) -> None:          # noqa: N802
        path = self.path.split('?')[0]
        if path in (BASE, BASE + '/', '/'):
            return self._html(PAGE)
        if path.startswith(BASE + '/_api'):
            return self._proxy(self.path[len(BASE + '/_api'):] or '/')
        if path == '/health':
            return self._json({'ok': True, 'app': 'embed', 'api': self.api})
        self._html('<h1>404</h1><p>the console lives at <a href="/embed">/embed</a></p>', 404)

    def do_POST(self) -> None:         # noqa: N802
        if self.path.startswith(BASE + '/_api'):
            length = int(self.headers.get('content-length') or 0)
            return self._proxy(self.path[len(BASE + '/_api'):] or '/',
                               body=self.rfile.read(length) if length else b'',
                               method='POST')
        self._json({'error': 'not found'}, 404)

    def _proxy(self, tail: str, body: bytes = None, method: str = 'GET') -> None:
        url = self.api.rstrip('/') + tail
        request = urllib.request.Request(url, data=body, method=method)
        if body:
            request.add_header('content-type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload, status = response.read(), response.status
        except urllib.error.HTTPError as exc:
            payload, status = exc.read(), exc.code
        except Exception as exc:
            payload = json.dumps({'error': f'{type(exc).__name__}: {exc}',
                                  'api': self.api}).encode()
            status = 502
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, body: str, status: int = 200) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header('content-type', 'text/html; charset=utf-8')
        self.send_header('content-length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj: dict, status: int = 200) -> None:
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass                                   # the access log is noise here


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('EMBED_APP_PORT', 50621)))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    Handler.api = args.api
    print(f'embed console  http://localhost:{args.port}{BASE}  → api {args.api}')
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
