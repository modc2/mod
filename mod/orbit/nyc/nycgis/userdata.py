"""
nyc.userdata — datasets the owner added, served as first-class map layers.

The built-in catalogue is curated; this module is the door for everything
else. The deployment owner can add a dataset three ways, and each becomes a
toggleable layer in the app rail (category "Your data"), a tool answer, and
an MCP-visible layer, with no frontend work:

    geojson   an inline FeatureCollection, pasted or uploaded. Stored
              verbatim on disk; this is the "I have my own data" path.
    overlay   any Socrata dataset on NYC/NYS Open Data, described by the
              same spec the chat agent's `nyc_map` overlays use (points,
              heat or areas by zip/borough, with a SoQL where clause).
              Fetched through `scene.overlay_data`, so it refreshes on the
              overlay cache's cadence instead of freezing at save time.
    url       a remote GeoJSON file, refetched on a TTL.

Records live in ``~/.mod/nyc/data`` — deliberately NOT the cache directory,
because ``clear_cache`` must never delete the owner's data. Each dataset is
one ``<slug>.json`` record; an inline FeatureCollection sits beside it as
``<slug>.geojson``.

Who may write: the deployment owner, shown by a mod-protocol token
(`m.mod('auth')().token({})` — the same envelope every module in this fleet
verifies). The owner address is the box's own key by default, overridable
with ``NYC_OWNER``. Two write paths exist and both end at `require_writer`:

  - HTTP (`POST /data`, write tools over `/tools` and `/mcp`): the API
    verifies the Authorization header and sets a context grant around the
    call.
  - the in-app chat agent: `/chat` verifies the request's token and, only
    for the owner, launches the agent's MCP server with ``NYC_DATA_WRITE=1``
    in its environment — an unauthenticated chat gets an agent that simply
    does not have the write bit.

Reading is public, like every other layer: open data in, open layers out.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import sources as S

DATA_DIR = Path(os.path.expanduser(os.environ.get('NYC_DATA_DIR', '~/.mod/nyc/data')))

MAX_DATASETS = 24
MAX_FEATURES = 50_000
MAX_BYTES = 15_000_000
TTL = 6 * 3600                      # overlay/url re-fetch cadence
TOKEN_MAX_AGE = int(os.environ.get('NYC_TOKEN_MAX_AGE', 7 * 86_400))

_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{1,47}$')

KINDS = ('geojson', 'overlay', 'url')


class AuthError(Exception):
    """The caller may not write the data store."""


# ─────────────────────────────────────────────────────────────── identity

# Set by the API around a dispatch whose Authorization header verified to the
# owner; read by require_writer. A contextvar, not a global, because tool
# calls for different requests interleave on the server's thread pool.
_WRITER: contextvars.ContextVar[bool] = contextvars.ContextVar('nyc_writer', default=False)


def _auth():
    # Only ever the ALREADY-imported protocol: the API and the CLI import
    # `mod` at startup, which is exactly where token verification matters. A
    # fresh import from here (tests, the MCP stdio subprocess) is fragile and
    # can leave a half-initialised package in sys.modules — in those contexts
    # identity simply reports unknown and the env-var grant does the gating.
    import sys as _sys
    m = _sys.modules.get('mod')
    if m is None or not hasattr(m, 'mod'):
        raise RuntimeError('mod protocol not loaded in this process')
    return m.mod('auth')(max_age=TOKEN_MAX_AGE)


def owner_address() -> Optional[str]:
    """The deployment owner: ``NYC_OWNER`` if set, else the box's own key."""
    env = os.environ.get('NYC_OWNER')
    if env:
        return env.lower()
    try:
        return str(_auth().key_address()).lower()
    except Exception:
        return None


def verify(token: Optional[str]) -> Optional[str]:
    """The signer address of a mod-protocol token, or None."""
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not token:
        return None
    try:
        return str(_auth().verify(token).get('key') or '').lower() or None
    except Exception:
        return None


def is_owner_token(token: Optional[str]) -> bool:
    address = verify(token)
    owner = owner_address()
    return bool(address and owner and address == owner)


def identity(token: Optional[str]) -> dict:
    """Who a token verifies to, and what that standing unlocks here.

    The owner sign-in handshake: the app sends its stored mod-protocol token
    and learns whether the signer IS the deployment owner. Anonymous and
    garbage tokens are not errors — they are simply nobody.
    """
    address = verify(token)
    owner = owner_address()
    is_owner = bool(address and owner and address == owner)
    return {
        'mod': 'nyc',
        'address': address,
        'owner': owner,
        'is_owner': is_owner,
        'writable': is_owner,
        'token_max_age': TOKEN_MAX_AGE,
        'unlocks': (
            ['save datasets as map layers (YOUR DATA, POST /data)',
             'the ASK NYC agent may save data when asked']
            if is_owner else []),
    }


