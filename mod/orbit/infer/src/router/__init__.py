"""The router registry.

Adding a router to this module is: write one adapter subclassing Provider, add
it here. Nothing else knows a provider's name.

Order is the default display order and it encodes the brief: the routers that
ask for nothing but money come first, the ones that want an account after, and
nothing that wants a document is in the file at all.
"""

from .base import (CAPS, KYC_LEVELS, MODALITIES, NeedsKey, Offering, Provider,
                   RouterError, Unsupported, http, model_key, set_key)
from .chutes import Chutes
from .ionet import IoNet
from .nanogpt import NanoGPT
from .openrouter import OpenRouter
from .ppq import PPQ
from .price import Price, sniff, to_mtok
from .venice import Venice

REGISTRY = {p.name: p for p in (
    NanoGPT, Venice, PPQ, Chutes, IoNet, OpenRouter)}

__all__ = ['REGISTRY', 'get', 'every', 'CAPS', 'KYC_LEVELS', 'MODALITIES',
           'Offering', 'Price', 'Provider', 'RouterError', 'NeedsKey',
           'Unsupported', 'http', 'model_key', 'set_key', 'to_mtok', 'sniff']


def get(name, keys=None):
    """One provider, constructed with the caller's key if they sent one."""
    cls = REGISTRY.get(str(name or '').strip().lower())
    if not cls:
        raise RouterError(f'unknown provider: {name} — have {", ".join(REGISTRY)}',
                          provider=name)
    return cls(key=(keys or {}).get(cls.name))


def every(keys=None, names=None, cap=None, kyc='none', coin=None):
    """The providers a request should touch.

    `kyc` is a *ceiling*, not an equality test: `kyc='account'` means "anything
    that asks for an account or less", so the strict default `kyc='none'` admits
    only routers that want nothing but money. `kyc=None` disables the filter
    entirely and is the only way to reach a provider whose policy is unread —
    an unverified claim is never quietly treated as a passing one.
    """
    want = [n.strip().lower() for n in (
        names.split(',') if isinstance(names, str) else (names or [])) if n.strip()]
    ceiling = KYC_LEVELS.index(kyc) if kyc in KYC_LEVELS else None
    out = []
    for name, cls in REGISTRY.items():
        if want and name not in want:
            continue
        if cap and cap not in cls.caps:
            continue
        if ceiling is not None:
            if cls.kyc not in KYC_LEVELS or KYC_LEVELS.index(cls.kyc) > ceiling:
                continue
        if coin and coin.upper() not in {c.upper() for c in cls.pay}:
            continue
        out.append(cls(key=(keys or {}).get(name)))
    return out
