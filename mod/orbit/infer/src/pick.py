"""Choosing a router, calling it, and writing down what it cost.

`plan` ranks and explains; `chat` ranks, calls, and falls back. They share one
candidate function so the dry run is the real decision and not a description of
it — a planner that reasons differently from the executor is a planner that
lies.

Three things this does that a single-router client cannot:

  **Fall back across vendors.** A 429 from one router is not an outage when four
  others serve the same weights. The failover chain is recorded on the response,
  so a call that cost more than the plan said shows why on its own receipt.

  **Price the call after the fact.** The plan's price is an estimate over an
  assumed token mix; the receipt's price is computed from the `usage` the router
  actually returned. Both appear, because the gap between them is the only
  honest measure of how good the estimate was.

  **Refuse to spend silently.** Above `INFER_SPEND_USD` a call returns
  `needs_confirm` instead of running. The guard prices the worst case, and when
  a router publishes no price at all it cannot bound anything — so an unpriced
  model is treated as over the limit rather than under it.
"""

import os
import time

import catalog as C
import ledger as L
import router as R

SPEND_LIMIT = float(os.environ.get('INFER_SPEND_USD', '0.50'))


class PickError(Exception):
    pass


def candidates(model=None, kyc='none', names=None, keys=None, max_age=None,
               require=(), **filters):
    """Every offering that could serve this request, best first.

    `model` is matched on the cross-router key, so asking for `gpt-oss-120b`
    finds it on whichever routers carry it regardless of how each spells the id.
    `require` is a modality list the offering must accept — asking for vision
    and getting a text-only model back is the failure this prevents.
    """
    doc = C.load(max_age=max_age, kyc=kyc, names=names, keys=keys)
    key = R.model_key(model) if model else None
    rows = []
    for r in doc['offerings']:
        if key and r.get('model') != key:
            continue
        if not C._matches(r, **filters):
            continue
        if require and not set(require).issubset(set(r.get('inputs') or [])):
            continue
        rows.append(r)
    rows = C._sorted(rows, filters.get('sort') or 'price')
    for r in rows:
        r['usd_per_call'] = C._blended(r)
    return rows, doc


def plan(model, kyc='none', names=None, keys=None, require=(), limit=10, **kw):
    """What would be called, in what order, and why — without spending."""
    rows, doc = candidates(model, kyc=kyc, names=names, keys=keys,
                           require=require, **kw)
    if not rows:
        return {'model': R.model_key(model), 'candidates': [], 'ready': [],
                'why': f'no router in the kyc={kyc!r} set serves {model!r}',
                'providers': doc['providers'], 'errors': doc['errors']}
    ready = {p.name for p in R.every(keys=keys, kyc=kyc) if p.ready}
    out = []
    for r in rows[:limit]:
        out.append({**r, 'funded': r['provider'] in ready})
    best = out[0]
    priced = [r['usd_per_call'] for r in out if r['usd_per_call'] is not None]
    return {
        'model': R.model_key(model),
        'candidates': out,
        'ready': sorted(ready),
        'chosen': next((r['provider'] for r in out if r['funded']), None),
        'cheapest': best['provider'],
        'saving': (round(1 - min(priced) / max(priced), 4)
                   if len(priced) > 1 and max(priced) > 0 else None),
        'why': ('cheapest funded router first, then the rest as failover'
                if any(r['funded'] for r in out)
                else 'no router in this set is funded — POST /router/key or fund one'),
        'providers': doc['providers'], 'errors': doc['errors'], 'kyc': kyc,
    }


def _cost(row, usage):
    """USD actually spent, from the usage the router reported."""
    if not isinstance(usage, dict):
        return None
    pt = usage.get('prompt_tokens') or usage.get('input_tokens')
    ct = usage.get('completion_tokens') or usage.get('output_tokens')
    if pt is None and ct is None:
        return None
    i, o = row.get('usd_per_mtok_in'), row.get('usd_per_mtok_out')
    if i is None and o is None:
        return None
    return ((pt or 0) * (i or 0.0) + (ct or 0) * (o or 0.0)) / 1e6


def _worst_case(row, max_tokens):
    """The most this call could cost — what the spend guard is allowed to bound."""
    o = row.get('usd_per_mtok_out')
    i = row.get('usd_per_mtok_in')
    if o is None and i is None:
        return None                       # unpriced: unbounded, not free
    worst = ((max_tokens or 1024) * (o or 0.0)) / 1e6 + (row.get('usd_per_request') or 0)
    if worst == 0 and not C.declared_free(row):
        return None                       # a published 0 bounds nothing either
    return worst


def chat(model, messages=None, prompt=None, kyc='none', names=None, keys=None,
         require=(), max_tokens=1024, confirm=False, attempts=3, **kw):
    """Call the best router that will take the job; fall back if it will not."""
    if messages is None:
        if not prompt:
            raise PickError('pass prompt= or messages=')
        messages = [{'role': 'user', 'content': str(prompt)}]
    rows, doc = candidates(model, kyc=kyc, names=names, keys=keys, require=require)
    if not rows:
        raise PickError(f'no router in the kyc={kyc!r} set serves {model!r} — '
                        f'try /router/models?q={model}')

    keymap = keys or {}
    funded = [r for r in rows if R.get(r['provider'], keymap).ready]
    if not funded:
        raise PickError(
            f'{len(rows)} router(s) serve {model!r} but none is funded: '
            + ', '.join(sorted({r['provider'] for r in rows}))
            + ' — POST /router/key {provider, key}')

    head = funded[0]
    worst = _worst_case(head, max_tokens)
    if not confirm and (worst is None or worst > SPEND_LIMIT):
        return {'needs_confirm': True, 'limit_usd': SPEND_LIMIT,
                'worst_case_usd': worst, 'provider': head['provider'],
                'model': head['id'],
                'why': ('this router publishes no price, so the worst case '
                        'cannot be bounded' if worst is None else
                        'worst case is over INFER_SPEND_USD'),
                'retry_with': {'confirm': True}}

    tried = []
    for row in funded[:max(1, attempts)]:
        prov = R.get(row['provider'], keymap)
        started = time.time()
        try:
            resp = prov.chat(row['id'], messages, max_tokens=max_tokens, **kw)
        except R.RouterError as e:
            tried.append({'provider': row['provider'], 'model': row['id'],
                          'error': str(e), 'status': e.status})
            continue
        ms = round((time.time() - started) * 1000, 1)
        usage = resp.get('usage') or {}
        spent = _cost(row, usage)
        entry = L.record(provider=row['provider'], model=row['id'],
                         key=row['model'], usd=spent, usage=usage, ms=ms,
                         estimated=row.get('usd_per_call'))
        resp['routing'] = {
            'provider': row['provider'], 'model': row['id'],
            'model_key': row['model'], 'ms': ms,
            'usd': spent, 'estimated_usd': row.get('usd_per_call'),
            'failover': tried, 'considered': len(rows),
            'alternatives': [{'provider': r['provider'], 'usd_per_call':
                              r.get('usd_per_call')} for r in rows[1:4]],
            'kyc': kyc, 'ledger': entry['id'],
        }
        return resp
    raise PickError('every funded router refused this call: '
                    + '; '.join(f"{t['provider']}: {t['error']}" for t in tried))
