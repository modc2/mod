"""
Grants — a link that works once, and only for the next N seconds.

A grant is a secret code pointing at one image. It carries no identity: whoever
holds the code is the audience, which is the whole point of putting it in a QR
code on a screen someone is looking at. Two things bound it, and they are
independent so that failing to use it is as safe as using it:

    time      the code stops working N seconds after it was minted, whether or
              not anybody ever scanned it
    use       the first successful claim burns it; the second gets 410

THE ONE-TIME PART IS A SINGLE UPDATE, DELIBERATELY
    `claim` does not read-then-write. It issues one conditional UPDATE with
    `claimed IS NULL AND expires > now` in the WHERE clause and checks rowcount,
    so the database decides the winner. Two phones scanning the same QR code in
    the same millisecond is not a hypothetical — it is what happens when a code
    is on a screen in front of a room — and a read-then-write would hand the
    image to both of them. Anything that reads first can only report *why* a
    claim failed; it can never be what authorises one.

    The diagnosis pass below runs AFTER the update has already failed, purely
    to turn "no" into "expired" or "already used". It is advisory, and it is
    never the thing that lets a claim through.

WHY A CLAIMED GRANT IS KEPT
    Redemption does not delete the row. A deleted code is indistinguishable
    from one that never existed, so a burned link would answer "unknown" and
    the person holding it could not tell "someone else got there first" from
    "you typed it wrong". The row is kept, answers 410, and `sweep` removes it
    long after it stopped mattering.
"""
import secrets
import time

from . import library
from .library import StoreError

# Bounds on the caller's N. A grant shorter than a second cannot be scanned;
# one longer than a day is not a handoff any more, it is publishing with extra
# steps, and `publish` is right there.
MIN_TTL = 1
MAX_TTL = 86400
DEFAULT_TTL = 60

# How long a spent or expired grant is kept so it can still say what happened.
KEEP_DEAD_SECONDS = 7 * 86400


def _shape(row, now=None):
    now = now if now is not None else time.time()
    out = dict(row)
    out['claimed'] = bool(row['claimed'])
    out['claimed_at'] = row['claimed']
    out['expired'] = row['expires'] <= now
    out['live'] = not out['claimed'] and not out['expired']
    out['seconds_left'] = max(0, round(row['expires'] - now, 3))
    return out


def create(image_id: str, owner: str, ttl_seconds: int = DEFAULT_TTL):
    """Mint a one-time code for an image you own, good for N seconds."""
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        raise StoreError('ttl_seconds must be a whole number of seconds', 400)
    if ttl < MIN_TTL or ttl > MAX_TTL:
        raise StoreError(
            f'ttl_seconds must be between {MIN_TTL} and {MAX_TTL}', 400)

    if library.record(image_id, owner) is None:
        raise StoreError('no such image of yours', 404)

    now = time.time()
    # 32 url-safe chars from the system CSPRNG. The code IS the credential, so
    # it has to be unguessable — a short or sequential code would let anyone
    # walk the space and drain every live grant on the box.
    code = secrets.token_urlsafe(24)
    conn = library.connect()
    try:
        conn.execute(
            'INSERT INTO grants (code, image, owner, ttl, created, expires) '
            'VALUES (?,?,?,?,?,?)',
            (code, image_id, owner, ttl, now, now + ttl))
        return _shape(conn.execute('SELECT * FROM grants WHERE code=?',
                                   (code,)).fetchone(), now)
    finally:
        conn.close()


def claim(code: str, claimed_by: str = ''):
    """
    Redeem a code. Returns the grant on success and raises otherwise.

    The UPDATE is the authorisation. Everything after it is bookkeeping, and
    everything in the failure branch is diagnosis.
    """
    if not code:
        raise StoreError('no code', 404)
    now = time.time()
    conn = library.connect()
    try:
        burned = conn.execute(
            'UPDATE grants SET claimed=?, claimed_by=? '
            'WHERE code=? AND claimed IS NULL AND expires > ?',
            (now, (claimed_by or '')[:200], code, now)).rowcount

        row = conn.execute('SELECT * FROM grants WHERE code=?',
                           (code,)).fetchone()
        if not burned:
            # Advisory only — the claim has already been refused above.
            if row is None:
                raise StoreError('no such grant', 404)
            if row['claimed'] is not None:
                raise StoreError(
                    'this link has already been used once, which is all it '
                    'was good for', 410)
            raise StoreError('this link has expired', 410)
        return _shape(row, now)
    finally:
        conn.close()


def peek(code: str):
    """What a code would do if claimed — without claiming it."""
    conn = library.connect()
    try:
        row = conn.execute('SELECT * FROM grants WHERE code=?',
                           (code,)).fetchone()
        return _shape(row) if row else None
    finally:
        conn.close()


def listing(owner: str, include_dead: bool = False, limit: int = 100):
    """The owner's grants — live ones by default."""
    now = time.time()
    conn = library.connect()
    try:
        sql = 'SELECT * FROM grants WHERE owner=?'
        args = [owner]
        if not include_dead:
            sql += ' AND claimed IS NULL AND expires > ?'
            args.append(now)
        sql += ' ORDER BY created DESC LIMIT ?'
        args.append(max(1, min(int(limit), 500)))
        return [_shape(r, now) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def revoke(code: str, owner: str):
    """Kill a live code early — the screen showing the QR walked out of the room."""
    conn = library.connect()
    try:
        gone = conn.execute('DELETE FROM grants WHERE code=? AND owner=?',
                            (code, owner)).rowcount
        if not gone:
            raise StoreError('no such grant of yours', 404)
        return {'code': code, 'revoked': True}
    finally:
        conn.close()


def sweep(older_than: float = KEEP_DEAD_SECONDS):
    """Forget grants that stopped mattering a week ago."""
    cutoff = time.time() - float(older_than)
    conn = library.connect()
    try:
        n = conn.execute(
            'DELETE FROM grants WHERE (claimed IS NOT NULL AND claimed < ?) '
            'OR (claimed IS NULL AND expires < ?)', (cutoff, cutoff)).rowcount
        return {'swept': n}
    finally:
        conn.close()
