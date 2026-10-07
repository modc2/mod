"""
nyc.news — New York City news, from key-free public feeds.

Two kinds of source, both free and unauthenticated:

  * RSS feeds of NYC newsrooms (Gothamist, THE CITY, NYT Metro). Parsed with
    the stdlib ElementTree — a feed is a few dozen <item>s, not worth a
    dependency. Feeds that 403 bots (City Limits) or serve HTML at their
    "RSS" URL (NY1) are deliberately absent.
  * GDELT's document API for topic search beyond what the feeds carry.
    GDELT rate-limits to one request per 5 seconds and answers a *plain-text
    scolding with HTTP 200* when throttled — the JSON parse failing IS the
    throttle signal, and the stale cache is the right answer then.

Headlines are merged, de-duplicated by normalized title, tagged with a crude
topic (housing / crime / transit / government) so the atlas can relate them
to its data layers, and cached for 15 minutes.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

from . import sources as S

FEEDS = [
    {'name': 'Gothamist', 'url': 'https://gothamist.com/feed'},
    {'name': 'THE CITY', 'url': 'https://www.thecity.nyc/feed/'},
    {'name': 'NYT Metro', 'url': 'https://rss.nytimes.com/services/xml/rss/nyt/NYRegion.xml'},
]

GDELT = 'https://api.gdeltproject.org/api/v2/doc/doc'
TTL = 15 * 60

# word → topic; first match wins, checked against title + summary
TOPICS = [
    ('housing', re.compile(r'\b(housing|rent|rents|rental|tenant|landlord|evict|'
                           r'afford|apartment|real estate|homeless|shelter|zoning|'
                           r'development|condo|co-op|nycha)\b', re.I)),
    ('crime', re.compile(r'\b(crime|police|nypd|shooting|arrest|murder|robbery|'
                         r'assault|gun|stabbing|theft|prosecut|indict)\b', re.I)),
    ('transit', re.compile(r'\b(subway|mta|bus|transit|congestion pricing|bike lane|'
                           r'traffic|train|ferry|lirr|metro-north|e-bike)\b', re.I)),
    ('government', re.compile(r'\b(mayor|council|albany|governor|city hall|budget|'
                              r'election|agency|comptroller|legislation|bill)\b', re.I)),
]


def _topic(text: str) -> str:
    for name, rx in TOPICS:
        if rx.search(text):
            return name
    return ''


def _clean(s: Optional[str]) -> str:
    s = re.sub(r'<[^>]+>', ' ', _html.unescape(s or ''))
    return re.sub(r'\s+', ' ', s).strip()


def _when(s: Optional[str]) -> Optional[str]:
    """RFC-822 or ISO date → UTC ISO minute, else None."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()[:16]


