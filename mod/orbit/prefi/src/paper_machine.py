"""Paper pool contract — a deterministic state machine over stored messages.

There is no blockchain here and none is needed: the contract is a pure
transition function `apply(state, msg) -> (state', events)` over integer
state, and the host stores every accepted message in a hash-chained log
(paper.py). Replaying the log through this module reproduces the state
byte for byte, which is what makes balances auditable — anyone holding
the log can check every payout without trusting the server or waiting on
a chain.

Determinism rules the design:
  * every quantity is an integer — balances in micro-PAPER, prices in
    e6, accuracy in parts-per-million; no float ever enters state;
  * anything the outside world knows (the clock, the oracle price) rides
    IN the message, stamped at admission, so replay never re-asks;
  * the pot split is the WASM contract (paper_contract.wat); the Python
    `split_weighted` below is its bit-exact reference, and a test holds
    the two equal.

Fake money, real records: PAPER is minted freely from a faucet and worth
nothing, but an address's accuracy history is public and append-only —
that record is what an agent is actually playing for.
"""

import copy
import hashlib
import json
from typing import Callable, Dict, List, Optional, Tuple

MICRO = 1_000_000        # 1 PAPER = 1e6 units
PPM = 1_000_000          # accuracy is scored in parts-per-million
MAX_ENTRIES = 2048       # per (round, asset) pot — bounds the wasm memory
MAX_NAME = 48

DEFAULT_CONFIG = {
    'interval': 3600,          # a round an hour — agents play fast
    'entry_cutoff': 300,       # entries stop this long before the close
    'faucet_grant': 1000 * MICRO,
    'faucet_interval': 86400,  # one grant per address per day
    'per_round': 10,           # entries per address per round, 1 per asset
    'min_stake': MICRO,        # 1 PAPER
    'max_stake': 0,            # 0 = uncapped (the balance is the cap)
    'tol_ppm': PPM,            # linear score: 1 − err/tol, floored at 0
    'open': True,              # unsigned play allowed — it is play money
}


class MachineError(ValueError):
    """An invalid message. The host refuses it; it never reaches the log."""


# ── Canonical form ───────────────────────────────────────────────────

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))


def state_root(state: Dict) -> str:
    return hashlib.sha256(canonical(state).encode()).hexdigest()


# ── The distribution reference (mirror of paper_contract.wat) ────────

def split_weighted(pot: int, weights: List[int]) -> Tuple[List[int], int]:
    """(payouts, leftover): the exact algorithm the wasm kernel runs.

    Largest-remainder over pot*w/total with weights halved until their sum
    fits 31 bits (so the wasm's r*w never overflows i64). Any change here
    must change contract.wat identically — test_paper holds them equal.
    """
    pot = int(pot)
    weights = [int(w) for w in weights]
    payouts = [0] * len(weights)
    total = sum(weights)
    if total == 0:
        return payouts, pot
    while total > 0x7FFFFFFF:
        weights = [w >> 1 for w in weights]
        total = sum(weights)
    if total == 0:
        return payouts, pot
    q, r = divmod(pot, total)
    rems = []
    for i, w in enumerate(weights):
        payouts[i] = q * w + (r * w) // total
        rems.append((r * w) % total)
    left = pot - sum(payouts)
    while left > 0:
        best, bestrem = -1, 0
        for i, rem in enumerate(rems):
            if rem > bestrem:
                best, bestrem = i, rem
        if best < 0:
            break
        payouts[best] += 1
        rems[best] = -1
        left -= 1
    return payouts, left


