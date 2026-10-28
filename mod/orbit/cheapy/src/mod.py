"""cheapy — the cheapest LLM that can actually do the job.

Describe the job (tokens in, tokens out, how many calls, what it needs:
tools, vision, reasoning, a big context) and get every model that fits,
ranked by what that job costs on it. Prices are OpenRouter's public list,
no key needed; cached an hour, with an offline snapshot as the floor.

    m cheapy                                         # null call → info()
    m cheapy/cheapest input_tokens=8000 output_tokens=1000 calls=500 tools=1
    m cheapy/cheapest vision=1 free=0 limit=5
    m cheapy/compare ids=openai/gpt-4o-mini,anthropic/claude-haiku-4.5
    m cheapy/model id=<model id>
    m cheapy/providers
    m cheapy/estimate text="how many tokens is this?"
    m cheapy/refresh                                 # re-pull prices now
    m cheapy/serve                                   # console + API on :51370
    m cheapy/test
    m cheapy/kill

Anchor file (config.json → "anchor": "src/mod.py"): the orbit loader imports
it by path and instantiates ``Mod``. Every public method is the module's API.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    cheapy — find the cheapest LLM that can do a given job. Say how many
    tokens go in and out, how many calls, and what the model must support;
    get every fitting model ranked by real cost. Keyless (OpenRouter public
    prices), cached, works offline from a bundled snapshot.
    """

    def __init__(self, port=None, **kwargs):
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51370))
        self.base = cfg.get('base_path', '/cheapy')
        self._core = None

    @property
    def core(self):
        if self._core is None:
            import cheapy
            self._core = cheapy.Cheapy()
        return self._core

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(ROOT, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, where its price list came from, every route."""
        cfg = self.config()
        return {'name': 'cheapy', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'stats': self.core.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness, plus how fresh the price list is."""
        return {'ok': True, 'port': self.port, **self.core.stats()}

    def readme(self):
        """The project README."""
        p = os.path.join(ROOT, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── prices ───────────────────────────────────────────────────

    def cheapest(self, input_tokens=2000, output_tokens=500, calls=1,
                 min_context=0, tools=False, vision=False, reasoning=False,
                 free=None, q='', provider='', limit=25, offset=0):
        """Every model that fits the job, cheapest first (USD)."""
        return self.core.cheapest(input_tokens, output_tokens, calls, min_context,
                                  tools, vision, reasoning, free, q, provider,
                                  limit, offset)

    def compare(self, ids, input_tokens=2000, output_tokens=500, calls=1):
        """The same job priced on chosen models (comma-separated ids)."""
        return self.core.compare(ids, input_tokens, output_tokens, calls)

    def model(self, id):
        """One model's prices and capabilities."""
        return self.core.model(id)

    def providers(self):
        """Every provider, with model count and its cheapest paid model."""
        return {'providers': self.core.providers()}

    def estimate(self, text='', chars=None):
        """Rough token count for a piece of text (~4 chars/token)."""
        import cheapy
        return cheapy.estimate(text, chars)

    def refresh(self):
        """Re-pull the price list now instead of waiting out the hour."""
        rows, source, at = self.core.load(refresh=True)
        return {'models': len(rows), 'source': source, 'fetched_at': int(at)}

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
                                cwd=HERE, start_new_session=True)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/',
                'api': f'http://localhost:{port}/cheapest'}

    def kill(self, port=None):
        """Stop whatever holds the port. By port, never by name — a pattern
        kill on this box takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests (offline, against a fake price list)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
