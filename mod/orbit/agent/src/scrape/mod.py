"""
scrape - scraper agents that read the web and write skills

Discover finds the skills other people already published. This writes the ones
nobody published: point a scraper agent at a topic, and it searches the web,
crawls what it finds, and distills the pages into one SKILL.md — grounded in
the sources it actually read, every section traceable back to a URL.

A run is three phases, each visible while it happens:

    search   a topic becomes result URLs (DuckDuckGo), or you hand it seeds
    crawl    fetch, extract readable text, follow the most on-topic links
    write    one model call turns the corpus into a skill document

Crawling is polite by construction: robots.txt is honored per host, one
request at a time to a host with a delay between, and a hard page/character
budget — so a scraper can never wander off into the whole internet.

Nothing installs itself. A finished run holds a draft you read first; install
files it into the library as a skill document (a document, never executable
code), owned by whoever installed it and pinned to a CID like the rest of the
library. Re-scraping the same topic refreshes that skill in place instead of
piling up near-duplicates.

Usage:
    s = Scrapers(model=lambda: agent.model, library=agent.library)
    job = s.start("how to write a good SKILL.md", key=token)
    s.job(job['id'])                 # poll: status, pages read, draft
    s.install(job['id'], key=token)  # file the draft in the library
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.robotparser
import uuid
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from ..discover.mod import parse_frontmatter, FRONTMATTER

# Identify honestly, in the compatible-token form servers expect. Not cosmetic:
# DuckDuckGo serves its no-results challenge page to UAs that pretend to be a
# browser, and answers a self-declared bot normally.
UA = "Mozilla/5.0 (compatible; ModAgent/1.0; +https://github.com/mod)"

SEARCH_URL = "https://html.duckduckgo.com/html/"

# budgets — a scraper is a bounded errand, not a crawler farm
MAX_PAGES = 10          # pages fetched per run (hard cap MAX_PAGES_CAP)
MAX_PAGES_CAP = 40
MAX_DEPTH = 2           # how many link hops past the seeds
PAGE_CHARS = 6000       # readable text kept per page
CORPUS_CHARS = 45000    # total text handed to the model
HOST_DELAY = 0.6        # seconds between two hits on the same host
MAX_JOBS = 40           # runs kept in the on-disk registry

# link text / paths worth following when a scraper goes a hop deeper
DOCISH = ("doc", "docs", "guide", "guides", "tutorial", "reference", "manual",
          "handbook", "getting-started", "quickstart", "readme", "api",
          "examples", "faq", "how-to", "howto", "best-practices", "spec")

# never worth fetching — binaries, archives, media
SKIP_EXT = (".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".exe", ".dmg", ".pkg",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".mp4",
            ".mp3", ".wav", ".mov", ".avi", ".woff", ".woff2", ".ttf", ".css",
            ".js", ".map", ".xml", ".rss", ".atom", ".csv", ".xlsx", ".doc",
            ".docx", ".ppt", ".pptx", ".iso", ".bin")

DDG_LINK = re.compile(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DDG_SNIP = re.compile(r'<a[^>]+class="result__snippet"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
TAGS = re.compile(r"<[^>]+>")
FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n\s*```\s*$", re.S)


def _slug(text: str, fallback: str = "scraped-skill") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48].strip("-") or fallback)


def _strip_html(s: str) -> str:
    return unescape(TAGS.sub("", s or "")).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo hands back /l/?uddg=<encoded target> — unwrap to the target."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def _norm_url(url: str) -> str:
    """Canonical form for dedupe: no fragment, no trailing slash, no tracking."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref_"))]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path,
                                    urllib.parse.urlencode(q), ""))


