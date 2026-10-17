"""jokes — store and share jokes from the comedians who told them.

Every joke is credited (comedian, and where it is from: a special, an album,
a set, a link). The book is one local SQLite file; sharing is a pack — plain
JSON you hand to someone else, who imports it and gets the same ids, so two
books merge without duplicates.

    m jokes                                         # null call → info()
    m jokes/add text="..." comedian="Mitch Hedberg" source="Strategic Grill Locations" tags=oneliner
    m jokes/search q=escalator                      # text, comedian, source, tags
    m jokes/search comedian="Mitch Hedberg" sort=top
    m jokes/random                                  # one, any comedian
    m jokes/comedians                               # who is in the book
    m jokes/share id=<id>                           # a link + a one-joke pack
    m jokes/export comedian="Mitch Hedberg"         # a pack to hand to a friend
    m jokes/import_pack pack='{"jokes": [...]}'     # merge someone's pack in
    m jokes/serve                                   # console + API on :51130
    m jokes/test                                    # offline tests
    m jokes/kill                                    # stop it

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
    jokes — a jokebook for other people's jokes. Save a joke with the comedian
    who told it and where it is from, search and tag your collection, pull a
    random one, and share: one joke as a link, or a whole comedian as a pack
    that someone else imports. Local SQLite, stdlib only, no accounts or keys.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51130))
        self.base = cfg.get('base_path', '/jokes')
        self._path = path
        self._book = None

    @property
    def book(self):
        if self._book is None:
            import jokebook
            self._book = jokebook.Jokebook(self._path)
        return self._book

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
        return {'name': 'jokes', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'stats': self.book.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — touches only the local file."""
        return {'ok': True, 'port': self.port, **self.book.stats()}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the book ─────────────────────────────────────────────────

    def add(self, text, comedian='', source='', year=None, tags=None, by=''):
        """Save a joke, credited to the comedian who told it."""
        return self.book.add(text, comedian, source, year, tags, by)

    def get(self, id):
        """One joke by id."""
        return self.book.get(id)

    def search(self, q='', comedian='', tag='', sort='new', limit=50, offset=0):
        """Search text, comedian, source and tags (sort=new|top|old|random)."""
        return self.book.search(q, comedian, tag, sort, limit, offset)

    def random(self, comedian='', tag=''):
        """One joke at random, optionally from one comedian or tag."""
        return self.book.random(comedian, tag)

    def vote(self, id, delta=1):
        """+1 or -1 a joke. Votes are local; they never travel in a pack."""
        return self.book.vote(id, delta)

    def remove(self, id):
        """Delete a joke from this book."""
        return self.book.remove(id)

    def comedians(self):
        """Every comedian in the book, with joke counts."""
        return {'comedians': self.book.comedians()}

    def tags(self):
        """Every tag in the book, with counts."""
        return {'tags': self.book.tags()}

    # ── sharing ──────────────────────────────────────────────────

    def share(self, id, host=None):
        """One joke as something to send: a link, plain text, and a pack."""
        j = self.book.get(id)
        if not j:
            return {'error': f'no joke {id}'}
        host = (host or f'http://localhost:{self.port}').rstrip('/')
        credit = j['comedian'] + (f" — {j['source']}" if j['source'] else '')
        return {'id': id, 'url': f'{host}{self.base}/?j={id}',
                'text': f"{j['text']}\n\n— {credit}",
                'pack': self.book.export(ids=[id])}

    def export(self, ids=None, comedian='', tag='', name=''):
        """A pack (plain JSON) of chosen ids, a comedian, a tag, or everything."""
        return self.book.export(ids, comedian, tag, name)

    def import_pack(self, pack, by=''):
        """Merge someone's pack in. Same joke + comedian = same id = no duplicate."""
        return self.book.import_pack(pack, by)

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
        """Run the module's tests (offline, in a throwaway book)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
