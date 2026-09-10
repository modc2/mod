"""Where this module keeps its own things.

Everything mutable lives under ~/.mod/freetoken — never in the repo. The box
list can carry a daemon token, so it is written 0600.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path:
    """The state directory. Read at call time so tests can move it."""
    override = os.environ.get('FREETOKEN_DIR')
    root = Path(override).expanduser() if override else Path.home() / '.mod' / 'freetoken'
    root.mkdir(parents=True, exist_ok=True)
    return root


def logs() -> Path:
    d = home() / 'logs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def venv() -> Path:
    """The managed virtualenv FreeToken is installed into, if this module put it there."""
    return home() / 'venv'


def read(name: str, default: Any = None) -> Any:
    target = home() / name
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def write(name: str, payload: Any, private: bool = False) -> Any:
    target = home() / name
    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2))
    if private:
        os.chmod(tmp, 0o600)
    tmp.replace(target)
    return payload


def tail(path: Path, lines: int = 80) -> str:
    """The last N lines of a log, without reading the whole file into memory."""
    if not path.exists():
        return ''
    with path.open('rb') as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        block, data = 8192, b''
        while size > 0 and data.count(b'\n') <= lines:
            step = min(block, size)
            size -= step
            fh.seek(size)
            data = fh.read(step) + data
    return b'\n'.join(data.splitlines()[-lines:]).decode('utf-8', 'replace')
