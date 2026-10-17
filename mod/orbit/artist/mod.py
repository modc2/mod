"""artist — a drag-and-drop studio that compiles clips and music into one film.

Drop video, audio and image files into the library; drag them onto a two-lane
timeline (picture on top, sound underneath); press EXPORT and the browser
itself compiles the cut into a single webm — no cloud, no accounts, no keys.
When ffmpeg is installed on the host, RENDER does the same compile server-side
into an mp4. AI engines (Veo, Lyria, ElevenLabs, local models, anything) are
optional sources: whatever produces a file drops into the library like any
other clip.

    m artist                                        # null call → info()
    m artist/assets                                 # the library
    m artist/projects                               # saved cuts
    m artist/render id=<project>                    # server-side mp4 (ffmpeg)
    m artist/export_pack id=<project>               # project + assets as JSON
    m artist/import_pack pack='{...}'               # merge someone's pack in
    m artist/serve                                  # studio + API on :51140
    m artist/test                                   # offline tests
    m artist/kill                                   # stop it

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
    artist — a drag-and-drop media studio. Keep a local library of video,
    audio and image assets; arrange them on a two-lane timeline; compile the
    cut in the browser itself (webm) or server-side with ffmpeg when it is
    installed (mp4). Share a whole project as a plain JSON pack. Local SQLite
    and files, stdlib only, no accounts or keys.
    """

    def __init__(self, port=None, path=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51140))
        self.base = cfg.get('base_path', '/artist')
        self._path = path
        self._studio = None

    @property
    def studio(self):
        if self._studio is None:
            import studio
            self._studio = studio.Studio(self._path)
        return self._studio

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
        return {'name': 'artist', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'stats': self.studio.stats(),
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — touches only local files."""
        return {'ok': True, 'port': self.port, **self.studio.stats()}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the library ──────────────────────────────────────────────

    def assets(self, kind=''):
        """The asset library (kind=video|audio|image to filter)."""
        return {'assets': self.studio.assets(kind)}

    def add_asset(self, name, path=None, data=None):
        """Save a media file into the library, from a host path or raw bytes."""
        if path:
            with open(os.path.expanduser(path), 'rb') as f:
                data = f.read()
            name = name or os.path.basename(path)
        if data is None:
            return {'error': 'pass path= or data='}
        return self.studio.add_asset(name, data)

    def remove_asset(self, id):
        """Delete one asset from the library."""
        return self.studio.remove_asset(id)

    # ── projects ─────────────────────────────────────────────────

    def projects(self):
        """Every saved project."""
        return {'projects': self.studio.projects()}

    def project(self, id):
        """One project, timeline included."""
        return self.studio.get_project(id)

    def save_project(self, name, timeline, id=None):
        """Create or update a project (timeline = {video: [...], audio: [...]})."""
        return self.studio.save_project(name, timeline, id)

    def remove_project(self, id):
        """Delete a project (its assets stay in the library)."""
        return self.studio.remove_project(id)

    def render(self, id, width=1280, height=720, fps=30):
        """Compile a project into one mp4 server-side. Needs ffmpeg on the
        host; the console's EXPORT compiles in the browser without it."""
        return self.studio.render(id, int(width), int(height), int(fps))

    # ── sharing ──────────────────────────────────────────────────

    def export_pack(self, id):
        """A project plus every asset it uses, as plain JSON to hand around."""
        return self.studio.export_pack(id)

    def import_pack(self, pack):
        """Merge someone's pack in. Content-hash asset ids: no duplicates."""
        return self.studio.import_pack(pack)

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the studio console and the API on one port."""
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
        """Run the module's tests (offline, in a throwaway studio)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
