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
# A live portfolio pass signs one extrinsic per subnet it rebalances; the
# read timeout above would cut a wide book off mid-sync.
SYNC_TIMEOUT_SEC = float(os.environ.get("COPYTENSOR_MCP_SYNC_TIMEOUT", "600"))
# Strat ownership on the API is an X-Owner-Key header; an MCP client can pin
# its key here so ct_strats sees the same shelf the browser does.
OWNER_KEY = os.environ.get("COPYTENSOR_OWNER_KEY")

# Board reads are the same call for every trader the agent scores, so hold
# one copy for a beat rather than re-fetching 372 rows per lookup.
_BOARD_TTL_SEC = 30
_board_lock = threading.Lock()
_board_cache: tuple = (0.0, None)

SS58_RE = re.compile(r"^5[1-9A-HJ-NP-Za-km-z]{46,47}$")


def _request(method: str, path: str, params: Optional[Dict] = None,
             body: Any = None, headers: Optional[Dict] = None,
             timeout: Optional[float] = None) -> Any:
    """One HTTP path for every tool — reads and writes alike go through the
    running API, so the MCP surface can never drift from the REST one."""
    r = requests.request(method, f"{API_URL}{path}", params=params or None,
                         json=body, headers=headers or None,
                         timeout=timeout or TIMEOUT_SEC)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
    return r.json()


def _get(path: str, **params) -> Any:
    return _request("GET", path, params=params)


def _post(path: str, body: Any = None, timeout: Optional[float] = None,
          **params) -> Any:
    return _request("POST", path, params=params or None, body=body,
                    timeout=timeout)


def _put(path: str, body: Any = None) -> Any:
    return _request("PUT", path, body=body)


def _delete(path: str) -> Any:
    return _request("DELETE", path)


def _owner_headers(owner_key: Optional[str]) -> Dict:
    key = owner_key or OWNER_KEY
    return {"X-Owner-Key": key} if key else {}


def _clean(d: Dict) -> Dict:
    return {k: v for k, v in d.items() if v is not None}


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


# ── ops: the copy book, and syncing it ───────────────────────────
#
# These are NOT in the strat agent's toolbox (it stays read-only by
# construction — see TOOLS below). They exist for an MCP client that runs
# the engine: create/resize/pause a copy, and above all `ct_sync`, which
# applies the blended book to the chain right now.

def _copy_row(c: Dict) -> Dict:
    cfg = c.get("config") or {}
    ti = c.get("target_info") or {}
    row = {
        "id": c.get("id"), "target_ss58": c.get("target_ss58"),
        "label": c.get("label"), "status": c.get("status"),
        "alloc_tao": _num(c.get("alloc_tao"), 6),
        "our_hotkey": cfg.get("our_hotkey"),
        "max_tao_per_tx": cfg.get("max_tao_per_tx"),
        "rebalance_threshold_pct": cfg.get("rebalance_threshold_pct"),
        "poll_interval_sec": cfg.get("poll_interval_sec"),
        "subnet_allowlist": cfg.get("subnet_allowlist"),
        "subnet_denylist": cfg.get("subnet_denylist"),
        "last_sync_block": c.get("last_sync_block"),
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
    }
    if ti:
        row["target"] = {
            "label": ti.get("label"),
            "total_stake_tao": _num(ti.get("total_stake_tao"), 3),
            "num_subnets": ti.get("num_subnets"),
            "pnl_tao": _num(ti.get("pnl_tao"), 3),
            "pnl_pct": _num(ti.get("pnl_pct"), 3),
            "pnl_days": ti.get("pnl_days"),
            "top_allocations": (ti.get("top_allocations") or [])[:5],
        }
    return row


def _plan_out(p: Dict) -> Dict:
    """A portfolio plan for the wire: the book, every sleeve, every trade
    that closes the gap. Rows already carry subnet names."""
    return {
        "our_ss58": p.get("our_ss58"),
        "staked_tao": _num(p.get("staked_tao"), 6),
        "free_tao": _num(p.get("free_tao"), 6),
        "requested_tao": _num(p.get("requested_tao"), 6),
        "deployable_tao": _num(p.get("deployable_tao"), 6),
        "scale": _num(p.get("scale"), 4),
        "band_tao": _num(p.get("band_tao"), 6),
        "sleeves": p.get("sleeves") or [],
        "rows": p.get("rows") or [],
        "trades": p.get("trades"),
        "blocked": p.get("blocked"),
        "notes": p.get("notes") or [],
        "executed": bool(p.get("executed")),
        "results": p.get("results") or [],
    }


