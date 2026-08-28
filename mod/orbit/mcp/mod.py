"""
mcp — a hub of MCP servers.

The Model Context Protocol ecosystem is real but scattered: the official
registry, GitHub, npm, Glama, Smithery, a couple of awesome-lists, and — on
this box — the mod fleet's own servers. This module aggregates all of them into
one searchable directory, speaks MCP well enough to ask a live server what its
tools actually are, and gives anyone a place to publish their own: sign with a
browser wallet or a locally derived key, and the manifest is pinned by CID to
the store mod under *your* address.

    h = Mod()
    h.search('postgres')                     # every provider at once
    h.search('', sources=['fleet'])          # what's running here
    h.server('official:io.github.foo/bar')   # one merged card
    h.probe(url='http://localhost:50152/mcp')# live tools/list
    h.client_config('npm:@foo/bar')          # paste-ready client config
    h.submit(key='test', name='my-server', description='…', repo='https://…')

State lives off-tree in ~/.mod/mcp/ (submissions, provider + probe cache,
owner, optional GitHub token) — never in committed config.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MODULE_DIR = Path(__file__).resolve().parent
HUB_DIR = Path(os.environ.get('MCP_HUB_DIR') or os.path.expanduser('~/.mod/mcp'))


def mod_pkg():
    """The real top-level `mod` package.

    This file is itself named mod.py, so any time the module directory lands on
    sys.path — uvicorn, pytest, `python mod.py` — a bare `import mod` resolves
    to *us* instead of the package. Drop our directory for the import, then put
    the path back; the package sticks in sys.modules and un-shadows everyone.
    """
    cached = sys.modules.get('mod')
    if cached is not None and hasattr(cached, 'mod'):
        return cached
    saved = list(sys.path)
    sys.path[:] = [p for p in saved if Path(p or '.').resolve() != MODULE_DIR]
    sys.modules.pop('mod', None)
    try:
        import mod as pkg
    finally:
        sys.path[:] = saved
    return pkg


def _load(name: str):
    """Load a src/ module by file path.

    Deliberately not `from src.x import …`: half the fleet has a `src/` on
    sys.path and this module's own mod.py shadows the `mod` package whenever
    its directory lands on the path. A file-path load has neither problem.
    """
    spec = importlib.util.spec_from_file_location(
        f'_mcphub_{name}', MODULE_DIR / 'src' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry_mod = _load('registry')
probe_mod = _load('probe')
index_mod = _load('index')

Registry = registry_mod.Registry
Probe = probe_mod.Probe
Index = index_mod.Index
SubmitError = index_mod.SubmitError


class Mod:
    description = """A hub of MCP servers — aggregates the open-source Model
    Context Protocol ecosystem into one searchable directory, probes servers
    for their real tool lists, and hosts wallet-signed submissions pinned by
    CID to the store mod."""

    def __init__(self, dir: Optional[str] = None, store_url: Optional[str] = None):
        self.dir = Path(dir or HUB_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = Index(dir=str(self.dir), store_url=store_url)
        self.registry = Registry(dir=str(self.dir), hub=self.index)
        self.prober = Probe()
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())

    # ── identity ────────────────────────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        return {
            'name': 'mcp',
            'title': self.config.get('title'),
            'version': self.config.get('version'),
            'description': self.config.get('description'),
            'providers': [p['id'] for p in self.registry.providers()],
            'fns': self.config.get('fns', []),
            'urls': self.config.get('urls', {}),
            'state_dir': str(self.dir),
        }

    def owner(self) -> Optional[str]:
        """Hub admin (may delist anything). Off-tree: ~/.mod/mcp/owner.json."""
        try:
            owner = json.loads((self.dir / 'owner.json').read_text()).get('owner', '')
            return owner.lower() or None
        except Exception:
            return (self.config.get('owner') or '').lower() or None

    # ── directory ───────────────────────────────────────────────────

    def sources(self) -> List[Dict[str, Any]]:
        """The provider catalog: what each one indexes, and its cache TTL."""
        return self.registry.providers()

    def search(self, q: str = '', sources: Optional[List[str]] = None,
               limit: int = 40, oss: bool = True, transport: str = '',
               license: str = '', tag: str = '', category: str = '',
               sort: str = 'relevance') -> Dict[str, Any]:
        """Search every provider at once. `oss=False` also returns servers with
        no public source."""
        if isinstance(sources, str):
            sources = [s for s in sources.replace(' ', '').split(',') if s]
        return self.registry.search(q=q, sources=sources, limit=int(limit),
                                    oss=bool(oss), transport=transport,
                                    license=license, tag=tag, category=category,
                                    sort=sort)

    def server(self, id: str) -> Dict[str, Any]:
        """One server card, merged across every provider that lists it, with
        the last probe result attached when we have one."""
        rec = self.registry.server(id)
        cached = self._probe_cache_get(probe_mod.remote_url(rec) or '')
        if cached:
            rec = {**rec, 'probe': cached, 'tools': cached.get('tool_count')}
        return rec

    def stats(self) -> Dict[str, Any]:
        """Hub totals — cheap enough for a status bar."""
        fleet = self.registry.src_fleet('', 100)
        return {
            'providers': len(registry_mod.PROVIDERS),
            'fleet_servers': len(fleet),
            'fleet': [f['name'] for f in fleet],
            'probes': len(list((self.dir / 'probes').glob('*.json')))
            if (self.dir / 'probes').exists() else 0,
            'cache': self.registry.cache_state(),
            **self.index.stats(),
        }

    # ── live protocol ───────────────────────────────────────────────

    def _probe_path(self, url: str) -> Path:
        import hashlib
        d = self.dir / 'probes'
        d.mkdir(parents=True, exist_ok=True)
        return d / f'{hashlib.sha256(url.encode()).hexdigest()[:24]}.json'

    def _probe_cache_get(self, url: str, ttl: int = 900) -> Optional[Dict]:
        if not url:
            return None
        try:
            blob = json.loads(self._probe_path(url).read_text())
        except Exception:
            return None
        return blob if time.time() - blob.get('probed_at', 0) <= ttl else None

    def probe(self, url: Optional[str] = None, id: Optional[str] = None,
              token: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
        """Handshake with a live MCP server and list its real tools.

        Give a URL, or an id whose card advertises a remote endpoint. stdio
        servers can't be probed — running one means executing someone else's
        command on this box — so those return a clear reason instead.
        """
        if not url and id:
            rec = self.registry.server(id)
            url = probe_mod.remote_url(rec)
            if not url:
                return {'ok': False, 'id': id, 'stdio_only': True,
                        'error': 'this server is stdio-only — install it in your '
                                 'own client; the hub only probes remote endpoints',
                        'install': rec.get('install')}
        if not url:
            raise ValueError('probe needs a url= or an id=')
        if not refresh:
            cached = self._probe_cache_get(url)
            if cached:
                return {**cached, 'cached': True}
        result = self.prober.probe(url, token=token)
        try:
            self._probe_path(url).write_text(json.dumps(result))
        except Exception:
            pass
        return result

    def client_config(self, id: str, client: str = 'claude') -> Dict[str, Any]:
        """Paste-ready MCP client config (and a `claude mcp add` line)."""
        return probe_mod.client_config(self.registry.server(id), client=client)

    # ── publishing ──────────────────────────────────────────────────

    def _token(self, token: Optional[str] = None, key: Optional[str] = None) -> str:
        """A protocol token: the caller's, or one minted from a local mod key
        (the CLI path — the browser signs its own with a wallet)."""
        if token:
            return token
        m = mod_pkg()
        # The signing key goes in the constructor: Auth.token() stamps the
        # envelope with *self.key*'s address regardless of its key= argument,
        # so passing it there would sign with one key and claim another.
        auth = m.mod('auth')(key=m.key(key or 'test'), crypto_type='ecdsa')
        return auth.token({'mcp': 'submit'})

    @staticmethod
    def verify(token: str, max_age: Optional[int] = None) -> str:
        """Protocol token → lowercase signer address. Raises on bad/expired."""
        ttl = int(max_age or os.environ.get('MCP_SESSION_TTL') or 86400 * 7)
        headers = mod_pkg().mod('auth')(crypto_type='ecdsa', max_age=ttl).verify(token)
        addr = str(headers.get('key', '')).lower()
        if not addr.startswith('0x'):
            raise ValueError('token is missing a signer address')
        return addr

    def terms(self, token: Optional[str] = None, key: Optional[str] = None,
              accept: bool = False) -> Dict[str, Any]:
        """store's publisher terms, which a manifest pin requires.

        Proxied through the hub so publishing is one flow: sign in, sign the
        terms, publish — the signature is still the publisher's own.
        """
        if accept:
            return self.index.accept_terms(self._token(token, key))
        return self.index.terms(token)

    def submit(self, token: Optional[str] = None, key: Optional[str] = None,
               **body) -> Dict[str, Any]:
        """Publish an MCP server to the hub.

        The manifest is pinned to the store mod as *your* object (your token,
        your address, your quota) and the hub records the CID. Needs at least a
        name, a description, and one of: repo / remote endpoint / package.
        """
        tok = self._token(token, key)
        address = self.verify(tok)
        return self.index.submit(address, body, tok)

    def submissions(self, mine: Optional[str] = None) -> List[Dict[str, Any]]:
        """Servers published to this hub (all, or `mine=<address>`)."""
        return self.index.list(author=mine)

    def repin(self, id: str, token: Optional[str] = None,
              key: Optional[str] = None) -> Dict[str, Any]:
        """Retry a manifest pin that failed (store down, terms unsigned)."""
        tok = self._token(token, key)
        return self.index.repin(id, self.verify(tok), tok)

    def delist(self, id: str, token: Optional[str] = None,
               key: Optional[str] = None) -> Dict[str, Any]:
        """Remove your own submission (the hub owner may remove any)."""
        address = self.verify(self._token(token, key))
        return self.index.remove(id, address, admin=(address == self.owner()))

    # ── housekeeping ────────────────────────────────────────────────

    def clear_cache(self) -> Dict[str, int]:
        return self.registry.clear_cache()

    def readme(self):
        """Return the project README."""
        p = MODULE_DIR / 'README.md'
        return p.read_text() if p.exists() else None

    def test(self) -> bool:
        """Smoke test: the fleet provider is offline-safe, so it always works."""
        fleet = self.registry.src_fleet('', 50)
        assert isinstance(fleet, list)
        assert all(r['source'] == 'fleet' for r in fleet)
        assert self.info()['name'] == 'mcp'
        return True
