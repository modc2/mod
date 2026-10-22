"""
Bitevo — Bittensor subnet for YC-style startup idea generation & judging.

Runs on the zuma protocol (orbit/ztensor): miners and validators are public
SETS and every submission / score / claim is an LSAG ring signature over the
STARK curve, so a validator scores a pitch without knowing which miner sent
it and a miner never learns which validator scored it (core/zuma.py).

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
import shutil
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent  # orbit/bitevo/
API_PORT = 51460
APP_PORT = 51461  # was 50120/50121 — :50121 is orbit/ztensor's console

# Ensure bitevo root is on path for core/neurons imports
_bitevo_root = str(DIR)
if _bitevo_root not in sys.path:
    sys.path.insert(0, _bitevo_root)


class Mod:
    description = "Bittensor subnet — miners generate YC-style startup ideas, validators judge them."

    fns = [
        'forward', 'simulate', 'epoch', 'leaderboard', 'results',
        'status', 'add_miner', 'challenge', 'score_idea',
        'evolve', 'forecast', 'ensemble_forecast', 'population', 'datasets',
        'zuma', 'audit',
        'backends', 'whitepaper', 'top_pitches', 'list_epochs', 'test', 'serve', 'kill',
    ]

    def __init__(self, backend='claude', model=None, local=True,
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
        self._engine = None
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
            'epochs': self.validator.epochs,
            'tempo': self.tempo,
            'miners': len(self.validator._local_miners),
            'leaderboard': self.validator.leaderboard(),
            'evolution': self._evo_summary(),
            'zuma': self.validator.zuma.status(),
            'urls': self.config.get('urls', {}),
        }

    def _evo_summary(self):
        engine = self._get_engine()
        if engine is None:
            return None
        s = engine.status()
        return {k: s[k] for k in ('dataset', 'variables', 'generation', 'population', 'best_fitness')}

    def simulate(self, n_miners: int = 3, backends=None, epochs: int = 1, tempo: int = 0,
                 progress_callback=None):
        from neurons.miner import BitevoMiner

        if backends is None:
            backends = ['claude']

        for i in range(int(n_miners)):
            backend = backends[i % len(backends)]
            miner = BitevoMiner(backend=backend, uid=i, local=True)
            self.validator.add_miner(miner)
            print(f"  added miner uid={i} backend={backend} model={miner.model}")

        old_tempo = self.validator.tempo
        self.validator.tempo = int(tempo)

        results = []
        for _ in range(int(epochs)):
            result = self.validator.epoch(progress_callback=progress_callback)
            results.append(result)

        self.validator.tempo = old_tempo
        return {
            'epochs_run': len(results),
            'miners': int(n_miners),
            'leaderboard': self.validator.leaderboard(),
        }

    def epoch(self, challenge_type=None, challenge=None, **kwargs):
        from core.schemas import Challenge
        ch = None
        if challenge:
            ch = Challenge(**{**challenge, 'epoch': self.validator.epochs + 1})
        elif challenge_type:
            ch = self.validator.challenge_gen.generate(
                epoch=self.validator.epochs + 1,
                challenge_type=challenge_type,
            )
        result = self.validator.epoch(challenge=ch, **kwargs)
        return result.model_dump() if hasattr(result, 'model_dump') else result

    def leaderboard(self):
        return self.validator.leaderboard()

    def results(self, epoch: int = None):
        return self.validator.results(epoch=int(epoch) if epoch is not None else None)

    def add_miner(self, backend='claude', uid=None, model=None, payout=None):
        from neurons.miner import BitevoMiner
        if uid is None:
            uid = len(self.validator._local_miners)
        else:
            uid = int(uid)
        miner = BitevoMiner(backend=backend, uid=uid, model=model, local=True,
                            payout=payout)
        self.validator.add_miner(miner)
        return {'uid': uid, 'backend': backend, 'model': miner.model,
                'payout': miner.payout,
                'zuma_key_id': self.validator.zuma.keygen(miner.zuma_key, 'miner')['key_id']}

    # ── zuma (anonymous subnet protocol) ──────────────────────────

    def zuma(self):
        """Subnet registry: ring sizes, min_ring, emission, credits per payout."""
        z = self.validator.zuma
        return {**z.status(),
                'members': {r: [m['key_id'] for m in z.members[r]] for r in z.members}}

    def audit(self, epoch: int = None):
        """Re-verify every ring signature (submit/score/claim) of an epoch
        from the public record — anyone can run this."""
        z = self.validator.zuma
        if epoch is None:
            done = [int(k) for k, e in z.epochs.items() if not e['open']]
            if not done:
                return {'error': 'no zuma epochs yet — run simulate or epoch'}
            epoch = max(done)
        return z.audit(int(epoch))

    def challenge(self, challenge_type=None):
        c = self.validator.challenge_gen.generate(
            epoch=self.validator.epochs + 1,
            challenge_type=challenge_type,
        )
        return c.model_dump()

    def score_idea(self, idea: str, challenge: str = None, previous_feedback: str = None,
                   progress_callback=None):
        from core.schemas import Challenge
        from neurons.miner import BitevoMiner
        if challenge is None:
            challenge = f"Pitch a YC-quality startup around this idea: {idea}"
        else:
            challenge = f"{challenge}\n\nBuild your pitch specifically around this idea: {idea}"
        if previous_feedback:
            challenge = f"{challenge}\n\nPrevious validator feedback to address: {previous_feedback}"
        ch = Challenge(id="manual", prompt=challenge)
        miner = BitevoMiner(
            backend=self.validator.backend,
            model=self.validator.model,
            uid=-1,
            local=True,
        )
        if progress_callback:
            progress_callback("generating pitch…")
        resp = miner.forward(ch)
        if progress_callback:
            progress_callback("scoring…")
        score = self.validator._score_response(ch, resp)
        result = score.model_dump()
        result['pitch'] = resp.pitch.model_dump()
        return result

    # ── Evolution (Track II: multivariate future-fitting) ─────────

    @property
    def _evo_path(self):
        return os.path.join(os.path.expanduser(self.storage_path), 'evolution_state.json')

    def _get_engine(self):
        if self._engine is None:
            from core.evolve import EvolutionEngine
            self._engine = EvolutionEngine.load(self._evo_path)
        return self._engine

    def evolve(self, generations: int = 25, population: int = 40, dataset: str = 'coupled',
               horizon: int = 10, max_lag: int = 6, seed=None, fresh: bool = False,
               csv: str = None, progress_callback=None):
        """Evolve a population of multivariate predictor genomes against the
        held-out future window. Resumes the saved population unless fresh=True
        or the dataset changed. csv=<path> evolves against your own local data."""
        from core.evolve import EvolutionEngine, make_dataset, load_csv
        engine = None if fresh else self._get_engine()
        target = csv or dataset
        if engine is None or engine.dataset != target:
            series = load_csv(csv) if csv else make_dataset(dataset, seed=int(seed) if seed is not None else 7)
            engine = EvolutionEngine(
                series, dataset=target, population=int(population),
                horizon=int(horizon), max_lag=int(max_lag),
                seed=int(seed) if seed is not None else None,
            )
            self._engine = engine
        result = engine.run(int(generations), progress_callback=progress_callback)
        engine.save(self._evo_path)
        return {**result, 'status': engine.status(), 'leaderboard': engine.leaderboard(top=10)}

    def forecast(self, horizon: int = None, tail: int = 60):
        """Best evolved genome predicts the next `horizon` steps for every variable."""
        engine = self._get_engine()
        if engine is None:
            return {'error': 'no evolved population yet — run evolve first'}
        return engine.forecast(horizon=int(horizon) if horizon else None, tail=int(tail))

    def ensemble_forecast(self, top_n: int = 10, horizon: int = None, tail: int = 60):
        """Weighted-ensemble forecast from the top-N genomes with lo/hi spread band."""
        engine = self._get_engine()
        if engine is None:
            return {'error': 'no evolved population yet — run evolve first'}
        return engine.ensemble_forecast(
            top_n=int(top_n),
            horizon=int(horizon) if horizon else None,
            tail=int(tail),
        )

    def population(self, top: int = 20):
        engine = self._get_engine()
        if engine is None:
            return {'error': 'no evolved population yet — run evolve first'}
        return {'status': engine.status(), 'leaderboard': engine.leaderboard(top=int(top))}

    def datasets(self):
        from core.evolve import DATASETS
        return {k: v['docs'] for k, v in DATASETS.items()}

    # ── Backends ──────────────────────────────────────────────────

    SUPPORTED_BACKENDS = {
        'claude': {'model': 'haiku', 'type': 'local-cli'},
        'openrouter': {'model': 'anthropic/claude-sonnet-4', 'type': 'api'},
        'venice': {'model': 'llama-3.3-70b', 'type': 'api'},
        'chutes': {'model': 'unsloth/Llama-3.3-70B-Instruct', 'type': 'decentralized'},
    }

    def backends(self):
        return self.SUPPORTED_BACKENDS

    def top_pitches(self, limit: int = 10):
        import glob as _glob
        epoch_dir = os.path.expanduser(os.path.join(self.storage_path, 'epochs'))
        files = sorted(_glob.glob(os.path.join(epoch_dir, 'epoch_*.json')))
        entries = []
        for fpath in files:
            try:
                with open(fpath) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            epoch_num = data.get('epoch', 0)
            challenge = data.get('challenge', {})
            challenge_type = challenge.get('type', '')
            responses = data.get('responses', [])
            scores = data.get('scores', [])
            score_by_tag = {}
            score_by_uid = {}
            for s in scores:
                key = (s.get('challenge_id', ''), s.get('tag', ''))
                score_by_tag[key] = s
                uid_key = (s.get('challenge_id', ''), s.get('miner_uid', -1))
                score_by_uid[uid_key] = s
            for resp in responses:
                cid = resp.get('challenge_id', '')
                tag = resp.get('tag', '')
                uid = resp.get('miner_uid', -1)
                s = score_by_tag.get((cid, tag)) or score_by_uid.get((cid, uid))
                if s is None:
                    continue
                pitch = resp.get('pitch', {})
                entries.append({
                    'epoch': epoch_num,
                    'challenge_type': challenge_type,
                    'company_name': pitch.get('company_name', ''),
                    'one_liner': pitch.get('one_liner', ''),
                    'composite_score': s.get('composite_score', 0.0),
                    'breakdown': s.get('breakdown', {}),
                    'feedback': s.get('feedback', ''),
                })
                for field in ['problem', 'solution', 'market', 'traction', 'business_model', 'team', 'defensibility', 'ask']:
                    entries[-1][field] = pitch.get(field, '')
        entries.sort(key=lambda x: x['composite_score'], reverse=True)
        entries = entries[:int(limit)]
        for i, e in enumerate(entries, 1):
            e['rank'] = i
        return entries

    def list_epochs(self):
        import glob as _glob
        epoch_dir = os.path.expanduser(os.path.join(self.storage_path, 'epochs'))
        files = sorted(_glob.glob(os.path.join(epoch_dir, 'epoch_*.json')))
        summaries = []
        for fpath in files:
            try:
                with open(fpath) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            challenge = data.get('challenge', {})
            scores = data.get('scores', [])
            top_score = None
            valid = [s.get('composite_score') for s in scores if s.get('composite_score') is not None]
            if valid:
                top_score = max(valid)
            ts = data.get('timestamp') or data.get('created_at')
            if ts is None:
                try:
                    ts = os.path.getmtime(fpath)
                except OSError:
                    pass
            mean_score = round(sum(valid) / len(valid), 2) if valid else None
            summaries.append({
                'epoch': data.get('epoch', 0),
                'challenge_type': challenge.get('type', ''),
                'challenge_prompt': (challenge.get('prompt') or '')[:100],
                'n_responses': len(data.get('responses', [])),
                'top_score': top_score,
                'mean_score': mean_score,
                'timestamp': ts,
            })
        summaries.sort(key=lambda x: x['epoch'], reverse=True)
        return summaries

    def whitepaper(self):
        wp = self.module_dir / 'WHITEPAPER.md'
        return wp.read_text() if wp.exists() else 'whitepaper missing'

    # ── Test ─────────────────────────────────────────────────────

    def test(self, backends=None, full=False):
        from tests.test_bitevo import run_tests
        return run_tests(backends=backends, full=bool(full))

    # ── Serve / Kill ──────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=False):
        api_port = int(api_port or self.api_port)
        app_port = int(app_port or self.app_port)
        log_dir = Path('/tmp/bitevo')
        log_dir.mkdir(parents=True, exist_ok=True)
        self.kill()

        api_dir = self.module_dir / 'api'
        mod_root = str(self.module_dir.parent.parent.parent)
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{mod_root}:{self.module_dir}:{env.get('PYTHONPATH', '')}"
        env['PORT'] = str(api_port)

        cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(api_port),
            '--app-dir', str(api_dir),
        ]
        app_cmd = ['python3', str(self.module_dir / 'app' / 'server.py'), str(app_port)]
        # pm2 keeps both up across external kills/host restarts and lets the
        # activator sleep/wake them like the rest of the fleet. dev (--reload)
        # or no pm2 -> detached Popen, logs in /tmp/bitevo.
        if not dev and shutil.which('pm2'):
            for name, c in [(self.PM2_NAMES[0], cmd), (self.PM2_NAMES[1], app_cmd)]:
                subprocess.run(
                    ['pm2', 'start', c[0], '--name', name, '--interpreter', 'none',
                     '--cwd', str(self.module_dir), '--', *c[1:]],
                    env=env, capture_output=True, text=True,
                )
            subprocess.run(['pm2', 'save'], capture_output=True)
            mode = 'pm2'
        else:
            if dev:
                cmd.append('--reload')
            subprocess.Popen(cmd, env=env, stdout=open(log_dir / 'api.log', 'w'),
                             stderr=subprocess.STDOUT, start_new_session=True)
            subprocess.Popen(app_cmd, env=env, stdout=open(log_dir / 'app.log', 'w'),
                             stderr=subprocess.STDOUT, start_new_session=True)
            mode = 'popen'

        return {
            'api': f'http://localhost:{api_port}',
            'app': f'http://localhost:{app_port}',
            'docs': f'http://localhost:{api_port}/docs',
            'logs': 'pm2 logs bitevo-api' if mode == 'pm2' else str(log_dir),
            'mode': mode,
        }

    PM2_NAMES = ('bitevo-api', 'bitevo-app')

    def kill(self):
        killed = []
        if shutil.which('pm2'):
            for name in self.PM2_NAMES:
                subprocess.run(['pm2', 'delete', name], capture_output=True)
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
