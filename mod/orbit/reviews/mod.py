"""reviews — ratings with pluggable, transparent aggregators.

Any module can hand ratings here (a subject, a rater, a 1–5 score, human or
agent) and ask for ONE number back. The number always comes with its working:
which aggregator, each rater's weight and why, the formula. Aggregators are a
registry — `mean` (the default), `median`, `trimmed`, `consensus`, `recent`,
`weighted` (a published weight table), `staketime` (provider coming) and
`market`: the aggregators are themselves rated (subject `agg:<id>`), anyone
can publish a new one from a base + params, and `market` blends every
aggregator's answer by how the market ranks it.

    m reviews                                       # null call → info()
    m reviews/aggregators                           # the registry, with formulas
    m reviews/rate subject=build:polymarket rater=0xabc score=4 kind=agent ref=sg_123
    m reviews/score subject=build:polymarket        # default aggregator (mean)
    m reviews/score subject=build:polymarket aggregator=weighted
    m reviews/set_weights subject=build:polymarket weights='{"by_kind":{"agent":0.5}}'
    m reviews/market                                # aggregators ranked by their own ratings
    m reviews/rate subject=agg:consensus rater=0xabc score=5   # rate an aggregator
    m reviews/publish id=harsh base=consensus params='{"k":3}' author=0xabc
    m reviews/score subject=build:polymarket aggregator=market  # the consensus of aggregators
    m reviews/serve                                 # console + API on :51440
    m reviews/test
    m reviews/kill

Anchor file: the orbit loader imports it by path and instantiates ``Mod``.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    reviews — a modular rating layer. Store 1–5 ratings (human or agent) on
    any subject and aggregate them with a pluggable, transparent aggregator:
    mean by default, custom weights by kind/rater, stake-time weighting next.
    Every score ships with its per-rater breakdown and formula.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51440))
        self.base = cfg.get('base_path', '/reviews')
        self._path = path
        self._store = None

    @property
    def store(self):
        if self._store is None:
            import reviews
            self._store = reviews.Store(self._path)
        return self._store

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, the aggregator registry, and every route."""
        import reviews
        cfg = self.config()
        return {'name': 'reviews', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'default_aggregator': reviews.DEFAULT_AGGREGATOR, 'scale': reviews.SCALE,
                'aggregators': reviews.aggregators(), 'stats': self.store.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — touches only the local file."""
        return {'ok': True, 'port': self.port, **self.store.stats()}

    def readme(self):
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── ratings ──────────────────────────────────────────────────

    def rate(self, subject, rater, score, kind='human', ref='', note='', source=''):
        """Give (or change) a 1–5 rating on a subject. One per (rater, ref)."""
        return self.store.rate(subject, rater, score, kind, ref, note, source)

    def unrate(self, subject, rater, ref=''):
        """Take a rating back."""
        return self.store.unrate(subject, rater, ref)

    def ratings(self, subject, kind=None):
        """Every rating on a subject, newest first."""
        return {'subject': subject, 'ratings': self.store.ratings(subject, kind)}

    def subjects(self, prefix=''):
        """Everything that has been rated, with count and plain mean."""
        return {'subjects': self.store.subjects(prefix)}

    # ── aggregation ──────────────────────────────────────────────

    def aggregators(self):
        """The registry: id, title, formula, status (live|soon), params."""
        import reviews
        return {'default': reviews.DEFAULT_AGGREGATOR, 'scale': reviews.SCALE,
                'aggregators': reviews.aggregators(), 'published': self.store.published(),
                'publishable': list(reviews.PUBLISHABLE)}

    def score(self, subject, aggregator=None, weights=None):
        """One number for a subject, with its working (per-rater weights,
        formula). aggregator=any built-in, a published id, or market; default is the subject's
        preference, else mean."""
        if isinstance(weights, str):
            weights = json.loads(weights)
        return self.store.score(subject, aggregator, weights)

    def aggregate(self, ratings, aggregator=None, weights=None, subject=''):
        """Stateless: aggregate ratings you pass in ([{rater, kind, score}])."""
        if isinstance(ratings, str):
            ratings = json.loads(ratings)
        if isinstance(weights, str):
            weights = json.loads(weights)
        return self.store.aggregate(ratings, aggregator, weights, subject)

    # ── the market ───────────────────────────────────────────────

    def market(self):
        """Every aggregator ranked by its own ratings (subject agg:<id>), with
        adopters and its weight in the `market` blend."""
        return self.store.market()

    def publish(self, id, base, params=None, title='', author='', note=''):
        """Publish a new aggregator: a base (mean|median|trimmed|consensus|
        recent|weighted) plus params, under a new id. No code runs."""
        if isinstance(params, str):
            params = json.loads(params)
        return self.store.publish(id, base, params, title, author, note)

    def retract(self, id):
        """Take a published aggregator off the market (operator)."""
        return self.store.retract(id)

    def weights(self, subject='*'):
        """The weight table in force for a subject (own, else "*", else identity)."""
        return self.store.weights(subject)

    def set_weights(self, subject='*', weights=None):
        """Publish a weight table: {default, by_kind:{human,agent}, by_rater:{addr}}."""
        if isinstance(weights, str):
            weights = json.loads(weights)
        return self.store.set_weights(subject, weights or {})

    def set_aggregator(self, subject, aggregator):
        """Pick which aggregator answers `score` for a subject by default."""
        return self.store.set_aggregator(subject, aggregator)

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the console and the API on one port."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/',
                'api': f'http://localhost:{port}/'}

    def kill(self, port=None):
        """Stop whatever holds the port. Targets the port, never a name."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests (offline, in a throwaway store)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
