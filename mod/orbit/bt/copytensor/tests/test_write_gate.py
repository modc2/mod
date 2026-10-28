"""Write gate — the copy book moves real TAO, so writes are operator-only.

A tiny app mounts `write_gate.middleware` in front of one catch-all route,
so no chain, bt or wallet is involved. What these pin:
  * reads are open; copy/wallet/sync/config writes 403 from the internet
    (proxy headers, foreign Host, foreign Origin),
  * a loopback call with no proxy headers passes — the desk agent's MCP
    dispatcher loops back that way,
  * the bearer token passes from anywhere,
  * strats (owner-keyed), backtest, dry-run sync, /mcp and /agent/ask stay
    open; answering an approval does not.
"""

import os
import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import write_gate  # noqa: E402

PUBLIC = {"x-forwarded-for": "203.0.113.9", "host": "modc2.com"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("COPYTENSOR_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("BT_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(write_gate, "TOKEN_FILE", str(tmp_path / "write_token"))
    (tmp_path / "write_token").write_text("sekret\n")
    app = FastAPI()
    app.middleware("http")(write_gate.middleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def any_route(path: str, request: Request):
        return {"ok": True}

    # TestClient connects as "testclient", not loopback — patch is_operator's
    # notion of the peer per test via the `peer` header shim below.
    return TestClient(app, base_url="http://localhost")


def _loopback(monkeypatch):
    orig = write_gate.is_operator

    def fake(request):
        request.scope["client"] = ("127.0.0.1", 5555)
        return orig(request)
    monkeypatch.setattr(write_gate, "is_operator", fake)


@pytest.mark.parametrize("method,path", [
    ("POST", "/copy"), ("PUT", "/copy/abc"), ("POST", "/copy/abc/pause"),
    ("DELETE", "/copy/abc"), ("POST", "/copy/abc/sync"), ("POST", "/wallet/set"),
    ("POST", "/portfolio/sync"), ("POST", "/config"), ("POST", "/watch"),
    ("DELETE", "/watch/5Gx"), ("POST", "/agent/approvals/xyz"), ("POST", "/discover"),
])
def test_writes_refused_from_the_internet(client, method, path):
    r = client.request(method, path, headers=PUBLIC)
    assert r.status_code == 403
    assert "operator" in r.json()["detail"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/copies"), ("GET", "/portfolio"), ("POST", "/strats"),
    ("PUT", "/strats/s1"), ("DELETE", "/strats/s1"), ("POST", "/strats/s1/clone"),
    ("POST", "/strats/backtest"), ("POST", "/portfolio/sync?dry_run=true"),
    ("POST", "/mcp"), ("POST", "/agent/ask"),
])
def test_open_routes_stay_open(client, method, path):
    assert client.request(method, path, headers=PUBLIC).status_code == 200


def test_bearer_token_passes_from_anywhere(client):
    h = dict(PUBLIC, authorization="Bearer sekret")
    assert client.post("/copy", headers=h).status_code == 200
    assert client.post("/copy", headers=dict(PUBLIC, authorization="Bearer nope")).status_code == 403


def test_env_token_wins_over_file(client, monkeypatch):
    monkeypatch.setenv("BT_WRITE_TOKEN", "fromenv")
    assert client.post("/copy", headers=dict(PUBLIC, authorization="Bearer fromenv")).status_code == 200
    assert client.post("/copy", headers=dict(PUBLIC, authorization="Bearer sekret")).status_code == 403


def test_loopback_passes_but_not_through_a_proxy(client, monkeypatch):
    _loopback(monkeypatch)
    assert client.post("/copy").status_code == 200
    assert client.post("/copy", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    assert client.post("/copy", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/copy", headers={"host": "modc2.com"}).status_code == 403


def test_no_token_file_means_loopback_only(client, monkeypatch, tmp_path):
    monkeypatch.setattr(write_gate, "TOKEN_FILE", str(tmp_path / "missing"))
    assert client.post("/copy", headers=dict(PUBLIC, authorization="Bearer ")).status_code == 403
