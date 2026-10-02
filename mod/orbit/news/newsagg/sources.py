"""
newsagg.sources — every place a headline can come from, behind one shape.

A source is a function ``(query, limit) -> [Item]``. Adding one is adding a
function and a row in SOURCES; nothing else in the module knows source names.

All sources are keyless. Defaults lean on open/independent infrastructure
(GDELT, Hacker News, your own RSS feeds); the big-platform ones (google,
reddit) exist but are opt-in.
"""
from __future__ import annotations

import email.utils
import html
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional

from . import cache

UA = 'Mozilla/5.0 (X11; Linux x86_64) mod-news/0.1 (+local news aggregator)'
TIMEOUT = 12


# ───────────────────────────────────────────────────────────────── items

def item(title, url, source, ts=0, summary='', outlet='', score_hint=0.0) -> dict:
    """The one shape every source returns."""
    return {
        'title': clean(title)[:300],
        'url': url or '',
        'source': source,                       # which adapter found it
        'outlet': outlet or domain(url),        # who published it
        'ts': int(ts or 0),                     # unix seconds, 0 = unknown
        'summary': clean(summary)[:600],
        'hint': float(score_hint or 0),         # source-native popularity
    }


TAG = re.compile(r'<[^>]+>')


def clean(s) -> str:
    return re.sub(r'\s+', ' ', html.unescape(TAG.sub(' ', str(s or '')))).strip()


def domain(url: str) -> str:
    host = urllib.parse.urlsplit(url or '').hostname or ''
    return host[4:] if host.startswith('www.') else host


# ─────────────────────────────────────────────────────────────── fetching

