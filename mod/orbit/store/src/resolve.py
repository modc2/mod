"""
Turning what a person typed into what the database wants.

Everything below this file addresses pictures by the sha256 of their bytes and
durations by an integer count of seconds, because those are the only forms that
are unambiguous once you have more than one caller. Everything above it is a
person or an agent, and neither of them has a 64-character hash to hand. So:

    resolve.image('sunset.png')     the name you gave it
    resolve.image('e54c50db')       a prefix of the id, git-style
    resolve.image('latest')         the last one you put in
    resolve.image('e54c50db…full')  the id itself, still

    resolve.ttl('90')  resolve.ttl('30s')  resolve.ttl('5m')  resolve.ttl('2h')

AMBIGUITY IS AN ERROR AND NOT A GUESS
    Two pictures whose ids share a prefix, or two called `screenshot.png`,
    raise rather than picking the newer one. The operations on the other end of
    this are publishing something forever and deleting something permanently,
    and a resolver that silently picks a winner turns a typo into whichever of
    those the caller happened to be running. The error names the candidates so
    the next attempt is exact.

CODES ARE RESOLVED ONLY FOR SOMEONE WHO ALREADY OWNS THEM
    `resolve.code` takes a prefix too, but it is scoped to one owner's grants
    and is used by the CLI and the MCP tools, never by the HTTP claim or peek
    paths. Those take the whole code and nothing shorter, because the code IS
    the credential: a public endpoint that accepts prefixes is a public
    endpoint that can be walked eight characters at a time.

THE SHORTEST PREFIX IS FOUR
    Below that a "prefix" matches most of the shelf and the error listing the
    candidates is longer than the id would have been. Four hex characters over
    a library this size is already specific enough to be a typo rather than a
    collision, which is the failure worth catching.
"""
import re
import time

from . import library
from .library import StoreError

MIN_PREFIX = 4
NEWEST = ('latest', 'last', 'newest', '-', '@last')

# 90 · 90s · 5m · 2h · 1d — a number, optionally with one unit letter. The
# sign is allowed through so that "-5" reaches the bounds check and is told
# what the bounds are, rather than being called "not a duration" — it plainly
# is one, and the useful answer is the range it fell outside of.
_DURATION = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*([smhd]?)\s*$', re.I)
_UNITS = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}

_HEX = re.compile(r'^[0-9a-f]+$')


# ── durations ────────────────────────────────────────────────────────

def ttl(value, default=None, minimum=None, maximum=None) -> int:
    """
    Seconds, from whatever the caller found natural to type.

    Bare numbers stay seconds so that every ttl_seconds= that was ever written
    against this module keeps meaning what it meant. The unit suffixes are
    additive, and `5m` is unambiguous in a way that `300` is only unambiguous
    to someone who already knew the unit.
    """
    from . import grants  # imported here: grants imports library, not this

    default = grants.DEFAULT_TTL if default is None else default
    minimum = grants.MIN_TTL if minimum is None else minimum
    maximum = grants.MAX_TTL if maximum is None else maximum

    if value in (None, ''):
        return int(default)
    if isinstance(value, bool):
        raise StoreError('a duration, not a true/false — try 30s, 5m or 2h', 400)
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        match = _DURATION.match(str(value))
        if not match:
            raise StoreError(
                f'{value!r} is not a duration — write it as a number of '
                f'seconds, or with a unit: 30s, 5m, 2h, 1d', 400)
        seconds = float(match.group(1)) * _UNITS[match.group(2).lower()]

    seconds = int(round(seconds))
    if seconds < minimum or seconds > maximum:
        raise StoreError(
            f'{human_duration(seconds)} is outside what a one-time code may '
            f'live: {human_duration(minimum)} to {human_duration(maximum)}. '
            f'Anything longer than a day is publishing with extra steps, and '
            f'publish is right there.', 400)
    return seconds


def human_duration(seconds) -> str:
    """The inverse, for messages: 90 -> '1m 30s', 3600 -> '1h'."""
    seconds = int(round(float(seconds)))
    if seconds <= 0:
        return '0s'
    parts, remaining = [], seconds
    for unit, size in (('d', 86400), ('h', 3600), ('m', 60), ('s', 1)):
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f'{count}{unit}')
    return ' '.join(parts[:2])


# ── pictures ─────────────────────────────────────────────────────────

