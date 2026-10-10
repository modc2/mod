"""judge — a multisig of agent judges that approves or fails an input.

The creator of a panel sets the params: the judges that sit on it (LLM
agents with a criteria prompt, or deterministic rule judges), the approval
threshold (0-100) and the quorum. Every judge scores the input 0-100; if
the weighted average falls under the threshold — or too few judges manage
to vote — the verdict is FAIL. Verdicts are recorded with every judge's
score and reason.

    m judge                                        # null call → info()
    m judge/create_panel name=pr creator=alice threshold=70 \
        judges='[{"name":"clarity","kind":"llm","prompt":"Is it clear?"},
                 {"name":"short","kind":"rule","max_len":500}]'
    m judge/judge panel=pr input="the thing to approve"
    m judge/panels                                 # every panel, with params
    m judge/verdicts panel=pr                      # the record
    m judge/serve                                  # console + API on :51150
    m judge/test                                   # offline tests
    m judge/kill                                   # stop it

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
    judge — approval as a multisig of agents. A panel's creator picks the
    judges (LLM agents with criteria prompts, or offline rule judges), the
    threshold and the quorum; each judge scores an input 0-100 and the
    weighted average must reach the threshold or the verdict fails. Errors
    fail closed: a judge that cannot vote never counts toward approval.
    Local SQLite, stdlib only.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51150))
        self.base = cfg.get('base_path', '/judge')
        self._path = path
        self._panels = None

    @property
    def book(self):
        if self._panels is None:
            import panel
            self._panels = panel.Panels(self._path)
        return self._panels

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
        return {'name': 'judge', 'description': self.description.strip(),
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

    # ── panels: the creator sets the params ──────────────────────

    def create_panel(self, name, creator, judges, threshold=60, min_votes=None):
        """Create a panel: judges, threshold 0-100, quorum (default: all must vote)."""
        return self.book.create(name, creator, judges, threshold, min_votes)

    def panel(self, name):
        """One panel with its full params."""
        return self.book.get(name)

    def panels(self):
        """Every panel, with params and verdict counts."""
        return {'panels': self.book.list()}

    def update_panel(self, name, creator, judges=None, threshold=None, min_votes=None):
        """Change a panel's params — creator only."""
        return self.book.update(name, creator, judges, threshold, min_votes)

    def remove_panel(self, name, creator):
        """Delete a panel — creator only. Its verdicts stay on record."""
        return self.book.remove(name, creator)

    # ── judging ──────────────────────────────────────────────────

    def judge(self, panel, input):
        """Put an input before the panel: every judge scores it 0-100 and the
        weighted average must reach the threshold, else the verdict is FAIL."""
        return self.book.judge(panel, input)

    def verdict(self, id):
        """One verdict, with every judge's score and reason."""
        return self.book.verdict(int(id))

    def verdicts(self, panel='', limit=50, offset=0):
        """The verdict record, newest first, optionally for one panel."""
        return {'verdicts': self.book.verdicts(panel, limit, offset)}

    def verify(self, id):
        """Re-check every signature on one verdict. Each vote is signed by
        the judge's whole keyring — classical ed25519 plus the quantum-
        resistant ml-dsa-65 and wots-sha256 — so a tampered record fails."""
        return self.book.verify_verdict(int(id))

    def key_kinds(self):
        """The signature key types judges are issued, and their availability."""
        import keys
        return {'kinds': keys.kinds()}

    # ── the judge market ─────────────────────────────────────────

    @property
    def shop(self):
        if getattr(self, '_market', None) is None:
            import market as mkt
            self._market = mkt.Market(self.book)
        return self._market

    def market(self, q='', kind=''):
        """Browse the judge market — most installed first; q searches
        name/author/description/tags, kind filters rule/llm/agent/panel."""
        return {'listings': self.shop.list(q, kind)}

    def listing(self, id):
        """One market listing with its full judge spec."""
        return self.shop.get(int(id))

    def publish_judge(self, name, author, spec, description='', tags=None):
        """List a judge spec on the market; re-publishing your own name
        updates the listing in place."""
        return self.shop.publish(name, author, spec, description, tags)

    def unpublish_judge(self, id, author):
        """Take a listing off the market — its author only."""
        return self.shop.unpublish(int(id), author)

    def install_judge(self, id, panel, creator, name=None, weight=None):
        """Seat a market judge on a panel — the panel's creator only."""
        return self.shop.install(int(id), panel, creator, name, weight)

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
        """Run the module's tests (offline, in a throwaway store)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
