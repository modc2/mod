"""
Vali — Validator module for distributed networks.

Scores modules, runs validation epochs, maintains scoreboards,
and votes on module performance.

Usage:
    Python:
        import mod as m
        v = m.mod('vali')()
        v.status()
        v.serve()

    CLI:
        m vali/status
        m vali/serve
        m vali/epoch
        m vali/results
        m vali/kill
"""

import json
import os
import signal
import subprocess
from pathlib import Path

DIR = Path(__file__).parent.parent
API_PORT = 50100
APP_PORT = 50101


class Mod:
    """Validator for distributed networks — scoring, epochs, voting."""

    description = "Validator module — scores modules, runs epochs, maintains scoreboards."
    endpoints = [
        'forward', 'status', 'epoch', 'results', 'score_module',
        'vote', 'set_network', 'set_score', 'modules',
        'serve', 'kill', 'refresh_results',
    ]

    def __init__(self, network='local', key=None, tempo=60,
                 search=None, batch_size=12, timeout=32,
                 loop=False, verbose=True, **kwargs):
        self.module_dir = DIR
        self.api_port = API_PORT
        self.app_port = APP_PORT
        self.config = self._load_config()
        self._vali = None
        self._init_kwargs = dict(
            network=network, key=key, tempo=tempo,
            search=search, batch_size=batch_size,
            timeout=timeout, loop=loop, verbose=verbose,
            **kwargs
        )

    def _load_config(self):
        cfg_path = self.module_dir / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg):
        cfg_path = self.module_dir / 'config.json'
        with open(cfg_path, 'w') as f:
            json.dump(cfg, f, indent=2)
        self.config = cfg

    @property
    def vali(self):
        if self._vali is None:
            from src.vali.vali import Vali
            self._vali = Vali(**self._init_kwargs)
        return self._vali

    # ── Core methods ──────────────────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point — returns status."""
        return self.status()

    def status(self):
        """Get validator status."""
        info = {
            'name': 'vali',
            'network': getattr(self.vali, 'network', 'unknown'),
            'subnet': getattr(self.vali, 'subnet', 0),
            'epochs': getattr(self.vali, 'epochs', 0),
            'tempo': getattr(self.vali, 'tempo', 60),
            'batch_size': getattr(self.vali, 'batch_size', 12),
            'timeout': getattr(self.vali, 'timeout', 32),
            'modules': len(getattr(self.vali, 'mods', [])),
            'urls': self.config.get('urls', {}),
        }
        return info

    def epoch(self, **kwargs):
        """Run a validation epoch."""
        result = self.vali.epoch(**kwargs)
        if hasattr(result, 'to_dict'):
            return result.to_dict(orient='records')
        return result

    def results(self, by='score', ascending=True, max_age=10000, **kwargs):
        """Get scored results (scoreboard)."""
        result = self.vali.results(by=by, ascending=ascending, max_age=max_age, to_dict=True, **kwargs)
        if hasattr(result, 'to_dict'):
            return result.to_dict(orient='records')
        return result

    def score_module(self, module, **kwargs):
        """Score a single module."""
        return self.vali.forward(module, **kwargs)

    def vote(self, results):
        """Submit votes for modules."""
        return self.vali.vote(results)

    def set_network(self, network=None, tempo=None, search=None, **kwargs):
        """Configure the network connection."""
        return self.vali.set_network(network=network, tempo=tempo, search=search, **kwargs)

    def set_score(self, score):
        """Set the scoring function."""
        return self.vali.set_score(score)

    def modules(self):
        """List discovered modules on the network."""
        return getattr(self.vali, 'mods', [])

    def refresh_results(self):
        """Clear the scoreboard."""
        return self.vali.refresh_results()

    # ── Serve ─────────────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True):
        """Launch the API (FastAPI) and App (Next.js)."""
        api_port = int(api_port or self.api_port)
        app_port = int(app_port or self.app_port)
        log_dir = Path('/tmp/vali')
        log_dir.mkdir(parents=True, exist_ok=True)
        results = {}

        self.kill()

        api_url = f'http://localhost:{api_port}'
        app_url = f'http://localhost:{app_port}'

        # ── API (FastAPI/uvicorn) ──
        api_dir = self.module_dir / 'src' / 'api'
        repo_root = str(self.module_dir.parent.parent.parent)  # ~/mod
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{repo_root}:{self.module_dir}:{self.module_dir / 'src'}:{env.get('PYTHONPATH', '')}"
        env['PORT'] = str(api_port)

        api_log = open(log_dir / 'api.log', 'w')
        api_cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(api_port),
            '--app-dir', str(api_dir),
        ]
        if dev:
            api_cmd.append('--reload')

        subprocess.Popen(api_cmd, env=env, stdout=api_log, stderr=subprocess.STDOUT)
        results['api'] = api_url
        results['api_docs'] = f'{api_url}/docs'
        results['api_log'] = str(log_dir / 'api.log')

        # ── App (Next.js) ──
        app_dir = self.module_dir / 'src' / 'app'
        if (app_dir / 'package.json').exists():
            app_env = os.environ.copy()
            app_env['NEXT_PUBLIC_API_URL'] = api_url
            app_env['PORT'] = str(app_port)
            app_log = open(log_dir / 'app.log', 'w')
            app_cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            subprocess.Popen(
                app_cmd, cwd=str(app_dir), env=app_env,
                stdout=app_log, stderr=subprocess.STDOUT,
            )
            results['app'] = app_url
            results['app_log'] = str(log_dir / 'app.log')

        # Save urls to config
        cfg = self._load_config()
        cfg['urls'] = {'api': api_url, 'app': app_url}
        self._save_config(cfg)

        results['dev'] = dev
        results['logs'] = str(log_dir)
        return results

    def kill(self):
        """Kill the API and App processes."""
        killed = []
        for port in [self.api_port, self.app_port]:
            try:
                result = subprocess.run(
                    ['lsof', '-ti', f':{port}'],
                    capture_output=True, text=True,
                )
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip():
                        try:
                            os.kill(int(pid.strip()), signal.SIGTERM)
                            killed.append(int(pid.strip()))
                        except (ProcessLookupError, ValueError):
                            pass
            except Exception:
                pass
        return {'killed': killed}
