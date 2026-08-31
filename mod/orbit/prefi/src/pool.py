"""The stake pool — real stablecoins in, one pot per asset, accuracy takes it.

    deposit USDC/USDT0 on HyperEVM
        → stake $N on where an asset closes this round
        → at the round's close the pot is split by  dollars × accuracy
        → withdraw back to your wallet

The scoring rule is the one thing worth reading twice. Every entry gets

    relative L1 error   e = |called − actual| / actual
    accuracy            a = model(e, tolerance)          # 1 at e=0, 0 far out
    score               s = dollars × a
    share               payout = pot × s / Σs

so being twice as close is worth twice as much, and so is staking twice as
much. The default model is `linear` at `tolerance = 1.0`, which is exactly
`a = 1 − e`: a pure relative-L1 score with no curve on top. Sharpen it by
dropping the tolerance (0.02 → only calls inside 2% score at all) or swap the
curve for `l2`/`exponential`/`threshold`/… — or write your own. The model is a
**score function** (`curves.py`): an expression over the miss plus its params,
stored in the library beside the ledger, shareable as a code or a store CID.

A round also takes **free calls**: same price call, same scoring rule, no
money. A free entry is kept out of the pot entirely — not merely staked at $0 —
so it cannot dilute a single dollar somebody put at risk. What it earns instead
is `would_win`: what that call *would* have taken had it been staked
`free_notional`, computed against the pot that actually formed. It is the only
honest way to show someone what they missed.

Three design points that are load-bearing:

* **One pot per asset, never a shared one.** Normalised error is comparable
  across assets, which tempts you into a single pot — but then anyone can call
  a stablecoin at $1.00 for a guaranteed ~0 error and drain the BTC stakers.
  Pots are keyed (round, asset) and only ever pay their own stakers.
* **Entries close before the round does.** `entry_cutoff` (1h by default) stops
  the last-second stake that is placed once the answer is basically known.
* **Params are snapshotted onto the round** the moment it opens, so an owner
  retuning tolerance mid-week cannot re-price bets already on the table. A free
  call snapshots its notional the same way, so a counterfactual cannot be
  re-priced either.

Money is held as integer micro-dollars everywhere it is divided; the
largest-remainder split guarantees Σ payouts == pot to the last unit, with no
dust quietly accruing to the house.
"""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import scoring
    import curves
    import hyperevm
    import sigauth
except ImportError:                                  # imported as a package
    from . import scoring, curves, hyperevm, sigauth


MICRO = 1_000_000          # ledger resolution: 1 micro-dollar
MIN_INTERVAL = 3600        # an hour is the shortest round that can be settled
MAX_INTERVAL = 31_536_000  # a year
MAX_FEE_BPS = 500          # 5% — the protocol cannot take more than this

DEFAULT_CONFIG = {
    'interval': 604800,      # weekly, the owner's to change
    'entry_cutoff': 3600,    # entries stop this long before the close
    'model': 'linear',       # a score function: a default, or one from the library
    'tolerance': 1.0,        # its `tol` param; 1.0 + linear == accuracy is exactly 1 − relL1
    'model_params': {},      # the function's other params, overridden by name
    'min_stake': 5.0,        # dollars
    'max_stake': 0.0,        # 0 = uncapped
    'min_withdraw': 1.0,
    'fee_bps': 0,            # protocol cut of a settled pot, to the treasury
    'auto_pay': False,       # send withdrawals from the hot key automatically
    'spot_grace': 900,       # settle off spot if we're this close to the close
    'free_per_round': 3,     # free calls per address per round (0 = off)
    'free_notional': 100.0,  # paper stake a free call's `would_win` is priced at
    # A DEX token (Solana, Base) must have this many dollars in its pool to be
    # listed, and to take a stake — a pot on a $900 pool settles against a
    # price one trade can move. 0 = no floor. The owner's number.
    'min_liquidity_usd': 10_000.0,
}

MAX_FREE_PER_ROUND = 50    # a free board is only readable if it is bounded


# ── Pure math ────────────────────────────────────────────────────────

def usd_to_units(amount: float) -> int:
    """Dollars → micro-dollars, truncated. Never rounds a balance up."""
    return int(float(amount) * MICRO)


def units_to_usd(units: int) -> float:
    return round(int(units) / MICRO, 6)


def accuracy(predicted: float, actual: float, fn: Dict) -> Dict:
    """The relative-L1 miss and the 0..1 accuracy it earns under `fn`, a
    score-function snapshot `{name, expr, params}` (see `curves.resolve`)."""
    err = scoring.normalized_error(predicted, actual)
    if err == float('inf'):
        return {'rel_error': None, 'accuracy': 0.0}
    value = curves.evaluate(fn, err)
    return {'rel_error': round(err, 8),
            'accuracy': round(min(1.0, max(0.0, value)), 8)}


def fn_for(model, tolerance: float = None, params: Dict = None,
           library: 'curves.Library' = None, fallback: Dict = None) -> Dict:
    """The snapshot a round stores: the named function with the pool's
    tolerance and overrides folded into its params."""
    return curves.resolve(model, library, tolerance=tolerance, params=params,
                          fallback=fallback)


def split_pot(pot_units: int, scores: List[float]) -> List[int]:
    """Divide a pot by score with no dust left over.

    Largest-remainder: floor every share, then hand the leftover units out one
    at a time to whoever was robbed hardest by the flooring. Σ result is always
    exactly `pot_units`, which is the property that makes the ledger balance.
    """
    total = sum(scores)
    if pot_units <= 0 or total <= 0:
        return [0] * len(scores)

    exact = [pot_units * s / total for s in scores]
    out = [int(x) for x in exact]
    leftover = pot_units - sum(out)
    if leftover:
        order = sorted(range(len(scores)),
                       key=lambda i: (-(exact[i] - out[i]), -scores[i], i))
        for i in order[:leftover]:
            out[i] += 1
    return out


def fee_split(gross_units: int, fee_bps: int) -> Tuple[int, int]:
    """(fee, pot) in micro-dollars. One definition, so a free call's
    counterfactual is charged exactly the fee a real stake would have been."""
    fee_units = int(gross_units) * int(fee_bps) // 10000
    return fee_units, int(gross_units) - fee_units


def settle_asset(entries: List[Dict], actual: float, fn: Dict,
                 fee_bps: int) -> Dict:
    """Score one (round, asset) pot and decide who gets what.

    Two outcomes. Normally the pot is split by dollars × accuracy. If *nobody*
    scored above zero — every call outside tolerance under a `threshold` or
    `linear` model — there is no winner to pay, so every stake is refunded in
    full and the protocol takes no fee. Losing everyone's money because the
    week was hard is not a rule anyone would agree to in advance.
    """
    scored = []
    for e in entries:
        acc = accuracy(e['predicted_price'], actual, fn)
        scored.append({
            **e,
            'rel_error': acc['rel_error'],
            'accuracy': acc['accuracy'],
            'score': round(float(e['amount']) * acc['accuracy'], 8),
        })

    gross_units = sum(usd_to_units(e['amount']) for e in scored)
    total_score = sum(e['score'] for e in scored)

    if total_score <= 0:
        for e in scored:
            e['payout'] = e['amount']
            e['share'] = 0.0
            e['net'] = 0.0
        return {'mode': 'refund', 'actual_price': actual,
                'gross': units_to_usd(gross_units), 'fee': 0.0,
                'pot': units_to_usd(gross_units), 'total_score': 0.0,
                'winner': None, 'entries': scored}

    fee_units, pot_units = fee_split(gross_units, fee_bps)
    payouts = split_pot(pot_units, [e['score'] for e in scored])

    for e, units in zip(scored, payouts):
        e['payout'] = units_to_usd(units)
        e['share'] = round(e['score'] / total_score, 8)
        e['net'] = round(e['payout'] - float(e['amount']), 6)

    best = max(scored, key=lambda e: e['score'])
    return {'mode': 'scored', 'actual_price': actual,
            'gross': units_to_usd(gross_units),
            'fee': units_to_usd(fee_units),
            'pot': units_to_usd(pot_units),
            'total_score': round(total_score, 8),
            'winner': {'address': best['address'], 'score': round(best['score'], 8),
                       'payout': best['payout'], 'rel_error': best['rel_error']},
            'entries': scored}


def shadow_payout(predicted: float, actual: float, fn: Dict,
                  fee_bps: int, notional: float, paid_score: float = 0.0,
                  paid_gross: float = 0.0) -> Dict:
    """What a free call would have taken, had it been staked `notional`.

    The counterfactual includes the caller's own money. Staking moves the pot as
    well as the split, so the question worth answering is "if I had put $100 in,
    what would have come back out" — not "what could I have skimmed off a pot I
    never funded", which is a bigger number and a lie.

    `paid_score` and `paid_gross` describe the real pot (Σ dollars × accuracy and
    Σ dollars), computed once per pot by the caller — a free call is priced
    against the pot that actually formed, and never against another free one.
    Floored to the micro-dollar instead of largest-remainder'd like the real
    split: nobody is being paid here, so the spare unit has nowhere to go.
    """
    acc = accuracy(predicted, actual, fn)
    notional = round(float(notional), 6)
    my_score = round(notional * acc['accuracy'], 8)
    total_score = paid_score + my_score

    if total_score <= 0:
        # The everybody-missed refund: the stake comes back, nothing is won.
        return {**acc, 'score': my_score, 'notional': notional,
                'would_win': notional, 'would_net': 0.0, 'would_mode': 'refund'}

    gross_units = usd_to_units(paid_gross) + usd_to_units(notional)
    _fee, pot_units = fee_split(gross_units, fee_bps)
    won = units_to_usd(int(pot_units * my_score / total_score))
    return {**acc, 'score': my_score, 'notional': notional,
            'would_win': won, 'would_net': round(won - notional, 6),
            'would_mode': 'scored'}


