"""
zuma protocol for bitevo — anonymous miners and validators.

Implements orbit/zuma's scheme (scope-linkable LSAG over the STARK curve,
Poseidon challenges, key image I = x * Hp(scope)) in pure Python. The curve,
Poseidon and hash-to-point come straight from orbit/zuma/stark.py, so a
signature made here verifies in zuma's Cairo prover / Starknet contract /
NEAR contract unchanged (the `zuma_wire` test proves it).

How an epoch runs (same messages as zuma's contract):

    submit  miner ring-signs  msg = work  in scope H('zuma-mine', subnet, epoch)
            tag = key_id(image): one submission per miner per epoch, and the
            validator cannot tell WHICH registered miner made it
    score   validator ring-signs  msg = score (0..100)  in scope
            H('zuma-score', subnet, epoch, tag); nullifier = key_id(image):
            one score per validator per tag, unlinkable across tags
    claim   miner re-signs the mine scope with  msg = recipient  — same image,
            same tag — and the recipient is credited
            emission * tag_sum / epoch_total

Secrets live in <storage>/zuma/keys.json (0600) and never leave the box.
"""

import hashlib
import importlib.util
import json
import os
import secrets as _secrets
import time
from pathlib import Path

ZUMA_DIR = Path(__file__).resolve().parents[2] / 'zuma'


def _load_stark():
    path = ZUMA_DIR / 'stark.py'
    if not path.exists():
        raise ImportError(f'zuma protocol needs {path} (orbit/zuma)')
    spec = importlib.util.spec_from_file_location('zuma_stark', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_stark()
P = S.P
ORDER = 0x800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f
MAX_RING = 64
MIN_RING = 3
MAX_SCORE = 100
ROLES = ('miner', 'validator')


def short(s):
    return S.short(s)


def felt(v):
    """int / hex / short string / anything else -> felt (sha256 mod P)."""
    if isinstance(v, int):
        return v % P
    s = str(v)
    try:
        return int(s, 0) % P
    except ValueError:
        pass
    if len(s) <= 31 and s.isascii():
        return short(s)
    return int.from_bytes(hashlib.sha256(s.encode()).digest(), 'big') % P


def commit(obj):
    """Work commitment: sha256 of canonical JSON, as a felt (< 2^248)."""
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)
    return int.from_bytes(hashlib.sha256(raw.encode()).digest()[:31], 'big')


# ---- curve helpers ---------------------------------------------------------

def _lin(a, A, b, B):
    """a*A + b*B (affine, None = infinity)."""
    return S.straus(a % ORDER, A, b % ORDER, B, 64)[-1]


def _m(k, A):
    return _lin(k, A, 0, A)


def _z(pt):
    return pt if pt is not None else (0, 0)


def scalar(v):
    return int(v) % ORDER


def public_key(secret):
    return _m(scalar(secret), S.G)


def key_id(pt):
    return S.key_id(pt)


def hash_to_point(scope):
    return S.hash_to_point(scope)[0]


def key_image(secret, scope):
    return _m(scalar(secret), hash_to_point(scope))


def mine_scope(subnet, epoch):
    return S.h(short('zuma-mine'), subnet, int(epoch))


def score_scope(subnet, epoch, tag):
    return S.h(short('zuma-score'), subnet, int(epoch), tag)


def sort_ring(ring):
    return sorted(ring, key=key_id)


def _digest(ring, scope, msg, image):
    xs = [S.TAG_MSG, scope, msg, image[0], image[1], len(ring)]
    for p in ring:
        xs += [p[0], p[1]]
    return S.h(*xs)


def _chal(d, l, r):
    return S.h(S.TAG_CHAL, d, *_z(l), *_z(r))


# ---- LSAG ------------------------------------------------------------------

def sign(ring, secret, scope, msg):
    """Ring-sign `msg` in `scope`. `ring` = [(x, y)] including your key, any
    order (sorted here, as zuma's prover does). Returns a JSON-able sig."""
    ring = sort_ring([tuple(p) for p in ring])
    n = len(ring)
    x = scalar(secret)
    if x == 0:
        raise ValueError('zero secret')
    me = public_key(x)
    if me not in ring:
        raise ValueError('your key is not in the ring')
    idx = ring.index(me)
    hp = hash_to_point(scope)
    image = _m(x, hp)
    d = _digest(ring, scope, msg, image)
    a = _secrets.randbelow(ORDER - 1) + 1
    c = [0] * n
    s = [0] * n
    j = (idx + 1) % n
    c[j] = _chal(d, _m(a, S.G), _m(a, hp))
    while j != idx:
        s[j] = _secrets.randbelow(ORDER)
        l = _lin(s[j], S.G, c[j], ring[j])
        r = _lin(s[j], hp, c[j], image)
        nj = (j + 1) % n
        c[nj] = _chal(d, l, r)
        j = nj
    s[idx] = (a - c[idx] * x) % ORDER
    return {'ring': [{'x': hex(p[0]), 'y': hex(p[1])} for p in ring],
            'c0': hex(c[0]), 's': [hex(v) for v in s],
            'image': {'x': hex(image[0]), 'y': hex(image[1])},
            'tag': hex(key_id(image))}


