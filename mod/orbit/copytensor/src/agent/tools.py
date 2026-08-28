"""
copytensor.agent.tools — the read-only toolbox the strat agent plays.

Every tool is a thin, *trimmed* call against copytensor's own REST API, so
the agent sees exactly the numbers the console shows and there is one code
path for caching, the bt index and the RPC fallback. Rows are cut down to
the fields a strat decision actually turns on — a raw /traders is 372 rows
of sparklines, which is a token bill and no more signal.

The one tool that is not a read is `propose_strat`: it validates a basket
and hands it back. Nothing here can sign, stake or start a copy — going
live stays a human click in the strat maker.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

API_URL = os.environ.get("COPYTENSOR_API_URL", "http://localhost:50150")
TIMEOUT_SEC = float(os.environ.get("COPYTENSOR_AGENT_HTTP_TIMEOUT", "45"))

# Board reads are the same call for every trader the agent scores, so hold
# one copy for a beat rather than re-fetching 372 rows per lookup.
_BOARD_TTL_SEC = 30
_board_lock = threading.Lock()
_board_cache: tuple = (0.0, None)

SS58_RE = re.compile(r"^5[1-9A-HJ-NP-Za-km-z]{46,47}$")


def _get(path: str, **params) -> Any:
    r = requests.get(f"{API_URL}{path}", params=params or None, timeout=TIMEOUT_SEC)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} -> {r.status_code} {r.text[:200]}")
    return r.json()


def _num(x, nd: int = 4):
    """Round for the wire — full float precision is noise in a prompt."""
    return round(x, nd) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _board(force: bool = False) -> List[Dict]:
    """The tracked-trader board, briefly cached."""
    global _board_cache
    with _board_lock:
        ts, rows = _board_cache
        if rows is not None and not force and time.time() - ts < _BOARD_TTL_SEC:
            return rows
    rows = (_get("/traders", sort_by="total_tao") or {}).get("rows") or []
    with _board_lock:
        _board_cache = (time.time(), rows)
    return rows


# ── handlers ─────────────────────────────────────────────────────

def status() -> Dict:
    return _get("/status")


def market() -> Dict:
    m = _get("/market", movers=5)
    keep = ("netuid", "name", "symbol", "alpha_price_tao", "change_24h", "change_7d")
    return {
        "subnets": m.get("subnets"),
        "total_market_cap_tao": _num(m.get("total_market_cap_tao"), 1),
        "volume_24h_tao": _num(m.get("volume_24h_tao"), 1),
        "block": m.get("block"),
        "tao_usd": m.get("tao_usd"),
        "gainers": [{k: _num(g.get(k), 6) for k in keep} for g in m.get("gainers", [])],
        "losers": [{k: _num(l.get(k), 6) for k in keep} for l in m.get("losers", [])],
    }


SUBNET_SORTS = {
    "market_cap": "market_cap_tao",
    "volume": "vol_24h_tao",
    "change_1h": "change_1h",
    "change_24h": "change_24h",
    "change_7d": "change_7d",
    "price": "alpha_price_tao",
}


def subnets(sort_by: str = "market_cap", limit: int = 25) -> Dict:
    key = SUBNET_SORTS.get(sort_by, "market_cap_tao")
    limit = max(1, min(int(limit), 128))
    rows = _get("/subnets") or []
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))
    keep = ("netuid", "name", "symbol", "alpha_price_tao", "change_1h",
            "change_24h", "change_7d", "market_cap_tao", "vol_24h_tao")
    return {"count": len(rows), "sorted_by": key,
            "subnets": [{k: _num(r.get(k), 6) for k in keep} for r in rows[:limit]]}


TRADER_SORTS = ("pnl_7d", "change_7d", "pnl_24h", "change_24h", "total_tao",
                "staked_tao", "subnets", "flows_24h")


def traders(sort_by: str = "change_7d", limit: int = 30,
            min_total_tao: float = 0.0) -> Dict:
    """The board copytensor ranks traders off — the pool a basket is drawn from."""
    if sort_by not in TRADER_SORTS:
        raise ValueError(f"sort_by must be one of {', '.join(TRADER_SORTS)}")
    limit = max(1, min(int(limit), 200))
    rows = [r for r in _board() if (r.get("total_tao") or 0) >= min_total_tao]
    # Traders with no history for this window sort last, not first — a null
    # 7d change is "unknown", never "flat".
    rows.sort(key=lambda r: (r.get(sort_by) is None, -(r.get(sort_by) or 0)))
    out = []
    for r in rows[:limit]:
        out.append({
            "ss58": r.get("ss58"),
            "label": r.get("label"),
            "total_tao": _num(r.get("total_tao"), 1),
            "staked_tao": _num(r.get("staked_tao"), 1),
            "subnets": r.get("subnets"),
            "change_24h": _num(r.get("change_24h"), 3),
            "change_7d": _num(r.get("change_7d"), 3),
            "pnl_24h": _num(r.get("pnl_24h"), 1),
            "pnl_7d": _num(r.get("pnl_7d"), 1),
            "flows_24h": r.get("flows_24h"),
            "top_subnets": [
                {"netuid": s.get("netuid"), "value_tao": _num(s.get("value_tao"), 1)}
                for s in (r.get("top_subnets") or [])[:3]
            ],
        })
    return {"tracked": len(rows), "sorted_by": sort_by, "traders": out,
            "note": "change_* are percent moves in portfolio value; pnl_* are TAO"}


def trader(ss58: str, hours: int = 168, positions: int = 12,
           flows: int = 15) -> Dict:
    """One trader's book: what they hold, and what they've been doing."""
    p = _get(f"/traders/{ss58}", hours=hours)
    return {
        "ss58": p.get("ss58"),
        "label": p.get("label"),
        "tracked": p.get("tracked"),
        "total_tao": _num(p.get("total_tao"), 1),
        "free_tao": _num(p.get("free_tao"), 1),
        "staked_tao": _num(p.get("staked_tao"), 1),
        "subnets": p.get("subnets"),
        "change_24h": _num(p.get("change_24h"), 3),
        "change_7d": _num(p.get("change_7d"), 3),
        "pnl_24h": _num(p.get("pnl_24h"), 1),
        "pnl_7d": _num(p.get("pnl_7d"), 1),
        "positions": [
            {"netuid": x.get("netuid"), "name": x.get("name"),
             "value_tao": _num(x.get("value_tao"), 1),
             "pct_of_total": _num(x.get("pct_of_total"), 2)}
            for x in (p.get("positions") or [])[:positions]
        ],
        "recent_flows": [_flow(f) for f in (p.get("flows") or [])[:flows]],
    }


def _flow(f: Dict) -> Dict:
    return {"ts": f.get("ts"), "netuid": f.get("netuid"), "name": f.get("name"),
            "side": f.get("side"), "tao_value": _num(f.get("tao_value"), 2),
            "ss58": f.get("ss58")}


def trader_flows(ss58: str, hours: int = 168, limit: int = 40) -> Dict:
    d = _get(f"/traders/{ss58}/flows", hours=hours, limit=limit)
    return {"ss58": ss58, "hours": hours, "count": d.get("count"),
            "flows": [_flow(f) for f in (d.get("flows") or [])]}


def flows(hours: int = 24, limit: int = 50) -> Dict:
    """The tape across every tracked trader — who is buying what, right now."""
    d = _get("/flows", hours=hours, limit=limit)
    return {"hours": hours, "count": d.get("count"), "note": d.get("note"),
            "flows": [_flow(f) for f in (d.get("flows") or [])]}


def leaderboard(days: int = 7, top: int = 25) -> Dict:
    rows = _get("/leaderboard", days=days, top=top) or []
    if not rows:
        return {"days": days, "entries": [],
                "note": "board still building for this horizon — rank off "
                        "ct_traders (the same index, already warm) instead"}
    return {"days": days, "entries": [{
        "ss58": r.get("ss58"), "label": r.get("label"),
        "total_stake_tao": _num(r.get("total_stake_tao"), 1),
        "pnl_tao": _num(r.get("pnl_tao"), 1),
        "pnl_pct": _num(r.get("pnl_pct"), 3),
        "market_pnl_tao": _num(r.get("market_pnl_tao"), 1),
        "flow_tao": _num(r.get("flow_tao"), 1),
        "num_subnets": r.get("num_subnets"),
        "top_subnet": r.get("top_subnet"),
        "window_days": _num(r.get("window_days"), 2),
    } for r in rows],
        "note": "pnl splits into market_pnl_tao (price move on the book held) "
                "and flow_tao (stake deposited/withdrawn) — rank on the first"}


def copies() -> Dict:
    """What is already running, so a new strat doesn't duplicate it."""
    rows = _get("/copies") or []
    return {"count": len(rows), "copies": [{
        "id": c.get("id"), "target_ss58": c.get("target_ss58"),
        "label": c.get("label"), "status": c.get("status"),
        "daily_limit_tao": (c.get("config") or {}).get("daily_limit_tao"),
    } for c in rows]}


