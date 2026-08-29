"""
Logo — the fleet's brand marks.

Every module in the protocol shows a mark somewhere: the cube in a console's
corner, the icon on a catalog card. This module is where that mark lives, so
the module that *displays* it does not have to be the module that *owns* it.

The rule, and the whole reason this is its own module:

    a mark may only be changed by the owner of the module it is drawn on —
    the address in that module's own config.json — proved with a mod-protocol
    token (`m.mod('auth').token`, or one `personal_sign` from a browser
    wallet).

Nothing here holds a credential that stands in for anybody. A console can
render the logo editor, take the owner's signature and forward it, and still
be unable to change a mark on its own. That is what orbit/build does.

Four faces, one behaviour:

    CLI      `m logo/glyph build 'X'` — this file
    API      api/api.py on :50760 — FastAPI, mod-protocol auth
    console  app/server.py on :50761 at /logo, proxying _api to the API
    peers    any module fetching GET /logo/{module} for a mark to draw

Name collisions: module names are path-derived and `core/` wins over `orbit/`,
so a bare `store` means core/store. Say `orbit/store` to mean the other one.

Usage (Python):
    import mod as m
    logo = m.mod('logo')()
    logo.get('build')
    logo.glyph('build', 'X')
    logo.upload('build', '/path/to/mark.png')
    logo.reset('build')

Usage (CLI):
    m logo/status
    m logo/marks
    m logo/owner build
    m logo/glyph build X
    m logo/url build https://example.com/mark.png
    m logo/reset build
"""
import base64
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

DIR = Path(__file__).resolve().parent
STATE = Path(os.path.expanduser(os.environ.get('LOGO_DIR', '~/.mod/logo')))
API_URL = os.environ.get('LOGO_API', 'http://127.0.0.1:50760').rstrip('/')

_LOCAL: Dict[str, Any] = {}


def _local() -> Dict[str, Any]:
    """This module's own `protocol` / `identity` / `marks`, imported without
    leaving them on sys.path or in sys.modules.

    Both matter. `import mod` from inside a module directory resolves to that
    module's own mod.py rather than the protocol package (see protocol.py),
    and plain names like `identity` are used by several modules in this fleet —
    a stray sys.modules entry would hand the next one ours.
    """
    if _LOCAL:
        return _LOCAL
    names = ('protocol', 'identity', 'marks')
    saved_path = list(sys.path)
    saved_mods = {n: sys.modules.pop(n, None) for n in names}
    sys.path.insert(0, str(DIR))
    try:
        import identity
        import marks
        import protocol
        _LOCAL.update(protocol=protocol, identity=identity, marks=marks)
    finally:
        for name, previous in saved_mods.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous
        sys.path[:] = saved_path
    return _LOCAL


