"""cairo.zuma extras — the member side of zk Yuma, over the sandbox devnet.

The contract only ever sees ring signatures, so somebody has to make them.
These fns keep member keys on this box (data/keys.json, 0600), build the
ring from the chain (every member of your role in your stake tier), sign
with the prover (prover/ = `scarb cairo-run` over the SAME zuma::lsag the
contract verifies with) and post through a RELAYER account — the tx sender
is public, so a member never posts from an account tied to itself.

    m cairo.zuma/keygen name=m1 role=miner
    m cairo.zuma/register name=m1                  # owner account adds the key
    m cairo.zuma/submit name=m1 work=answer-hash
    m cairo.zuma/commit name=v1 weights=4,3,2,1    # salt drawn + kept locally
    m cairo.zuma/reveal name=v1
    m cairo.zuma/finalize
    m cairo.zuma/claim name=m1 epoch=0 to=0x...
"""

import json
import os
import re
import secrets
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROVER = os.path.join(HERE, 'prover')
KEYS = os.path.join(HERE, 'data', 'keys.json')
BIN = os.path.expanduser('~/.local/bin')
ORDER = 0x0800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f
ROLES = {'miner': 1, 'validator': 2}


def _felt(v):
    """int | '0x..' | decimal | short string ('pepper') -> int."""
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if re.fullmatch(r'0x[0-9a-fA-F]+', s):
        return int(s, 16)
    if re.fullmatch(r'\d+', s):
        return int(s)
    b = s.encode()
    if len(b) > 31:
        raise ValueError(f'{s!r} is longer than a short string (31 bytes)')
    return int.from_bytes(b, 'big')


def _rand():
    return 1 + secrets.randbelow(ORDER - 1)


def _load():
    try:
        return json.load(open(KEYS))
    except Exception:  # noqa: BLE001
        return {}


def _save(keys):
    os.makedirs(os.path.dirname(KEYS), exist_ok=True)
    tmp = KEYS + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(keys, f, indent=1)
    os.replace(tmp, KEYS)


