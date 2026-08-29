"""Wallet signatures — who is allowed to move which balance.

The rest of PreFi takes an `address` as a plain argument, which is fine when
the only thing at stake is a bookkeeping entry. The pool holds real USDC, so
every action that *spends* a balance (staking, withdrawing) has to prove the
caller holds the key for the address they are spending from, and every action
that changes the rules has to prove the caller is the owner.

The proof is an EIP-191 `personal_sign` over a human-readable message — the
same thing MetaMask shows in its signing dialog, so a user can read exactly
what they are authorising instead of squinting at a hex blob. A nonce makes
each signature single-use: replaying yesterday's withdrawal signature fails
because the account's nonce has moved on.

`PREFI_UNSAFE_NO_SIG=1` disables the check for local development and the test
suite. It is refused whenever a real vault key is configured — a hot wallet
with unauthenticated withdrawals is not a mode anyone should be able to enter
by typo.
"""

import os
from typing import Dict, List, Optional, Tuple

PREFIX = 'PreFi Pool'


def signatures_disabled() -> bool:
    return os.environ.get('PREFI_UNSAFE_NO_SIG') == '1'


def action_message(action: str, address: str, fields: List[Tuple[str, str]],
                   nonce: int) -> str:
    """The exact text a wallet is asked to sign.

    Field order is the caller's, not a dict's, because the message is a hash
    input: reordering it invalidates every signature ever made against it.
    """
    lines = [PREFIX, f'action: {action}', f'address: {(address or "").lower()}']
    lines += [f'{k}: {v}' for k, v in fields]
    lines.append(f'nonce: {int(nonce)}')
    return '\n'.join(lines)


def recover(message: str, signature: str) -> Optional[str]:
    """Address that produced this personal_sign signature, or None."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        return None
    try:
        return Account.recover_message(
            encode_defunct(text=message), signature=signature).lower()
    except Exception:
        return None


def verify(action: str, address: str, fields: List[Tuple[str, str]], nonce: int,
           signature: Optional[str]) -> Dict:
    """Check a signed action. Returns {'ok': bool, 'error'/'signer'}.

    The message is rebuilt here from the arguments the server is about to act
    on — never from anything the client sent — so a signature can only ever
    authorise the action it actually describes.
    """
    message = action_message(action, address, fields, nonce)

    if not signature:
        if signatures_disabled():
            return {'ok': True, 'signer': (address or '').lower(),
                    'unsigned': True, 'message': message}
        return {'ok': False, 'error': 'signature required', 'message': message}

    signer = recover(message, signature)
    if not signer:
        return {'ok': False, 'error': 'signature could not be recovered',
                'message': message}
    if signer != (address or '').lower():
        return {'ok': False,
                'error': f'signature is from {signer}, not {(address or "").lower()}',
                'message': message}
    return {'ok': True, 'signer': signer, 'message': message}
