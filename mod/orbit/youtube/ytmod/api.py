"""ytmod.api — YouTube, three ways, stdlib only.

keyless   resolve() any YouTube URL/id/@handle into its framable embed form,
          rss() a channel's newest uploads, home() a merged feed of channels.
          youtube.com itself sends X-Frame-Options: SAMEORIGIN, so the only
          YouTube a page can frame is /embed/... — that is what resolve() emits.
api key   Data API v3 reads that need no user: search, video, channel,
          playlist, trending. Quota is the key owner's (search = 100 units).
account   everything "as a user" (subscriptions, feed, like, subscribe,
          comment, playlists) and "as a creator" (my channel, my videos,
          upload, update, thumbnail, delete, comments moderation, Analytics).

`Session(owner=bool, account=id)` decides which rail a call may use: a
non-owner caller never touches an account, only the api key.
"""
from __future__ import annotations

import concurrent.futures as cf
import html
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import auth, store

API = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/'
ANALYTICS = 'https://youtubeanalytics.googleapis.com/v2/reports'
UA = 'Mozilla/5.0 (X11; Linux x86_64) mod-youtube/0.1'
CHUNK = 8 * 1024 * 1024

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
PLAYLIST_ID = re.compile(r'^(PL|UU|LL|FL|OL|RD|UL|WL)[A-Za-z0-9_-]{10,}$|^WL$|^LL$')
CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')


class ApiError(Exception):
    def __init__(self, status: int, message: str, reason: str = ''):
        super().__init__(message)
        self.status, self.reason = status, reason


class Forbidden(ApiError):
    """A non-owner asked for an account rail."""

    def __init__(self, msg='this needs a signed-in account and is for the box or its owner'):
        super().__init__(401, msg, 'owner')


# ── keyless: urls ──────────────────────────────────────────────────────

def embed_url(kind: str, ident: str, *, autoplay: bool = False, start: int = 0, queue: list[str] | None = None) -> str:
    base = 'https://www.youtube.com/embed/'
    q: dict = {'rel': '0', 'modestbranding': '1', 'enablejsapi': '1'}
    if autoplay:
        q['autoplay'] = '1'
    if kind == 'video':
        if start:
            q['start'] = str(int(start))
        if queue:
            q['playlist'] = ','.join(i for i in queue if i != ident)
        return f'{base}{ident}?' + urllib.parse.urlencode(q)
    if kind == 'playlist':
        q.update({'listType': 'playlist', 'list': ident})
        return f'{base}videoseries?' + urllib.parse.urlencode(q)
    if kind == 'channel':
        # UC... -> UU... is YouTube's uploads playlist for the channel, no API needed
        q.update({'listType': 'playlist', 'list': 'UU' + ident[2:]})
        return f'{base}videoseries?' + urllib.parse.urlencode(q)
    if kind == 'queue':
        ids = [i for i in (queue or []) if VIDEO_ID.match(i)]
        if not ids:
            raise ApiError(400, 'queue needs at least one video id')
        q['playlist'] = ','.join(ids)          # path id first AND first in the list: the player starts at index 0
        return f'{base}{ids[0]}?' + urllib.parse.urlencode(q)
    raise ApiError(400, f'cannot embed kind {kind!r}')


def watch_url(kind: str, ident: str) -> str:
    return {'video': f'https://www.youtube.com/watch?v={ident}',
            'playlist': f'https://www.youtube.com/playlist?list={ident}',
            'channel': f'https://www.youtube.com/channel/{ident}'}.get(kind, 'https://www.youtube.com/')


def channel_id_from_handle(handle: str) -> str:
    """@handle / c/name / user/name -> UC... by reading the public page's canonical link."""
    h = handle.strip()
    path = h if h.startswith(('@', 'c/', 'user/')) else '@' + h.lstrip('@')
    url = 'https://www.youtube.com/' + path
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read(6_000_000).decode('utf-8', 'replace')   # the canonical link sits deep in a ~3MB page
    except urllib.error.HTTPError as e:
        raise ApiError(404 if e.code == 404 else 424, f'youtube.com/{path}: http {e.code}')
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"', page)
    if not m:
        m = re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', page)
    if not m:
        raise ApiError(404, f'no channel found for {h}')
    return m.group(1)


