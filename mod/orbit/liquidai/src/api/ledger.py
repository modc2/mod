"""The call ledger — every call this module answers, written down once.

Three runtimes mean three different bills: a browser run costs the visitor's
battery, a server run costs this box's CPU, a cloud run costs someone's key.
Until now none of that was recorded anywhere, so "which provider is actually
carrying the traffic" had no answer. This module is that answer: one append-only
line per call, with the provider that did the work, the model, who asked, how
long it took and how many tokens came back.

Two rules the shape follows:

    SHAPES, NOT CONTENTS.  A prompt is the user's, not the operator's. The
    ledger holds message counts and token counts and never the text — there is
    no line here that leaks what someone asked, which is what makes it safe to
    leave on by default.

    A STREAM IS MEASURED WHEN IT ENDS.  /chat returns its response object in
    milliseconds and then streams for a minute. Closing the record at response
    time would write "3 ms, 0 tokens" for every generation on the box, so a
    streaming route defers its record and the generator closes it.

Browser runs never touch this server at all — the tab reports them afterwards
(`reported: true`), and the ledger keeps that distinction rather than pretending
it measured something it never saw.

Storage is ~/.mod/liquidai/calls.jsonl, rotated at 8 MB, with the tail also held
in memory so the console's polling never re-reads the file.
"""

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Optional

STORE = os.path.expanduser("~/.mod/liquidai")
LEDGER_PATH = os.path.join(STORE, "calls.jsonl")
ROTATED_PATH = os.path.join(STORE, "calls.1.jsonl")
MAX_BYTES = 8 * 1024 * 1024
MEMORY = 3000            # rows kept hot for the console

_LOCK = threading.Lock()
_RECENT: deque = deque(maxlen=MEMORY)
_LOADED = False

# Which provider does the work behind each route, when the handler doesn't say.
# Anything that reads the catalog or pulls weights is HuggingFace's traffic,
# not ours — that's a provider this module depends on and had never shown.
_ROUTE_PROVIDER = {
    "/models": "huggingface",
    "/local/pull": "huggingface",
    "/local/models": "server",
    "/local/load": "server",
    "/local/unload": "server",
    "/local/pulls": "server",
    "/embed": "server",
    "/transcribe": "server",
    "/cloud/models": "cloud",
    "/v1/embeddings": "server",
}

PROVIDERS = ("browser", "server", "cloud", "huggingface", "liquidai")


# ── paths and files ──────────────────────────────────────────────────

def _load_tail():
    """Seed the hot ring from disk, so a restart doesn't blank the console."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(LEDGER_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 2 * 1024 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return
    for line in lines[-MEMORY:]:
        try:
            _RECENT.append(json.loads(line))
        except Exception:
            continue


def _append(row: Dict[str, Any]):
    os.makedirs(STORE, exist_ok=True)
    try:
        if os.path.exists(LEDGER_PATH) and os.path.getsize(LEDGER_PATH) > MAX_BYTES:
            os.replace(LEDGER_PATH, ROTATED_PATH)
        with open(LEDGER_PATH, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass          # a ledger that can't write must never break the call


# ── writing ──────────────────────────────────────────────────────────

def route_provider(path: str) -> str:
    """The provider a path bills to before the handler knows better."""
    if path in _ROUTE_PROVIDER:
        return _ROUTE_PROVIDER[path]
    if path.startswith("/models"):
        return "huggingface"
    return "liquidai"


def begin(method: str, path: str, via: str = "api",
          caller: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Open a record. Nothing is written until `end`."""
    return {
        "id": os.urandom(4).hex(),
        "at": time.time(),
        "t0": time.monotonic(),
        "route": f"{method} {path}",
        "path": path,
        "via": via,
        "provider": route_provider(path),
        "model": None,
        "caller": (caller or {}).get("address") or "anon",
        "caller_kind": (caller or {}).get("kind") or "anon",
        "owner": bool((caller or {}).get("owner")),
        "defer": False,
        "written": False,
    }


def tag(record: Optional[Dict[str, Any]], **fields):
    """Let a handler correct what the path guessed (runtime, model, tokens)."""
    if record is None:
        return
    for key, value in fields.items():
        if value is not None:
            record[key] = value


def end(record: Optional[Dict[str, Any]], status: int = 200,
        error: Optional[str] = None, **fields) -> Optional[Dict[str, Any]]:
    """Close a record and write the line. Idempotent — a deferred stream that
    also gets closed by the middleware must not appear twice."""
    if record is None or record.get("written"):
        return None
    record["written"] = True
    row = {k: v for k, v in record.items()
           if k not in ("t0", "defer", "written") and v is not None}
    row.update({k: v for k, v in fields.items() if v is not None})
    row["ms"] = round((time.monotonic() - record["t0"]) * 1000)
    row["status"] = status
    row["ok"] = status < 400 and not error
    if error:
        row["error"] = str(error)[:300]

    # Throughput is tokens over the *generation*, not over the request: a call
    # that spent 15 s loading a model and 4 s writing is not a 0.2 tok/s model,
    # and a row that says so would libel the runtime. `gen_sec` comes from the
    # done frame; when there isn't one, wall-clock is all there is.
    tokens = row.get("completion_tokens") or row.get("chunks")
    span = row.get("gen_sec") or (row["ms"] / 1000)
    if tokens and span:
        row["tok_per_sec"] = round(tokens / span, 2)
    if row.get("gen_sec") and row["ms"]:
        overhead = round(row["ms"] / 1000 - row["gen_sec"], 2)
        if overhead > 0.5:            # loading the weights, almost always
            row["setup_sec"] = overhead
    with _LOCK:
        _load_tail()
        _RECENT.append(row)
        _append(row)
    return row


