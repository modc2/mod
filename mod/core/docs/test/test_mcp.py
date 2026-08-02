"""docs mcp — protocol + tool tests (no server, no network: handle() direct).

Run: pytest core/docs/test
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location("docs_mcp", os.path.join(MODULE, "api", "mcp.py"))
mcp = importlib.util.module_from_spec(spec)
sys.modules["docs_mcp"] = mcp
spec.loader.exec_module(mcp)


def call(method, params=None, id_=1):
    return mcp.handle({"jsonrpc": "2.0", "id": id_, "method": method,
                       "params": params or {}})


def tool(_name, **args):
    r = call("tools/call", {"name": _name, "arguments": args})["result"]
    return r, r["content"][0]["text"]


# ── protocol ──

def test_initialize_echoes_known_protocol_version():
    r = call("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert r["protocolVersion"] == "2025-06-18"
    assert r["serverInfo"]["name"] == "docs"
    assert r["capabilities"]["tools"] == {}
    assert "docs_page" in r["instructions"]


def test_initialize_falls_back_for_unknown_version():
    r = call("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert r["protocolVersion"] == mcp.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_shape():
    tools = call("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == set(mcp.TOOLS)
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_unknown_method_and_tool_are_errors():
    assert call("nope")["error"]["code"] == -32601
    assert call("tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert mcp.handle({"jsonrpc": "2.0", "id": 1})["error"]["code"] == -32600


# ── tools ──

def test_overview_is_the_front_door():
    r, text = tool("docs_overview")
    assert not r["isError"] and "mod" in text.lower()


def test_pages_flags_simple_twins():
    r, _ = tool("docs_pages")
    pages = r["structuredContent"]["pages"]
    assert {"cli", "protocol", "whitepaper"} <= {p["name"] for p in pages}
    assert any(p["simple"] for p in pages)


def test_page_serves_both_variants():
    tech = tool("docs_page", name="cli")[0]["structuredContent"]
    simple = tool("docs_page", name="cli", simple=True)[0]["structuredContent"]
    assert tech["variant"] == "tech" and simple["variant"] == "simple"
    assert tech["text"] != simple["text"]


def test_page_requires_a_name_and_reports_missing_pages():
    r, text = tool("docs_page")
    assert r["isError"] and "name required" in text
    r, text = tool("docs_page", name="no-such-page")
    assert r["isError"] and "no doc page" in text


def test_search_spans_pages_and_modules():
    r, _ = tool("docs_search", query="storage")
    hits = r["structuredContent"]
    assert isinstance(hits["pages"], list) and "store" in hits["modules"]


def test_whitepaper_formats():
    assert tool("docs_whitepaper")[0]["structuredContent"]["fmt"] == "md"
    assert "#" in tool("docs_whitepaper", fmt="simple")[0]["structuredContent"]["text"]
    assert tool("docs_whitepaper", fmt="bogus")[0]["isError"]


def test_modules_catalog_and_module_doc():
    core = tool("docs_modules", group="core")[0]["structuredContent"]["modules"]
    assert {"docs", "hub"} <= {x["name"] for x in core}
    assert all(x["group"] == "core" for x in core)
    doc = tool("docs_module_doc", module="hub")[0]["structuredContent"]
    assert doc["module"] == "hub" and doc["readme"]


def test_results_are_json_when_structured():
    r, text = tool("docs_pages")
    assert json.loads(text) == r["structuredContent"]
