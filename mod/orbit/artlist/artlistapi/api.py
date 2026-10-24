"""Typed queries against Artlist's search GraphQL, normalized to flat dicts.

Schema source: validation-error leakage (introspection is disabled upstream).
Every query here was run against the live endpoint on 2026-10-05 and returned
data. If Artlist renames a field the error message will name the replacement
("Did you mean ...") — that is also how to extend this file.

Upstream quirks to respect:
- Sfx items reuse Song field names (the list field is literally `songs`).
- sfxList has no `take` — trim client-side. Its `page` is Float!, not Int!.
- Clip.duration is Int milliseconds; Sfx/Song duration is a "m:ss" string.
- maFootage validates but its resolver 500s on every execution (upstream
  Motion Array source is dead or auth-gated) — footage goes through clipList.
"""
from . import client

SONG_FIELDS = ('songId songName artistName albumName albumId artistId duration '
               'sitePlayableFilePath tags { name }')
SFX_SORTS = ('NEWEST', 'TOP_DOWNLOADS', 'STAFF_PICKS')  # confirmed enum values


def _song(s: dict) -> dict:
    return {
        'id': s.get('songId'),
        'name': s.get('songName'),
        'artist': s.get('artistName'),
        'album': s.get('albumName'),
        'album_id': s.get('albumId'),
        'duration': s.get('duration'),
        'preview_url': s.get('sitePlayableFilePath') or None,
        'tags': [t['name'] for t in s.get('tags') or []],
        'page_url': f"https://artlist.io/song/{s.get('songId')}/",
    }


def music(term: str = '', k: int = 10, page: int = 1, sort: int = 1, vocal: int = 0) -> dict:
    """Search royalty-free songs. sort: songSortType int (1 = relevance). vocal: vocalMenuId (0 = all)."""
    q = ('query ($page:Int!,$sort:Int!,$take:Int!,$vocal:Int!,$term:String) {'
         ' songList(page:$page, songSortType:$sort, take:$take, vocalMenuId:$vocal, searchTerm:$term)'
         ' { totalResults songs { ' + SONG_FIELDS + ' } } }')
    data = client.gql(q, {'page': int(page), 'sort': int(sort), 'take': int(k),
                          'vocal': int(vocal), 'term': term or None})
    r = data['songList']
    return {'total': r['totalResults'], 'results': [_song(s) for s in r['songs']]}


def songs(ids: list[str]) -> list[dict]:
    q = 'query ($ids:[String!]!) { songs(ids:$ids) { ' + SONG_FIELDS + ' } }'
    return [_song(s) for s in client.gql(q, {'ids': [str(i) for i in ids]})['songs']]


def albums(ids: list[str]) -> list[dict]:
    q = 'query ($ids:[String!]!) { albums(ids:$ids) { __typename } }'
    return client.gql(q, {'ids': [str(i) for i in ids]})['albums']


def artist(ids: list[str]) -> list[dict]:
    q = ('query ($ids:[String!]!) { artist(ids:$ids) '
         '{ id name bio profileImage albums { id name } } }')
    return client.gql(q, {'ids': [str(i) for i in ids]})['artist']


def sfx(term: str = '', k: int = 10, page: int = 1, sort: str = 'NEWEST', category_ids: str = '') -> dict:
    """Search sound effects. sort: NEWEST | TOP_DOWNLOADS | STAFF_PICKS."""
    sort = str(sort).upper()
    if sort not in SFX_SORTS:
        raise client.ArtlistError(f'sfx sort must be one of {SFX_SORTS}')
    q = ('query ($page:Float!,$sort:SfxListRequestSortByOptions!,$term:String!,$cat:String!) {'
         ' sfxList(page:$page, sortBy:$sort, term:$term, categoryIds:$cat)'
         ' { page songs { songId songName artistName duration sitePlayableFilePath'
         '   sonCategories { id name } } } }')
    data = client.gql(q, {'page': float(page), 'sort': sort, 'term': term, 'cat': category_ids})
    items = (data['sfxList'].get('songs') or [])[:int(k)]
    return {'results': [{
        'id': s.get('songId'), 'name': s.get('songName'), 'artist': s.get('artistName'),
        'duration': s.get('duration'), 'preview_url': s.get('sitePlayableFilePath') or None,
        'categories': [c['name'] for c in s.get('sonCategories') or []],
    } for s in items]}


