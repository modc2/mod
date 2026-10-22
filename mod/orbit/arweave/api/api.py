"""
Arweave API — FastAPI server exposing the ArweaveClient and serving the static app.

Run via the module:
    m arweave/serve

Or directly:
    uvicorn api:app --port 50153

Surfaces (all on one port):
    /            static web app          (gateway: modc2.com/arweave)
    /api/*       REST                    (gateway: /arweave/api/*)
    /api/mcp     MCP Streamable HTTP     (gateway: /arweave/api/mcp)
    /docs        swagger
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import requests as _requests

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

from arlocal import ArweaveClient  # noqa: E402
from arlocal import bundle as ar_bundle  # noqa: E402
from arlocal.client import WALLET_PATH  # noqa: E402
import mcp_server as arweave_mcp  # noqa: E402  (api/mcp_server.py; not pip `mcp`, see import-shadowing note)


app = FastAPI(
    title="Arweave Mod API",
    description="Store and retrieve data on Arweave via a public gateway, with optional wallet uploads.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = ArweaveClient()
APP_DIR = MODULE_DIR / "app"
BASE = "/arweave"


def load_config() -> dict:
    cfg_path = MODULE_DIR / "config.json"
    return json.loads(cfg_path.read_text()) if cfg_path.exists() else {}


arweave_mcp.bind(client, load_config)


@app.middleware("http")
async def _strip_mod_prefix(request: Request, call_next):
    # The gateway app route proxies {host}/arweave with the prefix KEPT;
    # serve both shapes so / and /arweave are the same app.
    path = request.scope["path"]
    if path == BASE or path.startswith(BASE + "/"):
        request.scope["path"] = path[len(BASE):] or "/"
    return await call_next(request)


# ─── API routes (at /api locally; the gateway strips /arweave/api to bare) ──

api = APIRouter()


@api.get("/health")
def health():
    return {"ok": True, "service": "arweave"}


@api.get("/info")
def info():
    cfg = load_config()
    return {
        "name": cfg.get("name", "arweave"),
        "version": cfg.get("version"),
        "description": cfg.get("description"),
        "endpoints": list((cfg.get("endpoints") or {}).keys()),
        "gateway": client.gateway,
        "wallet": bool(client.wallet_path),
        "mcp": {"tools": sorted(arweave_mcp.TOOLS), "urls": (cfg.get("mcp") or {})},
    }


@api.get("/status")
def status():
    return client.status()


@api.post("/put")
async def put(request: Request, file: Optional[UploadFile] = File(None), tags: Optional[str] = Form(None)):
    """Accepts JSON body `{data, name?, tags?}` OR a multipart upload field `file`."""
    if file is not None:
        payload = await file.read()
        extra_tags = None
        if tags:
            try:
                raw_tags = json.loads(tags)
                pairs = [
                    (t["name"], t["value"])
                    for t in raw_tags
                    if isinstance(t, dict) and t.get("name") and t.get("value") is not None
                ]
                if pairs:
                    extra_tags = pairs
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        return client._upload_bytes(payload, file.filename or "upload.bin", extra_tags=extra_tags)

    body: Any
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        if not raw:
            raise HTTPException(400, "empty body")
        return client._upload_bytes(raw, "upload.bin")

    extra_tags = None
    if isinstance(body, dict):
        raw_tags = body.get("tags") or []
        pairs = [
            (t["name"], t["value"])
            for t in raw_tags
            if isinstance(t, dict) and t.get("name") and t.get("value") is not None
        ]
        if pairs:
            extra_tags = pairs

    if isinstance(body, dict) and "data" in body:
        return client.put(body["data"], name=body.get("name"), extra_tags=extra_tags)
    return client.put(body, extra_tags=extra_tags)


@api.post("/archive")
async def archive(request: Request):
    """Fetch a URL and store its content on Arweave, auto-tagging Source-URL and App-Name."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON {url: string}")
    url = str(body.get("url") or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(400, "url must start with http:// or https://")
    try:
        r = _requests.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
    except _requests.RequestException as e:
        raise HTTPException(502, f"fetch failed: {e}")
    content_type = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    content = r.content
    if content_type == "text/html":
        html_str = content.decode("utf-8", errors="replace")
        html_str = re.sub(
            r'(<head[^>]*>)',
            lambda m: m.group(1) + f'<base href="{url}">',
            html_str, count=1, flags=re.IGNORECASE,
        )
        content = html_str.encode("utf-8")
    extra_tags = [("Source-URL", url), ("App-Name", "mod-arweave")]
    return client._upload_bytes(content, url, content_type, extra_tags=extra_tags)


@api.post("/manifest")
async def create_manifest(request: Request):
    """Build and store an Arweave path manifest for multi-file static site hosting.

    Body: {paths: [{path: "index.html", txid: "abc..."}, ...], index?: "index.html"}
    Returns: {txid, url: "https://arweave.net/{txid}/"}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON {paths: [{path, txid}], index?}")
    paths_input = body.get("paths")
    if not paths_input or not isinstance(paths_input, list):
        raise HTTPException(400, "paths must be a non-empty array of {path, txid}")
    manifest_paths: dict = {}
    for entry in paths_input:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("txid"):
            raise HTTPException(400, "each paths entry must have path and txid fields")
        manifest_paths[entry["path"]] = {"id": entry["txid"]}
    index_path = body.get("index") or (paths_input[0]["path"] if paths_input else "index.html")
    manifest = {
        "manifest": "arweave/paths",
        "version": "0.1.0",
        "index": {"path": index_path},
        "paths": manifest_paths,
    }
    result = client.put(manifest, name="manifest.json",
                        content_type="application/x.arweave-manifest+json")
    out: dict = {"txid": result["txid"], "url": f"https://arweave.net/{result['txid']}/",
                 "backend": result.get("backend")}
    if result.get("warning"):
        out["warning"] = result["warning"]
    return out


@api.get("/get/{txid}")
def get(txid: str, raw: bool = False):
    try:
        content, gateway_ct = client._fetch_with_ct(txid)
    except Exception as e:
        raise HTTPException(404, str(e))
    if raw:
        return Response(content=content, media_type="application/octet-stream")
    # Prefer the locally-indexed content type; fall back to what the gateway sent.
    stored_ct = client._get_stored_content_type(txid)
    effective_ct = stored_ct or gateway_ct
    if effective_ct:
        if effective_ct == "application/json":
            try:
                return JSONResponse(json.loads(content.decode()))
            except Exception:
                pass
        return Response(content=content, media_type=effective_ct)
    # Fall through to guess logic for Arweave-native txids not in the local index
    try:
        text = content.decode()
    except UnicodeDecodeError:
        return Response(content=content, media_type="application/octet-stream")
    try:
        return JSONResponse(json.loads(text))
    except json.JSONDecodeError:
        return Response(content=text, media_type="text/plain; charset=utf-8")


@api.get("/list")
def list_objects(limit: int = 100):
    return {"items": client.list(limit=limit)}


@api.delete("/rm/{txid}")
def rm(txid: str):
    return client.rm(txid)


@api.get("/tx/{txid}")
def tx(txid: str):
    return client.tx(txid)


@api.get("/tx/{txid}/confirm")
def tx_confirm(txid: str):
    return client.tx_confirm_status(txid)


@api.get("/price/{num_bytes}")
def price(num_bytes: int):
    return client.price(num_bytes)


@api.get("/network")
def network():
    return client.info()


@api.get("/query")
def query(
    owner: Optional[str] = None,
    tag_name: Optional[str] = None,
    tag_value: Optional[str] = None,
    first: int = 10,
    after: Optional[str] = None,
):
    tags = None
    if tag_name:
        tags = [{"name": tag_name, "values": [tag_value] if tag_value else []}]
    try:
        edges = client.query(owner=owner, tags=tags, first=first, after=after)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"edges": edges}


@api.get("/view/{txid}", response_class=HTMLResponse)
def view_tx(txid: str):
    """Self-contained inline viewer for a stored item."""
    txid_js = json.dumps(txid)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Arweave · {txid[:12]}…</title>
<meta property="og:title" content="Arweave · {txid[:12]}…">
<meta property="og:type" content="website">
<meta property="og:url" content="https://arweave.net/{txid}">
<meta property="og:image" content="https://arweave.net/{txid}">
<meta property="og:description" content="Permanent content on Arweave">
<meta property="og:site_name" content="arweave mod">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Arweave · {txid[:12]}…">
<meta name="twitter:image" content="https://arweave.net/{txid}">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #0b0d10; color: #d8dee4; max-width: 960px; margin-inline: auto; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; letter-spacing: 0.5px; }}
  h2 {{ font-size: 13px; margin: 24px 0 8px; color: #7a8896; text-transform: uppercase; letter-spacing: 1px; }}
  .sub {{ color: #7a8896; font-size: 12px; margin-bottom: 16px; }}
  .card {{ background: #14181d; border: 1px solid #20262d; border-radius: 8px; padding: 14px; margin-bottom: 12px; }}
  pre {{ background: #0b0d10; border: 1px solid #20262d; border-radius: 6px; padding: 10px; overflow: auto; max-height: 500px; font-size: 12px; margin: 0; white-space: pre-wrap; word-break: break-word; }}
  .txid {{ color: #c0a8ff; word-break: break-all; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; background: #1c2030; color: #c0a8ff; }}
  .pill.warn {{ background: #2a1f12; color: #f5a142; }}
  .pill.bad  {{ background: #2a1418; color: #ff6b6b; }}
  button {{ background: #5d3aa8; color: white; border: 0; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-family: inherit; font-size: 13px; }}
  button:hover {{ background: #7150c8; }}
  button.ghost {{ background: transparent; color: #7a8896; border: 1px solid #20262d; }}
  button.ghost:hover {{ color: #d8dee4; border-color: #2c343d; }}
  a {{ color: #c0a8ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .meta-grid {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 12px; align-items: start; }}
  .meta-key {{ color: #7a8896; padding-top: 2px; }}
  .tag-pills {{ display: flex; flex-wrap: wrap; gap: 3px; }}
</style>
</head>
<body>
  <h1>arweave viewer</h1>
  <div class="sub txid" id="txid-display"></div>

  <div class="row" style="margin-bottom:16px">
    <div id="permalink-area"></div>
    <a href="../" style="font-size:12px;color:#7a8896">← back</a>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Content</h2>
    <div id="content-loading" style="color:#7a8896;font-size:12px">loading…</div>
    <div id="content-area" hidden></div>
  </div>

  <div class="card" id="meta-card" hidden>
    <h2 style="margin-top:0">Transaction</h2>
    <div class="meta-grid" id="meta-grid"></div>
  </div>

<script>
const TXID = {txid_js};
const API = (location.pathname.replace(/\\/view\\/.*$/, '') || '') + '/api';
const isLocal = TXID.startsWith('sha256-');

document.getElementById('txid-display').textContent = TXID;
document.title = 'arweave · ' + TXID.slice(0, 12) + '…';

function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

async function init() {{
  const permalinkArea = document.getElementById('permalink-area');
  if (isLocal) {{
    permalinkArea.innerHTML = '<span class="pill warn">local only · not on Arweave</span>';
  }} else {{
    const url = 'https://arweave.net/' + TXID;
    permalinkArea.innerHTML =
      '<button class="ghost" id="copy-btn" style="font-size:12px;padding:4px 10px" onclick="copyPermalink()">Copy permalink</button> ' +
      '<a href="' + esc(url) + '" target="_blank" class="pill" style="font-size:11px">' + esc(url) + '</a>';
  }}

  const [contentP, txP, confirmP] = await Promise.allSettled([
    fetch(API + '/get/' + TXID),
    fetch(API + '/tx/' + TXID).then(r => r.json()).catch(() => null),
    fetch(API + '/tx/' + TXID + '/confirm').then(r => r.json()).catch(() => null),
  ]);

  if (contentP.status === 'fulfilled') {{
    await renderContent(contentP.value);
  }} else {{
    document.getElementById('content-loading').textContent = 'error: ' + String(contentP.reason);
  }}

  const txData = txP.status === 'fulfilled' ? txP.value : null;
  const confirmData = confirmP.status === 'fulfilled' ? confirmP.value : null;
  renderMeta(txData, confirmData);
}}

async function renderContent(res) {{
  const loadEl = document.getElementById('content-loading');
  const areaEl = document.getElementById('content-area');
  const ct = (res.headers.get('content-type') || '').split(';')[0].trim();
  try {{
    if (!res.ok) {{
      const msg = await res.text().catch(() => res.status);
      loadEl.textContent = 'error ' + res.status + ': ' + msg;
      return;
    }}
    loadEl.hidden = true;
    areaEl.hidden = false;
    if (ct.startsWith('image/')) {{
      const blob = await res.blob();
      const img = document.createElement('img');
      img.src = URL.createObjectURL(blob);
      img.style.cssText = 'max-width:100%;max-height:500px;display:block;border-radius:4px';
      areaEl.appendChild(img);
    }} else if (ct === 'application/x.arweave-manifest+json') {{
      const mf = await res.json();
      const mfPaths = mf.paths || {{}};
      const siteUrl = 'https://arweave.net/' + TXID + '/';
      const openA = document.createElement('a');
      openA.href = siteUrl;
      openA.target = '_blank';
      openA.textContent = 'Open site ↗';
      openA.style.cssText = 'display:inline-block;padding:4px 12px;background:#5d3aa8;color:white;border-radius:6px;font-size:12px;text-decoration:none;margin-bottom:10px';
      areaEl.appendChild(openA);
      const mfTbl = document.createElement('table');
      mfTbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:12px';
      const mfThead = document.createElement('thead');
      mfThead.innerHTML = '<tr><th style="text-align:left;padding:6px 8px;border-bottom:1px solid #20262d;color:#7a8896">path</th><th style="text-align:left;padding:6px 8px;border-bottom:1px solid #20262d;color:#7a8896">tx id</th></tr>';
      const mfTbody = document.createElement('tbody');
      Object.entries(mfPaths).forEach(([p, v]) => {{
        const itemTxid = (v || {{}}).id || '';
        const viewBase = location.pathname.replace(/\\/view\\/.*$/, '') || '';
        const viewUrl = viewBase + '/view/' + itemTxid;
        const tr = document.createElement('tr');
        const td1 = document.createElement('td');
        td1.style.cssText = 'padding:6px 8px;border-bottom:1px solid #20262d';
        const pathA = document.createElement('a');
        pathA.href = viewUrl;
        pathA.textContent = p;
        td1.appendChild(pathA);
        const td2 = document.createElement('td');
        td2.style.cssText = 'padding:6px 8px;border-bottom:1px solid #20262d;font-size:11px';
        const txA = document.createElement('a');
        txA.href = viewUrl;
        txA.className = 'txid';
        txA.textContent = itemTxid.slice(0, 12) + '…';
        td2.appendChild(txA);
        tr.appendChild(td1);
        tr.appendChild(td2);
        mfTbody.appendChild(tr);
      }});
      mfTbl.appendChild(mfThead);
      mfTbl.appendChild(mfTbody);
      areaEl.appendChild(mfTbl);
    }} else if (ct === 'application/json') {{
      const json = await res.json();
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(json, null, 2);
      areaEl.appendChild(pre);
    }} else if (ct === 'text/html') {{
      const text = await res.text();
      const iframe = document.createElement('iframe');
      iframe.setAttribute('sandbox', 'allow-same-origin allow-popups');
      iframe.srcdoc = text;
      iframe.style.cssText = 'width:100%;height:420px;border:1px solid #20262d;border-radius:6px;background:#fff;display:block;margin-top:6px';
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'ghost';
      toggleBtn.style.cssText = 'font-size:11px;padding:2px 8px';
      toggleBtn.textContent = 'show source';
      let srcPre = null;
      toggleBtn.onclick = function() {{
        if (!srcPre) {{ srcPre = document.createElement('pre'); srcPre.textContent = text; }}
        const showingSrc = !iframe.hidden;
        iframe.hidden = showingSrc;
        srcPre.hidden = !showingSrc;
        if (!areaEl.contains(srcPre)) areaEl.appendChild(srcPre);
        toggleBtn.textContent = showingSrc ? 'show preview' : 'show source';
      }};
      areaEl.appendChild(toggleBtn);
      areaEl.appendChild(iframe);
    }} else if (ct.startsWith('video/')) {{
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const video = document.createElement('video');
      video.controls = true;
      video.src = url;
      video.style.cssText = 'max-width:100%;border-radius:6px;display:block';
      const a = document.createElement('a');
      a.href = url;
      a.download = TXID;
      a.textContent = 'Download video';
      a.className = 'txid';
      a.style.cssText = 'display:block;margin-top:4px;font-size:12px';
      areaEl.appendChild(video);
      areaEl.appendChild(a);
    }} else if (ct.startsWith('audio/')) {{
      const blob = await res.blob();
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.src = URL.createObjectURL(blob);
      audio.style.cssText = 'width:100%';
      areaEl.appendChild(audio);
    }} else if (ct.startsWith('text/')) {{
      const text = await res.text();
      const pre = document.createElement('pre');
      pre.textContent = text;
      areaEl.appendChild(pre);
    }} else {{
      const buf = await res.arrayBuffer();
      const hashBuf = await crypto.subtle.digest('SHA-256', buf);
      const hashHex = Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2,'0')).join('');
      const div = document.createElement('div');
      div.style.cssText = 'color:#7a8896;font-size:12px';
      div.innerHTML = buf.byteLength.toLocaleString() + ' bytes · SHA-256: <span class="txid">' + esc(hashHex) + '</span>';
      areaEl.appendChild(div);
    }}
  }} catch(e) {{
    loadEl.hidden = false;
    loadEl.textContent = 'error: ' + String(e);
    areaEl.hidden = true;
  }}
}}

function confBadge(confirm) {{
  if (!confirm) return '';
  const s = confirm.status;
  if (s === 'confirmed')
    return '<span class="pill" style="background:#0f2010;color:#6abf6a">✓ ' + esc(confirm.confirmations) + ' confs</span>';
  if (s === 'bundler_accepted')
    return '<span class="pill" style="background:#0f1a10;color:#7abf5a">✓ turbo (pending chain)</span>';
  if (s === 'pending')
    return '<span class="pill warn">⏳ pending</span>';
  if (s === 'local')
    return '<span class="pill warn">local only</span>';
  return '<span class="pill bad">' + esc(s || 'unknown') + '</span>';
}}

function renderMeta(tx, confirm) {{
  const card = document.getElementById('meta-card');
  const grid = document.getElementById('meta-grid');
  const rows = [];

  const badge = confBadge(confirm);
  if (badge) rows.push(['status', badge]);

  if (confirm && confirm.block_height)
    rows.push(['block', String(confirm.block_height)]);

  const dataSize = tx && !tx.error && (tx.data_size || (tx.data && tx.data.size));
  if (dataSize) rows.push(['size', Number(dataSize).toLocaleString() + ' bytes']);

  if (tx && !tx.error && tx.owner) {{
    const o = String(tx.owner);
    const short = o.slice(0, 10) + '…' + o.slice(-6);
    rows.push(['owner',
      '<span style="cursor:pointer;font-size:11px" title="' + esc(o) + '" onclick="navigator.clipboard.writeText(' + JSON.stringify(o) + ')">' + esc(short) + '</span>']);
  }}

  const tags = tx && !tx.error && Array.isArray(tx.tags) && tx.tags.length ? tx.tags : null;
  if (tags) {{
    const pills = tags.map(t =>
      '<span class="pill" title="' + esc(t.name) + '=' + esc(t.value) + '" style="margin:1px 2px">' +
      esc(t.name) + '<span style="color:#7a8896">=</span>' + esc(t.value) + '</span>'
    ).join('');
    rows.push(['tags', '<div class="tag-pills">' + pills + '</div>']);
  }}

  if (rows.length) {{
    grid.innerHTML = rows.map(([k, v]) => '<span class="meta-key">' + k + '</span><span>' + v + '</span>').join('');
    card.hidden = false;
  }}
  if (!isLocal) {{
    card.hidden = false;
    let saveArea = document.getElementById('save-area');
    if (!saveArea) {{
      saveArea = document.createElement('div');
      saveArea.id = 'save-area';
      saveArea.style.marginTop = '12px';
      card.appendChild(saveArea);
    }}
    if (!saveArea.dataset.rendered) {{
      saveArea.dataset.rendered = '1';
      const btn = document.createElement('button');
      btn.className = 'ghost';
      btn.style.cssText = 'font-size:12px;padding:4px 10px';
      btn.textContent = 'Save to index';
      btn.onclick = bookmarkThisTx;
      saveArea.appendChild(btn);
    }}
  }}
}}

async function bookmarkThisTx() {{
  const area = document.getElementById('save-area');
  if (area) area.innerHTML = '<span style="color:#7a8896;font-size:12px">saving…</span>';
  try {{
    const r = await fetch(API + '/index/' + TXID, {{method: 'POST'}});
    const j = await r.json();
    if (area) {{
      if (r.ok) {{
        area.innerHTML = '<span class="pill" style="background:#0f2010;color:#6abf6a">✓ saved' + (j.already_indexed ? ' (already in index)' : '') + '</span>';
      }} else {{
        area.innerHTML = '<span class="pill bad">error: ' + esc(j.detail || String(r.status)) + '</span>';
      }}
    }}
  }} catch(e) {{
    if (area) area.innerHTML = '<span class="pill bad">error: ' + esc(String(e)) + '</span>';
  }}
}}

async function copyPermalink() {{
  const url = 'https://arweave.net/' + TXID;
  try {{
    await navigator.clipboard.writeText(url);
    const btn = document.getElementById('copy-btn');
    if (btn) {{ btn.textContent = 'copied!'; setTimeout(() => {{ btn.textContent = 'Copy permalink'; }}, 1500); }}
  }} catch(e) {{}}
}}

init();
</script>
</body>
</html>"""
    return HTMLResponse(html)


@api.post("/index/{txid}")
def bookmark_tx(txid: str):
    """Bookmark a permaweb txid — fetch its metadata and add it to the local index."""
    if txid.startswith("sha256-"):
        raise HTTPException(400, "local ids cannot be bookmarked")
    try:
        return client.bookmark(txid)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@api.post("/push/{txid}")
def push_local(txid: str):
    """Push a locally-stored item to Arweave once a wallet is configured."""
    try:
        return client.push_local(txid)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@api.post("/wallet/generate")
def wallet_generate():
    wp = WALLET_PATH.expanduser()
    if wp.is_file():
        try:
            addr = ar_bundle.address(wp)
            return {"address": addr, "path": str(wp), "created": False}
        except Exception as e:
            raise HTTPException(500, str(e))
    try:
        ar_bundle.generate_jwk(wp)
        client.wallet_path = str(wp)
        addr = ar_bundle.address(wp)
        return {"address": addr, "path": str(wp), "created": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@api.get("/wallet/address")
def wallet_address():
    if not client.wallet_path or not Path(client.wallet_path).expanduser().is_file():
        return {"address": None}
    try:
        return {"address": ar_bundle.address(client.wallet_path)}
    except Exception as e:
        return {"address": None, "error": str(e)}


@api.get("/wallet/balance")
def wallet_balance():
    if not client.wallet_path or not Path(client.wallet_path).expanduser().is_file():
        return {"address": None, "ar": None}
    try:
        address = ar_bundle.address(client.wallet_path)
    except Exception as e:
        return {"address": None, "ar": None, "error": str(e)}
    return client.balance(address)


# MCP rides the same router so it lands at /api/mcp and bare /mcp alike.
api.include_router(arweave_mcp.router)

# /api/* is the canonical local shape; the bare include answers the gateway,
# which strips /arweave/api (and legacy /api/arweave) before proxying.
app.include_router(api, prefix="/api")
app.include_router(api)


# ─── App (static frontend) ──────────────────────────────────────────────────

if APP_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(APP_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_html = APP_DIR / "index.html"
        if index_html.exists():
            return HTMLResponse(index_html.read_text())
        return HTMLResponse("<h1>arweave</h1><p>app/index.html missing</p>")