class _Reader(HTMLParser):
    """Readable text + outbound links from one HTML page.

    Chrome (nav, header, footer, aside, forms) is dropped: it repeats on every
    page of a site and would otherwise dominate a small corpus.
    """
    DROP = {"script", "style", "noscript", "svg", "nav", "header", "footer",
            "aside", "form", "template", "iframe"}
    BREAK = {"p", "br", "div", "li", "tr", "section", "article", "pre",
             "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title = ""
        self.links: List[tuple] = []      # (absolute url, anchor text)
        self._out: List[str] = []
        self._drop = 0
        self._in_title = False
        self._href: Optional[str] = None
        self._anchor: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self._drop += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href, self._anchor = href, []
        if tag in self.BREAK:
            self._out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self._drop = max(0, self._drop - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            try:
                url = urllib.parse.urljoin(self.base, self._href)
            except ValueError:
                url = ""
            if url.startswith(("http://", "https://")):
                self.links.append((url, " ".join(self._anchor)[:120]))
            self._href, self._anchor = None, []

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()[:200]
            return
        if self._drop:
            return
        self._out.append(text)
        if self._href is not None:
            self._anchor.append(text)

    def text(self) -> str:
        joined = " ".join(self._out)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", joined)).strip()


# ── the prompt that turns pages into a skill ────────────────────────
# Written as a contract rather than a request: the failure mode of a
# distillation model is confident filler, so the instructions push toward
# "what the sources actually said" and give it an out when they said nothing.

WRITE_PROMPT = """You are a scraper agent. You just read {n} web pages about a topic. Turn what you read into ONE skill document that another AI agent will load as context when it works on this topic.

TOPIC: {topic}

Write a Markdown document, and nothing else — no preamble, no code fence around the whole thing. Exact shape:

---
name: {slug}
description: <one sentence, under 200 chars: what this skill knows and when an agent should reach for it>
---

# <Title>

## When to use
2-4 bullets naming the situations this applies to.

## What to know
The substance: the concepts, rules, parameters, names, and numbers the sources
actually state. Prefer specifics over summary — an API signature, a real
default value, an exact command. Cite sources inline as [1], [2].

## How to do it
Concrete steps or a worked example, if the sources support one.

## Gotchas
What the sources warn about: failure modes, deprecations, version limits.
Skip this section if the sources warn about nothing.

## Sources
A numbered list matching the citations: [n] Title — URL

RULES
- Ground every claim in the SOURCES below. If they do not say it, do not write it.
- Where sources disagree, say so and cite both.
- If the sources turned out to be thin or off-topic, say that plainly in
  "What to know" instead of padding the document.
- No invented URLs, versions, or numbers. No filler.
- Target 300-900 words.

SOURCES
{corpus}
"""


class Scrapers:
    description = "Scraper agents that crawl the web and distill it into skills"

    def __init__(self, dir: str = None, model: Callable = None,
                 library=None, identity=None, timeout: int = 15):
        self.dir = Path(dir) if dir else Path.home() / ".mod" / "agent" / "scrape"
        self.dir.mkdir(parents=True, exist_ok=True)
        # a getter, not the model: the provider can be switched at runtime and
        # a scraper started afterwards must use the live one
        self._model = model
        self.library = library
        self.identity = identity
        self.timeout = timeout
        self._jobs: "Dict[str, dict]" = {}
        self._lock = threading.Lock()
        self._robots: Dict[str, Any] = {}
        self._last_hit: Dict[str, float] = {}
        self._load()

    # ── registry (off-tree, survives a restart) ──────────────────────

    @property
    def _path(self) -> Path:
        return self.dir / "jobs.json"

    def _load(self):
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return
        for job in data if isinstance(data, list) else []:
            # a run that was in flight when the process died is not running now
            if job.get("status") in ("searching", "crawling", "writing", "queued"):
                job["status"] = "error"
                job["error"] = job.get("error") or "interrupted — the module restarted"
            self._jobs[job["id"]] = job

    def _save(self):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.get("started", 0),
                          reverse=True)[:MAX_JOBS]
            self._jobs = {j["id"]: j for j in jobs}
        try:
            self._path.write_text(json.dumps(jobs, indent=2, default=str))
        except Exception as e:
            print(f"scrape: could not persist jobs: {e}")

    # ── crawl primitives ─────────────────────────────────────────────

    def _allowed(self, url: str) -> bool:
        """robots.txt for this host, cached. Unreachable robots means allowed —
        that is what every other polite crawler does with a 404."""
        host = urllib.parse.urlsplit(url).netloc
        rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = requests.get(f"{urllib.parse.urlsplit(url).scheme}://{host}/robots.txt",
                                 headers={"User-Agent": UA}, timeout=6)
                rp.parse(r.text.splitlines() if r.ok else [])
            except Exception:
                rp.parse([])
            self._robots[host] = rp
        try:
            return rp.can_fetch(UA, url)
        except Exception:
            return True

    def _wait(self, url: str):
        """One request at a time per host, with a gap between."""
        host = urllib.parse.urlsplit(url).netloc
        gap = time.time() - self._last_hit.get(host, 0)
        if gap < HOST_DELAY:
            time.sleep(HOST_DELAY - gap)
        self._last_hit[host] = time.time()

    def search(self, q: str, limit: int = 8) -> List[Dict]:
        """Web search for a topic — result URLs with titles and snippets."""
        self._wait(SEARCH_URL)
        r = requests.post(SEARCH_URL, data={"q": q}, timeout=self.timeout,
                          headers={"User-Agent": UA,
                                   "Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        snips = {_unwrap(h): _strip_html(s)[:300] for h, s in DDG_SNIP.findall(r.text)}
        out, seen = [], set()
        for href, title in DDG_LINK.findall(r.text):
            url = _unwrap(href)
            key = _norm_url(url)
            if key in seen or not url.startswith(("http://", "https://")):
                continue
            if url.lower().endswith(SKIP_EXT):
                continue
            seen.add(key)
            out.append({"url": url, "title": _strip_html(title)[:200],
                        "snippet": snips.get(url, "")})
            if len(out) >= limit:
                break
        return out

    def _page(self, url: str) -> Dict:
        """Fetch one page and extract its readable text and links."""
        self._wait(url)
        r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout,
                         allow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ValueError(f"not readable text ({ctype.split(';')[0] or 'unknown'})")
        if "html" not in ctype:  # plain text / markdown: take it as-is
            return {"title": url.rsplit("/", 1)[-1], "text": r.text[:PAGE_CHARS],
                    "links": [], "url": r.url}
        reader = _Reader(r.url)
        reader.feed(r.text[:400_000])
        return {"title": reader.title or url, "text": reader.text()[:PAGE_CHARS],
                "links": reader.links, "url": r.url}

    @staticmethod
    def _rank_links(links: List[tuple], topic: str, origin: str) -> List[str]:
        """Order a page's links by how likely they are to be more of the topic."""
        words = {w for w in re.split(r"\W+", topic.lower()) if len(w) > 3}
        host = urllib.parse.urlsplit(origin).netloc
        scored, seen = [], set()
        for url, anchor in links:
            key = _norm_url(url)
            if key in seen or url.lower().endswith(SKIP_EXT):
                continue
            seen.add(key)
            hay = f"{url} {anchor}".lower()
            score = sum(2.0 for w in words if w in hay)
            score += sum(0.5 for d in DOCISH if d in hay)
            if urllib.parse.urlsplit(url).netloc == host:
                score += 1.0     # the site you are already reading knows the topic
            if score > 0:
                scored.append((score, url))
        scored.sort(key=lambda s: -s[0])
        return [u for _, u in scored]

    # ── the run ──────────────────────────────────────────────────────

    def start(self, topic: str, seeds: List[str] = None, depth: int = 1,
              max_pages: int = MAX_PAGES, model: str = None, free: bool = False,
              install: bool = False, key: str = None, on_event: Callable = None,
              wait: bool = False) -> Dict:
        """Launch a scraper agent. Returns the job immediately; it runs in the
        background unless wait=True."""
        topic = (topic or "").strip()
        seeds = [s.strip() for s in (seeds or []) if s and s.strip()]
        if not topic and not seeds:
            raise ValueError("a scraper needs a topic to search for, or seed URLs to read")
        job = {
            "id": f"s-{uuid.uuid4().hex[:8]}",
            "topic": topic or seeds[0],
            "seeds": seeds,
            "status": "queued",
            "phase": "queued",
            "depth": max(0, min(int(depth), MAX_DEPTH)),
            "max_pages": max(1, min(int(max_pages), MAX_PAGES_CAP)),
            "model": model,
            "free": bool(free),
            "auto_install": bool(install),
            "owner": self.identity.addr(key) if self.identity else None,
            "started": time.time(),
            "finished": None,
            "hits": [],
            "pages": [],
            "log": [],
            "draft": None,
            "skill_id": None,
            "error": None,
        }
        self._jobs[job["id"]] = job
        self._save()

        run = lambda: self._run(job, key=key, on_event=on_event)
        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True, name=f"scrape-{job['id']}").start()
        return self.job(job["id"])

    def _emit(self, job: Dict, on_event: Optional[Callable], type: str, **fields):
        entry = {"t": round(time.time() - job["started"], 2), "type": type, **fields}
        job["log"] = (job["log"] + [entry])[-120:]
        if on_event:
            try:
                on_event({"type": type, "job": job["id"], **fields})
            except Exception:
                pass

    def _phase(self, job: Dict, on_event, phase: str, note: str = ""):
        job["status"] = job["phase"] = phase
        self._emit(job, on_event, "phase", phase=phase, note=note)

    def _run(self, job: Dict, key: str = None, on_event: Callable = None):
        try:
            urls = list(job["seeds"])
            if not urls or job["topic"] != (job["seeds"][0] if job["seeds"] else None):
                if not urls:
                    self._phase(job, on_event, "searching", job["topic"])
                    job["hits"] = self.search(job["topic"], limit=max(4, job["max_pages"]))
                    self._emit(job, on_event, "hits", count=len(job["hits"]),
                               urls=[h["url"] for h in job["hits"]])
                    urls = [h["url"] for h in job["hits"]]
            if not urls:
                raise RuntimeError("search returned nothing — try different words, "
                                   "or hand the scraper seed URLs")

            self._phase(job, on_event, "crawling")
            corpus = self._crawl(job, urls, on_event)
            read = [p for p in job["pages"] if p["status"] == "ok"]
            if not read:
                raise RuntimeError("could not read any page — every candidate was "
                                   "blocked, empty, or unreachable")

            self._phase(job, on_event, "writing", f"{len(read)} pages")
            job["draft"] = self._write(job, corpus, len(read))
            self._emit(job, on_event, "draft", name=job["draft"]["name"],
                       chars=len(job["draft"]["body"]))

            job["status"] = job["phase"] = "ready"
            if job["auto_install"]:
                try:
                    job["skill_id"] = self.install(job["id"], key=key)["id"]
                    job["status"] = "installed"
                except Exception as e:
                    self._emit(job, on_event, "note", note=f"install failed: {e}")
        except Exception as e:
            job["status"] = job["phase"] = "error"
            job["error"] = str(e)
            self._emit(job, on_event, "error", error=str(e))
        finally:
            job["finished"] = time.time()
            job["elapsed"] = round(job["finished"] - job["started"], 2)
            self._save()
            if on_event:
                try:
                    on_event({"type": "done", "job": job["id"],
                              "status": job["status"], "result": self.job(job["id"])})
                except Exception:
                    pass

    def _crawl(self, job: Dict, seeds: List[str], on_event=None) -> str:
        """Walk the frontier within budget, returning the numbered corpus."""
        frontier = [(u, 0) for u in seeds]
        seen = {_norm_url(u) for u in seeds}
        parts: List[str] = []
        chars = 0
        n = 0
        while frontier and len(parts) < job["max_pages"] and chars < CORPUS_CHARS:
            url, depth = frontier.pop(0)
            entry = {"url": url, "depth": depth, "title": "", "chars": 0,
                     "status": "ok", "note": ""}
            try:
                if not self._allowed(url):
                    entry.update(status="skipped", note="robots.txt")
                else:
                    page = self._page(url)
                    text = page["text"].strip()
                    if len(text) < 200:
                        entry.update(status="skipped", note="no readable text")
                    else:
                        n += 1
                        entry.update(title=page["title"][:160], chars=len(text),
                                     url=page["url"], n=n)
                        parts.append(f"[{n}] {page['title']}\nURL: {page['url']}\n{text}")
                        chars += len(text)
                        if depth < job["depth"]:
                            room = job["max_pages"] - len(parts) - len(frontier)
                            for link in self._rank_links(page["links"], job["topic"], page["url"]):
                                if room <= 0:
                                    break
                                k = _norm_url(link)
                                if k in seen:
                                    continue
                                seen.add(k)
                                frontier.append((link, depth + 1))
                                room -= 1
            except Exception as e:
                entry.update(status="error", note=str(e)[:160])
            job["pages"].append(entry)
            self._emit(job, on_event, "page", **entry)
        return "\n\n".join(parts)[:CORPUS_CHARS]

    def _write(self, job: Dict, corpus: str, n_pages: int) -> Dict:
        """One model call: corpus in, skill document out."""
        model = self._model() if callable(self._model) else self._model
        if model is None:
            raise RuntimeError("No model configured — add or unlock an API key in "
                               "the Builder (model node) before running a scraper.")
        slug = _slug(job["topic"])
        out = model.forward(
            WRITE_PROMPT.format(n=n_pages, topic=job["topic"], slug=slug, corpus=corpus),
            stream=False, model=job.get("model"), free=job.get("free", False),
            max_tokens=4096, temperature=0.2,
        )
        if not isinstance(out, str):      # a provider that streamed anyway
            out = "".join(str(c) for c in out)
        return self._draft(out, job, slug)

    def _draft(self, md: str, job: Dict, slug: str) -> Dict:
        """Parse the model's markdown into an installable skill bundle."""
        md = (md or "").strip()
        fenced = FENCE.match(md)
        if fenced:
            md = fenced.group(1).strip()
        if not md:
            raise RuntimeError("the model returned an empty document")
        fm = parse_frontmatter(md)
        head = FRONTMATTER.match(md)
        body = md[len(head.group(0)):].strip() if head else md
        desc = str(fm.get("description") or "").strip()
        if not desc:
            # no frontmatter: first prose line under the title is the summary
            for line in body.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    desc = line[:400]
                    break
        urls = [p["url"] for p in job["pages"] if p["status"] == "ok"]
        return {
            "name": _slug(str(fm.get("name") or ""), slug),
            "description": desc[:400],
            "body": body,
            "tags": ["scraped", *[w for w in re.split(r"\W+", job["topic"].lower())
                                  if len(w) > 3][:4]],
            "sources": urls,
            "url": urls[0] if urls else "",
            "words": len(body.split()),
        }

    # ── reading and filing runs ──────────────────────────────────────

    @staticmethod
    def _view(job: Dict) -> Dict:
        ok = [p for p in job.get("pages", []) if p.get("status") == "ok"]
        return {**job,
                "read": len(ok),
                "running": job.get("status") in ("queued", "searching", "crawling", "writing")}

    def jobs(self, owner: str = None, limit: int = 25) -> Dict:
        items = sorted(self._jobs.values(), key=lambda j: j.get("started", 0), reverse=True)
        if owner:
            items = [j for j in items if (j.get("owner") or "").lower() == owner.lower()]
        views = [self._view(j) for j in items[:max(1, limit)]]
        return {"jobs": views, "total": len(items),
                "running": sum(1 for j in self._jobs.values()
                               if j.get("status") in ("queued", "searching", "crawling", "writing"))}

    def job(self, id: str) -> Dict:
        job = self._jobs.get(id)
        if not job:
            raise KeyError(f"no such scraper run: {id}")
        return self._view(job)

    def install(self, id: str, key: str = None) -> Dict:
        """File a finished draft in the library as an installed tool doc."""
        job = self._jobs.get(id)
        if not job:
            raise KeyError(f"no such scraper run: {id}")
        draft = job.get("draft")
        if not draft:
            raise ValueError(f"run {id} has no draft yet (status: {job.get('status')})")
        if self.library is None:
            raise RuntimeError("no library bound — cannot install")
        doc = self.library.tool_add(
            draft["name"], draft["body"], draft["description"], draft["tags"],
            source="scrape", url=draft.get("url", ""),
            origin_id=f"scrape:{_slug(job['topic'])}", key=key)
        job["skill_id"] = doc.get("id")
        job["status"] = "installed"
        self._save()
        return doc

    def rm(self, id: str, key: str = None) -> Dict:
        job = self._jobs.get(id)
        if not job:
            raise KeyError(f"no such scraper run: {id}")
        if self.identity:
            self.identity.require(job.get("owner"), key,
                                  operation=f"remove scraper run {id}", builtin=False)
        self._jobs.pop(id, None)
        self._save()
        return {"removed": id}

    # ── mod protocol entry point ─────────────────────────────────────

    def forward(self, action: str = None, key: str = None, **kwargs) -> Any:
        if action in (None, "jobs"):
            return self.jobs(kwargs.get("owner"), int(kwargs.get("limit", 25)))
        if action in ("start", "scrape"):
            return self.start(kwargs.get("topic", ""), kwargs.get("seeds"),
                              int(kwargs.get("depth", 1)),
                              int(kwargs.get("max_pages", MAX_PAGES)),
                              kwargs.get("model"), bool(kwargs.get("free")),
                              bool(kwargs.get("install")), key=key,
                              wait=bool(kwargs.get("wait")))
        if action == "job":
            return self.job(kwargs.get("id", ""))
        if action == "install":
            return self.install(kwargs.get("id", ""), key=key)
        if action == "rm":
            return self.rm(kwargs.get("id", ""), key=key)
        if action == "search":
            return {"hits": self.search(kwargs.get("q", ""), int(kwargs.get("limit", 8)))}
        raise ValueError(f"unknown scrape action: {action}")

    def test(self) -> bool:
        # url handling
        assert _unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") \
            == "https://example.com/a"
        assert _norm_url("https://Example.com/a/?utm_source=x#frag") == "https://example.com/a"
        assert _slug("How to write a SKILL.md!") == "how-to-write-a-skill-md"

        # html extraction: chrome dropped, links absolute
        r = _Reader("https://example.com/docs/")
        r.feed("<html><title>T</title><nav>menu junk</nav><body><p>Hello world</p>"
               "<a href='/docs/next'>Next guide</a><script>var x=1</script></body></html>")
        assert r.title == "T" and "Hello world" in r.text() and "menu junk" not in r.text()
        assert r.links == [("https://example.com/docs/next", "Next guide")]

        # link ranking prefers on-topic, same-host, docs-ish
        ranked = Scrapers._rank_links(
            [("https://other.com/x", "random"),
             ("https://example.com/docs/skills-guide", "skills guide")],
            "writing skills", "https://example.com/a")
        assert ranked[0] == "https://example.com/docs/skills-guide"

        # a draft parses out of the model's markdown, fence and all
        s = Scrapers(dir="/tmp/scrape-test")
        job = {"topic": "widget testing", "pages": [{"url": "https://example.com", "status": "ok"}]}
        draft = s._draft("```markdown\n---\nname: Widget Testing\ndescription: How to test widgets\n---\n"
                         "# Widgets\n\nBody text.\n```", job, "widget-testing")
        assert draft["name"] == "widget-testing"
        assert draft["description"] == "How to test widgets"
        assert draft["body"].startswith("# Widgets") and "scraped" in draft["tags"]

        # a run with neither topic nor seeds is a mistake, not an empty crawl
        try:
            s.start("")
            assert False, "empty scraper should refuse to start"
        except ValueError:
            pass
        return True
