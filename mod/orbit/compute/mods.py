"""The mod lane — reading a market through the module that fronts it.

Three of the markets in this registry also ship as their own modules on this
box: **targon** (Bittensor SN4), **lium** (SN51) and **cathedral** (confidential
TDX). Each one is a server with its own console, MCP tools and key file, and
each is a front for the same upstream this module's adapters call directly.

This lane asks those modules the question the direct lane asks the upstream,
and puts the two answers next to each other. That comparison is the point: a
sibling module pinned to an API version its market has retired looks perfectly
healthy from the outside and answers an error here, and nothing else in the
fleet would say so.

Ids are the upstream's own, so an offer found through a mod round-trips into
`compute_quote` / `compute_rent` unchanged — the mod lane is a view, never a
second way to spend money.

Where a module proxies its upstream path for path, the module's own adapter
reads it with only the base URL moved. Where a module publishes its own
flattened row instead (lium), the mapping lives here.
"""

import concurrent.futures
import json
import os
import time

import providers as P
from providers.base import ProviderError, http, num, offer

HERE = os.path.dirname(os.path.abspath(__file__))
ORBIT = os.path.dirname(HERE)           # …/orbit — where the sibling modules live
HOST = os.environ.get('COMPUTE_MOD_HOST', '127.0.0.1')
TIMEOUT = float(os.environ.get('COMPUTE_MOD_TIMEOUT', '12'))


# ── reading a sibling module ─────────────────────────────────────────────

def _rebase(name, base):
    """This module's own adapter, pointed at the sibling module's server.

    A module that proxies its upstream keeps the paths, so the adapter needs
    no other change. The one thing that differs is failure: a mod answers 200
    with an `{"error": …}` body where an upstream would answer 4xx, and that
    has to become an error here or it reads as an empty market.
    """
    p = P.REGISTRY[name]()
    p.upstream = base.rstrip('/')
    inner = p.get

    def guarded(path, *a, **kw):
        r = inner(path, *a, **kw)
        if isinstance(r, dict) and r.get('error') and len(r) <= 3:
            raise ProviderError(f'{name} mod {path} → {r["error"]}', provider=name)
        return r

    p.get = guarded
    return p


def _proxied(name):
    return lambda base, f: _rebase(name, base).search(f)


def _lium_offers(base, f):
    """lium publishes its own flattened executor row, so it maps here."""
    r = http('GET', base + '/executors', params={'limit': 500}, provider='lium',
             timeout=TIMEOUT)
    rows = r if isinstance(r, list) else (r.get('executors') or [])
    if not rows and isinstance(r, dict) and r.get('error'):
        raise ProviderError(f'lium mod /executors → {r["error"]}', provider='lium')
    out = []
    for e in rows:
        free = int(num(e.get('available_gpu_count'), 0) or 0)
        count = int(num(e.get('gpu_count'), 1) or 1)
        per_gpu = num(e.get('price_per_gpu_hr'))
        out.append(offer(
            'lium', e.get('id'),
            usd_hr=num(e.get('price_per_hr'),
                       per_gpu * count if per_gpu is not None else None),
            gpu=e.get('gpu'), gpus=count,
            vram_gb=num(e.get('vram_gb_per_gpu')),
            cpu=num(e.get('cpu_count')), ram_gb=num(e.get('ram_gb')),
            disk_gb=num(e.get('disk_gb')),
            region=e.get('location') or e.get('country'),
            available=free > 0,
            note=f"tier {e.get('tier')} · {free}/{count} free · "
                 f"reliability {e.get('reliability')}",
            raw=e))
    return [o for o in out if f.match(o)]


# Which module fronts which market, what it is read through, and the port it
# answers on when its config.json is missing.
LANES = {
    'targon': {
        'port': 50440,
        'read': _proxied('targon'),
        'reads': 'GET /inventory — the module proxies the Targon Hub path for path',
    },
    'lium': {
        'port': 50430,
        'read': _lium_offers,
        'reads': 'GET /executors — the module publishes its own flattened row',
    },
    'cathedral': {
        'port': 50390,
        'read': _proxied('cathedral'),
        'reads': 'GET /profiles — the module proxies the confidential catalog',
    },
}


def _config(name):
    """The sibling module's own config.json, when it is installed here."""
    try:
        with open(os.path.join(ORBIT, name, 'config.json')) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _base(name, cfg):
    return f"http://{HOST}:{int(cfg.get('port') or LANES[name]['port'])}"


def _console(name, cfg, base):
    urls = cfg.get('urls') if isinstance(cfg.get('urls'), dict) else {}
    return urls.get('app') or (base + (cfg.get('base_path') or f'/{name}'))


def _cheapest(offers):
    priced = [o['usd_hr'] for o in offers if o.get('usd_hr') is not None]
    return min(priced) if priced else None


