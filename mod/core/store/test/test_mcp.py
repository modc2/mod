"""MCP endpoint tests through FastAPI's TestClient.

Same harness as test_api.py: auth verification is stubbed (token string ==
signer address) so the JSON-RPC layer is exercised against the real tool
wiring — initialize → tools/list → tools/call over the localfs backend.
"""
import importlib
import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
STORE = os.path.dirname(os.path.dirname(__file__))
sys.path[:0] = [REPO, STORE]

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import mod as m  # noqa: E402

ALICE = "0x" + "a" * 40
BOB = "0x" + "b" * 40


@pytest.fixture
def client(monkeypatch):
    priv = tempfile.mkdtemp(prefix="store_priv_")
    store = tempfile.mkdtemp(prefix="store_data_")
    os.environ["STORE_PRIVATE_DIR"] = priv
    import api.api as a
    importlib.reload(a)
    a.store_mod = m.mod("dstore")(store_path=store)
    monkeypatch.setattr(a.AUTH, "verify", lambda token: {"key": token})
    return TestClient(a.app)


def H(addr):
    return {"Authorization": f"Bearer {addr}"}


def rpc(client, method, params=None, id=1, addr=None):
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=H(addr) if addr else {})


def call(client, tool, arguments=None, addr=None):
    r = rpc(client, "tools/call",
            {"name": tool, "arguments": arguments or {}}, addr=addr)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def structured(result):
    assert result["isError"] is False, result
    # structuredContent mirrors the text content — both must agree.
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def test_initialize_handshake(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18",
                                   "capabilities": {}, "clientInfo": {"name": "t"}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18"  # echoed: we support it
    assert res["serverInfo"]["name"] == "store"
    assert "tools" in res["capabilities"]
    # Unknown client version falls back to a version we actually implement.
    res2 = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert res2["protocolVersion"] == "2025-03-26"


def test_notifications_get_202_and_ping_pongs(client):
    n = client.post("/mcp", json={"jsonrpc": "2.0",
                                  "method": "notifications/initialized"})
    assert n.status_code == 202 and not n.content
    assert rpc(client, "ping").json()["result"] == {}
    assert client.get("/mcp").status_code == 405


def test_tools_list_shape(client):
    res = rpc(client, "tools/list").json()["result"]
    tools = {t["name"]: t for t in res["tools"]}
    expected = {"store_status", "store_market_browse", "store_terms", "store_me",
                "store_list", "store_search", "store_get", "store_object_info",
                "store_put_text", "store_share", "store_pin", "store_pins",
                "store_pools"}
    assert expected <= set(tools)
    for t in tools.values():
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
    assert "cid" in tools["store_get"]["inputSchema"]["required"]


def test_public_tool_call_unauthenticated(client):
    market = structured(call(client, "store_market_browse", {"q": ""}))
    assert market["count"] == 0 and market["listings"] == []
    terms = structured(call(client, "store_terms"))
    assert terms["required"] is True and terms["text"]


def test_authed_put_text_then_list(client):
    # Storing is terms-gated on MCP exactly like REST: first call errors 451…
    denied = call(client, "store_put_text",
                  {"name": "note.txt", "text": "hello mcp"}, addr=ALICE)
    assert denied["isError"] is True and "451" in denied["content"][0]["text"]
    # …sign-accept, then the same tool call stores and lists round-trip.
    assert client.post("/terms/accept", headers=H(ALICE)).status_code == 200
    put = structured(call(client, "store_put_text",
                          {"name": "note.txt", "text": "hello mcp"}, addr=ALICE))
    cid = put["results"]["localfs"]["cid"]
    objs = structured(call(client, "store_list", {}, addr=ALICE))["objects"]
    assert any(o["cid"] == cid for o in objs)
    # The stored bytes read back through store_get (owner sees private objects).
    got = structured(call(client, "store_get", {"cid": cid}, addr=ALICE))
    assert got["text"] == "hello mcp"


def test_share_grants_read_access(client):
    client.post("/terms/accept", headers=H(ALICE))
    put = structured(call(client, "store_put_text",
                          {"name": "s.txt", "text": "secret"}, addr=ALICE))
    cid = put["results"]["localfs"]["cid"]
    # Bob is blocked from the private object until Alice shares it.
    blocked = call(client, "store_get", {"cid": cid}, addr=BOB)
    assert blocked["isError"] is True
    structured(call(client, "store_share",
                    {"grantee": BOB, "cid": cid, "ttl_seconds": 600}, addr=ALICE))
    assert structured(call(client, "store_get", {"cid": cid}, addr=BOB))["text"] == "secret"


def test_authed_tool_without_token_is_tool_error(client):
    res = call(client, "store_me")
    assert res["isError"] is True
    msg = res["content"][0]["text"]
    assert "Bearer" in msg and "401" in msg


def test_unknown_tool_and_bad_args(client):
    r = rpc(client, "tools/call", {"name": "store_nope", "arguments": {}})
    assert r.json()["error"]["code"] == -32602
    missing = call(client, "store_get", {}, addr=ALICE)
    assert missing["isError"] is True and "cid required" in missing["content"][0]["text"]


def test_unknown_method_is_32601(client):
    r = rpc(client, "resources/list")
    assert r.status_code == 200
    err = r.json()["error"]
    assert err["code"] == -32601 and "resources/list" in err["message"]


def test_malformed_bodies(client):
    bad = client.post("/mcp", content=b"{not json",
                      headers={"Content-Type": "application/json"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == -32700
    nomethod = client.post("/mcp", json={"jsonrpc": "2.0", "id": 5})
    assert nomethod.status_code == 400
    assert nomethod.json()["error"]["code"] == -32600
