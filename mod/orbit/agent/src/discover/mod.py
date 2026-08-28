"""
discover - internet-wide tool aggregator

Scans public registries for things an agent can learn: Claude/agent SKILL.md
documents, MCP servers, and agent packages. Every source is a small adapter that
normalizes its results into one shape, so the console can search "pdf" once
and see hits from GitHub, npm, the MCP registry, Glama, and curated
awesome-lists side by side.

Sources (all public, no auth required):
    github     GitHub repo search for skill repos
    topics     GitHub repos carrying skill topics (claude-skill, agent-skill…)
    anthropic  the official anthropics/skills catalog
    awesome    curated awesome-list READMEs, parsed into entries
    npm        npm registry search (mcp servers, skill installers)
    mcp        the official Model Context Protocol registry
    glama      the Glama MCP server directory

A GitHub token (env GITHUB_TOKEN / GH_TOKEN, or ~/.mod/agent/discover/github.token)
is optional; it lifts GitHub's 10 searches/min + 60 core calls/hr anonymous
budget and is the only way to reach code search. Tokens are private state and
live off-tree, never in the module directory.

Everything is cached to disk (per-source TTL) because the anonymous GitHub
budget is small and a scan fans out to every source at once. One dead registry
never fails a scan — sources report their own errors alongside partial results.

Usage:
    d = Discover()
    d.search("pdf")                        # scan every source
    d.search("postgres", sources=["mcp"])  # one platform
    d.detail("gh:anthropics/skills:skills/pdf")
    d.tool_doc("gh:anthropics/skills:skills/pdf")    # installable markdown
"""
import base64
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

UA = "mod-agent-discover/1.0 (+https://github.com/mod)"

# ── source registry ─────────────────────────────────────────────────
# ttl: seconds a scan result stays fresh on disk. Slow-moving catalogs
# (the official skill repo, awesome-lists) cache far longer than search.

SOURCES: List[Dict[str, Any]] = [
    {"id": "github", "label": "GitHub", "kind": "tool", "ttl": 900,
     "about": "Repo search for skill repositories"},
    {"id": "topics", "label": "GitHub Topics", "kind": "tool", "ttl": 900,
     "about": "Repos tagged claude-skill / agent-skill / claude-code-skill"},
    {"id": "anthropic", "label": "Anthropic", "kind": "tool", "ttl": 21600,
     "about": "The official anthropics/skills catalog"},
    {"id": "awesome", "label": "Awesome Lists", "kind": "tool", "ttl": 21600,
     "about": "Curated community indexes of skills and MCP servers"},
    {"id": "npm", "label": "npm", "kind": "package", "ttl": 1800,
     "about": "npm packages: MCP servers and skill installers"},
    {"id": "mcp", "label": "MCP Registry", "kind": "mcp", "ttl": 1800,
     "about": "The official Model Context Protocol registry"},
    {"id": "glama", "label": "Glama", "kind": "mcp", "ttl": 1800,
     "about": "The Glama MCP server directory"},
]

SOURCE_IDS = [s["id"] for s in SOURCES]

# curated awesome-lists: (raw markdown url, kind, label)
AWESOME_LISTS = [
    ("https://raw.githubusercontent.com/ComposioHQ/awesome-claude-skills/master/README.md",
     "tool", "awesome-claude-skills"),
    ("https://raw.githubusercontent.com/wong2/awesome-mcp-servers/main/README.md",
     "mcp", "awesome-mcp-servers"),
    ("https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
     "mcp", "awesome-mcp-servers (punkpeye)"),
]

# GitHub repo topics that mark a repo as an agent skill
SKILL_TOPICS = ["claude-skill", "claude-skills", "agent-skill", "agent-skills",
                "claude-code-skill"]

# how much a source's results are trusted when ranking, before relevance
SOURCE_WEIGHT = {"anthropic": 3.0, "awesome": 1.6, "github": 1.2, "topics": 1.4,
                 "mcp": 1.2, "glama": 1.0, "npm": 0.8}

MD_LINK = re.compile(r"^\s*[-*]\s*\[([^\]]{1,120})\]\((https?://[^)\s]+)\)\s*(.*)$")
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def _norm_repo(url: str) -> str:
    """Canonical key for a repo URL so the same project from three sources
    collapses to one card."""
    if not url:
        return ""
    u = url.strip().lower().rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"\.git$", "", u)
    m = re.match(r"github\.com/([^/]+)/([^/#?]+)", u)
    return f"github.com/{m.group(1)}/{m.group(2)}" if m else u


