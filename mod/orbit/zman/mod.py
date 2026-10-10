"""
zman — New York buys its buildings back, one bond at a time.

A city acquisition program in the shape of Mayor Zohran Mamdani's housing plan
("Block by Block", May 2026: 200,000 new and 200,000 preserved rent-stabilized
homes, financed by municipal bonds, with buildings moved into non-profit
hands), built on the openhouse framework rather than beside it.

zman adds only what openhouse doesn't have — the money coming IN:

  1. Issue a bond      — issue_bond(series, principal, coupon_pct, term_years, key)
  2. Investors buy in  — subscribe(series, investor, amount)
  3. Buy a building    — acquire(name, borough, units, price, monthly_rent, series, key)
                         → spends bond proceeds, opens an openhouse property with
                           the CITY in both seats (owner + civic authority, fee 0)
  4. Tenants pay rent  — pay_rent(property, tenant, amount)
                         → openhouse splits it: tenant equity / owner income;
                           the owner income is the bond's debt service
  5. Pay bondholders   — pay_coupon(series, key) → pro-rata to subscribers
  6. Watch coverage    — coverage(series): rent collected vs debt due (DSCR)

Everything a building does after acquisition — terms, rent ledger, equity,
civic pause/hold, bank reconcile — is plain openhouse, in its own store
under ~/.zman/buildings/<id>/. That is the "city-owned rent-to-own" recipe
from openhouse's civic layer: the city takes the bank seat and charters
itself as authority. zman is the bond desk bolted to the front of it.

The rent freeze is a program-level switch (on by default): while it stands,
no building's monthly rent can go up through zman.

Testnet bookkeeping only. No bond is issued, no deed moves, no money is real.
Key checks are address matches, not signatures — same as openhouse.

Storage: ~/.zman/{program,bonds,buildings}.json + ~/.zman/buildings/<id>/
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import mod as m


class Mod:
    description = ("Mamdani-style NYC housing acquisition on openhouse — municipal bonds "
                   "buy buildings, the city holds both seats, rent services the bonds and "
                   "builds tenant equity, and the rent freeze is enforced in code.")

    # ── The plan, as published ─────────────────────────────────────
    # Editorial facts, kept apart from anything computed. Each one carries its
    # source so the claim can be checked rather than trusted.
    PLAN = {
        'name': 'Block by Block: The Housing Plan for a New Era',
        'released': '2026-05',
        'mayor': 'Zohran Mamdani',
        'targets': {
            'build_homes': 200_000,
            'preserve_homes': 200_000,
            'five_year_capital_usd': 22_000_000_000,
            'decade_public_investment_usd': 100_000_000_000,
        },
        'financing': 'municipal bonds plus higher taxes on corporations and top earners '
                     '(the tax half needs state approval)',
        'acquisition': 'move distressed buildings to non-profit community partners, '
                       'stabilized as permanently affordable',
        'sources': [
            'https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n',
            'https://www.cityandstateny.com/policy/2026/05/5-things-know-about-mamdanis-big-housing-plan/413771/',
            'https://abc7ny.com/post/nyc-mayor-zohran-mamdani-housing-plan-allow-time-rent-hikes-vacant-units-despite-possible-freeze/19173941/',
        ],
    }

    BOROUGHS = ('manhattan', 'brooklyn', 'queens', 'bronx', 'staten_island')

    # Default split for an acquired building. Half of every rent dollar builds
    # the tenants' stake in their building, half services the bonds that bought
    # it. Tune per building with credit_pct=; openhouse's presets also work.
    DEFAULT_MODEL = 'hybrid'
    DEFAULT_CITY = {
        'name': 'NYC Department of Housing Preservation and Development',
        'region': 'US-NY',
        'uri': 'https://www.nyc.gov/site/hpd/',
    }

    def __init__(self, config=None, store=None):
        """store: put the whole program somewhere other than ~/.zman (tests)."""
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(store) if store else Path(os.path.expanduser('~/.zman'))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.program_path = self.store_dir / 'program.json'
        self.bonds_path = self.store_dir / 'bonds.json'
        self.buildings_path = self.store_dir / 'buildings.json'
        self.buildings_dir = self.store_dir / 'buildings'
        self._houses = {}

    def _load_config(self):
        p = self.module_dir / 'config.json'
        return json.loads(p.read_text()) if p.exists() else {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load(self, path, default):
        try:
            return json.loads(Path(path).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _save(self, path, data):
        Path(path).write_text(json.dumps(data, indent=2))

    def _program(self):
        return {'city': None, 'rent_freeze': True, 'log': [],
                **self._load(self.program_path, {})}

    def _bonds(self) -> dict:
        return self._load(self.bonds_path, {})

    def _buildings(self) -> dict:
        return self._load(self.buildings_path, {})

    def _log(self, program, action, **details):
        program.setdefault('log', []).append(
            {'timestamp': int(time.time()), 'action': action, **details})

    # ━━ openhouse, one store per building ━━━━━━━━━━━━━━━━━━━━━━━━━

    def _openhouse_cls(self):
        """The framework's resolver first; by path if the fleet isn't wired up."""
        if getattr(self, '_oh_cls', None) is None:
            try:
                self._oh_cls = m.mod('openhouse')
            except Exception:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    'zman_openhouse', self.module_dir.parent / 'openhouse' / 'mod.py')
                mod_ = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod_)
                self._oh_cls = mod_.Mod
        return self._oh_cls

    def house(self, building: str):
        """The openhouse instance for one building — its own terms, ledger and
        civic seat, in its own directory. Callers can use the full openhouse
        surface on it (equity, rent_ledger, bank_reconcile, ...)."""
        if building not in self._houses:
            self._houses[building] = self._openhouse_cls()(
                store=self.buildings_dir / building)
        return self._houses[building]

    # ━━ The city seat ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def claim_city(self, key: str, name: str = '', region: str = '', uri: str = '') -> dict:
        """Seat the city's key — first writer wins, like openhouse's owner seat.
        Every write after this (bonds, acquisitions, coupons, the freeze) must
        present the same key."""
        if not key:
            return {'error': 'City key required'}
        p = self._program()
        if p.get('city'):
            return {'error': 'City seat already held', 'city': p['city']}
        p['city'] = {**self.DEFAULT_CITY, 'key': key, 'claimed': int(time.time()),
                     **{k: v for k, v in (('name', name), ('region', region), ('uri', uri)) if v}}
        self._log(p, 'claim_city', key=key)
        self._save(self.program_path, p)
        return {'success': True, 'city': p['city']}

    def _gate(self, key) -> Optional[dict]:
        city = self._program().get('city')
        if not city:
            return {'error': 'No city seated — claim_city(key=) first'}
        if (key or '').lower() != city['key'].lower():
            return {'error': 'Only the city can do that'}
        return None

    def set_freeze(self, on: bool, key: str) -> dict:
        """Turn the program-wide rent freeze on or off."""
        blocked = self._gate(key)
        if blocked:
            return blocked
        p = self._program()
        p['rent_freeze'] = bool(on)
        self._log(p, 'freeze' if on else 'unfreeze')
        self._save(self.program_path, p)
        return {'success': True, 'rent_freeze': p['rent_freeze']}

    # ━━ Bonds ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def annual_debt_service(principal: float, coupon_pct: float, term_years: int) -> float:
        """Level annual payment that retires `principal` over `term_years` at
        `coupon_pct` — the standard serial-bond / mortgage annuity."""
        r = float(coupon_pct) / 100.0
        n = int(term_years)
        if n <= 0:
            return 0.0
        if r == 0:
            return principal / n
        return principal * r / (1 - (1 + r) ** -n)

    def issue_bond(self, series: str, principal: float, coupon_pct: float,
                   term_years: int, key: str, issuer: str = 'NYC Housing Development Corp.',
                   purpose: str = 'acquisition') -> dict:
        """The city authorizes a bond series. Proceeds only exist once
        investors subscribe; acquisitions spend subscribed proceeds only."""
        blocked = self._gate(key)
        if blocked:
            return blocked
        series = (series or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', series):
            return {'error': 'Series id: 1-40 chars of letters, digits, . _ -'}
        principal, coupon_pct, term_years = float(principal), float(coupon_pct), int(term_years)
        if principal <= 0:
            return {'error': 'Principal must be greater than 0'}
        if not 0 <= coupon_pct <= 20:
            return {'error': 'Coupon must be between 0% and 20%'}
        if not 1 <= term_years <= 50:
            return {'error': 'Term must be 1-50 years'}
        bonds = self._bonds()
        if series in bonds:
            return {'error': f'Series {series} already exists'}
        bonds[series] = {
            'series': series, 'issuer': issuer, 'purpose': purpose,
            'principal': principal, 'coupon_pct': coupon_pct, 'term_years': term_years,
            'annual_debt_service': round(self.annual_debt_service(principal, coupon_pct, term_years), 2),
            'issued': int(time.time()),
            'holders': {},        # investor -> amount subscribed
            'spent': 0.0,         # proceeds deployed into buildings
            'collected': 0.0,     # debt-service cash swept from building rent
            'paid': 0.0,          # cash handed to holders
            'payments': [],
        }
        self._save(self.bonds_path, bonds)
        p = self._program()
        self._log(p, 'issue_bond', series=series, principal=principal)
        self._save(self.program_path, p)
        return {'success': True, 'bond': self.bond(series)}

    def subscribe(self, series: str, investor: str, amount: float) -> dict:
        """An investor buys into a series — capped at what's left unsold."""
        if not investor:
            return {'error': 'Investor address required'}
        amount = float(amount)
        if amount <= 0:
            return {'error': 'Amount must be greater than 0'}
        bonds = self._bonds()
        b = bonds.get(series)
        if not b:
            return {'error': f'Unknown series: {series}'}
        sold = sum(b['holders'].values())
        room = b['principal'] - sold
        if amount > room + 1e-9:
            return {'error': f'Only {room:.2f} of series {series} left unsold'}
        b['holders'][investor] = round(b['holders'].get(investor, 0.0) + amount, 8)
        self._save(self.bonds_path, bonds)
        return {'success': True, 'series': series, 'investor': investor,
                'holding': b['holders'][investor], 'bond': self.bond(series)}

    def bond(self, series: str) -> dict:
        b = self._bonds().get(series)
        if not b:
            return {'error': f'Unknown series: {series}'}
        sold = sum(b['holders'].values())
        return {**b, 'sold': round(sold, 8),
                'available': round(sold - b['spent'], 8),
                'unsold': round(b['principal'] - sold, 8),
                'owed_to_holders': round(b['collected'] - b['paid'], 8),
                'coverage': self.coverage(series)}

    def bonds(self) -> list:
        return [self.bond(s) for s in self._bonds()]

    def holder(self, investor: str) -> dict:
        """One investor across every series: principal held and coupons received."""
        out, principal, received = [], 0.0, 0.0
        inv = (investor or '').lower()
        for s, b in self._bonds().items():
            held = sum(v for k, v in b['holders'].items() if k.lower() == inv)
            if not held:
                continue
            got = sum(pay['to'].get(k, 0.0) for pay in b['payments']
                      for k in pay['to'] if k.lower() == inv)
            principal += held
            received += got
            out.append({'series': s, 'held': round(held, 8), 'received': round(got, 8),
                        'coupon_pct': b['coupon_pct']})
        return {'investor': investor, 'principal': round(principal, 8),
                'received': round(received, 8), 'series': out}

    # ━━ Acquisition ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _building_id(name: str, borough: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', f'{borough} {name}'.lower()).strip('-')[:40]
        return f"{slug}-{hashlib.sha256(f'{name}|{borough}'.encode()).hexdigest()[:6]}"

    def acquire(self, name: str, borough: str, units: int, price: float,
                monthly_rent: float, series: str, key: str,
                partner: str = '', model: str = DEFAULT_MODEL,
                credit_pct: Optional[float] = None, bbl: str = '') -> dict:
        """Spend bond proceeds on a building and open it as an openhouse property.

        Args:
            name:         street address or building name
            borough:      manhattan | brooklyn | queens | bronx | staten_island
            units:        apartments in the building
            price:        acquisition price (becomes openhouse home_price — what
                          the tenants collectively own once their credit reaches it)
            monthly_rent: scheduled rent roll for the whole building, per month
            series:       which bond series pays for it
            key:          the city key
            partner:      non-profit / community land trust that manages it
            model:        openhouse rent-to-own preset (default hybrid 50/50)
            credit_pct:   override the preset's tenant-equity share
            bbl:          NYC borough-block-lot, if known
        """
        blocked = self._gate(key)
        if blocked:
            return blocked
        borough = (borough or '').lower().replace(' ', '_')
        if borough not in self.BOROUGHS:
            return {'error': f"Borough must be one of: {', '.join(self.BOROUGHS)}"}
        if not name:
            return {'error': 'Building name or address required'}
        price, monthly_rent, units = float(price), float(monthly_rent), int(units)
        if price <= 0 or monthly_rent <= 0 or units <= 0:
            return {'error': 'price, monthly_rent and units must all be greater than 0'}
        b = self.bond(series)
        if 'error' in b:
            return b
        if price > b['available'] + 1e-9:
            return {'error': f"Series {series} has {b['available']:.2f} of subscribed "
                             f"proceeds available — {price:.2f} needed"}
        bid = self._building_id(name, borough)
        buildings = self._buildings()
        if bid in buildings:
            return {'error': 'Building already in the program', 'building': bid}

        city = self._program()['city']
        oh = self.house(bid)
        t = oh.set_terms(model=model, fee_pct=0.0, home_price=price,
                         monthly_rent=monthly_rent, owner=city['key'],
                         **({'credit_pct': credit_pct} if credit_pct is not None else {}))
        if 'error' in t:
            return t
        oh.civic_charter(key=city['key'], name=city['name'], region=city['region'],
                         uri=city['uri'], owner=city['key'])

        bonds = self._bonds()
        bonds[series]['spent'] = round(bonds[series]['spent'] + price, 8)
        self._save(self.bonds_path, bonds)
        buildings[bid] = {'id': bid, 'name': name, 'borough': borough, 'units': units,
                          'price': price, 'series': series, 'partner': partner, 'bbl': bbl,
                          'acquired': int(time.time()), 'swept': 0.0}
        self._save(self.buildings_path, buildings)
        p = self._program()
        self._log(p, 'acquire', building=bid, series=series, price=price)
        self._save(self.program_path, p)
        return {'success': True, 'building': self.building(bid)}

    def building(self, building: str) -> dict:
        rec = self._buildings().get(building)
        if not rec:
            return {'error': f'Unknown building: {building}'}
        oh = self.house(building)
        return {**rec, 'terms': oh.terms(), 'rent': oh.rent_stats(), 'civic': oh.civic()}

    def buildings(self, borough: str = '') -> list:
        return [self.building(b) for b, r in self._buildings().items()
                if not borough or r['borough'] == borough.lower()]

    # ━━ Rent ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_rent(self, building: str, monthly_rent: float, key: str) -> dict:
        """Change a building's rent roll. Refused upward while the freeze stands."""
        blocked = self._gate(key)
        if blocked:
            return blocked
        if building not in self._buildings():
            return {'error': f'Unknown building: {building}'}
        oh = self.house(building)
        current = float(oh.terms()['monthly_rent'])
        if self._program()['rent_freeze'] and float(monthly_rent) > current:
            return {'error': f'Rent freeze: {current:.2f} cannot go up while the freeze stands'}
        return oh.set_terms(monthly_rent=monthly_rent, owner=key)

    def pay_rent(self, building: str, tenant: str, amount: float,
                 source: Optional[dict] = None) -> dict:
        """A tenant pays. openhouse splits it (civic pause honored); the owner
        income half is swept into the building's bond series as debt service."""
        rec = self._buildings().get(building)
        if not rec:
            return {'error': f'Unknown building: {building}'}
        r = self.house(building).pay_rent(tenant, amount, source=source)
        if 'error' in r:
            return r
        sweep = float(r['owner_income'])
        bonds = self._bonds()
        bonds[rec['series']]['collected'] = round(bonds[rec['series']]['collected'] + sweep, 8)
        self._save(self.bonds_path, bonds)
        buildings = self._buildings()
        buildings[building]['swept'] = round(buildings[building]['swept'] + sweep, 8)
        self._save(self.buildings_path, buildings)
        return {**r, 'debt_service': sweep, 'series': rec['series']}

    # ━━ Paying bondholders ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def pay_coupon(self, series: str, key: str) -> dict:
        """Hand every collected-but-unpaid debt-service dollar to the series'
        holders, pro-rata by what they subscribed."""
        blocked = self._gate(key)
        if blocked:
            return blocked
        bonds = self._bonds()
        b = bonds.get(series)
        if not b:
            return {'error': f'Unknown series: {series}'}
        pot = round(b['collected'] - b['paid'], 8)
        sold = sum(b['holders'].values())
        if pot <= 0 or sold <= 0:
            return {'error': 'Nothing to pay', 'owed': pot}
        to = {k: round(pot * v / sold, 8) for k, v in b['holders'].items()}
        b['paid'] = round(b['paid'] + sum(to.values()), 8)
        b['payments'].append({'timestamp': int(time.time()), 'amount': pot, 'to': to})
        self._save(self.bonds_path, bonds)
        return {'success': True, 'series': series, 'amount': pot, 'to': to}

    def coverage(self, series: str) -> dict:
        """Debt-service coverage: the rent roll's bond share vs what the bond
        needs per year. DSCR >= 1 means the buildings carry their own debt."""
        b = self._bonds().get(series)
        if not b:
            return {'error': f'Unknown series: {series}'}
        # Debt is sized on what was actually sold, not what was authorized.
        sold = sum(b['holders'].values())
        need = self.annual_debt_service(sold, b['coupon_pct'], b['term_years'])
        scheduled = 0.0
        for bid, rec in self._buildings().items():
            if rec['series'] != series:
                continue
            t = self.house(bid).terms()
            scheduled += float(t['monthly_rent']) * 12 * float(t['owner_pct_of_rent']) / 100.0
        return {'annual_debt_service': round(need, 2),
                'annual_scheduled_to_bonds': round(scheduled, 2),
                'dscr': round(scheduled / need, 4) if need > 0 else None,
                'collected_to_date': round(b['collected'], 2)}

    # ━━ Views ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def plan(self) -> dict:
        """The published plan, and how far this program has got against it."""
        bl = self._buildings()
        homes = sum(r['units'] for r in bl.values())
        capital = sum(r['price'] for r in bl.values())
        t = self.PLAN['targets']
        return {**self.PLAN, 'progress': {
            'homes_acquired': homes,
            'preserve_pct': round(homes / t['preserve_homes'] * 100, 6),
            'capital_deployed_usd': round(capital, 2),
            'five_year_capital_pct': round(capital / t['five_year_capital_usd'] * 100, 6),
        }}

    def status(self) -> dict:
        p = self._program()
        bonds = self.bonds()
        bl = self._buildings()
        stats = [self.house(b).rent_stats() for b in bl]
        return {
            'network': 'testnet (bookkeeping only)',
            'city': p.get('city'),
            'rent_freeze': p['rent_freeze'],
            'series': len(bonds),
            'bonds_sold': round(sum(b['sold'] for b in bonds), 2),
            'proceeds_available': round(sum(b['available'] for b in bonds), 2),
            'buildings': len(bl),
            'homes': sum(r['units'] for r in bl.values()),
            'by_borough': {bo: sum(r['units'] for r in bl.values() if r['borough'] == bo)
                           for bo in self.BOROUGHS},
            'rent_collected': round(sum(s['gross_rent'] for s in stats), 2),
            'tenant_equity': round(sum(s['renter_equity'] for s in stats), 2),
            'to_bondholders': round(sum(b['paid'] for b in bonds), 2),
        }

    def log(self, limit: int = 50) -> list:
        return list(reversed(self._program().get('log', [])))[:int(limit)]

    def health(self) -> dict:
        try:
            self._openhouse_cls()
            ok = True
        except Exception as e:
            return {'status': 'degraded', 'openhouse': str(e)}
        return {'status': 'ok', 'openhouse': ok, 'store': str(self.store_dir)}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def forward(self, action=None, **kwargs):
        """CLI entry point: zman <action> [args]

        Actions:
            plan        - The published Mamdani plan + progress against it
            status      - Program totals
            health      - Can we reach openhouse
            claim_city  - Seat the city key (key=, name=, region=, uri=)
            set_freeze  - Rent freeze on/off (on=, key=)
            issue_bond  - Authorize a series (series=, principal=, coupon_pct=, term_years=, key=)
            subscribe   - Buy into a series (series=, investor=, amount=)
            bonds       - Every series
            bond        - One series (series=)
            holder      - One investor's holdings (investor=)
            acquire     - Buy a building with proceeds (name=, borough=, units=, price=,
                          monthly_rent=, series=, key=, partner=, model=, credit_pct=, bbl=)
            buildings   - Every building (borough=)
            building    - One building, with its openhouse terms/rent/civic (building=)
            set_rent    - Change a rent roll; no increases under freeze (building=, monthly_rent=, key=)
            pay_rent    - A tenant pays (building=, tenant=, amount=)
            pay_coupon  - Pay collected debt service to holders (series=, key=)
            coverage    - DSCR for a series (series=)
            log         - Program actions, newest first (limit=)
        """
        public = {'plan', 'status', 'health', 'claim_city', 'set_freeze', 'issue_bond',
                  'subscribe', 'bonds', 'bond', 'holder', 'acquire', 'buildings', 'building',
                  'set_rent', 'pay_rent', 'pay_coupon', 'coverage', 'log'}
        if action is None:
            return self.status()
        if action not in public:
            return {'error': f'Unknown action: {action}', 'actions': sorted(public)}
        return getattr(self, action)(**kwargs)
