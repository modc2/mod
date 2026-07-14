"""
BlocTime App — the Next.js console for BlocTime time-weighted staking.

Represents the frontend shown at http://localhost:8852: dark mono terminal-style
UI with a STAKE / REWARDS tab bar, wallet connect, stake form with live
multiplier preview, position table, delegation, and reward claim/distribute.

Usage:
  m.fn('bloctime/app')()            # info: identity, urls, running state
  m.fn('bloctime/app/ui')()         # declarative representation of the UI
  m.fn('bloctime/app/serve')()      # start next dev on 8852
  m.fn('bloctime/app/status')()     # running? health? url?
  m.fn('bloctime/app/build')()      # production build
  m.fn('bloctime/app/kill')()       # stop
"""

import json
import os
import signal
import socket
import subprocess
from pathlib import Path

import mod as m

DIR = Path(__file__).parent
APP_PORT = 8852
API_PORT = 8851
BASE_PATH = os.environ.get('NEXT_PUBLIC_BASE_PATH', '/bloctime')  # next.config.js basePath
LOG_DIR = Path('/tmp/bloctime')


class Mod:
    description = (
        "BlocTime app — Next.js staking console: wallet connect, STAKE tab "
        "(multiplier curve, stake form, positions) and REWARDS tab (epoch stats, "
        "Bitcoin-style inflation curve, delegation, claim/distribute)."
    )
    path = str(DIR)

    def __init__(self, config=None, **kwargs):
        self.app_port = APP_PORT
        self.api_port = API_PORT
        self.config = config or self._load_config()

    def _load_config(self):
        cfg_path = DIR.parent / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    def forward(self, **kwargs):
        return self.info()

    # ── Representation ────────────────────────────────────────────

    def info(self):
        pkg = {}
        pkg_path = DIR / 'package.json'
        if pkg_path.exists():
            with open(pkg_path) as f:
                pkg = json.load(f)
        return {
            'name': 'bloctime.app',
            'description': self.description,
            'path': self.path,
            'url': f'http://localhost:{self.app_port}{BASE_PATH}',
            'api_url': f'http://localhost:{self.api_port}',
            'port': self.app_port,
            'base_path': BASE_PATH,
            'running': self.running(),
            'framework': {
                'next': pkg.get('dependencies', {}).get('next'),
                'react': pkg.get('dependencies', {}).get('react'),
                'ethers': pkg.get('dependencies', {}).get('ethers'),
                'tailwindcss': pkg.get('devDependencies', {}).get('tailwindcss'),
            },
            'pages': ['/'],
            'tabs': ['stake', 'rewards'],
            'source': str(DIR / 'src' / 'app' / 'page.tsx'),
        }

    def ui(self):
        """Declarative map of the app's UI, mirroring src/app/page.tsx."""
        return {
            'theme': {
                'background': '#0a0a0f',
                'font': 'monospace',
                'style': 'dark terminal panels — white/10 borders, uppercase tracking-wider labels',
                'accents': {'cyan': 'bloctime/actions', 'amber': 'staked/inflation',
                            'emerald': 'rewards/unlocked', 'violet': 'network/voting'},
            },
            'header': {
                'title': 'BLOCTIME',
                'subtitle': 'TIME-WEIGHTED STAKING',
                'icon': 'clock',
                'actions': ['connect (MetaMask via ethers BrowserProvider)', 'refresh'],
            },
            'tabs': {
                'stake': {
                    'multiplier_curve': 'bar chart of lock-blocks → multiplier (GET /points)',
                    'stake_form': {
                        'inputs': ['amount (NTV)', 'lock blocks (default 10000)'],
                        'preview': 'interpolated multiplier (e.g. 1.00x) + projected BT',
                        'action': 'POST /stake',
                    },
                    'positions': {
                        'columns': ['id', 'staked', 'bloctime', 'lock', 'left', 'unstake'],
                        'sortable': ['amount', 'bloctime', 'remaining'],
                        'action': 'POST /unstake when blocksRemaining == 0',
                    },
                },
                'rewards': {
                    'epoch_stats': ['current epoch', 'epoch reward', 'your pending', 'total distributed'],
                    'inflation_curve': 'SVG chart w/ halving markers + current-epoch line (GET /get_inflation_curve)',
                    'delegation': {'inputs': ['delegate address'], 'actions': ['POST /delegate', 'POST /undelegate']},
                    'claim': {'actions': ['POST /claim_rewards', 'POST /distribute_rewards']},
                },
            },
            'stats_bars': {
                'global': ['total stakes', 'total bloctime', 'bt supply', 'network'],
                'account (when connected)': ['my bloc', 'staked', 'pending rewards', 'voting power'],
            },
            'polling': 'stats/points/overview refetched every 15s',
            'footer': 'BLOCTIME MODULE',
        }

    def endpoints(self):
        """API endpoints the app calls (FastAPI on the api port)."""
        return {
            'GET': ['/health', '/stats', '/points', '/params',
                    '/get_inflation_params', '/get_inflation_curve'],
            'POST': ['/overview', '/stake', '/unstake', '/get_position',
                     '/get_multiplier', '/get_voting_power', '/get_rewards',
                     '/delegate', '/undelegate', '/claim_rewards',
                     '/distribute_rewards', '/set_inflation_params'],
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def serve(self, port=None, dev=True):
        port = int(port or self.app_port)
        self.kill(port)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['NEXT_PUBLIC_API_URL'] = f'http://localhost:{self.api_port}'
        env['PORT'] = str(port)
        log = open(LOG_DIR / 'app.log', 'w')
        cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(port)]
        subprocess.Popen(cmd, cwd=str(DIR), env=env, stdout=log, stderr=subprocess.STDOUT)
        return {
            'app': f'http://localhost:{port}{BASE_PATH}',
            'dev': dev,
            'log': str(LOG_DIR / 'app.log'),
        }

    def build(self):
        result = subprocess.run(
            ['npx', 'next', 'build'], cwd=str(DIR),
            capture_output=True, text=True, timeout=600,
        )
        return {'success': result.returncode == 0,
                'output': (result.stdout + result.stderr)[-2000:]}

    def kill(self, port=None):
        port = int(port or self.app_port)
        killed = []
        result = subprocess.run(['pgrep', '-f', f'next.*{port}'],
                                capture_output=True, text=True)
        for pid in result.stdout.strip().split('\n'):
            if pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    killed.append(pid)
                except ProcessLookupError:
                    pass
        return {'killed': killed}

    def running(self, port=None):
        port = int(port or self.app_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(('127.0.0.1', port)) == 0

    def status(self):
        up = self.running()
        out = {
            'running': up,
            'url': f'http://localhost:{self.app_port}{BASE_PATH}',
            'api_url': f'http://localhost:{self.api_port}',
            'api_running': self.running(self.api_port),
            'log': str(LOG_DIR / 'app.log'),
        }
        if up:
            try:
                import urllib.request
                req = urllib.request.urlopen(out['url'], timeout=5)
                out['http'] = req.status
            except Exception as e:
                out['http'] = f'error: {e}'
        return out

    def logs(self, n=50):
        log = LOG_DIR / 'app.log'
        if not log.exists():
            return {'error': f'no log at {log}'}
        lines = log.read_text().splitlines()
        return {'log': str(log), 'lines': lines[-int(n):]}
