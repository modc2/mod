"""store — the installed catalog, on disk, as files you can read.

    ~/.mod/skills/
        catalog/<name>/SKILL.md    the document itself
        catalog/<name>/meta.json   where it came from, when, who
        cache/*.json               search results, per-source TTL
        index.json                 every card ever scanned, by id
        github.token               optional, 0600, never in the module dir

A skill on disk is a SKILL.md and nothing else, so the catalog is portable:
copy a folder into ~/.claude/skills and Claude Code reads it, copy one out of
there and this reads it. Nothing here is executable and nothing here is run.

The seen-index exists because a card is not always re-findable: an
awesome-list entry has no API of its own, so the id remembers the record
instead of hoping a later query returns the same row.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

INDEX_MAX = 1200
CACHE_KEEP = 400


def _safe(name: str) -> str:
    """A catalog name that cannot escape the catalog."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-._")
    if not s or s in (".", ".."):
        raise ValueError(f"bad skill name: {name!r}")
    return s[:64]


class Store:
    def __init__(self, dir: str = None):
        self.dir = Path(dir or os.environ.get("SKILLS_DIR")
                        or (Path.home() / ".mod" / "skills"))
        self.catalog = self.dir / "catalog"
        self.cache_dir = self.dir / "cache"
        for d in (self.dir, self.catalog, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── the catalog ──────────────────────────────────────────────────

    def names(self) -> List[str]:
        return sorted(d.name for d in self.catalog.iterdir()
                      if d.is_dir() and (d / "SKILL.md").exists())

    def has(self, name: str) -> bool:
        return (self.catalog / _safe(name) / "SKILL.md").exists()

    def path(self, name: str) -> Path:
        return self.catalog / _safe(name) / "SKILL.md"

    def read(self, name: str) -> str:
        p = self.path(name)
        if not p.exists():
            raise KeyError(f"skill not installed: {name}")
        return p.read_text(encoding="utf-8", errors="replace")

    def meta(self, name: str) -> Dict[str, Any]:
        p = self.catalog / _safe(name) / "meta.json"
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}

    def get(self, name: str, body: bool = True) -> Dict[str, Any]:
        rec = dict(self.meta(name))
        rec["name"] = _safe(name)
        rec["installed"] = True
        rec["path"] = str(self.path(name))
        if body:
            rec["body"] = self.read(name)
        return rec

    def items(self, q: str = "", tag: str = None) -> List[Dict[str, Any]]:
        """Catalog cards — no bodies, because a list of twenty skills is a
        list of twenty documents and nobody wants that in one response."""
        out = []
        terms = [t for t in (q or "").lower().split() if t]
        for name in self.names():
            rec = self.get(name, body=False)
            hay = f"{name} {rec.get('title','')} {rec.get('description','')} " \
                  f"{' '.join(rec.get('tags') or [])}".lower()
            if terms and not all(t in hay for t in terms):
                continue
            if tag and tag.lower() not in [str(x).lower() for x in (rec.get("tags") or [])]:
                continue
            out.append(rec)
        return out

    def put(self, rec: Dict[str, Any], markdown: str, who: str = "") -> Dict[str, Any]:
        """Install (or update) one skill. Returns the catalog record."""
        name = _safe(rec.get("name") or "skill")
        folder = self.catalog / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(markdown, encoding="utf-8")
        meta = {k: v for k, v in rec.items() if k != "body"}
        prior = self.meta(name)
        meta["name"] = name
        meta["installed_at"] = prior.get("installed_at") or time.time()
        meta["updated_at"] = time.time()
        meta["by"] = who or prior.get("by") or ""
        meta["updates"] = int(prior.get("updates") or 0) + (1 if prior else 0)
        (folder / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        return {**meta, "path": str(folder / "SKILL.md"), "installed": True}

    def rm(self, name: str) -> Dict[str, Any]:
        folder = self.catalog / _safe(name)
        if not folder.exists():
            raise KeyError(f"skill not installed: {name}")
        shutil.rmtree(folder)
        return {"removed": _safe(name)}

    # ── search cache ─────────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        import hashlib
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"

    def cache_get(self, key: str, ttl: int) -> Any:
        p = self._cache_path(key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text())
        except Exception:
            return None
        return blob.get("v") if time.time() - blob.get("t", 0) <= ttl else None

    def cache_put(self, key: str, value: Any) -> None:
        try:
            self._cache_path(key).write_text(
                json.dumps({"t": time.time(), "key": key, "v": value}, default=str))
        except Exception:
            return
        files = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in files[:-CACHE_KEEP]:
            f.unlink(missing_ok=True)

    def cache_clear(self) -> Dict[str, int]:
        n = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)
            n += 1
        return {"cleared": n}

    # ── the seen index ───────────────────────────────────────────────

    def _index_path(self) -> Path:
        return self.dir / "index.json"

    def _index(self) -> Dict[str, Any]:
        try:
            return json.loads(self._index_path().read_text())
        except Exception:
            return {}

    def remember(self, items: List[Dict[str, Any]]) -> None:
        idx = self._index()
        for it in items:
            if it.get("id"):
                idx[it["id"]] = {k: v for k, v in it.items() if k != "body"}
        if len(idx) > INDEX_MAX:
            idx = dict(list(idx.items())[-INDEX_MAX:])
        try:
            self._index_path().write_text(json.dumps(idx, default=str))
        except Exception:
            pass

    def recall(self, id: str) -> Optional[Dict[str, Any]]:
        return self._index().get(id)

    def stats(self) -> Dict[str, Any]:
        return {"installed": len(self.names()), "dir": str(self.dir),
                "cached_searches": len(list(self.cache_dir.glob("*.json"))),
                "seen": len(self._index())}