def settle_free(free_entries: List[Dict], paid_entries: List[Dict], actual: float,
                fn: Dict, fee_bps: int) -> List[Dict]:
    """Score every free call in a pot against the pot it never entered."""
    paid_gross = sum(float(e['amount']) for e in paid_entries)
    paid_score = sum(float(e['amount'])
                     * accuracy(e['predicted_price'], actual, fn)['accuracy']
                     for e in paid_entries)
    return [{**e, **shadow_payout(e['predicted_price'], actual, fn,
                                  fee_bps, e.get('notional') or 0.0,
                                  paid_score, paid_gross)}
            for e in free_entries]


def validate_config(patch: Dict, current: Dict = None,
                    library: 'curves.Library' = None) -> Dict:
    """Merge a config patch over the live config and bounds-check every field.

    `library` is where a non-default `model` name is looked up; without one
    only the built-in functions are accepted."""
    merged = {**DEFAULT_CONFIG, **(current or {})}
    for key, value in (patch or {}).items():
        if key in DEFAULT_CONFIG:
            merged[key] = value

    merged['interval'] = int(merged['interval'])
    if not MIN_INTERVAL <= merged['interval'] <= MAX_INTERVAL:
        raise ValueError(f'interval must be {MIN_INTERVAL}..{MAX_INTERVAL} seconds')

    merged['entry_cutoff'] = int(merged['entry_cutoff'])
    if merged['entry_cutoff'] < 0:
        raise ValueError('entry_cutoff must be >= 0')
    if merged['entry_cutoff'] >= merged['interval']:
        raise ValueError('entry_cutoff must be shorter than the interval — '
                         'otherwise a round closes to entries before it opens')

    merged['tolerance'] = float(merged['tolerance'])
    if merged['tolerance'] <= 0:
        raise ValueError('tolerance must be > 0')

    # The model is a score function; resolving it proves the name exists, the
    # overrides name real parameters, and the program runs across the grid.
    merged['model'] = str(merged['model'] or '').strip().lower()
    raw_params = merged.get('model_params') or {}
    if isinstance(raw_params, str):
        try:
            raw_params = json.loads(raw_params or '{}')
        except json.JSONDecodeError as exc:
            raise ValueError(f'model_params must be JSON: {exc.msg}')
    try:
        fn = fn_for(merged['model'], merged['tolerance'], raw_params, library)
    except curves.ExprError as exc:
        raise ValueError(str(exc))
    merged['model_params'] = {k: v for k, v in fn['params'].items() if k != 'tol'}

    for key in ('min_stake', 'max_stake', 'min_withdraw'):
        merged[key] = float(merged[key])
        if merged[key] < 0:
            raise ValueError(f'{key} must be >= 0')
    if merged['max_stake'] and merged['max_stake'] < merged['min_stake']:
        raise ValueError('max_stake must be >= min_stake')

    merged['fee_bps'] = int(merged['fee_bps'])
    if not 0 <= merged['fee_bps'] <= MAX_FEE_BPS:
        raise ValueError(f'fee_bps must be 0..{MAX_FEE_BPS} ({MAX_FEE_BPS/100}%)')

    merged['spot_grace'] = int(merged['spot_grace'])
    if merged['spot_grace'] < 0:
        raise ValueError('spot_grace must be >= 0')

    merged['free_per_round'] = int(merged['free_per_round'])
    if not 0 <= merged['free_per_round'] <= MAX_FREE_PER_ROUND:
        raise ValueError(f'free_per_round must be 0..{MAX_FREE_PER_ROUND} '
                         '(0 switches free play off)')

    merged['free_notional'] = float(merged['free_notional'])
    if merged['free_notional'] <= 0:
        raise ValueError('free_notional must be > 0 — it is the paper stake a '
                         "free call's would-have-won is priced at")

    merged['min_liquidity_usd'] = float(merged['min_liquidity_usd'])
    if merged['min_liquidity_usd'] < 0:
        raise ValueError('min_liquidity_usd must be >= 0 (0 = no floor)')

    merged['auto_pay'] = bool(merged['auto_pay'])
    return merged


# ── The pool ─────────────────────────────────────────────────────────

