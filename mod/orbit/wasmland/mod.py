"""
wasmland — a marketplace for computations anyone can check.

Upload something that computes. Run it in a browser tab or on this box. Get a
receipt. Have somebody else run it again and see whether they agree. That last
step is the product; everything else exists to make it possible.

    m wasmland                                  # what this box carries
    m wasmland/engines                          # compute types, live and planned
    m wasmland/venues                           # what each venue enforces
    m wasmland/inspect path=~/thing.wasm        # read the bytes, store nothing
    m wasmland/publish path=~/thing.wasm title=thing price=2
    m wasmland/listings                         # the market
    m wasmland/run listing=thing-a1b2 input=hi  # run it here, get a receipt
    m wasmland/verify <run id>                  # replay it and attest
    m wasmland/to_arena listing=snake-c3d4      # a game becomes its own mod
    m wasmland/serve                            # API :50480, console :50481

WHAT MAKES A RESULT WORTH ANYTHING
    A run is a claim until an independent party replays it and agrees. Both
    venues import the same runtime (src/runtime/), on a seeded clock and a
    seeded PRNG with no network and no filesystem, so the same three inputs —
    artifact, input, seed — produce the same bytes wherever they are run. Two
    agreeing runs make a receipt `verified`; two disagreeing ones make it
    `disputed`, which is information rather than an error.

COMPUTE TYPES ARE A PLUG-IN
    wasm and js run today. python, container, tee and gpu are declared in
    src/engines.py with the same fields — where they run, whether they are
    reproducible, and how each would actually be verified — and refuse to run
    with a message naming what they need. Nothing above that file is
    wasm-shaped, so adding a type is adding an entry.

EVERYTHING LIVES IN THE STORE MOD
    Artifacts, listings, runs, receipts and the credit ledger are all keys in
    `m.mod('store')`. This module has no database of its own. Bytes sit under
    the SHA-256 of themselves at the shared `blobs/` prefix, so the arena reads
    the same key for a game published here rather than being handed a copy.
"""
import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
PORT = 50480       # the API
APP_PORT = 50481   # the console

sys.path.insert(0, str(DIR))
from src import engines, games, identity, market, receipts, sandbox, storage  # noqa: E402


