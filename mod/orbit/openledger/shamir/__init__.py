"""
shamir — Shamir's Secret Sharing over GF(256), pure python, zero deps.

A secret is split into `n` shares so that any `k` of them rebuild it and any
`k-1` reveal nothing. Each byte of the secret is the constant term of its own
random degree-(k-1) polynomial; share `x` holds that polynomial evaluated at x.

Share format (identical to orbit/secretshare — this file is a vendored copy of
its engine — so pieces move freely between the two modules):

    ss1.<set>.<k>.<x>.<payload>

    set      8 hex chars, random per split — shares from different splits
             refuse to combine instead of producing garbage
    k        threshold
    x        share index, 1..255
    payload  base64url (no padding) of the share bytes

The split input is `secret || sha256(secret)[:4]`, so a combine with the wrong
or too few shares fails its checksum instead of silently returning noise. The
checksum is itself split, so a share leaks nothing about it.
"""
import base64
import hashlib
import secrets as _rand

VERSION = 'ss1'
CHECK = 4
MAX_SHARES = 255

# ── GF(256), AES polynomial x^8+x^4+x^3+x+1, generator 3 ────────────────
EXP = [0] * 512
LOG = [0] * 256
_v = 1
for _i in range(255):
    EXP[_i] = _v
    LOG[_v] = _i
    _v ^= (_v << 1) ^ (0x11b if _v & 0x80 else 0)   # _v *= 3
    _v &= 0xff
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError('gf256 divide by zero')
    return 0 if a == 0 else EXP[(LOG[a] - LOG[b]) % 255]


class ShareError(ValueError):
    """Bad input: malformed share, mismatched set, too few shares, bad checksum."""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _checksum(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:CHECK]


# ── split / combine ──────────────────────────────────────────────────────

def split(secret, n: int = 5, k: int = 3) -> list:
    """Split `secret` (str or bytes) into `n` shares, any `k` of which rebuild it."""
    if isinstance(secret, str):
        secret = secret.encode()
    n, k = int(n), int(k)
    if not secret:
        raise ShareError('nothing to split: secret is empty')
    if not 2 <= k <= n <= MAX_SHARES:
        raise ShareError(f'need 2 <= k <= n <= {MAX_SHARES} (got k={k}, n={n})')
    data = secret + _checksum(secret)
    set_id = _rand.token_hex(4)
    ys = [bytearray(len(data)) for _ in range(n)]
    for i, byte in enumerate(data):
        coeffs = [byte] + [_rand.randbelow(256) for _ in range(k - 1)]
        for xi in range(1, n + 1):
            acc = 0
            for c in reversed(coeffs):          # Horner
                acc = _mul(acc, xi) ^ c
            ys[xi - 1][i] = acc
    return [f'{VERSION}.{set_id}.{k}.{x}.{_b64e(bytes(y))}'
            for x, y in zip(range(1, n + 1), ys)]


def parse(share: str) -> dict:
    """Decode one share string into {set, k, x, y}."""
    parts = (share or '').strip().split('.')
    if len(parts) != 5 or parts[0] != VERSION:
        raise ShareError(f'not a {VERSION} share: {share[:24]!r}')
    _, set_id, k, x, payload = parts
    try:
        k, x, y = int(k), int(x), _b64d(payload)
    except Exception:
        raise ShareError(f'corrupt share: {share[:24]!r}')
    if not (1 <= x <= MAX_SHARES and 2 <= k <= MAX_SHARES and len(y) > CHECK):
        raise ShareError(f'corrupt share: {share[:24]!r}')
    return {'set': set_id, 'k': k, 'x': x, 'y': y}


def combine(shares) -> bytes:
    """Rebuild the secret bytes from at least `k` shares of one split."""
    if isinstance(shares, str):
        shares = [s for s in shares.replace(',', '\n').split() if s.strip()]
    parsed = {}
    for s in shares:
        p = parse(s)
        parsed[p['x']] = p                      # duplicates collapse
    if not parsed:
        raise ShareError('no shares given')
    first = next(iter(parsed.values()))
    for p in parsed.values():
        if p['set'] != first['set']:
            raise ShareError('shares come from different splits')
        if p['k'] != first['k'] or len(p['y']) != len(first['y']):
            raise ShareError('shares disagree on threshold or length — corrupt share?')
    k = first['k']
    if len(parsed) < k:
        raise ShareError(f'need {k} shares, have {len(parsed)}')
    pts = list(parsed.values())[:k]
    xs = [p['x'] for p in pts]
    out = bytearray(len(first['y']))
    for i in range(len(out)):
        acc = 0
        for j, pj in enumerate(pts):            # Lagrange at x=0
            num, den = 1, 1
            for m, xm in enumerate(xs):
                if m != j:
                    num = _mul(num, xm)
                    den = _mul(den, xm ^ pj['x'])
            acc ^= _mul(pj['y'][i], _div(num, den))
        out[i] = acc
    secret, check = bytes(out[:-CHECK]), bytes(out[-CHECK:])
    if _checksum(secret) != check:
        raise ShareError('checksum failed — a share is corrupt or tampered with')
    return secret


def inspect(share: str) -> dict:
    """What a share says about itself — never anything about the secret."""
    p = parse(share)
    return {'set': p['set'], 'k': p['k'], 'x': p['x'],
            'secret_bytes': len(p['y']) - CHECK}
