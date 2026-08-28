"""Whitelisted charity registry.

The registry is the consensus surface of the subnet: validators only credit
donations sent to a verified charity's address. Curation is the subnet
owner's job — on a live deployment every validator must run the same
registry (ship it in the repo / pin it to a CID) or weights will diverge.

Demo defaults (network='demo') exist so the local simulation works out of
the box; replace them with real, verified addresses before going live.
"""
import json
import os
import time
from typing import Dict, List, Optional

from .schemas import Charity

REGISTRY_PATH = '~/.mod/charitao/registry.json'

DEFAULT_CHARITIES = [
    dict(id='cleanwater', name='Clean Water Fund', cause='safe drinking water',
         address='demo://cleanwater', verified=True, network='demo',
         url='https://www.charitywater.org',
         links=[
             dict(label='charity: water (real-world model)', url='https://www.charitywater.org'),
             dict(label='Charity Navigator', url='https://www.charitynavigator.org/search?q=charity%20water'),
             dict(label='ProPublica 990s', url='https://projects.propublica.org/nonprofits/search?q=charity+water'),
         ],
         audits=[
             dict(year=2024, label='Audited financials — charity: water',
                  url='https://www.charitywater.org/about/financials'),
         ]),
    dict(id='directcash', name='Direct Cash Transfers', cause='unconditional cash to the poorest households',
         address='demo://directcash', verified=True, network='demo',
         url='https://www.givedirectly.org',
         links=[
             dict(label='GiveDirectly (real-world model)', url='https://www.givedirectly.org'),
             dict(label='GiveWell review', url='https://www.givewell.org/charities/givedirectly'),
             dict(label='Charity Navigator', url='https://www.charitynavigator.org/search?q=GiveDirectly'),
         ],
         audits=[
             dict(year=2024, label='Audited financials — GiveDirectly',
                  url='https://www.givedirectly.org/financials/'),
         ]),
    dict(id='malarianets', name='Malaria Nets', cause='insecticide-treated bed nets',
         address='demo://malarianets', verified=True, network='demo',
         url='https://www.againstmalaria.com',
         links=[
             dict(label='Against Malaria Foundation (real-world model)', url='https://www.againstmalaria.com'),
             dict(label='GiveWell top charity review', url='https://www.givewell.org/charities/amf'),
             dict(label='ProPublica 990s', url='https://projects.propublica.org/nonprofits/search?q=Against+Malaria+Foundation'),
         ],
         audits=[
             dict(year=2024, label='GiveWell independent review (financials + evidence)',
                  url='https://www.givewell.org/charities/amf'),
         ]),
    dict(id='opensource', name='Open Source Commons', cause='funding public-good software',
         address='demo://opensource', verified=True, network='demo',
         url='https://opencollective.com/opensource',
         links=[
             dict(label='Open Source Collective (real-world model)', url='https://opencollective.com/opensource'),
             dict(label='Software Freedom Conservancy', url='https://sfconservancy.org'),
             dict(label='ProPublica 990s', url='https://projects.propublica.org/nonprofits/search?q=Open+Source+Collective'),
         ],
         audits=[
             dict(year=2025, label='Open Collective public ledger (real-time transparency)',
                  url='https://opencollective.com/opensource'),
         ]),
]


def legitimacy(c: dict) -> dict:
    """Derived is-it-legit verdict for a charity entry.

    Owner override (`legit`) wins; otherwise the verdict is what the record
    can actually prove: on-registry verification, published audits, and
    independent info links.
    """
    checks = {
        'verified': bool(c.get('verified')),
        'audits_on_file': bool(c.get('audits')),
        'independent_links': bool(c.get('links')),
        'has_website': bool(c.get('url')),
        'owner_vouched': c.get('legit') is True,
    }
    if c.get('legit') is False:
        return {'status': 'flagged', 'label': '✗ flagged — not legit',
                'checks': checks, 'reasons': ['flagged by the subnet owner']}
    reasons = []
    reasons.append('verified on the subnet whitelist' if checks['verified']
                   else 'NOT verified — donations to it are never credited')
    reasons.append('audits/financials on file' if checks['audits_on_file']
                   else 'no audits on file')
    reasons.append('independent info links attached' if checks['independent_links']
                   else 'no independent links')
    if checks['owner_vouched']:
        reasons.append('vouched for by the subnet owner')
    if checks['owner_vouched'] or (checks['verified'] and checks['audits_on_file']
                                   and (checks['independent_links'] or checks['has_website'])):
        status, label = 'legit', '✓ legit'
    elif checks['verified']:
        status, label = 'probably', 'probably legit — docs missing'
    else:
        status, label = 'dyor', '⚠ unverified — DYOR'
    return {'status': status, 'label': label, 'checks': checks, 'reasons': reasons}


