"""The signer every network shares: member keys (data/keys.json, 0600) and
the Cairo prover (core/prover/, `scarb cairo-run`) that links the same lsag
module the Starknet contract runs. Each network's verifier is checked
against what this produces — so a new network only has to VERIFY.

prover ops (first arg): 1 keygen, 2 sign, 3 verify, 4 tag, 5 scope,
6 hash-to-point. It prints one `Z <felt>` line per output value.

Every op runs NATIVE by default (core/lsag.py: the same lsag.cairo math in
Python, bit-identical outputs, ~50x faster than a cairo-run per call);
ZTAO_SIGNER=cairo routes them through the Cairo prover again.
"""
import json
import os
import re
import subprocess
import time

from . import lsag, util
from .util import felt, load, rand, save

PROVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prover')
SCARB = os.path.join(util.BIN, 'scarb')


def native():
    return os.environ.get('ZTAO_SIGNER', 'native') != 'cairo'


def _run(cmd, cwd, timeout=900):
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                         timeout=timeout, env=util.env())
    log = '\n'.join(l for l in (out.stdout + out.stderr).splitlines()
                    if not l.startswith(('warning:', '   ', ' -->')))
    return {'ok': out.returncode == 0, 'log': log.strip()[-4000:]}


class Signer:
    def __init__(self, keys_path=util.KEYS):
        self.keys_path = keys_path

    # ---- the prover --------------------------------------------------------

    def built(self):
        """The artifact for the package name Scarb.toml has NOW — a stale
        target/ from a renamed package does not count."""
        import re
        m = re.search(r'^name\s*=\s*"([^"]+)"', open(os.path.join(
            PROVER, 'Scarb.toml')).read(), re.M)
        return bool(m) and os.path.exists(os.path.join(
            PROVER, 'target', 'dev', f'{m.group(1)}.sierra.json'))

    def build(self):
        return _run([SCARB, 'build'], PROVER)

    def prove(self, args, timeout=120):
        if not self.built():
            b = self.build()
            if not b['ok']:
                raise RuntimeError('prover build failed: ' + b['log'][-800:])
        out = subprocess.run(
            [SCARB, 'cairo-run', '--no-build', '--available-gas', '4000000000',
             json.dumps([[int(a) for a in args]])],
            cwd=PROVER, capture_output=True, text=True, timeout=timeout,
            env=util.env())
        vals = [int(x) for x in re.findall(r'^Z (\d+)$', out.stdout, re.M)]
        if out.returncode != 0 or 'Run panicked' in out.stdout:
            raise RuntimeError('prover: ' + (out.stdout + out.stderr)
                               .strip()[-800:])
        return vals

    # ---- keys --------------------------------------------------------------

    def keygen(self, name, secret=None):
        keys = load(self.keys_path, {})
        if name in keys and secret is None:
            return {'error': f'key {name!r} exists', **self.pubkey(name)}
        x = felt(secret) % util.ORDER if secret is not None else rand()
        if x == 0:
            return {'error': 'secret reduces to zero'}
        if native():
            (px, py) = lsag.public_key(x)
            kid = lsag.key_id((px, py))
        else:
            px, py, kid = self.prove([1, x])
        keys[name] = {'secret': hex(x), 'x': hex(px), 'y': hex(py),
                      'key_id': hex(kid), 'at': int(time.time())}
        save(self.keys_path, keys, private=True)
        return self.pubkey(name)

    def keys(self):
        return [{'name': n, 'x': k['x'], 'y': k['y'], 'key_id': k['key_id']}
                for n, k in sorted(load(self.keys_path, {}).items())]

    def pubkey(self, name):
        k = load(self.keys_path, {}).get(name)
        if not k:
            raise ValueError(f'no key {name!r} (keygen it first)')
        return {'name': name, 'x': k['x'], 'y': k['y'], 'key_id': k['key_id']}

    def secret(self, name):
        k = load(self.keys_path, {}).get(name)
        if not k:
            raise ValueError(f'no key {name!r}')
        return int(k['secret'], 16)

    # ---- the scheme --------------------------------------------------------

    def sign(self, name, scope, msg, ring):
        """Ring-sign `msg` in `scope` as key `name`. `ring` is a list of
        {x,y} (yours included, any order — the prover sorts)."""
        pts = [(int(str(p['x']), 0), int(str(p['y']), 0)) for p in ring]
        n = len(pts)
        if native():
            x, srt = self.secret(name), lsag.sort_ring(pts)
            me = lsag.public_key(x)
            if me not in srt:
                raise RuntimeError('prover: your key is not in the ring')
            c0, s, img = lsag.sign(srt, srt.index(me), x, felt(scope),
                                   felt(msg), rand(), [rand() for _ in srt])
            return {'ring': [{'x': hex(a), 'y': hex(b)} for a, b in srt],
                    'c0': hex(c0), 's': [hex(v) for v in s],
                    'image': {'x': hex(img[0]), 'y': hex(img[1])},
                    'tag': hex(lsag.key_id(img))}
        args = [2, felt(scope), felt(msg), self.secret(name), rand(), n]
        for x, y in pts:
            args += [x, y]
        args += [rand() for _ in range(n)]
        v = self.prove(args)
        n = v[0]
        ring_sorted = [{'x': hex(v[1 + 2 * i]), 'y': hex(v[2 + 2 * i])}
                       for i in range(n)]
        o = 1 + 2 * n
        return {'ring': ring_sorted, 'c0': hex(v[o]),
                's': [hex(s) for s in v[o + 1:o + 1 + n]],
                'image': {'x': hex(v[o + 1 + n]), 'y': hex(v[o + 2 + n])},
                'tag': hex(v[o + 3 + n])}

    def timed_sign(self, name, scope, msg, ring):
        t0 = time.time()
        sig = self.sign(name, scope, msg, ring)
        return sig, int((time.time() - t0) * 1000)

    def verify(self, scope, msg, sig):
        n = len(sig['ring'])
        if native():
            return lsag.verify(
                [(int(p['x'], 16), int(p['y'], 16)) for p in sig['ring']],
                felt(scope), felt(msg), int(sig['c0'], 16),
                [int(v, 16) for v in sig['s']],
                (int(sig['image']['x'], 16), int(sig['image']['y'], 16)))
        args = [3, felt(scope), felt(msg), n]
        for p in sig['ring']:
            args += [int(p['x'], 16), int(p['y'], 16)]
        args += [int(sig['c0'], 16), *[int(s, 16) for s in sig['s']],
                 int(sig['image']['x'], 16), int(sig['image']['y'], 16)]
        return self.prove(args) == [1]

    def scope(self, kind, subnet, epoch, tag=0):
        """mine scope = H('zuma-mine', subnet, epoch); score adds tag."""
        k = 1 if str(kind) in ('1', 'mine', 'miner') else 2
        if native():
            return lsag.scope(k, felt(subnet), int(epoch), felt(tag))
        return self.prove([5, k, felt(subnet), int(epoch), felt(tag)])[0]

    def tag(self, name, scope):
        """The key image id of `name` under `scope` (its epoch tag)."""
        if native():
            return lsag.key_id(lsag.key_image(self.secret(name), felt(scope)))
        return self.prove([4, self.secret(name), scope])[2]

    def hash_point(self, scope):
        """Hp(scope): the hint verifiers without a cheap sqrt check."""
        if native():
            x, y = lsag.hash_to_point(felt(scope))
        else:
            x, y = self.prove([6, felt(scope)])
        return {'x': hex(x), 'y': hex(y)}
