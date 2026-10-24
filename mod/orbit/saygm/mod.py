"""saygm — the SayGM LLM gateway as a mod.

SayGM (https://saygm.com) puts Claude, GPT, Gemini and open-weight models
behind one key and one base URL, priced at or below the maker's list, with
every request sealed inside an Intel TDX enclave (the ``-tee`` models run
inference inside it too). This module makes it a mod: the live catalog and
prices, a cost quote against list price, one ``chat`` call that picks the
right wire shape per model, and an OpenAI/Anthropic-compatible passthrough
at ``/v1`` so any SDK on the box can point at it.

    m saygm                                         # null call → info()
    m saygm/models                                  # live catalog, USD per Mtok
    m saygm/models tier=confidential sort=input     # TEE models, cheapest first
    m saygm/models sort=discount                    # biggest discount vs list
    m saygm/model id=claude-sonnet-5
    m saygm/quote model=gpt-5.5 input_tokens=20000 output_tokens=2000
    m saygm/set_key key=<gm-api-key>                # stored 0600 in ~/.mod/saygm
    m saygm/chat model=glm-5.3-flash prompt="say gm"
    m saygm/credits                                 # prepaid balance
    m saygm/serve                                   # console + API on :51270
    m saygm/test                                    # offline tests
    m saygm/kill

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Every public method is a fn on the CLI, the gateway
and to other modules.
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

import gm  # noqa: E402


class Mod:
    description = """
    saygm — the SayGM gateway (saygm.com) as a mod. 65 models from Anthropic,
    OpenAI, Google and the open labs behind one key, priced at or below list,
    every request sealed in an Intel TDX enclave. Live catalog + prices, cost
    quotes vs list, one chat call for every model, and an OpenAI/Anthropic
    compatible /v1 passthrough. Bring your own SayGM key.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51270))
        self.base = cfg.get('base_path', '/saygm')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, the key state, and every route it serves."""
        cfg = self.config()
        return {'name': 'saygm', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'upstream': gm.API, 'key': gm.mask(gm.load_key()),
                'app': f'http://localhost:{self.port}{self.base}/',
                'openai_base_url': f'http://localhost:{self.port}/v1',
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — local only, never calls upstream."""
        return {'ok': True, 'port': self.port, 'key_set': bool(gm.load_key())}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── catalog ──────────────────────────────────────────────────

    def models(self, q='', tier='', lab='', shape='', confidential=None,
               available=True, sort='id', limit=200):
        """Live catalog in USD per Mtok (tier=frontier|confidential|open,
        sort=id|input|output|discount)."""
        rows = gm.models(q, tier, lab, shape, confidential, available, sort, limit)
        return {'count': len(rows), 'models': rows}

    def model(self, id):
        """One model: prices, list prices, discount, wire shapes, capabilities."""
        return gm.model(id)

    def labs(self):
        """Every lab in the catalog, with model counts."""
        return {'labs': gm.labs()}

    def quote(self, model, input_tokens=1000, output_tokens=1000):
        """Cost of one call here vs the maker's list price, in USD."""
        return gm.quote(model, input_tokens, output_tokens)

    def refresh(self):
        """Drop the 5-minute catalog cache and refetch."""
        return {'count': len(gm.fetch_models(refresh=True))}

    # ── calling (needs a key) ────────────────────────────────────

    def set_key(self, key=''):
        """Store a SayGM key in ~/.mod/saygm/key.json (0600). Empty clears it."""
        return gm.save_key(key)

    def chat(self, model, prompt='', messages=None, system='', max_tokens=1024,
             temperature=None, confirm=False):
        """One text call to any chat model; the wire shape is picked per model.
        Refuses (needs_confirm) when max_tokens could cost over $0.50."""
        return gm.chat(model, prompt, messages, system, max_tokens, temperature, confirm)

    def credits(self):
        """The stored key's prepaid balance."""
        return gm.credits()

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the console, the API and the /v1 passthrough on one port."""
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
        """Run the module's tests (offline, against a catalog fixture)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
