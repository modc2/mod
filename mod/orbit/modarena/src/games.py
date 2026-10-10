"""
Uploaded mods, each one its own mod — one level up.

A mod in this registry is already a folder: a config.json and an anchor. What
publishing does is take that folder out of the registry and put it in the
fleet, where the same shape means the same thing. Three things happen, and the
order matters:

    1. the folder is read     — the ABI is checked against the anchor itself,
                                not against what the uploader said it was, and
                                the config has to agree with the anchor
    2. the folder is stored   — every file in the store mod, under the SHA-256
                                of its own bytes, at the shared `blobs/<hash>`
                                key that any mod on this box can read
    3. a mod is written       — orbit/<slug>/, with a fleet config.json, a
                                mod.py that answers for the game, and `mod/` —
                                the arena folder itself, source and all

That third step is the point, and it is why the directory sits in orbit/ next
to every other module rather than tucked under this one: the fleet's tree scans
orbit one level deep, so a game nested inside the arena would be a folder, and
a game beside it is a mod. `m snake`, `m snake/abi`, `m snake/bytes` — a game
you can call, that agents play because the arena can serve it, and that
anything else in the fleet can find because it is a module like any other.

WHAT IS ON DISK, AND WHAT ISN'T
    The minted mod is a pointer, never a copy. The repo gets a config.json and
    a twelve-line mod.py; the wasm stays in the store. So publishing a game
    does not commit somebody's binary to this repository, and deleting the
    directory does not lose the game.

The export reader below duplicates about thirty lines that wasmland also has.
That is deliberate: this mod validates uploads whether or not wasmland is
installed, and a shared parser would make the arena depend on a marketplace it
does not otherwise need.
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

DIR = os.path.dirname(os.path.abspath(__file__))
ARENA_DIR = os.path.dirname(DIR)
ORBIT_DIR = os.path.dirname(ARENA_DIR)          # where mods live, one level deep
STORE_PREFIX = 'modarena/mods'
FOLDER_DIR = 'mod'          # where the arena folder lands inside the published mod
ANCHORS = {'python': 'mod.py', 'rust': 'mod.rs', 'wasm': 'mod.wasm'}
TEXT_EXT = ('.py', '.rs', '.json', '.md', '.txt')
BLOBS = 'blobs'

GAME_ABI = ('game_init', 'game_view', 'game_step', 'game_done', 'game_result')
GAME_OPTIONAL = ('game_info', 'game_turn')
GAME_METHODS = ('view', 'step', 'done', 'result')
SLUG = re.compile(r'[^a-z0-9-]+')


class GameError(ValueError):
    """The upload is not a game, or not one this arena can accept."""


def protocol():
    """`import mod` — the protocol package, not some mod's own mod.py."""
    import importlib
    import sys
    from pathlib import Path
    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        return got
    mine = {DIR, os.path.dirname(DIR)}
    saved = list(sys.path)
    sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path
                    if p and str(Path(p).resolve()) not in mine]
        return importlib.import_module('mod')
    finally:
        sys.path = saved


def store():
    return protocol().mod('store')()


# ── reading the binary ───────────────────────────────────────────────

def _leb(data: bytes, pos: int):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def exports(data: bytes) -> List[str]:
    """The export names, read off the module's own export section."""
    if len(data) < 8 or data[:4] != b'\x00asm':
        raise GameError('not a wasm module (missing the \\0asm magic)')
    names, pos = [], 8
    while pos < len(data):
        section, pos = _leb(data, pos)
        size, pos = _leb(data, pos)
        end = pos + size
        if section == 7:
            count, cursor = _leb(data, pos)
            for _ in range(count):
                length, cursor = _leb(data, cursor)
                names.append(data[cursor:cursor + length].decode('utf-8', 'replace'))
                cursor += length + 1
                _, cursor = _leb(data, cursor)
        pos = end
    return names


def check(data: bytes) -> Dict[str, Any]:
    """Is this a game? Answered by the exports, with the missing ones named."""
    found = exports(data)
    missing = [fn for fn in GAME_ABI if fn not in found]
    if missing:
        raise GameError(
            'this module does not implement the game ABI — missing '
            f'{", ".join(missing)}. It exports: {", ".join(found) or "nothing"}. '
            'See `m modarena/abi` for the contract.')
    if 'alloc' not in found:
        raise GameError('a game must export `alloc(i32) -> i32` — without it '
                        'the host cannot pass it a state string')
    return {'exports': found,
            'optional': [fn for fn in GAME_OPTIONAL if fn in found]}


def slugify(text: str) -> str:
    return SLUG.sub('-', (text or '').lower()).strip('-')[:40] or 'game'



# ── folders ──────────────────────────────────────────────────────────