def resolve(target: str) -> dict:
    """Any YouTube URL, bare id or @handle -> {kind, id, embed, watch, input}.
    Plain words that are not an id come back as kind='search'."""
    s = (target or '').strip()
    if not s:
        raise ApiError(400, 'empty target')
    if s.startswith('@') and ' ' not in s:
        cid = channel_id_from_handle(s)
        return _res('channel', cid, s, handle=s)
    if VIDEO_ID.match(s):
        return _res('video', s, s)
    if CHANNEL_ID.match(s):
        return _res('channel', s, s)
    if PLAYLIST_ID.match(s):
        return _res('playlist', s, s)
    if '://' not in s and re.match(r'^(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/', s):
        s = 'https://' + s
    if '://' in s:
        u = urllib.parse.urlsplit(s)
        host = (u.hostname or '').lower()
        if host not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
                        'youtu.be', 'www.youtube-nocookie.com', 'youtube-nocookie.com'):
            raise ApiError(400, f'not a YouTube address: {host}')
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [p for p in u.path.split('/') if p]
        start = _seconds(q.get('t') or q.get('start') or '')
        if host == 'youtu.be' and parts:
            return _res('video', parts[0], target, start=start, list=q.get('list'))
        if q.get('v') and VIDEO_ID.match(q['v']):
            return _res('video', q['v'], target, start=start, list=q.get('list'))
        if parts:
            head = parts[0]
            if head in ('shorts', 'live', 'embed', 'v', 'e') and len(parts) > 1 and VIDEO_ID.match(parts[1]):
                return _res('video', parts[1], target, start=start)
            if head == 'embed' and len(parts) > 1 and parts[1] == 'videoseries' and q.get('list'):
                return _res('playlist', q['list'], target)
            if head == 'playlist' and q.get('list'):
                return _res('playlist', q['list'], target)
            if head == 'channel' and len(parts) > 1 and CHANNEL_ID.match(parts[1]):
                return _res('channel', parts[1], target)
            if head.startswith('@'):
                cid = channel_id_from_handle(head)
                return _res('channel', cid, target, handle=head)
            if head in ('c', 'user') and len(parts) > 1:
                cid = channel_id_from_handle(f'{head}/{parts[1]}')
                return _res('channel', cid, target)
            if head == 'results' and q.get('search_query'):
                return {'kind': 'search', 'q': q['search_query'], 'input': target}
        if q.get('list'):
            return _res('playlist', q['list'], target)
        if not parts:
            return {'kind': 'home', 'input': target}
        raise ApiError(400, f'do not know how to open {target}')
    return {'kind': 'search', 'q': s, 'input': target}


def _res(kind: str, ident: str, inp: str, **extra) -> dict:
    out = {'kind': kind, 'id': ident, 'input': inp,
           'embed': embed_url(kind, ident, start=int(extra.get('start') or 0)),
           'watch': watch_url(kind, ident)}
    if extra.get('list') and kind == 'video':
        out['list'] = extra['list']
        out['embed'] += '&list=' + urllib.parse.quote(extra['list'])
    if extra.get('handle'):
        out['handle'] = extra['handle']
    if extra.get('start'):
        out['start'] = extra['start']
    return out


def _seconds(t: str) -> int:
    if not t:
        return 0
    if t.isdigit():
        return int(t)
    m = re.match(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$', t)
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def oembed(video_id: str) -> dict:
    """Title, channel and thumbnail of one video from YouTube's public oEmbed. Keyless, zero quota."""
    url = 'https://www.youtube.com/oembed?' + urllib.parse.urlencode(
        {'url': watch_url('video', video_id), 'format': 'json'})
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            o = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ApiError(404 if e.code in (401, 404) else 424, f'oembed {video_id}: http {e.code}')
    handle = (o.get('author_url') or '').rsplit('/', 1)[-1]
    return {'kind': 'video', 'id': video_id, 'title': o.get('title'), 'channel': o.get('author_name'),
            'channel_id': handle if handle.startswith('@') else None, 'channel_url': o.get('author_url'),
            'thumb': o.get('thumbnail_url'), 'url': watch_url('video', video_id),
            'embed': embed_url('video', video_id), 'source': 'oembed'}


# ── keyless: feeds ─────────────────────────────────────────────────────

_NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015',
       'media': 'http://search.yahoo.com/mrss/'}


_RSS_CACHE: dict[str, tuple[float, dict]] = {}
RSS_TTL = 600