# ── the deliverable ──────────────────────────────────────────────

def propose_strat(name: str, thesis: str, traders: List[Dict],
                  capital_tao: float = 100.0, max_tao_per_tx: float = 10.0,
                  rebalance_threshold_pct: float = 5.0,
                  poll_interval_sec: int = 300) -> Dict:
    """Validate a basket and hand it to the console as a saveable strat."""
    if not isinstance(traders, list) or not traders:
        raise ValueError("traders must be a non-empty list of {ss58, weight, why}")
    if len(traders) > 100:
        raise ValueError("at most 100 traders in one basket")

    board = {r.get("ss58"): r for r in _board()}
    rows: List[Dict] = []
    seen = set()
    for t in traders:
        if not isinstance(t, dict):
            raise ValueError("each trader must be an object with ss58 and weight")
        ss58 = str(t.get("ss58") or "").strip()
        if not SS58_RE.match(ss58):
            raise ValueError(f"not an SS58 coldkey: {ss58[:12]!r}")
        if ss58 in seen:
            continue
        seen.add(ss58)
        weight = float(t.get("weight", 1) or 0)
        if weight <= 0:
            raise ValueError(f"weight must be > 0 for {ss58[:8]}")
        b = board.get(ss58) or {}
        rows.append({
            "ss58": ss58,
            "label": t.get("label") or b.get("label"),
            "weight": round(weight, 6),
            "why": (t.get("why") or "").strip() or None,
            # Live stats so the card can render the basket without a second
            # round-trip, and so the agent sees what it actually picked.
            "tracked": ss58 in board,
            "total_tao": _num(b.get("total_tao"), 1),
            "change_7d": _num(b.get("change_7d"), 3),
            "pnl_7d": _num(b.get("pnl_7d"), 1),
            "subnets": b.get("subnets"),
        })

    total = sum(r["weight"] for r in rows)
    for r in rows:
        r["share_pct"] = round(r["weight"] / total * 100, 2)

    strat = {
        "name": name.strip() or "Agent strat",
        "thesis": thesis.strip(),
        "traders": rows,
        "capital_tao": max(1.0, float(capital_tao)),
        "max_tao_per_tx": max(0.1, float(max_tao_per_tx)),
        "rebalance_threshold_pct": max(0.1, float(rebalance_threshold_pct)),
        "poll_interval_sec": max(60, int(poll_interval_sec)),
    }
    untracked = [r["ss58"] for r in rows if not r["tracked"]]
    if untracked:
        strat["warning"] = (
            f"{len(untracked)} coldkey(s) are not in the tracked index — no "
            "history to rank them on: " + ", ".join(s[:8] for s in untracked))
    strat["delivered"] = (
        "Proposed. It is now a card in the console — the human saves it to "
        "the strat library and clicks ACTIVATE to go live. Do not propose the "
        "same basket twice; refine it only if asked.")
    return strat