def _candidates(reference: str, owner: str, public_too: bool):
    """Every row this reference could plausibly mean, newest first."""
    conn = library.connect()
    try:
        where = 'owner = ?'
        args = [owner]
        if public_too:
            where = f'({where} OR public = 1)'
        rows = conn.execute(
            f'SELECT id, owner, name, created, public FROM images '
            f'WHERE {where} ORDER BY created DESC', args).fetchall()
    finally:
        conn.close()

    lowered = reference.lower()
    by_id, by_name = [], []
    seen = set()
    for row in rows:
        if row['id'] in seen:
            continue
        if row['id'].lower().startswith(lowered):
            by_id.append(dict(row))
            seen.add(row['id'])
        elif row['name'].lower() == lowered:
            by_name.append(dict(row))
            seen.add(row['id'])
    # An id prefix beats a name: the hash is the thing this module actually
    # keys on, and a picture someone named `e54c50db` should not shadow it.
    return by_id or by_name


def image(reference, owner: str, public_too: bool = False) -> str:
    """
    The full image id a reference means, or a StoreError naming the near misses.

    `public_too` widens the search to published pictures for the read-only
    callers (view a picture, look one up) and stays off for the ones that
    change or destroy something, which may only ever act on your own rows.
    """
    reference = str(reference or '').strip()
    if not reference:
        raise StoreError(
            'which picture? Pass its id, a unique prefix of it, its name, or '
            '"latest" for the one you added most recently.', 400)

    if reference.lower() in NEWEST:
        newest = library.listing(owner, limit=1)
        if not newest:
            raise StoreError(
                'nothing in your library yet — add a picture first', 404)
        return newest[0]['id']

    # A whole id that exists is the answer without a search.
    if len(reference) == 64 and _HEX.match(reference.lower()):
        exact = library.record(reference.lower(), owner)
        if exact or (public_too and library.public_record(reference.lower())):
            return reference.lower()

    if _HEX.match(reference.lower()) and len(reference) < MIN_PREFIX:
        raise StoreError(
            f'{reference!r} is too short to identify a picture — use at least '
            f'{MIN_PREFIX} characters of its id, or its name', 400)

    found = _candidates(reference, owner, public_too)
    if not found:
        raise StoreError(
            f'nothing here matches {reference!r} — `images` lists what you '
            f'have, and ids may be shortened to any unique prefix', 404)
    if len(found) > 1:
        listed = ', '.join(f'{row["id"][:12]} ({row["name"]})'
                           for row in found[:6])
        more = '' if len(found) <= 6 else f' …and {len(found) - 6} more'
        raise StoreError(
            f'{reference!r} matches {len(found)} pictures — {listed}{more}. '
            f'Say more of the id.', 409)
    return found[0]['id']


def short(image_id: str, length: int = 12) -> str:
    """The prefix worth showing a human. Long enough to paste back in."""
    return str(image_id or '')[:length]


# ── codes ────────────────────────────────────────────────────────────

def code(reference, owner: str) -> str:
    """
    A grant code from a prefix — but only among grants this owner minted.

    Scoped on purpose. Prefix matching is a convenience for the person who
    minted the code and is holding a terminal; on a public endpoint it would be
    an oracle. Live codes are preferred over dead ones so that `revoke abc1`
    means the code you are still worried about rather than one spent last week.
    """
    reference = str(reference or '').strip()
    if not reference:
        raise StoreError('which code?', 400)

    conn = library.connect()
    try:
        rows = [dict(r) for r in conn.execute(
            'SELECT code, image, created, claimed, expires FROM grants '
            'WHERE owner = ? ORDER BY created DESC', (owner,)).fetchall()]
    finally:
        conn.close()

    exact = [row for row in rows if row['code'] == reference]
    if exact:
        return reference

    if len(reference) < MIN_PREFIX:
        raise StoreError(
            f'{reference!r} is too short to identify a code — use at least '
            f'{MIN_PREFIX} characters of it', 400)

    now = time.time()
    found = [row for row in rows if row['code'].startswith(reference)]
    live = [row for row in found
            if row['claimed'] is None and row['expires'] > now]
    pool = live or found
    if not pool:
        raise StoreError(
            f'no code of yours starts with {reference!r} — `grants` lists the '
            f'ones still live', 404)
    if len(pool) > 1:
        listed = ', '.join(row['code'][:10] for row in pool[:6])
        raise StoreError(
            f'{reference!r} matches {len(pool)} of your codes — {listed}. '
            f'Say more of it.', 409)
    return pool[0]['code']
