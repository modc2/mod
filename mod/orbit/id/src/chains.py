"""What each chain calls an address, and what a wallet actually signs.

Every entry here answers three questions the same way:

  parse    — is this string an address on this chain, and what is its canonical
             spelling? (Ethereum lowercases; Solana does not, because base58 is
             case-significant and mangling it would break the key.)
  digest   — what bytes does a wallet hash before signing? This is the part
             nobody agrees on. Ethereum prefixes "\\x19Ethereum Signed Message:\\n",
             Bitcoin prefixes "\\x18Bitcoin Signed Message:\\n" and hashes twice,
             Cosmos wraps the text in an Amino JSON document, Sui prepends an
             intent and hashes with Blake2b, and Solana signs the bytes as they
             are. Getting this wrong doesn't fail loudly — it just never matches.
  match    — does the key behind the signature produce this address?

One address, many chains: an EVM address is the same 20 bytes on Ethereum, Base,
Arbitrum, Optimism, Polygon and every other EVM network, so this module records
one `ethereum` identity rather than pretending they are different accounts. The
same is true of a Cosmos key, which prints under a different prefix per chain —
those *are* recorded separately, because the printed address is what a user
recognises, and `equivalents()` reports the rest.

Signing is included for Ethereum, Bitcoin and Solana so the test suite can prove
the verifier against signatures it did not itself produce, and so `m id/demo`
can show the whole flow without a wallet.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .crypto import base58, bech32, ed25519, secp256k1
from .crypto.keccak import keccak256
from .crypto.ripemd160 import ripemd160


class ProofError(ValueError):
    """A signature that does not prove what it claims to prove."""


class AddressError(ValueError):
    """A string that is not an address on the chain it was offered for."""


# ── helpers ──────────────────────────────────────────────────────────────

def hash160(data: bytes) -> bytes:
    return ripemd160(hashlib.sha256(data).digest())


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def blake2b256(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=32).digest()


def varint(value: int) -> bytes:
    if value < 0xFD:
        return bytes([value])
    if value <= 0xFFFF:
        return b'\xfd' + value.to_bytes(2, 'little')
    if value <= 0xFFFFFFFF:
        return b'\xfe' + value.to_bytes(4, 'little')
    return b'\xff' + value.to_bytes(8, 'little')


def uleb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def unhex(text: str, expect: Tuple[int, ...] = ()) -> bytes:
    """Signatures arrive hex from EVM wallets, base64 from Bitcoin, base58 from Solana.

    The encodings overlap — an 88-character base58 Solana signature is also
    valid base64, and decodes to 66 bytes of nonsense — so when the caller knows
    what length it is expecting, every encoding is tried and the one that yields
    a plausible length wins. Guessing by shape alone silently corrupts.
    """
    if text is None:
        raise ProofError('no signature given')
    cleaned = text.strip()
    if cleaned.startswith(('0x', '0X')):
        try:
            return bytes.fromhex(cleaned[2:])
        except ValueError as exc:
            raise ProofError(f'signature is not hex: {exc}') from exc

    candidates = []
    for decode in (lambda s: bytes.fromhex(s),
                   lambda s: base64.b64decode(s, validate=True),
                   base58.decode):
        try:
            candidates.append(decode(cleaned))
        except (ValueError, binascii.Error):
            continue
    if not candidates:
        raise ProofError('signature is not hex, base64 or base58')
    for candidate in candidates:
        if len(candidate) in expect:
            return candidate
    return candidates[0]


def _is_hex(text: str, length: int) -> bool:
    body = text[2:] if text.startswith('0x') else text
    if len(body) != length * 2:
        return False
    try:
        bytes.fromhex(body)
        return True
    except ValueError:
        return False


def eip55(address_bytes: bytes) -> str:
    """The mixed-case checksum. Displayed, never stored — storage is lowercase."""
    body = address_bytes.hex()
    marks = keccak256(body.encode()).hex()
    return '0x' + ''.join(c.upper() if c.isalpha() and int(marks[i], 16) >= 8 else c
                          for i, c in enumerate(body))


def ss58_encode(public_key: bytes, prefix: int) -> str:
    if prefix < 64:
        head = bytes([prefix])
    else:
        head = bytes([((prefix & 0xFC) >> 2) | 0x40, (prefix >> 8) | ((prefix & 0x03) << 6)])
    body = head + public_key
    checksum = hashlib.blake2b(b'SS58PRE' + body, digest_size=64).digest()[:2]
    return base58.encode(body + checksum)


def ss58_decode(address: str) -> Tuple[int, bytes]:
    raw = base58.decode(address)
    if len(raw) < 35:
        raise AddressError('SS58 address too short')
    if raw[0] & 0x40:
        prefix = ((raw[0] & 0x3F) << 2) | (raw[1] >> 6) | ((raw[1] & 0x3F) << 8)
        head, body = raw[:2], raw[2:-2]
    else:
        prefix, head, body = raw[0], raw[:1], raw[1:-2]
    checksum = hashlib.blake2b(b'SS58PRE' + head + body, digest_size=64).digest()[:2]
    if checksum != raw[-2:]:
        raise AddressError('SS58 checksum mismatch')
    if len(body) != 32:
        raise AddressError(f'SS58 public key is {len(body)} bytes, expected 32')
    return prefix, body


# ── the chain table ──────────────────────────────────────────────────────

@dataclass
class Chain:
    name: str
    title: str
    curve: str
    scheme: str                       # what the wallet signs, in one line
    parse: Callable[[str], str]
    check: Callable[..., Dict[str, Any]]
    aliases: List[str] = field(default_factory=list)
    networks: List[str] = field(default_factory=list)
    wallets: List[str] = field(default_factory=list)
    needs_pubkey: bool = False
    note: str = ''

    def card(self) -> Dict[str, Any]:
        return {'chain': self.name, 'title': self.title, 'curve': self.curve,
                'scheme': self.scheme, 'aliases': self.aliases,
                'networks': self.networks, 'wallets': self.wallets,
                'needs_pubkey': self.needs_pubkey, 'note': self.note}


CHAINS: Dict[str, Chain] = {}
_ALIASES: Dict[str, str] = {}


def register(chain: Chain) -> Chain:
    CHAINS[chain.name] = chain
    _ALIASES[chain.name] = chain.name
    for alias in chain.aliases:
        _ALIASES[alias.lower()] = chain.name
    return chain


def get(name: str) -> Chain:
    key = (name or '').strip().lower()
    if key not in _ALIASES:
        raise AddressError(f'unknown chain {name!r} — try one of: {", ".join(sorted(CHAINS))}')
    return CHAINS[_ALIASES[key]]


def known() -> List[Dict[str, Any]]:
    return [chain.card() for chain in CHAINS.values()]


# ── ethereum and every EVM network ───────────────────────────────────────

def _eth_parse(address: str) -> str:
    text = address.strip()
    if not _is_hex(text, 20):
        raise AddressError('an Ethereum address is 20 hex bytes, 0x-prefixed')
    return '0x' + (text[2:] if text.startswith('0x') else text).lower()


def _eip191_digest(message: str) -> bytes:
    body = message.encode()
    return keccak256(b'\x19Ethereum Signed Message:\n' + str(len(body)).encode() + body)


def _recover_evm(digest: bytes, signature: bytes) -> Tuple[bytes, bool]:
    if len(signature) != 65:
        raise ProofError(f'an EIP-191 signature is 65 bytes, got {len(signature)}')
    r, s = secp256k1.split(signature)
    v = signature[64]
    if v in (27, 28):
        v -= 27           # MetaMask and every hardware wallet emit 27/28
    elif v in (0, 1):
        pass
    elif v >= 35:
        v = (v - 35) % 2  # an EIP-155 v that leaked out of a transaction signer
    else:
        raise ProofError(f'signature recovery byte {signature[64]} is not one of 0,1,27,28')
    low_s = s <= secp256k1.N // 2
    point = secp256k1.recover(digest, r, s, v, allow_high_s=True)
    return secp256k1.uncompressed(point), low_s


def _eth_check(address: str, message: str, signature: str, **_: Any) -> Dict[str, Any]:
    want = _eth_parse(address)
    raw, low_s = _recover_evm(_eip191_digest(message), unhex(signature, (65,)))
    got = '0x' + keccak256(raw)[-20:].hex()
    if got != want:
        raise ProofError(f'signature is by {eip55(bytes.fromhex(got[2:]))}, not {eip55(bytes.fromhex(want[2:]))}')
    return {'address': want, 'display': eip55(bytes.fromhex(want[2:])),
            'pubkey': '0x' + raw.hex(), 'low_s': low_s,
            'detail': 'address recovered from the signature, no public key needed'}


register(Chain(
    name='ethereum', title='Ethereum & every EVM chain', curve='secp256k1',
    scheme='EIP-191: keccak256("\\x19Ethereum Signed Message:\\n" + len + text), key recovered from the 65-byte signature',
    parse=_eth_parse, check=_eth_check,
    aliases=['eth', 'evm', 'base', 'arbitrum', 'optimism', 'polygon', 'bsc', 'bnb',
             'avalanche', 'avax', 'gnosis', 'zksync', 'scroll', 'linea', 'blast',
             'celo', 'fantom', 'mantle', 'sepolia'],
    networks=['ethereum', 'base', 'arbitrum', 'optimism', 'polygon', 'bnb chain',
              'avalanche c-chain', 'gnosis', 'zksync era', 'scroll', 'linea', 'and the rest'],
    wallets=['MetaMask', 'Rabby', 'Coinbase Wallet', 'Ledger', 'Trezor', 'Safe (EOA owners)'],
    note='One key, one address, every EVM network — so this is recorded once, not once per chain.'))


# ── tron ─────────────────────────────────────────────────────────────────

def _tron_parse(address: str) -> str:
    text = address.strip()
    if not text.startswith('T'):
        raise AddressError('a Tron address starts with T')
    payload = base58.check_decode(text)
    if len(payload) != 21 or payload[0] != 0x41:
        raise AddressError('a Tron address is 0x41 + 20 bytes, base58check')
    return text


def _tron_check(address: str, message: str, signature: str, **_: Any) -> Dict[str, Any]:
    want = _tron_parse(address)
    body = message.encode()
    digest = keccak256(b'\x19TRON Signed Message:\n' + str(len(body)).encode() + body)
    raw, low_s = _recover_evm(digest, unhex(signature, (65,)))
    got = base58.check_encode(b'\x41' + keccak256(raw)[-20:])
    if got != want:
        raise ProofError(f'signature is by {got}, not {want}')
    return {'address': want, 'display': want, 'pubkey': '0x' + raw.hex(),
            'low_s': low_s, 'detail': 'TIP-191 recovery matched the base58check address'}


register(Chain(
    name='tron', title='Tron', curve='secp256k1',
    scheme='TIP-191: keccak256("\\x19TRON Signed Message:\\n" + len + text), key recovered',
    parse=_tron_parse, check=_tron_check, aliases=['trx'], networks=['tron'],
    wallets=['TronLink', 'Ledger']))


# ── bitcoin, litecoin, dogecoin ──────────────────────────────────────────

@dataclass
class BitcoinLike:
    name: str
    magic: bytes
    p2pkh: int
    p2sh: int
    hrp: Optional[str]


_BITCOIN = BitcoinLike('bitcoin', b'Bitcoin Signed Message:\n', 0x00, 0x05, 'bc')
_LITECOIN = BitcoinLike('litecoin', b'Litecoin Signed Message:\n', 0x30, 0x32, 'ltc')
_DOGECOIN = BitcoinLike('dogecoin', b'Dogecoin Signed Message:\n', 0x1E, 0x16, None)


def _btc_addresses(public_key: bytes, spec: BitcoinLike) -> Dict[str, str]:
    """Every address form this key can be printed as."""
    point = secp256k1.decompress(public_key)
    compressed = secp256k1.compress(point)
    uncompressed = b'\x04' + secp256k1.uncompressed(point)
    forms = {
        'p2pkh': base58.check_encode(bytes([spec.p2pkh]) + hash160(compressed)),
        'p2pkh-uncompressed': base58.check_encode(bytes([spec.p2pkh]) + hash160(uncompressed)),
        'p2sh-p2wpkh': base58.check_encode(
            bytes([spec.p2sh]) + hash160(b'\x00\x14' + hash160(compressed))),
    }
    if spec.hrp:
        forms['p2wpkh'] = bech32.encode_segwit(spec.hrp, 0, hash160(compressed))
    return forms


def _btc_parse_for(spec: BitcoinLike):
    def parse(address: str) -> str:
        text = address.strip()
        if spec.hrp and text.lower().startswith(spec.hrp + '1'):
            version, program = bech32.decode_segwit(spec.hrp, text.lower())
            if version is None:
                raise AddressError(f'not a valid {spec.name} bech32 address')
            if version == 1:
                raise AddressError(
                    'taproot (bc1p…) addresses have no settled message-signing standard '
                    '(BIP-322 is not widely shipped) — link a bc1q, 1… or 3… address instead')
            return text.lower()
        payload = base58.check_decode(text)
        if len(payload) != 21 or payload[0] not in (spec.p2pkh, spec.p2sh):
            raise AddressError(f'not a {spec.name} address')
        return text
    return parse


def _btc_check_for(spec: BitcoinLike):
    parse = _btc_parse_for(spec)

    def check(address: str, message: str, signature: str, **_: Any) -> Dict[str, Any]:
        want = parse(address)
        body = message.encode()
        digest = sha256d(varint(len(spec.magic)) + spec.magic + varint(len(body)) + body)
        raw = unhex(signature, (65,))
        if len(raw) != 65:
            raise ProofError(
                f'a signed message is 65 bytes (header + r + s), got {len(raw)} — '
                'paste the base64 blob the wallet gave you, not the transaction')
        header = raw[0]
        if not 27 <= header <= 42:
            raise ProofError(f'header byte {header} outside the signed-message range 27-42')
        r, s = secp256k1.split(raw[1:])
        recovery = (header - 27) & 3
        low_s = s <= secp256k1.N // 2
        point = secp256k1.recover(digest, r, s, recovery, allow_high_s=True)
        forms = _btc_addresses(secp256k1.compress(point), spec)
        for form, candidate in forms.items():
            if candidate == want:
                return {'address': want, 'display': want,
                        'pubkey': '0x' + secp256k1.compress(point).hex(),
                        'low_s': low_s, 'form': form,
                        'equivalents': {k: v for k, v in forms.items() if v != want},
                        'detail': f'recovered key prints as this address in {form} form'}
        raise ProofError(
            f'the signature is by a key that prints as {", ".join(sorted(set(forms.values())))} '
            f'— none of which is {want}')
    return check


for _spec, _title, _aliases, _wallets in (
    (_BITCOIN, 'Bitcoin', ['btc', 'xbt'], ['Electrum', 'Sparrow', 'Bitcoin Core', 'Ledger', 'Trezor']),
    (_LITECOIN, 'Litecoin', ['ltc'], ['Electrum-LTC', 'Ledger']),
    (_DOGECOIN, 'Dogecoin', ['doge'], ['Dogecoin Core', 'Ledger']),
):
    register(Chain(
        name=_spec.name, title=_title, curve='secp256k1',
        scheme=f'sha256d(varint+"{_spec.magic.decode().strip()}"+varint+text), key recovered from the 65-byte header+r+s blob',
        parse=_btc_parse_for(_spec), check=_btc_check_for(_spec), aliases=_aliases,
        networks=[_spec.name], wallets=_wallets,
        note='legacy (1…), nested segwit (3…) and native segwit (bc1q…) all verify; '
             'taproot is refused, because message signing for it is not settled'
             if _spec.hrp else 'no segwit — legacy and P2SH addresses only'))


# ── cosmos and its prefixes ──────────────────────────────────────────────

_COSMOS_PREFIXES = {
    'cosmos': 'Cosmos Hub', 'osmo': 'Osmosis', 'celestia': 'Celestia',
    'juno': 'Juno', 'akash': 'Akash', 'stars': 'Stargaze', 'neutron': 'Neutron',
    'kava': 'Kava', 'axelar': 'Axelar', 'dydx': 'dYdX', 'tia': 'Celestia',
    'noble': 'Noble', 'sei': 'Sei',
}


def _cosmos_parse(address: str) -> str:
    text = address.strip().lower()
    hrp, payload = bech32.decode_data(text)
    if hrp is None:
        raise AddressError('not a bech32 address')
    if len(payload) != 20:
        raise AddressError(f'a Cosmos account address holds 20 bytes, this holds {len(payload)}')
    if hrp.endswith(('valoper', 'valcons')):
        raise AddressError('that is a validator operator address, not an account')
    return text


def _adr036_document(signer: str, message: str) -> bytes:
    """The Amino JSON document a Cosmos wallet signs for an off-chain message.

    Key order is the signature: Amino sorts keys, and a single character out of
    place changes the hash, so this is written as a literal rather than dumped
    from a dict whose order could drift.
    """
    data = base64.b64encode(message.encode()).decode()
    return (
        '{"account_number":"0","chain_id":"","fee":{"amount":[],"gas":"0"},'
        '"memo":"","msgs":[{"type":"sign/MsgSignData","value":'
        f'{{"data":"{data}","signer":"{signer}"}}}}],"sequence":"0"}}'
    ).encode()


def _cosmos_check(address: str, message: str, signature: str,
                  pubkey: str = None, **_: Any) -> Dict[str, Any]:
    want = _cosmos_parse(address)
    if not pubkey:
        raise ProofError(
            'Cosmos signatures are not recoverable — send the public key too '
            '(Keplr returns it as pub_key.value alongside the signature)')
    key = unhex(pubkey, (33, 64, 65))
    if len(key) == 33:
        compressed = key
    else:
        compressed = secp256k1.compress(secp256k1.decompress(key))
    hrp, _ = bech32.decode_data(want)
    derived = bech32.encode_data(hrp, hash160(compressed))
    if derived != want:
        raise ProofError(f'that public key belongs to {derived}, not {want}')
    raw = unhex(signature, (64,))
    if len(raw) != 64:
        raise ProofError(f'an ADR-036 signature is 64 bytes, got {len(raw)}')
    digest = hashlib.sha256(_adr036_document(want, message)).digest()
    if not secp256k1.verify(digest, raw, compressed):
        raise ProofError('signature does not verify against the ADR-036 sign document')
    return {'address': want, 'display': want, 'pubkey': '0x' + compressed.hex(),
            'low_s': secp256k1.split(raw)[1] <= secp256k1.N // 2,
            'equivalents': {p: bech32.encode_data(p, hash160(compressed))
                            for p in ('cosmos', 'osmo', 'celestia')},
            'detail': 'ADR-036 sign document verified, address derived from the key'}


register(Chain(
    name='cosmos', title='Cosmos (Hub, Osmosis, Celestia, …)', curve='secp256k1',
    scheme='ADR-036: sha256 of the Amino JSON sign document wrapping your text, verified against the sent public key',
    parse=_cosmos_parse, check=_cosmos_check, needs_pubkey=True,
    aliases=['atom', 'osmosis', 'celestia', 'keplr', 'osmo', 'juno', 'akash',
             'dydx', 'sei', 'neutron', 'noble'],
    networks=sorted(set(_COSMOS_PREFIXES.values())),
    wallets=['Keplr', 'Leap', 'Cosmostation', 'Ledger'],
    note='one key prints under every chain prefix — the equivalents are reported '
         'so the same key is not linked twice by accident'))


# ── ed25519 chains ───────────────────────────────────────────────────────

def _ed_verify(message: bytes, signature: str, public_key: bytes) -> bytes:
    raw = unhex(signature, (64, 65, 97))
    if len(raw) == 97 and raw[0] in (0, 1, 2):
        raw = raw[1:65]                     # Sui serialises flag ‖ signature ‖ public key
    elif len(raw) == 65 and raw[0] in (0, 1, 2):
        raw = raw[1:]
    if len(raw) != 64:
        raise ProofError(f'an Ed25519 signature is 64 bytes, got {len(raw)}')
    if not ed25519.verify(message, raw, public_key):
        raise ProofError('the signature does not verify against this address')
    return raw


def _solana_parse(address: str) -> str:
    text = address.strip()
    try:
        raw = base58.decode(text)
    except ValueError as exc:
        raise AddressError(f'a Solana address is base58: {exc}') from exc
    if len(raw) != 32:
        raise AddressError(f'a Solana address decodes to 32 bytes, this is {len(raw)}')
    return text


def _solana_check(address: str, message: str, signature: str, **_: Any) -> Dict[str, Any]:
    want = _solana_parse(address)
    key = base58.decode(want)
    _ed_verify(message.encode(), signature, key)
    return {'address': want, 'display': want, 'pubkey': want,
            'detail': 'Ed25519 signature over the statement bytes, key is the address'}


register(Chain(
    name='solana', title='Solana', curve='ed25519',
    scheme='Ed25519 over the UTF-8 statement, unmodified (window.solana.signMessage)',
    parse=_solana_parse, check=_solana_check, aliases=['sol', 'phantom'],
    networks=['solana'], wallets=['Phantom', 'Solflare', 'Backpack', 'Ledger'],
    note='the address IS the public key, so nothing else has to be sent'))


def _sui_address(public_key: bytes) -> str:
    return '0x' + blake2b256(b'\x00' + public_key).hex()


def _sui_parse(address: str) -> str:
    text = address.strip().lower()
    if not _is_hex(text, 32):
        raise AddressError('a Sui address is 32 hex bytes, 0x-prefixed')
    return text if text.startswith('0x') else '0x' + text


def _sui_check(address: str, message: str, signature: str,
               pubkey: str = None, **_: Any) -> Dict[str, Any]:
    want = _sui_parse(address)
    raw_sig = unhex(signature, (64, 65, 97))
    if not pubkey and len(raw_sig) == 97 and raw_sig[0] == 0:
        pubkey = raw_sig[65:].hex()          # flag ‖ signature ‖ public key
    if not pubkey:
        raise ProofError(
            'a Sui address is a hash of the key, so the key has to be sent too — '
            'pass the wallet\'s serialised signature and it will be read out of that')
    key = unhex(pubkey, (32, 33))
    if len(key) == 33 and key[0] == 0:
        key = key[1:]
    if _sui_address(key) != want:
        raise ProofError(f'that public key hashes to {_sui_address(key)}, not {want}')
    body = message.encode()
    intent = bytes([3, 0, 0]) + uleb128(len(body)) + body
    _ed_verify(blake2b256(intent), signature, key)
    return {'address': want, 'display': want, 'pubkey': '0x' + key.hex(),
            'detail': 'personal-message intent hashed with Blake2b, then Ed25519 verified'}


register(Chain(
    name='sui', title='Sui', curve='ed25519',
    scheme='Ed25519 over blake2b256(intent(3,0,0) ‖ uleb128(len) ‖ text) — signPersonalMessage',
    parse=_sui_parse, check=_sui_check, aliases=['suix'], networks=['sui'],
    wallets=['Sui Wallet', 'Suiet', 'Ethos'], needs_pubkey=True,
    note='Ed25519 accounts only — multisig and zkLogin addresses are not verified here'))


def _aptos_address(public_key: bytes) -> str:
    return '0x' + hashlib.sha3_256(public_key + b'\x00').hexdigest()


def _aptos_parse(address: str) -> str:
    text = address.strip().lower()
    body = text[2:] if text.startswith('0x') else text
    if not body or len(body) > 64:
        raise AddressError('an Aptos address is up to 32 hex bytes')
    try:
        bytes.fromhex(body.zfill(64))
    except ValueError as exc:
        raise AddressError(f'not hex: {exc}') from exc
    return '0x' + body.zfill(64)


def _aptos_check(address: str, message: str, signature: str,
                 pubkey: str = None, **_: Any) -> Dict[str, Any]:
    want = _aptos_parse(address)
    if not pubkey:
        raise ProofError('an Aptos address is a hash of the key — send the public key too')
    key = unhex(pubkey, (32,))
    if _aptos_address(key) != want:
        raise ProofError(f'that public key hashes to {_aptos_address(key)}, not {want}')
    _ed_verify(message.encode(), signature, key)
    return {'address': want, 'display': want, 'pubkey': '0x' + key.hex(),
            'detail': 'Ed25519 over the statement bytes; address = sha3_256(key ‖ 0x00)'}


register(Chain(
    name='aptos', title='Aptos', curve='ed25519',
    scheme='Ed25519 over the statement bytes; address = sha3_256(public key ‖ 0x00)',
    parse=_aptos_parse, check=_aptos_check, aliases=['apt'], networks=['aptos'],
    wallets=['Petra', 'Martian', 'Pontem'], needs_pubkey=True,
    note='Petra wraps what you sign in "APTOS\\nmessage: …\\nnonce: …" — paste the '
         'fullMessage it returns as the statement if the raw text does not verify'))


def _near_parse(address: str) -> str:
    text = address.strip().lower()
    if _is_hex(text, 32) and not text.startswith('0x'):
        return text
    if text.endswith(('.near', '.testnet')) and 2 <= len(text) <= 64:
        return text
    raise AddressError('a NEAR account is a 64-hex implicit account or a name ending .near')


def _near_check(address: str, message: str, signature: str,
                pubkey: str = None, **_: Any) -> Dict[str, Any]:
    want = _near_parse(address)
    if want.endswith(('.near', '.testnet')):
        if not pubkey:
            raise ProofError('a named NEAR account can hold many keys — send the public key '
                             'you signed with, and check it on chain yourself')
        key = base58.decode(pubkey.split(':')[-1])
        _ed_verify(message.encode(), signature, key)
        return {'address': want, 'display': want, 'pubkey': 'ed25519:' + base58.encode(key),
                'weak': True,
                'detail': 'signature verified, but that this key has access to this named '
                          'account is not checked here — only the chain can say that'}
    key = bytes.fromhex(want)
    _ed_verify(message.encode(), signature, key)
    return {'address': want, 'display': want, 'pubkey': 'ed25519:' + base58.encode(key),
            'detail': 'implicit account — the account name is the public key'}


register(Chain(
    name='near', title='NEAR', curve='ed25519',
    scheme='Ed25519 over the statement bytes',
    parse=_near_parse, check=_near_check, networks=['near'],
    wallets=['MyNearWallet', 'Meteor', 'Ledger'],
    note='implicit (64-hex) accounts verify completely; a named account only proves the '
         'key, not that the chain still grants that key access'))


def _substrate_parse(address: str) -> str:
    text = address.strip()
    ss58_decode(text)
    return text


def _substrate_check(address: str, message: str, signature: str, **_: Any) -> Dict[str, Any]:
    want = _substrate_parse(address)
    prefix, key = ss58_decode(want)
    raw = unhex(signature, (64, 65))
    if len(raw) == 65 and raw[0] in (0, 1, 2):
        raw = raw[1:]
    if len(raw) != 64:
        raise ProofError(f'a Substrate signature is 64 bytes, got {len(raw)}')
    body = message.encode()
    for label, payload in (('raw bytes', body), ('<Bytes>-wrapped', b'<Bytes>' + body + b'</Bytes>')):
        if ed25519.verify(payload, raw, key):
            return {'address': want, 'display': want, 'pubkey': '0x' + key.hex(),
                    'ss58_prefix': prefix, 'wrapping': label,
                    'detail': f'Ed25519 verified over the {label} statement'}
    raise ProofError(
        'not a valid Ed25519 signature for this account. Polkadot-JS defaults to '
        'sr25519 keys, whose Schnorr signatures this module does not verify — link '
        'an Ed25519 account, or use the Ethereum key this fleet already signs with')


register(Chain(
    name='substrate', title='Polkadot / Kusama / Bittensor (SS58)', curve='ed25519',
    scheme='Ed25519 over the statement, raw or <Bytes>-wrapped as the browser extension sends it',
    parse=_substrate_parse, check=_substrate_check,
    aliases=['polkadot', 'dot', 'kusama', 'ksm', 'bittensor', 'tao', 'ss58'],
    networks=['polkadot', 'kusama', 'bittensor', 'any SS58 chain'],
    wallets=['Polkadot-JS (ed25519 accounts)', 'Talisman', 'Ledger'],
    note='sr25519 — the Polkadot-JS default — is NOT verified here; it needs Schnorrkel, '
         'which no pure-Python implementation does correctly. Ed25519 accounts work.'))


# ── the surface the rest of the module uses ──────────────────────────────

def parse(chain: str, address: str) -> str:
    return get(chain).parse(address)


def verify(chain: str, address: str, message: str, signature: str,
           pubkey: str = None) -> Dict[str, Any]:
    """Prove that whoever signed `message` holds the key behind `address`."""
    entry = get(chain)
    try:
        result = entry.check(address, message, signature, pubkey=pubkey)
    except (ProofError, AddressError):
        raise
    except ValueError as exc:
        # the curve arithmetic rejects malformed scalars and points with its own
        # wording; callers should only ever have to catch one kind of failure
        raise ProofError(f'the signature is malformed: {exc}') from exc
    result.update({'ok': True, 'chain': entry.name, 'curve': entry.curve,
                   'scheme': entry.scheme, 'strength': 'key'})
    return result


def equivalents(chain: str, address: str) -> Dict[str, str]:
    """Other ways the same key is printed — Cosmos prefixes, Bitcoin script forms."""
    entry = get(chain)
    if entry.name == 'cosmos':
        hrp, payload = bech32.decode_data(parse(chain, address))
        return {prefix: bech32.encode_data(prefix, payload)
                for prefix in _COSMOS_PREFIXES if prefix != hrp}
    return {}
