"""Choosing the engine, and being able to say why.

Three policies, because there are only three questions anyone actually asks:

    fast     what finishes soonest here — measured, not advertised
    cheap    what costs the least money; local is free, so local wins
    best     the largest model that is available, whatever it costs

The rule the router will not break: it never picks an engine that cannot run,
and it never silently substitutes the stub for a recogniser. If the only thing
available is the stub, the caller is told that, and has to ask for it by name.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import engines, ledger

POLICIES = ('fast', 'cheap', 'best')

# Rough quality order, best last — used only by policy='best'.
QUALITY = {'stub': 0, 'whisper.cpp': 3, 'whisper-torch': 4, 'faster-whisper': 5,
           'deepinfra': 6, 'groq': 7, 'openai': 7}

# What we assume before we have measured anything on this machine.
GUESS_RTF = {'faster-whisper': 0.12, 'whisper-torch': 0.55, 'whisper.cpp': 0.25,
             'openai': 0.15, 'groq': 0.05, 'deepinfra': 0.10, 'stub': 0.001}


def _score(card: Dict[str, Any], policy: str) -> float:
    name = card['name']
    measured = ledger.rtf(name, card.get('model'))
    speed = measured if measured is not None else GUESS_RTF.get(name, 1.0)
    if policy == 'cheap':
        return (card.get('cost_per_min', 0.0) * 1000) + speed
    if policy == 'best':
        return -QUALITY.get(name, 1)
    return speed


def options(policy: str = 'fast', **overrides: Any) -> List[Dict[str, Any]]:
    """Every engine ranked under this policy, unavailable ones last."""
    cards = engines.catalog(**overrides)
    for card in cards:
        measured = ledger.rtf(card['name'], card.get('model'))
        card['measured_rtf'] = measured
        card['assumed_rtf'] = GUESS_RTF.get(card['name'])
        card['score'] = _score(card, policy)
    # Unavailable engines sink; the stub sinks below them, because it is never a
    # real answer to "which one should I use" however cheap and fast it looks.
    return sorted(cards, key=lambda c: (c['name'] == 'stub',
                                        not c.get('available'), c['score']))


def choose(prefer: Optional[str] = None, policy: str = 'fast',
           allow_stub: bool = False, **overrides: Any) -> Dict[str, Any]:
    """The decision, with the reasoning attached — nothing here is a black box."""
    if policy not in POLICIES:
        raise ValueError(f'policy must be one of {POLICIES}')

    ranked = options(policy, **overrides)
    by_name = {c['name']: c for c in ranked}

    if prefer:
        wanted = engines.ALIASES.get(prefer, prefer) or prefer
        card = by_name.get(wanted)
        if card is None:
            raise KeyError(f'unknown engine {prefer!r} — {list(by_name)}')
        if not card.get('available'):
            raise RuntimeError(f'{wanted} cannot run here: {card.get("note")}')
        return {'engine': wanted, 'why': f'asked for by name', 'card': card,
                'policy': policy, 'ranked': ranked}

    for card in ranked:
        if not card.get('available'):
            continue
        if card['name'] == 'stub' and not allow_stub:
            continue
        why = {'fast': 'fastest available here', 'cheap': 'cheapest available here',
               'best': 'best model available here'}[policy]
        if card.get('measured_rtf') is not None:
            why += f' (measured rtf {card["measured_rtf"]})'
        return {'engine': card['name'], 'why': why, 'card': card,
                'policy': policy, 'ranked': ranked}

    raise RuntimeError(
        'no recogniser is installed. Pick one: `pip install faster-whisper` (fastest '
        'local), `pip install transformers torch`, a whisper.cpp binary, or set a key '
        'with `m sound2text/set_key vendor=groq key=…`. '
        'For a pipeline dry run without a model, pass engine=stub.')
