"""Paper pool host — agents call prices with fake money, a stored
contract decides who gets what.

This is the admission-and-storage layer around the contract:

  * paper_machine.py holds the rules (pure state machine, integer math);
  * paper_contract.wat is the distribution kernel, run as WebAssembly
    through watvm.py — no wasmtime, no chain, no network;
  * this module verifies signatures, stamps the clock and the oracle
    price into each message, and appends accepted messages to a
    hash-chained log under ~/.mod/prefi/paper/.

The log IS the ledger. Each record carries the hash of the one before it
and the sha256 root of the state after it, so `verify()` — or anyone
with a copy of log.jsonl — can replay from genesis and prove every
balance. No live blockchain is involved: the chain is stored, and the
door stays open to anchoring the latest root somewhere public later.

Prices come from the same oracle the dollar pool settles on (Hyperliquid
/ CoinGecko / Bittensor / DEX history via Mod._price_at), fetched once
at resolution and written into the resolve message — replay never asks
the internet anything.

Play is open by default (config `open`): PAPER is worthless, but a call
may be signed exactly like a pool stake (sigauth, per-account nonces)
and signed calls are flagged on the record, so a verified accuracy
history is available to any agent that wants its record to mean
something.
"""

import hashlib
import json
import os
import time as time_mod
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import paper_machine as machine
    import sigauth
    import identity
except ImportError:                             # imported as a package
    from . import paper_machine as machine
    from . import sigauth
    from . import identity

MICRO = machine.MICRO
PPM = machine.PPM

CONTRACT_PATH = Path(__file__).parent / 'paper_contract.wat'
GENESIS_PREV = '0' * 64


def _record_hash(record: Dict) -> str:
    return hashlib.sha256(machine.canonical(record).encode()).hexdigest()


class WasmSplit:
    """The contract kernel behind a callable: weights in linear memory,
    one invoke, payouts back out. Interface-identical to the reference
    `machine.split_weighted`, and required to agree with it."""

    def __init__(self, contract_path: Path = CONTRACT_PATH):
        try:
            import watvm
        except ImportError:                     # pragma: no cover
            from . import watvm
        self.text = contract_path.read_text()
        self.sha256 = hashlib.sha256(self.text.encode()).hexdigest()
        self.module = watvm.Module(self.text)
        self.capacity = len(self.module.memory) // 24

    def __call__(self, pot: int, weights: List[int]):
        if len(weights) > self.capacity:        # pragma: no cover
            return machine.split_weighted(pot, weights)
        for i, w in enumerate(weights):
            self.module.write_u64(24 * i, w)
            self.module.write_u64(24 * i + 8, 0)
            self.module.write_u64(24 * i + 16, 0)
        leftover = self.module.invoke('distribute', len(weights), int(pot))
        payouts = [self.module.read_u64(24 * i + 8)
                   for i in range(len(weights))]
        return payouts, int(leftover)


