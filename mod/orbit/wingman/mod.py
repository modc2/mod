"""wingman — a folder of photos in, a dating-app-ready lineup out.

Tinder, Hinge and Bumble each show a photo in a fixed card, and most people
upload whatever the camera roll had: a landscape shot where the face becomes a
dot, a group where nobody knows which one you are, two near-identical selfies,
and a 12 MB file with the GPS coordinates of their kitchen in it. This module
does the part a program can do and says so about the part it cannot.

    m wingman/add dir=~/Pictures/me           # a set, from a folder
    m wingman/audit <set>                      # what each photo is, and what it costs
    m wingman/lineup <set> n=6                 # the best six, in order, with gaps
    m wingman/render <set> preset=hinge        # face-aware crops, polished, EXIF gone
    m wingman/export <set> preset=tinder       # the lineup zipped in slot order
    m wingman/serve                            # console, API and MCP on :50830

Measured, not felt: faces are found by a detector (UltraFace on onnxruntime),
sharpness is the Laplacian variance, exposure is the histogram. Expression,
outfit, setting — not scored, because nothing here can see them. No skin is
smoothed, no background is replaced, and nothing leaves this box.
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
    wingman — turn a set of photos into Tinder/Hinge/Bumble-ready portraits.
    Audit each photo (faces, sharpness, exposure, duplicates, GPS in EXIF),
    pick the best N in the order they should go up with the gaps named, then
    crop face-aware to each app's card ratio, polish gently and strip every
    byte of metadata. Twelve MCP tools, a REST API and a console on one port.
    Local only; nothing retouched; nothing uploaded.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50830))
        self.base = cfg.get('base_path', '/wingman')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    def health(self):
        """Runtime, which face detector is live, where state lives."""
        import engine
        return engine.health()

    def guide(self):
        """What makes a profile photo work — the checkable part — and the card ratios."""
        import mcp
        return mcp.call_tool('wingman_guide', {})

    def presets(self):
        """The app card ratios and output sizes."""
        import engine
        return {'presets': engine.PRESETS, 'default': engine.DEFAULT_PRESET}

    # ── the sets ─────────────────────────────────────────────────

    def sets(self, limit=100):
        """Every set held, newest first."""
        import engine
        return engine.sets(limit=limit)

    ls = sets

    def new(self, name=None):
        """An empty set; returns its unguessable id."""
        import engine
        return engine.new_set(name)

    def add(self, set=None, dir=None, path=None, url=None, data=None, name=None,
            set_name=None):
        """Photos in — a directory, a file, a URL or base64. New set if none given."""
        import engine
        return engine.add(set_ref=set, dir=dir, path=path, url=url, data=data,
                          name=name, set_name=set_name)

    def get(self, set):
        """One set and its photos."""
        import engine
        return engine.get_set(set)

    def remove(self, set, photo):
        """Drop one photo from a set."""
        import engine
        return engine.remove(set, photo)

    def rm(self, set):
        """Delete a whole set."""
        import engine
        return engine.delete_set(set)

    delete = rm

    # ── the work ─────────────────────────────────────────────────

    def audit(self, set, photo=None, force=False):
        """Measure every photo: faces, role, sharpness, exposure, issues, score, verdict."""
        import engine
        return engine.audit(set, photo=photo, force=force)

    def faces(self, set, photo, threshold=0.7):
        """Face boxes for one photo, in source pixels."""
        import engine
        return engine.faces(set, photo, threshold=threshold)

    def lineup(self, set, n=6, min_score=35, allow_group=True, force=False):
        """The best N in order, why each, why not the rest, and what the set lacks."""
        import engine
        return engine.lineup(set, n=n, min_score=min_score, allow_group=allow_group,
                             force=force)

    def render(self, set, photo=None, preset=None, ratio=None, size=None, zoom='auto',
               polish='auto', quality=90, force=False, only_lineup=False, n=6):
        """Face-aware crop to an app's card, polished, metadata stripped."""
        import engine
        return engine.render(set, photo=photo, preset=preset, ratio=ratio, size=size,
                             zoom=zoom, polish_mode=polish, quality=quality, force=force,
                             only_lineup=only_lineup, n=n)

    def export(self, set, preset=None, n=6, zoom='auto', polish='auto', quality=90,
               force=False):
        """Lineup → renders → a zip in slot order with report.json."""
        import engine
        return engine.export(set, preset=preset, n=n, zoom=zoom, polish_mode=polish,
                             quality=quality, force=force)

    # ── surfaces ─────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry, as an agent sees it."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, **args):
        """Invoke one MCP tool directly, without a transport in the way."""
        import mcp
        return mcp.call_tool(tool, args)

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'wingman': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, background=False):
        """Run the REST API, the console and the MCP server on one port."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'api.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}

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
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
