"""Decode → find the speech → look it up → run only what is left.

This is the module's actual claim. A recogniser is one step of five, and the
other four decide what it costs:

    decode    reading the samples, without a media stack where possible
    detect    where the speech is, so silence never reaches the model
    recall    segments transcribed before are not transcribed again
    route     the engine that is fastest, cheapest or best — here, measured
    run       the model, on segments, batched

Every run reports how many seconds of audio existed, how many were sent, and
what the difference was worth. A transcript that arrives without those numbers
is a transcript you cannot compare to anything.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from . import audio, cache, engines, ledger, router, score, vad

RATE = audio.SAMPLE_RATE


def _percent(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole > 0 else 0.0


def transcribe(source: Any, engine: Optional[str] = None, policy: str = 'fast',
               language: Optional[str] = None, task: str = 'transcribe',
               use_vad: bool = True, use_cache: bool = True, pack: bool = True,
               normalise: bool = True, model: Optional[str] = None,
               allow_stub: bool = False, source_rate: Optional[int] = None,
               **options: Any) -> Dict[str, Any]:
    """Audio in, text out, with the arithmetic of how it got there."""
    began = time.time()

    # ── decode ───────────────────────────────────────────────────────
    if isinstance(source, np.ndarray) or (isinstance(source, list) and source
                                          and isinstance(source[0], (int, float))):
        pcm, meta = audio.load_array(source, int(source_rate or RATE), RATE)
    else:
        pcm, meta = audio.load(source, RATE)
    if normalise:
        pcm = audio.peak_normalise(pcm)
    decode_s = time.time() - began
    total_s = float(pcm.size) / RATE

    # ── detect ───────────────────────────────────────────────────────
    marked = time.time()
    vad_options = {k: v for k, v in options.items()
                   if k in vad.Settings.__dataclass_fields__}
    found = (vad.segments(pcm, RATE, **vad_options) if use_vad
             else vad.whole(pcm, RATE))
    detect_s = time.time() - marked
    spans = found['segments']

    # Packing is what makes the trimming pay on a window-based model; on a
    # streaming one it costs nothing, so it stays on unless asked otherwise.
    window = float(options.get('max_segment_s', vad.Settings.max_segment_s))
    if pack and use_vad:
        units = vad.pack(spans, limit=window)
        clips = vad.cut_packed(pcm, units, RATE)
    else:
        units = [{**s, 'speech_s': s['duration'], 'members': [dict(s)]} for s in spans]
        clips = vad.cut(pcm, spans, RATE)

    # ── route ────────────────────────────────────────────────────────
    build_options = {'model': model} if model else {}
    decision = router.choose(prefer=engine, policy=policy, allow_stub=allow_stub,
                             **build_options)
    picked = engines.build(decision['engine'], **build_options,
                           **{k: v for k, v in options.items()
                              if k in ('device', 'batch_size', 'beam_size',
                                       'compute_type', 'api_key', 'base_url')})

    # ── recall ───────────────────────────────────────────────────────
    caching = use_cache and cache.enabled()
    results: List[Optional[Dict[str, Any]]] = [None] * len(clips)
    digests: List[Optional[str]] = [None] * len(clips)
    pending: List[int] = []
    cached_s = 0.0
    for i, clip in enumerate(clips):
        if clip.size == 0:
            results[i] = {'text': '', 'engine': picked.name, 'seconds': 0.0}
            continue
        if caching:
            digest = cache.key(clip, picked.name, picked.model, language, task)
            digests[i] = digest
            hit = cache.get(digest)
            if hit is not None:
                hit['cached'] = True
                results[i] = hit
                cached_s += float(clip.size) / RATE
                continue
        pending.append(i)

    # ── run ──────────────────────────────────────────────────────────
    # Weights are brought in before the clock starts. Loading a model is a real
    # cost, and it is reported — but it is not recognition, and folding it into
    # the rate would make every first run look like a slow engine and teach the
    # router the wrong lesson.
    sent_s = float(sum(clips[i].size for i in pending)) / RATE
    marked = time.time()
    if pending:
        picked.load()
    load_s = time.time() - marked

    marked = time.time()
    if pending:
        produced = picked.transcribe([clips[i] for i in pending],
                                     language=language, task=task, **options)
        for i, out in zip(pending, produced):
            out['cached'] = False
            results[i] = out
            if caching and digests[i]:
                cache.put(digests[i], out)
    model_s = time.time() - marked

    device = next((r.get('device') for r in results if r and r.get('device')), None)
    if pending and picked.kind != 'stub':
        ledger.record(picked.name, picked.model, sent_s, model_s, device)

    # ── assemble ─────────────────────────────────────────────────────
    lines = []
    for unit, out in zip(units, results):
        text = (out or {}).get('text', '')
        lines.append({'start': unit['start'], 'end': unit['end'],
                      'speech_s': unit.get('speech_s', unit.get('duration')),
                      'parts': len(unit.get('members', [1])),
                      'text': text, 'cached': bool((out or {}).get('cached'))})
    text = ' '.join(line['text'] for line in lines if line['text']).strip()

    cost = sum(float((r or {}).get('cost_usd') or 0) for r in results)
    baseline_cost = (picked.cost_per_min * total_s / 60) if picked.cost_per_min else 0.0
    wall_s = time.time() - began

    return {
        'text': text,
        'segments': lines,
        'engine': picked.name,
        'model': picked.model,
        'routing': {'why': decision['why'], 'policy': policy,
                    'considered': [{'name': c['name'], 'available': c['available'],
                                    'measured_rtf': c.get('measured_rtf'),
                                    'cost_per_min': c.get('cost_per_min')}
                                   for c in decision['ranked']]},
        'transcript': all((r or {}).get('transcript', True) for r in results),
        'audio': meta,
        'vad': {k: v for k, v in found.items() if k != 'segments'},
        'stats': {
            'audio_s': round(total_s, 3),
            'speech_s': round(found['speech_s'], 3),
            'sent_to_model_s': round(sent_s, 3),
            'skipped_silence_s': round(found['silence_s'], 3),
            'from_cache_s': round(cached_s, 3),
            'sent_pct': _percent(sent_s, total_s),
            'saved_pct': _percent(total_s - sent_s, total_s),
            'speech_spans': len(spans),
            'windows': len(clips),
            'windows_run': len(pending),
            'window_fill_pct': _percent(sent_s, len(clips) * window) if clips else 0.0,
            'decode_s': round(decode_s, 3),
            'detect_s': round(detect_s, 3),
            'load_s': round(load_s, 3),
            'model_s': round(model_s, 3),
            'wall_s': round(wall_s, 3),
            'rtf': round(model_s / sent_s, 4) if sent_s else None,
            'x_realtime': round(total_s / wall_s, 2) if wall_s else None,
            'cost_usd': round(cost, 6),
            'cost_usd_without_this': round(baseline_cost, 6),
            'cost_saved_usd': round(max(baseline_cost - cost, 0.0), 6),
        },
    }


def compare(source: Any, engine: Optional[str] = None, policy: str = 'fast',
            **options: Any) -> Dict[str, Any]:
    """The same audio three ways, on the same engine, with the cache switched off.

    Three, not two, because the middle row is the one people get wrong. Cutting
    the silence out and sending the pieces separately can be *slower* than
    sending the whole file — a window-based model pads every piece back to
    thirty seconds. The saving only lands when the pieces are packed.
    """
    for key in ('use_vad', 'use_cache', 'pack'):
        options.pop(key, None)

    runs = {}
    for label, flags in (('whole_file', {'use_vad': False, 'pack': False}),
                         ('vad_only', {'use_vad': True, 'pack': False}),
                         ('vad_packed', {'use_vad': True, 'pack': True})):
        chosen = engine or runs.get('whole_file', {}).get('engine')
        run = transcribe(source, engine=chosen, policy=policy, use_cache=False,
                         model=options.get('model') or runs.get('whole_file', {}).get('model'),
                         **{k: v for k, v in options.items() if k != 'model'}, **flags)
        runs[label] = run

    rows = {label: {'windows': run['stats']['windows'],
                    'sent_s': run['stats']['sent_to_model_s'],
                    'model_s': run['stats']['model_s'],
                    'rtf': run['stats']['rtf'],
                    'cost_usd': run['stats']['cost_usd'],
                    'text': run['text']}
            for label, run in runs.items()}

    # Where the words that were actually said are known, score the transcript
    # rather than asking the reader to compare three paragraphs by eye.
    truth = score.truth_for(source)
    if truth:
        for label, row in rows.items():
            row['wer'] = score.wer(truth, row['text'])['wer']

    base, best = rows['whole_file'], rows['vad_packed']
    return {
        'reference': 'known' if truth else 'unknown — WER cannot be computed',
        'engine': runs['whole_file']['engine'], 'model': runs['whole_file']['model'],
        'audio_s': runs['whole_file']['stats']['audio_s'],
        'runs': rows,
        'audio_saved_pct': _percent(base['sent_s'] - best['sent_s'], base['sent_s']),
        'time_saved_pct': _percent(base['model_s'] - best['model_s'], base['model_s']),
        'packing_worth_pct': _percent(rows['vad_only']['model_s'] - best['model_s'],
                                      rows['vad_only']['model_s']),
        'same_text': _tidy(base['text']) == _tidy(best['text']),
    }


def transcribe_scored(source: Any, **options: Any) -> Dict[str, Any]:
    """A transcript with its word error rate, for audio whose words are known."""
    run = transcribe(source, **options)
    run['score'] = score.score(source, run['text'])
    return run


def _tidy(text: str) -> str:
    return ' '.join(''.join(c for c in text.lower() if c.isalnum() or c.isspace()).split())


def bench(source: Any, engine_names: Optional[List[str]] = None,
          **options: Any) -> Dict[str, Any]:
    """Every engine that can run here, on the same audio, timed.

    This is what fills the speed ledger, and therefore what the `fast` policy
    routes on afterwards.
    """
    names = engine_names or [c['name'] for c in engines.catalog()
                             if c.get('available') and c['name'] != 'stub']
    rows = []
    for name in names:
        try:
            run = transcribe(source, engine=name, use_cache=False,
                             allow_stub=True, **options)
            rows.append({'engine': name, 'model': run['model'],
                         'model_s': run['stats']['model_s'],
                         'rtf': run['stats']['rtf'],
                         'x_realtime': round(run['stats']['audio_s'] /
                                             run['stats']['model_s'], 2)
                         if run['stats']['model_s'] else None,
                         'cost_usd': run['stats']['cost_usd'],
                         'text': run['text'][:160]})
        except Exception as exc:
            rows.append({'engine': name, 'error': f'{type(exc).__name__}: {exc}'})
    ran = [r for r in rows if 'rtf' in r and r['rtf']]
    return {'results': sorted(rows, key=lambda r: r.get('rtf') or 1e9),
            'fastest': min(ran, key=lambda r: r['rtf'])['engine'] if ran else None,
            'ledger': ledger.table()['measured']}
