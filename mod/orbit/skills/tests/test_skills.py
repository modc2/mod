"""tests for the skills marketplace.

Offline by default: the format, the catalog, the ranking and the routing all
run without a network. The three tests that actually scrape are marked `live`
and skip themselves when GitHub is unreachable — a suite that goes red because
a rate limit was hit is a suite people stop running.

    python3 -m pytest tests/ -q
    python3 -m pytest tests/ -q -m live      # include the scrapers
"""
import json
import os
import sys
import tempfile
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from skillsrc import mcp as mcpsrv          # noqa: E402
from skillsrc import skill as sd            # noqa: E402
from skillsrc.market import Market          # noqa: E402
from skillsrc.store import Store            # noqa: E402

SAMPLE = """---
name: PDF Forms
description: |-
  Fill, split and read PDF forms.
  Use it whenever a .pdf is mentioned.
tools: [bash, read, write]
tags: [documents, pdf]
license: MIT
---

# PDF forms

Use pdftk. Read the form fields first, then fill them.
"""


@pytest.fixture()
def market():
    with tempfile.TemporaryDirectory() as tmp:
        yield Market(tmp)


def online():
    try:
        urllib.request.urlopen("https://api.github.com/", timeout=6).read(10)
        return True
    except Exception:
        return False


live = pytest.mark.skipif(not online(), reason="offline")


# ── the format ───────────────────────────────────────────────────────

def test_frontmatter_block_scalar():
    """`description: |-` is how half the catalog writes a long description."""
    fm = sd.parse_frontmatter(SAMPLE)
    assert fm["name"] == "PDF Forms"
    assert fm["description"].startswith("Fill, split")
    assert "whenever a .pdf" in fm["description"]
    assert fm["tools"] == ["bash", "read", "write"]


def test_normalize_and_roundtrip():
    rec = sd.normalize(SAMPLE, source="test")
    assert rec["name"] == "pdf-forms"
    assert rec["tools"] == ["bash", "read", "write"]
    assert rec["license"] == "MIT"
    assert set(rec["tags"]) == {"documents", "pdf"}
    again = sd.normalize(sd.to_markdown(rec))
    assert (again["name"], again["tools"], again["license"]) == \
           (rec["name"], rec["tools"], rec["license"])


def test_description_is_always_synthesized():
    """A skill with no description is useless to a model choosing between twenty."""
    rec = sd.normalize("# Deploy\n\nRun the checks, then ship it, then watch the logs.")
    assert rec["name"] == "deploy"
    assert "Run the checks" in rec["description"]


def test_looks_like_skill_rejects_a_stub():
    assert sd.looks_like_skill(SAMPLE)
    assert not sd.looks_like_skill("# TODO\n\nnothing here yet")


def test_body_is_capped():
    rec = sd.normalize("# Big\n\n" + ("x" * (sd.MAX_BODY + 5000)))
    assert rec["chars"] == sd.MAX_BODY and rec["truncated"] is True


# ── the catalog ──────────────────────────────────────────────────────

def test_write_read_remove(market):
    w = market.write("pdf-forms", SAMPLE, who="tester")
    assert w["wrote"] == "pdf-forms"
    assert market.installed()["total"] == 1
    doc = market.doc("pdf-forms")
    assert "pdftk" in doc["markdown"] and doc["installed"] is True
    assert doc["tools"] == ["bash", "read", "write"]
    assert market.remove("pdf-forms")["removed"] == "pdf-forms"
    assert market.installed()["total"] == 0


def test_install_is_idempotent_and_counts_updates(market):
    market.write("a", SAMPLE)
    market.write("a", SAMPLE)
    assert market.installed()["total"] == 1
    assert market.store.meta("a")["updates"] == 1


def test_load_carries_bodies(market):
    market.write("a", SAMPLE)
    market.write("b", "# B\n\nDo b, carefully and slowly, when asked.")
    payload = market.publish(["a"])
    assert payload["total"] == 1 and "pdftk" in payload["skills"][0]["markdown"]
    assert market.publish()["total"] == 2          # unfiltered = the whole catalog


def test_catalog_names_cannot_escape(market):
    store = Store(str(market.store.dir))
    for bad in ("../../etc/passwd", "..", "/etc/passwd"):
        with pytest.raises((KeyError, ValueError)):
            store.read(bad)


