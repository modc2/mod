"""
bt.news — an open, local-first news index for every subnet.

The explorer already knows each subnet's name, GitHub repo and website (the
on-chain subnet_identity, mirrored into history.db's subnet_meta). This module
turns that into a news desk: a background thread walks the subnets, asks a
handful of key-less public sources what is new about each one, and keeps what
it finds in `~/.mod/bt/news.db`. Every read is answered from that file — the
console never waits on a third party.

Sources are plain functions in SOURCES (subnet -> items), so adding one is a
few lines. Nothing needs an API key or an account:

  github   release + commit Atom feeds of the subnet's own repo
  site     the subnet website's own RSS/Atom feed (autodiscovered once a day)
  gnews    Google News RSS search      "<name>" bittensor
  reddit   Reddit search RSS           "<name>" bittensor
  hn       Hacker News (Algolia) search
  feeds    crypto/Bittensor outlet RSS (global; one fetch per pass, each item
           matched to the subnets it names). The list lives in
           ~/.mod/bt/news_feeds.json — add any feed, no code change.

BT_NEWS_SOURCES picks which per-subnet sources run (default: all but bing).
Search results are kept only when they actually name the subnet, and from
fuzzy sources (reddit/hn/outlet feeds) only when they also say "bittensor",
"subnet", "TAO" or "SN<n>" — a subnet called "Apex" must not inherit
everyone's Apex news. netuid 0 is the network itself: Bittensor-wide stories.

Env: BT_NO_NEWS=1 (or BT_NO_SNAPSHOT=1) disables the thread;
BT_NEWS_REFRESH_SEC (6h) = per-subnet staleness; BT_NEWS_PACE_SEC (15s) =
pause between subnets, so a full pass of ~130 subnets takes about half an hour
and no host sees more than a request every few seconds.
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

from .history import data_dir

REFRESH_SEC = int(os.environ.get('BT_NEWS_REFRESH_SEC', str(6 * 3600)))
PACE_SEC = float(os.environ.get('BT_NEWS_PACE_SEC', '15'))
FEEDS_REFRESH_SEC = int(os.environ.get('BT_NEWS_FEEDS_SEC', '1800'))
SITE_DISCOVER_SEC = 24 * 3600
TIMEOUT = 12
MAX_BYTES = 4_000_000
SUMMARY_CHARS = 420
KEEP_DAYS = int(os.environ.get('BT_NEWS_KEEP_DAYS', '365'))
UA = 'Mozilla/5.0 (compatible; bt-news/1.0; +https://modc2.com/bt)'

# outlets polled once per pass; their items are matched to subnets by name.
DEFAULT_FEEDS: List[Dict] = [
    {'url': 'https://blog.bittensor.com/feed', 'label': 'Bittensor blog', 'trusted': True, 'kind': 'blog'},
    {'url': 'https://github.com/opentensor/subtensor/releases.atom', 'label': 'subtensor releases',
     'trusted': True, 'netuid': 0, 'kind': 'release'},
    {'url': 'https://github.com/opentensor/bittensor/releases.atom', 'label': 'bittensor SDK releases',
     'trusted': True, 'netuid': 0, 'kind': 'release'},
    {'url': 'https://www.theblock.co/rss.xml', 'label': 'The Block'},
    {'url': 'https://decrypt.co/feed', 'label': 'Decrypt'},
    {'url': 'https://cointelegraph.com/rss', 'label': 'Cointelegraph'},
    {'url': 'https://www.coindesk.com/arc/outboundfeeds/rss/', 'label': 'CoinDesk'},
    {'url': 'https://thedefiant.io/feed', 'label': 'The Defiant'},
]

# what makes a mention "about Bittensor" rather than a namesake
CONTEXT = re.compile(r'bittensor|\bsubnets?\b|\$?\btao\b|\bdtao\b|opentensor|\bsn\s?\d{1,3}\b', re.I)
# names too generic to match on their own, even with context
TOO_GENERIC = {'root', 'unknown', 'subnet', 'tao', 'ai', 'data', 'test', 'template'}
# headlines that are never news: price-ticker pages and paid presale promos
MUTE = re.compile(os.environ.get(
    'BT_NEWS_MUTE', r'price today|live price|price prediction|presale|remittix|pepeto|to usd'), re.I)


# ------------------------------------------------------------------ storage

_db_lock = threading.Lock()          # writers only; readers use WAL
_ready: set = set()


def _path() -> str:
    return os.path.join(data_dir(), 'news.db')


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    if _path() not in _ready:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, netuid INTEGER NOT NULL, source TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
            summary TEXT, author TEXT, publisher TEXT,
            ts INTEGER NOT NULL, first_seen INTEGER NOT NULL, title_key TEXT NOT NULL,
            focus INTEGER NOT NULL DEFAULT 1);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_items_title ON items(netuid, title_key);
        CREATE INDEX IF NOT EXISTS idx_items_ts ON items(ts);
        CREATE INDEX IF NOT EXISTS idx_items_net ON items(netuid, ts);
        CREATE TABLE IF NOT EXISTS fetches (
            netuid INTEGER NOT NULL, source TEXT NOT NULL, ts INTEGER NOT NULL,
            ok INTEGER NOT NULL, found INTEGER, added INTEGER, ms INTEGER, error TEXT,
            PRIMARY KEY (netuid, source));
        CREATE TABLE IF NOT EXISTS site_feeds (
            netuid INTEGER PRIMARY KEY, site TEXT, feed TEXT, checked_ts INTEGER);
        ''')
        if 'focus' not in {r[1] for r in conn.execute('PRAGMA table_info(items)')}:
            conn.execute('ALTER TABLE items ADD COLUMN focus INTEGER NOT NULL DEFAULT 1')
        _ready.add(_path())
    return conn


# --------------------------------------------------------------------- http

_host_last: Dict[str, float] = {}
_host_lock = threading.Lock()
COOLDOWN_SEC = 180
HOST_GAP = {'news.google.com': 2.0, 'www.reddit.com': 8.0, 'hn.algolia.com': 1.0,
            'github.com': 1.0, 'www.bing.com': 2.0}


def _throttle(url: str) -> None:
    """Be a polite scraper: a minimum gap per host, shared by every thread."""
    host = urllib.parse.urlsplit(url).hostname or ''
    gap = HOST_GAP.get(host, 0.5)
    with _host_lock:
        wait = _host_last.get(host, 0) + gap - time.time()
        if wait > gap + 1:                # a 429 cooldown: skip, don't block a thread
            raise RuntimeError(f'{host} rate-limited us; cooling down {int(wait)}s')
        _host_last[host] = max(time.time(), _host_last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def fetch(url: str, accept: str = '*/*') -> bytes:
    _throttle(url)
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Accept': accept, 'Accept-Encoding': 'gzip, deflate'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(MAX_BYTES)
            enc = (r.headers.get('Content-Encoding') or '').lower()
    except urllib.error.HTTPError as e:
        if e.code == 429:                 # rate limited: leave that host alone a while
            host = urllib.parse.urlsplit(url).hostname or ''
            with _host_lock:
                _host_last[host] = time.time() + COOLDOWN_SEC
        raise
    if enc == 'gzip':
        body = gzip.decompress(body)
    elif enc == 'deflate':
        body = zlib.decompress(body)
    return body


# ------------------------------------------------------------------ parsing

_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')


def clean(text: Optional[str], n: int = SUMMARY_CHARS) -> str:
    t = _WS.sub(' ', html.unescape(_TAG.sub(' ', text or ''))).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(' ', 1)[0] + '…'


def parse_ts(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return None


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower()


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names) -> str:
    c = _child(el, *names)
    return (c.text or '').strip() if c is not None and c.text else ''


def parse_feed(body: bytes) -> List[Dict]:
    """RSS 2.0 / RSS 1.0 / Atom -> [{title,url,summary,author,publisher,ts}]."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ('item', 'entry'):
            continue
        link = ''
        for c in el:
            if _local(c.tag) != 'link':
                continue
            href = c.get('href')
            if href and c.get('rel', 'alternate') == 'alternate':
                link = href
                break
            if not href and c.text:
                link = c.text.strip()
                break
        if not link:
            link = _text(el, 'guid', 'id')
        src = _child(el, 'source')
        author = _child(el, 'author')
        out.append({
            'title': clean(_text(el, 'title'), 300),
            'url': link,
            'summary': clean(_text(el, 'description', 'summary', 'content', 'encoded')),
            'author': clean((_text(author, 'name') if author is not None and len(author)
                             else (author.text if author is not None else '')) or _text(el, 'creator'), 80),
            'publisher': clean(src.text if src is not None and src.text else '', 80),
            'ts': parse_ts(_text(el, 'pubdate', 'published', 'updated', 'date')),
        })
    return [i for i in out if i['title'] and i['url'].startswith('http')]


