"""
credits - prepaid usage ledger for the agent's public API key.

Guests top up with USDT/USDC or plain ETH (Base or Ethereum) sent to
the module's deposit address — from the console's own MetaMask button or
any wallet — then spend those credits to run the agent on the module's
own provider key ("the public key") instead of bringing their own.
1 credit = 1 USD.

A run is billed at what it actually cost us at the provider (metered by
billing.Meter against the live OpenRouter/Venice catalogs) plus a margin
— fee_rate, 5% by default and owner-settable. So a deposit is really the
guest pre-funding the OpenRouter/Venice credits their own runs will burn,
and the module keeps the margin. Runs the meter can't price fall back to
a flat price_per_step.

That split is what the treasury tracks: every charge is booked as
`provider_cost` (owed to OpenRouter/Venice) + `fee` (ours), the owner
records real top-ups they send to the providers, and treasury() compares
outstanding user credits against the live provider balances to say how
much needs to go over now.

Ledger state is private auth state — it lives OFF-tree under
~/.mod/agent/credits.json (same rule as the ACL), never in the repo.

Deposits are verified trustlessly: the caller submits a tx hash, we pull
the receipt from a public RPC, find ERC-20 Transfer logs of a supported
stablecoin into the deposit address (or a plain ETH value transfer to it,
priced at the Chainlink ETH/USD feed read over the same RPC), and credit
the ON-CHAIN SENDER — so nobody can claim someone else's deposit, and a
hash can only be credited once.

A deposit may be earmarked for one provider (`provider=openrouter|venice`).
Credits are one balance either way — the earmark is a note to the owner's
treasury about which key the guest expects the money to land on.
"""
import os
import json
import time
import threading
import urllib.request
from pathlib import Path
from typing import Optional

# keccak('Transfer(address,address,uint256)')
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

# supported stablecoins per network (all 6 decimals, 1:1 USD), plus the
# chain's own coin. `chain_id` and the contracts are what the console's
# MetaMask button needs to build the transfer itself; `price_feed` is the
# Chainlink ETH/USD aggregator a native deposit is priced at.
NETWORKS = {
    'base': {
        'chain_id': 8453,
        'rpc_env': 'AGENT_RPC_BASE',
        'rpc': ['https://mainnet.base.org', 'https://base-rpc.publicnode.com',
                'https://base.llamarpc.com'],
        'explorer': 'https://basescan.org/tx/',
        'tokens': {
            '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913': 'USDC',
            '0xfde4c96c8593536e31f229ea8f37b2ada2699bb2': 'USDT',
        },
        'native': 'ETH',
        'price_feed': '0x71041dddad3595f9ced3dccfbe3d1f4b0a16bb70',
    },
    'ethereum': {
        'chain_id': 1,
        'rpc_env': 'AGENT_RPC_ETHEREUM',
        'rpc': ['https://ethereum-rpc.publicnode.com', 'https://eth.llamarpc.com',
                'https://cloudflare-eth.com'],
        'explorer': 'https://etherscan.io/tx/',
        'tokens': {
            '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 'USDC',
            '0xdac17f958d2ee523a2206206994597c13d831ec7': 'USDT',
        },
        'native': 'ETH',
        'price_feed': '0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419',
    },
}

TOKEN_DECIMALS = 6
NATIVE_DECIMALS = 18
# Chainlink AggregatorV3 latestRoundData() — answer is slot 1, 8 decimals
LATEST_ROUND_DATA = '0xfeaf968c'
FEED_DECIMALS = 8
PRICE_TTL = 60                  # seconds an ETH/USD read is reused
PRICE_FALLBACK_URL = ('https://api.coingecko.com/api/v3/simple/price'
                      '?ids=ethereum&vs_currencies=usd')
DEFAULT_PRICE_PER_STEP = 0.01   # USD per step, only for runs the meter can't price
DEFAULT_FEE_RATE = 0.05         # our margin on top of provider cost (5%)
DEFAULT_COST_MULTIPLIER = 1.0   # safety factor on the metered cost estimate
MAX_HISTORY = 50                # ledger entries kept per account
MAX_TREASURY_LEDGER = 200       # top-up / withdrawal entries kept
PROVIDERS = ('openrouter', 'venice')

