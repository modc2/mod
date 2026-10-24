import os, sys, tempfile, threading, json
from http.server import BaseHTTPRequestHandler, HTTPServer
os.environ["SELFIMP_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mod as selfimp

CALLS = []
class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append(b)
        reviewer = "code reviewer" in b["prompt"]
        n = sum(1 for c in CALLS if "code reviewer" in c["prompt"])
        text = ("ok\nVERDICT: APPROVED" if n >= 2 else "fix it\nVERDICT: CHANGES") if reviewer else "did work"
        out = json.dumps({"result": [{"type": "text", "text": text}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
r = selfimp.Mod().run(query="t", coder={"url": url}, reviewer={"url": url, "model": "m"}, rounds=3)
assert r["status"] == "approved" and len(r["rounds"]) == 2, r
assert "fix it" in CALLS[2]["query"], "feedback must reach coder in round 2"
assert selfimp.Mod().get(r["id"])["id"] == r["id"]
print("ok")
