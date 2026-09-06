"""sites — the scraper adapters behind the memes module.

Four sources, no API keys:

    reddit        the meme subreddits' public JSON (search + hot). The one
                  source with real velocity; scores are upvotes.
    imgflip       the public template list (api.imgflip.com/get_memes) —
                  the blank canvases everything else is captioned from.
    knowyourmeme  the encyclopedia. Scraped from its search page HTML;
                  answers "what IS this meme", not "show me a fresh one".
    ninegag       9GAG's undocumented search-posts JSON.

Every adapter returns the same shape and NEVER raises out of the fan-out:
a source that is down, blocking this IP, or has changed its HTML reports
itself in ``errors`` and the others still answer.

The normalized meme::

    {source, id, title, url, image, score, author, nsfw, ts}

``url`` is the page a human opens; ``image`` is the direct file an <img>
tag loads (may be '' for a Know Your Meme entry whose thumb didn't parse).
"""

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = 'mod-memes/0.1 (meme search; +https://modc2.com/memes)'
TIMEOUT = 12

SOURCES = ['reddit', 'imgflip', 'knowyourmeme', 'ninegag']

# The subreddits the reddit adapter fans across when none is named.
SUBS = ['memes', 'dankmemes', 'me_irl', 'wholesomememes', 'AdviceAnimals',
        'ProgrammerHumor', 'HistoryMemes', 'terriblefacebookmemes']

IMG_RE = re.compile(r'\.(jpe?g|png|gif|webp)(\?|$)', re.I)


def _get(url, accept='application/json'):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode('utf-8', 'replace')


def _get_json(url):
    return json.loads(_get(url))


def _meme(source, id, title, url, image, score=0, author='', nsfw=False, ts=0):
    return {'source': source, 'id': str(id), 'title': title.strip(),
            'url': url, 'image': image, 'score': int(score or 0),
            'author': author or '', 'nsfw': bool(nsfw), 'ts': int(ts or 0)}


# ── reddit ───────────────────────────────────────────────────────────

def _reddit_posts(data, nsfw):
    out = []
    for child in data.get('data', {}).get('children', []):
        p = child.get('data', {})
        if p.get('over_18') and not nsfw:
            continue
        image = p.get('url_overridden_by_dest') or p.get('url') or ''
        if not IMG_RE.search(image):
            # galleries and videos: fall back to the preview still if any
            try:
                image = html.unescape(
                    p['preview']['images'][0]['source']['url'])
            except Exception:
                continue
        out.append(_meme('reddit', p.get('id'), p.get('title', ''),
                         'https://www.reddit.com' + p.get('permalink', ''),
                         image, p.get('score'), 'r/' + p.get('subreddit', ''),
                         p.get('over_18'), p.get('created_utc')))
    return out


def reddit_search(q, limit=10, sub=None, nsfw=False):
    subs = sub or '+'.join(SUBS)
    qs = urllib.parse.urlencode({'q': q, 'restrict_sr': 1, 'sort': 'relevance',
                                 'limit': min(int(limit) * 2, 100), 't': 'year'})
    data = _get_json(f'https://www.reddit.com/r/{subs}/search.json?{qs}')
    return _reddit_posts(data, nsfw)[:int(limit)]


def reddit_hot(limit=10, sub=None, nsfw=False):
    subs = sub or '+'.join(SUBS)
    qs = urllib.parse.urlencode({'limit': min(int(limit) * 2, 100)})
    data = _get_json(f'https://www.reddit.com/r/{subs}/hot.json?{qs}')
    return _reddit_posts(data, nsfw)[:int(limit)]


# ── imgflip ──────────────────────────────────────────────────────────

_imgflip_cache = {'ts': 0, 'memes': []}


def imgflip_templates():
    """The ~100 most-captioned templates. Cached 1h — the list barely moves."""
    if time.time() - _imgflip_cache['ts'] > 3600 or not _imgflip_cache['memes']:
        data = _get_json('https://api.imgflip.com/get_memes')
        _imgflip_cache['memes'] = data.get('data', {}).get('memes', [])
        _imgflip_cache['ts'] = time.time()
    return _imgflip_cache['memes']


def imgflip_search(q, limit=10, **_):
    q = q.lower()
    out = []
    for i, t in enumerate(imgflip_templates()):
        if q and q not in t.get('name', '').lower():
            continue
        # rank = list position: imgflip orders by caption volume
        out.append(_meme('imgflip', t.get('id'), t.get('name', ''),
                         f'https://imgflip.com/memetemplate/{t.get("id")}',
                         t.get('url', ''), score=100 - i, author='imgflip'))
    return out[:int(limit)]


# ── knowyourmeme ─────────────────────────────────────────────────────

