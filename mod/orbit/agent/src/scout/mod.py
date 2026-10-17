"""
scout - goes on the internet and comes back with an agent idea

Vibe turns a description into an agent. Scout writes the description: it reads
what people are building and talking about right now, and turns that into an
idea for an agent nobody on this console has made yet. The run is the product
as much as the idea, so every phase is an event you can watch:

    lens     the angle being scouted — the caller's theme, or one picked at
             random so "surprise me" means something
    search   keyless, public sources fanned out in parallel: Hacker News
             (Algolia), GitHub repos created in the last month, arXiv, and a
             DuckDuckGo web search. Each hit is one numbered signal
    read     the most promising pages fetched and boiled down to readable
             text — robots.txt honored, one request per host at a time,
             through the same polite crawler the scrapers use
    (ideate and vibe are model runs, owned by the module — see agent_scout)

This file never calls a model and never holds a key. It gathers, numbers and
digests; the ideation is the idea-scout agent's job, and the agent is the
vibe-builder's. That split is what makes each piece swappable: a new source is
one function in SOURCES, a different ideator is a different agent.

Runs are kept off-tree (~/.mod/agent/scout/runs.json, last MAX_RUNS) so the
whole process can be reopened after the fact — not only the idea, but what it
was built from.

Usage:
    s = Scout()
    signals = s.gather("local-first tools", on_event=print)
    pages = s.read(signals, limit=4, on_event=print)
    digest = s.digest(signals, pages)
"""
import json
import random
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from ..scrape.mod import Scrapers, UA

# angles worth scouting when the caller has none — broad enough to always
# return something, specific enough that two runs don't converge on one idea
LENSES = [
    "AI agents", "developer tools", "local-first software", "self-hosted",
    "open source LLM", "MCP servers", "automation scripts", "personal knowledge",
    "data journalism", "security tooling", "crypto onchain analytics",
    "scientific research tools", "education", "music and audio", "maps and GIS",
    "home automation", "accessibility", "code review", "testing", "devops",
    "small business", "health tracking", "writing tools", "game modding",
]

MAX_RUNS = 40
PER_SOURCE = 6            # hits kept per source
READ_DEFAULT = 4          # pages actually opened
READ_CAP = 8
PAGE_CHARS = 1800         # per page, in the digest the ideator reads
DIGEST_CHARS = 16000      # the whole digest
HTTP_TIMEOUT = 12


def _clean(s: Any, n: int = 300) -> str:
    s = unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    return re.sub(r"\s+", " ", s).strip()[:n]


# ── sources — each is (theme) -> [{title, url, snippet, meta}] ───────

