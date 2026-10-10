"""What each engine actually did, on this machine.

Published benchmarks are for somebody else's hardware. The router prefers the
engine that has been fastest *here*, so every run writes back one number: the
real-time factor, model seconds per audio second. Below 1.0 means faster than
listening. The file is small and append-light — a rolling mean per engine, not
a log of every call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .keys import HOME

LEDGER = HOME / 'speed.json'


def _read() -> Dict[str, Dict[str, Any]]:
    if not LEDGER.exists():
        return {}
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def record(engine: str, model: str, audio_s: float, model_s: float,
           device: Optional[str] = None) -> Dict[str, Any]:
    """One completed run. Ignores nonsense so a zero-length clip cannot poison it."""
    if audio_s <= 0.05 or model_s <= 0:
        return {}
    entry_key = f'{engine}:{model}'
    book = _read()
    entry = book.get(entry_key, {'runs': 0, 'audio_s': 0.0, 'model_s': 0.0})
    entry['runs'] += 1
    entry['audio_s'] = round(entry['audio_s'] + audio_s, 3)
    entry['model_s'] = round(entry['model_s'] + model_s, 3)
    entry['rtf'] = round(entry['model_s'] / entry['audio_s'], 4)
    entry['x_realtime'] = round(entry['audio_s'] / entry['model_s'], 2)
    if device:
        entry['device'] = device
    book[entry_key] = entry
    HOME.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(book, indent=2, sort_keys=True))
    return entry


def rtf(engine: str, model: Optional[str] = None) -> Optional[float]:
    """The measured real-time factor, for this model or averaged over the engine."""
    book = _read()
    if model and f'{engine}:{model}' in book:
        return book[f'{engine}:{model}'].get('rtf')
    rows = [v for k, v in book.items() if k.split(':')[0] == engine and v.get('rtf')]
    if not rows:
        return None
    audio_s = sum(r['audio_s'] for r in rows)
    return round(sum(r['model_s'] for r in rows) / audio_s, 4) if audio_s else None


def table() -> Dict[str, Any]:
    book = _read()
    return {'path': str(LEDGER), 'measured': book,
            'note': 'rtf = model seconds per audio second; lower is faster'}


def clear() -> Dict[str, Any]:
    existed = LEDGER.exists()
    LEDGER.unlink(missing_ok=True)
    return {'cleared': existed, 'path': str(LEDGER)}
