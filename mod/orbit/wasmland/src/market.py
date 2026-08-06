"""
The marketplace.

A listing is an artifact somebody put a name and a price on. Publishing does
not copy anything and does not change the artifact — the artifact is already
in the store under the hash of its own bytes, and the listing points at it. Two
people can list the same bytes; the id says they are the same computation.

WHAT A PRICE MEANS HERE, EXACTLY
    A paid listing charges per run, in credits, to the address that ran it,
    and the seller is credited the same amount. That is the whole model, and
    it is worth being precise about what it can and cannot enforce:

      * a *server* run is metered — the box does the work, so it charges for it
      * a *browser* run needs the bytes, so buying gives you the bytes, and
        after that the tab can run them all day without asking again

    So the honest description is that a price buys access to an artifact plus
    the box's willingness to run it, not a meter on the buyer's own CPU. A
    listing that wants a real per-run meter should be run on the server venue.
    The console says this where it takes the money rather than in a footnote.

Credits are an internal unit. They are not a token, they do not leave this
module, and nothing here mints them out of thin air on a user's say-so: an
owner grants them, and after that they only move between accounts. Making them
redeemable — against BlocTime stake, or a chain balance — is a bridge this
module deliberately doesn't pretend to have already crossed.
"""
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from . import engines, storage

SLUG = re.compile(r'[^a-z0-9-]+')


class MarketError(Exception):
    """A refusal the caller can act on: not yours, not enough, not allowed."""


def slugify(text: str) -> str:
    return SLUG.sub('-', (text or '').lower()).strip('-')[:48] or 'listing'


# ── listings ─────────────────────────────────────────────────────────

def publish(artifact_id: str, seller: str, *, title: str = '',
            description: str = '', entry: str = '', price: float = 0.0,
            tags: Optional[List[str]] = None, license: str = '',
            venues: Optional[List[str]] = None) -> Dict[str, Any]:
    """List an artifact already in the store."""
    record = storage.artifact_record(artifact_id)
    if not record:
        raise MarketError(f'no artifact {artifact_id[:12]} — upload the bytes first')
    engine = engines.get(record['engine'])
    manifest = record.get('manifest') or {}
    if price < 0:
        raise MarketError('a negative price is not a discount')

    listing_id = f'{slugify(title or record.get("filename") or record["engine"])}-{uuid.uuid4().hex[:6]}'
    # A game has no `run`; the export a human would call first is game_info.
    default_entry = 'game_info' if manifest.get('role') == 'game' else engine.default_entry
    listing = {
        'id': listing_id,
        'artifact': artifact_id,
        'engine': record['engine'],
        'entry': entry or default_entry,
        'title': title or record.get('filename') or listing_id,
        'description': description,
        'tags': [t.strip() for t in (tags or []) if t.strip()][:12],
        'license': license,
        'price': round(float(price), 6),
        'currency': 'credits',
        'seller': seller,
        'role': manifest.get('role', 'module'),
        'venues': venues or list(engine.venues),
        'bytes': record.get('bytes'),
        'cid': record.get('cid'),
        'created': time.time(),
        'runs': 0,
        'verified_runs': 0,
        'earned': 0.0,
    }
    storage.put_record('listings', listing_id, listing)
    return listing


def listings(engine: str = None, role: str = None, tag: str = None,
             seller: str = None, q: str = None, free: bool = None,
             limit: int = 100) -> List[Dict[str, Any]]:
    out = storage.records('listings', limit=1000)
    if engine:
        out = [x for x in out if x['engine'] == engine]
    if role:
        out = [x for x in out if x.get('role') == role]
    if tag:
        out = [x for x in out if tag in (x.get('tags') or [])]
    if seller:
        out = [x for x in out if (x.get('seller') or '').lower() == seller.lower()]
    if free is True:
        out = [x for x in out if not x.get('price')]
    if free is False:
        out = [x for x in out if x.get('price')]
    if q:
        needle = q.lower()
        out = [x for x in out if needle in x['title'].lower()
               or needle in (x.get('description') or '').lower()
               or any(needle in t for t in x.get('tags') or [])]
    return out[:limit]


def listing(listing_id: str) -> Dict[str, Any]:
    got = storage.get_record('listings', listing_id)
    if not got:
        raise MarketError(f'no listing {listing_id}')
    return got