def src_hn(theme: str, limit: int = PER_SOURCE) -> List[Dict]:
    """Hacker News stories from the last 30 days, best first."""
    since = int(time.time()) - 30 * 86400
    r = requests.get("https://hn.algolia.com/api/v1/search",
                     params={"query": theme, "tags": "story",
                             "numericFilters": f"created_at_i>{since}",
                             "hitsPerPage": limit * 2},
                     headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    hits = sorted(r.json().get("hits") or [], key=lambda h: -(h.get("points") or 0))
    out = []
    for h in hits[:limit]:
        hn = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append({"title": _clean(h.get("title"), 200),
                    "url": h.get("url") or hn,
                    "snippet": _clean(h.get("story_text"), 240),
                    "meta": f"{h.get('points') or 0} pts · {h.get('num_comments') or 0} comments",
                    "score": float(h.get("points") or 0)})
    return out


def src_github(theme: str, limit: int = PER_SOURCE) -> List[Dict]:
    """GitHub repos created in the last 30 days, most starred first."""
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
    r = requests.get("https://api.github.com/search/repositories",
                     params={"q": f"{theme} created:>{since}", "sort": "stars",
                             "order": "desc", "per_page": limit},
                     headers={"User-Agent": UA,
                              "Accept": "application/vnd.github+json"},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for it in r.json().get("items") or []:
        out.append({"title": it.get("full_name") or "",
                    "url": it.get("html_url") or "",
                    "snippet": _clean(it.get("description"), 240),
                    "meta": f"★ {it.get('stargazers_count') or 0} · {it.get('language') or '?'}",
                    "score": float(it.get("stargazers_count") or 0)})
    return out


ARXIV_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def src_arxiv(theme: str, limit: int = PER_SOURCE) -> List[Dict]:
    """The newest arXiv papers matching the theme."""
    r = requests.get("https://export.arxiv.org/api/query",
                     params={"search_query": f"all:{theme}", "max_results": limit,
                             "sortBy": "submittedDate", "sortOrder": "descending"},
                     headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for e in ARXIV_ENTRY.findall(r.text)[:limit]:
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", e, re.S)
            return m.group(1) if m else ""
        out.append({"title": _clean(tag("title"), 200),
                    "url": _clean(tag("id"), 300),
                    "snippet": _clean(tag("summary"), 240),
                    "meta": _clean(tag("published"), 10),
                    "score": 0.0})
    return out


SOURCES: Dict[str, Dict[str, Any]] = {
    "hn": {"label": "Hacker News", "fn": src_hn},
    "github": {"label": "GitHub (new repos)", "fn": src_github},
    "arxiv": {"label": "arXiv", "fn": src_arxiv},
    "web": {"label": "Web (DuckDuckGo)", "fn": None},   # bound to the crawler below
}


class Scout:
    description = "Reads the internet and gathers the signals an agent idea is built from"

    def __init__(self, dir: str = None, crawler: Scrapers = None):
        self.dir = Path(dir) if dir else Path.home() / ".mod" / "agent" / "scout"
        self.dir.mkdir(parents=True, exist_ok=True)
        # the scrapers' crawler is the polite one (robots, per-host gap) —
        # reuse it rather than growing a second, ruder one
        self.crawler = crawler or Scrapers(dir=str(self.dir / "crawl"))
        self._lock = threading.Lock()

    # ── lens ─────────────────────────────────────────────────────────

    @staticmethod
    def lens(theme: str = None) -> Dict[str, Any]:
        theme = str(theme or "").strip()
        if theme:
            return {"theme": theme[:120], "picked": False}
        return {"theme": random.choice(LENSES), "picked": True}

    @staticmethod
    def sources() -> List[Dict[str, str]]:
        return [{"name": k, "label": v["label"]} for k, v in SOURCES.items()]

    # ── search ───────────────────────────────────────────────────────

    def _search_one(self, name: str, theme: str) -> List[Dict]:
        if name == "web":
            q = f"new open source {theme} tool"
            return [{**h, "meta": "", "score": 0.0}
                    for h in self.crawler.search(q, limit=PER_SOURCE)]
        return SOURCES[name]["fn"](theme)

    def gather(self, theme: str, sources: List[str] = None,
               on_event: Callable = None) -> List[Dict]:
        """Every source at once; hits come back numbered [1..n] in one list.
        A source that fails is reported and skipped — one dead API never sinks
        the run."""
        names = [s for s in (sources or list(SOURCES)) if s in SOURCES] or list(SOURCES)
        emit = on_event or (lambda ev: None)
        results: Dict[str, List[Dict]] = {}
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futs = {n: pool.submit(self._search_one, n, theme) for n in names}
            for n in names:
                emit({"type": "source_start", "source": n,
                      "label": SOURCES[n]["label"]})
            for n, f in futs.items():
                try:
                    results[n] = f.result(timeout=HTTP_TIMEOUT * 2) or []
                    emit({"type": "source_done", "source": n, "count": len(results[n])})
                except Exception as e:
                    results[n] = []
                    emit({"type": "source_error", "source": n, "error": str(e)[:200]})
        # interleave so the digest isn't just whichever source answered first
        signals, seen, i = [], set(), 0
        while any(i < len(v) for v in results.values()):
            for n in names:
                if i < len(results[n]):
                    h = results[n][i]
                    key = (h.get("url") or "").rstrip("/").lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    sig = {"n": len(signals) + 1, "source": n, **h}
                    signals.append(sig)
                    emit({"type": "signal", "signal": sig})
            i += 1
        return signals

    # ── read ─────────────────────────────────────────────────────────

    @staticmethod
    def pick(signals: List[Dict], limit: int) -> List[Dict]:
        """Which signals are worth opening: the loudest of each source first,
        so one source never takes every read."""
        by: Dict[str, List[Dict]] = {}
        for s in signals:
            by.setdefault(s["source"], []).append(s)
        for v in by.values():
            v.sort(key=lambda s: -float(s.get("score") or 0))
        out: List[Dict] = []
        while len(out) < limit and any(by.values()):
            for v in by.values():
                if v and len(out) < limit:
                    out.append(v.pop(0))
        return out

    def read(self, signals: List[Dict], limit: int = READ_DEFAULT,
             on_event: Callable = None) -> List[Dict]:
        limit = max(0, min(int(limit), READ_CAP))
        emit = on_event or (lambda ev: None)
        pages = []
        for s in self.pick(signals, limit):
            url = s["url"]
            entry = {"n": s["n"], "url": url, "title": s.get("title", ""),
                     "status": "ok", "chars": 0, "text": ""}
            emit({"type": "read_start", "n": s["n"], "url": url})
            try:
                if not self.crawler._allowed(url):
                    entry.update(status="skipped", note="robots.txt")
                else:
                    page = self.crawler._page(url)
                    text = re.sub(r"\s+", " ", page.get("text") or "").strip()
                    if len(text) < 120:
                        entry.update(status="skipped", note="no readable text")
                    else:
                        entry.update(text=text[:PAGE_CHARS], chars=len(text))
            except Exception as e:
                entry.update(status="error", note=str(e)[:160])
            pages.append(entry)
            emit({"type": "read_done", **{k: v for k, v in entry.items() if k != "text"},
                  "preview": entry["text"][:220]})
        return pages

    # ── digest — what the ideator reads ──────────────────────────────

    @staticmethod
    def digest(signals: List[Dict], pages: List[Dict],
               limit: int = DIGEST_CHARS) -> str:
        text = {p["n"]: p["text"] for p in pages if p.get("status") == "ok"}
        lines = []
        for s in signals:
            head = f"[{s['n']}] ({s['source']}) {s.get('title', '')}"
            if s.get("meta"):
                head += f" — {s['meta']}"
            lines.append(f"{head}\n    {s['url']}")
            if s.get("snippet"):
                lines.append(f"    {s['snippet']}")
            if s["n"] in text:
                lines.append(f"    READ: {text[s['n']]}")
        return "\n".join(lines)[:limit]

    # ── run log (off-tree, survives a restart) ───────────────────────

    @property
    def _path(self) -> Path:
        return self.dir / "runs.json"

    def runs(self, owner: str = None, limit: int = 20) -> List[Dict]:
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            data = []
        if owner:
            data = [r for r in data if r.get("owner") == owner]
        return data[:max(1, int(limit))]

    def run(self, id: str) -> Optional[Dict]:
        return next((r for r in self.runs(limit=MAX_RUNS) if r.get("id") == id), None)

    def record(self, run: Dict) -> Dict:
        run = {"id": run.get("id") or f"sc-{uuid.uuid4().hex[:8]}", **run}
        with self._lock:
            data = [r for r in self.runs(limit=MAX_RUNS) if r.get("id") != run["id"]]
            data = [run, *data][:MAX_RUNS]
            try:
                self._path.write_text(json.dumps(data, indent=1, default=str))
            except Exception as e:
                print(f"scout: could not persist run: {e}")
        return run

    def test(self) -> bool:
        sigs = [{"n": 1, "source": "hn", "title": "a", "url": "https://a", "score": 5},
                {"n": 2, "source": "hn", "title": "b", "url": "https://b", "score": 9},
                {"n": 3, "source": "github", "title": "c", "url": "https://c", "score": 1}]
        picked = self.pick(sigs, 2)
        assert [p["n"] for p in picked] == [2, 3], picked
        d = self.digest(sigs, [{"n": 2, "status": "ok", "text": "hello"}])
        assert "READ: hello" in d and "[3] (github)" in d
        assert self.lens("x")["theme"] == "x" and self.lens()["picked"]
        return True
