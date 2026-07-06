"""
Bitevo — Bittensor subnet for YC-style startup idea generation & judging.

Miners generate startup pitches using LLMs (OpenRouter / Venice AI / Chutes).
Validators act as YC judges: score, critique, and rank pitches.

Backends:
    openrouter  — Claude, GPT, Llama via OpenRouter
    venice      — Venice AI (Llama 3.3 70B)
    chutes      — Chutes.ai serverless GPU inference (Bittensor-native, decentralized)

Usage (Python):
    import mod as m
    b = m.mod('bitevo')()
    b.simulate(n_miners=3)
    b.simulate(n_miners=3, backends=['chutes'])
    b.leaderboard()

Usage (CLI):
    m bitevo/simulate n_miners=3
    m bitevo/simulate n_miners=3 backends='["chutes","openrouter"]'
    m bitevo/leaderboard
    m bitevo/epoch
    m bitevo/serve
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent  # orbit/bitevo/
API_PORT = 50120
APP_PORT = 50121

# Ensure bitevo root is on path for core/neurons imports
_bitevo_root = str(DIR)
if _bitevo_root not in sys.path:
    sys.path.insert(0, _bitevo_root)


class Mod:
    description = "Bittensor subnet — miners generate YC-style startup ideas, validators judge them."

    fns = [
        'forward', 'simulate', 'epoch', 'leaderboard', 'results',
        'status', 'add_miner', 'challenge', 'score_idea',
        'backends', 'test', 'serve', 'kill',
    ]

    def __init__(self, backend='openrouter', model=None, local=True,
                 tempo=120, netuid=None, storage_path='~/.bitevo', **kwargs):
        self.module_dir = DIR
        self.api_port = API_PORT
        self.app_port = APP_PORT
        self.backend = backend
        self.model = model
        self.local = local
        self.tempo = tempo
        self.netuid = netuid
        self.storage_path = storage_path
        self.config = self._load_config()
        self._validator = None
        self._init_kwargs = dict(
            backend=backend, model=model, local=local,
            tempo=tempo, netuid=netuid, storage_path=storage_path,
        )

    def _load_config(self):
        cfg = self.module_dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                return json.load(f)
        return {}

    @property
    def validator(self):
        if self._validator is None:
            from neurons.validator import BitevoValidator
            self._validator = BitevoValidator(**self._init_kwargs)
        return self._validator

    # ── Core ──────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status()

    def status(self):
        return {
            'name': 'bitevo',
            'backend': self.backend,
            'model': self.model,
            'local': self.local,
            'epochs': self.validator.epochs if self._validator else 0,
            'tempo': self.tempo,
            'miners': len(self.validator._local_miners) if self._validator else 0,
            'leaderboard': self.validator.leaderboard() if self._validator else [],
            'urls': self.config.get('urls', {}),
        }

    def simulate(self, n_miners: int = 3, backends=None, epochs: int = 1, tempo: int = 0):
        from neurons.miner import BitevoMiner

        if backends is None:
            backends = ['openrouter', 'venice', 'chutes']

        for i in range(int(n_miners)):
            backend = backends[i % len(backends)]
            miner = BitevoMiner(backend=backend, uid=i, local=True)
            self.validator.add_miner(miner)
            print(f"  added miner uid={i} backend={backend} model={miner.model}")

        old_tempo = self.validator.tempo
        self.validator.tempo = int(tempo)

        results = []
        for _ in range(int(epochs)):
            result = self.validator.epoch()
            results.append(result)

        self.validator.tempo = old_tempo
        return {
            'epochs_run': len(results),
            'miners': int(n_miners),
            'leaderboard': self.validator.leaderboard(),
        }

    def epoch(self, challenge_type=None, **kwargs):
        from core.schemas import Challenge
        challenge = None
        if challenge_type:
            challenge = self.validator.challenge_gen.generate(
                epoch=self.validator.epochs + 1,
                challenge_type=challenge_type,
            )
        result = self.validator.epoch(challenge=challenge, **kwargs)
        return result.model_dump() if hasattr(result, 'model_dump') else result

    def leaderboard(self):
        return self.validator.leaderboard()

    def results(self, epoch: int = None):
        return self.validator.results(epoch=int(epoch) if epoch is not None else None)

    def add_miner(self, backend='openrouter', uid=None, model=None):
        from neurons.miner import BitevoMiner
        if uid is None:
            uid = len(self.validator._local_miners)
        else:
            uid = int(uid)
        miner = BitevoMiner(backend=backend, uid=uid, model=model, local=True)
        self.validator.add_miner(miner)
        return {'uid': uid, 'backend': backend, 'model': miner.model}

    def challenge(self, challenge_type=None):
        c = self.validator.challenge_gen.generate(
            epoch=self.validator.epochs + 1,
            challenge_type=challenge_type,
        )
        return c.model_dump()

    def score_idea(self, idea: str, challenge: str = None):
        from core.schemas import Challenge, StartupPitch, MinerResponse
        if challenge is None:
            challenge = "Pitch a startup that could be in the next YC batch."
        ch = Challenge(id="manual", prompt=challenge)
        pitch = StartupPitch(
            company_name="Manual Submission",
            one_liner=idea[:100] if idea else "",
            problem=idea, solution=idea, market="", traction="",
            business_model="", team="", defensibility="", ask="",
            raw_text=idea,
        )
        resp = MinerResponse(miner_uid=-1, challenge_id="manual", pitch=pitch)
        score = self.validator._score_response(ch, resp)
        return score.model_dump()

    # ── Backends ──────────────────────────────────────────────────

    SUPPORTED_BACKENDS = {
        'openrouter': {'model': 'anthropic/claude-sonnet-4', 'type': 'api'},
        'venice': {'model': 'llama-3.3-70b', 'type': 'api'},
        'chutes': {'model': 'unsloth/Llama-3.3-70B-Instruct', 'type': 'decentralized'},
    }

    def backends(self):
        return self.SUPPORTED_BACKENDS

    # ── Test ─────────────────────────────────────────────────────

    def test(self, backends=None, full=False):
        from tests.test_bitevo import run_tests
        return run_tests(backends=backends, full=bool(full))

    # ── Serve / Kill ──────────────────────────────────────────────

    def serve(self, api_port=None, dev=True):
        api_port = int(api_port or self.api_port)
        log_dir = Path('/tmp/bitevo')
        log_dir.mkdir(parents=True, exist_ok=True)
        self.kill()

        api_dir = self.module_dir / 'api'
        mod_root = str(self.module_dir.parent.parent.parent)
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{mod_root}:{self.module_dir}:{env.get('PYTHONPATH', '')}"
        env['PORT'] = str(api_port)

        api_log = open(log_dir / 'api.log', 'w')
        cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(api_port),
            '--app-dir', str(api_dir),
        ]
        if dev:
            cmd.append('--reload')
        subprocess.Popen(cmd, env=env, stdout=api_log, stderr=subprocess.STDOUT)

        return {
            'api': f'http://localhost:{api_port}',
            'docs': f'http://localhost:{api_port}/docs',
            'log': str(log_dir / 'api.log'),
        }

    def kill(self):
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
