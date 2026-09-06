"""
OpenHouse — Collective Asset Ownership Platform.

Rent-to-own property, on-chain. Renters pay monthly; the protocol takes 0–5%
(the owner picks the number — zero included — and the ceiling is hard-capped in
the contract) and the rest stays with the property, split between the renter's
equity and the owner's rent income by an owner-chosen rent-to-own model.

Whatever the fee does collect is not kept. It pools, and every quarter the pool
is handed back by BLOCTIME — dollars x seconds of liquidity locked in the
protocol — so the money goes to whoever left their money in, in proportion to
how long they left it.

Flow:
  1. Deploy contract  — deploy(network, key, property_details, total_shares, share_price)
  2. Set the deal     — set_terms(model=, fee_pct=, credit_pct=, owner=)   [fee_pct=0 is legal]
  3. Pay rent         — pay_rent(renter, amount)  → fee / equity / owner income
  4. Query equity     — equity(address), rent_ledger()
  5. Watch the pool   — pool(), bloctime(address)
  6. Every 90 days    — close_quarter() → pool_claim(address)

Also supports the original fractional-share float: purchase(), distribute().

On-chain: OpenHouse contract on Base Sepolia.
Storage:  ~/.openhouse/{shareholders,properties,dividends,terms,rent,pool}.json
"""

import json
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any
import mod as m


