"""
newsagg.engine — query in, one ranked de-duplicated list out.

    fan out to sources (parallel, each with its own failure)
      -> drop items outside the time window
      -> cluster duplicates (same canonical URL, or near-identical title)
      -> score = relevance + recency + coverage + popularity
      -> top k

Ranking is all local arithmetic: no model, no key, explainable per item
(each result carries its `why`).
"""
from __future__ import annotations

import math
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Dict, List, Optional

from . import sources as S

W_REL, W_NEW, W_COV, W_POP = 0.50, 0.30, 0.15, 0.05
HALF_LIFE_H = 24.0
STOP = set('a an and are as at be by for from has in is it its of on or that the to was were will with'.split())
TRACKING = re.compile(r'^(utm_|fbclid|gclid|mc_|ref$|cmpid|ocid|smid)')


def tokens(s: str) -> List[str]:
    return [t for t in re.findall(r'\w+', (s or '').lower()) if t not in STOP and len(t) > 1]


def canonical(url: str) -> str:
    p = urllib.parse.urlsplit(url or '')
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k)]
    host = (p.hostname or '').removeprefix('www.').removeprefix('m.')
    return f"{host}{p.path.rstrip('/')}?{urllib.parse.urlencode(q)}".rstrip('?')


def similar(a: set, b: set) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= 0.7


# ──────────────────────────────────────────────────────────────── search

def search(query: str, k: int = 20, sources: Optional[List[str]] = None,
           hours: float = 72, per_source: int = 40) -> dict:
    query = (query or '').strip()
    if not query:
        raise ValueError('query is required')
    names = S.pick(sources)
    raw: List[dict] = []
    report: Dict[str, dict] = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        jobs = {pool.submit(S.SOURCES[n]['fn'], query, per_source): n for n in names}
        for fut in as_completed(jobs):
            n = jobs[fut]
            try:
                got = fut.result()
                raw += got
                report[n] = {'ok': True, 'items': len(got)}
            except Exception as e:
                report[n] = {'ok': False, 'error': f'{type(e).__name__}: {e}'[:200]}

    now = time.time()
    if hours and hours > 0:
        raw = [i for i in raw if not i['ts'] or now - i['ts'] <= hours * 3600]

    clusters = cluster(raw)
    q = set(tokens(query))
    ranked = sorted((score(c, q, now) for c in clusters), key=lambda r: -r['score'])
    if q:
        ranked = [r for r in ranked if r['rel'] > 0] or ranked
    return {
        'query': query, 'sources': report, 'fetched': len(raw), 'unique': len(clusters),
        'ms': int((time.time() - t0) * 1000), 'results': ranked[:int(k)],
    }


def cluster(items: List[dict]) -> List[List[dict]]:
    """Group the same story across sources. O(n^2) on titles — fine for the
    few hundred items one query returns."""
    by_url: Dict[str, int] = {}
    groups: List[List[dict]] = []
    toks: List[set] = []
    for it in items:
        cu, tt = canonical(it['url']), set(tokens(it['title']))
        idx = by_url.get(cu)
        if idx is None:
            idx = next((g for g, gt in enumerate(toks) if similar(tt, gt)), None)
        if idx is None:
            groups.append([it])
            toks.append(tt)
            idx = len(groups) - 1
        else:
            groups[idx].append(it)
        by_url[cu] = idx
    return groups


def score(group: List[dict], q: set, now: float) -> dict:
    # Prefer the copy with the most information as the representative.
    lead = max(group, key=lambda i: (bool(i['ts']), len(i['summary']), -len(i['url'])))
    words = tokens(lead['title'] + ' ' + lead['summary'])
    title = set(tokens(lead['title']))
    rel = 0.0
    if q:
        hits_title = len(q & title) / len(q)
        hits_body = len(q & set(words)) / len(q)
        rel = 0.7 * hits_title + 0.3 * hits_body
    ts = max((i['ts'] for i in group), default=0)
    age_h = (now - ts) / 3600 if ts else 24 * 7
    new = math.pow(0.5, max(0.0, age_h) / HALF_LIFE_H)
    outlets = {i['outlet'] for i in group}
    cov = min(1.0, (len(outlets) - 1) / 4)
    pop = max(i['hint'] for i in group)
    s = W_REL * rel + W_NEW * new + W_COV * cov + W_POP * pop
    return {
        **lead, 'ts': ts, 'score': round(s, 4), 'rel': round(rel, 3),
        'age_h': round(age_h, 1) if ts else None,
        'also': sorted({(i['outlet'], i['url']) for i in group if i['url'] != lead['url']})[:6],
        'found_by': sorted({i['source'] for i in group}),
        'why': f'rel {rel:.2f} · fresh {new:.2f} · {len(outlets)} outlet(s) · pop {pop:.2f}',
    }


# ───────────────────────────────────────────────────────────────── read

class _Text(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'aside', 'form', 'svg', 'figure'}
    BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote', 'pre'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.title, self.paras, self._buf, self._in = 0, '', [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag in self.BLOCK or tag == 'title':
            self._flush()
            self._in = tag

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag == self._in:
            self._flush()

    def handle_data(self, data):
        if not self.depth and self._in:
            self._buf.append(data)

    def _flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self._buf)).strip()
        if self._in == 'title' and not self.title:
            self.title = text
        elif text and (self._in != 'p' or len(text) > 40):
            self.paras.append(text)
        self._buf, self._in = [], None


def read(url: str, max_chars: int = 8000) -> dict:
    """Fetch an article and return its readable text (stdlib heuristic:
    paragraphs outside nav/header/footer). Good enough for an agent to
    summarise; not a full readability port."""
    body = S.fetch(url, ttl=3600, accept='text/html')
    head = body[:512].lstrip().lower()
    if head.startswith(b'%pdf') or (b'<' not in head and b'\x00' in body[:512]):
        raise ValueError('not an HTML article (pdf/binary) — open the url directly')
    p = _Text()
    p.feed(body.decode('utf-8', 'replace'))
    p._flush()
    text = '\n\n'.join(p.paras)
    return {'url': url, 'outlet': S.domain(url), 'title': p.title,
            'chars': len(text), 'truncated': len(text) > max_chars, 'text': text[:int(max_chars)]}
