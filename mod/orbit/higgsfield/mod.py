"""higgsfield — Higgsfield AI's generation API as one mod.

Higgsfield (higgsfield.ai) does cinematic generation: Soul for photoreal
text-to-image, DoP for image-to-video with named camera motions (crash zoom,
dolly, 360 orbit, …), Speak for talking avatars. The platform API is async —
you submit a job, get a job-set id back, and poll until the artifact URLs
land. This module wraps that cycle behind the fleet's usual shape: BYOK keys
0600 under ~/.mod/higgsfield (never in this repo), one client answering both
the m CLI and a small REST server, and a raw escape hatch for any route the
named functions don't cover.

    m higgsfield/set_key key=hf-… secret=…            # the operator's key pair
    m higgsfield/image "neon street, rain, 35mm"      # Soul text-to-image
    m higgsfield/video image_url=https://… motion=crash_zoom_in
    m higgsfield/job id=<job_set_id>                  # poll until completed
    m higgsfield/wait id=<job_set_id>                 # …or block until done
    m higgsfield/serve                                # REST on :51210

Upstream paths follow docs.higgsfield.ai; if Higgsfield moves an endpoint,
`raw` still reaches it and the constants below are the only thing to touch.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)

STATE_DIR = os.path.expanduser(os.environ.get('HIGGSFIELD_DIR', '~/.mod/higgsfield'))
UPSTREAM = os.environ.get('HIGGSFIELD_UPSTREAM', 'https://platform.higgsfield.ai')

# The three generation families and where they live upstream.
PATHS = {
    'image': '/v1/text2image/soul',
    'video': '/v1/image2video/dop',
    'speak': '/v1/speak',
    'motions': '/v1/motions',
    'job': '/v1/job-sets/{id}',
}

TERMINAL = {'completed', 'failed', 'canceled', 'nsfw'}


class Mod:
    description = """
    higgsfield — Higgsfield AI (Soul text-to-image, DoP image-to-video with
    named camera motions, Speak avatars) as one mod. BYOK: every call spends
    the caller's own Higgsfield credits. Generation is async — submit returns
    a job-set id, poll it with job/wait until the artifact URLs land.
    """

    def __init__(self, key=None, secret=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51210))
        self._key, self._secret = key, secret

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def _keys(self):
        """(api_key, secret) — per-call kwargs, then env, then the keystore."""
        key = self._key or os.environ.get('HIGGSFIELD_API_KEY')
        secret = self._secret or os.environ.get('HIGGSFIELD_SECRET')
        if key and secret:
            return key, secret
        try:
            with open(os.path.join(STATE_DIR, 'key.json')) as f:
                stored = json.load(f)
            return key or stored.get('key'), secret or stored.get('secret')
        except Exception:
            return key, secret

    def _call(self, path, method='GET', body=None):
        key, secret = self._keys()
        if not (key and secret):
            return {'error': 'no key — m higgsfield/set_key key=hf-… secret=… '
                             '(get one at https://cloud.higgsfield.ai)'}
        url = UPSTREAM.rstrip('/') + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'hf-api-key': key,
            'hf-secret': secret,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')
            try:
                detail = json.loads(detail)
            except Exception:
                pass
            return {'error': f'upstream {e.code}', 'detail': detail, 'path': path}
        except Exception as e:
            return {'error': str(e), 'path': path}

    # ── api ──────────────────────────────────────────────────────

    def info(self):
        """What this module is, and every function and route it serves."""
        cfg = self.config()
        key, secret = self._keys()
        return {
            'name': 'higgsfield',
            'description': ' '.join(self.description.split()),
            'upstream': UPSTREAM,
            'key': bool(key and secret),
            'port': self.port,
            'fns': cfg.get('fns', []),
            'endpoints': cfg.get('endpoints', {}),
        }

    forward = info

    def set_key(self, key=None, secret=None):
        """Store the hf-api-key + hf-secret pair, 0600, off-tree."""
        if not (key and secret):
            return {'error': 'need key= and secret= (https://cloud.higgsfield.ai)'}
        os.makedirs(STATE_DIR, exist_ok=True)
        path = os.path.join(STATE_DIR, 'key.json')
        with open(path, 'w') as f:
            json.dump({'key': key, 'secret': secret}, f)
        os.chmod(path, 0o600)
        return {'stored': path, 'key': key[:8] + '…'}

    def key(self):
        """Whether a key pair resolved, and from where — never the key itself."""
        key, secret = self._keys()
        source = ('kwargs' if self._key else
                  'env' if os.environ.get('HIGGSFIELD_API_KEY') else
                  'keystore' if key else None)
        return {'key': bool(key and secret), 'source': source, 'store': STATE_DIR}

    def image(self, prompt=None, **params):
        """Soul text-to-image. Returns a job-set — poll it with job/wait."""
        if not prompt:
            return {'error': 'need prompt='}
        return self._call(PATHS['image'], 'POST', {'params': {'prompt': prompt, **params}})

    def video(self, image_url=None, prompt=None, motion=None, model='dop', **params):
        """DoP image-to-video. motion= is a named camera move (see motions)."""
        if not image_url:
            return {'error': 'need image_url= (a start frame; make one with image)'}
        body = {'prompt': prompt or '', 'input_images': [{'type': 'image_url', 'image_url': image_url}],
                'model': model, **params}
        if motion:
            body['motions'] = [{'id': motion}] if not isinstance(motion, list) else motion
        return self._call(PATHS['video'], 'POST', {'params': body})

    def speak(self, image_url=None, audio_url=None, **params):
        """Speak — a talking avatar from one image and one audio track."""
        if not (image_url and audio_url):
            return {'error': 'need image_url= and audio_url='}
        return self._call(PATHS['speak'], 'POST',
                          {'params': {'image_url': image_url, 'audio_url': audio_url, **params}})

    def motions(self):
        """The named camera motions DoP accepts (crash zoom, dolly, orbit, …)."""
        return self._call(PATHS['motions'])

    def job(self, id=None):
        """Poll one job-set: status plus artifact URLs once completed."""
        if not id:
            return {'error': 'need id= (the job-set id a submit returned)'}
        return self._call(PATHS['job'].format(id=id))

    def wait(self, id=None, timeout=600, interval=5):
        """Block on a job-set until it reaches a terminal status."""
        if not id:
            return {'error': 'need id='}
        deadline = time.time() + int(timeout)
        while True:
            out = self.job(id=id)
            jobs = out.get('jobs', []) if isinstance(out, dict) else []
            statuses = {j.get('status') for j in jobs} or {out.get('status')} if isinstance(out, dict) else set()
            if 'error' in out or (statuses and statuses <= TERMINAL):
                return out
            if time.time() > deadline:
                return {'error': 'timeout', 'last': out}
            time.sleep(int(interval))

    def raw(self, path=None, method='GET', body=None, **kwargs):
        """Any platform.higgsfield.ai route — the escape hatch."""
        if not path:
            return {'error': 'need path= (e.g. /v1/job-sets/<id>)'}
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                return {'error': 'body must be JSON'}
        return self._call(path if path.startswith('/') else '/' + path, method, body)

    # ── server ───────────────────────────────────────────────────

    def serve(self, port=None, daemon=False):
        """REST on one port: /, /health, /image, /video, /speak, /job, /raw."""
        port = int(port or self.port)
        if daemon:
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), 'serve', str(port)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            return {'serving': port, 'pid': p.pid}
        import http.server
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path, _, q = self.path.partition('?')
                qs = dict(p.split('=', 1) for p in q.split('&') if '=' in p)
                path = path.removeprefix('/higgsfield').rstrip('/') or '/'
                if path == '/':
                    return self._send(outer.info())
                if path == '/health':
                    return self._send({'ok': True})
                if path == '/motions':
                    return self._send(outer.motions())
                if path == '/job':
                    return self._send(outer.job(id=qs.get('id')))
                self._send({'error': 'not found'}, 404)

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b'{}')
                except Exception:
                    return self._send({'error': 'bad json'}, 400)
                path = self.path.partition('?')[0].removeprefix('/higgsfield').rstrip('/')
                fn = {'/image': outer.image, '/video': outer.video,
                      '/speak': outer.speak, '/raw': outer.raw}.get(path)
                if not fn:
                    return self._send({'error': 'not found'}, 404)
                out = fn(**body)
                self._send(out, 400 if isinstance(out, dict) and 'error' in out else 200)

            def log_message(self, *a):
                pass

        http.server.ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()

    def kill(self):
        """Stop the REST server on this module's port."""
        out = subprocess.run(['fuser', '-k', f'{self.port}/tcp'], capture_output=True, text=True)
        return {'killed': self.port, 'out': (out.stdout + out.stderr).strip()}

    def test(self):
        """No-network sanity: config loads, fns exist, key plumbing answers."""
        cfg = self.config()
        missing = [f for f in cfg.get('fns', []) if not callable(getattr(self, f, None))]
        return {'config': bool(cfg), 'missing_fns': missing, 'key': self.key(),
            'ok': bool(cfg) and not missing}

    def readme(self):
        try:
            with open(os.path.join(HERE, 'README.md')) as f:
                return f.read()
        except Exception:
            return ' '.join(self.description.split())


if __name__ == '__main__':
    args = sys.argv[1:]
    m = Mod(port=args[1] if len(args) > 1 and args[1].isdigit() else None)
    if args and args[0] == 'serve':
        m.serve()
    else:
        print(json.dumps(getattr(m, args[0] if args else 'info')(), indent=2, default=str))
