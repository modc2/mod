"""
model — unified gateway over the per-provider model orbit submodules.

This anchor exposes:
  - serve()/kill()/status()/logs() lifecycle for the FastAPI BYOK gateway in `app/server.py`
  - providers()/models() introspection across known back-ends
  - forward() — one-shot completion against any registered provider (BYOK)

Per-provider classes still live under `model/openrouter/`, `model/grok/`, etc.
This module just unifies them behind one CLI / API surface.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import mod as m


HERE = Path(__file__).parent
APP_DIR = HERE / 'app'
SERVER_FILE = APP_DIR / 'server.py'
LOG_DIR = Path('/tmp/model')
PID_FILE = LOG_DIR / 'api.pid'
LOG_FILE = LOG_DIR / 'api.log'

DEFAULT_PORT = 50110


# Mirrors app/server.py PROVIDERS — kept in sync so CLI users see the same
# list the web UI does without booting the server.
PROVIDERS = {
    'openrouter': {
        'label': 'OpenRouter',
        'url': 'https://openrouter.ai/api/v1',
        'default_model': 'anthropic/claude-opus-4',
        'env': 'OPENROUTER_API_KEY',
    },
    'chutes': {
        'label': 'Chutes',
        'url': 'https://llm.chutes.ai/v1',
        'default_model': 'deepseek-ai/DeepSeek-V3',
        'env': 'CHUTES_API_KEY',
    },
    'targon': {
        'label': 'Targon',
        'url': 'https://api.targon.com/v1',
        'default_model': 'deepseek-ai/DeepSeek-V3',
        'env': 'TARGON_API_KEY',
    },
    'venice': {
        'label': 'Venice',
        'url': 'https://api.venice.ai/api/v1',
        'default_model': 'llama-3.3-70b',
        'env': 'VENICE_API_KEY',
    },
}


class Model:
    """Anchor class for the `model` orbit module."""

    name = 'model'

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port

    # ── lifecycle ────────────────────────────────────────────────

    def serve(self, port: int = None, reload: bool = False, background: bool = True, backend: str = 'python'):
        """Start the BYOK gateway. backend='python' (FastAPI) or 'rust' (axum).

        Both expose the same endpoints; the Rust binary additionally honours
        MODEL_GATE_ADDRESS / MODEL_GATE_CHAIN_ID env vars for ModelGate.
        """
        port = int(port or self.port or DEFAULT_PORT)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if self.status().get('running'):
            return {'status': 'already running', 'url': f'http://localhost:{port}'}

        if backend == 'rust':
            bin_path = HERE / 'api' / 'target' / 'release' / 'model-api'
            if not bin_path.exists():
                # cargo build --release
                build = subprocess.run(
                    ['cargo', 'build', '--release'], cwd=str(HERE / 'api'), capture_output=True,
                )
                if build.returncode != 0:
                    return {'error': 'cargo build failed', 'stderr': build.stderr.decode(errors='replace')[-2000:]}
            env = {**os.environ, 'MODEL_API_PORT': str(port)}
            cmd = [str(bin_path)]
        else:
            env = {**os.environ, 'MODEL_GATEWAY_PORT': str(port)}
            cmd = [sys.executable, '-m', 'uvicorn', 'app.server:app',
                   '--host', '0.0.0.0', '--port', str(port)]
            if reload:
                cmd.append('--reload')

        if background:
            with open(LOG_FILE, 'ab') as logf:
                p = subprocess.Popen(cmd, cwd=str(HERE), env=env, stdout=logf, stderr=logf, start_new_session=True)
            PID_FILE.write_text(str(p.pid))
            time.sleep(0.5)
            return {'status': 'started', 'pid': p.pid, 'backend': backend,
                    'url': f'http://localhost:{port}', 'log': str(LOG_FILE)}
        subprocess.run(cmd, cwd=str(HERE), env=env, check=False)
        return {'status': 'exited'}

    def kill(self):
        """Stop the running gateway."""
        if not PID_FILE.exists():
            return {'status': 'not running'}
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except (ProcessLookupError, ValueError):
            pass
        finally:
            try: PID_FILE.unlink()
            except FileNotFoundError: pass
        return {'status': 'stopped'}

    def status(self):
        if not PID_FILE.exists():
            return {'running': False}
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return {'running': True, 'pid': pid, 'url': f'http://localhost:{self.port}'}
        except (ProcessLookupError, ValueError):
            try: PID_FILE.unlink()
            except FileNotFoundError: pass
            return {'running': False}

    def logs(self, tail: int = 80):
        if not LOG_FILE.exists():
            return ''
        with open(LOG_FILE, 'rb') as f:
            data = f.read().decode('utf-8', errors='replace').splitlines()
        return '\n'.join(data[-tail:])

    # ── introspection ────────────────────────────────────────────

    def providers(self):
        return [{'id': k, **v} for k, v in PROVIDERS.items()]

    def models(self, provider: str, api_key: str = None):
        """List models for a provider. BYOK: pass api_key or set the env var."""
        cfg = PROVIDERS.get(provider)
        if not cfg:
            return {'error': f'unknown provider {provider!r}; choose from {list(PROVIDERS)}'}
        key = api_key or os.environ.get(cfg['env'])
        if not key:
            return {'error': f'no key — pass api_key= or set {cfg["env"]}'}
        import requests
        r = requests.get(cfg['url'].rstrip('/') + '/models',
                         headers={'Authorization': f'Bearer {key}'}, timeout=15)
        if r.status_code >= 400:
            return {'error': f'{provider}: {r.status_code} {r.text[:200]}'}
        data = r.json().get('data') or r.json().get('models') or []
        ids = []
        for x in data:
            if isinstance(x, str): ids.append(x)
            elif isinstance(x, dict) and (x.get('id') or x.get('name')):
                ids.append(x.get('id') or x.get('name'))
        return ids

    # ── one-shot forward (BYOK) ──────────────────────────────────

    def forward(
        self,
        message: str,
        provider: str = 'openrouter',
        model: str = None,
        api_key: str = None,
        temperature: float = 1.0,
        max_tokens: int = 4096,
        system: str = None,
    ):
        """One-shot chat completion. BYOK: api_key or env var per provider."""
        cfg = PROVIDERS.get(provider)
        if not cfg:
            raise ValueError(f'unknown provider {provider!r}; choose from {list(PROVIDERS)}')
        key = api_key or os.environ.get(cfg['env'])
        if not key:
            raise ValueError(f'no key — pass api_key= or set {cfg["env"]}')

        import openai
        client = openai.OpenAI(base_url=cfg['url'], api_key=key, timeout=120, max_retries=2)
        msgs = []
        if system:
            msgs.append({'role': 'system', 'content': system})
        msgs.append({'role': 'user', 'content': message})
        res = client.chat.completions.create(
            model=model or cfg['default_model'],
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return res.choices[0].message.content
