"""What was spent, on which router, for what — append-only.

Two numbers are kept per call and they are not the same number: `usd` computed
from the usage the router reported, and `estimated` from the catalog price at
routing time. Keeping both is what makes the catalog auditable — a router whose
bills drift from its published prices shows up as a widening gap here, and
nowhere else.

Append-only, one JSON object per line. A ledger that can be rewritten is not a
ledger, and a spend record that a later bug can silently edit is worse than none.
"""

import json
import os
import time

STATE_DIR = os.path.expanduser(os.environ.get('INFER_DIR', '~/.mod/infer'))
LEDGER = os.path.join(STATE_DIR, 'router-ledger.jsonl')


def record(provider, model, key=None, usd=None, usage=None, ms=None,
           estimated=None, kind='chat', note=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    entry = {
        'id': '%d-%s' % (time.time_ns() // 1000, provider),
        'at': time.time(), 'provider': provider, 'model': model,
        'model_key': key or model, 'kind': kind, 'usd': usd, 'ms': ms,
        'estimated_usd': estimated,
        'tokens_in': (usage or {}).get('prompt_tokens'),
        'tokens_out': (usage or {}).get('completion_tokens'),
    }
    if note:
        entry['note'] = note
    with open(LEDGER, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def entries(limit=200, provider=None, since=None):
    try:
        with open(LEDGER) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
    if provider:
        rows = [r for r in rows if r.get('provider') == provider]
    if since:
        rows = [r for r in rows if r.get('at', 0) >= since]
    return rows[-limit:][::-1]


def spend(since=None):
    """Rollup by provider: what each router has actually cost."""
    rows = entries(limit=10 ** 7, since=since)
    by = {}
    for r in rows:
        b = by.setdefault(r['provider'], {
            'provider': r['provider'], 'calls': 0, 'usd': 0.0, 'estimated': 0.0,
            'tokens_in': 0, 'tokens_out': 0, 'unpriced': 0, 'ms': []})
        b['calls'] += 1
        if r.get('usd') is None:
            b['unpriced'] += 1
        else:
            b['usd'] += r['usd']
        b['estimated'] += r.get('estimated_usd') or 0.0
        b['tokens_in'] += r.get('tokens_in') or 0
        b['tokens_out'] += r.get('tokens_out') or 0
        if r.get('ms'):
            b['ms'].append(r['ms'])
    out = []
    for b in by.values():
        lat = sorted(b.pop('ms'))
        b['usd'] = round(b['usd'], 6)
        b['estimated'] = round(b['estimated'], 6)
        # The whole point of keeping both: drift between billed and published.
        b['drift'] = (round(b['usd'] / b['estimated'], 3)
                      if b['estimated'] > 0 and b['usd'] > 0 else None)
        b['p50_ms'] = lat[len(lat) // 2] if lat else None
        out.append(b)
    out.sort(key=lambda b: -b['usd'])
    return {'providers': out, 'total_usd': round(sum(b['usd'] for b in out), 6),
            'calls': sum(b['calls'] for b in out)}