def rss(channel_id: str, n: int = 15) -> dict:
    """A channel's newest uploads from its public Atom feed. Zero quota.

    Cached 10 min in-process and served stale on error: YouTube's "RSS Feeds
    server" answers 404 to a whole IP for a while after a burst of requests,
    so never fan out faster than you must (see home())."""
    if not CHANNEL_ID.match(channel_id or ''):
        channel_id = channel_id_from_handle(channel_id)
    hit = _RSS_CACHE.get(channel_id)
    if hit and time.time() - hit[0] < RSS_TTL:
        return {**hit[1], 'items': hit[1]['items'][: int(n)], 'cached': True}
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/atom+xml, text/xml'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, ET.ParseError) as e:
        if hit:
            return {**hit[1], 'items': hit[1]['items'][: int(n)], 'cached': True, 'stale': True}
        code = getattr(e, 'code', None)
        raise ApiError(424, f'feed for {channel_id}: ' + (f'http {code} (YouTube\'s feed server is refusing '
                       f'this box for now; it clears after a while)' if code else str(e)))
    title = root.findtext('a:title', '', _NS)
    items = []
    for e in root.findall('a:entry', _NS)[:15]:
        vid = e.findtext('yt:videoId', '', _NS)
        mg = e.find('media:group', _NS)
        thumb = views = desc = None
        if mg is not None:
            th = mg.find('media:thumbnail', _NS)
            thumb = th.get('url') if th is not None else None
            st = mg.find('media:community/media:statistics', _NS)
            views = int(st.get('views')) if st is not None and st.get('views') else None
            desc = (mg.findtext('media:description', '', _NS) or '')[:300]
        items.append({'kind': 'video', 'id': vid, 'title': html.unescape(e.findtext('a:title', '', _NS)),
                      'channel': title, 'channel_id': channel_id,
                      'published': e.findtext('a:published', '', _NS), 'views': views,
                      'thumb': thumb or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', 'description': desc,
                      'url': watch_url('video', vid), 'embed': embed_url('video', vid)})
    out = {'channel_id': channel_id, 'title': title, 'items': items, 'source': 'rss'}
    _RSS_CACHE[channel_id] = (time.time(), out)
    return {**out, 'items': items[: int(n)]}


def home(channel_ids: list[str] | None = None, n: int = 24, per: int = 5) -> dict:
    """Newest uploads across many channels, merged by date. RSS, so it costs no quota
    and works signed-out; signed in, the caller passes the subscriptions' ids."""
    ids = list(channel_ids or store.home_channels())[:60]
    out, errors = [], {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(rss, cid, per): cid for cid in ids}
        for f in cf.as_completed(futs):
            cid = futs[f]
            try:
                out.extend(f.result()['items'])
            except Exception as e:  # noqa: BLE001 — one dead feed must not empty the page
                errors[cid] = str(e)
    out.sort(key=lambda i: i.get('published') or '', reverse=True)
    items = out[: int(n)]
    res = {'items': items, 'channels': len(ids), 'errors': errors, 'source': 'rss',
           'embed': embed_url('queue', '', queue=[i['id'] for i in items]) if items else None}
    if not items and ids:
        # the frame can still be YouTube: a channel's uploads playlist embeds with no request from here
        res['embed'] = embed_url('channel', ids[0])
        res['note'] = ('every feed failed; framing the first home channel\'s uploads instead — '
                       + next(iter(errors.values()), ''))
    return res


# ── Data API client ────────────────────────────────────────────────────