class Mod:
    description = __doc__
    path = str(DIR)

    # ── info ─────────────────────────────────────────────────────

    def forward(self, fn: str = 'info', *args, **kwargs):
        return getattr(self, fn)(*args, **kwargs)

    def info(self):
        """What this box carries, and what it will actually enforce."""
        return {
            'name': 'wasmland',
            'compute_types': {e['id']: e['status'] for e in engines.descriptors()},
            'venues': {'browser': True, 'server': sandbox.capabilities()['ok']},
            'verification': 'independent replay; two agreeing runs verify a receipt',
            'storage': storage.stats(),
            'arena': games.available(),
            'api': f'http://localhost:{PORT}',
            'app': f'http://localhost:{APP_PORT}/wasmland',
            'counts': {
                'listings': len(market.listings(limit=1000)),
                'runs': len(receipts.runs(limit=1000)),
            },
        }

    def health(self):
        return {'ok': True, 'server_venue': sandbox.capabilities(),
                'store': storage.stats()}

    def readme(self):
        for name in ('README.md', 'readme.md'):
            path = DIR / name
            if path.exists():
                return path.read_text()
        return None

    def engines(self):
        """Every compute type — live ones and the declared-but-unbuilt ones."""
        return {'engines': engines.descriptors(),
                'live': [e.id for e in engines.live()]}

    def venues(self):
        """Where a run can happen, and what containment each venue really has."""
        return {'server': sandbox.capabilities(),
                'browser': {'venue': 'browser',
                            'isolation': 'a Worker the page can terminate',
                            'trust': 'results are claims until replayed'}}

    # ── artifacts ────────────────────────────────────────────────

    def inspect(self, path: str, engine: str = None):
        """Read a file without storing it: what it imports, exports, and is."""
        data = Path(os.path.expanduser(path)).read_bytes()
        kind = engine or engines.guess(path, data)
        return {'engine': kind, **engines.inspect(kind, data)}

    def upload(self, path: str, engine: str = None):
        """Store bytes under their own hash. Same bytes, same id, every time."""
        file = Path(os.path.expanduser(path))
        data = file.read_bytes()
        kind = engine or engines.guess(str(file), data)
        return storage.put_artifact(data, kind, engines.inspect(kind, data), file.name)

    def artifacts(self, limit: int = 50):
        return {'artifacts': storage.records('artifacts', limit=limit)}

    def artifact(self, id: str):
        return storage.artifact_record(id)

    # ── market ───────────────────────────────────────────────────

    def publish(self, path: str = '', artifact: str = '', title: str = '',
                description: str = '', entry: str = '', price: float = 0,
                tags: str = '', key: str = None, seller: str = ''):
        """Upload if needed, then list it.

        m wasmland/publish path=~/thing.wasm title=thing price=2
        """
        who = seller or self._me(key)
        if not artifact:
            if not path:
                return {'error': 'give a path= to the artifact, or an artifact= id'}
            artifact = self.upload(path)['id']
        return market.publish(artifact, who, title=title, description=description,
                              entry=entry, price=float(price),
                              tags=[t.strip() for t in tags.split(',') if t.strip()])

    def listings(self, engine: str = None, role: str = None, q: str = None,
                 seller: str = None, limit: int = 50):
        return {'listings': market.listings(engine=engine, role=role, q=q,
                                            seller=seller, limit=limit)}

    def listing(self, id: str):
        return market.listing(id)

    def unpublish(self, id: str, key: str = None):
        return market.unpublish(id, self._me(key))

    def buy(self, id: str, key: str = None):
        return market.charge(self._me(key), id)

    # ── running and verifying ────────────────────────────────────

    def run(self, listing: str = '', artifact: str = '', input: str = '',
            seed: int = 0, entry: str = '', engine: str = '', key: str = None):
        """Run on this box, in the sandbox, and record it with a receipt."""
        who = self._me(key)
        if listing:
            got = market.listing(listing)
            market.charge(who, listing)
            artifact, engine, entry = got['artifact'], got['engine'], entry or got['entry']
        elif artifact:
            record = storage.artifact_record(artifact)
            if not record:
                return {'error': f'no artifact {artifact[:12]}'}
            engine = engine or record['engine']
        else:
            return {'error': 'name a listing= or an artifact='}
        out = receipts.run_here(artifact, engine, entry=entry or 'run', input=input,
                                seed=int(seed), runner=who, listing=listing or None)
        if listing:
            market.touch(listing)
        return out

    def verify(self, id: str, key: str = None):
        """Replay a recorded run here and attest to the result.

        This is the mechanism. The job is read out of the stored run, so
        whoever claimed it cannot smuggle a different computation into its own
        verification.
        """
        out = receipts.verify(id, verifier=self._me(key))
        if out.get('listing') and out['verdict']['status'] == 'verified':
            market.touch(out['listing'], verified=True)
        return out

    def runs(self, limit: int = 20, listing: str = None, status: str = None):
        return {'runs': [{k: v for k, v in r.items() if k not in ('logs', 'stdout')}
                         for r in receipts.runs(limit=limit, listing=listing, status=status)]}

    # ── accounts ─────────────────────────────────────────────────

    def account(self, address: str = '', key: str = None):
        return market.account(address or self._me(key))

    def grant(self, address: str, amount: float, reason: str = 'grant'):
        """Put credits into an account. This is the mint; there is no other."""
        return market.grant(address, float(amount), reason)

    # ── arena ────────────────────────────────────────────────────

    def to_arena(self, listing: str = '', artifact: str = '', name: str = '',
                 key: str = None):
        """Publish a game to the arena, where it becomes its own mod."""
        who = self._me(key)
        if listing:
            return games.publish_listing(listing, who)
        if artifact:
            return games.send(artifact, name=name, author=who)
        return {'error': 'name a listing= or an artifact='}

    def games(self, limit: int = 50):
        return {'arena': games.available(), 'games': games.games(limit=limit)}

    # ── identity ─────────────────────────────────────────────────

    def auth(self, key=None):
        """The shared mod-protocol auth — the same token every mod here speaks."""
        return storage.protocol().mod('auth')(key=key)

    def token(self, key='test.wasmland', **data):
        """Sign a token with a local key. Use it as the Authorization header."""
        return self.auth(key=key).token(data or {'action': 'signin'})

    def signin(self, token: str):
        """Verify a token and return the identity it names."""
        return {'address': identity.from_token(token), 'ts': time.time()}

    def _me(self, key=None) -> str:
        """The address acting. A local key by default — the CLI is somebody."""
        try:
            return self.auth(key=key or 'test.wasmland').key.address
        except Exception:
            return 'local'

    # ── serving ──────────────────────────────────────────────────

    def app(self, gateway: str = None):
        base = f'{gateway.rstrip("/")}/wasmland' if gateway else f'http://localhost:{APP_PORT}/wasmland'
        api = f'{gateway.rstrip("/")}/api/wasmland' if gateway else f'http://localhost:{PORT}'
        return {'app': base, 'same_origin_api': base + '/_api', 'api': api,
                'path': str(DIR / 'src' / 'app')}

    def _pm2(self, name: str, cmd: list) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True)
        r = subprocess.run(['pm2', 'start', cmd[0], '--name', name,
                            '--cwd', str(DIR), '--'] + cmd[1:],
                           capture_output=True, text=True)
        return r.returncode == 0

    def serve(self, port: int = PORT, app_port: int = APP_PORT, host: str = '0.0.0.0'):
        """Run both halves: the API on `port`, the console on `app_port`.

        Two processes, the fleet's convention, so the router can send
        /api/wasmland to one and /wasmland to the other — and so a restart of
        the API doesn't take the page down with it. The console reaches the API
        through its own origin at /wasmland/_api, which src/app/server.py
        forwards; nothing in the page knows where the API actually lives.
        """
        api_cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', host,
                   '--port', str(port), '--app-dir', str(DIR / 'src' / 'api')]
        app_cmd = ['python3', str(DIR / 'src' / 'app' / 'server.py'),
                   '--port', str(app_port), '--host', host,
                   '--api', f'http://127.0.0.1:{port}']
        if shutil.which('pm2'):
            ok = self._pm2('wasmland-api', api_cmd) and self._pm2('wasmland-app', app_cmd)
        else:
            subprocess.Popen(api_cmd, cwd=str(DIR))
            subprocess.Popen(app_cmd, cwd=str(DIR))
            ok = True
        return {'ok': ok, 'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/wasmland',
                'services': ['wasmland-api', 'wasmland-app']}

    def restart(self):
        if shutil.which('pm2'):
            subprocess.run(['pm2', 'restart', 'wasmland-api', 'wasmland-app'],
                           capture_output=True)
            return {'restarted': ['wasmland-api', 'wasmland-app']}
        return self.serve()

    def kill(self):
        if shutil.which('pm2'):
            subprocess.run(['pm2', 'delete', 'wasmland-api', 'wasmland-app'],
                           capture_output=True)
        else:
            subprocess.run(['pkill', '-f', f'uvicorn api:app --host 0.0.0.0 --port {PORT}'])
            subprocess.run(['pkill', '-f', 'wasmland/src/app/server.py'])
        return {'killed': ['wasmland-api', 'wasmland-app']}

    def test(self, **kwargs):
        """Run the module's own test suite."""
        r = subprocess.run([sys.executable, '-m', 'pytest', str(DIR / 'tests'), '-q'],
                           cwd=str(DIR), capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout or r.stderr)[-4000:]}
