"""promptland app — zero-dep static server.

Serves the console at the module base path (/promptland, prefix kept by the
caddy router) on APP_PORT. The vendored ethers bundle is the only asset;
everything else returns index.html. /health answers JSON for probes.
"""
import http.server
import json
import os

APP_PORT = int(os.environ.get("PROMPTLAND_APP_PORT", "50581"))
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/health"):
            body = json.dumps({"ok": True, "app": "promptland"}).encode()
            ctype = "application/json"
        elif path.endswith("/ethers.umd.min.js"):
            with open(os.path.join(HERE, "ethers.umd.min.js"), "rb") as f:
                body = f.read()
            ctype = "application/javascript"
        else:
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep pm2 logs quiet
        pass


if __name__ == "__main__":
    print(f"promptland-app listening on :{APP_PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", APP_PORT), Handler).serve_forever()
