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

IMAGE_TXID = "IMAGETXIDDD000000000000000000000000000000"


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
        def __init__(self, status=200, content=b"", js=None, text="", headers=None):
            self.status_code, self.content, self._js, self.text = status, content, js, text
            self.ok = status < 400
            self.headers = headers or {}
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
        if url.endswith("/" + IMAGE_TXID):
            return FakeResp(content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"})
        if "/wallet/" in url and url.endswith("/balance"):
            return FakeResp(text="1000000000000")  # 1 AR in winston
        return FakeResp(status=404)

    def fake_post(url, **kw):
        if url.endswith("/graphql"):
            return FakeResp(js={
                "data": {"transactions": {"edges": [
                    {"cursor": "c1", "node": {"id": "TXABC123", "tags": [{"name": "App-Name", "value": "test"}], "data": {"size": "512"}}},
                    {"cursor": "c2", "node": {"id": "TXDEF456", "tags": [], "data": {"size": "256"}}},
                ]}}
            })
        return FakeResp(status=404)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    monkeypatch.setattr(cl.requests, "post", fake_post)
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


def test_put_multipart_with_tags(tc):
    tags_json = json.dumps([{"name": "App-Name", "value": "test-app"}])
    r = tc.post("/api/put", data={"tags": tags_json}, files={"file": ("t.bin", b"tagged data")}).json()
    assert r["backend"] == "local"
    txid = r["txid"]
    items = tc.get("/api/list").json()["items"]
    txitem = next(i for i in items if i["txid"] == txid)
    stored_tags = (txitem.get("metadata") or {}).get("tags", [])
    assert any(t[0] == "App-Name" and t[1] == "test-app" for t in stored_tags)


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
                     "arweave_rm", "arweave_tx", "arweave_tx_status", "arweave_price", "arweave_network",
                     "arweave_query", "arweave_archive"}
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


# ── Query (GraphQL) ───────────────────────────────────────────────────────────

def test_query_rest_by_owner(tc):
    r = tc.get("/api/query?owner=wallet123&first=2")
    assert r.status_code == 200
    data = r.json()
    assert "edges" in data
    assert len(data["edges"]) == 2
    assert data["edges"][0]["node"]["id"] == "TXABC123"


def test_query_rest_by_tag(tc):
    r = tc.get("/api/query?tag_name=App-Name&tag_value=test")
    assert r.status_code == 200
    edges = r.json()["edges"]
    assert edges[0]["node"]["tags"][0]["name"] == "App-Name"


def test_query_rest_no_params(tc):
    r = tc.get("/api/query")
    assert r.status_code == 200
    assert "edges" in r.json()


def test_query_mcp_tool(tc):
    res = tool(tc, "arweave_query", owner="wallet123", first=2)
    assert res["isError"] is False
    sc = res["structuredContent"]
    assert sc["count"] == 2
    assert sc["edges"][0]["node"]["id"] == "TXABC123"


def test_query_mcp_tool_with_tags(tc):
    res = tool(tc, "arweave_query", tags=[{"name": "App-Name", "values": ["test"]}])
    assert res["isError"] is False
    assert res["structuredContent"]["count"] == 2


# ── Arweave-backend URL field ─────────────────────────────────────────────────

@pytest.fixture()
def tc_arweave(tmp_path, monkeypatch):
    """Fixture with a fake wallet so the arweave (not local) backend is used."""
    import types

    wallet_file = tmp_path / "wallet.json"
    wallet_file.write_text('{"kty":"RSA"}')

    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    import arlocal.client as cl
    monkeypatch.setattr(cl, "WALLET_PATH", wallet_file)

    fake_txid = "FAKEARWEAVETXID000000000000000000000000000"

    class _FakeTx:
        id = fake_txid
        def add_tag(self, *a): pass
        def sign(self): pass
        def send(self): pass

    class _FakeWallet:
        def __init__(self, path): pass

    fake_arweave = types.ModuleType("arweave")
    fake_arweave.Wallet = _FakeWallet
    fake_arweave.Transaction = lambda wallet, data: _FakeTx()
    monkeypatch.setitem(sys.modules, "arweave", fake_arweave)

    import api as api_mod
    import mcp_server
    client = cl.ArweaveClient(store_path=str(tmp_path / "store"), wallet_path=str(wallet_file))
    monkeypatch.setattr(api_mod, "client", client)
    mcp_server.bind(client, api_mod.load_config)

    from fastapi.testclient import TestClient
    return TestClient(api_mod.app)