def wallet() -> Dict:
    """Whether a wallet is loaded, and its balance. Never raises on 'not
    set' — that is the answer, not an error."""
    try:
        w = _get("/wallet/balance")
    except RuntimeError as e:
        if "400" in str(e):
            return {"wallet_set": False,
                    "hint": "POST /wallet/set (or `m copytensor/set_wallet`) "
                            "loads a wallet; mnemonics never travel over MCP"}
        raise
    return {"wallet_set": True, "ss58": w.get("ss58"),
            "balance_tao": _num(w.get("balance_tao"), 6)}


def copy(copy_id: str) -> Dict:
    return _copy_row(_get(f"/copy/{copy_id}"))


def create_copy(target_ss58: str, alloc_tao: float, label: Optional[str] = None,
                our_hotkey: Optional[str] = None,
                max_tao_per_tx: Optional[float] = None,
                rebalance_threshold_pct: Optional[float] = None,
                poll_interval_sec: Optional[int] = None,
                subnet_allowlist: Optional[List[int]] = None,
                subnet_denylist: Optional[List[int]] = None) -> Dict:
    target_ss58 = (target_ss58 or "").strip()
    if not SS58_RE.match(target_ss58):
        raise ValueError(f"not an SS58 coldkey: {target_ss58[:12]!r}")
    if not alloc_tao or float(alloc_tao) <= 0:
        raise ValueError("alloc_tao must be > 0 — it is the TAO behind this trader")
    if not our_hotkey:
        w = wallet()
        if not w.get("wallet_set"):
            raise ValueError("no wallet set — load one with /wallet/set before "
                             "going live (copies need a signer)")
        our_hotkey = w["ss58"]
    body = _clean({
        "target_ss58": target_ss58, "our_hotkey": our_hotkey,
        "label": label, "alloc_tao": float(alloc_tao),
        "max_tao_per_tx": max_tao_per_tx,
        "rebalance_threshold_pct": rebalance_threshold_pct,
        "poll_interval_sec": poll_interval_sec,
        "subnet_allowlist": subnet_allowlist, "subnet_denylist": subnet_denylist,
    })
    row = _copy_row(_post("/copy", body))
    row["note"] = ("created and active — the portfolio loop picks it up on its "
                   "next pass; call ct_sync to apply the book right now")
    return row


def resize_copy(copy_id: str, alloc_tao: Optional[float] = None,
                label: Optional[str] = None,
                max_tao_per_tx: Optional[float] = None,
                rebalance_threshold_pct: Optional[float] = None,
                poll_interval_sec: Optional[int] = None) -> Dict:
    body = _clean({"alloc_tao": alloc_tao, "label": label,
                   "max_tao_per_tx": max_tao_per_tx,
                   "rebalance_threshold_pct": rebalance_threshold_pct,
                   "poll_interval_sec": poll_interval_sec})
    if not body:
        raise ValueError("nothing to change — give alloc_tao, label or a limit")
    return _copy_row(_put(f"/copy/{copy_id}", body))


def pause_copy(copy_id: str) -> Dict:
    return _post(f"/copy/{copy_id}/pause")


def resume_copy(copy_id: str) -> Dict:
    return _post(f"/copy/{copy_id}/resume")


def delete_copy(copy_id: str) -> Dict:
    out = _delete(f"/copy/{copy_id}")
    out["note"] = ("the sleeve is gone from the book; the stake it held is "
                   "unwound on the next sync, not by this call")
    return out


def portfolio() -> Dict:
    """The blended book and the trades that would close the gap. Pure read
    — identical to what ct_sync(dry_run=true) would execute."""
    out = {"plan": _plan_out(_get("/portfolio"))}
    try:
        out["loop"] = _get("/portfolio/status")
    except Exception as e:  # the plan is the answer; the loop is colour
        out["loop"] = {"error": str(e)}
    return out


