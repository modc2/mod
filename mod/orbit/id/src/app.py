"""The console — one file, no dependencies, and it can drive a real wallet.

Serves /id and proxies /id/_api/* to the API, so the browser talks to one origin
and nothing needs CORS. MetaMask and Phantom are driven directly from the page:
the statement the API hands back is passed to `personal_sign` and to Phantom's
`signMessage` unmodified, and the signature comes straight back. Every other
chain is a paste — the statement is shown, you sign it wherever the key lives,
and you paste the result.

No key ever enters this page, and the page never asks for one. The only thing it
holds is a session token, which is the *consent* of an account already in the
identity and expires in an hour.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API = 'http://127.0.0.1:50650'
BASE = '/id'

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>id — one identity, many accounts</title>
<style>
 :root{--bg:#0a0c0f;--panel:#11151a;--sunk:#0d1116;--line:#1e2530;--ink:#dfe6ee;
       --dim:#8593a4;--key:#4ade80;--pub:#fbbf24;--accent:#67e8f9;--bad:#f87171;
       --root:#c084fc}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
      font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:16px 22px;border-bottom:1px solid var(--line);display:flex;
        align-items:center;gap:14px;flex-wrap:wrap}
 h1{font-size:15px;margin:0;letter-spacing:.18em;text-transform:uppercase}
 .sub{color:var(--dim);font-size:12px}
 main{max-width:1080px;margin:0 auto;padding:20px;display:grid;gap:16px}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
 .panel h2{font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;
           color:var(--dim);margin:0 0 12px;display:flex;gap:10px;align-items:center}
 .row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
 button,select,input,textarea{background:var(--sunk);color:var(--ink);
        border:1px solid var(--line);border-radius:7px;padding:8px 12px;
        font:inherit;font-size:13px}
 button{cursor:pointer}
 button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
 button:disabled{opacity:.35;cursor:not-allowed}
 button.go{border-color:var(--key);color:var(--key)}
 button.thin{padding:5px 9px;font-size:12px}
 input,textarea{flex:1;min-width:180px}
 .wide{width:100%;margin-top:8px}
 textarea{width:100%;min-height:78px;resize:vertical;white-space:pre;overflow-x:auto}
 .acct{display:flex;gap:12px;align-items:center;padding:10px 12px;background:var(--sunk);
       border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
 .acct .glyph{width:26px;height:26px;flex:none;border-radius:6px;display:grid;
       place-items:center;font-size:10px;letter-spacing:.02em;border:1px solid var(--line);
       color:var(--dim);text-transform:uppercase}
 .acct .who{flex:1;min-width:0}
 .acct .addr{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .acct .meta{font-size:11px;color:var(--dim);margin-top:2px}
 .tag{font-size:10px;padding:2px 7px;border-radius:999px;border:1px solid var(--line);
      letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}
 .tag.key{color:var(--key);border-color:#1d4a30}
 .tag.pub{color:var(--pub);border-color:#4a3c15}
 .tag.root{color:var(--root);border-color:#3d2a52}
 .empty{color:var(--dim);padding:14px 0}
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12.5px;color:var(--dim)}
 .stmt{background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:12px;
       white-space:pre;overflow-x:auto;font-size:12.5px;line-height:1.6}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);
       vertical-align:top}
 th{color:var(--dim);font-weight:400;font-size:10px;letter-spacing:.12em;
    text-transform:uppercase}
 .ok{color:var(--key)} .no{color:var(--bad)} .dim{color:var(--dim)}
 .note{color:var(--dim);font-size:12px;margin-top:10px}
 .flash{padding:10px 12px;border-radius:8px;border:1px solid var(--line);
        background:var(--sunk);font-size:12.5px;margin-top:10px}
 .flash.bad{border-color:#4a1f1f;color:#fca5a5}
 .flash.good{border-color:#1d4a30;color:#86efac}
 .id{color:var(--accent)}
 .step{display:none} .step.on{display:block}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:9px}
 .tile{background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
 .tile .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
 .tile .v{font-size:17px;margin-top:3px}
 a{color:var(--accent)}
</style></head><body>
<header>
  <h1>id</h1>
  <span class="sub">one identity &mdash; many accounts &mdash; every one of them signed</span>
  <span class="sub" id="who" style="margin-left:auto"></span>
</header>
<main>

<section class="panel">
  <h2>connect an account</h2>
  <div class="row">
    <button id="mm">MetaMask</button>
    <button id="ph">Phantom</button>
    <button id="manual">any other chain &mdash; paste a signature</button>
    <button id="pub">GitHub / X / a domain</button>
    <span class="sub" id="connhint"></span>
  </div>

  <div class="step" id="stepManual" style="margin-top:12px">
    <div class="row">
      <select id="kind"></select>
      <input id="addr" placeholder="address or handle" spellcheck="false">
      <button id="ask" class="go">get the text to sign</button>
    </div>
    <div id="askout"></div>
  </div>
  <div id="flash"></div>
</section>

<section class="panel">
  <h2>this identity <span id="idname" class="id"></span></h2>
  <div id="me"><div class="empty">Nothing linked yet. Connect a wallet above &mdash; the
   first one to sign creates the identity, and every account after that needs its
   consent as well as its own signature.</div></div>
  <div class="row" id="idactions" style="margin-top:10px"></div>
</section>

<section class="panel">
  <h2>the log, re-checked</h2>
  <div class="row">
    <button id="doaudit" class="thin">re-verify every signature</button>
    <span class="sub">replayed offline, from the stored statements &mdash; nothing is trusted because it is written down</span>
  </div>
  <div id="auditout"></div>
</section>

<section class="panel">
  <h2>what can be linked</h2>
  <div id="chains"><div class="empty">loading&hellip;</div></div>
</section>

<section class="panel">
  <h2>everything on this host</h2>
  <div id="all"><div class="empty">loading&hellip;</div></div>
</section>

</main>
<script>
const API = location.pathname.replace(/\/$/,'') + '/_api';
const SKEY = 'mod_id_session', IKEY = 'mod_id_current';
let CHAINS = [], SERVICES = [], PENDING = null;

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => { const n = document.createElement(tag);
  if (cls) n.className = cls; if (txt !== undefined) n.textContent = txt; return n; };
const shorten = s => s && s.length > 26 ? s.slice(0,12) + '…' + s.slice(-8) : s;

async function call(path, options) {
  const r = await fetch(API + path, options);
  const body = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
  return body;
}
const post = (path, body) => call(path, {method:'POST',
  headers:{'content-type':'application/json'}, body: JSON.stringify(body)});

function flash(message, good) {
  const box = $('#flash'); box.innerHTML = '';
  const node = el('div', 'flash ' + (good ? 'good' : 'bad'), message);
  box.appendChild(node);
  if (good) setTimeout(() => node.remove(), 9000);
}

// ── base58, for Phantom's signature ────────────────────────────────
const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58(bytes) {
  const digits = [0];
  for (const byte of bytes) {
    let carry = byte;
    for (let i = 0; i < digits.length; i++) {
      carry += digits[i] << 8; digits[i] = carry % 58; carry = (carry / 58) | 0;
    }
    while (carry) { digits.push(carry % 58); carry = (carry / 58) | 0; }
  }
  let out = '';
  for (const byte of bytes) { if (byte) break; out += '1'; }
  for (let i = digits.length - 1; i >= 0; i--) out += B58[digits[i]];
  return out;
}
const hex = bytes => '0x' + [...bytes].map(b => b.toString(16).padStart(2,'0')).join('');

// ── the two-step dance, whatever the wallet ────────────────────────
async function prove(kind, handle, sign, extra) {
  const body = Object.assign({chain: kind, address: handle}, extra || {});
  const id = localStorage.getItem(IKEY);
  if (id && !body.id && (!body.op || body.op === 'link')) body.id = id;
  const ask = await post('/challenge', body);
  const signed = await sign(ask);
  const answer = await post('/submit', Object.assign(
    {nonce: ask.nonce, session: localStorage.getItem(SKEY) || undefined}, signed));
  if (answer.session) localStorage.setItem(SKEY, answer.session);
  if (answer.id) localStorage.setItem(IKEY, answer.id);
  await refresh();
  return answer;
}

async function metamask() {
  if (!window.ethereum) return flash('No injected Ethereum wallet in this browser.');
  const [account] = await window.ethereum.request({method: 'eth_requestAccounts'});
  const answer = await prove('ethereum', account, async ask => {
    const message = '0x' + [...new TextEncoder().encode(ask.statement)]
      .map(b => b.toString(16).padStart(2,'0')).join('');
    return {signature: await window.ethereum.request(
      {method: 'personal_sign', params: [message, account]})};
  });
  flash(said(answer), true);
}

async function phantom() {
  const provider = (window.phantom && window.phantom.solana) || window.solana;
  if (!provider) return flash('No Solana wallet in this browser.');
  const { publicKey } = await provider.connect();
  const address = publicKey.toString();
  const answer = await prove('solana', address, async ask => {
    const signed = await provider.signMessage(
      new TextEncoder().encode(ask.statement), 'utf8');
    return {signature: b58(signed.signature || signed)};
  });
  flash(said(answer), true);
}

const said = a => a.op === 'genesis'
  ? 'New identity ' + a.id + ' — created by ' + shorten(a.account)
  : a.op === 'link' ? shorten(a.account) + ' joined ' + a.id +
      ', with ' + shorten(a.authorized_by) + ' consenting'
  : a.note || (a.op + ' — ok');

// ── the paste flow ─────────────────────────────────────────────────
function fillKinds(publication) {
  const select = $('#kind'); select.innerHTML = '';
  const list = publication
    ? SERVICES.map(s => [s.service, s.title + ' — ' + s.where])
    : CHAINS.map(c => [c.chain, c.title]);
  for (const [value, label] of list) {
    const option = el('option', null, label); option.value = value; select.appendChild(option);
  }
  $('#addr').placeholder = publication ? 'your handle, or the domain' : 'the address';
  $('#stepManual').classList.add('on');
  $('#askout').innerHTML = '';
}

async function askFor() {
  const kind = $('#kind').value, handle = $('#addr').value.trim();
  if (!handle) return flash('Put the address or handle in first.');
  const id = localStorage.getItem(IKEY);
  let ask;
  try { ask = await post('/challenge', {chain: kind, address: handle, id: id || undefined}); }
  catch (e) { return flash(e.message); }
  PENDING = ask;
  const out = $('#askout'); out.innerHTML = '';
  out.appendChild(el('div', 'note', ask.how));
  const chain = CHAINS.find(c => c.chain === ask.kind);
  if (chain) out.appendChild(el('div', 'note', 'what gets hashed: ' + chain.scheme));

  if (ask.strength === 'key') {
    out.appendChild(el('div', 'stmt', ask.statement));
    const sig = el('textarea'); sig.placeholder = 'paste the signature'; sig.id = 'sig';
    out.appendChild(sig);
    if (ask.needs_pubkey) {
      const key = el('input', 'wide');
      key.placeholder = 'public key — this chain cannot recover it from the signature';
      key.id = 'pk'; out.appendChild(key);
    }
  } else {
    out.appendChild(el('div', 'stmt', ask.token));
    out.appendChild(el('div', 'note', ask.hint || ''));
    const src = el('input', 'wide');
    src.placeholder = 'link to where you published it (X needs this)';
    src.id = 'src'; out.appendChild(src);
  }
  const row = el('div', 'row'); row.style.marginTop = '10px';
  const copy = el('button', 'thin', 'copy the text');
  copy.onclick = () => navigator.clipboard.writeText(
    ask.strength === 'key' ? ask.statement : ask.token);
  const send = el('button', 'go', 'submit the proof');
  send.onclick = async () => {
    try {
      const body = {nonce: ask.nonce, session: localStorage.getItem(SKEY) || undefined};
      if (ask.strength === 'key') {
        body.signature = ($('#sig').value || '').trim();
        if ($('#pk')) body.pubkey = ($('#pk').value || '').trim();
      } else if ($('#src')) body.source = ($('#src').value || '').trim();
      const answer = await post('/submit', body);
      if (answer.session) localStorage.setItem(SKEY, answer.session);
      if (answer.id) localStorage.setItem(IKEY, answer.id);
      out.innerHTML = ''; $('#addr').value = '';
      flash(said(answer), true);
      await refresh();
    } catch (e) { flash(e.message); }
  };
  row.appendChild(copy); row.appendChild(send); out.appendChild(row);
  out.appendChild(el('div', 'note', 'expires ' + ask.expires));
}

// ── drawing ────────────────────────────────────────────────────────
function drawIdentity(doc) {
  const box = $('#me'); box.innerHTML = '';
  $('#idname').textContent = doc ? (doc.id + (doc.name ? ' · ' + doc.name : '')) : '';
  const actions = $('#idactions'); actions.innerHTML = '';
  if (!doc) {
    box.appendChild(el('div', 'empty', 'Nothing linked yet. Connect a wallet above.'));
    return;
  }
  const tiles = el('div', 'grid');
  for (const [label, value] of [['accounts', doc.count],
        ['chains', doc.chains.length], ['proved by key', doc.by_strength.key],
        ['proved by publishing', doc.by_strength.publication]]) {
    const tile = el('div', 'tile');
    tile.appendChild(el('div', 'k', label)); tile.appendChild(el('div', 'v', value));
    tiles.appendChild(tile);
  }
  box.appendChild(tiles);
  const list = el('div'); list.style.marginTop = '12px';
  for (const account of doc.accounts) {
    const row = el('div', 'acct');
    row.appendChild(el('div', 'glyph', account.kind.slice(0,3)));
    const who = el('div', 'who');
    who.appendChild(el('div', 'addr', account.address));
    who.appendChild(el('div', 'meta', account.kind
      + (account.via ? ' · let in by ' + shorten(account.via) : ' · founder')
      + ' · ' + new Date(account.linked_at * 1000).toLocaleString()));
    row.appendChild(who);
    if (account.account === doc.root) row.appendChild(el('span', 'tag root', 'root'));
    row.appendChild(el('span', 'tag ' + (account.strength === 'key' ? 'key' : 'pub'),
      account.strength));
    list.appendChild(row);
  }
  box.appendChild(list);
  if (doc.also_known_as && doc.also_known_as.length)
    box.appendChild(el('div', 'note', 'also answers to: ' + doc.also_known_as.join(', ')));

  const forget = el('button', 'thin', 'forget this identity in this browser');
  forget.onclick = () => { localStorage.removeItem(IKEY); localStorage.removeItem(SKEY);
                           refresh(); };
  const dump = el('button', 'thin', 'export');
  dump.onclick = async () => {
    const doc2 = await call('/export/' + doc.id);
    const blob = new Blob([JSON.stringify(doc2, null, 2)], {type: 'application/json'});
    const a = el('a'); a.href = URL.createObjectURL(blob); a.download = doc.id + '.json';
    a.click();
  };
  actions.appendChild(forget); actions.appendChild(dump);
  actions.appendChild(el('span', 'sub',
    'the export holds every proof and no secret — another host re-checks it on import'));
}

function drawAudit(report) {
  const box = $('#auditout'); box.innerHTML = '';
  const head = el('div', 'flash ' + (report.ok ? 'good' : 'bad'),
    report.ok ? 'All ' + report.events + ' events re-verified. '
              : 'Something does not check out.');
  box.appendChild(head);
  const table = el('table');
  table.innerHTML = '<tr><th>#</th><th>event</th><th>account</th><th>proof</th><th></th></tr>';
  for (const row of report.checked) {
    const tr = el('tr');
    tr.innerHTML = '<td>' + row.seq + '</td><td>' + row.op + '</td>'
      + '<td class="dim">' + shorten(row.account || '') + '</td>'
      + '<td>' + (row.strength || '') + '</td>'
      + '<td class="' + (row.ok ? 'ok' : 'no') + '">'
      + (row.ok ? 'verified' : row.problems.join('; ')) + '</td>';
    table.appendChild(tr);
  }
  box.appendChild(table);
  box.appendChild(el('div', 'note', report.means));
}

function drawChains() {
  const box = $('#chains'); box.innerHTML = '';
  const table = el('table');
  table.innerHTML = '<tr><th>chain</th><th>curve</th><th>what the wallet signs</th></tr>';
  for (const chain of CHAINS) {
    const tr = el('tr');
    tr.innerHTML = '<td>' + chain.title + '</td><td class="dim">' + chain.curve + '</td>'
      + '<td class="dim">' + chain.scheme
      + (chain.note ? '<br><span style="color:#5d6b7c">' + chain.note + '</span>' : '')
      + '</td>';
    table.appendChild(tr);
  }
  for (const service of SERVICES) {
    const tr = el('tr');
    tr.innerHTML = '<td>' + service.title + '</td><td class="dim">no key</td>'
      + '<td class="dim">publishes a token to ' + service.where
      + '<br><span style="color:#5d6b7c">holds only while that stays up</span></td>';
    table.appendChild(tr);
  }
  box.appendChild(table);
}

function drawAll(rows) {
  const box = $('#all'); box.innerHTML = '';
  if (!rows.length) { box.appendChild(el('div', 'empty', 'no identities yet')); return; }
  const table = el('table');
  table.innerHTML = '<tr><th>id</th><th>name</th><th>accounts</th><th>chains</th></tr>';
  for (const row of rows) {
    const tr = el('tr');
    tr.innerHTML = '<td class="id">' + row.id + '</td><td>' + (row.name || '')
      + '</td><td>' + row.count + '</td><td class="dim">'
      + row.chains.concat(row.services).join(', ') + '</td>';
    tr.style.cursor = 'pointer';
    tr.onclick = () => { localStorage.setItem(IKEY, row.id); refresh(); };
    table.appendChild(tr);
  }
  box.appendChild(table);
}

async function refresh() {
  const id = localStorage.getItem(IKEY);
  let doc = null;
  if (id) {
    try { doc = await call('/id/' + id); }
    catch (e) { localStorage.removeItem(IKEY); }
  }
  drawIdentity(doc);
  const session = localStorage.getItem(SKEY);
  $('#who').textContent = doc
    ? (doc.count + ' account' + (doc.count === 1 ? '' : 's') + (session ? ' · session held' : ''))
    : 'not connected';
  drawAll(await call('/ids'));
}

(async function start() {
  [CHAINS, SERVICES] = await Promise.all([call('/chains'), call('/services')]);
  drawChains();
  $('#mm').onclick = () => metamask().catch(e => flash(e.message));
  $('#ph').onclick = () => phantom().catch(e => flash(e.message));
  $('#manual').onclick = () => fillKinds(false);
  $('#pub').onclick = () => fillKinds(true);
  $('#ask').onclick = () => askFor().catch(e => flash(e.message));
  $('#doaudit').onclick = async () => {
    const id = localStorage.getItem(IKEY);
    if (!id) return flash('No identity selected.');
    try { drawAudit(await call('/id/' + id + '/audit')); }
    catch (e) { flash(e.message); }
  };
  $('#connhint').textContent = window.ethereum ? '' : 'no injected wallet here — paste works for everything';
  await refresh();
})();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    api = API

    def log_message(self, fmt: str, *args: Any) -> None:      # quiet
        pass

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        tail = self.path[len(BASE) + len('/_api'):] or '/'
        length = int(self.headers.get('Content-Length') or 0)
        payload = self.rfile.read(length) if length else None
        request = urllib.request.Request(self.api + tail, data=payload, method=method)
        request.add_header('Content-Type', 'application/json')
        for header in ('Authorization',):
            if self.headers.get(header):
                request.add_header(header, self.headers[header])
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._send(response.status, response.read(), 'application/json')
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read() or b'{}', 'application/json')
        except Exception as exc:                              # the API is not up
            self._send(502, json.dumps({'error': f'the API is not answering: {exc}'}
                                       ).encode(), 'application/json')

    def do_GET(self) -> None:
        if self.path.startswith(BASE + '/_api'):
            return self._proxy('GET')
        if self.path.rstrip('/') in (BASE, ''):
            return self._send(200, PAGE.encode(), 'text/html; charset=utf-8')
        if self.path == '/':
            self.send_response(302)
            self.send_header('Location', BASE)
            self.end_headers()
            return
        self._send(404, b'not here', 'text/plain')

    def do_POST(self) -> None:
        if self.path.startswith(BASE + '/_api'):
            return self._proxy('POST')
        self._send(404, b'not here', 'text/plain')

    def do_DELETE(self) -> None:
        if self.path.startswith(BASE + '/_api'):
            return self._proxy('DELETE')
        self._send(404, b'not here', 'text/plain')


def main() -> None:
    parser = argparse.ArgumentParser(description='id — the console')
    parser.add_argument('--port', type=int, default=50651)
    parser.add_argument('--api', default=API)
    options = parser.parse_args()
    Handler.api = options.api
    server = ThreadingHTTPServer(('0.0.0.0', options.port), Handler)
    print(f'id console on http://localhost:{options.port}{BASE} → {options.api}')
    server.serve_forever()


if __name__ == '__main__':
    main()
