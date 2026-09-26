"""sources — where skills are found on the open web.

Six adapters, one shape. Each takes a query and returns cards; the shape is
identical whether the card came from Anthropic's own catalog or a line in
somebody's awesome-list, so one search reads as one list.

    anthropic  the official anthropics/skills catalog
    github     repo search, scoped to names and descriptions
    topics     repos that self-declare claude-skill / agent-skill
    code       GitHub code search for SKILL.md — the widest net (needs a token)
    awesome    curated community indexes, parsed out of their READMEs
    registry   skills published by other mods on this host, via the fleet

Only `fetch` pulls the actual document, and only when something is opened or
installed: a search that fetched every hit would spend the whole GitHub budget
on cards nobody read.

A GitHub token (GITHUB_TOKEN / GH_TOKEN, or ~/.mod/skills/github.token) lifts
the 10 searches/min anonymous cap and is the only way to reach code search. It
is private state and lives off-tree, never in the module directory.
"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import skill as skilldoc
from .store import Store

UA = "mod-skills/1.0 (+https://github.com/mod)"
TIMEOUT = int(os.environ.get("SKILLS_TIMEOUT", "14"))

SOURCES: List[Dict[str, Any]] = [
    {"id": "anthropic", "label": "Anthropic", "ttl": 21600, "weight": 3.0,
     "about": "The official anthropics/skills catalog"},
    {"id": "topics", "label": "GitHub Topics", "ttl": 900, "weight": 1.8,
     "about": "Repos tagged claude-skill / agent-skill / claude-code-skill"},
    {"id": "code", "label": "GitHub Code", "ttl": 900, "weight": 2.2,
     "about": "Code search for SKILL.md files — the widest net, needs a token"},
    {"id": "github", "label": "GitHub", "ttl": 900, "weight": 1.2,
     "about": "Repo search over names and descriptions"},
    {"id": "awesome", "label": "Awesome Lists", "ttl": 21600, "weight": 1.5,
     "about": "Curated community indexes of agent skills"},
    {"id": "registry", "label": "Fleet", "ttl": 300, "weight": 2.0,
     "about": "skill.md documents published by modules on this host"},
]
SOURCE_IDS = [s["id"] for s in SOURCES]
WEIGHT = {s["id"]: s["weight"] for s in SOURCES}

SKILL_TOPICS = ["claude-skill", "claude-skills", "agent-skill",
                "claude-code-skill", "agent-skills"]

AWESOME_LISTS = [
    ("https://raw.githubusercontent.com/ComposioHQ/awesome-claude-skills/master/README.md",
     "awesome-claude-skills"),
    ("https://raw.githubusercontent.com/anthropics/skills/main/README.md",
     "anthropics/skills"),
]

MD_LINK = re.compile(r"^\s*[-*]\s*\[([^\]]{1,120})\]\((https?://[^)\s]+)\)\s*(.*)$")
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_INLINE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

SKILL_FILES = ["SKILL.md", "skill.md", "Skill.md"]


class SourceError(RuntimeError):
    """A source failed. One dead source never fails a scan."""


def _clean(text: str) -> str:
    text = MD_IMAGE.sub("", text or "")
    text = MD_INLINE.sub(r"\1", text)
    text = re.sub(r"^[\s\-–—:|]+", "", text)
    return re.sub(r"\s{2,}", " ", re.sub(r"[*`]", "", text)).strip()


def repo_key(url: str) -> str:
    """Canonical key so the same repo from three sources collapses to one card."""
    u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower().rstrip("/"))
    u = re.sub(r"\.git$", "", u)
    m = re.match(r"github\.com/([^/]+)/([^/#?]+)", u)
    return f"github.com/{m.group(1)}/{m.group(2)}" if m else u


class Sources:
    """The web, as six adapters and a disk cache."""

    def __init__(self, store: Store = None, timeout: int = TIMEOUT):
        self.store = store or Store()
        self.timeout = timeout

    # ── github auth (optional, off-tree) ─────────────────────────────

    def token(self) -> Optional[str]:
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if tok:
            return tok.strip()
        p = self.store.dir / "github.token"
        try:
            return p.read_text().strip() or None if p.exists() else None
        except Exception:
            return None

    def set_token(self, token: str) -> Dict[str, Any]:
        p = self.store.dir / "github.token"
        if not (token or "").strip():
            p.unlink(missing_ok=True)
            return {"token": False}
        p.write_text(token.strip())
        try:
            p.chmod(0o600)
        except Exception:
            pass
        return {"token": True}

    # ── http ─────────────────────────────────────────────────────────

    def _open(self, url: str, gh: bool = False, accept: str = None) -> str:
        headers = {"User-Agent": UA,
                   "Accept": accept or ("application/vnd.github+json" if gh
                                        else "application/json")}
        if gh:
            tok = self.token()
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code in (403, 429) and "rate limit" in detail.lower():
                raise SourceError("github rate limit — set a token for more headroom "
                                  "(POST /token, or GITHUB_TOKEN)")
            if e.code == 422 and "code" in url:
                raise SourceError("github code search rejected the query")
            raise SourceError(f"{e.code} {e.reason} — {url.split('?')[0]}")
        except Exception as e:
            raise SourceError(f"{type(e).__name__}: {e}")

    def _json(self, url: str, gh: bool = False, params: Dict = None) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            return json.loads(self._open(url, gh=gh))
        except SourceError:
            raise
        except Exception as e:
            raise SourceError(f"bad json from {url.split('?')[0]}: {e}")

    def _text(self, url: str) -> str:
        return self._open(url, accept="text/plain, text/markdown, */*")

    # ── cards ────────────────────────────────────────────────────────

    def _card(self, **kw) -> Dict[str, Any]:
        card = {"id": "", "source": "", "name": "", "title": "", "description": "",
                "url": "", "repo": "", "path": "", "author": "", "stars": None,
                "license": None, "tags": [], "updated": None, "installable": True}
        card.update(kw)
        return card

    def _repo_card(self, repo: Dict, path: str = None, source: str = "github") -> Dict:
        full = repo.get("full_name") or ""
        return self._card(
            id=f"gh:{full}" + (f":{path}" if path else ""),
            source=source,
            name=skilldoc.slug(path.rsplit("/", 1)[-1] if path else repo.get("name", full)),
            title=full + (f"/{path}" if path else ""),
            description=repo.get("description") or "",
            url=(f"https://github.com/{full}/tree/HEAD/{path}" if path
                 else repo.get("html_url") or f"https://github.com/{full}"),
            repo=f"https://github.com/{full}", path=path or "",
            author=(repo.get("owner") or {}).get("login", ""),
            stars=repo.get("stargazers_count"),
            license=((repo.get("license") or {}) or {}).get("spdx_id"),
            tags=(repo.get("topics") or [])[:8],
            updated=repo.get("pushed_at") or repo.get("updated_at"))

    # ── adapters ─────────────────────────────────────────────────────

    def _gh_repos(self, query: str, limit: int, source: str) -> List[Dict]:
        data = self._json("https://api.github.com/search/repositories", gh=True,
                          params={"q": query, "sort": "stars", "order": "desc",
                                  "per_page": min(max(limit, 5), 30)})
        return [self._repo_card(r, source=source) for r in data.get("items", [])]

    def src_github(self, q: str, limit: int) -> List[Dict]:
        """Repo search, scoped to name+description.

        `in:readme` was tried and dropped: it returns every awesome-list and
        monorepo that merely says the word "skill" once.
        """
        terms = (q or "").strip()
        query = (f"{terms} skill in:name,description fork:false" if terms
                 else "claude skill in:name,description fork:false")
        return self._gh_repos(query, limit, "github")

    def src_topics(self, q: str, limit: int) -> List[Dict]:
        """Repos that tagged themselves. High precision, and cheap.

        GitHub cannot OR topic qualifiers against free terms, so topics are
        tried in order and the first with hits wins — two calls at worst
        against the 10 searches/min anonymous budget.
        """
        terms = (q or "").strip()
        for topic in (SKILL_TOPICS[:3] if terms else SKILL_TOPICS[:2]):
            hits = self._gh_repos(f"topic:{topic} {terms}".strip(), limit, "topics")
            if hits:
                return hits
        return []

    def src_code(self, q: str, limit: int) -> List[Dict]:
        """Every SKILL.md on GitHub that matches the query.

        This is the source that actually finds skills rather than repos about
        skills — the file is the artifact, so search for the file. It is also
        the one GitHub refuses without a token, so it says so rather than
        returning nothing.
        """
        if not self.token():
            raise SourceError("github code search needs a token — POST /token "
                              "with a GitHub PAT (public_repo scope is enough)")
        terms = (q or "").strip()
        query = f"{terms} filename:SKILL.md" if terms else "filename:SKILL.md path:skills"
        data = self._json("https://api.github.com/search/code", gh=True,
                          params={"q": query, "per_page": min(max(limit, 5), 30)})
        out = []
        for hit in data.get("items", []):
            repo = hit.get("repository") or {}
            path = hit.get("path") or "SKILL.md"
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            card = self._repo_card(repo, path=path, source="code")
            card["name"] = skilldoc.slug(folder.rsplit("/", 1)[-1] if folder
                                         else repo.get("name", "skill"))
            card["title"] = f"{repo.get('full_name', '')} · {path}"
            card["description"] = card["description"] or f"SKILL.md at {path}"
            out.append(card)
        return out

    def src_anthropic(self, q: str, limit: int) -> List[Dict]:
        """The official catalog — one card per skills/<name>/SKILL.md.

        Listed rather than searched: the catalog is small enough to hold whole,
        so a query filters it locally and never spends a search call.
        """
        repo = self._json("https://api.github.com/repos/anthropics/skills", gh=True)
        out: List[Dict] = []
        for top in ("skills", "document-skills", "artifacts-builder"):
            try:
                listing = self._json(
                    f"https://api.github.com/repos/anthropics/skills/contents/{top}", gh=True)
            except SourceError:
                continue
            for entry in listing if isinstance(listing, list) else []:
                if entry.get("type") != "dir":
                    continue
                name = entry["name"]
                card = self._repo_card(repo, path=f"{top}/{name}", source="anthropic")
                card["name"] = skilldoc.slug(name)
                card["title"] = f"anthropics/skills · {name}"
                card["description"] = f"Official Anthropic skill: {name.replace('-', ' ')}"
                card["url"] = f"https://github.com/anthropics/skills/tree/main/{top}/{name}"
                card["tags"] = ["official", "anthropic"]
                out.append(card)
            if out:
                break
        return self._filter(out, q)[:max(limit, 10)]

    def _awesome_list(self, url: str, label: str) -> List[Dict]:
        cached = self.store.cache_get(f"list:{url}", 21600)
        if cached is not None:
            return cached
        text = self._text(url)
        items, seen = [], set()
        for line in text.splitlines():
            m = MD_LINK.match(line)
            if not m:
                continue
            name, link, rest = m.group(1), m.group(2), m.group(3)
            if "github.com" not in link:
                continue
            key = repo_key(link)
            if not key or key in seen:
                continue
            seen.add(key)
            gh = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)(?:/tree/[^/]+/(.+))?",
                          link.rstrip("/"))
            path = gh.group(3) if gh and gh.group(3) else ""
            ident = (f"gh:{gh.group(1)}/{gh.group(2)}" + (f":{path}" if path else "")
                     if gh else f"url:{hashlib.sha1(link.encode()).hexdigest()[:12]}")
            items.append(self._card(
                id=ident, source="awesome", name=skilldoc.slug(_clean(name)),
                title=_clean(name), description=_clean(rest)[:400], url=link,
                repo=link.split("/tree/")[0], path=path,
                author=gh.group(1) if gh else "", tags=[label],
                installable=bool(gh)))
            if len(items) >= 3000:
                break
        self.store.cache_put(f"list:{url}", items)
        return items

    def src_awesome(self, q: str, limit: int) -> List[Dict]:
        out, errs = [], []
        with ThreadPoolExecutor(max_workers=len(AWESOME_LISTS)) as pool:
            futures = [pool.submit(self._awesome_list, u, l) for u, l in AWESOME_LISTS]
            for f in futures:
                try:
                    out.extend(f.result(timeout=self.timeout + 8))
                except Exception as e:
                    errs.append(str(e))
        if errs and not out:
            raise SourceError("; ".join(errs[:2]))
        return self._filter(out, q)[:max(limit * 3, 60)]

    def src_registry(self, q: str, limit: int) -> List[Dict]:
        """Skills already on this host: every module's own skill.md.

        The fleet documents itself in exactly this format, so the modules on
        this box are a skill catalog that needs no network at all — and it is
        the one catalog where installing a skill teaches the agent something
        it can actually call.
        """
        root = os.environ.get("MOD_ROOT", "/root/mod/mod")
        out: List[Dict] = []
        for base in ("orbit", "core"):
            d = os.path.join(root, base)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name, "skill.md")
                if not os.path.isfile(path):
                    continue
                try:
                    head = open(path, encoding="utf-8", errors="replace").read(4000)
                except Exception:
                    continue
                fm = skilldoc.parse_frontmatter(head)
                out.append(self._card(
                    id=f"mod:{base}/{name}", source="registry",
                    name=skilldoc.slug(name),
                    title=f"{base}/{name}",
                    description=str(fm.get("description") or
                                    skilldoc.summarize(head) or
                                    f"the {name} module's own skill document")[:400],
                    url=f"file://{path}", repo=f"{base}/{name}", path="skill.md",
                    author=base, tags=["fleet", base]))
        return self._filter(out, q)[:max(limit * 2, 40)]

    ADAPTERS = {"anthropic": "src_anthropic", "github": "src_github",
                "topics": "src_topics", "code": "src_code",
                "awesome": "src_awesome", "registry": "src_registry"}

    # ── search ───────────────────────────────────────────────────────

    @staticmethod
    def _filter(items: List[Dict], q: str) -> List[Dict]:
        terms = [t for t in (q or "").lower().split() if t]
        if not terms:
            return items
        return [i for i in items
                if all(t in f"{i.get('name','')} {i.get('title','')} "
                            f"{i.get('description','')} {' '.join(i.get('tags') or [])}".lower()
                       for t in terms)]

    @staticmethod
    def score(item: Dict, q: str) -> float:
        """Source trust × relevance × a nod to stars.

        Relevance is term hits in the name (worth more) and the description;
        stars are logarithmic and capped, so a 40k-star framework that merely
        mentions the query cannot outrank the skill that answers it.
        """
        base = WEIGHT.get(item.get("source"), 1.0)
        name = f"{item.get('name','')} {item.get('title','')}".lower()
        desc = (item.get("description") or "").lower()
        rel = 1.0
        for term in [t for t in (q or "").lower().split() if t]:
            if term in name:
                rel += 1.5
            if term in desc:
                rel += 0.5
        stars = item.get("stars") or 0
        pop = 1.0 + min((stars ** 0.5) / 60.0, 0.8)
        return round(base * rel * pop, 4)

    def search(self, q: str = "", sources: List[str] = None, limit: int = 30,
               fresh: bool = False) -> Dict[str, Any]:
        """One query, every source at once, merged and ranked.

        Duplicates across sources collapse onto one card that remembers all of
        them (`also`), because the same skill appearing in the official catalog
        AND an awesome-list is evidence, not noise.
        """
        want = [s for s in (sources or SOURCE_IDS) if s in self.ADAPTERS]
        if not want:
            want = list(SOURCE_IDS)
        limit = max(1, min(int(limit or 30), 100))
        results: Dict[str, List[Dict]] = {}
        errors: Dict[str, str] = {}

        def run(sid: str):
            ttl = next((s["ttl"] for s in SOURCES if s["id"] == sid), 900)
            key = f"search:{sid}:{q}:{limit}"
            if not fresh:
                hit = self.store.cache_get(key, ttl)
                if hit is not None:
                    return sid, hit, None
            try:
                items = getattr(self, self.ADAPTERS[sid])(q, limit)
                self.store.cache_put(key, items)
                return sid, items, None
            except Exception as e:
                return sid, [], str(e)

        with ThreadPoolExecutor(max_workers=len(want)) as pool:
            for sid, items, err in pool.map(run, want):
                results[sid] = items
                if err:
                    errors[sid] = err

        merged: Dict[str, Dict] = {}
        for sid in want:
            for item in results.get(sid, []):
                key = item.get("id") or repo_key(item.get("url", ""))
                if key in merged:
                    prev = merged[key]
                    prev.setdefault("also", [])
                    if item["source"] not in prev["also"] and item["source"] != prev["source"]:
                        prev["also"].append(item["source"])
                    # keep the richer description
                    if len(item.get("description") or "") > len(prev.get("description") or ""):
                        prev["description"] = item["description"]
                    prev["score"] = round(prev.get("score", 0) + 0.4, 4)
                    continue
                item = dict(item)
                item["score"] = self.score(item, q)
                merged[key] = item

        ranked = sorted(merged.values(), key=lambda i: -i.get("score", 0))[:limit]
        installed = set(self.store.names())
        for item in ranked:
            item["installed"] = item.get("name") in installed
        self.store.remember(ranked)
        return {
            "query": q, "total": len(ranked), "scanned": len(merged),
            "sources": {sid: len(results.get(sid, [])) for sid in want},
            "errors": errors, "results": ranked,
            "token": bool(self.token()),
        }

    # ── fetching the document itself ─────────────────────────────────

    def _raw(self, owner: str, repo: str, path: str) -> Optional[str]:
        for branch in ("HEAD", "main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            try:
                return self._text(url)
            except SourceError:
                continue
        return None

    def _contents(self, owner: str, repo: str, path: str = "") -> List[Dict]:
        try:
            data = self._json(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}".rstrip("/"),
                gh=True)
            return data if isinstance(data, list) else [data]
        except SourceError:
            return []

    def _find_skill_file(self, owner: str, repo: str, path: str = "") -> Optional[str]:
        """Where the SKILL.md actually is, given a repo or a folder in one.

        Four shapes are common in the wild and all four are tried: the path IS
        the file; the path holds one; the repo root holds one; the repo keeps
        them under skills/ or .claude/skills/.
        """
        path = (path or "").strip("/")
        if path.lower().endswith(".md"):
            return path
        for base in [path] if path else []:
            for f in SKILL_FILES:
                if self._raw(owner, repo, f"{base}/{f}") is not None:
                    return f"{base}/{f}"
        for f in SKILL_FILES:
            if not path and self._raw(owner, repo, f) is not None:
                return f
        if not path:
            for folder in ("skills", ".claude/skills"):
                for entry in self._contents(owner, repo, folder):
                    if entry.get("type") == "dir":
                        for f in SKILL_FILES:
                            cand = f"{folder}/{entry['name']}/{f}"
                            if self._raw(owner, repo, cand) is not None:
                                return cand
            if self._raw(owner, repo, "README.md") is not None:
                return "README.md"
        return None

    def fetch(self, id: str, path: str = None) -> Dict[str, Any]:
        """Pull the real document behind a card id.

        ids are stable and readable so this works without the search that
        produced them:
            gh:owner/repo[:path]   a GitHub repo, or a file/folder inside it
            mod:orbit/dag          a module on this host
            url:<sha>              a card with no repo — needs the remembered
                                   record, hence the seen-index
        """
        ident = (id or "").strip()
        if not ident:
            raise ValueError("id required")
        remembered = self.store.recall(ident) or {}
        if ident.startswith("mod:"):
            rel = ident[4:]
            root = os.environ.get("MOD_ROOT", "/root/mod/mod")
            full = os.path.realpath(os.path.join(root, rel, "skill.md"))
            if not full.startswith(os.path.realpath(root)) or not os.path.isfile(full):
                raise ValueError(f"no skill.md for module {rel}")
            body = open(full, encoding="utf-8", errors="replace").read()
            return skilldoc.normalize(
                body, name=rel.split("/")[-1], source="registry",
                url=f"file://{full}", origin_id=ident,
                description=remembered.get("description", ""),
                tags=["fleet"])
        if ident.startswith("gh:"):
            rest = ident[3:]
            repo_part, _, in_path = rest.partition(":")
            owner, _, repo = repo_part.partition("/")
            if not owner or not repo:
                raise ValueError(f"malformed id: {id}")
            target = self._find_skill_file(owner, repo, path or in_path)
            if not target:
                raise ValueError(f"no SKILL.md found in {owner}/{repo}"
                                 + (f" at {path or in_path}" if (path or in_path) else ""))
            body = self._raw(owner, repo, target)
            if body is None:
                raise ValueError(f"could not read {target} from {owner}/{repo}")
            return skilldoc.normalize(
                body,
                name=remembered.get("name") or target.rsplit("/", 2)[-2]
                     if "/" in target else repo,
                description=remembered.get("description", ""),
                source=remembered.get("source") or "github",
                url=f"https://github.com/{owner}/{repo}/blob/HEAD/{target}",
                origin_id=ident, license=remembered.get("license") or "",
                tags=remembered.get("tags") or [])
        if ident.startswith(("http://", "https://")):
            body = self._text(ident)
            return skilldoc.normalize(body, source="url", url=ident, origin_id=ident)
        if remembered.get("url"):
            return self.fetch(remembered["url"])
        raise ValueError(f"unknown id: {id} — expected gh:owner/repo, mod:base/name or a URL")

    def bundle(self, id: str) -> List[Dict[str, Any]]:
        """Every skill in a repo, not just the first — for a skill pack.

        anthropics/skills is one repo with a dozen skills in it; so are most
        of the community packs. Installing "the repo" should mean installing
        what is in it.
        """
        ident = (id or "").strip()
        if not ident.startswith("gh:"):
            return [self.fetch(ident)]
        repo_part = ident[3:].split(":")[0]
        owner, _, repo = repo_part.partition("/")
        found: List[Dict[str, Any]] = []
        for folder in ("skills", ".claude/skills", "document-skills"):
            for entry in self._contents(owner, repo, folder):
                if entry.get("type") != "dir":
                    continue
                for f in SKILL_FILES:
                    body = self._raw(owner, repo, f"{folder}/{entry['name']}/{f}")
                    if body is None:
                        continue
                    found.append(skilldoc.normalize(
                        body, name=entry["name"], source="github",
                        url=f"https://github.com/{owner}/{repo}/tree/HEAD/{folder}/{entry['name']}",
                        origin_id=f"gh:{owner}/{repo}:{folder}/{entry['name']}"))
                    break
            if found:
                break
        return found or [self.fetch(ident)]
