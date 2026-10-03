"""whisper.cpp — a binary and a .bin file, and no python at all.

The engine to use on a box where installing torch is not on the table. It is
driven the way the shell would drive it: write the clip as a WAV, run the
binary, read the text back. Set WHISPER_CPP_BIN and WHISPER_CPP_MODEL, or put
`whisper-cli` on PATH with a model beside it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .. import audio
from .base import Engine, clean

CANDIDATES = ('whisper-cli', 'whisper.cpp', 'main')


def find_binary() -> Optional[str]:
    explicit = os.environ.get('WHISPER_CPP_BIN')
    if explicit and Path(explicit).exists():
        return explicit
    for name in CANDIDATES[:2]:                  # never pick up a random `main`
        found = shutil.which(name)
        if found:
            return found
    return None


def find_model() -> Optional[str]:
    explicit = os.environ.get('WHISPER_CPP_MODEL')
    if explicit and Path(explicit).exists():
        return explicit
    for folder in (Path.home() / '.mod/sound2text/models',
                   Path.home() / '.cache/whisper.cpp'):
        if folder.exists():
            models = sorted(folder.glob('ggml-*.bin'))
            if models:
                return str(models[0])
    return None


class WhisperCpp(Engine):
    name = 'whisper.cpp'
    kind = 'local'
    description = ('whisper.cpp through its CLI — no python dependencies at '
                   'all, quantised ggml weights, happy on a small box.')
    needs = ('whisper-cli binary', 'a ggml-*.bin model')
    default_model = ''

    def __init__(self, model: Optional[str] = None, binary: Optional[str] = None,
                 threads: int = 0, **options: Any):
        super().__init__(model or find_model() or '', **options)
        self._binary = binary or find_binary()
        self._threads = int(threads or os.cpu_count() or 4)

    def check(self) -> Tuple[bool, str]:
        if not self._binary:
            return False, 'no whisper-cli on PATH (set WHISPER_CPP_BIN)'
        if not self.model:
            return False, 'no ggml model found (set WHISPER_CPP_MODEL)'
        return True, f'ready — {self._binary}'

    def transcribe_one(self, clip: np.ndarray, language: Optional[str] = None,
                       task: str = 'transcribe', **options: Any) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / 'clip.wav'
            wav.write_bytes(audio.write_wav(clip))
            command = [self._binary, '-m', self.model, '-f', str(wav),
                       '-t', str(self._threads), '-nt', '-np']
            if language:
                command += ['-l', language]
            if task == 'translate':
                command += ['-tr']
            done = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            raise RuntimeError(f'whisper.cpp failed: {done.stderr[-300:]}')
        return {'text': clean(done.stdout), 'binary': self._binary}