class Pool:
    """Everything with a dollar sign on it. Owns its own files under the
    module store, and reaches back into `mod.py` only for prices and markets —
    passed in as callables so the whole engine is testable without a network.
    """

    def __init__(self, store_dir, price_at: Callable = None,
                 price_now: Callable = None, markets: Callable = None,
                 on_fee: Callable = None, library: 'curves.Library' = None,
                 liquidity_now: Callable = None):
        self.dir = Path(store_dir)
        # Score functions live beside the ledger: the defaults plus whatever
        # has been saved or imported here. Shared with the PREFI predict layer.
        self.library = library or curves.Library(self.dir / 'functions.json')
        self.dir.mkdir(parents=True, exist_ok=True)

        self.state_path = self.dir / 'pool.json'
        self.ledger_path = self.dir / 'pool_ledger.json'
        self.entries_path = self.dir / 'pool_entries.json'
        self.rounds_path = self.dir / 'pool_rounds.json'
        self.withdrawals_path = self.dir / 'pool_withdrawals.json'
        self.key_path = self.dir / 'hyperevm_key.json'
        self.lock_path = self.dir / 'pool.lock'

        self._price_at = price_at
        self._price_now = price_now
        self._markets = markets or (lambda: [])
        self._on_fee = on_fee                      # fee → treasury, set by mod
        # Dollars in a DEX token's pool right now — the owner's liquidity
        # floor is checked against it before a stake goes in.
        self._liquidity_now = liquidity_now
        self._chain = None

    # ── files ────────────────────────────────────────────────────────

    @contextmanager
    def _lock(self):
        """Serialise mutations. Two API workers crediting the same deposit at
        the same moment would otherwise both read a stale ledger."""
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass                               # best effort on odd platforms
            yield
        finally:
            os.close(fd)

    def _read(self, path, default):
        if not path.exists():
            return default
        try:
            with open(path) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return default

    def _write(self, path, data):
        """Atomic — a torn write on the ledger is money that never existed."""
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w') as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def state(self) -> Dict:
        st = self._read(self.state_path, {})
        changed = False
        if 'config' not in st:
            st['config'] = dict(DEFAULT_CONFIG)
            changed = True
        else:
            # A pool that has been running since before a setting existed still
            # has to be able to read its own config. Stored values always win;
            # only the keys that were never written get a default.
            missing = {k: v for k, v in DEFAULT_CONFIG.items()
                       if k not in st['config']}
            if missing:
                st['config'] = {**DEFAULT_CONFIG, **st['config']}
                changed = True
        if 'chain_id' not in st:
            st['chain_id'] = int(os.environ.get('PREFI_HYPEREVM_CHAIN', 999))
            changed = True
        if 'tokens' not in st:
            st['tokens'] = {
                sym: {**tok, 'symbol': sym, 'verified': False}
                for sym, tok in hyperevm.DEFAULT_TOKENS.get(st['chain_id'], {}).items()
            }
            changed = True
        if 'schedule' not in st:
            now = time.time()
            st['schedule'] = {'anchor': now, 'anchor_index': 0,
                              'interval': st['config']['interval']}
            changed = True
        st.setdefault('owner', None)
        st.setdefault('vault', None)
        st.setdefault('sync', {})
        st.setdefault('nonces', {})
        st.setdefault('seq', {'ledger': 0, 'entry': 0, 'withdrawal': 0})
        if changed:
            self._write(self.state_path, st)
        return st

    def _save_state(self, st):
        self._write(self.state_path, st)

    def _next(self, st, name) -> int:
        st['seq'][name] = st['seq'].get(name, 0) + 1
        return st['seq'][name]

    # ── chain ────────────────────────────────────────────────────────

    def chain(self) -> hyperevm.HyperEVM:
        st = self.state()
        if self._chain is None or self._chain.chain_id != st['chain_id']:
            self._chain = hyperevm.HyperEVM(st['chain_id'])
        return self._chain

    def vault_key(self) -> Optional[str]:
        """The hot key, if the operator installed one. Env beats the file so a
        deployment can keep the key out of the filesystem entirely."""
        env = os.environ.get('PREFI_VAULT_KEY')
        if env:
            return env
        data = self._read(self.key_path, {})
        return data.get('private_key')

    def has_hot_key(self) -> bool:
        return bool(self.vault_key())

    # ── owner ────────────────────────────────────────────────────────

    def owner_status(self) -> Dict:
        st = self.state()
        return {
            'owner': st.get('owner'),
            'claimed': bool(st.get('owner')),
            'secret_set': bool(st.get('owner_secret')),
            'env_owner': bool(os.environ.get('PREFI_OWNER')),
        }

    def claim_owner(self, address: str, secret: str = None) -> Dict:
        """First claim wins; after that it takes the secret to move it.

        `PREFI_OWNER` in the environment overrides everything — that is the
        deployment operator's escape hatch if a claim ever goes wrong.
        """
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        with self._lock():
            st = self.state()
            env_owner = hyperevm.normalize(os.environ.get('PREFI_OWNER', ''))
            if st.get('owner') and hyperevm.normalize(address) != st['owner']:
                if not env_owner or env_owner != hyperevm.normalize(address):
                    if not st.get('owner_secret') or secret != st['owner_secret']:
                        return {'error': 'pool already has an owner — '
                                         'the owner secret is required to transfer it'}
            st['owner'] = hyperevm.normalize(address)
            if not st.get('owner_secret'):
                st['owner_secret'] = os.urandom(16).hex()
            self._save_state(st)
            return {'owner': st['owner'], 'secret': st['owner_secret'],
                    'note': 'keep this secret — it is how the CLI proves ownership'}

    def _require_owner(self, address: str = None, secret: str = None,
                       action: str = None, fields: List[Tuple] = None,
                       signature: str = None) -> Optional[Dict]:
        """None when the caller is the owner, an error dict otherwise.

        Two ways in: the secret (CLI, servers) or a wallet signature from the
        owner address (the browser). Before anyone has claimed the pool it is
        unowned and open, so a fresh install is configurable without ceremony.
        """
        st = self.state()
        env_owner = hyperevm.normalize(os.environ.get('PREFI_OWNER', ''))
        owner = env_owner or st.get('owner')
        if not owner:
            return None                            # unclaimed → anyone may set up
        if secret and st.get('owner_secret') and secret == st['owner_secret']:
            return None
        if hyperevm.normalize(address) == owner:
            nonce = self.nonce(owner)
            check = sigauth.verify(action or 'owner', owner, fields or [],
                                   nonce, signature)
            if check['ok']:
                self._bump_nonce(owner)
                return None
            return {'error': f"owner action rejected — {check['error']}",
                    'sign_message': check['message'], 'nonce': nonce}
        return {'error': 'owner only'}

    # ── nonces ───────────────────────────────────────────────────────

    def nonce(self, address: str) -> int:
        return int(self.state()['nonces'].get(hyperevm.normalize(address), 0))

    def _bump_nonce(self, address: str):
        with self._lock():
            st = self.state()
            addr = hyperevm.normalize(address)
            st['nonces'][addr] = int(st['nonces'].get(addr, 0)) + 1
            self._save_state(st)

    def sign_request(self, action: str, address: str, **fields) -> Dict:
        """The exact message a wallet must sign for this action, and the nonce
        it is bound to. The UI calls this, shows it, signs it, sends it back."""
        ordered = [(k, str(v)) for k, v in sorted(fields.items())]
        nonce = self.nonce(address)
        return {
            'action': action,
            'address': hyperevm.normalize(address),
            'nonce': nonce,
            'message': sigauth.action_message(action, address, ordered, nonce),
            'required': not sigauth.signatures_disabled(),
        }

    # ── tokens ───────────────────────────────────────────────────────

    def tokens(self, verify: bool = False) -> Dict:
        """Accepted stablecoins. `verify=True` reads symbol/decimals back off
        the chain — an address nobody has checked is an address nobody should
        be sending money to."""
        st = self.state()
        out = dict(st['tokens'])
        if not verify:
            return out
        chain = self.chain()
        for sym, tok in out.items():
            try:
                meta = chain.erc20_meta(tok['address'])
                tok['onchain_symbol'] = meta['symbol']
                tok['onchain_decimals'] = meta['decimals']
                tok['verified'] = (meta['decimals'] == tok['decimals']
                                   and meta['symbol'] is not None)
            except Exception as exc:
                tok['verified'] = False
                tok['error'] = str(exc)
        with self._lock():
            st = self.state()
            st['tokens'] = out
            self._save_state(st)
        return out

    def add_token(self, symbol: str, address: str, address_arg: str = None,
                  secret: str = None, owner: str = None,
                  signature: str = None) -> Dict:
        """Register a stablecoin, decimals read from the contract itself."""
        deny = self._require_owner(owner, secret, 'add_token',
                                   [('symbol', symbol), ('token', address)], signature)
        if deny:
            return deny
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x token address is required'}
        try:
            meta = self.chain().erc20_meta(address)
        except Exception as exc:
            return {'error': f'could not read the token contract — {exc}'}
        if meta['decimals'] is None:
            return {'error': f'{address} does not answer decimals() — not an ERC-20'}

        sym = (symbol or meta['symbol'] or '').upper()
        if not sym:
            return {'error': 'symbol required — the contract does not expose one'}
        with self._lock():
            st = self.state()
            st['tokens'][sym] = {
                'symbol': sym, 'address': hyperevm.normalize(address),
                'decimals': int(meta['decimals']), 'name': meta['name'],
                'onchain_symbol': meta['symbol'], 'verified': True,
                'added_at': time.time(),
            }
            self._save_state(st)
            return {'added': st['tokens'][sym]}

    def _token_by_address(self, address: str) -> Optional[Dict]:
        addr = hyperevm.normalize(address)
        for sym, tok in self.state()['tokens'].items():
            if hyperevm.normalize(tok['address']) == addr:
                return {**tok, 'symbol': sym}
        return None

    # ── vault ────────────────────────────────────────────────────────

    def vault(self, balances: bool = True) -> Dict:
        """Where deposits go, what it holds, and whether it can pay out."""
        st = self.state()
        chain = self.chain()
        addr = st.get('vault')
        out = {
            'address': addr,
            'chain_id': st['chain_id'],
            'chain': chain.chain['name'],
            'rpc': chain.rpc_url,
            'explorer': chain.chain['explorer'],
            'hot_key': self.has_hot_key(),
            'auto_pay': bool(st['config'].get('auto_pay')) and self.has_hot_key(),
            'tokens': st['tokens'],
        }
        if not addr:
            out['note'] = ('no vault yet — run pool_create_vault (generates a key '
                           'here) or pool_set_vault with an address you control')
            return out
        out['explorer_url'] = chain.address_url(addr)
        if balances:
            held, gas = {}, None
            for sym, tok in st['tokens'].items():
                try:
                    units = chain.erc20_balance(tok['address'], addr)
                    held[sym] = hyperevm.from_units(units or 0, tok['decimals'])
                except Exception:
                    held[sym] = None
            try:
                gas = chain.native_balance(addr) / 1e18
            except Exception:
                pass
            owed = self.liabilities()
            held_total = sum(v for v in held.values() if v) if held else 0.0
            out['held'] = held
            out['gas'] = gas
            out['held_total'] = round(held_total, 6)
            out['owed'] = owed
            out['credited'] = owed['credited']
            # Solvency is the only number that matters to a depositor: does the
            # wallet hold at least what the ledger says it owes?
            out['solvent'] = held_total + 1e-6 >= owed['total']
            out['coverage'] = round(held_total / owed['total'], 4) if owed['total'] else None
        return out

    def create_vault(self, secret: str = None, owner: str = None,
                     signature: str = None) -> Dict:
        """Generate a hot wallet for the pool. Custodial by construction — the
        note says so, because a user handing over USDC deserves to know."""
        deny = self._require_owner(owner, secret, 'create_vault', [], signature)
        if deny:
            return deny
        st = self.state()
        if st.get('vault') and self.has_hot_key():
            return {'error': f"vault already exists ({st['vault']}) — "
                             'refusing to orphan the key that holds the deposits'}
        try:
            wallet = hyperevm.new_wallet()
        except ImportError:
            return {'error': 'eth_account is not installed — cannot create a key'}

        with self._lock():
            st = self.state()
            self._write(self.key_path, {'address': wallet['address'],
                                        'private_key': wallet['private_key'],
                                        'chain_id': st['chain_id'],
                                        'created_at': time.time()})
            os.chmod(self.key_path, 0o600)
            st['vault'] = hyperevm.normalize(wallet['address'])
            st['sync'] = {}
            self._save_state(st)
        return {'vault': st['vault'], 'key_file': str(self.key_path),
                'custodial': True,
                'note': 'the pool now holds user funds with this key — back up '
                        f'{self.key_path} and fund it with HYPE for gas'}

    def set_vault(self, address: str, secret: str = None, owner: str = None,
                  signature: str = None) -> Dict:
        """Point the pool at an address you already control (a Safe, a ledger).
        Deposits still credit; withdrawals queue for you to pay by hand."""
        deny = self._require_owner(owner, secret, 'set_vault',
                                   [('vault', address)], signature)
        if deny:
            return deny
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        with self._lock():
            st = self.state()
            st['vault'] = hyperevm.normalize(address)
            st['sync'] = {}
            self._save_state(st)
        return {'vault': st['vault'],
                'hot_key': self.has_hot_key(),
                'note': 'watch-only vault — withdrawals will queue as pending'
                        if not self.has_hot_key() else 'vault updated'}

    # ── ledger ───────────────────────────────────────────────────────

    def _ledger(self) -> List[Dict]:
        return self._read(self.ledger_path, [])

    def _post(self, st, rows: List[Dict], entries: List[Dict]) -> List[Dict]:
        """Append to the ledger. Every balance in the pool is a sum of these —
        there is no stored balance to drift out of sync with the history."""
        for entry in entries:
            entry.setdefault('ts', time.time())
            entry['id'] = self._next(st, 'ledger')
            rows.append(entry)
        return rows

    def balance(self, address: str) -> Dict:
        """What one address has, and where it is."""
        addr = hyperevm.normalize(address)
        available_units = 0
        deposited = withdrawn = staked = won = refunded = 0.0
        for row in self._ledger():
            if row.get('address') != addr:
                continue
            available_units += usd_to_units(row['amount'])
            kind, amt = row['kind'], float(row['amount'])
            if kind == 'deposit':
                deposited += amt
            elif kind == 'withdraw':
                withdrawn += -amt
            elif kind == 'withdraw_reversed':
                withdrawn -= amt
            elif kind == 'stake':
                staked += -amt
            elif kind == 'payout':
                won += amt
            elif kind == 'refund':
                refunded += amt

        at_stake = sum(float(e['amount']) for e in self._read(self.entries_path, [])
                       if e['address'] == addr and e['status'] == 'open')
        pending = sum(float(w['amount']) for w in self._read(self.withdrawals_path, [])
                      if w['address'] == addr and w['status'] == 'pending')

        return {
            'address': addr,
            'available': units_to_usd(available_units),
            'at_stake': round(at_stake, 6),
            'pending_withdrawal': round(pending, 6),
            'deposited': round(deposited, 6),
            'withdrawn': round(withdrawn, 6),
            'staked_lifetime': round(staked, 6),
            'won': round(won, 6),
            'refunded': round(refunded, 6),
            'net': round(won + refunded - staked, 6),
            'nonce': self.nonce(addr),
        }

    def total_credited(self) -> float:
        """Spendable user balances — the sum of every ledger row with an owner."""
        units = sum(usd_to_units(r['amount']) for r in self._ledger()
                    if r.get('address'))
        return units_to_usd(units)

    def liabilities(self) -> Dict:
        """Every dollar the vault has to be holding.

        Three buckets, and missing any of them makes the pool look richer than
        it is: spendable balances, money sitting in open pots (debited from
        balances but still owed to whoever wins), and withdrawals that have been
        debited but not yet broadcast.
        """
        credited = self.total_credited()
        at_stake = round(sum(float(e['amount'])
                             for e in self._read(self.entries_path, [])
                             if e['status'] == 'open'), 6)
        pending = round(sum(float(w['amount'])
                            for w in self._read(self.withdrawals_path, [])
                            if w['status'] == 'pending'), 6)
        return {'credited': credited, 'at_stake': at_stake,
                'pending_withdrawals': pending,
                'total': round(credited + at_stake + pending, 6)}

    def ledger(self, address: str = None, limit: int = 100) -> List[Dict]:
        rows = self._ledger()
        if address:
            addr = hyperevm.normalize(address)
            rows = [r for r in rows if r.get('address') == addr]
        return list(reversed(rows))[:max(1, int(limit))]

    # ── deposits ─────────────────────────────────────────────────────

    def _credit_transfers(self, transfers: List[Dict], source: str) -> Dict:
        """Turn on-chain Transfer logs into credits, exactly once each.

        Idempotency is the whole job: the key is (tx, log_index), so the same
        deposit submitted by hash, then found again by the log scan, credits
        once.
        """
        with self._lock():
            st = self.state()
            rows = self._ledger()
            seen = {r.get('ref') for r in rows if r.get('ref')}
            credited, skipped = [], []

            for tr in transfers:
                ref = f"{tr['tx']}:{tr['log_index']}"
                if ref in seen:
                    skipped.append({'ref': ref, 'reason': 'already credited'})
                    continue
                token = self._token_by_address(tr['token'])
                if not token:
                    skipped.append({'ref': ref, 'reason': f"unsupported token {tr['token']}"})
                    continue
                amount = hyperevm.from_units(tr['units'], token['decimals'])
                if amount <= 0:
                    skipped.append({'ref': ref, 'reason': 'zero amount'})
                    continue
                self._post(st, rows, [{
                    'kind': 'deposit', 'address': hyperevm.normalize(tr['from']),
                    'amount': round(amount, 6), 'token': token['symbol'],
                    'tx': tr['tx'], 'block': tr.get('block'), 'ref': ref,
                    'via': source,
                }])
                seen.add(ref)
                credited.append({'address': hyperevm.normalize(tr['from']),
                                 'amount': round(amount, 6),
                                 'token': token['symbol'], 'tx': tr['tx']})

            if credited:
                self._write(self.ledger_path, rows)
                self._save_state(st)
            return {'credited': credited, 'skipped': skipped,
                    'total': round(sum(c['amount'] for c in credited), 6)}

    def deposit(self, tx_hash: str) -> Dict:
        """Credit a deposit from its transaction hash — one RPC call.

        This is the path the UI uses the moment a wallet returns a hash, so a
        user sees their balance before the log scanner would ever reach that
        block.
        """
        st = self.state()
        if not st.get('vault'):
            return {'error': 'no vault configured — nothing to deposit into'}
        if not (isinstance(tx_hash, str) and tx_hash.startswith('0x')
                and len(tx_hash) == 66):
            return {'error': 'a 0x… 32-byte transaction hash is required'}

        try:
            found = self.chain().transfers_in_tx(tx_hash, st['vault'])
        except Exception as exc:
            return {'error': f'RPC error — {exc}'}
        if not found['confirmed']:
            return {'error': f"not credited — {found['reason']}",
                    'retry': True, 'tx': tx_hash}
        if not found['transfers']:
            return {'error': 'that transaction moved no supported token into the '
                             f"vault ({st['vault']})", 'tx': tx_hash}

        result = self._credit_transfers(found['transfers'], 'tx')
        result['tx'] = tx_hash
        result['block'] = found.get('block')
        result['explorer'] = self.chain().tx_url(tx_hash)
        if not result['credited']:
            result['error'] = ('nothing new to credit — '
                               + '; '.join(s['reason'] for s in result['skipped']))
        return result

    def sync(self, max_chunks: int = 20) -> Dict:
        """Sweep Transfer logs into the vault since the last cursor.

        Catches deposits nobody told us about. Bounded on purpose — the RPC caps
        `eth_getLogs` at 1000 blocks and rate-limits, so this makes progress in
        slices and reports how far behind it still is instead of hanging.
        """
        st = self.state()
        if not st.get('vault'):
            return {'error': 'no vault configured'}
        chain = self.chain()
        try:
            head = max(0, chain.block_number() - hyperevm.SCAN_LAG)
        except Exception as exc:
            return {'error': f'RPC error — {exc}'}

        results, all_transfers = {}, []
        budget = max(1, int(max_chunks))
        for sym, token in st['tokens'].items():
            if budget <= 0:
                results[sym] = {'skipped': 'chunk budget spent'}
                continue
            cursor = st['sync'].get(sym, {}).get('cursor')
            if cursor is None:
                # First sweep: start at the head. Everything before the vault
                # existed cannot be a deposit into it, and the RPC keeps no
                # archive to look for one anyway.
                cursor = max(0, head - hyperevm.MAX_LOG_RANGE)
            try:
                scan = chain.scan_transfers(token['address'], st['vault'],
                                            cursor, head, max_chunks=budget)
            except Exception as exc:
                results[sym] = {'error': str(exc), 'cursor': cursor}
                continue
            budget -= scan['chunks']
            all_transfers.extend(scan['transfers'])
            results[sym] = {'found': len(scan['transfers']), 'cursor': scan['cursor'],
                            'head': scan['head'], 'done': scan['done'],
                            'blocks_behind': scan['blocks_behind']}
            with self._lock():
                st2 = self.state()
                st2['sync'][sym] = {'cursor': scan['cursor'], 'head': scan['head'],
                                    'at': time.time()}
                self._save_state(st2)
                st = st2

        credited = self._credit_transfers(all_transfers, 'scan') if all_transfers \
            else {'credited': [], 'skipped': [], 'total': 0.0}
        return {'head': head, 'tokens': results, **credited}

    # ── rounds ───────────────────────────────────────────────────────

    def current_index(self, now: float = None) -> int:
        st = self.state()
        sched = st['schedule']
        now = now if now is not None else time.time()
        elapsed = max(0.0, now - sched['anchor'])
        return int(sched['anchor_index'] + elapsed // sched['interval'])

    def window(self, index: int = None, now: float = None) -> Dict:
        """When a round opens, stops taking entries, and closes."""
        st = self.state()
        sched, cfg = st['schedule'], st['config']
        index = self.current_index(now) if index is None else int(index)
        opens = sched['anchor'] + (index - sched['anchor_index']) * sched['interval']
        closes = opens + sched['interval']
        return {
            'index': index,
            'opens': opens,
            'closes': closes,
            'entry_deadline': closes - cfg['entry_cutoff'],
            'interval': sched['interval'],
        }

    def _rounds(self) -> List[Dict]:
        return self._read(self.rounds_path, [])

    def active_fn(self, cfg: Dict = None) -> Dict:
        """The score function the *next* round will open with — the config's
        model resolved through the library with its tolerance and overrides."""
        cfg = cfg or self.state()['config']
        return fn_for(cfg['model'], cfg['tolerance'], cfg.get('model_params'),
                      self.library)

    def active_fn_or_default(self, cfg: Dict = None) -> Dict:
        """`active_fn` for read paths: a config naming a function that has
        since vanished from the library must not take a page down, so it
        falls back to the pool default and says so in `error`."""
        cfg = cfg or self.state()['config']
        try:
            return self.active_fn(cfg)
        except (ValueError, curves.ExprError) as exc:
            return {**fn_for(DEFAULT_CONFIG['model'], cfg['tolerance']),
                    'error': f"{cfg['model']}: {exc}"}

    @staticmethod
    def _fn_of(record: Dict) -> Dict:
        """The function a round settles under. Rounds opened before functions
        were snapshotted only carry a built-in name and a tolerance, which is
        enough to rebuild exactly the program they were sold with."""
        fn = record.get('fn')
        if fn:
            return fn
        return fn_for(record['model'], record['tolerance'])

    def _round_record(self, st, rounds, index: int) -> Dict:
        """Fetch or materialise a round. Materialising snapshots the scoring
        params, which is what stops a mid-round retune from re-pricing bets."""
        for r in rounds:
            if r['index'] == index:
                return r
        cfg = st['config']
        win = self.window(index)
        record = {
            **win,
            'status': 'open',
            'model': cfg['model'],
            'tolerance': cfg['tolerance'],
            'fn': self.active_fn(cfg),
            'fee_bps': cfg['fee_bps'],
            'created_at': time.time(),
            'assets': {},
            'settled_at': None,
        }
        rounds.append(record)
        return record

    # ── staking ──────────────────────────────────────────────────────

    # The oracles a pot can settle against: both answer "the price AT a
    # moment" from a local feed, which is what honest lazy settlement needs.
    # CoinGecko markets stay out — its free tier can't be relied on at a close.
    # DEX tokens (Solana, Base) are in: priced per pool, with hourly history
    # from GeckoTerminal and our own snapshots, behind the owner's liquidity
    # floor.
    POOL_SOURCES = ('hyperliquid', 'bittensor', 'dex')

    def _market_of(self, asset: str) -> Optional[Dict]:
        want = (asset or '').strip().upper()
        for m in self._markets():
            if m['symbol'].upper() == want:
                return m
        return None

    def _source_of(self, asset: str) -> str:
        """Which feed prices this asset. Entries predating the Bittensor
        source have no market lookup to fail — they were Hyperliquid."""
        m = self._market_of(asset)
        return (m or {}).get('source') or 'hyperliquid'

    def _quote_of(self, asset: str) -> str:
        """What the price is denominated in — dollars, or TAO for a subnet."""
        m = self._market_of(asset) or {}
        return m.get('quote') or ('TAO' if m.get('source') == 'bittensor' else 'USD')

    def _pool_market(self, asset: str, cfg: Dict = None) -> Dict:
        """Pool markets must be priced by a feed that can answer historically
        — Hyperliquid marks, Bittensor subnet prices, or a DEX pool with
        hourly candles. A market priced anywhere else could not be settled
        against honestly.

        A DEX token also has to clear the owner's liquidity floor *now*, not
        just when it was listed: a pool that has drained since is a price one
        trade can move, and no new money should go into a pot on it.
        """
        want = (asset or '').strip().upper()
        m = self._market_of(want)
        if m is None:
            return {'error': f'no market for {want} — list it with add_hl_market, '
                             'add_bt_market or add_dex_market'}
        if m.get('source') not in self.POOL_SOURCES:
            return {'error': f"{m['symbol']} is priced by "
                             f"{m.get('source', 'coingecko')} — the pool "
                             'settles on Hyperliquid marks, Bittensor subnet '
                             'prices and DEX pools on Solana/Base only. add it '
                             f"with add_hl_market({want}), add_bt_market({want}) "
                             f"or add_dex_market(chain, {want})"}
        if not m.get('active'):
            return {'error': f'{want} market is not active'}
        if m.get('source') == 'dex':
            cfg = cfg or self.state()['config']
            floor = float(cfg.get('min_liquidity_usd') or 0)
            if floor > 0:
                liq = self._liquidity_now(m['symbol']) if self._liquidity_now else None
                if liq is None:
                    return {'error': f"{m['symbol']} has no liquidity reading right now — "
                                     'the DEX feed is unreachable, so the owner\'s '
                                     f'${floor:,.0f} floor cannot be checked'}
                if liq < floor:
                    return {'error': f"{m['symbol']}'s pool holds ${liq:,.0f} — under the "
                                     f'${floor:,.0f} liquidity floor the owner set, so it '
                                     'cannot take a stake until it refills'}
        return {'market': m}

    def stake(self, address: str, asset: str, predicted_price: float,
              amount: float, signature: str = None, nonce: int = None) -> Dict:
        """Put dollars behind a price call for this round.

        The stake leaves the available balance immediately — it is in the pot,
        not in your account — and comes back only as a settlement payout or a
        refund.
        """
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        addr = hyperevm.normalize(address)

        try:
            predicted_price = float(predicted_price)
            amount = round(float(amount), 6)
        except (TypeError, ValueError):
            return {'error': 'predicted_price and amount must be numbers'}
        if predicted_price <= 0:
            return {'error': 'predicted price must be positive'}

        st = self.state()
        cfg = st['config']
        if amount < cfg['min_stake']:
            return {'error': f"minimum stake is ${cfg['min_stake']:,.2f}"}
        if cfg['max_stake'] and amount > cfg['max_stake']:
            return {'error': f"maximum stake is ${cfg['max_stake']:,.2f}"}

        found = self._pool_market(asset, cfg)
        if 'error' in found:
            return found
        market = found['market']
        symbol = market['symbol'].upper()

        win = self.window()
        now = time.time()
        if now >= win['entry_deadline']:
            return {'error': 'entries for this round are closed — '
                             f"{int(win['closes'] - now)}s until it settles, "
                             'your stake would land in the next round',
                    'round': win['index'], 'next_opens': win['closes']}

        check = sigauth.verify('stake', addr, [
            ('amount', f'{amount:.6f}'), ('asset', symbol),
            ('price', f'{predicted_price:.8f}'), ('round', str(win['index'])),
        ], self.nonce(addr) if nonce is None else int(nonce), signature)
        if not check['ok']:
            return {'error': check['error'], 'sign_message': check['message'],
                    'nonce': self.nonce(addr)}

        bal = self.balance(addr)
        if bal['available'] < amount:
            return {'error': f"insufficient balance — ${bal['available']:,.2f} "
                             f'available, ${amount:,.2f} needed. deposit USDC or '
                             'USDT0 to the vault first'}

        mark = self._price_now(symbol, self._source_of(symbol)) if self._price_now else None

        with self._lock():
            st = self.state()
            rounds = self._rounds()
            self._round_record(st, rounds, win['index'])
            entries = self._read(self.entries_path, [])
            entry = {
                'id': self._next(st, 'entry'),
                'round': win['index'],
                'address': addr,
                'asset': symbol,
                'predicted_price': predicted_price,
                'amount': amount,
                'mark_at_entry': mark,
                'created_at': now,
                'status': 'open',
                'rel_error': None, 'accuracy': None, 'score': None,
                'payout': None, 'net': None,
            }
            entries.append(entry)
            rows = self._post(st, self._ledger(), [{
                'kind': 'stake', 'address': addr, 'amount': -amount,
                'round': win['index'], 'entry': entry['id'], 'asset': symbol,
            }])
            self._write(self.entries_path, entries)
            self._write(self.rounds_path, rounds)
            self._write(self.ledger_path, rows)
            st['nonces'][addr] = int(st['nonces'].get(addr, 0)) + 1
            self._save_state(st)

        return {
            'entry_id': entry['id'],
            'round': win['index'],
            'asset': symbol,
            'quote': self._quote_of(symbol),
            'staked': amount,
            'predicted_price': predicted_price,
            'mark_at_entry': mark,
            'implied_move_pct': round((predicted_price - mark) / mark * 100, 2)
                                if mark else None,
            'entries_close': win['entry_deadline'],
            'settles': win['closes'],
            'model': st['config']['model'],
            'balance': self.balance(addr)['available'],
        }

    # ── free play ────────────────────────────────────────────────────

    def _free_entries(self, index: int, address: str = None) -> List[Dict]:
        rows = [e for e in self._read(self.entries_path, [])
                if e.get('free') and e['round'] == index]
        if address:
            addr = hyperevm.normalize(address)
            rows = [e for e in rows if e['address'] == addr]
        return rows

    def free_quota(self, address: str, index: int = None) -> Dict:
        """Free calls left this round, and which assets are already spoken for."""
        st = self.state()
        cfg = st['config']
        win = self.window(index)
        limit = int(cfg['free_per_round'])
        if not hyperevm.is_address(address or ''):
            used, assets = 0, []
        else:
            mine = self._free_entries(win['index'], address)
            used, assets = len(mine), sorted({e['asset'] for e in mine})
        return {
            'address': hyperevm.normalize(address) if address else None,
            'round': win['index'],
            'enabled': limit > 0,
            'limit': limit,
            'used': used,
            'remaining': max(0, limit - used),
            'assets_used': assets,
            'notional': cfg['free_notional'],
            'entries_close': win['entry_deadline'],
            'resets_at': win['closes'],
            'entries_open': time.time() < win['entry_deadline'],
        }

    def free_stake(self, address: str, asset: str, predicted_price: float,
                   signature: str = None, nonce: int = None) -> Dict:
        """Call a price with no money down.

        Scored by exactly the rule the pot is scored by, listed next to the paid
        calls, and paid nothing — the entry never enters the pot, so no staker is
        diluted by someone who risked nothing. What it gets back is `would_win`:
        what the same call would have taken at `free_notional` dollars.

        One call per asset per round, `free_per_round` in total. Both caps exist
        for the same reason: a free player who could place ten calls on BTC at
        ten different prices would have a meaningless accuracy and a would-win
        number that advertises a bet nobody could have placed.
        """
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        addr = hyperevm.normalize(address)

        try:
            predicted_price = float(predicted_price)
        except (TypeError, ValueError):
            return {'error': 'predicted_price must be a number'}
        if predicted_price <= 0:
            return {'error': 'predicted price must be positive'}

        st = self.state()
        cfg = st['config']
        limit = int(cfg['free_per_round'])
        if limit <= 0:
            return {'error': 'free play is switched off — stake to enter a round'}

        found = self._pool_market(asset, cfg)
        if 'error' in found:
            return found
        symbol = found['market']['symbol'].upper()

        win = self.window()
        now = time.time()
        if now >= win['entry_deadline']:
            return {'error': 'entries for this round are closed — '
                             f"{int(win['closes'] - now)}s until it settles, "
                             'your call would land in the next round',
                    'round': win['index'], 'next_opens': win['closes']}

        mine = self._free_entries(win['index'], addr)
        if len(mine) >= limit:
            return {'error': f'out of free calls — {limit} per round. '
                             'the next round resets them',
                    'round': win['index'], 'resets_at': win['closes']}
        if any(e['asset'] == symbol for e in mine):
            return {'error': f'you already have a free call on {symbol} this '
                             'round — one per asset, so the board means something',
                    'round': win['index']}

        # Signed like a stake. It costs nothing, but it writes to a public
        # accuracy record, and nobody else gets to write to yours.
        check = sigauth.verify('free_stake', addr, [
            ('asset', symbol), ('price', f'{predicted_price:.8f}'),
            ('round', str(win['index'])),
        ], self.nonce(addr) if nonce is None else int(nonce), signature)
        if not check['ok']:
            return {'error': check['error'], 'sign_message': check['message'],
                    'nonce': self.nonce(addr)}

        mark = self._price_now(symbol, self._source_of(symbol)) if self._price_now else None

        with self._lock():
            st = self.state()
            rounds = self._rounds()
            self._round_record(st, rounds, win['index'])
            entries = self._read(self.entries_path, [])
            entry = {
                'id': self._next(st, 'entry'),
                'round': win['index'],
                'address': addr,
                'asset': symbol,
                'predicted_price': predicted_price,
                'amount': 0.0,
                'free': True,
                # Snapshotted, like the round's scoring params: retuning the
                # notional later cannot re-price a counterfactual already made.
                'notional': float(st['config']['free_notional']),
                'mark_at_entry': mark,
                'created_at': now,
                'status': 'open',
                'rel_error': None, 'accuracy': None, 'score': None,
                'payout': None, 'net': None,
                'would_win': None, 'would_net': None,
            }
            entries.append(entry)
            # No ledger row, by design. The ledger is money; this is not.
            self._write(self.entries_path, entries)
            self._write(self.rounds_path, rounds)
            st['nonces'][addr] = int(st['nonces'].get(addr, 0)) + 1
            self._save_state(st)

        return {
            'entry_id': entry['id'],
            'round': win['index'],
            'asset': symbol,
            'quote': self._quote_of(symbol),
            'free': True,
            'staked': 0.0,
            'notional': entry['notional'],
            'predicted_price': predicted_price,
            'mark_at_entry': mark,
            'implied_move_pct': round((predicted_price - mark) / mark * 100, 2)
                                if mark else None,
            'entries_close': win['entry_deadline'],
            'settles': win['closes'],
            'model': cfg['model'],
            'free_remaining': max(0, limit - len(mine) - 1),
            'note': (f"scored like every other call — pays nothing, but reports "
                     f"what ${entry['notional']:,.0f} on it would have won"),
        }

    # ── settlement ───────────────────────────────────────────────────

    def settle(self, force: bool = False) -> Dict:
        """Settle every round whose close has passed.

        Runs on read as well as on a cron, so a pot is never left hanging
        because nobody ran a job. A round the oracle cannot price yet stays
        open and is retried; it is never settled off a made-up number.
        """
        now = time.time()
        settled, waiting = [], []

        with self._lock():
            st = self.state()
            rounds = self._rounds()
            entries = self._read(self.entries_path, [])
            rows = self._ledger()
            cfg = st['config']
            dirty = False

            for record in rounds:
                if record['status'] == 'settled' or record['closes'] > now:
                    continue

                open_entries = [e for e in entries
                                if e['round'] == record['index'] and e['status'] == 'open']
                if not open_entries:
                    record['status'] = 'settled'
                    record['settled_at'] = now
                    dirty = True
                    continue

                by_asset = {}
                for e in open_entries:
                    by_asset.setdefault(e['asset'], []).append(e)

                for asset, group in by_asset.items():
                    # Free calls are held out of the pot, not staked at zero —
                    # the money math never sees an entry that risked nothing.
                    paid = [e for e in group if not e.get('free')]
                    free = [e for e in group if e.get('free')]

                    quote = self._price_at(asset, record['closes'], self._source_of(asset)) \
                        if self._price_at else {'price': None, 'mode': 'none'}
                    price, mode = quote.get('price'), quote.get('mode')

                    # A spot price is only the truth *at the close*. Accept it
                    # inside the grace window, refuse it afterwards rather than
                    # pay a pot against the wrong moment.
                    if price and mode == 'spot' and not force \
                            and (now - record['closes']) > cfg['spot_grace']:
                        price = None
                        mode = 'stale-spot'
                    if not price:
                        waiting.append({'round': record['index'], 'asset': asset,
                                        'reason': f'no settlement price ({mode})',
                                        'entries': len(paid),
                                        'free_entries': len(free)})
                        continue

                    fn = self._fn_of(record)
                    result = settle_asset(paid, price, fn, record['fee_bps'])
                    if not paid:
                        result['mode'] = 'free-only'

                    ledger_rows = []
                    for scored in result['entries']:
                        target = next(e for e in entries if e['id'] == scored['id'])
                        target.update({
                            'status': 'settled',
                            'actual_price': price,
                            'price_mode': mode,
                            'rel_error': scored['rel_error'],
                            'accuracy': scored['accuracy'],
                            'score': scored['score'],
                            'share': scored['share'],
                            'payout': scored['payout'],
                            'net': scored['net'],
                            'settled_at': now,
                        })
                        if scored['payout'] > 0:
                            ledger_rows.append({
                                'kind': 'refund' if result['mode'] == 'refund' else 'payout',
                                'address': scored['address'],
                                'amount': scored['payout'],
                                'round': record['index'], 'entry': scored['id'],
                                'asset': asset, 'score': scored['score'],
                            })
                    for scored in settle_free(free, paid, price, fn,
                                              record['fee_bps']):
                        target = next(e for e in entries if e['id'] == scored['id'])
                        target.update({
                            'status': 'settled',
                            'actual_price': price,
                            'price_mode': mode,
                            'rel_error': scored['rel_error'],
                            'accuracy': scored['accuracy'],
                            'score': scored['score'],
                            'payout': 0.0, 'net': 0.0,
                            'would_win': scored['would_win'],
                            'would_net': scored['would_net'],
                            'settled_at': now,
                        })

                    if result['fee'] > 0:
                        ledger_rows.append({
                            'kind': 'fee', 'address': None, 'amount': result['fee'],
                            'round': record['index'], 'asset': asset,
                        })
                        if self._on_fee:
                            try:
                                self._on_fee(result['fee'])
                            except Exception:
                                pass
                    rows = self._post(st, rows, ledger_rows)

                    record['assets'][asset] = {
                        'settled': True, 'actual_price': price, 'price_mode': mode,
                        'quote': self._quote_of(asset),
                        'gross': result['gross'], 'fee': result['fee'],
                        'pot': result['pot'], 'mode': result['mode'],
                        'total_score': result['total_score'],
                        'winner': result['winner'], 'entries': len(paid),
                        'free_entries': len(free),
                    }
                    settled.append({'round': record['index'], 'asset': asset,
                                    'pot': result['pot'], 'mode': result['mode'],
                                    'winner': result['winner']})
                    dirty = True

                still_open = [e for e in entries
                              if e['round'] == record['index'] and e['status'] == 'open']
                if not still_open:
                    record['status'] = 'settled'
                    record['settled_at'] = now
                    dirty = True
                elif record['status'] == 'open':
                    record['status'] = 'closed'
                    dirty = True

            if dirty:
                self._write(self.entries_path, entries)
                self._write(self.rounds_path, rounds)
                self._write(self.ledger_path, rows)
                self._save_state(st)

        return {'settled': settled, 'waiting': waiting,
                'paid': round(sum(s['pot'] for s in settled), 6)}

    def settle_manual(self, index: int, asset: str, price: float,
                      secret: str = None, owner: str = None,
                      signature: str = None) -> Dict:
        """Settle a stuck pot against a price the owner supplies.

        The escape hatch for when Hyperliquid cannot answer for that hour and
        the grace window has passed. Recorded with `price_mode: manual` so the
        round history always says a human chose the number.
        """
        deny = self._require_owner(owner, secret, 'settle_manual',
                                   [('asset', asset), ('price', str(price)),
                                    ('round', str(index))], signature)
        if deny:
            return deny
        try:
            price = float(price)
        except (TypeError, ValueError):
            return {'error': 'price must be a number'}
        if price <= 0:
            return {'error': 'price must be positive'}

        index, asset = int(index), (asset or '').upper()
        now = time.time()
        with self._lock():
            st = self.state()
            rounds = self._rounds()
            record = next((r for r in rounds if r['index'] == index), None)
            if not record:
                return {'error': f'no round {index}'}
            if record['closes'] > now:
                return {'error': 'round has not closed yet'}
            if asset in record['assets']:
                return {'error': f'{asset} in round {index} is already settled'}

            entries = self._read(self.entries_path, [])
            group = [e for e in entries if e['round'] == index
                     and e['asset'] == asset and e['status'] == 'open']
            if not group:
                return {'error': f'no open entries for {asset} in round {index}'}
            paid = [e for e in group if not e.get('free')]
            free = [e for e in group if e.get('free')]

            fn = self._fn_of(record)
            result = settle_asset(paid, price, fn, record['fee_bps'])
            if not paid:
                result['mode'] = 'free-only'
            rows = self._ledger()
            ledger_rows = []
            for scored in result['entries']:
                target = next(e for e in entries if e['id'] == scored['id'])
                target.update({'status': 'settled', 'actual_price': price,
                               'price_mode': 'manual',
                               'rel_error': scored['rel_error'],
                               'accuracy': scored['accuracy'], 'score': scored['score'],
                               'share': scored['share'], 'payout': scored['payout'],
                               'net': scored['net'], 'settled_at': now})
                if scored['payout'] > 0:
                    ledger_rows.append({
                        'kind': 'refund' if result['mode'] == 'refund' else 'payout',
                        'address': scored['address'], 'amount': scored['payout'],
                        'round': index, 'entry': scored['id'], 'asset': asset,
                        'manual': True})
            for scored in settle_free(free, paid, price, fn, record['fee_bps']):
                target = next(e for e in entries if e['id'] == scored['id'])
                target.update({'status': 'settled', 'actual_price': price,
                               'price_mode': 'manual',
                               'rel_error': scored['rel_error'],
                               'accuracy': scored['accuracy'],
                               'score': scored['score'],
                               'payout': 0.0, 'net': 0.0,
                               'would_win': scored['would_win'],
                               'would_net': scored['would_net'],
                               'settled_at': now})
            if result['fee'] > 0:
                ledger_rows.append({'kind': 'fee', 'address': None,
                                    'amount': result['fee'], 'round': index,
                                    'asset': asset})
                if self._on_fee:
                    try:
                        self._on_fee(result['fee'])
                    except Exception:
                        pass
            rows = self._post(st, rows, ledger_rows)
            record['assets'][asset] = {
                'settled': True, 'actual_price': price, 'price_mode': 'manual',
                'quote': self._quote_of(asset),
                'gross': result['gross'], 'fee': result['fee'], 'pot': result['pot'],
                'mode': result['mode'], 'total_score': result['total_score'],
                'winner': result['winner'], 'entries': len(paid),
                'free_entries': len(free)}
            if not [e for e in entries if e['round'] == index and e['status'] == 'open']:
                record['status'] = 'settled'
                record['settled_at'] = now

            self._write(self.entries_path, entries)
            self._write(self.rounds_path, rounds)
            self._write(self.ledger_path, rows)
            self._save_state(st)

        return {'round': index, 'asset': asset, 'price': price,
                'price_mode': 'manual', 'pot': result['pot'],
                'winner': result['winner']}

    # ── views ────────────────────────────────────────────────────────

    def round(self, index: int = None, address: str = None) -> Dict:
        """One round, with live provisional scores.

        Before the close, `actual` is the current Hyperliquid mark, so the table
        shows what the split would be if the round ended right now. It is
        labelled `provisional` because it will move.
        """
        self.settle()
        st = self.state()
        index = self.current_index() if index is None else int(index)
        rounds = self._rounds()
        record = next((r for r in rounds if r['index'] == index), None)
        win = self.window(index)
        cfg = st['config']
        if not record:
            record = {**win, 'status': 'open' if index >= self.current_index() else 'empty',
                      'model': cfg['model'], 'tolerance': cfg['tolerance'],
                      'fn': self.active_fn_or_default(cfg),
                      'fee_bps': cfg['fee_bps'], 'assets': {}, 'settled_at': None}
        fn = self._fn_of(record)

        entries = [e for e in self._read(self.entries_path, []) if e['round'] == index]
        now = time.time()
        assets = {}
        for e in entries:
            assets.setdefault(e['asset'], []).append(e)

        out_assets = []
        for asset, group in sorted(assets.items()):
            paid = [e for e in group if not e.get('free')]
            free = [e for e in group if e.get('free')]
            settled = record['assets'].get(asset)
            actual = settled['actual_price'] if settled else (
                self._price_now(asset, self._source_of(asset)) if self._price_now else None)
            rows, free_rows = [], []
            if actual:
                preview = settle_asset(paid, actual, fn, record['fee_bps'])
                rows = preview['entries']
                free_rows = settle_free(free, paid, actual, fn, record['fee_bps'])
                totals = {'gross': preview['gross'], 'fee': preview['fee'],
                          'pot': preview['pot'], 'total_score': preview['total_score'],
                          'winner': preview['winner'],
                          'mode': preview['mode'] if paid else 'free-only'}
            else:
                rows = [{**e, 'accuracy': None, 'score': None, 'payout': None} for e in paid]
                free_rows = [{**e, 'accuracy': None, 'score': None,
                              'would_win': None, 'would_net': None} for e in free]
                totals = {'gross': round(sum(float(e['amount']) for e in paid), 6),
                          'fee': 0.0, 'pot': None, 'total_score': None,
                          'winner': None, 'mode': 'unpriced'}
            out_assets.append({
                'asset': asset,
                'quote': self._quote_of(asset),
                'settled': bool(settled),
                'actual_price': actual,
                'price_mode': settled['price_mode'] if settled else 'live-mark',
                'provisional': not settled,
                'stakers': len({e['address'] for e in paid}),
                'free_callers': len({e['address'] for e in free}),
                'entries': sorted(rows, key=lambda r: -(r.get('score') or 0)),
                'free': sorted(free_rows, key=lambda r: -(r.get('accuracy') or 0)),
                **totals,
            })

        mine = [e for e in entries
                if address and e['address'] == hyperevm.normalize(address)]

        return {
            'index': index,
            'status': record['status'],
            'opens': record['opens'],
            'closes': record['closes'],
            'entry_deadline': record['entry_deadline'],
            'interval': record.get('interval', win['interval']),
            'seconds_to_deadline': max(0, int(record['entry_deadline'] - now)),
            'seconds_to_close': max(0, int(record['closes'] - now)),
            'entries_open': now < record['entry_deadline'],
            'model': record['model'],
            'tolerance': record['tolerance'],
            'fn': fn,
            'fee_bps': record['fee_bps'],
            'total_staked': round(sum(float(e['amount']) for e in entries), 6),
            'stakers': len({e['address'] for e in entries if not e.get('free')}),
            'free_calls': sum(1 for e in entries if e.get('free')),
            'free_callers': len({e['address'] for e in entries if e.get('free')}),
            'free_per_round': cfg['free_per_round'],
            'free_notional': cfg['free_notional'],
            'assets': out_assets,
            'mine': mine,
            'is_current': index == self.current_index(),
        }

    def rounds(self, limit: int = 20) -> List[Dict]:
        """Round history, newest first — what each pot paid and who took it."""
        self.settle()
        entries = self._read(self.entries_path, [])
        out = []
        for record in sorted(self._rounds(), key=lambda r: -r['index'])[:max(1, int(limit))]:
            mine = [e for e in entries if e['round'] == record['index']]
            out.append({
                'index': record['index'],
                'status': record['status'],
                'opens': record['opens'],
                'closes': record['closes'],
                'model': record['model'],
                'tolerance': record['tolerance'],
                'fn': self._fn_of(record),
                'staked': round(sum(float(e['amount']) for e in mine), 6),
                'stakers': len({e['address'] for e in mine if not e.get('free')}),
                'free_calls': sum(1 for e in mine if e.get('free')),
                'assets': record['assets'],
                'paid': round(sum(a.get('pot', 0) or 0
                                  for a in record['assets'].values()), 6),
            })
        return out

    def entries(self, address: str = None, limit: int = 100) -> List[Dict]:
        self.settle()
        rows = self._read(self.entries_path, [])
        if address:
            addr = hyperevm.normalize(address)
            rows = [r for r in rows if r['address'] == addr]
        return list(reversed(rows))[:max(1, int(limit))]

    def leaderboard(self, limit: int = 50) -> List[Dict]:
        """Ranked by profit — staked versus taken home, across settled rounds."""
        self.settle()
        book = {}
        all_entries = self._read(self.entries_path, [])
        for e in all_entries:
            if e.get('free'):
                continue          # $0 rows do not belong on a board ranked by $
            row = book.setdefault(e['address'], {
                'address': e['address'], 'staked': 0.0, 'returned': 0.0,
                'settled_staked': 0.0, 'entries': 0, 'settled': 0, 'wins': 0,
                'accuracy_sum': 0.0, 'best_accuracy': None, 'free_entries': 0})
            row['staked'] += float(e['amount'])
            row['entries'] += 1
            if e['status'] == 'settled':
                row['settled'] += 1
                row['settled_staked'] += float(e['amount'])
                row['returned'] += float(e.get('payout') or 0)
                acc = e.get('accuracy')
                if acc is not None:
                    row['accuracy_sum'] += acc
                    row['best_accuracy'] = max(row['best_accuracy'] or 0, acc)
                if (e.get('payout') or 0) > float(e['amount']):
                    row['wins'] += 1

        for e in all_entries:                     # how many arrived free first
            if e.get('free') and e['address'] in book:
                book[e['address']]['free_entries'] += 1

        out = []
        for row in book.values():
            settled = row['settled'] or 1
            out.append({
                **row,
                'staked': round(row['staked'], 2),
                'returned': round(row['returned'], 2),
                # PnL only counts rounds that actually paid — an open stake is
                # not a loss just because it has not settled yet.
                'pnl': round(row['returned'] - row['settled_staked'], 2),
                'avg_accuracy': round(row['accuracy_sum'] / settled, 4),
                'win_rate': round(row['wins'] / settled * 100, 1),
            })
        out.sort(key=lambda r: -r['pnl'])
        return out[:max(1, int(limit))]

    def free_leaderboard(self, limit: int = 50) -> List[Dict]:
        """Free play, ranked by accuracy — and by the money it would have made.

        Deliberately a second board rather than rows on the first one. The paid
        board ranks by dollars won; a free caller has won no dollars, and mixing
        the two would be scoring one game on another's scale. `would_net` is the
        column that matters: it is what these calls would have been worth at the
        notional they were priced against.
        """
        self.settle()
        book = {}
        for e in self._read(self.entries_path, []):
            if not e.get('free'):
                continue
            row = book.setdefault(e['address'], {
                'address': e['address'], 'calls': 0, 'settled': 0,
                'would_win': 0.0, 'would_net': 0.0, 'notional': 0.0,
                'accuracy_sum': 0.0, 'best_accuracy': None, 'assets': set()})
            row['calls'] += 1
            row['assets'].add(e['asset'])
            if e['status'] == 'settled':
                row['settled'] += 1
                row['would_win'] += float(e.get('would_win') or 0)
                row['would_net'] += float(e.get('would_net') or 0)
                row['notional'] += float(e.get('notional') or 0)
                acc = e.get('accuracy')
                if acc is not None:
                    row['accuracy_sum'] += acc
                    row['best_accuracy'] = max(row['best_accuracy'] or 0, acc)

        # Whoever also stakes has converted — worth saying on the board that
        # exists to convert people.
        staked = {e['address'] for e in self._read(self.entries_path, [])
                  if not e.get('free')}

        out = []
        for row in book.values():
            settled = row['settled'] or 1
            assets = sorted(row.pop('assets'))
            out.append({
                **row,
                'assets': assets,
                'would_win': round(row['would_win'], 2),
                'would_net': round(row['would_net'], 2),
                'notional': round(row['notional'], 2),
                'avg_accuracy': round(row['accuracy_sum'] / settled, 4),
                'staker': row['address'] in staked,
            })
        # Accuracy first, but an unsettled caller has none — rank them last
        # rather than at 0%, which would read as a bad call instead of no call.
        out.sort(key=lambda r: (-(r['settled'] > 0), -r['avg_accuracy'], -r['settled']))
        return out[:max(1, int(limit))]

    # ── withdrawals ──────────────────────────────────────────────────

    def withdraw(self, address: str, amount: float, token: str = None,
                 signature: str = None, nonce: int = None) -> Dict:
        """Take dollars back out to the depositing wallet.

        Always signed, always to the address that owns the balance — there is no
        "withdraw to" parameter, because a stolen signature should not be able
        to redirect funds.
        """
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        addr = hyperevm.normalize(address)
        try:
            amount = round(float(amount), 6)
        except (TypeError, ValueError):
            return {'error': 'amount must be a number'}

        st = self.state()
        cfg = st['config']
        if amount < cfg['min_withdraw']:
            return {'error': f"minimum withdrawal is ${cfg['min_withdraw']:,.2f}"}

        symbol = (token or next(iter(st['tokens']), '')).upper()
        tok = st['tokens'].get(symbol)
        if not tok:
            return {'error': f"unknown token {symbol} — have {sorted(st['tokens'])}"}

        check = sigauth.verify('withdraw', addr, [
            ('amount', f'{amount:.6f}'), ('token', symbol)],
            self.nonce(addr) if nonce is None else int(nonce), signature)
        if not check['ok']:
            return {'error': check['error'], 'sign_message': check['message'],
                    'nonce': self.nonce(addr)}

        bal = self.balance(addr)
        if bal['available'] < amount:
            return {'error': f"insufficient balance — ${bal['available']:,.2f} available"}

        with self._lock():
            st = self.state()
            pending = self._read(self.withdrawals_path, [])
            record = {
                'id': self._next(st, 'withdrawal'),
                'address': addr, 'amount': amount, 'token': symbol,
                'status': 'pending', 'created_at': time.time(),
                'tx': None, 'paid_at': None, 'error': None,
            }
            pending.append(record)
            rows = self._post(st, self._ledger(), [{
                'kind': 'withdraw', 'address': addr, 'amount': -amount,
                'token': symbol, 'withdrawal': record['id'],
            }])
            self._write(self.withdrawals_path, pending)
            self._write(self.ledger_path, rows)
            st['nonces'][addr] = int(st['nonces'].get(addr, 0)) + 1
            self._save_state(st)

        out = {'withdrawal_id': record['id'], 'address': addr, 'amount': amount,
               'token': symbol, 'status': 'pending',
               'balance': self.balance(addr)['available']}
        if cfg.get('auto_pay') and self.has_hot_key():
            out['payment'] = self.pay_withdrawal(record['id'], _internal=True)
            out['status'] = out['payment'].get('status', 'pending')
        else:
            out['note'] = ('queued — the operator pays it from the vault'
                           if not self.has_hot_key() else
                           'queued — auto_pay is off, an owner must release it')
        return out

    def pay_withdrawal(self, withdrawal_id: int, secret: str = None,
                       owner: str = None, signature: str = None,
                       _internal: bool = False) -> Dict:
        """Send a queued withdrawal on-chain from the vault key."""
        if not _internal:
            deny = self._require_owner(owner, secret, 'pay_withdrawal',
                                       [('withdrawal', str(withdrawal_id))], signature)
            if deny:
                return deny

        key = self.vault_key()
        if not key:
            return {'error': 'no vault key on this host — pay it manually and '
                             'mark it with pool_mark_paid'}

        st = self.state()
        pending = self._read(self.withdrawals_path, [])
        record = next((w for w in pending if w['id'] == int(withdrawal_id)), None)
        if not record:
            return {'error': f'no withdrawal {withdrawal_id}'}
        if record['status'] != 'pending':
            return {'error': f"withdrawal {withdrawal_id} is already {record['status']}"}

        tok = st['tokens'].get(record['token'])
        if not tok:
            return {'error': f"token {record['token']} is no longer registered"}

        units = hyperevm.to_units(record['amount'], tok['decimals'])
        try:
            sent = self.chain().send_erc20(key, tok['address'], record['address'], units)
        except Exception as exc:
            sent = {'error': str(exc)}

        with self._lock():
            pending = self._read(self.withdrawals_path, [])
            record = next(w for w in pending if w['id'] == int(withdrawal_id))
            if 'error' in sent:
                # The money never left — put it back rather than leave a user
                # short because the vault was out of gas.
                st2 = self.state()
                rows = self._post(st2, self._ledger(), [{
                    'kind': 'withdraw_reversed', 'address': record['address'],
                    'amount': record['amount'], 'token': record['token'],
                    'withdrawal': record['id'], 'reason': sent['error'],
                }])
                record['status'] = 'failed'
                record['error'] = sent['error']
                self._write(self.ledger_path, rows)
                self._save_state(st2)
            else:
                record['status'] = 'sent'
                record['tx'] = sent['tx']
                record['paid_at'] = time.time()
            self._write(self.withdrawals_path, pending)

        if 'error' in sent:
            return {'withdrawal_id': record['id'], 'status': 'failed',
                    'error': sent['error'], 'refunded': record['amount']}
        return {'withdrawal_id': record['id'], 'status': 'sent', 'tx': sent['tx'],
                'explorer': self.chain().tx_url(sent['tx']),
                'amount': record['amount'], 'token': record['token']}

    def mark_paid(self, withdrawal_id: int, tx_hash: str, secret: str = None,
                  owner: str = None, signature: str = None) -> Dict:
        """Record an off-line payout — the watch-only vault path."""
        deny = self._require_owner(owner, secret, 'mark_paid',
                                   [('tx', tx_hash), ('withdrawal', str(withdrawal_id))],
                                   signature)
        if deny:
            return deny
        with self._lock():
            pending = self._read(self.withdrawals_path, [])
            record = next((w for w in pending if w['id'] == int(withdrawal_id)), None)
            if not record:
                return {'error': f'no withdrawal {withdrawal_id}'}
            if record['status'] == 'sent':
                return {'error': 'already marked paid'}
            record['status'] = 'sent'
            record['tx'] = hyperevm.normalize(tx_hash)
            record['paid_at'] = time.time()
            record['manual'] = True
            self._write(self.withdrawals_path, pending)
        return {'withdrawal_id': record['id'], 'status': 'sent', 'tx': record['tx']}

    def withdrawals(self, address: str = None, limit: int = 50) -> List[Dict]:
        rows = self._read(self.withdrawals_path, [])
        if address:
            addr = hyperevm.normalize(address)
            rows = [r for r in rows if r['address'] == addr]
        return list(reversed(rows))[:max(1, int(limit))]

    # ── config ───────────────────────────────────────────────────────

    def config(self) -> Dict:
        """The live rules, plus what they mean in practice."""
        st = self.state()
        cfg = st['config']
        win = self.window()
        fn = self.active_fn_or_default(cfg)
        return {
            **cfg,
            'interval_days': round(cfg['interval'] / 86400, 3),
            'schedule': st['schedule'],
            'round': win,
            'fn': fn,
            'scoring': (f"score = dollars × {cfg['model']}(e = |called−actual|/actual) "
                        f"where {cfg['model']}(e) = {fn['expr']} with "
                        f"{json.dumps(fn['params'], sort_keys=True)} · pot split pro-rata by score"),
            'free_play': (f"{cfg['free_per_round']} free calls per address per "
                          f"round, one per asset, scored against a "
                          f"${cfg['free_notional']:,.0f} notional"
                          if cfg['free_per_round'] else 'off'),
            'dex_floor': (f"a Solana or Base token needs ${cfg['min_liquidity_usd']:,.0f} "
                          'of pool liquidity to be listed or staked'
                          if cfg['min_liquidity_usd'] else 'no liquidity floor on DEX tokens'),
            'owner': st.get('owner'),
            'chain_id': st['chain_id'],
            'vault': st.get('vault'),
            'models': curves.describe(self.library),
        }

    def set_config(self, secret: str = None, owner: str = None,
                   signature: str = None, **patch) -> Dict:
        """Retune the pool. The interval is the one the owner actually cares
        about — "weekly" is just `interval=604800`.

        A new interval takes effect at the **next** round boundary: the round
        people have already staked into keeps the length it was sold with.
        """
        patch = {k: v for k, v in patch.items() if k in DEFAULT_CONFIG and v is not None}
        if not patch:
            return {'error': f'nothing to set — fields are {sorted(DEFAULT_CONFIG)}'}
        if 'model_params' in patch:
            # Signed as one canonical JSON field, so the wallet message is the
            # same whether the overrides arrived as a dict or a string.
            raw_params = patch['model_params']
            if isinstance(raw_params, str):
                try:
                    raw_params = json.loads(raw_params or '{}')
                except json.JSONDecodeError as exc:
                    return {'error': f'model_params must be JSON: {exc.msg}'}
            if not isinstance(raw_params, dict):
                return {'error': 'model_params must be an object of name → number'}
            patch['model_params'] = json.dumps(raw_params, sort_keys=True,
                                               separators=(',', ':'))

        deny = self._require_owner(owner, secret, 'set_config',
                                   [(k, str(patch[k])) for k in sorted(patch)],
                                   signature)
        if deny:
            return deny

        with self._lock():
            st = self.state()
            try:
                merged = validate_config(patch, st['config'], self.library)
            except ValueError as exc:
                return {'error': str(exc)}

            old_interval = st['schedule']['interval']
            if merged['interval'] != old_interval:
                current = self.window()
                st['schedule'] = {'anchor': current['closes'],
                                  'anchor_index': current['index'] + 1,
                                  'interval': merged['interval']}
            st['config'] = merged
            self._save_state(st)

        out = self.config()
        if merged['interval'] != old_interval:
            out['note'] = (f'interval {old_interval}s → {merged["interval"]}s, '
                           f'effective from round {st["schedule"]["anchor_index"]} '
                           '— the open round keeps its original length')
        return out

    # ── summary ──────────────────────────────────────────────────────

    def stats(self) -> Dict:
        """One call for the dashboard tiles and `status`."""
        rows = self._read(self.entries_path, [])
        entries = [e for e in rows if not e.get('free')]
        free = [e for e in rows if e.get('free')]
        ledger = self._ledger()
        open_entries = [e for e in entries if e['status'] == 'open']
        st = self.state()
        owed = self.liabilities()
        return {
            'enabled': bool(st.get('vault')),
            'chain_id': st['chain_id'],
            'vault': st.get('vault'),
            'interval': st['config']['interval'],
            'interval_days': round(st['config']['interval'] / 86400, 3),
            'model': st['config']['model'],
            'tolerance': st['config']['tolerance'],
            'fee_bps': st['config']['fee_bps'],
            'round': self.current_index(),
            'tvl': owed['total'],
            'balances': owed['credited'],
            'at_stake': owed['at_stake'],
            'entries_total': len(entries),
            'entries_open': len(open_entries),
            'stakers': len({e['address'] for e in entries}),
            'free_per_round': st['config']['free_per_round'],
            'free_notional': st['config']['free_notional'],
            'min_liquidity_usd': st['config']['min_liquidity_usd'],
            'free_calls': len(free),
            'free_open': sum(1 for e in free if e['status'] == 'open'),
            'free_callers': len({e['address'] for e in free}),
            'deposited': round(sum(float(r['amount']) for r in ledger
                                   if r['kind'] == 'deposit'), 2),
            'paid_out': round(sum(float(r['amount']) for r in ledger
                                  if r['kind'] in ('payout', 'refund')), 2),
            'fees': round(sum(float(r['amount']) for r in ledger
                              if r['kind'] == 'fee'), 6),
            'pending_withdrawals': sum(1 for w in self._read(self.withdrawals_path, [])
                                       if w['status'] == 'pending'),
        }
