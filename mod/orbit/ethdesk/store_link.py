"""
Where a contract actually lives: the **store** module.

This module compiles and deploys; it is not a filesystem, and a Solidity source
that only exists in one box's sqlite is not shareable, not addressable and not
worth calling storage. So every project written here is uploaded to
`core/store` and identified by the **CID it comes back with**. Sharing a
contract is handing somebody that CID — no export, no copy, no second copy to
keep in sync, because the CID *is* the content.

The one rule this file keeps: **it owns no credentials.** Every call carries
the caller's own mod-protocol token, forwarded verbatim, so the store applies
its own whitelist, quota, terms and ACL to that address. Nothing routed through
eth can reach an object the caller could not have fetched from the store
directly, and eth never becomes a way around the store's gate.

Two consequences worth stating plainly, because the console renders both:

    a caller the store has not whitelisted can still write contracts here —
    they are kept in the local index with their bytes cached — but they have
    no CID, so they cannot be shared until the store lets them in.

    a public project is readable by anyone, signed in or not, because the
    store serves public objects without a token. That is what makes a share
    link a link rather than an invitation.

    from store_link import StoreLink, local_token
    link = StoreLink()
    link.status(local_token())
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

DIR = Path(__file__).resolve().parent
DEFAULT_STORE = 'http://127.0.0.1:50152'
DEFAULT_ACTIVATOR = 'http://127.0.0.1:9000'
STORE_MODULE = os.environ.get('ETH_STORE_MODULE', 'store')
TIMEOUT = float(os.environ.get('ETH_STORE_TIMEOUT', 30))
WAKE_TIMEOUT = float(os.environ.get('ETH_WAKE_TIMEOUT', 45))

# A project bundle is JSON and small; anything much bigger than this is not a
# contract, and refusing early beats a 413 from the store's quota check.
MAX_BUNDLE = int(os.environ.get('ETH_MAX_BUNDLE', 4 << 20))


class StoreError(Exception):
    """The store said no (or did not answer). Carries its status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message


def store_url() -> str:
    """Where the store lives: env override, else config.json, else localhost."""
    env = os.environ.get('ETH_STORE_URL')
    if env:
        return env.rstrip('/')
    try:
        cfg = json.loads((DIR / 'config.json').read_text())
        url = (cfg.get('store') or {}).get('url')
        if url:
            return str(url).rstrip('/')
    except Exception:
        pass
    return DEFAULT_STORE


def activator_url() -> str:
    """Where the fleet's activator lives. Empty string turns waking off."""
    env = os.environ.get('ETH_ACTIVATOR_URL')
    if env is not None:
        return env.rstrip('/')
    return DEFAULT_ACTIVATOR


def local_token(data: Optional[dict] = None, key=None) -> str:
    """Mint a protocol token with this box's own mod key (CLI and tests).

    The browser console mints the same envelope with the visitor's wallet; the
    store cannot tell the two apart, which is the point of the protocol auth.
    """
    from protocol import auth
    return auth(key=key).token(data or {'mod': 'eth'})


