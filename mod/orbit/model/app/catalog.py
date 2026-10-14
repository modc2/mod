"""
Combined model catalog — the one place that knows every provider.

Pure helpers (no FastAPI) shared by app/server.py and the mod.py CLI:
  - PROVIDERS        registry loaded from ../providers.json
  - registry()       PROVIDERS + the caller's own custom endpoints
  - parse_ref()      "chutes:deepseek-ai/DeepSeek-V3" -> ("chutes", "deepseek-ai/DeepSeek-V3")
  - list_models()    one provider's /models, normalised and typed (kind)
  - catalog()        every provider at once, each model tagged with its provider

Every model gets a `kind` — chat | image | video | embedding | speech |
transcription | rerank | moderation — read from the provider's own metadata
(OpenRouter output_modalities, Venice/Together `type`) and otherwise guessed
from the id. The UI picks a workspace per kind.

Keys are never persisted. The in-memory model-list cache is keyed by a hash
of the key (and URL), so a listing fetched with one user's key is never
served to a caller holding a different key.
"""

import hashlib
import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

REGISTRY_PATH = Path(__file__).resolve().parent.parent / 'providers.json'
PROVIDERS: dict = {p['id']: p for p in json.loads(REGISTRY_PATH.read_text())['providers']}

PUBLIC_FIELDS = ('id', 'label', 'group', 'url', 'default_model', 'key_hint', 'docs')
REF_SEP = ':'
CACHE_TTL = 300
CUSTOM_PREFIX = 'c-'  # custom endpoint ids, so they can never shadow a registry id

KINDS = ('chat', 'image', 'video', 'embedding', 'speech', 'transcription', 'rerank', 'moderation')

_cache: dict = {}  # {(pid, url, key_hash): (ts, models)}


