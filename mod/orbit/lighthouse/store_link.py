"""
The bridge between this module and the store module.

Lighthouse holds bytes forever; the **store** module holds *who may see them*.
This file is the only place the two meet, and it deliberately owns no
credentials: every call to the store carries the **caller's** mod-protocol
token, forwarded verbatim. The store then applies its own whitelist, quota and
signed terms to that address — so nothing routed through this module can reach
content the caller could not have reached by calling the store directly, and
this module never becomes a way around the store's gate.

Two directions, both of them useful:

    push    bytes → Lighthouse (perpetual IPFS/Filecoin pin) → the CID is
            registered in the store as an external object, so it gets the
            store's grants, pools, marketplace and public/private flag without
            the store ever holding the bytes.

    mirror  a CID the store already has → fetched with the caller's token →
            re-uploaded to Lighthouse → registered back. This is the "make it
            perpetual" move for something that only exists on localfs today.

A note on CIDs: the CID Lighthouse returns for mirrored bytes is usually the
same as the store's (both are IPFS v0/v1 over the same content), but chunking
differences can produce a different one. Nothing here assumes they match — the
Lighthouse CID is what gets registered, and the caller is told both.

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
STORE_MODULE = os.environ.get('LIGHTHOUSE_STORE_MODULE', 'store')
TIMEOUT = float(os.environ.get('LIGHTHOUSE_STORE_TIMEOUT', 30))
WAKE_TIMEOUT = float(os.environ.get('LIGHTHOUSE_WAKE_TIMEOUT', 45))


class StoreError(Exception):
    """The store said no (or did not answer). Carries its status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message


def store_url() -> str:
    """Where the store lives: env override, else config.json, else localhost."""
    env = os.environ.get('LIGHTHOUSE_STORE_URL')
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
    env = os.environ.get('LIGHTHOUSE_ACTIVATOR_URL')
    if env is not None:
        return env.rstrip('/')
    return DEFAULT_ACTIVATOR


