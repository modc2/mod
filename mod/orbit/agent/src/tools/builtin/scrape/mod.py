"""scrape - search the web and crawl what it finds, in one call

fetch reads one URL and search lists results; scrape is both plus the walk in
between. Give it a query and it searches, or give it URLs and it starts there,
then reads each page and follows the most on-topic links for `depth` hops.

It rides the scrape module's crawler, so it is polite the same way: robots.txt
per host, one request at a time to a host with a gap between, and a hard page
budget. What comes back is the pages themselves (url, title, readable text),
not a distilled skill. That is the scrape module's job.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PAGES_CAP = 15
CHARS_CAP = 8000

_crawler = None


def _scrapers():
    """The scrape module's Scrapers class. Builtins load by file path, so a
    relative import has no package to resolve against. Reuse the copy the API
    already imported, else import it through the agent's `src` package."""
    target = (Path(__file__).resolve().parents[3] / 'scrape' / 'mod.py')
    for m in list(sys.modules.values()):
        f = getattr(m, '__file__', None)
        if f and Path(f).resolve() == target and hasattr(m, 'Scrapers'):
            return m
    root = str(target.parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from src.scrape import mod
    return mod


def _get():
    global _crawler
    if _crawler is None:
        s = _scrapers()
        _crawler = s.Scrapers(dir=str(Path.home() / '.mod' / 'agent' / 'scrape' / 'tool'))
    return _crawler


class Tool:
    description = ("Scrape the web: search a query (or start from given URLs), read each page, "
                   "follow on-topic links up to `depth` hops, and return every page's text. "
                   "Polite: honors robots.txt and rate-limits per host.")

    def forward(self, query: str = None, urls: List[str] = None, depth: int = 1,
                max_pages: int = 5, max_chars: int = 3000, **kwargs) -> Dict[str, Any]:
        """Crawl the web and return what was read.

        Args:
            query: what to search for; its top results become the seeds
            urls: start from these URLs instead of (or as well as) a search
            depth: link hops to follow past the seeds (0 = seeds only, max 2)
            max_pages: pages to read in total (max 15)
            max_chars: text kept per page (max 8000)
        """
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.replace(',', ' ').split() if u.strip()]
        urls = [u for u in (urls or []) if u.startswith(('http://', 'https://'))]
        query = (query or '').strip()
        if not query and not urls:
            return {"success": False, "error": "give a query or at least one http(s) url"}
        depth = max(0, min(int(depth or 0), 2))
        max_pages = max(1, min(int(max_pages or 5), PAGES_CAP))
        max_chars = max(200, min(int(max_chars or 3000), CHARS_CAP))
        try:
            c = _get()
        except Exception as e:
            return {"success": False, "error": f"crawler unavailable: {e}"}

        results: List[Dict] = []
        if query:
            try:
                results = c.search(query, limit=max(3, min(max_pages, 8)))
            except Exception as e:
                if not urls:
                    return {"success": False, "query": query, "error": f"search failed: {e}"}
        seeds = list(dict.fromkeys(urls + [r["url"] for r in results]))
        topic = query or ' '.join(urls)

        s = _scrapers()
        frontier = [(u, 0) for u in seeds]
        seen = {s._norm_url(u) for u in seeds}
        pages: List[Dict] = []
        skipped: List[Dict] = []
        while frontier and len(pages) < max_pages:
            url, hop = frontier.pop(0)
            try:
                if not c._allowed(url):
                    skipped.append({"url": url, "why": "robots.txt"})
                    continue
                page = c._page(url)
            except Exception as e:
                skipped.append({"url": url, "why": str(e)[:120]})
                continue
            text = page["text"].strip()
            if len(text) < 100:
                skipped.append({"url": url, "why": "no readable text"})
                continue
            pages.append({"url": page["url"], "title": page["title"][:160], "depth": hop,
                          "text": text[:max_chars], "truncated": len(text) > max_chars})
            if hop < depth:
                for link in c._rank_links(page["links"], topic, page["url"])[:max_pages]:
                    k = s._norm_url(link)
                    if k not in seen:
                        seen.add(k)
                        frontier.append((link, hop + 1))

        return {"success": bool(pages), "query": query or None,
                "results": [{"url": r["url"], "title": r["title"], "snippet": r["snippet"]}
                            for r in results],
                "pages": pages, "skipped": skipped[:20], "read": len(pages),
                **({} if pages else {"error": "nothing readable was reached"})}

    def test(self):
        r = self.forward()
        assert not r["success"]
        return True