class Session:
    """Who is asking. owner=True may use accounts; `account` picks one (default active)."""

    def __init__(self, owner: bool = False, account: str | None = None):
        self.owner = owner
        self.account = account or None

    # auth rails
    def _bearer(self) -> tuple[str, dict]:
        if not self.owner:
            raise Forbidden()
        return auth.token_for(self.account)

    def _headers(self, need_account: bool, params: dict) -> dict:
        """Prefer the account when the caller may use one; else the api key."""
        if need_account:
            tok, _ = self._bearer()
            return {'Authorization': f'Bearer {tok}'}
        if self.owner and (self.account or store.active_id()):
            try:
                tok, _ = auth.token_for(self.account)
                return {'Authorization': f'Bearer {tok}'}
            except auth.AuthError:
                if not store.api_key():
                    raise
        key = store.api_key()
        if not key:
            raise ApiError(401, 'no api key and no signed-in account: m youtube/set_client api_key=... '
                                'or sign in (app -> SIGN IN)', 'noauth')
        params['key'] = key
        return {}

    def call(self, method: str, resource: str, params: dict | None = None, body=None,
             *, need_account: bool = False, base: str = API, raw_body: bytes | None = None,
             content_type: str = 'application/json') -> dict:
        params = {k: v for k, v in (params or {}).items() if v not in (None, '')}
        headers = self._headers(need_account, params)
        url = base + resource + ('?' + urllib.parse.urlencode(params) if params else '')
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                err = json.loads(raw).get('error', {})
                msg = err.get('message') or str(err)
                reason = ((err.get('errors') or [{}])[0]).get('reason', '')
            except Exception:
                msg, reason = raw[:300].decode(errors='replace'), ''
            raise ApiError(e.code, f'youtube api {e.code}: {msg}', reason)

    def get(self, resource, need_account=False, **params):
        return self.call('GET', resource, params, need_account=need_account)

    def all_pages(self, resource, n: int, need_account=False, **params) -> list:
        items, token = [], None
        while len(items) < n:
            page = self.get(resource, need_account, maxResults=min(50, n - len(items)), pageToken=token, **params)
            items.extend(page.get('items', []))
            token = page.get('nextPageToken')
            if not token:
                break
        return items[:n]

    # ── reads (api key or account) ──

    def search(self, q: str, kind: str = 'video', n: int = 12, order: str = 'relevance',
               channel_id: str | None = None, published_after: str | None = None,
               duration: str | None = None, mine: bool = False) -> dict:
        kind = kind if kind in ('video', 'channel', 'playlist', 'any') else 'video'
        params = dict(part='snippet', q=q, type=None if kind == 'any' else kind, order=order,
                      channelId=channel_id, publishedAfter=published_after,
                      videoDuration=duration if kind == 'video' else None, safeSearch='none')
        if mine:
            params['forMine'] = 'true'
            params['type'] = 'video'
        items = self.all_pages('search', int(n), need_account=mine, **params)
        return {'q': q, 'items': [_norm_search(i) for i in items], 'source': 'api'}

    def video(self, video_id: str) -> dict:
        """Full record from the Data API; with no key and no account, the keyless oEmbed subset."""
        try:
            r = self.get('videos', part='snippet,statistics,contentDetails,status', id=video_id)
        except ApiError as e:
            if e.reason != 'noauth':
                raise
            return oembed(video_id)
        items = r.get('items') or []
        if not items:
            raise ApiError(404, f'no video {video_id}')
        return _norm_video(items[0])

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            r = self.get('videos', part='snippet,statistics,contentDetails,status', id=','.join(ids[i:i + 50]))
            out.extend(_norm_video(v) for v in r.get('items', []))
        return out

    def channel(self, ident: str) -> dict:
        ident = (ident or '').strip()
        if ident.startswith('@'):
            params = {'forHandle': ident}
        elif CHANNEL_ID.match(ident):
            params = {'id': ident}
        else:
            params = {'forHandle': '@' + ident}
        r = self.get('channels', part='snippet,statistics,contentDetails,brandingSettings', **params)
        items = r.get('items') or []
        if not items:
            raise ApiError(404, f'no channel {ident}')
        return _norm_channel(items[0])

    def playlist(self, playlist_id: str, n: int = 50) -> dict:
        items = self.all_pages('playlistItems', int(n), part='snippet,contentDetails', playlistId=playlist_id)
        vids = [{'kind': 'video', 'id': i['contentDetails']['videoId'], 'title': i['snippet'].get('title'),
                 'channel': i['snippet'].get('videoOwnerChannelTitle') or i['snippet'].get('channelTitle'),
                 'channel_id': i['snippet'].get('videoOwnerChannelId'),
                 'published': i['contentDetails'].get('videoPublishedAt') or i['snippet'].get('publishedAt'),
                 'thumb': _thumb(i['snippet']), 'position': i['snippet'].get('position'),
                 'playlist_item_id': i['id'],
                 'url': watch_url('video', i['contentDetails']['videoId']),
                 'embed': embed_url('video', i['contentDetails']['videoId'])} for i in items]
        return {'playlist_id': playlist_id, 'items': vids, 'embed': embed_url('playlist', playlist_id),
                'url': watch_url('playlist', playlist_id)}

    def trending(self, region: str = 'US', n: int = 24, category: str | None = None) -> dict:
        items = self.all_pages('videos', int(n), part='snippet,statistics,contentDetails', chart='mostPopular',
                               regionCode=region, videoCategoryId=category)
        vids = [_norm_video(v) for v in items]
        return {'region': region, 'items': vids, 'source': 'api',
                'embed': embed_url('queue', '', queue=[v['id'] for v in vids]) if vids else None}

    def comments(self, video_id: str, n: int = 20, order: str = 'relevance') -> dict:
        items = self.all_pages('commentThreads', int(n), part='snippet,replies', videoId=video_id,
                               order=order, textFormat='plainText')
        return {'video_id': video_id, 'items': [_norm_thread(t) for t in items]}

    # ── as a user (account) ──

    def subscriptions(self, n: int = 50) -> dict:
        items = self.all_pages('subscriptions', int(n), need_account=True, part='snippet,contentDetails',
                               mine='true', order='alphabetical')
        return {'items': [{'subscription_id': s['id'], 'channel_id': s['snippet']['resourceId']['channelId'],
                           'title': s['snippet'].get('title'), 'thumb': _thumb(s['snippet']),
                           'new_items': (s.get('contentDetails') or {}).get('newItemCount'),
                           'total_items': (s.get('contentDetails') or {}).get('totalItemCount'),
                           'url': watch_url('channel', s['snippet']['resourceId']['channelId'])}
                          for s in items]}

    def feed(self, n: int = 24, per: int = 4) -> dict:
        """Home as the signed-in user: newest uploads of every subscription (RSS, quota-free)."""
        subs = self.subscriptions(60)['items']
        out = home([s['channel_id'] for s in subs], n=n, per=per)
        out['subscriptions'] = len(subs)
        return out

    def my_playlists(self, n: int = 50) -> dict:
        items = self.all_pages('playlists', int(n), need_account=True, part='snippet,contentDetails,status',
                               mine='true')
        return {'items': [{'id': p['id'], 'title': p['snippet'].get('title'),
                           'count': (p.get('contentDetails') or {}).get('itemCount'),
                           'privacy': (p.get('status') or {}).get('privacyStatus'), 'thumb': _thumb(p['snippet']),
                           'url': watch_url('playlist', p['id']), 'embed': embed_url('playlist', p['id'])}
                          for p in items]}

    def create_playlist(self, title: str, description: str = '', privacy: str = 'private') -> dict:
        r = self.call('POST', 'playlists', {'part': 'snippet,status'},
                      {'snippet': {'title': title, 'description': description},
                       'status': {'privacyStatus': privacy}}, need_account=True)
        return {'id': r['id'], 'title': title, 'privacy': privacy, 'url': watch_url('playlist', r['id'])}

    def playlist_add(self, playlist_id: str, video_id: str) -> dict:
        r = self.call('POST', 'playlistItems', {'part': 'snippet'},
                      {'snippet': {'playlistId': playlist_id,
                                   'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}},
                      need_account=True)
        return {'playlist_item_id': r['id'], 'playlist_id': playlist_id, 'video_id': video_id}

    def playlist_remove(self, playlist_item_id: str) -> dict:
        self.call('DELETE', 'playlistItems', {'id': playlist_item_id}, need_account=True)
        return {'removed': playlist_item_id}

    def rate(self, video_id: str, rating: str = 'like') -> dict:
        if rating not in ('like', 'dislike', 'none'):
            raise ApiError(400, 'rating must be like, dislike or none')
        self.call('POST', 'videos/rate', {'id': video_id, 'rating': rating}, need_account=True)
        return {'video_id': video_id, 'rating': rating}

    def rating(self, video_id: str) -> dict:
        r = self.get('videos/getRating', need_account=True, id=video_id)
        return {'video_id': video_id, 'rating': ((r.get('items') or [{}])[0]).get('rating')}

    def subscribe(self, channel_id: str) -> dict:
        if not CHANNEL_ID.match(channel_id):
            channel_id = self.channel(channel_id)['id']
        r = self.call('POST', 'subscriptions', {'part': 'snippet'},
                      {'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': channel_id}}},
                      need_account=True)
        return {'subscription_id': r['id'], 'channel_id': channel_id, 'title': r['snippet'].get('title')}

    def unsubscribe(self, channel_or_subscription_id: str) -> dict:
        sid = channel_or_subscription_id
        if CHANNEL_ID.match(sid):
            r = self.get('subscriptions', need_account=True, part='id', mine='true', forChannelId=sid)
            items = r.get('items') or []
            if not items:
                raise ApiError(404, f'not subscribed to {sid}')
            sid = items[0]['id']
        self.call('DELETE', 'subscriptions', {'id': sid}, need_account=True)
        return {'unsubscribed': sid}

    def comment(self, video_id: str, text: str) -> dict:
        r = self.call('POST', 'commentThreads', {'part': 'snippet'},
                      {'snippet': {'videoId': video_id,
                                   'topLevelComment': {'snippet': {'textOriginal': text}}}}, need_account=True)
        return _norm_thread(r)

    def reply(self, parent_id: str, text: str) -> dict:
        r = self.call('POST', 'comments', {'part': 'snippet'},
                      {'snippet': {'parentId': parent_id, 'textOriginal': text}}, need_account=True)
        return _norm_comment(r)

    # ── as a creator (account) ──

    def me(self) -> dict:
        r = self.get('channels', need_account=True, part='snippet,statistics,contentDetails,status', mine='true')
        items = r.get('items') or []
        if not items:
            raise ApiError(404, 'this Google account has no YouTube channel yet')
        return _norm_channel(items[0])

    def my_videos(self, n: int = 25) -> dict:
        me = self.me()
        up = me.get('uploads')
        if not up:
            return {'channel': me, 'items': []}
        pl = self.playlist(up, n)['items']
        vids = self.videos([v['id'] for v in pl]) if pl else []
        return {'channel': me, 'items': vids}

    def update_video(self, video_id: str, title: str | None = None, description: str | None = None,
                     tags: list[str] | None = None, privacy: str | None = None,
                     category_id: str | None = None, made_for_kids: bool | None = None) -> dict:
        cur = self.get('videos', need_account=True, part='snippet,status', id=video_id)
        items = cur.get('items') or []
        if not items:
            raise ApiError(404, f'no video {video_id} on this account')
        sn, st = items[0]['snippet'], items[0].get('status', {})
        snippet = {'title': title if title is not None else sn.get('title'),
                   'description': description if description is not None else sn.get('description', ''),
                   'tags': tags if tags is not None else sn.get('tags', []),
                   'categoryId': category_id or sn.get('categoryId') or '22'}
        status = {k: v for k, v in st.items() if k in ('privacyStatus', 'embeddable', 'license',
                                                        'publicStatsViewable', 'selfDeclaredMadeForKids',
                                                        'publishAt')}
        if privacy:
            status['privacyStatus'] = privacy
        if made_for_kids is not None:
            status['selfDeclaredMadeForKids'] = bool(made_for_kids)
        r = self.call('PUT', 'videos', {'part': 'snippet,status'},
                      {'id': video_id, 'snippet': snippet, 'status': status}, need_account=True)
        return _norm_video(r)

    def delete_video(self, video_id: str) -> dict:
        self.call('DELETE', 'videos', {'id': video_id}, need_account=True)
        return {'deleted': video_id}

    def set_thumbnail(self, video_id: str, path: str) -> dict:
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            raise ApiError(400, f'no file {path}')
        ctype = mimetypes.guess_type(p)[0] or 'image/jpeg'
        with open(p, 'rb') as f:
            data = f.read()
        r = self.call('POST', 'thumbnails/set', {'videoId': video_id, 'uploadType': 'media'},
                      raw_body=data, content_type=ctype, need_account=True, base=UPLOAD)
        return {'video_id': video_id, 'thumbnails': ((r.get('items') or [{}])[0])}

    def upload(self, path: str, title: str, description: str = '', tags: list[str] | None = None,
               privacy: str = 'private', category_id: str = '22', made_for_kids: bool = False,
               publish_at: str | None = None, progress=None) -> dict:
        """Resumable upload (chunked, 308-aware). Private by default: publish on purpose."""
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            raise ApiError(400, f'no file {path}')
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or 'video/*'
        tok, _ = self._bearer()
        meta = {'snippet': {'title': title, 'description': description, 'tags': tags or [],
                            'categoryId': str(category_id)},
                'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': bool(made_for_kids)}}
        if publish_at:
            meta['status']['publishAt'] = publish_at
            meta['status']['privacyStatus'] = 'private'
        init = urllib.request.Request(
            UPLOAD + 'videos?' + urllib.parse.urlencode({'uploadType': 'resumable', 'part': 'snippet,status'}),
            data=json.dumps(meta).encode(), method='POST',
            headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json; charset=UTF-8',
                     'X-Upload-Content-Type': ctype, 'X-Upload-Content-Length': str(size)})
        try:
            with urllib.request.urlopen(init, timeout=60) as r:
                session_url = r.headers.get('Location')
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, f'upload init: {e.read()[:300].decode(errors="replace")}')
        if not session_url:
            raise ApiError(424, 'upload init returned no session url')
        sent, attempt, result = 0, 0, None
        with open(p, 'rb') as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                req = urllib.request.Request(session_url, data=chunk, method='PUT', headers={
                    'Authorization': f'Bearer {tok}', 'Content-Type': ctype,
                    'Content-Length': str(len(chunk)), 'Content-Range': f'bytes {sent}-{end}/{size}'})
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        result = json.loads(r.read() or b'{}')
                        sent = size
                except urllib.error.HTTPError as e:
                    if e.code == 308:
                        rng = e.headers.get('Range')          # bytes=0-N
                        sent = int(rng.split('-')[-1]) + 1 if rng else end + 1
                        attempt = 0
                    elif e.code in (500, 502, 503, 504) and attempt < 5:
                        attempt += 1
                        time.sleep(2 ** attempt)
                        sent = _resume_offset(session_url, tok, size, sent)
                    else:
                        raise ApiError(e.code, f'upload: {e.read()[:300].decode(errors="replace")}')
                if progress:
                    progress(sent, size)
        if not result:
            raise ApiError(424, 'upload finished without a video resource')
        return _norm_video(result)

    def analytics(self, start: str, end: str, metrics: str = 'views,estimatedMinutesWatched,'
                  'averageViewDuration,subscribersGained,likes,comments',
                  dimensions: str | None = 'day', filters: str | None = None, sort: str | None = None,
                  n: int = 200) -> dict:
        tok, _ = self._bearer()
        params = {'ids': 'channel==MINE', 'startDate': start, 'endDate': end, 'metrics': metrics,
                  'dimensions': dimensions, 'filters': filters, 'sort': sort, 'maxResults': int(n)}
        params = {k: v for k, v in params.items() if v not in (None, '')}
        req = urllib.request.Request(ANALYTICS + '?' + urllib.parse.urlencode(params),
                                     headers={'Authorization': f'Bearer {tok}'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rep = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, f'analytics: {e.read()[:300].decode(errors="replace")}')
        cols = [c['name'] for c in rep.get('columnHeaders', [])]
        rows = [dict(zip(cols, r)) for r in rep.get('rows', [])]
        totals = {}
        for c, h in zip(cols, rep.get('columnHeaders', [])):
            if h.get('columnType') == 'METRIC':
                vals = [r.get(c) for r in rows if isinstance(r.get(c), (int, float))]
                totals[c] = round(sum(vals), 2) if vals else 0
        return {'start': start, 'end': end, 'columns': cols, 'rows': rows, 'totals': totals}

    def my_comments(self, n: int = 25, moderation: str = 'published') -> dict:
        me = self.me()
        items = self.all_pages('commentThreads', int(n), need_account=True, part='snippet,replies',
                               allThreadsRelatedToChannelId=me['id'], moderationStatus=moderation,
                               order='time', textFormat='plainText')
        return {'channel_id': me['id'], 'moderation': moderation, 'items': [_norm_thread(t) for t in items]}

    def moderate(self, comment_id: str, status: str = 'published', ban: bool = False) -> dict:
        if status not in ('published', 'heldForReview', 'rejected'):
            raise ApiError(400, 'status must be published, heldForReview or rejected')
        params = {'id': comment_id, 'moderationStatus': status}
        if ban and status == 'rejected':
            params['banAuthor'] = 'true'
        self.call('POST', 'comments/setModerationStatus', params, need_account=True)
        return {'comment_id': comment_id, 'status': status, 'banned_author': bool(ban and status == 'rejected')}


def _resume_offset(session_url: str, tok: str, size: int, fallback: int) -> int:
    req = urllib.request.Request(session_url, data=b'', method='PUT', headers={
        'Authorization': f'Bearer {tok}', 'Content-Length': '0', 'Content-Range': f'bytes */{size}'})
    try:
        with urllib.request.urlopen(req, timeout=60):
            return size
    except urllib.error.HTTPError as e:
        if e.code == 308:
            rng = e.headers.get('Range')
            return int(rng.split('-')[-1]) + 1 if rng else 0
        return fallback


# ── normalizers ────────────────────────────────────────────────────────

def _thumb(sn: dict) -> str | None:
    th = sn.get('thumbnails') or {}
    for k in ('medium', 'high', 'default', 'standard', 'maxres'):
        if th.get(k):
            return th[k]['url']
    return None


def _norm_search(i: dict) -> dict:
    sn, idd = i.get('snippet', {}), i.get('id', {})
    kind = (idd.get('kind') or '').split('#')[-1] or 'video'
    ident = idd.get('videoId') or idd.get('channelId') or idd.get('playlistId') or ''
    return {'kind': kind, 'id': ident, 'title': html.unescape(sn.get('title', '')),
            'channel': sn.get('channelTitle'), 'channel_id': sn.get('channelId'),
            'published': sn.get('publishedAt'), 'thumb': _thumb(sn),
            'description': (sn.get('description') or '')[:300], 'live': sn.get('liveBroadcastContent'),
            'url': watch_url(kind, ident), 'embed': embed_url(kind, ident) if ident else None}


def _norm_video(v: dict) -> dict:
    sn, st, cd, ss = v.get('snippet', {}), v.get('statistics', {}), v.get('contentDetails', {}), v.get('status', {})
    vid = v.get('id')
    return {'kind': 'video', 'id': vid, 'title': sn.get('title'), 'channel': sn.get('channelTitle'),
            'channel_id': sn.get('channelId'), 'published': sn.get('publishedAt'),
            'description': sn.get('description'), 'tags': sn.get('tags', []), 'category_id': sn.get('categoryId'),
            'thumb': _thumb(sn), 'duration': _iso_dur(cd.get('duration')),
            'views': _int(st.get('viewCount')), 'likes': _int(st.get('likeCount')),
            'comments': _int(st.get('commentCount')), 'privacy': ss.get('privacyStatus'),
            'upload_status': ss.get('uploadStatus'), 'embeddable': ss.get('embeddable'),
            'publish_at': ss.get('publishAt'), 'url': watch_url('video', vid), 'embed': embed_url('video', vid)}


def _norm_channel(c: dict) -> dict:
    sn, st, cd = c.get('snippet', {}), c.get('statistics', {}), c.get('contentDetails', {})
    cid = c.get('id')
    return {'kind': 'channel', 'id': cid, 'title': sn.get('title'), 'handle': sn.get('customUrl'),
            'description': (sn.get('description') or '')[:600], 'thumb': _thumb(sn),
            'country': sn.get('country'), 'created': sn.get('publishedAt'),
            'subscribers': _int(st.get('subscriberCount')), 'views': _int(st.get('viewCount')),
            'videos': _int(st.get('videoCount')),
            'uploads': (cd.get('relatedPlaylists') or {}).get('uploads') or ('UU' + cid[2:] if cid else None),
            'url': watch_url('channel', cid), 'embed': embed_url('channel', cid) if cid else None}


def _norm_comment(c: dict) -> dict:
    sn = c.get('snippet', {})
    return {'id': c.get('id'), 'author': sn.get('authorDisplayName'), 'author_channel_id':
            (sn.get('authorChannelId') or {}).get('value'), 'text': sn.get('textOriginal') or sn.get('textDisplay'),
            'likes': sn.get('likeCount'), 'published': sn.get('publishedAt'),
            'moderation': sn.get('moderationStatus'), 'parent_id': sn.get('parentId')}


def _norm_thread(t: dict) -> dict:
    sn = t.get('snippet', {})
    top = _norm_comment(sn.get('topLevelComment') or {})
    top.update({'video_id': sn.get('videoId'), 'replies_count': sn.get('totalReplyCount'),
                'replies': [_norm_comment(r) for r in (t.get('replies') or {}).get('comments', [])]})
    return top


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso_dur(d: str | None) -> int | None:
    if not d:
        return None
    m = re.match(r'^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', d)
    if not m:
        return None
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s
