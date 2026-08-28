"""BYOK vault — the caller's Liquid Cloud key, off-tree and 0600.

Cloud runs are billed to whoever's key made the call, so the key never lands
in config.json and never leaves this box: it lives in ~/.mod/liquidai/keys.json
with owner-only permissions, exactly like every other module's secret. The API
returns it masked, always.
"""

import json
import os
from typing import Any, Dict, Optional

STORE = os.path.expanduser("~/.mod/liquidai")
KEYS_PATH = os.path.join(STORE, "keys.json")

# Env wins over the vault so a container can be handed a key without writing
# one to disk.
ENV_VARS = {
    "cloud": ("LIQUID_API_KEY", "LIQUIDAI_API_KEY"),
    "hf": ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
}


def _read() -> Dict[str, str]:
    try:
        with open(KEYS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _write(vault: Dict[str, str]):
    os.makedirs(STORE, exist_ok=True)
    with open(KEYS_PATH, "w") as f:
        json.dump(vault, f, indent=2)
    os.chmod(KEYS_PATH, 0o600)


def get(provider: str = "cloud") -> Optional[str]:
    for var in ENV_VARS.get(provider, ()):
        if os.environ.get(var):
            return os.environ[var]
    return _read().get(provider) or None


def put(provider: str, key: str) -> Dict[str, Any]:
    vault = _read()
    if key:
        vault[provider] = key
    else:
        vault.pop(provider, None)
    _write(vault)
    return status()


def mask(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "set"


def status() -> Dict[str, Any]:
    out = {}
    for provider in ("cloud", "hf"):
        key = get(provider)
        from_env = any(os.environ.get(v) for v in ENV_VARS.get(provider, ()))
        out[provider] = {
            "set": bool(key),
            "masked": mask(key),
            "source": "env" if from_env else ("vault" if key else None),
        }
    out["path"] = KEYS_PATH
    return out
