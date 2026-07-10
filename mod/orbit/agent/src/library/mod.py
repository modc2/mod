"""
library - unified agent library

One filterable index across four collections:
    prompts  - reusable prompt templates (user-curated, seeded with defaults)
    skills   - the agent's skill registry (read-only, from skills/)
    memory   - persistent knowledge notes the agent can be pointed at
    agents   - installed agent personas + published agent CIDs (the market)

Prompts and memory notes are private user state and live OFF-tree under
~/.mod/agent/library/ (never in the committed module dir).

Usage:
    lib = Library(skills=skills, agents=agents)
    lib.items(q="review", kind="prompt", tag="quality")
    lib.prompt_add("bug hunt", "Find and fix ...", tags=["debug"])
    lib.note_add("api conventions", "All endpoints return ...", tags=["project"])
"""
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


SEED_PROMPTS = [
    {"name": "Map the codebase", "description": "Architecture overview before touching anything",
     "text": "Map this codebase: show the file tree, read the entry points and config, then summarize the architecture, key modules, and how they connect.",
     "tags": ["explore", "onboarding"]},
    {"name": "Bug hunt", "description": "Root-cause a failure and fix it",
     "text": "Find the root cause of this bug. Reproduce it if possible, read the relevant code paths, explain the cause, then apply a minimal fix and verify it.",
     "tags": ["debug", "fix"]},
    {"name": "Write tests", "description": "Add coverage for recent changes",
     "text": "Write tests for the most recently changed code. Match the existing test style, cover the happy path and edge cases, then run the suite and report results.",
     "tags": ["tests", "quality"]},
    {"name": "Refactor pass", "description": "Simplify without changing behavior",
     "text": "Do a refactor pass on this code: remove duplication, simplify control flow, improve naming. Behavior must not change - run tests before and after.",
     "tags": ["refactor", "quality"]},
    {"name": "Security sweep", "description": "Audit for common vulnerabilities",
     "text": "Audit this code for security issues: injection, path traversal, secrets in code, unsafe deserialization, missing auth checks. Report findings by severity with file:line references.",
     "tags": ["security", "review"]},
    {"name": "Document it", "description": "Generate docs from the code",
     "text": "Read this module and write clear documentation: what it does, how to use it, key functions with examples. Keep it concise and accurate to the code.",
     "tags": ["docs"]},
    {"name": "TODO triage", "description": "Collect and prioritize open work",
     "text": "Find all TODO/FIXME/HACK comments in the codebase, group them by area, and produce a prioritized action list with effort estimates.",
     "tags": ["planning"]},
    {"name": "Performance check", "description": "Find the hot spots",
     "text": "Profile this code path for performance: identify hot spots, unnecessary work in loops, N+1 patterns, and blocking IO. Propose the top 3 fixes with expected impact.",
     "tags": ["performance", "review"]},
]

# rough category tags for built-in skills so they participate in tag filtering
SKILL_TAGS = {
    "read": ["files"], "write": ["files"], "edit": ["files"], "patch": ["files"],
    "glob": ["files", "search"], "tree": ["files", "explore"],
    "grep": ["search"], "search": ["search"], "symbols": ["search", "code"],
    "context": ["search", "explore"],
    "bash": ["exec"], "task": ["exec"], "fetch": ["exec", "web"],
    "git": ["vcs"], "diff": ["vcs"],
    "test": ["quality"], "lint": ["quality"], "debug": ["quality", "fix"],
    "refactor": ["quality", "code"],
    "think": ["planning"], "todo": ["planning"],
}


