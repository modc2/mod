"""
tradekit.standard — the one shape every copy-trading venue module shares.

The hyperliquid module is the reference framework: a Python orchestrator
(src/mod.py) in front of a Rust axum API (src/api/) and a Next.js app
(src/app/), with canonical Strat classes under src/strats/ and an MCP tool
server over the same fn surface. This file pins that shape as STANDARD so
hyperliquid, polymarket and every module scaffolded from the template can
be checked against each other.

What is standardized is the MOD-PROTOCOL SURFACE — fn names, config.json
keys, layout — not the REST routes underneath. A module may reach a
canonical fn through its own native one: config.json `standard.aliases`
maps canonical -> native (e.g. polymarket `backtest_strats` -> `backtests`).
The strat contract (setup/sync/signal/execute/tick/backtest/teardown/state)
is pinned separately by orbit/strat's protocol.py.

check() is static: it reads config.json and parses src/mod.py with ast, so
it never imports a module, opens a socket or touches a wallet.
"""

from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List, Optional

SPEC = "tradekit/1"

# Canonical fn groups. Names are hyperliquid's (the reference framework).
REQUIRED: Dict[str, List[str]] = {
    "lifecycle": ["serve", "kill", "status", "build", "logs", "test", "forward"],
    "market": ["markets"],
    "traders": ["top_traders", "analyze_trader"],
    "strats": ["list_strats", "backtest_strats"],
    "live": ["live_start", "live_stop", "live_status"],
    "mcp": ["mcp", "mcp_tools", "mcp_call", "mcp_config"],
    "provenance": ["build_cid", "publish_build", "build_onchain"],
}

OPTIONAL: Dict[str, List[str]] = {
    "identity": ["whoami"],
    "traders": ["trader_curve", "backtest_trader", "leaderboard", "scout"],
    "strats": ["strat", "run_strat", "strats_board"],
    "agent": ["ask", "ask_status", "chat"],
    "sync": ["sync", "sync_status"],
    "market": ["candles", "orderbook", "user_fills", "user_state"],
}

# config.json keys every venue module carries.
CONFIG_KEYS = ["name", "version", "description", "fns", "ports", "urls", "mcp", "standard"]
URL_KEYS = ["api", "app", "gateway"]
PORT_KEYS = ["api", "app"]
MCP_KEYS = ["tool_prefix"]
STANDARD_KEYS = ["spec", "venue", "currency", "symbol"]

# Paths relative to the module root.
LAYOUT_REQUIRED = ["config.json", "src/mod.py", "src/api", "src/strats"]
LAYOUT_OPTIONAL = ["src/app", "src/agent.py", "tests", "README.md", "skill.md"]

# Live-money rules every venue module honours (documented, and the template
# enforces them in code): starting is dry run unless explicitly asked,
# stopping is never gated.
SAFETY = [
    "live_start defaults to DRY RUN — real orders need an explicit auto_execute=True",
    "live_stop is never gated: reducing exposure always works",
    "no fn holds a key it was not given; wallet-scoped calls carry the caller's token",
]


def required_fns() -> List[str]:
    return [f for g in REQUIRED.values() for f in g]


def optional_fns() -> List[str]:
    return [f for g in OPTIONAL.values() for f in g]


def spec() -> Dict[str, Any]:
    return {
        "spec": SPEC,
        "reference": "hyperliquid",
        "required": REQUIRED,
        "optional": OPTIONAL,
        "config_keys": CONFIG_KEYS,
        "urls": URL_KEYS,
        "ports": PORT_KEYS,
        "mcp": MCP_KEYS,
        "standard_block": STANDARD_KEYS,
        "layout": {"required": LAYOUT_REQUIRED, "optional": LAYOUT_OPTIONAL},
        "safety": SAFETY,
    }