def _pt(p):
    return (int(str(p['x']), 0), int(str(p['y']), 0))


def verify(scope, msg, sig):
    """Same checks as lsag::verify in zuma's Cairo contract."""
    try:
        ring = [_pt(p) for p in sig['ring']]
        image = _pt(sig['image'])
        s = [int(v, 16) for v in sig['s']]
        c0 = int(sig['c0'], 16)
    except (KeyError, TypeError, ValueError):
        return False
    n = len(ring)
    if n == 0 or len(s) != n or not S.on_curve(image):
        return False
    if not all(S.on_curve(p) for p in ring):
        return False
    hp = hash_to_point(scope)
    d = _digest(ring, scope, msg, image)
    c = c0
    for i in range(n):
        c = _chal(d, _lin(s[i], S.G, c, ring[i]), _lin(s[i], hp, c, image))
    return c == c0


def tag_of(sig):
    return hex(key_id(_pt(sig['image'])))


# ---- the subnet -------------------------------------------------------------

def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class ZumaSubnet:
    """Membership registry + epoch ledger for one bitevo subnet. Mirrors
    zuma's contract checks (ring rules, one tag per epoch, one nullifier per
    tag, claim after close) off-chain; every signature is wire-compatible
    with the on-chain verifiers."""

    def __init__(self, storage_path='~/.bitevo', subnet='bitevo',
                 min_ring=MIN_RING, emission=1000):
        self.dir = os.path.join(os.path.expanduser(storage_path), 'zuma')
        self.keys_path = os.path.join(self.dir, 'keys.json')
        self.state_path = os.path.join(self.dir, 'subnet.json')
        st = _load(self.state_path, {})
        self.subnet = felt(st.get('subnet', subnet))
        self.min_ring = int(st.get('min_ring', min_ring))
        self.emission = int(st.get('emission', emission))
        self.members = st.get('members', {'miner': [], 'validator': []})
        self.epochs = st.get('epochs', {})
        self.credit = st.get('credit', {})

    # -- persistence
    def save(self):
        _save(self.state_path, {
            'subnet': hex(self.subnet), 'min_ring': self.min_ring,
            'emission': self.emission, 'members': self.members,
            'epochs': self.epochs, 'credit': self.credit})

    # -- keys (local members only; secrets never leave this file)
    def _keys(self):
        return _load(self.keys_path, {})

    def keygen(self, name, role):
        if role not in ROLES:
            raise ValueError(f'role must be one of {ROLES}')
        keys = self._keys()
        if name not in keys:
            x = _secrets.randbelow(ORDER - 1) + 1
            pk = public_key(x)
            keys[name] = {'secret': hex(x), 'role': role, 'x': hex(pk[0]),
                          'y': hex(pk[1]), 'key_id': hex(key_id(pk)),
                          'at': int(time.time())}
            _save(self.keys_path, keys, private=True)
        k = keys[name]
        self.register({'x': k['x'], 'y': k['y']}, k['role'])
        return {'name': name, 'role': k['role'], 'x': k['x'], 'y': k['y'],
                'key_id': k['key_id']}

    def secret(self, name):
        k = self._keys().get(name)
        if not k:
            raise ValueError(f'no zuma key {name!r}')
        return int(k['secret'], 16)

    # -- membership (public keys only)
    def register(self, pub, role):
        pt = _pt(pub)
        if not S.on_curve(pt):
            raise ValueError('public key not on the STARK curve')
        kid = hex(key_id(pt))
        for r in ROLES:
            if any(m['key_id'] == kid for m in self.members[r]):
                if r != role:
                    raise ValueError(f'key already registered as {r}')
                return kid
        self.members[role].append({'x': hex(pt[0]), 'y': hex(pt[1]),
                                   'key_id': kid})
        self.save()
        return kid

    def ring(self, role):
        pts = sort_ring([_pt(m) for m in self.members[role]])
        return pts[-MAX_RING:] if len(pts) > MAX_RING else pts

    def _check_ring(self, sig, role):
        ids = [key_id(_pt(p)) for p in sig['ring']]
        n = len(ids)
        if n < self.min_ring:
            raise ValueError(f'ring below min_ring ({n} < {self.min_ring})')
        if n > MAX_RING:
            raise ValueError('ring above MAX_RING')
        if any(a >= b for a, b in zip(ids, ids[1:])):
            raise ValueError('ring key ids not strictly increasing')
        known = {int(m['key_id'], 16) for m in self.members[role]}
        if not set(ids) <= known:
            raise ValueError(f'ring has a key that is not a registered {role}')

    def _ep(self, epoch):
        return self.epochs.setdefault(str(int(epoch)), {
            'open': True, 'work': {}, 'scores': {}, 'nullifiers': [],
            'claims': {}, 'total': 0})

    # -- actions (each = one zuma contract call)
    def submit(self, epoch, work, sig):
        e = self._ep(epoch)
        if not e['open']:
            raise ValueError('epoch closed')
        self._check_ring(sig, 'miner')
        if not verify(mine_scope(self.subnet, epoch), int(work), sig):
            raise ValueError('bad ring signature')
        tag = tag_of(sig)
        if tag in e['work']:
            raise ValueError('already submitted this epoch')
        e['work'][tag] = {'work': hex(int(work)), 'sig': sig}
        return tag

    def score(self, epoch, tag, score, sig):
        e = self._ep(epoch)
        score = int(score)
        if not 0 <= score <= MAX_SCORE:
            raise ValueError('score is 0..100')
        if not e['open']:
            raise ValueError('epoch closed')
        if tag not in e['work']:
            raise ValueError('no such tag this epoch')
        self._check_ring(sig, 'validator')
        if not verify(score_scope(self.subnet, epoch, int(tag, 16)), score, sig):
            raise ValueError('bad ring signature')
        nul = tag_of(sig)
        if nul in e['nullifiers']:
            raise ValueError('already scored this tag')
        e['nullifiers'].append(nul)
        e['scores'].setdefault(tag, []).append(
            {'score': score, 'nullifier': nul, 'sig': sig})
        e['total'] += score
        return nul

    def close(self, epoch):
        self._ep(epoch)['open'] = False
        self.save()

    def tally(self, epoch):
        e = self.epochs.get(str(int(epoch)))
        if not e:
            return {}
        return {t: {'sum': sum(s['score'] for s in e['scores'].get(t, [])),
                    'n': len(e['scores'].get(t, []))} for t in e['work']}

    def claim(self, epoch, recipient, sig):
        e = self.epochs.get(str(int(epoch)))
        if not e:
            raise ValueError('no such epoch')
        if e['open']:
            raise ValueError('epoch still open')
        self._check_ring(sig, 'miner')
        if not verify(mine_scope(self.subnet, epoch), felt(recipient), sig):
            raise ValueError('bad ring signature')
        tag = tag_of(sig)
        if tag not in e['work']:
            raise ValueError('tag did not submit')
        if tag in e['claims']:
            raise ValueError('already claimed')
        tsum = sum(s['score'] for s in e['scores'].get(tag, []))
        amount = self.emission * tsum // e['total'] if e['total'] else 0
        e['claims'][tag] = {'recipient': str(recipient), 'amount': amount,
                            'sig': sig}
        self.credit[str(recipient)] = self.credit.get(str(recipient), 0) + amount
        self.save()
        return amount

    def audit(self, epoch):
        """Re-verify every signature of an epoch from the public record."""
        e = self.epochs.get(str(int(epoch)))
        if not e:
            return {'epoch': int(epoch), 'error': 'no such epoch'}
        bad = []
        ms = mine_scope(self.subnet, epoch)
        for tag, w in e['work'].items():
            if not (verify(ms, int(w['work'], 16), w['sig']) and tag_of(w['sig']) == tag):
                bad.append(('submit', tag))
            for s in e['scores'].get(tag, []):
                sc = score_scope(self.subnet, epoch, int(tag, 16))
                if not (verify(sc, s['score'], s['sig']) and tag_of(s['sig']) == s['nullifier']):
                    bad.append(('score', s['nullifier']))
        for tag, c in e['claims'].items():
            if not (verify(ms, felt(c['recipient']), c['sig']) and tag_of(c['sig']) == tag):
                bad.append(('claim', tag))
        n = len(e['work']) + sum(len(v) for v in e['scores'].values()) + len(e['claims'])
        return {'epoch': int(epoch), 'signatures': n, 'valid': not bad,
                'bad': bad}

    def status(self):
        return {'protocol': 'zuma (LSAG over the STARK curve)',
                'subnet': hex(self.subnet), 'min_ring': self.min_ring,
                'max_ring': MAX_RING, 'emission': self.emission,
                'miners': len(self.members['miner']),
                'validators': len(self.members['validator']),
                'epochs': len(self.epochs),
                'credit': dict(sorted(self.credit.items(),
                                      key=lambda kv: -kv[1]))}