def footage(term: str = '', k: int = 10, page: int = 1) -> dict:
    """Search stock footage (Artgrid clips via clipList; maFootage is broken upstream)."""
    q = ('{ clipList(searchTerm:' + client.jstr(term) + ', page:' + str(int(page)) + ')'
         ' { totalExact exactResults { id storyId clipName storyName thumbnailUrl duration'
         '   categories { name } tags { name } } totalSimilar } }')
    r = client.gql(q)['clipList']
    return {'total': r['totalExact'], 'similar': r['totalSimilar'],
            'results': [_clip(c) for c in (r['exactResults'] or [])[:int(k)]]}


def _clip(c: dict) -> dict:
    return {
        'id': c.get('id'), 'story_id': c.get('storyId'),
        'name': c.get('clipName'), 'story': c.get('storyName'),
        'thumbnail_url': c.get('thumbnailUrl'),
        'duration_s': round((c.get('duration') or 0) / 1000, 1),
        'categories': [x['name'] for x in c.get('categories') or []],
        'tags': [x['name'] for x in c.get('tags') or []],
    }


def clip(clip_id: int) -> dict:
    q = 'query ($id:Int!) { clip(id:$id) { id storyId clipName storyName thumbnailUrl duration } }'
    return _clip(client.gql(q, {'id': int(clip_id)})['clip'])


def story(story_id: int, page: int = 1) -> dict:
    q = 'query ($id:Int!,$p:Int!) { story(id:$id, page:$p) { id name clips { id clipName duration thumbnailUrl } } }'
    s = client.gql(q, {'id': int(story_id), 'p': int(page)})['story']
    return {'id': s['id'], 'name': s['name'], 'clips': [_clip(c) for c in s.get('clips') or []]}


def templates(term: str = '', k: int = 10, page: int = 1, sort: str = 'TOP_DOWNLOADS') -> dict:
    """Search video templates (After Effects / Premiere / FCP / Resolve)."""
    sort = str(sort).upper()
    if sort not in SFX_SORTS:
        raise client.ArtlistError(f'templates sort must be one of {SFX_SORTS}')
    q = ('query ($p:TemplatesByTermSearchPayload!) { templatesList(payload:$p)'
         ' { id name artistName rate downloadCount tags categories'
         '   previewVideoFileUrl thumbnailUrl files { softwareName softwareVersionName } } }')
    payload = {'page': int(page), 'take': int(k), 'searchTerms': term, 'sortBy': sort}
    return {'results': [{
        'id': t.get('id'), 'name': t.get('name'), 'artist': t.get('artistName'),
        'rate': t.get('rate'), 'downloads': t.get('downloadCount'),
        'categories': t.get('categories') or [], 'tags': t.get('tags') or [],
        'preview_video_url': t.get('previewVideoFileUrl'), 'thumbnail_url': t.get('thumbnailUrl'),
        'software': [f"{f['softwareName']}" for f in t.get('files') or []],
    } for t in client.gql(q, {'p': payload})['templatesList'] or []]}


def voices(page: int = 1, k: int = 10) -> dict:
    """AI voiceover voices (Cartesia-provided); preview audio lives on each accent."""
    q = ('query ($page:Int!,$take:Int!) { voices(page:$page, take:$take)'
         ' { id name gender language age description providerName'
         '   accents { id name previewAudioUrl } } }')
    return {'results': [{
        'id': v.get('id'), 'name': v.get('name'), 'gender': v.get('gender'),
        'language': v.get('language'), 'age': v.get('age'), 'provider': v.get('providerName'),
        'accents': [{'name': a.get('name'), 'preview_url': a.get('previewAudioUrl')}
                    for a in v.get('accents') or []],
    } for v in client.gql(q, {'page': int(page), 'take': int(k)})['voices'] or []]}
