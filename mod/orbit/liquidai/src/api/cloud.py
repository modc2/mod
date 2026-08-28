"""CLOUD runtime — Liquid's own hosted inference, on the caller's key.

`https://inference.liquid.ai/v1` is OpenAI-compatible, so this is a thin proxy:
model list, chat, streaming. The only opinion it holds is about the key — it
always uses the caller's (header first, then this box's vault), and it never
falls back to an ambient one for someone else's request.
"""

import json
from typing import Any, Dict, Iterator, List, Optional

import requests

BASE = "https://inference.liquid.ai/v1"
TIMEOUT = 120


def _headers(key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def available(key: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"runtime": "cloud", "base": BASE, "ok": False}
    if not key:
        out["error"] = "no key"
        out["hint"] = "POST /keys {provider:'cloud', key:'…'} or set LIQUID_API_KEY"
        return out
    try:
        r = requests.get(f"{BASE}/models", headers=_headers(key), timeout=20)
        if r.status_code == 401:
            out["error"] = "key rejected (401)"
            return out
        r.raise_for_status()
        data = r.json().get("data", [])
        out.update(ok=True, models=[m.get("id") for m in data], count=len(data))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def models(key: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE}/models", headers=_headers(key), timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def generate(key: str, model: str, messages: List[Dict[str, str]],
             max_tokens: int = 512, temperature: float = 0.3,
             top_p: float = 0.95) -> Iterator[Dict[str, Any]]:
    """Stream a completion, translating SSE deltas into our event shape."""
    body = {
        "model": model, "messages": messages, "max_tokens": int(max_tokens),
        "temperature": temperature, "top_p": top_p, "stream": True,
    }
    try:
        r = requests.post(f"{BASE}/chat/completions", headers=_headers(key),
                          json=body, stream=True, timeout=TIMEOUT)
    except Exception as e:
        yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
        return
    if not r.ok:
        yield {"type": "error", "error": f"HTTP {r.status_code}",
               "detail": r.text[:500]}
        return

    usage: Dict[str, Any] = {}
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            text = (choice.get("delta") or {}).get("content")
            if text:
                yield {"type": "token", "text": text}
    yield {"type": "done", "runtime": "cloud", "repo": model, "usage": usage}