def _parity(card):
    """One sentence on whether the module still agrees with its own market."""
    direct, err = card.get('direct') or {}, card.get('error')
    if err and direct.get('count'):
        return (f"the module is not serving the market it fronts — the direct lane "
                f"reads {direct['count']} offers from the same upstream")
    if err:
        return 'neither this module nor the direct lane can read this market right now'
    if 'count' not in direct:
        return 'read through the module; not compared with the direct lane'
    if not card['count'] and direct['count']:
        return f"the module answers but returns nothing; direct reads {direct['count']}"
    a, b = card.get('cheapest_usd_hr'), direct.get('cheapest_usd_hr')
    if a is not None and b is not None and abs(a - b) > max(0.01, 0.01 * b):
        return (f"prices differ: ${a}/hr through the module vs ${b}/hr direct — "
                f"one of the two is reading a stale catalog")
    if card['count'] != direct['count']:
        return (f"{card['count']} offers through the module vs {direct['count']} "
                f"direct — the module filters or pages differently")
    return f"agrees with the direct lane on {card['count']} offers"


# ── one module ───────────────────────────────────────────────────────────

def one(name, keys=None, compare=True, sample=6, **filters):
    """Everything the console shows for a single mod-fronted market."""
    if name not in LANES:
        raise ProviderError(f'no mod lane for {name} — have {", ".join(LANES)}')
    lane, cfg = LANES[name], _config(name)
    base = _base(name, cfg)
    provider = P.REGISTRY[name](key=(keys or {}).get(name))
    f = P.Filters(**{**filters, 'limit': 200})
    card = {
        'name': name,
        'provider': name,
        'title': provider.title,
        'module': {
            'installed': os.path.isdir(os.path.join(ORBIT, name)),
            'version': cfg.get('version'),
            'what': (cfg.get('description') or '').split('. ')[0] or None,
            'url': base,
            'console': _console(name, cfg, base),
            'mcp': f'{base}/mcp',
            'reads': lane['reads'],
        },
        'chain': provider.chain,
        'kyc': provider.kyc,
        'pay': list(provider.pay),
        'caps': list(provider.caps),
        'key': provider.key_state(),
        'upstream': provider.upstream,
    }

    started = time.time()
    try:
        offers = lane['read'](base, f)
        offers.sort(key=lambda o: (o.get('usd_hr') is None, o.get('usd_hr') or 0))
        card.update(status='up', count=len(offers),
                    cheapest_usd_hr=_cheapest(offers),
                    offers=offers[:max(0, int(sample or 0))] if sample else offers)
    except ProviderError as e:
        card.update(status='down', count=0, offers=[], error=str(e))
    except Exception as e:
        card.update(status='down', count=0, offers=[],
                    error=f'{type(e).__name__}: {e}')
    card['took_ms'] = round((time.time() - started) * 1000)

    if compare:
        from hub import Hub
        try:
            d = Hub(keys=keys).search(provider=name, limit=1, **filters)
            report = d['providers'].get(name)
            # A per-provider report is a count when it worked and the failure
            # itself when it did not — the fan-out never raises.
            card['direct'] = ({'error': report.get('error')} if isinstance(report, dict)
                              else {'count': d['total_found'],
                                    'cheapest_usd_hr': (d['cheapest'] or {}).get('usd_hr'),
                                    'took_ms': d['took_ms']})
        except ProviderError as e:
            card['direct'] = {'error': str(e)}
        except Exception as e:
            card['direct'] = {'error': f'{type(e).__name__}: {e}'}
    card['note'] = _parity(card)
    return card


# ── every module ─────────────────────────────────────────────────────────

def lane(names=None, keys=None, compare=True, sample=6, **filters):
    """Every mod-fronted market at once. One slow module never blocks the rest."""
    want = [n.strip().lower() for n in (
        names.split(',') if isinstance(names, str) else (names or [])) if n.strip()]
    pool = [n for n in LANES if not want or n in want]
    if not pool:
        raise ProviderError(f'no mod lane matches {names} — have {", ".join(LANES)}')

    started, cards = time.time(), {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(pool)) as ex:
        futures = {ex.submit(one, n, keys=keys, compare=compare, sample=sample,
                             **filters): n for n in pool}
        for fut in concurrent.futures.as_completed(futures, timeout=TIMEOUT * 4):
            n = futures[fut]
            try:
                cards[n] = fut.result(timeout=TIMEOUT * 2)
            except Exception as e:
                cards[n] = {'name': n, 'provider': n, 'status': 'down', 'count': 0,
                            'offers': [], 'error': f'{type(e).__name__}: {e}',
                            'note': 'the mod lane itself timed out'}
    rows = [cards[n] for n in pool if n in cards]
    return {
        'mods': rows,
        'count': len(rows),
        'up': sum(1 for c in rows if c.get('status') == 'up'),
        'offers': sum(c.get('count') or 0 for c in rows),
        'compared': bool(compare),
        'took_ms': round((time.time() - started) * 1000),
        'note': 'the same markets read through their own modules on this box; '
                'ids round-trip into compute_quote / compute_rent, which still '
                'go direct to the upstream',
    }