class ProviderError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── registry (+ caller's custom endpoints) ───────────────────────────

def _public_host(url: str) -> bool:
    """True when every address the host resolves to is publicly routable.

    Custom endpoints are fetched by THIS server, so a private/loopback URL
    would let any caller probe the fleet's internal ports."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global:
            return False
    return True


def custom_provider(pid: str, spec) -> dict:
    """Validate one caller-supplied endpoint {url, label?} into a provider cfg."""
    if isinstance(spec, str):
        spec = {'url': spec}
    url = str((spec or {}).get('url') or '').strip().rstrip('/')
    if not re.fullmatch(r'c-[a-z0-9][a-z0-9_-]{0,31}', pid):
        raise ProviderError(400, f"custom provider id '{pid}' must look like c-name")
    if urlparse(url).scheme != 'https':
        raise ProviderError(400, f'{pid}: custom endpoints must be https:// URLs')
    if not _public_host(url):
        raise ProviderError(400, f'{pid}: {urlparse(url).hostname} is not a public host')
    return {
        'id': pid, 'label': str(spec.get('label') or pid[2:])[:40], 'group': 'custom',
        'url': url, 'default_model': str(spec.get('default_model') or ''), 'key_hint': 'optional',
        'docs': url, 'custom': True, 'public_models': True,
    }


def registry(custom: Optional[dict] = None) -> dict:
    """PROVIDERS plus the caller's custom endpoints ({id: url | {url,label}})."""
    if not custom:
        return PROVIDERS
    out = dict(PROVIDERS)
    for pid, spec in list(custom.items())[:12]:
        out[pid] = custom_provider(pid, spec)
    return out


def provider_cfg(pid: str, providers: Optional[dict] = None) -> dict:
    providers = providers or PROVIDERS
    cfg = providers.get(pid)
    if not cfg:
        raise ProviderError(400, f"unknown provider '{pid}'; choose from {list(providers)}")
    return cfg


def public_info(cfg: dict) -> dict:
    return {k: cfg.get(k) for k in PUBLIC_FIELDS} | {'public_models': bool(cfg.get('public_models'))}


def make_ref(pid: str, model: str) -> str:
    return f'{pid}{REF_SEP}{model}'


def parse_ref(ref: Optional[str], provider: Optional[str] = None, providers: Optional[dict] = None) -> tuple:
    """Split a combined ref into (provider, model).

    Only a known provider id counts as a prefix, so OpenRouter ids such as
    "meta-llama/llama-3-8b:free" pass through untouched when provider is set.
    """
    providers = providers or PROVIDERS
    if ref and REF_SEP in ref:
        head, tail = ref.split(REF_SEP, 1)
        if head in providers and (not provider or provider in ('all', head)):
            return head, tail
    if not provider or provider == 'all':
        raise ProviderError(400, f"model '{ref}' has no provider; use '<provider>{REF_SEP}<model>'")
    cfg = provider_cfg(provider, providers)
    return provider, ref or cfg['default_model']


# ── model typing + normalisation ─────────────────────────────────────

# provider-declared type -> kind (Venice `type`, Together `type`, OpenRouter output modality)
TYPE_MAP = {
    'text': 'chat', 'chat': 'chat', 'language': 'chat', 'code': 'chat', 'llm': 'chat',
    'image': 'image', 'upscale': 'image', 'inpaint': 'image',
    'video': 'video',
    'embedding': 'embedding', 'embeddings': 'embedding',
    'tts': 'speech', 'speech': 'speech', 'audio': 'speech',
    'asr': 'transcription', 'stt': 'transcription', 'transcribe': 'transcription', 'transcription': 'transcription',
    'rerank': 'rerank',
    'moderation': 'moderation', 'decisions': 'moderation',
}

# id heuristics, first match wins — for providers that publish no type at all
GUESS = (
    ('embedding', r'embed|bge-|e5-|gte-|nomic|minilm'),
    ('rerank', r'rerank'),
    ('transcription', r'whisper|transcri|scribe|\bstt\b|\basr\b|parakeet'),
    ('speech', r'tts|text-to-speech|kokoro|voice|sonic|orpheus'),
    ('moderation', r'moderation|llama-guard|shieldgemma|prompt-guard'),
    ('video', r'sora|\bveo|video|kling|hunyuan-video|wan2|seedance'),
    ('image', r'dall-e|gpt-image|imagen|flux|stable-diffusion|sdxl|sd3|playground-v|recraft|ideogram|hidream|image-gen|seedream'),
)


def guess_kind(mid: str) -> str:
    low = mid.lower()
    for kind, pat in GUESS:
        if re.search(pat, low):
            return kind
    return 'chat'


def _kind(m: dict, mid: str) -> str:
    arch = m.get('architecture') or {}
    outs = arch.get('output_modalities') or []
    if outs:
        if 'text' in outs:
            return 'chat'
        for o in outs:
            if o in TYPE_MAP:
                return TYPE_MAP[o]
    t = str(m.get('type') or '').lower()
    if t in TYPE_MAP:
        return TYPE_MAP[t]
    return guess_kind(mid)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f  # OpenRouter uses -1 for "router decides"


def _prices(m: dict) -> tuple:
    """(usd per 1M input tokens, usd per 1M output tokens, usd per image) — None if unknown."""
    p = m.get('pricing')
    spec = (m.get('model_spec') or {}).get('pricing') if isinstance(m.get('model_spec'), dict) else None
    if isinstance(spec, dict):  # Venice: per-M usd, images per generation
        inp = _num(((spec.get('input') or {}).get('usd')))
        out = _num(((spec.get('output') or {}).get('usd')))
        img = _num(((spec.get('generation') or {}).get('usd')))
        return inp, out, img
    if not isinstance(p, dict):
        provs = m.get('providers')
        if isinstance(provs, list) and provs and isinstance(provs[0], dict):  # HF router
            p = provs[0].get('pricing') or {}
            return _num(p.get('input')), _num(p.get('output')), None
        return None, None, None
    if 'prompt' in p or 'completion' in p:  # OpenRouter / Chutes: per-token strings
        inp, out = _num(p.get('prompt')), _num(p.get('completion'))
        scale = 1e6 if (inp or 0) < 0.01 and (out or 0) < 0.01 else 1
        img = _num(p.get('image_output')) if p.get('image_output') else None
        return (inp * scale if inp is not None else None,
                out * scale if out is not None else None,
                img)
    return _num(p.get('input')), _num(p.get('output')), _num(p.get('image') or p.get('per_image'))


def _ctx(m: dict) -> Optional[int]:
    spec = m.get('model_spec') if isinstance(m.get('model_spec'), dict) else {}
    provs = m.get('providers') if isinstance(m.get('providers'), list) else []
    for v in (m.get('context_length'), m.get('max_model_len'), m.get('context_window'),
              spec.get('availableContextTokens'), m.get('inputTokenLimit'),
              provs[0].get('context_length') if provs and isinstance(provs[0], dict) else None):
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def _vision(m: dict) -> bool:
    arch = m.get('architecture') or {}
    if 'image' in (arch.get('input_modalities') or []):
        return True
    caps = ((m.get('model_spec') or {}).get('capabilities') or {}) if isinstance(m.get('model_spec'), dict) else {}
    return bool(caps.get('supportsVision'))


def _normalise(pid: str, data) -> list:
    out, seen = [], set()
    for m in data:
        if isinstance(m, str):
            m = {'id': m}
        if not isinstance(m, dict):
            continue
        mid = m.get('id') or m.get('name')
        if not mid or mid in seen:
            continue
        seen.add(mid)
        spec = m.get('model_spec') if isinstance(m.get('model_spec'), dict) else {}
        pin, pout, pimg = _prices(m)
        out.append({
            'provider': pid,
            'id': mid,
            'ref': make_ref(pid, mid),
            'name': m.get('display_name') or spec.get('name') or (m.get('name') if m.get('name') != mid else None) or mid,
            'kind': _kind(m, mid),
            'vision': _vision(m),
            'context_length': _ctx(m),
            'price_in': pin,      # USD / 1M input tokens
            'price_out': pout,    # USD / 1M output tokens
            'price_image': pimg,  # USD / image, when the provider bills per image
            'created': m.get('created') if isinstance(m.get('created'), (int, float)) else None,
            'description': (m.get('description') or spec.get('description') or '')[:400] or None,
        })
    return out


def list_models(pid: str, key: Optional[str] = None, refresh: bool = False,
                providers: Optional[dict] = None) -> list:
    cfg = provider_cfg(pid, providers)
    if not key and not cfg.get('public_models'):
        raise ProviderError(401, f'{pid}: BYOK — no key supplied')
    ck = (pid, cfg['url'], hashlib.sha256((key or '').encode()).hexdigest())
    hit = _cache.get(ck)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    headers = dict(cfg.get('models_headers') or {})
    if key:
        if cfg.get('models_auth') == 'x-api-key':
            headers['x-api-key'] = key
        else:
            headers['Authorization'] = f'Bearer {key}'
    url = cfg['url'].rstrip('/') + '/models'
    if cfg.get('models_query'):
        url += '?' + cfg['models_query']
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=not cfg.get('custom'))
    except requests.RequestException as e:
        raise ProviderError(502, f'{pid}/models failed: {e}')
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, f'{pid}: {resp.text[:200]}')
    try:
        body = resp.json()
    except ValueError:
        raise ProviderError(502, f'{pid}/models did not return JSON')
    data = (body.get('data') or body.get('models') or []) if isinstance(body, dict) else body
    models = _normalise(pid, data if isinstance(data, list) else [])
    _cache[ck] = (time.time(), models)
    return models


