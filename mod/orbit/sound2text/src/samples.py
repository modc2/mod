"""Audio to test against, including a recording shaped like a real one.

`harvard-8k.wav` is the module's honest worst case: read sentences, one after
another, 86% speech. Almost nothing can be skipped, so it is the file to check
that skipping never *costs* anything.

Real recordings are not like that. A voice note has a fumble at the start, a
meeting has pauses and someone finding a file, a call has hold time. `sparse`
is built here from the same real speech with the pauses that a room puts
between sentences — faint room tone, not digital silence, because digital
silence would make the detector's job unrealistically easy. It is constructed,
and labelled as constructed everywhere it appears.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from . import audio, vad

HERE = Path(__file__).resolve().parent.parent / 'samples'
RATE = audio.SAMPLE_RATE


def room_tone(seconds: float, rng: np.random.Generator, level: float = 0.0015
              ) -> np.ndarray:
    """Quiet, slightly coloured noise — what an empty room sounds like."""
    n = int(seconds * RATE)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    white = rng.standard_normal(n).astype(np.float32)
    smooth = np.convolve(white, np.ones(8, dtype=np.float32) / 8, mode='same')
    hum = 0.15 * np.sin(2 * np.pi * 50 * np.arange(n) / RATE).astype(np.float32)
    return ((smooth + hum) * level).astype(np.float32)


def make_sparse(target_ratio: float = 0.45, seed: int = 7) -> Path:
    """The same sentences, spaced out until speech is `target_ratio` of the file."""
    out = HERE / 'sparse-45pct-speech.wav'
    if out.exists():
        return out
    pcm, _ = audio.load(HERE / 'harvard-8k.wav')
    spans = vad.segments(pcm)['segments']
    rng = np.random.default_rng(seed)

    speech = [pcm[int(s['start'] * RATE):int(s['end'] * RATE)] for s in spans]
    speech_s = sum(c.size for c in speech) / RATE
    total_gap = speech_s / target_ratio - speech_s
    # Uneven gaps: a real pause is not a metronome.
    weights = rng.uniform(0.4, 1.6, len(speech) + 1)
    gaps = total_gap * weights / weights.sum()

    pieces: List[np.ndarray] = [room_tone(gaps[0], rng)]
    for clip, gap in zip(speech, gaps[1:]):
        pieces.append(clip)
        pieces.append(room_tone(gap, rng))
    joined = np.concatenate(pieces).astype(np.float32)

    HERE.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio.write_wav(joined, RATE))
    return out


def catalog() -> List[Dict[str, Any]]:
    """What is here to test with, and what each one is for."""
    make_sparse()
    entries = []
    for path in sorted(HERE.glob('*.wav')):
        try:
            pcm, meta = audio.load(path)
            found = vad.segments(pcm)
            entries.append({
                'name': path.name, 'path': str(path),
                'seconds': meta['seconds'], 'source_rate': meta['source_rate'],
                'speech_ratio': found['speech_ratio'],
                'real': path.name.startswith('harvard'),
                'note': ('Harvard sentences, read aloud, 8 kHz — a real recording, '
                         'and nearly all speech'
                         if path.name.startswith('harvard') else
                         'built from the real one by this module: the same sentences '
                         'with room tone between them, as a recording of a room would be'),
            })
        except Exception as exc:
            entries.append({'name': path.name, 'error': str(exc)})
    return entries


def default() -> str:
    return str(HERE / 'harvard-8k.wav')
