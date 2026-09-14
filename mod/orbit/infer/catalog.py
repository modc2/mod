"""One catalog over every router, and the grouping that makes it a market.

A flat merged list is not yet useful: the same weights appear on six routers
under six ids, and until those collapse into one row, "who serves this cheapest"
cannot be asked. So the catalog has two shapes and both are first-class —

    offerings   one row per (provider, model): what you actually call
    models      one row per model_key: every router that serves it, cheapest first

Fan-out is partial by design. A router that is down, rate-limited or unreachable
removes its own rows and lands in `errors`; it never empties the answer. The
count of routers that replied travels with every response, because "the cheapest
of four routers" and "the cheapest of six" are different claims and a caller
reading a price needs to know which one they were handed.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import router as R

STATE_DIR = os.path.expanduser(os.environ.get('INFER_DIR', '~/.mod/infer'))
CACHE = os.path.join(STATE_DIR, 'router-catalog.json')
TTL = int(os.environ.get('INFER_CATALOG_TTL', '900'))       # 15 minutes


class CatalogError(Exception):
    pass


# ── fetching ─────────────────────────────────────────────────────────────

def _fetch(provider):
    """One router's catalog, stamped with how that router takes money.

    The funding terms live on the provider, but the filters people actually
    write are about rows ("show me what I can pay for in Monero"), so each
    offering carries its router's `pay` list and `kyc` rung. Denormalized on
    purpose: the alternative is a join at query time against a registry the
    cached catalog cannot see.
    """
    try:
        rows = []
        for o in provider.catalog():
            d = o.dict()
            d['pay'] = list(provider.pay)
            d['kyc'] = provider.kyc
            rows.append(d)
        return provider.name, rows, None
    except R.RouterError as e:
        return provider.name, [], e.dict()
    except Exception as e:                                   # adapter bug, not upstream
        return provider.name, [], {'error': f'{type(e).__name__}: {e}',
                                   'provider': provider.name}


def refresh(names=None, kyc='none', keys=None, workers=8):
    """Ask every eligible router for its catalog, at once, and cache the merge."""
    providers = R.every(keys=keys, names=names, cap='catalog', kyc=kyc)
    if not providers:
        raise CatalogError(
            f'no router matches kyc={kyc!r} names={names!r} — '
            f'have {", ".join(R.REGISTRY)}')
    rows, errors, served = [], {}, []
    with ThreadPoolExecutor(max_workers=min(workers, len(providers))) as pool:
        for name, got, err in pool.map(_fetch, providers):
            if err:
                errors[name] = err
            else:
                served.append(name)
            rows.extend(got)
    doc = {'fetched': time.time(), 'kyc': kyc, 'providers': served,
           'errors': errors, 'offerings': rows,
           'asked': [p.name for p in providers]}
    _save(doc)
    return doc


def _save(doc):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CACHE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(doc, f)
    os.replace(tmp, CACHE)


def load(max_age=None, **kw):
    """The cached catalog, refreshed if it is older than `max_age` seconds."""
    age = max_age if max_age is not None else TTL
    try:
        with open(CACHE) as f:
            doc = json.load(f)
        if time.time() - doc.get('fetched', 0) <= age and doc.get('offerings'):
            doc['cached'] = True
            doc['age'] = round(time.time() - doc['fetched'], 1)
            return doc
    except Exception:
        pass
    doc = refresh(**kw)
    doc['cached'] = False
    doc['age'] = 0.0
    return doc


# ── filtering ────────────────────────────────────────────────────────────

def _matches(row, q=None, inp=None, out=None, provider=None, coin=None,
             max_usd=None, min_context=None, multimodal=None, tag=None,
             free=None, quote_coin=None, kyc_row=None):
    if q:
        hay = (row.get('model', '') + ' ' + row.get('id', '') + ' '
               + str(row.get('name', ''))).lower()
        if not all(t in hay for t in q.lower().split()):
            return False
    if inp and inp not in (row.get('inputs') or []):
        return False
    if out and out not in (row.get('outputs') or []):
        return False
    if provider and row.get('provider') != provider:
        return False
    if multimodal is not None and bool(row.get('multimodal')) != bool(multimodal):
        return False
    if tag and tag not in (row.get('tags') or []):
        return False
    if min_context and (row.get('context') or 0) < min_context:
        return False
    if coin:
        # "pay in XMR" asks what the ROUTER accepts for funding, not what it
        # quotes prices in. `quote_coin=` is the other question; conflating them
        # made `coin=XMR` return nothing while NanoGPT was sitting right there.
        if coin.upper() not in {c.upper() for c in (row.get('pay') or ())}:
            return False
    if quote_coin and str(row.get('coin', '')).upper() != quote_coin.upper():
        return False
    if kyc_row and row.get('kyc') != kyc_row:
        return False
    price = _blended(row)
    if free and not declared_free(row):
        return False
    if max_usd is not None and (price is None or price > max_usd):
        return False
    return True


def declared_free(row):
    """Free because the router says so, not because a field happened to be 0.

    OpenRouter marks a genuinely free variant in the id (`:free`); anything else
    claiming zero is claiming it by omission.
    """
    return str(row.get('id', '')).endswith(':free') or 'free' in (row.get('tags') or [])


def _blended(row, in_tok=1000, out_tok=500):
    """USD for one representative call — the number ranking sorts on.

    A published zero is not a price. PPQ lists `input_per_1M_tokens: 0` for the
    Lyria music models because they bill per clip, and NanoGPT lists it for
    `auto-model` because the real price depends on what it routes to. Both are
    "no token price here", and reading them as free puts them at the top of a
    cheapest-first ranking and walks them straight through the spend guard,
    which is the exact failure this module exists to prevent. So zero across
    every field means unknown unless the router declares the model free.
    """
    i, o = row.get('usd_per_mtok_in'), row.get('usd_per_mtok_out')
    req, img = row.get('usd_per_request'), row.get('usd_per_image')
    if i is None and o is None:
        return req if req is not None else img
    total = ((i or 0.0) * in_tok + (o or 0.0) * out_tok) / 1e6
    if req:
        total += req
    if total == 0 and not img and not declared_free(row):
        return None
    return total


SORTS = ('price', 'context', 'fast', 'name')


def _sorted(rows, sort='price'):
    if sort == 'context':
        return sorted(rows, key=lambda r: -(r.get('context') or 0))
    if sort == 'fast':
        # Only io.net publishes latency; anything unmeasured sorts last rather
        # than sorting as zero and winning a race it never ran.
        return sorted(rows, key=lambda r: r.get('latency_ms') or float('inf'))
    if sort == 'name':
        return sorted(rows, key=lambda r: r.get('model') or '')
    known = [r for r in rows if _blended(r) is not None]
    unknown = [r for r in rows if _blended(r) is None]
    return sorted(known, key=_blended) + unknown


def search(limit=50, sort='price', max_age=None, kyc='none', names=None,
           keys=None, **filters):
    doc = load(max_age=max_age, kyc=kyc, names=names, keys=keys)
    rows = [r for r in doc['offerings'] if _matches(r, **filters)]
    rows = _sorted(rows, sort)
    for r in rows:
        r['usd_per_call'] = _blended(r)
    return {'offerings': rows[:limit], 'matched': len(rows),
            'total': len(doc['offerings']), 'providers': doc['providers'],
            'errors': doc['errors'], 'kyc': doc.get('kyc'),
            'cached': doc.get('cached'), 'age': doc.get('age'), 'sort': sort}


def models(limit=50, sort='price', max_age=None, kyc='none', names=None,
           keys=None, **filters):
    """The market view: one row per model, every router that serves it."""
    doc = load(max_age=max_age, kyc=kyc, names=names, keys=keys)
    groups = {}
    for r in doc['offerings']:
        if not _matches(r, **filters):
            continue
        g = groups.setdefault(r['model'], [])
        g.append(r)
    out = []
    for key, offers in groups.items():
        offers = _sorted(offers, 'price')
        for o in offers:
            o['usd_per_call'] = _blended(o)
        best = offers[0]
        spread = [o['usd_per_call'] for o in offers if o['usd_per_call'] is not None]
        out.append({
            'model': key,
            'name': best.get('name'),
            'providers': [o['provider'] for o in offers],
            'n': len(offers),
            'cheapest': best['provider'],
            'usd_per_call': best.get('usd_per_call'),
            'inputs': sorted({m for o in offers for m in (o.get('inputs') or [])}),
            'outputs': sorted({m for o in offers for m in (o.get('outputs') or [])}),
            'context': max((o.get('context') or 0) for o in offers) or None,
            # What the aggregation is actually worth on this row: the ratio
            # between the dearest and cheapest router for identical weights.
            'spread': (round(max(spread) / min(spread), 2)
                       if len(spread) > 1 and min(spread) > 0 else None),
            'offerings': offers,
        })
    out = _sorted(out, sort) if sort != 'price' else sorted(
        out, key=lambda r: (r['usd_per_call'] is None, r['usd_per_call'] or 0))
    return {'models': out[:limit], 'matched': len(out),
            'providers': doc['providers'], 'errors': doc['errors'],
            'kyc': doc.get('kyc'), 'cached': doc.get('cached'),
            'age': doc.get('age'), 'sort': sort}


def modalities(max_age=None, kyc='none', keys=None):
    """What this registry can actually do, counted rather than claimed."""
    doc = load(max_age=max_age, kyc=kyc, keys=keys)
    pairs, by_provider = {}, {}
    for r in doc['offerings']:
        k = '+'.join(r.get('inputs') or []) + '->' + '+'.join(r.get('outputs') or [])
        pairs[k] = pairs.get(k, 0) + 1
        by_provider.setdefault(r['provider'], set()).update(
            (r.get('inputs') or []) + (r.get('outputs') or []))
    return {'pairs': dict(sorted(pairs.items(), key=lambda kv: -kv[1])),
            'by_provider': {k: sorted(v) for k, v in by_provider.items()},
            'vocabulary': list(R.MODALITIES),
            'total': len(doc['offerings']), 'providers': doc['providers'],
            'errors': doc['errors'], 'cached': doc.get('cached')}
