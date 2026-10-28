"""store — the shop's books: saved designs and the order rail.

One SQLite file under ~/.mod/fabricas/ (override with FABRICAS_DATA).
Designs are stored as the validated JSON dict atelier hands back, keyed
by a short content hash, so an identical design saved twice is the same
row twice over — remixes reference their parent by that id.

Orders move down one rail and never skip or reverse except to cancel:

    placed → cutting → printing → sewing → ready → shipped

Every hop is appended to the order's log with a timestamp, so the
history is the row, not a separate table.
"""

import hashlib
import json
import os
import sqlite3
import time

STATUSES = ['placed', 'cutting', 'printing', 'sewing', 'ready', 'shipped']
CANCELLED = 'cancelled'


def data_dir():
    return os.environ.get('FABRICAS_DATA') or os.path.expanduser('~/.mod/fabricas')


def _conn():
    os.makedirs(data_dir(), exist_ok=True)
    conn = sqlite3.connect(os.path.join(data_dir(), 'fabricas.db'))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS designs (
            id TEXT PRIMARY KEY, body TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT 'anon',
            public INTEGER NOT NULL DEFAULT 1,
            remix_of TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, design_id TEXT NOT NULL,
            size TEXT NOT NULL, qty INTEGER NOT NULL,
            quote TEXT NOT NULL, note TEXT,
            author TEXT NOT NULL DEFAULT 'anon',
            status TEXT NOT NULL DEFAULT 'placed',
            log TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
    """)
    return conn


def _design_row(row):
    d = json.loads(row['body'])
    return {'id': row['id'], 'design': d, 'name': d.get('name', 'untitled'),
            'garment': d.get('garment'), 'author': row['author'],
            'public': bool(row['public']), 'remix_of': row['remix_of'],
            'created': row['created']}


def save_design(design, author='anon', public=True, remix_of=None):
    body = json.dumps(design, sort_keys=True)
    did = hashlib.sha256(body.encode()).hexdigest()[:10]
    with _conn() as c:
        c.execute('INSERT OR IGNORE INTO designs VALUES (?,?,?,?,?,?)',
                  (did, body, str(author or 'anon')[:40], int(bool(public)),
                   remix_of, time.time()))
    return did


def get_design(design_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM designs WHERE id=?', (design_id,)).fetchone()
    if not row:
        raise LookupError(f'no design {design_id!r}')
    return _design_row(row)


def list_designs(author=None, public=None, limit=60):
    q, args = 'SELECT * FROM designs', []
    where = []
    if author:
        where.append('author=?'); args.append(author)
    if public is not None:
        where.append('public=?'); args.append(int(bool(public)))
    if where:
        q += ' WHERE ' + ' AND '.join(where)
    q += ' ORDER BY created DESC LIMIT ?'
    args.append(int(limit))
    with _conn() as c:
        return [_design_row(r) for r in c.execute(q, args)]


def _order_row(row):
    return {'id': row['id'], 'design_id': row['design_id'], 'size': row['size'],
            'qty': row['qty'], 'quote': json.loads(row['quote']),
            'note': row['note'], 'author': row['author'],
            'status': row['status'], 'log': json.loads(row['log']),
            'created': row['created'], 'updated': row['updated']}


def save_order(design_id, size, qty, quote, author='anon', note=None):
    get_design(design_id)  # must exist — a LookupError here is the answer
    now = time.time()
    oid = hashlib.sha256(f'{design_id}{size}{qty}{author}{now}'.encode()).hexdigest()[:10]
    log = [{'status': 'placed', 'at': now}]
    with _conn() as c:
        c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (oid, design_id, size, int(qty), json.dumps(quote),
                   (str(note)[:200] if note else None), str(author or 'anon')[:40],
                   'placed', json.dumps(log), now, now))
    return oid


def get_order(order_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not row:
        raise LookupError(f'no order {order_id!r}')
    return _order_row(row)


def list_orders(author=None, status=None, limit=60):
    q, args, where = 'SELECT * FROM orders', [], []
    if author:
        where.append('author=?'); args.append(author)
    if status:
        where.append('status=?'); args.append(status)
    if where:
        q += ' WHERE ' + ' AND '.join(where)
    q += ' ORDER BY created DESC LIMIT ?'
    args.append(int(limit))
    with _conn() as c:
        return [_order_row(r) for r in c.execute(q, args)]


def advance_order(order_id, status=None):
    """Move an order one stop down the rail (or to an explicit status).
    Cancelling is allowed from anywhere short of shipped; nothing else
    may skip or reverse."""
    order = get_order(order_id)
    current = order['status']
    if current in (CANCELLED, STATUSES[-1]):
        raise ValueError(f'order {order_id} is {current} — the rail ends there')
    if status is None:
        status = STATUSES[STATUSES.index(current) + 1]
    elif status == CANCELLED:
        pass
    elif status not in STATUSES or STATUSES.index(status) != STATUSES.index(current) + 1:
        raise ValueError(f'{current} → {status} skips the rail '
                         f'({" → ".join(STATUSES)}, or cancelled)')
    now = time.time()
    log = order['log'] + [{'status': status, 'at': now}]
    with _conn() as c:
        c.execute('UPDATE orders SET status=?, log=?, updated=? WHERE id=?',
                  (status, json.dumps(log), now, order_id))
    return get_order(order_id)


def stats():
    with _conn() as c:
        designs = c.execute('SELECT COUNT(*) FROM designs').fetchone()[0]
        orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        by_status = dict(c.execute(
            'SELECT status, COUNT(*) FROM orders GROUP BY status').fetchall())
    return {'designs': designs, 'orders': orders, 'orders_by_status': by_status,
            'db': os.path.join(data_dir(), 'fabricas.db')}