# ── registry ─────────────────────────────────────────────────────

class Tool:
    def __init__(self, name: str, description: str, params: Dict[str, Dict],
                 handler: Callable[..., Any]):
        self.name = name
        self.description = description
        self.params = params
        self.handler = handler

    def schema(self) -> Dict:
        props, required = {}, []
        for pname, spec in self.params.items():
            p = {"type": spec["type"], "description": spec["description"]}
            for k in ("items", "enum"):
                if k in spec:
                    p[k] = spec[k]
            if "default" in spec:
                p["default"] = spec["default"]
            else:
                required.append(pname)
            props[pname] = p
        return {"name": self.name, "description": self.description,
                "inputSchema": {"type": "object", "properties": props,
                                "required": required}}


def _p(type_: str, description: str, **rest) -> Dict:
    return {"type": type_, "description": description, **rest}


TOOLS: List[Tool] = [
    Tool("ct_status", "Engine state: block height, tracked accounts, active "
         "copies, and whether reads come from the bt index or raw RPC.",
         {}, status),
    Tool("ct_market", "Network totals (alpha market cap, 24h volume, TAO/USD) "
         "plus the top subnet gainers and losers.", {}, market),
    Tool("ct_subnets", "Subnet screener — price, 1h/24h/7d change, market cap "
         "and 24h volume for every subnet.",
         {"sort_by": _p("string", "Ranking field.", default="market_cap",
                        enum=list(SUBNET_SORTS)),
          "limit": _p("integer", "Rows to return (max 128).", default=25)},
         subnets),
    Tool("ct_traders", "The tracked-trader board — every coldkey copytensor "
         "indexes, with portfolio value, subnet count and windowed PnL. This "
         "is the pool a basket is picked from.",
         {"sort_by": _p("string", "Ranking field.", default="change_7d",
                        enum=list(TRADER_SORTS)),
          "limit": _p("integer", "Rows to return (max 200).", default=30),
          "min_total_tao": _p("number", "Drop books smaller than this, in TAO.",
                              default=0)},
         traders),
    Tool("ct_trader", "One trader in full: positions by subnet with percent of "
         "book, windowed PnL, and their most recent inferred trades.",
         {"ss58": _p("string", "Coldkey SS58 address."),
          "hours": _p("integer", "History window.", default=168)},
         trader),
    Tool("ct_trader_flows", "One trader's inferred buys and sells over a window.",
         {"ss58": _p("string", "Coldkey SS58 address."),
          "hours": _p("integer", "History window.", default=168),
          "limit": _p("integer", "Max flows.", default=40)},
         trader_flows),
    Tool("ct_flows", "The tape across every tracked trader — who is buying or "
         "selling which subnet, most recent first.",
         {"hours": _p("integer", "History window.", default=24),
          "limit": _p("integer", "Max flows.", default=50)},
         flows),
    Tool("ct_leaderboard", "Traders ranked by PnL over a horizon, split into "
         "market move vs stake flow. Falls back to a note when the horizon is "
         "still building.",
         {"days": _p("integer", "Horizon in days.", default=7),
          "top": _p("integer", "Rows to return.", default=25)},
         leaderboard),
    Tool("ct_copies", "Copy configs already running, so a new strat does not "
         "duplicate one.", {}, copies),
    Tool("propose_strat",
         "Deliver a finished strat: a weighted basket of traders to mirror. "
         "Call this once you have picked the traders and can justify each "
         "one. It does NOT go live — it renders as a card the human saves and "
         "activates. Weights are relative; they get normalised to shares.",
         {"name": _p("string", "Short name for the strat."),
          "thesis": _p("string", "One or two sentences: what this basket is "
                       "betting on and why these traders express it."),
          "traders": _p("array", "The basket.", items={
              "type": "object",
              "properties": {
                  "ss58": {"type": "string", "description": "Coldkey SS58."},
                  "weight": {"type": "number", "description": "Relative weight, > 0."},
                  "why": {"type": "string", "description": "One line: why this trader."},
              },
              "required": ["ss58", "weight"]}),
          "capital_tao": _p("number", "Total TAO the basket may deploy per day.",
                            default=100),
          "max_tao_per_tx": _p("number", "Ceiling on any single stake/unstake.",
                               default=10),
          "rebalance_threshold_pct": _p("number", "Only mirror an allocation "
                                        "gap wider than this.", default=5),
          "poll_interval_sec": _p("integer", "How often each copy re-syncs.",
                                  default=300)},
         propose_strat),
]

BY_NAME: Dict[str, Tool] = {t.name: t for t in TOOLS}

# The tool whose result is a strat, not a reading — the driver watches for it.
STRAT_TOOL = "propose_strat"


def list_tools() -> List[Dict]:
    return [t.schema() for t in TOOLS]


def call_tool(name: str, args: Optional[Dict] = None) -> Any:
    tool = BY_NAME.get(name)
    if not tool:
        raise ValueError(f"unknown tool: {name}")
    kwargs = dict(args or {})
    unknown = set(kwargs) - set(tool.params)
    if unknown:
        raise ValueError(f"unknown arguments for {name}: {', '.join(sorted(unknown))}")
    return tool.handler(**kwargs)