def _clean_md(text: str) -> str:
    """Strip badges/images/links out of an awesome-list description."""
    text = MD_IMAGE.sub("", text or "")
    text = MD_INLINE_LINK.sub(r"\1", text)
    text = re.sub(r"^[\s\-–—:|]+", "", text)
    text = re.sub(r"[*`]", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_frontmatter(md: str) -> Dict[str, Any]:
    """Pull name/description out of a SKILL.md YAML frontmatter block.

    Deliberately a tiny scalar-only parser — skill frontmatter is flat, and
    this avoids a yaml dependency (and yaml's surprises) on untrusted input.
    """
    m = FRONTMATTER.match(md or "")
    if not m:
        return {}
    out: Dict[str, Any] = {}
    key = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+-\s+", line) and key:            # list item
            out.setdefault(key, [])
            if isinstance(out[key], list):
                out[key].append(line.split("-", 1)[1].strip().strip("'\""))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip().strip("'\"")
        out[key] = v if v else []
    return out


class Discover:
    description = "Scan public registries for agent tool docs, MCP servers, and packages"

    def __init__(self, dir: str = None, timeout: int = 12):
        self.dir = Path(dir) if dir else Path.home() / '.mod' / 'agent' / 'discover'
        self.cache_dir = self.dir / 'cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    # ── github auth (optional, off-tree) ─────────────────────────────

    def token(self) -> Optional[str]:
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if tok:
            return tok.strip()
        p = self.dir / 'github.token'
        if p.exists():
            try:
                return p.read_text().strip() or None
            except Exception:
                return None
        return None

    def set_token(self, token: str) -> Dict:
        """Persist a GitHub token off-tree (or clear it with an empty string)."""
        p = self.dir / 'github.token'
        if not (token or "").strip():
            p.unlink(missing_ok=True)
            return {"token": False}
        p.write_text(token.strip())
        try:
            p.chmod(0o600)
        except Exception:
            pass
        return {"token": True}

    def _headers(self, gh: bool = False) -> Dict[str, str]:
        h = {"User-Agent": UA, "Accept": "application/json"}
        if gh:
            h["Accept"] = "application/vnd.github+json"
            tok = self.token()
            if tok:
                h["Authorization"] = f"Bearer {tok}"
        return h

    def _get(self, url: str, gh: bool = False, params: Dict = None,
             timeout: int = None) -> Any:
        r = requests.get(url, headers=self._headers(gh), params=params,
                         timeout=timeout or self.timeout)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            raise RuntimeError("github rate limit — add a token for more headroom")
        r.raise_for_status()
        return r.json()

    # ── disk cache ───────────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"

    def cache_get(self, key: str, ttl: int) -> Any:
        p = self._cache_path(key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text())
        except Exception:
            return None
        if time.time() - blob.get("t", 0) > ttl:
            return None
        return blob.get("v")

    def cache_put(self, key: str, value: Any):
        try:
            self._cache_path(key).write_text(
                json.dumps({"t": time.time(), "key": key, "v": value}, default=str))
        except Exception:
            return
        self._prune()

    def _prune(self, keep: int = 300):
        files = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            f.unlink(missing_ok=True)

    def clear_cache(self) -> Dict:
        n = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)
            n += 1
        return {"cleared": n}

    # ── seen-item index ──────────────────────────────────────────────
    # Registry results (MCP, Glama) have no fetchable page of their own, so
    # a later detail/install would have to guess a query that finds them
    # again. Instead every scanned result is remembered by id.

    INDEX_MAX = 600

    def _index_path(self) -> Path:
        return self.dir / 'index.json'

    def _remember(self, items: List[Dict]):
        try:
            idx = json.loads(self._index_path().read_text())
        except Exception:
            idx = {}
        for it in items:
            if it.get("id"):
                idx[it["id"]] = it
        if len(idx) > self.INDEX_MAX:                  # drop the oldest keys
            idx = dict(list(idx.items())[-self.INDEX_MAX:])
        try:
            self._index_path().write_text(json.dumps(idx, default=str))
        except Exception:
            pass

    def recall(self, id: str) -> Optional[Dict]:
        """The record for a previously scanned id, if it's still indexed."""
        try:
            return json.loads(self._index_path().read_text()).get(id)
        except Exception:
            return None

    # ── source adapters ──────────────────────────────────────────────
    # Each returns a list of normalized items:
    #   {id, source, kind, name, title, description, url, repo, author,
    #    stars, license, tags, updated, installable, install}

    def _gh_item(self, repo: Dict, path: str = None, kind: str = "tool",
                 source: str = "github") -> Dict:
        full = repo.get("full_name") or ""
        return {
            "id": f"gh:{full}" + (f":{path}" if path else ""),
            "source": source, "kind": kind,
            "name": (path.rsplit("/", 1)[-1] if path else repo.get("name", full)),
            "title": full + (f"/{path}" if path else ""),
            "description": repo.get("description") or "",
            "url": repo.get("html_url") or f"https://github.com/{full}",
            "repo": f"https://github.com/{full}",
            "author": (repo.get("owner") or {}).get("login", ""),
            "stars": repo.get("stargazers_count"),
            "license": ((repo.get("license") or {}) or {}).get("spdx_id"),
            "tags": (repo.get("topics") or [])[:8],
            "updated": repo.get("pushed_at") or repo.get("updated_at"),
            "installable": True,
        }

    def _gh_search(self, query: str, limit: int, source: str) -> List[Dict]:
        data = self._get("https://api.github.com/search/repositories", gh=True,
                         params={"q": query, "sort": "stars", "order": "desc",
                                 "per_page": min(limit, 30)})
        return [self._gh_item(r, source=source) for r in data.get("items", [])]

    def src_github(self, q: str, limit: int) -> List[Dict]:
        """GitHub repo search, biased toward skill repositories.

        Scoped to name+description on purpose: `in:readme` drags in every
        awesome-list and mega-repo that merely mentions the word.
        """
        terms = (q or "").strip()
        query = f"{terms} skill in:name,description fork:false" if terms \
            else "claude skill in:name,description fork:false"
        return self._gh_search(query, limit, "github")

    def src_topics(self, q: str, limit: int) -> List[Dict]:
        """Repos that self-declare a skill topic — high precision, low noise.

        GitHub can't OR topic qualifiers against free terms, so topics are
        tried in order and the first one with hits wins (2 calls at worst,
        which matters against the 10 searches/min anonymous budget).
        """
        terms = (q or "").strip()
        for topic in SKILL_TOPICS[:2] if terms else SKILL_TOPICS[:1]:
            items = self._gh_search(f"topic:{topic} {terms}".strip(), limit, "topics")
            if items:
                return items
        return []

    def src_anthropic(self, q: str, limit: int) -> List[Dict]:
        """The official skill catalog — one card per skills/<name>/SKILL.md."""
        repo = self._get("https://api.github.com/repos/anthropics/skills", gh=True)
        listing = self._get(
            "https://api.github.com/repos/anthropics/skills/contents/skills", gh=True)
        out = []
        for entry in listing if isinstance(listing, list) else []:
            if entry.get("type") != "dir":
                continue
            item = self._gh_item(repo, path=f"skills/{entry['name']}", source="anthropic")
            item["name"] = entry["name"]
            item["title"] = f"anthropics/skills · {entry['name']}"
            item["description"] = f"Official Anthropic skill: {entry['name'].replace('-', ' ')}"
            item["url"] = f"https://github.com/anthropics/skills/tree/main/skills/{entry['name']}"
            item["tags"] = ["official", "anthropic"]
            out.append(item)
        return out

    def _awesome_list(self, url: str, kind: str, label: str) -> List[Dict]:
        """Fetch + parse one awesome-list README (cached hard — they're big)."""
        cached = self.cache_get(f"list:{url}", 21600)
        if cached is not None:
            return cached
        r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout)
        r.raise_for_status()
        items: List[Dict] = []
        seen = set()
        for line in r.text.splitlines():
            m = MD_LINK.match(line)
            if not m:
                continue
            name, link, rest = m.group(1), m.group(2), m.group(3)
            if "github.com" not in link and "gitlab.com" not in link:
                continue
            key = _norm_repo(link)
            if not key or key in seen:
                continue
            seen.add(key)
            gh = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)(?:/tree/[^/]+/(.+))?",
                          link.rstrip("/"))
            path = gh.group(3) if gh and gh.group(3) else None
            ident = f"gh:{gh.group(1)}/{gh.group(2)}" + (f":{path}" if path else "") \
                if gh else f"url:{hashlib.sha1(link.encode()).hexdigest()[:12]}"
            items.append({
                "id": ident, "source": "awesome", "kind": kind,
                "name": _clean_md(name), "title": _clean_md(name),
                "description": _clean_md(rest)[:400],
                "url": link, "repo": link.split("/tree/")[0] if gh else link,
                "author": gh.group(1) if gh else "",
                "stars": None, "license": None,
                "tags": [label], "updated": None,
                "installable": bool(gh),
            })
            if len(items) >= 4000:
                break
        self.cache_put(f"list:{url}", items)
        return items

    def src_awesome(self, q: str, limit: int) -> List[Dict]:
        """Curated community indexes — parsed once, then filtered locally."""
        out: List[Dict] = []
        errs = []
        with ThreadPoolExecutor(max_workers=len(AWESOME_LISTS)) as pool:
            futures = [pool.submit(self._awesome_list, u, k, l)
                       for u, k, l in AWESOME_LISTS]
            for f in futures:
                try:
                    out.extend(f.result(timeout=self.timeout + 8))
                except Exception as e:
                    errs.append(str(e))
        if errs and not out:
            raise RuntimeError("; ".join(errs[:2]))
        ql = (q or "").strip().lower()
        if ql:
            terms = ql.split()
            out = [i for i in out
                   if all(t in f"{i['name']} {i['description']} {i['url']}".lower()
                          for t in terms)]
        return out[:max(limit * 3, 60)]

    def src_npm(self, q: str, limit: int) -> List[Dict]:
        """npm packages — MCP servers, skill installers, agent tooling."""
        text = f"{q.strip()} mcp" if (q or "").strip() else "keywords:mcp-server"
        data = self._get("https://registry.npmjs.org/-/v1/search",
                         params={"text": text, "size": min(limit, 25)})
        out = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            links = pkg.get("links", {}) or {}
            repo = links.get("repository") or ""
            kws = pkg.get("keywords") or []
            kind = "tool" if any("skill" in str(k) for k in kws) else "mcp" \
                if any("mcp" in str(k) for k in kws) else "package"
            out.append({
                "id": f"npm:{pkg.get('name')}", "source": "npm", "kind": kind,
                "name": pkg.get("name", ""), "title": pkg.get("name", ""),
                "description": pkg.get("description", "") or "",
                "url": links.get("npm") or f"https://www.npmjs.com/package/{pkg.get('name')}",
                "repo": repo,
                "author": (pkg.get("publisher") or {}).get("username", ""),
                "stars": None, "license": None,
                "tags": [str(k) for k in kws][:8],
                "updated": pkg.get("date") or obj.get("updated"),
                "downloads": (obj.get("downloads") or {}).get("monthly"),
                "installable": True,
                "install": {"npm": f"npx -y {pkg.get('name')}"},
            })
        return out

    def src_mcp(self, q: str, limit: int) -> List[Dict]:
        """The official MCP registry."""
        params = {"limit": min(limit, 30)}
        if (q or "").strip():
            params["search"] = q.strip()
        data = self._get("https://registry.modelcontextprotocol.io/v0/servers",
                         params=params)
        out = []
        for rec in data.get("servers", []):
            s = rec.get("server", rec) or {}
            name = s.get("name", "")
            repo = (s.get("repository") or {}).get("url", "")
            remotes = s.get("remotes") or []
            pkgs = s.get("packages") or []
            install: Dict[str, Any] = {}
            if remotes:
                install["remote"] = remotes[0].get("url")
            if pkgs:
                p0 = pkgs[0]
                install["package"] = f"{p0.get('registryType', '')}:{p0.get('identifier', p0.get('name', ''))}"
            out.append({
                "id": f"mcp:{name}", "source": "mcp", "kind": "mcp",
                "name": name.split("/")[-1] or name, "title": name,
                "description": s.get("description", "") or "",
                "url": repo or f"https://registry.modelcontextprotocol.io/v0/servers?search={urllib.parse.quote(name)}",
                "repo": repo, "author": name.split("/")[0] if "/" in name else "",
                "stars": None, "license": None,
                "tags": ["mcp"] + ([f"v{s['version']}"] if s.get("version") else []),
                "updated": ((rec.get("_meta") or {}).get(
                    "io.modelcontextprotocol.registry/official") or {}).get("updatedAt"),
                "installable": True, "install": install,
            })
        return out

    def src_glama(self, q: str, limit: int) -> List[Dict]:
        """The Glama MCP directory — carries per-server tool listings."""
        params = {"first": min(limit, 25)}
        if (q or "").strip():
            params["query"] = q.strip()
        data = self._get("https://glama.ai/api/mcp/v1/servers", params=params)
        out = []
        for s in data.get("servers", []):
            repo = (s.get("repository") or {}).get("url", "")
            tools = s.get("tools") or []
            out.append({
                "id": f"glama:{s.get('id')}", "source": "glama", "kind": "mcp",
                "name": s.get("name") or s.get("slug", ""),
                "title": s.get("name") or s.get("slug", ""),
                "description": s.get("description", "") or "",
                "url": s.get("url") or repo, "repo": repo,
                "author": s.get("namespace", ""),
                "stars": None, "license": s.get("spdxLicense") or None,
                "tags": ["mcp"] + [a.split(":")[-1] for a in (s.get("attributes") or [])][:5],
                "updated": None, "tools": len(tools),
                "installable": True,
                "install": {"tools": [t.get("name") for t in tools][:20]},
            })
        return out

    # ── scan ─────────────────────────────────────────────────────────

    def sources(self) -> List[Dict]:
        """The source catalog, with cache/auth state for the UI."""
        tok = bool(self.token())
        return [{**s, "auth": ("token" if tok else "anonymous") if s["id"] in
                 ("github", "topics", "anthropic") else "open"} for s in SOURCES]

    def _score(self, item: Dict, q: str) -> float:
        """Relevance first, popularity second.

        Popularity is damped and capped: a 300k-star awesome-list that merely
        mentions the query must never outrank the skill you asked for.
        """
        s = SOURCE_WEIGHT.get(item.get("source", ""), 1.0)
        name = (item.get("name") or "").lower()
        title = (item.get("title") or "").lower()
        desc = (item.get("description") or "").lower()
        tags = " ".join(item.get("tags") or []).lower()
        terms = [t for t in (q or "").lower().split() if t]
        hits = 0
        for t in terms:
            if t in name or t in title:
                s += 3.0
                hits += 1
            elif t in desc or t in tags:
                s += 1.0
                hits += 1
        if terms and not hits:            # matched only via readme/full text
            s -= 6.0
        s += min(math.log10(1 + (item.get("stars") or 0)) * 0.7, 3.0)
        s += min(math.log10(1 + (item.get("downloads") or 0)) * 0.3, 1.5)
        if item.get("kind") == "tool":
            s += 1.5
        if not item.get("description"):
            s -= 1.0
        return s

    def search(self, q: str = "", sources: List[str] = None, limit: int = 30,
               kind: str = None, fresh: bool = False) -> Dict:
        """Fan out across every selected source, merge, dedupe, rank.

        Partial results are the norm: a source that errors or times out
        reports its error and the rest of the scan still returns.
        """
        picked = [s for s in SOURCES if not sources or s["id"] in sources]
        if not picked:
            raise ValueError(f"unknown sources: {sources}")
        q = (q or "").strip()
        results: Dict[str, List[Dict]] = {}
        errors: Dict[str, str] = {}
        cached_from: List[str] = []
        started = time.time()

        def run(src: Dict):
            key = f"scan:{src['id']}:{q}:{limit}"
            if not fresh:
                hit = self.cache_get(key, src["ttl"])
                if hit is not None:
                    cached_from.append(src["id"])
                    return src["id"], hit, None
            try:
                items = getattr(self, f"src_{src['id']}")(q, limit) or []
                self.cache_put(key, items)
                return src["id"], items, None
            except Exception as e:
                stale = self.cache_get(key, src["ttl"] * 20)   # serve stale over nothing
                return src["id"], (stale or []), f"{type(e).__name__}: {e}"[:200]

        with ThreadPoolExecutor(max_workers=min(8, len(picked))) as pool:
            for sid, items, err in pool.map(run, picked):
                results[sid] = items
                if err:
                    errors[sid] = err

        # merge + dedupe: the same repo from three registries is one card
        merged: Dict[str, Dict] = {}
        for sid, items in results.items():
            for it in items:
                if kind and it.get("kind") != kind:
                    continue
                key = _norm_repo(it.get("repo") or "") or f"{it['source']}:{it['id']}"
                it = {**it, "score": self._score(it, q)}
                cur = merged.get(key)
                if cur is None:
                    it["also"] = []
                    merged[key] = it
                else:
                    also = sorted(set(cur.get("also", []) + [cur["source"], it["source"]])
                                  - {max(cur, it, key=lambda x: x["score"])["source"]})
                    winner = cur if cur["score"] >= it["score"] else it
                    loser = it if winner is cur else cur
                    winner = {**winner, "also": also}
                    # keep the richer description / star count across sources
                    if not winner.get("description") and loser.get("description"):
                        winner["description"] = loser["description"]
                    if winner.get("stars") is None and loser.get("stars") is not None:
                        winner["stars"] = loser["stars"]
                    merged[key] = winner

        items = sorted(merged.values(), key=lambda i: -i["score"])[:limit]
        self._remember(items)
        counts: Dict[str, int] = {}
        for i in merged.values():
            counts[i["source"]] = counts.get(i["source"], 0) + 1
        kinds: Dict[str, int] = {}
        for i in merged.values():
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1

        return {
            "q": q, "items": items, "total": len(merged),
            "sources": {s["id"]: {"label": s["label"],
                                  "found": len(results.get(s["id"], [])),
                                  "cached": s["id"] in cached_from,
                                  "error": errors.get(s["id"])}
                        for s in picked},
            "facets": {"sources": counts, "kinds": kinds},
            "errors": errors,
            "elapsed": round(time.time() - started, 2),
            "token": bool(self.token()),
        }

    # ── detail + install ─────────────────────────────────────────────

    @staticmethod
    def _parse_id(item_id: str):
        kind, _, rest = (item_id or "").partition(":")
        if kind == "gh":
            repo, _, path = rest.partition(":")
            return "gh", repo, (path or None)
        return kind, rest, None

    def _gh_contents(self, repo: str, path: str = "") -> Any:
        url = f"https://api.github.com/repos/{repo}/contents/{path.strip('/')}"
        return self._get(url, gh=True)

    def _gh_file(self, repo: str, path: str) -> Optional[str]:
        try:
            data = self._gh_contents(repo, path)
        except Exception:
            return None
        if isinstance(data, dict) and data.get("content"):
            try:
                return base64.b64decode(data["content"]).decode("utf-8", "replace")
            except Exception:
                return None
        return None

    def find_docs(self, repo: str, path: str = None) -> List[Dict]:
        """Locate SKILL.md files in a repo: at `path`, at the root, or one
        level down under skills/ (the common multi-skill layout)."""
        found: List[Dict] = []
        probes = []
        if path:
            probes.append(path.strip("/"))
        probes += ["", "skills", ".claude/skills"]
        for probe in probes:
            try:
                listing = self._gh_contents(repo, probe)
            except Exception:
                continue
            if isinstance(listing, dict):                      # a file, not a dir
                continue
            names = {e["name"]: e for e in listing if isinstance(e, dict)}
            if "SKILL.md" in names:
                found.append({"path": f"{probe}/SKILL.md".lstrip("/"),
                              "name": probe.rsplit("/", 1)[-1] or repo.split("/")[-1]})
            for e in listing:
                if e.get("type") == "dir" and probe in ("skills", ".claude/skills"):
                    found.append({"path": f"{probe}/{e['name']}/SKILL.md",
                                  "name": e["name"], "unverified": True})
            if found:
                break
        return found[:40]

    def detail(self, id: str) -> Dict:
        """Full record for one result, including installable doc paths."""
        cached = self.cache_get(f"detail:{id}", 3600)
        if cached is not None:
            return cached
        kind, ref, path = self._parse_id(id)
        out: Dict[str, Any]
        if kind == "gh":
            repo = self._get(f"https://api.github.com/repos/{ref}", gh=True)
            out = self._gh_item(repo, path=path)
            out["docs"] = self.find_docs(ref, path)
            out["readme"] = (self._gh_file(ref, "README.md") or "")[:8000]
            out["topics"] = repo.get("topics") or []
            out["forks"] = repo.get("forks_count")
            out["open_issues"] = repo.get("open_issues_count")
        elif kind == "npm":
            data = self._get(f"https://registry.npmjs.org/{urllib.parse.quote(ref, safe='@')}")
            latest = (data.get("dist-tags") or {}).get("latest")
            ver = (data.get("versions") or {}).get(latest, {})
            repo_url = ((data.get("repository") or {}) or {}).get("url", "")
            out = {
                "id": id, "source": "npm", "kind": "package",
                "name": data.get("name", ref), "title": data.get("name", ref),
                "description": data.get("description", ""),
                "url": f"https://www.npmjs.com/package/{ref}",
                "repo": re.sub(r"^git\+|\.git$", "", repo_url),
                "license": data.get("license"), "version": latest,
                "tags": (ver.get("keywords") or [])[:10],
                "readme": (data.get("readme") or "")[:8000],
                "install": {"npm": f"npx -y {ref}"},
                "bin": list((ver.get("bin") or {}).keys()),
                "installable": True,
            }
        elif kind in ("mcp", "glama"):
            # the scan already saw this record — re-searching for it is a
            # guess, so trust the index first
            match = self.recall(id)
            if not match:
                hits = self.search(ref.split("/")[-1], sources=[kind], limit=25)
                match = next((i for i in hits["items"] if i["id"] == id), None)
            if not match:
                raise KeyError(f"not found: {id} — scan again, then open it")
            out = dict(match)
            if out.get("repo") and "github.com" in out["repo"]:
                slug = _norm_repo(out["repo"]).replace("github.com/", "")
                out["readme"] = (self._gh_file(slug, "README.md") or "")[:8000]
                out["docs"] = self.find_docs(slug)
        else:
            raise KeyError(f"cannot resolve id: {id}")
        self.cache_put(f"detail:{id}", out)
        return out

    def tool_doc(self, id: str, path: str = None) -> Dict:
        """Build the installable document for a result.

        Real SKILL.md content when the source has one; otherwise a reference
        card (install command, remote URL, tool list) so MCP servers and
        packages are still useful in the library.
        """
        kind, ref, id_path = self._parse_id(id)
        path = path or id_path

        if kind == "gh":
            candidates = [path] if path else []
            if path and not path.endswith(".md"):
                candidates = [f"{path.rstrip('/')}/SKILL.md"]
            if not candidates:
                candidates = [s["path"] for s in self.find_docs(ref)] or ["README.md"]
            for cand in candidates[:6]:
                md = self._gh_file(ref, cand)
                if not md:
                    continue
                fm = parse_frontmatter(md)
                # a skill names itself in frontmatter; otherwise use its
                # directory (skills/pdf/SKILL.md → pdf), else the repo
                fallback = cand.rsplit("/", 2)[-2] if "/" in cand else ref.split("/")[-1]
                head = FRONTMATTER.match(md)
                body = md[len(head.group(0)):] if head else md
                url = f"https://github.com/{ref}/blob/HEAD/{cand}"
                return {
                    "name": str(fm.get("name") or fallback),
                    "description": str(fm.get("description") or "")[:400],
                    "body": body.strip(),
                    "tags": ["github"] + ([str(t) for t in fm.get("tags", [])][:6]
                                          if isinstance(fm.get("tags"), list) else []),
                    "source": "github", "url": url, "origin_id": id,
                    "kind": "tool",
                    "license": fm.get("license"),
                }
            raise KeyError(f"no SKILL.md or README.md found in {ref}")

        info = self.detail(id)
        install = info.get("install") or {}
        lines = [f"# {info.get('title') or info.get('name')}", ""]
        if info.get("description"):
            lines += [info["description"], ""]
        lines += ["## Source", f"- registry: {info.get('source')}",
                  f"- url: {info.get('url')}"]
        if info.get("repo"):
            lines.append(f"- repo: {info['repo']}")
        if info.get("version"):
            lines.append(f"- version: {info['version']}")
        if info.get("license"):
            lines.append(f"- license: {info['license']}")
        if install.get("npm") or install.get("package"):
            lines += ["", "## Install",
                      f"```\n{install.get('npm') or install.get('package')}\n```"]
        if install.get("remote"):
            lines += ["", "## Remote endpoint", f"`{install['remote']}`"]
        if install.get("tools"):
            lines += ["", "## Tools", *[f"- {t}" for t in install["tools"]]]
        if info.get("readme"):
            lines += ["", "## README (excerpt)", info["readme"][:4000]]
        return {
            "name": info.get("name") or id,
            "description": (info.get("description") or "")[:400],
            "body": "\n".join(lines),
            "tags": [info.get("source", "web")] + (info.get("tags") or [])[:6],
            "source": info.get("source", "web"), "url": info.get("url", ""),
            "origin_id": id, "kind": info.get("kind", "mcp"),
            "license": info.get("license"),
        }

    # ── mod protocol entry point ─────────────────────────────────────

    def forward(self, action: str = None, **kwargs) -> Any:
        if action in (None, "search", "scan"):
            return self.search(kwargs.get("q", ""), kwargs.get("sources"),
                               int(kwargs.get("limit", 30)), kwargs.get("kind"),
                               bool(kwargs.get("fresh")))
        if action == "sources":
            return {"sources": self.sources(), "token": bool(self.token())}
        if action == "detail":
            return self.detail(kwargs.get("id", ""))
        if action == "doc":
            return self.tool_doc(kwargs.get("id", ""), kwargs.get("path"))
        if action == "token":
            return self.set_token(kwargs.get("token", ""))
        if action == "clear_cache":
            return self.clear_cache()
        raise ValueError(f"unknown action: {action}")

    def test(self) -> bool:
        """Offline-safe: exercises parsing, ranking, dedupe, and the cache."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dc = Discover(dir=d)

            # frontmatter parser
            fm = parse_frontmatter("---\nname: pdf\ndescription: Do PDFs\n---\nbody here")
            assert fm["name"] == "pdf" and fm["description"] == "Do PDFs"
            assert parse_frontmatter("no frontmatter") == {}

            # awesome-list line parsing
            line = "- [D3.js Viz](https://github.com/a/b) - Charts *By [@a](https://github.com/a)*"
            m = MD_LINK.match(line)
            assert m and m.group(2) == "https://github.com/a/b"
            assert _clean_md(m.group(3)) == "Charts By @a"

            # repo url normalization (dedupe key)
            assert _norm_repo("https://github.com/A/B.git") == "github.com/a/b"
            assert _norm_repo("http://www.github.com/A/B/") == "github.com/a/b"

            # id round-trip
            assert dc._parse_id("gh:o/r:skills/x") == ("gh", "o/r", "skills/x")
            assert dc._parse_id("npm:@scope/pkg") == ("npm", "@scope/pkg", None)

            # cache honors ttl
            dc.cache_put("k", [1, 2])
            assert dc.cache_get("k", 60) == [1, 2]
            assert dc.cache_get("k", 0) is None
            assert dc.clear_cache()["cleared"] >= 1

            # ranking prefers name hits and stars
            hi = dc._score({"source": "github", "name": "pdf tools",
                            "description": "x", "stars": 900, "kind": "tool"}, "pdf")
            lo = dc._score({"source": "npm", "name": "other",
                            "description": "y", "kind": "package"}, "pdf")
            assert hi > lo

            # a dead source degrades to an error, never an exception
            dc.src_github = lambda q, l: (_ for _ in ()).throw(RuntimeError("boom"))
            out = dc.search("x", sources=["github"], limit=5)
            assert out["items"] == [] and "github" in out["errors"]

            # unknown source is a hard error
            try:
                dc.search("x", sources=["nope"])
                assert False, "expected ValueError"
            except ValueError:
                pass

            # merge keeps one card per repo and records the other sources
            dc.src_github = lambda q, l: [{"id": "gh:a/b", "source": "github",
                                           "kind": "tool", "name": "b",
                                           "description": "", "repo": "https://github.com/a/b",
                                           "stars": 5}]
            dc.src_npm = lambda q, l: [{"id": "npm:b", "source": "npm", "kind": "package",
                                        "name": "b", "description": "from npm",
                                        "repo": "https://github.com/A/B.git"}]
            out = dc.search("b", sources=["github", "npm"], limit=5)
            assert out["total"] == 1, out["total"]
            assert out["items"][0]["description"] == "from npm"   # richer field wins
            assert "npm" in out["items"][0]["also"]
        return True