class CharityRegistry:

    def __init__(self, path: str = REGISTRY_PATH, seed_defaults: bool = True):
        self.path = os.path.expanduser(path)
        self._charities: Dict[str, dict] = {}
        self._load(seed_defaults)

    def _load(self, seed_defaults: bool):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self._charities = json.load(f)
            if self._backfill():
                self._save()
        elif seed_defaults:
            for c in DEFAULT_CHARITIES:
                self._charities[c['id']] = Charity(**c).to_dict()
            self._save()

    def _backfill(self) -> bool:
        """Upgrade registry files written before audits/links/legit existed:
        add the missing keys, and re-hydrate default entries with the shipped
        audit + info links so old installs get the enriched cards too."""
        defaults = {c['id']: c for c in DEFAULT_CHARITIES}
        changed = False
        for cid, c in self._charities.items():
            for key, empty in (('links', []), ('audits', []), ('legit', None)):
                if key not in c:
                    c[key] = empty
                    changed = True
            d = defaults.get(cid)
            if d:
                for key in ('url', 'links', 'audits'):
                    if not c.get(key) and d.get(key):
                        c[key] = d[key]
                        changed = True
        return changed

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self._charities, f, indent=2)

    # ── queries ──────────────────────────────────────────────────

    def charities(self, verified_only: bool = False) -> List[dict]:
        items = list(self._charities.values())
        if verified_only:
            items = [c for c in items if c['verified']]
        return [{**c, 'legitimacy': legitimacy(c)} for c in items]

    def get(self, charity_id: str) -> Optional[dict]:
        c = self._charities.get(charity_id)
        return {**c, 'legitimacy': legitimacy(c)} if c else None

    def addresses(self) -> Dict[str, str]:
        """address -> charity_id, verified charities only (the credit filter)."""
        return {c['address']: c['id']
                for c in self._charities.values() if c['verified']}

    # ── curation (owner-gated at the deployment layer) ───────────

    def add(self, charity_id: str, name: str, address: str, cause: str = '',
            url: str = '', verified: bool = False, network: str = 'demo',
            links: Optional[list] = None, audits: Optional[list] = None,
            legit: Optional[bool] = None) -> dict:
        if charity_id in self._charities:
            return {'error': f'charity {charity_id!r} already exists'}
        taken = {c['address'] for c in self._charities.values()}
        if address in taken:
            return {'error': f'address {address!r} already whitelisted'}
        c = Charity(id=charity_id, name=name, address=address, cause=cause,
                    url=url, verified=verified, network=network,
                    links=list(links or []), audits=list(audits or []),
                    legit=legit, added_at=int(time.time()))
        self._charities[charity_id] = c.to_dict()
        self._save()
        return self.get(charity_id)

    def remove(self, charity_id: str) -> dict:
        c = self._charities.pop(charity_id, None)
        if c is None:
            return {'error': f'no charity {charity_id!r}'}
        self._save()
        return c

    def verify(self, charity_id: str, verified: bool = True) -> dict:
        c = self._charities.get(charity_id)
        if c is None:
            return {'error': f'no charity {charity_id!r}'}
        c['verified'] = bool(verified)
        self._save()
        return self.get(charity_id)

    def flag(self, charity_id: str, legit) -> dict:
        """Owner verdict: True = vouched legit, False = flagged, None = derived."""
        c = self._charities.get(charity_id)
        if c is None:
            return {'error': f'no charity {charity_id!r}'}
        c['legit'] = None if legit is None else bool(legit)
        self._save()
        return self.get(charity_id)
