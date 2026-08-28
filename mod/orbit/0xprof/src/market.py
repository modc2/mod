"""
The money half: credits, entitlements and escrow.

Credits are an internal unit. They are not a token, they do not leave this
module, and nothing mints them on a user's say-so — an owner grants them, and
after that they only move between accounts. Making them redeemable against a
chain balance or BlocTime stake is a bridge this module deliberately has not
pretended to cross.

Three things move value here:

    buy      a buyer pays a proof's price and gets the proof bytes forever
    escrow   a bounty's reward is held by the module, not by the poster, so a
             prover can see that the money exists before doing the work
    refund   a purchase whose proof later turns invalid or disputed can be
             unwound — the one guarantee a market for verified things must
             actually honour, rather than describe

The refund is why `sold_as` is recorded on every purchase. A buyer who paid
for something the box called verified, and which later stopped being verified,
has a receipt that says so, and does not have to argue about it.
"""
import time
from typing import Any, Dict, List, Optional

from . import storage


class MarketError(Exception):
    """A refusal the caller can act on: not yours, not enough, not allowed."""


def account(address: str) -> Dict[str, Any]:
    key = (address or '').lower()
    got = storage.get_json(f'ledger/{key}')
    if not got:
        got = {'address': address, 'credits': 0.0, 'spent': 0.0, 'earned': 0.0,
               'escrowed': 0.0, 'entitlements': [], 'purchases': [],
               'history': [], 'created': time.time()}
    return got


def _save(record: Dict[str, Any]) -> Dict[str, Any]:
    storage.put_json(f'ledger/{record["address"].lower()}', record)
    return record


def _log(record: Dict[str, Any], delta: float, reason: str) -> Dict[str, Any]:
    record.setdefault('history', []).append(
        {'ts': time.time(), 'delta': round(float(delta), 6), 'reason': reason,
         'balance': round(float(record['credits']), 6)})
    record['history'] = record['history'][-200:]
    return record


def grant(address: str, amount: float, reason: str = 'grant') -> Dict[str, Any]:
    """Put credits into an account. The only way credits come into existence."""
    if amount <= 0:
        raise MarketError('grant a positive number of credits')
    acct = account(address)
    acct['credits'] = round(float(acct['credits']) + float(amount), 6)
    return _save(_log(acct, amount, reason))


def transfer(sender: str, recipient: str, amount: float, reason: str) -> Dict[str, Any]:
    if amount <= 0:
        raise MarketError('transfer a positive number of credits')
    payer = account(sender)
    if payer['credits'] < amount:
        raise MarketError(f'{amount} credits needed, {payer["credits"]} available')
    payer['credits'] = round(payer['credits'] - amount, 6)
    payer['spent'] = round(float(payer.get('spent') or 0) + amount, 6)
    _save(_log(payer, -amount, reason))
    payee = account(recipient)
    payee['credits'] = round(float(payee['credits']) + amount, 6)
    payee['earned'] = round(float(payee.get('earned') or 0) + amount, 6)
    _save(_log(payee, amount, reason))
    return {'from': sender, 'to': recipient, 'amount': amount,
            'balance': payer['credits']}


# ── entitlements ─────────────────────────────────────────────────────

def entitled(address: str, proof_id: str, seller: str = '', price: float = 0.0) -> bool:
    if not price:
        return True
    if address and seller and address.lower() == seller.lower():
        return True
    if address == 'open-mode':
        return True
    return proof_id in (account(address).get('entitlements') or [])


def buy(buyer: str, proof_id: str, seller: str, price: float,
        sold_as: str = 'unverified') -> Dict[str, Any]:
    """Pay once, hold the entitlement forever. Buying twice charges once."""
    acct = account(buyer)
    if proof_id in (acct.get('entitlements') or []):
        return {'charged': 0.0, 'proof': proof_id, 'balance': acct['credits'],
                'note': 'already bought — entitlements are per buyer, not per read'}
    if price > 0:
        if buyer.lower() == (seller or '').lower():
            return {'charged': 0.0, 'proof': proof_id, 'balance': acct['credits'],
                    'note': 'you are the seller'}
        transfer(buyer, seller, price, f'buy {proof_id[:12]}')
        acct = account(buyer)
    acct.setdefault('entitlements', []).append(proof_id)
    acct.setdefault('purchases', []).append(
        {'proof': proof_id, 'seller': seller, 'price': price,
         'sold_as': sold_as, 'ts': time.time(), 'refunded': False})
    _save(acct)
    return {'charged': price, 'proof': proof_id, 'seller': seller,
            'sold_as': sold_as, 'balance': acct['credits']}


