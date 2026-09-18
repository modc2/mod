"""Every stored module, as a mod of its own — inside the arena.

Somebody uploads a game or an agent. Three things happen, and the order
matters:

    1. the bytes are read     — the role is checked against the file itself:
                                a wasm module's export section, a Python
                                class's `def`s, a Rust struct's `impl` block.
                                Never against what the uploader said.
    2. the bytes are stored   — under the SHA-256 of the bytes, in the arena's
                                own blob store and (for a published game) in
                                the store mod at the shared `blobs/<hash>` key
    3. a mod is written       — orbit/arena/mods/<slug>/, with a config.json
                                and a mod.py that talks to the arena about
                                itself

That third step is the point. `m arena.nim`, `m arena.nim/open`,
`m arena.nim/source` — a game you can call, an agent you can ask, and an entry
in the fleet's module tree like anything else.

WHY NESTED, AND WHY THAT IS NOT THE OBVIOUS CHOICE
    The fleet's tree scans orbit one level deep by default, so the easy thing
    is to write orbit/<slug>/ and be found immediately. That is what this
    module used to do. The trouble with it is that it is a lie about
    ownership: a game is not a peer of the arena, it is a thing inside the
    arena, and forty uploaded games sitting beside forty real modules makes
    the fleet's own directory unreadable.

    So they go in orbit/arena/mods/, and the tree finds them anyway. `mods` is
    one of the names the tree drops when it turns a path into a name, and the
    search widens its depth when a name does not resolve at one — so the
    directory `orbit/arena/mods/nim` is the module `arena.nim`, and `m nim`
    still finds it. The nesting says what is true; the name says where it
    lives.

WHAT IS ON DISK, AND WHAT ISN'T
    The minted mod is a pointer, never a copy. The directory gets a
    config.json and a mod.py; the bytes stay in the arena and the store. So
    minting somebody's upload does not commit their code to this repository,
    and deleting the directory does not lose the module.
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

DIR = os.path.dirname(os.path.abspath(__file__))
ARENA_DIR = os.path.dirname(DIR)
ORBIT_DIR = os.path.dirname(ARENA_DIR)

# Inside the arena, not beside it. `mods` is a name the fleet's tree drops
# when it derives a module name from a path, which is what makes
# orbit/arena/mods/nim answer to `arena.nim` rather than `arena.mods.nim`.
MODS_DIR = os.path.join(ARENA_DIR, 'mods')

STORE_PREFIX = 'arena/games'
BLOBS = 'blobs'

GAME_ABI = ('game_init', 'game_view', 'game_step', 'game_done', 'game_result')
GAME_OPTIONAL = ('game_info', 'game_turn')
SLUG = re.compile(r'[^a-z0-9-]+')

#: Roles worth minting. A `class` that is neither a game nor a player yet has
#: nothing to offer a caller, and a bare wasm blob has no ABI to expose.
MINTABLE = ('game', 'player', 'command')


class GameError(ValueError):
    """The upload is not what it claims, or not one this arena can accept."""


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
            'See `m arena/abi` for the contract.')
    if 'alloc' not in found:
        raise GameError('a game must export `alloc(i32) -> i32` — without it '
                        'the host cannot pass it a state string')
    return {'exports': found,
            'optional': [fn for fn in GAME_OPTIONAL if fn in found]}


def slugify(text: str) -> str:
    return SLUG.sub('-', (text or '').lower()).strip('-')[:40] or 'game'


# ── publishing ───────────────────────────────────────────────────────

def publish(data: bytes, name: str = '', description: str = '',
            author: str = '', tags: Optional[List[str]] = None,
            artifact: str = '') -> Dict[str, Any]:
    """Store an uploaded wasm game in the store mod and mint its mod.

    The door wasmland hands games through. An ordinary upload does not need
    this — it goes to the arena, and `sync` mints it from the registry.
    """
    import base64
    import hashlib
    abi = check(data)
    artifact_id = artifact or hashlib.sha256(data).hexdigest()
    slug = slugify(name or f'game-{artifact_id[:8]}')

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
        'lang': 'wasm',
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


# ── minting ──────────────────────────────────────────────────────────

def folder_for(slug: str) -> str:
    return os.path.join(MODS_DIR, slug)


def legacy_folder_for(slug: str) -> str:
    """Where these used to be written: beside the arena rather than inside it."""
    return os.path.join(ORBIT_DIR, slug)


def _mine(folder: str) -> bool:
    """Is this directory one of ours, or somebody else's module?"""
    config = os.path.join(folder, 'config.json')
    if not os.path.isfile(config):
        return False
    try:
        with open(config) as f:
            return json.load(f).get('protocol') == 'arena/1.0'
    except Exception:
        return False


