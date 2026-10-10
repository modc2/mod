"""recurse — a tiny, safe playground for recursion.

This module demonstrates *bounded* recursion: well-defined recursive
functions that always terminate, each protected by an explicit depth
guard so a single call can never run away or exhaust the stack.

    m recurse                       # null call → info()
    m recurse/factorial n=10        # 10! computed recursively
    m recurse/fibonacci n=20        # the 20th Fibonacci number
    m recurse/gcd a=48 b=36         # Euclid's algorithm, recursively
    m recurse/test                  # offline tests

Deliberately inert: it does not spawn processes, call itself across the
network, schedule work, or run anything on its own. Every function is a
pure computation over its arguments and returns a value. The name is
about the *shape* of the algorithms, not self-replication.

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Everything the module exposes to the CLI, the
gateway and other modules is a public method on that class.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# A conservative ceiling that stays well under CPython's own recursion
# limit. Any request that would exceed it is refused with a clear error
# rather than being allowed to blow the stack.
DEFAULT_MAX_DEPTH = 1000


class Mod:
    description = """
    recurse — a safe playground for recursion. Computes factorial,
    Fibonacci and gcd recursively, each with a hard depth guard so a call
    can never run away. Pure stdlib, stateless, no network, no spawning.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51200))
        self.base = cfg.get('base_path', '/recurse')

    # ── protocol surface ─────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        cfg = self.config()
        return {
            'name': 'recurse',
            'description': self.description.strip(),
            'version': cfg.get('version'),
            'port': self.port,
            'app': f'http://localhost:{self.port}{self.base}/',
            'max_depth': DEFAULT_MAX_DEPTH,
            'endpoints': cfg.get('endpoints', {}),
        }

    forward = info

    def health(self):
        """Liveness — pure, touches nothing."""
        return {'ok': True, 'port': self.port}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the recursion demos ──────────────────────────────────────

    def factorial(self, n, max_depth=DEFAULT_MAX_DEPTH):
        """n! computed by honest recursion, with a depth guard."""
        n, max_depth = int(n), int(max_depth)
        if n < 0:
            raise ValueError('factorial is undefined for negative n')
        if n > max_depth:
            raise ValueError(
                f'n={n} exceeds max_depth={max_depth}; raise max_depth '
                f'deliberately if you really mean it')

        def go(k):
            return 1 if k <= 1 else k * go(k - 1)

        return go(n)

    def fibonacci(self, n, max_depth=DEFAULT_MAX_DEPTH):
        """The n-th Fibonacci number (0-indexed), depth-guarded.

        Uses linear recursion over an accumulator pair so the depth is
        exactly n rather than the exponential naive form.
        """
        n, max_depth = int(n), int(max_depth)
        if n < 0:
            raise ValueError('fibonacci is undefined for negative n')
        if n > max_depth:
            raise ValueError(
                f'n={n} exceeds max_depth={max_depth}; raise max_depth '
                f'deliberately if you really mean it')

        def go(k, a, b):
            return a if k == 0 else go(k - 1, b, a + b)

        return go(n, 0, 1)

    def gcd(self, a, b, max_depth=DEFAULT_MAX_DEPTH):
        """Greatest common divisor by recursive Euclid, depth-guarded."""
        a, b, max_depth = abs(int(a)), abs(int(b)), int(max_depth)

        def go(x, y, depth):
            if depth > max_depth:
                raise ValueError(f'recursion exceeded max_depth={max_depth}')
            return x if y == 0 else go(y, x % y, depth + 1)

        return go(a, b, 0)

    # ── offline tests ────────────────────────────────────────────

    def test(self):
        """Offline self-check; returns a pass/fail summary."""
        checks = []

        def expect(name, got, want):
            checks.append({'name': name, 'ok': got == want,
                           'got': got, 'want': want})

        expect('factorial(0)', self.factorial(0), 1)
        expect('factorial(5)', self.factorial(5), 120)
        expect('fibonacci(0)', self.fibonacci(0), 0)
        expect('fibonacci(1)', self.fibonacci(1), 1)
        expect('fibonacci(10)', self.fibonacci(10), 55)
        expect('gcd(48,36)', self.gcd(48, 36), 12)
        expect('gcd(17,5)', self.gcd(17, 5), 1)

        guard_ok = False
        try:
            self.factorial(5, max_depth=2)
        except ValueError:
            guard_ok = True
        checks.append({'name': 'depth guard refuses over-limit n',
                       'ok': guard_ok})

        passed = sum(1 for c in checks if c['ok'])
        return {'passed': passed, 'total': len(checks),
                'ok': passed == len(checks), 'checks': checks}


if __name__ == '__main__':
    import pprint
    pprint.pprint(Mod().test())
