"""zkapi module HTTP server (:51420).

URL shape (mod protocol): ``/zkapi`` = app, ``/zkapi/api/*`` = API; the router
may strip either prefix, so both bare and prefixed paths are accepted.

Access model
  * reads are public;
  * devnet user actions (create wallet, deposit, chat, withdraw, escape, ...) are
    open while the provider runs the local ``echo`` engine (no real money exists
    anywhere on the devnet) — a wallet is then controlled by the token returned
    at creation (``X-ZKAPI-Wallet-Token``);
  * operator actions (up/down/reset/fast-forward/claim-expired) and anything once
    a paid upstream engine is configured need loopback or ``ZKAPI_API_TOKEN``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from . import networks, paths
from .client import ZkApiError
from .devnet import FIRST_USER, Devnet
from .wallet import WalletError

APP_DIR = paths.SRC_DIR / "app"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
DEFAULT_DEPOSIT_GWEI = 10_000_000  # 0.01 ETH


class HttpError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, detail: Any = None):
        super().__init__(message)
        self.status, self.code, self.detail = status, code, detail


class App:
    def __init__(self, devnet: Devnet | None = None, config: dict | None = None):
        self.devnet = devnet or Devnet()
        self.config = config or json.loads((paths.MODULE_DIR / "config.json").read_text())
        self.api_token = os.environ.get("ZKAPI_API_TOKEN", "")
        self._verify_cache: dict[str, tuple[float, dict]] = {}
        self._rate: dict[str, list[float]] = {}
        self.boot_error: str | None = None
        self.started = time.time()

    # -- boot ----------------------------------------------------------------------------------
    def boot(self) -> None:
        if os.environ.get("ZKAPI_DEVNET_AUTOSTART", "1") == "0":
            return
        try:
            self.devnet.up()
        except Exception as e:  # noqa: BLE001
            self.boot_error = f"{type(e).__name__}: {e}"

    # -- access ---------------------------------------------------------------------------------------
    def is_admin(self, h: BaseHTTPRequestHandler) -> bool:
        auth = h.headers.get("authorization", "")
        if self.api_token and secrets.compare_digest(auth, f"Bearer {self.api_token}"):
            return True
        local = h.client_address[0] in ("127.0.0.1", "::1")
        forwarded = h.headers.get("x-forwarded-for") or h.headers.get("x-real-ip")
        return local and not forwarded

    def open_devnet(self) -> bool:
        return bool(self.devnet.provider) and self.devnet.provider.engine == "echo"

    def require(self, h: BaseHTTPRequestHandler, level: str) -> None:
        if self.is_admin(h):
            return
        if level == "admin":
            raise HttpError(403, "operator action: loopback or ZKAPI_API_TOKEN required", "forbidden")
        if not self.open_devnet():
            raise HttpError(403, "devnet actions are operator-only while a paid upstream engine is configured",
                            "forbidden")
        ip = (h.headers.get("x-forwarded-for") or h.client_address[0]).split(",")[0].strip()
        now = time.time()
        hits = [t for t in self._rate.get(ip, []) if now - t < 60] + [now]
        self._rate[ip] = hits
        if len(hits) > 30:
            raise HttpError(429, "slow down: 30 devnet actions per minute", "rate_limited")

    def require_wallet(self, h: BaseHTTPRequestHandler, name: str) -> None:
        if self.is_admin(h):
            return
        st = self.devnet.wallets("status", wallet=name)
        want = (st.get("labels") or {}).get("token_sha256")
        got = h.headers.get("x-zkapi-wallet-token", "")
        if not want or not got or not secrets.compare_digest(hashlib.sha256(got.encode()).hexdigest(), want):
            raise HttpError(403, f"wallet {name} belongs to someone else (X-ZKAPI-Wallet-Token)", "wallet_forbidden")

    # -- helpers ------------------------------------------------------------------------------------------
    def client(self):
        if not self.devnet.running():
            raise HttpError(503, f"devnet is not running{': ' + self.boot_error if self.boot_error else ''}",
                            "devnet_down")
        return self.devnet.client()

    def account(self, body: dict, default: int = FIRST_USER) -> int:
        idx = int(body.get("account", default))
        if not 3 <= idx <= 9:
            raise HttpError(400, "account must be an anvil user account index 3..9", "bad_account")
        return idx

    def verify(self, network: str, fresh: bool = False) -> dict:
        cached = self._verify_cache.get(network)
        if cached and not fresh and time.time() - cached[0] < 120:
            return cached[1]
        result = networks.verify(network)
        self._verify_cache[network] = (time.time(), result)
        return result

    def info(self) -> dict:
        return {
            "name": self.config["name"], "version": self.config["version"],
            "description": self.config["description"], "upstream": paths.UPSTREAM_REPO,
            "upstream_commit": paths.UPSTREAM_COMMIT, "circuit_id": paths.CIRCUIT_ID,
            "devnet_running": self.devnet.running(), "boot_error": self.boot_error,
            "networks": list(networks.NETWORKS), "uptime_s": int(time.time() - self.started),
        }

    # -- routing ----------------------------------------------------------------------------------------------
    def route(self, h: BaseHTTPRequestHandler, method: str, path: str, query: dict, body: dict) -> Any:
        d = self.devnet
        seg = [s for s in path.split("/") if s]
        if method == "GET":
            if path in ("/health", "/"):
                return {"ok": True, "devnet": d.running()}
            if path == "/info":
                return self.info()
            if path == "/status":
                return {"running": True, "port": self.config.get("port"), "version": self.config["version"],
                        "devnet": d.running()}
            if path == "/devnet":
                return d.status()
            if path in ("/devnet/manifest", "/devnet/config.json"):
                return d.manifest()
            if path == "/devnet/price":
                return d.price()
            if path == "/devnet/accounts":
                rpc = d.rpc
                accts = rpc.accounts()
                roles = {0: "deployer/owner/keeper", 1: "challenger", 2: "treasury"}
                return [{"index": i, "address": a, "role": roles.get(i, "user"),
                         "balance_eth": rpc.balance(a) / 1e18} for i, a in enumerate(accts)]
            if path == "/networks":
                return {n: {k: networks.pins(n)[k] for k in ("deployment_id", "chain_id", "contract_address",
                                                             "protocol_server_url", "rpc_url")}
                        for n in networks.NETWORKS}
            if len(seg) == 2 and seg[0] == "networks":
                return self.verify(seg[1], fresh=query.get("fresh") == "1")
            if len(seg) == 3 and seg[0] == "networks" and seg[2] == "probe":
                return networks.probe(seg[1])
            if path == "/wallets":
                return d.wallets("list")
            if len(seg) == 2 and seg[0] == "wallets":
                return self.client().status(seg[1])
            if len(seg) == 2 and seg[0] == "notes":
                c = self.client()
                note = c.note(int(seg[1]))
                if note["status"] == "pending_withdrawal":
                    note["pending"] = c.pending_withdrawal(int(seg[1]))
                return note
            if path == "/v1/models":
                return {"object": "list", "data": d.provider.models() if d.provider else []}
            if path == "/tree":
                return self.client().indexer("/v1/tree/snapshot")
        if method == "POST":
            if path == "/devnet/up":
                self.require(h, "admin")
                self.boot_error = None
                return d.up()
            if path == "/devnet/down":
                self.require(h, "admin")
                return d.down()
            if path == "/devnet/reset":
                self.require(h, "admin")
                d.reset()
                return d.up()
            if path == "/devnet/fast_forward":
                self.require(h, "admin")
                return d.fast_forward(int(body.get("seconds", 86_401)))
            if path == "/devnet/price":
                self.require(h, "user")
                return d.push_price(body.get("usd"))
            if path == "/devnet/mine":
                self.require(h, "user")
                return {"block": d.mine(int(body.get("blocks", 1)))}
            if path == "/wallets":
                self.require(h, "user")
                name = str(body.get("name") or f"w-{secrets.token_hex(3)}").lower()
                if not NAME_RE.match(name):
                    raise HttpError(400, "wallet names: lowercase a-z 0-9 . _ -", "bad_name")
                token = secrets.token_urlsafe(24)
                created = self.client().create_wallet(name, labels={
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "created_via": "http"})
                created["wallet_token"] = token
                if body.get("deposit_gwei"):
                    created["deposit"] = self.client().deposit(name, int(body["deposit_gwei"]),
                                                               d.sender(self.account(body)))
                return created
            if len(seg) == 3 and seg[0] == "wallets":
                name, action = seg[1], seg[2]
                self.require(h, "user")
                self.require_wallet(h, name)
                c = self.client()
                if action == "deposit":
                    return c.deposit(name, int(body.get("amount_gwei", DEFAULT_DEPOSIT_GWEI)), d.sender(self.account(body)))
                if action == "chat":
                    payload = {k: v for k, v in body.items() if k in ("model", "messages", "max_tokens", "temperature")}
                    if not payload.get("messages") and body.get("prompt"):
                        payload["messages"] = [{"role": "user", "content": str(body["prompt"])}]
                    payload.setdefault("model", "zkapi/echo")
                    return c.chat(name, payload)
                if action == "recover":
                    return c.recover(name)
                if action == "withdraw":
                    idx = self.account(body)
                    dest = body.get("destination") or d.account(idx)
                    return c.withdraw(name, dest, d.sender(idx))
                if action == "escape":
                    idx = self.account(body)
                    dest = body.get("destination") or d.account(idx)
                    hist = body.get("history_index")
                    return c.escape(name, dest, d.sender(idx), None if hist in (None, "") else int(hist))
                if action == "delete":
                    return d.wallets("delete", wallet=name, force=bool(body.get("force")))
            if len(seg) == 3 and seg[0] == "notes" and seg[2] == "finalize":
                self.require(h, "user")
                return self.client().finalize_escape(int(seg[1]), d.sender(self.account(body)))
            if len(seg) == 3 and seg[0] == "notes" and seg[2] == "claim_expired":
                self.require(h, "admin")
                return self.client().claim_expired(int(seg[1]), d.sender(0))
            if path == "/v1/chat/completions":
                return self.openai_chat(h, body)
        raise HttpError(404, f"no route {method} {path}", "not_found")

    def openai_chat(self, h: BaseHTTPRequestHandler, body: dict) -> Any:
        """OpenAI-compatible endpoint: every call is one anonymous, proof-paid zkAPI request."""
        self.require(h, "user")
        auth = h.headers.get("authorization", "")
        name = h.headers.get("x-zkapi-wallet") or "default"
        if name == "default" and not self.is_admin(h):
            raise HttpError(403, "remote callers must name their wallet (X-ZKAPI-Wallet + X-ZKAPI-Wallet-Token)",
                            "wallet_required")
        c = self.client()
        if name == "default":
            try:
                self.devnet.wallets("status", wallet=name)
            except WalletError:
                c.create_wallet(name, labels={"created_via": "openai-endpoint"})
                c.deposit(name, DEFAULT_DEPOSIT_GWEI, self.devnet.sender(FIRST_USER))
        else:
            if auth.startswith("Bearer ") and not h.headers.get("x-zkapi-wallet-token"):
                h.headers["x-zkapi-wallet-token"] = auth[7:]
            self.require_wallet(h, name)
        payload = {k: v for k, v in body.items() if k not in ("stream",)}
        result = c.chat(name, payload)
        completion = result["completion"]
        if isinstance(completion, dict):
            completion = dict(completion)
            completion["zkapi"] = {"lease": result["lease"], "settlement": result["settlement"]}
        if result["status"] != 200:
            raise HttpError(result["status"], "inference failed", "inference_failed", completion)
        return completion

    def proxy(self, h: BaseHTTPRequestHandler, method: str, target: str, raw: bytes) -> None:
        """Expose the devnet's upstream server/indexer so SDK clients can use it remotely."""
        req = urllib.request.Request(target, data=raw if method in ("POST", "PUT", "PATCH") else None, method=method,
                                     headers={k: v for k, v in h.headers.items()
                                              if k.lower() in ("content-type", "x-zkapi-testnet-password", "accept")})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status, data, ctype = resp.status, resp.read(), resp.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as e:
            status, data, ctype = e.code, e.read(), e.headers.get("content-type", "application/json")
        except (urllib.error.URLError, OSError) as e:
            status, data, ctype = 502, json.dumps({"error": str(e)}).encode(), "application/json"
        h.send_response(status)
        h.send_header("content-type", ctype)
        h.send_header("content-length", str(len(data)))
        h.send_header("access-control-allow-origin", "*")
        h.end_headers()
        h.wfile.write(data)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "zkapi/0.2"

        def _send(self, status: int, value: Any, ctype: str = "application/json") -> None:
            data = value if isinstance(value, bytes) else json.dumps(value, default=str).encode()
            self.send_response(status)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.send_header("cache-control", "no-store")
            self.send_header("access-control-allow-origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _handle(self, method: str) -> None:
            parts = urlsplit(self.path)
            path = parts.path
            is_app = path in ("/zkapi", "/zkapi/") or path.startswith("/zkapi/app")
            for prefix in ("/zkapi", "/api"):
                if path == prefix or path.startswith(prefix + "/"):
                    path = path[len(prefix):]
            path = "/" + path.strip("/") if path.strip("/") else "/"
            n = int(self.headers.get("content-length") or 0)
            if n > 2 * 1024 * 1024:
                return self._send(413, {"error": "body too large"})
            raw = self.rfile.read(n) if n else b""
            # app
            if method == "GET" and (is_app or (path == "/" and "json" not in self.headers.get("accept", ""))):
                index = APP_DIR / "index.html"
                return self._send(200, index.read_bytes(), "text/html; charset=utf-8")
            # upstream passthrough
            m = re.match(r"^/devnet/(server|indexer)(/.*)?$", path)
            if m:
                base = app.devnet.server_url if m.group(1) == "server" else app.devnet.indexer_url
                return app.proxy(self, method, base + (m.group(2) or "/") + (f"?{parts.query}" if parts.query else ""),
                                 raw)
            try:
                body = json.loads(raw or b"{}") if method == "POST" else {}
                if not isinstance(body, dict):
                    raise HttpError(400, "JSON object body required", "bad_body")
            except ValueError:
                return self._send(400, {"error": "invalid JSON", "error_code": "bad_json"})
            try:
                result = app.route(self, method, path, dict(parse_qsl(parts.query)), body)
                self._send(200, result)
            except HttpError as e:
                self._send(e.status, {"error": str(e), "error_code": e.code, "detail": e.detail})
            except ZkApiError as e:
                self._send(e.status if e.status and 400 <= e.status < 500 else 400,
                           {"error": str(e), "error_code": e.code, "detail": e.detail})
            except WalletError as e:
                self._send(400, {"error": str(e), "error_code": "wallet_error"})
            except Exception as e:  # noqa: BLE001
                # 4xx, not 5xx: Cloudflare replaces 5xx bodies (fleet note).
                self._send(400, {"error": f"{type(e).__name__}: {e}", "error_code": "internal",
                                 "trace": traceback.format_exc()[-1500:] if app.is_admin(self) else None})

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers",
                             "content-type, authorization, x-zkapi-wallet, x-zkapi-wallet-token, x-zkapi-testnet-password")
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


def run(port: int, host: str = "0.0.0.0", devnet: Devnet | None = None) -> None:
    app = App(devnet)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    threading.Thread(target=app.boot, daemon=True, name="zkapi-boot").start()
    import signal

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        app.devnet.down()
