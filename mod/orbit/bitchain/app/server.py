"""bitchain app — zero-dep static server.

Serves the console page at the module base path (/bitchain, prefix kept by
the caddy router) on APP_PORT. Any GET returns index.html; /health returns
JSON for probes. POST /bitchain/api/<fn> is proxied to the API so the page
also works when hit directly on APP_PORT (caddy routes it in prod).
"""
import http.server
import json
import os
import urllib.error
import urllib.request

APP_PORT = int(os.environ.get("BITCHAIN_APP_PORT", "50261"))
API_URL = os.environ.get("BITCHAIN_API_URL", "http://127.0.0.1:50260")
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/").endswith("/health"):
            body = json.dumps({"ok": True, "app": "bitchain"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        marker = "/api/"
        if marker not in self.path:
            self.send_error(404)
            return
        fn = self.path.split(marker, 1)[1]
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        req = urllib.request.Request(f"{API_URL}/{fn}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
        except OSError as e:
            status, body = 502, json.dumps({"error": str(e)}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep pm2 logs quiet
        pass


if __name__ == "__main__":
    print(f"bitchain-app listening on :{APP_PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", APP_PORT), Handler).serve_forever()
