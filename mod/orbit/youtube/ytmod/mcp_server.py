"""ytmod.mcp_server — the one JSON-RPC dispatch behind both MCP transports.

* stdio:  ``python3 -m ytmod.mcp_server``  (cwd = the youtube module dir) — the
          caller is a process on the box, so it is the owner.
* HTTP:   ``POST /mcp`` on the module server, which passes `owner` from the
          bearer token (mod-protocol) or a loopback client.

    claude mcp add youtube -- python3 -m ytmod.mcp_server          # from orbit/youtube
    claude mcp add --transport http youtube http://localhost:51400/mcp

Tools come in three rings. Open: resolve/search/video/channel/playlist/
trending/rss/home/comments (api key or keyless). As a user (owner): accounts,
subscriptions, feed, playlists, rate, subscribe, comment. As a creator
(owner): me, my_videos, upload, update, thumbnail, delete, analytics,
comments moderation. Every account tool takes an optional `account` to act
as any of the signed-in Google accounts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import api, auth, store

MODULE_DIR = Path(__file__).resolve().parents[1]


def _version() -> str:
    try:
        return json.loads((MODULE_DIR / 'config.json').read_text()).get('version', '0')
    except Exception:
        return '0'


SERVER_INFO = {'name': 'youtube', 'title': 'YouTube', 'version': _version()}
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'YouTube as a user and as a creator. Start with yt_status (which rails are live: api key, '
    'signed-in accounts, active account). yt_open turns any YouTube url/@handle/id into its framable '
    'embed; yt_search / yt_video / yt_channel / yt_playlist / yt_trending read the Data API; yt_rss and '
    'yt_home are keyless. Account tools act as the ACTIVE Google account unless you pass account=; '
    'yt_accounts lists them, yt_use_account switches. Uploads are private by default — set privacy '
    'public only when asked. yt_delete_video and yt_moderate(ban=true) are irreversible: confirm first.')


def _s(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _i(desc, default=None, **kw):
    d = {'type': 'integer', 'description': desc, **kw}
    if default is not None:
        d['default'] = default
    return d


ACCOUNT = _s('Which signed-in account to act as (id, email, @handle or title). Default: the active one.')


def _sess(ctx: dict, a: dict) -> api.Session:
    return api.Session(owner=bool(ctx.get('owner')), account=a.get('account'))


def _need_owner(ctx: dict):
    if not ctx.get('owner'):
        raise api.Forbidden()


def _status(ctx: dict, a: dict) -> dict:
    out = {'client': store.client_status(), 'accounts': len(store.accounts()),
           'active': None, 'caller': 'owner' if ctx.get('owner') else 'public',
           'rails': {'keyless': True, 'api_key': bool(store.api_key()), 'account': False}}
    act = store.get()
    if act:
        out['active'] = {k: act.get(k) for k in ('id', 'title', 'email', 'handle', 'channel_id')}
        out['rails']['account'] = bool(ctx.get('owner'))
    return out


def _home(ctx: dict, a: dict) -> dict:
    n = int(a.get('n', 24))
    s = _sess(ctx, a)
    if ctx.get('owner') and (a.get('account') or store.active_id()):
        try:
            return s.feed(n)
        except (auth.AuthError, api.ApiError) as e:
            fallback = api.home(n=n)
            fallback['note'] = f'feed as account failed ({e}); showing the keyless home'
            return fallback
    if store.api_key():
        try:
            return s.trending(a.get('region', 'US'), n)
        except api.ApiError:
            pass
    return api.home(n=n)


def _accounts(ctx, a):
    _need_owner(ctx)
    return {'active': store.active_id(), 'accounts': store.accounts(), 'client': store.client_status()}


def _use(ctx, a):
    _need_owner(ctx)
    return store.set_active(a['account'])


def _remove(ctx, a):
    _need_owner(ctx)
    return auth.revoke(a['account'])


def _upload(ctx, a):
    s = _sess(ctx, a)
    return s.upload(a['path'], a['title'], a.get('description', ''), a.get('tags'), a.get('privacy', 'private'),
                    str(a.get('category_id', '22')), bool(a.get('made_for_kids', False)), a.get('publish_at'))


TOOLS: Dict[str, Dict[str, Any]] = {
    # ── open ──
    'yt_status': {'fn': _status, 'ring': 'open', 'read_only': True,
                  'description': 'Which rails are live: OAuth client, api key, signed-in accounts, the active '
                                 'account, and whether this caller may use accounts.',
                  'schema': {'type': 'object', 'properties': {}}},
    'yt_open': {'fn': lambda c, a: api.resolve(a['target']), 'ring': 'open', 'read_only': True,
                'description': 'Resolve any YouTube url, youtu.be link, 11-char video id, playlist id, channel id '
                               'or @handle into {kind, id, embed, watch}. `embed` is the one YouTube address a '
                               'page may iframe (youtube.com itself refuses framing). Plain words come back as '
                               'kind=search.',
                'schema': {'type': 'object', 'required': ['target'], 'properties': {
                    'target': _s('url / id / @handle / words')}}},
    'yt_search': {'fn': lambda c, a: _sess(c, a).search(a['q'], a.get('kind', 'video'), a.get('n', 12),
                                                         a.get('order', 'relevance'), a.get('channel_id'),
                                                         a.get('published_after'), a.get('duration')),
                  'ring': 'open', 'read_only': True,
                  'description': 'Search YouTube (Data API, 100 quota units). kind: video|channel|playlist|any. '
                                 'order: relevance|date|viewCount|rating. duration: short|medium|long.',
                  'schema': {'type': 'object', 'required': ['q'], 'properties': {
                      'q': _s('query'), 'kind': _s('video|channel|playlist|any', default='video'),
                      'n': _i('results', 12, minimum=1, maximum=50), 'order': _s('relevance|date|viewCount|rating'),
                      'channel_id': _s('only this channel'), 'published_after': _s('RFC3339, e.g. 2026-10-01T00:00:00Z'),
                      'duration': _s('short|medium|long'), 'account': ACCOUNT}}},
    'yt_video': {'fn': lambda c, a: _sess(c, a).video(api.resolve(a['video'])['id']), 'ring': 'open',
                 'read_only': True,
                 'description': 'One video: title, channel, stats, duration, tags, privacy, embed url (keyless: '
                                'title/channel/thumbnail via oEmbed).',
                 'schema': {'type': 'object', 'required': ['video'], 'properties': {
                     'video': _s('video id or url'), 'account': ACCOUNT}}},
    'yt_channel': {'fn': lambda c, a: _sess(c, a).channel(a['channel']), 'ring': 'open', 'read_only': True,
                   'description': 'One channel by id or @handle: stats, uploads playlist id, embed url.',
                   'schema': {'type': 'object', 'required': ['channel'], 'properties': {
                       'channel': _s('UC... id or @handle'), 'account': ACCOUNT}}},
    'yt_playlist': {'fn': lambda c, a: _sess(c, a).playlist(a['playlist_id'], a.get('n', 50)), 'ring': 'open',
                    'read_only': True,
                    'description': 'Videos in a playlist (any public playlist; a channel\'s uploads are UU + '
                                   'its id without the UC).',
                    'schema': {'type': 'object', 'required': ['playlist_id'], 'properties': {
                        'playlist_id': _s('PL... / UU... id'), 'n': _i('max videos', 50, maximum=500),
                        'account': ACCOUNT}}},
    'yt_trending': {'fn': lambda c, a: _sess(c, a).trending(a.get('region', 'US'), a.get('n', 24),
                                                             a.get('category_id')),
                    'ring': 'open', 'read_only': True,
                    'description': 'Most popular videos right now in a region (Data API mostPopular chart).',
                    'schema': {'type': 'object', 'properties': {
                        'region': _s('ISO 3166 country, e.g. US, GB, DE', default='US'),
                        'n': _i('videos', 24, maximum=50), 'category_id': _s('YouTube category id'),
                        'account': ACCOUNT}}},
    'yt_rss': {'fn': lambda c, a: api.rss(a['channel'], a.get('n', 15)), 'ring': 'open', 'read_only': True,
               'description': 'Newest uploads of a channel from its public Atom feed. Keyless, zero quota.',
               'schema': {'type': 'object', 'required': ['channel'], 'properties': {
                   'channel': _s('UC... id or @handle'), 'n': _i('videos', 15, maximum=15)}}},
    'yt_home': {'fn': _home, 'ring': 'open', 'read_only': True,
                'description': 'The home page: as the active account, newest uploads of its subscriptions '
                               '(quota-free RSS); with only an api key, trending; keyless, a merged feed of '
                               'the home channels. Also returns a single embed url that plays them as a queue.',
                'schema': {'type': 'object', 'properties': {
                    'n': _i('videos', 24, maximum=60), 'region': _s('trending region', default='US'),
                    'account': ACCOUNT}}},
    'yt_comments': {'fn': lambda c, a: _sess(c, a).comments(api.resolve(a['video'])['id'], a.get('n', 20),
                                                             a.get('order', 'relevance')),
                    'ring': 'open', 'read_only': True,
                    'description': 'Comment threads on a video (top-level + replies).',
                    'schema': {'type': 'object', 'required': ['video'], 'properties': {
                        'video': _s('video id or url'), 'n': _i('threads', 20, maximum=100),
                        'order': _s('relevance|time'), 'account': ACCOUNT}}},
    # ── as a user ──
    'yt_accounts': {'fn': _accounts, 'ring': 'user', 'read_only': True,
                    'description': 'Every signed-in Google account (channel title, handle, email, scopes) and '
                                   'which is active. Owner only.',
                    'schema': {'type': 'object', 'properties': {}}},
    'yt_use_account': {'fn': _use, 'ring': 'user', 'read_only': False,
                       'description': 'Make one signed-in account the active one for every later call.',
                       'schema': {'type': 'object', 'required': ['account'], 'properties': {'account': ACCOUNT}}},
    'yt_remove_account': {'fn': _remove, 'ring': 'user', 'read_only': False, 'destructive': True,
                          'description': 'Sign an account out: revoke its token at Google and forget it here.',
                          'schema': {'type': 'object', 'required': ['account'], 'properties': {'account': ACCOUNT}}},
    'yt_subscriptions': {'fn': lambda c, a: _sess(c, a).subscriptions(a.get('n', 50)), 'ring': 'user',
                         'read_only': True, 'description': 'Channels the account subscribes to.',
                         'schema': {'type': 'object', 'properties': {'n': _i('max', 50, maximum=500),
                                                                     'account': ACCOUNT}}},
    'yt_feed': {'fn': lambda c, a: _sess(c, a).feed(a.get('n', 24)), 'ring': 'user', 'read_only': True,
                'description': 'The subscriptions feed: newest uploads across every channel the account follows.',
                'schema': {'type': 'object', 'properties': {'n': _i('videos', 24, maximum=60), 'account': ACCOUNT}}},
    'yt_my_playlists': {'fn': lambda c, a: _sess(c, a).my_playlists(a.get('n', 50)), 'ring': 'user',
                        'read_only': True, 'description': 'The account\'s own playlists.',
                        'schema': {'type': 'object', 'properties': {'n': _i('max', 50), 'account': ACCOUNT}}},
    'yt_create_playlist': {'fn': lambda c, a: _sess(c, a).create_playlist(a['title'], a.get('description', ''),
                                                                           a.get('privacy', 'private')),
                           'ring': 'user', 'read_only': False,
                           'description': 'Create a playlist (private unless told otherwise).',
                           'schema': {'type': 'object', 'required': ['title'], 'properties': {
                               'title': _s('title'), 'description': _s('description'),
                               'privacy': _s('private|unlisted|public', default='private'), 'account': ACCOUNT}}},
    'yt_playlist_add': {'fn': lambda c, a: _sess(c, a).playlist_add(a['playlist_id'], api.resolve(a['video'])['id']),
                        'ring': 'user', 'read_only': False,
                        'description': 'Add a video to one of the account\'s playlists (WL = Watch Later is not '
                                       'writable by the API; use a real playlist id).',
                        'schema': {'type': 'object', 'required': ['playlist_id', 'video'], 'properties': {
                            'playlist_id': _s('PL... id'), 'video': _s('video id or url'), 'account': ACCOUNT}}},
    'yt_playlist_remove': {'fn': lambda c, a: _sess(c, a).playlist_remove(a['playlist_item_id']), 'ring': 'user',
                           'read_only': False,
                           'description': 'Remove an item from a playlist by its playlist_item_id (from yt_playlist).',
                           'schema': {'type': 'object', 'required': ['playlist_item_id'], 'properties': {
                               'playlist_item_id': _s('item id'), 'account': ACCOUNT}}},
    'yt_rate': {'fn': lambda c, a: _sess(c, a).rate(api.resolve(a['video'])['id'], a.get('rating', 'like')),
                'ring': 'user', 'read_only': False,
                'description': 'Like, dislike or clear the rating on a video as the account.',
                'schema': {'type': 'object', 'required': ['video'], 'properties': {
                    'video': _s('video id or url'), 'rating': _s('like|dislike|none', default='like'),
                    'account': ACCOUNT}}},
    'yt_rating': {'fn': lambda c, a: _sess(c, a).rating(api.resolve(a['video'])['id']), 'ring': 'user',
                  'read_only': True, 'description': 'The account\'s current rating on a video.',
                  'schema': {'type': 'object', 'required': ['video'], 'properties': {
                      'video': _s('video id or url'), 'account': ACCOUNT}}},
    'yt_subscribe': {'fn': lambda c, a: _sess(c, a).subscribe(a['channel']), 'ring': 'user', 'read_only': False,
                     'description': 'Subscribe the account to a channel (id or @handle).',
                     'schema': {'type': 'object', 'required': ['channel'], 'properties': {
                         'channel': _s('UC... id or @handle'), 'account': ACCOUNT}}},
    'yt_unsubscribe': {'fn': lambda c, a: _sess(c, a).unsubscribe(a['channel']), 'ring': 'user',
                       'read_only': False, 'description': 'Unsubscribe by channel id or subscription id.',
                       'schema': {'type': 'object', 'required': ['channel'], 'properties': {
                           'channel': _s('UC... id or subscription id'), 'account': ACCOUNT}}},
    'yt_comment': {'fn': lambda c, a: _sess(c, a).comment(api.resolve(a['video'])['id'], a['text']), 'ring': 'user',
                   'read_only': False, 'description': 'Post a top-level comment on a video as the account.',
                   'schema': {'type': 'object', 'required': ['video', 'text'], 'properties': {
                       'video': _s('video id or url'), 'text': _s('comment text'), 'account': ACCOUNT}}},
    'yt_reply': {'fn': lambda c, a: _sess(c, a).reply(a['parent_id'], a['text']), 'ring': 'user',
                 'read_only': False, 'description': 'Reply to a comment (parent_id from yt_comments).',
                 'schema': {'type': 'object', 'required': ['parent_id', 'text'], 'properties': {
                     'parent_id': _s('comment id'), 'text': _s('reply text'), 'account': ACCOUNT}}},
    # ── as a creator ──
    'yt_me': {'fn': lambda c, a: _sess(c, a).me(), 'ring': 'creator', 'read_only': True,
              'description': 'The account\'s own channel: stats, uploads playlist id.',
              'schema': {'type': 'object', 'properties': {'account': ACCOUNT}}},
    'yt_my_videos': {'fn': lambda c, a: _sess(c, a).my_videos(a.get('n', 25)), 'ring': 'creator',
                     'read_only': True,
                     'description': 'The account\'s uploads with stats and privacy (private ones included).',
                     'schema': {'type': 'object', 'properties': {'n': _i('videos', 25, maximum=200),
                                                                 'account': ACCOUNT}}},
    'yt_upload': {'fn': _upload, 'ring': 'creator', 'read_only': False,
                  'description': 'Upload a video file from this box (resumable). Private by default; '
                                 'publish_at schedules a public release.',
                  'schema': {'type': 'object', 'required': ['path', 'title'], 'properties': {
                      'path': _s('local file path'), 'title': _s('title (<=100 chars)'),
                      'description': _s('description'), 'tags': {'type': 'array', 'items': {'type': 'string'}},
                      'privacy': _s('private|unlisted|public', default='private'),
                      'category_id': _s('YouTube category id (22 = People & Blogs, 28 = Science & Tech, 10 = Music)',
                                        default='22'),
                      'made_for_kids': {'type': 'boolean', 'default': False},
                      'publish_at': _s('RFC3339 time to go public'), 'account': ACCOUNT}}},
    'yt_update_video': {'fn': lambda c, a: _sess(c, a).update_video(
        api.resolve(a['video'])['id'], a.get('title'), a.get('description'), a.get('tags'), a.get('privacy'),
        a.get('category_id'), a.get('made_for_kids')),
        'ring': 'creator', 'read_only': False,
        'description': 'Change a video\'s title / description / tags / privacy / category. Only given fields change.',
        'schema': {'type': 'object', 'required': ['video'], 'properties': {
            'video': _s('video id or url'), 'title': _s('new title'), 'description': _s('new description'),
            'tags': {'type': 'array', 'items': {'type': 'string'}}, 'privacy': _s('private|unlisted|public'),
            'category_id': _s('category id'), 'made_for_kids': {'type': 'boolean'}, 'account': ACCOUNT}}},
    'yt_set_thumbnail': {'fn': lambda c, a: _sess(c, a).set_thumbnail(api.resolve(a['video'])['id'], a['path']),
                         'ring': 'creator', 'read_only': False,
                         'description': 'Set a custom thumbnail from a local image (jpg/png, <2MB; channel must '
                                        'be verified).',
                         'schema': {'type': 'object', 'required': ['video', 'path'], 'properties': {
                             'video': _s('video id or url'), 'path': _s('image file'), 'account': ACCOUNT}}},
    'yt_delete_video': {'fn': lambda c, a: _sess(c, a).delete_video(api.resolve(a['video'])['id']),
                        'ring': 'creator', 'read_only': False, 'destructive': True,
                        'description': 'Delete one of the account\'s videos. Irreversible.',
                        'schema': {'type': 'object', 'required': ['video'], 'properties': {
                            'video': _s('video id or url'), 'account': ACCOUNT}}},
    'yt_analytics': {'fn': lambda c, a: _sess(c, a).analytics(a['start'], a['end'], a.get('metrics') or
                                                               'views,estimatedMinutesWatched,averageViewDuration,'
                                                               'subscribersGained,likes,comments',
                                                               a.get('dimensions', 'day'), a.get('filters'),
                                                               a.get('sort'), a.get('n', 200)),
                     'ring': 'creator', 'read_only': True,
                     'description': 'YouTube Analytics for the account\'s channel: rows per day (or video, '
                                    'country, trafficSource...) plus totals.',
                     'schema': {'type': 'object', 'required': ['start', 'end'], 'properties': {
                         'start': _s('YYYY-MM-DD'), 'end': _s('YYYY-MM-DD'),
                         'metrics': _s('comma list, e.g. views,estimatedMinutesWatched,subscribersGained'),
                         'dimensions': _s('day|month|video|country|trafficSourceType|deviceType or empty',
                                          default='day'),
                         'filters': _s('e.g. video==ID or country==US'), 'sort': _s('e.g. -views'),
                         'n': _i('rows', 200), 'account': ACCOUNT}}},
    'yt_my_comments': {'fn': lambda c, a: _sess(c, a).my_comments(a.get('n', 25), a.get('moderation', 'published')),
                       'ring': 'creator', 'read_only': True,
                       'description': 'Comments across the account\'s channel. moderation: published|'
                                      'heldForReview|likelySpam.',
                       'schema': {'type': 'object', 'properties': {
                           'n': _i('threads', 25, maximum=100), 'moderation': _s('published|heldForReview|likelySpam',
                                                                                 default='published'),
                           'account': ACCOUNT}}},
    'yt_moderate': {'fn': lambda c, a: _sess(c, a).moderate(a['comment_id'], a.get('status', 'published'),
                                                             bool(a.get('ban', False))),
                    'ring': 'creator', 'read_only': False, 'destructive': True,
                    'description': 'Approve, hold or reject a comment on the account\'s videos; ban=true with '
                                   'rejected also bans the author from the channel.',
                    'schema': {'type': 'object', 'required': ['comment_id'], 'properties': {
                        'comment_id': _s('comment id'), 'status': _s('published|heldForReview|rejected'),
                        'ban': {'type': 'boolean', 'default': False}, 'account': ACCOUNT}}},
}


def tool_list() -> list:
    return [{'name': n, 'description': t['description'], 'inputSchema': t['schema'],
             'annotations': {'readOnlyHint': t['read_only'], 'openWorldHint': True,
                             'destructiveHint': bool(t.get('destructive')),
                             'idempotentHint': t['read_only']},
             '_meta': {'ring': t['ring'], 'access': 'public' if t['ring'] == 'open' else 'owner'}}
            for n, t in TOOLS.items()]


def call_tool(name: str, args: dict, ctx: Optional[dict] = None) -> dict:
    t = TOOLS.get(name)
    ctx = ctx or {'owner': False}
    if not t:
        return _err_result(f'unknown tool {name!r}')
    if t['ring'] != 'open' and not ctx.get('owner'):
        return _err_result(f'{name} acts as a Google account and is for the box or its owner: send a '
                           'mod-protocol token (Authorization: Bearer) or call over stdio on the box')
    try:
        out = t['fn'](ctx, args or {})
    except KeyError as e:
        return _err_result(f'missing argument {e}')
    except (api.ApiError, auth.AuthError) as e:
        return _err_result(str(e))
    except Exception as e:      # noqa: BLE001 — the model gets the error, the server stays up
        return _err_result(f'{type(e).__name__}: {e}')
    return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, default=str)}],
            'structuredContent': out if isinstance(out, dict) else {'result': out},
            'isError': False}


def _err_result(msg: str) -> dict:
    return {'content': [{'type': 'text', 'text': msg}], 'isError': True}


def _methods(ctx: dict) -> Dict[str, Callable[[dict], Any]]:
    return {
        'initialize': lambda p: {
            'protocolVersion': p.get('protocolVersion') if p.get('protocolVersion') in SUPPORTED else PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS},
        'ping': lambda p: {},
        'tools/list': lambda p: {'tools': tool_list()},
        'tools/call': lambda p: call_tool(p.get('name', ''), p.get('arguments') or {}, ctx),
    }


def handle_message(msg: Any, ctx: Optional[dict] = None) -> Optional[Any]:
    """One JSON-RPC message (or batch) -> response, or None for notifications."""
    ctx = ctx or {'owner': False}
    if isinstance(msg, list):
        out = [r for r in (handle_message(m, ctx) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
    mid, method = msg.get('id'), msg.get('method', '')
    if mid is None:
        return None
    fn = _methods(ctx).get(method)
    if fn is None:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'no method {method}'}}
    try:
        return {'jsonrpc': '2.0', 'id': mid, 'result': fn(msg.get('params') or {})}
    except Exception as e:  # noqa: BLE001
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)}}


def main() -> None:
    """stdio transport: a process on the box, so owner=True."""
    ctx = {'owner': True}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        else:
            resp = handle_message(msg, ctx)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
