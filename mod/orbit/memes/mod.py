"""memes — a meme search engine over the sites that actually have the memes.

One query fans out to Reddit's meme subreddits, Imgflip's template list,
Know Your Meme's encyclopedia and 9GAG, comes back as one deduplicated,
score-ranked list, and lands in a console that is just a search box and a
wall of memes. No keys anywhere; a source that blocks us reports itself in
``errors`` and the rest still answer.

    m memes                          # null call → info()
    m memes/search q="this is fine"  # every site at once
    m memes/search q=doge source=knowyourmeme
    m memes/trending                 # what is hot right now
    m memes/random                   # one, off the top of the hot feeds
    m memes/templates q=drake        # imgflip's blank canvases
    m memes/serve                    # console + API on :50900
    m memes/test                     # offline tests
    m memes/kill                     # stop it

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Everything the module exposes to the CLI, the
gateway and other modules is a public method on that class.
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
    memes — a meme search engine that scrapes the meme sites. One query fans
    out to Reddit's meme subreddits, Imgflip's templates, Know Your Meme and
    9GAG in parallel and comes back as one deduplicated, score-ranked list.
    Trending, random, and template lookup on top; a console that is a search
    box and a wall of memes. No API keys; dead sources degrade, never break.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50900))
        self.base = cfg.get('base_path', '/memes')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        cfg = self.config()
        return {'name': 'memes', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'sources': self.sources()['sources'],
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — no network calls, so it answers even when every source is down."""
        import sites
        return {'ok': True, 'port': self.port, 'sources': sites.SOURCES,
                'subreddits': sites.SUBS}

    def sources(self):
        """The sites this engine reaches, and how it reaches each one."""
        import sites
        return {'sources': sites.SOURCES, 'subreddits': sites.SUBS,
                'how': {'reddit': 'public JSON — search + hot across the meme subs',
                        'imgflip': 'the public template list, cached an hour',
                        'knowyourmeme': 'search page HTML, scraped',
                        'ninegag': 'the undocumented search-posts JSON'}}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None

    # ── the engine ───────────────────────────────────────────────

    def search(self, q=None, source='all', limit=24, nsfw=False):
        """Search every meme site at once, or one (source=reddit|imgflip|knowyourmeme|ninegag)."""
        import sites
        return sites.search(q, source=source, limit=limit, nsfw=nsfw)

    def trending(self, limit=24, nsfw=False):
        """What is hot right now — Reddit's and 9GAG's hot feeds, merged."""
        import sites
        return sites.trending(limit=limit, nsfw=nsfw)

    def random(self, nsfw=False):
        """One meme, at random, off the top of the hot feeds."""
        import sites
        return sites.random_meme(nsfw=nsfw)

    def templates(self, q=None, limit=100):
        """Imgflip's blank canvases — the templates memes get captioned from."""
        import sites
        return {'templates': sites.imgflip_search(q or '', limit=limit)}

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
        """Run the module's tests (offline — no source is hit)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
