"""fabricas — a custom clothing store where the customer is the designer.

No print-on-demand API, no upload to someone else's cloud: the catalog,
the design format, the mockup renderer, the pricing and the order book
all live in this directory and a SQLite file under ~/.mod/fabricas.
A design is a small JSON dict — garment, fabric color, and up to eight
text/mark layers placed in percent of the print area — so it renders
identically on the CLI, in the console, and on whichever cut it's
remixed onto.

    m fabricas                                # null call → info()
    m fabricas/catalog                        # cuts, fabric, inks, marks, type
    m fabricas/quote design='{...}' qty=10    # price breakdown
    m fabricas/save design='{...}'            # persist → short id
    m fabricas/render id=ab12cd34ef           # SVG mockup
    m fabricas/gallery                        # public designs, newest first
    m fabricas/remix id=ab12cd34ef            # a copy ready to edit
    m fabricas/order id=ab12cd34ef size=L qty=2
    m fabricas/orders author=me               # the order rail
    m fabricas/advance id=... [status=...]    # move an order one stop
    m fabricas/serve                          # console + API on :51050
    m fabricas/test                           # offline tests
    m fabricas/kill                           # stop it

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Everything the module exposes to the CLI, the
gateway and other modules is a public method on this class.
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


def _as_design(design):
    """Accept a dict, or the JSON string the CLI hands over."""
    if isinstance(design, str):
        try:
            design = json.loads(design)
        except json.JSONDecodeError as e:
            raise ValueError(f'design is not valid JSON: {e}')
    if design is None:
        raise ValueError("pass design='{...}' — m fabricas/catalog shows the shape")
    return design


class Mod:
    description = """
    fabricas — design your own clothes and order the cut. Five garments
    (tee, longsleeve, hoodie, tote, cap), twelve stock fabrics, seven inks,
    text and mark layers placed in percent of the print area. Deterministic
    pricing with quantity breaks, an SVG mockup renderer with no external
    dependencies, a public gallery anyone can remix from, and an order rail
    (placed → cutting → printing → sewing → ready → shipped) kept in local
    SQLite. Local-first: nothing here calls out.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51050))
        self.base = cfg.get('base_path', '/fabricas')

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
        return {'name': 'fabricas', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness plus the shop's books at a glance. Local disk only."""
        import store
        return {'ok': True, 'port': self.port, **store.stats()}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None

    # ── the cutting table ────────────────────────────────────────

    def catalog(self):
        """Cuts, fabric, inks, marks, type, pricing and limits — one call,
        with the actual SVG geometry, so any client composes previews from
        the same paths the renderer uses."""
        import atelier
        return atelier.catalog()

    def quote(self, design=None, id=None, size='M', qty=1):
        """Price breakdown for a design (inline JSON or a saved id)."""
        import atelier, store
        if id and not design:
            design = store.get_design(id)['design']
        return atelier.quote(_as_design(design), size=size, qty=qty)

    def render(self, id=None, design=None, width=400):
        """The SVG mockup, by saved id or inline design JSON."""
        import atelier, store
        if id and not design:
            design = store.get_design(id)['design']
        return atelier.render(_as_design(design), width=width)

    # ── the racks ────────────────────────────────────────────────

    def save(self, design=None, author='anon', public=True, remix_of=None):
        """Validate and persist a design. Content-addressed: the same
        design saves to the same short id."""
        import atelier, store
        clean = atelier.validate(_as_design(design))
        did = store.save_design(clean, author=author, public=public,
                                remix_of=remix_of)
        return {'id': did, 'design': clean, 'author': author,
                'render': f'{self.base}/api/render?id={did}'}

    def design(self, id=None):
        """One saved design, whole."""
        import store
        if not id:
            raise ValueError('which design? id=...')
        return store.get_design(id)

    def designs(self, author=None, limit=60):
        """Saved designs, newest first (author= filters to one handle)."""
        import store
        return store.list_designs(author=author, limit=limit)

    def gallery(self, limit=60):
        """Public designs, newest first — the rack anyone can remix from."""
        import store
        return store.list_designs(public=True, limit=limit)

    def remix(self, id=None, author='anon'):
        """A copy of a saved design, ready to edit — carries remix_of so
        attribution survives the save."""
        import store
        if not id:
            raise ValueError('remix what? id=...')
        parent = store.get_design(id)
        design = dict(parent['design'])
        design['name'] = f"{design.get('name', 'untitled')} (remix)"
        return {'design': design, 'remix_of': id,
                'original_author': parent['author'], 'author': author}

    # ── the order rail ───────────────────────────────────────────

    def order(self, id=None, size='M', qty=1, author='anon', note=None):
        """Place an order for a saved design. The quote is computed and
        frozen into the order at placement."""
        import store
        if not id:
            raise ValueError('order what? id=<saved design id>')
        q = self.quote(id=id, size=size, qty=qty)
        oid = store.save_order(id, q['size'], q['qty'], q,
                               author=author, note=note)
        return store.get_order(oid)

    def orders(self, author=None, status=None, limit=60):
        """The order book, newest first (author=, status= filter)."""
        import store
        return store.list_orders(author=author, status=status, limit=limit)

    def advance(self, id=None, status=None):
        """Move an order one stop down the rail — or cancel it. The rail:
        placed → cutting → printing → sewing → ready → shipped."""
        import store
        if not id:
            raise ValueError('advance what? id=<order id>')
        return store.advance_order(id, status=status)

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
        """Run the module's tests (offline — nothing here calls out anyway)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
