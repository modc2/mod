"""
Offline end-to-end: a local fake x402 video seller (402 -> verify signature ->
async job -> clip), driven through Mod.make — plus fake peer tools (artist,
artlist, sound2text) behind the fleet bridge, with activator='' so nothing
knocks on the real fleet. Runs under pytest or `m vidz/test`.
"""
import base64
import hashlib
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vidzkit import planner, x402  # noqa: E402

PAY_TO = '0x000000000000000000000000000000000000dEaD'
ASSET = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
PRICE = 250000  # 0.25 USDC


def requirement(port, network='base'):
    return {'scheme': 'exact', 'network': network, 'maxAmountRequired': str(PRICE), 'amount': str(PRICE),
            'resource': f'http://127.0.0.1:{port}/gen', 'payTo': PAY_TO, 'asset': ASSET,
            'maxTimeoutSeconds': 300, 'extra': {'name': 'USD Coin', 'version': '2'}}


def seller():
    paid = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj, headers=None):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            port = self.server.server_address[1]
            v2 = self.path.startswith('/v2')
            req = requirement(port, 'eip155:8453' if v2 else 'base')
            hdr = self.headers.get('PAYMENT-SIGNATURE' if v2 else 'X-PAYMENT')
            if not hdr:
                body = {'x402Version': 2 if v2 else 1, 'accepts': [req]}
                if v2:
                    return self._json(402, {}, {'PAYMENT-REQUIRED': base64.b64encode(json.dumps(body).encode()).decode()})
                return self._json(402, body)
            signer = x402.recover(hdr, req)
            paid.append(signer)
            job = len(paid)
            return self._json(200, {'status': 'running', 'status_url': f'/job/{job}'},
                              {'PAYMENT-RESPONSE': base64.b64encode(b'{"success":true,"transaction":"0xabc"}').decode()})

        def do_GET(self):
            port = self.server.server_address[1]
            if self.path.startswith('/job/'):
                return self._json(200, {'status': 'success', 'video': f'http://127.0.0.1:{port}/clip.mp4'})
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.end_headers()
            self.wfile.write(b'\x00\x00\x00\x18ftypmp42fake')

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, paid


