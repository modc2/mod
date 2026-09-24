"""@ref — your reference images, searched by pose instead of by folder.

Index the folders your references already live in, give each image the
orientation of its subject (yaw / pitch / roll — auto-estimated when a
local model is installed, gimbal-tagged when not), and from then on you
find references the way you actually think about them: "3/4 left, seen
slightly from above" is a gimbal position, not a filename.

    m @ref                                   # null call → info()
    m @ref/scan path=~/refs tags=figure      # index a folder in place
    m @ref/query yaw=-45 pitch=15            # the gimbal query
    m @ref/query yaw=90 tolerance=30 tags=hands
    m @ref/untagged                          # what still needs a pose
    m @ref/set id=<id> yaw=-45 pitch=10      # pin a pose on one image
    m @ref/describe yaw=-45 pitch=15         # what that view is called
    m @ref/serve                             # gimbal console on :51040
    m @ref/test                              # offline tests
    m @ref/kill

Everything is local: images stay where they are on disk, the index is one
SQLite file in ./data, and no request ever leaves this box.

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


def _tags(tags):
    if tags is None:
        return None
    if isinstance(tags, str):
        return [t for t in tags.replace(',', ' ').split() if t]
    return list(tags)


class Mod:
    description = """
    @ref — a pose-searchable index over the reference images you already
    have. Every indexed image carries a subject orientation (yaw/pitch/roll,
    like a gimbal holding a mannequin); querying is pointing the gimbal, not
    remembering folder names. Poses come from an optional local estimator or
    from you, via the console's drag-gimbal. One SQLite file of state,
    images never move, nothing leaves the machine.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51040))
        self.base = cfg.get('base_path', '/@ref')

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
        import store
        return {'name': '@ref', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'stats': store.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness plus which auto-estimators this box actually has."""
        import estimate
        import store
        return {'ok': True, 'port': self.port, 'db': store.DB_PATH,
                'estimators': estimate.available()}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None

    # ── the index ────────────────────────────────────────────────

    def scan(self, path=None, tags=None, subject='figure', recursive=True):
        """Index every image under a folder, in place. Uses a local pose
        estimator when one is installed; the rest lands in untagged."""
        if not path:
            return {'error': 'path= is required'}
        import estimate
        import store
        est = estimate.estimate if estimate.available() else None
        return store.scan(path, tags=_tags(tags), subject=subject,
                          recursive=recursive, estimator=est)

    def add(self, path=None, yaw=None, pitch=None, roll=None, tags=None,
            subject='figure'):
        """Index a single image, optionally with its pose."""
        if not path:
            return {'error': 'path= is required'}
        import store
        return store.add(path, yaw=yaw, pitch=pitch, roll=roll,
                         tags=_tags(tags), subject=subject)

    def set(self, id=None, yaw=None, pitch=0, roll=0, tags=None):
        """Pin a pose (and optionally tags) onto an indexed image."""
        if not id or yaw is None:
            return {'error': 'id= and yaw= are required'}
        import store
        return store.set_pose(id, yaw, pitch, roll, tags=_tags(tags))

    def remove(self, id=None):
        """Drop an image from the index. The file on disk is untouched."""
        if not id:
            return {'error': 'id= is required'}
        import store
        return store.remove(id)

    # ── the gimbal ───────────────────────────────────────────────

    def query(self, yaw=0, pitch=0, roll=0, tolerance=45, limit=24,
              tags=None, subject=None, roll_weight=0.5):
        """Point the gimbal: references within tolerance° of the view,
        nearest first. tags= narrows, roll_weight= discounts tilt."""
        import store
        return store.query(float(yaw), float(pitch), float(roll),
                           tolerance=float(tolerance), limit=limit,
                           tags=_tags(tags), subject=subject,
                           roll_weight=float(roll_weight))

    def similar(self, id=None, tolerance=45, limit=24):
        """References posed like an image you already have."""
        if not id:
            return {'error': 'id= is required'}
        import store
        ref = store.get(id)
        if ref.get('yaw') is None:
            return ref if 'error' in ref else {'error': f'{id} has no pose yet'}
        out = store.query(ref['yaw'], ref['pitch'], ref['roll'],
                          tolerance=float(tolerance), limit=int(limit) + 1)
        out['refs'] = [r for r in out['refs'] if r['id'] != id][:int(limit)]
        return out

    def describe(self, yaw=0, pitch=0, roll=0):
        """What a gimbal position is called, in an artist's words."""
        import orientation as ori
        y, p, r = ori.normalize(float(yaw), float(pitch), float(roll))
        return {'yaw': y, 'pitch': p, 'roll': r, 'view': ori.describe(y, p, r)}

    def untagged(self, limit=50):
        """Indexed images still waiting for a pose."""
        import store
        return store.untagged(limit=limit)

    def list(self, limit=100, tags=None, subject=None):
        """Everything indexed, newest first."""
        import store
        return store.listing(limit=limit, tags=_tags(tags), subject=subject)

    def stats(self):
        """Index totals, tag counts, and where the database lives."""
        import store
        return store.stats()

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the gimbal console and the API on one port."""
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
        """Run the module's tests (offline — pure math and a temp DB)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
