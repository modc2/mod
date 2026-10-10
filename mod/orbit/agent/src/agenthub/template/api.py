#!/usr/bin/env python3
"""
The module's interface and API on one port, stdlib only.

    GET  /               the interface (index.html) — this agent's own page
    GET  /agents         {"agents": [...]} — the probe orbit/build mounts by
    GET  /health /info /readme /env /setup
    POST /run/stream     SSE: model_start, step, done|error
    POST /run            the same, blocking
    POST /setup          install the agent into ~/.mod/<name>/      (owner)
    POST /env            set/clear keys in ~/.mod/<name>/env.json   (owner)
    POST /spec           teach a repo-only agent its run command    (owner)

Paths are accepted bare, under /api, under /<name> and under /<name>/api, so
the same server answers the mod protocol's {host}/{mod} and {host}/{mod}/api
forms and a direct hit on the port alike.

    python3 api.py [--port N]
"""
import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# `python3 api.py` puts HERE first on sys.path, and HERE has a mod.py — so a
# later `import mod` (auth's verifier) would import this module instead of the
# fleet. Keep HERE, but last, and load our own mod.py by file path.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
sys.path.append(HERE)

import auth  # noqa: E402

CONFIG = json.load(open(os.path.join(HERE, "config.json")))
NAME = CONFIG["name"]


def _load_agent():
    spec = importlib.util.spec_from_file_location(f"{NAME}_agentmod", os.path.join(HERE, "mod.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGENT = _load_agent().Mod()
PORT = int(os.environ.get("PORT") or CONFIG.get("port") or 8000)


def route(path: str) -> str:
    path = path.split("?", 1)[0] or "/"
    for prefix in (f"/{NAME}", "/api"):
        if path == prefix or path.startswith(prefix + "/"):
            path = path[len(prefix):] or "/"
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = f"{NAME}-agent"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status, why, **extra):
        self._send(status, {"error": why, **extra})

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        if not n:
            return {}
        try:
            got = json.loads(self.rfile.read(n) or b"{}")
            return got if isinstance(got, dict) else {}
        except ValueError:
            return {}

    def _guard(self, path, method, key=None):
        return auth.guard(path, method=method, headers=self.headers,
                          client_addr=self.client_address[0], key=key)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, authorization, token")
        self.end_headers()

    def do_GET(self):
        path = route(self.path)
        try:
            self._guard(path, "GET")
        except auth.Denied as e:
            return self._fail(403, e.why, hint=e.hint)
        try:
            if path in ("/", "/index.html"):
                html = open(os.path.join(HERE, "index.html"), "rb").read()
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/health":
                return self._send(200, AGENT.health())
            if path == "/info":
                return self._send(200, AGENT.info())
            if path == "/agents":
                return self._send(200, AGENT.agents())
            if path == "/readme":
                return self._send(200, AGENT.readme())
            if path == "/env":
                return self._send(200, AGENT.env_keys())
            if path == "/setup":
                return self._send(200, AGENT.setup_status())
        except Exception as e:
            return self._fail(400, f"{type(e).__name__}: {e}")
        self._fail(404, f"no route {path}")

    def do_POST(self):
        path = route(self.path)
        body = self._body()
        try:
            who = self._guard(path, "POST", key=body.get("key"))
        except auth.Denied as e:
            return self._fail(403, e.why, hint=e.hint)
        try:
            if path == "/run/stream":
                return self._run_stream(body, who)
            if path == "/run":
                steps = AGENT.run(**self._run_kwargs(body))
                return self._send(200, {"steps": steps, "caller": who})
            if path == "/setup":
                return self._send(200, AGENT.setup(wait=False))
            if path == "/env":
                return self._send(200, AGENT.set_env(body.get("values") or {}))
            if path == "/spec":
                return self._send(200, AGENT.set_run(body.get("run"), body.get("model")))
        except Exception as e:
            return self._fail(400, f"{type(e).__name__}: {e}")
        self._fail(404, f"no route {path}")

    @staticmethod
    def _run_kwargs(body):
        query = (body.get("query") or body.get("prompt") or "").strip()
        if not query:
            raise ValueError("a run needs a `query`")
        return {"query": query, "path": body.get("path") or None,
                "goal": body.get("goal") or None, "model": body.get("model") or None,
                "timeout": int(body.get("timeout") or 1800)}

    def _run_stream(self, body, who):
        try:
            kwargs = self._run_kwargs(body)
        except ValueError as e:
            return self._fail(400, str(e))
        if not AGENT.available():
            # refuse before opening the stream — a 200 that is one error
            # frame is a failure the caller has to parse to notice
            return self._fail(409, f"{AGENT.label} is not installed here yet",
                              install=AGENT.install_hint())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for ev in AGENT.run_stream(**kwargs):
                self.wfile.write(f"data: {json.dumps(ev, default=str)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        self.close_connection = True


def serve(port: int = None, host: str = "0.0.0.0"):
    port = int(port or PORT)
    print(f"{NAME} on http://{host}:{port}/ — {AGENT.label}"
          f"{'' if AGENT.available() else ' (not installed yet)'}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    argv = sys.argv[1:]
    serve(int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT)
