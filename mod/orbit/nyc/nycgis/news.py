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

import hashlib
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
from .realestate import norm_name

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


# ─────────────────────────────────────────────────────────────────────────────
# news on the map
#
# Headlines carry no coordinates, so they are pinned by the place they *name*:
# a gazetteer of neighborhood names (from the NTA polygons already in the
# cache), boroughs, and the handful of landmarks newsrooms actually write.
# Stories that name no place are counted in meta rather than drawn — a dot
# invented for an un-placed story would be a lie on a map.
# ─────────────────────────────────────────────────────────────────────────────

BOROUGH_CENTER = {
    'Manhattan': (-73.9712, 40.7764), 'Brooklyn': (-73.9496, 40.6501),
    'Queens': (-73.8014, 40.7282), 'Bronx': (-73.8648, 40.8448),
    'Staten Island': (-74.1502, 40.5795),
}

# Places that headline constantly but are not NTA names (or whose NTA name
# nobody writes). lng/lat.
LANDMARKS = {
    'times square': (-73.9855, 40.7580), 'central park': (-73.9665, 40.7812),
    'city hall': (-74.0064, 40.7127), 'wall street': (-74.0088, 40.7069),
    'world trade center': (-74.0134, 40.7118), 'penn station': (-73.9935, 40.7506),
    'grand central': (-73.9772, 40.7527), 'rikers island': (-73.8860, 40.7932),
    'jfk': (-73.7781, 40.6413), 'laguardia': (-73.8740, 40.7769),
    'yankee stadium': (-73.9262, 40.8296), 'citi field': (-73.8458, 40.7571),
    'madison square garden': (-73.9934, 40.7505),
    'barclays center': (-73.9754, 40.6826),
    'columbia university': (-73.9626, 40.8075), 'nyu': (-73.9965, 40.7295),
    'prospect park': (-73.9690, 40.6602), 'coney island': (-73.9772, 40.5755),
    'brooklyn bridge': (-73.9969, 40.7061), 'union square': (-73.9904, 40.7359),
    'bryant park': (-73.9832, 40.7536), 'lincoln center': (-73.9830, 40.7725),
    'port authority': (-73.9899, 40.7570), 'ellis island': (-74.0397, 40.6995),
    'statue of liberty': (-74.0445, 40.6892), 'high line': (-74.0048, 40.7480),
    'hudson yards': (-74.0014, 40.7540), 'roosevelt island': (-73.9510, 40.7614),
    'governors island': (-74.0169, 40.6895),
}

# Single words too generic to pin a story on their own, even though they occur
# inside NTA names ("Midtown-Times Square" is fine; a bare "park" is not).
_GAZETTEER_STOP = {
    'the', 'new', 'york', 'city', 'north', 'south', 'east', 'west', 'central',
    'park', 'parks', 'hill', 'hills', 'heights', 'island', 'islands', 'beach',
    'bay', 'point', 'square', 'garden', 'gardens', 'village', 'town', 'green',
    'ferry', 'port', 'college', 'cemetery', 'etc', 'north shore', 'south shore',
}


def _ring_center(geom: dict):
    """Bbox midpoint of the largest ring — cheap, and right for a label pin."""
    t, c = geom.get('type'), geom.get('coordinates')
    rings = [c[0]] if t == 'Polygon' else [p[0] for p in c] if t == 'MultiPolygon' else []
    best, size = None, -1.0
    for ring in rings:
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > size:
            size = area
            best = (round((min(xs) + max(xs)) / 2, 5),
                    round((min(ys) + max(ys)) / 2, 5))
    return best


def _gazetteer() -> List[dict]:
    """
    Name → pin, longest names first (so "East Harlem" wins over "Harlem").
    Each entry: {name, lng, lat, place, precision}.
    """
    def build():
        from . import layers as L       # deferred: layers imports us
        out: Dict[str, dict] = {}

        def put(name: str, lng: float, lat: float, place: str, precision: str):
            key = norm_name(name)
            if len(key) < 4 or key in _GAZETTEER_STOP or key in out:
                return
            out[key] = {'name': key, 'lng': lng, 'lat': lat,
                        'place': place, 'precision': precision}

        for f in L.neighborhoods()['features']:
            p = f['properties']
            center = _ring_center(f['geometry'])
            if not center:
                continue
            full = str(p.get('ntaname') or '')
            # "Astoria (North)-Ditmars-Steinway" → the whole name, then each
            # hyphen part, so both "Ditmars" and the full compound pin here.
            parts = [full] + re.split(r'-', re.sub(r'\(.*?\)', ' ', full))
            for part in parts:
                put(part, center[0], center[1], full, 'neighborhood')
        for name, (lng, lat) in LANDMARKS.items():
            put(name, lng, lat, name.title(), 'landmark')
        for boro, (lng, lat) in BOROUGH_CENTER.items():
            put(boro, lng, lat, boro, 'borough')
            put('the ' + boro.lower(), lng, lat, boro, 'borough')
        return sorted(out.values(), key=lambda e: -len(e['name']))
    # v2: keys normalized with realestate.norm_name (shared with match_nta)
    return S.cached('news-gazetteer-v2', 7 * S.DAY, build)


def _place(text: str, gazetteer: List[dict]) -> Optional[dict]:
    """The most specific place a story names: longest match, borough last."""
    t = f' {norm_name(text)} '
    hit = None
    for e in gazetteer:
        if f' {e["name"]} ' not in t:
            continue
        # Entries are longest-first, so the first non-borough hit is the best
        # one; a borough only sticks if nothing finer ever matches.
        if e['precision'] != 'borough':
            return e
        hit = hit or e
    return hit


def _jitter(title: str, scale: float) -> tuple:
    """Deterministic offset so co-located stories fan out instead of stacking."""
    h = hashlib.sha1(title.encode()).digest()
    return ((h[0] / 255 - 0.5) * scale, (h[1] / 255 - 0.5) * scale)


def points(limit: int = 150) -> Dict[str, Any]:
    """
    The current headlines as a GeoJSON layer, each pinned to the neighborhood,
    landmark or borough it names. Borough pins are approximate by nature and
    say so in their ``precision`` property.
    """
    limit = max(1, min(int(limit), 200))

    def build():
        data = headlines(limit=200)
        gaz = _gazetteer()
        feats, unplaced = [], 0
        for it in data['items']:
            if len(feats) >= limit:
                break
            hit = _place(f"{it['title']} {it.get('summary', '')}", gaz)
            if not hit:
                unplaced += 1
                continue
            dx, dy = _jitter(it['title'], 0.025 if hit['precision'] == 'borough' else 0.004)
            feats.append({
                'type': 'Feature',
                'geometry': {'type': 'Point',
                             'coordinates': [round(hit['lng'] + dx, 5),
                                             round(hit['lat'] + dy, 5)]},
                'properties': {
                    'title': it['title'], 'url': it['url'], 'source': it['source'],
                    'published': it.get('published'),
                    'summary': it.get('summary', '')[:200],
                    'topic': it.get('topic') or 'other',
                    'place': hit['place'], 'precision': hit['precision'],
                },
            })
        return {'type': 'FeatureCollection', 'features': feats,
                'meta': {'fetched': data['fetched'], 'placed': len(feats),
                         'unplaced': unplaced,
                         'sources': data.get('sources', []),
                         'note': ('Stories are pinned to the place they name; a '
                                  'story that names no NYC place is not drawn. '
                                  'Borough-level pins are approximate.')}}

    return S.cached(f'news-points-v1-{limit}', TTL, build)


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
