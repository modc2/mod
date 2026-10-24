"""Wallet signatures — who is allowed to move which balance.

The rest of PreFi takes an `address` as a plain argument, which is fine when
the only thing at stake is a bookkeeping entry. The pool holds real USDC, so
every action that *spends* a balance (staking, withdrawing) has to prove the
caller holds the key for the address they are spending from, and every action
that changes the rules has to prove the caller is the owner.

The proof is a signature over a human-readable message — the same thing the
wallet shows in its signing dialog, so a user can read exactly what they are
authorising instead of squinting at a hex blob. A nonce makes each signature
single-use: replaying yesterday's withdrawal signature fails because the
account's nonce has moved on.

*Which* signature scheme is not decided here. `identity.py` picks the verifier
off the shape of the address, so an EVM wallet signing EIP-191 and a TAO
wallet (SubWallet, Talisman) signing sr25519 both land in the same ledger, and
a new key type is added there without touching this file.

`PREFI_UNSAFE_NO_SIG=1` disables the check for local development and the test
suite. It is refused whenever a real vault key is configured — a hot wallet
with unauthenticated withdrawals is not a mode anyone should be able to enter
by typo.
"""

import os
from typing import Dict, List, Optional, Tuple

try:                                            # package or flat import
    import identity
except ImportError:                             # pragma: no cover
    from . import identity

PREFIX = 'PreFi Pool'


def signatures_disabled() -> bool:
    return os.environ.get('PREFI_UNSAFE_NO_SIG') == '1'


def action_message(action: str, address: str, fields: List[Tuple[str, str]],
                   nonce: int) -> str:
    """The exact text a wallet is asked to sign.

    Field order is the caller's, not a dict's, because the message is a hash
    input: reordering it invalidates every signature ever made against it.
    The address is the canonical one for its key type — the same string the
    ledger keys by — so a wallet showing a Polkadot-prefixed form of a TAO
    account still signs for the account the server is about to debit.
    """
    lines = [PREFIX, f'action: {action}',
             f'address: {identity.normalize(address)}']
    lines += [f'{k}: {v}' for k, v in fields]
    lines.append(f'nonce: {int(nonce)}')
    return '\n'.join(lines)


def recover(message: str, signature: str) -> Optional[str]:
    """Address that produced this EIP-191 signature, or None.

    Only meaningful for secp256k1, where the key is recovered from the
    signature. sr25519 and ed25519 verify against a known public key instead —
    there is nothing to recover — so use `verify` for anything key-agnostic.
    """
    return identity._evm_check(message, signature, '').get('signer')


def verify(action: str, address: str, fields: List[Tuple[str, str]], nonce: int,
           signature: Optional[str]) -> Dict:
    """Check a signed action. Returns {'ok': bool, 'error'/'signer'}.

    The message is rebuilt here from the arguments the server is about to act
    on — never from anything the client sent — so a signature can only ever
    authorise the action it actually describes.
    """
    message = action_message(action, address, fields, nonce)
    addr = identity.normalize(address)

    if not signature:
        if signatures_disabled():
            return {'ok': True, 'signer': addr, 'unsigned': True,
                    'message': message}
        return {'ok': False, 'error': 'signature required', 'message': message}

    result = identity.verify(message, signature, address)
    if not result['ok']:
        return {'ok': False, 'error': result.get('error', 'signature rejected'),
                'scheme': result.get('scheme'), 'message': message}
    return {'ok': True, 'signer': result['signer'], 'scheme': result['scheme'],
            'curve': result.get('curve'), 'message': message}
