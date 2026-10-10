"""An engine that does not recognise anything, and says so in every result.

It exists so the parts of this module that are not a model — decoding, VAD,
segmentation, caching, routing, the API, the console — can be exercised and
tested on a machine with no weights on it and no network. Its "transcript" is
a description of the sound it was given: loud or quiet, how long, roughly what
pitch. Every result carries transcript=False, and the pipeline passes that
flag up, so nothing downstream can mistake this for speech recognition.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

from .base import RATE, Engine


class Stub(Engine):
    name = 'stub'
    kind = 'stub'
    description = ('No model: describes the sound instead of transcribing it. '
                   'For testing the pipeline, never for reading a recording.')
    default_model = 'describe'

    def check(self) -> Tuple[bool, str]:
        return True, 'always available — not a recogniser'

    def transcribe_one(self, clip: np.ndarray, **options: Any) -> Dict[str, Any]:
        if clip.size == 0:
            return {'text': '[empty]', 'transcript': False}
        rms = float(np.sqrt(np.mean(clip.astype(np.float64) ** 2)))
        spectrum = np.abs(np.fft.rfft(clip * np.hanning(clip.size)))
        pitch = float(np.argmax(spectrum) * RATE / clip.size)
        loudness = 'silent' if rms < 1e-4 else 'quiet' if rms < 0.02 else \
            'loud' if rms > 0.2 else 'ordinary'
        return {'text': f'[{loudness} sound, {clip.size / RATE:.2f}s, '
                        f'peak near {pitch:.0f} Hz]',
                'transcript': False, 'rms': round(rms, 5)}