def _class_methods(mod_py: str) -> Dict[str, Any]:
    """Methods + the `fns` list of the Mod class in mod_py, by AST."""
    with open(mod_py) as f:
        tree = ast.parse(f.read(), filename=mod_py)
    best: Optional[ast.ClassDef] = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            has_fns = any(isinstance(s, ast.Assign) and any(
                getattr(t, "id", None) == "fns" for t in s.targets) for s in node.body)
            if has_fns or best is None:
                best = node
                if has_fns:
                    break
    if best is None:
        return {"class": None, "methods": set(), "fns": []}
    methods = {s.name for s in best.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
    fns: List[str] = []
    for s in best.body:
        if isinstance(s, ast.Assign) and any(getattr(t, "id", None) == "fns" for t in s.targets):
            try:
                fns = list(ast.literal_eval(s.value))
            except Exception:
                fns = []
    return {"class": best.name, "methods": methods, "fns": fns}


def check(mod_dir: str) -> Dict[str, Any]:
    """Conformance report for one module directory. `ok` means every
    required fn, config key and layout path is present and the class, its
    `fns` list and config.json agree on the canonical surface."""
    problems: List[str] = []
    cfg_path = os.path.join(mod_dir, "config.json")
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except Exception as e:
        return {"ok": False, "dir": mod_dir, "problems": [f"config.json unreadable: {e}"]}

    for k in CONFIG_KEYS:
        if k not in cfg:
            problems.append(f"config.json missing `{k}`")
    for k in URL_KEYS:
        if k not in (cfg.get("urls") or {}):
            problems.append(f"config.json urls missing `{k}`")
    for k in PORT_KEYS:
        if k not in (cfg.get("ports") or {}):
            problems.append(f"config.json ports missing `{k}`")
    for k in MCP_KEYS:
        if k not in (cfg.get("mcp") or {}):
            problems.append(f"config.json mcp missing `{k}`")
    std = cfg.get("standard") or {}
    for k in STANDARD_KEYS:
        if k not in std:
            problems.append(f"config.json standard missing `{k}`")
    if std and std.get("spec") != SPEC:
        problems.append(f"standard.spec is {std.get('spec')!r}, expected {SPEC!r}")
    aliases: Dict[str, str] = dict(std.get("aliases") or {})

    for p in LAYOUT_REQUIRED:
        if not os.path.exists(os.path.join(mod_dir, p)):
            problems.append(f"layout missing {p}")

    mod_py = os.path.join(mod_dir, "src", "mod.py")
    cls = _class_methods(mod_py) if os.path.exists(mod_py) else {"class": None, "methods": set(), "fns": []}
    cfg_fns = set(cfg.get("fns") or [])
    cls_fns = set(cls["fns"])

    fns_report: Dict[str, Any] = {}
    for canon in required_fns() + optional_fns():
        native = aliases.get(canon, canon)
        has_method = native in cls["methods"]
        exposed = native in cls_fns and native in cfg_fns
        fns_report[canon] = {"native": native, "method": has_method, "exposed": exposed}
        if canon in required_fns():
            if not has_method:
                problems.append(f"required fn `{canon}`" + (f" (alias `{native}`)" if native != canon else "")
                                + " has no method on the Mod class")
            elif not exposed:
                where = [w for w, s in (("class fns", cls_fns), ("config.json fns", cfg_fns)) if native not in s]
                problems.append(f"required fn `{native}` not listed in {' or '.join(where)}")

    drift = sorted(cls_fns ^ cfg_fns)
    if drift:
        problems.append("class fns and config.json fns disagree: " + ", ".join(drift))

    req = required_fns()
    have = sum(1 for f in req if fns_report[f]["method"] and fns_report[f]["exposed"])
    opt = optional_fns()
    have_opt = sum(1 for f in opt if fns_report[f]["method"] and fns_report[f]["exposed"])
    return {
        "ok": not problems,
        "name": cfg.get("name"),
        "dir": mod_dir,
        "spec": std.get("spec"),
        "venue": std.get("venue"),
        "required": f"{have}/{len(req)}",
        "optional": f"{have_opt}/{len(opt)}",
        "aliases": aliases,
        "fns": fns_report,
        "problems": problems,
    }
