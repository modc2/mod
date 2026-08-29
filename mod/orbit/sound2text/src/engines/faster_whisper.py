"""faster-whisper — the same weights, four times the speed, when it is installed.

CTranslate2 runs Whisper as int8 on a CPU without the accuracy loss showing up
in ordinary speech, and it is the right local engine whenever it is present.
It is not a dependency of this module: if the import fails, the router simply
routes elsewhere and says so.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .base import RATE, Engine, clean


class FasterWhisper(Engine):
    name = 'faster-whisper'
    kind = 'local'
    description = ('Whisper on CTranslate2 — int8 on CPU, float16 on GPU. The '
                   'fastest local option, and the one to install if you plan '
                   'to transcribe more than an hour of anything.')
    needs = ('faster-whisper',)
    default_model = 'base.en'

    def __init__(self, model: Optional[str] = None, device: str = 'auto',
                 compute_type: Optional[str] = None, **options: Any):
        super().__init__(model, **options)
        self._device, self._compute = device, compute_type
        self._model = None

    def check(self) -> Tuple[bool, str]:
        try:
            import faster_whisper                                   # noqa: F401
        except ImportError:
            return False, 'pip install faster-whisper'
        return True, 'ready'

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        device = self._device
        if device == 'auto':
            try:
                import torch
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
            except Exception:
                device = 'cpu'
        compute = self._compute or ('float16' if device == 'cuda' else 'int8')
        self._model = WhisperModel(self.model, device=device, compute_type=compute)
        self._on, self._precision = device, compute

    def transcribe_one(self, clip: np.ndarray, language: Optional[str] = None,
                       task: str = 'transcribe', **options: Any) -> Dict[str, Any]:
        parts, info = self._model.transcribe(
            np.asarray(clip, dtype=np.float32), language=language, task=task,
            beam_size=int(options.get('beam_size', 1)),
            vad_filter=False)                    # our own VAD already ran
        text = ' '.join(clean(p.text) for p in parts)
        return {'text': clean(text), 'device': self._on, 'precision': self._precision,
                'language': getattr(info, 'language', language)}
