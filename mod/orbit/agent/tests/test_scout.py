"""Scouting an agent idea — POST /agents/scout and the machinery under it.

The network and the model are both faked: the scout's sources return fixed
hits, the crawler returns fixed pages, and each model run is one finish step
carrying JSON (the idea-scout's pitch first, then the vibe-builder's spec).
What's pinned is the pipeline itself — phases in order, citations held to the
digest, the pitch reaching vibe intact, a dead source not sinking the run,
and the run being kept so the process can be reopened.
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import src.scout.mod as scout_mod
from src.scout.mod import Scout
from src.mod import Mod
from src.agents.mod import BUILTINS
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vibe import _make_mod, _finish_trace, CATALOG  # noqa: E402


class FakeCrawler:
    def search(self, q, limit=8):
        return [{"url": "https://web.example/a", "title": "Web A", "snippet": "s"}]

    def _allowed(self, url):
        return "blocked" not in url

    def _page(self, url):
        return {"text": f"readable text of {url} " * 20, "links": [], "url": url}


def _fake_sources(monkeypatch, broken=()):
    def make(name, hits):
        def fn(theme, limit=6):
            if name in broken:
                raise RuntimeError(f"{name} is down")
            return hits
        return fn
    monkeypatch.setitem(scout_mod.SOURCES, "hn", {"label": "HN", "fn": make("hn", [
        {"title": "Show HN: tiny local LLM router", "url": "https://hn.example/1",
         "snippet": "", "meta": "300 pts", "score": 300.0}])})
    monkeypatch.setitem(scout_mod.SOURCES, "github", {"label": "GH", "fn": make("github", [
        {"title": "someone/repo", "url": "https://github.com/someone/repo",
         "snippet": "a repo", "meta": "★ 50", "score": 50.0},
        {"title": "dup", "url": "https://hn.example/1/", "snippet": "", "score": 1.0}])})
    monkeypatch.setitem(scout_mod.SOURCES, "arxiv", {"label": "arXiv", "fn": make("arxiv", [
        {"title": "A paper", "url": "https://arxiv.example/blocked", "snippet": "abs",
         "score": 0.0}])})


IDEA = {"title": "Router Whisperer", "pitch": "Routes prompts to the cheapest local model",
        "why_now": "local routers are trending", "inspired_by": [1, 2, 99, "x"],
        "brief": "You take a prompt, benchmark local models, pick one.",
        "novelty": "nothing here routes"}
SPEC = {"names": ["router-whisperer"], "icon": "◎", "description": "routes prompts",
        "prompt": "You are a router.", "tools": ["read", "think", "finish"]}


def _scout_mod(tmp_path, idea=IDEA, spec=SPEC, direct_ok=True):
    mod = _make_mod()
    mod.is_owner = lambda key=None: False
    mod._vibe_catalog = lambda key=None: CATALOG
    mod._scout_inst = Scout(dir=str(tmp_path), crawler=FakeCrawler())
    calls = []

    def run(**kw):
        calls.append(kw)
        if kw.get("on_step"):
            kw["on_step"]({"tool": "think", "params": {"thought": "hmm"}})
        return _finish_trace(idea if kw["agent_type"] == Mod.IDEA_SCOUT else spec)
    mod._run = run

    def complete(query, on_token=None, agent_type=None, **kw):
        vibe = agent_type == Mod.VIBE_BUILDER
        calls.append({"agent_type": "direct-vibe" if vibe else "direct", "query": query})
        text = "```json\n" + json.dumps(spec if vibe else idea) + "\n```" if direct_ok else "lol no"
        if on_token:
            on_token(text)
        return text
    mod._scout_complete = complete
    return mod, calls


class TestScoutPieces:
    def test_self_test(self, tmp_path):
        assert Scout(dir=str(tmp_path), crawler=FakeCrawler()).test()

    def test_idea_scout_is_a_builtin(self):
        assert "idea-scout" in BUILTINS

    def test_gather_numbers_dedupes_and_survives_a_dead_source(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch, broken=("arxiv",))
        ev = []
        sigs = Scout(dir=str(tmp_path), crawler=FakeCrawler()).gather("x", on_event=ev.append)
        assert [s["n"] for s in sigs] == list(range(1, len(sigs) + 1))
        urls = [s["url"].rstrip("/") for s in sigs]
        assert len(urls) == len(set(urls))                # trailing-slash dup dropped
        assert any(e["type"] == "source_error" and e["source"] == "arxiv" for e in ev)
        assert {s["source"] for s in sigs} == {"hn", "github", "web"}

    def test_read_honors_robots(self, tmp_path):
        s = Scout(dir=str(tmp_path), crawler=FakeCrawler())
        pages = s.read([{"n": 1, "source": "arxiv", "url": "https://x/blocked"},
                        {"n": 2, "source": "hn", "url": "https://x/ok"}], limit=4)
        by = {p["n"]: p["status"] for p in pages}
        assert by == {1: "skipped", 2: "ok"}

    def test_parse_idea_needs_a_brief_or_pitch(self):
        assert Mod._parse_idea_json('```json\n{"brief": "b"}\n```') == {"brief": "b"}
        assert Mod._parse_idea_json('{"title": "t"}') is None

    def test_parse_idea_survives_a_typographic_quote(self):
        raw = '```json\n{"title": "t", "pitch": "p\u201d, "brief": "b"}\n```'
        assert Mod._parse_idea_json(raw)["brief"] == "b"

    def test_citations_held_to_the_digest(self):
        sigs = [{"n": 1, "source": "hn", "title": "a", "url": "u1"}]
        idea = Mod._clean_idea({"pitch": "p", "inspired_by": [1, 1, 7, "[1]", "z"]}, sigs)
        assert [r["n"] for r in idea["inspired_by"]] == [1]
        assert idea["brief"] == "p"                       # pitch backs a missing brief


class TestAgentScout:
    def test_whole_process_end_to_end(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, calls = _scout_mod(tmp_path)
        ev = []
        before = set(mod.agents.ls())
        run = mod.agent_scout(theme="local llm", on_event=ev.append)
        assert run["status"] == "done", run.get("error")
        phases = [e["phase"] for e in ev if e["type"] == "phase"]
        assert phases == ["lens", "search", "read", "ideate", "vibe"]
        kinds = {e["type"] for e in ev}
        assert {"signal", "read_done", "model_start", "token", "idea", "draft"} <= kinds
        # the ideator read the digest; vibe got the pitch
        assert "DIGEST" in calls[0]["query"] and "[1]" in calls[0]["query"]
        assert calls[0]["agent_type"] == "direct"          # no tool loop by default
        assert calls[1]["agent_type"] == "direct-vibe"     # vibe drafted direct too
        assert any(e["type"] == "token" and e["phase"] == "ideate" for e in ev)
        assert "Router Whisperer" in calls[1]["query"]
        assert [r["n"] for r in run["idea"]["inspired_by"]] == [1, 2]
        assert run["draft"]["name"] == "router-whisperer"
        assert set(mod.agents.ls()) == before             # draft only
        # kept, and readable back by its owner
        assert mod.agent_scout_run(run["id"])["idea"]["title"] == "Router Whisperer"
        assert mod.agent_scout_runs()["runs"][0]["id"] == run["id"]

    def test_junk_completion_falls_back_to_the_agent_loop(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, calls = _scout_mod(tmp_path, direct_ok=False)
        run = mod.agent_scout(theme="x")
        assert run["status"] == "done" and run["draft"]["name"] == "router-whisperer"
        assert [c["agent_type"] for c in calls] == \
            ["direct", "idea-scout", "direct-vibe", "vibe-builder"]

    def test_harness_skips_the_direct_call(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, calls = _scout_mod(tmp_path)
        mod._draft_harness = lambda n: n
        mod._run_harness = lambda name, **kw: (calls.append({"agent_type": "harness:" + name}),
                                               _finish_trace(IDEA))[1]
        run = mod.agent_scout(theme="x", vibe=False, harness="claude")
        assert run["status"] == "done"
        assert [c["agent_type"] for c in calls] == ["harness:claude"]

    def test_vibe_false_stops_at_the_idea(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, calls = _scout_mod(tmp_path)
        run = mod.agent_scout(theme="x", vibe=False)
        assert run["idea"] and "draft" not in run and len(calls) == 1

    def test_random_lens_when_no_theme(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, _ = _scout_mod(tmp_path)
        run = mod.agent_scout(vibe=False)
        assert run["lens"]["picked"] and run["lens"]["theme"] in scout_mod.LENSES

    def test_no_idea_is_an_error_run_still_kept(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, _ = _scout_mod(tmp_path, idea={"nope": 1})
        run = mod.agent_scout(theme="x")
        assert run["status"] == "error" and "idea" in run["error"]
        assert mod.agent_scout_run(run["id"])["status"] == "error"

    def test_someone_elses_run_is_not_yours(self, tmp_path, monkeypatch):
        _fake_sources(monkeypatch)
        mod, _ = _scout_mod(tmp_path)
        run = mod.agent_scout(theme="x", vibe=False)
        mod.identity = SimpleNamespace(require_signed_in=lambda key=None, operation=None: None,
                                       addr=lambda key=None: "0xother")
        try:
            mod.agent_scout_run(run["id"])
            assert False, "should have raised"
        except ValueError:
            pass
        assert mod.agent_scout_runs()["runs"] == []
