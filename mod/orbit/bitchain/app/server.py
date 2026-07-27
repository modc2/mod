"""bitchain app — zero-dep static server.

Serves the console page at the module base path (/bitchain, prefix kept by
the caddy router) on APP_PORT. Any GET returns index.html; /health returns
JSON for probes.
"""
import http.server
import json
import os

APP_PORT = int(os.environ.get("BITCHAIN_APP_PORT", "50261"))
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

    def log_message(self, fmt, *args):  # keep pm2 logs quiet
        pass


if __name__ == "__main__":
    print(f"bitchain-app listening on :{APP_PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", APP_PORT), Handler).serve_forever()
