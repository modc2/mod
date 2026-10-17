"""Filesystem layout shared by every zkstack component."""
from __future__ import annotations

import os
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = MODULE_DIR / "src"
VENDOR_DIR = MODULE_DIR / "vendor"
UPSTREAM_DIR = VENDOR_DIR / "zkapi"
UPSTREAM_REPO = "https://github.com/vbuterin/zkapi"
# Pinned upstream commit (2026-10-01, "zkapi-v2-note-bound-v1" circuit).
UPSTREAM_COMMIT = "045b444ea1b52538d1b40273c7cb6ed09468a052"
CIRCUIT_ID = "zkapi-v2-note-bound-v1"

SETUP_DIR = UPSTREAM_DIR / "protocol" / "setup" / "v2"
UPSTREAM_CONTRACTS = UPSTREAM_DIR / "protocol" / "contracts"
LOCAL_CONTRACTS = SRC_DIR / "contracts"
WALLET_CRATE = SRC_DIR / "wallet"
TARGET_DIR = UPSTREAM_DIR / "target"
BIN_DIR = TARGET_DIR / "release"
NODE24_DIR = VENDOR_DIR / "node24"

STATE_DIR = Path(os.environ.get("ZKAPI_DATA", MODULE_DIR / "state"))


def bin_path(name: str) -> Path:
    return BIN_DIR / name


def artifact(name: str, local: bool = False) -> Path:
    root = LOCAL_CONTRACTS if local else UPSTREAM_CONTRACTS
    return root / "out" / f"{name}.sol" / f"{name}.json"


def which(name: str) -> str | None:
    import shutil

    found = shutil.which(name)
    if found:
        return found
    for extra in (Path.home() / ".foundry" / "bin" / name, Path.home() / ".cargo" / "bin" / name):
        if extra.exists():
            return str(extra)
    return None