def card_from_module(module: Dict[str, Any], base: str = '') -> Dict[str, Any]:
    """A mint card out of what the arena's registry already knows.

    Everything here was read off the bytes by the server. Nothing is taken
    from the uploader, and nothing is recomputed here — a second reader would
    be a second thing to disagree with.
    """
    slug = slugify(module.get('name') or f"module-{module.get('id', '')[:8]}")
    role = module.get('role') or 'class'
    lang = module.get('lang') or 'wasm'
    return {
        'name': slug,
        'title': module.get('name') or slug,
        'description': module.get('description') or f'A {lang} {role} in the arena.',
        'protocol': 'arena/1.0',
        'role': role,
        'engine': lang,
        'lang': lang,
        'artifact': module.get('id', ''),
        'short': module.get('short') or module.get('id', '')[:12],
        'bytes': module.get('size', 0),
        'author': module.get('author', ''),
        'tags': module.get('tags') or [],
        'defines': module.get('exports') or [],
        'mcp': f"{base.rstrip('/')}/m/{module.get('name')}/mcp" if base else '',
        'created': module.get('created') or time.time(),
    }


def mint(card: Dict[str, Any]) -> str:
    """Write orbit/arena/mods/<slug>/ — a real mod pointing at the arena."""
    slug = card['name']
    folder = folder_for(slug)
    if os.path.isdir(folder) and not _mine(folder):
        raise GameError(
            f'{folder} is already a module and it is not one of ours — '
            'publish this under another name')
    os.makedirs(folder, exist_ok=True)

    role = card.get('role', 'game')
    config = {
        'name': slug,
        'description': card['description'],
        'version': '1.0.0',
        'protocol': 'arena/1.0',
        'role': role,
        'engine': card.get('engine', 'wasm'),
        'lang': card.get('lang', card.get('engine', 'wasm')),
        'anchor': 'mod.py',
        'artifact': card.get('artifact', ''),
        'bytes': card.get('bytes', 0),
        'author': card.get('author', ''),
        'tags': card.get('tags', []),
        'defines': card.get('defines', []),
        'mcp': card.get('mcp', ''),
        # Not a service of its own: no port, no route. It is a face on the
        # arena, and the arena is what listens.
        'serves': 'arena',
        'plays_in': 'arena',
        'nested_in': 'arena',
    }
    if card.get('blob'):
        config['blob'] = card['blob']
        config['stored_in'] = 'store'
    if card.get('cid'):
        config['cid'] = card['cid']
    if card.get('abi'):
        config['abi'] = card['abi']

    with open(os.path.join(folder, 'config.json'), 'w') as f:
        json.dump(config, f, indent=4)
        f.write('\n')
    with open(os.path.join(folder, 'mod.py'), 'w') as f:
        f.write(mod_source(card))

    refresh_tree()
    return folder


def refresh_tree(depths=(1, 2, 3)) -> bool:
    """Rebuild the fleet's module tree, including the depths a nested mod needs.

    The tree is cached per depth, and orbit is scanned one level deep by
    default — which is exactly the level a mod inside the arena is *not* at.
    Refreshing only depth 1, as this used to, left `arena.nim` existing on
    disk and unfindable until something else happened to widen the search.
    """
    try:
        proto = protocol()
        root = proto.mod()()
        orbit = root.paths['orbit']['orbit']
        for depth in depths:
            root.get_tree(orbit, depth=depth, update=True)
        return True
    except Exception:
        return False


def mod_source(card: Dict[str, Any]) -> str:
    """The mod.py for one minted module — common part, then its role's part."""
    role = card.get('role', 'game')
    body = COMMON
    if role == 'game':
        body += GAME_PART
    elif role == 'player':
        body += PLAYER_PART
    else:
        body += OTHER_PART
    kind = {'game': 'game', 'player': 'agent'}.get(role, role)
    return HEADER.format(
        title=card.get('title', card['name']),
        kind=kind,
        lang=card.get('lang', 'wasm'),
        description=(card.get('description') or '').replace('"""', "'''"),
        slug=card['name'],
    ) + body


