"""Yuma consensus weighted by BlocTime — the paper twin of YumaBlocTime.sol.

Every number here is an int and every division floors, in the same order as
the contract, so `yuma()` and `pay()` return exactly what the chain would.
tests/test_yuma.py and test/yuma/YumaBlocTime.test.js both read the fixtures
in fixtures/ to keep the two pinned together.

Stake is BlocTime: a validator's voice is the BLOC it holds plus what is
delegated to it (BlocTime.getVotingPower). BLOC is dollars x seconds locked,
so a long lock outweighs a big one.

`Ledger` is a stateful paper subnet (JSON on disk) with the contract's rules:
registration with replacement, masked weights for recycled uids, tempo-gated
epochs, pending balances and claims. Its stake source is pluggable — paper
numbers by default, or the live BlocTime contract via `stake_fn`.
"""

import json
import os
import time
from pathlib import Path

ONE = 10 ** 18
BPS = 10_000

DEFAULTS = {
    'tempo': 3600,                      # seconds between epochs
    'emission_per_epoch': 100 * ONE,    # reward tokens per epoch
    'kappa': ONE // 2,                  # consensus quantile
    'bond_alpha': ONE // 10,            # EMA rate for new bonds
    'owner_cut_bps': 1000,              # off the top to the subnet owner
    'miner_share_bps': 5000,            # of the rest, to miners by incentive
    'min_validator_bloc': 0,
    'max_validators': 16,
    'max_miners': 64,
}


# ── pure maths ────────────────────────────────────────────────────────────

