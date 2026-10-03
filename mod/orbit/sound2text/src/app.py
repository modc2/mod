"""The console — one file, no dependencies, and it draws what was skipped.

Serves /sound2text and proxies /sound2text/_api/* to the API, so the browser
talks to one origin and nothing needs CORS. The microphone is recorded through
WebAudio and encoded to a 16 kHz WAV in the page, which is why this module can
take a recording on a host with no ffmpeg on it.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API = 'http://127.0.0.1:50640'
BASE = '/sound2text'

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>sound2text</title>
<style>
 :root{--bg:#0b0d10;--panel:#12161b;--line:#1f2630;--ink:#dfe6ee;--dim:#8a97a8;
       --speech:#4ade80;--skip:#2a3441;--accent:#67e8f9;--warn:#fbbf24}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
      font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;
        align-items:baseline;gap:14px;flex-wrap:wrap}
 h1{font-size:16px;margin:0;letter-spacing:.14em;text-transform:uppercase}
 .sub{color:var(--dim);font-size:12px}
 main{max-width:1100px;margin:0 auto;padding:22px;display:grid;gap:18px}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
 .panel h2{font-size:11px;letter-spacing:.16em;text-transform:uppercase;
           color:var(--dim);margin:0 0 12px}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 button,select,input{background:#0e1319;color:var(--ink);border:1px solid var(--line);
        border-radius:7px;padding:8px 12px;font:inherit;font-size:13px;cursor:pointer}
 button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
 button:disabled{opacity:.4;cursor:not-allowed}
 button.on{border-color:var(--speech);color:var(--speech)}
 button.rec{border-color:#f87171;color:#f87171}
 canvas{width:100%;height:120px;display:block;background:#0e1319;
        border:1px solid var(--line);border-radius:8px;margin-top:12px}
 .legend{display:flex;gap:16px;font-size:12px;color:var(--dim);margin-top:8px}
 .swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
 .stat{background:#0e1319;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
 .stat .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
 .stat .v{font-size:19px;margin-top:3px}
 .stat .v.good{color:var(--speech)}
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px}
 .transcript{background:#0e1319;border:1px solid var(--line);border-radius:8px;
             padding:14px;line-height:1.7;min-height:60px}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}
 th{color:var(--dim);font-weight:400;font-size:10px;letter-spacing:.12em;text-transform:uppercase}
 .no{color:var(--dim)} .yes{color:var(--speech)} .warn{color:var(--warn)}
 .note{color:var(--dim);font-size:12px;margin-top:10px}
 .drop{border:1px dashed var(--line);border-radius:8px;padding:22px;text-align:center;
       color:var(--dim);cursor:pointer}
 .drop.over{border-color:var(--accent);color:var(--accent)}
</style></head><body>
<header>
  <h1>sound2text</h1>
  <span class="sub">decode &rarr; detect &rarr; recall &rarr; route &rarr; run</span>
  <span class="sub" id="ready" style="margin-left:auto"></span>
</header>
<main>

<section class="panel">
  <h2>audio</h2>
  <div class="row">
    <select id="sample"></select>
    <button id="load">load sample</button>
    <button id="mic">record</button>
    <input type="file" id="file" accept="audio/*" hidden>
    <button id="pick">choose a file</button>
    <span class="sub" id="what"></span>
  </div>
  <div class="drop" id="drop">drop an audio file here</div>
  <canvas id="wave" height="120"></canvas>
  <div class="legend">
    <span><i class="swatch" style="background:var(--speech)"></i>speech &mdash; sent to the model</span>
    <span><i class="swatch" style="background:var(--skip)"></i>skipped &mdash; never leaves this machine</span>
    <span id="vadline"></span>
  </div>
</section>

<section class="panel">
  <h2>run</h2>
  <div class="row">
    <select id="engine"><option value="">route for me</option></select>
    <select id="policy">
      <option value="fast">fastest here</option>
      <option value="cheap">cheapest</option>
      <option value="best">best model</option>
    </select>
    <button id="vadBtn" class="on">trim silence</button>
    <button id="packBtn" class="on">pack windows</button>
    <button id="cacheBtn" class="on">use cache</button>
    <button id="go">transcribe</button>
    <button id="cmp">compare all three</button>
    <span class="sub" id="status"></span>
  </div>
</section>

<section class="panel">
  <h2>transcript</h2>
  <div class="transcript" id="text">nothing yet</div>
  <div class="note" id="why"></div>
  <div class="grid" id="stats" style="margin-top:14px"></div>
</section>

<section class="panel">
  <h2>engines</h2>
  <div id="engines"></div>
</section>
</main>

<script>
const API = location.pathname.replace(/\/$/,'') + '/_api';
const $ = s => document.querySelector(s);
let source = null;           // {kind:'path'|'blob', value}
let lastVad = null, waveform = [], duration = 0;

const get = (p) => fetch(API + p).then(r => r.json());

function flag(btn){ return btn.classList.contains('on'); }
for (const id of ['vadBtn','packBtn','cacheBtn'])
  $('#'+id).onclick = e => e.target.classList.toggle('on');

// ── the picture: what was sent, and what was not ──────────────────
function draw(){
  const c = $('#wave'), ctx = c.getContext('2d');
  const w = c.width = c.clientWidth * devicePixelRatio;
  const h = c.height = 120 * devicePixelRatio;
  ctx.clearRect(0,0,w,h);
  if (!waveform.length) return;
  const spans = (lastVad && lastVad.segments) || [];
  const inSpeech = x => spans.some(s => x >= s.start && x <= s.end);
  const step = w / waveform.length;
  for (let i = 0; i < waveform.length; i++){
    const t = (i / waveform.length) * duration;
    const amp = Math.min(waveform[i], 1) * (h/2 - 4);
    // Room tone is a hundredth of the height of a vowel, so a skipped stretch
    // would be an invisible gap rather than a visible saving. Floor it.
    const tall = Math.max(amp * 2, 3 * devicePixelRatio);
    ctx.fillStyle = spans.length ? (inSpeech(t) ? '#4ade80' : '#2a3441') : '#4b5563';
    ctx.fillRect(i*step, h/2 - tall/2, Math.max(step,1), tall);
  }
}
addEventListener('resize', draw);

// ── loading audio ─────────────────────────────────────────────────
async function useSource(kind, value, label){
  source = {kind, value};
  $('#what').textContent = label;
  $('#text').textContent = 'nothing yet';
  $('#stats').innerHTML = ''; $('#why').textContent = '';
  await runVad();
}

async function runVad(){
  if (!source) return;
  $('#status').textContent = 'finding the speech…';
  const body = new FormData();
  if (source.kind === 'path') body.append('path', source.value);
  else body.append('file', source.value, 'clip.wav');
  const r = await fetch(API + '/vad', {method:'POST', body}).then(r=>r.json());
  $('#status').textContent = '';
  if (r.error){ $('#vadline').textContent = r.error; return; }
  lastVad = r; waveform = r.waveform || []; duration = r.total_s;
  $('#vadline').innerHTML = `<span class="sub">${r.speech_s}s speech of ${r.total_s}s
    &mdash; ${(100 - r.speech_ratio*100).toFixed(0)}% skipped,
    ${r.segments.length} spans packed into ${r.packed.length} window(s)</span>`;
  draw();
}

$('#pick').onclick = () => $('#file').click();
$('#file').onchange = e => { const f = e.target.files[0];
  if (f) useSource('blob', f, f.name + ' (' + (f.size/1024|0) + ' KB)'); };

const drop = $('#drop');
drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over');
  const f = e.dataTransfer.files[0]; if (f) useSource('blob', f, f.name); };
drop.onclick = () => $('#file').click();

$('#load').onclick = () => { const s = $('#sample').value;
  if (s) useSource('path', s, $('#sample').selectedOptions[0].textContent); };

// ── the microphone, encoded here so no ffmpeg is needed ───────────
let recorder = null;
$('#mic').onclick = async () => {
  if (recorder){ recorder.stop(); return; }
  const stream = await navigator.mediaDevices.getUserMedia({audio:{
    channelCount:1, echoCancellation:true, noiseSuppression:false}});
  const ctx = new AudioContext({sampleRate:16000});
  const src = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  node.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(node); node.connect(ctx.destination);
  $('#mic').textContent = 'stop'; $('#mic').classList.add('rec');
  recorder = { stop(){
    node.disconnect(); src.disconnect(); stream.getTracks().forEach(t=>t.stop());
    ctx.close(); recorder = null;
    $('#mic').textContent = 'record'; $('#mic').classList.remove('rec');
    const total = chunks.reduce((n,c)=>n+c.length,0);
    const pcm = new Float32Array(total); let o = 0;
    for (const c of chunks){ pcm.set(c, o); o += c.length; }
    useSource('blob', new Blob([wav(pcm, ctx.sampleRate)], {type:'audio/wav'}),
              `recording, ${(total/16000).toFixed(1)}s`);
  }};
};

function wav(pcm, rate){                       // 16-bit PCM WAV, written by hand
  const b = new ArrayBuffer(44 + pcm.length*2), v = new DataView(b);
  const put = (o,s) => { for (let i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); };
  put(0,'RIFF'); v.setUint32(4, 36+pcm.length*2, true); put(8,'WAVEfmt ');
  v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
  v.setUint32(24,rate,true); v.setUint32(28,rate*2,true); v.setUint16(32,2,true);
  v.setUint16(34,16,true); put(36,'data'); v.setUint32(40, pcm.length*2, true);
  for (let i=0;i<pcm.length;i++)
    v.setInt16(44+i*2, Math.max(-1,Math.min(1,pcm[i]))*32767, true);
  return b;
}

// ── transcribing ──────────────────────────────────────────────────
function stat(k, v, good){
  return `<div class="stat"><div class="k">${k}</div>
          <div class="v ${good?'good':''}">${v}</div></div>`;
}

$('#go').onclick = async () => {
  if (!source){ $('#status').textContent = 'pick some audio first'; return; }
  $('#go').disabled = true; $('#status').textContent = 'transcribing…';
  const body = new FormData();
  if (source.kind === 'path') body.append('path', source.value);
  else body.append('file', source.value, 'clip.wav');
  body.append('engine', $('#engine').value);
  body.append('policy', $('#policy').value);
  body.append('vad_on', flag($('#vadBtn')));
  body.append('pack', flag($('#packBtn')));
  body.append('cache_on', flag($('#cacheBtn')));
  const t0 = performance.now();
  const r = await fetch(API + '/transcribe', {method:'POST', body}).then(r=>r.json());
  $('#go').disabled = false; $('#status').textContent = '';
  if (r.error){ $('#text').innerHTML = `<span class="warn">${r.error}</span>`; return; }
  $('#text').textContent = r.text || '(nothing said)';
  if (r.transcript === false)
    $('#text').innerHTML += `<div class="warn" style="margin-top:8px">
      this engine does not recognise speech — it describes the sound</div>`;
  const s = r.stats;
  $('#why').textContent = `${r.engine} / ${r.model} — ${r.routing.why}`;
  $('#stats').innerHTML = [
    stat('audio', s.audio_s + 's'),
    stat('sent to the model', s.sent_to_model_s + 's'),
    stat('skipped', s.saved_pct + '%', s.saved_pct > 0),
    stat('windows', s.windows + ' of ' + s.speech_spans + ' spans'),
    stat('model time', s.model_s + 's'),
    stat('faster than real time', (s.x_realtime||0) + '×', s.x_realtime > 1),
    stat('from cache', s.from_cache_s + 's', s.from_cache_s > 0),
    stat('cost', '$' + (s.cost_usd||0).toFixed(4)),
  ].join('');
  lastVad = lastVad || null; draw();
};

$('#cmp').onclick = async () => {
  if (!source || source.kind !== 'path'){
    $('#status').textContent = 'compare runs on a sample — load one first'; return; }
  $('#cmp').disabled = true; $('#status').textContent = 'running all three…';
  const r = await get('/compare?path=' + encodeURIComponent(source.value));
  $('#cmp').disabled = false; $('#status').textContent = '';
  if (r.error){ $('#why').textContent = r.error; return; }
  const rows = Object.entries(r.runs).map(([k,v]) =>
    `<tr><td>${k.replace('_',' ')}</td><td>${v.windows}</td><td>${v.sent_s}s</td>
     <td>${v.model_s}s</td><td>${v.rtf}</td></tr>`).join('');
  $('#stats').innerHTML = '';
  $('#text').innerHTML = `<table><tr><th>run</th><th>windows</th><th>sent</th>
    <th>model time</th><th>rtf</th></tr>${rows}</table>
    <div class="note">${r.audio_saved_pct}% less audio sent,
    ${r.time_saved_pct}% less model time than sending the whole file.
    Packing was worth ${r.packing_worth_pct}% on top of trimming.</div>`;
};

// ── the panels ────────────────────────────────────────────────────
(async () => {
  const [s, e] = await Promise.all([get('/samples'), get('/engines')]);
  $('#sample').innerHTML = (s.samples||[]).map(x =>
    `<option value="${x.path}">${x.name} — ${x.seconds}s, ${(x.speech_ratio*100)|0}% speech${x.real?'':' (constructed)'}</option>`).join('');
  const ready = (e.engines||[]).filter(x => x.available);
  $('#ready').textContent = ready.length
    ? ready.filter(x=>x.name!=='stub').map(x=>x.name).join(' · ') || 'no recogniser installed'
    : 'no engine available';
  $('#engine').innerHTML = '<option value="">route for me</option>' +
    (e.engines||[]).map(x => `<option value="${x.name}" ${x.available?'':'disabled'}>
      ${x.name}${x.available?'':' — '+x.note}</option>`).join('');
  $('#engines').innerHTML = `<table><tr><th>engine</th><th>kind</th><th>model</th>
    <th>$/min</th><th>state</th></tr>` + (e.engines||[]).map(x =>
    `<tr><td>${x.name}</td><td class="no">${x.kind||''}</td><td class="no">${x.model||''}</td>
     <td class="no">${x.cost_per_min ? '$'+x.cost_per_min : 'free'}</td>
     <td class="${x.available?'yes':'no'}">${x.available ? 'ready' : x.note}</td></tr>`
    ).join('') + '</table>';
  if (s.samples && s.samples.length) $('#load').click();
})();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    api = API

    def log_message(self, *args: Any) -> None:      # one line per request is plenty
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        target = self.api + self.path[len(BASE) + 5:]
        length = int(self.headers.get('Content-Length') or 0)
        payload = self.rfile.read(length) if length else None
        request = urllib.request.Request(target, data=payload, method=method)
        for header in ('Content-Type', 'Authorization'):
            if self.headers.get(header):
                request.add_header(header, self.headers[header])
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                self._send(response.status, response.read(),
                           response.headers.get('Content-Type', 'application/json'))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), 'application/json')
        except Exception as exc:
            self._send(502, json.dumps({'error': f'api unreachable: {exc}'}).encode(),
                       'application/json')

    def do_GET(self) -> None:
        if self.path.startswith(f'{BASE}/_api'):
            return self._proxy('GET')
        if self.path.rstrip('/') in (BASE, ''):
            return self._send(200, PAGE.encode(), 'text/html; charset=utf-8')
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
    parser.add_argument('--port', type=int, default=50641)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    Handler.api = args.api.rstrip('/')
    print(f'sound2text console  http://{args.host}:{args.port}{BASE}  → {Handler.api}')
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