def sync(copy_id: Optional[str] = None, dry_run: bool = False) -> Dict:
    """Apply the book now. Sleeves only add up when they are diffed against
    the chain together, so a copy_id is a hint about *why* — the pass always
    runs the whole portfolio. dry_run returns the same plan unsigned."""
    if dry_run:
        plan = _plan_out(_post("/portfolio/sync", dry_run="true"))
        plan["dry_run"] = True
        return plan
    if copy_id:
        out = _post(f"/copy/{copy_id}/sync", timeout=SYNC_TIMEOUT_SEC)
        out["copy_id"] = copy_id
        return out
    plan = _plan_out(_post("/portfolio/sync", timeout=SYNC_TIMEOUT_SEC))
    plan["dry_run"] = False
    return plan


def trades(limit: int = 50, copy_id: Optional[str] = None) -> Dict:
    limit = max(1, min(int(limit), 500))
    rows = _get("/trades", **_clean({"limit": limit, "copy_id": copy_id})) or []
    return {"count": len(rows), "trades": rows}


def watch(ss58: str, label: Optional[str] = None) -> Dict:
    ss58 = (ss58 or "").strip()
    if not SS58_RE.match(ss58):
        raise ValueError(f"not an SS58 coldkey: {ss58[:12]!r}")
    return _post("/watch", {"ss58": ss58, "label": label})


def unwatch(ss58: str) -> Dict:
    return _delete(f"/watch/{(ss58 or '').strip()}")


def watches() -> Dict:
    rows = _get("/watches")
    if isinstance(rows, dict):
        return rows
    return {"count": len(rows), "watches": rows}


def strats(owner_key: Optional[str] = None) -> Dict:
    """Your saved strats (plus public + whitelisted-to-you). Needs the
    browser's owner key to see private ones — pass it or set
    COPYTENSOR_OWNER_KEY."""
    out = _request("GET", "/strats", headers=_owner_headers(owner_key))
    for s in out.get("strats") or []:
        s.pop("thesis", None)
    return out