def unpublish(listing_id: str, caller: str) -> Dict[str, Any]:
    """Delist. The artifact stays: other listings and past runs still point at
    it, and a receipt whose artifact vanished could never be re-verified."""
    got = listing(listing_id)
    if not _is_owner(got, caller):
        raise MarketError(f'listing {listing_id} belongs to {got["seller"]}')
    storage.drop_record('listings', listing_id)
    return {'unpublished': listing_id, 'artifact_kept': got['artifact']}


def _is_owner(record: Dict[str, Any], caller: str) -> bool:
    seller = (record.get('seller') or '').lower()
    return bool(caller) and (caller.lower() == seller or caller == 'open-mode')


def touch(listing_id: str, verified: bool = False, earned: float = 0.0):
    """Fold one run's outcome back into the listing's counters."""
    got = storage.get_record('listings', listing_id)
    if not got:
        return
    got['runs'] = int(got.get('runs') or 0) + 1
    if verified:
        got['verified_runs'] = int(got.get('verified_runs') or 0) + 1
    if earned:
        got['earned'] = round(float(got.get('earned') or 0) + earned, 6)
    storage.put_record('listings', listing_id, got)


# ── accounts ─────────────────────────────────────────────────────────

def account(address: str) -> Dict[str, Any]:
    key = (address or '').lower()
    got = storage.get_json(f'ledger/{key}')
    if not got:
        got = {'address': address, 'credits': 0.0, 'spent': 0.0,
               'earned': 0.0, 'entitlements': [], 'created': time.time()}
    return got


def _save(account_record: Dict[str, Any]) -> Dict[str, Any]:
    storage.put_json(f'ledger/{account_record["address"].lower()}', account_record)
    return account_record


def grant(address: str, amount: float, reason: str = 'grant') -> Dict[str, Any]:
    """Put credits into an account. The only way credits come into existence."""
    if amount <= 0:
        raise MarketError('grant a positive number of credits')
    acct = account(address)
    acct['credits'] = round(float(acct['credits']) + float(amount), 6)
    acct.setdefault('history', []).append(
        {'ts': time.time(), 'delta': float(amount), 'reason': reason})
    acct['history'] = acct['history'][-200:]
    return _save(acct)


def charge(buyer: str, listing_id: str) -> Dict[str, Any]:
    """Move a listing's price from buyer to seller. Free listings are free.

    Buying twice does not charge twice: the entitlement is per (buyer,
    listing), because what the price buys is access to the artifact.
    """
    got = listing(listing_id)
    price = float(got.get('price') or 0)
    acct = account(buyer)
    if price <= 0:
        return {'charged': 0.0, 'listing': listing_id, 'balance': acct['credits'],
                'note': 'free listing'}
    if listing_id in (acct.get('entitlements') or []):
        return {'charged': 0.0, 'listing': listing_id, 'balance': acct['credits'],
                'note': 'already bought — entitlements are per buyer, not per run'}
    if acct['credits'] < price:
        raise MarketError(
            f'{price} credits needed, {acct["credits"]} available — '
            'an owner grants credits with `m wasmland/grant`')

    acct['credits'] = round(acct['credits'] - price, 6)
    acct['spent'] = round(float(acct.get('spent') or 0) + price, 6)
    acct.setdefault('entitlements', []).append(listing_id)
    acct.setdefault('history', []).append(
        {'ts': time.time(), 'delta': -price, 'reason': f'run {listing_id}'})
    _save(acct)

    seller = account(got['seller'])
    seller['credits'] = round(float(seller['credits']) + price, 6)
    seller['earned'] = round(float(seller.get('earned') or 0) + price, 6)
    seller.setdefault('history', []).append(
        {'ts': time.time(), 'delta': price, 'reason': f'{listing_id} run by {buyer}'})
    _save(seller)
    touch(listing_id, earned=price)
    return {'charged': price, 'listing': listing_id, 'seller': got['seller'],
            'balance': acct['credits']}


def entitled(buyer: str, listing_id: str) -> bool:
    got = listing(listing_id)
    if not float(got.get('price') or 0):
        return True
    if _is_owner(got, buyer):
        return True
    return listing_id in (account(buyer).get('entitlements') or [])


def require_entitlement(buyer: str, listing_id: str):
    if not entitled(buyer, listing_id):
        got = listing(listing_id)
        raise MarketError(
            f'{got["title"]} costs {got["price"]} credits per buyer — '
            f'POST /listings/{listing_id}/buy first')
