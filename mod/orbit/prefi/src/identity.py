"""Who a caller is, whatever kind of key they hold.

PreFi started as one address format — a 0x EVM account signing EIP-191 — and
every balance, nonce and leaderboard row is keyed by that string. The pool now
settles Bittensor subnet prices, so the obvious wallet for a lot of its users
is a TAO wallet (SubWallet, Talisman, Polkadot{.js}), which holds an sr25519
key and presents an SS58 address. Those users could watch the pool but not
sign into it.

This module is the seam. A **scheme** is one key type: how its addresses look,
how they canonicalise, and how a signature over a message is checked against
one. Everything else in PreFi asks this module instead of assuming secp256k1:

    identity.normalize(addr)      → the ledger key for that account
    identity.is_account(addr)     → is this any identity we can verify?
    identity.verify(msg, sig, a)  → did the holder of `a` sign `msg`?

Adding a key type is one function plus one `register(...)` call — the same
shape as the scoring registry in `scoring.py`. Nothing downstream changes,
because nothing downstream knows what a scheme is.

Shipped schemes:

  evm     secp256k1, EIP-191 `personal_sign`, 0x address, lowercased.
          MetaMask, Rabby, Coinbase Wallet, SubWallet's EVM account.
  ss58    Substrate. sr25519 *or* ed25519 under one address (the address does
          not say which curve, so both are tried), signed with the polkadot
          extension's `signRaw`, which wraps the payload in `<Bytes>…</Bytes>`.
          SubWallet, Talisman, Polkadot{.js}, Nova, Enkrypt — and so Bittensor.
  solana  ed25519, base58 32-byte pubkey, Phantom/Solflare/Backpack
          `signMessage` over the raw UTF-8 bytes.

SS58 canonicalisation: one keypair is one account here, whatever chain prefix
the wallet chose to display it under. A Bittensor address (prefix 42), the
same key shown as Polkadot (prefix 0) and as Kusama (prefix 2) all normalise
to the prefix-42 form, so a user cannot end up with three balances and one
key. The public key is what is really being keyed by; the prefix is a skin.

Address parsing and normalisation are dependency-free (base58 and blake2b are
written out below). Only *verification* needs a curve library, and a missing
one is reported as a plain error rather than a silent rejection.
"""

import hashlib
import re
from typing import Callable, Dict, List, Optional

# ── base58 / SS58, no dependencies ───────────────────────────────────
# Written out rather than imported: address handling has to work on any box
# the pool runs on, and `base58` is not in anybody's default environment.

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_B58_INDEX = {c: i for i, c in enumerate(_B58)}

SS58_PREFIX = b'SS58PRE'
#: Bittensor and generic Substrate. The canonical prefix for stored addresses.
BITTENSOR_SS58_FORMAT = 42


def b58decode(value: str) -> bytes:
    n = 0
    for char in value:
        if char not in _B58_INDEX:
            raise ValueError(f'not base58: {char!r}')
        n = n * 58 + _B58_INDEX[char]
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    pad = len(value) - len(value.lstrip('1'))
    return b'\x00' * pad + body


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, rem = divmod(n, 58)
        out = _B58[rem] + out
    pad = len(raw) - len(raw.lstrip(b'\x00'))
    return '1' * pad + out


def _ss58_checksum(body: bytes) -> bytes:
    return hashlib.blake2b(SS58_PREFIX + body, digest_size=64).digest()[:2]


def ss58_decode(address: str) -> Optional[Dict]:
    """`{'prefix': int, 'pubkey': bytes}` for a valid SS58 address, else None.

    The checksum is what makes this safe to use as a detector: a Solana pubkey
    or a random base58 string will not survive it.
    """
    if not address or not isinstance(address, str):
        return None
    try:
        raw = b58decode(address.strip())
    except ValueError:
        return None
    if len(raw) < 35:                          # 1 prefix + 32 key + 2 checksum
        return None

    first = raw[0]
    if first & 0b0100_0000:                    # two-byte network prefix
        prefix = ((first & 0b0011_1111) << 2) | (raw[1] >> 6) | \
                 ((raw[1] & 0b0011_1111) << 8)
        offset = 2
    elif first in (46, 47):                    # reserved, never a real network
        return None
    else:
        prefix = first
        offset = 1

    body, checksum = raw[:-2], raw[-2:]
    pubkey = body[offset:]
    if len(pubkey) not in (32, 33):            # 33 = a compressed ecdsa key
        return None
    if _ss58_checksum(body) != checksum:
        return None
    return {'prefix': prefix, 'pubkey': pubkey}