_ALT = re.compile(r'<link\b[^>]*>', re.I)


def discover_feed(site: str, body: str) -> Optional[str]:
    """<link rel=alternate type=application/rss+xml|atom+xml href=…> on a page."""
    for tag in _ALT.findall(body):
        low = tag.lower()
        if 'alternate' in low and ('rss+xml' in low or 'atom+xml' in low):
            m = re.search(r'href\s*=\s*["\']([^"\']+)', tag, re.I)
            if m:
                return urllib.parse.urljoin(site, html.unescape(m.group(1)))
    return None


# ---------------------------------------------------------------- relevance

def _name_re(name: str) -> Optional[re.Pattern]:
    n = (name or '').strip()
    if len(n) < 3 or n.lower() in TOO_GENERIC:
        return None
    parts = [re.escape(p) for p in re.split(r'[\s\-—_]+', n) if p]
    return re.compile(r'(?<![\w])' + r'[\s\-—_]*'.join(parts) + r'(?![\w])', re.I)


def mentions(sub: Dict, text: str, need_context: bool) -> bool:
    netuid = sub.get('netuid')
    if netuid and re.search(rf'\b(sn|subnet)\s?#?{netuid}\b', text, re.I):
        return True
    rx = _name_re(sub.get('name') or '')
    if not rx or not rx.search(text):
        return False
    return not need_context or bool(CONTEXT.search(text))


