"""catalog — every model a provider serves, and how each one has scored.

The arena scores one model at a time. This is the layer that turns that into a
question about a whole provider: "of the 128 text models Venice serves, which
ones hold up?" It does two things and keeps them apart:

    fetch(provider)     the provider's own model list, normalised to one shape
    results()           the latest score per model, from every finished round

and `listing()` joins them, with the counts, filters and search the console
draws its header from.

    provider     where the list comes from               key needed to LIST?
    venice       api.venice.ai/api/v1/models?type=text   no
    openrouter   openrouter.ai/api/v1/models             no
    mock         written down here                       no — offline

WHY THE LIST IS CACHED ON DISK
    Local first. The catalogs are public and change a few times a day, so the
    last good copy lives in ~/.mod/redblue/catalog/<provider>.json and is
    served whenever the provider is unreachable — a scoreboard should not go
    blank because a gateway did. `refresh=1` forces a fetch.

WHY RESULTS ARE AN INDEX, NOT A SCAN OF ROUNDS
    A sweep over every OpenRouter model is ~465 rounds, and round records are
    pruned at RB_KEEP_ROUNDS (300). Scanning rounds would silently forget the
    first third of a sweep. So every finished round writes a compact summary
    per model into results.json, which is never pruned and reads in O(1).

Only text-in/text-out chat models are listed: the game is a conversation, and
an image or embedding model cannot refuse anything.
"""

import json
import os
import threading
import time
import urllib.request

from . import models, store

TTL = int(os.environ.get('RB_CATALOG_TTL', 6 * 3600))
CATALOG_DIR = os.path.join(store.DIR, 'catalog')
RESULTS = os.path.join(store.DIR, 'results.json')
_lock = threading.Lock()

PROVIDERS = {
    'venice': {
        'url': 'https://api.venice.ai/api/v1/models?type=text',
        'site': 'https://venice.ai',
        'note': 'private-by-default inference; many models are uncensored on '
                'purpose, which is exactly what a refusal score should show',
    },
    'openrouter': {
        'url': 'https://openrouter.ai/api/v1/models',
        'site': 'https://openrouter.ai',
        'note': 'one key, every major lab; ids are <lab>/<model>',
        # The openrouter module keeps its own copy of the same list — used when
        # this box cannot reach openrouter.ai and has no cache of its own yet.
        'fallback': '~/.mod/openrouter/models.json',
    },
    'mock': {
        'url': None,
        'note': 'offline targets with a written-down policy — free, instant, '
                'and how the sweep itself is tested',
    },
}

MOCKS = [
    {'id': 'naive', 'name': 'mock naive — falls for framing',
     'description': 'refuses plain harmful asks, complies with roleplay / '
                    'fiction / override framings'},
    {'id': 'strict', 'name': 'mock strict — refuses everything',
     'description': 'the refuse-everything defense; should score ~0'},
    {'id': 'compliant', 'name': 'mock compliant — refuses nothing',
     'description': 'answers anything; the floor'},
]


class CatalogError(Exception):
    pass


# ── fetch + normalise ────────────────────────────────────────────

def _provider(name):
    name = str(name or '').lower().strip()
    if name not in PROVIDERS:
        raise CatalogError(f'no catalog for {name!r} — one of '
                           f'{", ".join(PROVIDERS)}')
    return name


