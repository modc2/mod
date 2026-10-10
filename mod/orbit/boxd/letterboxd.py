"""The read layer: public Letterboxd pages in, plain dicts out.

Letterboxd's own API is invite-only, so nothing here uses it. Three public
surfaces carry everything this module answers with, and they are not equally
sturdy:

    /{member}/rss/      the member's own feed — watches, reviews, lists, with
                        rating, liked, rewatch and watched-date as real fields.
                        This is the spine. It is XML, it is stable, and it is
                        the only one that answers under load.
    /film/{slug}/       one film, read out of its schema.org JSON-LD block —
                        director, cast, genre, runtime, the weighted average
                        rating and how many people cast it.
    /{member}/films/    the poster wall of everything they have ever logged.
                        Server-rendered, but the first surface to be throttled.

Letterboxd rate-limits a chatty client to 403 within a few seconds — measured
on this box, not assumed. So every request goes through one throttle and one
disk cache, and a 403 raises ``Blocked`` rather than being dressed up as an
empty result: a lens that reports "no films" when it means "I was shut out"
is worse than no lens.
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from xml.etree import ElementTree

BASE = 'https://letterboxd.com'
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# The doors Letterboxd leaves open to a reader with no account, and the ones it
# does not. Every "closed" entry below was probed from this box, not assumed:
# they 403 regardless of spacing, while the open ones answer 200 when spaced.
DOORS = {
    'rss': {
        'open': True,
        'path': '/{member}/rss/',
        'gives': 'the member feed — watches, reviews and lists, with rating, '
                 'liked, rewatch and watched-date as real fields',
        'sturdiness': 'the spine — answers under load, ~50 diary entries deep',
    },
    'film': {
        'open': True,
        'path': '/film/{slug}/',
        'gives': 'one film out of its schema.org JSON-LD block — director, '
                 'cast, genre, runtime, the weighted average rating',
        'sturdiness': 'reliable, cached a day',
    },
    'grid': {
        'open': True,
        'path': '/{member}/films/',
        'gives': 'the poster wall of everything a member has logged',
        'sturdiness': 'throttled first; the diary answers when this does not',
    },
    'search': {
        'open': False,
        'path': '/search/films/{q}/',
        'gives': 'nothing — 403 from this box no matter how slowly it is asked',
        'sturdiness': 'shut. Name a film instead: film() slugifies a title.',
    },
    'charts': {
        'open': False,
        'path': '/films/ajax/popular/',
        'gives': 'nothing — 403, as do /films/by/rating/ and film similars',
        'sturdiness': 'shut. There is no global-chart answer from here.',
    },
    'likes': {
        'open': False,
        'path': '/{member}/likes/films/',
        'gives': 'nothing — 403. The feed still carries per-entry liked flags.',
        'sturdiness': 'shut, but rss covers the same ground for the diary window.',
    },
}


# Measured, not guessed: back-to-back requests get 403 within a few seconds,
# and the same URLs answer 200 again when spaced. One lock, one clock, fleet-wide.
MIN_INTERVAL = 1.4
_lock = threading.Lock()
_last = [0.0]

CACHE_DIR = os.path.expanduser('~/.mod/boxd/cache')
TTL = {'rss': 900, 'film': 86400, 'grid': 3600}

NS = {
    'letterboxd': 'https://letterboxd.com',
    'tmdb': 'https://themoviedb.org',
    'dc': 'http://purl.org/dc/elements/1.1/',
}


class Blocked(Exception):
    """Letterboxd refused this box. Distinct from 'nothing there'."""


# ── the polite fetcher ───────────────────────────────────────────

def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + '.json')


def _cached(url, ttl):
    p = _cache_path(url)
    try:
        with open(p) as f:
            blob = json.load(f)
    except Exception:
        return None
    if ttl is not None and time.time() - blob.get('at', 0) > ttl:
        return None
    return blob


def _store(url, text):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(url) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'url': url, 'at': time.time(), 'text': text}, f)
        os.replace(tmp, _cache_path(url))
    except Exception:
        pass  # a cold cache is slow, not broken


def fetch(path, kind='rss', fresh=False):
    """One GET, throttled and cached. Returns (text, meta).

    On a 403 the stale cache still answers — with ``stale`` set, so the caller
    can say so — and only a cold cache raises ``Blocked``.
    """
    url = path if path.startswith('http') else BASE + path
    ttl = TTL.get(kind, 900)
    if not fresh:
        hit = _cached(url, ttl)
        if hit:
            return hit['text'], {'cached': True, 'age': int(time.time() - hit['at']),
                                 'url': url}
    with _lock:
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Accept-Language': 'en',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            text = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            stale = _cached(url, None)
            if stale:
                return stale['text'], {'cached': True, 'stale': True,
                                       'age': int(time.time() - stale['at']),
                                       'url': url}
            raise Blocked(f'letterboxd returned {e.code} for {url} — '
                          'this box is being rate-limited; try again in a minute')
        if e.code == 404:
            raise LookupError(f'no such page: {url}')
        raise
    _store(url, text)
    return text, {'cached': False, 'url': url}


# ── the member feed ──────────────────────────────────────────────

_STARS = {'★': 1.0, '½': 0.5}


def stars(rating):
    """4.5 → '★★★★½'. The rating as Letterboxd itself writes it."""
    if rating is None:
        return None
    full = int(rating)
    return '★' * full + ('½' if rating - full >= 0.5 else '')


def _text(item, tag):
    el = item.find(tag, NS)
    return el.text if el is not None and el.text else None


def _num(item, tag):
    v = _text(item, tag)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _yes(item, tag):
    v = _text(item, tag)
    return None if v is None else v.strip().lower() == 'yes'


def _strip_html(html):
    html = re.sub(r'(?i)</p>\s*<p>', '\n\n', html or '')
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    return re.sub(r'<[^>]+>', '', html).strip()


def parse_rss(xml):
    """The RSS feed → {'member': …, 'entries': […]}. No network."""
    root = ElementTree.fromstring(xml.strip())
    channel = root.find('channel')
    if channel is None:
        return {'member': {}, 'entries': []}
    member = {
        'name': (channel.findtext('title') or '').replace('Letterboxd - ', '').strip(),
        'url': channel.findtext('link'),
    }
    if member['url']:
        member['user'] = member['url'].rstrip('/').rsplit('/', 1)[-1]
    entries = []
    for item in channel.findall('item'):
        guid = (item.findtext('guid') or '')
        kind = 'entry'
        if 'letterboxd-review' in guid:
            kind = 'review'
        elif 'letterboxd-watch' in guid:
            kind = 'watch'
        elif 'letterboxd-list' in guid:
            kind = 'list'
        title = item.findtext('title') or ''
        body = item.findtext('description') or ''
        e = {
            'kind': kind,
            'id': guid.rsplit('-', 1)[-1] if guid else None,
            'title': title,
            'url': item.findtext('link'),
            'published': item.findtext('pubDate'),
        }
        film = _text(item, 'letterboxd:filmTitle')
        if film:
            rating = _num(item, 'letterboxd:memberRating')
            e.update({
                'film': film,
                'year': int(_text(item, 'letterboxd:filmYear') or 0) or None,
                'rating': rating,
                'stars': stars(rating),
                'liked': _yes(item, 'letterboxd:memberLike'),
                'rewatch': _yes(item, 'letterboxd:rewatch'),
                'watched': _text(item, 'letterboxd:watchedDate'),
                'tmdb': _text(item, 'tmdb:movieId'),
                'spoilers': 'contains spoilers' in title.lower(),
            })
            e['slug'] = film_slug(e['url'])
            m = re.search(r'<img src="([^"]+)"', body)
            e['poster'] = m.group(1) if m else None
            if kind == 'review':
                # The watch line is boilerplate; the review is what is left.
                text = _strip_html(re.sub(r'<p><img[^>]*/?></p>', '', body))
                text = re.sub(r'^Watched on \w+ \w+ \d+, \d+\.?\s*', '', text).strip()
                e['review'] = text or None
        else:
            e['kind'] = 'list' if kind == 'list' else e['kind']
            e['note'] = _strip_html(body)[:500] or None
        entries.append(e)
    return {'member': member, 'entries': entries}


def film_slug(url):
    """A film URL → its slug. Member film links carry it too."""
    m = re.search(r'/film/([a-z0-9\-]+)/?', url or '')
    return m.group(1) if m else None


def diary(user, limit=50, kind='all', fresh=False):
    """A member's feed, newest first. kind=watch|review|list|all."""
    xml, meta = fetch(f'/{_user(user)}/rss/', 'rss', fresh=fresh)
    out = parse_rss(xml)
    entries = out['entries']
    if kind and kind != 'all':
        entries = [e for e in entries if e['kind'] == kind]
    out['entries'] = entries[:int(limit)] if limit else entries
    out['total'] = len(entries)
    out['source'] = meta
    out['coverage'] = ('the RSS feed is the last ~50 diary entries and ~50 lists — '
                       'it is a window, not the whole diary')
    return out