class Mod:
    description = ("The fleet's brand-mark service — every module's logo, "
                   "changeable only by that module's owner.")
    path = str(DIR)

    fns = [
        'forward', 'get', 'set', 'glyph', 'url', 'upload', 'reset',
        'marks', 'owner', 'whoami', 'status', 'serve', 'stop', 'app', 'api',
    ]

    def __init__(self, key: str = None, api: str = None, **kw):
        # `key` is the signing key these writes are made with. None means the
        # box's default protocol key — which still has to BE the target
        # module's owner; running the CLI on the host grants nothing extra.
        self.key = key
        self.module_dir = DIR
        self.state = STATE
        self.api_url = (api or API_URL).rstrip('/')

    # -- Core API -----------------------------------------------------

    def forward(self, action: str = None, **kw):
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not fn:
            return {'error': f'unknown action: {action}'}
        return fn(**kw)

    # -- read ---------------------------------------------------------

    def get(self, module: str, base: str = None, **kw) -> Dict[str, Any]:
        """The mark a module should draw. Public — no token, no session."""
        L = _local()
        try:
            return {'module': L['identity'].owners(module)['module'],
                    'logo': L['marks'].public(module, base=base)}
        except L['identity'].UnknownModule as e:
            return {'error': str(e)}

    def marks(self, base: str = None, **kw) -> Dict[str, Any]:
        """Every module that has actually set a mark. The cube is the default,
        not a mark, so an untouched module does not appear."""
        return {'marks': _local()['marks'].marks(base)}

    def owner(self, module: str, **kw) -> Dict[str, Any]:
        """Who may change this module's mark, and where that came from."""
        L = _local()
        try:
            return L['identity'].owners(module)
        except L['identity'].UnknownModule as e:
            return {'error': str(e)}

    def whoami(self, token: str = None, **kw) -> Dict[str, Any]:
        """The address behind a token — or behind this box's signing key when
        no token is given."""
        L = _local()
        if token:
            return {'address': L['identity'].whoami(token), 'source': 'token'}
        try:
            return {'address': self._address(), 'source': 'local key'}
        except Exception as e:
            return {'error': str(e)}

    def status(self, **kw) -> Dict[str, Any]:
        L = _local()
        return {
            'module': 'logo',
            'state': str(self.state),
            'api': self.api_url,
            'marks': len(L['marks'].marks()),
            'auth': L['identity'].status(),
            'limits': {'max_image_bytes': L['marks'].MAX_IMAGE_BYTES,
                       'glyph_chars': L['marks'].MAX_GLYPH_CHARS,
                       'mime': sorted(L['marks'].ALLOWED_MIME)},
        }

    # -- write --------------------------------------------------------

    def set(self, module: str, glyph: str = None, url: str = None,
            image: str = None, reset: bool = False, token: str = None,
            **kw) -> Dict[str, Any]:
        """Set a module's mark. One of glyph / url / image / reset.

        The write goes through the SAME gate the HTTP API uses: a token is
        minted from the signing key and verified against the target module's
        declared owner. Being on the host is not a privilege here — if this
        box's key is not the owner's, this refuses exactly as the network does.
        """
        L = _local()
        body: Dict[str, Any] = {}
        if reset:
            body['reset'] = True
        elif glyph is not None:
            body['glyph'] = glyph
        elif url is not None:
            body['url'] = url
        elif image is not None:
            body['dataUrl'] = self._data_url(image)
        else:
            return {'error': 'give one of: glyph=, url=, image=, reset=True'}
        try:
            signer = L['identity'].require_owner(token or self._token(), module)
            state = L['marks'].apply(module, body, by=signer)
            return {'ok': True, 'module': L['identity'].owners(module)['module'],
                    'by': signer, 'logo': L['marks'].public(module, state)}
        except (L['identity'].AuthError, L['identity'].UnknownModule,
                L['marks'].BadMark) as e:
            return {'error': str(e)}

    def glyph(self, module: str, glyph: str, token: str = None, **kw):
        """A one-to-four character mark. The cheapest possible logo."""
        return self.set(module, glyph=glyph, token=token)

    def url(self, module: str, url: str, token: str = None, **kw):
        """A mark hosted somewhere else."""
        return self.set(module, url=url, token=token)

    def upload(self, module: str, path: str, token: str = None, **kw):
        """A mark from a local file — stored here and served back from here."""
        return self.set(module, image=path, token=token)

    def reset(self, module: str, token: str = None, **kw):
        """Back to the mod protocol's own cube."""
        return self.set(module, reset=True, token=token)

    # -- signing ------------------------------------------------------

    def _auth(self):
        return _local()['protocol'].auth(
            key=self.key, max_age=_local()['identity'].TOKEN_MAX_AGE)

    def _token(self) -> str:
        return self._auth().token({'scope': 'logo'})

    def _address(self) -> str:
        return self._auth().key.address.lower()

    def _data_url(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        if not p.is_file():
            raise FileNotFoundError(path)
        mime = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        if p.suffix.lower() == '.svg':
            mime = 'image/svg+xml'
        return f'data:{mime};base64,' + base64.b64encode(p.read_bytes()).decode()

    # -- the services -------------------------------------------------

    def serve(self, **kw) -> Dict[str, Any]:
        """Start the API and console under pm2 (./serve.sh)."""
        return self._sh(['./serve.sh'])

    def stop(self, **kw) -> Dict[str, Any]:
        return self._sh(['./serve.sh', 'stop'])

    def api(self, port: int = None, **kw) -> Dict[str, Any]:
        """The API's url (and start it in the foreground with port=)."""
        if port is None:
            return {'api': self.api_url}
        return self._sh([sys.executable, 'api/api.py', '--port', str(port)])

    def app(self, **kw) -> Dict[str, Any]:
        cfg = json.loads((DIR / 'config.json').read_text())
        return {'app': cfg.get('urls', {}).get('app'),
                'gateway': cfg.get('urls', {}).get('gateway_app')}

    def _sh(self, argv) -> Dict[str, Any]:
        try:
            out = subprocess.run(argv, cwd=str(DIR), capture_output=True,
                                 text=True, timeout=120)
            return {'ok': out.returncode == 0, 'stdout': out.stdout.strip(),
                    'stderr': out.stderr.strip()}
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'}
