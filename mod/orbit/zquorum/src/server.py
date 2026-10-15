"""server.py — stdlib HTTP API + console for zquorum. No framework, one port.

Lives in src/ (config.json + data/ stay at the module root).
On start it runs MOD.boot() in a thread: circuit, devnet, contract, console.

Serves two things on :51300, behind the mod-protocol gateway:

  GET  /zquorum              -> the console (static Next.js export in dist/)
  GET  /zquorum/_next/*      -> the export's static assets
  */zquorum/api/<fn>         -> the JSON protocol API (gateway strips the prefix,
                                so the stripped form /<fn> is accepted too)
  */zquorum/_api/<fn>        -> the app's own self-calls (gateway KEEPS the
                                prefix); mapped straight onto the same handlers.

All quorum logic lives in mod.py (the anchor). This file only maps URLs onto
Mod methods and ships the console; it reimplements no cryptography.
"""
import importlib.util
import json
import mimetypes
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))

# Load zquorum's OWN mod.py by path. A bare `import mod` would resolve to the
# protocol's `mod` package; appending HERE (not prepending) keeps that safe,
# and loading by file path removes all doubt. See [[mod-import-shadowing]].
_spec = importlib.util.spec_from_file_location("zquorum_mod", HERE / "mod.py")
modpy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modpy)

MOD = modpy.Mod()
PORT = int(os.environ.get("ZQUORUM_PORT", MOD.port))
DIST = HERE / "dist"  # Next.js export (build.sh); a symlink to releases/<ts>

# GET handlers that take only query params; POST handlers that take a JSON body.
GET_FNS = {
    "health": lambda q: MOD.health(),
    "info": lambda q: MOD.info(),
    "status": lambda q: MOD.status(),
    "list": lambda q: MOD.list(),
    "quorums": lambda q: MOD.list(),
    "tally": lambda q: MOD.tally(q.get("name", "")),
    "get": lambda q: MOD.get(q.get("name", "")),
    "toolchain": lambda q: MOD.toolchain(),
    "onchain": lambda q: MOD.onchain(q.get("name", ""), q.get("network") or None),
    "chain": lambda q: MOD.chain(q.get("network") or "devnet"),
    "deployments": lambda q: MOD.deployments(),
    "boot": lambda q: dict(modpy.BOOT) or {"running": True},
}
POST_FNS = {"commit", "create", "join", "vote", "tally", "get", "remove",
            "anchor", "onchain", "chain", "deploy", "devnet"}
# Writes that move the ledger or spend from the tallier's Starknet account.
# Reachable only from loopback without proxy headers (the anchor's own box),
# so the public route can read and vote but not deploy/reset chains.
LOCAL_FNS = {"deploy", "devnet"}


def _route(path):
    """Collapse any of the gateway's URL forms to a bare fn name.

    /zquorum/api/vote  /zquorum/_api/vote  /api/zquorum/vote  /vote  -> "vote"
    """
    parts = [p for p in path.split("/") if p and p not in ("zquorum", "api", "_api")]
    return parts[-1] if parts else ""


def _is_app_path(path):
    """True for the console shell routes (not the API)."""
    stripped = "/" + "/".join(p for p in path.split("/") if p and p != "zquorum")
    return stripped in ("/", "") or stripped.startswith("/_next")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _local(self):
        proxied = any(self.headers.get(h) for h in
                      ("X-Forwarded-For", "X-Real-IP", "Forwarded", "CF-Connecting-IP"))
        return self.client_address[0] in ("127.0.0.1", "::1") and not proxied

    def do_OPTIONS(self):
        self._send(204, b"")

    # ---- static console export ---------------------------------------------

    def _serve_static(self, path):
        """Serve dist/<route>. Falls back to index.html so the SPA shell loads
        even on an unknown route; 503 (not 404 — CF strips 5xx bodies but this
        is a bare port) a friendly note when the export hasn't been built."""
        rel = "/".join(p for p in path.split("/") if p and p != "zquorum")
        if not DIST.exists():
            return self._send(200, b"<h1>zQuorum</h1><p>Console not built yet. "
                              b"Run <code>bash build.sh</code>.</p>", "text/html")
        target = (DIST / rel) if rel else (DIST / "index.html")
        if target.is_dir():
            target = target / "index.html"
        if not target.exists() and not rel.startswith("_next"):
            target = DIST / "index.html"  # SPA fallback
        if not target.exists():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        p = urlparse(self.path)
        if _is_app_path(p.path):
            return self._serve_static(p.path)
        fn = _route(p.path)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        try:
            if fn in GET_FNS:
                return self._send(200, GET_FNS[fn](q))
            # unknown GET -> treat as a console deep link
            return self._serve_static(p.path)
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        p = urlparse(self.path)
        fn = _route(p.path)
        if fn not in POST_FNS:
            return self._send(404, {"error": f"unknown fn {fn!r}"})
        if fn in LOCAL_FNS and not self._local():
            return self._send(403, {"error": f"{fn} is local-only (run it on the anchor host: m zquorum/{fn})"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            kw = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            kw = {}
        if not isinstance(kw, dict):
            kw = {}
        if (fn in ("anchor", "create") and (kw.get("network") or "devnet") != "devnet"
                and not self._local()):
            return self._send(403, {"error": "anchoring on a paid network is local-only"})
        try:
            return self._send(200, getattr(MOD, fn)(**kw))
        except TypeError as e:  # bad/missing kwargs
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    # auto-run: circuit, devnet, contract, console — in the background so the
    # port answers immediately while the slow parts warm up.
    threading.Thread(target=MOD.boot, daemon=True).start()
    print(f"zquorum on :{PORT} -> http://localhost:{PORT}/zquorum "
          f"(API: /zquorum/api/<fn>)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
