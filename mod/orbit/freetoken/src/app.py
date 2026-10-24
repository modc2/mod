"""The console — one file, no dependencies, and it never pretends there is a GPU.

Serves /freetoken and proxies /freetoken/_api/* to the API, so the browser talks
to one origin and nothing needs CORS. The first panel is this machine's
preflight, because on most machines that is the answer, and the second is the
list of machines that *can* serve — which is how the rest of the console stays
useful from a laptop.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = 'http://127.0.0.1:50660'
BASE = '/freetoken'

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>freetoken</title>
<style>
 :root{--bg:#07090c;--panel:#0f1319;--line:#1c232d;--ink:#dbe3ec;--dim:#7f8b9c;
       --ok:#4ade80;--no:#f87171;--warn:#fbbf24;--accent:#7dd3fc}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
      font:13.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:16px 22px;border-bottom:1px solid var(--line);display:flex;
        align-items:baseline;gap:14px;flex-wrap:wrap}
 h1{font-size:15px;margin:0;letter-spacing:.18em;text-transform:uppercase}
 .sub{color:var(--dim);font-size:12px}
 main{max-width:1180px;margin:0 auto;padding:20px;display:grid;gap:16px}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:15px}
 .panel h2{font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;
           color:var(--dim);margin:0 0 12px}
 .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 .two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
 @media(max-width:860px){.two{grid-template-columns:1fr}}
 button,select,input,textarea{background:#0a0e13;color:var(--ink);border:1px solid var(--line);
        border-radius:7px;padding:7px 11px;font:inherit;font-size:12.5px}
 button{cursor:pointer}
 button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
 button:disabled{opacity:.4;cursor:not-allowed}
 button.go{border-color:var(--ok);color:var(--ok)}
 button.stop{border-color:var(--no);color:var(--no)}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
 th{color:var(--dim);font-weight:400;font-size:10px;letter-spacing:.12em;text-transform:uppercase}
 .yes{color:var(--ok)} .no{color:var(--no)} .warn{color:var(--warn)} .dim{color:var(--dim)}
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;color:var(--dim);
     max-height:280px;overflow:auto}
 .pill{border:1px solid var(--line);border-radius:999px;padding:2px 9px;font-size:11px;color:var(--dim)}
 .pill.on{border-color:var(--ok);color:var(--ok)}
 .pill.off{border-color:var(--no);color:var(--no)}
 .card{background:#0a0e13;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
 .card.sel{border-color:var(--accent)}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
 .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
 .v{font-size:17px;margin-top:2px}
 .chat{background:#0a0e13;border:1px solid var(--line);border-radius:8px;padding:12px;
       min-height:110px;max-height:420px;overflow:auto;white-space:pre-wrap;line-height:1.65}
 .note{color:var(--dim);font-size:11.5px;margin-top:9px}
 a{color:var(--accent)}
</style></head><body>
<header>
  <h1>freetoken</h1>
  <span class="sub">an engine, wherever the GPU is</span>
  <span class="sub" id="who" style="margin-left:auto"></span>
</header>
<main>

<section class="panel">
  <h2>this machine</h2>
  <div id="pre" class="dim">checking…</div>
  <div class="row" style="margin-top:12px">
    <button id="inst">install ft here</button>
    <button id="instDry">show the commands</button>
    <span class="sub" id="ftv"></span>
  </div>
  <pre id="instLog" style="margin-top:10px"></pre>
</section>

<section class="panel">
  <h2>engines</h2>
  <div id="boxes" class="grid"></div>
  <div class="row" style="margin-top:12px">
    <input id="bn" placeholder="name" style="width:110px">
    <input id="bu" placeholder="http://host:1919 (serve)" style="width:230px">
    <input id="bd" placeholder="http://host:1900 (daemon, optional)" style="width:250px">
    <input id="bt" placeholder="X-FT-Token (optional)" style="width:180px">
    <button id="badd">add</button>
  </div>
  <div class="note">The serve port answers OpenAI and Anthropic requests. The daemon
    port is what lets this console start and switch models on that machine.</div>
</section>

<div class="two">
<section class="panel">
  <h2>model</h2>
  <div class="row">
    <select id="known" style="min-width:260px"></select>
    <button id="start" class="go">start</button>
    <button id="stop" class="stop">stop</button>
  </div>
  <div class="row" style="margin-top:8px">
    <input id="model" placeholder="or a repo id / path" style="flex:1;min-width:220px">
    <select id="moe">
      <option value="">moe: auto</option>
      <option>offload</option><option>hybrid</option>
      <option>cpu</option><option>fused</option>
    </select>
  </div>
  <div id="startOut" class="note"></div>
  <pre id="serveLog" style="margin-top:10px"></pre>
</section>

<section class="panel">
  <h2>pools</h2>
  <div id="pools" class="dim">—</div>
  <div class="row" style="margin-top:10px">
    <input id="rmoe" placeholder="moe slots" style="width:110px">
    <input id="rkv" placeholder="kv tokens (200k)" style="width:140px">
    <button id="resize">resize live</button>
  </div>
  <div class="note">VRAM moves between the expert cache and KV without a restart or
    a weight reload — the engine's own /v1/cache/rebuild.</div>
</section>
</div>

<section class="panel">
  <h2>stats</h2>
  <div id="stats" class="grid"></div>
</section>

<section class="panel">
  <h2>chat</h2>
  <div class="row">
    <input id="prompt" placeholder="ask the engine something" style="flex:1;min-width:260px">
    <input id="maxtok" value="512" style="width:80px">
    <button id="send" class="go">send</button>
  </div>
  <div class="chat" id="out">nothing yet</div>
  <div class="note" id="chatnote"></div>
</section>

<section class="panel">
  <h2>on this disk</h2>
  <div id="local" class="dim">—</div>
</section>

</main>
<script>
const B='__BASE__/_api';
const $=id=>document.getElementById(id);
const j=async(p,o)=>{const r=await fetch(B+p,o);return r.json()};
const post=(p,b)=>j(p,{method:'POST',headers:{'Content-Type':'application/json'},
                       body:JSON.stringify(b||{})});
const esc=s=>String(s??'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

let BOX=null;

async function preflight(){
  const p=await j('/preflight');
  const found=c=>c.found==null||c.found===''?'not found'
    :Array.isArray(c.found)?c.found.join(', '):c.found;
  $('pre').innerHTML='<table><tr><th>check</th><th>found</th><th>wanted</th><th></th></tr>'+
    p.checks.map(c=>`<tr><td>${esc(c.check)}</td><td class="${c.ok?'yes':(c.blocking?'no':'warn')}">${esc(
      found(c))}</td><td class="dim">${esc(c.want)}</td>
      <td class="dim">${esc(c.note||'')}</td></tr>`).join('')+'</table>'+
    `<div class="note ${p.can_serve_here?'yes':''}">${esc(p.verdict)}</div>`;
  const s=await j('/install');
  $('ftv').textContent=s.installed?`ft ${s.version||''} — ${s.from}`:'ft not installed here';
  if(s.installing){$('instLog').textContent=s.tail;setTimeout(preflight,4000)}
}

async function loadBoxes(){
  const b=await j('/boxes');
  BOX=BOX||b.default;
  $('who').textContent='box: '+(BOX||'none');
  $('boxes').innerHTML=(b.engines||[]).map(e=>`
    <div class="card ${e.name===BOX?'sel':''}">
      <div class="row" style="justify-content:space-between">
        <b>${esc(e.name)}</b>
        <span class="pill ${e.up?'on':'off'}">${e.up?e.ms+' ms':'down'}</span>
      </div>
      <div class="dim" style="font-size:11.5px;margin-top:4px">${esc(e.url||'')}</div>
      <div style="margin-top:4px">${e.model?esc(e.model):'<span class="dim">no model</span>'}</div>
      <div class="row" style="margin-top:7px">
        <button onclick="pick('${esc(e.name)}')">use</button>
        <button onclick="drop('${esc(e.name)}')">forget</button>
        ${e.steerable?'<span class="pill on">steerable</span>':
                      '<span class="pill">no daemon</span>'}
      </div>
      ${e.error?`<div class="note no">${esc(e.error)}</div>`:''}
    </div>`).join('')||'<span class="dim">no engines registered</span>';
}
window.pick=async n=>{await post('/boxes/'+encodeURIComponent(n)+'/use');BOX=n;refresh()};
window.drop=async n=>{await j('/boxes/'+encodeURIComponent(n),{method:'DELETE'});
                      if(BOX===n)BOX=null;refresh()};

async function models(){
  const m=await j('/models?size=false');
  $('known').innerHTML=m.known_good.flatMap(f=>f.checkpoints.map(c=>
    `<option value="${esc(c)}">${esc(c)}${f.local.includes(c)?'  ✓ on disk':''}</option>`)).join('');
  $('local').innerHTML=m.local.length
    ? '<table><tr><th>repo</th><th>kind</th><th>size</th><th>path</th></tr>'+
      m.local.map(l=>`<tr><td>${esc(l.repo||'—')}</td>
        <td class="${l.kind==='ftw'?'yes':''}">${esc(l.kind)}</td>
        <td>${l.gb!=null?l.gb+' GB':''}</td>
        <td class="dim">${esc(l.path)}</td></tr>`).join('')+'</table>'
    : `<span class="dim">nothing found under ${esc((m.searched||[]).join(', ')||'the usual places')}</span>`;
}

async function pools(){
  const c=await j('/cache'+(BOX?'?box='+encodeURIComponent(BOX):''));
  $('pools').innerHTML=c.error?`<span class="dim">${esc(c.detail||c.error)}</span>`
    :`<pre>${esc(JSON.stringify(c,null,1))}</pre>`;
}

async function stats(){
  const s=await j('/stats'+(BOX?'?box='+encodeURIComponent(BOX):''));
  if(s.error){$('stats').innerHTML=`<span class="dim">${esc(s.detail||s.error)}</span>`;return}
  const flat={};(function walk(o,p){for(const[k,v]of Object.entries(o||{})){
    if(v&&typeof v==='object'&&!Array.isArray(v))walk(v,p+k+'.');
    else flat[p+k]=v}})(s,'');
  $('stats').innerHTML=Object.entries(flat).slice(0,18).map(([k,v])=>
    `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
}

async function serveLog(){
  const l=await j('/logs?lines=40');
  $('serveLog').textContent=l.tail||'';
}

$('inst').onclick=async()=>{$('instLog').textContent='starting…';
  const r=await post('/install',{});$('instLog').textContent=JSON.stringify(r,null,1);
  setTimeout(preflight,3000)};
$('instDry').onclick=async()=>{const r=await post('/install',{dry:true});
  $('instLog').textContent=(r.steps||[]).join('\n')};

$('badd').onclick=async()=>{
  const r=await post('/boxes',{name:$('bn').value.trim(),url:$('bu').value.trim(),
    daemon:$('bd').value.trim()||null,token:$('bt').value.trim()||null,use:true});
  BOX=r.name||BOX;$('bt').value='';refresh()};

$('start').onclick=async()=>{
  const model=$('model').value.trim()||$('known').value;
  const flags={};if($('moe').value)flags.moe_backend=$('moe').value;
  $('startOut').textContent='starting '+model+' …';
  const r=await post('/start',{model,box:BOX,flags});
  $('startOut').textContent=JSON.stringify(r,null,1);serveLog()};
$('stop').onclick=async()=>{$('startOut').textContent=
  JSON.stringify(await post('/stop',{box:BOX}),null,1);serveLog()};

$('resize').onclick=async()=>{
  const b={box:BOX};if($('rmoe').value)b.moe=$('rmoe').value;if($('rkv').value)b.kv=$('rkv').value;
  $('pools').innerHTML='<span class="dim">rebuilding…</span>';
  await post('/cache/rebuild',b);pools()};

$('send').onclick=async()=>{
  const p=$('prompt').value.trim();if(!p)return;
  $('out').textContent='';$('chatnote').textContent='';
  const r=await fetch(B+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prompt:p,box:BOX,max_tokens:+$('maxtok').value||512,stream:true})});
  if(!r.body){$('out').textContent='no stream';return}
  const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
  for(;;){const{done,value}=await rd.read();if(done)break;
    buf+=dec.decode(value,{stream:true});
    const lines=buf.split('\n');buf=lines.pop();
    for(const line of lines){
      if(!line.startsWith('data:'))continue;
      const body=line.slice(5).trim();
      if(body==='[DONE]'){$('chatnote').textContent='done';continue}
      try{const d=JSON.parse(body);
        const c=d.choices&&d.choices[0]&&(d.choices[0].delta||{});
        if(c.reasoning_content)$('chatnote').textContent='thinking…';
        if(c.content)$('out').textContent+=c.content;
        if(d.error)$('chatnote').textContent=JSON.stringify(d.error);
      }catch(e){}
      $('out').scrollTop=$('out').scrollHeight;
    }}};

function refresh(){preflight();loadBoxes();models();pools();stats();serveLog()}
refresh();setInterval(()=>{loadBoxes();stats();pools()},10000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    api = API

    def log_message(self, *_a) -> None:            # the console is not a logger
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        path = self.path[len(f'{BASE}/_api'):] or '/'
        length = int(self.headers.get('Content-Length') or 0)
        payload = self.rfile.read(length) if length else None
        request = urllib.request.Request(self.api + path, data=payload,
                                         method=method)
        if payload:
            request.add_header('Content-Type', 'application/json')
        try:
            response = urllib.request.urlopen(request, timeout=900)
            ctype = response.headers.get('Content-Type', 'application/json')
            if 'event-stream' in ctype:
                return self._relay(response, ctype)
            self._send(response.status, response.read(), ctype)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), 'application/json')
        except Exception as exc:
            self._send(502, json.dumps({'error': f'api unreachable: {exc}'}).encode(),
                       'application/json')

    def _relay(self, response, ctype: str) -> None:
        """Tokens arrive one at a time; the browser should see them that way."""
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            for line in response:
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        if self.path.startswith(f'{BASE}/_api'):
            return self._proxy('GET')
        if self.path.rstrip('/') in (BASE, ''):
            page = PAGE.replace('__BASE__', BASE)
            return self._send(200, page.encode(), 'text/html; charset=utf-8')
        if self.path == '/health':
            return self._send(200, b'{"ok":true}', 'application/json')
        self._send(404, b'not here', 'text/plain')

    def do_POST(self) -> None:
        if self.path.startswith(f'{BASE}/_api'):
            return self._proxy('POST')
        self._send(404, b'not here', 'text/plain')

    def do_DELETE(self) -> None:
        if self.path.startswith(f'{BASE}/_api'):
            return self._proxy('DELETE')
        self._send(404, b'not here', 'text/plain')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=50661)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    Handler.api = args.api.rstrip('/')
    print(f'freetoken console  http://{args.host}:{args.port}{BASE}  → {Handler.api}')
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
