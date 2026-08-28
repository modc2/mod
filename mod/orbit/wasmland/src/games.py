"""
The arena bridge.

A game is not a special kind of listing here — it is an artifact whose exports
happen to implement the game ABI, which the registry already knows because it
read the binary. What this file adds is the one thing wasmland cannot do
itself: hand that game to the arena, where agents play it.

The split is deliberate and the arena stays its own mod:

    wasmland   stores it, prices it, runs it, verifies it
    arena      seats players against it, rates them, keeps the board

So `send(artifact)` calls the arena's own publish, which checks the ABI again
from the bytes (it does not take wasmland's word for it), writes the game its
own module in orbit/, and registers it with the running arena server. Both
mods read the same bytes from the same `blobs/<sha256>` key in the store, so
nothing is copied and the two never drift.

If the arena isn't installed, or isn't running, this is a listing that says
"playable: not here". That is the honest failure and it is not fatal — the
marketplace half works with no arena at all.
"""
from typing import Any, Dict, List, Optional

from . import market, storage


def is_game(record: Dict[str, Any]) -> bool:
    """Does this artifact implement the game ABI? Read from its manifest."""
    return (record.get('manifest') or {}).get('role') == 'game'


def arena():
    """The arena mod, or None if this box doesn't carry one."""
    try:
        return storage.protocol().mod('arena')()
    except Exception:
        return None


def available() -> Dict[str, Any]:
    """Whether games published here can actually reach an arena."""
    mod = arena()
    if mod is None:
        return {'arena': False, 'why': 'no arena module on this box'}
    up = False
    try:
        up = bool(mod._up())
    except Exception:
        pass
    return {'arena': True, 'server_up': up, 'url': getattr(mod, 'server_url', None),
            'note': (None if up else
                     'the arena module is here but its server is stopped — a '
                     'game can still be published, and becomes playable when '
                     '`m arena/serve` runs')}


def send(artifact_id: str, name: str = '', description: str = '',
         author: str = '', tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Publish a stored artifact to the arena as a game."""
    record = storage.artifact_record(artifact_id)
    if not record:
        raise ValueError(f'no artifact {artifact_id[:12]} in the store')
    if not is_game(record):
        role = (record.get('manifest') or {}).get('role', 'module')
        raise ValueError(
            f'this artifact is a {role}, not a game — the arena seats games, '
            'which are modules exporting game_init/view/step/done/result')
    mod = arena()
    if mod is None:
        raise ValueError('no arena module on this box to publish to')

    import base64
    data = storage.get_artifact(artifact_id)
    card = mod.publish(b64=base64.b64encode(data).decode(),
                       name=name or record.get('filename', '').removesuffix('.wasm'),
                       description=description, author=author, tags=tags or [])
    if isinstance(card, dict) and card.get('error'):
        raise ValueError(card['error'])
    storage.put_record('games', card['name'], {
        'id': card['name'], 'artifact': artifact_id, 'mod': card.get('mod'),
        'cid': card.get('cid'), 'created': card.get('created'),
        'author': author, 'registered': bool(card.get('registered')),
    })
    return card


def publish_listing(listing_id: str, caller: str) -> Dict[str, Any]:
    """Send a listing's artifact to the arena. Only the seller may."""
    got = market.listing(listing_id)
    if not market._is_owner(got, caller):
        raise market.MarketError(f'listing {listing_id} belongs to {got["seller"]}')
    card = send(got['artifact'], name=got['title'], description=got['description'],
                author=got['seller'], tags=got.get('tags'))
    got['game'] = card['name']
    got['playable'] = True
    storage.put_record('listings', listing_id, got)
    return card


def games(limit: int = 100) -> List[Dict[str, Any]]:
    """Games published from this marketplace, with the arena's own card where
    it can be reached."""
    out = storage.records('games', limit=limit)
    mod = arena()
    for row in out:
        try:
            row['card'] = mod.game(row['id']) if mod else None
        except Exception:
            row['card'] = None
    return out
