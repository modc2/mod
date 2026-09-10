"""Somebody else's GPU, over an OpenAI-shaped endpoint.

Three vendors, one class, because they all took the same `POST
/v1/audio/transcriptions` with a file part. What differs is the base URL, the
model name and the price — so those are data, not code. Keys live in
~/.mod/sound2text/keys.json at 0600 or in the environment, never in this
repository, and a request may carry its own key instead of using the stored
one.

Sending a *segment* rather than a file is where this gets cheap: these
endpoints bill by the second of audio uploaded, so the silence the VAD removed
is money that is never spent.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .. import audio, keys
from .base import RATE, Engine, clean

VENDORS: Dict[str, Dict[str, Any]] = {
    'openai': {
        'base': 'https://api.openai.com/v1',
        'model': 'whisper-1',
        'env': 'OPENAI_API_KEY',
        'cost_per_min': 0.006,
        'note': 'whisper-1, or gpt-4o-mini-transcribe / gpt-4o-transcribe',
    },
    'groq': {
        'base': 'https://api.groq.com/openai/v1',
        'model': 'whisper-large-v3-turbo',
        'env': 'GROQ_API_KEY',
        'cost_per_min': 0.00067,
        'note': 'large-v3 quality at roughly a tenth of the price, and faster',
    },
    'deepinfra': {
        'base': 'https://api.deepinfra.com/v1/openai',
        'model': 'openai/whisper-large-v3-turbo',
        'env': 'DEEPINFRA_API_KEY',
        'cost_per_min': 0.0002,
        'note': 'cheapest of the three at the time of writing',
    },
}


def _multipart(fields: Dict[str, str], filename: str, blob: bytes) -> Tuple[bytes, str]:
    """A multipart/form-data body, built by hand so this file needs no requests."""
    boundary = f'----s2t{uuid.uuid4().hex}'
    parts: List[bytes] = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f'{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.append(blob)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


class RemoteWhisper(Engine):
    kind = 'remote'

    def __init__(self, vendor: str = 'openai', model: Optional[str] = None,
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 **options: Any):
        spec = VENDORS.get(vendor)
        if spec is None:
            raise KeyError(f'unknown vendor {vendor!r} — {list(VENDORS)}')
        self.vendor, self.spec = vendor, spec
        self.name = vendor
        self.description = spec['note']
        self.needs = (spec['env'],)
        self.cost_per_min = spec['cost_per_min']
        self.default_model = spec['model']
        self._base = (base_url or spec['base']).rstrip('/')
        self._key = api_key
        super().__init__(model, **options)

    def key(self) -> Optional[str]:
        return self._key or os.environ.get(self.spec['env']) or keys.get(self.vendor)

    def check(self) -> Tuple[bool, str]:
        if not self.key():
            return False, (f'no key — m sound2text/set_key vendor={self.vendor} key=... '
                           f'or export {self.spec["env"]}')
        return True, f'ready — {self._base}'

    def transcribe_one(self, clip: np.ndarray, language: Optional[str] = None,
                       task: str = 'transcribe', **options: Any) -> Dict[str, Any]:
        key = self.key()
        if not key:
            raise PermissionError(self.check()[1])
        fields = {'model': self.model, 'response_format': 'json'}
        if language:
            fields['language'] = language
        if options.get('prompt'):
            fields['prompt'] = str(options['prompt'])
        endpoint = 'translations' if task == 'translate' else 'transcriptions'

        body, content_type = _multipart(fields, 'clip.wav', audio.write_wav(clip))
        request = urllib.request.Request(
            f'{self._base}/audio/{endpoint}', data=body,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': content_type})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'{self.vendor} {exc.code}: {exc.read()[:300].decode()}')
        return {'text': clean(payload.get('text')),
                'cost_usd': round(self.cost_per_min * clip.size / RATE / 60, 6),
                'endpoint': f'{self._base}/audio/{endpoint}'}
