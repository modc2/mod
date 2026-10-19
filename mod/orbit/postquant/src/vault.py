"""The vault — off-chain storage the nodes can hold but never read.

The chain prices a 32-byte commitment below a blob on purpose (8 KiB value
cap, per-byte write gas, per-byte rent): it is built to store the fingerprint,
not the file. The vault is where the file goes, and it is built so that the
node holding it learns nothing from it:

    client                               node                      chain
    ──────                               ────                      ─────
    file ─▶ AES-256-GCM (key never   ─▶  blobs/<cid>          ─▶  key = cid
            leaves the client)            ciphertext only           32 bytes,
                                          cid = SHA3-256(blob)      rent-paying

ENVELOPE (PQV1) — byte-identical from the browser (WebCrypto) and from
python (pq/aesgcm.py, or `cryptography` when installed):

    magic  'PQV1'                         4
    mode   0 = random key, 1 = passphrase 1
    iters  PBKDF2-HMAC-SHA256 rounds      4   (0 in mode 0)
    salt                                  16  (zeros in mode 0)
    iv                                    12
    AES-256-GCM(key, iv, plaintext, aad = the 37 header bytes)

    plaintext = u32 len(meta) | meta JSON {name, type, size} | data | zero pad

The file name and type live INSIDE the ciphertext, and the plaintext is padded
to a power-of-two bucket (min 1 KiB), so the node sees neither what the file
is called nor its exact size.

RETENTION — off-chain bytes ride on-chain rent. A blob is kept while any live
chain entry commits to its cid; an unanchored blob is pruned after a grace
window. Rent the 32-byte pointer and the node keeps the bytes; stop paying and
the pointer expires, then the bytes go.

CONTRACTS — code is stored in the same content-addressed store, but PUBLIC
(code nobody can read cannot be audited or re-run). The anchor key
`contract/<name>` holds SHA3-256(code); see mcp._t_contract.

What the node can still see, stated plainly: the ciphertext length bucket,
when it arrived, and which chain key anchors it. What it cannot: the bytes,
the name, the exact size, or the key.
"""

import base64
import hashlib
import json
import os
import struct
import time

try:                                     # fast path when the box has it
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _Fast
except Exception:                        # noqa: BLE001 — stdlib only is fine
    _Fast = None

from pq import aesgcm

MAGIC = b'PQV1'
HEADER = 37
MODE_KEY, MODE_PASS = 0, 1
PBKDF2_ITERS = 600_000                   # OWASP 2023 floor for SHA-256
MIN_BUCKET = 1024

DATA_DIR = os.path.expanduser(os.environ.get('POSTQUANT_DATA_DIR',
                                             '~/.mod/postquant'))
MAX_BLOB = int(os.environ.get('POSTQUANT_VAULT_MAX', 8 << 20))
QUOTA = int(os.environ.get('POSTQUANT_VAULT_QUOTA', 1 << 30))
GRACE = int(os.environ.get('POSTQUANT_VAULT_GRACE', 3600))
LANGS = ('wasm', 'sierra', 'cairo', 'source')