# A result card's anchor: data-title names the entry, href is /memes/<slug>
# (or /sensitive/memes/<slug> when the thumb is behind the blur — the entry
# itself is the same). The direct image follows inside the card as data-image.
_KYM_A = re.compile(
    r'<a\b[^>]*data-title="([^"]*)"[^>]*href="/((?:sensitive/)?memes/[a-z0-9\-_]+)"',
    re.I)
_KYM_IMG = re.compile(r'data-image="([^"]+)"')


def knowyourmeme_search(q, limit=10, nsfw=False, **_):
    qs = urllib.parse.urlencode({'q': q})
    page = _get(f'https://knowyourmeme.com/search?{qs}',
                accept='text/html')
    out, seen = [], set()
    for hit in _KYM_A.finditer(page):
        title, path = hit.group(1), '/' + hit.group(2)
        sensitive = path.startswith('/sensitive/')
        if sensitive:
            if not nsfw:
                continue
            path = path[len('/sensitive'):]
        slug = path.rsplit('/', 1)[-1]
        if slug in seen:
            continue
        seen.add(slug)
        img = _KYM_IMG.search(page, hit.end(), hit.end() + 2500)
        out.append(_meme('knowyourmeme', slug,
                         html.unescape(title) or slug.replace('-', ' ').title(),
                         'https://knowyourmeme.com' + path,
                         html.unescape(img.group(1)) if img else '',
                         nsfw=sensitive))
    return out[:int(limit)]


# ── 9gag ─────────────────────────────────────────────────────────────

def _ninegag_posts(data, nsfw):
    out = []
    for p in data.get('data', {}).get('posts', []):
        if p.get('nsfw') and not nsfw:
            continue
        images = p.get('images', {})
        image = (images.get('image700') or images.get('image460') or {}).get('url', '')
        if not image:
            continue
        out.append(_meme('ninegag', p.get('id'), p.get('title', ''),
                         p.get('url', ''), image, p.get('upVoteCount'),
                         '9GAG', p.get('nsfw'), p.get('creationTs')))
    return out


def ninegag_search(q, limit=10, nsfw=False, **_):
    qs = urllib.parse.urlencode({'query': q, 'c': 0})
    data = _get_json(f'https://9gag.com/v1/search-posts?{qs}')
    return _ninegag_posts(data, nsfw)[:int(limit)]


def ninegag_hot(limit=10, nsfw=False, **_):
    data = _get_json('https://9gag.com/v1/group-posts/group/default/type/hot')
    return _ninegag_posts(data, nsfw)[:int(limit)]


# ── the fan-out ──────────────────────────────────────────────────────

_SEARCHERS = {'reddit': reddit_search, 'imgflip': imgflip_search,
              'knowyourmeme': knowyourmeme_search, 'ninegag': ninegag_search}


def _fan(jobs, limit):
    """Run the per-source jobs in parallel; a dead source becomes an error,
    never an exception out of the module."""
    memes, errors = [], {}
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        futs = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                memes.extend(fut.result())
            except Exception as e:  # noqa: BLE001 — the source's problem, reported
                errors[name] = f'{type(e).__name__}: {e}'
    seen, unique = set(), []
    for meme in memes:
        key = meme['image'] or (meme['source'] + meme['id'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(meme)
    unique.sort(key=lambda x: x['score'], reverse=True)
    return {'count': len(unique[:limit]), 'memes': unique[:limit],
            'sources': sorted(jobs), 'errors': errors}


def search(q, source='all', limit=24, nsfw=False):
    """Search every meme site at once — or one, with ``source=``."""
    limit = int(limit)
    if not q or not str(q).strip():
        return {'error': 'q required', 'sources': SOURCES}
    names = SOURCES if source in (None, '', 'all') else [source]
    bad = [n for n in names if n not in _SEARCHERS]
    if bad:
        return {'error': f'unknown source {bad[0]}', 'sources': SOURCES}
    per = max(limit // len(names), 6)
    jobs = {n: (lambda fn=_SEARCHERS[n]: fn(q, limit=per, nsfw=nsfw))
            for n in names}
    out = _fan(jobs, limit)
    out['query'] = q
    return out


def trending(limit=24, nsfw=False):
    """What is hot right now, from the sources that have a firehose."""
    limit = int(limit)
    per = max(limit // 2, 6)
    jobs = {'reddit': lambda: reddit_hot(limit=per, nsfw=nsfw),
            'ninegag': lambda: ninegag_hot(limit=per, nsfw=nsfw)}
    return _fan(jobs, limit)


def random_meme(nsfw=False):
    """One meme off the top of the hot feeds, at random."""
    import random
    got = trending(limit=50, nsfw=nsfw)
    if not got['memes']:
        return {'error': 'no memes reachable', 'errors': got['errors']}
    return random.choice(got['memes'])
