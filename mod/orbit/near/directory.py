#!/usr/bin/env python3
"""near directory — a local, self-sustaining index of every contract on chain.

There is no RPC that lists contracts, and the public indexers time out on the
question, so this module builds the answer itself from the only primitive the
chain does offer: `EXPERIMENTAL_changes_in_block` names every account whose
contract code was touched in a block, one call per block. Two threads turn
that into a directory:

  tail      follows the chain head and catches every deploy the moment it
            lands — from the day the scraper starts, coverage is total.
  backfill  walks history backwards through the archival RPC at a polite,
            configurable pace, so the directory keeps growing toward genesis
            for as long as the module runs.

Everything lands in one JSON file under ~/.mod/near/ — no database, no API
key, no third-party indexer — and survives restarts: both threads resume from
the persisted high- and low-water marks. Lookups elsewhere in the module feed
the same store (`note()`), so any contract a user ever touches is indexed
even before a scan reaches its deploy block.

Env: NEAR_SCRAPE=0 disables the threads (the store still answers),
NEAR_TAIL_POLL seconds between head checks (default 6), NEAR_SCAN_RPS
backfill requests per second (default 1), NEAR_HOME the store directory.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
if HERE not in sys.path:
    sys.path.append(HERE)

from chain import (ARCHIVAL, KNOWN_CONTRACTS, Client, NearError,   # noqa: E402
                   near)

HOME = os.path.expanduser(os.environ.get('NEAR_HOME') or '~/.mod/near')
TAIL_POLL = float(os.environ.get('NEAR_TAIL_POLL', 6))
# Between the calls of one catch-up burst, so a poll that owes ten blocks
# spreads them instead of firing them back-to-back into a rate limiter.
TAIL_PACE = float(os.environ.get('NEAR_TAIL_PACE', 0.25))
SCAN_RPS = max(0.1, float(os.environ.get('NEAR_SCAN_RPS', 1)))
SCRAPE_ON = os.environ.get('NEAR_SCRAPE', '1').lower() not in ('0', 'false', 'no')
# A block whose height a regular node has garbage-collected only an archival
# node remembers; anything this far behind the head goes straight there.
ARCHIVAL_DEPTH = 150_000
# After downtime the tail only replays this many blocks itself; a larger gap
# is handed to the backfill thread so the head is never minutes behind.
MAX_CATCHUP = 600
SAVE_EVERY = 60          # seconds between progress saves even with no finds
GENESIS = {'mainnet': 9_820_210}   # first block of mainnet; testnet resets

_dirs = {}
_dirs_lock = threading.Lock()


class Directory:
    def __init__(self, network):
        self.network = network
        self.path = os.path.join(HOME, f'contracts.{network}.json')
        self.lock = threading.Lock()
        self.state = {'tail': None, 'floor': None, 'origin': None,
                      'gaps': [], 'contracts': {}}
        self.started = False
        self.last_save = 0.0
        self.last_error = None
        os.makedirs(HOME, exist_ok=True)
        try:
            with open(self.path) as f:
                held = json.load(f)
            if isinstance(held.get('contracts'), dict):
                self.state.update({k: held[k] for k in
                                   ('tail', 'floor', 'origin', 'gaps',
                                    'contracts') if k in held})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ── store ────────────────────────────────────────────────────

    def save(self, force=False):
        now = time.time()
        if not force and now - self.last_save < SAVE_EVERY:
            return
        with self.lock:
            blob = json.dumps({'network': self.network,
                               'saved': _iso(), **self.state},
                              separators=(',', ':'))
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            f.write(blob)
        os.replace(tmp, self.path)
        self.last_save = now

    def record(self, account_id, block=None, src='tail', probe=None):
        """One contract into the store: probe is a view_account result (or
        None when the caller could not reach the account any more)."""
        with self.lock:
            c = self.state['contracts'].setdefault(account_id, {})
            if block is not None:
                c['b'] = max(c.get('b') or 0, block)
                c['b0'] = min(c.get('b0') or block, block)
            c['t'] = _iso()
            c.setdefault('src', src)
            if probe is not None:
                code_hash = probe.get('code_hash')
                c['live'] = code_hash not in (
                    None, '11111111111111111111111111111111')
                if code_hash:
                    c['hash'] = code_hash
                c['bytes'] = probe.get('storage_usage')
                c['near'] = round(near(probe.get('amount')) +
                                  near(probe.get('locked')), 2)

    # ── scanning ─────────────────────────────────────────────────

    def _client(self, archival=False):
        c = Client(network=self.network)
        if archival and ARCHIVAL.get(self.network):
            c.endpoints = list(ARCHIVAL[self.network])
        return c

    def scan_block(self, height, src, head=None):
        """One block → the accounts whose contract code changed in it, each
        probed for its current state. UNKNOWN_BLOCK means the height was
        skipped by consensus — scanned, nothing there."""
        deep = head is not None and head - height > ARCHIVAL_DEPTH
        client = self._client(archival=deep)
        try:
            r = client.call('EXPERIMENTAL_changes_in_block',
                            {'block_id': height})
        except NearError as e:
            if 'UNKNOWN_BLOCK' in str(e):
                # Near the head a missing height is a height consensus
                # skipped; deeper down the pool may have garbage-collected
                # it, so ask the archival node before calling it empty.
                if not deep and head - height > 10_000 and \
                        ARCHIVAL.get(self.network):
                    return self.scan_block(height, src,
                                           head=height + ARCHIVAL_DEPTH + 1)
                return 0
            raise
        touched = sorted({ch.get('account_id')
                          for ch in (r or {}).get('changes') or []
                          if ch.get('type') == 'contract_code_touched'
                          and ch.get('account_id')})
        if not touched:
            return 0
        probes = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for aid, p in zip(touched, pool.map(self._probe, touched)):
                probes[aid] = p
        for aid in touched:
            self.record(aid, block=height, src=src, probe=probes.get(aid))
        return len(touched)

    def _probe(self, account_id):
        try:
            return self._client().call('query', {
                'finality': 'final', 'request_type': 'view_account',
                'account_id': account_id})
        except Exception:
            return None

    def _tail(self):
        client = self._client()
        while True:
            try:
                head = (client.call('block', {'finality': 'final'})
                        .get('header') or {}).get('height')
                if head:
                    with self.lock:
                        tail = self.state['tail']
                        if tail is None:
                            # First run: the tail owns [head, ∞); backfill
                            # owns everything older.
                            self.state.update(tail=head - 1, origin=head,
                                              floor=head)
                            tail = head - 1
                        if head - tail > MAX_CATCHUP:
                            self.state['gaps'].append(
                                [tail + 1, head - MAX_CATCHUP])
                            tail = head - MAX_CATCHUP
                            self.state['tail'] = tail
                    for h in range(tail + 1, head + 1):
                        self.scan_block(h, 'tail', head=head)
                        with self.lock:
                            self.state['tail'] = h
                        time.sleep(TAIL_PACE)
                    self.last_error = None
                self.save()
            except Exception as e:
                self.last_error = f'tail: {e}'
                time.sleep(10)
            time.sleep(TAIL_POLL)

    def _next_backfill_block(self):
        """The next unscanned height, gaps (downtime the tail skipped) before
        the walk toward genesis."""
        with self.lock:
            gaps = self.state['gaps']
            while gaps:
                lo, hi = gaps[-1]
                if hi < lo:
                    gaps.pop()
                    continue
                return hi, 'gap'
            floor = self.state['floor']
            if floor and floor - 1 > GENESIS.get(self.network, 0):
                return floor - 1, 'floor'
            return None, None

    def _mark_backfilled(self, h, kind):
        with self.lock:
            if kind == 'gap' and self.state['gaps']:
                self.state['gaps'][-1][1] = h - 1
            elif kind == 'floor':
                self.state['floor'] = h

    def _backfill(self):
        """Down through history at SCAN_RPS, one block per pass. The floor
        mark makes every restart a resume — the walk to genesis just
        continues. A throttled provider earns exponentially longer pauses;
        the block that failed is simply picked again."""
        pace = 1.0 / SCAN_RPS
        fails = 0
        while True:
            h, kind = self._next_backfill_block()
            if h is None:
                time.sleep(30)     # nothing to do: waiting on the tail
                continue
            try:
                self.scan_block(h, 'backfill',
                                head=self.state['tail'] or
                                (h + ARCHIVAL_DEPTH + 1))
                self._mark_backfilled(h, kind)
                self.save()
                fails, self.last_error = 0, None
                time.sleep(pace)
            except Exception as e:
                fails += 1
                wait = min(600, 15 * (2 ** min(fails - 1, 5)))
                self.last_error = f'backfill: {e} — backing off {wait}s'
                time.sleep(wait)

    def start(self):
        if self.started or not SCRAPE_ON:
            return
        self.started = True
        for fn in (self._tail, self._backfill):
            threading.Thread(target=fn, daemon=True,
                             name=f'near-dir-{self.network}-{fn.__name__}').start()

    # ── queries ──────────────────────────────────────────────────

    def status(self):
        with self.lock:
            s = dict(self.state)
            total = len(s['contracts'])
        genesis = GENESIS.get(self.network, 0)
        tail, floor = s.get('tail'), s.get('floor')
        span = (tail - genesis) if tail else None
        out = {'total': total, 'tail_block': tail, 'floor_block': floor,
               'origin_block': s.get('origin'),
               'pending_gaps': len(s.get('gaps') or []),
               'scanning': self.started,
               'store': self.path}
        if tail and floor and span:
            out['history_pct'] = round(100 * (tail - floor) / span, 3)
        if self.last_error:
            out['last_error'] = self.last_error
        return out

    def snapshot(self, q=None, limit=50, offset=0):
        labels = {cid: (label, cat) for cid, label, cat
                  in KNOWN_CONTRACTS.get(self.network, [])}
        q = (q or '').strip().lower()
        with self.lock:
            items = list(self.state['contracts'].items())
        rows = []
        for aid, c in items:
            if q and q not in aid:
                continue
            label, cat = labels.get(aid, (None, None))
            rows.append({'account_id': aid, 'label': label, 'category': cat,
                         'deploy_block': c.get('b'),
                         'first_seen_block': c.get('b0'),
                         'live': c.get('live'), 'code_hash': c.get('hash'),
                         'storage_bytes': c.get('bytes'),
                         'balance_near': c.get('near'),
                         'seen': c.get('t'), 'via': c.get('src')})
        rows.sort(key=lambda r: (r['deploy_block'] or 0, r['seen'] or ''),
                  reverse=True)
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))
        return {'network': self.network, 'status': self.status(),
                'matched': len(rows), 'offset': offset, 'limit': limit,
                'q': q or None,
                'contracts': rows[offset:offset + limit],
                'note': 'scraped from the chain itself — a live tail catches '
                        'every deploy as it lands; a backfill walks history '
                        'backwards through the archival RPC, so coverage '
                        'grows the longer the module runs'}


def _iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def get(network):
    network = network or os.environ.get('NEAR_NETWORK') or 'mainnet'
    with _dirs_lock:
        d = _dirs.get(network)
        if d is None:
            d = _dirs[network] = Directory(network)
        return d


def ensure(network=None):
    """The directory for a network, threads running. Called at serve() and on
    every snapshot, so the scraper follows whichever networks are looked at."""
    d = get(network)
    d.start()
    return d


def snapshot(network=None, q=None, limit=50, offset=0):
    return ensure(network).snapshot(q=q, limit=limit, offset=offset)


def note(network, account_id, code_hash=None, storage_bytes=None,
         balance_near=None, methods=None):
    """Discovery by use: any lookup that finds code feeds the index — and a
    parsed interface (its WASM export names) makes the contract searchable
    by what it can do, not just what it is called."""
    try:
        d = get(network)
        with d.lock:
            c = d.state['contracts'].setdefault(account_id, {})
            c['t'] = _iso()
            c.setdefault('src', 'lookup')
            c['live'] = True
            if code_hash:
                c['hash'] = code_hash
            if storage_bytes is not None:
                c['bytes'] = storage_bytes
            if balance_near is not None:
                c['near'] = round(balance_near, 2)
            if methods:
                c['fns'] = list(methods)[:64]
        d.save()
    except Exception:
        pass                      # the index is a bonus, never a failure


if __name__ == '__main__':
    net = sys.argv[1] if len(sys.argv) > 1 else 'mainnet'
    d = ensure(net)
    print(json.dumps(d.status(), indent=2))
    while SCRAPE_ON:
        time.sleep(30)
        print(json.dumps(d.status()), flush=True)
