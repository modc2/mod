"""The cheapest transcription is the one that already happened.

Keyed on the audio itself — a sha256 of the samples, not of the filename — so
the same voice note under two names is transcribed once, and re-running a job
after a crash costs nothing. The key is per *segment*, which is what makes it
useful in practice: append thirty seconds to a recording and re-run, and only
the new thirty seconds reach the model. Engine, model, language and task are
part of the key, because a different model is a different answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .keys import HOME

CACHE = HOME / 'cache'


def key(clip: np.ndarray, engine: str, model: str, language: Optional[str],
        task: str) -> str:
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(clip, dtype=np.float32).tobytes())
    digest.update(f'|{engine}|{model}|{language or "auto"}|{task}'.encode())
    return digest.hexdigest()


def _path(digest: str) -> Path:
    return CACHE / digest[:2] / f'{digest}.json'


def get(digest: str) -> Optional[Dict[str, Any]]:
    target = _path(digest)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except Exception:
        return None


def put(digest: str, result: Dict[str, Any]) -> None:
    target = _path(digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload['cached_at'] = time.time()
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload))
    tmp.replace(target)                       # atomic: a torn file is a wrong transcript


def stats() -> Dict[str, Any]:
    files = list(CACHE.rglob('*.json')) if CACHE.exists() else []
    return {'path': str(CACHE), 'entries': len(files),
            'bytes': sum(f.stat().st_size for f in files)}


def clear() -> Dict[str, Any]:
    files = list(CACHE.rglob('*.json')) if CACHE.exists() else []
    for f in files:
        f.unlink(missing_ok=True)
    return {'cleared': len(files), 'path': str(CACHE)}


def enabled() -> bool:
    return os.environ.get('SOUND2TEXT_CACHE', '1') != '0'
