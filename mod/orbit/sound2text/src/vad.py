"""Finding the speech, so the model never sees the rest.

A recogniser charges by the second — in GPU time, in API cents, in a phone's
battery — and most recordings are mostly not speech. A meeting has pauses, a
voice note has a fumble at either end, a call has hold music and hang time.
Cutting that out before the model runs is the single largest saving available,
and it needs no model of its own: energy against an adaptive noise floor,
with a flatness test so that hiss and hum are not mistaken for a voice.

Everything here is numpy over frames of 30 ms at a 10 ms hop.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

FRAME_MS, HOP_MS = 30, 10


@dataclass
class Settings:
    """Every knob, with a defensible default for ordinary recorded speech."""
    threshold_db: float = 6.0      # how far above the noise floor counts as speech
    hysteresis_db: float = 3.0     # ... and how far below it stops counting
    flatness_max: float = 0.35     # spectrally flat frames are noise, not voice
    silence_db: float = -55.0      # nothing this quiet is speech, whatever the floor
    min_speech_ms: int = 200       # shorter than this is a click or a breath
    min_silence_ms: int = 320      # shorter gaps stay inside one segment
    pad_ms: int = 200              # keep this much either side — consonants live there
    max_segment_s: float = 28.0    # whisper's window is 30 s; leave room
    floor_percentile: float = 15.0 # the quietest fifth-ish of frames define "quiet"

    @classmethod
    def make(cls, **kwargs: Any) -> 'Settings':
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in kwargs.items() if k in fields and v is not None})


def frame_features(pcm: np.ndarray, rate: int) -> Dict[str, np.ndarray]:
    """Per-frame energy in dB, spectral flatness and zero-crossing rate."""
    size, hop = int(rate * FRAME_MS / 1000), int(rate * HOP_MS / 1000)
    if pcm.size < size:
        pcm = np.pad(pcm, (0, size - pcm.size))
    count = 1 + (pcm.size - size) // hop
    # One strided view over the signal — no copy, no python loop over frames.
    frames = np.lib.stride_tricks.as_strided(
        pcm, shape=(count, size), strides=(pcm.strides[0] * hop, pcm.strides[0]),
        writeable=False)

    energy = 10.0 * np.log10(np.maximum((frames ** 2).mean(axis=1), 1e-12))
    zcr = (np.diff(np.signbit(frames), axis=1).sum(axis=1) / (size - 1)).astype(np.float32)

    spectrum = np.abs(np.fft.rfft(frames * np.hanning(size), axis=1)) ** 2 + 1e-12
    band = spectrum[:, 1:]                          # drop DC — a hum offset is not tone
    flatness = np.exp(np.log(band).mean(axis=1)) / band.mean(axis=1)

    return {'energy_db': energy.astype(np.float32), 'flatness': flatness.astype(np.float32),
            'zcr': zcr, 'hop': hop, 'size': size}


def _mask(features: Dict[str, np.ndarray], cfg: Settings) -> Tuple[np.ndarray, float]:
    """Two thresholds and a Schmitt trigger: loud starts a run, quiet ends it."""
    energy, flatness = features['energy_db'], features['flatness']
    floor = float(np.percentile(energy, cfg.floor_percentile))
    ceiling = float(np.percentile(energy, 95))

    # A recording with no dynamic range — hiss all the way through, a held tone,
    # digital silence — has no floor to measure against. Putting the threshold
    # at its median, as the obvious fallback does, turns half of any hiss into
    # speech. Fall back to the absolute gate and the voicing test instead, which
    # is the difference between "this is quiet" and "this is not a voice".
    span = ceiling - floor
    high = (floor + cfg.threshold_db) if span > cfg.threshold_db else cfg.silence_db
    high = max(high, cfg.silence_db)
    low = max(high - cfg.hysteresis_db, cfg.silence_db)

    # Voicing is required to *start* a run but not to continue one: /s/, /f/ and
    # /sh/ are as flat as noise, and they are the ends of words.
    voiced = flatness < cfg.flatness_max
    active = np.zeros(energy.size, dtype=bool)
    on = False
    for i in range(energy.size):
        if on:
            on = energy[i] > low
        else:
            on = energy[i] > high and voiced[i]
        active[i] = on
    return active, floor


def _runs(active: np.ndarray) -> List[Tuple[int, int]]:
    """Contiguous True runs as [start, end) frame indices."""
    if not active.any():
        return []
    edges = np.diff(active.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    ends = list(np.flatnonzero(edges == -1) + 1)
    if active[0]:
        starts.insert(0, 0)
    if active[-1]:
        ends.append(active.size)
    return list(zip(starts, ends))


def _split_long(segment: Tuple[float, float], energy: np.ndarray, hop_s: float,
                limit: float) -> List[Tuple[float, float]]:
    """Break a segment longer than the model's window at its quietest interior point."""
    start, end = segment
    if end - start <= limit:
        return [segment]
    lo = int((start + limit * 0.6) / hop_s)
    hi = min(int((start + limit) / hop_s), energy.size - 1)
    if hi <= lo:
        cut = start + limit
    else:
        cut = (lo + int(np.argmin(energy[lo:hi]))) * hop_s
    return [(start, cut)] + _split_long((cut, end), energy, hop_s, limit)