class StoreLink:
    def __init__(self, url: Optional[str] = None, timeout: float = TIMEOUT,
                 activator: Optional[str] = None):
        self.url = (url or store_url()).rstrip('/')
        self.timeout = timeout
        self.activator = (activator if activator is not None
                          else activator_url()).rstrip('/')

    # ── plumbing ──────────────────────────────────────────────────

    @staticmethod
    def _bearer(token: str) -> Dict[str, str]:
        token = (token or '').strip()
        if not token:
            raise StoreError('no protocol token: sign in first', 401)
        if token.lower().startswith('bearer '):
            token = token[7:].strip()
        return {'Authorization': f'Bearer {token}'}

    def _wake(self):
        """Ask the fleet's activator to start the store, and wait for its port.

        The activator stops modules that have been idle and starts them when a
        request arrives *through it*. We talk to the store on its own port,
        which the activator never sees — so without this knock a slept store
        stays slept from our side forever, and the console reports a perfectly
        healthy module as dead.

        Returns (worth retrying, why not); the reason comes back rather than
        being stashed on self, because one StoreLink serves every request the
        API handles and two callers racing on an attribute would report each
        other's failure.
        """
        if not self.activator:
            return False, 'no activator configured to start it'
        try:
            r = requests.get(f'{self.activator}/api/{STORE_MODULE}/health',
                             timeout=WAKE_TIMEOUT)
        except requests.RequestException:
            return False, f'the activator at {self.activator} did not answer'
        if r.status_code == 503 and 'disabled by host' in r.text:
            return False, (f'the host has turned {STORE_MODULE} off '
                           f'(`actl enable {STORE_MODULE}` to allow it back)')
        if not r.ok:
            return False, f'the activator could not start it ({r.status_code})'
        return True, ''

    def _send(self, method: str, path: str, **kw):
        """One request, with a single wake-and-retry when the port is closed.

        Only replayable bodies come through here — JSON, params, or bytes we
        still hold. A consumed file handle would replay as an empty upload.
        """
        url = f'{self.url}{path}'
        try:
            return requests.request(method, url, **kw)
        except requests.ConnectionError:
            woken, why = self._wake()
            if not woken:
                raise StoreError(f'the store module is not running at '
                                 f'{self.url} — {why}', 503)
        try:
            return requests.request(method, url, **kw)
        except requests.RequestException as e:
            raise StoreError(f'the store woke but is not answering at '
                             f'{self.url}: {e}', 503)

    @staticmethod
    def _detail(r) -> str:
        try:
            return str(r.json().get('detail', r.text[:400]))
        except Exception:
            return r.text[:400]

    def _call(self, method: str, path: str, token: Optional[str] = None,
              **kw) -> Any:
        headers = self._bearer(token) if token else {}
        headers.update(kw.pop('headers', None) or {})
        try:
            r = self._send(method, path, headers=headers,
                           timeout=kw.pop('timeout', self.timeout), **kw)
        except requests.RequestException as e:
            raise StoreError(f'store unreachable at {self.url}: {e}', 503)
        if r.status_code >= 400:
            raise StoreError(f'store {path} → {r.status_code}: {self._detail(r)}',
                             r.status_code)
        try:
            return r.json()
        except ValueError:
            return {'raw': r.text[:2000]}

    # ── read ──────────────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        return self._call('GET', '/health')

    def me(self, token: str) -> Dict[str, Any]:
        """The caller as the *store* sees them: whitelisted, quota, terms."""
        return self._call('GET', '/me', token)

    def terms(self, token: Optional[str] = None) -> Dict[str, Any]:
        return self._call('GET', '/terms', token)

    def accept_terms(self, token: str) -> Dict[str, Any]:
        """Sign-accept the store's terms — the caller's token is the proof."""
        return self._call('POST', '/terms/accept', token)

    def object_info(self, token: Optional[str], cid: str) -> Dict[str, Any]:
        return self._call('GET', '/object', token, params={'cid': cid})

    def fetch(self, token: Optional[str], cid: str) -> bytes:
        """The bytes behind a CID, with the caller's read rights.

        `token` is optional on purpose: a public object is fetchable signed
        out, which is what makes a shared project a link anyone can open.
        """
        headers = self._bearer(token) if token else {}
        try:
            r = self._send('GET', '/get', headers=headers, params={'cid': cid},
                           timeout=max(self.timeout, 120))
        except requests.RequestException as e:
            raise StoreError(f'store unreachable at {self.url}: {e}', 503)
        if r.status_code >= 400:
            raise StoreError(f'store /get {cid} → {r.status_code}: '
                             f'{self._detail(r)}', r.status_code)
        return r.content

    def fetch_json(self, token: Optional[str], cid: str) -> Any:
        raw = self.fetch(token, cid)
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise StoreError(f'{cid} is in the store but is not the JSON this '
                             f'module writes: {e}', 415)

    def objects(self, token: str, limit: int = 200) -> List[dict]:
        out = self._call('GET', '/list', token, params={'limit': limit})
        return out.get('objects', []) if isinstance(out, dict) else []

    # ── write ─────────────────────────────────────────────────────

    def put_bytes(self, token: str, name: str, data: bytes,
                  public: bool = False,
                  content_type: str = 'application/json') -> Dict[str, Any]:
        """Upload bytes and get their CID back.

        The upload is a multipart body the store reads as a file, so it is
        rebuilt on the retry rather than replayed — `_send` would otherwise
        hand the store an already-drained stream after a wake.
        """
        if len(data) > MAX_BUNDLE:
            raise StoreError(f'{len(data)} bytes is more than this module will '
                             f'hand the store ({MAX_BUNDLE}) — a contract '
                             f'project should be far smaller than that', 413)
        headers = self._bearer(token)
        form = {'backend': 'localfs', 'key': name,
                'public': 'true' if public else 'false'}

        def attempt():
            return requests.post(f'{self.url}/put', headers=headers, data=form,
                                 files={'file': (name, data, content_type)},
                                 timeout=max(self.timeout, 120))

        try:
            r = attempt()
        except requests.ConnectionError:
            woken, why = self._wake()
            if not woken:
                raise StoreError(f'the store module is not running at '
                                 f'{self.url} — {why}', 503)
            try:
                r = attempt()
            except requests.RequestException as e:
                raise StoreError(f'the store woke but is not answering: {e}', 503)
        except requests.RequestException as e:
            raise StoreError(f'store unreachable at {self.url}: {e}', 503)
        if r.status_code >= 400:
            raise StoreError(f'store /put → {r.status_code}: {self._detail(r)}',
                             r.status_code)
        out = r.json()
        cid = self.cid_of(out)
        if not cid:
            raise StoreError(f'the store accepted the upload but returned no '
                             f'CID: {json.dumps(out)[:300]}', 502)
        out['cid'] = cid
        return out

    def put_json(self, token: str, name: str, payload: Any,
                 public: bool = False) -> Dict[str, Any]:
        body = json.dumps(payload, indent=2, sort_keys=False).encode('utf-8')
        out = self.put_bytes(token, name, body, public=public)
        out['size'] = len(body)
        return out

    @staticmethod
    def cid_of(put_result: Dict[str, Any]) -> Optional[str]:
        """Dig the CID out of a /put response.

        The store answers `{results: {<backend>: {cid: …}}}` because one upload
        can land in several backends; a single-backend put still comes back in
        that shape, so this is not defensive coding, it is the format.
        """
        if not isinstance(put_result, dict):
            return None
        if put_result.get('cid'):
            return str(put_result['cid'])
        for entry in (put_result.get('results') or {}).values():
            if isinstance(entry, dict) and entry.get('cid'):
                return str(entry['cid'])
        return None

    def publish(self, token: str, cid: str, public: bool = True) -> Dict[str, Any]:
        """Flip an object between public and private. Owner only, store-side."""
        return self._call('POST', '/publish', token,
                          json={'cid': cid, 'public': bool(public)})

    def remove(self, token: str, cid: str) -> Dict[str, Any]:
        return self._call('DELETE', '/rm', token, params={'cid': cid})

    # ── the whole picture ─────────────────────────────────────────

    def peek(self) -> Dict[str, Any]:
        """Is the port open right now — no wake, no retry, no waiting.

        For the paths that only want to *report* the store's state. Waking it
        costs up to WAKE_TIMEOUT, and a caller who is not about to upload
        anything should never pay that.
        """
        try:
            r = requests.get(f'{self.url}/health', timeout=min(self.timeout, 3))
        except requests.RequestException as e:
            raise StoreError(f'the store is not answering at {self.url}: '
                             f'{type(e).__name__} (it may just be asleep — '
                             f'saving a project will wake it)', 503)
        if not r.ok:
            raise StoreError(f'store /health → {r.status_code}', r.status_code)
        return r.json()

    def status(self, token: Optional[str] = None,
               wake: bool = True) -> Dict[str, Any]:
        """Is the store there, and what may this caller do with it?

        Never raises: an unreachable store or an unauthorised caller is a
        *state* the console has to render, not an error that takes a page down.

        `wake=False` for anything on a page's critical path. The activator
        sleeps idle modules, and a console whose first paint waits out a wake
        looks broken for the same forty-five seconds it takes to fix itself.
        """
        out: Dict[str, Any] = {'url': self.url, 'module': STORE_MODULE,
                               'reachable': False}
        try:
            # With waking on, this is also what brings a slept store back, so
            # opening the projects list is enough to restore the bridge.
            out['health'] = self.health() if wake else self.peek()
            out['reachable'] = True
        except StoreError as e:
            out['error'] = e.message
            out['blockers'] = [e.message]
            out['can_share'] = False
            return out
        if not token:
            out['authenticated'] = False
            out['blockers'] = ['sign in to save a project to the store']
            out['can_share'] = False
            return out
        out['authenticated'] = True
        try:
            me = self.me(token)
            out['address'] = me.get('address')
            out['authorized'] = bool(me.get('authorized'))
            out['admin'] = bool(me.get('admin'))
            out['quota'] = me.get('quota')
            terms = me.get('terms') or {}
            out['terms'] = terms
            out['terms_accepted'] = bool(terms.get('accepted'))
        except StoreError as e:
            out['error'] = e.message
            out['authorized'] = False
        blockers = []
        if out.get('authorized') is False:
            blockers.append(f"{out.get('address') or 'this address'} is not on "
                            'the store whitelist — the store owner has to add '
                            'it (or the address has to hold BlocTime)')
        if not out.get('terms_accepted'):
            blockers.append("the store's terms have not been accepted — "
                            'one signed call does it')
        out['blockers'] = blockers
        out['can_share'] = out.get('reachable') and not blockers
        return out


LINK = StoreLink()