def grant_writer(granted: bool) -> contextvars.Token:
    """Mark this context as owner-verified; returns the reset token."""
    return _WRITER.set(bool(granted))


def reset_writer(token: contextvars.Token) -> None:
    _WRITER.reset(token)


def writer_allowed() -> bool:
    return _WRITER.get() or os.environ.get('NYC_DATA_WRITE', '') not in ('', '0', 'false')


def require_writer() -> None:
    if not writer_allowed():
        raise AuthError(
            'saving data is owner-only. Send the owner\'s mod-protocol token as '
            '`Authorization: Bearer <token>` (mint one with '
            'm.mod("auth")().token({}) on the box that holds the key, or sign '
            'in with the owner wallet in the app).')


# ─────────────────────────────────────────────────────────────── the store

def _record_path(slug: str) -> Path:
    return DATA_DIR / f'{slug}.json'


def _geojson_path(slug: str) -> Path:
    return DATA_DIR / f'{slug}.geojson'


def slugs() -> List[str]:
    if not DATA_DIR.is_dir():
        return []
    return sorted(p.stem for p in DATA_DIR.glob('*.json'))


def _read(slug: str) -> dict:
    try:
        return json.loads(_record_path(slug).read_text())
    except FileNotFoundError:
        raise KeyError(f'no saved dataset {slug!r}; saved: {slugs()}')