HEADER = '''"""{title} — a {lang} {kind} in the arena.

{description}

Nothing of it is here. The bytes live in the arena under the hash of
themselves, and this directory is a pointer: a config.json, and the methods
below, which are a thin face over two things the arena already serves —

    the REST API      http://…/api/arena
    its own MCP server  /m/{slug}/mcp — this module alone, as a server

`mcp_config()` prints the block that points an MCP client straight at it.
"""
import json
import os
import urllib.error
import urllib.request


class Mod:
    path = os.path.dirname(os.path.abspath(__file__))
    slug = '{slug}'
'''

COMMON = '''
    def config(self):
        with open(os.path.join(self.path, 'config.json')) as f:
            return json.load(f)

    def _base(self):
        """The arena, from wherever this is called."""
        base = os.environ.get('ARENA_BASE')
        if base:
            return base.rstrip('/')
        try:
            import mod as m
            return m.mod('arena')()._up().rstrip('/')
        except Exception:
            return 'http://127.0.0.1:50470'

    def _rpc(self, method, params=None, timeout=300):
        """One JSON-RPC call to this module's own MCP server."""
        body = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {},
        }).encode()
        url = f'{self._base()}/m/{self.slug}/mcp'
        req = urllib.request.Request(
            url, data=body, headers={'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
        except urllib.error.URLError as e:
            return {'error': f'{url} is unreachable: {e} — is the arena running? '
                             '`m arena/serve`'}
        if 'error' in out:
            return {'error': out['error'].get('message', 'the call failed')}
        return out.get('result', {})

    def call(self, tool: str, **arguments):
        """Call one tool on this module's own MCP server."""
        out = self._rpc('tools/call', {'name': tool, 'arguments': arguments})
        if 'error' in out:
            return out
        if out.get('isError'):
            return {'error': (out.get('content') or [{}])[0].get('text', 'failed')}
        if 'structuredContent' in out:
            return out['structuredContent']
        return out

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        """The card: what this is, where its bytes are, what it can be asked."""
        live = self.call('about')
        return live if 'error' not in live else self.config()

    def mcp(self):
        """This module's own MCP endpoint, and what it offers there."""
        return {
            'url': f'{self._base()}/m/{self.slug}/mcp',
            'transport': 'streamable-http',
            'tools': self.tools(),
        }

    def tools(self):
        out = self._rpc('tools/list')
        return out.get('tools', out)

    def mcp_config(self):
        """A client config block pointing at this one module."""
        return {'mcpServers': {self.slug: {
            'type': 'http', 'url': f'{self._base()}/m/{self.slug}/mcp'}}}

    def source(self):
        """The module itself — the source of a class, the exports of a binary."""
        return self.call('source')

    def abi(self):
        """The contract this module implements."""
        config = self.config()
        if config.get('abi'):
            return config['abi']
        import mod as m
        lang = {'python': 'class', 'rust': 'rust'}.get(config.get('lang'), 'wasm')
        return m.mod('arena')().abi(role=config.get('role', 'game'), lang=lang)
'''

GAME_PART = '''
    # ── playing it, a turn at a time ──────────────────────────────────
    #
    # A table is its seed and the moves played at it, and nothing else. Every
    # call replays it from the start, so a table survives the arena
    # restarting and the same seed and moves are the same game anywhere.

    def open(self, seats: int = None, seed: int = None):
        """Sit down. Returns a table id and the opening view for every seat."""
        args = {}
        if seats:
            args['seats'] = int(seats)
        if seed is not None:
            args['seed'] = int(seed)
        return self.call('open', **args)

    def view(self, table: str, seat: int = 0):
        """What one seat can see — the whole of what a player is entitled to."""
        return self.call('view', table=table, seat=int(seat))

    def move(self, table: str, move: str = None, seat: int = None, moves: dict = None):
        """Play one move. `moves` instead, for a game where seats move at once."""
        args = {'table': table}
        if moves:
            args['moves'] = moves
        else:
            args['move'] = move
            if seat is not None:
                args['seat'] = int(seat)
        return self.call('move', **args)

    def state(self, table: str):
        """Whose move it is, every seat's view, and the result if it is over."""
        return self.call('state', table=table)

    # ── playing it out, rated ─────────────────────────────────────────

    def play(self, players, seed: int = None, turns: int = None, mcp=None):
        """Play a full match. The arena runs it and rates the result."""
        import mod as m
        return m.mod('arena')().play(game=self.slug, players=players,
                                     seed=seed, turns=turns, mcp=mcp)

    def leaderboard(self, limit: int = 10):
        """Who is good at THIS game — never the overall number."""
        return self.call('leaderboard', limit=int(limit))
'''