def _protocol():
    """protocol.py, by path — this file is imported both as part of the API
    (module root on sys.path) and straight out of mod.py, and only one of those
    has the directory on the path."""
    try:
        import protocol
        return protocol
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location('lighthouse_protocol',
                                                      DIR / 'protocol.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def local_token(data: Optional[dict] = None, key=None) -> str:
    """Mint a protocol token with this box's own mod key (CLI use).

    The browser console mints the same envelope with the visitor's wallet; the
    store cannot tell the two apart, which is the point of the protocol auth.
    """
    return _protocol().auth().token(data or {'mod': 'lighthouse'}, key=key)


def local_address(key=None) -> str:
    """The address a CLI push will be recorded under."""
    return _protocol().protocol().key(key).address if key else \
        _protocol().protocol().key().address


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

    def _wake(self) -> bool:
        """Ask the fleet's activator to start the store, and wait for its port.

        The activator (core/server/activator) stops modules that have been idle
        for a while and starts them again when a request arrives *through it*.
        We talk to the store on its own port, which the activator never sees —
        so without this knock a slept store stays slept from our side forever,
        and the console reports a perfectly healthy module as dead.

        The knock is an ordinary proxied request rather than the
        `/_activator/control` "wake" action on purpose. Control-wake clears the
        host's `actl disable` flag, and a peer module has no business
        overriding a deliberate "keep this off"; the proxy path honours it
        (503) and, unlike control-wake, waits on the *api* port — the one we
        are about to use — instead of the app port.

        Returns (worth retrying, why not). The reason comes back rather than
        being stashed on self: one StoreLink is shared by every request the API
        serves, and two callers racing on an instance attribute would report
        each other's failure.
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
        """One request to the store, with a single wake-and-retry when the port
        is closed. Bodies here are JSON or query params — never a consumed file
        handle — so replaying the request is safe."""
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
            detail = r.text[:400]
            try:
                detail = r.json().get('detail', detail)
            except Exception:
                pass
            raise StoreError(f'store {path} → {r.status_code}: {detail}',
                             r.status_code)
        try:
            return r.json()
        except ValueError:
            return {'raw': r.text[:2000]}

    # ── read ──────────────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        return self._call('GET', '/health')

    def me(self, token: str) -> Dict[str, Any]:
        """Caller as the *store* sees them: whitelisted, quota, terms."""
        return self._call('GET', '/me', token)

    def terms(self, token: Optional[str] = None) -> Dict[str, Any]:
        return self._call('GET', '/terms', token)

    def accept_terms(self, token: str) -> Dict[str, Any]:
        """Sign-accept the store's terms — the caller's own token is the proof."""
        return self._call('POST', '/terms/accept', token)

    def backends(self, token: str) -> Dict[str, Any]:
        return self._call('GET', '/backends/status', token)

    def objects(self, token: str, limit: int = 200,
                only_lighthouse: bool = True) -> List[dict]:
        """The caller's store objects, narrowed to the ones this module put there."""
        out = self._call('GET', '/list', token, params={'limit': limit})
        objs = out.get('objects', []) if isinstance(out, dict) else []
        if only_lighthouse:
            objs = [o for o in objs if o.get('backend') == 'lighthouse']
        return objs

    def object_info(self, token: str, cid: str) -> Dict[str, Any]:
        return self._call('GET', '/object', token, params={'cid': cid})

    def fetch(self, token: str, cid: str, out: Path) -> Path:
        """Stream a store object to disk with the caller's read rights."""
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = self._send('GET', '/get', headers=self._bearer(token),
                           params={'cid': cid}, stream=True,
                           timeout=max(self.timeout, 120))
        except requests.RequestException as e:
            raise StoreError(f'store unreachable at {self.url}: {e}', 503)
        if r.status_code >= 400:
            raise StoreError(f'store /get {cid} → {r.status_code}: {r.text[:200]}',
                             r.status_code)
        with open(out, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return out

    # ── write ─────────────────────────────────────────────────────

    def register(self, token: str, cid: str, key: Optional[str] = None,
                 size: Optional[int] = None, url: Optional[str] = None,
                 public: bool = False, pool: Optional[str] = None) -> Dict[str, Any]:
        """Reference a Lighthouse CID in the store — no bytes move.

        The store is CID-agnostic by design: this is exactly the case it was
        built for. It records the gateway url so `store /get` can redirect a
        reader straight at Lighthouse.
        """
        # Only send what we actually know. The store's RegisterBody types
        # `size` as a plain int with a default, so a null there is a 422 — an
        # unknown size has to be an *absent* field, not an explicit None.
        body: Dict[str, Any] = {'cid': cid, 'backend': 'lighthouse',
                                'scheme': 'ipfs', 'public': bool(public)}
        for name, value in (('key', key), ('size', size), ('url', url),
                            ('pool', pool)):
            if value is not None:
                body[name] = value
        return self._call('POST', '/register', token, json=body)

    # ── the whole picture ─────────────────────────────────────────

    def status(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Is the store there, and what may this caller do with it?

        Never raises: an unreachable store or an unauthorised caller is a
        *state* the console has to render, not an error that should take a page
        down with it.
        """
        out: Dict[str, Any] = {'url': self.url, 'reachable': False}
        try:
            # This also wakes the store when the activator has slept it, so
            # simply opening the console is enough to bring the bridge back.
            out['health'] = self.health()
            out['reachable'] = True
        except StoreError as e:
            # A store that is down is still a state with a next action in it —
            # say what to do rather than leaving the console with a dead panel
            # and an empty blocker list.
            out['error'] = e.message
            out['blockers'] = [e.message]
            out['can_push'] = False
            return out
        if not token:
            out['authenticated'] = False
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
        try:
            out['lighthouse_backend'] = (self.backends(token)
                                         .get('backends', {})
                                         .get('lighthouse'))
        except StoreError:
            pass
        # What the caller still has to do before a push can land.
        blockers = []
        if out.get('authorized') is False:
            blockers.append(f"{out.get('address') or 'this address'} is not on "
                            "the store whitelist — the store owner must add it")
        if out.get('authenticated') and not out.get('terms_accepted'):
            blockers.append('the store terms have not been accepted '
                            '(POST /store/terms/accept)')
        out['blockers'] = blockers
        out['can_push'] = out.get('reachable') and not blockers
        return out
