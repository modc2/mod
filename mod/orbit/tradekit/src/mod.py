"""
tradekit — the hyperliquid framework as a reusable standard for
copy-trading venue modules.

    spec()                    the tradekit/1 standard (fns, config, layout, safety)
    check(mod)                does one module conform? (static: config.json + ast)
    board()                   conformance of every venue module in orbit
    scaffold(name, venue)     copy the framework into a NEW module orbit/<name>

hyperliquid is the reference implementation; polymarket conforms through
`standard.aliases` in its config.json. Modules scaffolded here start
conformant (their own tests/test_standard.py pins it).

Pure python, no server, no keys.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
ORBIT = os.path.dirname(ROOT)
TEMPLATE = os.path.join(SRC, "template")


def _load_standard():
    name = "tradekit_standard"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SRC, "standard.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


standard = _load_standard()

try:
    import mod as m
    _Base = m.Mod
except Exception:  # standalone / tests without the mod framework
    _Base = object


def _resolve(mod: str) -> str:
    """Module name or path → its directory."""
    if os.path.isdir(mod):
        return os.path.abspath(mod)
    d = os.path.join(ORBIT, mod)
    if os.path.isdir(d):
        return d
    raise ValueError(f"no module {mod!r} under {ORBIT}")


def _used_ports() -> set:
    used = set()
    for name in os.listdir(ORBIT):
        p = os.path.join(ORBIT, name, "config.json")
        try:
            with open(p) as f:
                txt = f.read()
        except Exception:
            continue
        used.update(int(x) for x in re.findall(r'(?<![\d.])(\d{4,5})(?![\d.])', txt) if 1024 < int(x) < 65536)
    try:
        out = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True, timeout=5).stdout
        used.update(int(x) for x in re.findall(r":(\d+)\s", out))
    except Exception:
        pass
    return used


def _free_pair(start: int = 51440) -> List[int]:
    used = _used_ports()
    p = start
    while p < 65000:
        if p not in used and p + 1 not in used:
            ok = True
            for q in (p, p + 1):
                with socket.socket() as s:
                    ok = ok and s.connect_ex(("127.0.0.1", q)) != 0
            if ok:
                return [p, p + 1]
        p += 2
    raise RuntimeError("no free port pair")


class Tradekit(_Base):
    """The copy-trading venue standard (tradekit/1) + its scaffolder."""

    name = "tradekit"
    description = "Standard + scaffolder for copy-trading venue modules (hyperliquid framework)"
    fns = ["spec", "check", "board", "scaffold", "venues"]

    def __init__(self, **kwargs):
        pass

    def spec(self) -> Dict[str, Any]:
        """The tradekit/1 standard."""
        return standard.spec()

    def check(self, mod: str = "hyperliquid") -> Dict[str, Any]:
        """Conformance report for one module (name under orbit, or a path)."""
        return standard.check(_resolve(mod))

    def venues(self) -> List[str]:
        """Every orbit module that declares a tradekit `standard` block."""
        out = []
        for name in sorted(os.listdir(ORBIT)):
            p = os.path.join(ORBIT, name, "config.json")
            try:
                with open(p) as f:
                    cfg = json.load(f)
            except Exception:
                continue
            if str((cfg.get("standard") or {}).get("spec", "")).startswith("tradekit/"):
                out.append(name)
        return out

    def board(self) -> Dict[str, Any]:
        """Side-by-side conformance of every venue module."""
        rows = []
        for name in self.venues():
            r = standard.check(os.path.join(ORBIT, name))
            rows.append({k: r.get(k) for k in ("name", "venue", "ok", "required", "optional", "aliases", "problems")})
        return {"spec": standard.SPEC, "modules": rows,
                "ok": all(r["ok"] for r in rows) if rows else False}

    def scaffold(self, name: str, venue: str = "", currency: str = "USDC",
                 symbol: str = "market ticker", api_port: Optional[int] = None,
                 tool_prefix: str = "", force: bool = False) -> Dict[str, Any]:
        """Copy the hyperliquid framework into a NEW module orbit/<name>.

        m tradekit/scaffold name=dydx venue=dYdX symbol="BTC-USD"

        Writes config.json (private, own ports), src/mod.py (every canonical
        fn), src/api (axum skeleton — fill src/api/src/venue.rs), src/strats
        (the canonical Strat base copied from hyperliquid), and a conformance
        test. Refuses to overwrite an existing directory unless force=True."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", name or ""):
            raise ValueError("name must be lowercase [a-z0-9_], 2-31 chars")
        dest = os.path.join(ORBIT, name)
        if os.path.exists(dest):
            if not force:
                raise ValueError(f"{dest} exists — pass force=True to overwrite")
            # force only ever re-scaffolds a module tradekit made itself
            try:
                with open(os.path.join(dest, "config.json")) as f:
                    mine = "Scaffolded by tradekit" in json.load(f).get("description", "")
            except Exception:
                mine = False
            if not mine:
                raise ValueError(f"{dest} was not scaffolded by tradekit — refusing to overwrite")
            shutil.rmtree(dest)
        if api_port is None:
            api_port, app_port = _free_pair()
        else:
            api_port, app_port = int(api_port), int(api_port) + 1
        venue = venue or name.capitalize()
        prefix = tool_prefix or (name[:4] + "_")
        subs = {
            "__NAME__": name,
            "__CLASS__": "".join(p.capitalize() for p in name.split("_")),
            "__VENUE__": venue,
            "__PREFIX__": name.upper(),
            "__TOOL_PREFIX__": prefix,
            "__API_PORT__": str(api_port),
            "__APP_PORT__": str(app_port),
            "__CURRENCY__": currency,
            "__SYMBOL__": symbol,
        }
        written = []
        for dirpath, dirnames, filenames in os.walk(TEMPLATE):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "target")]
            rel = os.path.relpath(dirpath, TEMPLATE)
            out_dir = os.path.normpath(os.path.join(dest, rel))
            os.makedirs(out_dir, exist_ok=True)
            for fn in filenames:
                with open(os.path.join(dirpath, fn)) as f:
                    txt = f.read()
                for k in sorted(subs, key=len, reverse=True):
                    txt = txt.replace(k, subs[k])
                # the strat base is copied verbatim from hyperliquid — rename
                # the venue in its prose so the fork reads as its own module
                if fn.endswith(".py") and "strats" in rel:
                    txt = txt.replace("hyperliquid", name).replace("Hyperliquid", venue)
                path = os.path.join(out_dir, fn)
                with open(path, "w") as f:
                    f.write(txt)
                written.append(os.path.relpath(path, dest))
        report = standard.check(dest)
        return {"ok": report["ok"], "module": name, "dir": dest,
                "ports": {"api": api_port, "app": app_port},
                "files": sorted(written), "check": report["problems"],
                "next": f"fill src/api/src/venue.rs, then `m {name}/build` and `m {name}/serve`"}


Mod = Tradekit