def backtest(traders: List[Dict], days: int = 7, capital_tao: float = 100.0) -> Dict:
    if not isinstance(traders, list) or not traders:
        raise ValueError("traders must be a non-empty list of {ss58, weight|alloc_tao}")
    rows = []
    for t in traders:
        ss58 = str((t or {}).get("ss58") or "").strip()
        if not SS58_RE.match(ss58):
            raise ValueError(f"not an SS58 coldkey: {ss58[:12]!r}")
        rows.append(_clean({"ss58": ss58, "label": t.get("label"),
                            "weight": t.get("weight", 1.0),
                            "alloc_tao": t.get("alloc_tao")}))
    return _post("/strats/backtest", {"traders": rows, "days": int(days),
                                      "capital_tao": float(capital_tao)},
                 timeout=SYNC_TIMEOUT_SEC)


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
        # A basket can be sized either way. `alloc_tao` is an explicit sleeve
        # (the unit the live engine deploys in); `weight` is a relative share
        # of the pot. Whichever is given becomes the weight here, and the τ is
        # resolved below once the total is known.
        alloc = t.get("alloc_tao")
        alloc = float(alloc) if alloc not in (None, "") else None
        if alloc is not None and alloc < 0:
            raise ValueError(f"alloc_tao must be >= 0 for {ss58[:8]}")
        weight = alloc if alloc else float(t.get("weight", 1) or 0)
        if weight <= 0:
            raise ValueError(
                f"give {ss58[:8]} either a weight > 0 or an alloc_tao > 0")
        b = board.get(ss58) or {}
        rows.append({
            "ss58": ss58,
            "label": t.get("label") or b.get("label"),
            "weight": round(weight, 6),
            "alloc_tao": round(alloc, 6) if alloc else None,
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
    # If every leg carries its own τ, the pot IS their sum — otherwise the
    # card would show a capital figure the sleeves don't add up to.
    sleeved = sum(r["alloc_tao"] or 0 for r in rows)
    if sleeved > 0 and all(r["alloc_tao"] for r in rows):
        capital_tao = sleeved
    for r in rows:
        r["share_pct"] = round(r["weight"] / total * 100, 2)
        # Resolve the τ for every leg, so the card always says what each
        # trader gets in money — not only when the agent thought in τ.
        if not r["alloc_tao"]:
            r["alloc_tao"] = round(
                max(1.0, float(capital_tao)) * r["weight"] / total, 6)

    strat = {
        "name": name.strip() or "Agent strat",
        "thesis": thesis.strip(),
        "traders": rows,
        "sizing": "tao" if sleeved > 0 else "split",
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
         "activates. Size it either way: give every trader an `alloc_tao` "
         "(the TAO behind them, which is what the live engine deploys) or "
         "relative `weight`s that get normalised against capital_tao. Use "
         "alloc_tao when the human names amounts.",
         {"name": _p("string", "Short name for the strat."),
          "thesis": _p("string", "One or two sentences: what this basket is "
                       "betting on and why these traders express it."),
          "traders": _p("array", "The basket.", items={
              "type": "object",
              "properties": {
                  "ss58": {"type": "string", "description": "Coldkey SS58."},
                  "weight": {"type": "number", "description": "Relative weight, > 0. Ignored when alloc_tao is set."},
                  "alloc_tao": {"type": "number", "description": "Absolute TAO behind this trader. Use this when the human names amounts."},
                  "why": {"type": "string", "description": "One line: why this trader."},
              },
              "required": ["ss58"]}),
          "capital_tao": _p("number", "Total TAO behind the basket. Ignored "
                            "when every trader carries its own alloc_tao.",
                            default=100),
          "max_tao_per_tx": _p("number", "Ceiling on any single stake/unstake.",
                               default=10),
          "rebalance_threshold_pct": _p("number", "Only mirror an allocation "
                                        "gap wider than this.", default=5),
          "poll_interval_sec": _p("integer", "How often each copy re-syncs.",
                                  default=300)},
         propose_strat),
]

# The tool whose result is a strat, not a reading — the driver watches for it.
STRAT_TOOL = "propose_strat"

_SS58 = _p("string", "Coldkey SS58 address.")
_COPY_ID = _p("string", "Copy id, as returned by ct_copies / ct_create_copy.")

# Ops tools: everything that moves the copy book. Served over MCP (HTTP and
# stdio) but never handed to the strat agent — `list_tools(scope="agent")`
# is what it sees, and `agent.py` allow-lists only those names.
OPS_TOOLS: List[Tool] = [
    Tool("ct_wallet", "Whether a signing wallet is loaded, and its TAO balance. "
         "Copies and syncs need one; loading it is a REST/console action, "
         "never an MCP argument.", {}, wallet),
    Tool("ct_copy", "One copy config in full: sizing, limits, sync state, and "
         "the target trader's current book.", {"copy_id": _COPY_ID}, copy),
    Tool("ct_create_copy",
         "Start mirroring a trader: alloc_tao is the TAO that follows them. "
         "Becomes a sleeve of the blended book; the loop applies it on its "
         "next pass, or call ct_sync to apply now. Needs a wallet (ct_wallet).",
         {"target_ss58": _SS58,
          "alloc_tao": _p("number", "TAO behind this trader (> 0)."),
          "label": _p("string", "Display name.", default=None),
          "our_hotkey": _p("string", "Hotkey to stake through; defaults to "
                           "the loaded wallet.", default=None),
          "max_tao_per_tx": _p("number", "Ceiling on any single stake/unstake.",
                               default=None),
          "rebalance_threshold_pct": _p("number", "Only mirror an allocation "
                                        "gap wider than this.", default=None),
          "poll_interval_sec": _p("integer", "Seconds between passes.",
                                  default=None),
          "subnet_allowlist": _p("array", "Only these netuids.",
                                 items={"type": "integer"}, default=None),
          "subnet_denylist": _p("array", "Never these netuids.",
                                items={"type": "integer"}, default=None)},
         create_copy),
    Tool("ct_resize_copy", "Re-size or re-label a live copy. Changing alloc_tao "
         "re-weights the book on the next pass — no stop/start.",
         {"copy_id": _COPY_ID,
          "alloc_tao": _p("number", "New TAO behind this trader.", default=None),
          "label": _p("string", "New display name.", default=None),
          "max_tao_per_tx": _p("number", "Per-tx ceiling.", default=None),
          "rebalance_threshold_pct": _p("number", "Drift tolerance.", default=None),
          "poll_interval_sec": _p("integer", "Seconds between passes.",
                                  default=None)},
         resize_copy),
    Tool("ct_pause_copy", "Pause a copy: its sleeve drops out of the book until "
         "resumed.", {"copy_id": _COPY_ID}, pause_copy),
    Tool("ct_resume_copy", "Resume a paused copy.", {"copy_id": _COPY_ID},
         resume_copy),
    Tool("ct_delete_copy", "Remove a copy. The stake it held unwinds on the "
         "next sync, not by this call.", {"copy_id": _COPY_ID}, delete_copy),
    Tool("ct_portfolio", "The blended book: every sleeve, what it asks for, "
         "and the trades that would close the gap. Pure read — the exact "
         "plan ct_sync would execute.", {}, portfolio),
    Tool("ct_sync",
         "SYNC the book to the chain now: diff every active sleeve against "
         "our stake and sign the stake/unstake extrinsics that close the gap. "
         "Always runs the whole portfolio (a copy_id only names the reason). "
         "dry_run=true returns the same plan with nothing signed — call that "
         "first. Needs a wallet; can take minutes on a wide book.",
         {"copy_id": _p("string", "Optional copy id this sync is for.",
                        default=None),
          "dry_run": _p("boolean", "Preview only — no extrinsics.",
                        default=False)},
         sync),
    Tool("ct_trades", "The copy engine's trade history — what it staked or "
         "unstaked, when, and whether it landed.",
         {"limit": _p("integer", "Max rows (≤ 500).", default=50),
          "copy_id": _p("string", "Only this copy's trades.", default=None)},
         trades),
    Tool("ct_watch", "Track a coldkey: it joins the watchlist and the bt index "
         "starts snapshotting it.", {"ss58": _SS58,
                                    "label": _p("string", "Display name.",
                                                default=None)}, watch),
    Tool("ct_unwatch", "Stop tracking a coldkey.", {"ss58": _SS58}, unwatch),
    Tool("ct_watches", "The watchlist.", {}, watches),
    Tool("ct_strats", "Saved strats — yours (with an owner key), plus every "
         "public and whitelisted-to-you one.",
         {"owner_key": _p("string", "The browser's X-Owner-Key; defaults to "
                          "COPYTENSOR_OWNER_KEY.", default=None)}, strats),
    Tool("ct_backtest", "Replay a basket over the last N days off the bt "
         "index: equity curve, stats, per-trader contribution. No id needed "
         "— the basket travels inline.",
         {"traders": _p("array", "The basket.", items={
              "type": "object",
              "properties": {
                  "ss58": {"type": "string", "description": "Coldkey SS58."},
                  "weight": {"type": "number", "description": "Relative weight."},
                  "alloc_tao": {"type": "number", "description": "Absolute TAO sleeve."},
                  "label": {"type": "string"}},
              "required": ["ss58"]}),
          "days": _p("integer", "Window in days (≤ 365).", default=7),
          "capital_tao": _p("number", "Pot behind the basket when legs carry "
                            "weights rather than alloc_tao.", default=100)},
         backtest),
]

ALL_TOOLS: List[Tool] = TOOLS + OPS_TOOLS
BY_NAME: Dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
SCOPES: Dict[str, List[Tool]] = {"agent": TOOLS, "ops": OPS_TOOLS, "all": ALL_TOOLS}


def list_tools(scope: str = "all") -> List[Dict]:
    return [t.schema() for t in SCOPES.get(scope, ALL_TOOLS)]


def call_tool(name: str, args: Optional[Dict] = None, scope: str = "all") -> Any:
    tool = BY_NAME.get(name)
    if not tool or tool not in SCOPES.get(scope, ALL_TOOLS):
        raise ValueError(f"unknown tool: {name}")
    kwargs = dict(args or {})
    unknown = set(kwargs) - set(tool.params)
    if unknown:
        raise ValueError(f"unknown arguments for {name}: {', '.join(sorted(unknown))}")
    return tool.handler(**kwargs)
