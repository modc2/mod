"""The registry — every recogniser this module knows how to drive.

Adding one is a file and a line here. Nothing else in the module names an
engine: the pipeline asks the router, the router asks the registry, and the
registry asks each engine whether it can run on this machine.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .base import Engine, clean
from .faster_whisper import FasterWhisper
from .remote import VENDORS, RemoteWhisper
from .stub import Stub
from .whispercpp import WhisperCpp
from .whisper_torch import SIZES, WhisperTorch

# Order is the router's preference among equals: fastest local first, then the
# one that needs nothing but pip, then the CLI, then anyone's API, then the stub.
BUILDERS: Dict[str, Callable[..., Engine]] = {
    'faster-whisper': FasterWhisper,
    'whisper-torch': WhisperTorch,
    'whisper.cpp': WhisperCpp,
    **{vendor: (lambda v=vendor, **kw: RemoteWhisper(vendor=v, **kw)) for vendor in VENDORS},
    'stub': Stub,
}

ALIASES = {'whisper': 'whisper-torch', 'torch': 'whisper-torch',
           'cpp': 'whisper.cpp', 'ct2': 'faster-whisper', 'local': None}


def build(name: str, **options: Any) -> Engine:
    """An engine by name, configured but not loaded."""
    key = ALIASES.get(name, name) or name
    builder = BUILDERS.get(key)
    if builder is None:
        raise KeyError(f'unknown engine {name!r} — {list(BUILDERS)}')
    return builder(**options)


def catalog(**options: Any) -> List[Dict[str, Any]]:
    """Every engine, whether it can run here, and what it would cost."""
    cards = []
    for name in BUILDERS:
        try:
            cards.append(build(name, **options).card())
        except Exception as exc:                  # a broken engine must not hide the rest
            cards.append({'name': name, 'available': False, 'note': f'{type(exc).__name__}: {exc}'})
    return cards


def names() -> List[str]:
    return list(BUILDERS)


__all__ = ['ALIASES', 'BUILDERS', 'Engine', 'SIZES', 'VENDORS', 'build',
           'catalog', 'clean', 'names']
