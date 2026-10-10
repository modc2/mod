"""
nyc.catalog — the ENTIRE open-data catalog, harvested to disk.

`nyc_find_datasets` used to answer every search with a live call to Socrata's
Discovery API — fine for one query, but it meant search was online-only, rate
limited, and only ever saw the slice the API chose to return. This module
instead harvests the full dataset catalog of a portal (every dataset's
metadata: ~2,400 on data.cityofnewyork.us, ~1,000 on data.ny.gov) into one
disk-cache entry per domain, and searches it offline with simple token
scoring. A whole-corpus search over a few thousand small dicts is
single-digit milliseconds in Python; no index needed.

Harvesting pages the Discovery API 100 datasets at a time until a page
comes back short — about 25 requests for the city portal, ~30s cold. The
result is under a MB of JSON per domain in the module's normal cache
(`~/.mod/nyc/cache`), TTL 7 days, with the usual stale-beats-error
behaviour from ``sources.cached``.

Paging is by ``offset``, NOT ``scroll_id``, and that is a measured choice:
walking this very portal with ``scroll_id`` (passing the last result's
resource id) terminated after ~1,750 of 2,404 datasets — the scroll walks
an id-ordered cursor that silently skips entries — while offset paging
returned all 2,404 distinct ids. Discovery caps offsets at 10k, which is
3x the size of both portals put together.

One honest limitation: the Discovery API does not return row counts in its
result objects (``rows_size`` is absent), so ``rows`` is carried only when
the API happens to provide it and ``stats()`` reports how many datasets it
is actually known for.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import requests

from . import sources as S

DISCOVERY = 'https://api.us.socrata.com/api/catalog/v1'
DOMAINS = {'nyc': 'data.cityofnewyork.us', 'nys': 'data.ny.gov'}

TTL = 7 * S.DAY
PAGE = 100
# Hard stop at 15k datasets — both portals are well under 3k; this only
# guards against a paging bug turning into an infinite crawl.
MAX_PAGES = 150

DESC_CHARS = 300


def domain_of(name: str) -> str:
    key = str(name or 'nyc').strip().lower()
    return DOMAINS.get(key, key or DOMAINS['nyc'])


def _key(dom: str) -> str:
    return f'catalog-full-{dom}'


def _entry(hit: dict, dom: str) -> dict:
    res = hit.get('resource') or {}
    cls = hit.get('classification') or {}
    desc = re.sub(r'<[^>]+>', '', ' '.join((res.get('description') or '').split()))
    if len(desc) > DESC_CHARS:
        desc = desc[:DESC_CHARS].rsplit(' ', 1)[0] + '…'
    return {
        'id': res.get('id'),
        'name': res.get('name') or '',
        'description': desc,
        'category': cls.get('domain_category') or '',
        'updated': str(res.get('data_updated_at') or '')[:10],
        'rows': res.get('rows_size') or None,
        'url': f'https://{dom}/d/{res.get("id")}',
    }


def _fetch(dom: str) -> dict:
    datasets: List[dict] = []
    seen: set = set()
    offset = 0
    for _ in range(MAX_PAGES):
        r = requests.get(DISCOVERY,
                         params={'domains': dom, 'search_context': dom,
                                 'only': 'dataset', 'limit': PAGE,
                                 'offset': offset},
                         timeout=60, headers={'User-Agent': S.USER_AGENT})
        r.raise_for_status()
        results = r.json().get('results', [])
        for h in results:
            e = _entry(h, dom)
            if e['id'] and e['id'] not in seen:
                seen.add(e['id'])
                datasets.append(e)
        offset += len(results)
        if len(results) < PAGE:
            break
    if not datasets:
        raise ValueError(f'Discovery API returned no datasets for {dom}')
    return {'domain': dom,
            'harvested': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'count': len(datasets),
            'datasets': datasets}


def harvest(domain: str = 'nyc', refresh: bool = False) -> dict:
    """The full catalog for one portal, from cache or freshly crawled."""
    dom = domain_of(domain)
    if refresh:
        return S.cache_write(_key(dom), _fetch(dom))
    return S.cached(_key(dom), TTL, lambda: _fetch(dom))


def has_harvest(domain: str = 'nyc') -> bool:
    """True if a harvest exists on disk at all — stale counts (offline search)."""
    return S.cache_read(_key(domain_of(domain)), None) is not None


# ─────────────────────────────────────────────────────────────────────────────
# offline search
# ─────────────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r'[a-z0-9]+')


def _tokens(s: str) -> List[str]:
    return _TOKEN.findall((s or '').lower())


def _score(d: dict, ql: str, qtokens: List[str]) -> int:
    """
    Rank a dataset against the query. Name beats category beats description:
    a user typing "evictions" wants the dataset *named* Evictions, not every
    file whose blurb mentions the word.
    """
    name = (d['name'] or '').lower()
    name_tokens = set(_tokens(name))
    score = 0
    if ql and ql == name:
        score += 100                      # exact name
    elif ql and len(ql) >= 4 and ql in name:
        score += 30                       # query is a phrase inside the name
    cat_tokens = set(_tokens(d.get('category', '')))
    desc = (d.get('description') or '').lower()
    desc_tokens = set(_tokens(desc))
    for t in qtokens:
        # Exact token matches count at any length ("311", "dob"); fuzzy
        # substring/prefix matches need 4+ chars or "rat" hits "Ratings".
        if t in name_tokens:
            score += 12
        elif len(t) >= 4 and any(nt.startswith(t) for nt in name_tokens):
            score += 6                    # "evict" hits "evictions"
        if t in cat_tokens:
            score += 5
        elif t in desc_tokens or (len(t) >= 4 and t in desc):
            score += 2
    return score


def search(q: str, domain: str = 'nyc', category: str = '',
           limit: int = 15) -> dict:
    """Offline token-scored search over the harvested catalog."""
    cat = harvest(domain)
    ql = str(q or '').strip().lower()
    qtokens = _tokens(ql)
    catq = str(category or '').strip().lower()
    scored = []
    for d in cat['datasets']:
        if catq and catq not in (d.get('category') or '').lower():
            continue
        s = _score(d, ql, qtokens) if qtokens else 1
        if s > 0:
            scored.append((s, d))
    # Equal scores break on data freshness: for "311" the living Service
    # Requests file should outrank a 2014 survey. Both sorts are stable.
    scored.sort(key=lambda x: x[1].get('updated') or '', reverse=True)
    scored.sort(key=lambda x: -x[0])
    limit = max(1, min(int(limit), 50))
    hits = [{**d, 'score': s} for s, d in scored[:limit]]
    return {'domain': cat['domain'], 'query': str(q), 'count': len(hits),
            'matched': len(scored), 'cataloged': cat['count'],
            'search': 'local catalog (harvested '
                      f"{cat.get('harvested', '?')[:10]})",
            'datasets': hits}


def search_local(q: str, domain: str = 'nyc', limit: int = 15) -> Optional[dict]:
    """``search`` only if a harvest already exists on disk; else None.

    This is what lets ``nyc_find_datasets`` stay fast: it must never block a
    first-time search for ~35s crawling the catalog — the live Discovery API
    handles that case and ``nyc_catalog refresh=true`` builds the harvest.
    """
    if not has_harvest(domain):
        return None
    try:
        return search(q, domain, limit=limit)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# stats and browsing
# ─────────────────────────────────────────────────────────────────────────────

def stats(domain: str = 'nyc') -> dict:
    """What the harvest holds: counts by category, row totals, freshness."""
    cat = harvest(domain)
    by_cat: Counter = Counter()
    rows_total, rows_known = 0, 0
    newest = ''
    for d in cat['datasets']:
        by_cat[d.get('category') or '(uncategorized)'] += 1
        if d.get('rows'):
            rows_total += int(d['rows'])
            rows_known += 1
        newest = max(newest, d.get('updated') or '')
    age = S.cache_age(_key(domain_of(domain)))
    return {
        'domain': cat['domain'],
        'datasets': cat['count'],
        'by_category': [{'category': c, 'datasets': n}
                        for c, n in by_cat.most_common()],
        'rows_cataloged': rows_total or None,
        'rows_known_for': rows_known,
        'newest_data_update': newest or None,
        'harvested': cat.get('harvested'),
        'harvest_age_hours': round(age / 3600, 1) if age is not None else None,
        'source': f'Socrata Discovery API over {cat["domain"]}',
        'note': ('Search this catalog offline with nyc_find_datasets; '
                 'pass category to nyc_catalog to browse one category.'),
    }


def browse(domain: str = 'nyc', category: str = '', limit: int = 25) -> dict:
    """Datasets in one category, most recently updated first."""
    cat = harvest(domain)
    catq = str(category or '').strip().lower()
    hits = [d for d in cat['datasets']
            if catq in (d.get('category') or '').lower()]
    hits.sort(key=lambda d: d.get('updated') or '', reverse=True)
    limit = max(1, min(int(limit), 100))
    return {'domain': cat['domain'], 'category': category,
            'count': len(hits), 'datasets': hits[:limit]}
