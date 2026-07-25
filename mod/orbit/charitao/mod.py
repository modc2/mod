"""
charitao — proof-of-donation Bittensor subnet.

Miners "mine" by donating TAO from their registered coldkey to whitelisted
charity addresses. Validators verify the transfers on chain and set weights
proportional to credited donations. A coverage-ratio budget guarantees the
emission a miner earns always exceeds what they donated, at a fixed and
predictable margin: payout = donation / coverage_ratio (default 0.8 → 25%
profit), with under-filled epochs burning the residual weight so the rate
never inflates, and over-filled epochs carrying donations forward so they
are never diluted.

CLI:
    m charitao                                   # status
    m charitao/charities                         # the whitelist
    m charitao/add_charity id=x name=X address=demo://x verified=1
    m charitao/simulate n_miners=3 epochs=2      # local end-to-end sim
    m charitao/donate uid=0 amount=0.2           # sim miner donates
    m charitao/epoch                             # run one settlement epoch
    m charitao/leaderboard                       # donated/credited/payout per uid
    m charitao/projection donation=1             # profit math for a donation
    m charitao/reset                             # wipe local sim state
    m charitao/test                              # pytest suite
    m charitao/serve                             # web app on :50270
    m charitao/deploy                            # pm2 worker (self-healing)
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent

_root = str(DIR)
if _root not in sys.path:
    sys.path.insert(0, _root)


class Mod:
    description = ('Proof-of-donation Bittensor subnet: miners donate TAO to '
                   'whitelisted charities, validators verify on-chain and set '
                   'weights so emissions always cover donations at a fixed '
                   'predictable profit margin')

    fns = [
        'forward', 'info', 'status',
        'charities', 'add_charity', 'remove_charity', 'verify_charity', 'flag_charity',
        'simulate', 'donate', 'epoch', 'leaderboard', 'results',
        'projection', 'suggest_donation', 'reset', 'test',
        'serve', 'kill', 'deploy', 'stop_worker',
    ]

    APP_PORT = 50270
    PM2_NAME = 'charitao-app'

    def __init__(self, key='charitao', local=True, netuid=None, network='finney',
                 coverage_ratio=0.8, epoch_emission=1.0, burn_uid=0,
                 tempo=300, storage_path='~/.mod/charitao', **kwargs):
        self.key = key
        self.local = local
        self.storage_path = str(Path(storage_path).expanduser())
        self._init_kwargs = dict(
            local=local, netuid=netuid, network=network,
            coverage_ratio=coverage_ratio, epoch_emission=epoch_emission,
            burn_uid=burn_uid, tempo=tempo, storage_path=storage_path,
        )
        self._validator = None
        self._miners = {}

    # ── lazies ───────────────────────────────────────────────────

    @property
    def validator(self):
        if self._validator is None:
            from charitao_subnet.validator import CharitaoValidator
            self._validator = CharitaoValidator(**self._init_kwargs)
        return self._validator

    @property
    def registry(self):
        return self.validator.registry

    def _miner(self, uid: int):
        from charitao_subnet.miner import CharitaoMiner
        uid = int(uid)
        if uid not in self._miners:
            m = CharitaoMiner(uid=uid, local=self.local,
                              chain=self.validator.chain,
                              registry=self.registry)
            self.validator.add_miner(m)
            self._miners[uid] = m
        return self._miners[uid]

    # ── info ─────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status()

    def info(self):
        return self.status()

    def status(self):
        v = self.validator
        return {
            'name': self.key,
            'description': self.description,
            'local': self.local,
            'epoch': v.incentive.epoch,
            'coverage_ratio': v.incentive.coverage_ratio,
            'margin_pct': round((1 / v.incentive.coverage_ratio - 1) * 100, 4),
            'epoch_emission': v.epoch_emission,
            'burn_uid': v.incentive.burn_uid,
            'charities': len(self.registry.charities(verified_only=True)),
            'miners_bound': len(v.bindings),
            'carryover': round(sum(q['amount'] for q in v.incentive.queue), 8),
            'storage_path': self.storage_path,
        }

    # ── charity whitelist ────────────────────────────────────────

    def charities(self, verified_only=False):
        return self.registry.charities(verified_only=bool(verified_only))

    def add_charity(self, id: str, name: str, address: str, cause: str = '',
                    url: str = '', verified=False, network: str = 'demo',
                    links=None, audits=None):
        def _list(x):
            if isinstance(x, str):
                x = json.loads(x) if x.strip() else []
            return x or []
        return self.registry.add(id, name, address, cause=cause, url=url,
                                 verified=bool(int(verified) if isinstance(verified, str) else verified),
                                 network=network,
                                 links=_list(links), audits=_list(audits))

    def remove_charity(self, id: str):
        return self.registry.remove(id)

    def verify_charity(self, id: str, verified=True):
        return self.registry.verify(
            id, bool(int(verified) if isinstance(verified, str) else verified))

    def flag_charity(self, id: str, legit=None):
        """Owner is-it-legit verdict: legit=1 vouch, legit=0 flag, omit to reset."""
        if isinstance(legit, str):
            legit = None if legit.strip() in ('', 'none', 'null') else bool(int(legit))
        return self.registry.flag(id, legit)

    # ── mining (local sim) ───────────────────────────────────────

    def donate(self, uid: int = 0, amount: float = 0.1, charity: str = None):
        """A sim miner donates on the mock chain (credited next epoch)."""
        return self._miner(uid).donate(float(amount), charity_id=charity)

    def simulate(self, n_miners: int = 3, epochs: int = 1,
                 amount: float = None, emission: float = None):
        """End-to-end local sim: n miners donate each epoch, validator settles.

        Default per-miner amount fills the budget exactly, so every epoch
        settles fully and the payout math is visible in one run.
        """
        v = self.validator
        if emission is not None:
            v.epoch_emission = float(emission)
        budget = v.epoch_emission * v.incentive.coverage_ratio
        n_miners = int(n_miners)
        per_miner = float(amount) if amount is not None else round(budget / n_miners, 6)

        results = []
        for _ in range(int(epochs)):
            for uid in range(n_miners):
                self._miner(uid).donate(per_miner)
            results.append(v.epoch())

        return {
            'epochs_run': len(results),
            'miners': n_miners,
            'per_miner_donation': per_miner,
            'last_epoch': results[-1] if results else None,
            'leaderboard': v.leaderboard(),
        }

    # ── validation ───────────────────────────────────────────────

    def epoch(self):
        return self.validator.epoch()

    def leaderboard(self):
        return self.validator.leaderboard()

    def results(self, epoch=None):
        return self.validator.results(epoch=int(epoch) if epoch is not None else None)

    # ── economics ────────────────────────────────────────────────

    def projection(self, donation: float = 1.0, emission: float = None):
        """Guaranteed payout/profit for a donation of this size."""
        v = self.validator
        return v.incentive.projection(
            float(donation), float(emission) if emission is not None else v.epoch_emission)

    def suggest_donation(self, expected_donors: int = 1, emission: float = None):
        """Largest donation that settles in one epoch at your budget share."""
        from charitao_subnet.miner import CharitaoMiner
        v = self.validator
        m = CharitaoMiner(local=True, chain=v.chain, registry=self.registry)
        return m.suggest_donation(
            float(emission) if emission is not None else v.epoch_emission,
            coverage_ratio=v.incentive.coverage_ratio,
            expected_donors=int(expected_donors))

    # ── housekeeping ─────────────────────────────────────────────

    def reset(self):
        """Wipe local sim state (mock chain, incentive, scan state, epochs)."""
        path = Path(self.storage_path)
        removed = []
        if path.exists():
            removed = [p.name for p in path.iterdir()]
            shutil.rmtree(path)
        self._validator = None
        self._miners = {}
        return {'removed': removed, 'path': str(path)}

    def test(self):
        r = subprocess.run(
            [sys.executable, '-m', 'pytest', str(DIR / 'tests'), '-q'],
            capture_output=True, text=True, cwd=str(DIR))
        out = (r.stdout + r.stderr).strip().splitlines()
        return {'passed': r.returncode == 0, 'tail': out[-5:]}

    # ── web app ──────────────────────────────────────────────────

    def serve(self, port=None, host='0.0.0.0', background=True):
        """Run the charitao web app — single-port, zero-dependency: dashboard UI
        at / and a JSON API at /api/*. background=True spawns a detached process
        and returns the URL; False blocks (serve_forever)."""
        port = int(port or self.APP_PORT)
        if background:
            self.kill(port)
            log_dir = '/tmp/charitao'
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, 'app.log'), 'w')
            proc = subprocess.Popen(
                [sys.executable, str(DIR / 'run_app.py')],
                stdout=logf, stderr=subprocess.STDOUT,
                env=dict(os.environ, CHARITAO_PORT=str(port)),
                start_new_session=True)
            with open(os.path.join(log_dir, 'app.pid'), 'w') as f:
                f.write(str(proc.pid))
            self._wait_health(port)
            url = f'http://localhost:{port}'
            return {'running': True, 'pid': proc.pid, 'url': url,
                    'api': f'{url}/api/status', 'log': os.path.join(log_dir, 'app.log')}
        from http.server import ThreadingHTTPServer
        from app import make_handler
        httpd = ThreadingHTTPServer((host, port), make_handler(self))
        print(f'charitao app on http://{host}:{port}')
        httpd.serve_forever()

    def kill(self, port=None):
        """Stop a running app (by pid file, then by port)."""
        port = int(port or self.APP_PORT)
        killed = []
        pid_path = '/tmp/charitao/app.pid'
        if os.path.exists(pid_path):
            try:
                os.kill(int(open(pid_path).read().strip()), 15)
                killed.append('pidfile')
            except Exception:
                pass
            try:
                os.remove(pid_path)
            except OSError:
                pass
        try:
            out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} 2>/dev/null'],
                                 capture_output=True, text=True).stdout.split()
            for pid in out:
                os.kill(int(pid), 15)
                killed.append(int(pid))
        except Exception:
            pass
        return {'killed': killed}

    def deploy(self, port=None):
        """Run the app as a managed pm2 background worker (auto-restart,
        survives logout). Idempotent. Route comes from config.json
        `route: true` + `m caddy/apply`."""
        port = int(port or self.APP_PORT)
        self.kill(port)
        subprocess.run(['pm2', 'delete', self.PM2_NAME], capture_output=True, text=True)
        r = subprocess.run(
            ['pm2', 'start', str(DIR / 'run_app.py'), '--name', self.PM2_NAME,
             '--interpreter', 'python3', '--time'],
            capture_output=True, text=True,
            env=dict(os.environ, CHARITAO_PORT=str(port)))
        if r.returncode != 0:
            raise RuntimeError(f'pm2 start failed: {r.stderr or r.stdout}')
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        healthy = self._wait_health(port)
        return {'worker': self.PM2_NAME, 'port': port, 'healthy': healthy,
                'url': f'http://localhost:{port}'}

    def stop_worker(self):
        subprocess.run(['pm2', 'delete', self.PM2_NAME], capture_output=True, text=True)
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        return {'stopped': self.PM2_NAME}

    def _wait_health(self, port, tries=40):
        import time
        import urllib.request
        for _ in range(tries):
            try:
                urllib.request.urlopen(f'http://localhost:{port}/api/status', timeout=1)
                return True
            except Exception:
                time.sleep(0.25)
        return False
