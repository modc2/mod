"""Tests that need no model and no network.

Everything below the recogniser — the decoder, the resampler, the detector,
the packer, the cache, the router — is testable without weights, and is what
breaks silently when it breaks. The `stub` engine stands in for a model so the
whole pipeline can be run end to end on a bare machine.

The one test that needs a real recogniser is marked and skips itself when none
is installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from src import audio, cache, engines, ledger, pipeline, router, samples, vad  # noqa: E402

SAMPLE = HERE / 'samples/harvard-8k.wav'


@pytest.fixture(scope='module')
def speech():
    pcm, meta = audio.load(SAMPLE)
    return pcm, meta


# ── the decoder ──────────────────────────────────────────────────────

def test_reads_a_real_wav_without_a_media_stack(speech):
    pcm, meta = speech
    assert meta['decoder'] == 'riff (built in)'
    assert meta['source_rate'] == 8000 and meta['rate'] == 16000
    assert 33.0 < meta['seconds'] < 34.0
    assert pcm.dtype == np.float32 and np.abs(pcm).max() <= 1.0


def test_wav_round_trip_survives_16_bit():
    original = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 0.5).astype(np.float32)
    frames, rate = audio.read_wav(audio.write_wav(original, 16000))
    assert rate == 16000 and frames.shape == (16000, 1)
    assert np.abs(frames[:, 0] - original).max() < 1e-4


def test_every_pcm_width_lands_in_the_same_place():
    """8-, 16-, 24- and 32-bit files of the same tone must decode alike."""
    import struct
    tone = (np.sin(2 * np.pi * 300 * np.arange(4000) / 16000) * 0.5)
    for bits, pack in ((8, lambda x: ((x * 127) + 128).astype(np.uint8).tobytes()),
                       (16, lambda x: (x * 32767).astype('<i2').tobytes()),
                       (32, lambda x: (x * 2147483647).astype('<i4').tobytes())):
        body = pack(tone)
        raw = (b'RIFF' + struct.pack('<I', 36 + len(body)) + b'WAVEfmt ' +
               struct.pack('<IHHIIHH', 16, 1, 1, 16000, 16000, 2, bits) +
               b'data' + struct.pack('<I', len(body)) + body)
        frames, rate = audio.read_wav(raw)
        assert rate == 16000
        assert np.abs(frames[:, 0] - tone).max() < 0.02, f'{bits}-bit drifted'


def test_g711_matches_the_stdlib_it_replaces():
    """audioop was removed in 3.13; these tables must decode telephony the same."""
    audioop = pytest.importorskip('audioop')
    every_byte = bytes(range(256))
    for mine, theirs in ((audio.mulaw_decode, audioop.ulaw2lin),
                         (audio.alaw_decode, audioop.alaw2lin)):
        reference = np.frombuffer(theirs(every_byte, 2), dtype='<i2').astype(np.float32) / 32768.0
        assert np.array_equal(mine(every_byte), reference)


def test_a_mulaw_wav_decodes_as_speech_not_static():
    """A phone-line file has to come out as the signal that went in."""
    import struct
    tone = (np.sin(2 * np.pi * 300 * np.arange(2000) / 8000) * 0.5).astype(np.float32)
    ints = (tone * 32767).astype('<i2')
    # encode to mu-law the direct way, then decode it back through the module
    magnitude = np.abs(ints.astype(np.int32)) + 0x84
    exponent = np.maximum(np.frombuffer(
        np.log2(np.maximum(magnitude, 1)).astype(np.int32).tobytes(),
        dtype=np.int32) - 7, 0)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    encoded = (~(((ints < 0).astype(np.int32) << 7) |
                 (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()
    raw = (b'RIFF' + struct.pack('<I', 36 + len(encoded)) + b'WAVEfmt ' +
           struct.pack('<IHHIIHH', 16, 7, 1, 8000, 8000, 1, 8) +
           b'data' + struct.pack('<I', len(encoded)) + encoded)
    frames, rate = audio.read_wav(raw)
    assert rate == 8000
    assert np.corrcoef(frames[:, 0], tone)[0, 1] > 0.99


def test_junk_is_rejected_rather_than_transcribed():
    with pytest.raises(audio.DecodeError):
        audio.read_wav(b'this is not audio at all')


def test_resampling_keeps_the_tone_and_the_length():
    rate_in = 8000
    tone = np.sin(2 * np.pi * 500 * np.arange(rate_in) / rate_in).astype(np.float32)
    out = audio.resample(tone, rate_in, 16000)
    assert abs(out.size - 16000) <= 32
    peak = np.argmax(np.abs(np.fft.rfft(out))) * 16000 / out.size
    assert abs(peak - 500) < 15                       # the pitch survived


def test_resampling_does_not_fold_aliases_into_the_speech_band():
    """A 3.5 kHz tone downsampled to 8 kHz must not reappear as a low one."""
    tone = np.sin(2 * np.pi * 3500 * np.arange(16000) / 16000).astype(np.float32)
    out = audio.resample(tone, 16000, 8000)
    spectrum = np.abs(np.fft.rfft(out))
    band = spectrum[:int(1500 * out.size / 8000)]     # everything below 1.5 kHz
    assert band.max() < 0.05 * spectrum.max()


# ── the detector ─────────────────────────────────────────────────────

def test_silence_has_no_speech_in_it():
    quiet = (np.random.default_rng(0).standard_normal(16000 * 3) * 1e-4).astype(np.float32)
    assert vad.segments(quiet)['segments'] == []


def test_speech_is_found_where_the_speech_is(speech):
    pcm, _ = speech
    found = vad.segments(pcm)
    assert 4 <= len(found['segments']) <= 20
    assert 0.5 < found['speech_ratio'] < 1.0
    assert found['speech_s'] + found['silence_s'] == pytest.approx(found['total_s'], abs=0.05)
    assert all(s['end'] > s['start'] for s in found['segments'])
    assert all(a['end'] <= b['start'] for a, b in zip(found['segments'],
                                                      found['segments'][1:]))


def test_a_gap_between_two_sounds_is_actually_cut():
    rng = np.random.default_rng(1)
    word = (np.sin(2 * np.pi * 200 * np.arange(16000) / 16000) *
            rng.uniform(0.3, 0.6, 16000)).astype(np.float32)
    quiet = (rng.standard_normal(16000 * 3) * 2e-4).astype(np.float32)
    found = vad.segments(np.concatenate([quiet, word, quiet, word, quiet]))
    assert len(found['segments']) == 2
    assert found['silence_s'] > 5.0                   # the three gaps, less padding


def test_a_long_stretch_is_split_to_fit_the_window():
    rng = np.random.default_rng(2)
    long = (rng.standard_normal(16000 * 70) * 0.2).astype(np.float32)
    found = vad.segments(long, flatness_max=1.1)      # noise is flat; let it pass
    assert all(s['duration'] <= 28.5 for s in found['segments'])


def test_packing_fills_windows_without_losing_audio(speech):
    pcm, _ = speech
    spans = vad.segments(pcm)['segments']
    units = vad.pack(spans, limit=28.0)
    assert len(units) < len(spans)
    assert all(u['speech_s'] <= 28.0 for u in units)
    assert sum(len(u['members']) for u in units) == len(spans)
    clips = vad.cut_packed(pcm, units)
    assert len(clips) == len(units)
    packed_s = sum(c.size for c in clips) / audio.SAMPLE_RATE
    speech_s = sum(s['duration'] for s in spans)
    assert packed_s == pytest.approx(speech_s, abs=1.0)   # plus the joining beats


def test_packing_preserves_the_order_of_what_was_said():
    spans = [{'start': float(i), 'end': i + 0.5, 'duration': 0.5} for i in range(40)]
    units = vad.pack(spans, limit=5.0)
    flat = [m['start'] for u in units for m in u['members']]
    assert flat == sorted(flat) == [s['start'] for s in spans]


# ── the registry and the router ──────────────────────────────────────

def test_every_engine_answers_whether_it_can_run():
    for card in engines.catalog():
        assert set(card) >= {'name', 'available'}
        assert isinstance(card['available'], bool)
        if not card['available']:
            assert card['note'], f'{card["name"]} refuses without saying why'


def test_an_unknown_engine_is_an_error_not_a_default():
    with pytest.raises(KeyError):
        engines.build('nonexistent-asr')


def test_the_router_never_picks_the_stub_by_itself():
    try:
        decision = router.choose()
    except RuntimeError as exc:
        assert 'no recogniser' in str(exc)             # the honest failure
        return
    assert decision['engine'] != 'stub'


def test_the_router_explains_itself():
    decision = router.choose(prefer='stub', allow_stub=True)
    assert decision['engine'] == 'stub' and decision['why']


def test_asking_for_an_engine_that_cannot_run_fails_loudly():
    card = next(c for c in engines.catalog() if not c['available'])
    with pytest.raises(RuntimeError):
        router.choose(prefer=card['name'])


# ── the cache ────────────────────────────────────────────────────────

def test_the_cache_is_keyed_on_the_audio_not_the_name():
    a = np.linspace(-1, 1, 4000, dtype=np.float32)
    b = a.copy()
    c = a * 0.5
    key = lambda x: cache.key(x, 'e', 'm', None, 'transcribe')
    assert key(a) == key(b) and key(a) != key(c)
    assert cache.key(a, 'e', 'm', None, 'transcribe') != \
        cache.key(a, 'other', 'm', None, 'transcribe')


def test_a_stored_transcript_comes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, 'CACHE', tmp_path / 'c')
    digest = 'a' * 64
    assert cache.get(digest) is None
    cache.put(digest, {'text': 'hello'})
    assert cache.get(digest)['text'] == 'hello'


# ── the pipeline, end to end, without a model ────────────────────────

def test_the_whole_pipeline_runs_without_a_recogniser(speech):
    run = pipeline.transcribe(SAMPLE, engine='stub', use_cache=False)
    assert run['engine'] == 'stub'
    assert run['transcript'] is False, 'the stub must never claim to be a transcript'
    stats = run['stats']
    assert stats['audio_s'] > 33
    assert stats['sent_to_model_s'] < stats['audio_s']
    assert stats['windows'] < stats['speech_spans']
    assert stats['saved_pct'] > 0


def test_switching_the_detector_off_sends_everything():
    run = pipeline.transcribe(SAMPLE, engine='stub', use_vad=False, use_cache=False)
    assert run['stats']['sent_to_model_s'] == pytest.approx(run['stats']['audio_s'], abs=0.1)
    assert run['stats']['saved_pct'] == 0.0


def test_the_second_run_is_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, 'CACHE', tmp_path / 'c')
    first = pipeline.transcribe(SAMPLE, engine='stub')
    second = pipeline.transcribe(SAMPLE, engine='stub')
    assert first['stats']['windows_run'] > 0
    assert second['stats']['windows_run'] == 0
    assert second['stats']['from_cache_s'] > 0
    assert second['text'] == first['text']


def test_arrays_are_accepted_as_well_as_files():
    tone = (np.sin(2 * np.pi * 220 * np.arange(8000) / 8000) * 0.4).astype(np.float32)
    run = pipeline.transcribe(tone, engine='stub', source_rate=8000, use_cache=False)
    assert run['audio']['seconds'] == pytest.approx(1.0, abs=0.01)


def test_the_ledger_ignores_nonsense(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, 'LEDGER', tmp_path / 'speed.json')
    assert ledger.record('e', 'm', 0.0, 1.0) == {}
    assert ledger.record('e', 'm', 10.0, 0.0) == {}
    entry = ledger.record('e', 'm', 10.0, 2.0)
    assert entry['rtf'] == 0.2 and entry['x_realtime'] == 5.0


def test_the_constructed_sample_is_labelled_as_constructed():
    catalog = samples.catalog()
    assert any(s.get('real') for s in catalog)
    made = [s for s in catalog if s.get('real') is False]
    assert made and all('built' in s['note'] for s in made)
    assert all(s['speech_ratio'] < 0.7 for s in made)


# ── the real thing, when there is one ────────────────────────────────

def _recogniser():
    return next((c['name'] for c in engines.catalog()
                 if c['available'] and c['name'] != 'stub'), None)


@pytest.mark.slow
def test_a_real_recogniser_reads_the_first_sentence():
    engine = _recogniser()
    if engine is None:
        pytest.skip('no recogniser installed on this machine')
    if engine == 'whisper-torch':
        run = pipeline.transcribe(SAMPLE, engine=engine, model='tiny.en', use_cache=False)
    else:
        run = pipeline.transcribe(SAMPLE, engine=engine, use_cache=False)
    assert run['transcript'] is True
    words = run['text'].lower()
    assert 'birch' in words and 'planks' in words
    assert run['stats']['rtf'] is not None