def note(**row) -> Dict[str, Any]:
    """Write a finished call directly — for runs this box didn't perform."""
    row = {k: v for k, v in row.items() if v is not None}
    row.setdefault("id", os.urandom(4).hex())
    row.setdefault("at", time.time())
    row.setdefault("ok", True)
    row.setdefault("status", 200)
    with _LOCK:
        _load_tail()
        _RECENT.append(row)
        _append(row)
    return row


def stats_from_done(event: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the numbers worth keeping out of a `done` frame, either runtime's."""
    usage = event.get("usage") or {}
    out = {
        "prompt_tokens": event.get("prompt_tokens") or usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "chunks": event.get("chunks"),
        "gen_sec": event.get("elapsed_sec"),
        "ttft_sec": event.get("ttft_sec"),
        "modality": event.get("modality"),
    }
    if usage.get("total_tokens"):
        out["total_tokens"] = usage["total_tokens"]
    return {k: v for k, v in out.items() if v is not None}


# ── reading ──────────────────────────────────────────────────────────

def _rows() -> List[Dict[str, Any]]:
    with _LOCK:
        _load_tail()
        return list(_RECENT)


def query(provider: Optional[str] = None, model: Optional[str] = None,
          via: Optional[str] = None, path: Optional[str] = None,
          ok: Optional[bool] = None, since: Optional[float] = None,
          inference_only: bool = False, limit: int = 200) -> Dict[str, Any]:
    """Recent calls, newest first."""
    rows = _rows()
    if since:
        rows = [r for r in rows if r.get("at", 0) >= since]
    if provider:
        rows = [r for r in rows if r.get("provider") == provider]
    if model:
        needle = model.lower()
        rows = [r for r in rows if needle in str(r.get("model") or "").lower()]
    if via:
        rows = [r for r in rows if r.get("via") == via]
    if path:
        rows = [r for r in rows if path in str(r.get("path") or "")]
    if ok is not None:
        rows = [r for r in rows if bool(r.get("ok")) is ok]
    if inference_only:
        rows = [r for r in rows if r.get("kind") == "inference"]
    rows = list(reversed(rows))
    return {
        "count": len(rows),
        "held": len(_RECENT),
        "path": LEDGER_PATH,
        "calls": rows[:limit],
    }


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[idx])


def _bucket(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    latencies = [r["ms"] for r in rows if isinstance(r.get("ms"), (int, float))]
    tokens = sum((r.get("completion_tokens") or r.get("chunks") or 0) for r in rows)
    prompt = sum((r.get("prompt_tokens") or 0) for r in rows)
    errors = [r for r in rows if not r.get("ok")]
    inference = [r for r in rows if r.get("kind") == "inference"]
    return {
        "calls": len(rows),
        "inference": len(inference),
        "errors": len(errors),
        "error_rate": round(100 * len(errors) / len(rows)) if rows else 0,
        "tokens_out": tokens,
        "tokens_in": prompt,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "last_at": max((r.get("at", 0) for r in rows), default=None),
    }


def stats(window_hours: float = 24.0) -> Dict[str, Any]:
    """Rollups: totals, then the same numbers per provider, model, caller, hour."""
    since = time.time() - window_hours * 3600
    rows = [r for r in _rows() if r.get("at", 0) >= since]

    by_provider = {}
    for name in PROVIDERS:
        subset = [r for r in rows if r.get("provider") == name]
        if subset:
            by_provider[name] = _bucket(subset)

    # Only runs, not the calls that merely mention a model: POST /calls/report
    # names the model it is reporting, and counting that envelope as a second
    # run would double every browser generation on the board.
    models: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("model") and row.get("kind") == "inference":
            models.setdefault(row["model"], []).append(row)
    by_model = sorted(
        ({"model": name,
          "provider": max({r.get("provider") for r in subset},
                          key=lambda p: sum(1 for r in subset if r.get("provider") == p)),
          **_bucket(subset)}
         for name, subset in models.items()),
        key=lambda m: -m["calls"])

    vias: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        vias.setdefault(row.get("via") or "api", []).append(row)

    callers: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        callers.setdefault(row.get("caller") or "anon", []).append(row)

    # One point per hour, oldest first — enough to see a spike, small enough to
    # ship on every poll.
    span = max(1, int(window_hours))
    now = time.time()
    series = []
    for i in range(span - 1, -1, -1):
        lo, hi = now - (i + 1) * 3600, now - i * 3600
        window = [r for r in rows if lo <= r.get("at", 0) < hi]
        series.append({
            "hour": int(hi),
            "calls": len(window),
            "errors": sum(1 for r in window if not r.get("ok")),
            "tokens": sum((r.get("completion_tokens") or r.get("chunks") or 0)
                          for r in window),
        })

    return {
        "window_hours": window_hours,
        "total": _bucket(rows),
        "providers": by_provider,
        "models": by_model[:20],
        "via": {name: _bucket(subset) for name, subset in vias.items()},
        "callers": sorted(
            ({"caller": name, "kind": subset[-1].get("caller_kind"), **_bucket(subset)}
             for name, subset in callers.items()),
            key=lambda c: -c["calls"])[:15],
        "series": series,
        "path": LEDGER_PATH,
    }


def provider_stats(name: str, window_hours: float = 24.0) -> Dict[str, Any]:
    since = time.time() - window_hours * 3600
    return _bucket(r for r in _rows()
                   if r.get("provider") == name and r.get("at", 0) >= since)


def purge() -> Dict[str, Any]:
    """Drop the ledger. Owner-only upstream — this is someone's traffic history."""
    with _LOCK:
        held = len(_RECENT)
        _RECENT.clear()
        for path in (LEDGER_PATH, ROTATED_PATH):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
    return {"ok": True, "dropped": held, "path": LEDGER_PATH}