def peers():
    """One fake server standing in for artist + artlist + sound2text."""
    seen = {'uploads': [], 'projects': []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            u = urlparse(self.path)
            if u.path == '/artist/api/upload':
                name = parse_qs(u.query).get('name', [''])[0]
                seen['uploads'].append(name)
                return self._json(200, {'id': hashlib.sha256(body).hexdigest()[:16], 'name': name})
            if u.path == '/artist/api/save_project':
                p = json.loads(body)
                seen['projects'].append(p)
                return self._json(200, {'id': p.get('id') or 'proj1', 'name': p['name'], 'timeline': p['timeline']})
            if u.path == '/music':
                port = self.server.server_address[1]
                return self._json(200, {'total': 1, 'results': [
                    {'id': '9', 'name': 'Neon Run', 'artist': 'A', 'duration': '2:01',
                     'preview_url': f'http://127.0.0.1:{port}/track.aac'}]})
            return self._json(404, {'error': u.path})

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/track.aac':
                self.send_response(200)
                self.send_header('Content-Type', 'audio/aac')
                self.end_headers()
                return self.wfile.write(b'AACFAKE')
            if u.path == '/transcribe':
                return self._json(200, {'transcript': 'a fox crosses the city', 'engine': 'stub',
                                        'segments': [{'start': 0.0, 'end': 2.5, 'text': 'a fox crosses the city'}]})
            return self._json(200, {'ok': True})  # every health path

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def run() -> dict:
    import mod as vidz_mod_module  # noqa: F401  (the `mod` package, kept first on sys.path)
    import importlib.util
    spec = importlib.util.spec_from_file_location('vidz_mod', ROOT / 'mod.py')
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)

    checks = {}
    ls = planner.lengths(60, 10)
    checks['plan_60s_is_6x10'] = ls == [10] * 6
    checks['plan_uneven'] = planner.lengths(25, 10) == [9, 8, 8]
    checks['plan_shots_fit'] = [s['seconds'] for s in planner.plan('', 30, 10, shots=['a', 'b', 'c'])] == [10, 10, 10]

    srv, paid = seller()
    port = srv.server_address[1]
    from eth_account import Account
    acct = Account.create()
    import os
    os.environ['VIDZ_PRIVATE_KEY'] = acct.key.hex()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for ver, path in (('v1', '/gen'), ('v2', '/v2/gen')):
                m = vm.Mod()
                m.home, m.projects_dir = Path(tmp), Path(tmp) / 'projects'
                m.defaults = {**m.defaults, 'poll_seconds': 0.05, 'provider': 'fake'}
                m.config['providers'] = {'fake': {'url': f'http://127.0.0.1:{port}{path}', 'max_clip_seconds': 10,
                                                  'default_model': 'm1', 'body': {'prompt': '{prompt}', 'seconds': '{seconds}', 'model': '{model}'}}}
                n0 = len(paid)
                dry = m.make('a fox in a neon city', seconds=20, max_usd=1)
                checks[f'{ver}_quote_is_0.50'] = (dry.get('quote') or {}).get('usd') == 0.5 and len(paid) == n0
                over = m.make('a fox', seconds=60, max_usd=1, confirm=True)
                checks[f'{ver}_over_budget_refused'] = 'refused' in over and len(paid) == n0
                done = m.render(dry['id'], confirm=True)
                checks[f'{ver}_paid_2_clips'] = len(paid) - n0 == 2 and done['spent_usd'] == 0.5
                checks[f'{ver}_signer_is_wallet'] = all(a == acct.address for a in paid[n0:])
                proj = m.project(dry['id'])
                checks[f'{ver}_clips_on_disk'] = all((Path(done['dir']) / f'shot_{i:02d}.mp4').exists() for i in range(2))
                checks[f'{ver}_receipt_kept'] = proj['shots'][0].get('receipt', {}).get('transaction') == '0xabc'

        # ── the connected editing suite, against fake peers ─────────
        psrv, seen = peers()
        pbase = f'http://127.0.0.1:{psrv.server_address[1]}'
        try:
            with tempfile.TemporaryDirectory() as tmp:
                m = vm.Mod()
                m.home, m.projects_dir = Path(tmp), Path(tmp) / 'projects'
                m.defaults = {**m.defaults, 'poll_seconds': 0.05, 'provider': 'fake'}
                m.config['providers'] = {'fake': {'url': f'http://127.0.0.1:{port}/gen', 'max_clip_seconds': 10,
                                                  'default_model': 'm1', 'body': {'prompt': '{prompt}', 'seconds': '{seconds}', 'model': '{model}'}}}
                m.config['activator'] = ''  # fakes only; never knock on the real fleet
                m.config['tools'] = {t: {'url': pbase, 'about': t} for t in ('artist', 'artlist', 'sound2text')}
                pid = m.make('a fox in a neon city', seconds=20, max_usd=1)['id']
                m.render(pid, confirm=True)
                d = m.projects_dir / pid

                tl = m.tools()
                checks['tools_all_up'] = all(v['up'] for v in tl['tools'].values()) and len(tl['tools']) == 3
                snd = m.soundtrack(pid, query='synthwave')
                checks['soundtrack_downloaded'] = (d / 'soundtrack.aac').read_bytes() == b'AACFAKE'
                checks['soundtrack_recorded'] = m.project(pid).get('soundtrack', {}).get('name') == 'Neon Run'
                ed = m.edit(pid)
                checks['edit_pushed_clips'] = ed.get('clips') == 2 and len([u for u in seen['uploads'] if 'shot' in u]) == 2
                tline = seen['projects'][-1]['timeline']
                checks['edit_two_lanes'] = len(tline['video']) == 2 and len(tline['audio']) == 1
                cap = m.captions(pid)
                checks['captions_srt'] = '00:00:00,000 --> 00:00:02,500' in (d / 'captions.srt').read_text()
                checks['captions_text'] = cap.get('text') == 'a fox crosses the city'
        finally:
            psrv.shutdown()
    finally:
        srv.shutdown()
        os.environ.pop('VIDZ_PRIVATE_KEY', None)
    return {'ok': all(checks.values()), 'checks': checks}


def test_vidz():
    r = run()
    assert r['ok'], r['checks']


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