def yuma(S, W, B, kappa=ONE // 2, alpha=ONE // 10):
    """S: stake per validator. W: raw weight rows (V x M). B: bonds (V x M,
    ONE-scaled), updated IN PLACE. Returns dict(incentive, dividends,
    consensus, total_stake) — all ONE-scaled ints, like the contract."""
    V = len(S)
    M = len(W[0]) if V else 0
    I, D, C = [0] * M, [0] * V, [0] * M
    total = sum(S)
    out = lambda: {'incentive': I, 'dividends': D, 'consensus': C, 'total_stake': total}
    if total == 0 or M == 0:
        return out()

    W = [list(r) for r in W]
    s = [x * ONE // total for x in S]
    for i in range(V):
        if S[i] == 0:
            W[i] = [0] * M
            continue
        row = sum(W[i])
        W[i] = [w * ONE // row for w in W[i]]

    for j in range(M):
        # high → low by weight; ties keep lower uid first (stable insertion sort)
        order = sorted(range(V), key=lambda i: -W[i][j])
        cum = 0
        for i in order:
            cum += s[i]
            if cum >= kappa:
                C[j] = W[i][j]
                break

    R = [0] * M
    for j in range(M):
        col = [s[i] * min(W[i][j], C[j]) // ONE for i in range(V)]
        cs = sum(col)
        R[j] = cs
        for i in range(V):
            db = 0 if cs == 0 else col[i] * ONE // cs
            B[i][j] = (alpha * db + (ONE - alpha) * B[i][j]) // ONE

    rs = sum(R)
    if rs == 0:
        return out()
    I[:] = [r * ONE // rs for r in R]

    for i in range(V):
        D[i] = sum(B[i][j] * I[j] // ONE for j in range(M))
    ds = sum(D)
    if ds:
        D[:] = [d * ONE // ds for d in D]
    return out()


def pay(emission, I, D, owner_cut_bps=1000, miner_share_bps=5000):
    """Split one epoch's emission. Returns (miner_amounts, validator_amounts,
    owner_amount); owner takes its cut plus every unit of rounding dust. If
    nobody earned anything, nothing is emitted (all zeros)."""
    if emission == 0 or (sum(I) == 0 and sum(D) == 0):
        return [0] * len(I), [0] * len(D), 0
    owner_amt = emission * owner_cut_bps // BPS
    rest = emission - owner_amt
    miner_pool = rest * miner_share_bps // BPS
    val_pool = rest - miner_pool
    m = [miner_pool * x // ONE for x in I]
    v = [val_pool * x // ONE for x in D]
    return m, v, emission - sum(m) - sum(v)


# ── paper subnet ──────────────────────────────────────────────────────────

def _state_dir():
    return Path(os.environ.get('BLOCTIME_YUMA_DIR', os.path.expanduser('~/.mod/bloctime/yuma')))


class Ledger:
    """Paper YumaBlocTime. `stake_fn(addr) -> int` supplies BlocTime voting
    power; without one, stakes come from `set_stake` (paper BLOC, wei)."""

    def __init__(self, path=None, clock=None, stake_fn=None, owner='owner'):
        self.path = Path(path) if path else _state_dir() / 'ledger.json'
        self.clock = clock or (lambda: int(time.time()))
        self.stake_fn = stake_fn
        self.s = self._load(owner)

    # state

    def _load(self, owner):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {
            'params': dict(DEFAULTS), 'owner': owner,
            'validators': [], 'miners': [], 'paper_stake': {},
            'weights': {}, 'weights_seq': {}, 'bonds': {},
            'miner_epoch': {}, 'miner_seq': {}, 'seq': 0,
            'pending': {}, 'total_emitted': 0,
            'epoch_count': 0, 'last_epoch_time': self.clock(),
            'last': {'incentive': {}, 'consensus': {}, 'dividends': {}, 'stake': {}},
            'history': [],
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.s, indent=1))
        tmp.replace(self.path)

    @property
    def p(self):
        return self.s['params']

    def stake_of(self, addr):
        if self.stake_fn:
            return int(self.stake_fn(addr))
        return int(self.s['paper_stake'].get(addr, 0))

    def set_stake(self, addr, bloc_wei):
        self.s['paper_stake'][addr] = int(bloc_wei)
        self.save()
        return {'address': addr, 'stake': int(bloc_wei)}

    def set_params(self, **kw):
        for k, v in kw.items():
            if k not in DEFAULTS:
                raise ValueError(f'unknown param {k}; one of {sorted(DEFAULTS)}')
            self.p[k] = int(v)
        if not (0 < self.p['kappa'] <= ONE and 0 < self.p['bond_alpha'] <= ONE):
            raise ValueError('kappa and bond_alpha must be in (0, ONE]')
        self.save()
        return dict(self.p)

    # registration

    def _bonds(self, i):
        return self.s['bonds'].setdefault(str(i), [])

    def register_validator(self, addr):
        vs = self.s['validators']
        if addr in vs:
            raise ValueError('already validator')
        power = self.stake_of(addr)
        if power == 0 or power < self.p['min_validator_bloc']:
            raise ValueError('not enough BlocTime')
        replaced = None
        if len(vs) < self.p['max_validators']:
            uid = len(vs)
            vs.append(addr)
        else:
            powers = [self.stake_of(v) for v in vs]
            uid = min(range(len(vs)), key=lambda i: powers[i])
            if power <= powers[uid]:
                raise ValueError('set full: need more BlocTime than the weakest')
            replaced = vs[uid]
            vs[uid] = addr
            self.s['bonds'][str(uid)] = []
            self.s['weights'].pop(str(uid), None)
            self.s['last']['dividends'].pop(str(uid), None)
        self.save()
        return {'uid': uid, 'replaced': replaced}

    def register_miner(self, addr):
        ms = self.s['miners']
        if addr in ms:
            raise ValueError('already miner')
        replaced = None
        if len(ms) < self.p['max_miners']:
            uid = len(ms)
            ms.append(addr)
        else:
            inc = self.s['last']['incentive']
            cands = [j for j in range(len(ms)) if self.s['miner_epoch'].get(str(j), 0) < self.s['epoch_count']]
            if not cands:
                raise ValueError('set full: everyone is immune')
            uid = min(cands, key=lambda j: (inc.get(str(j), 0), j))
            replaced = ms[uid]
            ms[uid] = addr
            for b in self.s['bonds'].values():
                if uid < len(b):
                    b[uid] = 0
            inc.pop(str(uid), None)
            self.s['last']['consensus'].pop(str(uid), None)
        self.s['miner_epoch'][str(uid)] = self.s['epoch_count']
        self.s['seq'] += 1
        self.s['miner_seq'][str(uid)] = self.s['seq']
        self.save()
        return {'uid': uid, 'replaced': replaced}

    def set_weights(self, addr, weights):
        """weights: {miner_uid: raw_weight} (or a list indexed by uid)."""
        vs = self.s['validators']
        if addr not in vs:
            raise ValueError('not validator')
        v = vs.index(addr)
        if isinstance(weights, list):
            weights = dict(enumerate(weights))
        row = [0] * len(self.s['miners'])
        for j, w in weights.items():
            j = int(j)
            if not 0 <= j < len(row):
                raise ValueError(f'bad uid {j}')
            row[j] = int(w)
        self.s['weights'][str(v)] = row
        self.s['seq'] += 1
        self.s['weights_seq'][str(v)] = self.s['seq']
        self.save()
        return {'uid': v, 'row': row}

    # epoch

    def _masked(self, i, M):
        row = self.s['weights'].get(str(i), [])
        at = self.s['weights_seq'].get(str(i), 0)
        return [0 if self.s['miner_seq'].get(str(j), 0) > at else (row[j] if j < len(row) else 0)
                for j in range(M)]

    def _inputs(self):
        V, M = len(self.s['validators']), len(self.s['miners'])
        S, W, B = [], [], []
        for i, v in enumerate(self.s['validators']):
            w = self._masked(i, M)
            stake = 0
            if sum(w) > 0:
                p = self.stake_of(v)
                if p >= self.p['min_validator_bloc']:
                    stake = p
            b = self._bonds(i)
            S.append(stake)
            W.append(w)
            B.append([b[j] if j < len(b) else 0 for j in range(M)])
        return S, W, B

    def next_epoch_time(self):
        return self.s['last_epoch_time'] + self.p['tempo']

    def preview(self):
        S, W, B = self._inputs()
        r = yuma(S, W, B, self.p['kappa'], self.p['bond_alpha'])
        r['stake'] = S
        r['bonds'] = B
        return r

    def epoch(self, force=False):
        now = self.clock()
        if not force and now < self.next_epoch_time():
            raise ValueError(f'epoch not due for {self.next_epoch_time() - now}s')
        S, W, B = self._inputs()
        r = yuma(S, W, B, self.p['kappa'], self.p['bond_alpha'])
        vs, ms = self.s['validators'], self.s['miners']
        for i in range(len(vs)):
            self.s['bonds'][str(i)] = B[i]
        last = self.s['last']
        last['stake'] = {str(i): S[i] for i in range(len(vs))}
        last['dividends'] = {str(i): r['dividends'][i] for i in range(len(vs))}
        last['incentive'] = {str(j): r['incentive'][j] for j in range(len(ms))}
        last['consensus'] = {str(j): r['consensus'][j] for j in range(len(ms))}

        mp, vp, op = pay(self.p['emission_per_epoch'], r['incentive'], r['dividends'],
                         self.p['owner_cut_bps'], self.p['miner_share_bps'])
        pend = self.s['pending']
        for j, a in enumerate(mp):
            if a:
                pend[ms[j]] = pend.get(ms[j], 0) + a
        for i, a in enumerate(vp):
            if a:
                pend[vs[i]] = pend.get(vs[i], 0) + a
        emitted = sum(mp) + sum(vp) + op
        if emitted:
            pend[self.s['owner']] = pend.get(self.s['owner'], 0) + op
        self.s['total_emitted'] += emitted
        self.s['epoch_count'] += 1
        self.s['last_epoch_time'] = now
        rec = {'epoch': self.s['epoch_count'], 'time': now, 'emission': emitted,
               'total_stake': r['total_stake'], 'to_miners': sum(mp),
               'to_validators': sum(vp), 'to_owner': op if emitted else 0}
        self.s['history'] = (self.s['history'] + [rec])[-200:]
        self.save()
        return {**rec, 'incentive': r['incentive'], 'dividends': r['dividends'],
                'consensus': r['consensus'], 'stake': S}

    def claim(self, addr):
        amt = self.s['pending'].pop(addr, 0)
        if not amt:
            raise ValueError('nothing to claim')
        self.save()
        return {'address': addr, 'claimed': amt}

    def state(self):
        s = self.s
        M = len(s['miners'])
        return {
            'params': s['params'], 'owner': s['owner'],
            'epoch_count': s['epoch_count'], 'last_epoch_time': s['last_epoch_time'],
            'next_epoch_time': self.next_epoch_time(), 'total_emitted': s['total_emitted'],
            'validators': [{
                'uid': i, 'address': v, 'bloctime': self.stake_of(v),
                'last_stake': s['last']['stake'].get(str(i), 0),
                'dividend': s['last']['dividends'].get(str(i), 0),
                'weights': self._masked(i, M),
                'bonds': (self._bonds(i) + [0] * M)[:M],
                'pending': s['pending'].get(v, 0),
            } for i, v in enumerate(s['validators'])],
            'miners': [{
                'uid': j, 'address': a,
                'incentive': s['last']['incentive'].get(str(j), 0),
                'consensus': s['last']['consensus'].get(str(j), 0),
                'pending': s['pending'].get(a, 0),
            } for j, a in enumerate(s['miners'])],
            'owner_pending': s['pending'].get(s['owner'], 0),
            'history': s['history'][-20:],
        }

    def reset(self):
        owner = self.s['owner']
        if self.path.exists():
            self.path.unlink()
        self.s = self._load(owner)
        self.save()
        return {'reset': True}