def test_a_skill_folder_is_portable(market):
    """One folder, one SKILL.md — droppable straight into ~/.claude/skills."""
    market.write("pdf-forms", SAMPLE)
    path = market.store.path("pdf-forms")
    assert path.name == "SKILL.md" and path.parent.name == "pdf-forms"
    assert sd.parse_frontmatter(path.read_text())["name"] == "pdf-forms"


# ── ranking ──────────────────────────────────────────────────────────

def test_name_hits_outrank_description_hits(market):
    hit = {"source": "github", "name": "pdf-tools", "description": "", "stars": 0}
    miss = {"source": "github", "name": "framework", "description": "pdf", "stars": 0}
    assert market.web.score(hit, "pdf") > market.web.score(miss, "pdf")


def test_stars_cannot_swamp_relevance(market):
    """A 40k-star repo that mentions the query must not beat the skill for it."""
    giant = {"source": "github", "name": "framework", "description": "pdf", "stars": 40000}
    answer = {"source": "anthropic", "name": "pdf", "description": "pdf forms", "stars": 0}
    assert market.web.score(answer, "pdf") > market.web.score(giant, "pdf")


# ── surfaces ─────────────────────────────────────────────────────────

def test_every_mcp_tool_dispatches():
    names = {t["name"] for t in mcpsrv.TOOLS}
    assert "skills_search" in names and "skills_load" in names
    for t in mcpsrv.TOOLS:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    with pytest.raises(KeyError):
        mcpsrv.call_tool("skills_nope", {})


def test_mcp_write_tools_are_gated():
    denied = mcpsrv.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "skills_install", "arguments": {"id": "gh:x/y"}}},
                        authorized=False)
    assert denied["error"]["code"] == -32001
    listed = mcpsrv.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, False)
    assert len(listed["result"]["tools"]) == len(mcpsrv.TOOLS)


def test_api_routes_strip_the_gateway_prefix(monkeypatch):
    from skillsrc import api

    class Fake:
        path = "/skills/_api/health"
        headers = {}
    got = api.Handler._path(Fake())
    assert got[0] == "/health" and got[2] is True
    Fake.path = "/health"
    assert api.Handler._path(Fake())[0] == "/health"


def test_api_write_gate(monkeypatch):
    from skillsrc import api
    monkeypatch.setattr(api, "secret", lambda: None)
    assert api.authorized("127.0.0.1", {}) is True
    # a proxied caller is not on this box, whatever the socket says
    assert api.authorized("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"}) is False
    assert api.authorized("8.8.8.8", {}) is False
    monkeypatch.setattr(api, "secret", lambda: "s3cret")
    assert api.authorized("8.8.8.8", {"Authorization": "Bearer s3cret"}) is True
    assert api.authorized("8.8.8.8", {"Authorization": "Bearer nope"}) is False


def test_mod_selftest():
    import mod as skillsmod
    assert skillsmod.test()["passed"] is True


# ── the fleet as a source (no network) ───────────────────────────────

def test_registry_source_reads_this_host(market):
    hits = market.web.src_registry("", 50)
    if not hits:
        pytest.skip("no fleet on this box")
    assert all(h["id"].startswith("mod:") for h in hits)
    one = market.web.fetch(hits[0]["id"])
    assert one["body"] and one["source"] == "registry"


def test_registry_fetch_refuses_to_escape_mod_root(market):
    with pytest.raises(ValueError):
        market.web.fetch("mod:../../../etc")


# ── live: the actual scraping ────────────────────────────────────────

@live
@pytest.mark.live
def test_live_search_anthropic(market):
    r = market.search("pdf", sources=["anthropic"], limit=5, fresh=True)
    assert r["results"], r["errors"]
    assert any(i["name"] == "pdf" for i in r["results"])


@live
@pytest.mark.live
def test_live_fetch_and_install(market):
    rec = market.install("gh:anthropics/skills:skills/pdf")
    assert rec["installed"] == ["pdf"]
    assert "pdf" in market.doc("pdf")["markdown"].lower()


@live
@pytest.mark.live
def test_live_multi_source_merges(market):
    r = market.search("", sources=["anthropic", "topics", "awesome"], limit=20, fresh=True)
    ids = [i["id"] for i in r["results"]]
    assert len(ids) == len(set(ids)), "duplicate cards survived the merge"
