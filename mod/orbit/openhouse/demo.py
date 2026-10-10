"""
demo.py — a fake, fully-labelled example scenario for the testnet store.

``seed(mod)`` populates ``~/.openhouse`` through the SAME code paths a real
deployment uses — deploy / set_terms / pay_rent / purchase / distribute —
then backdates the timestamps, so sixty days of history exist, the equity
math is exactly what the real splitter produces, and the bloctime pool has
weight without a single hand-written number in the ledger.

``unseed(mod)`` removes exactly what seed wrote, and refuses to touch a
store it didn't write.

Everything is deterministic: same cast, same amounts, same relative dates,
so tests, screenshots and demos agree run to run. Nothing here goes near a
chain or real money — the property is fictional, the addresses are
hash-derived vanity hex, and the property record carries ``demo: true`` so
every surface can say so.
"""
import hashlib
import time

DAY = 24 * 3600

# ── The cast ──────────────────────────────────────────────────────
# Addresses are derived, not real: sha256 of a name, dressed as hex.
# Deterministic, so the same person has the same address every seeding.


def addr(name: str) -> str:
    return '0x' + hashlib.sha256(f'openhouse-demo:{name}'.encode()).hexdigest()[:40]


CAST = {
    'owner':    addr('owner'),      # holds the deed, set the terms
    'treasury': addr('treasury'),   # protocol fee sink
    'maya':     addr('maya'),       # the renter-buyer: option fee + monthly rent
    'jonah':    addr('jonah'),      # housemate, pays a share of the rent
    'ada':      addr('ada'),        # earliest shareholder in the float
    'felix':    addr('felix'),      # mid-quarter shareholder
    'ivy':      addr('ivy'),        # recent shareholder
}

# ── The scenario ──────────────────────────────────────────────────

PROPERTY = ('DEMO — 128 Maple Ave, Cleveland OH · 3-bed brick rowhouse. '
            'Fictional property, fake addresses, testnet bookkeeping only.')
HOME_PRICE = 120.0        # Ξ to own outright
MONTHLY_RENT = 0.62       # Ξ, the scheduled payment
MODEL = 'full_credit'
FEE_PCT = 2.5
TOTAL_SHARES = 1000
SHARE_PRICE = 0.1

# (renter, amount, kind, days_ago) — chronological, oldest first.
PAYMENTS = [
    ('maya',  3.60, 'option', 60),   # 3% option fee on the price, all equity
    ('maya',  0.62, 'rent',   59),
    ('jonah', 0.62, 'rent',   55),
    ('maya',  0.62, 'rent',   29),
    ('jonah', 0.62, 'rent',   25),
    ('maya',  0.62, 'rent',    1),
]

# (buyer, shares, days_ago) — chronological, oldest first.
PURCHASES = [
    ('ada',   120, 55),
    ('felix',  80, 40),
    ('ivy',    40, 12),
]

DIVIDEND = (1.5, 20)      # (amount, days_ago) — one distribution to the float

_DATA_ATTRS = ('shareholders_path', 'properties_path', 'dividends_path',
               'terms_path', 'rent_path', 'pool_path', 'civic_path')


def _marker(mod):
    return mod.store_dir / 'demo.json'


def _has_data(mod):
    """Is there anything in the store a wipe would destroy?"""
    return any(getattr(mod, a).exists() for a in _DATA_ATTRS)


def seed(mod, force: bool = False) -> dict:
    """Populate the store with the demo scenario. Refuses to overwrite a
    store it didn't seed unless ``force=True`` — real bookkeeping, however
    testnet, is not something a demo should eat silently."""
    if _has_data(mod) and not _marker(mod).exists() and not force:
        return {'error': 'Store has data this demo did not write — '
                         'pass force=True to overwrite it, or unseed first'}

    for a in _DATA_ATTRS:
        p = getattr(mod, a)
        if p.exists():
            p.unlink()

    now = int(time.time())

    # Every write below goes through the real code path; only the
    # timestamps are rewritten afterwards, because history can't be
    # asked of a clock that only knows about now.
    steps = [
        mod.deploy(network='testnet', property_details=PROPERTY,
                   total_shares=TOTAL_SHARES, share_price=SHARE_PRICE),
        mod.set_terms(model=MODEL, fee_pct=FEE_PCT,
                      home_price=HOME_PRICE, monthly_rent=MONTHLY_RENT,
                      owner=CAST['owner'], treasury=CAST['treasury']),
    ]
    steps += [mod.pay_rent(CAST[who], amount, kind=kind)
              for who, amount, kind, _ in PAYMENTS]
    steps += [mod.purchase(CAST[who], shares) for who, shares, _ in PURCHASES]
    steps.append(mod.distribute(DIVIDEND[0]))
    for s in steps:
        if 'error' in s:
            return {'error': f'seed step failed: {s["error"]}'}

    # Backdate: rent entries are in call order, so they pair 1:1 with
    # PAYMENTS; shareholders and the dividend record are keyed directly.
    ledger = mod._load_rent()
    for entry, (_, _, _, days_ago) in zip(ledger, PAYMENTS):
        entry['timestamp'] = now - days_ago * DAY
    mod._save_rent(ledger)

    holders = mod._load_shareholders()
    for who, _, days_ago in PURCHASES:
        holders[CAST[who]]['joined'] = now - days_ago * DAY
    mod._save_shareholders(holders)

    divs = mod._load_dividends()
    divs[-1]['timestamp'] = now - DIVIDEND[1] * DAY
    mod._save_dividends(divs)

    props = mod._load_properties()
    props['default']['demo'] = True
    props['default']['deployed'] = now - PAYMENTS[0][3] * DAY
    mod._save_properties(props)

    cast = {name: a for name, a in CAST.items()}
    mod._save_json(_marker(mod), {
        'seeded': now,
        'cast': cast,
        'payments': len(PAYMENTS),
        'purchases': len(PURCHASES),
        'note': 'Fake example data. unseed() removes all of it.',
    })

    return {
        'success': True,
        'demo': True,
        'property': PROPERTY,
        'cast': cast,
        'rent': mod.rent_stats(),
        'shareholders': len(holders),
        'pool': {k: mod.pool()[k] for k in
                 ('quarter', 'pool', 'progress_pct', 'total_locked')},
        'note': 'Fake example data on the testnet store — unseed() removes it.',
    }


def unseed(mod) -> dict:
    """Remove the demo scenario — and only the demo scenario."""
    if not _marker(mod).exists():
        return {'error': 'Store was not seeded by this demo — nothing removed'}
    removed = []
    for a in _DATA_ATTRS:
        p = getattr(mod, a)
        if p.exists():
            p.unlink()
            removed.append(p.name)
    _marker(mod).unlink()
    removed.append('demo.json')
    return {'success': True, 'removed': removed}
