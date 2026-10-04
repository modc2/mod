"""
server.py — stdlib HTTP API + console for ztensor. Local-first, no framework.
Routes mirror config.json endpoints under /ztensor.
"""
import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

# Load ztensor's OWN mod.py by path — a bare `import mod` resolves to the
# protocol's mod package (MODULE_DIR is only appended, so it never shadows it).
_spec = importlib.util.spec_from_file_location("ztensor_mod", MODULE_DIR / "mod.py")
modpy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modpy)

MOD = modpy.Mod()
PORT = int(os.environ.get("ZTENSOR_PORT", MOD.port))
APP = (MODULE_DIR / "app" / "index.html")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        p = urlparse(self.path)
        parts = [x for x in p.path.split("/") if x]
        route = parts[-1] if parts else ""
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        try:
            if route in ("", "ztensor") and APP.exists():
                return self._send(200, APP.read_bytes(), "text/html")
            if route == "health":
                return self._send(200, {"ok": True})
            if route == "info":
                return self._send(200, MOD.info())
            if route == "set":
                return self._send(200, MOD.set())
            if route == "tally":
                return self._send(200, MOD.tally(q.get("topic", "")))
            if route == "payout":
                return self._send(200, MOD.payout(q.get("topic", ""), float(q.get("pool", 0))))
            if route == "status":
                return self._send(200, MOD.status())
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        p = urlparse(self.path)
        route = [x for x in p.path.split("/") if x][-1]
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if route == "register":
                return self._send(200, MOD.register(body["pub"]))
            if route == "vote":
                return self._send(200, MOD.vote(body["topic"], body["choice"], body["sig"]))
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})


if __name__ == "__main__":
    print(f"ztensor on :{PORT} -> http://localhost:{PORT}/ztensor/")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
