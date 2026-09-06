"""What every recogniser has to look like from the outside.

An engine is handed a list of clips — already mono float32 at 16 kHz, already
trimmed to speech — and gives back one result per clip. Nothing above this
line knows whether the work happened in this process, in a subprocess, or in
somebody's data centre, which is the whole point: the pipeline that saves the
audio is worth writing once, not once per vendor.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

RATE = 16000


class Engine:
    name: str = 'engine'
    kind: str = 'local'                 # local | remote | stub
    description: str = ''
    needs: Tuple[str, ...] = ()         # pip packages, binaries or keys
    cost_per_min: float = 0.0           # USD, as billed by the vendor
    default_model: str = ''
    batches: bool = False               # can it take clips together?

    def __init__(self, model: Optional[str] = None, **options: Any):
        self.model = model or self.default_model
        self.options = options

    # ── availability ─────────────────────────────────────────────────

    def check(self) -> Tuple[bool, str]:
        """(usable here, why not). Must never raise, and never load a model."""
        return True, 'ready'

    def available(self) -> bool:
        return self.check()[0]

    def card(self) -> Dict[str, Any]:
        ok, note = self.check()
        return {'name': self.name, 'kind': self.kind, 'model': self.model,
                'description': self.description, 'needs': list(self.needs),
                'cost_per_min': self.cost_per_min, 'available': ok, 'note': note}

    # ── the work ─────────────────────────────────────────────────────

    def load(self) -> None:
        """Bring weights into memory. Called once, lazily, before the first clip."""

    def transcribe_one(self, clip: np.ndarray, **options: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def transcribe(self, clips: List[np.ndarray], **options: Any) -> List[Dict[str, Any]]:
        """One result per clip: {'text', 'seconds', 'engine', ...}."""
        self.load()
        results = []
        for clip in clips:
            started = time.time()
            out = self.transcribe_one(clip, **options)
            out.setdefault('engine', self.name)
            out.setdefault('model', self.model)
            out['seconds'] = round(time.time() - started, 3)
            out['audio_s'] = round(float(clip.size) / RATE, 3)
            results.append(out)
        return results


def clean(text: Any) -> str:
    """Whisper family models pad with spaces and repeat the odd token."""
    return ' '.join(str(text or '').split()).strip()
