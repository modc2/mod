"""
store client — every byte this module keeps lives in the store mod.

encrypt owns no storage of its own: ciphertext and circuit sources are uploaded
to the store gateway with the *caller's* protocol token, so the store's own
whitelist, quota, terms and ACLs apply, and the caller — not this module — is
the recorded owner of every object. That is also what makes "delete it server
side" honest: DELETE /rm is issued by the object's owner.
"""
import json
import os
from typing import Optional

import requests


class StoreError(Exception):
    """The store gateway said no — its message is carried through verbatim."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class Store:

    def __init__(self, url: str = 'http://localhost:50152', timeout: int = 60,
                 activator: str = 'http://localhost:9000'):
        self.url = os.environ.get('ENCRYPT_STORE_URL', url).rstrip('/')
        self.timeout = timeout
        self.activator = os.environ.get('ENCRYPT_ACTIVATOR_URL', activator).rstrip('/')

    # ── plumbing ─────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        return {'Authorization': f'Bearer {token}'}

    def _request(self, method: str, path: str, **kw):
        """One place where a dead gateway becomes a StoreError — callers should
        never have to catch requests' exceptions to report 'store is down'.

        A refused connection usually means the fleet's activator put the store to
        sleep for being idle, so we ask it to wake and try once more. Bodies here
        are bytes, never file handles, so the retry is safe."""
        try:
            return requests.request(method, f'{self.url}{path}', timeout=self.timeout, **kw)
        except requests.RequestException as e:
            if not self._wake():
                raise StoreError(f'store unreachable at {self.url}: {e}', status=503)
        try:
            return requests.request(method, f'{self.url}{path}', timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise StoreError(f'store unreachable at {self.url} after waking it: {e}', status=503)

    def _wake(self) -> bool:
        """Ask the activator to start the store. False when there is no activator
        to ask — the caller then reports the original connection failure."""
        if not self.activator:
            return False
        try:
            r = requests.post(f'{self.activator}/_activator/control',
                              json={'module': 'store', 'action': 'wake'}, timeout=30)
            return r.ok
        except requests.RequestException:
            return False

    def _raise(self, r) -> None:
        try:
            detail = r.json().get('detail')
        except Exception:
            detail = (r.text or '')[:300]
        raise StoreError(f'store {r.status_code}: {detail}', status=r.status_code)

    def _json(self, r):
        if not r.ok:
            self._raise(r)
        return r.json()

    # ── calls ────────────────────────────────────────────────────────

    def health(self) -> dict:
        try:
            r = requests.get(f'{self.url}/health', timeout=5)
            return {'ok': r.ok, 'url': self.url, **(r.json() if r.ok else {})}
        except Exception as e:
            return {'ok': False, 'url': self.url, 'error': str(e)}

    def me(self, token: str) -> dict:
        return self._json(self._request('GET', '/me', headers=self._headers(token)))

    def put(self, token: str, data: bytes, name: str, key: Optional[str] = None,
            public: bool = False, backend: str = 'localfs') -> dict:
        """Upload bytes and return the store's result. In memory the whole way —
        plaintext never reaches a temp file, and neither does ciphertext."""
        r = self._request(
            'POST', '/put',
            headers=self._headers(token),
            files={'file': (name, data, 'application/octet-stream')},
            data={'backend': backend, 'public': 'true' if public else 'false',
                  **({'key': key} if key else {})},
        )
        out = self._json(r)
        cid = self.first_cid(out)
        if not cid:
            raise StoreError(f'store accepted the upload but returned no cid: {json.dumps(out)[:300]}')
        out['cid'] = cid
        return out

    def get(self, token: Optional[str], cid: str) -> bytes:
        headers = self._headers(token) if token else {}
        r = self._request('GET', '/get', params={'cid': cid}, headers=headers)
        if not r.ok:
            self._raise(r)
        return r.content

    def rm(self, token: str, cid: str) -> dict:
        r = self._request('DELETE', '/rm', params={'cid': cid}, headers=self._headers(token))
        if not r.ok:
            self._raise(r)
        return r.json()

    def publish(self, token: str, cid: str, public: bool) -> dict:
        return self._json(self._request('POST', '/publish', json={'cid': cid, 'public': public},
                                        headers=self._headers(token)))

    def readable(self, token: str, cid: str) -> bool:
        """Can this CID still be read? One byte through /preview, so verifying a
        delete costs nothing even for an 8 MB object.

        Raises StoreError when the store can't be asked — 'I could not check' is
        not the same answer as 'it is gone', and callers report the difference."""
        return self._request('GET', '/preview', params={'cid': cid, 'max_bytes': 1},
                             headers=self._headers(token)).ok

    def object(self, token: Optional[str], cid: str) -> dict:
        headers = self._headers(token) if token else {}
        return self._json(self._request('GET', '/object', params={'cid': cid}, headers=headers))

    @staticmethod
    def first_cid(result: dict) -> Optional[str]:
        """/put reports one entry per backend; we store to one, so take the first."""
        if result.get('cid'):
            return result['cid']
        for v in (result.get('results') or {}).values():
            if isinstance(v, dict) and v.get('cid'):
                return v['cid']
        return None
