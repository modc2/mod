"""sound2text — speech to text, where the model is the last thing tried.

    m sound2text/info                          what this is
    m sound2text/engines                       every recogniser, and which run here
    m sound2text/route                         which one it would pick, and why
    m sound2text/transcribe file=voice.wav     the transcript, and the arithmetic
    m sound2text/vad file=voice.wav            where the speech is, without a model
    m sound2text/compare file=voice.wav        whole file vs trimmed vs packed
    m sound2text/bench                         every engine on the same audio
    m sound2text/samples                       audio to try it on
    m sound2text/serve                         API :50640 + console :50641

Every function is the same code the API calls. The API is a transport.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class Mod:
    description = ('Speech to text as five steps rather than one call: decode '
                   'the audio without a media stack, find the speech, recall '
                   'what was transcribed before, route to whichever recogniser '
                   'is fastest or cheapest here, and run only what is left — '
                   'packed to fill the model window, because trimming without '
                   'packing is slower than not trimming at all.')
    path = str(HERE)
    port = 50640
    app_port = 50641

    # ── what it is ───────────────────────────────────────────────────

    def forward(self, **kwargs: Any) -> Dict[str, Any]:
        """The null call: the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        from src import audio, engines, keys, router
        ready = [c['name'] for c in engines.catalog() if c['available']]
        return {
            'name': 'sound2text',
            'description': self.description,
            'engines': engines.names(),
            'available_here': ready,
            'would_pick': self._quiet_pick(),
            'policies': list(router.POLICIES),
            'audio': audio.capabilities(),
            'keys': keys.listing(),
            'state': str(keys.HOME),
            'urls': {'api': f'http://localhost:{self.port}',
                     'app': f'http://localhost:{self.app_port}/sound2text'},
            'fns': [f for f in dir(self)
                    if not f.startswith('_') and callable(getattr(self, f))],
        }

    def _quiet_pick(self) -> Optional[str]:
        from src import router
        try:
            return router.choose()['engine']
        except Exception:
            return None

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        from src import cache, engines, ledger
        return {'ok': True,
                'engines_ready': [c['name'] for c in engines.catalog() if c['available']],
                'cache': cache.stats(), 'measured': ledger.table()['measured']}

    # ── the recognisers ──────────────────────────────────────────────

    def engines(self) -> List[Dict[str, Any]]:
        """Every engine this module can drive, and whether it can run here."""
        from src import engines as registry
        return registry.catalog()

    def route(self, policy: str = 'fast', prefer: str = None) -> Dict[str, Any]:
        """Which engine would be used, and the reasoning — without transcribing."""
        from src import router
        decision = router.choose(prefer=prefer, policy=policy)
        return {'engine': decision['engine'], 'why': decision['why'],
                'policy': policy, 'ranked': decision['ranked']}

    def pull(self, model: str = 'base.en', engine: str = 'whisper-torch') -> Dict[str, Any]:
        """Download a local model now, rather than during the first transcript."""
        from src import engines as registry
        picked = registry.build(engine, model=model)
        ok, note = picked.check()
        if not ok:
            return {'ok': False, 'engine': engine, 'note': note}
        picked.load()
        return {'ok': True, 'engine': engine, 'model': picked.model,
                'device': getattr(picked, '_on', None), 'note': 'loaded'}

    # ── the work ─────────────────────────────────────────────────────

    def transcribe(self, file: str = None, url: str = None, engine: str = None,
                   policy: str = 'fast', model: str = None, language: str = None,
                   task: str = 'transcribe', vad: bool = True, cache: bool = True,
                   pack: bool = True, text_only: bool = False,
                   **options: Any) -> Any:
        """Audio to text, with a full account of what was sent to the model."""
        from src import pipeline, samples
        source = file or url or samples.default()
        run = pipeline.transcribe(source, engine=engine, policy=policy, model=model,
                                  language=language, task=task, use_vad=_flag(vad),
                                  use_cache=_flag(cache), pack=_flag(pack), **options)
        return run['text'] if _flag(text_only) else run

    def vad(self, file: str = None, url: str = None, **options: Any) -> Dict[str, Any]:
        """Where the speech is. No model, no network — about 80 ms for a minute."""
        from src import audio, samples, vad as detector
        pcm, meta = audio.load(file or url or samples.default())
        found = detector.segments(pcm, audio.SAMPLE_RATE, **options)
        found['audio'] = meta
        found['packed_windows'] = len(detector.pack(found['segments']))
        return found

    def compare(self, file: str = None, engine: str = None, model: str = None,
                **options: Any) -> Dict[str, Any]:
        """The same audio whole, trimmed, and trimmed-then-packed. The proof."""
        from src import pipeline, samples
        return pipeline.compare(file or samples.default(), engine=engine,
                                model=model, **options)

    def bench(self, file: str = None, engines: str = None, model: str = None,
              **options: Any) -> Dict[str, Any]:
        """Every engine that runs here, on the same audio, timed. Fills the ledger."""
        from src import pipeline, samples
        names = [e.strip() for e in engines.split(',')] if engines else None
        return pipeline.bench(file or samples.default(), engine_names=names,
                              model=model, **options)

    def speed(self) -> Dict[str, Any]:
        """What each engine has actually done on this machine."""
        from src import ledger
        return ledger.table()

    # ── housekeeping ─────────────────────────────────────────────────

    def samples(self) -> List[Dict[str, Any]]:
        """Audio to try it on, real and constructed, labelled as such."""
        from src import samples as store
        return store.catalog()

    def cache(self, clear: bool = False) -> Dict[str, Any]:
        """What has been transcribed before, keyed on the audio itself."""
        from src import cache as store
        return store.clear() if _flag(clear) else store.stats()

    def set_key(self, vendor: str, key: str) -> Dict[str, Any]:
        """Store a vendor key at 0600 under ~/.mod/sound2text — never in the repo."""
        from src import keys
        return keys.put(vendor, key)

    def drop_key(self, vendor: str) -> Dict[str, Any]:
        from src import keys
        return keys.drop(vendor)

    def keys(self) -> Dict[str, str]:
        """Which vendors have a key, and from where. The keys themselves stay put."""
        from src import keys as store
        return store.listing()

    # ── running it ───────────────────────────────────────────────────

    def serve(self, port: int = None, app_port: int = None,
              background: bool = True) -> Dict[str, Any]:
        port = int(port or os.environ.get('SOUND2TEXT_PORT', self.port))
        app_port = int(app_port or os.environ.get('SOUND2TEXT_APP_PORT', self.app_port))
        api = subprocess.Popen([sys.executable, str(HERE / 'src/api.py'),
                                '--port', str(port)], cwd=str(HERE))
        app = subprocess.Popen([sys.executable, str(HERE / 'src/app.py'),
                                '--port', str(app_port),
                                '--api', f'http://127.0.0.1:{port}'], cwd=str(HERE))
        if not background:
            api.wait()
            app.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/sound2text',
                'pids': [api.pid, app.pid]}

    def kill(self) -> Dict[str, Any]:
        killed = []
        for pattern in ('sound2text/src/api.py', 'sound2text/src/app.py'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def test(self) -> Dict[str, Any]:
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                              cwd=str(HERE), capture_output=True, text=True)
        return {'ok': done.returncode == 0,
                'output': (done.stdout or done.stderr)[-4000:]}


def _flag(value: Any) -> bool:
    """CLI arguments arrive as strings; `vad=false` has to mean false."""
    if isinstance(value, str):
        return value.strip().lower() not in ('0', 'false', 'no', 'off', '')
    return bool(value)
