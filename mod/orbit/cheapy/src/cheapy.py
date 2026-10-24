"""cheapy core — the cheapest model that can actually do the job.

Prices come from OpenRouter's public model list (no key). The list is cached
at ~/.mod/cheapy/models.json for an hour; if the network is down the stale
cache is used, and if there is no cache the bundled snapshot.json is. Every
answer says which of the three it came from (``source``).

Costs are USD. OpenRouter quotes per token; we keep per-million-token prices
(``in_m`` / ``out_m``) because that is what people compare.
"""

import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, 'snapshot.json')
URL = 'https://openrouter.ai/api/v1/models'
TTL = 3600


def data_dir():
    d = os.path.expanduser(os.environ.get('CHEAPY_DIR') or '~/.mod/cheapy')
    os.makedirs(d, exist_ok=True)
    return d


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize(raw):
    """OpenRouter /models rows → flat rows. Drops routers (price -1) and
    anything without a prompt+completion price."""
    out = []
    for m in raw:
        p = m.get('pricing') or {}
        pi, po = _num(p.get('prompt')), _num(p.get('completion'))
        if pi is None or po is None or pi < 0 or po < 0:
            continue
        arch = m.get('architecture') or {}
        ins = arch.get('input_modalities') or []
        params = m.get('supported_parameters') or []
        cache = _num(p.get('input_cache_read'))
        out.append({
            'id': m['id'],
            'name': m.get('name') or m['id'],
            'provider': m['id'].split('/')[0],
            'context': int(m.get('context_length') or 0),
            'max_out': int((m.get('top_provider') or {}).get('max_completion_tokens') or 0),
            'in_m': round(pi * 1e6, 6),
            'out_m': round(po * 1e6, 6),
            'cache_m': round(cache * 1e6, 6) if cache is not None and cache >= 0 else None,
            'free': pi == 0 and po == 0,
            'tools': 'tools' in params,
            'vision': 'image' in ins,
            'reasoning': 'reasoning' in params or 'include_reasoning' in params,
            'created': int(m.get('created') or 0),
        })
    return out


class Cheapy:
    def __init__(self, dir=None, fetch=None):
        self.dir = dir or data_dir()
        self.cache = os.path.join(self.dir, 'models.json')
        self._fetch = fetch or self._http
        self._mem = None  # (loaded_at, rows, source, fetched_at)

    # ── price list ───────────────────────────────────────────────

    @staticmethod
    def _http():
        req = urllib.request.Request(URL, headers={'User-Agent': 'cheapy/0.1'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)['data']

    def _read(self, path):
        try:
            with open(path) as f:
                d = json.load(f)
            return d['models'], d.get('fetched_at', 0)
        except Exception:
            return None, 0

    def load(self, refresh=False):
        """(rows, source, fetched_at). source = live | cache | stale | snapshot."""
        now = time.time()
        if self._mem and not refresh and now - self._mem[0] < 60:
            return self._mem[1:]
        rows, at = self._read(self.cache)
        if rows and not refresh and now - at < TTL:
            res = (rows, 'cache', at)
        else:
            try:
                rows = normalize(self._fetch())
                if not rows:
                    raise ValueError('empty model list')
                tmp = self.cache + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump({'fetched_at': now, 'models': rows}, f)
                os.replace(tmp, self.cache)
                res = (rows, 'live', now)
            except Exception:
                if rows:
                    res = (rows, 'stale', at)
                else:
                    snap, sat = self._read(SNAPSHOT)
                    res = (snap or [], 'snapshot', sat)
        self._mem = (now, *res)
        return res

    def stats(self):
        rows, source, at = self.load()
        return {'models': len(rows), 'free': sum(r['free'] for r in rows),
                'providers': len({r['provider'] for r in rows}),
                'source': source, 'fetched_at': int(at)}

    # ── answers ──────────────────────────────────────────────────

    @staticmethod
    def cost(row, input_tokens, output_tokens, calls=1):
        per = (row['in_m'] * input_tokens + row['out_m'] * output_tokens) / 1e6
        return per, per * calls

    def cheapest(self, input_tokens=2000, output_tokens=500, calls=1,
                 min_context=0, tools=False, vision=False, reasoning=False,
                 free=None, q='', provider='', limit=25, offset=0):
        """Every model that fits the job, cheapest first.

        A model fits if its context holds input+output and it has every
        capability asked for. free=True keeps only $0 models, free=False
        drops them (free tiers are rate-limited), None keeps both."""
        it, ot, n = int(input_tokens), int(output_tokens), max(1, int(calls))
        need = max(int(min_context or 0), it + ot)
        tools, vision, reasoning = (_bool(tools), _bool(vision), _bool(reasoning))
        free = None if free in (None, '', 'any') else _bool(free)
        q, provider = (q or '').lower().strip(), (provider or '').lower().strip()
        rows, source, at = self.load()
        hits = []
        for r in rows:
            if r['context'] and r['context'] < need:
                continue
            if (tools and not r['tools']) or (vision and not r['vision']) \
                    or (reasoning and not r['reasoning']):
                continue
            if free is not None and r['free'] != free:
                continue
            if provider and r['provider'] != provider:
                continue
            if q and q not in r['id'].lower() and q not in r['name'].lower():
                continue
            per, total = self.cost(r, it, ot, n)
            hits.append({**r, 'per_call': per, 'total': total})
        hits.sort(key=lambda h: (h['total'], -h['context'], h['id']))
        limit, offset = int(limit), int(offset)
        return {'job': {'input_tokens': it, 'output_tokens': ot, 'calls': n,
                        'min_context': need},
                'count': len(hits), 'source': source, 'fetched_at': int(at),
                'models': hits[offset:offset + limit]}

    def compare(self, ids, input_tokens=2000, output_tokens=500, calls=1):
        """Price the same job on chosen models, cheapest first, with how many
        times the cheapest each one costs."""
        if isinstance(ids, str):
            ids = [i.strip() for i in ids.split(',') if i.strip()]
        rows, source, _ = self.load()
        by = {r['id']: r for r in rows}
        out, missing = [], []
        for i in ids:
            if i not in by:
                missing.append(i)
                continue
            per, total = self.cost(by[i], int(input_tokens), int(output_tokens),
                                   max(1, int(calls)))
            out.append({**by[i], 'per_call': per, 'total': total})
        out.sort(key=lambda h: h['total'])
        low = out[0]['total'] if out else 0
        for h in out:
            h['x'] = round(h['total'] / low, 2) if low else None
        return {'models': out, 'missing': missing, 'source': source}

    def model(self, id):
        """One model's row."""
        for r in self.load()[0]:
            if r['id'] == id:
                return r
        return {'error': f'no model {id}'}

    def providers(self):
        """Every provider, with model count and its cheapest paid model."""
        agg = {}
        for r in self.load()[0]:
            a = agg.setdefault(r['provider'], {'provider': r['provider'],
                                               'models': 0, 'free': 0, 'cheapest': None})
            a['models'] += 1
            a['free'] += r['free']
            if not r['free']:
                blended = r['in_m'] * 0.75 + r['out_m'] * 0.25
                if a['cheapest'] is None or blended < a['cheapest']['blended_m']:
                    a['cheapest'] = {'id': r['id'], 'blended_m': round(blended, 6)}
        return sorted(agg.values(), key=lambda a: -a['models'])


def estimate(text='', chars=None):
    """Rough token count: ~4 characters per token for English text."""
    n = len(text or '') if chars is None else int(chars)
    return {'chars': n, 'tokens': max(0, round(n / 4))}


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(v)