class Library:
    description = "Unified library: prompts, memory notes, skills, and the agent market"

    def __init__(self, skills=None, agents=None, dir: str = None):
        self.dir = Path(dir) if dir else Path.home() / '.mod' / 'agent' / 'library'
        self.dir.mkdir(parents=True, exist_ok=True)
        self._skills = skills
        self._agents = agents

    # ── json store helpers ───────────────────────────────────────────

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def _load(self, name: str) -> Optional[List[Dict]]:
        p = self._path(name)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None

    def _save(self, name: str, data: List[Dict]):
        self._path(name).write_text(json.dumps(data, indent=2, default=str))

    # ── prompts ──────────────────────────────────────────────────────

    def prompts(self) -> List[Dict]:
        """List prompts, seeding defaults on first use."""
        data = self._load('prompts')
        if data is None:
            data = [{**p, "id": f"p-{i}", "updated": time.time(), "builtin": True}
                    for i, p in enumerate(SEED_PROMPTS)]
            self._save('prompts', data)
        return data

    def prompt_add(self, name: str, text: str, description: str = "",
                   tags: List[str] = None, id: str = None) -> Dict:
        """Create or update a prompt (upsert by id)."""
        if not name or not text:
            raise ValueError("prompt requires name and text")
        data = self.prompts()
        entry = {
            "id": id or f"p-{uuid.uuid4().hex[:8]}",
            "name": name, "text": text, "description": description,
            "tags": tags or [], "updated": time.time(),
        }
        data = [p for p in data if p.get("id") != entry["id"]]
        data.append(entry)
        self._save('prompts', data)
        return entry

    def prompt_rm(self, id: str) -> Dict:
        data = self.prompts()
        kept = [p for p in data if p.get("id") != id]
        if len(kept) == len(data):
            raise KeyError(f"prompt not found: {id}")
        self._save('prompts', kept)
        return {"removed": id}

    # ── memory notes ─────────────────────────────────────────────────

    def notes(self) -> List[Dict]:
        return self._load('memory') or []

    def note_add(self, name: str, content: str, tags: List[str] = None,
                 id: str = None) -> Dict:
        """Create or update a memory note (upsert by id)."""
        if not name or not content:
            raise ValueError("memory note requires name and content")
        data = self.notes()
        entry = {
            "id": id or f"m-{uuid.uuid4().hex[:8]}",
            "name": name, "content": content,
            "tags": tags or [], "updated": time.time(),
        }
        data = [n for n in data if n.get("id") != entry["id"]]
        data.append(entry)
        self._save('memory', data)
        return entry

    def note_rm(self, id: str) -> Dict:
        data = self.notes()
        kept = [n for n in data if n.get("id") != id]
        if len(kept) == len(data):
            raise KeyError(f"memory note not found: {id}")
        self._save('memory', kept)
        return {"removed": id}

    # ── unified index ────────────────────────────────────────────────

    def items(self, q: str = None, kind: str = None, tag: str = None) -> Dict:
        """Aggregate all collections into one filterable list.

        Filters compose: q matches name/description/body text, kind is one of
        prompt|skill|memory|agent, tag must be present in the item's tags.
        Facet counts (kinds, tags) are computed BEFORE the kind filter so the
        UI can show counts for every pill while one is selected.
        """
        items: List[Dict] = []

        for p in self.prompts():
            items.append({"kind": "prompt", "id": p["id"], "name": p["name"],
                          "description": p.get("description", ""),
                          "tags": p.get("tags", []), "body": p.get("text", ""),
                          "updated": p.get("updated"),
                          "builtin": p.get("builtin", False)})

        if self._skills:
            try:
                schemas = self._skills.schema()
            except Exception:
                schemas = {}
            for sname in self._skills.ls():
                sch = schemas.get(sname, {})
                items.append({"kind": "skill", "id": f"s-{sname}", "name": sname,
                              "description": sch.get("description", ""),
                              "tags": SKILL_TAGS.get(sname, ["skill"]),
                              "params": sch.get("params", {}), "builtin": True})

        for n in self.notes():
            items.append({"kind": "memory", "id": n["id"], "name": n["name"],
                          "description": (n.get("content", "")[:120]),
                          "tags": n.get("tags", []), "body": n.get("content", ""),
                          "updated": n.get("updated")})

        if self._agents:
            try:
                schemas = self._agents.schema()
            except Exception:
                schemas = {}
            for aname, cfg in schemas.items():
                if "error" in cfg:
                    continue
                items.append({"kind": "agent", "id": f"a-{aname}", "name": aname,
                              "label": cfg.get("name", aname),
                              "description": cfg.get("description", ""),
                              "tags": ["installed"] + (["custom"] if aname not in
                                      ("default", "architect", "reviewer", "debugger",
                                       "builder", "refactorer", "safety") else []),
                              "icon": cfg.get("icon", ">_"),
                              "body": cfg.get("goal", "") or "",
                              "skills": cfg.get("skills"), "model": cfg.get("model")})
            try:
                for c in self._agents.ls_cids():
                    items.append({"kind": "agent", "id": f"a-cid-{c['cid']}",
                                  "name": c.get("name") or c["cid"][:14],
                                  "description": f"published agent · {c['cid'][:20]}…",
                                  "tags": ["published"] + (["private"] if c.get("private") else []),
                                  "cid": c["cid"], "updated": c.get("saved")})
            except Exception:
                pass

        # q filter first, then facets, then kind/tag filter
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["name"].lower()
                     or ql in i.get("description", "").lower()
                     or ql in i.get("body", "").lower()
                     or any(ql in t for t in i.get("tags", []))]

        kinds: Dict[str, int] = {}
        tags: Dict[str, int] = {}
        for i in items:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        for i in items:
            if kind and i["kind"] != kind:
                continue
            for t in i.get("tags", []):
                tags[t] = tags.get(t, 0) + 1

        if kind:
            items = [i for i in items if i["kind"] == kind]
        if tag:
            items = [i for i in items if tag in i.get("tags", [])]

        return {"items": items, "total": len(items),
                "facets": {"kinds": kinds, "tags": tags}}

    # ── mod protocol entry point ─────────────────────────────────────

    def forward(self, action: str = None, **kwargs) -> Any:
        if action == "prompts":
            return {"prompts": self.prompts()}
        if action == "prompt_add":
            return self.prompt_add(kwargs.get("name", ""), kwargs.get("text", ""),
                                   kwargs.get("description", ""), kwargs.get("tags"),
                                   kwargs.get("id"))
        if action == "prompt_rm":
            return self.prompt_rm(kwargs.get("id", ""))
        if action == "memory":
            return {"memory": self.notes()}
        if action == "memory_add":
            return self.note_add(kwargs.get("name", ""), kwargs.get("content", ""),
                                 kwargs.get("tags"), kwargs.get("id"))
        if action == "memory_rm":
            return self.note_rm(kwargs.get("id", ""))
        return self.items(q=kwargs.get("q"), kind=kwargs.get("kind"),
                          tag=kwargs.get("tag"))

    def test(self) -> bool:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            lib = Library(dir=d)
            assert len(lib.prompts()) == len(SEED_PROMPTS)
            p = lib.prompt_add("t", "text", tags=["x"])
            assert any(i["id"] == p["id"] for i in lib.prompts())
            lib.prompt_rm(p["id"])
            n = lib.note_add("k", "v", tags=["y"])
            assert lib.notes()[0]["name"] == "k"
            out = lib.items(q="k", kind="memory")
            assert out["total"] == 1
            lib.note_rm(n["id"])
            assert lib.notes() == []
        return True
