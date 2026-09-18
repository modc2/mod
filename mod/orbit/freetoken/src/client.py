"""The wire to a FreeToken server. stdlib only — this half never needs a GPU.

Two surfaces, both real endpoints of the upstream project:

  serve  (:1919)  /health /v1/models /v1/stats /v1/cache/status /v1/cache/rebuild
                  /v1/requests /generate /v1/chat/completions /v1/completions
                  /v1/messages /v1/messages/count_tokens /v1/responses
  daemon (:1900)  /health /engine/status /engine/health /engine/stats
                  /engine/metrics /engine/start /engine/stop /engine/switch
                  /engine/logs /bench/run /bench/profile /checkpoint/*

The daemon authenticates with an `X-FT-Token` header when it was started with
`--token`; the box carries that secret, and it is only ever sent upstream.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

TIMEOUT = 30.0


class Unreachable(RuntimeError):
    """No FreeToken server answered. The caller decides how loud that is."""


class Refused(RuntimeError):
    """A server answered, and said no."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f'HTTP {status}: {body}')
        self.status, self.body = status, body


# ── one request ──────────────────────────────────────────────────────

def call(base: str, path: str, method: str = 'GET', body: Any = None,
         token: str = None, timeout: float = TIMEOUT, raw: bool = False) -> Any:
    if not base:
        raise Unreachable('no server url')
    url = base.rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['X-FT-Token'] = token
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            pass
        raise Refused(exc.code, detail) from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Unreachable(f'{url}: {getattr(exc, "reason", exc)}') from None
    if raw:
        return payload
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload.decode('utf-8', 'replace')


def stream(base: str, path: str, body: Any, token: str = None,
           timeout: float = 600.0) -> Iterator[str]:
    """Server-sent events, line by line, exactly as the engine emits them."""
    url = base.rstrip('/') + path
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    if token:
        headers['X-FT-Token'] = token
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for line in response:
                yield line.decode('utf-8', 'replace').rstrip('\n')
    except urllib.error.HTTPError as exc:
        raise Refused(exc.code, exc.read().decode('utf-8', 'replace')) from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Unreachable(f'{url}: {getattr(exc, "reason", exc)}') from None


def _serve(box: Dict[str, Any]) -> str:
    if not box.get('url'):
        raise Unreachable(f'box {box.get("name")!r} has no serve url')
    return box['url']


def _daemon(box: Dict[str, Any]) -> str:
    if not box.get('daemon'):
        raise Unreachable(f'box {box.get("name")!r} has no daemon url — '
                          'start one with `ft daemon` on that machine, or '
                          'register it with daemon=http://host:1900')
    return box['daemon']


# ── the serve process ────────────────────────────────────────────────

def health(box: Dict[str, Any], timeout: float = 5.0) -> Any:
    return call(_serve(box), '/health', token=box.get('token'), timeout=timeout)


def models(box: Dict[str, Any], timeout: float = 10.0) -> Any:
    return call(_serve(box), '/v1/models', token=box.get('token'), timeout=timeout)


def stats(box: Dict[str, Any]) -> Any:
    return call(_serve(box), '/v1/stats', token=box.get('token'))


def cache_status(box: Dict[str, Any]) -> Any:
    return call(_serve(box), '/v1/cache/status', token=box.get('token'))


def cache_rebuild(box: Dict[str, Any], moe: Any = None, kv: Any = None,
                  mamba: Any = None, swa: Any = None, wait: int = 300) -> Any:
    """Resize a live pool. Same knobs as `ft ctl cache --moe N --kv N`."""
    pools = {'moe': moe, 'kv': kv, 'mamba': mamba, 'swa': swa}
    body = {k: _sizes(v) for k, v in pools.items() if v not in (None, '')}
    if not body:
        raise ValueError('nothing to rebuild — pass at least one of moe/kv/mamba/swa')
    body['wait'] = int(wait)
    return call(_serve(box), '/v1/cache/rebuild', 'POST', body,
                token=box.get('token'), timeout=float(wait) + 30)


