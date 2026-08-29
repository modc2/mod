"""
The library — images on disk, content-addressed, with one row per owner.

An image's id is the sha256 of its bytes, so the same picture uploaded twice
is stored once. The *row* is per-owner, though, keyed `(id, owner)`: two people
who upload the same bytes each get their own record, their own visibility flag
and their own grants, and one deleting it does not delete the other's. The blob
underneath is refcounted and only removed when the last row referring to it is.

Visibility is per-blob on the read path (`public_record`): if any owner has
published these bytes, `/i/<id>` serves them. That is not a leak — the bytes
were already public by the time the question is asked — but it is why the
answer is phrased as "are these bytes public" and not "is your row public".

SVG IS REFUSED
    Every other image format is inert data. SVG is a document that can carry
    script, and this module serves what it is given from an origin shared with
    the rest of the fleet, so an uploaded SVG is a stored-XSS primitive against
    every neighbouring module. Formats are decided by sniffing magic bytes, not
    by the filename or the Content-Type the uploader claims.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path

HOME = Path(os.environ.get(
    'STORE_SHARE_HOME', Path.home() / '.mod' / 'store-share'))
BLOBS = HOME / 'blobs'
DB = HOME / 'store.db'

# 10 MiB. A phone photo is 3-5; anything past this is not being shared, it is
# being parked, and this module is not core/store.
MAX_BYTES = int(os.environ.get('STORE_SHARE_MAX_BYTES', 10 * 1024 * 1024))

# (prefix, offset, mime, extension). Sniffed from the bytes — never trusted
# from the filename, which the uploader controls.
MAGIC = (
    (b'\x89PNG\r\n\x1a\n', 0, 'image/png', 'png'),
    (b'\xff\xd8\xff', 0, 'image/jpeg', 'jpg'),
    (b'GIF87a', 0, 'image/gif', 'gif'),
    (b'GIF89a', 0, 'image/gif', 'gif'),
    (b'BM', 0, 'image/bmp', 'bmp'),
)


class StoreError(Exception):
    """Something the caller did — carries the status the API should send."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def sniff(data: bytes):
    """The mime type these bytes actually are, or None if not a known image."""
    for prefix, offset, mime, ext in MAGIC:
        if data[offset:offset + len(prefix)] == prefix:
            return mime, ext
    # WEBP is a RIFF container: 'RIFF' .... 'WEBP'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    return None


def dimensions(data: bytes):
    """(width, height) if Pillow is around to say so. Never fatal if not."""
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return None, None


# ── the database ─────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id      TEXT NOT NULL,          -- sha256 of the bytes
    owner   TEXT NOT NULL,
    name    TEXT NOT NULL DEFAULT '',
    mime    TEXT NOT NULL,
    size    INTEGER NOT NULL,
    width   INTEGER,
    height  INTEGER,
    public  INTEGER NOT NULL DEFAULT 0,
    created REAL    NOT NULL,
    PRIMARY KEY (id, owner)
);
CREATE INDEX IF NOT EXISTS images_owner  ON images(owner, created DESC);
CREATE INDEX IF NOT EXISTS images_public ON images(public) WHERE public = 1;

CREATE TABLE IF NOT EXISTS grants (
    code       TEXT PRIMARY KEY,    -- the secret in the QR code
    image      TEXT NOT NULL,
    owner      TEXT NOT NULL,       -- who the grant reads on behalf of
    ttl        INTEGER NOT NULL,
    created    REAL    NOT NULL,
    expires    REAL    NOT NULL,
    claimed    REAL,                -- NULL until redeemed; this is the lock
    claimed_by TEXT
);
CREATE INDEX IF NOT EXISTS grants_owner ON grants(owner, created DESC);
CREATE INDEX IF NOT EXISTS grants_image ON grants(image);
"""


def connect():
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.executescript(SCHEMA)
    return conn


def blob_path(image_id: str) -> Path:
    return BLOBS / image_id[:2] / image_id[2:4] / image_id


def _row(row):
    if row is None:
        return None
    out = dict(row)
    out['public'] = bool(out['public'])
    return out


# ── writing ──────────────────────────────────────────────────────────

def put(data: bytes, name: str = '', owner: str = 'local', public: bool = False):
    """Store bytes as an image. Returns the record; re-uploads are idempotent."""
    if not data:
        raise StoreError('empty upload', 400)
    if len(data) > MAX_BYTES:
        raise StoreError(
            f'{len(data)} bytes exceeds the {MAX_BYTES} byte limit', 413)
    kind = sniff(data)
    if kind is None:
        raise StoreError(
            'not an image this module will store — png, jpeg, gif, webp or '
            'bmp only, decided by content and not by filename (svg is '
            'refused: it can carry script)', 415)
    mime, ext = kind

    image_id = hashlib.sha256(data).hexdigest()
    path = blob_path(image_id)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside and rename, so a reader never sees a half-written blob
        # under a name that promises a hash.
        tmp = path.with_suffix('.part')
        tmp.write_bytes(data)
        os.replace(tmp, path)

    width, height = dimensions(data)
    if not name:
        name = f'{image_id[:12]}.{ext}'

    conn = connect()
    try:
        existing = conn.execute(
            'SELECT * FROM images WHERE id=? AND owner=?',
            (image_id, owner)).fetchone()
        if existing:
            return _row(existing)
        conn.execute(
            'INSERT INTO images (id, owner, name, mime, size, width, height, '
            'public, created) VALUES (?,?,?,?,?,?,?,?,?)',
            (image_id, owner, name[:200], mime, len(data), width, height,
             1 if public else 0, time.time()))
        return _row(conn.execute(
            'SELECT * FROM images WHERE id=? AND owner=?',
            (image_id, owner)).fetchone())
    finally:
        conn.close()


def publish(image_id: str, owner: str, public: bool = True):
    """Flip one owner's row between public and private."""
    conn = connect()
    try:
        changed = conn.execute(
            'UPDATE images SET public=? WHERE id=? AND owner=?',
            (1 if public else 0, image_id, owner)).rowcount
        if not changed:
            raise StoreError('no such image of yours', 404)
        return _row(conn.execute(
            'SELECT * FROM images WHERE id=? AND owner=?',
            (image_id, owner)).fetchone())
    finally:
        conn.close()


