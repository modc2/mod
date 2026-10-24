"""
Agent — the shared base every polymarket agent mod inherits.

Each agent is its own mod at src/agents/<name>/ (config.json + mod.py +
README.md). The config is the contract: the Next app's roster
(lib/server/agentMods.ts) reads the same files, so adding an agent = adding a
folder, and removing one = deleting it. Nothing is registered in code.

On/off lives in ONE file shared with the console's AGENTS tab:
    ~/.mod/polymarket/agents.json   {"off": ["lab", ...]}
An agent is ON unless its name is listed there (or its config says
"default_enabled": false and it has never been switched on — tracked in "on").
A switched-off agent's route answers 409 {"error": "agent off", "agent": name}
and its background loop (if any) skips its ticks.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests


def state_dir() -> str:
    return os.environ.get("POLYMARKET_ACCESS_DIR") or os.path.join(os.path.expanduser("~"), ".mod", "polymarket")


def state_path() -> str:
    return os.path.join(state_dir(), "agents.json")


def read_state() -> Dict[str, list]:
    try:
        with open(state_path()) as f:
            raw = json.load(f)
        return {"off": list(raw.get("off") or []), "on": list(raw.get("on") or [])}
    except Exception:
        return {"off": [], "on": []}


def write_state(state: Dict[str, list]) -> None:
    os.makedirs(state_dir(), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"off": sorted(set(state["off"])), "on": sorted(set(state["on"]))}, f, indent=2)
    os.replace(tmp, state_path())


class Agent:
    """One polymarket agent. Subclasses only need to sit next to a config.json."""

    APP = os.environ.get("POLYMARKET_APP_URL", "http://localhost:3091/polymarket")

    def __init__(self, dirpath: Optional[str] = None):
        self.dir = dirpath or os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(self.dir, "config.json")) as f:
            self.config: Dict[str, Any] = json.load(f)
        self.name: str = self.config["name"]

    # ── on / off ──
    def enabled(self) -> bool:
        s = read_state()
        if self.name in s["off"]:
            return False
        if self.config.get("default_enabled", True) is False:
            return self.name in s["on"]
        return True

    def set_enabled(self, on: bool) -> Dict[str, Any]:
        s = read_state()
        s["off"] = [n for n in s["off"] if n != self.name]
        s["on"] = [n for n in s["on"] if n != self.name]
        (s["on"] if on else s["off"]).append(self.name)
        write_state(s)
        return self.info()

    def on(self) -> Dict[str, Any]:
        return self.set_enabled(True)

    def off(self) -> Dict[str, Any]:
        return self.set_enabled(False)

    def toggle(self) -> Dict[str, Any]:
        return self.set_enabled(not self.enabled())

    # ── describe ──
    def info(self) -> Dict[str, Any]:
        return {**self.config, "enabled": self.enabled(), "path": self.dir}

    def readme(self) -> str:
        try:
            with open(os.path.join(self.dir, "README.md")) as f:
                return f.read()
        except OSError:
            return ""

    # ── call ──
    def call(self, body: Dict[str, Any], token: str, query: str = "", timeout: float = 300) -> Any:
        """POST to this agent's route in the running app (owner Bearer token)."""
        url = f"{self.APP}{self.config['route']}{('?' + query) if query else ''}"
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        try:
            return r.json()
        except ValueError:
            return {"status": r.status_code, "text": r.text[:2000]}