def segments(pcm: np.ndarray, rate: int = 16000, **kwargs: Any) -> Dict[str, Any]:
    """Where the speech is, in seconds, plus what skipping the rest is worth.

    The analysis runs on a peak-normalised copy — the audio itself is not
    touched — so that `silence_db` means the same thing whether the recording
    came off a headset or a phone in a pocket.
    """
    cfg = Settings.make(**kwargs)
    total_s = float(pcm.size) / rate
    if total_s <= 0:
        return {'segments': [], 'total_s': 0.0, 'speech_s': 0.0, 'silence_s': 0.0,
                'speech_ratio': 0.0, 'noise_floor_db': None, 'settings': asdict(cfg)}

    peak = float(np.max(np.abs(pcm)))
    scaled = (pcm * (0.95 / peak)).astype(np.float32) if peak > 1e-9 else pcm
    features = frame_features(scaled, rate)
    active, floor = _mask(features, cfg)
    hop_s = HOP_MS / 1000.0

    # Close short gaps first, then drop short bursts: order matters — doing it the
    # other way round deletes the syllables that would have bridged into a word.
    min_gap = max(1, int(cfg.min_silence_ms / HOP_MS))
    for start, end in _runs(~active):
        if 0 < start and end < active.size and (end - start) < min_gap:
            active[start:end] = True

    min_run = max(1, int(cfg.min_speech_ms / HOP_MS))
    kept = [(a, b) for a, b in _runs(active) if (b - a) >= min_run]

    pad = cfg.pad_ms / 1000.0
    spans: List[Tuple[float, float]] = []
    for a, b in kept:
        start = max(0.0, a * hop_s - pad)
        end = min(total_s, b * hop_s + (FRAME_MS / 1000.0) + pad)
        if spans and start <= spans[-1][1]:               # padding made them touch
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))

    final: List[Tuple[float, float]] = []
    for span in spans:
        final.extend(_split_long(span, features['energy_db'], hop_s, cfg.max_segment_s))

    speech_s = float(sum(e - s for s, e in final))
    return {
        'segments': [{'start': round(float(s), 3), 'end': round(float(e), 3),
                      'duration': round(float(e - s), 3)} for s, e in final],
        'total_s': round(total_s, 3),
        'speech_s': round(speech_s, 3),
        'silence_s': round(total_s - speech_s, 3),
        'speech_ratio': round(speech_s / total_s, 4),
        'noise_floor_db': round(floor, 2),
        'settings': asdict(cfg),
    }


def cut(pcm: np.ndarray, spans: List[Dict[str, float]], rate: int = 16000
        ) -> List[np.ndarray]:
    """The audio each segment refers to."""
    return [pcm[int(s['start'] * rate):int(s['end'] * rate)] for s in spans]


# ── packing ──────────────────────────────────────────────────────────
#
# Cutting the silence out is only half the saving, and on its own it can be a
# loss. Whisper's encoder always sees thirty seconds: a two-second clip is
# padded to thirty and costs what thirty costs. Eight two-second segments sent
# one at a time are eight full windows — four minutes of encoder for sixteen
# seconds of speech, which is worse than sending the untouched file.
#
# So the segments are packed back together, in order, up to just under the
# window, with a short silence between them to keep the word boundaries. The
# silence is gone, and the window is full.

def pack(spans: List[Dict[str, float]], limit: float = 28.0, gap: float = 0.12
         ) -> List[Dict[str, Any]]:
    """Consecutive speech spans grouped into units that fill a model window."""
    units: List[Dict[str, Any]] = []
    for span in spans:
        current = units[-1] if units else None
        would_be = (current['speech_s'] + gap + span['duration']) if current else 0.0
        if current is None or would_be > limit:
            units.append({'start': span['start'], 'end': span['end'],
                          'speech_s': round(span['duration'], 3),
                          'members': [dict(span)]})
        else:
            current['members'].append(dict(span))
            current['end'] = span['end']
            current['speech_s'] = round(would_be, 3)
    for unit in units:
        unit['duration'] = round(unit['end'] - unit['start'], 3)
    return units


def cut_packed(pcm: np.ndarray, units: List[Dict[str, Any]], rate: int = 16000,
               gap: float = 0.12) -> List[np.ndarray]:
    """One array per unit: its members, back to back, with a beat between them."""
    quiet = np.zeros(int(gap * rate), dtype=np.float32)
    clips = []
    for unit in units:
        pieces: List[np.ndarray] = []
        for member in unit['members']:
            if pieces:
                pieces.append(quiet)
            pieces.append(pcm[int(member['start'] * rate):int(member['end'] * rate)])
        clips.append(np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32))
    return clips


def whole(pcm: np.ndarray, rate: int = 16000, limit: float = 28.0) -> Dict[str, Any]:
    """VAD switched off: fixed windows over everything. The control condition."""
    total_s = float(pcm.size) / rate
    spans, t = [], 0.0
    while t < total_s:
        end = min(total_s, t + limit)
        spans.append({'start': round(t, 3), 'end': round(end, 3),
                      'duration': round(end - t, 3)})
        t = end
    return {'segments': spans, 'total_s': round(total_s, 3),
            'speech_s': round(total_s, 3), 'silence_s': 0.0, 'speech_ratio': 1.0,
            'noise_floor_db': None, 'settings': {'vad': False, 'window_s': limit}}