def manifest(files: Dict[str, Any]) -> str:
    """The listing a mod's id is the hash of — byte for byte what the server
    computes in `folder.rs`. Written twice on purpose: an id nobody else can
    recompute is an id nobody can check, and this is the second opinion."""
    import hashlib
    lines = [PROTOCOL]
    anchor = anchor_of(files)
    if anchor:
        lines.append(f'anchor {anchor}')
    for path in sorted(files):
        raw = as_bytes(files[path])
        lines.append(f'{hashlib.sha256(raw).hexdigest()} {len(raw)} {path}')
    return '\n'.join(lines) + '\n'


def folder_id(files: Dict[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(manifest(files).encode()).hexdigest()


def as_bytes(body: Any) -> bytes:
    """A file from a folder — text as itself, {"b64": …} decoded."""
    import base64
    if isinstance(body, bytes):
        return body
    if isinstance(body, dict):
        return base64.b64decode(body.get('b64') or body.get('bytes') or '')
    return str(body).encode()


def anchor_of(files: Dict[str, Any]) -> Optional[str]:
    config = config_of(files)
    declared = (config or {}).get('anchor')
    if declared and declared in files:
        return declared
    for name in ANCHORS.values():
        if name in files:
            return name
    return None


def config_of(files: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if 'config.json' not in files:
        return None
    try:
        return json.loads(as_bytes(files['config.json']).decode())
    except Exception:
        return None


def check_folder(files: Dict[str, Any]) -> Dict[str, Any]:
    """Is this a mod? The same question the server answers, asked here so that
    publishing works with the arena stopped. It is the shorter reading — the
    server's is the one that decides what may be stored — but it catches the
    failures that matter: no config, no anchor, or a config that claims
    something its anchor does not define."""
    config = config_of(files)
    if config is None:
        raise GameError('no readable config.json — a mod is a folder with one, '
                        'and `m modarena/template` prints the folder')
    for key in ('name', 'kind', 'lang', 'anchor'):
        if not config.get(key):
            raise GameError(f'config.json declares no {key}')
    if config.get('protocol') != PROTOCOL:
        raise GameError(f'config.json should say "protocol": "{PROTOCOL}"')
    lang, kind = config['lang'], config['kind']
    want = ANCHORS.get(lang)
    if not want:
        raise GameError(f'lang {lang!r} is not one of {", ".join(ANCHORS)}')
    if config['anchor'] != want or want not in files:
        raise GameError(f'a {lang} mod is anchored on {want}, and this folder '
                        f'has {", ".join(sorted(files)) or "nothing"}')
    raw = as_bytes(files[want])
    if lang == 'wasm':
        found = exports(raw)
        missing = [fn for fn in GAME_ABI if fn not in found] if kind == 'game' else \
                  ([] if 'play' in found else ['play'])
        role = 'game' if not [fn for fn in GAME_ABI if fn not in found] else \
               ('player' if 'play' in found else 'wasm')
    else:
        source = raw.decode('utf-8', 'replace')
        keyword = 'def' if lang == 'python' else 'fn'
        defines = set(re.findall(rf'\b{keyword}\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', source))
        role = 'game' if set(GAME_METHODS) <= defines else ('player' if 'play' in defines else 'class')
        missing = [m for m in (GAME_METHODS if kind == 'game' else ('play',)) if m not in defines]
        found = sorted(defines)
    if missing:
        raise GameError(f'config.json says {kind!r}, and the anchor is still missing '
                        f'{", ".join(missing)}. `m modarena/verify path=…` says the same '
                        'thing with the rest of the checks.')
    if role != kind:
        raise GameError(f'config.json says {kind!r} but the anchor defines a {role!r} — '
                        'the config is a claim and the anchor is the fact')
    return {'config': config, 'role': role, 'lang': lang, 'defines': list(found),
            'id': folder_id(files), 'anchor': want}


# ── publishing ───────────────────────────────────────────────────────

def publish(data: bytes, name: str = '', description: str = '',
            author: str = '', tags: Optional[List[str]] = None,
            artifact: str = '') -> Dict[str, Any]:
    """Store an uploaded game and mint its mod. Returns the game card."""
    import hashlib
    abi = check(data)
    artifact_id = artifact or hashlib.sha256(data).hexdigest()
    slug = slugify(name or f'game-{artifact_id[:8]}')

    import base64
    s = store()
    s.put_json(f'{BLOBS}/{artifact_id}', {'b64': base64.b64encode(data).decode()})

    cid = None
    try:
        cid = protocol().mod('localfs')().put(data)
    except Exception:
        pass

    card = {
        'name': slug,
        'title': name or slug,
        'description': description or f'A wasm game, {len(data)} bytes.',
        'protocol': 'arena/1.0',
        'role': 'game',
        'engine': 'wasm',
        'artifact': artifact_id,
        'blob': f'{BLOBS}/{artifact_id}',
        'cid': cid,
        'bytes': len(data),
        'author': author,
        'tags': tags or [],
        'abi': {'required': list(GAME_ABI), 'optional': abi['optional'],
                'strings': 'alloc(i32)->i32; returns are one i64 packed as (ptr << 32) | len'},
        'exports': abi['exports'],
        'created': time.time(),
    }
    s.put_json(f'{STORE_PREFIX}/{slug}', card)
    card['mod'] = mint(card)
    return card


def folder_for(slug: str) -> str:
    return os.path.join(ORBIT_DIR, slug)


def _mine(folder: str) -> bool:
    """Is this directory one of ours, or somebody else's module?"""
    config = os.path.join(folder, 'config.json')
    if not os.path.isfile(config):
        return False
    try:
        with open(config) as f:
            return json.load(f).get('protocol') == 'modarena/1.0'
    except Exception:
        return False


def mint(card: Dict[str, Any]) -> str:
    """Write orbit/<slug>/ — a real mod whose bytes live in the store."""
    slug = card['name']
    folder = folder_for(slug)
    if os.path.isdir(folder) and not _mine(folder):
        raise GameError(
            f'orbit/{slug} is already a module and it is not a game — '
            'publish this under another name')
    os.makedirs(folder, exist_ok=True)

    config = {
        'name': slug,
        'description': card['description'],
        'version': '1.0.0',
        'protocol': 'arena/1.0',
        'role': 'game',
        'engine': 'wasm',
        'anchor': 'mod.py',
        'artifact': card['artifact'],
        'blob': card['blob'],
        'cid': card.get('cid'),
        'bytes': card['bytes'],
        'author': card.get('author', ''),
        'tags': card.get('tags', []),
        'abi': card['abi'],
        'exports': card['exports'],
        'plays_in': 'arena',
        'stored_in': 'store',
    }
    with open(os.path.join(folder, 'config.json'), 'w') as f:
        json.dump(config, f, indent=4)
        f.write('\n')
    with open(os.path.join(folder, 'mod.py'), 'w') as f:
        f.write(MOD_TEMPLATE.format(slug=slug, title=card.get('title', slug),
                                    description=card['description'].replace('"""', "'''")))
    # The fleet's module tree is cached; without this the game exists on disk
    # and `m <slug>` still says no such mod until something else refreshes it.
    try:
        protocol().tree(update=True)
    except Exception:
        pass
    return folder


MOD_TEMPLATE = '''"""{title} — a wasm game in the arena.

{description}

The bytes are not here. They live in the store mod under the hash of
themselves, which is what `bytes()` fetches — so this directory is a pointer
and the game survives it being deleted.
"""
import json
import os


class Mod:
    path = os.path.dirname(os.path.abspath(__file__))

    def config(self):
        with open(os.path.join(self.path, 'config.json')) as f:
            return json.load(f)

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        """The card: what this game is, and where its bytes are."""
        return self.config()

    def abi(self):
        """The contract this module implements."""
        return self.config()['abi']

    def bytes(self):
        """The wasm, fetched from the store mod."""
        import base64
        import mod as m
        blob = m.mod('store')().get(self.config()['blob'])
        return base64.b64decode(blob['b64'])

    def play(self, players, seed: int = None):
        """Play a match of this game. The arena runs it and rates the result."""
        import mod as m
        return m.mod('arena')().play(game='{slug}', players=players, seed=seed)
'''


# ── reading them back ────────────────────────────────────────────────

def cards(limit: int = 200) -> List[Dict[str, Any]]:
    """Every published game, from the store rather than from the directory."""
    out = []
    root = os.path.join(str(getattr(store(), 'path', '')), STORE_PREFIX)
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if not name.endswith('.json'):
            continue
        card = store().get(f'{STORE_PREFIX}/{name[:-5]}')
        if card:
            out.append(card)
    out.sort(key=lambda c: c.get('created', 0), reverse=True)
    return out[:limit]


def card(slug: str) -> Dict[str, Any]:
    got = store().get(f'{STORE_PREFIX}/{slug}')
    if not got:
        raise GameError(f'no published game {slug!r}')
    return got


def blob(slug: str) -> bytes:
    """A published game's bytes, checked against the hash they are filed under."""
    import base64
    import hashlib
    got = card(slug)
    stored = store().get(got['blob'])
    if not stored:
        raise GameError(f'{slug} has a card but no bytes in the store')
    data = base64.b64decode(stored['b64'])
    if hashlib.sha256(data).hexdigest() != got['artifact']:
        raise GameError(f'{slug} bytes do not match their hash — the store copy has changed')
    return data


def remove(slug: str) -> Dict[str, Any]:
    """Unpublish. The blob stays: other games may be the same bytes, and a
    match transcript that can't be replayed is not a transcript."""
    import shutil
    got = card(slug)
    try:
        store().rm(f'{STORE_PREFIX}/{slug}')
    except Exception:
        pass
    folder = folder_for(slug)
    if os.path.isdir(folder) and _mine(folder):
        shutil.rmtree(folder)
    return {'unpublished': slug, 'blob_kept': got['blob']}
