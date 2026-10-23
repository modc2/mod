"""Signatures, borrowed from the chain that will have to verify them.

A receipt signed with a key an attacker can forge in 2030 is not a receipt,
and a receipt postquant cannot check is not worth anchoring. Both problems
have the same answer: do not invent a key type here. tensorcash loads
ML-DSA (FIPS 204) and SLH-DSA (FIPS 205) straight out of the postquant
module's src/pq/ — the same pure-python implementations the chain verifies
witnesses with — and derives addresses with postquant's rule:

    address = 'pq' + SHA3-256('pq-addr\\x00', addr_domain, pk)[:20]

ML-DSA carries an EMPTY addr_domain (its addresses predate postquant's key
type registry and are already on chain); every other scheme hashes its name
in. Get that wrong and the address in a receipt would not be the address on
the chain, so it is pinned by a test, not by comment.

The consequence worth stating: a tensorcash worker address IS a postquant
address, and a tci1 receipt's signature verifies inside postquant with
`algos.get(scheme).verify` and no tensorcash code at all. That is why
postquant's inference proof system can accept one.

POSTQUANT_SRC (or a sibling orbit/postquant) says where to find it. With
nothing to load, tensorcash still runs inference and still commits to
traces — it just cannot sign, and says so rather than falling back to
something weaker.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys

ADDRESS_PREFIX = 'pq'
DEFAULT_SCHEME = 'ML-DSA-44'

# scheme -> (module attribute, addr_domain)
SCHEMES = {
    'ML-DSA-44': ('mldsa', b''),
    'ML-DSA-65': ('mldsa', b''),
    'ML-DSA-87': ('mldsa', b''),
    'SLH-DSA-SHAKE-128f': ('slhdsa', b'SLH-DSA-SHAKE-128f\x00'),
    'SLH-DSA-SHAKE-192f': ('slhdsa', b'SLH-DSA-SHAKE-192f\x00'),
    'SLH-DSA-SHAKE-256f': ('slhdsa', b'SLH-DSA-SHAKE-256f\x00'),
}

HERE = os.path.dirname(os.path.abspath(__file__))
_SIBLING = os.path.normpath(os.path.join(HERE, '..', '..', 'postquant', 'src'))
PQ_SRC = os.path.expanduser(os.environ.get('TENSORCASH_POSTQUANT_SRC')
                            or _SIBLING)

_MODS: dict = {}
_ERROR = None


class SigError(ValueError):
    def __init__(self, message, code='bad_sig', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _load():
    """Import postquant's mldsa/slhdsa by PATH, under private names.

    By path and not `import mldsa` because tensorcash must not take
    postquant's whole src/ onto sys.path — api.py, keys.py and mcp.py are
    names both modules use, and whichever imported first would win. The
    private prefix keeps them out of everyone else's way too."""
    global _ERROR
    if _MODS or _ERROR:
        return _MODS
    pqdir = os.path.join(PQ_SRC, 'pq')
    for name in ('mldsa', 'slhdsa'):
        path = os.path.join(pqdir, name + '.py')
        try:
            spec = importlib.util.spec_from_file_location(
                '_tc_pq_' + name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f'cannot load {path}')
            mod = importlib.util.module_from_spec(spec)
            sys.modules['_tc_pq_' + name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:                            # noqa: BLE001
            _ERROR = (f'cannot load postquant\'s {name} from {pqdir} '
                      f'({e}) — set TENSORCASH_POSTQUANT_SRC to the '
                      'postquant module\'s src/ directory')
            _MODS.clear()
            return _MODS
        _MODS[name] = mod
    return _MODS


def available() -> bool:
    return bool(_load())


def status() -> dict:
    _load()
    out = {'available': bool(_MODS), 'source': PQ_SRC,
           'schemes': sorted(SCHEMES) if _MODS else []}
    if _ERROR:
        out['reason'] = _ERROR
    return out


def _mod(scheme):
    mods = _load()
    if not mods:
        raise SigError(_ERROR or 'no signature backend', 'no_crypto', 503)
    if scheme not in SCHEMES:
        raise SigError(f'unknown scheme {scheme!r} — '
                       f'{", ".join(sorted(SCHEMES))}', 'bad_scheme')
    return mods[SCHEMES[scheme][0]]


def sizes(scheme=DEFAULT_SCHEME) -> dict:
    return dict(_mod(scheme).sizes(scheme))


def keygen(scheme=DEFAULT_SCHEME, seed: bytes | None = None):
    m = _mod(scheme)
    if seed is None:
        seed = os.urandom(32)
    if len(seed) != 32:
        raise SigError('seed must be 32 bytes', 'bad_seed')
    return m.keygen_internal(seed, scheme)


def sign(scheme: str, sk: bytes, msg: bytes, ctx: bytes = b'') -> bytes:
    return _mod(scheme).sign(sk, msg, scheme, ctx=ctx)


def verify(scheme: str, pk: bytes, msg: bytes, sig: bytes,
           ctx: bytes = b'') -> bool:
    try:
        return bool(_mod(scheme).verify(pk, msg, sig, scheme, ctx=ctx))
    except SigError:
        raise
    except Exception:                                     # noqa: BLE001
        return False                                      # fail closed


def address(pk: bytes, scheme: str = DEFAULT_SCHEME) -> str:
    if scheme not in SCHEMES:
        raise SigError(f'unknown scheme {scheme!r}', 'bad_scheme')
    h = hashlib.sha3_256()
    h.update(b'pq-addr\x00')
    h.update(SCHEMES[scheme][1])
    h.update(pk)
    return ADDRESS_PREFIX + h.digest()[:20].hex()


def valid_address(addr) -> bool:
    return (isinstance(addr, str) and addr.startswith(ADDRESS_PREFIX)
            and len(addr) == len(ADDRESS_PREFIX) + 40
            and all(c in '0123456789abcdef' for c in addr[2:]))