# ------------------------------------------------------------------ sources

def _gh_repo(url: Optional[str]) -> Optional[str]:
    m = re.match(r'https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+)', url or '')
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"


def src_github(sub: Dict) -> List[Dict]:
    repo = _gh_repo(sub.get('github'))
    if not repo:
        return []
    out = []
    for kind, path, n in (('release', 'releases.atom', 20), ('commit', 'commits.atom', 5)):
        for it in parse_feed(fetch(f'https://github.com/{repo}/{path}'))[:n]:
            it.update(kind=kind, publisher=repo)
            if kind == 'commit':
                it['summary'] = ''
            out.append(it)
    return out


def src_site(sub: Dict) -> List[Dict]:
    site = (sub.get('url') or '').strip()
    if not site:
        return []
    if not site.startswith('http'):
        site = 'https://' + site
    netuid = sub['netuid']
    with _connect() as c:
        row = c.execute('SELECT site, feed, checked_ts FROM site_feeds WHERE netuid=?',
                        (netuid,)).fetchone()
    feed = row['feed'] if row and row['site'] == site else None
    if not row or row['site'] != site or time.time() - (row['checked_ts'] or 0) > SITE_DISCOVER_SEC:
        feed = None
        try:
            feed = discover_feed(site, fetch(site, 'text/html').decode('utf-8', 'replace'))
        except Exception:
            pass
        if not feed:                      # the usual suspects
            for p in ('/feed', '/rss.xml', '/feed.xml', '/blog/rss.xml', '/blog/feed', '/atom.xml'):
                u = urllib.parse.urljoin(site, p)
                try:
                    if parse_feed(fetch(u)):
                        feed = u
                        break
                except Exception:
                    continue
        with _db_lock, _connect() as c:
            c.execute('INSERT OR REPLACE INTO site_feeds VALUES (?,?,?,?)',
                      (netuid, site, feed, int(time.time())))
    if not feed:
        return []
    host = urllib.parse.urlsplit(site).hostname or site
    return [dict(it, kind='blog', publisher=it['publisher'] or host)
            for it in parse_feed(fetch(feed))[:30]]


def _query(sub: Dict) -> str:
    return 'bittensor' if not sub.get('netuid') else f'"{sub["name"]}" bittensor'


