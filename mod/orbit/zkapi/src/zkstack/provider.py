"""OpenRouter-compatible lease provider for the devnet.

``zkapi serverd`` mints one bounded runtime key per proof-backed lease through
OpenRouter's Management API, then disables it, reads its usage and deletes it
before signing the settled balance. This module implements exactly that
surface (``/v1/keys`` CRUD with ``limit``/``expires_at``/``include_byok_in_limit``
semantics) plus OpenAI-style inference on ``/v1/chat/completions``, so the
real server can run its production settlement path against it.

Inference engines:
  * ``echo``     — deterministic local model ``zkapi/echo`` (no network).
  * ``upstream`` — forwards to any OpenAI-compatible endpoint
                   (ZKAPI_UPSTREAM_BASE / ZKAPI_UPSTREAM_KEY) and meters the real
                   reported cost (``usage.cost``) or token prices.

Keys and usage are persisted, so the server can settle leases across restarts.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ECHO_MODEL = "zkapi/echo"
# USD per 1M tokens for the local model: priced so one short exchange costs a
# few micro-USD..milli-USD, i.e. a visible but tiny gwei charge.
ECHO_PRICE_PROMPT = 2.0
ECHO_PRICE_COMPLETION = 6.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> float:
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value).timestamp()


def _tokens(text: str) -> int:
    return max(1, len(re.findall(r"\w+|[^\w\s]", text or "")))


class Provider:
    def __init__(self, state_path: Path, management_key: str | None = None,
                 engine: str | None = None, upstream_base: str | None = None,
                 upstream_key: str | None = None, upstream_model: str | None = None):
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        data = self._read()
        self.management_key = management_key or data.get("management_key") or "zkmgmt-" + secrets.token_hex(24)
        self.keys: dict[str, dict] = data.get("keys", {})
        self.events: list[dict] = data.get("events", [])[-500:]
        self.upstream_base = (upstream_base or os.environ.get("ZKAPI_UPSTREAM_BASE") or "").rstrip("/")
        self.upstream_key = upstream_key or os.environ.get("ZKAPI_UPSTREAM_KEY") or ""
        self.upstream_model = upstream_model or os.environ.get("ZKAPI_UPSTREAM_MODEL") or ""
        self.engine = engine or os.environ.get("ZKAPI_PROVIDER_ENGINE") or (
            "upstream" if self.upstream_base else "echo")
        self.fail_next_create = 0
        self._server: ThreadingHTTPServer | None = None
        self._save()

    # -- persistence ----------------------------------------------------------------
    def _read(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        with self._lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"management_key": self.management_key, "keys": self.keys,
                                       "events": self.events[-500:]}, indent=1))
            os.chmod(tmp, 0o600)
            tmp.replace(self.state_path)

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append({"type": kind, "at": time.time(), **fields})

    # -- management API ---------------------------------------------------------------
    def create_key(self, body: dict) -> tuple[int, dict]:
        limit = body.get("limit")
        expires_at = body.get("expires_at")
        if not isinstance(limit, (int, float)) or limit <= 0:
            return 400, {"error": {"message": "limit must be a positive USD amount"}}
        if not expires_at:
            return 400, {"error": {"message": "expires_at is required"}}
        try:
            exp = _parse_iso(expires_at)
        except ValueError:
            return 400, {"error": {"message": "invalid expires_at"}}
        if exp <= time.time():
            return 400, {"error": {"message": "expires_at must be in the future"}}
        with self._lock:
            if self.fail_next_create > 0:
                self.fail_next_create -= 1
                self._event("create_failed", name=body.get("name"))
                self._save()
                return 503, {"error": {"message": "injected provider failure"}}
            khash = uuid.uuid4().hex
            key = {
                "hash": khash, "key": "sk-zkapi-" + secrets.token_hex(24), "name": str(body.get("name", "")),
                "label": str(body.get("name", ""))[:32], "limit": float(limit), "expires_at": _iso(exp),
                "include_byok_in_limit": bool(body.get("include_byok_in_limit")), "disabled": False,
                "usage": 0.0, "byok_usage": 0.0, "calls": 0, "created_at": _iso(time.time()),
            }
            self.keys[khash] = key
            self._event("issued", hash=khash, limit_usd=key["limit"])
            self._save()
        return 200, {"key": key["key"], "data": self._public(key)}

    @staticmethod
    def _public(key: dict) -> dict:
        return {k: key[k] for k in ("hash", "name", "label", "limit", "expires_at", "include_byok_in_limit",
                                    "disabled", "usage", "byok_usage", "created_at")}

    def handle_management(self, method: str, path: str, query: dict, body: dict | None) -> tuple[int, dict]:
        if path == "/v1/keys" and method == "POST":
            return self.create_key(body or {})
        if path == "/v1/keys" and method == "GET":
            offset = int(query.get("offset", "0") or 0)
            items = sorted(self.keys.values(), key=lambda k: k["created_at"])
            return 200, {"data": [self._public(k) for k in items[offset:offset + 100]]}
        m = re.fullmatch(r"/v1/keys/([0-9a-f]+)", path)
        if not m:
            return 404, {"error": {"message": "not found"}}
        with self._lock:
            key = self.keys.get(m.group(1))
            if not key:
                return 404, {"error": {"message": "unknown key"}}
            if method == "GET":
                self._event("usage_read", hash=key["hash"], usage=key["usage"])
                self._save()
                return 200, {"data": self._public(key)}
            if method == "PATCH":
                if "disabled" in (body or {}):
                    key["disabled"] = bool(body["disabled"])
                    self._event("disabled" if key["disabled"] else "enabled", hash=key["hash"])
                self._save()
                return 200, {"data": self._public(key)}
            if method == "DELETE":
                del self.keys[key["hash"]]
                self._event("deleted", hash=key["hash"])
                self._save()
                return 200, {"deleted": True}
        return 405, {"error": {"message": "method not allowed"}}

    # -- inference ------------------------------------------------------------------------
    def models(self) -> list[dict]:
        out = [{"id": ECHO_MODEL, "object": "model", "owned_by": "zkapi-devnet",
                "pricing": {"prompt": ECHO_PRICE_PROMPT / 1e6, "completion": ECHO_PRICE_COMPLETION / 1e6}}]
        if self.upstream_base:
            out.append({"id": self.upstream_model or "upstream/default", "object": "model",
                        "owned_by": self.upstream_base})
        return out

    def _runtime_key(self, auth: str) -> dict | None:
        token = auth[7:] if auth.startswith("Bearer ") else ""
        for key in self.keys.values():
            if token and secrets.compare_digest(key["key"], token):
                return key
        return None

    def chat(self, auth: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            key = self._runtime_key(auth)
            if not key or key["disabled"] or _parse_iso(key["expires_at"]) <= time.time():
                return 401, {"error": {"message": "invalid, disabled or expired runtime key", "code": 401}}
            remaining = key["limit"] - key["usage"] - key["byok_usage"]
            if remaining <= 0:
                return 402, {"error": {"message": "runtime key spending limit reached", "code": 402}}
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return 400, {"error": {"message": "messages are required"}}
        model = body.get("model") or ECHO_MODEL
        if self.engine == "upstream" and model != ECHO_MODEL:
            status, result, cost = self._upstream(body)
        else:
            status, result, cost = self._echo(body)
        if status != 200:
            return status, result
        with self._lock:
            key = self.keys.get(key["hash"])
            if key is None:
                return 401, {"error": {"message": "runtime key was revoked during inference"}}
            charged = min(cost, max(0.0, key["limit"] - key["usage"]))
            key["usage"] = round(key["usage"] + charged, 9)
            key["calls"] += 1
            self._event("inference", hash=key["hash"], cost_usd=charged, model=result.get("model"))
            self._save()
        result.setdefault("usage", {})["cost"] = charged
        return 200, result

    def _echo(self, body: dict) -> tuple[int, dict, float]:
        messages = body["messages"]
        last = next((m for m in reversed(messages) if m.get("role") == "user"), messages[-1])
        content = last.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = str(content or "")
        reply = (f"[zkapi devnet echo] Paid anonymously through a zkAPI lease. "
                 f"You said: {content[:2000]}")
        max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            words = re.findall(r"\S+", reply)
            reply = " ".join(words[:max_tokens])
        prompt_tokens = sum(_tokens(str(m.get("content", ""))) for m in messages)
        completion_tokens = _tokens(reply)
        cost = (prompt_tokens * ECHO_PRICE_PROMPT + completion_tokens * ECHO_PRICE_COMPLETION) / 1e6
        return 200, {
            "id": "gen-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": int(time.time()),
            "model": ECHO_MODEL, "provider": "zkapi-devnet",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        }, cost

    def _upstream(self, body: dict) -> tuple[int, dict, float]:
        payload = dict(body)
        payload["stream"] = False
        if self.upstream_model and payload.get("model") in (None, "", "upstream/default"):
            payload["model"] = self.upstream_model
        payload.setdefault("usage", {"include": True})
        req = urllib.request.Request(self.upstream_base + "/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json",
                                              "authorization": f"Bearer {self.upstream_key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, {"error": {"message": f"upstream: {e.read()[:300].decode(errors='replace')}"}}, 0.0
        except (urllib.error.URLError, OSError) as e:
            return 502, {"error": {"message": f"upstream unreachable: {e}"}}, 0.0
        usage = result.get("usage") or {}
        cost = usage.get("cost")
        if not isinstance(cost, (int, float)):
            cost = (usage.get("prompt_tokens", 0) * float(os.environ.get("ZKAPI_UPSTREAM_PRICE_PROMPT", "1.0"))
                    + usage.get("completion_tokens", 0) * float(os.environ.get("ZKAPI_UPSTREAM_PRICE_COMPLETION", "3.0"))) / 1e6
        return 200, result, float(cost)

    # -- HTTP --------------------------------------------------------------------------------
    def serve(self, host: str, port: int) -> ThreadingHTTPServer:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, status: int, value: Any, sse: bool = False) -> None:
                if sse:
                    chunk = dict(value)
                    chunk["object"] = "chat.completion.chunk"
                    chunk["choices"] = [{"index": 0, "delta": c["message"], "finish_reason": c.get("finish_reason")}
                                        for c in value.get("choices", [])]
                    data = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                    ctype = "text/event-stream"
                else:
                    data = json.dumps(value).encode()
                    ctype = "application/json"
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self) -> dict | None:
                n = int(self.headers.get("content-length") or 0)
                if n > 4 * 1024 * 1024:
                    return None
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return None

            def _route(self, method: str) -> None:
                from urllib.parse import parse_qsl, urlsplit

                parts = urlsplit(self.path)
                path = parts.path.rstrip("/") or "/"
                if path.startswith("/api/v1"):  # accept OpenRouter's /api prefix too
                    path = path[4:]
                query = dict(parse_qsl(parts.query))
                body = self._body() if method in ("POST", "PATCH") else None
                if method in ("POST", "PATCH") and body is None:
                    return self._send(400, {"error": {"message": "invalid JSON"}})
                auth = self.headers.get("authorization", "")
                if path == "/v1/chat/completions" and method == "POST":
                    status, value = provider.chat(auth, body)
                    return self._send(status, value, sse=bool(body.get("stream")) and status == 200)
                if path == "/v1/models" and method == "GET":
                    return self._send(200, {"object": "list", "data": provider.models()})
                if path in ("/", "/health"):
                    return self._send(200, {"ok": True, "engine": provider.engine, "keys": len(provider.keys)})
                if not secrets.compare_digest(auth, f"Bearer {provider.management_key}"):
                    return self._send(401, {"error": {"message": "management key required"}})
                status, value = provider.handle_management(method, path, query, body)
                self._send(status, value)

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PATCH(self):
                self._route("PATCH")

            def do_DELETE(self):
                self._route("DELETE")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True, name="zkapi-provider").start()
        self._server = server
        return server

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def summary(self) -> dict:
        with self._lock:
            return {
                "engine": self.engine, "upstream": self.upstream_base or None,
                "active_keys": sum(1 for k in self.keys.values() if not k["disabled"]),
                "keys": len(self.keys), "events": self.events[-20:],
            }