def refund(buyer: str, proof_id: str, status_now: str, reason: str) -> Dict[str, Any]:
    """Unwind a purchase of something that is no longer what it was sold as.

    The seller's balance can go negative here, and that is intended: the
    alternative is a market where selling a proof that fails verification is
    free as long as you withdraw first. A negative balance is a debt the
    account carries and the console shows.
    """
    acct = account(buyer)
    purchase = next((p for p in reversed(acct.get('purchases') or [])
                     if p['proof'] == proof_id and not p.get('refunded')), None)
    if not purchase:
        raise MarketError(f'no unrefunded purchase of {proof_id[:12]} by {buyer}')
    if status_now not in ('invalid', 'disputed'):
        raise MarketError(
            f'that proof is {status_now} — refunds are for proofs sold as verified '
            'that stopped verifying, not for changing your mind')
    if purchase.get('sold_as') not in ('verified', 'claimed'):
        raise MarketError(f'it was sold as {purchase.get("sold_as")}, not as verified')

    amount = float(purchase.get('price') or 0)
    purchase['refunded'] = True
    purchase['refunded_at'] = time.time()
    if proof_id in (acct.get('entitlements') or []):
        acct['entitlements'].remove(proof_id)
    if amount:
        acct['credits'] = round(acct['credits'] + amount, 6)
        acct['spent'] = round(float(acct.get('spent') or 0) - amount, 6)
    _save(_log(acct, amount, f'refund {proof_id[:12]}: {reason}'))

    seller = account(purchase['seller'])
    if amount:
        seller['credits'] = round(seller['credits'] - amount, 6)
        seller['earned'] = round(float(seller.get('earned') or 0) - amount, 6)
    _save(_log(seller, -amount, f'refund clawback {proof_id[:12]}: {reason}'))
    return {'refunded': amount, 'proof': proof_id, 'to': buyer,
            'from': purchase['seller'], 'reason': reason,
            'seller_balance': seller['credits'],
            'seller_in_debt': seller['credits'] < 0}


def refundable(proof_id: str, status_now: str) -> List[Dict[str, Any]]:
    """Everyone who bought this while it looked verified. Used by the console."""
    if status_now not in ('invalid', 'disputed'):
        return []
    out = []
    for address in ledger_addresses():
        acct = account(address)
        for purchase in acct.get('purchases') or []:
            if (purchase['proof'] == proof_id and not purchase.get('refunded')
                    and purchase.get('sold_as') in ('verified', 'claimed')):
                out.append({'buyer': acct['address'], **purchase})
    return out


def ledger_addresses() -> List[str]:
    """Every account this deployment has ever written."""
    import os
    root = os.path.join(str(getattr(storage.store(), 'path', '')),
                        storage.PREFIX, 'ledger')
    if not os.path.isdir(root):
        return []
    return [name[:-len('.json')] for name in os.listdir(root) if name.endswith('.json')]


# ── escrow ───────────────────────────────────────────────────────────

def hold(address: str, amount: float, reason: str) -> Dict[str, Any]:
    """Move credits out of a balance and into the module's custody."""
    if amount <= 0:
        raise MarketError('escrow a positive number of credits')
    acct = account(address)
    if acct['credits'] < amount:
        raise MarketError(f'{amount} credits needed to fund that, '
                          f'{acct["credits"]} available')
    acct['credits'] = round(acct['credits'] - amount, 6)
    acct['escrowed'] = round(float(acct.get('escrowed') or 0) + amount, 6)
    _save(_log(acct, -amount, f'escrow: {reason}'))
    return {'escrowed': amount, 'by': address, 'balance': acct['credits']}


def release(poster: str, winner: str, amount: float, reason: str) -> Dict[str, Any]:
    """Escrow out to whoever earned it."""
    acct = account(poster)
    acct['escrowed'] = round(max(0.0, float(acct.get('escrowed') or 0) - amount), 6)
    acct['spent'] = round(float(acct.get('spent') or 0) + amount, 6)
    _save(_log(acct, 0, f'escrow released to {winner}: {reason}'))
    payee = account(winner)
    payee['credits'] = round(float(payee['credits']) + amount, 6)
    payee['earned'] = round(float(payee.get('earned') or 0) + amount, 6)
    _save(_log(payee, amount, f'bounty paid: {reason}'))
    return {'paid': amount, 'to': winner, 'from': poster, 'balance': payee['credits']}


def unhold(address: str, amount: float, reason: str) -> Dict[str, Any]:
    """Escrow back to where it came from — an expired or cancelled bounty."""
    acct = account(address)
    acct['escrowed'] = round(max(0.0, float(acct.get('escrowed') or 0) - amount), 6)
    acct['credits'] = round(acct['credits'] + amount, 6)
    _save(_log(acct, amount, f'escrow returned: {reason}'))
    return {'returned': amount, 'to': address, 'balance': acct['credits']}


def summary(address: str) -> Dict[str, Any]:
    acct = account(address)
    return {
        'address': acct['address'],
        'credits': round(float(acct['credits']), 6),
        'escrowed': round(float(acct.get('escrowed') or 0), 6),
        'spent': round(float(acct.get('spent') or 0), 6),
        'earned': round(float(acct.get('earned') or 0), 6),
        'entitlements': acct.get('entitlements') or [],
        'purchases': (acct.get('purchases') or [])[-25:],
        'history': (acct.get('history') or [])[-25:],
        'in_debt': float(acct['credits']) < 0,
    }