def score_ppm(price_e6: int, actual_e6: int, tol_ppm: int) -> int:
    """Linear accuracy in ppm: 1 − (|called−actual|/actual)/tol, floored 0."""
    if actual_e6 <= 0:
        return 0
    err_ppm = abs(int(price_e6) - int(actual_e6)) * PPM // int(actual_e6)
    return max(0, PPM - err_ppm * PPM // max(1, int(tol_ppm)))


# ── Config ───────────────────────────────────────────────────────────

def validate_config(patch: Dict, current: Dict = None) -> Dict:
    """The merged config a patch produces, or MachineError."""
    base = dict(current) if current else dict(DEFAULT_CONFIG)
    unknown = set(patch) - set(DEFAULT_CONFIG)
    if unknown:
        raise MachineError(f'unknown config keys: {sorted(unknown)}')
    merged = dict(base)
    merged.update(patch)
    merged['open'] = bool(merged['open'])
    for key in ('interval', 'entry_cutoff', 'faucet_grant', 'faucet_interval',
                'per_round', 'min_stake', 'max_stake', 'tol_ppm'):
        merged[key] = int(merged[key])
    if merged['interval'] < 60:
        raise MachineError('interval must be at least 60 seconds')
    if current and merged['interval'] != current['interval']:
        # Round indexes are time // interval; moving the interval would
        # silently re-key every open round. Fixed at genesis.
        raise MachineError('interval is fixed at genesis')
    if not 0 <= merged['entry_cutoff'] < merged['interval']:
        raise MachineError('entry_cutoff must be 0..interval')
    if merged['faucet_grant'] <= 0 or merged['faucet_interval'] < 0:
        raise MachineError('faucet_grant must be positive')
    if not 1 <= merged['per_round'] <= 100:
        raise MachineError('per_round must be 1..100')
    if merged['min_stake'] < 1:
        raise MachineError('min_stake must be at least 1 unit')
    if merged['max_stake'] and merged['max_stake'] < merged['min_stake']:
        raise MachineError('max_stake below min_stake')
    if not 1 <= merged['tol_ppm'] <= 100 * PPM:
        raise MachineError('tol_ppm must be 1..100e6')
    return merged


def genesis(config: Dict = None, time: int = 0) -> Dict:
    return {
        'v': 1,
        'seq': 0,
        'time': int(time),
        'config': validate_config(config or {}),
        'accounts': {},
        'rounds': {},
        'reserve': 0,        # unearned pots — every zero-score pot lands here
        'minted': 0,
    }


# ── Accounts ─────────────────────────────────────────────────────────

def _account(state: Dict, address: str, time: int) -> Dict:
    acct = state['accounts'].get(address)
    if acct is None:
        acct = {'balance': 0, 'nonce': 0, 'name': '', 'kind': 'agent',
                'registered': int(time), 'last_faucet': 0,
                'staked': 0, 'won': 0, 'entries': 0, 'resolved': 0,
                'score_sum': 0}
        state['accounts'][address] = acct
    return acct


def _check_nonce(acct: Dict, msg: Dict):
    """A signed message consumes the account's nonce, exactly like the
    dollar pool — replaying an old signed call must fail."""
    if not msg.get('signed'):
        return
    if int(msg.get('nonce', -1)) != acct['nonce']:
        raise MachineError(f"nonce mismatch: expected {acct['nonce']}")
    acct['nonce'] += 1


# ── Transitions ──────────────────────────────────────────────────────

def apply(state: Dict, msg: Dict, split: Callable = None
          ) -> Tuple[Dict, List[Dict]]:
    """One accepted message → the next state. Pure: no IO, no clock."""
    split = split or split_weighted
    st = copy.deepcopy(state)
    t = int(msg.get('time', 0))
    if t < st['time']:
        raise MachineError('message time runs backwards')
    st['time'] = t
    kind = msg.get('type')
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise MachineError(f'unknown message type {kind!r}')
    events = handler(st, msg, t, split) or []
    st['seq'] += 1
    return st, events


def _register(st, msg, t, split):
    address = msg['address']
    name = str(msg.get('name', ''))[:MAX_NAME]
    kind = msg.get('kind', 'agent')
    if kind not in ('agent', 'human'):
        raise MachineError("kind must be 'agent' or 'human'")
    exists = address in st['accounts']
    acct = _account(st, address, t)
    if exists and not msg.get('signed'):
        # Anyone may open an account, but renaming one takes its key.
        raise MachineError('already registered — updating a profile '
                           'needs a signature')
    _check_nonce(acct, msg)
    acct['name'] = name
    acct['kind'] = kind
    return [{'event': 'register', 'address': address}]


def _faucet(st, msg, t, split):
    cfg = st['config']
    acct = _account(st, msg['address'], t)
    _check_nonce(acct, msg)
    since = t - acct['last_faucet']
    if acct['last_faucet'] and since < cfg['faucet_interval']:
        raise MachineError('faucet already used — next grant in '
                           f"{cfg['faucet_interval'] - since}s")
    grant = cfg['faucet_grant']
    acct['balance'] += grant
    acct['last_faucet'] = t
    st['minted'] += grant
    return [{'event': 'faucet', 'address': msg['address'], 'amount': grant}]


def _round_record(st, index: int) -> Dict:
    key = str(index)
    rec = st['rounds'].get(key)
    if rec is None:
        cfg = st['config']
        rec = {'index': index,
               'opens': index * cfg['interval'],
               'closes': (index + 1) * cfg['interval'],
               # Snapshot the scoring tolerance: retuning the config must
               # never re-price a round that already has entries in it.
               'tol_ppm': cfg['tol_ppm'],
               'assets': {}}
        st['rounds'][key] = rec
    return rec


def _predict(st, msg, t, split):
    cfg = st['config']
    address = msg['address']
    asset = msg['asset']
    price_e6 = int(msg['price_e6'])
    stake = int(msg['stake'])
    if price_e6 <= 0:
        raise MachineError('price must be positive')
    if stake < cfg['min_stake']:
        raise MachineError(f"stake below min_stake ({cfg['min_stake']})")
    if cfg['max_stake'] and stake > cfg['max_stake']:
        raise MachineError(f"stake above max_stake ({cfg['max_stake']})")
    acct = st['accounts'].get(address)
    if acct is None:
        raise MachineError('unknown account — faucet first')
    _check_nonce(acct, msg)
    if acct['balance'] < stake:
        raise MachineError('insufficient balance')

    index = t // cfg['interval']
    if int(msg.get('round', index)) != index:
        raise MachineError('round mismatch — the signed round has closed')
    rec = _round_record(st, index)
    if t > rec['closes'] - cfg['entry_cutoff']:
        raise MachineError('entries for this round are closed')

    mine = sum(1 for pot in rec['assets'].values()
               for e in pot['entries'] if e['address'] == address)
    if mine >= cfg['per_round']:
        raise MachineError(f"round cap reached ({cfg['per_round']} calls)")
    pot = rec['assets'].setdefault(asset, {'entries': [], 'resolved': None})
    if any(e['address'] == address for e in pot['entries']):
        raise MachineError(f'already called {asset} this round')
    if len(pot['entries']) >= MAX_ENTRIES:
        raise MachineError('pot is full')

    acct['balance'] -= stake
    acct['staked'] += stake
    acct['entries'] += 1
    pot['entries'].append({'address': address, 'price_e6': price_e6,
                           'stake': stake, 'time': t,
                           'signed': bool(msg.get('signed'))})
    return [{'event': 'predict', 'address': address, 'asset': asset,
             'round': index, 'stake': stake}]


def _resolve(st, msg, t, split):
    index = int(msg['round'])
    asset = msg['asset']
    actual_e6 = int(msg['actual_e6'])
    rec = st['rounds'].get(str(index))
    pot = (rec or {}).get('assets', {}).get(asset)
    if pot is None:
        raise MachineError(f'no {asset} pot in round {index}')
    if pot['resolved'] is not None:
        raise MachineError('already resolved')
    if t < rec['closes']:
        raise MachineError('round still open')

    entries = pot['entries']
    pot_units = sum(e['stake'] for e in entries)
    weights = []
    for e in entries:
        s = score_ppm(e['price_e6'], actual_e6, rec['tol_ppm'])
        e['score_ppm'] = s
        weights.append(e['stake'] * s // PPM)
    payouts, leftover = split(pot_units, weights)
    for e, paid in zip(entries, payouts):
        e['payout'] = int(paid)
        acct = st['accounts'][e['address']]
        acct['balance'] += int(paid)
        acct['won'] += int(paid)
        acct['resolved'] += 1
        acct['score_sum'] += e['score_ppm']
    st['reserve'] += int(leftover)
    pot['resolved'] = {'actual_e6': actual_e6,
                       'mode': str(msg.get('mode', 'historical')),
                       'time': t, 'pot': pot_units,
                       'leftover': int(leftover)}
    return [{'event': 'resolve', 'round': index, 'asset': asset,
             'pot': pot_units, 'paid': pot_units - int(leftover)}]


def _transfer(st, msg, t, split):
    cfg = st['config']
    address, to = msg['address'], msg['to']
    amount = int(msg['amount'])
    if amount <= 0:
        raise MachineError('amount must be positive')
    if address == to:
        raise MachineError('cannot transfer to self')
    acct = st['accounts'].get(address)
    if acct is None:
        raise MachineError('unknown account')
    if not msg.get('signed') and not cfg['open']:
        raise MachineError('transfers need a signature on this deployment')
    _check_nonce(acct, msg)
    if acct['balance'] < amount:
        raise MachineError('insufficient balance')
    acct['balance'] -= amount
    _account(st, to, t)['balance'] += amount
    return [{'event': 'transfer', 'from': address, 'to': to,
             'amount': amount}]


def _config(st, msg, t, split):
    st['config'] = validate_config(msg.get('patch', {}), st['config'])
    return [{'event': 'config', 'patch': msg.get('patch', {})}]


_HANDLERS = {
    'register': _register,
    'faucet': _faucet,
    'predict': _predict,
    'resolve': _resolve,
    'transfer': _transfer,
    'config': _config,
}
