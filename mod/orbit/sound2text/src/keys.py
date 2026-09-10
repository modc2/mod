"""API keys, kept off the tree.

Anything private lives under ~/.mod/sound2text/ at 0600 — never in config.json,
never in the repository. The environment wins over the file, and a per-request
key wins over both, so a caller can pass their own without it ever being
written down.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

HOME = Path(os.environ.get('SOUND2TEXT_DIR', Path.home() / '.mod/sound2text'))
KEYS = HOME / 'keys.json'


def _read() -> Dict[str, str]:
    if not KEYS.exists():
        return {}
    try:
        return json.loads(KEYS.read_text())
    except Exception:
        return {}


def get(vendor: str) -> Optional[str]:
    return _read().get(vendor) or None


def put(vendor: str, key: str) -> Dict[str, object]:
    HOME.mkdir(parents=True, exist_ok=True)
    stored = _read()
    stored[vendor] = key
    KEYS.write_text(json.dumps(stored, indent=2))
    KEYS.chmod(0o600)
    return {'vendor': vendor, 'stored': str(KEYS), 'tail': f'…{key[-4:]}'}


def drop(vendor: str) -> Dict[str, object]:
    stored = _read()
    had = stored.pop(vendor, None) is not None
    KEYS.write_text(json.dumps(stored, indent=2))
    return {'vendor': vendor, 'removed': had}


def listing() -> Dict[str, str]:
    """Which vendors have a key, and from where — the value itself never leaves."""
    from .engines.remote import VENDORS
    out = {}
    for vendor, spec in VENDORS.items():
        if os.environ.get(spec['env']):
            out[vendor] = f'environment ({spec["env"]})'
        elif get(vendor):
            out[vendor] = f'keystore ({KEYS})'
        else:
            out[vendor] = 'none'
    return out