# Where provider credits are actually bought, and how a purchase can be seen
# from here. Neither provider sells credits over an API — OpenRouter removed
# its Coinbase endpoint (it answers 410 Gone: "use the web credits purchase
# flow instead") and Venice never had one — so the money always leaves on the
# provider's own page. What this module can do is read the purchase back off
# the key: `meter` is the number that moves when it lands.
PROVIDER_TOPUP = {
    'openrouter': {
        'url': 'https://openrouter.ai/settings/credits',
        # credits ever bought on the key. Monotonic, so a rise in it is a
        # purchase and nothing else — the amount is exact.
        'meter': 'purchased', 'exact': True,
    },
    'venice': {
        'url': 'https://venice.ai/settings/api',
        # no purchase counter, only USD left. Spending pulls it down too, so
        # the mark trails it down and only a rise above the mark is a top-up.
        'meter': 'balance', 'exact': False,
    },
}
TOPUP_EPSILON = 0.01            # ignore rounding noise in a provider's numbers


def _now() -> float:
    return time.time()


def _first_float(*values, default: float = 0.0) -> float:
    """First value that parses as a float — 0 counts, None and '' don't."""
    for v in values:
        if v is None or v == '':
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return default


class Credits:
    """Per-address prepaid credit ledger with on-chain USDT/USDC top-ups."""

    def __init__(self, state_dir, deposit_address: Optional[str] = None,
                 price_per_step: Optional[float] = None):
        self._path = Path(state_dir) / 'credits.json'
        self._lock = threading.Lock()
        self._state = self._load()
        self._price_cache: dict = {}
        cfg = self._state.setdefault('config', {})
        # config file wins, then env, then the module owner as deposit target
        self.deposit_address = (cfg.get('deposit_address')
                                or os.environ.get('AGENT_DEPOSIT_ADDRESS')
                                or deposit_address)
        if self.deposit_address:
            self.deposit_address = self.deposit_address.lower()
        self.price_per_step = float(
            cfg.get('price_per_step')
            or os.environ.get('AGENT_CREDIT_PRICE_PER_STEP')
            or (price_per_step if price_per_step is not None else DEFAULT_PRICE_PER_STEP))
        # the margin: a run costs the guest provider_cost × (1 + fee_rate).
        # 0 is a legitimate setting (run the key at cost), so `is None` — not
        # `or` — decides whether a source supplied a value.
        self.fee_rate = _first_float(cfg.get('fee_rate'),
                                     os.environ.get('AGENT_CREDIT_FEE_RATE'),
                                     DEFAULT_FEE_RATE)
        self.cost_multiplier = _first_float(cfg.get('cost_multiplier'),
                                            os.environ.get('AGENT_CREDIT_COST_MULTIPLIER'),
                                            DEFAULT_COST_MULTIPLIER)

    # ── persistence ──────────────────────────────────────────────────

    def _load(self) -> dict:
        state = {'accounts': {}, 'txs': {}, 'config': {}}
        try:
            if self._path.exists():
                with open(self._path) as f:
                    state.update(json.load(f))
        except Exception:
            pass
        # the books: money in from guests, money out to the providers, our cut
        book = state.setdefault('treasury', {})
        for k in ('deposits', 'grants', 'revenue', 'provider_cost', 'fees', 'withdrawn'):
            book.setdefault(k, 0.0)
        book.setdefault('topups', {})
        book.setdefault('ledger', [])
        book.setdefault('baseline', {})
        # per-provider meter reading as of the last booked top-up
        book.setdefault('purchase', {})
        # deposits guests tagged for one provider (a hint, not a sub-balance)
        book.setdefault('earmarked', {})
        return state

    def _save(self):
        tmp = self._path.with_suffix('.json.tmp')
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, 'w') as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self._path)

    # ── accounts ─────────────────────────────────────────────────────

    def _account(self, address: str) -> dict:
        addr = (address or '').lower()
        return self._state['accounts'].setdefault(addr, {'balance': 0.0, 'history': []})

    def _record(self, acct: dict, kind: str, amount: float, note: str = '', tx: str = None):
        entry = {'time': _now(), 'type': kind, 'amount': round(amount, 6)}
        if note:
            entry['note'] = note[:120]
        if tx:
            entry['tx'] = tx
        acct['history'].append(entry)
        if len(acct['history']) > MAX_HISTORY:
            acct['history'] = acct['history'][-MAX_HISTORY:]

    def balance(self, address: Optional[str]) -> float:
        if not address:
            return 0.0
        acct = self._state['accounts'].get(address.lower())
        return round(float(acct['balance']), 6) if acct else 0.0

    def credit(self, address: str, amount: float, kind: str = 'deposit',
               note: str = '', tx: str = None) -> dict:
        """Add credits to an account (deposit verification or owner grant)."""
        if not address or not str(address).startswith('0x'):
            raise ValueError('a 0x address is required')
        amount = float(amount)
        with self._lock:
            acct = self._account(address)
            acct['balance'] = round(float(acct['balance']) + amount, 6)
            if acct['balance'] < 0:
                acct['balance'] = 0.0
            self._record(acct, kind, amount, note, tx)
            self._book_credit(kind, amount)
            self._save()
            return {'address': address.lower(), 'balance': acct['balance'], 'credited': amount}

    # ── charging ─────────────────────────────────────────────────────

    def quote(self, cost: float) -> dict:
        """What a run that cost us `cost` at the provider bills the guest."""
        cost = max(0.0, float(cost or 0))
        fee = cost * self.fee_rate
        return {'cost': round(cost, 6), 'fee': round(fee, 6), 'total': round(cost + fee, 6)}

    def charge_usage(self, address: str, cost: float, note: str = '',
                     model: str = None, steps: int = 0) -> dict:
        """Bill a finished run at metered provider cost + margin.

        `cost` is what the run burned on the module's provider key. The
        guest pays that plus fee_rate, so their deposit is what buys the
        OpenRouter/Venice credits their run just spent.
        """
        q = self.quote(cost)
        return self._charge(address, q['total'], note=note, model=model,
                            steps=steps, basis='usage')

    def charge_steps(self, address: str, steps: int, note: str = '',
                     model: str = None) -> dict:
        """Fallback billing for a run the meter couldn't price: steps × price.

        The flat price is taken to already include the margin, so the books
        split it the same way a metered charge is split.
        """
        amount = round(max(0, int(steps)) * self.price_per_step, 6)
        return self._charge(address, amount, note=note, model=model,
                            steps=int(steps), basis='steps')

    def _charge(self, address: str, total: float, note: str = '', model: str = None,
                steps: int = 0, basis: str = 'usage') -> dict:
        """Debit an account and book the charge as provider cost + our fee.

        A charge is clamped to the balance (an account never goes negative),
        and the clamped amount is split on the same ratio — so the books
        still say how much of what we collected is owed to the providers.
        """
        if not address:
            return {'charged': 0.0, 'balance': 0.0, 'cost': 0.0, 'fee': 0.0}
        total = round(max(0.0, float(total or 0)), 6)
        with self._lock:
            acct = self._account(address)
            charged = round(min(total, float(acct['balance'])), 6)
            cost = round(charged / (1 + max(0.0, self.fee_rate)), 6)
            fee = round(charged - cost, 6)
            out = {'charged': charged, 'cost': cost, 'fee': fee, 'steps': int(steps),
                   'basis': basis, 'quoted': total, 'balance': acct['balance']}
            if charged <= 0:
                return out
            acct['balance'] = round(float(acct['balance']) - charged, 6)
            entry_note = f'{model} · {note}' if model and note else (model or note)
            self._record(acct, 'spend', -charged, entry_note or '')
            acct['history'][-1].update(cost=cost, fee=fee)
            book = self._state['treasury']
            book['revenue'] = round(book['revenue'] + charged, 6)
            book['provider_cost'] = round(book['provider_cost'] + cost, 6)
            book['fees'] = round(book['fees'] + fee, 6)
            self._save()
            out['balance'] = acct['balance']
            return out

    # ── treasury ─────────────────────────────────────────────────────

    def _book_credit(self, kind: str, amount: float):
        """Money in: a verified deposit is real cash, a grant is not."""
        book = self._state['treasury']
        field = 'grants' if kind == 'grant' else 'deposits'
        book[field] = round(book.get(field, 0.0) + float(amount), 6)

    def _book_ledger(self, entry: dict):
        led = self._state['treasury']['ledger']
        led.append(entry)
        if len(led) > MAX_TREASURY_LEDGER:
            self._state['treasury']['ledger'] = led[-MAX_TREASURY_LEDGER:]

    def record_topup(self, provider: str, amount: float, ref: str = '',
                     note: str = '') -> dict:
        """Record real money sent to a provider to buy API credits.

        This is bookkeeping, not a payment: the owner buys the OpenRouter or
        Venice credits out of the deposit float and logs it here so the
        treasury can say what is still unfunded.
        """
        provider = (provider or '').strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider '{provider}' — use one of {list(PROVIDERS)}")
        amount = float(amount)
        if amount <= 0:
            raise ValueError('top-up amount must be positive')
        with self._lock:
            book = self._state['treasury']
            book['topups'][provider] = round(book['topups'].get(provider, 0.0) + amount, 6)
            self._book_ledger({'time': _now(), 'type': 'topup', 'provider': provider,
                               'amount': round(amount, 6), 'ref': (ref or '')[:120],
                               'note': (note or '')[:120]})
            # a hand-logged purchase is the same money verify_topup would see
            # on the key — walk the mark past it so it can't be booked twice
            marks = book.setdefault('purchase', {})
            if marks.get(provider) is not None:
                marks[provider] = round(float(marks[provider]) + amount, 6)
            self._save()
            return {'provider': provider, 'amount': round(amount, 6),
                    'topups': dict(book['topups'])}

    def _purchase_view(self, provider: str, live: dict) -> dict:
        """Has money landed on this key that the books haven't seen?

        `mark` is the provider's own meter as of the last booked top-up.
        OpenRouter counts credits ever bought, which only goes up, so
        mark → now is exactly what was purchased. Venice reports only the
        USD left, which spending pulls down — so the mark follows it down
        and only a rise above the mark reads as a top-up.
        """
        spec = PROVIDER_TOPUP.get(provider) or {}
        out = {'url': spec.get('url'), 'meter': spec.get('meter'),
               'exact': bool(spec.get('exact')),
               'mark': None, 'now': None, 'pending': 0.0}
        if not spec or live.get('error'):
            return out
        now = live.get(spec['meter'])
        if now is None:
            return out
        now = round(float(now), 6)
        marks = self._state['treasury'].setdefault('purchase', {})
        mark = marks.get(provider)
        if mark is None or (not spec['exact'] and now < float(mark)):
            # first sight of this key, or an inexact meter spent down
            marks[provider] = mark = now
        out.update(mark=round(float(mark), 6), now=now,
                   pending=round(max(0.0, now - float(mark)), 6))
        return out

    def verify_topup(self, provider: str, live: dict) -> dict:
        """Book what the provider's own numbers say landed on the key.

        The owner buys the credits on the provider's page — see
        PROVIDER_TOPUP for why there is no API to buy them with — and this
        is the other half of that trip: it reads the purchase back off the
        key, so the ledger records what arrived rather than what was typed.
        """
        provider = (provider or '').strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider '{provider}' — use one of {list(PROVIDERS)}")
        with self._lock:
            view = self._purchase_view(provider, live or {})
            out = {'provider': provider, 'booked': 0.0, **view}
            if view['now'] is None:
                out['reason'] = ((live or {}).get('error')
                                 or f'no readable {view["meter"] or "balance"} on this key')
                self._save()
                return out
            if view['pending'] <= TOPUP_EPSILON:
                out['reason'] = 'nothing new on the key yet'
                self._save()
                return out
            book = self._state['treasury']
            amount = view['pending']
            book['topups'][provider] = round(book['topups'].get(provider, 0.0) + amount, 6)
            self._book_ledger({
                'time': _now(), 'type': 'topup', 'provider': provider,
                'amount': amount, 'verified': True,
                'ref': f"{view['meter']} {view['mark']} → {view['now']}",
                'note': ('read off the provider key' if view['exact']
                         else 'balance rose on the key'),
            })
            book.setdefault('purchase', {})[provider] = view['now']
            self._save()
            return {**out, 'booked': amount, 'mark': view['now'], 'pending': 0.0,
                    'topups': dict(book['topups'])}

    def record_withdrawal(self, amount: float, note: str = '') -> dict:
        """Take earned margin out of the float. Capped at what we've earned —
        the rest of the balance is guests' unspent credits, not ours."""
        amount = float(amount)
        if amount <= 0:
            raise ValueError('withdrawal amount must be positive')
        with self._lock:
            book = self._state['treasury']
            available = round(book['fees'] - book['withdrawn'], 6)
            if amount > available:
                raise ValueError(f'only ${available:.6f} of margin has been earned')
            book['withdrawn'] = round(book['withdrawn'] + amount, 6)
            self._book_ledger({'time': _now(), 'type': 'withdraw',
                               'amount': round(amount, 6), 'note': (note or '')[:120]})
            self._save()
            return {'withdrawn': round(amount, 6), 'fees_available': round(available - amount, 6)}

    def set_config(self, **kwargs) -> dict:
        """Owner knobs: fee_rate, price_per_step, cost_multiplier, deposit_address."""
        with self._lock:
            cfg = self._state.setdefault('config', {})
            if kwargs.get('fee_rate') is not None:
                rate = float(kwargs['fee_rate'])
                if not 0 <= rate <= 10:
                    raise ValueError('fee_rate must be between 0 and 10 (0.05 = 5%)')
                cfg['fee_rate'] = self.fee_rate = rate
            if kwargs.get('price_per_step') is not None:
                price = float(kwargs['price_per_step'])
                if price < 0:
                    raise ValueError('price_per_step cannot be negative')
                cfg['price_per_step'] = self.price_per_step = price
            if kwargs.get('cost_multiplier') is not None:
                mult = float(kwargs['cost_multiplier'])
                if not 0 < mult <= 10:
                    raise ValueError('cost_multiplier must be between 0 and 10')
                cfg['cost_multiplier'] = self.cost_multiplier = mult
            addr = kwargs.get('deposit_address')
            if addr:
                if not str(addr).startswith('0x') or len(str(addr)) != 42:
                    raise ValueError('deposit_address must be a 0x… 42-char address')
                cfg['deposit_address'] = self.deposit_address = str(addr).lower()
            self._save()
        return self.config()

    def config(self) -> dict:
        return {'fee_rate': self.fee_rate, 'price_per_step': self.price_per_step,
                'cost_multiplier': self.cost_multiplier,
                'deposit_address': self.deposit_address}

    def treasury(self, providers: Optional[dict] = None) -> dict:
        """The funding picture: what guests paid in, what we owe the providers.

        `providers` is {name: {'balance': usd, 'usage': lifetime_usd|None}} read
        live from the provider APIs — pass None to skip the live half.

        The number that matters is `topup_needed`: unspent guest credits, less
        our margin, minus what the provider keys already hold. That is how much
        of the deposit float has to go to OpenRouter/Venice to stay solvent.
        """
        with self._lock:
            book = self._state['treasury']
            liability = round(sum(float(a['balance']) for a in self._state['accounts'].values()), 6)
            funding_required = round(liability / (1 + self.fee_rate), 6)
            topups = dict(book.get('topups', {}))
            out = {
                **self.config(),
                'deposits': round(book['deposits'], 6),
                'grants': round(book['grants'], 6),
                'revenue': round(book['revenue'], 6),
                'provider_cost': round(book['provider_cost'], 6),
                'fees': round(book['fees'], 6),
                'fees_withdrawn': round(book['withdrawn'], 6),
                'fees_available': round(book['fees'] - book['withdrawn'], 6),
                'topups': topups,
                'topups_total': round(sum(topups.values()), 6),
                'earmarked': {k: round(float(v), 6) for k, v in book.get('earmarked', {}).items()},
                'user_credits': liability,
                'funding_required': funding_required,
                'accounts': len(self._state['accounts']),
                'ledger': list(reversed(book.get('ledger', [])))[:50],
            }
            # cash we hold that hasn't been sent to a provider or taken as margin
            out['float'] = round(out['deposits'] - out['topups_total'] - out['fees_withdrawn'], 6)
            out['providers'] = self._provider_view(providers or {})
            balance = round(sum(p.get('balance') or 0.0 for p in out['providers'].values()), 6)
            out['provider_balance'] = balance if providers else None
            out['topup_needed'] = (round(max(0.0, funding_required - balance), 6)
                                   if providers else None)
            # credits seen on a key that the books haven't booked yet — a
            # purchase made on the provider's page, waiting to be confirmed
            out['topup_pending'] = round(sum(
                (p.get('topup') or {}).get('pending') or 0.0
                for p in out['providers'].values()), 6) if providers else None
            self._save()   # _provider_view may have stamped a drift baseline
            return out

    def _provider_view(self, providers: dict) -> dict:
        """Per-provider funding + drift, baselined the first time we see a meter.

        OpenRouter reports lifetime usage on the key, so the honest read of our
        estimate is: usage since we started metering vs what we billed since
        then. Owner runs burn the same key and are never billed, which pushes
        `actual` above `billed` — the drift is a signal to read, not a dial.
        """
        book = self._state['treasury']
        base = book.setdefault('baseline', {})
        billed_now = round(book['provider_cost'], 6)
        view = {}
        for name in PROVIDERS:
            live = providers.get(name) or {}
            entry = {'balance': live.get('balance'),
                     'topups': round(book.get('topups', {}).get(name, 0.0), 6),
                     # where to buy more, and whether a purchase is sitting
                     # on the key unbooked
                     'topup': self._purchase_view(name, live)}
            if live.get('error'):
                entry['error'] = live['error']
            usage = live.get('usage')
            if usage is not None:
                mark = base.get(name)
                if not mark:
                    mark = {'usage': float(usage), 'billed': billed_now, 'time': _now()}
                    base[name] = mark
                actual = round(float(usage) - mark['usage'], 6)
                billed = round(billed_now - mark['billed'], 6)
                entry['metered'] = {
                    'actual': actual, 'billed': billed, 'since': mark['time'],
                    'ratio': round(actual / billed, 3) if billed > 0 else None,
                }
            view[name] = entry
        return view

    # ── views ────────────────────────────────────────────────────────

    def info(self, address: Optional[str] = None, owner: bool = False) -> dict:
        """Public deposit/pricing info + the caller's own account view."""
        out = {
            'enabled': bool(self.deposit_address),
            'price_per_step': self.price_per_step,
            # what a run costs: the provider's own price for the tokens it
            # burned, plus this margin. The console shows both.
            'fee_rate': self.fee_rate,
            'pricing': 'provider cost + margin',
            'deposit': {
                'address': self.deposit_address,
                'networks': {
                    net: {
                        'chain_id': info['chain_id'],
                        'tokens': sorted(set(info['tokens'].values())) + [info['native']],
                        'native': info['native'],
                        # contract + decimals per token — a wallet builds the
                        # transfer from this, no hardcoding in the console
                        'contracts': {
                            sym: {'address': addr, 'decimals': TOKEN_DECIMALS}
                            for addr, sym in info['tokens'].items()
                        },
                        'explorer': info['explorer'],
                    }
                    for net, info in NETWORKS.items()
                },
                # a deposit can be earmarked for one of these keys
                'providers': list(PROVIDERS),
            },
        }
        if address:
            acct = self._state['accounts'].get(address.lower())
            out['account'] = {
                'address': address.lower(),
                'balance': round(float(acct['balance']), 6) if acct else 0.0,
                'history': list(reversed(acct['history'])) if acct else [],
            }
        if owner:
            out['accounts'] = [
                {'address': a, 'balance': round(float(v['balance']), 6)}
                for a, v in sorted(self._state['accounts'].items())
            ]
        return out

    # ── rpc ──────────────────────────────────────────────────────────

    def _rpc(self, network: str, method: str, params: list):
        """JSON-RPC over the network's public nodes — env override first, then
        each built-in endpoint in turn. A public node being down is routine;
        a JSON-RPC error (bad params, unknown hash) is final and not retried."""
        info = NETWORKS[network]
        urls = [os.environ.get(info['rpc_env'])] if os.environ.get(info['rpc_env']) else []
        urls += info['rpc'] if isinstance(info['rpc'], list) else [info['rpc']]
        body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                           'method': method, 'params': params}).encode()
        errors = []
        for url in urls:
            req = urllib.request.Request(url, data=body, headers={
                'Content-Type': 'application/json',
                'User-Agent': 'mod-agent-credits/1.0',
            })
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode())
            except Exception as e:
                errors.append(f'{url}: {e}')
                continue
            if data.get('error'):
                raise RuntimeError(f"rpc error: {data['error'].get('message', data['error'])}")
            return data.get('result')
        raise RuntimeError(f'{network} rpc unreachable — ' + '; '.join(errors))

    # ── ETH/USD ──────────────────────────────────────────────────────

    def eth_usd(self, network: str = 'base') -> dict:
        """ETH in USD, read off the Chainlink feed over the network's own RPC.

        The feed is the same source every DEX and lender on the chain
        settles on, and it needs no key — so an ETH deposit is priced by the
        chain it landed on, not by us. `AGENT_ETH_USD` pins a price (tests,
        air-gapped boxes); CoinGecko is the fallback when the RPC is down.
        """
        network = (network or 'base').lower()
        if network not in NETWORKS:
            raise ValueError(f"unsupported network '{network}' — use one of {sorted(NETWORKS)}")
        pinned = os.environ.get('AGENT_ETH_USD')
        if pinned:
            return {'usd': float(pinned), 'source': 'env', 'network': network, 'time': _now()}
        hit = self._price_cache.get(network)
        if hit and _now() - hit['time'] < PRICE_TTL:
            return hit
        out = None
        try:
            raw = self._rpc(network, 'eth_call',
                            [{'to': NETWORKS[network]['price_feed'], 'data': LATEST_ROUND_DATA},
                             'latest'])
            out = {'usd': self._decode_feed(raw), 'source': 'chainlink',
                   'network': network, 'time': _now()}
        except Exception as e:
            try:
                req = urllib.request.Request(PRICE_FALLBACK_URL,
                                             headers={'User-Agent': 'mod-agent-credits/1.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    usd = float(json.loads(resp.read().decode())['ethereum']['usd'])
                out = {'usd': usd, 'source': 'coingecko', 'network': network,
                       'time': _now(), 'feed_error': str(e)}
            except Exception as e2:
                raise RuntimeError(f'ETH price unavailable: {e}; fallback: {e2}')
        self._price_cache[network] = out
        return out

    @staticmethod
    def _decode_feed(raw: str) -> float:
        """latestRoundData() → (roundId, answer, startedAt, updatedAt, answeredInRound)."""
        data = (raw or '')[2:] if str(raw).startswith('0x') else (raw or '')
        if len(data) < 64 * 2:
            raise RuntimeError('short feed response')
        answer = int(data[64:128], 16)
        if answer >= 2 ** 255:            # int256 two's complement
            answer -= 2 ** 256
        if answer <= 0:
            raise RuntimeError('feed answered a non-positive price')
        return round(answer / 10 ** FEED_DECIMALS, 2)

    # ── on-chain deposit verification ────────────────────────────────

    def verify_deposit(self, tx_hash: str, network: str = 'base',
                       provider: Optional[str] = None) -> dict:
        """Verify a USDT/USDC or ETH transfer into the deposit address and
        credit the on-chain sender. Each tx hash can only be credited once.

        `provider` earmarks the deposit for one provider key (openrouter or
        venice) — the treasury shows it, the balance is the same either way.
        """
        if not self.deposit_address:
            raise ValueError('deposits are disabled — no deposit address configured')
        network = (network or 'base').lower()
        if network not in NETWORKS:
            raise ValueError(f"unsupported network '{network}' — use one of {sorted(NETWORKS)}")
        provider = (provider or '').strip().lower() or None
        if provider and provider not in PROVIDERS:
            raise ValueError(f"unknown provider '{provider}' — use one of {list(PROVIDERS)}")
        tx_hash = (tx_hash or '').strip().lower()
        if not (tx_hash.startswith('0x') and len(tx_hash) == 66):
            raise ValueError('tx_hash must be a 0x… 66-char transaction hash')
        with self._lock:
            if tx_hash in self._state['txs']:
                raise ValueError('this transaction was already credited')

        receipt = self._rpc(network, 'eth_getTransactionReceipt', [tx_hash])
        if not receipt:
            raise ValueError('transaction not found (still pending? wrong network?)')
        if receipt.get('status') != '0x1':
            raise ValueError('transaction failed on-chain')

        tokens = NETWORKS[network]['tokens']
        want_to = self.deposit_address[2:].rjust(64, '0')
        total = 0.0
        sender = None
        token_seen = None
        detail = {}
        for log in receipt.get('logs', []):
            topics = log.get('topics') or []
            if (len(topics) == 3
                    and topics[0].lower() == TRANSFER_TOPIC
                    and (log.get('address') or '').lower() in tokens
                    and topics[2].lower().endswith(want_to)):
                total += int(log.get('data', '0x0'), 16) / 10 ** TOKEN_DECIMALS
                sender = '0x' + topics[1][-40:].lower()
                token_seen = tokens[(log.get('address') or '').lower()]

        # a plain ETH send has no log — read the transaction itself
        if total <= 0:
            tx = self._rpc(network, 'eth_getTransactionByHash', [tx_hash]) or {}
            if ((tx.get('to') or '').lower() == self.deposit_address
                    and int(tx.get('value') or '0x0', 16) > 0):
                eth = int(tx['value'], 16) / 10 ** NATIVE_DECIMALS
                price = self.eth_usd(network)
                total = round(eth * price['usd'], 6)
                sender = (tx.get('from') or '').lower()
                token_seen = NETWORKS[network]['native']
                detail = {'eth': round(eth, 8), 'eth_usd': price['usd'],
                          'price_source': price['source']}
        if total <= 0 or not sender:
            raise ValueError(
                f'no USDT/USDC/ETH transfer to {self.deposit_address} found in this transaction')

        note = f'{token_seen} on {network}'
        if detail:
            note = f"{detail['eth']} {note} @ ${detail['eth_usd']:,.2f}"
        if provider:
            note += f' → {provider}'
        with self._lock:
            if tx_hash in self._state['txs']:   # raced with a duplicate submit
                raise ValueError('this transaction was already credited')
            self._state['txs'][tx_hash] = {
                'network': network, 'token': token_seen, 'from': sender,
                'amount': round(total, 6), 'time': _now(), **detail,
                **({'provider': provider} if provider else {}),
            }
            acct = self._account(sender)
            acct['balance'] = round(float(acct['balance']) + total, 6)
            self._record(acct, 'deposit', total, note, tx_hash)
            self._book_credit('deposit', total)
            if provider:
                marks = self._state['treasury'].setdefault('earmarked', {})
                marks[provider] = round(marks.get(provider, 0.0) + total, 6)
            self._save()
        return {'credited': round(total, 6), 'token': token_seen, 'network': network,
                'address': sender, 'balance': self.balance(sender),
                'explorer': NETWORKS[network]['explorer'] + tx_hash,
                **detail, **({'provider': provider} if provider else {})}
