"""passport - one identity for the whole fleet.

    p = Passport()
    p.verify(token)           -> {"address", "scheme", "time"}   raises ValueError
    p.address(token)          -> "0x…" or None                   never raises
    p.role(token, mod="x")    -> {"address", "owner", "is_owner", …}
    p.protocol()              -> protocol.json, as currently on disk

Nothing about the protocol is frozen into a process: protocol.json is re-read
and src/schemes.py re-imported whenever their mtime changes. That is what lets
the protocol change under a running fleet — modules hold a Passport (or talk
to the passport service), never a copy of the rules.
"""
import hashlib
import importlib.util
import json
import os
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORBIT = os.path.dirname(ROOT)
PROTOCOL_PATH = os.path.join(ROOT, 'protocol.json')
SCHEMES_PATH = os.path.join(HERE, 'schemes.py')


class Passport:
    description = 'The fleet auth protocol: verify any accepted token, resolve roles'

    def __init__(self, protocol_path: str = PROTOCOL_PATH, schemes_path: str = SCHEMES_PATH):
        self.protocol_path = protocol_path
        self.schemes_path = schemes_path
        self._lock = threading.Lock()
        self._proto = (None, {})      # (mtime, parsed)
        self._schemes = (None, None)  # (mtime, module)
        self._cache = {}              # sha256(token) -> (expires_at, verdict | error str)

    # ── the live protocol ────────────────────────────────────────────

    def protocol(self) -> dict:
        try:
            mt = os.path.getmtime(self.protocol_path)
        except OSError:
            return self._proto[1]
        if mt != self._proto[0]:
            with self._lock:
                try:
                    with open(self.protocol_path) as f:
                        self._proto = (mt, json.load(f))
                    self._cache.clear()   # new rules: old verdicts no longer stand
                except Exception:
                    pass                  # half-written edit: keep the last good one
        return self._proto[1]

    def schemes(self):
        mt = os.path.getmtime(self.schemes_path)
        if mt != self._schemes[0]:
            with self._lock:
                spec = importlib.util.spec_from_file_location(
                    f'passport_schemes_{int(mt * 1000)}', self.schemes_path)
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                    self._schemes = (mt, mod)
                    self._cache.clear()
                except Exception:
                    if self._schemes[1] is None:
                        raise
                    # broken edit: keep serving the last good verifier
        return self._schemes[1]

    def fingerprint(self) -> str:
        """Changes whenever the protocol does — clients can watch it."""
        h = hashlib.sha256()
        for p in (self.protocol_path, self.schemes_path):
            try:
                with open(p, 'rb') as f:
                    h.update(f.read())
            except OSError:
                pass
        return h.hexdigest()[:16]

    # ── identity ─────────────────────────────────────────────────────

    def verify(self, token: str) -> dict:
        proto = self.protocol()
        schemes = self.schemes()
        token = (token or '').strip()
        k = hashlib.sha256(token.encode()).hexdigest()
        hit = self._cache.get(k)
        now = time.time()
        if hit and hit[0] > now:
            if isinstance(hit[1], str):
                raise ValueError(hit[1])
            return dict(hit[1])
        ttl = float(proto.get('cache_ttl', 60))
        try:
            out = schemes.verify(token, proto)
        except ValueError as e:
            self._cache[k] = (now + min(ttl, 10), str(e))
            raise
        except Exception as e:   # a verifier that cannot reach its authority
            raise ValueError(f'could not verify: {str(e)[:120]}')
        # a verdict never outlives the token's own max age
        max_age = float(((proto.get('schemes') or {}).get(out['scheme']) or {}).get('max_age', 86400))
        exp = min(now + ttl, float(out.get('time', now)) + max_age)
        self._cache[k] = (exp, out)
        if len(self._cache) > 5000:
            self._cache = {a: b for a, b in self._cache.items() if b[0] > now}
        return dict(out)

    def address(self, token: str):
        try:
            return self.verify(token)['address']
        except Exception:
            return None

    # ── authorization: who owns a module ─────────────────────────────

    @staticmethod
    def owners_of(mod: str) -> dict:
        """Owner + co-owners of a module. The off-tree ~/.mod/<mod>/owner.json
        wins over the in-tree config.json (editors can write the tree)."""
        mod = (mod or '').strip().lower()
        owner, co = None, []
        try:
            with open(os.path.expanduser(f'~/.mod/{mod}/owner.json')) as f:
                d = json.load(f)
            owner = d.get('owner')
            co = d.get('co_owners') or []
        except Exception:
            pass
        if not owner:
            try:
                with open(os.path.join(ORBIT, *mod.split('.'), 'config.json')) as f:
                    owner = json.load(f).get('owner')
            except Exception:
                pass
        return {'owner': (owner or '').lower() or None,
                'co_owners': [str(a).lower() for a in co]}

    def role(self, token: str, mod: str = None) -> dict:
        try:
            v = self.verify(token)
        except ValueError as e:
            return {'signed_in': False, 'address': None, 'is_owner': False, 'error': str(e)}
        addr = v['address']
        out = {'signed_in': True, 'address': addr, 'scheme': v['scheme'], 'mod': mod}
        if not mod:
            return out
        if mod == 'build':
            # build has richer roles (editor, invite scope) — it answers itself
            url = ((self.protocol().get('schemes') or {}).get('build') or {}).get('role_url')
            try:
                with urllib.request.urlopen(
                        f"{url}?{urllib.parse.urlencode({'address': addr})}", timeout=4) as r:
                    return {**out, **json.loads(r.read()), 'address': addr}
            except Exception:
                pass
        o = self.owners_of(mod)
        out.update(owner=o['owner'], is_owner=bool(o['owner']) and (
            addr == o['owner'] or addr in o['co_owners']))
        return out