def test_put_arweave_backend_includes_url(tc_arweave):
    r = tc_arweave.post("/api/put", json={"data": "hello arweave", "name": "hello.txt"}).json()
    assert r["backend"] == "arweave"
    txid = r["txid"]
    assert "url" in r
    assert f"arweave.net/{txid}" in r["url"]


def test_list_arweave_backend_includes_url(tc_arweave):
    r = tc_arweave.post("/api/put", json={"data": "stored", "name": "s.txt"}).json()
    txid = r["txid"]
    items = tc_arweave.get("/api/list").json()["items"]
    assert items[0]["txid"] == txid
    assert items[0]["url"] is not None
    assert f"arweave.net/{txid}" in items[0]["url"]


# ── Wallet balance ────────────────────────────────────────────────────────────

def test_wallet_balance_no_wallet(tc):
    r = tc.get("/api/wallet/balance")
    assert r.status_code == 200
    j = r.json()
    assert j["address"] is None
    assert j["ar"] is None


# ── tx confirm status ─────────────────────────────────────────────────────────

CONFIRMED_TX = "CONFIRMEDTXID00000000000000000000000000000"
PENDING_TX   = "PENDINGTXIDDD00000000000000000000000000000"
MISSING_TX   = "MISSINGTXIDDD00000000000000000000000000000"


@pytest.fixture()
def tc_confirm(tmp_path, monkeypatch):
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    import arlocal.client as cl
    monkeypatch.setattr(cl, "WALLET_PATH", tmp_path / "nope.json")
    import api as api_mod
    import mcp_server
    client = cl.ArweaveClient(store_path=str(tmp_path / "store_confirm"))
    monkeypatch.setattr(api_mod, "client", client)
    mcp_server.bind(client, api_mod.load_config)

    class FakeResp:
        def __init__(self, status=200, js=None):
            self.status_code = status
            self.ok = status < 400
            self._js = js or {}
        def json(self): return self._js
        def raise_for_status(self):
            if self.status_code >= 400:
                raise cl.requests.exceptions.HTTPError(response=self)

    def fake_get(url, **kw):
        if "/tx/" in url and url.endswith("/status"):
            txid = url.split("/tx/")[1][:-len("/status")]
            if txid == CONFIRMED_TX:
                return FakeResp(200, {"block_height": 1200, "number_of_confirmations": 50})
            if txid == PENDING_TX:
                return FakeResp(202)
            return FakeResp(404)
        return FakeResp(404)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    return TestClient(api_mod.app)