def _cache_path(provider):
    return os.path.join(CATALOG_DIR, provider + '.json')


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={'user-agent': 'redblue/catalog'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _usd_per_m(value, scale=1.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else round(v * scale, 4)


def _venice(raw):
    out = []
    for m in raw.get('data') or []:
        if m.get('type', 'text') != 'text':
            continue
        spec = m.get('model_spec') or {}
        price = spec.get('pricing') or {}
        caps = spec.get('capabilities') or {}
        pin = _usd_per_m((price.get('input') or {}).get('usd'))
        pout = _usd_per_m((price.get('output') or {}).get('usd'))
        out.append({
            'id': m['id'], 'name': spec.get('name') or m['id'],
            'online': not spec.get('offline', False),
            'private': spec.get('privacy') == 'private',
            'free': pin == 0 and pout == 0,
            'reasoning': bool(caps.get('supportsReasoning')),
            'context': spec.get('availableContextTokens') or m.get('context_length'),
            'price_in': pin, 'price_out': pout,
            'traits': spec.get('traits') or [],
            'description': (spec.get('description') or '')[:280],
        })
    return out


def _openrouter(raw):
    data = raw.get('data') if isinstance(raw, dict) else raw
    out = []
    for m in data or []:
        arch = m.get('architecture') or {}
        if 'text' not in (arch.get('output_modalities') or ['text']):
            continue
        price = m.get('pricing') or {}
        # OpenRouter quotes USD per token as a string; -1 means a router whose
        # price depends on where it routes. Neither is free and neither is a
        # number to sort on, so -1 becomes None rather than a negative price.
        pin = _usd_per_m(price.get('prompt'), 1e6)
        pout = _usd_per_m(price.get('completion'), 1e6)
        top = m.get('top_provider') or {}
        out.append({
            'id': m['id'], 'name': m.get('name') or m['id'],
            'online': True,
            'private': False,
            'moderated': bool(top.get('is_moderated')),
            'free': pin == 0 and pout == 0,
            'reasoning': 'reasoning' in (m.get('supported_parameters') or []),
            'context': m.get('context_length'),
            'price_in': pin, 'price_out': pout,
            'description': (m.get('description') or '')[:280],
        })
    return out


def _mock(_raw):
    return [dict(m, online=True, private=True, free=True, reasoning=False,
                 context=None, price_in=0, price_out=0) for m in MOCKS]


NORMALISE = {'venice': _venice, 'openrouter': _openrouter, 'mock': _mock}


def fetch(provider, refresh=False):
    """The provider's model list, normalised. Cache first, network on expiry,
    stale cache if the network fails, and say which one answered."""
    provider = _provider(provider)
    if provider == 'mock':
        return {'provider': provider, 'source': 'builtin', 'fetched': None,
                'models': _mock(None)}
    path = _cache_path(provider)
    cached = _read(path)
    fresh = cached and time.time() - cached.get('fetched', 0) < TTL
    if cached and fresh and not refresh:
        return dict(cached, source='cache')
    try:
        raw = _get(PROVIDERS[provider]['url'])
        rec = {'provider': provider, 'fetched': int(time.time()),
               'models': NORMALISE[provider](raw)}
        os.makedirs(CATALOG_DIR, exist_ok=True)
        _write(path, rec)
        return dict(rec, source='live')
    except Exception as e:                              # noqa: BLE001
        if cached:
            return dict(cached, source='stale-cache', error=str(e))
        fb = PROVIDERS[provider].get('fallback')
        raw = _read(os.path.expanduser(fb)) if fb else None
        if raw:
            return {'provider': provider, 'fetched': None, 'source': fb,
                    'error': str(e), 'models': NORMALISE[provider](raw)}
        raise CatalogError(f'{provider} catalog unreachable and nothing cached: '
                           f'{e}')


# ── results index ────────────────────────────────────────────────

def summarise(record):
    """One finished round → the per-model summary the catalog shows.

    The headline is the `none` defense when the round ran it — that is the bare
    model, which is what "how safe is this model" means. Otherwise it is the
    best defense, labelled as such, so a number is never read as the model's
    own when a pipeline earned it.
    """
    scores = record.get('scores') or []
    if not scores:
        return None
    bare = next((s for s in scores if s['defense'] == 'none'), None)
    head = bare or max(scores, key=lambda s: s['safety_score'])
    errors = sum(s.get('errors', 0) for s in scores)
    fired = sum(s.get('attacks', 0) for s in scores)
    return {
        'model': record.get('model'), 'round': record.get('id'),
        'sweep': record.get('sweep'), 'at': record.get('finished')
        or int(time.time()), 'judge': record.get('judge'),
        'defense': head['defense'], 'bare': bool(bare),
        'safety_score': head['safety_score'],
        'refusal_rate': head['refusal_rate'],
        'over_refusal': head['over_refusal'],
        'breach_rate': head['breach_rate'],
        'leak_rate': head.get('leak_rate', 0),
        'attacks': head.get('attacks', 0), 'errors': errors,
        # A round where nothing reached the model is not a score, it is a
        # missing key or a dead model id — flag it instead of ranking a 0.
        'failed': fired == 0 and errors > 0,
        'by_defense': {s['defense']: s['safety_score'] for s in scores},
    }


def record(round_record):
    """Fold a finished round into results.json. Called by the arena."""
    summary = summarise(round_record)
    if not summary or not summary.get('model'):
        return None
    with _lock:
        data = _read(RESULTS) or {}
        prev = data.get(summary['model'])
        if summary['failed'] and prev and not prev.get('failed'):
            # A dead key or a rate limit today does not erase the score the
            # model earned last week — keep it, note the failure beside it.
            prev['last_error_at'] = summary['at']
            prev['last_error_round'] = summary['round']
        else:
            data[summary['model']] = summary
        os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
        _write(RESULTS, data)
    return summary


def results(provider=None):
    data = _read(RESULTS) or {}
    if provider:
        pre = provider + ':'
        data = {k: v for k, v in data.items() if k.startswith(pre)}
    return data


def forget(model):
    with _lock:
        data = _read(RESULTS) or {}
        gone = data.pop(model, None)
        _write(RESULTS, data)
    return {'forgot': model, 'existed': gone is not None}


# ── the joined view ──────────────────────────────────────────────

SCOPES = ('all', 'tested', 'untested', 'online', 'private', 'free', 'failed')


def listing(provider='venice', q=None, scope='all', sort='name', refresh=False,
            limit=0):
    """The catalog joined with scores, plus the counts for the header.

    Counts are over the whole catalog, not the filtered view — the header says
    what exists, the table says what matches.
    """
    cat = fetch(provider, refresh=_flag(refresh))
    provider = cat['provider']
    scored = results(provider)
    rows = []
    for m in cat['models']:
        full = f'{provider}:{m["id"]}'
        rows.append(dict(m, model=full, result=scored.get(full)))

    def tested(r):
        return bool(r['result']) and not r['result'].get('failed')

    stats = {
        'models': len(rows),
        'online': sum(1 for r in rows if r.get('online')),
        'private': sum(1 for r in rows if r.get('private')),
        'free': sum(1 for r in rows if r.get('free')),
        'tested': sum(1 for r in rows if tested(r)),
        'failed': sum(1 for r in rows if r['result'] and r['result'].get('failed')),
    }
    stats['untested'] = stats['models'] - stats['tested']

    scope = (scope or 'all').lower()
    if scope not in SCOPES:
        raise CatalogError(f'scope {scope!r} — one of {", ".join(SCOPES)}')
    keep = {
        'all': lambda r: True, 'tested': tested,
        'untested': lambda r: not tested(r),
        'online': lambda r: r.get('online'),
        'private': lambda r: r.get('private'),
        'free': lambda r: r.get('free'),
        'failed': lambda r: bool(r['result']) and r['result'].get('failed'),
    }[scope]
    view = [r for r in rows if keep(r)]
    if q:
        terms = str(q).lower().split()
        view = [r for r in view if all(
            t in (r['id'] + ' ' + r['name'] + ' ' + (r.get('description') or ''))
            .lower() for t in terms)]

    if sort == 'safety':
        view.sort(key=lambda r: (r['result'] is None or r['result'].get('failed'),
                                 -((r['result'] or {}).get('safety_score') or 0)))
    elif sort == 'price':
        view.sort(key=lambda r: (r.get('price_out') is None, r.get('price_out') or 0))
    else:
        view.sort(key=lambda r: r['name'].lower())
    if limit:
        view = view[:int(limit)]

    return {'provider': provider, 'source': cat.get('source'),
            'fetched': cat.get('fetched'), 'error': cat.get('error'),
            'ready': models.has_key(provider), 'scope': scope, 'q': q or '',
            'stats': stats, 'shown': len(view), 'models': view,
            'providers': sorted(PROVIDERS)}


# ── plumbing ─────────────────────────────────────────────────────

def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, default=str)
    os.replace(tmp, path)


def _flag(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() not in ('0', 'false', 'no', 'none', '')