PLAYER_PART = '''
    # ── asking it ─────────────────────────────────────────────────────

    def ask(self, view: str, seat: int = 0, seed: int = None):
        """One question: here is what a seat can see, what do you play?

        The same question the arena asks it in a rated match, so the answer
        is the answer.
        """
        args = {'view': view, 'seat': int(seat)}
        if seed is not None:
            args['seed'] = int(seed)
        return self.call('play', **args)

    def play(self, view: str, seat: int = 0):
        """`ask`, under the name the ABI uses."""
        return self.ask(view, seat)

    def record(self):
        """How it has done: Elo, win rate, illegal moves, timeouts, calls out."""
        return self.call('record')

    def enter(self, name: str = None, owner: str = ''):
        """Enter it at the arena under a name, so its matches are rated."""
        import mod as m
        return m.mod('arena')().enter(
            name=name or self.slug, kind='class', owner=owner,
            config={'module': self.config().get('artifact') or self.slug})
'''

OTHER_PART = '''
    # ── running it ────────────────────────────────────────────────────

    def run(self, entry: str = None, stdin: str = '', seed: int = None):
        """Run it once and report what it did."""
        args = {}
        if entry:
            args['entry'] = entry
        if stdin:
            args['stdin'] = stdin
        if seed is not None:
            args['seed'] = int(seed)
        return self.call('run', **args)
'''


# ── keeping the directory and the registry in step ───────────────────

def sync(base: str = '', prune: bool = True, migrate: bool = True) -> Dict[str, Any]:
    """Mint a mod for every module the arena holds, and drop the stale ones.

    Idempotent, and the registry is the truth: this writes what the arena
    says exists and removes what it does not. Nothing here reads bytes or
    decides roles — that already happened, once, on the way in.
    """
    import mod as _  # noqa: F401 — fail early if the protocol is not importable
    arena = protocol().mod('arena')()
    base = base or arena._up()
    listing = arena.modules()
    modules = listing.get('modules', listing) if isinstance(listing, dict) else listing
    if not isinstance(modules, list):
        raise GameError(f'the arena did not list its modules: {listing}')

    os.makedirs(MODS_DIR, exist_ok=True)
    minted, skipped, moved = [], [], []

    for module in modules:
        role = module.get('role')
        if role not in MINTABLE:
            skipped.append({'name': module.get('name'), 'role': role,
                            'why': 'not a game, an agent or a command yet'})
            continue
        card = card_from_module(module, base=base)
        # An older mint may be sitting beside the arena rather than inside it.
        if migrate:
            old = legacy_folder_for(card['name'])
            if os.path.isdir(old) and _mine(old):
                import shutil
                shutil.rmtree(old)
                moved.append(f"orbit/{card['name']} → arena/mods/{card['name']}")
        try:
            mint(card)
            minted.append({'name': card['name'], 'role': role, 'lang': card['lang'],
                           'mod': f"arena.{card['name']}"})
        except GameError as e:
            skipped.append({'name': card['name'], 'why': str(e)})

    removed = []
    if prune and os.path.isdir(MODS_DIR):
        live = {m['name'] for m in minted}
        for entry in sorted(os.listdir(MODS_DIR)):
            folder = os.path.join(MODS_DIR, entry)
            if entry in live or not os.path.isdir(folder):
                continue
            if _mine(folder):
                import shutil
                shutil.rmtree(folder)
                removed.append(entry)

    refresh_tree()
    return {
        'minted': len(minted), 'mods': minted, 'skipped': skipped,
        'removed': removed, 'migrated': moved, 'dir': MODS_DIR,
        'note': 'each one is a mod: `m arena.<name>`, and an MCP server: '
                f'{base.rstrip("/")}/m/<name>/mcp',
    }


def minted() -> List[Dict[str, Any]]:
    """What is on disk right now, read from the directories themselves."""
    if not os.path.isdir(MODS_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(MODS_DIR)):
        folder = os.path.join(MODS_DIR, entry)
        if not _mine(folder):
            continue
        with open(os.path.join(folder, 'config.json')) as f:
            config = json.load(f)
        config['mod'] = f'arena.{entry}'
        config['dir'] = folder
        out.append(config)
    return out


# ── reading published games back ─────────────────────────────────────

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
    for folder in (folder_for(slug), legacy_folder_for(slug)):
        if os.path.isdir(folder) and _mine(folder):
            shutil.rmtree(folder)
    return {'unpublished': slug, 'blob_kept': got['blob']}