class VaultError(ValueError):
    def __init__(self, message, code='bad_blob', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def cid_of(blob):
    return hashlib.sha3_256(blob).hexdigest()


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def unb64(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


# ── the envelope (what a CLIENT runs) ────────────────────────────


def _gcm(encrypt, key, iv, data, aad):
    if _Fast is not None:
        f = _Fast(key)
        try:
            return (f.encrypt if encrypt else f.decrypt)(iv, data, aad)
        except Exception as e:           # noqa: BLE001 — InvalidTag
            raise ValueError('authentication failed — wrong key or '
                             'tampered blob') from e
    return (aesgcm.encrypt if encrypt else aesgcm.decrypt)(key, iv, data, aad)


def bucket(n):
    """Power-of-two size bucket, so a ciphertext length says little."""
    size = MIN_BUCKET
    while size < n:
        size *= 2
    return size


def seal(data, name='', type_='', passphrase=None, pad=True,
         iters=PBKDF2_ITERS):
    """Encrypt on the client. Returns (blob, key) — key is the url-safe
    base64 of the random 32-byte key in mode 0, None in passphrase mode.
    Neither the key nor the passphrase is ever sent anywhere."""
    if isinstance(data, str):
        data = data.encode()
    meta = json.dumps({'name': name, 'type': type_, 'size': len(data)},
                      separators=(',', ':')).encode()
    plain = struct.pack('>I', len(meta)) + meta + data
    if pad:
        plain = plain.ljust(bucket(len(plain)), b'\0')
    iv = os.urandom(12)
    if passphrase:
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, iters)
        header = MAGIC + bytes([MODE_PASS]) + struct.pack('>I', iters) + \
            salt + iv
        out_key = None
    else:
        key = os.urandom(32)
        header = MAGIC + bytes([MODE_KEY]) + b'\0' * 20 + iv
        out_key = b64(key)
    return header + _gcm(True, key, iv, plain, header), out_key


def parse_header(blob):
    if len(blob) < HEADER + 16 or blob[:4] != MAGIC:
        raise VaultError('not a PQV1 envelope — the vault only holds '
                         'ciphertext sealed on the client', 'not_sealed')
    mode = blob[4]
    if mode not in (MODE_KEY, MODE_PASS):
        raise VaultError(f'unknown envelope mode {mode}', 'not_sealed')
    iters = struct.unpack('>I', blob[5:9])[0]
    if mode == MODE_PASS and not 1000 <= iters <= 10_000_000:
        raise VaultError('passphrase envelope with an implausible PBKDF2 '
                         'round count', 'not_sealed')
    return {'mode': 'passphrase' if mode else 'key', 'iters': iters,
            'salt': blob[9:25], 'iv': blob[25:37]}


def open_(blob, key=None, passphrase=None):
    """Decrypt on the client. Returns (meta, data)."""
    h = parse_header(blob)
    if h['mode'] == 'passphrase':
        if not passphrase:
            raise VaultError('this blob is passphrase-sealed', 'need_secret')
        k = hashlib.pbkdf2_hmac('sha256', passphrase.encode(), h['salt'],
                                h['iters'])
    else:
        if not key:
            raise VaultError('this blob is key-sealed — pass the key from '
                             'its share link', 'need_secret')
        k = unb64(key) if isinstance(key, str) else key
    try:
        plain = _gcm(False, k, h['iv'], blob[HEADER:], blob[:HEADER])
    except ValueError as e:
        raise VaultError(str(e), 'bad_secret') from e
    n = struct.unpack('>I', plain[:4])[0]
    meta = json.loads(plain[4:4 + n])
    data = plain[4 + n:4 + n + int(meta.get('size', 0))]
    return meta, data


def entropy(b):
    """Shannon entropy in bits per byte."""
    if not b:
        return 0.0
    import math
    counts = [0] * 256
    for x in b:
        counts[x] += 1
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def looks_sealed(blob):
    """The node's sanity check on a put. It cannot PROVE a blob is encrypted
    — nobody can — but it refuses the obvious accident: a missing header, or
    a body whose bytes are plainly not ciphertext (text, zeros, a raw file
    pasted where its envelope should be)."""
    h = parse_header(blob)
    body = blob[HEADER:]
    if len(body) >= 1024:
        e = entropy(body[:65536])
        if e < 7.5:
            raise VaultError(f'body entropy {e:.2f} bits/byte — ciphertext '
                             'reads as ~8; this looks like plaintext behind '
                             'a PQV1 header. Seal it on the client.',
                             'not_sealed')
    return h


# ── the store (what a NODE runs) ─────────────────────────────────


class Store:
    """Content-addressed blobs on this node's disk. Sealed blobs and public
    contract code share it; a sidecar records which is which."""

    def __init__(self, root=None):
        self.root = os.path.join(root or DATA_DIR, 'vault')

    def _p(self, cid, ext=''):
        if len(cid) != 64 or any(c not in '0123456789abcdef' for c in cid):
            raise VaultError('cid is 64 hex chars — SHA3-256 of the blob',
                             'bad_cid')
        return os.path.join(self.root, cid + ext)

    def has(self, cid):
        return os.path.exists(self._p(cid))

    def meta(self, cid):
        try:
            with open(self._p(cid, '.json')) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def get(self, cid):
        try:
            with open(self._p(cid), 'rb') as f:
                return f.read()
        except OSError:
            raise VaultError(f'no blob {cid[:16]}… on this node', 'no_blob',
                             404) from None

    def used(self):
        try:
            return sum(os.path.getsize(os.path.join(self.root, f))
                       for f in os.listdir(self.root)
                       if not f.endswith('.json'))
        except OSError:
            return 0

    def put(self, blob, kind='sealed', lang=None, live=()):
        """Store one blob. kind=sealed must pass looks_sealed; kind=code is
        public contract code; kind=state / kind=receipt are the public
        blobs an off-chain contract run leaves behind. Returns its
        listing."""
        if len(blob) > MAX_BLOB:
            raise VaultError(f'{len(blob)} bytes is over the '
                             f'{MAX_BLOB}-byte blob cap', 'too_big', 413)
        if kind == 'sealed':
            looks_sealed(blob)
        elif kind == 'code':
            check_code(blob, lang)
        elif kind == 'state':
            pass                     # contract state: public bytes, any shape
        elif kind == 'receipt':
            check_receipt(blob)
        else:
            raise VaultError(f'unknown blob kind {kind!r}')
        cid = cid_of(blob)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        if not self.has(cid):
            self.prune(live)
            if self.used() + len(blob) > QUOTA:
                raise VaultError('this node\'s vault quota is full — anchor '
                                 'or let expire what is here', 'quota', 507)
            tmp = self._p(cid, '.tmp')
            with open(tmp, 'wb') as f:
                f.write(blob)
            os.replace(tmp, self._p(cid))
            with open(self._p(cid, '.json'), 'w') as f:
                json.dump({'kind': kind, 'lang': lang, 'bytes': len(blob),
                           'stored': int(time.time())}, f)
        return self.listing(cid)

    def listing(self, cid):
        m = self.meta(cid) or {}
        return {'cid': cid, 'kind': m.get('kind'), 'lang': m.get('lang'),
                'bytes': m.get('bytes'), 'stored': m.get('stored')}

    def list(self):
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        return [self.listing(f[:-5]) for f in sorted(names)
                if f.endswith('.json')]

    def prune(self, live, now=None):
        """Drop unanchored blobs past the grace window. `live` = the set of
        cids some unexpired chain entry commits to."""
        now = now or time.time()
        dropped = []
        for item in self.list():
            if item['cid'] in live:
                continue
            if now - (item['stored'] or 0) < GRACE:
                continue
            for ext in ('', '.json'):
                try:
                    os.remove(self._p(item['cid'], ext))
                except OSError:
                    pass
            dropped.append(item['cid'])
        return dropped


def check_receipt(blob):
    """A pqr1 execution receipt: signed JSON a contract runner publishes."""
    try:
        r = json.loads(blob.decode())
        if r['over']['v'] != 'pqr1' or not r.get('sig'):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise VaultError('not a pqr1 receipt', 'bad_blob') from None


def check_code(code, lang):
    """Contract code is public, so the check is the opposite of a seal: it
    must be what it says it is."""
    if lang not in LANGS:
        raise VaultError(f'lang is one of {", ".join(LANGS)}', 'bad_lang')
    if lang == 'wasm' and code[:4] != b'\0asm':
        raise VaultError('wasm starts with \\0asm — this does not',
                         'bad_code')
    if lang == 'sierra':
        try:
            j = json.loads(code)
        except ValueError:
            raise VaultError('sierra is the JSON scarb emits', 'bad_code') \
                from None
        if not isinstance(j, dict) or not any(
                k in j for k in ('sierra_program', 'contract_class_version')):
            raise VaultError('no sierra_program in this JSON', 'bad_code')
    if lang in ('cairo', 'source'):
        try:
            code.decode('utf-8')
        except UnicodeDecodeError:
            raise VaultError('source must be UTF-8 text', 'bad_code') \
                from None
    if not code:
        raise VaultError('empty code', 'bad_code')
