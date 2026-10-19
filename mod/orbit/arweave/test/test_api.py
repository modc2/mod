"""REST + MCP tests for orbit/arweave. Offline: a temp store dir, no wallet,
and the gateway is stubbed so nothing hits arweave.net."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MOD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOD))
sys.path.insert(0, str(MOD / "api"))


@pytest.fixture()
def tc(tmp_path, monkeypatch):
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    import arlocal.client as cl
    monkeypatch.setattr(cl, "WALLET_PATH", tmp_path / "nope.json")
    import api as api_mod
    import mcp_server
    client = cl.ArweaveClient(store_path=str(tmp_path / "store"))
    monkeypatch.setattr(api_mod, "client", client)
    mcp_server.bind(client, api_mod.load_config)

    class FakeResp:
        def __init__(self, status=200, content=b"", js=None, text=""):
            self.status_code, self.content, self._js, self.text = status, content, js, text
            self.ok = status < 400
        def json(self): return self._js
        def raise_for_status(self): pass

    def fake_get(url, **kw):
        if url.endswith("/info"):
            return FakeResp(js={"network": "arweave.N.1", "height": 42, "version": 5, "blocks": 43})
        if "/price/" in url:
            return FakeResp(text=str(int(url.rsplit("/", 1)[1]) * 1000))
        if "/tx/" in url:
            return FakeResp(js={"id": url.rsplit("/", 1)[1], "data_size": "7"})
        if url.endswith("/REALTXID_000000000000000000000000000000000"):
            return FakeResp(content=b'{"from":"gateway"}')
        return FakeResp(status=404)
    monkeypatch.setattr(cl.requests, "get", fake_get)
    return TestClient(api_mod.app)


def rpc(tc, method, params=None, id_=1, path="/api/mcp"):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return tc.post(path, json=body)


def tool(tc, tool_name, **args):
    r = rpc(tc, "tools/call", {"name": tool_name, "arguments": args})
    assert r.status_code == 200
    return r.json()["result"]


# ── REST ─────────────────────────────────────────────────────────────────────

def test_health_both_shapes_and_app(tc):
    assert tc.get("/api/health").json()["ok"] is True
    assert tc.get("/health").json()["ok"] is True            # gateway-stripped shape
    assert tc.get("/arweave/api/health").json()["ok"] is True  # prefix-kept shape
    html = tc.get("/").text
    assert "<title>arweave" in html and "/mcp" in html
    assert tc.get("/arweave/").status_code == 200


def test_put_get_list_rm_roundtrip_without_wallet(tc):
    r = tc.post("/api/put", json={"data": {"a": 1}, "name": "a.json"}).json()
    assert r["backend"] == "local" and r["txid"].startswith("sha256-") and "warning" in r
    txid = r["txid"]
    got = tc.get(f"/api/get/{txid}")
    assert got.status_code == 200 and got.json() == {"a": 1}
    assert tc.get(f"/api/get/{txid}?raw=1").content == b'{"a": 1}'
    items = tc.get("/api/list").json()["items"]
    assert [i["txid"] for i in items] == [txid] and items[0]["name"] == "a.json"
    assert tc.delete(f"/api/rm/{txid}").json()["removed"] is True
    assert tc.get(f"/api/get/{txid}").status_code == 404
    assert tc.get("/api/list").json()["items"] == []


def test_put_multipart_and_raw_body(tc):
    r = tc.post("/api/put", files={"file": ("x.bin", b"\xff\xfe\x00binary")}).json()
    assert r["backend"] == "local" and r["size"] == 9
    g = tc.get(f"/api/get/{r['txid']}")
    assert g.headers["content-type"].startswith("application/octet-stream")
    r2 = tc.post("/api/put", content=b"plain text", headers={"content-type": "text/plain"}).json()
    assert tc.get(f"/api/get/{r2['txid']}").text == "plain text"


def test_gateway_reads(tc):
    assert tc.get("/api/get/REALTXID_000000000000000000000000000000000").json() == {"from": "gateway"}
    assert tc.get("/api/price/1024").json() == {"bytes": 1024, "winston": "1024000"}
    assert tc.get("/api/status").json()["arweave"]["height"] == 42
    assert tc.get("/api/network").json()["network"] == "arweave.N.1"
    assert tc.get("/api/tx/abc").json()["id"] == "abc"
    assert tc.get("/api/get/missingmissingmissingmissingmissingmissing1").status_code == 404


def test_info_advertises_mcp(tc):
    j = tc.get("/api/info").json()
    assert "arweave_put" in j["mcp"]["tools"]
    assert "/mcp" in j["endpoints"]


# ── MCP ──────────────────────────────────────────────────────────────────────

def test_mcp_initialize_ping_notifications(tc):
    r = rpc(tc, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}).json()
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "arweave"
    assert r["result"]["capabilities"] == {"tools": {}}
    r = rpc(tc, "initialize", {"protocolVersion": "1999-01-01"}).json()
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert rpc(tc, "ping").json()["result"] == {}
    assert rpc(tc, "notifications/initialized", id_=None).status_code == 202
    assert tc.get("/api/mcp").status_code == 405


def test_mcp_tools_list_both_paths(tc):
    names = {t["name"] for t in rpc(tc, "tools/list").json()["result"]["tools"]}
    assert names == {"arweave_status", "arweave_info", "arweave_put", "arweave_get", "arweave_list",
                     "arweave_rm", "arweave_tx", "arweave_price", "arweave_network"}
    for t in rpc(tc, "tools/list", path="/mcp").json()["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"


def test_mcp_put_get_list_rm(tc):
    res = tool(tc, "arweave_put", data={"hello": "mcp"}, name="h.json")
    assert res["isError"] is False
    txid = res["structuredContent"]["txid"]
    assert res["structuredContent"]["backend"] == "local"
    got = tool(tc, "arweave_get", txid=txid)["structuredContent"]
    assert got["json"] == {"hello": "mcp"} and got["encoding"] == "json"
    res = tool(tc, "arweave_put", data='{"k":1}', as_json=True)
    assert tool(tc, "arweave_get", txid=res["structuredContent"]["txid"])["structuredContent"]["json"] == {"k": 1}
    res = tool(tc, "arweave_put", data="just text")
    assert tool(tc, "arweave_get", txid=res["structuredContent"]["txid"])["structuredContent"]["text"] == "just text"
    assert len(tool(tc, "arweave_list")["structuredContent"]["items"]) == 3
    assert tool(tc, "arweave_rm", txid=txid)["structuredContent"]["removed"] is True
    assert len(tool(tc, "arweave_list", limit=10)["structuredContent"]["items"]) == 2


def test_mcp_gateway_tools_and_errors(tc):
    assert tool(tc, "arweave_price", num_bytes=1000)["structuredContent"]["ar"] == 1e-6
    assert tool(tc, "arweave_network")["structuredContent"]["height"] == 42
    assert tool(tc, "arweave_status")["structuredContent"]["wallet_configured"] is False
    assert "arweave_put" in tool(tc, "arweave_info")["structuredContent"]["tools"]
    assert tool(tc, "arweave_tx", txid="abc")["structuredContent"]["id"] == "abc"
    # tool failures are isError results, not transport errors
    bad = tool(tc, "arweave_get", txid="missingmissingmissingmissingmissingmissing1")
    assert bad["isError"] is True and "failed" in bad["content"][0]["text"]
    assert tool(tc, "arweave_put")["isError"] is True
    assert tool(tc, "arweave_price", num_bytes=0)["isError"] is True
    assert tool(tc, "arweave_put", data="{oops", as_json=True)["isError"] is True
    # protocol-level errors
    r = rpc(tc, "tools/call", {"name": "nope", "arguments": {}}).json()
    assert r["error"]["code"] == -32602
    assert rpc(tc, "resources/list").json()["error"]["code"] == -32601
    assert tc.post("/api/mcp", content=b"not json").status_code == 400
    assert tc.post("/api/mcp", json=[1, 2]).status_code == 400


def test_blob_path_rejects_traversal(tc):
    import arlocal.client as cl
    c = cl.ArweaveClient(store_path="/tmp/arweave-test-trav")
    assert c._blob_path("sha256-../../etc/passwd") is None
    assert c._blob_path("sha256-" + "a" * 43) is not None
    assert c._blob_path("realTxIdNotLocal") is None