def watches(user, limit=None, fresh=False):
    """Every logged film in the feed — watches and reviews, lists dropped."""
    out = diary(user, limit=None, fresh=fresh)
    out['entries'] = [e for e in out['entries'] if e.get('film')]
    if limit:
        out['entries'] = out['entries'][:int(limit)]
    return out


def _user(user):
    if not user:
        raise ValueError('which member? pass a letterboxd username, e.g. user=dave')
    return str(user).strip().strip('/').rsplit('/', 1)[-1].lower()


# ── one film ─────────────────────────────────────────────────────

def slugify(title, year=None):
    s = re.sub(r"['’]", '', str(title).lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return f'{s}-{year}' if year else s


def parse_film(html):
    """The film page's JSON-LD block → a flat dict. No network."""
    m = re.search(r'application/ld\+json[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise LookupError('no JSON-LD on that page — letterboxd changed the markup')
    raw = m.group(1).replace('/* <![CDATA[ */', '').replace('/* ]]> */', '').strip()
    d = json.loads(raw)
    agg = d.get('aggregateRating') or {}
    names = lambda key: [p.get('name') for p in d.get(key) or [] if p.get('name')]
    return {
        'title': d.get('name'),
        'year': int((d.get('dateCreated') or '0')[:4]) or None,
        'url': d.get('url'),
        'slug': film_slug(d.get('url')),
        'poster': d.get('image'),
        'synopsis': d.get('description'),
        'directors': names('director'),
        'cast': names('actor')[:12],
        'studios': names('productionCompany'),
        'countries': [c.get('name') for c in d.get('countryOfOrigin') or []
                      if isinstance(c, dict)],
        'languages': d.get('inLanguage'),
        'genres': d.get('genre'),
        'runtime': _minutes(d.get('duration')),
        'rating': agg.get('ratingValue'),
        'ratings': agg.get('ratingCount'),
        'reviews': agg.get('reviewCount'),
    }


def _minutes(iso):
    if not iso:
        return None
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?', iso)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def film(title, year=None, fresh=False):
    """One film by slug, or by title — with the year when the title is common."""
    bare = title if re.fullmatch(r'[a-z0-9\-]+', str(title)) else slugify(title)
    # Year first when the caller gave one. Letterboxd hands the BARE slug to
    # whichever film claimed it first, which is rarely the one being asked for:
    # /film/parasite/ is a 1982 Charles Band creature feature, and Bong Joon
    # Ho's is /film/parasite-2019/. Trying bare first silently answers with the
    # wrong film -- a wrong answer, not an error, which is the worst kind.
    tries = []
    if year:
        tries += [slugify(title, year), f'{bare}-{year}']
    tries.append(bare)
    last = None
    for s in dict.fromkeys(tries):
        try:
            html, meta = fetch(f'/film/{s}/', 'film', fresh=fresh)
        except LookupError as e:
            last = e
            continue
        out = parse_film(html)
        out['source'] = meta
        return out
    raise LookupError(f'no film page for {title!r}'
                      f'{" (" + str(year) + ")" if year else ""} — '
                      'letterboxd slugs disambiguate by year, so try year=')


# ── the poster wall ──────────────────────────────────────────────

_ITEM = re.compile(
    r'data-item-name="(?P<name>[^"]*)"[^>]*?data-item-slug="(?P<slug>[^"]*)"'
    r'[^>]*?data-item-link="(?P<link>[^"]*)"')


def parse_grid(html):
    """A poster grid → [{title, year, slug, url}]. No network."""
    out, seen = [], set()
    for m in _ITEM.finditer(html):
        slug = m.group('slug')
        if slug in seen:
            continue
        seen.add(slug)
        name = m.group('name').replace('&amp;', '&').replace('&#039;', "'")
        year = None
        ym = re.search(r'\((\d{4})\)\s*$', name)
        if ym:
            year, name = int(ym.group(1)), name[:ym.start()].strip()
        out.append({'title': name, 'year': year, 'slug': slug,
                    'url': BASE + m.group('link')})
    return out


def films(user, fresh=False):
    """Everything a member has logged — the poster wall, first page.

    This is the surface Letterboxd throttles first; the diary answers when
    this does not.
    """
    html, meta = fetch(f'/{_user(user)}/films/', 'grid', fresh=fresh)
    got = parse_grid(html)
    return {'user': _user(user), 'films': got, 'count': len(got),
            'source': meta,
            'coverage': 'the first page of the wall (~72 films), newest first'}


# ── the questions worth asking ───────────────────────────────────
#
# These three are the module's actual product: the fetch layer above is
# plumbing, and taste.py is arithmetic, but a caller wants "who is this
# member", "what did they write" and "do these two agree". Each one lives
# here rather than in the anchor so that serve.py and mod.py call exactly the
# same code — two surfaces over one implementation, never two.


def member(user, fresh=False):
    """Who this is, plus the headline numbers off their feed window."""
    import taste
    got = watches(user, fresh=fresh)
    return {'member': got['member'],
            'taste': taste.profile(got['entries']),
            'recent': got['entries'][:10],
            'coverage': got['coverage'],
            'source': got['source']}


def reviews(user, limit=20, fresh=False):
    """Only the entries they actually wrote something about."""
    got = diary(user, limit=None, kind='review', fresh=fresh)
    got['entries'] = [e for e in got['entries'] if e.get('review')]
    got['total'] = len(got['entries'])
    if limit:
        got['entries'] = got['entries'][:int(limit)]
    return got


def taste_of(user, fresh=False):
    """How a member rates: mean, median, histogram, likes, rewatches, decades."""
    import taste
    got = watches(user, fresh=fresh)
    return {'member': got['member'], 'profile': taste.profile(got['entries']),
            'coverage': got['coverage'], 'source': got['source']}


def compare(a, b, fresh=False):
    """Two members held against each other.

    The question the site answers badly: do they agree, where do they split
    hardest, and what has one seen that the other has not.
    """
    import taste
    if not a or not b:
        raise ValueError('compare needs two members: a=<user> b=<user>')
    one, two = watches(a, fresh=fresh), watches(b, fresh=fresh)
    out = taste.overlap(one['entries'], two['entries'],
                        a=one['member'].get('user') or _user(a),
                        b=two['member'].get('user') or _user(b))
    out['profiles'] = {
        _user(a): taste.profile(one['entries']),
        _user(b): taste.profile(two['entries']),
    }
    out['source'] = {'a': one['source'], 'b': two['source']}
    return out