class Ext:
    # ---- prover -------------------------------------------------------------

    def _prove(self, args, timeout=120):
        env = {**os.environ, 'PATH': BIN + os.pathsep + os.environ.get('PATH', '')}
        if not os.path.isdir(os.path.join(PROVER, 'target')):
            b = subprocess.run([os.path.join(BIN, 'scarb'), 'build'], cwd=PROVER,
                               capture_output=True, text=True, timeout=900, env=env)
            if b.returncode != 0:
                raise RuntimeError('prover build failed: ' + (b.stdout + b.stderr)[-800:])
        out = subprocess.run(
            [os.path.join(BIN, 'scarb'), 'cairo-run', '--no-build',
             '--available-gas', '4000000000', json.dumps([[int(a) for a in args]])],
            cwd=PROVER, capture_output=True, text=True, timeout=timeout, env=env)
        if out.returncode != 0 or 'Run panicked' in out.stdout:
            raise RuntimeError('prover: ' + (out.stdout + out.stderr).strip()[-800:])
        return [int(x) for x in re.findall(r'^Z (\d+)$', out.stdout, re.M)]

    # ---- chain helpers ------------------------------------------------------

    def _view(self, fn, args=None):
        r = self.call(fn, args=args)
        if not r.get('ok'):
            raise RuntimeError(f'{fn}: ' + str(r.get('error')))
        return [int(x, 16) for x in r['raw']]

    def _address(self):
        row = self.cairo._sandbox().deployment(NAME_OF(self))
        if not row or not row.get('live'):
            raise RuntimeError('cairo.zuma is not deployed on the sandbox yet')
        return int(row['address'], 16)

    def _ring(self, role, stake):
        """Every registered member of `role` in this stake tier."""
        out = []
        for i in range(self._view('member_count', {'role': role})[0]):
            x, y = self._view('member', {'role': role, 'index': i})
            p = {'x': hex(x), 'y': hex(y)}
            if role == 1 or self._view('stake_of', {'key': p})[0] == stake:
                out.append(p)
        return out

    def _sign(self, key, role, scope, msg):
        ring = self._ring(role, key.get('stake', 0))
        n = len(ring)
        args = [2, scope, msg, int(key['secret'], 16), _rand(), n]
        for p in ring:
            args += [int(p['x'], 16), int(p['y'], 16)]
        args += [_rand() for _ in range(n)]
        v = self._prove(args)
        n = v[0]
        ring = [{'x': hex(v[1 + 2 * i]), 'y': hex(v[2 + 2 * i])} for i in range(n)]
        k = 1 + 2 * n
        sig = {'c0': hex(v[k]), 's': [hex(s) for s in v[k + 1:k + 1 + n]],
               'image': {'x': hex(v[k + 1 + n]), 'y': hex(v[k + 2 + n])}}
        return ring, sig, hex(v[k + 3 + n])

    def _scope(self, kind, epoch):
        return self._prove([5, kind, self._address(), epoch])[0]

    def _key(self, name, role=None):
        k = _load().get(name)
        if not k:
            raise KeyError(f'no key {name!r} — keygen it first')
        if role and k['role'] != role:
            raise ValueError(f'{name!r} is a {k["role"]}, not a {role}')
        return k

    def _wrap(self, f):
        try:
            return f()
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)}

    # ---- keys ---------------------------------------------------------------

    def keygen(self, name, role='miner', stake=0, secret=None):
        """A member key, kept on this box. Only the public key ever leaves.
        Validators carry a stake; a ring is one stake tier."""
        if role not in ROLES:
            return {'ok': False, 'error': 'role is miner or validator'}
        keys = _load()
        if name in keys:
            return {'ok': False, 'error': f'key {name!r} exists', **self.keys()[name]}
        x = (_felt(secret) % ORDER) if secret is not None else _rand()
        px, py, kid = self._prove([1, x])
        keys[name] = {'role': role, 'stake': int(stake) if role == 'validator' else 0,
                      'secret': hex(x), 'x': hex(px), 'y': hex(py), 'key_id': hex(kid)}
        _save(keys)
        return {'ok': True, 'name': name, **self.keys()[name]}

    def keys(self):
        """Local member keys, public halves only."""
        return {n: {k: v for k, v in d.items() if k not in ('secret', 'pending')}
                for n, d in _load().items()}

    # ---- the round ----------------------------------------------------------

    def register(self, name, account='alice'):
        """Add a local key to its set on-chain (signed by the owner `account`)."""
        def go():
            k = self._key(name)
            return self.call('register', {'role': ROLES[k['role']],
                                          'key': {'x': k['x'], 'y': k['y']},
                                          'stake': k['stake']}, account=account)
        return self._wrap(go)

    def submit(self, name, work, relayer='bob'):
        """Miner: post `work` (a commitment to your output) as one of the miners."""
        def go():
            k = self._key(name, 'miner')
            e = self._view('epoch')[0]
            w = _felt(work)
            ring, sig, tag = self._sign(k, 1, self._scope(1, e), w)
            r = self.call('submit', {'ring': ring, 'work': hex(w), 'sig': sig}, account=relayer)
            return {**r, 'epoch': e, 'tag': tag, 'ring_size': len(ring)}
        return self._wrap(go)

    def commit(self, name, weights, salt=None, relayer='bob'):
        """Validator: seal one weight per tag (tag order = `tags(epoch)`).
        The salt and weights stay in data/keys.json for reveal()."""
        def go():
            k = self._key(name, 'validator')
            e = self._view('epoch')[0]
            w = [int(x) for x in re.split(r'[\s,]+', str(weights).strip('[] ')) if x] \
                if not isinstance(weights, list) else [int(x) for x in weights]
            s = _felt(salt) if salt is not None else _rand()
            vs = self._scope(2, e)
            null = self._prove([4, int(k['secret'], 16), vs])[2]
            addr = self._address()
            seal = self._prove([6, addr, e, null, s, len(w), *w])[0]
            ring, sig, image_id = self._sign(k, 2, vs, seal)
            assert int(image_id, 16) == null, 'nullifier drift'
            r = self.call('commit', {'ring': ring, 'seal': hex(seal), 'sig': sig},
                          account=relayer)
            if r.get('ok'):
                keys = _load()
                keys[name]['pending'] = {'epoch': e, 'nullifier': hex(null),
                                         'weights': w, 'salt': hex(s)}
                _save(keys)
            return {**r, 'epoch': e, 'nullifier': hex(null), 'ring_size': len(ring)}
        return self._wrap(go)

    def reveal(self, name, relayer='bob'):
        """Validator: open the seal commit() left behind."""
        def go():
            p = self._key(name, 'validator').get('pending')
            if not p:
                raise ValueError('nothing sealed to reveal')
            return self.call('reveal', {'nullifier': p['nullifier'], 'weights': p['weights'],
                                        'salt': p['salt']}, account=relayer)
        return self._wrap(go)

    def finalize(self, relayer='bob'):
        """Anyone: run Yuma once the round is ready (phase 3)."""
        return self._wrap(lambda: self.call('finalize', {}, account=relayer))

    def claim(self, name, epoch, to=None, relayer='bob'):
        """Re-prove your tag (miner) or nullifier (validator) for `epoch` and
        credit your share to `to` (default: the relayer account)."""
        def go():
            k = self._key(name)
            e = int(epoch)
            role = ROLES[k['role']]
            dest = _felt(to) if to is not None else int(self._sandbox_addr(relayer), 16)
            ring, sig, _ = self._sign(k, role, self._scope(role, e), dest)
            fn = 'claim_miner' if role == 1 else 'claim_validator'
            return self.call(fn, {'epoch': e, 'ring': ring, 'recipient': hex(dest), 'sig': sig},
                             account=relayer)
        return self._wrap(go)

    def _sandbox_addr(self, name):
        for a in self.cairo._sandbox().accounts():
            if a['name'] == name:
                return a['address']
        raise KeyError(f'unknown sandbox account {name!r}')


def NAME_OF(mod):
    return os.path.basename(mod.path)
