"""crates — playlists you own, and the sharing that makes them worth keeping.

A playlist here is a plain JSON file under ``~/.mod/crates/playlists/<owner>/``.
Nothing about it is on a chain and nothing is in the repo: it is private state
on this box, addressed by whoever proved they own it.

Two ways to be an owner, because a DJ console that demands a wallet before you
can save an order of tracks is a console nobody saves anything in:

* **A wallet.** ``Authorization: Bearer <mod token>`` — the protocol's own
  token, minted by ``m`` or by a browser wallet's ``personal_sign``. The
  recovered address is the owner id, so the same playlists follow you to any
  browser and any agent that can sign.
* **A guest key.** A 32-byte random secret the console makes on first use and
  keeps in localStorage. The owner id is ``guest:<sha256(secret)[:24]>``, so
  the secret itself is never written down here — it is a bearer credential:
  whoever holds it is that guest, and losing it loses the playlists.

Sharing is a second, separate id. ``share()`` mints ``sh_…`` and writes it into
an index; anyone holding that id can READ the playlist and copy it into their
own library, and nobody but the owner can change it. A shared playlist is
unlisted by default — it is in the public directory only if you ask for it.

Every write goes through :func:`_save`, which writes a temp file and renames it
over the old one, so a half-written playlist is never a readable playlist.
"""

import hashlib
import json
import os
import random
import re
import string
import time
from pathlib import Path

STATE_DIR = Path.home() / '.mod' / 'crates'
PL_DIR = STATE_DIR / 'playlists'
SHARE_INDEX = STATE_DIR / 'shares.json'

# What a track row keeps. The console and the crate both speak this shape; a
# platform item is squeezed into it on the way in and comes back out ready to
# load onto a deck without another round trip.
TRACK_FIELDS = ('key', 'source', 'id', 'bc_id', 'kind', 'name', 'artists',
                'album', 'art', 'url', 'embed', 'duration_ms', 'streamable',
                'bpm', 'camelot', 'note', 'added')

MAX_TRACKS = 500
MAX_PLAYLISTS = 200


class PlaylistError(Exception):
    """Anything the caller can fix: no such playlist, not yours, bad input."""


# ── identity ─────────────────────────────────────────────────────────────

def _auth():
    """The protocol's shared auth module, or None if it cannot be reached.

    ``m.mod('auth')`` is the canonical identity for the whole fleet — the same
    class the CLI mints with. ``auth.base`` is a different, incompatible scheme
    (it signs a nonce); do not swap it in here or tokens stop verifying.
    """
    try:
        import mod as m
        return m.mod('auth')()
    except Exception:                                           # noqa: BLE001
        return None


def who(token=None, guest=None) -> dict:
    """Who is calling: a wallet address, a guest key, or nobody.

    Returns ``{'id', 'kind', 'address'|'guest', 'anon'}``. A caller with no
    credential at all is ``anon`` — they can still read shared playlists and
    the public directory, and that is all.
    """
    token = (token or '').strip()
    if token:
        auth = _auth()
        if auth is None:
            raise PlaylistError('token auth is unavailable on this deployment — '
                                'use a guest key instead')
        try:
            headers = auth.verify(token)
        except Exception as e:                                  # noqa: BLE001
            raise PlaylistError(f'bad token: {e}')
        addr = str((headers or {}).get('key') or '').lower()
        if not addr:
            raise PlaylistError('token carries no key')
        return {'id': addr, 'kind': 'wallet', 'address': addr, 'anon': False}
    guest = (guest or '').strip()
    if guest:
        if len(guest) < 16:
            raise PlaylistError('a guest key must be at least 16 characters — '
                                'the console makes a random 64-character one')
        digest = hashlib.sha256(guest.encode()).hexdigest()[:24]
        return {'id': 'guest:' + digest, 'kind': 'guest', 'guest': digest,
                'anon': False}
    return {'id': None, 'kind': 'anon', 'anon': True}


def _owner(token=None, guest=None) -> dict:
    me = who(token, guest)
    if me['anon']:
        raise PlaylistError('sign in first: pass a mod-protocol token, or a '
                            'guest key the console keeps for you')
    return me


def _owner_dir(owner_id: str) -> Path:
    """One directory per owner, named by a hash so no address is a path."""
    slug = hashlib.sha256(owner_id.encode()).hexdigest()[:32]
    return PL_DIR / slug


# ── storage ──────────────────────────────────────────────────────────────

