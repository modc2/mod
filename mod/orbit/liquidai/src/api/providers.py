"""The providers behind this module, each asked whether it works right now.

`/runtimes` answers "where could a chat go". This is the wider question the
backend actually has: *who does this module depend on*, which of them is up,
what does each cost, and how much traffic did each carry. Four answers:

    browser        the visitor's tab (transformers.js on WebGPU). Free to the
                   operator, invisible to this server — everything known about
                   it comes from the tab reporting back to the ledger.
    server         this box (torch + transformers), one resident model.
    cloud          inference.liquid.ai, on a BYOK key, metered by Liquid.
    huggingface    not a runtime, but the provider every one of the above pulls
                   weights and the catalog from. It goes down, everything here
                   does — so it belongs on the same board as the rest.

Health is asked of the thing itself, then cached for a few seconds: this table
is polled by an open console, and a page refresh should not mean four network
round trips per viewer.
"""

import os
import time
from typing import Any, Dict, List, Optional

import requests

try:
    from . import catalog, cloud, keys, ledger, server_rt
except ImportError:  # pragma: no cover — script-style import
    import catalog, cloud, keys, ledger, server_rt

HF_STATUS = "https://huggingface.co/api/models?author=LiquidAI&limit=1"
TTL = 20.0

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def _catalog_state() -> Dict[str, Any]:
    try:
        cat = catalog.load()
        return {"ok": True, "models": cat["count"], "source": cat["source"],
                "age_sec": round(time.time() - cat["fetched_at"]),
                "refresh_error": cat.get("refresh_error")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _hf_reachable() -> Dict[str, Any]:
    try:
        started = time.monotonic()
        r = requests.get(HF_STATUS, timeout=8)
        return {"ok": r.ok, "status": r.status_code,
                "ms": round((time.monotonic() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _disk() -> Dict[str, Any]:
    local = server_rt.local_models()
    return {
        "repos": len(local),
        "bytes": sum(m.get("bytes", 0) for m in local),
        "cache": server_rt.cache_root(),
    }


def _browser_capable(cat_ok: bool) -> Dict[str, Any]:
    """How many catalog rows a tab could run — the ONNX builds, nothing else."""
    if not cat_ok:
        return {}
    try:
        rows = catalog.load()["models"]
    except Exception:
        return {}
    return {"models": sum(1 for m in rows if "browser" in m["runtimes"])}


def table(window_hours: float = 24.0, key: Optional[str] = None,
          fresh: bool = False) -> Dict[str, Any]:
    """Every provider, its health, its price model and its share of the traffic."""
    if not fresh and _CACHE["value"] and time.time() - _CACHE["at"] < TTL and not key:
        table_out = _CACHE["value"]
    else:
        cat = _catalog_state()
        cloud_key = key or keys.get("cloud")
        srv = server_rt.available()
        cld = cloud.available(cloud_key)
        hf = _hf_reachable()

        table_out = [
            {
                "id": "browser",
                "label": "BROWSER",
                "where": "the visitor's tab",
                "engine": "transformers.js · WebGPU (wasm fallback)",
                "base": None,
                "ok": True,
                "state": "ready",
                "detail": "weights stream from HuggingFace straight into the tab — "
                          "no prompt and no token reaches this box",
                "cost": "free to the operator · the visitor's battery",
                "auth": {"needed": False},
                "measured_here": False,
                **_browser_capable(cat["ok"]),
            },
            {
                "id": "server",
                "label": "SERVER",
                "where": "this box",
                "engine": (f"transformers {srv.get('transformers')} · torch "
                           f"{srv.get('torch')} · {srv.get('device', '?')}"
                           if srv["ok"] else "transformers + torch"),
                "base": f"http://localhost:{os.environ.get('PORT', 50460)}",
                "ok": bool(srv["ok"]),
                "state": "ready" if srv["ok"] else "unavailable",
                "detail": srv.get("error") or srv.get("note") or
                          f"{srv.get('device')} · {srv.get('threads')} threads",
                "cost": "this box's CPU/GPU time",
                "auth": {"needed": True, "kind": "session token",
                         "set": True, "note": "owner for weights"},
                "measured_here": True,
                "resident": srv.get("loaded"),
                "device": srv.get("device"),
                "gpu": srv.get("gpu"),
                "disk": _disk(),
            },
            {
                "id": "cloud",
                "label": "CLOUD",
                "where": "inference.liquid.ai",
                "engine": "Liquid's hosted inference (OpenAI-compatible)",
                "base": cloud.BASE,
                "ok": bool(cld["ok"]),
                "state": "ready" if cld["ok"] else ("no key" if not cloud_key
                                                    else "unreachable"),
                "detail": cld.get("error") or
                          f"{cld.get('count', 0)} models on this key",
                "cost": "billed to whoever's key made the call",
                "auth": {"needed": True, "kind": "Liquid API key (BYOK)",
                         "set": bool(cloud_key),
                         "masked": keys.mask(cloud_key),
                         "source": keys.status()["cloud"]["source"],
                         "hint": cld.get("hint")},
                "measured_here": True,
                "models": cld.get("count"),
                "model_ids": (cld.get("models") or [])[:40],
            },
            {
                "id": "huggingface",
                "label": "HUGGINGFACE",
                "where": "huggingface.co",
                "engine": "catalog + every weight this module serves",
                "base": catalog.HF_API,
                "ok": bool(hf["ok"] or cat["ok"]),
                "state": ("ready" if hf["ok"] else
                          ("serving cache" if cat["ok"] else "down")),
                "detail": (cat.get("refresh_error") or hf.get("error") or
                           f"{cat.get('models', 0)} LFM repos folded · catalog "
                           f"{cat.get('source')} · {cat.get('age_sec')}s old"),
                "cost": "free · rate-limited by IP unless HF_TOKEN is set",
                "auth": {"needed": False, "kind": "HF token (optional)",
                         "set": bool(keys.get("hf")),
                         "masked": keys.mask(keys.get("hf"))},
                "measured_here": True,
                "models": cat.get("models"),
                "catalog": cat,
                "latency_ms": hf.get("ms"),
                "disk": _disk(),
            },
        ]
        if not key:
            _CACHE.update(at=time.time(), value=table_out)

    rollup = ledger.stats(window_hours)
    providers = []
    for row in table_out:
        seen = rollup["providers"].get(row["id"], {})
        providers.append({**row, "traffic": seen or ledger.provider_stats(
            row["id"], window_hours)})

    # `liquidai` is this module answering out of its own memory — catalog reads
    # served from cache, auth, the ledger itself. It isn't a dependency, but its
    # traffic is real and leaving it out makes the percentages lie.
    self_traffic = rollup["providers"].get("liquidai")
    return {
        "providers": providers,
        "self": {"id": "liquidai", "label": "THIS MODULE",
                 "where": "in-process", "traffic": self_traffic or {}},
        "window_hours": window_hours,
        "total": rollup["total"],
        "via": rollup["via"],
    }


def one(provider_id: str, window_hours: float = 24.0) -> Optional[Dict[str, Any]]:
    for row in table(window_hours)["providers"]:
        if row["id"] == provider_id:
            return {**row, "recent": ledger.query(provider=provider_id, limit=50)["calls"]}
    return None


def ids() -> List[str]:
    return ["browser", "server", "cloud", "huggingface"]
