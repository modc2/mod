"""boxd — a Letterboxd lens. Public pages in, answers out, no API key.

Letterboxd's API has been invite-only beta for years, so this reads what the
site serves anyone: a member's RSS feed (the diary, with ratings, likes,
rewatches and reviews as real fields), a film's JSON-LD block, and the poster
wall. The reading is the easy part — the point is the two questions the site
itself does not answer well:

    what does this diary say about the person keeping it, and
    do these two people agree about films?

    m boxd                               # null call → info()
    m boxd/diary user=dave               # the feed, newest first
    m boxd/reviews user=davidehrlich     # just the ones they wrote about
    m boxd/taste user=dave               # how they rate: mean, histogram, decades
    m boxd/overlap a=dave b=davidehrlich # shared films, agreement, hardest splits
    m boxd/film title=parasite year=2019 # one film off its JSON-LD
    m boxd/films user=dave               # the poster wall
    m boxd/serve                         # console + API on :50940
    m boxd/test                          # offline tests
    m boxd/kill                          # stop it

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on this class.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    boxd — a Letterboxd lens with no API key. Reads a member's public RSS
    diary, a film's JSON-LD and the poster wall through one throttled, cached
    fetcher, then does the arithmetic Letterboxd doesn't: a taste profile
    (mean, histogram, like and rewatch rates, decades, generosity against the
    site average) and an overlap between two members (shared films, agreement
    score, the hardest disagreements, and what each has seen that the other
    hasn't). Letterboxd 403s a chatty client within seconds, so a block is
    reported as a block and the cache answers when it can.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50940))
        self.base = cfg.get('base_path', '/boxd')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """Null call — what this module is, and every route it serves."""
        cfg = self.config()
        import letterboxd as lb
        return {'name': 'boxd', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'doors': {k: v['open'] for k, v in lb.DOORS.items()},
                'no_key': "letterboxd's API is invite-only; nothing here uses it",
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — no network call, so it answers when Letterboxd is blocking."""
        import letterboxd as lb
        cached = 0
        try:
            cached = len([n for n in os.listdir(lb.CACHE_DIR) if n.endswith('.json')])
        except OSError:
            pass
        return {'ok': True, 'port': self.port,
                'doors_open': [k for k, v in lb.DOORS.items() if v['open']],
                'cache_dir': lb.CACHE_DIR, 'cached_pages': cached,
                'min_interval_s': lb.MIN_INTERVAL}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None

    def sources(self):
        """The doors Letterboxd leaves open to a reader with no account —
        and, just as usefully, the ones it does not."""
        import letterboxd as lb
        return {'source': lb.BASE, 'doors': lb.DOORS,
                'open': [k for k, v in lb.DOORS.items() if v['open']],
                'shut': [k for k, v in lb.DOORS.items() if not v['open']],
                'throttle': f'{lb.MIN_INTERVAL}s between requests, fleet-wide',
                'cache_ttl_s': lb.TTL,
                'no_key': "letterboxd's API is invite-only; nothing here uses it"}

    # ── the lens ─────────────────────────────────────────────────
    #
    # Every method below is one line onto letterboxd.py. That is deliberate:
    # serve.py dispatches HTTP straight at these methods, so anything written
    # here rather than there would be logic the CLI has and the API doesn't.

    def diary(self, user=None, limit=50, kind='all', fresh=False):
        """A member's feed, newest first (kind=watch|review|list|all)."""
        import letterboxd as lb
        return lb.diary(user, limit=limit, kind=kind, fresh=fresh)

    def reviews(self, user=None, limit=20, fresh=False):
        """Only the entries they actually wrote something about."""
        import letterboxd as lb
        return lb.reviews(user, limit=limit, fresh=fresh)

    def member(self, user=None, fresh=False):
        """Who this is, plus the headline numbers off their diary."""
        import letterboxd as lb
        return lb.member(user, fresh=fresh)

    def taste(self, user=None, fresh=False):
        """How they rate: mean, median, histogram, likes, rewatches, decades."""
        import letterboxd as lb
        return lb.taste_of(user, fresh=fresh)

    def overlap(self, a=None, b=None, fresh=False):
        """Two members against each other — agreement, splits, what to steal."""
        import letterboxd as lb
        return lb.compare(a, b, fresh=fresh)

    def film(self, title=None, year=None, fresh=False):
        """One film by slug or title — cast, crew, genres, runtime, site rating.

        Pass year= for anything with a common name: Letterboxd gives the bare
        slug to whichever film claimed it first, so /film/parasite/ is a 1982
        creature feature, not Bong Joon Ho's.
        """
        import letterboxd as lb
        if not title:
            raise ValueError('which film? title=parasite (year= disambiguates)')
        return lb.film(title, year=year, fresh=fresh)

    def films(self, user=None, fresh=False):
        """The poster wall — everything a member has logged, newest first."""
        import letterboxd as lb
        return lb.films(user, fresh=fresh)

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
        """Stop whatever is holding the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests (offline — Letterboxd is never hit)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
