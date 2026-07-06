import os
import json
import subprocess
import requests

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
CONFIG = json.load(open(os.path.join(ROOT, 'config.json')))

API_URL = CONFIG['urls']['api']
APP_URL = CONFIG['urls']['app']
API_PORT = CONFIG['port']
APP_PORT = CONFIG['app_port']


class Uniswap:
    name = 'uniswap'
    description = 'Uniswap V3 multi-chain trader scraper'

    def serve(self, api_only=False, app_only=False, **kw):
        """Start Rust API and/or Next.js app."""
        results = {}
        if not app_only:
            results['api'] = self._serve_api()
        if not api_only:
            results['app'] = self._serve_app()
        return results

    def _serve_api(self):
        log = '/tmp/uniswap/api.log'
        os.makedirs('/tmp/uniswap', exist_ok=True)
        with open(log, 'w') as f:
            subprocess.Popen(
                ['cargo', 'run', '--release'],
                cwd=os.path.join(DIR, 'api'),
                stdout=f, stderr=f,
                env={**os.environ, 'PORT': str(API_PORT)}
            )
        return {'port': API_PORT, 'log': log}

    def _serve_app(self):
        log = '/tmp/uniswap/app.log'
        os.makedirs('/tmp/uniswap', exist_ok=True)
        with open(log, 'w') as f:
            subprocess.Popen(
                ['npx', 'next', 'dev', '-p', str(APP_PORT)],
                cwd=os.path.join(DIR, 'app'),
                stdout=f, stderr=f
            )
        return {'port': APP_PORT, 'log': log}

    def kill(self, **kw):
        """Stop all uniswap processes."""
        os.system("pkill -f 'uniswap-trader-api' 2>/dev/null")
        os.system(f"pkill -f 'next.*{APP_PORT}' 2>/dev/null")
        return {'status': 'killed'}

    def health(self, **kw):
        """Check API health."""
        try:
            r = requests.get(f'{API_URL}/health', timeout=5)
            return r.json()
        except Exception as e:
            return {'status': 'down', 'error': str(e)}

    def traders(self, chain='base', days=7, limit=20, min_swaps=5, sort='score', **kw):
        """Get top traders leaderboard."""
        r = requests.get(f'{API_URL}/traders', params={
            'chain': chain, 'days': days, 'limit': limit,
            'min_swaps': min_swaps, 'sort': sort
        }, timeout=30)
        return r.json()

    def trader(self, address, chain='base', days=30, **kw):
        """Get single trader profile."""
        r = requests.get(f'{API_URL}/traders/{address}', params={
            'chain': chain, 'days': days
        }, timeout=30)
        return r.json()

    def scrape(self, chain='base', days=7, **kw):
        """Trigger a fresh scrape."""
        r = requests.get(f'{API_URL}/traders/stream', params={
            'chain': chain, 'days': days, 'pool': 2000
        }, timeout=300, stream=True)
        lines = []
        for line in r.iter_lines():
            if line:
                lines.append(json.loads(line))
        return lines[-1] if lines else {'error': 'no data'}

    def chains(self, **kw):
        """Get supported chains."""
        r = requests.get(f'{API_URL}/chains', timeout=10)
        return r.json()
