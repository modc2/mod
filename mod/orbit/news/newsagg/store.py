"""
newsagg.store — the user's feed list, one JSON file next to the cache.

Seeded with a few public, non-paywalled outlets; edit freely.
"""
from __future__ import annotations

import json
import threading
from typing import List

from .cache import HOME

FEEDS = HOME / 'feeds.json'
_lock = threading.Lock()

DEFAULT_FEEDS = [
    {'name': 'BBC World', 'url': 'https://feeds.bbci.co.uk/news/world/rss.xml'},
    {'name': 'The Guardian', 'url': 'https://www.theguardian.com/world/rss'},
    {'name': 'Al Jazeera', 'url': 'https://www.aljazeera.com/xml/rss/all.xml'},
    {'name': 'NPR', 'url': 'https://feeds.npr.org/1001/rss.xml'},
    {'name': 'Ars Technica', 'url': 'https://feeds.arstechnica.com/arstechnica/index'},
    {'name': 'The Register', 'url': 'https://www.theregister.com/headlines.atom'},
]


def feeds() -> List[dict]:
    with _lock:
        if not FEEDS.exists():
            return [dict(f) for f in DEFAULT_FEEDS]
        return json.loads(FEEDS.read_text())


def _save(rows: List[dict]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = FEEDS.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows, indent=1))
    tmp.replace(FEEDS)


def add(url: str, name: str = '') -> dict:
    from .sources import guard, fetch, parse_feed
    guard(url)
    n = len(parse_feed(fetch(url, ttl=0), 'feeds'))   # prove it parses before keeping it
    rows = [f for f in feeds() if f['url'] != url]
    rows.append({'name': name or url, 'url': url})
    with _lock:
        _save(rows)
    return {'added': url, 'items': n, 'feeds': len(rows)}


def remove(url: str) -> dict:
    before = feeds()
    rows = [f for f in before if f['url'] != url and f.get('name') != url]
    with _lock:
        _save(rows)
    return {'removed': len(before) - len(rows), 'feeds': len(rows)}


def reset() -> dict:
    with _lock:
        if FEEDS.exists():
            FEEDS.unlink()
    return {'feeds': len(DEFAULT_FEEDS), 'reset': True}