def _slugify(title: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', str(title or '').lower()).strip('-')[:48]
    if not _SLUG.match(s or ''):
        raise ValueError('title must contain at least two letters or digits')
    return s


def _builtin_ids() -> List[str]:
    from . import layers as L           # lazy: layers imports this module
    return [l['id'] for l in L.LAYERS] + ['housing_prices', 'population', 'sales']


def _geometry_of(fc: dict) -> str:
    for f in fc.get('features', []):
        t = ((f.get('geometry') or {}).get('type') or '')
        if t.startswith('Point') or t.startswith('MultiPoint'):
            return 'point'
        if 'LineString' in t:
            return 'line'
        if 'Polygon' in t:
            return 'polygon'
    return 'point'


def _check_fc(fc: Any) -> dict:
    if not isinstance(fc, dict) or fc.get('type') != 'FeatureCollection' \
            or not isinstance(fc.get('features'), list):
        raise ValueError('geojson must be a FeatureCollection with a features list')
    if not fc['features']:
        raise ValueError('the FeatureCollection has no features')
    if len(fc['features']) > MAX_FEATURES:
        raise ValueError(f'too many features ({len(fc["features"]):,}; max {MAX_FEATURES:,})')
    blob = json.dumps(fc)
    if len(blob) > MAX_BYTES:
        # Heavy polygon files usually survive simplification; points rarely need it.
        fc = S.simplify_geojson(fc)
        blob = json.dumps(fc)
        if len(blob) > MAX_BYTES:
            raise ValueError(f'GeoJSON too large ({len(blob) / 1e6:.1f} MB even '
                             f'simplified; max {MAX_BYTES / 1e6:.0f} MB)')
    return fc


def _fetch_url(url: str) -> dict:
    r = requests.get(url, timeout=120, headers={'User-Agent': S.USER_AGENT})
    r.raise_for_status()
    return _check_fc(r.json())


def build_spec(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    One request shape → one validated record, shared by the HTTP route and
    the `nyc_add_data` tool. Exactly one of ``geojson`` / ``dataset`` /
    ``url`` picks the kind. Validation runs the fetch NOW, so a bad dataset
    id, a wrong column or a dead URL fails at save time with the real error,
    not at midnight when the layer first draws.
    """
    title = str(body.get('title') or '').strip()
    if not title:
        raise ValueError('title is required')
    slug = _slugify(title)
    if slug in _builtin_ids():
        raise ValueError(f'{slug!r} is a built-in layer; pick another title')

    picked = [k for k in ('geojson', 'dataset', 'url') if body.get(k)]
    if len(picked) != 1:
        raise ValueError('pass exactly one of: geojson (a FeatureCollection), '
                         'dataset (a Socrata id like "erm2-nwe9"), or url '
                         '(a GeoJSON file)')

    record: Dict[str, Any] = {
        'slug': slug,
        'title': title[:80],
        'description': str(body.get('description') or '').strip()[:400],
        'added_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

    if picked[0] == 'geojson':
        fc = _check_fc(body['geojson'])
        record.update(kind='geojson', geometry=_geometry_of(fc),
                      features=len(fc['features']),
                      source={'name': 'Owner upload', 'dataset': slug,
                              'url': '', 'portal': 'this deployment'})
        record['_fc'] = fc                      # stripped before the record is written

    elif picked[0] == 'dataset':
        from . import scene                     # lazy: scene imports layers imports this
        spec = scene.normalize_overlay({
            k: body.get(k) for k in
            ('dataset', 'domain', 'mode', 'where', 'by', 'column', 'value',
             'per_capita', 'lat', 'lng', 'label', 'limit') if body.get(k) is not None
        } | {'title': title})
        data = scene.overlay_data(spec)         # validates columns + runs the query
        meta = data.get('meta', {})
        record.update(kind='overlay', spec=spec,
                      geometry='polygon' if spec['mode'] == 'areas' else 'point',
                      features=len(data.get('features', [])),
                      source={'name': f"Socrata {spec['domain']}",
                              'dataset': spec['dataset'],
                              'url': meta.get('url', ''),
                              'portal': spec['domain']})

    else:
        url = str(body['url']).strip()
        if not url.lower().startswith('https://'):
            raise ValueError('url must be https://')
        fc = _fetch_url(url)
        record.update(kind='url', spec={'url': url}, geometry=_geometry_of(fc),
                      features=len(fc['features']),
                      source={'name': 'Remote GeoJSON', 'dataset': slug,
                              'url': url, 'portal': url.split('/')[2]})
        S.cache_write(f'userdata-{slug}', fc)

    return record


def add(body: Dict[str, Any], by: Optional[str] = None) -> dict:
    """Validate and persist one dataset; returns the saved record."""
    require_writer()
    if len(slugs()) >= MAX_DATASETS:
        raise ValueError(f'dataset limit reached ({MAX_DATASETS}); remove one first')
    record = build_spec(body)
    if by:
        record['added_by'] = by
    fc = record.pop('_fc', None)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if fc is not None:
        _geojson_path(record['slug']).write_text(json.dumps(fc))
    _record_path(record['slug']).write_text(json.dumps(record, indent=2))
    return record


def remove(slug: str) -> dict:
    require_writer()
    record = _read(slug)                        # KeyError with the saved list if absent
    _record_path(slug).unlink(missing_ok=True)
    _geojson_path(slug).unlink(missing_ok=True)
    S.cache_clear(f'userdata-{slug}')
    return {'removed': slug, 'title': record.get('title'), 'remaining': slugs()}


def refresh(slug: str) -> dict:
    """Refetch a fetch-backed dataset now; inline GeoJSON has nothing to refresh."""
    record = _read(slug)
    if record['kind'] == 'overlay':
        from . import scene
        spec = record['spec']
        S.cache_clear(f'overlay-{scene._overlay_key(spec)}')
        fc = scene.overlay_data(spec)
    elif record['kind'] == 'url':
        fc = S.cache_write(f'userdata-{slug}', _fetch_url(record['spec']['url']))
    else:
        fc = data(slug)
    return {'slug': slug, 'refreshed': record['kind'] != 'geojson',
            'features': len(fc.get('features', []))}


def data(slug: str) -> dict:
    """The dataset as a FeatureCollection — what the map actually draws."""
    record = _read(slug)
    if record['kind'] == 'geojson':
        return json.loads(_geojson_path(slug).read_text())
    if record['kind'] == 'overlay':
        from . import scene
        return scene.overlay_data(record['spec'])
    return S.cached(f'userdata-{slug}', TTL,
                    lambda: _fetch_url(record['spec']['url']))


def info(slug: str) -> dict:
    return _read(slug)


def list_() -> dict:
    items = []
    for s in slugs():
        try:
            items.append(_read(s))
        except KeyError:
            continue
    return {'count': len(items), 'max': MAX_DATASETS, 'owner': owner_address(),
            'datasets': items}


def catalog_entries() -> List[dict]:
    """These datasets as layer-catalogue entries — the app rail reads this."""
    entries = []
    for s in slugs():
        try:
            r = _read(s)
        except KeyError:
            continue
        mode = (r.get('spec') or {}).get('mode') if r['kind'] == 'overlay' else None
        kind = ('choropleth' if mode == 'areas'
                else 'heatmap' if mode == 'heat'
                else {'point': 'point', 'line': 'line', 'polygon': 'polygon'}[r['geometry']])
        entries.append({
            'id': s,
            'title': r['title'],
            'category': 'Your data',
            'kind': kind,
            'geometry': r['geometry'],
            'default_on': False,
            'custom': True,
            'description': r.get('description') or
                           f"Added by the owner ({r['kind']}, {r.get('features', '?')} features).",
            'endpoint': f'/layers/{s}',
            'source': r.get('source') or {'name': 'Owner data', 'dataset': s,
                                          'url': '', 'portal': ''},
        })
    return entries
