"""market — the operations, once.

Every surface this module has (REST, MCP, the console, the mod fn) calls the
methods on this class. There is no second implementation of "install a skill"
that could drift from the first, and no route that can do something MCP
cannot.

    search    scan the web
    get       one result, with the real document behind it
    install   put that document in the catalog
    installed the catalog
    doc       the markdown an agent is handed
    write     author one yourself
    remove    take it back out
    publish   hand the catalog to the fleet
"""
import time
from typing import Any, Dict, List, Optional

from . import skill as skilldoc
from .sources import SOURCES, SOURCE_IDS, Sources
from .store import Store


class Market:
    description = "A marketplace of agent skills, scraped off the open web"

    def __init__(self, dir: str = None, timeout: int = None):
        self.store = Store(dir)
        self.web = Sources(self.store, timeout=timeout) if timeout else Sources(self.store)

    # ── discovery ────────────────────────────────────────────────────

    def sources(self) -> Dict[str, Any]:
        return {"sources": [dict(s, ready=(s["id"] != "code" or bool(self.web.token())))
                            for s in SOURCES],
                "token": bool(self.web.token()),
                "note": "code search needs a GitHub token; every other source is anonymous"}

    def search(self, q: str = "", sources: List[str] = None, limit: int = 30,
               fresh: bool = False) -> Dict[str, Any]:
        if isinstance(sources, str):
            sources = [s.strip() for s in sources.split(",") if s.strip()]
        return self.web.search(q or "", sources, limit, fresh)

    def get(self, id: str, path: str = None, body: bool = True) -> Dict[str, Any]:
        """One skill, with its document — the preview before an install.

        Reads it out of the catalog when it is already installed and the id
        is a name, so opening a card never costs a network call twice.
        """
        ident = (id or "").strip()
        if not ident:
            raise ValueError("id required")
        if "/" not in ident and ":" not in ident and self.store.has(ident):
            rec = self.store.get(ident, body=body)
            rec["installed"] = True
            return rec
        doc = self.web.fetch(ident, path)
        doc["installed"] = self.store.has(doc["name"])
        doc["is_skill"] = skilldoc.looks_like_skill(doc.get("body", ""))
        if not body:
            doc.pop("body", None)
        return doc

    # ── installing ───────────────────────────────────────────────────

    def install(self, id: str = None, path: str = None, name: str = None,
                all: bool = False, who: str = "") -> Dict[str, Any]:
        """Fetch a document and file it in the catalog.

        `all=True` takes the whole repo — most community packs are one repo
        holding a dozen skills, and installing "the repo" should mean what it
        says. Nothing is executed at any point: the install writes markdown.
        """
        if not id:
            raise ValueError("id required — search first, then install by result id")
        docs = self.web.bundle(id) if all else [self.web.fetch(id, path)]
        out = []
        for doc in docs:
            if name and len(docs) == 1:
                doc["name"] = skilldoc.slug(name)
            rec = self.store.put(doc, skilldoc.to_markdown(doc), who=who)
            rec["chars"] = doc.get("chars")
            out.append(rec)
        return {"installed": [r["name"] for r in out], "count": len(out),
                "skills": out, "dir": str(self.store.catalog)}

    def write(self, name: str, body: str, description: str = "",
              tools: List[str] = None, tags: List[str] = None,
              who: str = "") -> Dict[str, Any]:
        """Author a skill here rather than finding one.

        The same parser reads it, so a hand-written skill and a scraped one
        are the same kind of thing the moment they are saved — including the
        front matter, which is filled in from the arguments when the body
        does not carry its own.
        """
        if not (body or "").strip():
            raise ValueError("body required — a skill is its instructions")
        doc = skilldoc.normalize(body, name=name, description=description,
                                 source="local", tags=tags, tools=tools)
        if name:
            doc["name"] = skilldoc.slug(name)
        rec = self.store.put(doc, skilldoc.to_markdown(doc), who=who)
        return {**rec, "wrote": rec["name"]}

    def installed(self, q: str = "", tag: str = None) -> Dict[str, Any]:
        items = self.store.items(q, tag)
        return {"total": len(items), "skills": items,
                "dir": str(self.store.catalog)}

    def doc(self, name: str) -> Dict[str, Any]:
        """The markdown an agent is handed — the whole point of the catalog."""
        ident = (name or "").strip()
        if self.store.has(ident):
            body = self.store.read(ident)
            meta = self.store.meta(ident)
            return {"name": ident, "markdown": body, "chars": len(body),
                    "tools": meta.get("tools") or [], "source": meta.get("source"),
                    "url": meta.get("url"), "installed": True}
        doc = self.get(ident)                   # not installed: fetch it live
        md = skilldoc.to_markdown(doc)
        return {"name": doc["name"], "markdown": md, "chars": len(md),
                "tools": doc.get("tools") or [], "source": doc.get("source"),
                "url": doc.get("url"), "installed": False}

    def remove(self, name: str) -> Dict[str, Any]:
        return self.store.rm(name)

    # ── serving the fleet ────────────────────────────────────────────

    def publish(self, names: List[str] = None, q: str = "") -> Dict[str, Any]:
        """The catalog as one payload — what an agent loads at the start of a run.

        Bodies are included, so this is the call that actually teaches: an
        agent asks once and holds every skill it is allowed to use. Names
        narrow it, because handing a model forty documents is worse than
        handing it none.
        """
        picked = [n for n in (names or []) if self.store.has(n)] or \
                 [i["name"] for i in self.store.items(q)]
        skills = []
        for n in picked:
            meta = self.store.meta(n)
            skills.append({"name": n,
                           "description": meta.get("description", ""),
                           "tools": meta.get("tools") or [],
                           "tags": meta.get("tags") or [],
                           "markdown": self.store.read(n)})
        return {"total": len(skills), "skills": skills,
                "chars": sum(len(s["markdown"]) for s in skills)}

    # ── housekeeping ─────────────────────────────────────────────────

    def token(self, token: str = None) -> Dict[str, Any]:
        if token is None:
            return {"token": bool(self.web.token())}
        return self.web.set_token(token)

    def clear_cache(self) -> Dict[str, Any]:
        return self.store.cache_clear()

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "module": "skills", **self.store.stats(),
                "sources": len(SOURCE_IDS), "code_search": bool(self.web.token()),
                "time": time.time()}