def ss58_encode(pubkey: bytes, prefix: int = BITTENSOR_SS58_FORMAT) -> str:
    if prefix < 64:
        head = bytes([prefix])
    else:                                      # two-byte form, prefixes 64..16383
        head = bytes([((prefix & 0b1111_1100) >> 2) | 0b0100_0000,
                      (prefix >> 8) | ((prefix & 0b0000_0011) << 6)])
    body = head + pubkey
    return b58encode(body + _ss58_checksum(body))


# ── signature bytes ──────────────────────────────────────────────────

def _hex_bytes(value: str) -> Optional[bytes]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.startswith('0x') or raw.startswith('0X'):
        raw = raw[2:]
    if not raw or len(raw) % 2:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return None


def _sig_bytes(signature: str, size: int = 64) -> Optional[bytes]:
    """A signature as raw bytes, hex or base58, with a MultiSignature enum byte
    stripped if the wallet included one (0=ed25519, 1=sr25519, 2=ecdsa)."""
    raw = _hex_bytes(signature)
    if raw is None:
        try:
            raw = b58decode((signature or '').strip())
        except ValueError:
            return None
    if len(raw) == size + 1 and raw[0] in (0, 1, 2):
        raw = raw[1:]
    return raw if len(raw) == size else None


# ── payload framing ──────────────────────────────────────────────────

def wrapped_payloads(message: str) -> List[bytes]:
    """Every byte string a substrate wallet might actually have signed.

    `signRaw({type: 'bytes'})` wraps the payload in `<Bytes>…</Bytes>` so that
    a dapp can never trick a wallet into signing something that decodes as an
    extrinsic. Wallets differ on whether they wrap, so both are accepted — and
    both are the same message from the user's point of view, because the tags
    are added around the exact text they were shown.
    """
    body = message.encode('utf-8')
    return [b'<Bytes>' + body + b'</Bytes>', body]


# ── curve verification ───────────────────────────────────────────────

def _verify_sr25519(payload: bytes, sig: bytes, pubkey: bytes) -> Optional[bool]:
    """True/False, or None when the curve library is not installed."""
    try:
        import sr25519
    except ImportError:
        return None
    try:
        return bool(sr25519.verify(sig, payload, pubkey))
    except Exception:
        return False


def _verify_ed25519(payload: bytes, sig: bytes, pubkey: bytes) -> Optional[bool]:
    try:
        import ed25519_zebra
    except ImportError:
        ed25519_zebra = None
    if ed25519_zebra is not None:
        try:
            return bool(ed25519_zebra.ed_verify(sig, payload, pubkey))
        except Exception:
            return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        return None
    try:
        VerifyKey(pubkey).verify(payload, sig)
        return True
    except BadSignatureError:
        return False
    except Exception:
        return False


# ── schemes ──────────────────────────────────────────────────────────

class Scheme:
    """One key type.

    `detect` decides whether an address belongs to this scheme, `canonical`
    turns it into the string the ledger keys by, and `check` says whether a
    signature over `message` came from it. `check` returns a dict so a scheme
    can report *how* it verified (which curve, which framing) and so a missing
    dependency can say so instead of looking like a bad signature.
    """

    def __init__(self, name: str, label: str, detect: Callable[[str], bool],
                 canonical: Callable[[str], str],
                 check: Callable[[str, str, str], Dict],
                 chains: str = '', wallets: Optional[List[str]] = None,
                 curves: Optional[List[str]] = None, evm: bool = False):
        self.name = name
        self.label = label
        self.detect = detect
        self.canonical = canonical
        self.check = check
        self.chains = chains
        self.wallets = wallets or []
        self.curves = curves or []
        self.evm = evm

    def describe(self) -> Dict:
        return {'scheme': self.name, 'label': self.label, 'curves': self.curves,
                'chains': self.chains, 'wallets': self.wallets, 'evm': self.evm,
                'available': self.available()}

    def available(self) -> bool:
        """Can this box actually verify a signature of this kind right now?"""
        probe = self.check('probe', '', '')
        return probe.get('error') != 'unavailable'


SCHEMES: Dict[str, Scheme] = {}