def guard(url: str) -> str:
    """Refuse anything that is not public http(s) — the HTTP API passes
    caller-chosen URLs here, so this is the SSRF line."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError(f'not an http(s) url: {url!r}')
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f'cannot resolve {p.hostname}: {e}')
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f'{p.hostname} resolves to non-public {ip}')
    return url


def fetch(url: str, ttl: int = 600, accept: str = '*/*') -> bytes:
    """GET with an on-disk cache. A stale copy beats an error."""
    hit = cache.get(url, ttl)
    if hit is not None:
        return hit
    guard(url)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(4_000_000)
    except Exception:
        stale = cache.get(url, None)
        if stale is not None:
            return stale
        raise
    cache.put(url, body)
    return body


def fetch_json(url: str, ttl: int = 600):
    return json.loads(fetch(url, ttl, 'application/json'))


# ─────────────────────────────────────────────────────────────── RSS/Atom

def parse_feed(body: bytes, source: str) -> List[dict]:
    """RSS 2.0 and Atom, namespace-agnostic."""
    root = ET.fromstring(body)
    out = []
    for el in root.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag not in ('item', 'entry'):
            continue
        f = {c.tag.rsplit('}', 1)[-1]: c for c in el}
        link = f.get('link')
        url = ''
        if link is not None:
            url = (link.text or '').strip() or link.get('href', '')
        when = next((f[k] for k in ('pubDate', 'published', 'updated', 'date') if k in f), None)
        # Elements with no children are falsy — test against None, never `or`.
        title, desc, src = f.get('title'), f.get('description'), f.get('source')
        if desc is None:
            desc = f.get('summary')
        out.append(item(
            title=title.text if title is not None else '',
            url=url, source=source,
            ts=parse_date(when.text if when is not None else ''),
            summary=desc.text if desc is not None else '',
            outlet=src.text if src is not None and src.text else ''))
    return [i for i in out if i['title'] and i['url']]


def parse_date(s: str) -> int:
    """RFC 822 (RSS), ISO 8601 (Atom) or GDELT's 20261002T120000Z -> unix secs."""
    from datetime import datetime, timezone
    s = (s or '').strip()
    if not s:
        return 0
    for parse in (email.utils.parsedate_to_datetime,
                  datetime.fromisoformat,
                  lambda v: datetime.strptime(v, '%Y%m%dT%H%M%SZ')):
        try:
            d = parse(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return int(d.timestamp())
        except Exception:
            continue
    return 0


# ──────────────────────────────────────────────────────────────── sources

def _despace(t) -> str:
    """GDELT tokenises titles: 'costs $0 . 46 , up' -> 'costs $0.46, up'."""
    t = re.sub(r'(\d) ([.,]) (\d)', r'\1\2\3', str(t or ''))
    return re.sub(r" ([.,:;!?%')])", r'\1', t)


_gdelt_lock = threading.Lock()
_gdelt_last = [0.0]
GDELT_GAP = 5.5          # GDELT asks for one request per 5s per IP


def gdelt(query: str, limit: int) -> List[dict]:
    """GDELT DOC 2.0 — the open global news index, every language, keyless."""
    q = urllib.parse.urlencode({'query': query, 'mode': 'artlist', 'format': 'json',
                                'maxrecords': min(limit, 250), 'sort': 'datedesc',
                                'timespan': '7d'})
    url = f'https://api.gdeltproject.org/api/v2/doc/doc?{q}'
    if cache.get(url, 900) is None:
        with _gdelt_lock:                       # space our own calls out
            for attempt in (0, 1):
                time.sleep(max(0.0, GDELT_GAP - (time.time() - _gdelt_last[0])))
                _gdelt_last[0] = time.time()
                try:
                    fetch(url, ttl=900)
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 429 or attempt:
                        raise
    try:
        data = json.loads(fetch(url, ttl=900))
    except json.JSONDecodeError:
        return []        # GDELT answers bad queries (too short, odd chars) in plain text
    return [item(_despace(a.get('title')), a.get('url'), 'gdelt', parse_date(a.get('seendate', '')),
                 outlet=a.get('domain', '')) for a in data.get('articles', [])]


def hn(query: str, limit: int) -> List[dict]:
    """Hacker News via the Algolia search API."""
    q = urllib.parse.urlencode({'query': query, 'tags': 'story', 'hitsPerPage': min(limit, 100)})
    data = fetch_json(f'https://hn.algolia.com/api/v1/search_by_date?{q}')
    out = []
    for h in data.get('hits', []):
        disc = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append(item(h.get('title'), h.get('url') or disc, 'hn', h.get('created_at_i'),
                        summary=f"{h.get('points') or 0} points, {h.get('num_comments') or 0} comments",
                        score_hint=min(1.0, (h.get('points') or 0) / 300)))
    return out


def reddit(query: str, limit: int) -> List[dict]:
    q = urllib.parse.urlencode({'q': query, 'sort': 'new', 'limit': min(limit, 100), 't': 'week'})
    data = fetch_json(f'https://www.reddit.com/search.json?{q}')
    out = []
    for c in data.get('data', {}).get('children', []):
        d = c.get('data', {})
        url = d.get('url_overridden_by_dest') or f"https://www.reddit.com{d.get('permalink', '')}"
        out.append(item(d.get('title'), url, 'reddit', d.get('created_utc'),
                        summary=f"r/{d.get('subreddit')} · {d.get('score', 0)} score",
                        score_hint=min(1.0, (d.get('score') or 0) / 1000)))
    return out


def google(query: str, limit: int) -> List[dict]:
    q = urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
    return parse_feed(fetch(f'https://news.google.com/rss/search?{q}'), 'google')[:limit]


def feeds(query: str, limit: int) -> List[dict]:
    """Your own RSS/Atom list, pulled whole and filtered here by the query."""
    from . import store
    out = []
    for f in store.feeds():
        try:
            for i in parse_feed(fetch(f['url'], ttl=900), 'feeds'):
                i['outlet'] = f.get('name') or i['outlet']
                out.append(i)
        except Exception:
            continue
    terms = [t for t in re.findall(r'\w+', query.lower()) if len(t) > 1]
    if terms:
        out = [i for i in out
               if any(t in (i['title'] + ' ' + i['summary']).lower() for t in terms)]
    return out[:limit * 3]


Source = Callable[[str, int], List[dict]]

SOURCES: Dict[str, dict] = {
    'gdelt':  {'fn': gdelt,  'default': True,  'docs': 'GDELT global news index (open data, all languages)'},
    'hn':     {'fn': hn,     'default': True,  'docs': 'Hacker News stories (Algolia search)'},
    'feeds':  {'fn': feeds,  'default': True,  'docs': 'Your RSS/Atom feeds, filtered locally by the query'},
    'reddit': {'fn': reddit, 'default': False, 'docs': 'Reddit search (often blocks server IPs)'},
    'google': {'fn': google, 'default': False, 'docs': 'Google News RSS search (big-platform, opt-in)'},
}


def pick(names: Optional[List[str]] = None) -> List[str]:
    if not names or names == ['default']:
        return [n for n, s in SOURCES.items() if s['default']]
    if names == ['all']:
        return list(SOURCES)
    bad = [n for n in names if n not in SOURCES]
    if bad:
        raise ValueError(f'unknown source(s) {bad}; have {list(SOURCES)}')
    return list(names)
