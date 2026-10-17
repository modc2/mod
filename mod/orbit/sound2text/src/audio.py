"""Getting samples out of a file, without a media stack.

Every speech recogniser wants the same thing: mono float32 at 16 kHz. Getting
there is usually a shell-out to ffmpeg, which is 80 MB of dependency for a job
that, for WAV, is a header and a memory copy. So this module reads RIFF itself
— PCM 8/16/24/32, IEEE float, A-law and mu-law — and resamples in numpy.
ffmpeg is still used when it is installed and the input is something it alone
can open (mp3, m4a, ogg, webm, or a video), and `soundfile` after that. What
decoded the audio is always reported, never guessed at.
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import struct
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

SAMPLE_RATE = 16000
_FFMPEG = shutil.which('ffmpeg')

# WAVE_FORMAT_* tags that appear in the fmt chunk.
_PCM, _FLOAT, _ALAW, _MULAW, _EXTENSIBLE = 0x0001, 0x0003, 0x0006, 0x0007, 0xFFFE


class DecodeError(ValueError):
    """The bytes are not audio this module can open."""


# ── G.711 ────────────────────────────────────────────────────────────
# Telephony audio — anything that came off a phone line or a SIP trunk — is
# a-law or mu-law, and it is exactly the audio people most want transcribed.
# The stdlib had `audioop` for this; it was removed in Python 3.13, so the two
# tables are unpacked here in numpy instead. Both are checked against the old
# module's output in the tests, while there is still a version to check against.

def mulaw_decode(raw: bytes) -> np.ndarray:
    """G.711 mu-law (North America, Japan) to float32."""
    byte = (~np.frombuffer(raw, dtype=np.uint8).astype(np.int32)) & 0xFF
    magnitude = (((byte & 0x0F) << 3) + 0x84) << ((byte >> 4) & 0x07)
    value = magnitude - 0x84
    return (np.where(byte & 0x80, -value, value) / 32768.0).astype(np.float32)


def alaw_decode(raw: bytes) -> np.ndarray:
    """G.711 a-law (Europe, and most of the rest) to float32."""
    byte = np.frombuffer(raw, dtype=np.uint8).astype(np.int32) ^ 0x55
    exponent, mantissa = (byte >> 4) & 0x07, byte & 0x0F
    magnitude = np.where(exponent == 0, (mantissa << 4) + 8,
                         ((mantissa << 4) + 0x108) << np.maximum(exponent - 1, 0))
    # a-law carries the sign the other way up from mu-law: after the 0x55 mask,
    # the high bit set means positive.
    return (np.where(byte & 0x80, magnitude, -magnitude) / 32768.0).astype(np.float32)


# ── reading the container ────────────────────────────────────────────

def read_wav(raw: bytes) -> Tuple[np.ndarray, int]:
    """RIFF/WAVE to float32 in [-1, 1], shape (frames, channels).

    Written out rather than handed to `wave` because `wave` refuses float32
    files and gives back bytes we would have to unpack anyway.
    """
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise DecodeError('not a RIFF/WAVE file')

    fmt: Optional[Dict[str, Any]] = None
    data: Optional[bytes] = None
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack('<I', raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            tag, channels, rate, _bps, _align, bits = struct.unpack('<HHIIHH', body[:16])
            if tag == _EXTENSIBLE and len(body) >= 26:
                tag = struct.unpack('<H', body[24:26])[0]
            fmt = {'tag': tag, 'channels': channels, 'rate': rate, 'bits': bits}
        elif cid == b'data':
            # A streamed WAV can carry size 0 or 0xFFFFFFFF; trust the file length.
            data = body if 0 < size <= len(raw) - pos - 8 else raw[pos + 8:]
            if fmt is not None:
                break
        pos += 8 + size + (size & 1)          # chunks are word-aligned

    if fmt is None or data is None:
        raise DecodeError('WAVE file has no fmt or data chunk')

    channels, bits, tag = fmt['channels'], fmt['bits'], fmt['tag']
    if tag in (_ALAW, _MULAW):
        samples = (alaw_decode if tag == _ALAW else mulaw_decode)(data)
        frames = len(samples) // max(channels, 1)
        return samples[:frames * channels].reshape(frames, channels), fmt['rate']

    if tag == _FLOAT:
        dtype = {32: '<f4', 64: '<f8'}.get(bits)
        if dtype is None:
            raise DecodeError(f'{bits}-bit float WAV is not a thing')
        samples = np.frombuffer(data, dtype=dtype).astype(np.float32)
    elif tag == _PCM:
        if bits == 8:                          # 8-bit PCM is unsigned
            samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128) / 128.0
        elif bits in (16, 32):
            dtype = {16: '<i2', 32: '<i4'}[bits]
            samples = np.frombuffer(data, dtype=dtype).astype(np.float32) / float(1 << (bits - 1))
        elif bits == 24:
            trimmed = data[:len(data) - len(data) % 3]
            triples = np.frombuffer(trimmed, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            packed = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
            packed = np.where(packed & 0x800000, packed - 0x1000000, packed)
            samples = packed.astype(np.float32) / float(1 << 23)
        else:
            raise DecodeError(f'{bits}-bit PCM is not supported')
    else:
        raise DecodeError(f'WAVE format tag 0x{tag:04x} is not supported '
                          f'(install ffmpeg for compressed audio)')

    frames = len(samples) // max(channels, 1)
    return samples[:frames * channels].reshape(frames, channels), fmt['rate']


def write_wav(pcm: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    """Mono float32 back to 16-bit PCM WAV — what remote APIs want to be posted."""
    clipped = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    ints = (clipped * 32767.0).astype('<i2').tobytes()
    header = b'RIFF' + struct.pack('<I', 36 + len(ints)) + b'WAVEfmt ' + \
        struct.pack('<IHHIIHH', 16, _PCM, 1, rate, rate * 2, 2, 16) + \
        b'data' + struct.pack('<I', len(ints))
    return header + ints


# ── resampling ───────────────────────────────────────────────────────

def _gcd_ratio(src: int, dst: int) -> Tuple[int, int]:
    g = np.gcd(src, dst)
    return dst // g, src // g          # up, down


def resample(x: np.ndarray, src: int, dst: int = SAMPLE_RATE) -> np.ndarray:
    """Rational resampling with a Kaiser-windowed sinc — polyphase, in numpy.

    Linear interpolation would be four lines, and would fold everything above
    the new Nyquist back into the band a recogniser listens to. The filter is
    the point of the function.
    """
    if src == dst or x.size == 0:
        return x.astype(np.float32)
    up, down = _gcd_ratio(src, dst)
    if up > 512 or down > 512:                  # absurd ratio: fall back to linear
        n = int(round(x.size * dst / src))
        return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)

    half, beta = 16, 8.6                        # 32 taps per polyphase branch
    cutoff = 0.5 / max(up, down)                # normalised to the upsampled rate
    n = np.arange(-half * max(up, down), half * max(up, down) + 1, dtype=np.float64)
    taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(n.size, beta)
    taps = (taps / taps.sum() * up).astype(np.float64)

    upsampled = np.zeros(x.size * up, dtype=np.float64)
    upsampled[::up] = x
    filtered = np.convolve(upsampled, taps, mode='same')
    return filtered[::down].astype(np.float32)


# ── the front door ───────────────────────────────────────────────────

def _fetch(source: Any) -> Tuple[bytes, str]:
    """Anything a caller might hand us, to bytes plus where they came from."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source), 'bytes'
    if isinstance(source, np.ndarray):
        raise DecodeError('arrays go to load_array(), not load()')
    text = str(source)
    if text.startswith('data:'):
        return base64.b64decode(text.split(',', 1)[-1]), 'data-uri'
    if text.startswith(('http://', 'https://')):
        with urllib.request.urlopen(text, timeout=60) as response:
            return response.read(), text
    path = Path(os.path.expanduser(text))
    if path.exists():
        return path.read_bytes(), str(path)
    if len(text) > 256:                          # long non-path string: assume base64
        try:
            return base64.b64decode(text, validate=True), 'base64'
        except Exception:
            pass
    raise DecodeError(f'no such audio: {text[:80]}')


