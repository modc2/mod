"""
model — unified gateway over the per-provider model orbit submodules.

This anchor exposes:
  - serve()/kill()/status()/logs() lifecycle for the FastAPI BYOK gateway in `app/server.py`
  - providers()/models() introspection across known back-ends
  - forward() — one-shot completion against any registered provider (BYOK)

Every provider (16 built in: openrouter, huggingface, together, anthropic, …)
is registered once in providers.json; models from all of them combine into
one catalog addressed as '<provider>:<model>'. The richer per-provider
clients (model.openrouter, model.grok, model.hf) stay as submodules and are
reachable through client().
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


def _load_catalog():
    """app/catalog.py by file path — `import app` would hit whichever module's
    app package is first on sys.path in a fleet process."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('model_gateway_catalog', APP_DIR / 'catalog.py')
    mod_ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod_)
    return mod_


catalog = _load_catalog()
PROVIDERS = catalog.PROVIDERS

# Rich per-provider clients that live as submodules here (key store, history,
# pricing, free models, local inference). The gateway itself only needs the
# registry above; these are for callers who want the full client.
CLIENTS = {
    'openrouter': 'model.openrouter',   # OpenRouter: free_models, pricing, history
    'grok': 'model.grok',               # Grok via chutes / targon / venice
    'hf': 'model.hf',                   # local transformers (torch) — no key
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
        return [{**catalog.public_info(cfg), 'env': cfg['env']} for cfg in PROVIDERS.values()]

    def env_keys(self):
        """CLI-only fallback: {provider: key} from each provider's env var."""
        return {pid: os.environ[cfg['env']] for pid, cfg in PROVIDERS.items() if os.environ.get(cfg['env'])}

    def models(self, provider: str = None, api_key: str = None, search: str = None, refresh: bool = False):
        """List model refs. provider=None combines every provider into one list
        of '<provider>:<model>' refs (keys from env vars, or api_key as a
        {provider: key} dict). BYOK: pass api_key or set the env vars."""
        if provider in (None, 'all'):
            keys = api_key if isinstance(api_key, dict) else self.env_keys()
            res = catalog.catalog(keys, search=search, refresh=refresh)
            return {k: res[k] for k in ('count', 'counts', 'errors', 'missing')} | {
                'models': [x['ref'] for x in res['models']]}
        cfg = PROVIDERS.get(provider)
        if not cfg:
            return {'error': f'unknown provider {provider!r}; choose from {list(PROVIDERS)}'}
        try:
            rows = catalog.list_models(provider, api_key or os.environ.get(cfg['env']), refresh=refresh)
        except catalog.ProviderError as e:
            return {'error': e.detail + ('' if api_key else f' (set {cfg["env"]})')}
        ids = [x['id'] for x in rows]
        if search:
            ids = [i for i in ids if any(t.strip().lower() in i.lower() for t in search.split(','))]
        return ids

    def client(self, name: str = 'openrouter', **kwargs):
        """Full client for a submodule: openrouter | grok | hf."""
        if name not in CLIENTS:
            return {'error': f'unknown client {name!r}; choose from {list(CLIENTS)}'}
        return m.mod(CLIENTS[name])(**kwargs)

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
        """One-shot chat completion against any provider. BYOK: api_key or env var.

        model may be a combined ref '<provider>:<model>' (e.g.
        'chutes:deepseek-ai/DeepSeek-V3'), which overrides provider=.
        """
        try:
            provider, model = catalog.parse_ref(model, provider)
        except catalog.ProviderError as e:
            raise ValueError(e.detail)
        cfg = PROVIDERS[provider]
        key = api_key or os.environ.get(cfg['env'])
        if not key:
            raise ValueError(f'no key — pass api_key= or set {cfg["env"]}')

        client = catalog.make_client(provider, key)
        msgs = []
        if system:
            msgs.append({'role': 'system', 'content': system})
        msgs.append({'role': 'user', 'content': message})
        res = client.chat.completions.create(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return res.choices[0].message.content
