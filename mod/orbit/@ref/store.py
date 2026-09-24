"""The @ref index — one local SQLite file, no services, no cloud.

Images stay wherever they already live on disk; the index holds only the
path, the pose (yaw/pitch/roll, NULL until tagged) and free-form tags.
Delete ``data/ref.db`` and you have deleted the module's entire state.
"""

import hashlib
import json
import os
import sqlite3
import time

import orientation as ori

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('REF_DATA_DIR') or os.path.join(HERE, 'data')
DB_PATH = os.path.join(DATA_DIR, 'ref.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    id      TEXT PRIMARY KEY,
    path    TEXT NOT NULL UNIQUE,
    name    TEXT NOT NULL,
    yaw     REAL,
    pitch   REAL,
    roll    REAL,
    tags    TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT 'figure',
    source  TEXT NOT NULL DEFAULT 'manual',
    added   INTEGER NOT NULL
);
"""


def _db():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    return db


def ref_id(path):
    return hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:16]


def _row(r):
    d = dict(r)
    d['tags'] = json.loads(d['tags'])
    if d['yaw'] is not None:
        d['pose'] = ori.describe(d['yaw'], d['pitch'], d['roll'])
    else:
        d['pose'] = None
    return d


def add(path, yaw=None, pitch=None, roll=None, tags=None, subject='figure',
        source='manual'):
    """Index one image. Pose may be omitted — it stays untagged until set."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return {'error': f'not a file: {path}'}
    if yaw is not None:
        yaw, pitch, roll = ori.normalize(yaw, pitch or 0, roll or 0)
    db = _db()
    with db:
        db.execute(
            'INSERT INTO refs (id,path,name,yaw,pitch,roll,tags,subject,source,added) '
            'VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET '
            'yaw=COALESCE(excluded.yaw, refs.yaw), '
            'pitch=COALESCE(excluded.pitch, refs.pitch), '
            'roll=COALESCE(excluded.roll, refs.roll)',
            (ref_id(path), path, os.path.basename(path), yaw, pitch, roll,
             json.dumps(sorted(set(tags or []))), subject, source, int(time.time())))
    return get(ref_id(path))


def scan(folder, tags=None, subject='figure', recursive=True, estimator=None):
    """Walk a folder and index every image in it.

    ``estimator`` is an optional callable(path) -> (yaw, pitch, roll) | None;
    images it can't read (or its absence) simply leave the pose untagged —
    you orient those from the gimbal later.
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        return {'error': f'not a folder: {folder}'}
    found = estimated = 0
    walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for root, _dirs, files in walker:
        for name in sorted(files):
            if not name.lower().endswith(ori.IMAGE_EXTS):
                continue
            path = os.path.join(root, name)
            pose = None
            if estimator is not None:
                try:
                    pose = estimator(path)
                except Exception:
                    pose = None
            if pose:
                add(path, *pose, tags=tags, subject=subject, source='estimated')
                estimated += 1
            else:
                add(path, tags=tags, subject=subject)
            found += 1
    return {'folder': folder, 'indexed': found, 'estimated': estimated,
            'untagged': found - estimated}


def set_pose(id, yaw, pitch=0, roll=0, tags=None):
    """Pin a pose (and optionally tags) onto an indexed image."""
    yaw, pitch, roll = ori.normalize(yaw, pitch, roll)
    db = _db()
    with db:
        db.execute('UPDATE refs SET yaw=?, pitch=?, roll=?, source=? WHERE id=?',
                   (yaw, pitch, roll, 'manual', id))
        if tags is not None:
            db.execute('UPDATE refs SET tags=? WHERE id=?',
                       (json.dumps(sorted(set(tags))), id))
    return get(id)


def get(id):
    r = _db().execute('SELECT * FROM refs WHERE id=?', (id,)).fetchone()
    return _row(r) if r else {'error': f'unknown id: {id}'}


def remove(id):
    with _db() as db:
        n = db.execute('DELETE FROM refs WHERE id=?', (id,)).rowcount
    return {'removed': n}


def _filtered(rows, tags=None, subject=None):
    want = set(t.lower() for t in (tags or []))
    for r in rows:
        d = _row(r)
        if subject and d['subject'] != subject:
            continue
        if want and not want.issubset(set(t.lower() for t in d['tags'])):
            continue
        yield d


def query(yaw, pitch=0, roll=0, tolerance=45, limit=24, tags=None,
          subject=None, roll_weight=0.5):
    """The gimbal query: everything indexed within ``tolerance`` degrees of
    the given view, nearest first."""
    target = (yaw, pitch, roll)
    rows = _db().execute('SELECT * FROM refs WHERE yaw IS NOT NULL').fetchall()
    out = []
    for d in _filtered(rows, tags, subject):
        d['distance'] = round(ori.distance(
            target, (d['yaw'], d['pitch'], d['roll']), roll_weight), 1)
        if d['distance'] <= float(tolerance):
            out.append(d)
    out.sort(key=lambda d: d['distance'])
    return {'query': {'yaw': yaw, 'pitch': pitch, 'roll': roll,
                      'view': ori.describe(yaw, pitch, roll),
                      'tolerance': tolerance},
            'count': len(out), 'refs': out[:int(limit)]}


def untagged(limit=50):
    rows = _db().execute(
        'SELECT * FROM refs WHERE yaw IS NULL ORDER BY added DESC').fetchall()
    return {'count': len(rows), 'refs': [_row(r) for r in rows[:int(limit)]]}


def listing(limit=100, tags=None, subject=None):
    rows = _db().execute('SELECT * FROM refs ORDER BY added DESC').fetchall()
    out = list(_filtered(rows, tags, subject))
    return {'count': len(out), 'refs': out[:int(limit)]}


def stats():
    db = _db()
    total = db.execute('SELECT COUNT(*) FROM refs').fetchone()[0]
    tagged = db.execute('SELECT COUNT(*) FROM refs WHERE yaw IS NOT NULL').fetchone()[0]
    tag_counts = {}
    for (t,) in db.execute('SELECT tags FROM refs'):
        for tag in json.loads(t):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return {'total': total, 'tagged': tagged, 'untagged': total - tagged,
            'tags': dict(sorted(tag_counts.items(), key=lambda kv: -kv[1])),
            'db': DB_PATH}