def catalog(keys: dict, search: Optional[str] = None, refresh: bool = False,
            providers: Optional[dict] = None, kind: Optional[str] = None) -> dict:
    """Every model from every provider the caller can reach, fetched in parallel.

    keys: {provider_id: api_key}. Providers with public catalogs are listed
    even without a key; the rest are skipped and reported under `missing`.
    """
    providers = providers or PROVIDERS
    targets = [pid for pid, cfg in providers.items() if keys.get(pid) or cfg.get('public_models')]
    missing = [pid for pid in providers if pid not in targets]
    models, errors = [], {}

    def fetch(pid):
        try:
            return pid, list_models(pid, keys.get(pid), refresh=refresh, providers=providers), None
        except ProviderError as e:
            return pid, [], e.detail

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        for pid, rows, err in pool.map(fetch, targets):
            models.extend(rows)
            if err:
                errors[pid] = err

    if search:
        terms = [t.strip().lower() for t in search.split(',') if t.strip()]
        models = [m for m in models if any(t in m['ref'].lower() or t in m['name'].lower() for t in terms)]
    if kind:
        models = [m for m in models if m['kind'] == kind]

    counts, kinds = {}, {}
    for m in models:
        counts[m['provider']] = counts.get(m['provider'], 0) + 1
        kinds[m['kind']] = kinds.get(m['kind'], 0) + 1
    return {'count': len(models), 'counts': counts, 'kinds': kinds, 'errors': errors,
            'missing': missing, 'models': models}


def make_client(pid: str, key: str, timeout: float = 120.0, providers: Optional[dict] = None):
    import openai
    return openai.OpenAI(base_url=provider_cfg(pid, providers)['url'], api_key=key or 'none',
                         timeout=timeout, max_retries=2)
