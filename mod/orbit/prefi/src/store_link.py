"""The bridge to the fleet's store — where a shared score function lives.

A function is one small JSON bundle. Sharing it means putting that bundle in
`core/store` and handing out the CID; importing it means fetching the CID.
This file owns no credentials: an upload carries the **caller's** protocol
token, forwarded verbatim, so the store applies its own whitelist, quota and
terms to the person publishing, and PreFi never becomes a way around them. A
public object is fetched with no token at all, which is what makes a CID a
link anyone can open.

Copied in spirit from `orbit/lighthouse/store_link.py`, the reference for this
pattern, including the activator knock: the store is scale-to-zero, so a
refused connection means asleep, not gone.
"""
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import requests

DIR = Path(__file__).resolve().parent
DEFAULT_STORE = 'http://127.0.0.1:50152'
DEFAULT_ACTIVATOR = 'http://127.0.0.1:9000'
STORE_MODULE = os.environ.get('PREFI_STORE_MODULE', 'store')
TIMEOUT = float(os.environ.get('PREFI_STORE_TIMEOUT', 20))
WAKE_TIMEOUT = float(os.environ.get('PREFI_STORE_WAKE_TIMEOUT', 45))
MAX_BUNDLE = 64 * 1024


class StoreError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message


def store_url() -> str:
    env = os.environ.get('PREFI_STORE_URL')
    return env.rstrip('/') if env else DEFAULT_STORE


def activator_url() -> str:
    env = os.environ.get('PREFI_ACTIVATOR_URL')
    return env.rstrip('/') if env is not None else DEFAULT_ACTIVATOR


def _protocol():
    """`import mod` — the protocol package, with this module's own dirs (each
    of which has a mod.py) taken off the path first."""
    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        return got
    mine = {str(DIR), str(DIR / 'api'), str(DIR / 'app')}
    saved = list(sys.path)
    sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path if p and str(Path(p).resolve()) not in mine]
        return importlib.import_module('mod')
    finally:
        sys.path = saved


def local_token(data: Optional[dict] = None) -> str:
    """Mint a protocol token with this box's own key (CLI use only)."""
    return _protocol().mod('auth')().token(data or {'mod': 'prefi'})


class StoreLink:
    def __init__(self, url: Optional[str] = None, timeout: float = TIMEOUT,
                 activator: Optional[str] = None):
        self.url = (url or store_url()).rstrip('/')
        self.timeout = timeout
        self.activator = (activator if activator is not None else activator_url()).rstrip('/')

    @staticmethod
    def _bearer(token: str) -> Dict[str, str]:
        token = (token or '').strip()
        if not token:
            raise StoreError('no protocol token — sign in first', 401)
        if token.lower().startswith('bearer '):
            token = token[7:].strip()
        return {'Authorization': f'Bearer {token}'}

    @staticmethod
    def _detail(r) -> str:
        try:
            return str(r.json().get('detail', r.text[:300]))
        except Exception:
            return r.text[:300]

    def _wake(self):
        if not self.activator:
            return False, 'no activator configured to start it'
        try:
            r = requests.get(f'{self.activator}/api/{STORE_MODULE}/health',
                             timeout=WAKE_TIMEOUT)
        except requests.RequestException:
            return False, f'the activator at {self.activator} did not answer'
        if r.status_code == 503 and 'disabled by host' in r.text:
            return False, f'the host has turned {STORE_MODULE} off'
        if not r.ok:
            return False, f'the activator could not start it ({r.status_code})'
        return True, ''

    def _send(self, attempt):
        try:
            return attempt()
        except requests.ConnectionError:
            woken, why = self._wake()
            if not woken:
                raise StoreError(f'the store is not running at {self.url} — {why}', 503)
        try:
            return attempt()
        except requests.RequestException as e:
            raise StoreError(f'the store woke but is not answering: {e}', 503)

    def health(self) -> Dict[str, Any]:
        r = self._send(lambda: requests.get(f'{self.url}/health', timeout=self.timeout))
        if not r.ok:
            raise StoreError(f'store /health → {r.status_code}', r.status_code)
        return r.json()

    def fetch_json(self, cid: str, token: Optional[str] = None) -> Any:
        """The JSON behind a CID. Public objects need no token."""
        headers = self._bearer(token) if token else {}
        r = self._send(lambda: requests.get(f'{self.url}/get', headers=headers,
                                            params={'cid': cid}, timeout=self.timeout))
        if r.status_code >= 400:
            raise StoreError(f'store /get {cid} → {r.status_code}: {self._detail(r)}',
                             r.status_code)
        if len(r.content) > MAX_BUNDLE:
            raise StoreError(f'{cid} is {len(r.content)} bytes — not a function bundle', 415)
        try:
            return json.loads(r.content.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise StoreError(f'{cid} is in the store but is not JSON: {e}', 415)

    def put_json(self, token: str, name: str, payload: Any,
                 public: bool = True) -> Dict[str, Any]:
        """Upload a bundle as a public object and get its CID back."""
        body = json.dumps(payload, indent=2).encode('utf-8')
        headers = self._bearer(token)
        form = {'backend': 'localfs', 'key': name,
                'public': 'true' if public else 'false'}
        r = self._send(lambda: requests.post(
            f'{self.url}/put', headers=headers, data=form,
            files={'file': (name, body, 'application/json')},
            timeout=max(self.timeout, 60)))
        if r.status_code >= 400:
            raise StoreError(f'store /put → {r.status_code}: {self._detail(r)}',
                             r.status_code)
        out = r.json()
        cid = out.get('cid')
        if not cid:
            for entry in (out.get('results') or {}).values():
                if isinstance(entry, dict) and entry.get('cid'):
                    cid = entry['cid']
                    break
        if not cid:
            raise StoreError('the store accepted the upload but returned no CID', 502)
        return {'cid': str(cid), 'size': len(body), 'url': f'{self.url}/get?cid={cid}'}