def test_tx_confirm_endpoint(tc_confirm):
    # local sha256- id returns immediately without a network call
    local_id = "sha256-" + "a" * 43
    r = tc_confirm.get(f"/api/tx/{local_id}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "local"

    # confirmed: gateway returns block_height + number_of_confirmations
    r = tc_confirm.get(f"/api/tx/{CONFIRMED_TX}/confirm")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "confirmed"
    assert j["confirmations"] == 50
    assert j["block_height"] == 1200

    # pending: gateway returns 202
    r = tc_confirm.get(f"/api/tx/{PENDING_TX}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

    # not_found: gateway returns 404
    r = tc_confirm.get(f"/api/tx/{MISSING_TX}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "not_found"


# ── Turbo bundler_accepted status ─────────────────────────────────────────────

TURBO_TX = "TURBOTXIDDD000000000000000000000000000000"


@pytest.fixture()
def tc_turbo_confirm(tmp_path, monkeypatch):
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    import arlocal.client as cl
    monkeypatch.setattr(cl, "WALLET_PATH", tmp_path / "nope.json")
    import api as api_mod
    import mcp_server
    client = cl.ArweaveClient(store_path=str(tmp_path / "store_turbo"))
    monkeypatch.setattr(api_mod, "client", client)
    mcp_server.bind(client, api_mod.load_config)

    # Simulate a Turbo-uploaded txid in the local index
    client._index(TURBO_TX, "file.json", 64, "arweave",
                  {"pinned": True, "via": "turbo", "content_type": "application/json"})

    class FakeResp:
        def __init__(self, status=200, js=None):
            self.status_code = status
            self.ok = status < 400
            self._js = js or {}
        def json(self): return self._js
        def raise_for_status(self):
            if self.status_code >= 400:
                raise cl.requests.exceptions.HTTPError(response=self)

    def fake_get(url, **kw):
        # Turbo status endpoint
        if "upload.ardrive.io" in url and "/tx/" in url and url.endswith("/status"):
            txid = url.split("/tx/")[1][:-len("/status")]
            if txid == TURBO_TX:
                return FakeResp(200, {"status": "FINALIZED", "id": txid})
            return FakeResp(404)
        # Chain gateway: hasn't seen the Turbo txid yet
        if "/tx/" in url and url.endswith("/status"):
            return FakeResp(404)
        return FakeResp(404)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    return TestClient(api_mod.app)


def test_tx_confirm_turbo_bundler_accepted(tc_turbo_confirm):
    r = tc_turbo_confirm.get(f"/api/tx/{TURBO_TX}/confirm")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "bundler_accepted"
    assert j["bundler"] == "turbo"


# ── push_local endpoint ───────────────────────────────────────────────────────

def test_push_local_to_arweave(tc_arweave):
    """Seeding a local item then pushing it should return arweave backend and remove the local entry."""
    import api as api_mod
    client = api_mod.client

    # Seed a local item directly (simulates a prior no-wallet upload)
    local_txid = "sha256-" + "b" * 43
    blob_path = client.blob_dir / ("b" * 43)
    blob_path.write_bytes(b'{"pushed": true}')
    client._index(local_txid, "pushed.json", 16, "local",
                  {"content_type": "application/json", "tags": []})

    # Confirm it shows as local
    items = tc_arweave.get("/api/list").json()["items"]
    assert any(i["txid"] == local_txid and i["backend"] == "local" for i in items)

    # Push to Arweave
    r = tc_arweave.post(f"/api/push/{local_txid}").json()
    assert r["backend"] == "arweave"
    assert "txid" in r

    # Old local entry must be gone
    items = tc_arweave.get("/api/list").json()["items"]
    assert not any(i["txid"] == local_txid for i in items)


def test_push_local_errors(tc):
    """Non-local txid → 400; sha256- with no wallet → 400; unknown blob → 400."""
    # Not a local-backend id
    r = tc.post("/api/push/" + "A" * 43)
    assert r.status_code == 400

    # sha256- id but no wallet configured → 400
    r = tc.post("/api/push/sha256-" + "a" * 43)
    assert r.status_code == 400


def test_push_local_missing_blob(tc_arweave):
    """sha256- id in index but blob deleted → 404."""
    import api as api_mod
    client = api_mod.client

    ghost_txid = "sha256-" + "c" * 43
    # Index entry exists but no blob on disk
    client._index(ghost_txid, "ghost.txt", 0, "local", {"content_type": "text/plain"})

    r = tc_arweave.post(f"/api/push/{ghost_txid}")
    assert r.status_code == 404


# ── Bookmark / index endpoint ─────────────────────────────────────────────────

def test_bookmark_index_endpoint(tc):
    txid = "REALTXID_000000000000000000000000000000000"
    r = tc.post(f"/api/index/{txid}")
    assert r.status_code == 200
    j = r.json()
    assert j["txid"] == txid
    assert j["backend"] == "arweave"
    assert j.get("already_indexed") is False

    # should appear in list with backend=arweave
    items = tc.get("/api/list").json()["items"]
    assert any(i["txid"] == txid and i["backend"] == "arweave" for i in items)

    # second call returns existing row with already_indexed=True
    r2 = tc.post(f"/api/index/{txid}")
    assert r2.status_code == 200
    assert r2.json()["already_indexed"] is True

    # local sha256- id returns 400
    r3 = tc.post("/api/index/sha256-" + "a" * 43)
    assert r3.status_code == 400


# ── Archive base-href injection ───────────────────────────────────────────────

def test_get_non_indexed_txid_uses_gateway_content_type(tc):
    """Gateway content-type header is propagated when txid is not in the local index."""
    r = tc.get(f"/api/get/{IMAGE_TXID}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


def test_archive_html_injects_base_href(tc, monkeypatch):
    import api as api_mod

    source_url = "https://example.com/page.html"
    html = b"<html><head><title>Test</title></head><body><img src='logo.png'></body></html>"

    class FakeArchiveResp:
        status_code = 200
        ok = True
        content = html
        headers = {"content-type": "text/html; charset=utf-8"}
        def raise_for_status(self): pass

    monkeypatch.setattr(api_mod._requests, "get", lambda url, **kw: FakeArchiveResp())

    r = tc.post("/api/archive", json={"url": source_url})
    assert r.status_code == 200
    txid = r.json()["txid"]

    stored = tc.get(f"/api/get/{txid}")
    assert stored.status_code == 200
    assert f'<base href="{source_url}">' in stored.text
