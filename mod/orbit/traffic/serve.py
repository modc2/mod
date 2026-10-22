"""traffic server: the collector thread + a read-only JSON API + the chart page.

    /traffic/            the page
    /traffic/api/{fn}    API as the page calls it (also /api/traffic/{fn}, /{fn})

    python3 serve.py [--port 51540]
"""

import argparse
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
PREFIX = "/traffic"
if HERE not in sys.path:
    sys.path.append(HERE)

import collector as C  # noqa: E402

STORE = None
COL = None


def fns():
    return {
        "health": lambda **k: {"ok": True, "lines": COL.lines if COL else 0},
        "live": lambda window=C.LIVE_WINDOW, **k: C.live(STORE, int(window)),
        "series": lambda range="24h", top=8, apps=None, **k: C.series(STORE, range, int(top), apps),
        "sparks": lambda range="24h", **k: C.app_series(STORE, range),
        "countries": lambda range="24h", app=None, **k: C.countries(STORE, range, app),
        "affinities": lambda app="", range="24h", **k: C.affinities(STORE, app, range),
        "heatmap": lambda range="30d", app=None, **k: C.heatmap(STORE, range, app),
        "cohorts": lambda range="24h", **k: C.cohorts(STORE, range),
        "pairs": lambda range="24h", **k: C.pairs(STORE, range),
        "vintage": lambda app="", range="24h", **k: C.vintage(STORE, app, range),
        "landing_apps": lambda range="24h", limit=10, **k: C.landing_apps(STORE, range, int(limit)),
        "funnel": lambda range="24h", **k: C.funnel(STORE, range),
        "paths": lambda range="24h", **k: C.paths(STORE, range),
        "info": lambda **k: {"name": "traffic", "log": C.LOG, "db": STORE.dir,
                             "live_window": C.LIVE_WINDOW, "ranges": list(C.RANGES)},
    }


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        for pre in (PREFIX + "/api/", "/api" + PREFIX + "/", "/"):
            if path.startswith(pre):
                fn = path[len(pre):].strip("/")
                if fn in fns():
                    args = {k: v[-1] for k, v in parse_qs(u.query).items()}
                    try:
                        return self._json(fns()[fn](**args))
                    except Exception as e:  # noqa: BLE001
                        return self._json({"error": str(e)}, 400)
        if path in ("", "/", PREFIX):
            self.send_response(308)
            self.send_header("Location", PREFIX + "/")
            self.end_headers()
            return
        if path.startswith(PREFIX + "/"):
            self.path = path[len(PREFIX):] or "/"
            return super().do_GET()
        self._json({"error": "not found"}, 404)


def serve(port=51540, host="0.0.0.0"):
    global STORE, COL
    STORE = C.Store()
    COL = C.Collector(STORE)
    threading.Thread(target=COL.run, daemon=True).start()
    print(f"traffic on :{port}{PREFIX}/", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 51540)))
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    serve(a.port, a.host)