def _parse_rss(xml_text: str, source: str) -> List[dict]:
    """<item>s (RSS) or <entry>s (Atom) → headline dicts. Bad feed → []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    out = []
    items = root.iter('item')
    for it in items:
        title = _clean(it.findtext('title'))
        link = (it.findtext('link') or '').strip()
        if not title or not link:
            continue
        summary = _clean(it.findtext('description'))[:300]
        out.append({'title': title, 'url': link, 'source': source,
                    'published': _when(it.findtext('pubDate')),
                    'summary': summary,
                    'topic': _topic(f'{title} {summary}')})
    if out:
        return out
    for it in root.iter('{http://www.w3.org/2005/Atom}entry'):
        title = _clean(it.findtext('atom:title', namespaces=ns))
        link_el = it.find('atom:link', ns)
        link = (link_el.get('href') if link_el is not None else '') or ''
        if not title or not link:
            continue
        summary = _clean(it.findtext('atom:summary', namespaces=ns))[:300]
        out.append({'title': title, 'url': link, 'source': source,
                    'published': _when(it.findtext('atom:updated', namespaces=ns)),
                    'summary': summary,
                    'topic': _topic(f'{title} {summary}')})
    return out


def _fetch_feed(feed: dict) -> List[dict]:
    r = requests.get(feed['url'], timeout=20,
                     headers={'User-Agent': S.USER_AGENT})
    r.raise_for_status()
    return _parse_rss(r.text, feed['name'])


def _dedupe(items: List[dict]) -> List[dict]:
    seen, out = set(), []
    for it in items:
        key = re.sub(r'\W+', '', it['title'].lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def headlines(topic: str = '', limit: int = 40) -> Dict[str, Any]:
    """
    The current NYC news picture from every feed, newest first.

    ``topic`` filters to one of housing / crime / transit / government
    (the crude tagger above — a story about a subway shooting is tagged with
    whichever pattern matched first, so filters are a lens, not the truth).
    """
    def fetch():
        items: List[dict] = []
        errors: List[str] = []
        for feed in FEEDS:
            try:
                items += _fetch_feed(feed)
            except Exception as e:      # one dead feed must not kill the rest
                errors.append(f'{feed["name"]}: {type(e).__name__}')
        items = _dedupe(items)
        items.sort(key=lambda x: x.get('published') or '', reverse=True)
        return {'fetched': datetime.now(timezone.utc).isoformat()[:16],
                'sources': [f['name'] for f in FEEDS],
                'errors': errors, 'items': items}

    data = S.cached('news-feeds-v1', TTL, fetch)
    items = data['items']
    want = str(topic or '').strip().lower()
    if want:
        items = [x for x in items if x['topic'] == want]
    limit = max(1, min(int(limit), 200))
    return {**data, 'topic': want or 'all', 'count': len(items[:limit]),
            'total': len(data['items']), 'items': items[:limit]}


_last_gdelt = [0.0]


def search(q: str, days: int = 7, limit: int = 25) -> Dict[str, Any]:
    """
    Search recent news coverage of any NYC topic through GDELT.

    The query is always scoped to New York City. GDELT's throttle answer is
    plain text with HTTP 200, so a failed JSON parse falls back to the cache
    like any other upstream failure.
    """
    q = str(q or '').strip()
    if not q:
        raise ValueError('empty query')
    days = max(1, min(int(days), 90))
    limit = max(1, min(int(limit), 75))

    def fetch():
        # ≥5s between calls, process-wide: GDELT's published rate limit.
        wait = _last_gdelt[0] + 5.5 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_gdelt[0] = time.time()
        r = requests.get(GDELT, params={
            'query': f'"new york city" {q}',
            'mode': 'artlist', 'format': 'json',
            'maxrecords': limit, 'timespan': f'{days}d',
            'sort': 'datedesc'},
            timeout=30, headers={'User-Agent': S.USER_AGENT})
        r.raise_for_status()
        data = json.loads(r.text)      # throttle page is text/200 → ValueError
        arts = []
        for a in data.get('articles', []):
            arts.append({'title': _clean(a.get('title')),
                         'url': a.get('url'),
                         'source': a.get('domain'),
                         'published': _when(a.get('seendate')),
                         'topic': _topic(_clean(a.get('title')))})
        return {'query': q, 'days': days, 'count': len(arts),
                'articles': _dedupe(arts),
                'attribution': 'GDELT Project (gdeltproject.org)'}

    try:
        return S.cached(f'news-gdelt-{q.lower()[:60]}-{days}-{limit}', 3600, fetch)
    except Exception as e:
        # GDELT throttles aggressively and holds a grudge. Rather than erroring,
        # answer from the newsroom feeds we already hold, filtered to the query.
        words = [w for w in re.findall(r'\w+', q.lower()) if len(w) > 2]
        feed = headlines(limit=200)
        hits = [x for x in feed['items']
                if any(w in f"{x['title']} {x.get('summary', '')}".lower()
                       for w in words)] if words else []
        return {'query': q, 'days': days, 'count': len(hits[:limit]),
                'articles': hits[:limit],
                'note': (f'GDELT search unavailable ({type(e).__name__}); '
                         'these are matching stories from the NYC newsroom '
                         'feeds instead.')}
