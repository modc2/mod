"""
library - unified agent library

One filterable index across four collections:
    prompts  - reusable prompt templates (user-curated, seeded with defaults)
    skills   - the agent's skill registry (read-only, from skills/) plus
               external skills installed from the internet via discover/
    memory   - persistent knowledge notes the agent can be pointed at
    agents   - installed agent personas + published agent CIDs (the market)

Plus a per-user conversations store (the console's chat history) so sessions
survive browsers, devices, and the shared modc2 localStorage origin.

Prompts, memory notes, and conversations are private user state and live
OFF-tree under ~/.mod/agent/library/ (never in the committed module dir).
Every prompt, note, and conversation is ALSO pinned to localfs — the mod
framework's content-addressed blob store — and carries its CID, so any item
can be shared/restored by CID alone.

Prompts, memory notes, and installed skills carry an owner — the address that
saved them — so making one takes a sign-in. Seeded and legacy items have no
owner, so they belong to the HOST (the module owner), who can edit or remove
anything. Everyone else manages only their own.

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

try:
    import mod as m
except ImportError:
    m = None

try:
    from src.identity import Identity
except ImportError:  # running the library standalone
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.identity import Identity


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

    def __init__(self, skills=None, agents=None, dir: str = None,
                 identity: "Identity" = None):
        self.dir = Path(dir) if dir else Path.home() / '.mod' / 'agent' / 'library'
        self.dir.mkdir(parents=True, exist_ok=True)
        self._skills = skills
        self._agents = agents
        # unbound library: no host known, so unowned items stay open
        self.identity = identity or Identity()

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

    def _localfs(self):
        """The mod framework's content-addressed blob store, or None."""
        if not m:
            return None
        try:
            return m.mod('localfs')()
        except Exception:
            return None

    @staticmethod
    def _bundle(kind: str, item: Dict) -> Dict:
        """Portable share bundle — only content fields, so identical items
        get identical CIDs regardless of local id/timestamps."""
        if kind == "prompt":
            return {"type": "prompt", "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "text": item.get("text", ""), "tags": item.get("tags", [])}
        if kind == "memory":
            return {"type": "memory", "name": item.get("name", ""),
                    "content": item.get("content", ""), "tags": item.get("tags", [])}
        if kind == "skill":
            return {"type": "skill", "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "body": item.get("body", ""), "tags": item.get("tags", []),
                    "source": item.get("source", ""), "url": item.get("url", "")}
        if kind == "conversation":
            return {"type": "conversation", "query": item.get("query", ""),
                    "agent_type": item.get("agent_type", "default"),
                    "status": item.get("status", "done"),
                    "messages": item.get("messages", [])}
        raise ValueError(f"unknown bundle kind: {kind}")

    def _mint_cids(self, data: List[Dict], kind: str = "prompt") -> bool:
        """Pin every item missing a cid to localfs. Returns True if any minted."""
        pending = [p for p in data if not p.get("cid")]
        if not pending:
            return False
        localfs = self._localfs()
        if localfs is None:
            return False
        changed = False
        for p in pending:
            try:
                p["cid"] = localfs.put(self._bundle(kind, p))
                changed = True
            except Exception:
                pass
        return changed

    def _fetch_bundle(self, cid: str, kind: str) -> Dict:
        """Load and type-check a share bundle from its localfs CID."""
        if not cid:
            raise ValueError("cid required")
        localfs = self._localfs()
        if localfs is None:
            raise RuntimeError("localfs unavailable")
        bundle = localfs.get(cid)
        if not isinstance(bundle, dict) or bundle.get("type") != kind:
            raise ValueError(f"CID does not contain a {kind}: {cid}")
        return bundle

    def prompts(self) -> List[Dict]:
        """List prompts, seeding defaults on first use.

        Every prompt is pinned to localfs and carries its CID; missing CIDs
        (seeds, pre-CID entries, or saves made while localfs was down) are
        minted here on read.
        """
        data = self._load('prompts')
        if data is None:
            data = [{**p, "id": f"p-{i}", "updated": time.time(), "builtin": True}
                    for i, p in enumerate(SEED_PROMPTS)]
            self._mint_cids(data)
            self._save('prompts', data)
        elif self._mint_cids(data):
            self._save('prompts', data)
        return data

    def prompt_owner(self, prompt: Dict) -> Dict:
        """Effective owner of a prompt — its own, else the host."""
        return self.identity.owner_of(prompt.get("owner"))

    def _require_prompt(self, prompt: Dict, key: str = None, operation: str = "modify"):
        self.identity.require(
            prompt.get("owner"), key,
            operation=f"cannot {operation} prompt: {prompt.get('name') or prompt.get('id')}",
            builtin=False)

    def prompt_add(self, name: str, text: str, description: str = "",
                   tags: List[str] = None, id: str = None,
                   key: str = None) -> Dict:
        """Create or update a prompt (upsert by id). Pins content to localfs.

        A new prompt is owned by the caller, so saving one needs a sign-in;
        editing an existing one is restricted to its owner or the host, and
        never transfers ownership.
        """
        if not name or not text:
            raise ValueError("prompt requires name and text")
        data = self.prompts()
        existing = next((p for p in data if p.get("id") == id), None) if id else None
        if existing is not None:
            self._require_prompt(existing, key, "edit")
        else:
            self.identity.require_signed_in(key, f"create prompt: {name}")
        owner = existing.get("owner") if existing else self.identity.addr(key)
        entry = {
            "id": id or f"p-{uuid.uuid4().hex[:8]}",
            "name": name, "text": text, "description": description,
            "tags": tags or [], "updated": time.time(),
        }
        if owner:
            entry["owner"] = owner
        self._mint_cids([entry])
        data = [p for p in data if p.get("id") != entry["id"]]
        data.append(entry)
        self._save('prompts', data)
        return {**entry, **self.prompt_owner(entry)}

    def prompt_import(self, cid: str, key: str = None) -> Dict:
        """Install a prompt from its localfs CID (the QR / share path).

        The importer owns their copy — sharing hands over content, not control.
        """
        bundle = self._fetch_bundle(cid, "prompt")
        if not bundle.get("name") or not bundle.get("text"):
            raise ValueError(f"prompt bundle missing name/text: {cid}")
        entry = self.prompt_add(bundle["name"], bundle["text"],
                                bundle.get("description", ""), bundle.get("tags"),
                                key=key)
        return entry

    def prompt_rm(self, id: str, key: str = None) -> Dict:
        data = self.prompts()
        target = next((p for p in data if p.get("id") == id), None)
        if target is None:
            raise KeyError(f"prompt not found: {id}")
        self._require_prompt(target, key, "remove")
        self._save('prompts', [p for p in data if p.get("id") != id])
        return {"removed": id}

    # ── memory notes ─────────────────────────────────────────────────

    def notes(self) -> List[Dict]:
        """List memory notes. Like prompts, every note is pinned to localfs —
        missing CIDs (pre-CID entries or saves made while localfs was down)
        are minted here on read."""
        data = self._load('memory') or []
        if self._mint_cids(data, "memory"):
            self._save('memory', data)
        return data

    def note_owner(self, note: Dict) -> Dict:
        """Effective owner of a note — its own, else the host."""
        return self.identity.owner_of(note.get("owner"))

    def _require_note(self, note: Dict, key: str = None, operation: str = "modify"):
        self.identity.require(
            note.get("owner"), key,
            operation=f"cannot {operation} memory note: {note.get('name') or note.get('id')}",
            builtin=False)

    def note_add(self, name: str, content: str, tags: List[str] = None,
                 id: str = None, key: str = None) -> Dict:
        """Create or update a memory note (upsert by id). Pins to localfs.

        Same rule as prompts: a new note belongs to the signed-in caller who
        made it, and only they (or the host) can edit it afterwards.
        """
        if not name or not content:
            raise ValueError("memory note requires name and content")
        data = self.notes()
        existing = next((n for n in data if n.get("id") == id), None) if id else None
        if existing is not None:
            self._require_note(existing, key, "edit")
        else:
            self.identity.require_signed_in(key, f"create memory note: {name}")
        owner = existing.get("owner") if existing else self.identity.addr(key)
        entry = {
            "id": id or f"m-{uuid.uuid4().hex[:8]}",
            "name": name, "content": content,
            "tags": tags or [], "updated": time.time(),
        }
        if owner:
            entry["owner"] = owner
        self._mint_cids([entry], "memory")
        data = [n for n in data if n.get("id") != entry["id"]]
        data.append(entry)
        self._save('memory', data)
        return {**entry, **self.note_owner(entry)}

    def note_import(self, cid: str, key: str = None) -> Dict:
        """Install a memory note from its localfs CID.

        The importer owns their copy — sharing hands over content, not control.
        """
        bundle = self._fetch_bundle(cid, "memory")
        if not bundle.get("name") or not bundle.get("content"):
            raise ValueError(f"memory bundle missing name/content: {cid}")
        return self.note_add(bundle["name"], bundle["content"], bundle.get("tags"),
                             key=key)

    def note_rm(self, id: str, key: str = None) -> Dict:
        data = self.notes()
        target = next((n for n in data if n.get("id") == id), None)
        if target is None:
            raise KeyError(f"memory note not found: {id}")
        self._require_note(target, key, "remove")
        self._save('memory', [n for n in data if n.get("id") != id])
        return {"removed": id}

    # ── installed skills (external, from the discover aggregator) ────
    # A skill found on the internet is instruction markdown (a SKILL.md), not
    # code — installing one never adds an executable to the registry. It is
    # stored as a document the agent can be pointed at, pinned to localfs like
    # every other library item so it can be re-shared by CID.

    MAX_SKILL_CHARS = 120_000

    def installed_skills(self) -> List[Dict]:
        """External skills installed from the aggregator."""
        data = self._load('skills') or []
        if self._mint_cids(data, "skill"):
            self._save('skills', data)
        return data

    def skill_owner(self, skill: Dict) -> Dict:
        """Effective owner of an installed skill — its own, else the host."""
        return self.identity.owner_of(skill.get("owner"))

    def _require_skill(self, skill: Dict, key: str = None, operation: str = "modify"):
        self.identity.require(
            skill.get("owner"), key,
            operation=f"cannot {operation} installed skill: {skill.get('name') or skill.get('id')}",
            builtin=False)

    def skill_add(self, name: str, body: str, description: str = "",
                  tags: List[str] = None, source: str = "", url: str = "",
                  origin_id: str = "", license: str = None,
                  id: str = None, key: str = None) -> Dict:
        """Install (or refresh) an external skill.

        Upserts on id, else on origin_id/url — re-installing the same skill
        updates it in place instead of piling up duplicates. Installing is a
        signed-in action and the installer owns what they added; refreshing
        someone else's install is theirs (or the host's) to do.
        """
        if not name or not body:
            raise ValueError("skill requires name and body")
        data = self.installed_skills()
        prior = None
        if id:
            prior = next((s for s in data if s.get("id") == id), None)
        if prior is None and (origin_id or url):
            prior = next((s for s in data
                          if (origin_id and s.get("origin_id") == origin_id)
                          or (url and s.get("url") == url)), None)
        if prior is not None:
            self._require_skill(prior, key, "refresh")
        else:
            self.identity.require_signed_in(key, f"install skill: {name}")
        # a refresh may carry fewer fields than the original install — keep
        # the provenance (url/source/origin) rather than blanking it
        was = prior or {}
        entry = {
            "id": was.get("id") or id or f"k-{uuid.uuid4().hex[:8]}",
            "name": name, "description": description or was.get("description", ""),
            "body": body[:self.MAX_SKILL_CHARS],
            "tags": tags or was.get("tags") or [],
            "source": source or was.get("source") or "web",
            "url": url or was.get("url", ""),
            "origin_id": origin_id or was.get("origin_id", ""),
            "license": license or was.get("license"),
            "installed": was.get("installed") or time.time(),
            "updated": time.time(),
        }
        owner = was.get("owner") or self.identity.addr(key)
        if owner:
            entry["owner"] = owner
        self._mint_cids([entry], "skill")
        data = [s for s in data if s.get("id") != entry["id"]]
        data.append(entry)
        self._save('skills', data)
        return {**entry, **self.skill_owner(entry)}

    def skill_import(self, cid: str, key: str = None) -> Dict:
        """Install an external skill from its localfs CID."""
        bundle = self._fetch_bundle(cid, "skill")
        if not bundle.get("name") or not bundle.get("body"):
            raise ValueError(f"skill bundle missing name/body: {cid}")
        return self.skill_add(bundle["name"], bundle["body"],
                              bundle.get("description", ""), bundle.get("tags"),
                              bundle.get("source", ""), bundle.get("url", ""),
                              key=key)

    def skill_rm(self, id: str, key: str = None) -> Dict:
        data = self.installed_skills()
        target = next((s for s in data if s.get("id") == id), None)
        if target is None:
            raise KeyError(f"installed skill not found: {id}")
        self._require_skill(target, key, "uninstall")
        self._save('skills', [s for s in data if s.get("id") != id])
        return {"removed": id}

    def skill_docs(self, ids: List[str]) -> List[Dict]:
        """The installed skills matching these ids (run-context injection)."""
        want = set(ids or [])
        return [s for s in self.installed_skills() if s.get("id") in want]

    # ── conversations (per-user console chat history) ────────────────
    # Server-side so sessions survive browsers/devices and the shared modc2
    # localStorage origin. Scoped by the caller's verified address; every
    # conversation is pinned to localfs and carries its CID.

    MAX_CONVS_PER_USER = 60      # newest kept, oldest dropped
    MAX_CONV_CHARS = 200_000     # per-conversation JSON budget

    def convs(self, user: str) -> List[Dict]:
        """List a user's conversations, newest first (messages included)."""
        if not user:
            return []
        data = self._load('conversations') or []
        mine = [c for c in data if c.get("user") == user.lower()]
        mine.sort(key=lambda c: -(c.get("updated") or 0))
        return mine

    @staticmethod
    def _clip_conv(entry: Dict) -> Dict:
        """Trim runaway step results so one conversation can't bloat the store."""
        s = json.dumps(entry, default=str)
        if len(s) <= Library.MAX_CONV_CHARS:
            return entry
        clip = lambda v: v[:2000] + "…[truncated]" if isinstance(v, str) and len(v) > 2000 else v
        msgs = []
        for msg in entry.get("messages", []):
            msg = dict(msg)
            msg["text"] = clip(msg.get("text"))
            if isinstance(msg.get("steps"), list):
                msg["steps"] = [{**st, "result": clip(st.get("result"))}
                                for st in msg["steps"][:60] if isinstance(st, dict)]
            msgs.append(msg)
        return {**entry, "messages": msgs}

    def conv_save(self, user: str, id: str = None, query: str = "",
                  agent_type: str = "default", status: str = "done",
                  messages: List[Dict] = None, started: float = None) -> Dict:
        """Upsert a conversation for a user. Pins the content to localfs."""
        if not user:
            raise PermissionError("sign in to save conversations")
        if not query and not messages:
            raise ValueError("conversation requires a query or messages")
        data = self._load('conversations') or []
        entry = self._clip_conv({
            "id": id or f"c-{uuid.uuid4().hex[:10]}",
            "user": user.lower(),
            "query": (query or "")[:400],
            "agent_type": agent_type or "default",
            "status": status or "done",
            "messages": messages or [],
            "started": started,
            "updated": time.time(),
        })
        entry.pop("cid", None)
        self._mint_cids([entry], "conversation")
        data = [c for c in data if not (c.get("id") == entry["id"]
                                        and c.get("user") == entry["user"])]
        data.append(entry)
        # cap per-user history (newest win)
        mine = sorted([c for c in data if c.get("user") == entry["user"]],
                      key=lambda c: -(c.get("updated") or 0))
        if len(mine) > self.MAX_CONVS_PER_USER:
            keep = {c.get("id") for c in mine[:self.MAX_CONVS_PER_USER]}
            data = [c for c in data
                    if c.get("user") != entry["user"] or c.get("id") in keep]
        self._save('conversations', data)
        return entry

    def conv_rm(self, user: str, id: str) -> Dict:
        if not user:
            raise PermissionError("sign in to manage conversations")
        data = self._load('conversations') or []
        kept = [c for c in data if not (c.get("id") == id
                                        and c.get("user") == user.lower())]
        if len(kept) == len(data):
            raise KeyError(f"conversation not found: {id}")
        self._save('conversations', kept)
        return {"removed": id}

    def conv_import(self, user: str, cid: str) -> Dict:
        """Install a shared conversation from its localfs CID."""
        bundle = self._fetch_bundle(cid, "conversation")
        return self.conv_save(user, query=bundle.get("query", ""),
                              agent_type=bundle.get("agent_type", "default"),
                              status=bundle.get("status", "done"),
                              messages=bundle.get("messages", []))

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
                          "updated": p.get("updated"), "cid": p.get("cid"),
                          "builtin": p.get("builtin", False),
                          **self.prompt_owner(p)})

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

        for s in self.installed_skills():
            items.append({"kind": "skill", "id": s["id"], "name": s["name"],
                          "description": s.get("description", ""),
                          "tags": list(dict.fromkeys(
                              ["installed", s.get("source", "web")]
                              + [t for t in s.get("tags", []) if t][:6])),
                          "body": s.get("body", ""), "url": s.get("url", ""),
                          "source": s.get("source", ""),
                          "license": s.get("license"),
                          "updated": s.get("updated"), "cid": s.get("cid"),
                          "external": True, "builtin": False,
                          **self.skill_owner(s)})

        for n in self.notes():
            items.append({"kind": "memory", "id": n["id"], "name": n["name"],
                          "description": (n.get("content", "")[:120]),
                          "tags": n.get("tags", []), "body": n.get("content", ""),
                          "updated": n.get("updated"), "cid": n.get("cid"),
                          **self.note_owner(n)})

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
                              "tags": ["installed"] + ([] if cfg.get("builtin") else ["custom"]),
                              "icon": cfg.get("icon", ">_"),
                              "body": cfg.get("goal", "") or "",
                              "builtin": bool(cfg.get("builtin")),
                              "owner": cfg.get("owner"),
                              "owner_source": cfg.get("owner_source"),
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
                                   kwargs.get("id"), key=kwargs.get("key"))
        if action == "prompt_rm":
            return self.prompt_rm(kwargs.get("id", ""), key=kwargs.get("key"))
        if action == "prompt_import":
            return self.prompt_import(kwargs.get("cid", ""), key=kwargs.get("key"))
        if action == "memory":
            return {"memory": self.notes()}
        if action == "memory_add":
            return self.note_add(kwargs.get("name", ""), kwargs.get("content", ""),
                                 kwargs.get("tags"), kwargs.get("id"),
                                 key=kwargs.get("key"))
        if action == "memory_import":
            return self.note_import(kwargs.get("cid", ""), key=kwargs.get("key"))
        if action == "memory_rm":
            return self.note_rm(kwargs.get("id", ""), key=kwargs.get("key"))
        if action == "installed_skills":
            return {"skills": self.installed_skills()}
        if action == "skill_add":
            return self.skill_add(kwargs.get("name", ""), kwargs.get("body", ""),
                                  kwargs.get("description", ""), kwargs.get("tags"),
                                  kwargs.get("source", ""), kwargs.get("url", ""),
                                  kwargs.get("origin_id", ""), kwargs.get("license"),
                                  kwargs.get("id"), key=kwargs.get("key"))
        if action == "skill_import":
            return self.skill_import(kwargs.get("cid", ""), key=kwargs.get("key"))
        if action == "skill_rm":
            return self.skill_rm(kwargs.get("id", ""), key=kwargs.get("key"))
        if action == "conversations":
            return {"conversations": self.convs(kwargs.get("user", ""))}
        if action == "conversation_save":
            return self.conv_save(kwargs.get("user", ""), kwargs.get("id"),
                                  kwargs.get("query", ""), kwargs.get("agent_type", "default"),
                                  kwargs.get("status", "done"), kwargs.get("messages"),
                                  kwargs.get("started"))
        if action == "conversation_rm":
            return self.conv_rm(kwargs.get("user", ""), kwargs.get("id", ""))
        if action == "conversation_import":
            return self.conv_import(kwargs.get("user", ""), kwargs.get("cid", ""))
        return self.items(q=kwargs.get("q"), kind=kwargs.get("kind"),
                          tag=kwargs.get("tag"))

    def test(self) -> bool:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            lib = Library(dir=d)
            assert len(lib.prompts()) == len(SEED_PROMPTS)
            if lib._localfs() is not None:
                assert all(p.get("cid") for p in lib.prompts())
            p = lib.prompt_add("t", "text", tags=["x"])
            assert any(i["id"] == p["id"] for i in lib.prompts())
            if p.get("cid"):
                imported = lib.prompt_import(p["cid"])
                assert imported["name"] == "t" and imported["cid"] == p["cid"]
                lib.prompt_rm(imported["id"])
            lib.prompt_rm(p["id"])
            # ownership: unowned prompts belong to the host, who may remove them
            host = "0xaaa0000000000000000000000000000000000001"
            other = "0xbbb0000000000000000000000000000000000002"
            owned = Library(dir=d, identity=Identity(host=host))
            seeded = owned.prompts()[0]
            assert owned.prompt_owner(seeded) == {"owner": host, "owner_source": "host"}
            mine = owned.prompt_add("mine", "text", key=other)
            assert mine["owner"] == other and mine["owner_source"] == "item"
            for stranger in (None, other):
                try:
                    owned.prompt_rm(seeded["id"], key=stranger)
                    raise AssertionError("host-owned prompt removed by a stranger")
                except PermissionError:
                    pass
            owned.prompt_rm(mine["id"], key=other)      # own prompt
            owned.prompt_rm(seeded["id"], key=host)     # host removes anything
            # creating anything at all takes a sign-in once a host is known
            for make in (lambda: owned.prompt_add("anon", "text"),
                         lambda: owned.note_add("anon", "content"),
                         lambda: owned.skill_add("anon", "# body")):
                try:
                    make()
                    raise AssertionError("anonymous create allowed")
                except PermissionError:
                    pass
            # notes and installed skills own like prompts do
            note = owned.note_add("mine", "content", key=other)
            assert note["owner"] == other and note["owner_source"] == "item"
            skill = owned.skill_add("mine", "# body", origin_id="x:1", key=other)
            assert skill["owner"] == other
            for stranger in (None, "0xccc0000000000000000000000000000000000003"):
                for rm in (lambda: owned.note_rm(note["id"], key=stranger),
                           lambda: owned.skill_rm(skill["id"], key=stranger),
                           lambda: owned.note_add("hijack", "c", id=note["id"], key=stranger)):
                    try:
                        rm()
                        raise AssertionError("stranger managed someone else's item")
                    except PermissionError:
                        pass
            owned.note_rm(note["id"], key=other)        # own note
            owned.skill_rm(skill["id"], key=host)       # host removes anything
            n = lib.note_add("k", "v", tags=["y"])
            assert lib.notes()[0]["name"] == "k"
            out = lib.items(q="k", kind="memory")
            assert out["total"] == 1
            if n.get("cid"):
                imported = lib.note_import(n["cid"])
                assert imported["name"] == "k" and imported["cid"] == n["cid"]
                lib.note_rm(imported["id"])
            lib.note_rm(n["id"])
            assert lib.notes() == []
            # installed skills: upsert by origin, indexed as kind=skill
            s = lib.skill_add("pdf", "# PDF\ndo pdfs", "Handle PDFs",
                              tags=["docs"], source="github",
                              url="https://github.com/a/b/blob/HEAD/SKILL.md",
                              origin_id="gh:a/b")
            again = lib.skill_add("pdf v2", "# PDF\nv2", origin_id="gh:a/b")
            assert again["id"] == s["id"] and len(lib.installed_skills()) == 1
            assert again["installed"] == s["installed"]      # install date survives
            found = lib.items(q="pdf", kind="skill")["items"]
            assert any(i["id"] == s["id"] and i.get("external") for i in found)
            assert lib.skill_docs([s["id"]])[0]["body"] == "# PDF\nv2"
            if again.get("cid"):
                dup = lib.skill_import(again["cid"])
                assert dup["id"] == s["id"]                  # same url ⇒ upsert
            lib.skill_rm(s["id"])
            assert lib.installed_skills() == []
            # conversations: per-user scoping + localfs pin + import
            u = "0xabc0000000000000000000000000000000000001"
            c = lib.conv_save(u, query="hello", messages=[{"role": "user", "text": "hi"}])
            assert lib.convs(u)[0]["id"] == c["id"]
            assert lib.convs("0xother") == []
            if c.get("cid"):
                dup = lib.conv_import(u, c["cid"])
                assert dup["query"] == "hello" and dup["id"] != c["id"]
                lib.conv_rm(u, dup["id"])
            lib.conv_rm(u, c["id"])
            assert lib.convs(u) == []
        return True
