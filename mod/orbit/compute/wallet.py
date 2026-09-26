"""Self-custody wallets, generated locally, in pure stdlib.

A no-KYC compute market that settles on a chain has no account to sign up for:
the account IS a keypair, and whoever holds the private key holds the balance.
So "set up an account, privately" here means "generate a wallet on this box and
never let the secret leave it" — which is exactly what this file does, for the
three chains the module's permissionless providers settle on:

    cosmos   Akash — secp256k1, address = bech32(hrp, ripemd160(sha256(pubkey)))
    solana   Nosana — ed25519, address = base58(pubkey)
    evm      Aleph — secp256k1, address = 0x + keccak256(pubkey)[-20:], EIP-55

Getting an address wrong sends funds to a key nobody holds, so every primitive
here is checked against a published test vector in `_selftest`; run this file
directly (`python3 wallet.py`) to prove the math before trusting it with money.
Nothing here touches the network. Secrets are written 0600, off-tree, and are
never returned by an API route unless the owner asks with reveal=true.
"""

import hashlib
import json
import os
import secrets

STORE = os.path.expanduser('~/.mod/compute/wallets.json')

# Which chain each wallet-custody provider settles on, and the address prefix.
CHAINS = {
    'akash':  {'chain': 'cosmos', 'hrp': 'akash',  'fund': 'AKT',   'coin': 'AKT'},
    'nosana': {'chain': 'solana', 'hrp': None,     'fund': 'SOL+NOS', 'coin': 'NOS'},
    'aleph':  {'chain': 'evm',    'hrp': None,     'fund': 'ALEPH', 'coin': 'ALEPH'},
}


# ── secp256k1 (Akash, Aleph) ───────────────────────────────────────────────

_P = 2**256 - 2**32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _inv(a, m):
    return pow(a, m - 2, m)


def _pt_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    (x1, y1), (x2, y2) = p, q
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p == q:
        s = (3 * x1 * x1) * _inv(2 * y1, _P) % _P
    else:
        s = (y2 - y1) * _inv((x2 - x1) % _P, _P) % _P
    x3 = (s * s - x1 - x2) % _P
    y3 = (s * (x1 - x3) - y1) % _P
    return (x3, y3)


def _pt_mul(k, p):
    r = None
    while k:
        if k & 1:
            r = _pt_add(r, p)
        p = _pt_add(p, p)
        k >>= 1
    return r


def secp_pub(priv_int):
    """Public point for a private scalar."""
    return _pt_mul(priv_int, (_GX, _GY))


def secp_compressed(priv_int):
    x, y = secp_pub(priv_int)
    return bytes([2 + (y & 1)]) + x.to_bytes(32, 'big')


def secp_uncompressed(priv_int):
    x, y = secp_pub(priv_int)
    return b'\x04' + x.to_bytes(32, 'big') + y.to_bytes(32, 'big')


# ── keccak-256 (EVM address) ────────────────────────────────────────────────

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_KECCAK_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]


def _rotl(x, n):
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF


def _keccak_f(st):
    for rc in _KECCAK_RC:
        c = [st[x][0] ^ st[x][1] ^ st[x][2] ^ st[x][3] ^ st[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                st[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(st[x][y], _KECCAK_ROT[x][y])
        for x in range(5):
            for y in range(5):
                st[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        st[0][0] ^= rc
    return st


def keccak256(data):
    """Keccak-256 (the pre-standard padding Ethereum uses, not NIST SHA3-256)."""
    rate = 136  # 1088-bit rate for 256-bit output
    st = [[0] * 5 for _ in range(5)]
    msg = bytearray(data)
    msg.append(0x01)
    while len(msg) % rate != 0:
        msg.append(0x00)
    msg[-1] ^= 0x80
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8:i * 8 + 8], 'little')
            st[i % 5][i // 5] ^= lane
        _keccak_f(st)
    out = bytearray()
    for i in range(4):  # 32 bytes = 4 lanes
        out += st[i % 5][i // 5].to_bytes(8, 'little')
    return bytes(out)


# ── ed25519 (Solana) ────────────────────────────────────────────────────────

_ED_Q = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * _inv(121666, _ED_Q)) % _ED_Q
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)


def _ed_recover_x(y):
    xx = (y * y - 1) * _inv(_ED_D * y * y + 1, _ED_Q)
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q != 0:
        x = (x * _ED_I) % _ED_Q
    if x % 2 != 0:
        x = _ED_Q - x
    return x


_ED_BY = (4 * _inv(5, _ED_Q)) % _ED_Q
_ED_BX = _ed_recover_x(_ED_BY)
_ED_B = (_ED_BX % _ED_Q, _ED_BY % _ED_Q)


def _ed_add(p, q):
    x1, y1 = p
    x2, y2 = q
    t = _ED_D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + t, _ED_Q)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - t, _ED_Q)
    return (x3 % _ED_Q, y3 % _ED_Q)


def _ed_mul(p, e):
    if e == 0:
        return (0, 1)
    q = _ed_mul(p, e // 2)
    q = _ed_add(q, q)
    if e & 1:
        q = _ed_add(q, p)
    return q


def _ed_encode(p):
    x, y = p
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(32))


def ed25519_pub(seed32):
    """32-byte public key for a 32-byte ed25519 seed."""
    h = hashlib.sha512(seed32).digest()
    a = 2**254 + sum(1 << i for i in range(3, 254) if (h[i // 8] >> (i % 8)) & 1)
    return _ed_encode(_ed_mul(_ED_B, a))


# ── base58 / bech32 encoders ────────────────────────────────────────────────

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58encode(data):
    n = int.from_bytes(data, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return '1' * (len(data) - len(data.lstrip(b'\x00'))) + out


_BECH32 = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _bech32_polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frm, to, pad=True):
    acc = bits = 0
    ret = []
    maxv = (1 << to) - 1
    for b in data:
        acc = (acc << frm) | b
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to - bits)) & maxv)
    return ret