def register(scheme: Scheme) -> Scheme:
    """Add a key type. Detection runs in registration order, so register a
    narrow format before a broad one."""
    SCHEMES[scheme.name] = scheme
    return scheme


# ── evm: secp256k1 / EIP-191 ─────────────────────────────────────────

_EVM_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def _evm_detect(address: str) -> bool:
    return bool(_EVM_RE.match((address or '').strip()))


def _evm_canonical(address: str) -> str:
    return (address or '').strip().lower()


def _evm_check(message: str, signature: str, address: str) -> Dict:
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        return {'ok': False, 'error': 'unavailable',
                'detail': 'eth_account is not installed'}
    if not signature:
        return {'ok': False, 'error': 'signature required'}
    try:
        signer = Account.recover_message(
            encode_defunct(text=message), signature=signature).lower()
    except Exception:
        return {'ok': False, 'error': 'signature could not be recovered'}
    if signer != _evm_canonical(address):
        return {'ok': False, 'error': 'wrong signer', 'signer': signer}
    return {'ok': True, 'signer': signer, 'curve': 'secp256k1'}


register(Scheme(
    'evm', 'EVM wallet', _evm_detect, _evm_canonical, _evm_check,
    chains='HyperEVM, Base, Ethereum', curves=['secp256k1'], evm=True,
    wallets=['MetaMask', 'Rabby', 'Coinbase Wallet', 'SubWallet (EVM)',
             'WalletConnect'],
))


# ── ss58: substrate (Bittensor, Polkadot, Kusama) ────────────────────

def _ss58_detect(address: str) -> bool:
    return ss58_decode(address) is not None


def _ss58_canonical(address: str) -> str:
    """Prefix-42 form. One keypair, one account, whatever chain it was shown
    under — see the module docstring."""
    decoded = ss58_decode(address)
    if not decoded:
        return (address or '').strip()
    return ss58_encode(decoded['pubkey'], BITTENSOR_SS58_FORMAT)


def _ss58_check(message: str, signature: str, address: str) -> Dict:
    decoded = ss58_decode(address)
    if not decoded:
        # The probe path (`Scheme.available`) lands here too; answer with the
        # library's state, not with "bad address".
        if _verify_sr25519(b'', b'\x00' * 64, b'\x00' * 32) is None and \
           _verify_ed25519(b'', b'\x00' * 64, b'\x00' * 32) is None:
            return {'ok': False, 'error': 'unavailable',
                    'detail': 'install sr25519 (py-sr25519-bindings) or PyNaCl'}
        return {'ok': False, 'error': 'not a valid SS58 address'}
    if len(decoded['pubkey']) != 32:
        return {'ok': False,
                'error': 'only 32-byte SS58 accounts (sr25519/ed25519) are supported'}
    if not signature:
        return {'ok': False, 'error': 'signature required'}

    sig = _sig_bytes(signature)
    if sig is None:
        return {'ok': False, 'error': 'signature is not 64 bytes'}

    pubkey = decoded['pubkey']
    missing = 0
    for payload in wrapped_payloads(message):
        for curve, verify in (('sr25519', _verify_sr25519),
                              ('ed25519', _verify_ed25519)):
            result = verify(payload, sig, pubkey)
            if result is None:
                missing += 1
            elif result:
                return {'ok': True, 'signer': _ss58_canonical(address),
                        'curve': curve,
                        'wrapped': payload.startswith(b'<Bytes>')}
    if missing >= 4:                           # neither curve is installed
        return {'ok': False, 'error': 'unavailable',
                'detail': 'install sr25519 (py-sr25519-bindings) or PyNaCl'}
    return {'ok': False, 'error': 'signature does not match this SS58 account'}


register(Scheme(
    'ss58', 'Substrate / TAO wallet', _ss58_detect, _ss58_canonical, _ss58_check,
    chains='Bittensor, Polkadot, Kusama', curves=['sr25519', 'ed25519'],
    wallets=['SubWallet', 'Talisman', 'Polkadot{.js}', 'Nova', 'Enkrypt'],
))


# ── solana: ed25519 over a base58 pubkey ─────────────────────────────

def _solana_detect(address: str) -> bool:
    value = (address or '').strip()
    if not (32 <= len(value) <= 44) or ss58_decode(value) is not None:
        return False
    try:
        return len(b58decode(value)) == 32
    except ValueError:
        return False


def _solana_canonical(address: str) -> str:
    return (address or '').strip()             # base58 is case-significant