def remove(image_id: str, owner: str):
    """Drop one owner's row, its grants, and the blob if nobody else holds it."""
    conn = connect()
    try:
        gone = conn.execute('DELETE FROM images WHERE id=? AND owner=?',
                            (image_id, owner)).rowcount
        if not gone:
            raise StoreError('no such image of yours', 404)
        conn.execute('DELETE FROM grants WHERE image=? AND owner=?',
                     (image_id, owner))
        others = conn.execute('SELECT COUNT(*) c FROM images WHERE id=?',
                              (image_id,)).fetchone()['c']
        blob_removed = False
        if not others:
            path = blob_path(image_id)
            if path.exists():
                path.unlink()
                blob_removed = True
        return {'id': image_id, 'removed': True, 'blob_removed': blob_removed}
    finally:
        conn.close()


# ── reading ──────────────────────────────────────────────────────────

def record(image_id: str, owner: str):
    conn = connect()
    try:
        return _row(conn.execute('SELECT * FROM images WHERE id=? AND owner=?',
                                 (image_id, owner)).fetchone())
    finally:
        conn.close()


def public_record(image_id: str):
    """Any published row for these bytes — the /i/<id> read path."""
    conn = connect()
    try:
        return _row(conn.execute(
            'SELECT * FROM images WHERE id=? AND public=1 '
            'ORDER BY created LIMIT 1', (image_id,)).fetchone())
    finally:
        conn.close()


def read(image_id: str) -> bytes:
    path = blob_path(image_id)
    if not path.exists():
        raise StoreError('the record exists but its bytes are gone', 410)
    return path.read_bytes()


def listing(owner: str, limit: int = 100, offset: int = 0, public_only=False):
    conn = connect()
    try:
        sql = 'SELECT * FROM images WHERE owner=?'
        args = [owner]
        if public_only:
            sql += ' AND public=1'
        sql += ' ORDER BY created DESC LIMIT ? OFFSET ?'
        args += [max(1, min(int(limit), 500)), max(0, int(offset))]
        return [_row(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def public_listing(limit: int = 100, offset: int = 0):
    """Everything anyone has published — the front page."""
    conn = connect()
    try:
        # One entry per blob, credited to whoever published it first. The bare
        # columns are safe to select alongside MIN() — SQLite guarantees they
        # come from the row that produced the minimum, unlike a plain GROUP BY.
        return [_row(r) for r in conn.execute(
            'SELECT id, owner, name, mime, size, width, height, public, '
            'MIN(created) AS created FROM images WHERE public=1 '
            'GROUP BY id ORDER BY created DESC LIMIT ? OFFSET ?',
            (max(1, min(int(limit), 500)), max(0, int(offset)))).fetchall()]
    finally:
        conn.close()


def stats():
    conn = connect()
    try:
        # `bytes` is summed over distinct blobs, not over rows — two owners
        # holding the same picture cost the disk one copy, and the number
        # should say so.
        row = conn.execute(
            'SELECT (SELECT COUNT(*) FROM images) AS rows, '
            '(SELECT COUNT(DISTINCT id) FROM images) AS images, '
            '(SELECT COALESCE(SUM(size),0) FROM '
            '  (SELECT DISTINCT id, size FROM images)) AS bytes, '
            '(SELECT COUNT(*) FROM images WHERE public=1) AS published'
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
