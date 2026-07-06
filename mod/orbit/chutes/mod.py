import os
import json
import subprocess
import requests
from typing import List, Dict, Optional, Union, Iterator

_engine = None

def _get_engine(api_key=None, base_url=None):
    """Lazy-load the chutes_rs Rust engine."""
    global _engine
    if _engine is None:
        try:
            import chutes_rs
            config = json.dumps({
                'api_key': api_key or '',
                'base_url': base_url or 'https://api.chutes.ai',
            })
            _engine = chutes_rs.ChutesEngine(config)
        except ImportError:
            return None
    return _engine


class Mod:
    description = """
    Chutes.ai - Serverless GPU inference platform.
    Chat completions, image generation, chute management.
    Uses Rust bindings (PyO3) for high-performance HTTP + SSE streaming.
    Falls back to pure Python when Rust engine unavailable.
    """

    def __init__(self, api_key: str = None, default_model: str = None,
                 base_url: str = None, **kwargs):
        self.api_key = api_key or os.environ.get('CHUTES_API_KEY', '')
        self.default_model = default_model or os.environ.get(
            'CHUTES_DEFAULT_MODEL', 'unsloth/Llama-3.3-70B-Instruct')
        self.base_url = base_url or os.environ.get(
            'CHUTES_BASE_URL', 'https://api.chutes.ai')
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = _get_engine(self.api_key, self.base_url)
        return self._engine

    @property
    def headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    # ── Primary Entry Point ──────────────────────────────────────

    def forward(self, message: str = None, model: str = None,
                stream: bool = False, system_prompt: str = None,
                **kwargs) -> Union[str, Iterator[str]]:
        """Default entry - simple chat completion."""
        if not message:
            return self.list_chutes()
        messages = [{'role': 'user', 'content': message}]
        system = system_prompt or kwargs.pop('system', None)
        if system:
            messages.insert(0, {'role': 'system', 'content': system})
        result = self.chat(messages, model=model, stream=stream, **kwargs)
        if stream:
            return result
        try:
            return result['choices'][0]['message']['content']
        except (KeyError, IndexError):
            return result

    # ── Chat Completions ─────────────────────────────────────────

    def chat(self, messages: List[Dict], model: str = None,
             stream: bool = False, temperature: float = 0.7,
             max_tokens: int = 4096, **kwargs):
        """OpenAI-compatible chat completions.

        Args:
            messages: List of {'role': str, 'content': str}
            model: Model name (defaults to self.default_model)
            stream: Enable SSE streaming
            temperature: Sampling temperature
            max_tokens: Max tokens to generate

        Returns:
            Response dict, or generator of delta dicts if stream=True
        """
        model = model or self.default_model

        if stream:
            return self._chat_stream(messages, model, temperature, max_tokens, **kwargs)

        # Try Rust engine first
        if self.engine:
            try:
                result = self.engine.chat(
                    json.dumps(messages), model, temperature, max_tokens)
                return json.loads(result)
            except Exception:
                pass

        # Python fallback
        return self._chat_python(messages, model, temperature, max_tokens, **kwargs)

    def _chat_python(self, messages, model, temperature, max_tokens, **kwargs):
        """Pure Python chat completion."""
        url = f'{self.base_url}/v1/chat/completions'
        body = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'stream': False,
            **kwargs,
        }
        resp = requests.post(url, json=body, headers=self.headers, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def _chat_stream(self, messages, model, temperature, max_tokens, **kwargs):
        """Streaming chat - uses Rust callback or Python SSE parsing."""
        if self.engine:
            try:
                return self._stream_rust(messages, model, temperature, max_tokens)
            except Exception:
                pass
        return self._stream_python(messages, model, temperature, max_tokens, **kwargs)

    def _stream_rust(self, messages, model, temperature, max_tokens):
        """Rust-accelerated SSE streaming via callback."""
        chunks = []

        def on_chunk(json_str):
            chunks.append(json.loads(json_str))

        self.engine.chat_stream(
            json.dumps(messages), model, on_chunk, temperature, max_tokens)

        for chunk in chunks:
            yield chunk

    def _stream_python(self, messages, model, temperature, max_tokens, **kwargs):
        """Pure Python SSE streaming."""
        url = f'{self.base_url}/v1/chat/completions'
        body = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'stream': True,
            **kwargs,
        }
        resp = requests.post(url, json=body, headers=self.headers,
                             stream=True, timeout=120)
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith('data: '):
                data = line[6:]
                if data == '[DONE]':
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    # ── Image Generation ─────────────────────────────────────────

    def generate_image(self, prompt: str, model: str = None,
                       size: str = '1024x1024', n: int = 1,
                       response_format: str = 'url', **kwargs) -> Dict:
        """Generate images from text prompts."""
        if self.engine:
            try:
                result = self.engine.generate_image(prompt, model or '', size, n)
                return json.loads(result)
            except Exception:
                pass

        url = f'{self.base_url}/v1/images/generations'
        body = {
            'prompt': prompt,
            'n': n,
            'size': size,
            'response_format': response_format,
            **kwargs,
        }
        if model:
            body['model'] = model
        resp = requests.post(url, json=body, headers=self.headers, timeout=120)
        resp.raise_for_status()
        return resp.json()

    # ── Chute Management ─────────────────────────────────────────

    def list_chutes(self, page: int = 1, limit: int = 50) -> List[Dict]:
        """List all deployed chutes."""
        if self.engine:
            try:
                return json.loads(self.engine.list_chutes())
            except Exception:
                pass

        resp = requests.get(
            f'{self.base_url}/chutes/',
            params={'page': page, 'limit': limit},
            headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_chute(self, chute_id: str) -> Dict:
        """Get chute details by ID or name."""
        if self.engine:
            try:
                return json.loads(self.engine.get_chute(chute_id))
            except Exception:
                pass

        resp = requests.get(
            f'{self.base_url}/chutes/{chute_id}',
            headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def deploy_chute(self, config: Dict) -> Dict:
        """Deploy a new chute."""
        if self.engine:
            try:
                return json.loads(self.engine.deploy_chute(json.dumps(config)))
            except Exception:
                pass

        resp = requests.post(
            f'{self.base_url}/chutes/',
            json=config, headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def delete_chute(self, chute_id: str) -> Dict:
        """Delete a chute by ID."""
        if self.engine:
            try:
                return json.loads(self.engine.delete_chute(chute_id))
            except Exception:
                pass

        resp = requests.delete(
            f'{self.base_url}/chutes/{chute_id}',
            headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.text else {'status': 'deleted'}

    def warmup(self, chute_id: str) -> Dict:
        """Pre-initialize a chute to reduce cold start latency."""
        if self.engine:
            try:
                return json.loads(self.engine.warmup(chute_id))
            except Exception:
                pass

        resp = requests.get(
            f'{self.base_url}/chutes/warmup/{chute_id}',
            headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def utilization(self) -> Dict:
        """Get current capacity and utilization metrics."""
        if self.engine:
            try:
                return json.loads(self.engine.utilization())
            except Exception:
                pass

        resp = requests.get(
            f'{self.base_url}/chutes/utilization',
            headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Model Discovery ──────────────────────────────────────────

    def models(self, search: str = None) -> List[Dict]:
        """List available models/chutes, optionally filtered."""
        chutes = self.list_chutes(limit=200)
        if search and isinstance(chutes, list):
            search_lower = search.lower()
            chutes = [c for c in chutes if search_lower in json.dumps(c).lower()]
        return chutes

    # ── Batch Operations ─────────────────────────────────────────

    def batch_chat(self, prompts: List[str], model: str = None,
                   **kwargs) -> List[str]:
        """Run multiple chat completions concurrently via Rust, or sequentially."""
        if self.engine:
            try:
                msgs_list = [json.dumps([{'role': 'user', 'content': p}]) for p in prompts]
                result = self.engine.batch_chat(
                    json.dumps(msgs_list),
                    model or self.default_model,
                    kwargs.get('temperature', 0.7),
                    kwargs.get('max_tokens', 4096),
                )
                return json.loads(result)
            except Exception:
                pass

        results = []
        for prompt in prompts:
            resp = self.forward(prompt, model=model, **kwargs)
            results.append(resp)
        return results

    # ── Serve / Kill ──────────────────────────────────────────────

    def _load_config(self):
        cfg_path = os.path.join(self.dir, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                return json.load(f)
        return {'port': 50120, 'app_port': 50121}

    def serve(self, port=None, app_port=None, dev=True, **kwargs):
        """Start FastAPI API and Next.js app via PM2."""
        self.kill()
        cfg = self._load_config()
        port = port or cfg.get('port', 50120)
        app_port = app_port or cfg.get('app_port', 50121)
        api_dir = os.path.join(self.dir, 'api')
        app_dir = os.path.join(self.dir, 'app')

        # Start API
        api_cmd = ['python3', '-m', 'uvicorn', 'api:app',
                    '--host', '0.0.0.0', '--port', str(port)]
        if dev:
            api_cmd.append('--reload')
        subprocess.run(['pm2', 'delete', 'chutes.api'], capture_output=True)
        subprocess.run(
            ['pm2', 'start', api_cmd[0], '--name', 'chutes.api', '--'] + api_cmd[1:],
            cwd=api_dir,
            env={**os.environ, 'PYTHONPATH': os.path.expanduser('~/mod'), 'PORT': str(port)},
        )

        # Start App
        app_cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
        subprocess.run(['pm2', 'delete', 'chutes.app'], capture_output=True)
        subprocess.run(
            ['pm2', 'start', app_cmd[0], '--name', 'chutes.app', '--'] + app_cmd[1:],
            cwd=app_dir,
            env={**os.environ, 'NEXT_PUBLIC_API_URL': f'http://localhost:{port}', 'PORT': str(app_port)},
        )

        return {
            'api': f'http://localhost:{port}',
            'app': f'http://localhost:{app_port}',
            'processes': ['chutes.api', 'chutes.app'],
        }

    def kill(self, **kwargs):
        """Stop chutes API and app PM2 processes."""
        killed = []
        for name in ['chutes.api', 'chutes.app']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── Configuration ────────────────────────────────────────────

    def set_api_key(self, api_key: str):
        """Update API key."""
        self.api_key = api_key
        self._engine = None
        global _engine
        _engine = None

    def set_model(self, model: str):
        """Set default model."""
        self.default_model = model

    # ── Build ────────────────────────────────────────────────────

    def build(self, **kwargs):
        """Build the Rust engine via maturin."""
        rs_dir = os.path.join(self.dir, 'chutes-rs')
        if not os.path.exists(rs_dir):
            return {'status': 'error', 'message': f'chutes-rs not found at {rs_dir}'}
        result = subprocess.run(
            ['maturin', 'develop', '--release'],
            cwd=rs_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {'status': 'build_failed', 'stderr': result.stderr}
        global _engine
        _engine = None
        self._engine = None
        return {'status': 'built', 'path': rs_dir}

    # ── Test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """Run basic connectivity test."""
        results = {}
        results['rust_engine'] = self.engine is not None

        try:
            util = self.utilization()
            results['api_connected'] = True
            results['utilization'] = util
        except Exception as e:
            results['api_connected'] = False
            results['error'] = str(e)

        if results.get('api_connected') and self.api_key:
            try:
                resp = self.forward('Say "ok" and nothing else.', max_tokens=10)
                results['chat'] = resp
            except Exception as e:
                results['chat_error'] = str(e)

        return results