def _rand(n=12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(random.SystemRandom().choice(alphabet) for _ in range(n))


def new_guest_key() -> str:
    """A fresh guest secret — what the console generates on first use."""
    return hashlib.sha256(os.urandom(32)).hexdigest()


def _read(path: Path):
    try:
        with path.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _save(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp-' + _rand(6))
    with tmp.open('w') as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _shares() -> dict:
    return _read(SHARE_INDEX) or {}


def _write_shares(idx: dict) -> None:
    _save(SHARE_INDEX, idx)


# ── shaping ──────────────────────────────────────────────────────────────

def _clean_name(name, fallback='untitled') -> str:
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    return (name or fallback)[:120]


def _track_key(t: dict) -> str:
    """A stable identity for a track row, so the same track is never twice.

    The console builds the same key client side (``source:id``); a row that
    arrives without one gets it from whatever it does have.
    """
    if t.get('key'):
        return str(t['key'])
    src = t.get('source') or 'unknown'
    ident = t.get('id') or t.get('url') or t.get('name') or _rand(8)
    return f'{src}:{ident}'


def normalize(track) -> dict:
    """One crate item → one playlist row, with only the fields we keep."""
    if isinstance(track, str):
        try:
            track = json.loads(track)
        except json.JSONDecodeError:
            raise PlaylistError('track must be a JSON object')
    if not isinstance(track, dict):
        raise PlaylistError('track must be an object')
    row = {k: track.get(k) for k in TRACK_FIELDS if track.get(k) is not None}
    row['name'] = _clean_name(track.get('name') or track.get('title'), 'untitled')
    row['source'] = str(track.get('source') or 'unknown')
    row['kind'] = 'track'
    row['key'] = _track_key(track)
    row.setdefault('streamable', track.get('streamable', True))
    row['added'] = round(time.time(), 3)
    return row


def _summary(doc: dict) -> dict:
    """The card the console lists — everything but the tracks themselves."""
    tracks = doc.get('tracks') or []
    timed = [t for t in tracks if t.get('duration_ms')]
    ms = sum(int(t['duration_ms']) for t in timed)
    sources = sorted({t.get('source') for t in tracks if t.get('source')})
    return {
        'id': doc.get('id'), 'name': doc.get('name'), 'note': doc.get('note') or '',
        'count': len(tracks), 'duration_ms': ms or None,
        # How many of those tracks actually knew their length: Bandcamp search
        # rows do not carry one, and a running time that silently counts only
        # half a playlist is worse than one that admits it.
        'timed': len(timed), 'sources': sources,
        'art': next((t.get('art') for t in tracks if t.get('art')), None),
        'created': doc.get('created'), 'updated': doc.get('updated'),
        'shared': bool(doc.get('share_id')), 'share_id': doc.get('share_id'),
        'listed': bool(doc.get('listed')),
        'copied_from': doc.get('copied_from'),
        'owner_kind': doc.get('owner_kind'),
    }


def _path(owner_id: str, pid: str) -> Path:
    if not re.fullmatch(r'pl_[a-z0-9]{6,24}', str(pid or '')):
        raise PlaylistError(f'not a playlist id: {pid!r}')
    return _owner_dir(owner_id) / f'{pid}.json'


def _load_own(owner_id: str, pid: str) -> dict:
    doc = _read(_path(owner_id, pid))
    if doc is None:
        raise PlaylistError(f'no playlist {pid} in your library')
    return doc


# ── the library ──────────────────────────────────────────────────────────

def mine(token=None, guest=None) -> dict:
    """Every playlist this caller owns, newest change first."""
    me = _owner(token, guest)
    d = _owner_dir(me['id'])
    items = []
    if d.exists():
        for p in d.glob('pl_*.json'):
            doc = _read(p)
            if doc:
                items.append(_summary(doc))
    items.sort(key=lambda x: x.get('updated') or 0, reverse=True)
    return {'owner': me['id'], 'kind': me['kind'], 'count': len(items),
            'items': items}


def create(name=None, note='', tracks=None, token=None, guest=None) -> dict:
    """Start a playlist. ``tracks`` may be a list of crate items or empty."""
    me = _owner(token, guest)
    d = _owner_dir(me['id'])
    if d.exists() and len(list(d.glob('pl_*.json'))) >= MAX_PLAYLISTS:
        raise PlaylistError(f'{MAX_PLAYLISTS} playlists is the limit — '
                            'delete one first')
    if isinstance(tracks, str):
        try:
            tracks = json.loads(tracks)
        except json.JSONDecodeError:
            raise PlaylistError('tracks must be a JSON array')
    rows, seen = [], set()
    for t in (tracks or [])[:MAX_TRACKS]:
        row = normalize(t)
        if row['key'] in seen:
            continue
        seen.add(row['key'])
        rows.append(row)
    now = round(time.time(), 3)
    doc = {'id': 'pl_' + _rand(10), 'name': _clean_name(name, 'new playlist'),
           'note': str(note or '')[:400], 'owner': me['id'],
           'owner_kind': me['kind'], 'created': now, 'updated': now,
           'tracks': rows, 'share_id': None, 'listed': False}
    _save(_path(me['id'], doc['id']), doc)
    return doc


def open_(id=None, share=None, token=None, guest=None) -> dict:
    """One playlist in full — yours by id, or anyone's by share id.

    A share id read this way comes back with ``mine: false`` so a console can
    show it read-only rather than pretending it can be edited.
    """
    share = (share or '').strip()
    if not share and str(id or '').startswith('sh_'):
        share, id = id, None
    if share:
        entry = _shares().get(share)
        if not entry:
            raise PlaylistError('no playlist behind that share link — it may '
                                'have been unshared')
        doc = _read(_path(entry['owner'], entry['id']))
        if doc is None:
            raise PlaylistError('that playlist has been deleted')
        me = who(token, guest)
        return {**doc, 'owner': None, 'mine': doc.get('owner') == me['id'],
                'shared_by': doc.get('owner_kind')}
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    return {**doc, 'mine': True}


def edit(id=None, name=None, note=None, token=None, guest=None) -> dict:
    """Rename a playlist or change its note."""
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    if name is not None:
        doc['name'] = _clean_name(name, doc['name'])
    if note is not None:
        doc['note'] = str(note)[:400]
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return doc


def delete(id=None, token=None, guest=None) -> dict:
    """Delete a playlist, and drop its share link with it."""
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    if doc.get('share_id'):
        idx = _shares()
        idx.pop(doc['share_id'], None)
        _write_shares(idx)
    _path(me['id'], id).unlink(missing_ok=True)
    return {'ok': True, 'deleted': id, 'name': doc.get('name')}


def add(id=None, track=None, tracks=None, at=None, token=None, guest=None) -> dict:
    """Add one track or several. Duplicates are ignored, not appended twice."""
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    incoming = []
    if track is not None:
        incoming.append(track)
    if tracks is not None:
        if isinstance(tracks, str):
            try:
                tracks = json.loads(tracks)
            except json.JSONDecodeError:
                raise PlaylistError('tracks must be a JSON array')
        incoming.extend(tracks or [])
    if not incoming:
        raise PlaylistError('nothing to add — pass track or tracks')
    have = {t.get('key') for t in doc['tracks']}
    rows = []
    for t in incoming:
        row = normalize(t)
        if row['key'] in have:
            continue
        have.add(row['key'])
        rows.append(row)
    if len(doc['tracks']) + len(rows) > MAX_TRACKS:
        raise PlaylistError(f'{MAX_TRACKS} tracks is the limit for one playlist')
    if at is None:
        doc['tracks'].extend(rows)
    else:
        i = max(0, min(int(at), len(doc['tracks'])))
        doc['tracks'][i:i] = rows
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {**doc, 'added': len(rows),
            'skipped': len(incoming) - len(rows), 'mine': True}


def replace(id=None, tracks=None, token=None, guest=None) -> dict:
    """Set the whole track list at once — what the console's auto-save writes.

    The console holds the set list the user is dragging around; sending the
    order back wholesale is simpler and less lossy than replaying every move,
    and an empty list is a legitimate thing to save (they cleared it).
    """
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    if isinstance(tracks, str):
        try:
            tracks = json.loads(tracks)
        except json.JSONDecodeError:
            raise PlaylistError('tracks must be a JSON array')
    if tracks is None:
        raise PlaylistError('tracks is required — pass [] to empty the playlist')
    if len(tracks) > MAX_TRACKS:
        raise PlaylistError(f'{MAX_TRACKS} tracks is the limit for one playlist')
    # Keep the original `added` stamp for rows that were already here, so
    # reordering a playlist does not make every track look brand new.
    was = {t.get('key'): t.get('added') for t in doc['tracks']}
    rows, seen = [], set()
    for t in tracks:
        row = normalize(t)
        if row['key'] in seen:
            continue
        seen.add(row['key'])
        if row['key'] in was:
            row['added'] = was[row['key']]
        rows.append(row)
    doc['tracks'] = rows
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {**doc, 'mine': True}


def reorder(id=None, keys=None, token=None, guest=None) -> dict:
    """Reorder the tracks already in a playlist, by their keys.

    Nothing is added or dropped: keys that are not in the playlist are
    ignored, and tracks the caller did not mention keep their order at the
    end. An agent asked to "put the slow ones first" can therefore send its
    order without being able to lose anyone's tracks.
    """
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    if isinstance(keys, str):
        try:
            keys = json.loads(keys)
        except json.JSONDecodeError:
            keys = [k.strip() for k in keys.split(',') if k.strip()]
    if not keys:
        raise PlaylistError('keys is required — the track keys in the order you want')
    have = {t.get('key'): t for t in doc['tracks']}
    unknown = [k for k in keys if k not in have]
    rows = [have.pop(k) for k in keys if k in have]
    rows.extend(have.values())
    doc['tracks'] = rows
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {**doc, 'mine': True, 'ignored': unknown}


def remove(id=None, key=None, index=None, token=None, guest=None) -> dict:
    """Take a track out, by its key or by its position (0-based)."""
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    n = len(doc['tracks'])
    if key:
        keep = [t for t in doc['tracks'] if t.get('key') != key]
        if len(keep) == n:
            raise PlaylistError(f'no track {key!r} in {doc["name"]!r}')
        doc['tracks'] = keep
    elif index is not None:
        i = int(index)
        if not 0 <= i < n:
            raise PlaylistError(f'index {i} is outside 0…{n - 1}')
        doc['tracks'].pop(i)
    else:
        raise PlaylistError('pass key or index')
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {**doc, 'removed': n - len(doc['tracks']), 'mine': True}


def move(id=None, **kw) -> dict:
    """Reorder: move the track at ``from`` to position ``to``.

    ``from`` is a keyword in Python, so it arrives through kwargs — the HTTP
    and MCP layers both name it ``from`` because that is what it is.
    """
    token, guest = kw.pop('token', None), kw.pop('guest', None)
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    src = kw.get('from', kw.get('from_', kw.get('index')))
    dst = kw.get('to')
    if src is None or dst is None:
        raise PlaylistError('pass from and to')
    n = len(doc['tracks'])
    i, j = int(src), int(dst)
    if not 0 <= i < n:
        raise PlaylistError(f'from {i} is outside 0…{n - 1}')
    j = max(0, min(j, n - 1))
    doc['tracks'].insert(j, doc['tracks'].pop(i))
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {**doc, 'mine': True}


# ── sharing ──────────────────────────────────────────────────────────────

def share(id=None, on=True, listed=False, token=None, guest=None) -> dict:
    """Mint (or revoke) a share link.

    ``listed`` also puts the playlist in the public directory. Off by default:
    a link you send to three friends should not become a listing.
    """
    me = _owner(token, guest)
    doc = _load_own(me['id'], id)
    idx = _shares()
    on = str(on).lower() not in ('false', '0', 'no', 'off', 'none')
    if not on:
        if doc.get('share_id'):
            idx.pop(doc['share_id'], None)
            _write_shares(idx)
        doc['share_id'], doc['listed'] = None, False
    else:
        if not doc.get('share_id'):
            doc['share_id'] = 'sh_' + _rand(14)
        doc['listed'] = str(listed).lower() in ('true', '1', 'yes', 'on')
        idx[doc['share_id']] = {'owner': me['id'], 'id': doc['id'],
                                'listed': doc['listed'],
                                'shared': round(time.time(), 3)}
        _write_shares(idx)
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return {'id': doc['id'], 'name': doc['name'], 'shared': bool(doc['share_id']),
            'share_id': doc['share_id'], 'listed': doc['listed'],
            'count': len(doc['tracks'])}


def copy(share=None, name=None, token=None, guest=None) -> dict:
    """Copy someone's shared playlist into your own library.

    The copy is yours from that moment — editing it does not touch theirs, and
    theirs changing does not change yours. ``copied_from`` remembers where it
    came from so the console can credit it.
    """
    me = _owner(token, guest)
    src = open_(share=share)
    doc = create(name=name or (src.get('name', 'playlist') + ' (copy)'),
                 note=src.get('note') or '', tracks=src.get('tracks') or [],
                 token=token, guest=guest)
    doc['copied_from'] = {'share_id': share, 'name': src.get('name')}
    doc['updated'] = round(time.time(), 3)
    _save(_path(me['id'], doc['id']), doc)
    return doc


def feed(limit=30) -> dict:
    """The public directory — every playlist shared AND listed."""
    idx = _shares()
    out = []
    for share_id, entry in idx.items():
        if not entry.get('listed'):
            continue
        doc = _read(_path(entry['owner'], entry['id']))
        if not doc:
            continue
        card = _summary(doc)
        card.update({'share_id': share_id, 'owner': None,
                     'shared': True, 'id': None})
        out.append(card)
    out.sort(key=lambda x: x.get('updated') or 0, reverse=True)
    return {'count': len(out), 'items': out[:int(limit or 30)]}
