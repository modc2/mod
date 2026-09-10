"""bt6 app — zero-dep static + JSON server.

Serves the arsenal console at the module base path (/bt6, prefix kept by the
caddy router) on APP_PORT. GET /bt6/arsenal.json returns the catalog so the
page can render from the same data the mod.py API exposes. /health is a probe.
"""
import http.server
import json
import os
import sys

APP_PORT = int(os.environ.get("BT6_APP_PORT", "50591"))
HERE = os.path.dirname(os.path.abspath(__file__))

# Reuse the single source of truth from mod.py.
sys.path.insert(0, os.path.dirname(HERE))
try:
    from mod import ARSENAL  # type: ignore
except Exception:
    ARSENAL = []


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.rstrip("/")
        if route.endswith("/health"):
            self._send(json.dumps({"ok": True, "app": "bt6"}).encode(),
                       "application/json")
        elif route.endswith("/arsenal.json"):
            self._send(json.dumps({"count": len(ARSENAL), "items": ARSENAL}).encode(),
                       "application/json")
        else:
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(f.read(), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):  # keep pm2 logs quiet
        pass


if __name__ == "__main__":
    print(f"bt6-app listening on :{APP_PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", APP_PORT), Handler).serve_forever()
