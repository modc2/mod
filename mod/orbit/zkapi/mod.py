"""
zkapi — zero-knowledge API layer (scaffold).

Intent: one HTTP surface to submit statements, prove them and verify proofs,
dispatching to the fleet's ZK backends (zkworld, cairo, zkwasm). No backend is
wired up yet — this anchor ships the standard module surface (info/test/serve/
status/kill) plus a stdlib HTTP stub so the route and port are live while the
real API grows underneath it.

CLI:
    m zkapi/info      # module description + status
    m zkapi/test      # self-check
    m zkapi/serve     # stub API on :51420, route /zkapi
    m zkapi/status
    m zkapi/kill
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("ZKAPI_DATA", MODULE_DIR / "state"))
PID_FILE = STATE_DIR / "server.pid"


def _config() -> dict:
    return json.loads((MODULE_DIR / "config.json").read_text())


def _pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


class Mod:
    description = (
        "Zero-knowledge API layer (scaffold): submit statements, prove them "
        "and verify proofs through one HTTP surface"
    )

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = _config()
        self.port = int(self.config.get("port", 51420))

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": self.config["name"],
            "description": self.config["description"],
            "version": self.config["version"],
            "port": self.port,
            "running": _pid() is not None,
            "stage": "scaffold — no ZK backend wired up yet",
        }

    def status(self) -> dict:
        pid = _pid()
        return {
            "running": pid is not None,
            "pid": pid,
            "port": self.port,
            "version": self.config["version"],
        }

    def test(self) -> dict:
        checks = {
            "config_loads": bool(self.config.get("name") == "zkapi"),
            "port_set": isinstance(self.port, int) and self.port > 0,
            "anchor_is_self": self.config.get("anchor") == "mod.py",
        }
        return {"ok": all(checks.values()), "checks": checks}

    def serve(self) -> dict:
        if (pid := _pid()) is not None:
            return {"running": True, "pid": pid, "port": self.port}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, __file__, "--serve"],
            cwd=MODULE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        PID_FILE.write_text(str(proc.pid))
        return {"running": True, "pid": proc.pid, "port": self.port}

    def kill(self) -> dict:
        pid = _pid()
        if pid is None:
            PID_FILE.unlink(missing_ok=True)
            return {"running": False}
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return {"running": False, "killed": pid}


def _run_server() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cfg = _config()
    port = int(cfg.get("port", 51420))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            # the gateway routes /zkapi/* here; accept both bare and prefixed
            if path.startswith("/zkapi"):
                path = path[len("/zkapi"):] or "/"
            if path in ("", "/", "/health"):
                body = {"ok": True}
            elif path == "/info":
                body = {
                    "name": cfg["name"],
                    "description": cfg["description"],
                    "version": cfg["version"],
                    "stage": "scaffold",
                }
            elif path == "/status":
                body = {"running": True, "port": port, "version": cfg["version"]}
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "not found"}')
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _run_server()
    else:
        print(json.dumps(Mod().info(), indent=2))
