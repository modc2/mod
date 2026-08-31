#!/usr/bin/env python3
"""skills api — REST, MCP and the console on one port, no dependencies.

    python3 -m skillsrc.api [--port 50860]

Reads are open: searching the web, reading a skill, listing the catalog. The
catalog is shared state on this box, so changing it — installing, writing,
removing, setting the token — is gated on a bearer token
(~/.mod/skills/server.secret) or on a caller from this box that is not being
proxied. That is the only asymmetry; both surfaces enforce it in the same
place, because both go through `route`.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from skillsrc import mcp
    from skillsrc.market import Market
    from skillsrc.sources import SOURCES
else:
    from . import mcp
    from .market import Market
    from .sources import SOURCES

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("SKILLS_BASE_PATH", "/skills")
PORT = int(os.environ.get("SKILLS_PORT", os.environ.get("PORT", 50860)))
BIND = os.environ.get("SKILLS_BIND", "0.0.0.0")

MARKET = Market()
SECRET_FILE = MARKET.store.dir / "server.secret"


class Denied(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def secret():
    try:
        return SECRET_FILE.read_text().strip() or None
    except Exception:
        return None


def authorized(client_ip, headers) -> bool:
    """May this caller change the catalog?"""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    want = secret()
    if want:
        auth = str(h.get("authorization") or "").strip()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else \
            str(h.get("x-skills-token") or "")
        return token == want
    proxied = any(h.get(k) for k in ("x-forwarded-for", "x-real-ip", "x-forwarded-host"))
    return not proxied and client_ip in ("127.0.0.1", "::1", "localhost")


def info():
    return {
        "module": "skills",
        "what": ("A marketplace of agent skills. A skill is a SKILL.md — front matter "
                 "plus instructions — scraped off the open web, filed in a catalog on "
                 "this box, and handed to an agent as context. Documents, never code: "
                 "installing one cannot run anything."),
        "sources": [{"id": s["id"], "label": s["label"], "about": s["about"]}
                    for s in SOURCES],
        "endpoints": {
            "GET /": "this",
            "GET /health": "liveness and catalog size",
            "GET /search": "?q=&sources=&limit=&fresh= — scan the web",
            "GET /sources": "the sources, and which are ready",
            "GET /skill": "?id=&path= — one result with its SKILL.md",
            "GET /installed": "?q=&tag= — the catalog",
            "GET /doc": "?name= — the markdown an agent is handed",
            "GET /raw": "?name= — the same, as text/markdown",
            "GET /load": "?names=a,b&q= — several skills with bodies, for a run",
            "POST /install": "{id, path?, name?, all?} — file it in the catalog (gated)",
            "POST /write": "{name, body, description?, tools?, tags?} — author one (gated)",
            "DELETE /installed/<name>": "remove one (gated)",
            "POST /token": "{token} — GitHub PAT for code search (gated)",
            "POST /mcp": "MCP JSON-RPC 2.0 — the same ten operations",
            f"GET {BASE}": "browser console",
        },
        "auth": ("bearer token required to change the catalog (%s)" % SECRET_FILE
                 if secret() else "catalog writes are loopback-only — no secret set"),
        "state": str(MARKET.store.dir),
    }


def route(method, path, query, body, client_ip=None, headers=None):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query or "").items()}
    b = body if isinstance(body, dict) else {}
    args = {**q, **b}
    may_write = authorized(client_ip, headers)

    def gate():
        if not may_write:
            raise Denied("changing the catalog is restricted to this box until a "
                         f"secret is set — write one to {SECRET_FILE} and send it "
                         "as Authorization: Bearer <secret>")

    if path in ("", "/"):
        return info()
    if path == "/health":
        return MARKET.health()
    if path == "/sources":
        return MARKET.sources()
    if path == "/search":
        srcs = args.get("sources")
        if isinstance(srcs, str):
            srcs = [s for s in srcs.replace(" ", "").split(",") if s]
        return MARKET.search(args.get("q", ""), srcs, int(args.get("limit") or 30),
                             str(args.get("fresh", "")).lower() in ("1", "true", "yes"))
    if path == "/skill":
        return MARKET.get(args.get("id"), args.get("path"))
    if path == "/installed" and method == "GET":
        return MARKET.installed(args.get("q", ""), args.get("tag"))
    if path.startswith("/installed/") and method == "DELETE":
        gate()
        return MARKET.remove(path.split("/", 2)[2])
    if path in ("/doc", "/raw"):
        return MARKET.doc(args.get("name"))
    if path == "/load":
        names = args.get("names")
        if isinstance(names, str):
            names = [n for n in names.replace(" ", "").split(",") if n]
        return MARKET.publish(names, args.get("q", ""))
    if path == "/install" and method == "POST":
        gate()
        return MARKET.install(args.get("id"), args.get("path"), args.get("name"),
                              all=bool(args.get("all")), who=args.get("who", ""))
    if path == "/write" and method == "POST":
        gate()
        return MARKET.write(args.get("name"), args.get("body"),
                            args.get("description", ""), args.get("tools"),
                            args.get("tags"), who=args.get("who", ""))
    if path == "/token" and method == "POST":
        gate()
        return MARKET.token(args.get("token"))
    if path == "/cache" and method == "DELETE":
        gate()
        return MARKET.clear_cache()
    raise KeyError(f"no route: {method} {path}")


class Handler(BaseHTTPRequestHandler):
    server_version = "skills/0.1"

    def log_message(self, fmt, *a):
        if os.environ.get("SKILLS_VERBOSE"):
            sys.stderr.write("[skills] %s\n" % (fmt % a))

    # ── plumbing ─────────────────────────────────────────────────────

    def _send(self, status, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else \
            (payload.encode() if isinstance(payload, str)
             else json.dumps(payload, indent=2, default=str).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return {}

    def _headers(self):
        return {k.lower(): v for k, v in self.headers.items()}

    def _path(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        # the gateway serves this module at /skills — strip the prefix so the
        # same handler answers on the bare port and behind the proxy
        for prefix in (f"{BASE}/_api", f"{BASE}/api", BASE):
            if path == prefix:
                return "/", parsed.query, True
            if path.startswith(prefix + "/"):
                return path[len(prefix):], parsed.query, True
        return path, parsed.query, False

    # ── verbs ────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path, query, under_base = self._path()
        # the console: /skills, or / on the bare port
        if path in ("/", "") and (under_base or not query):
            if under_base or self.headers.get("Accept", "").find("text/html") >= 0:
                return self._console()
        if path == "/console":
            return self._console()
        if path == "/raw":
            try:
                doc = route("GET", "/raw", query, {}, self.client_address[0], self._headers())
                return self._send(200, doc["markdown"], "text/markdown; charset=utf-8")
            except Exception as e:
                return self._send(404, {"error": str(e)})
        self._answer("GET", path, query, {})

    def do_POST(self):
        path, query, _ = self._path()
        body = self._body()
        if path == "/mcp":
            return self._mcp(body)
        self._answer("POST", path, query, body)

    def do_DELETE(self):
        path, query, _ = self._path()
        self._answer("DELETE", path, query, self._body())

    def _answer(self, method, path, query, body):
        try:
            result = route(method, path, query, body,
                           self.client_address[0], self._headers())
            self._send(200, result)
        except Denied as e:
            self._send(e.status, {"error": str(e)})
        except KeyError as e:
            self._send(404, {"error": str(e).strip("'")})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            # 4xx, not 5xx: a Cloudflare in front of this eats 5xx bodies, and
            # the body is the explanation
            self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def _mcp(self, body):
        may = authorized(self.client_address[0], self._headers())
        if isinstance(body, list):
            out = [r for r in (mcp.rpc(msg, may) for msg in body) if r is not None]
            return self._send(200, out if out else b"", "application/json")
        resp = mcp.rpc(body if isinstance(body, dict) else {}, may)
        if resp is None:
            return self._send(202, b"")
        self._send(200, resp)

    def _console(self):
        path = os.path.join(HERE, "console.html")
        try:
            with open(path, "rb") as f:
                html = f.read()
        except Exception:
            return self._send(404, {"error": "console.html missing"})
        self._send(200, html, "text/html; charset=utf-8")


def serve(port: int = None, bind: str = None):
    port = int(port or PORT)
    httpd = ThreadingHTTPServer((bind or BIND, port), Handler)
    print(f"[skills] http://{bind or BIND}:{port}{BASE}  (api, mcp and console)")
    httpd.serve_forever()


if __name__ == "__main__":
    argv = sys.argv[1:]
    port = PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    serve(port)