def _sizes(value: Any) -> int:
    """`ft ctl` takes k/m suffixes; so does this."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace('_', '')
    scale = {'k': 1_000, 'm': 1_000_000, 'g': 1_000_000_000}.get(text[-1:], 1)
    return int(float(text[:-1] if scale > 1 else text) * scale)


def requests(box: Dict[str, Any], since: int = 0, limit: int = 50) -> Any:
    return call(_serve(box), f'/v1/requests?since={int(since)}&limit={int(limit)}',
                token=box.get('token'))


def generate(box: Dict[str, Any], prompt: str = 'Hello', max_tokens: int = 32,
             ignore_eos: bool = False) -> Any:
    """The raw completion smoke test — no chat template, like `ft ctl generate`."""
    return call(_serve(box), '/generate', 'POST',
                {'text': prompt,
                 'sampling_params': {'max_new_tokens': int(max_tokens),
                                     'ignore_eos': bool(ignore_eos)}},
                token=box.get('token'), timeout=120)


def chat(box: Dict[str, Any], messages: List[Dict[str, str]], model: str = None,
         max_tokens: int = 512, temperature: float = None, timeout: float = 600.0,
         **extra: Any) -> Any:
    body: Dict[str, Any] = {'model': model or served_name(box), 'messages': messages,
                            'max_tokens': int(max_tokens)}
    if temperature is not None:
        body['temperature'] = float(temperature)
    body.update(extra)
    return call(_serve(box), '/v1/chat/completions', 'POST', body,
                token=box.get('token'), timeout=timeout)


def chat_stream(box: Dict[str, Any], messages: List[Dict[str, str]], model: str = None,
                max_tokens: int = 512, **extra: Any) -> Iterator[str]:
    body: Dict[str, Any] = {'model': model or served_name(box), 'messages': messages,
                            'max_tokens': int(max_tokens), 'stream': True}
    body.update(extra)
    return stream(_serve(box), '/v1/chat/completions', body, token=box.get('token'))


def count_tokens(box: Dict[str, Any], messages: List[Dict[str, str]],
                 model: str = None) -> Any:
    """The Anthropic surface — the same endpoint Claude Code would hit."""
    return call(_serve(box), '/v1/messages/count_tokens', 'POST',
                {'model': model or served_name(box), 'messages': messages},
                token=box.get('token'))


def served_name(box: Dict[str, Any]) -> Optional[str]:
    """Whatever `/v1/models` calls the resident model. Never guess it."""
    try:
        listing = models(box)
    except (Unreachable, Refused):
        return None
    data = listing.get('data') if isinstance(listing, dict) else None
    return data[0].get('id') if data else None


# ── the control daemon ───────────────────────────────────────────────

def daemon_self(box: Dict[str, Any], timeout: float = 5.0) -> Any:
    return call(_daemon(box), '/health', token=box.get('token'), timeout=timeout)


def engine_status(box: Dict[str, Any], timeout: float = 10.0) -> Any:
    return call(_daemon(box), '/engine/status', token=box.get('token'), timeout=timeout)


def engine_metrics(box: Dict[str, Any]) -> Any:
    return call(_daemon(box), '/engine/metrics', token=box.get('token'))


def engine_start(box: Dict[str, Any], model: str, port: int = None,
                 args: List[str] = None, timeout: float = 900.0) -> Any:
    body: Dict[str, Any] = {'model': model, 'args': list(args or [])}
    if port:
        body['port'] = int(port)
    return call(_daemon(box), '/engine/start', 'POST', body,
                token=box.get('token'), timeout=timeout)


def engine_switch(box: Dict[str, Any], model: str, port: int = None,
                  args: List[str] = None, timeout: float = 900.0) -> Any:
    body: Dict[str, Any] = {'model': model, 'args': list(args or [])}
    if port:
        body['port'] = int(port)
    return call(_daemon(box), '/engine/switch', 'POST', body,
                token=box.get('token'), timeout=timeout)


def engine_stop(box: Dict[str, Any], force: bool = False, timeout: float = 120.0) -> Any:
    return call(_daemon(box), '/engine/stop', 'POST', {'force': bool(force)},
                token=box.get('token'), timeout=timeout)


def bench_profile(box: Dict[str, Any]) -> Any:
    """The cached `ft bench bw` profile — what picked the MoE backend."""
    return call(_daemon(box), '/bench/profile', token=box.get('token'))


# ── what a box actually is, right now ────────────────────────────────

def probe(box: Dict[str, Any], timeout: float = 4.0) -> Dict[str, Any]:
    """One card per box: is it up, what is it serving, and can it be steered.

    Never raises — an unreachable box is a fact about the box, not an error in
    the caller. That is what makes it safe to probe every box at once.
    """
    started = time.time()
    card: Dict[str, Any] = {'name': box.get('name'), 'url': box.get('url'),
                            'daemon': box.get('daemon'), 'note': box.get('note', ''),
                            'up': False, 'model': None, 'ms': None,
                            'steerable': False, 'error': None}
    try:
        card['health'] = health(box, timeout=timeout)
        card['up'] = True
        card['ms'] = round((time.time() - started) * 1000)
    except (Unreachable, Refused) as exc:
        card['error'] = str(exc)
    if card['up']:
        h = card.get('health')
        if isinstance(h, dict):
            card['model'] = h.get('model') or h.get('served_model_name')
            card['version'] = h.get('version')
        if not card['model']:
            card['model'] = served_name(box)
    if box.get('daemon'):
        try:
            card['engine'] = engine_status(box, timeout=timeout)
            card['steerable'] = True
        except (Unreachable, Refused) as exc:
            card['daemon_error'] = str(exc)
    return card
