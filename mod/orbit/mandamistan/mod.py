"""mandamistan — how Mamdani fixes the New York housing crisis.

A research-backed explainer with a visual console: the crisis in numbers,
the "Block by Block" plan pillar by pillar, the timeline of what has
actually been delivered (the Oct 1, 2026 rent freeze is live), the honest
critiques with their counters, and a transparent simulator that projects
affordable stock and tenant savings under assumptions you control.

    m mandamistan                       # null call → info()
    m mandamistan/crisis                # the crisis in numbers, sourced
    m mandamistan/plan                  # Block by Block, pillar by pillar
    m mandamistan/pillar id=freeze      # one pillar in full
    m mandamistan/timeline              # promised → delivered
    m mandamistan/critiques             # the fight: claims and counters
    m mandamistan/simulate new_per_year=25000 freeze_years=3
    m mandamistan/sources               # every number's citation
    m mandamistan/serve                 # visual console + API on :51240
    m mandamistan/test                  # offline tests
    m mandamistan/kill                  # stop it

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

import housing  # noqa: E402


class Mod:
    description = """
    mandamistan — the case that Mamdani can fix the New York housing crisis,
    with receipts. Crisis numbers, the Block by Block plan, a delivered/next
    timeline, the critiques and their counters, a what-if simulator, and a
    visual console. Static data + stdlib only; every figure is sourced.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51240))
        self.base = cfg.get('base_path', '/mandamistan')

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
        return {'name': 'mandamistan', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'plan': housing.PLAN['name'],
                'status': 'rent freeze in effect since 2026-10-01',
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — touches nothing but local data."""
        return {'ok': True, 'port': self.port,
                'pillars': len(housing.PLAN['pillars']),
                'sources': len(housing.SOURCES)}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the case ─────────────────────────────────────────────────

    def crisis(self):
        """The New York housing crisis in numbers, every figure sourced."""
        return housing.CRISIS

    def plan(self):
        """Block by Block: the whole plan, pillar by pillar."""
        return housing.PLAN

    def pillar(self, id):
        """One pillar in full (freeze, build, preserve, nycha, zoning, tenants)."""
        for p in housing.PLAN['pillars']:
            if p['id'] == id:
                return p
        return {'error': f'no pillar {id}',
                'pillars': [p['id'] for p in housing.PLAN['pillars']]}

    def timeline(self):
        """Promised → delivered: what has actually happened, dated."""
        return {'timeline': housing.TIMELINE}

    def critiques(self):
        """The fight: the strongest objections, and the counters."""
        return {'critiques': housing.CRITIQUES}

    def simulate(self, new_per_year=20000, preserved_per_year=20000,
                 freeze_years=2, rgb_hike=3.0, attrition_per_year=10000,
                 median_stabilized_rent=1500, years=10):
        """Project affordable stock and tenant savings, plan vs status quo.
        A transparent toy — every assumption is an input you can change."""
        return housing.simulate(new_per_year, preserved_per_year, freeze_years,
                                rgb_hike, attrition_per_year,
                                median_stabilized_rent, years)

    def sources(self):
        """Every citation behind the numbers."""
        return {'sources': housing.SOURCES}

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the visual console and the API on one port."""
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
        """Run the module's tests (offline)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