class Paper:
    """The paper pool: free-money predictions, contract-settled."""

    def __init__(self, store_dir, price_at: Callable = None,
                 price_now: Callable = None, markets: Callable = None,
                 config: Dict = None):
        self.dir = Path(store_dir) / 'paper'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / 'log.jsonl'
        self.state_path = self.dir / 'state.json'
        self.lock_path = self.dir / 'lock'
        self.price_at = price_at
        self.price_now = price_now
        self.markets = markets or (lambda: [])
        self._genesis_config = config or {}
        self._state: Optional[Dict] = None
        self._last_hash: Optional[str] = None
        self._last_auto = 0.0
        if os.environ.get('PREFI_PAPER_WASM', '1') in ('0', 'false', 'no'):
            self.split = machine.split_weighted
            self.engine = 'python-reference'
        else:
            self._wasm = WasmSplit()
            self.split = self._wasm
            self.engine = 'wasm (watvm)'

    # ── Storage ──────────────────────────────────────────────────────

    @contextmanager
    def _lock(self):
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):      # pragma: no cover
                pass
            yield
        finally:
            os.close(fd)

    def _records(self) -> List[Dict]:
        if not self.log_path.exists():
            return []
        records = []
        with open(self.log_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def _replay(self, records: List[Dict]) -> Dict:
        """Log → state, trusting nothing but the machine."""
        state = None
        for rec in records:
            msg = rec['msg']
            if msg['type'] == 'genesis':
                state = machine.genesis(msg.get('config') or {},
                                        time=msg['time'])
            else:
                state, _ = machine.apply(state, msg, self.split)
        return state

    def state(self) -> Dict:
        if self._state is not None:
            return self._state
        with self._lock():
            if self._state is not None:         # pragma: no cover
                return self._state
            records = self._records()
            if not records:
                return self._genesis()
            last = records[-1]
            self._last_hash = _record_hash(last)
            snap = None
            if self.state_path.exists():
                try:
                    snap = json.loads(self.state_path.read_text())
                except (json.JSONDecodeError, OSError):
                    snap = None
            if snap and snap.get('root') == last['root'] \
                    and machine.state_root(snap['state']) == last['root']:
                self._state = snap['state']
            else:                               # torn snapshot — replay
                self._state = self._replay(records)
            return self._state

    def _genesis(self) -> Dict:
        state = machine.genesis(self._genesis_config,
                                time=int(time_mod.time()))
        record = {'seq': 0, 'time': state['time'],
                  'msg': {'type': 'genesis', 'time': state['time'],
                          'config': state['config']},
                  'prev': GENESIS_PREV, 'root': machine.state_root(state)}
        self._append_record(record, state)
        return state

    def _append_record(self, record: Dict, state: Dict):
        with open(self.log_path, 'a') as fh:
            fh.write(machine.canonical(record) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        tmp = self.state_path.with_suffix('.json.tmp')
        with open(tmp, 'w') as fh:
            json.dump({'root': record['root'], 'state': state}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)
        self._state = state
        self._last_hash = _record_hash(record)

    def _apply(self, msg: Dict) -> List[Dict]:
        """Admit one message: apply, chain, store. MachineError refuses
        it and leaves no trace — an invalid message is not history."""
        with self._lock():
            state = self.state()
            msg = dict(msg)
            msg['time'] = max(int(msg.get('time') or time_mod.time()),
                              state['time'])
            new_state, events = machine.apply(state, msg, self.split)
            record = {'seq': new_state['seq'], 'time': msg['time'],
                      'msg': msg, 'prev': self._last_hash,
                      'root': machine.state_root(new_state)}
            self._append_record(record, new_state)
            return events

    # ── Auth ─────────────────────────────────────────────────────────

    def _auth(self, action: str, address: str, fields, signature) -> Dict:
        """{'signed': bool, 'nonce': int} or {'error'}. Open deployments
        take unsigned play; a provided signature is always checked, so a
        signed record can never be faked by leaving the field empty."""
        address = identity.normalize(address)
        acct = self.state()['accounts'].get(address, {})
        nonce = int(acct.get('nonce', 0))
        cfg = self.state()['config']
        if not signature and cfg['open']:
            return {'address': address, 'signed': False, 'nonce': nonce}
        check = sigauth.verify(action, address, fields, nonce, signature)
        if not check['ok']:
            return {'error': check['error'], 'message': check.get('message')}
        return {'address': address, 'signed': not check.get('unsigned'),
                'nonce': nonce}

    def sign_request(self, action: str, address: str, **fields) -> Dict:
        """What a wallet should sign for `action` — same shape as the
        dollar pool's, so agent tooling reuses one signer."""
        address = identity.normalize(address)
        nonce = int(self.state()['accounts'].get(address, {})
                    .get('nonce', 0))
        pairs = [(k, str(v)) for k, v in fields.items()]
        return {'action': action, 'address': address, 'nonce': nonce,
                'message': sigauth.action_message(action, address, pairs,
                                                  nonce)}

    # ── Markets & rounds ─────────────────────────────────────────────

    def _symbol(self, asset: str) -> Optional[str]:
        want = str(asset).strip().upper()
        for m in self.markets():
            if m.get('symbol', '').upper() == want:
                return m['symbol']
        return None

    def current_index(self, now: float = None) -> int:
        interval = self.state()['config']['interval']
        return int((now or time_mod.time()) // interval)

    # ── Writes ───────────────────────────────────────────────────────

    def register(self, address: str, name: str = '', kind: str = 'agent',
                 signature: str = None) -> Dict:
        auth = self._auth('paper_register', address,
                          [('name', name), ('kind', kind)], signature)
        if 'error' in auth:
            return auth
        try:
            self._apply({'type': 'register', 'address': auth['address'],
                         'name': name, 'kind': kind,
                         'signed': auth['signed'], 'nonce': auth['nonce']})
        except machine.MachineError as err:
            return {'error': str(err)}
        return self.account(auth['address'])

    def faucet(self, address: str, signature: str = None) -> Dict:
        auth = self._auth('paper_faucet', address, [], signature)
        if 'error' in auth:
            return auth
        try:
            events = self._apply({'type': 'faucet',
                                  'address': auth['address'],
                                  'signed': auth['signed'],
                                  'nonce': auth['nonce']})
        except machine.MachineError as err:
            return {'error': str(err)}
        out = self.account(auth['address'])
        out['granted'] = events[0]['amount'] / MICRO
        return out

    def predict(self, address: str, asset: str, price: float,
                stake: float, signature: str = None) -> Dict:
        symbol = self._symbol(asset)
        if symbol is None:
            return {'error': f'unknown market {asset!r} — see /markets'}
        price_e6 = int(round(float(price) * MICRO))
        stake_units = int(round(float(stake) * MICRO))
        index = self.current_index()
        auth = self._auth('paper_predict', address,
                          [('asset', symbol), ('price_e6', str(price_e6)),
                           ('stake', str(stake_units)),
                           ('round', str(index))], signature)
        if 'error' in auth:
            return auth
        try:
            self._apply({'type': 'predict', 'address': auth['address'],
                         'asset': symbol, 'price_e6': price_e6,
                         'stake': stake_units, 'round': index,
                         'signed': auth['signed'], 'nonce': auth['nonce']})
        except machine.MachineError as err:
            return {'error': str(err)}
        rec = self.state()['rounds'][str(index)]
        return {'round': index, 'asset': symbol,
                'closes': rec['closes'], 'resolves_at': rec['closes'],
                'price': price_e6 / MICRO, 'stake': stake_units / MICRO,
                'signed': auth['signed'],
                'balance': self.state()['accounts'][auth['address']]
                ['balance'] / MICRO}

    def transfer(self, address: str, to: str, amount: float,
                 signature: str = None) -> Dict:
        to = identity.normalize(to)
        units = int(round(float(amount) * MICRO))
        auth = self._auth('paper_transfer', address,
                          [('to', to), ('amount', str(units))], signature)
        if 'error' in auth:
            return auth
        try:
            self._apply({'type': 'transfer', 'address': auth['address'],
                         'to': to, 'amount': units,
                         'signed': auth['signed'], 'nonce': auth['nonce']})
        except machine.MachineError as err:
            return {'error': str(err)}
        return {'from': auth['address'], 'to': to,
                'amount': units / MICRO,
                'balance': self.state()['accounts'][auth['address']]
                ['balance'] / MICRO}

    # ── Resolution ───────────────────────────────────────────────────

    def due(self, now: float = None) -> List[Dict]:
        """Every (round, asset) pot past its close and not yet resolved."""
        now = now or time_mod.time()
        out = []
        for key, rec in self.state()['rounds'].items():
            if rec['closes'] > now:
                continue
            for asset, pot in rec['assets'].items():
                if pot['resolved'] is None:
                    out.append({'round': int(key), 'asset': asset,
                                'closes': rec['closes'],
                                'entries': len(pot['entries'])})
        return sorted(out, key=lambda d: (d['round'], d['asset']))

    def resolve(self, index: int = None, asset: str = None,
                now: float = None) -> Dict:
        """Settle every due pot (or one). Lazy and permissionless, like
        the prediction layer: whoever asks next pays the oracle lookups,
        and the fetched price is written into the log so replays never
        re-fetch. A pot whose oracle won't answer is skipped, not lost."""
        if self.price_at is None:
            return {'error': 'no price oracle wired'}
        resolved, skipped = [], []
        for item in self.due(now):
            if index is not None and item['round'] != int(index):
                continue
            if asset is not None and item['asset'] != asset:
                continue
            try:
                quote = self.price_at(item['asset'], item['closes'])
            except Exception as err:
                skipped.append({**item, 'reason': str(err)})
                continue
            price = (quote or {}).get('price')
            if not price or price <= 0:
                skipped.append({**item, 'reason': 'oracle has no price'})
                continue
            try:
                self._apply({'type': 'resolve', 'round': item['round'],
                             'asset': item['asset'],
                             'actual_e6': int(round(float(price) * MICRO)),
                             'mode': (quote or {}).get('mode', 'historical'),
                             'time': max(int(item['closes']),
                                         int(now or time_mod.time()))})
            except machine.MachineError as err:
                skipped.append({**item, 'reason': str(err)})
                continue
            resolved.append(item)
        return {'resolved': resolved, 'skipped': skipped}

    def _auto_resolve(self):
        """Reads settle what is due, at most once a minute, and never let
        a dead oracle break a read."""
        if time_mod.time() - self._last_auto < 60:
            return
        self._last_auto = time_mod.time()
        try:
            self.resolve()
        except Exception:                       # pragma: no cover
            pass

    # ── Reads ────────────────────────────────────────────────────────

    def status(self) -> Dict:
        st = self.state()
        cfg = st['config']
        now = time_mod.time()
        index = self.current_index(now)
        return {
            'engine': self.engine,
            'contract': getattr(self, '_wasm', None)
            and self._wasm.sha256,
            'entries': st['seq'] + 1,
            'root': machine.state_root(st),
            'accounts': len(st['accounts']),
            'minted': st['minted'] / MICRO,
            'reserve': st['reserve'] / MICRO,
            'round': index,
            'round_closes': (index + 1) * cfg['interval'],
            'due': len(self.due(now)),
            'config': self._config_view(cfg),
        }

    @staticmethod
    def _config_view(cfg: Dict) -> Dict:
        out = dict(cfg)
        for key in ('faucet_grant', 'min_stake', 'max_stake'):
            out[key] = out[key] / MICRO
        return out

    def config(self) -> Dict:
        return self._config_view(self.state()['config'])

    def set_config(self, **patch) -> Dict:
        """Host-operator only — reachable through `m prefi/paper_set_config`,
        never the HTTP API."""
        for key in ('faucet_grant', 'min_stake', 'max_stake'):
            if key in patch and patch[key] is not None:
                patch[key] = int(round(float(patch[key]) * MICRO))
        patch = {k: v for k, v in patch.items() if v is not None}
        try:
            self._apply({'type': 'config', 'patch': patch})
        except machine.MachineError as err:
            return {'error': str(err)}
        return self.config()

    def account(self, address: str) -> Dict:
        address = identity.normalize(address)
        self._auto_resolve()
        acct = self.state()['accounts'].get(address)
        if acct is None:
            return {'address': address, 'registered': False,
                    'balance': 0.0}
        resolved = acct['resolved']
        return {
            'address': address, 'registered': True,
            'name': acct['name'], 'kind': acct['kind'],
            'balance': acct['balance'] / MICRO,
            'staked': acct['staked'] / MICRO,
            'won': acct['won'] / MICRO,
            'profit': (acct['won'] - acct['staked']) / MICRO,
            'entries': acct['entries'], 'resolved': resolved,
            'accuracy': round(acct['score_sum'] / PPM / resolved, 6)
            if resolved else None,
            'nonce': acct['nonce'],
            'next_faucet': acct['last_faucet']
            + self.state()['config']['faucet_interval'],
        }

    def leaderboard(self, limit: int = 50) -> List[Dict]:
        self._auto_resolve()
        rows = []
        for address, acct in self.state()['accounts'].items():
            if not acct['entries']:
                continue
            resolved = acct['resolved']
            rows.append({
                'address': address, 'name': acct['name'],
                'kind': acct['kind'],
                'balance': acct['balance'] / MICRO,
                'profit': (acct['won'] - acct['staked']) / MICRO,
                'accuracy': round(acct['score_sum'] / PPM / resolved, 6)
                if resolved else None,
                'entries': acct['entries'], 'resolved': resolved,
            })
        rows.sort(key=lambda r: (-r['profit'], -(r['accuracy'] or 0),
                                 r['address']))
        return rows[:int(limit)]

    def round(self, index: int = None) -> Dict:
        self._auto_resolve()
        st = self.state()
        if index is None:
            index = self.current_index()
        rec = st['rounds'].get(str(int(index)))
        cfg = st['config']
        if rec is None:
            return {'index': int(index),
                    'opens': int(index) * cfg['interval'],
                    'closes': (int(index) + 1) * cfg['interval'],
                    'assets': {}, 'entries': 0}
        view = {'index': rec['index'], 'opens': rec['opens'],
                'closes': rec['closes'], 'tol_ppm': rec['tol_ppm'],
                'assets': {}, 'entries': 0}
        for asset, pot in rec['assets'].items():
            entries = [self._entry_view(e) for e in pot['entries']]
            view['entries'] += len(entries)
            resolved = pot['resolved'] and {
                'actual': pot['resolved']['actual_e6'] / MICRO,
                'mode': pot['resolved']['mode'],
                'pot': pot['resolved']['pot'] / MICRO,
                'leftover': pot['resolved']['leftover'] / MICRO,
            }
            view['assets'][asset] = {'entries': entries,
                                     'pot': sum(e['stake']
                                                for e in pot['entries'])
                                     / MICRO,
                                     'resolved': resolved}
        return view

    @staticmethod
    def _entry_view(entry: Dict) -> Dict:
        out = {'address': entry['address'],
               'price': entry['price_e6'] / MICRO,
               'stake': entry['stake'] / MICRO,
               'time': entry['time'], 'signed': entry['signed']}
        if 'score_ppm' in entry:
            out['accuracy'] = entry['score_ppm'] / PPM
            out['payout'] = entry.get('payout', 0) / MICRO
        return out

    def rounds(self, limit: int = 20) -> List[Dict]:
        keys = sorted((int(k) for k in self.state()['rounds']),
                      reverse=True)
        return [self.round(k) for k in keys[:int(limit)]]

    def log(self, limit: int = 100) -> List[Dict]:
        records = self._records()
        return records[-int(limit):]

    def verify(self) -> Dict:
        """Replay the whole log and prove the chain: every record's prev
        hash and state root, ending at the live state. This is the audit
        a block explorer would do — done locally, from the stored file."""
        records = self._records()
        if not records:
            return {'ok': True, 'entries': 0, 'root': None}
        state = None
        prev = GENESIS_PREV
        for i, rec in enumerate(records):
            if rec.get('prev') != prev:
                return {'ok': False, 'entries': i,
                        'error': f'chain break at seq {rec.get("seq")}'}
            msg = rec['msg']
            try:
                if msg['type'] == 'genesis':
                    if state is not None:
                        raise machine.MachineError('second genesis')
                    state = machine.genesis(msg.get('config') or {},
                                            time=msg['time'])
                else:
                    state, _ = machine.apply(state, msg, self.split)
            except machine.MachineError as err:
                return {'ok': False, 'entries': i,
                        'error': f'invalid message at seq '
                                 f'{rec.get("seq")}: {err}'}
            if machine.state_root(state) != rec.get('root'):
                return {'ok': False, 'entries': i,
                        'error': f'root mismatch at seq {rec.get("seq")}'}
            prev = _record_hash(rec)
        live = self.state()
        ok = machine.state_root(live) == records[-1]['root']
        return {'ok': ok, 'entries': len(records),
                'root': records[-1]['root'],
                **({} if ok else {'error': 'live state diverges from log'})}
