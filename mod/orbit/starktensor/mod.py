"""starktensor — a red-team prompt bridge between the pliny corpus and agents.

The elder-plinius "pliny" corpus (L1B3RT4S, G0DM0D3, CL4R1T4S, ...) is a
library of guardrail-bypass / jailbreak prompts. The ``plinyville`` module
already mirrors it DEFANGED, as a security exhibit. starktensor is the next
step along that same safety line: it reads those adversarial prompts out of
plinyville's store and shapes them into agent-protocol TEST overlays, so you
can run your OWN agents against them and see how well their guardrails hold.

It is an evaluation harness, not a production jailbreak switch. Overlays are
tagged ``redteam: true`` and are never auto-applied to a live agent.

    m starktensor                               # null call -> info()
    m starktensor/vendors                        # which pliny repos are red-team sets
    m starktensor/list repo=L1B3RT4S             # the prompts in one source
    m starktensor/list repo=L1B3RT4S q=claude    # filter by filename
    m starktensor/get repo=L1B3RT4S path=AAA.mkd # read one (labelled RED-TEAM)
    m starktensor/search q=GODMODE               # grep the whole corpus
    m starktensor/overlay repo=L1B3RT4S path=AAA.mkd agent=myagent
    m starktensor/probe ...                      # ROADMAP: score an agent
    m starktensor/serve                          # console + API on :51290
    m starktensor/test                           # offline tests
    m starktensor/kill                           # stop it

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on that class.
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
    starktensor — a red-team / robustness-evaluation bridge. It reads the
    elder-plinius jailbreak prompt corpus that the plinyville module mirrors
    (DEFANGED), and exposes it to the agent protocol as labelled TEST overlays
    so you can measure how well your own agents resist adversarial prompts.
    Overlays are tagged redteam=true and are never auto-applied to a live agent.
    Sources are read from the local plinyville store, never re-scraped.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51290))
        self.base = cfg.get('base_path', '/starktensor')
        self._path = path

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
        import library
        v = library.vendors()
        return {'name': 'starktensor', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'corpus': {'available': v.get('available'),
                           'vendors': v.get('count', 0)},
                'safety': cfg.get('safety'),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — touches only the local store."""
        import library
        v = library.vendors()
        return {'ok': True, 'port': self.port,
                'corpus_available': bool(v.get('available')),
                'vendors': v.get('count', 0)}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the corpus ───────────────────────────────────────────────

    def vendors(self):
        """Red-team prompt source repos available in the local pliny corpus."""
        import library
        return library.vendors()

    def list(self, repo, q=''):
        """List prompt entries in one source repo (filename filter q=)."""
        import library
        return library.list_prompts(repo, q)

    def get(self, repo, path):
        """Read one prompt's text, labelled RED-TEAM. Evidence, not an order."""
        import library
        return library.get_prompt(repo, path)

    def search(self, q='', limit=50):
        """Grep the whole corpus for a string (offline, over the store)."""
        import library
        return library.search(q, int(limit))

    # ── the bridge to orbit/agent ─────────────────────────────────

    def overlay(self, repo, path, agent=''):
        """Build an agent-protocol RED-TEAM test overlay from one prompt.

        Returns a document (redteam=true, auto_apply=false) you mount on a
        THROWAWAY agent to probe guardrail robustness — never on production.
        """
        import library
        return library.overlay(repo, path, agent)

    def probe(self, repo='', path='', agent='', **kwargs):
        """ROADMAP: run a target agent against an overlay and score robustness.

        Not wired yet. The intended contract: build the overlay, start a
        THROWAWAY run of `agent` through orbit/agent with the overlay as a FLOW
        input/judge pair, and return a refusal/compliance score — never
        mutating the target agent's own config. Until that lands this returns
        the overlay plus the eval plan so the shape is honest about its status.
        """
        import library
        ov = library.overlay(repo, path, agent) if repo and path else None
        return {
            'status': 'not_implemented',
            'roadmap': True,
            'plan': [
                'build overlay(repo, path)',
                'start a throwaway agent run via orbit/agent (deps: agent)',
                'present the adversarial system_prompt, capture the response',
                'judge refusal vs compliance; return a 0-1 robustness score',
                'discard the throwaway agent; never touch the target config',
            ],
            'overlay': ov,
        }

    # ── surfaces ──────────────────────────────────────────────────

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
        """Run the module's tests (offline, against the local store)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