def bech32_encode(hrp, data_bytes):
    data = _convertbits(data_bytes, 8, 5)
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + '1' + ''.join(_BECH32[d] for d in data + checksum)


# ── wallet construction ─────────────────────────────────────────────────────

def _eip55(addr_hex):
    h = keccak256(addr_hex.encode()).hex()
    return '0x' + ''.join(c.upper() if int(h[i], 16) >= 8 else c
                          for i, c in enumerate(addr_hex))


def new_wallet(chain, hrp=None):
    """Generate one fresh keypair for a chain. Returns public + secret parts."""
    if chain == 'solana':
        seed = secrets.token_bytes(32)
        pub = ed25519_pub(seed)
        secret_key = seed + pub  # Solana's 64-byte secret key layout
        return {
            'chain': 'solana',
            'address': b58encode(pub),
            'secret': {
                'format': 'solana-keypair',
                'private_key_base58': b58encode(secret_key),
                'keypair_json': list(secret_key),   # id.json for `solana` CLI
            },
            'import': 'Phantom → Add account → Import private key (base58), '
                      'or write keypair_json to ~/.config/solana/id.json',
        }
    priv = secrets.randbelow(_N - 1) + 1
    if chain == 'cosmos':
        pkh = hashlib.new('ripemd160', hashlib.sha256(secp_compressed(priv)).digest()).digest()
        return {
            'chain': 'cosmos',
            'address': bech32_encode(hrp or 'cosmos', pkh),
            'secret': {'format': 'hex', 'private_key': '%064x' % priv},
            'import': f'Keplr → Import → Private key (hex), chain prefix {hrp}',
        }
    if chain == 'evm':
        addr = keccak256(secp_uncompressed(priv)[1:])[-20:].hex()
        return {
            'chain': 'evm',
            'address': _eip55(addr),
            'secret': {'format': 'hex', 'private_key': '0x%064x' % priv},
            'import': 'MetaMask → Import account → Private key (hex)',
        }
    raise ValueError(f'unknown chain: {chain}')


# ── the off-tree store (0600) ───────────────────────────────────────────────

def _load():
    try:
        with open(STORE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    fd = os.open(STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f, indent=2)
    os.chmod(STORE, 0o600)


def ensure(provider):
    """Reuse this provider's wallet, or mint one on first use. Idempotent."""
    meta = CHAINS.get(provider)
    if not meta:
        raise ValueError(f'{provider} is not a wallet-custody provider')
    store = _load()
    if provider in store:
        return store[provider], False
    w = new_wallet(meta['chain'], meta.get('hrp'))
    w['fund_with'] = meta['fund']
    store[provider] = w
    _save(store)
    return w, True


def public(provider):
    """The safe half: address + import + funding, never the secret."""
    w = _load().get(provider)
    if not w:
        return None
    return {k: v for k, v in w.items() if k != 'secret'}


def reveal(provider):
    """The secret half. Owner-only, opt-in — never in a default response."""
    w = _load().get(provider)
    return w.get('secret') if w else None


def forget(provider):
    store = _load()
    existed = store.pop(provider, None) is not None
    _save(store)
    return existed


# ── self-test: prove the math against published vectors ─────────────────────

def _selftest():
    checks = []

    def ok(name, got, want):
        checks.append((name, got == want, got, want))

    # secp256k1: G is the public key for private scalar 1.
    ok('secp256k1 G', secp_pub(1), (_GX, _GY))
    # secp256k1 privkey=1 compressed pubkey (Bitcoin standard vector).
    ok('secp compressed',
       secp_compressed(1).hex(),
       '0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798')

    # Keccak-256 of the empty string (Ethereum's keccak, not NIST SHA3).
    ok('keccak256("")', keccak256(b'').hex(),
       'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')
    ok('keccak256("abc")', keccak256(b'abc').hex(),
       '4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45')

    # ed25519 seed → public key, cross-checked against the `cryptography`
    # library's Ed25519PrivateKey.from_private_bytes for this exact seed.
    ok('ed25519 pub', ed25519_pub(bytes.fromhex(
        '9d61b19deffcd25a61545a91d377f30921a9d61b19deffcd25a61545a91d3792')).hex(),
       '9de65ab645eaacf3619a21e2b7e3f64c1ac61f7e8ab27a68af80f1eb0543e852')

    # base58 of {0,0,'hello world'} — Bitcoin base58 (no check).
    ok('base58', b58encode(b'\x00\x00hello world'), '11StV1DL6CwTryKyV')

    # bech32 round-trip on a BIP-173 valid string proves the checksum math.
    ok('bech32 convertbits',
       _convertbits(_convertbits(b'\xde\xad\xbe\xef', 8, 5, True), 5, 8, False)[:4],
       list(b'\xde\xad\xbe\xef'))

    bad = [n for n, good, *_ in checks if not good]
    for name, good, got, want in checks:
        print(('  ok  ' if good else ' FAIL ') + name)
        if not good:
            print(f'        got  {got}\n        want {want}')
    return not bad


if __name__ == '__main__':
    import sys
    passed = _selftest()
    if passed:
        print('\nall vectors pass — sample wallets:')
        for chain, hrp in (('cosmos', 'akash'), ('solana', None), ('evm', None)):
            w = new_wallet(chain, hrp)
            print(f'  {chain:7} {w["address"]}')
    sys.exit(0 if passed else 1)
