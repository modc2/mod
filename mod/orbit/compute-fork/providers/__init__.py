"""The provider registry.

Adding a market to this module is: write one adapter that subclasses Provider,
add it here. Nothing else in the module knows a provider's name.
"""

from .base import (CAPS, Filters, NeedsKey, Provider, ProviderError, Unsupported,
                   instance, offer, set_key)
from .akash import Akash
from .cathedral import Cathedral
from .clore import Clore
from .hyperbolic import Hyperbolic
from .lium import Lium
from .local import Local
from .nosana import Nosana
from .polaris import Polaris
from .prime import Prime
from .shadeform import Shadeform
from .targon import Targon
from .vast import Vast

# Order is the default display order: crypto-native and no-KYC first, the fiat
# benchmark last, your own hardware after that.
REGISTRY = {p.name: p for p in (
    Targon, Lium, Akash, Vast, Clore, Nosana, Cathedral, Prime, Polaris,
    Hyperbolic, Shadeform, Local)}

__all__ = ['REGISTRY', 'get', 'every', 'CAPS', 'Filters', 'Provider', 'ProviderError',
           'NeedsKey', 'Unsupported', 'offer', 'instance', 'set_key']


def get(name, keys=None):
    """One provider, constructed with the caller's key if they sent one."""
    cls = REGISTRY.get(str(name or '').strip().lower())
    if not cls:
        raise ProviderError(f'unknown provider: {name} — '
                            f'have {", ".join(REGISTRY)}', provider=name)
    return cls(key=(keys or {}).get(cls.name))


def every(keys=None, names=None, cap=None, kyc=None):
    """The providers a request should touch, filtered the way callers ask."""
    want = [n.strip().lower() for n in (
        names.split(',') if isinstance(names, str) else (names or [])) if n.strip()]
    out = []
    for name, cls in REGISTRY.items():
        if want and name not in want:
            continue
        if cap and cap not in cls.caps:
            continue
        if kyc and cls.kyc != kyc:
            continue
        out.append(cls(key=(keys or {}).get(name)))
    return out