def _via_ffmpeg(raw: bytes, rate: int) -> np.ndarray:
    done = subprocess.run(
        [_FFMPEG, '-nostdin', '-loglevel', 'error', '-i', 'pipe:0',
         '-f', 'f32le', '-ac', '1', '-ar', str(rate), 'pipe:1'],
        input=raw, capture_output=True)
    if done.returncode != 0 or not done.stdout:
        raise DecodeError(f'ffmpeg could not decode this: {done.stderr.decode()[:200]}')
    return np.frombuffer(done.stdout, dtype='<f4').copy()


def _via_soundfile(raw: bytes) -> Tuple[np.ndarray, int]:
    import soundfile                             # optional, never required
    data, rate = soundfile.read(io.BytesIO(raw), dtype='float32', always_2d=True)
    return data, rate


def load(source: Any, rate: int = SAMPLE_RATE) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Path, URL, bytes, data-URI or base64 → mono float32 at `rate`.

    Returns the samples and a note of how they were obtained, because "which
    decoder ran" is the first question when a transcript comes back as noise.
    """
    raw, origin = _fetch(source)
    meta: Dict[str, Any] = {'origin': origin, 'bytes': len(raw)}

    if raw[:4] == b'RIFF':
        frames, native = read_wav(raw)
        meta.update(decoder='riff (built in)', source_rate=native,
                    channels=int(frames.shape[1]))
        mono = frames.mean(axis=1) if frames.shape[1] > 1 else frames[:, 0]
    elif _FFMPEG:
        mono = _via_ffmpeg(raw, rate)
        meta.update(decoder='ffmpeg', source_rate=rate, channels=1)
        native = rate
    else:
        try:
            frames, native = _via_soundfile(raw)
        except ImportError:
            raise DecodeError(
                'this is not a WAV, and neither ffmpeg nor soundfile is installed '
                '— convert to WAV first, or `apt install ffmpeg`')
        meta.update(decoder='soundfile', source_rate=native,
                    channels=int(frames.shape[1]))
        mono = frames.mean(axis=1) if frames.shape[1] > 1 else frames[:, 0]

    mono = np.ascontiguousarray(mono, dtype=np.float32)
    if native != rate:
        mono = resample(mono, native, rate)
        meta['resampled'] = f'{native} → {rate} Hz'
    meta.update(rate=rate, samples=int(mono.size),
                seconds=round(float(mono.size) / rate, 3))
    return mono, meta


def load_array(samples: Any, source_rate: int, rate: int = SAMPLE_RATE
               ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Samples a caller already has in memory (a list, or an array of any dtype)."""
    mono = np.asarray(samples, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    if source_rate != rate:
        mono = resample(mono, source_rate, rate)
    return mono, {'origin': 'array', 'decoder': 'none', 'source_rate': source_rate,
                  'rate': rate, 'channels': 1, 'samples': int(mono.size),
                  'seconds': round(float(mono.size) / rate, 3)}


def peak_normalise(pcm: np.ndarray, target: float = 0.95) -> np.ndarray:
    """Quiet recordings transcribe badly; scale to a fixed peak, never clipping."""
    peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
    return pcm if peak < 1e-6 or peak >= target else (pcm * (target / peak)).astype(np.float32)


def capabilities() -> Dict[str, Any]:
    """Which containers this host can actually open."""
    try:
        import soundfile                                            # noqa: F401
        has_sf = True
    except Exception:
        has_sf = False
    formats = ['wav (pcm 8/16/24/32, float32/64, a-law, mu-law)']
    if _FFMPEG:
        formats.append('anything ffmpeg reads (mp3, m4a, ogg, webm, flac, video)')
    if has_sf:
        formats.append('anything libsndfile reads (flac, ogg, aiff)')
    return {'ffmpeg': _FFMPEG, 'soundfile': has_sf, 'formats': formats,
            'target_rate': SAMPLE_RATE}
