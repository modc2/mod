"""
Video providers as data. A provider is a config.json entry: an x402 URL, a body
template with {prompt} {seconds} {aspect} {model} slots, a max clip length.
Responses are read loosely (any provider names things differently), so adding
a provider is a config edit, not new code.
"""
import time
from typing import Optional

import requests

from . import x402

DONE = {'success', 'succeeded', 'completed', 'complete', 'done', 'finished', 'ready'}
FAILED = {'error', 'failed', 'failure', 'cancelled', 'canceled'}
VIDEO_KEYS = ('video', 'video_url', 'videoUrl', 'url', 'output', 'result', 'download_url')
STATUS_KEYS = ('status_url', 'statusUrl', 'poll_url', 'pollUrl')


def fill(template, values: dict):
    """Fill {slots}; a slot that is the whole string keeps the value's type, an empty one drops the key."""
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            f = fill(v, values)
            if f not in ('', None):
                out[k] = f
        return out
    if isinstance(template, list):
        return [fill(v, values) for v in template]
    if isinstance(template, str):
        if template.startswith('{') and template.endswith('}') and template[1:-1] in values:
            return values[template[1:-1]]
        return template.format(**values)
    return template


def find_video(obj) -> Optional[str]:
    """First thing that looks like a video URL, searched depth-first."""
    if isinstance(obj, str):
        return obj if obj.startswith('http') and ('.mp4' in obj or '.webm' in obj or '.mov' in obj) else None
    if isinstance(obj, dict):
        for k in VIDEO_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.startswith('http'):
                return v
            if isinstance(v, (dict, list)):
                hit = find_video(v)
                if hit:
                    return hit
        for v in obj.values():
            hit = find_video(v)
            if hit:
                return hit
    if isinstance(obj, list):
        for v in obj:
            hit = find_video(v)
            if hit:
                return hit
    return None


def status_of(obj) -> str:
    if not isinstance(obj, dict):
        return ''
    return str(obj.get('status') or obj.get('state') or '').lower()


def status_url(resp: requests.Response, obj) -> Optional[str]:
    u = resp.headers.get('X-Status-Url') or resp.headers.get('Location')
    if not u and isinstance(obj, dict):
        u = next((obj[k] for k in STATUS_KEYS if obj.get(k)), None)
    if u and u.startswith('/'):
        from urllib.parse import urljoin
        u = urljoin(resp.url, u)
    return u


class Provider:
    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self.url = spec['url']
        self.max_clip = int(spec.get('max_clip_seconds', 10))
        self.models = spec.get('models', [])
        self.default_model = spec.get('default_model', '')

    def body(self, prompt: str, seconds: int, aspect: str, model: str = '') -> dict:
        return fill(self.spec.get('body') or {'prompt': '{prompt}'},
                    {'prompt': prompt, 'seconds': int(seconds), 'aspect': aspect,
                     'model': model or self.default_model})

    def quote(self, prompt: str, seconds: int, aspect: str, model: str = '', networks=None) -> dict:
        return x402.quote(self.url, self.body(prompt, seconds, aspect, model), networks=networks)

    def render(self, prompt: str, seconds: int, aspect: str, model: str, key: str,
               max_usd: float, poll: float = 5, timeout: float = 900, networks=None) -> dict:
        """Pay for one clip and wait for its URL. Returns {video, paid_usd, receipt, ...}."""
        res = x402.pay(self.url, self.body(prompt, seconds, aspect, model), key, max_usd, networks=networks)
        r = res.pop('response')
        if not r.ok:
            return {'error': f'{self.name} {r.status_code}: {r.text[:300]}', **res}
        obj = r.json() if 'json' in r.headers.get('content-type', '') else {}
        if r.headers.get('content-type', '').startswith('video/'):
            return {'bytes': r.content, **res}
        video, poll_url = find_video(obj), status_url(r, obj)
        deadline = time.time() + float(timeout)
        while not video and poll_url and time.time() < deadline:
            if status_of(obj) in FAILED:
                break
            time.sleep(float(poll))
            pr = requests.get(poll_url, timeout=30)
            obj = pr.json() if pr.ok else {'status': 'pending'}
            video = find_video(obj) if status_of(obj) in DONE or not status_of(obj) else None
            poll_url = status_url(pr, obj) or poll_url
        if not video:
            return {'error': f'{self.name}: no video ({status_of(obj) or "timeout"})',
                    'last': obj, 'status_url': poll_url, **res}
        return {'video': video, 'status_url': poll_url, **res}


def load(cfg: dict, include_disabled: bool = False) -> dict:
    return {n: Provider(n, s) for n, s in (cfg.get('providers') or {}).items()
            if include_disabled or s.get('enabled', True)}