def src_gnews(sub: Dict) -> List[Dict]:
    q = urllib.parse.quote(_query(sub))
    items = parse_feed(fetch(f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'))
    out = []
    for it in items[:40]:
        pub = it['publisher']
        if pub and it['title'].endswith(' - ' + pub):
            it['title'] = it['title'][:-len(pub) - 3]
        it['summary'] = ''               # gnews' description just repeats the title
        # Google matched the article body; we only see the headline. Require
        # the name in the headline unless it is network-wide news.
        if sub.get('netuid') and not mentions(sub, it['title'], need_context=False):
            continue
        out.append(dict(it, kind='news'))
    return out


def src_bing(sub: Dict) -> List[Dict]:
    q = urllib.parse.quote(_query(sub))
    items = parse_feed(fetch(f'https://www.bing.com/news/search?q={q}&format=rss&setlang=en'))
    return [dict(it, kind='news') for it in items[:30]
            if not sub.get('netuid') or mentions(sub, it['title'] + ' ' + it['summary'], False)]


def src_reddit(sub: Dict) -> List[Dict]:
    q = urllib.parse.quote(_query(sub))
    items = parse_feed(fetch(f'https://www.reddit.com/search.rss?q={q}&sort=new&limit=50'))
    out = []
    for it in items:
        m = re.search(r'reddit\.com/(r/[\w]+)', it['url'])
        it['publisher'] = m.group(1) if m else 'reddit'
        if sub.get('netuid') and not mentions(sub, it['title'] + ' ' + it['summary'], True):
            continue
        if not sub.get('netuid') and not CONTEXT.search(it['title'] + ' ' + it['summary']):
            continue
        out.append(dict(it, kind='social'))
    return out


def src_hn(sub: Dict) -> List[Dict]:
    q = urllib.parse.quote(_query(sub).replace('"', ''))
    j = json.loads(fetch(f'https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story&hitsPerPage=30'))
    out = []
    for h in j.get('hits', []):
        title = h.get('title') or ''
        text = title + ' ' + (h.get('story_text') or '') + ' ' + (h.get('url') or '')
        if sub.get('netuid') and not mentions(sub, text, True):
            continue
        out.append({'title': clean(title, 300),
                    'url': h.get('url') or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    'summary': clean(h.get('story_text')), 'author': h.get('author') or '',
                    'publisher': 'Hacker News', 'ts': h.get('created_at_i'), 'kind': 'social'})
    return out


SOURCES: Dict[str, Dict] = {
    'github': {'fn': src_github, 'label': 'GitHub releases + commits', 'kind': 'release'},
    'site':   {'fn': src_site,   'label': "the subnet's own blog feed", 'kind': 'blog'},
    'gnews':  {'fn': src_gnews,  'label': 'Google News search (RSS)', 'kind': 'news'},
    'reddit': {'fn': src_reddit, 'label': 'Reddit search (RSS)', 'kind': 'social'},
    'hn':     {'fn': src_hn,     'label': 'Hacker News search', 'kind': 'social'},
    'bing':   {'fn': src_bing,   'label': 'Bing News search (RSS)', 'kind': 'news'},
}
DEFAULT_SOURCES = 'github,site,gnews,reddit,hn'


def enabled_sources() -> List[str]:
    raw = os.environ.get('BT_NEWS_SOURCES', DEFAULT_SOURCES)
    return [s for s in (x.strip() for x in raw.split(',')) if s in SOURCES]


# ---------------------------------------------------------------- subnets

def subnets() -> List[Dict]:
    """Subnet identities from the market indexer (no chain call)."""
    path = os.path.join(data_dir(), 'history.db')
    if not os.path.exists(path):
        return []
    c = sqlite3.connect(path, timeout=30)
    try:
        rows = c.execute('SELECT netuid, name, symbol, github, url FROM subnet_meta '
                         'ORDER BY netuid').fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        c.close()
    return [{'netuid': r[0], 'name': 'Bittensor' if r[0] == 0 else (r[1] or ''),
             'symbol': r[2], 'github': r[3], 'url': r[4]} for r in rows]


def _sub(netuid: int) -> Dict:
    for s in subnets():
        if s['netuid'] == netuid:
            return s
    raise ValueError(f'unknown subnet {netuid} — the market indexer has not seen it yet')


# ---------------------------------------------------------------- ingest

def _title_key(t: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()[:160]


def store(netuid: int, source: str, items: Iterable[Dict]) -> int:
    """Insert new items, ignoring ones already held (same url or same headline)."""
    now = int(time.time())
    rows = []
    sub = None
    for it in items:
        if MUTE.search(it['title']):
            continue
        url = it['url'].strip()
        if 'focus' not in it:          # does the headline itself name the subnet?
            if sub is None:
                sub = next((s for s in subnets() if s['netuid'] == netuid), {'netuid': netuid})
            it['focus'] = (not netuid or it.get('kind') in ('release', 'commit', 'blog')
                           or mentions(sub, it['title'], False))
        rows.append((hashlib.sha1(f'{netuid}|{url}'.encode()).hexdigest()[:20], netuid,
                     source, it.get('kind') or SOURCES.get(source, {}).get('kind', 'news'),
                     it['title'], url, it.get('summary') or '', it.get('author') or '',
                     it.get('publisher') or '', int(min(it.get('ts') or now, now)), now,
                     _title_key(it['title']), int(bool(it['focus']))))
    if not rows:
        return 0
    with _db_lock, _connect() as c:
        before = c.total_changes
        c.executemany('INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        return c.total_changes - before


def _log(netuid: int, source: str, ok: bool, found: int, added: int, ms: int, err: str = '') -> None:
    with _db_lock, _connect() as c:
        c.execute('INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?,?,?)',
                  (netuid, source, int(time.time()), int(ok), found, added, ms, err[:300]))


def run_source(sub: Dict, source: str) -> Dict:
    t0 = time.time()
    try:
        items = SOURCES[source]['fn'](sub)
        added = store(sub['netuid'], source, items)
        ms = int((time.time() - t0) * 1000)
        _log(sub['netuid'], source, True, len(items), added, ms)
        return {'source': source, 'ok': True, 'found': len(items), 'added': added, 'ms': ms}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        err = f'{type(e).__name__}: {e}'
        _log(sub['netuid'], source, False, 0, 0, ms, err)
        return {'source': source, 'ok': False, 'error': err, 'ms': ms}


def refresh(netuid: int, sources: Optional[List[str]] = None) -> Dict:
    """Scrape every enabled source for one subnet now (sources in parallel)."""
    sub = _sub(netuid)
    names = [s for s in (sources or enabled_sources()) if s in SOURCES]
    if not sub['netuid']:
        names = [s for s in names if s not in ('github', 'site')]
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
        runs = list(ex.map(lambda s: run_source(sub, s), names))
    return {'netuid': netuid, 'name': sub['name'], 'runs': runs,
            'added': sum(r.get('added', 0) for r in runs)}


# -------------------------------------------------------- outlet feeds

def feeds_path() -> str:
    return os.path.join(data_dir(), 'news_feeds.json')


def feeds() -> List[Dict]:
    try:
        with open(feeds_path()) as f:
            return json.load(f)
    except FileNotFoundError:
        return [dict(f) for f in DEFAULT_FEEDS]
    except Exception:
        return []


def _save_feeds(fs: List[Dict]) -> None:
    tmp = feeds_path() + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(fs, f, indent=2)
    os.replace(tmp, feeds_path())


def add_feed(url: str, label: Optional[str] = None, netuid: Optional[int] = None,
             trusted: bool = False) -> Dict:
    if not re.match(r'https?://', url or ''):
        raise ValueError('feed url must be http(s)')
    items = parse_feed(fetch(url))   # prove it parses before keeping it
    fs = [f for f in feeds() if f['url'] != url]
    entry = {'url': url, 'label': label or urllib.parse.urlsplit(url).hostname}
    if netuid is not None:
        entry['netuid'] = int(netuid)
    if trusted:
        entry['trusted'] = True
    fs.append(entry)
    _save_feeds(fs)
    return {'added': entry, 'items_now': len(items), 'feeds': len(fs)}


def remove_feed(url: str) -> Dict:
    fs = feeds()
    keep = [f for f in fs if f['url'] != url]
    if len(keep) == len(fs):
        raise ValueError(f'no such feed: {url}')
    _save_feeds(keep)
    return {'removed': url, 'feeds': len(keep)}


def match_subnets(text: str, subs: List[Dict], need_context: bool = True) -> List[int]:
    if need_context and not CONTEXT.search(text):
        return []
    return [s['netuid'] for s in subs if s['netuid'] and mentions(s, text, False)]


def refresh_feeds() -> Dict:
    """Poll every outlet feed once; file each item under the subnets it names
    (or the network, netuid 0, when it is Bittensor news naming none)."""
    subs = subnets()
    runs = []
    for f in feeds():
        t0 = time.time()
        key = 'feed:' + (f.get('label') or f['url'])[:40]
        try:
            items = parse_feed(fetch(f['url']))
            added = found = 0
            for it in items:
                text = it['title'] + ' ' + it['summary']
                pinned = f.get('netuid')
                if pinned is not None:
                    targets = [int(pinned)]
                else:
                    targets = match_subnets(text, subs, need_context=not f.get('trusted'))
                    if not targets and (f.get('trusted') or re.search(r'bittensor|opentensor|\bdtao\b', text, re.I)):
                        targets = [0]
                it.update(kind=f.get('kind') or 'news',
                          publisher=it['publisher'] or f.get('label') or '')
                for n in targets:
                    found += 1
                    added += store(n, 'feeds', [dict(it)])
            ms = int((time.time() - t0) * 1000)
            _log(-1, key, True, found, added, ms)
            runs.append({'feed': f['url'], 'ok': True, 'matched': found, 'added': added})
        except Exception as e:
            _log(-1, key, False, 0, 0, int((time.time() - t0) * 1000), f'{type(e).__name__}: {e}')
            runs.append({'feed': f['url'], 'ok': False, 'error': str(e)})
    return {'feeds': len(runs), 'runs': runs, 'added': sum(r.get('added', 0) for r in runs)}


# ------------------------------------------------------------------- reads

def news(netuid: Optional[int] = None, source: Optional[str] = None,
         kind: Optional[str] = None, days: float = 30, search: Optional[str] = None,
         limit: int = 50, offset: int = 0, focused: bool = False) -> Dict:
    where, args = ['ts >= ?'], [int(time.time() - days * 86400) if days else 0]
    if focused:
        where.append('focus = 1')
    if netuid is not None:
        where.append('netuid = ?'); args.append(int(netuid))
    if source:
        if source == 'feeds':
            where.append("source = 'feeds'")
        else:
            where.append('source = ?'); args.append(source)
    if kind:
        kinds = [k.strip() for k in kind.split(',') if k.strip()]
        where.append(f"kind IN ({','.join('?' * len(kinds))})"); args += kinds
    if search:
        where.append('(title LIKE ? OR summary LIKE ? OR publisher LIKE ?)')
        args += [f'%{search}%'] * 3
    w = ' AND '.join(where)
    limit = max(1, min(int(limit or 50), 500))
    with _connect() as c:
        total = c.execute(f'SELECT COUNT(*) FROM items WHERE {w}', args).fetchone()[0]
        rows = c.execute(f'SELECT netuid, source, kind, title, url, summary, author, publisher, '
                         f'ts, first_seen, focus FROM items WHERE {w} ORDER BY ts DESC LIMIT ? OFFSET ?',
                         args + [limit, int(offset)]).fetchall()
    names = {s['netuid']: s['name'] for s in subnets()}
    items = [dict(r, subnet=names.get(r['netuid'])) for r in rows]
    return {'total': total, 'count': len(items), 'offset': offset, 'days': days, 'items': items}


def buzz(days: float = 7, limit: int = 0) -> Dict:
    """News volume per subnet: who is being talked about, and who is shipping."""
    since = int(time.time() - days * 86400)
    prev = int(since - days * 86400)
    with _connect() as c:
        rows = c.execute('''SELECT netuid,
               SUM(ts >= ?) AS items,
               SUM(ts >= ? AND kind IN ('news','social','blog') AND focus = 1) AS press,
               SUM(ts >= ? AND kind = 'release') AS releases,
               SUM(ts >= ? AND kind = 'commit') AS commits,
               SUM(ts >= ? AND ts < ?) AS prev_items,
               MAX(ts) AS last_ts
            FROM items WHERE ts >= ? GROUP BY netuid''',
                            (since, since, since, since, prev, since, prev)).fetchall()
        heads = {r['netuid']: dict(r) for r in c.execute(
            '''SELECT i.netuid, i.title, i.url, i.ts, i.source FROM items i
               JOIN (SELECT netuid, MAX(ts) AS m FROM items WHERE kind != 'commit' AND ts >= ?
                     GROUP BY netuid) x ON x.netuid = i.netuid AND x.m = i.ts
               WHERE i.kind != 'commit' ''', (since,)).fetchall()}
    names = {s['netuid']: s['name'] for s in subnets()}
    out = []
    for r in rows:
        d = dict(r)
        if not d['items']:
            continue
        d['subnet'] = names.get(d['netuid'])
        d['headline'] = heads.get(d['netuid'])
        out.append(d)
    out.sort(key=lambda d: (d['press'] or 0, d['items']), reverse=True)
    return {'days': days, 'subnets': out[:limit] if limit else out}


def status() -> Dict:
    with _connect() as c:
        total = c.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        last24 = c.execute('SELECT COUNT(*) FROM items WHERE first_seen >= ?',
                           (int(time.time()) - 86400,)).fetchone()[0]
        per = [dict(r) for r in c.execute(
            '''SELECT CASE WHEN source LIKE 'feed:%' THEN 'feeds' ELSE source END AS source,
                      COUNT(*) AS runs, SUM(ok) AS ok, SUM(added) AS added,
                      MAX(ts) AS last_ts, CAST(AVG(ms) AS INT) AS avg_ms
               FROM fetches GROUP BY 1''')]
        covered = c.execute('SELECT COUNT(DISTINCT netuid) FROM fetches WHERE netuid >= 0').fetchone()[0]
        errors = [dict(r) for r in c.execute(
            'SELECT netuid, source, ts, error FROM fetches WHERE ok = 0 ORDER BY ts DESC LIMIT 10')]
        site_feeds = c.execute('SELECT COUNT(*) FROM site_feeds WHERE feed IS NOT NULL').fetchone()[0]
    return {'items': total, 'new_24h': last24, 'subnets_covered': covered,
            'subnets_known': len(subnets()), 'site_feeds_found': site_feeds,
            'sources': [{'name': k, 'label': v['label'], 'enabled': k in enabled_sources()}
                        for k, v in SOURCES.items()],
            'feeds': feeds(), 'runs': per, 'recent_errors': errors,
            'refresh_sec': REFRESH_SEC, 'pace_sec': PACE_SEC,
            'running': _thread is not None and _thread.is_alive()}


# ------------------------------------------------------------- background

def _stalest(subs: List[Dict]) -> Optional[Dict]:
    with _connect() as c:
        last = {r[0]: r[1] for r in c.execute(
            'SELECT netuid, MIN(ts) FROM fetches WHERE netuid >= 0 GROUP BY netuid')}
    due = [(last.get(s['netuid'], 0), s) for s in subs
           if time.time() - last.get(s['netuid'], 0) > REFRESH_SEC]
    return min(due, key=lambda x: x[0])[1] if due else None


def prune() -> int:
    with _db_lock, _connect() as c:
        return c.execute('DELETE FROM items WHERE ts < ?',
                         (int(time.time() - KEEP_DAYS * 86400),)).rowcount


_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _loop() -> None:
    last_feeds = 0.0
    last_prune = 0.0
    while not _stop.is_set():
        try:
            if time.time() - last_feeds > FEEDS_REFRESH_SEC:
                refresh_feeds()
                last_feeds = time.time()
            if time.time() - last_prune > 86400:
                prune()
                last_prune = time.time()
            sub = _stalest(subnets())
            if sub:
                refresh(sub['netuid'])
        except Exception:
            pass
        _stop.wait(PACE_SEC)


def start() -> None:
    global _thread
    if os.environ.get('BT_NO_NEWS') == '1' or os.environ.get('BT_NO_SNAPSHOT') == '1':
        return
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='bt-news', daemon=True)
        _thread.start()