class Mod:
    description = "Rent-to-own, on-chain — the protocol takes 0–5% (owner-set, zero allowed), the rest stays with the property, and the fee pool is paid back quarterly by dollars x time locked."

    # ── The protocol take ──────────────────────────────────────────
    # Mirrors MIN_FEE_BPS / MAX_FEE_BPS in contracts/OpenHouse.sol. The owner
    # picks a number inside this band; nothing can widen it. The floor is 0 —
    # an owner who wants to run the protocol at cost is allowed to, and the
    # rent-to-own split doesn't change when they do.
    MIN_FEE_PCT = 0.0
    MAX_FEE_PCT = 5.0

    # ── Rent-to-own models ─────────────────────────────────────────
    # A model is one number with a name: how much of each post-fee payment is
    # credited to the renter as principal. Owners start from a preset and tune.
    MODELS = [
        {
            'id': 'full_credit',
            'name': 'Full credit',
            'credit_pct': 100.0,
            'option_fee_pct': 0.0,
            'headline': 'Every net dollar buys the house',
            'detail': 'The whole payment, after the protocol fee, is principal. '
                      'The owner earns from lowfi yield on funds in flight rather than from rent.',
        },
        {
            'id': 'hybrid',
            'name': 'Hybrid 50/50',
            'credit_pct': 50.0,
            'option_fee_pct': 0.0,
            'headline': 'Half equity, half rent income',
            'detail': 'Half of each payment builds the renter\'s stake, half is the owner\'s income. '
                      'The balanced deal when the owner still carries a mortgage.',
        },
        {
            'id': 'classic',
            'name': 'Classic lease-option',
            'credit_pct': 25.0,
            'option_fee_pct': 3.0,
            'headline': 'The standard rent-to-own, minus the middleman',
            'detail': 'A 25% rent credit plus an upfront option fee of 3% of the price — the usual '
                      'contract-for-deed shape, except the credit is enforced on-chain instead of promised.',
        },
        {
            'id': 'lease',
            'name': 'Plain lease',
            'credit_pct': 0.0,
            'option_fee_pct': 0.0,
            'headline': 'No equity, still no extraction',
            'detail': 'A normal tenancy. The renter builds nothing, but the owner keeps 95–99% of the '
                      'rent instead of handing a platform double digits.',
        },
    ]

    # What the incumbents take off the top. Published headline rates — the point
    # of the comparison is the order of magnitude, not the decimal.
    BENCHMARKS = [
        {'name': 'Airbnb', 'take_pct': 15.0, 'equity_pct': 0.0,
         'note': 'host 3% + guest ~14%, or ~15% host-only'},
        {'name': 'Vrbo / Booking', 'take_pct': 13.0, 'equity_pct': 0.0,
         'note': 'commission plus payment processing'},
        {'name': 'Property manager', 'take_pct': 10.0, 'equity_pct': 0.0,
         'note': '8–12% of monthly rent, plus leasing fees'},
        {'name': 'Traditional lease', 'take_pct': 0.0, 'equity_pct': 0.0,
         'note': 'no platform — and no equity either'},
    ]

    DEFAULT_TERMS = {
        'model': 'full_credit',
        'fee_pct': 2.5,
        'credit_pct': 100.0,
        'option_fee_pct': 0.0,
        'home_price': 0.0,
        'monthly_rent': 0.0,
        'owner': '',
        'treasury': '',
        'updated': 0,
    }

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser('~/.openhouse'))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Paths
        self.shareholders_path = self.store_dir / 'shareholders.json'
        self.properties_path = self.store_dir / 'properties.json'
        self.dividends_path = self.store_dir / 'dividends.json'
        self.terms_path = self.store_dir / 'terms.json'
        self.rent_path = self.store_dir / 'rent.json'
        self.pool_path = self.store_dir / 'pool.json'
        self.peers_cache_path = self.store_dir / 'peers_cache.json'

        # Config
        self.network = self.config.get('network', 'testnet')
        self.port = int(self.config.get('port', 50132))
        self.app_port = int(self.config.get('app_port', 50131))

        # Chain config
        net_cfg = self.config.get('contracts', {}).get(self.network, {})
        self.rpc_url = net_cfg.get('url', 'https://sepolia.base.org')
        self.contract_address = (
            net_cfg.get('contracts', {})
            .get('OpenHouse', {})
            .get('address', '')
        )

    def _load_config(self):
        config_path = self.module_dir / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_json(self, path, default=None):
        p = Path(path)
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return default if default is not None else {}

    def _save_json(self, path, data):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _load_shareholders(self):
        return self._load_json(self.shareholders_path, {})

    def _save_shareholders(self, data):
        self._save_json(self.shareholders_path, data)

    def _load_properties(self):
        return self._load_json(self.properties_path, {})

    def _save_properties(self, data):
        self._save_json(self.properties_path, data)

    def _load_dividends(self):
        return self._load_json(self.dividends_path, [])

    def _save_dividends(self, data):
        self._save_json(self.dividends_path, data)

    def _load_rent(self):
        return self._load_json(self.rent_path, [])

    def _save_rent(self, data):
        self._save_json(self.rent_path, data)

    # ━━ Rent-to-own terms ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _model(self, model_id):
        for m_ in self.MODELS:
            if m_['id'] == model_id:
                return m_
        return None

    def models(self):
        """The rent-to-own models an owner can start from.

        Each preset is a starting point, not a cage — the owner tunes credit_pct
        and fee_pct afterwards. Fee bounds are the same for every model.
        """
        return {
            'models': self.MODELS,
            'fee_band': {'min_pct': self.MIN_FEE_PCT, 'max_pct': self.MAX_FEE_PCT,
                         'note': 'Zero is inside the band. Whatever is taken above it '
                                 'pools and is paid back quarterly by bloctime.'},
            'benchmarks': self.BENCHMARKS,
        }

    def terms(self):
        """The live deal: model, protocol fee, rent credit, and what it implies."""
        t = {**self.DEFAULT_TERMS, **self._load_json(self.terms_path, {})}
        fee_pct = float(t['fee_pct'])
        credit_pct = float(t['credit_pct'])
        # Of every 100 paid: fee to the protocol, the rest split equity / owner.
        to_property = 100.0 - fee_pct
        t['equity_pct_of_rent'] = round(to_property * credit_pct / 100.0, 4)
        t['owner_pct_of_rent'] = round(to_property - t['equity_pct_of_rent'], 4)
        t['to_property_pct'] = round(to_property, 4)
        t['fee_band'] = {'min_pct': self.MIN_FEE_PCT, 'max_pct': self.MAX_FEE_PCT}
        # Zero is a position, not a missing value: no pool, nothing to hand back.
        t['zero_fee'] = fee_pct == 0.0
        t['quarter_seconds'] = self.QUARTER_SECONDS
        m_ = self._model(t.get('model'))
        t['model_name'] = m_['name'] if m_ else 'Custom'
        t['custom'] = bool(m_ and abs(credit_pct - m_['credit_pct']) > 1e-9)
        return t

    def set_terms(self, model=None, fee_pct=None, credit_pct=None,
                  option_fee_pct=None, home_price=None, monthly_rent=None,
                  owner=None, treasury=None) -> dict:
        """Owner sets the deal.

        Args:
            model:          preset id (full_credit | hybrid | classic | lease)
            fee_pct:        protocol take, 0–5 (rejected outside the band; 0 means
                            no fee and no pool — the rest of the deal is unchanged)
            credit_pct:     share of the post-fee payment credited as equity, 0–100
            option_fee_pct: upfront option fee, % of home price
            home_price:     price to own outright
            monthly_rent:   the scheduled monthly payment
            owner:          address making the change — must match the recorded
                            owner once one is set (see the note below)
            treasury:       protocol fee sink

        The owner check here is an address match, not a signature — the local
        store mirrors the contract, where ``onlyOwner`` does the real enforcing.
        """
        current = {**self.DEFAULT_TERMS, **self._load_json(self.terms_path, {})}
        recorded_owner = (current.get('owner') or '').lower()
        caller = (owner or '').strip()
        if recorded_owner and caller.lower() != recorded_owner:
            return {'error': 'Only the property owner can change the terms'}
        if not recorded_owner and caller:
            current['owner'] = caller

        if model is not None:
            m_ = self._model(model)
            if not m_:
                return {'error': f"Unknown model: {model}. "
                                 f"Choose one of: {', '.join(x['id'] for x in self.MODELS)}"}
            current['model'] = m_['id']
            # A preset sets the dials; explicit args below still win.
            current['credit_pct'] = m_['credit_pct']
            current['option_fee_pct'] = m_['option_fee_pct']

        if fee_pct is not None:
            fee = float(fee_pct)
            if fee < self.MIN_FEE_PCT or fee > self.MAX_FEE_PCT:
                return {'error': f'Protocol fee must be between {self.MIN_FEE_PCT}% '
                                 f'and {self.MAX_FEE_PCT}% — got {fee}%'}
            current['fee_pct'] = round(fee, 4)

        if credit_pct is not None:
            credit = float(credit_pct)
            if credit < 0 or credit > 100:
                return {'error': f'Rent credit must be between 0% and 100% — got {credit}%'}
            current['credit_pct'] = round(credit, 4)

        if option_fee_pct is not None:
            opt = float(option_fee_pct)
            if opt < 0 or opt > 100:
                return {'error': 'Option fee must be between 0% and 100% of the price'}
            current['option_fee_pct'] = round(opt, 4)

        if home_price is not None:
            current['home_price'] = max(float(home_price), 0.0)
        if monthly_rent is not None:
            current['monthly_rent'] = max(float(monthly_rent), 0.0)
        if treasury is not None:
            current['treasury'] = str(treasury)

        current['updated'] = int(time.time())
        self._save_json(self.terms_path, current)
        return {'success': True, 'terms': self.terms()}

    def claim_owner(self, address: str) -> dict:
        """Claim the owner seat while it's empty (first writer wins)."""
        if not address:
            return {'error': 'Address required'}
        current = {**self.DEFAULT_TERMS, **self._load_json(self.terms_path, {})}
        if current.get('owner'):
            return {'error': 'Owner already set', 'owner': current['owner']}
        current['owner'] = address
        current['updated'] = int(time.time())
        self._save_json(self.terms_path, current)
        return {'success': True, 'owner': address}

    # ━━ Rent ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def quote(self, amount: float, kind: str = 'rent') -> dict:
        """Split a payment the way pay_rent would, without recording it.

        Mirrors ``quoteRent`` in the contract, including the clamp that stops
        equity credit from running past the home price.
        """
        amount = float(amount)
        if amount <= 0:
            return {'error': 'Amount must be greater than 0'}
        t = self.terms()
        fee = amount * float(t['fee_pct']) / 100.0
        net = amount - fee
        # An option fee is pure equity — that's what the renter is buying with it.
        credit_pct = 100.0 if kind == 'option' else float(t['credit_pct'])
        credit = net * credit_pct / 100.0

        price = float(t['home_price'])
        if price > 0:
            room = max(price - self._principal_paid_total(), 0.0)
            credit = min(credit, room)
        owner_income = net - credit

        return {
            'amount': round(amount, 8),
            'fee': round(fee, 8),
            'credit': round(credit, 8),
            'owner_income': round(owner_income, 8),
            'fee_pct': t['fee_pct'],
            'credit_pct': credit_pct,
            'to_property': round(net, 8),
            'to_property_pct': t['to_property_pct'],
            'kind': kind,
        }

    def _principal_paid_total(self):
        return sum(float(r.get('credit', 0)) for r in self._load_rent())

    def pay_rent(self, renter: str, amount: float, kind: str = 'rent') -> dict:
        """Record a rent payment and split it: protocol fee, equity, owner income.

        Args:
            renter: the paying address
            amount: payment amount
            kind:   'rent' (split by the model) or 'option' (all equity)
        """
        if not renter:
            return {'error': 'Renter address required'}
        split = self.quote(amount, kind=kind)
        if 'error' in split:
            return split

        t = self.terms()
        price = float(t['home_price'])
        if price > 0 and self._principal_paid_total() >= price:
            return {'error': 'Home already paid off'}

        entry = {
            'timestamp': int(time.time()),
            'renter': renter,
            'amount': split['amount'],
            'fee': split['fee'],
            'credit': split['credit'],
            'owner_income': split['owner_income'],
            'fee_pct': split['fee_pct'],
            'credit_pct': split['credit_pct'],
            'model': t['model'],
            'kind': kind,
        }
        ledger = self._load_rent()
        ledger.append(entry)
        self._save_rent(ledger)

        return {'success': True, **entry, 'equity': self.equity(renter)}

    def rent_ledger(self, renter: str = '') -> list:
        """Every recorded payment, newest first. Filter by renter if given."""
        ledger = self._load_rent()
        if renter:
            ledger = [r for r in ledger if r.get('renter', '').lower() == renter.lower()]
        return list(reversed(ledger))

    def equity(self, address: str) -> dict:
        """A renter's stake: principal credited, rent paid, and what it bought."""
        ledger = [r for r in self._load_rent()
                  if r.get('renter', '').lower() == (address or '').lower()]
        credit = sum(float(r.get('credit', 0)) for r in ledger)
        paid = sum(float(r.get('amount', 0)) for r in ledger)
        fees = sum(float(r.get('fee', 0)) for r in ledger)
        price = float(self.terms()['home_price'])
        return {
            'address': address,
            'payments': len(ledger),
            'rent_paid': round(paid, 8),
            'principal': round(credit, 8),
            'fees_paid': round(fees, 8),
            'equity_pct': round(credit / price * 100, 4) if price > 0 else 0.0,
            'remaining': round(max(price - credit, 0.0), 8) if price > 0 else 0.0,
            'fully_owned': bool(price > 0 and credit >= price),
        }

    def rent_stats(self) -> dict:
        """Where the rent actually went — the number that makes the case."""
        ledger = self._load_rent()
        gross = sum(float(r.get('amount', 0)) for r in ledger)
        fees = sum(float(r.get('fee', 0)) for r in ledger)
        credit = sum(float(r.get('credit', 0)) for r in ledger)
        income = sum(float(r.get('owner_income', 0)) for r in ledger)
        t = self.terms()
        price = float(t['home_price'])
        renters = sorted({r.get('renter', '') for r in ledger if r.get('renter')})
        return {
            'payments': len(ledger),
            'renters': len(renters),
            'gross_rent': round(gross, 8),
            'protocol_fees': round(fees, 8),
            'renter_equity': round(credit, 8),
            'owner_income': round(income, 8),
            # Of every dollar paid, what stayed with the property vs the platform.
            'to_property_pct': round((gross - fees) / gross * 100, 4) if gross > 0 else t['to_property_pct'],
            'take_pct': round(fees / gross * 100, 4) if gross > 0 else t['fee_pct'],
            'owned_pct': round(credit / price * 100, 4) if price > 0 else 0.0,
            'home_price': price,
        }

    # ━━ The pool ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #
    # The protocol fee is not a skim. The owner may set it to 0% and run the
    # thing at cost; whatever is set above that lands in a pool that is handed
    # back every quarter to the people whose money was locked in the protocol,
    # weighted by BLOCTIME — dollars x seconds, measured on block time.
    #
    # Three kinds of liquidity are locked here and all three earn:
    #   renter       principal credited toward the home, locked the moment it lands
    #   shareholder  capital paid into the float
    #   owner        the part of the home nobody has bought out yet
    # A dollar locked for a whole quarter earns twice what a dollar locked for
    # half of it; a dollar that arrived yesterday earns almost nothing. Nobody
    # earns for capital they didn't leave in.
    #
    # Mirrors QUARTER / bloctime accrual in contracts/OpenHouse.sol.

    QUARTER_SECONDS = 90 * 24 * 3600     # the cadence, same constant as the contract
    DAY_SECONDS = 24 * 3600              # weights are shown in dollar-days

    def _load_pool(self):
        return self._load_json(self.pool_path, {'genesis': 0, 'quarters': []})

    def _save_pool(self, data):
        self._save_json(self.pool_path, data)

    def _first_lock(self) -> int:
        """The earliest moment anything was locked — the epoch's natural zero."""
        stamps = [int(r.get('timestamp', 0) or 0) for r in self._load_rent()]
        stamps += [int(s.get('joined', 0) or 0) for s in self._load_shareholders().values()]
        stamps = [t for t in stamps if t > 0]
        return min(stamps) if stamps else 0

    def _quarter_window(self, pool=None):
        """``(index, start, ends_at)`` for the quarter now accruing.

        A quarter starts when the one before it was closed, not on a wall
        clock — the same rule as ``lastRedistribution = block.timestamp``
        in the contract. Close late and the next quarter simply runs from
        the late close; no bloctime is created or lost at the seam.
        """
        pool = self._load_pool() if pool is None else pool
        closed = pool.get('quarters') or []
        if closed:
            start = int(closed[-1]['end'])
            idx = int(closed[-1]['quarter']) + 1
        else:
            start = int(pool.get('genesis') or self._first_lock() or time.time())
            idx = 0
        return idx, start, start + self.QUARTER_SECONDS

    def _bloctime_window(self, start: int, end: int) -> dict:
        """Dollar-seconds of locked liquidity accrued by each address in a window.

            weight(addr) = ∫ locked(addr, t) dt   over [start, end]

        Renter principal only ever goes up, so its integral is a sum of
        ``credit x (end - max(credited_at, start))``. The owner's stake is the
        home price minus the principal bought out so far, so their integral is
        ``price x span`` minus the renters' — the two always sum to the whole
        house, which is why the owner needs no separate bookkeeping.
        """
        start = int(start)
        end = max(int(end), start)
        span = end - start
        weights, kinds, locked_now = {}, {}, {}

        def add(addr, kind, weight, locked):
            # A position with no bloctime yet still exists — it was locked a
            # second ago. Weight of zero is a fact about the clock, not a
            # reason to leave someone off the table.
            addr = (addr or '').strip()
            if not addr or (weight <= 0 and locked <= 0):
                return
            weights[addr] = weights.get(addr, 0.0) + weight
            kinds.setdefault(addr, {})
            kinds[addr][kind] = kinds[addr].get(kind, 0.0) + weight
            locked_now[addr] = locked_now.get(addr, 0.0) + locked

        renter_weight = 0.0
        for r in self._load_rent():
            credit = float(r.get('credit', 0) or 0)
            ts = int(r.get('timestamp', 0) or 0)
            if credit <= 0 or ts >= end:
                continue
            w = credit * (end - max(ts, start))
            add(r.get('renter', ''), 'renter', w, credit)
            renter_weight += w

        for addr, info in self._load_shareholders().items():
            contribution = float(info.get('contribution', 0) or 0)
            joined = int(info.get('joined', 0) or 0)
            if contribution <= 0 or joined >= end:
                continue
            add(addr, 'shareholder', contribution * (end - max(joined, start)), contribution)

        t = self.terms()
        price = float(t.get('home_price') or 0)
        # What the owner still has in the deal: the house, less what's been bought out.
        owner_weight = max(price * span - renter_weight, 0.0)
        owner_locked = max(price - self._principal_paid_total(), 0.0)
        add(t.get('owner', ''), 'owner', owner_weight, owner_locked)

        return {
            'start': start, 'end': end, 'seconds': span,
            'weights': weights, 'kinds': kinds, 'locked': locked_now,
            'total_weight': sum(weights.values()),
            'total_locked': sum(locked_now.values()),
        }

    def _fees_since(self, index: int) -> float:
        """Fees collected after the ledger position a close was cut at.

        Attribution is by ledger position, not timestamp: a payment landing in
        the same second as a close would otherwise fall between two quarters
        or into both.
        """
        return sum(float(r.get('fee', 0) or 0) for r in self._load_rent()[int(index):])

    def _positions(self, w: dict, amount: float) -> list:
        """Turn a weight map into a payout table, biggest stake first."""
        total = w['total_weight']
        rows = []
        for addr, weight in w['weights'].items():
            share = weight / total if total > 0 else 0.0
            rows.append({
                'address': addr,
                'kinds': sorted(w['kinds'].get(addr, {}), key=lambda k: -w['kinds'][addr][k]),
                'locked': round(w['locked'].get(addr, 0.0), 8),
                'weight': round(weight, 6),
                'weight_days': round(weight / self.DAY_SECONDS, 6),
                'share_pct': round(share * 100, 6),
                'amount': round(amount * share, 8),
            })
        rows.sort(key=lambda r: -r['weight'])
        return rows

    def pool(self) -> dict:
        """The quarter now accruing: what's in the pool and who it's owed to.

        Everything here is live — the pool is what the fee has collected since
        the last close, and the shares are the bloctime earned so far. Nothing
        is owed until :meth:`close_quarter` freezes it.
        """
        state = self._load_pool()
        closed = state.get('quarters') or []
        idx, start, ends_at = self._quarter_window(state)
        now = int(time.time())
        ledger_from = int(closed[-1]['ledger_to']) if closed else 0

        w = self._bloctime_window(start, now)
        amount = self._fees_since(ledger_from)
        t = self.terms()
        elapsed = max(now - start, 0)
        distributed = sum(float(q.get('pool', 0) or 0) for q in closed)
        unclaimed = sum(float(a.get('amount', 0) or 0)
                        for q in closed for a in q.get('allocations', [])
                        if not a.get('claimed'))

        return {
            'quarter': idx,
            'start': start,
            'ends_at': ends_at,
            'now': now,
            'elapsed': elapsed,
            'quarter_seconds': self.QUARTER_SECONDS,
            'progress_pct': round(min(elapsed / self.QUARTER_SECONDS, 1.0) * 100, 4),
            'ready': now >= ends_at,
            'ready_in': max(ends_at - now, 0),
            'pool': round(amount, 8),
            'fee_pct': t['fee_pct'],
            # 0% is a real answer: no fee, no pool, and the split is untouched.
            'zero_fee': float(t['fee_pct']) == 0.0,
            'total_locked': round(w['total_locked'], 8),
            'total_weight': round(w['total_weight'], 6),
            'total_weight_days': round(w['total_weight'] / self.DAY_SECONDS, 6),
            'positions': self._positions(w, amount),
            'quarters_closed': len(closed),
            'distributed': round(distributed, 8),
            'unclaimed': round(unclaimed, 8),
            'basis': 'bloctime — dollars x seconds of liquidity locked in the protocol',
        }

    def pool_history(self) -> list:
        """Every closed quarter, newest first."""
        return list(reversed((self._load_pool().get('quarters') or [])))

    def close_quarter(self, caller: str = '', force: bool = False) -> dict:
        """Close the quarter and freeze the split by bloctime.

        Permissionless once the 90 days are up — anyone can call it, the same
        as ``redistribute()`` on-chain, because the numbers are fixed by then
        and only the calling costs the caller anything.

        Args:
            caller: address closing it (recorded; required to force)
            force:  cut the quarter early — owner only, and stamped ``forced``
                    in the record so nobody mistakes it for the cadence
        """
        state = self._load_pool()
        closed = state.get('quarters') or []
        idx, start, ends_at = self._quarter_window(state)
        now = int(time.time())

        if now < ends_at:
            if not force:
                return {'error': f'Quarter {idx} is not over — {ends_at - now}s '
                                 f'({(ends_at - now) // self.DAY_SECONDS}d) still to run',
                        'ready_in': ends_at - now, 'ends_at': ends_at}
            owner = (self.terms().get('owner') or '').lower()
            if owner and (caller or '').strip().lower() != owner:
                return {'error': 'Only the property owner can cut a quarter short'}

        ledger_from = int(closed[-1]['ledger_to']) if closed else 0
        ledger_to = len(self._load_rent())
        w = self._bloctime_window(start, now)
        amount = self._fees_since(ledger_from)
        if w['total_weight'] <= 0:
            return {'error': 'Nothing was locked this quarter — no bloctime to split'}

        record = {
            'quarter': idx,
            'start': start,
            'end': now,
            'seconds': now - start,
            'ledger_from': ledger_from,
            'ledger_to': ledger_to,
            'pool': round(amount, 8),
            'total_weight': round(w['total_weight'], 6),
            'total_weight_days': round(w['total_weight'] / self.DAY_SECONDS, 6),
            'total_locked': round(w['total_locked'], 8),
            'allocations': [{**p, 'claimed': False, 'claimed_at': 0}
                            for p in self._positions(w, amount)],
            'closed_by': (caller or '').strip(),
            'closed_at': now,
            'forced': bool(now < ends_at),
        }
        state['genesis'] = int(state.get('genesis') or start)
        state.setdefault('quarters', []).append(record)
        self._save_pool(state)
        return {'success': True, 'quarter': record}

    def pool_claim(self, address: str, quarter=None) -> dict:
        """Claim an address's share of one closed quarter, or of all of them.

        Pull, not push — the same shape as the contract, where a payout nobody
        asks for can't strand a distribution.
        """
        address = (address or '').strip()
        if not address:
            return {'error': 'Address required'}
        state = self._load_pool()
        quarters = state.get('quarters') or []
        if not quarters:
            return {'error': 'No quarter has closed yet'}
        if quarter is not None:
            quarter = int(quarter)
            if not any(q['quarter'] == quarter for q in quarters):
                return {'error': f'Quarter {quarter} has not closed'}

        now, claimed, total = int(time.time()), [], 0.0
        for q in quarters:
            if quarter is not None and q['quarter'] != quarter:
                continue
            for a in q.get('allocations', []):
                if a['address'].lower() != address.lower() or a.get('claimed'):
                    continue
                if a['amount'] <= 0:
                    continue
                a['claimed'] = True
                a['claimed_at'] = now
                total += float(a['amount'])
                claimed.append({'quarter': q['quarter'], 'amount': a['amount'],
                                'share_pct': a['share_pct'], 'weight_days': a['weight_days']})
        if not claimed:
            return {'error': f'Nothing to claim for {address}'}
        self._save_pool(state)
        return {'success': True, 'address': address,
                'claimed': round(total, 8), 'quarters': claimed}

    def bloctime(self, address: str) -> dict:
        """One address's locked liquidity and the bloctime it has earned.

        ``this_quarter`` is what they are on track to be paid at the next
        close; ``lifetime`` is every dollar-second since the epoch began,
        which is the number that says who actually carried the protocol.
        """
        address = (address or '').strip()
        if not address:
            return {'error': 'Address required'}
        state = self._load_pool()
        closed = state.get('quarters') or []
        idx, start, ends_at = self._quarter_window(state)
        now = int(time.time())
        genesis = int(state.get('genesis') or self._first_lock() or start)

        live = self._bloctime_window(start, now)
        life = self._bloctime_window(genesis, now)
        ledger_from = int(closed[-1]['ledger_to']) if closed else 0
        amount = self._fees_since(ledger_from)

        def slice_(w, key):
            weight = w['weights'].get(key, 0.0)
            total = w['total_weight']
            return weight, (weight / total if total > 0 else 0.0)

        key = next((a for a in live['weights'] if a.lower() == address.lower()), address)
        w_now, share_now = slice_(live, key)
        key_life = next((a for a in life['weights'] if a.lower() == address.lower()), address)
        w_life, share_life = slice_(life, key_life)

        paid = [{'quarter': q['quarter'], 'amount': a['amount'], 'claimed': a['claimed'],
                 'share_pct': a['share_pct'], 'weight_days': a['weight_days']}
                for q in closed for a in q.get('allocations', [])
                if a['address'].lower() == address.lower()]

        return {
            'address': address,
            'kinds': sorted(live['kinds'].get(key, {}), key=lambda k: -live['kinds'][key][k]),
            'locked': round(live['locked'].get(key, 0.0), 8),
            'this_quarter': {
                'quarter': idx, 'start': start, 'ends_at': ends_at,
                'weight': round(w_now, 6),
                'weight_days': round(w_now / self.DAY_SECONDS, 6),
                'share_pct': round(share_now * 100, 6),
                'projected': round(amount * share_now, 8),
                'pool': round(amount, 8),
            },
            'lifetime': {
                'since': genesis,
                'weight': round(w_life, 6),
                'weight_days': round(w_life / self.DAY_SECONDS, 6),
                'share_pct': round(share_life * 100, 6),
            },
            'earned': round(sum(float(p['amount']) for p in paid), 8),
            'unclaimed': round(sum(float(p['amount']) for p in paid if not p['claimed']), 8),
            'quarters': paid,
        }

    # ━━ The landscape ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _peers_mod(self):
        """Load peers.py by path — `peers` is too common a name to risk on sys.path."""
        if getattr(self, '_peers_cache', None) is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'openhouse_peers', self.module_dir / 'peers.py')
            mod_ = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod_)
            self._peers_cache = mod_
        return self._peers_cache

    def peers(self, refresh: bool = False) -> dict:
        """Every other on-chain housing project, sorted by who ends up owning
        the house. Live numbers come from RealT's keyless community API and
        CoinGecko; see peers.py for what is editorial and what is fetched."""
        return self._peers_mod().peers(self.peers_cache_path, refresh=refresh)

    def compare(self, refresh: bool = False) -> dict:
        """OpenHouse against the field — including where the field is ahead."""
        return self._peers_mod().compare(self.terms(), self.peers_cache_path, refresh=refresh)

    # ━━ Health & Status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def health(self):
        """Service health check."""
        shareholders = self._load_shareholders()
        return {
            'status': 'ok',
            'module': 'openhouse',
            'shareholders': len(shareholders),
            'contract': self.contract_address or 'not deployed',
        }

    def status(self):
        """Aggregate stats: property, shareholders, shares."""
        shareholders = self._load_shareholders()
        props = self._load_properties()
        dividends = self._load_dividends()

        total_shares_sold = sum(int(s.get('shares', 0)) for s in shareholders.values())
        total_contributed = sum(float(s.get('contribution', 0)) for s in shareholders.values())
        total_dividends = sum(float(d.get('total_amount', 0)) for d in dividends)

        prop = props.get('default')
        deployed = bool(prop)
        total_shares = int(prop.get('total_shares', 0)) if deployed else 0

        rent = self.rent_stats()
        pool = self.pool()
        return {
            'deployed': deployed,
            'terms': self.terms(),
            'rent': rent,
            'pool': {k: pool[k] for k in (
                'quarter', 'pool', 'ready', 'ready_in', 'ends_at', 'progress_pct',
                'total_locked', 'total_weight_days', 'zero_fee', 'quarters_closed',
                'distributed', 'unclaimed')},
            'shareholders': len(shareholders),
            'total_shares': total_shares,
            'shares_sold': total_shares_sold,
            'available_shares': max(total_shares - total_shares_sold, 0),
            'total_contributed': total_contributed,
            'total_dividends_distributed': total_dividends,
            'dividend_count': len(dividends),
            'contract': self.contract_address or 'not deployed',
            'is_active': prop.get('is_active', False) if deployed else False,
        }

    # ━━ Property ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def property(self):
        """Get property details."""
        props = self._load_properties()
        prop = props.get('default')
        if not prop:
            # Nothing deployed yet — report honest emptiness, no invented numbers.
            return {
                'deployed': False,
                'description': '',
                'total_shares': 0,
                'share_price': '0',
                'available_shares': 0,
                'is_active': False,
                'status': 'not_deployed',
                'contract': self.contract_address,
            }
        prop = dict(prop)
        prop['deployed'] = True
        shareholders = self._load_shareholders()
        total_sold = sum(int(s.get('shares', 0)) for s in shareholders.values())
        prop['available_shares'] = max(int(prop.get('total_shares', 0)) - total_sold, 0)
        prop['contract'] = self.contract_address
        return prop

    # ━━ Shareholders ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def shareholders(self):
        """Get all shareholders."""
        data = self._load_shareholders()
        total = sum(int(s.get('shares', 0)) for s in data.values())
        result = []
        for addr, info in data.items():
            shares = int(info.get('shares', 0))
            result.append({
                'address': addr,
                'shares': shares,
                'contribution': float(info.get('contribution', 0)),
                'ownership_pct': round((shares / total * 100), 2) if total > 0 else 0,
                'dividends_claimed': float(info.get('dividends_claimed', 0)),
                'joined': info.get('joined', 0),
            })
        return result

    def shareholder(self, address: str):
        """Get info for a specific shareholder."""
        data = self._load_shareholders()
        if address not in data:
            return {'address': address, 'shares': 0, 'contribution': 0, 'ownership_pct': 0}
        total = sum(int(s.get('shares', 0)) for s in data.values())
        info = data[address]
        shares = int(info.get('shares', 0))
        return {
            'address': address,
            'shares': shares,
            'contribution': float(info.get('contribution', 0)),
            'ownership_pct': round((shares / total * 100), 2) if total > 0 else 0,
            'dividends_claimed': float(info.get('dividends_claimed', 0)),
            'joined': info.get('joined', 0),
        }

    def portfolio(self, address: str):
        """Get portfolio summary for an address."""
        sh = self.shareholder(address)
        prop = self.property()
        share_price = float(prop.get('share_price', 0))
        return {
            'address': address,
            'shares': sh['shares'],
            'ownership_pct': sh['ownership_pct'],
            'contribution': sh['contribution'],
            'dividends_claimed': sh.get('dividends_claimed', 0),
            'current_value': sh['shares'] * share_price,
            'property_status': prop.get('status', 'pending'),
        }

    def available_shares(self):
        """Get number of available shares."""
        prop = self.property()
        return {'available': prop.get('available_shares', 0)}

    def share_price(self):
        """Get current share price."""
        prop = self.property()
        return {'share_price': float(prop.get('share_price', 0))}

    def dividends(self):
        """Get dividend distribution history."""
        return self._load_dividends()

    # ━━ Transactions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def purchase(self, buyer: str, share_count: int, payment: float = 0) -> dict:
        """Purchase shares in the property.

        Args:
            buyer: Address of the buyer
            share_count: Number of shares to purchase
            payment: Payment amount (auto-calculated if 0)
        """
        if not buyer:
            return {'error': 'Buyer address required'}
        share_count = int(share_count)
        if share_count <= 0:
            return {'error': 'Must purchase at least 1 share'}

        prop = self.property()
        price = float(prop.get('share_price', 0))
        cost = share_count * price
        available = prop.get('available_shares', 0)

        if share_count > available:
            return {'error': f'Insufficient shares. Available: {available}, Requested: {share_count}'}

        payment = float(payment) if payment else cost
        if payment < cost:
            return {'error': f'Insufficient payment. Required: {cost}, Provided: {payment}'}

        shareholders = self._load_shareholders()
        if buyer in shareholders:
            shareholders[buyer]['shares'] = int(shareholders[buyer].get('shares', 0)) + share_count
            shareholders[buyer]['contribution'] = float(shareholders[buyer].get('contribution', 0)) + cost
        else:
            shareholders[buyer] = {
                'shares': share_count,
                'contribution': cost,
                'dividends_claimed': 0,
                'joined': int(time.time()),
            }
        self._save_shareholders(shareholders)

        refund = payment - cost
        return {
            'success': True,
            'buyer': buyer,
            'shares_purchased': share_count,
            'cost': cost,
            'refund': refund if refund > 0 else 0,
            'new_balance': int(shareholders[buyer]['shares']),
        }

    def distribute(self, total_amount: float) -> dict:
        """Distribute dividends to all shareholders.

        Args:
            total_amount: Total dividend amount to distribute
        """
        total_amount = float(total_amount)
        if total_amount <= 0:
            return {'error': 'Amount must be greater than 0'}

        shareholders = self._load_shareholders()
        total_shares = sum(int(s.get('shares', 0)) for s in shareholders.values())
        if total_shares == 0:
            return {'error': 'No shareholders to distribute to'}

        per_share = total_amount / total_shares
        distributions = []

        for addr, info in shareholders.items():
            shares = int(info.get('shares', 0))
            if shares > 0:
                dividend = per_share * shares
                info['dividends_claimed'] = float(info.get('dividends_claimed', 0)) + dividend
                distributions.append({
                    'address': addr,
                    'shares': shares,
                    'dividend': round(dividend, 6),
                    'ownership_pct': round((shares / total_shares * 100), 2),
                })

        self._save_shareholders(shareholders)

        record = {
            'timestamp': int(time.time()),
            'total_amount': total_amount,
            'per_share': round(per_share, 6),
            'recipients': len(distributions),
        }
        divs = self._load_dividends()
        divs.append(record)
        self._save_dividends(divs)

        return {
            'success': True,
            'total_distributed': total_amount,
            'per_share': round(per_share, 6),
            'distributions': distributions,
        }

    # ━━ Governance ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def record_action(self, action: str, details: str = '') -> dict:
        """Record a property management action (authority only)."""
        return {
            'status': 'recorded',
            'action': action,
            'details': details,
            'timestamp': int(time.time()),
        }

    def transfer_authority(self, new_authority: str) -> dict:
        """Transfer authority to new legal entity."""
        if not new_authority:
            return {'error': 'New authority address required'}
        return {
            'status': 'transferred',
            'new_authority': new_authority,
            'timestamp': int(time.time()),
        }

    def toggle_active(self) -> dict:
        """Toggle contract active status."""
        props = self._load_properties()
        prop = props.get('default', {})
        prop['is_active'] = not prop.get('is_active', True)
        props['default'] = prop
        self._save_properties(props)
        return {'is_active': prop['is_active']}

    def balance(self) -> dict:
        """Get total contract balance (sum of contributions)."""
        shareholders = self._load_shareholders()
        total = sum(float(s.get('contribution', 0)) for s in shareholders.values())
        return {'balance': total}

    # ━━ Contracts ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compile(self):
        """Compile OpenHouse contract via hardhat."""
        contracts_dir = self.module_dir / 'contracts'
        if not contracts_dir.exists():
            return {'error': 'contracts/ directory not found'}

        result = subprocess.run(
            ['npx', 'hardhat', 'compile'],
            cwd=str(self.module_dir),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {'error': result.stderr, 'stdout': result.stdout}
        return {'success': True, 'output': result.stdout}

    def deploy(self, network='testnet', key=None, property_details='',
               total_shares=1000, share_price=0.1, home_price=0,
               monthly_rent=0, model=None, fee_pct=None, owner=None) -> dict:
        """Deploy OpenHouse contract.

        Args:
            network: testnet | mainnet
            key: signing key name
            property_details: description of the property
            total_shares: total number of shares
            share_price: price per share in ETH
            home_price: price to own the home outright
            monthly_rent: scheduled monthly payment
            model: rent-to-own model id (see models())
            fee_pct: protocol take, 0–5
            owner: address that owns the deal terms
        """
        # Save property info locally
        props = self._load_properties()
        props['default'] = {
            'description': property_details,
            'total_shares': int(total_shares),
            'share_price': str(share_price),
            'is_active': True,
            'status': 'active',
            'deployed': int(time.time()),
        }
        self._save_properties(props)

        # Seed the rent-to-own deal alongside the float, if given.
        terms_result = None
        if any(v is not None and v != 0 for v in (home_price, monthly_rent, model, fee_pct, owner)):
            terms_result = self.set_terms(
                model=model, fee_pct=fee_pct, owner=owner,
                home_price=home_price or None, monthly_rent=monthly_rent or None,
            )

        return {
            'success': True,
            'network': network,
            'property_details': property_details,
            'total_shares': int(total_shares),
            'share_price': float(share_price),
            'contract': self.contract_address or 'pending deployment',
            'terms': (terms_result or {}).get('terms') or self.terms(),
        }

    # ━━ Source ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Whitelisted, human-meaningful source files surfaced to the app.
    _SOURCE_FILES = [
        ('contracts/OpenHouse.sol', 'solidity',
         'The on-chain contract: rent credited as principal, the quarterly '
         'BLOCTIME pool, governance.'),
        ('mod.py', 'python',
         'Module logic — shares, dividends, governance, serving.'),
        ('api/api.py', 'python',
         'FastAPI REST surface over the module.'),
        ('api/mcp_server.py', 'python',
         'MCP tool server — the same protocol, driveable by an agent.'),
    ]

    def source(self):
        """Return the real, on-disk source for the contract + backend.

        Reads whitelisted files only — never an arbitrary path.
        """
        out = []
        for rel, lang, desc in self._SOURCE_FILES:
            p = self.module_dir / rel
            if not p.exists():
                continue
            try:
                content = p.read_text()
            except Exception as e:
                content = f'// could not read {rel}: {e}'
            out.append({
                'name': rel,
                'language': lang,
                'description': desc,
                'lines': content.count('\n') + 1,
                'bytes': len(content.encode('utf-8')),
                'content': content,
            })
        return out

    # ━━ Serve / Kill ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def serve(self, port=None, app_port=None, dev=True):
        """Start both the FastAPI API and the Next.js app."""
        return self.serve_app(app_port=app_port, dev=dev)

    def kill(self):
        """Stop both openhouse.api and openhouse.app."""
        return self.kill_app()

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name, '--']
        pm2_cmd.extend(cmd[1:])
        if cwd:
            idx = pm2_cmd.index('--')
            pm2_cmd.insert(idx, cwd)
            pm2_cmd.insert(idx, '--cwd')
        result = subprocess.run(
            pm2_cmd,
            capture_output=True, text=True,
            env={**os.environ, **(env or {})}
        )
        return result.returncode == 0

    def _pm2_kill(self, name):
        result = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        return result.returncode == 0

    def serve_api(self, port=None, reload=True):
        """Start the FastAPI API as openhouse.api PM2 process."""
        port = int(port or self.port)
        name = 'openhouse.api'

        api_dir = self.module_dir / 'api'
        if not (api_dir / 'api.py').exists():
            return {'error': 'api/api.py not found'}

        mod_root = str(self.module_dir.parent.parent.parent)
        env = {
            'PYTHONPATH': f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
            'PORT': str(port),
        }

        cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(port),
            '--app-dir', str(api_dir),
        ]
        if reload:
            cmd.append('--reload')

        self._pm2_start(name, cmd, env=env)
        return {
            'api': f'http://localhost:{port}',
            'pm2': name,
            'docs': f'http://localhost:{port}/docs',
        }

    def kill_api(self):
        """Stop the openhouse.api PM2 process."""
        success = self._pm2_kill('openhouse.api')
        return {'killed': ['openhouse.api'] if success else [], 'success': success}

    def serve_app(self, app_port=None, dev=True):
        """Start openhouse.api and openhouse.app as separate PM2 processes."""
        app_port = int(app_port or self.app_port)
        results = {}

        self.kill_app()

        # Start API
        api_result = self.serve_api(port=self.port, reload=dev)
        results.update(api_result)

        # Start Next.js app
        app_dir = self.module_dir / 'app'
        if (app_dir / 'package.json').exists():
            name = 'openhouse.app'
            env = {
                'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}',
                'PORT': str(app_port),
            }
            cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            self._pm2_start(name, cmd, cwd=str(app_dir), env=env)
            results['app'] = f'http://localhost:{app_port}'
            results['pm2_app'] = name
        else:
            results['app'] = None

        results['dev'] = dev

        # Register with the mod protocol so the gateway routes
        # modc2.com/openhouse → this app (and /openhouse/api → this API).
        results['registration'] = self.register(
            app_url=f'http://localhost:{app_port}',
            api_url=f'http://localhost:{self.port}',
            owner=os.environ.get('OPENHOUSE_OWNER', ''),
        )

        return results

    # ━━ Mod-protocol registration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com'):
        """Register openhouse with the mod gateway.

        Uses the ``server.namespace`` registry the same way every other
        module does, so the gateway routes ``/openhouse`` → this app and
        ``/openhouse/api`` → this API. Returns the public URL on success.

        Args:
            app_url:  loopback URL the gateway proxies for the Next.js app
            api_url:  loopback URL the gateway proxies for the FastAPI API
            owner:    wallet address that owns this app registration
            gateway:  public gateway base (default https://modc2.com)
        """
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('openhouse', app_url)
            ns.reg_app('openhouse', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/openhouse"
            print(f"openhouse registered → {public}  (app: {app_url}, api: {api_url})")
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f"openhouse: gateway registration failed: {e}")
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self):
        """Remove openhouse from the mod gateway registry."""
        try:
            ns = m.mod('server.namespace')()
            ns.dereg_app('openhouse')
            return {'ok': True, 'deregistered': 'openhouse'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def kill_app(self):
        """Stop openhouse.api and openhouse.app PM2 processes."""
        killed = []
        if self._pm2_kill('openhouse.api'):
            killed.append('openhouse.api')
        if self._pm2_kill('openhouse.app'):
            killed.append('openhouse.app')
        return {'killed': killed}

    def forward(self, action=None, **kwargs):
        """CLI entry point: openhouse <action> [args]

        Actions:
            status             - Aggregate stats
            health             - Service health check
            models             - Rent-to-own models + fee band + benchmarks
            terms              - The live deal (fee, credit, model)
            set_terms          - Owner sets the deal (model=, fee_pct=, credit_pct=,
                                 option_fee_pct=, home_price=, monthly_rent=, owner=)
            claim_owner        - Claim the owner seat while empty (address=)
            quote              - Preview a payment split (amount=, kind=)
            pay_rent           - Record a payment (renter=, amount=, kind=)
            rent_ledger        - Payment history (renter=)
            equity             - A renter's stake (address=)
            rent_stats         - Where the rent went
            pool               - The quarter now accruing + who the pool is owed to
            pool_history       - Every closed quarter, newest first
            close_quarter      - Freeze the quarter and split it by bloctime (caller=, force=)
            pool_claim         - Claim a share of a closed quarter (address=, quarter=)
            bloctime           - One address's locked liquidity + dollar-days (address=)
            peers              - Other on-chain housing projects (refresh=)
            compare            - OpenHouse against the field (refresh=)
            property           - Property details
            shareholders       - All shareholders
            shareholder        - Shareholder info (address=)
            portfolio          - Portfolio summary (address=)
            available_shares   - Available shares
            share_price        - Current share price
            dividends          - Dividend history
            purchase           - Buy shares (buyer=, share_count=, payment=)
            distribute         - Distribute dividends (total_amount=)
            record_action      - Record management action (action=, details=)
            transfer_authority - Transfer authority (new_authority=)
            toggle_active      - Toggle active status
            balance            - Contract balance
            compile            - Compile contracts
            deploy             - Deploy contract (network=, key=, property_details=, total_shares=, share_price=)
            serve              - Start API + App
            kill               - Stop API + App
            serve_api          - Start API only
            kill_api           - Stop API only
            serve_app          - Start API + App
            kill_app           - Stop API + App
        """
        actions = {
            'status': lambda: self.status(),
            'health': lambda: self.health(),
            'models': lambda: self.models(),
            'terms': lambda: self.terms(),
            'set_terms': lambda: self.set_terms(
                model=kwargs.get('model'),
                fee_pct=kwargs.get('fee_pct'),
                credit_pct=kwargs.get('credit_pct'),
                option_fee_pct=kwargs.get('option_fee_pct'),
                home_price=kwargs.get('home_price'),
                monthly_rent=kwargs.get('monthly_rent'),
                owner=kwargs.get('owner'),
                treasury=kwargs.get('treasury'),
            ),
            'claim_owner': lambda: self.claim_owner(kwargs.get('address', '')),
            'quote': lambda: self.quote(
                float(kwargs.get('amount', 0)),
                kind=kwargs.get('kind', 'rent'),
            ),
            'pay_rent': lambda: self.pay_rent(
                kwargs.get('renter', ''),
                float(kwargs.get('amount', 0)),
                kind=kwargs.get('kind', 'rent'),
            ),
            'rent_ledger': lambda: self.rent_ledger(kwargs.get('renter', '')),
            'equity': lambda: self.equity(kwargs.get('address', '')),
            'rent_stats': lambda: self.rent_stats(),
            'pool': lambda: self.pool(),
            'pool_history': lambda: self.pool_history(),
            'close_quarter': lambda: self.close_quarter(
                caller=kwargs.get('caller', kwargs.get('address', '')),
                force=bool(kwargs.get('force')),
            ),
            'pool_claim': lambda: self.pool_claim(
                kwargs.get('address', ''),
                quarter=kwargs.get('quarter'),
            ),
            'bloctime': lambda: self.bloctime(kwargs.get('address', '')),
            'peers': lambda: self.peers(refresh=bool(kwargs.get('refresh'))),
            'compare': lambda: self.compare(refresh=bool(kwargs.get('refresh'))),
            'property': lambda: self.property(),
            'shareholders': lambda: self.shareholders(),
            'shareholder': lambda: self.shareholder(kwargs.get('address', '')),
            'portfolio': lambda: self.portfolio(kwargs.get('address', '')),
            'available_shares': lambda: self.available_shares(),
            'share_price': lambda: self.share_price(),
            'dividends': lambda: self.dividends(),
            'purchase': lambda: self.purchase(
                kwargs.get('buyer', ''),
                int(kwargs.get('share_count', 0)),
                float(kwargs.get('payment', 0)),
            ),
            'distribute': lambda: self.distribute(float(kwargs.get('total_amount', 0))),
            'record_action': lambda: self.record_action(
                kwargs.get('action', ''),
                kwargs.get('details', ''),
            ),
            'transfer_authority': lambda: self.transfer_authority(kwargs.get('new_authority', '')),
            'toggle_active': lambda: self.toggle_active(),
            'balance': lambda: self.balance(),
            'source': lambda: self.source(),
            'compile': lambda: self.compile(),
            'deploy': lambda: self.deploy(
                network=kwargs.get('network', 'testnet'),
                key=kwargs.get('key'),
                property_details=kwargs.get('property_details', ''),
                total_shares=int(kwargs.get('total_shares', 1000)),
                share_price=float(kwargs.get('share_price', 0.1)),
                home_price=float(kwargs.get('home_price', 0)),
                monthly_rent=float(kwargs.get('monthly_rent', 0)),
                model=kwargs.get('model'),
                fee_pct=kwargs.get('fee_pct'),
                owner=kwargs.get('owner'),
            ),
            'serve': lambda: self.serve(
                port=kwargs.get('port'),
                app_port=kwargs.get('app_port'),
                dev=kwargs.get('dev', True),
            ),
            'kill': lambda: self.kill(),
            'serve_api': lambda: self.serve_api(
                port=kwargs.get('port'),
                reload=kwargs.get('reload', True),
            ),
            'kill_api': lambda: self.kill_api(),
            'serve_app': lambda: self.serve_app(
                app_port=kwargs.get('app_port'),
                dev=kwargs.get('dev', True),
            ),
            'kill_app': lambda: self.kill_app(),
            'register': lambda: self.register(
                app_url=kwargs.get('app_url'),
                api_url=kwargs.get('api_url'),
                owner=kwargs.get('owner'),
                gateway=kwargs.get('gateway', 'https://modc2.com'),
            ),
            'deregister': lambda: self.deregister(),
        }

        if not action or action not in actions:
            return {
                'module': 'openhouse',
                'description': self.description,
                'actions': list(actions.keys()),
                'status': self.status(),
            }

        return actions[action]()