def _solana_check(message: str, signature: str, address: str) -> Dict:
    if not _solana_detect(address):
        if _verify_ed25519(b'', b'\x00' * 64, b'\x00' * 32) is None:
            return {'ok': False, 'error': 'unavailable',
                    'detail': 'install PyNaCl or ed25519-zebra'}
        return {'ok': False, 'error': 'not a valid Solana address'}
    if not signature:
        return {'ok': False, 'error': 'signature required'}
    sig = _sig_bytes(signature)
    if sig is None:
        return {'ok': False, 'error': 'signature is not 64 bytes'}
    pubkey = b58decode(address.strip())
    result = _verify_ed25519(message.encode('utf-8'), sig, pubkey)
    if result is None:
        return {'ok': False, 'error': 'unavailable',
                'detail': 'install PyNaCl or ed25519-zebra'}
    if not result:
        return {'ok': False, 'error': 'signature does not match this Solana account'}
    return {'ok': True, 'signer': _solana_canonical(address), 'curve': 'ed25519'}


register(Scheme(
    'solana', 'Solana wallet', _solana_detect, _solana_canonical, _solana_check,
    chains='Solana', curves=['ed25519'],
    wallets=['Phantom', 'Solflare', 'Backpack'],
))


# ── the seam everything else uses ────────────────────────────────────

def scheme_of(address: str) -> Optional[Scheme]:
    """Which key type this address belongs to, or None."""
    value = (address or '').strip()
    if not value:
        return None
    for scheme in SCHEMES.values():
        if scheme.detect(value):
            return scheme
    return None


def scheme_name(address: str) -> Optional[str]:
    scheme = scheme_of(address)
    return scheme.name if scheme else None


def is_account(address: str) -> bool:
    """Can this string be an account here — i.e. can a signature prove it?"""
    return scheme_of(address) is not None


def is_evm(address: str) -> bool:
    """On-chain moves (deposits, withdrawals) need an EVM address specifically."""
    scheme = scheme_of(address)
    return bool(scheme and scheme.evm)


def normalize(address: str) -> str:
    """The ledger key for an account.

    Falls back to a plain strip+lowercase for anything unrecognised, so old
    records and test fixtures (`0xAlice`) keep resolving the way they did.
    """
    value = (address or '').strip()
    scheme = scheme_of(value)
    return scheme.canonical(value) if scheme else value.lower()


def verify(message: str, signature: str, address: str) -> Dict:
    """Did the holder of `address` sign `message`?

    `{'ok': True, 'signer': <canonical address>, 'scheme':, 'curve':}` or
    `{'ok': False, 'error': …}`. The scheme is chosen by the address, so a
    caller cannot pick a weaker verifier by claiming a different key type.
    """
    scheme = scheme_of(address)
    if not scheme:
        return {'ok': False,
                'error': f'unrecognised address format: {(address or "")[:16]}…'
                         if address else 'address is required'}
    result = dict(scheme.check(message, signature, address))
    result.setdefault('ok', False)
    result['scheme'] = scheme.name
    if result['ok']:
        result['signer'] = normalize(result.get('signer') or address)
    elif result.get('error') == 'unavailable':
        result['error'] = (f'this server cannot verify {scheme.label} signatures — '
                           + result.get('detail', 'missing dependency'))
    elif result.get('error') == 'wrong signer':
        result['error'] = (f"signature is from {result.get('signer')}, "
                           f'not {normalize(address)}')
    return result


def schemes() -> List[Dict]:
    """Every key type this build accepts — what the console renders its wallet
    picker from, so a new scheme shows up in the UI without a UI change."""
    return [s.describe() for s in SCHEMES.values()]


def describe(address: str) -> Dict:
    """What one address is, for the console and for `m prefi/auth_address`."""
    scheme = scheme_of(address)
    out = {'address': (address or '').strip(), 'known': bool(scheme),
           'normalized': normalize(address), 'scheme': scheme.name if scheme else None,
           'label': scheme.label if scheme else None,
           'evm': bool(scheme and scheme.evm)}
    decoded = ss58_decode(address)
    if decoded:
        out['ss58_prefix'] = decoded['prefix']
        out['pubkey'] = '0x' + decoded['pubkey'].hex()
        out['bittensor'] = ss58_encode(decoded['pubkey'], BITTENSOR_SS58_FORMAT)
    return out
