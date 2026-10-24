"""jokebook — the joke store behind the jokes module.

One SQLite file, stdlib only. Every joke is credited to the comedian who told
it, and its id is a content hash of (text, comedian), so the same joke saved
on two machines gets the same id and a shared pack merges without duplicates.

    add(text, comedian, source=, year=, tags=, by=)   → the joke
    search(q=, comedian=, tag=, sort=new|top|random)  → jokes
    get(id) · random(comedian=) · vote(id, +1|-1) · remove(id)
    comedians() · tags() · stats()
    export(ids=, comedian=) → a pack (plain JSON) · import_pack(pack) → counts

A pack is the unit of sharing: hand someone the JSON (file, paste, QR, peer
call) and they import it. No server, account or network is involved.
"""

import hashlib
import json
import os
import random as _random
import re
import sqlite3
import threading
import time

PACK_FORMAT = 'jokes.pack/1'
MAX_TEXT = 4000
SORTS = ('new', 'top', 'random', 'old')

_lock = threading.Lock()


def default_path():
    root = os.environ.get('JOKES_DIR') or os.path.expanduser('~/.mod/jokes')
    return os.path.join(root, 'jokes.db')


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '').strip())


def joke_id(text, comedian):
    """Stable across machines: case- and whitespace-insensitive."""
    key = _norm(text).lower() + '\n' + _norm(comedian).lower()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _tags(tags):
    if isinstance(tags, str):
        tags = re.split(r'[,\s]+', tags)
    out = []
    for t in tags or []:
        t = re.sub(r'[^a-z0-9_-]', '', str(t).lower().lstrip('#'))
        if t and t not in out:
            out.append(t)
    return out[:12]


class Jokebook:
    def __init__(self, path=None):
        self.path = path or default_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jokes (
                id        TEXT PRIMARY KEY,
                text      TEXT NOT NULL,
                comedian  TEXT NOT NULL,
                source    TEXT NOT NULL DEFAULT '',
                year      INTEGER,
                tags      TEXT NOT NULL DEFAULT '[]',
                added_by  TEXT NOT NULL DEFAULT '',
                votes     INTEGER NOT NULL DEFAULT 0,
                created   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jokes_comedian ON jokes(comedian COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS jokes_created ON jokes(created);
        """)
        self.db.commit()

    # ── rows ─────────────────────────────────────────────────────

    @staticmethod
    def _row(r):
        d = dict(r)
        d['tags'] = json.loads(d['tags'] or '[]')
        return d

    def get(self, id):
        r = self.db.execute('SELECT * FROM jokes WHERE id=?', (id,)).fetchone()
        return self._row(r) if r else None

    # ── writes ───────────────────────────────────────────────────

    def add(self, text, comedian='', source='', year=None, tags=None, by='',
            created=None, votes=0):
        """Save a joke. Re-adding the same joke by the same comedian returns
        the existing one (with any new tags merged in) instead of a copy."""
        text = (text or '').strip()
        if not text:
            raise ValueError('text is required')
        if len(text) > MAX_TEXT:
            raise ValueError(f'text is over {MAX_TEXT} characters')
        comedian = _norm(comedian) or 'unknown'
        year = int(year) if str(year or '').strip().isdigit() else None
        tags = _tags(tags)
        id = joke_id(text, comedian)
        with _lock:
            old = self.get(id)
            if old:
                merged = old['tags'] + [t for t in tags if t not in old['tags']]
                if merged != old['tags']:
                    self.db.execute('UPDATE jokes SET tags=? WHERE id=?',
                                    (json.dumps(merged[:12]), id))
                    self.db.commit()
                out = self.get(id)
                out['existed'] = True
                return out
            self.db.execute(
                'INSERT INTO jokes (id,text,comedian,source,year,tags,added_by,votes,created)'
                ' VALUES (?,?,?,?,?,?,?,?,?)',
                (id, text, comedian, _norm(source), year, json.dumps(tags),
                 _norm(by), int(votes or 0), float(created or time.time())))
            self.db.commit()
        out = self.get(id)
        out['existed'] = False
        return out

    def vote(self, id, delta=1):
        delta = 1 if int(delta) >= 0 else -1
        with _lock:
            n = self.db.execute('UPDATE jokes SET votes=votes+? WHERE id=?',
                                (delta, id)).rowcount
            self.db.commit()
        if not n:
            raise KeyError(id)
        return self.get(id)

    def remove(self, id):
        with _lock:
            n = self.db.execute('DELETE FROM jokes WHERE id=?', (id,)).rowcount
            self.db.commit()
        return {'id': id, 'removed': bool(n)}

    # ── reads ────────────────────────────────────────────────────

    def search(self, q='', comedian='', tag='', sort='new', limit=50, offset=0):
        where, args = [], []
        for word in _norm(q).split(' ') if q else []:
            where.append('(text LIKE ? OR comedian LIKE ? OR source LIKE ? OR tags LIKE ?)')
            like = f'%{word}%'
            args += [like] * 4
        if comedian:
            where.append('comedian = ? COLLATE NOCASE')
            args.append(_norm(comedian))
        if tag:
            where.append('tags LIKE ?')
            args.append(f'%"{_tags(tag)[0] if _tags(tag) else tag}"%')
        order = {'new': 'created DESC', 'old': 'created ASC',
                 'top': 'votes DESC, created DESC',
                 'random': 'RANDOM()'}.get(sort, 'created DESC')
        sql_where = (' WHERE ' + ' AND '.join(where)) if where else ''
        limit = max(1, min(int(limit or 50), 500))
        total = self.db.execute(f'SELECT COUNT(*) FROM jokes{sql_where}', args).fetchone()[0]
        rows = self.db.execute(
            f'SELECT * FROM jokes{sql_where} ORDER BY {order} LIMIT ? OFFSET ?',
            args + [limit, int(offset or 0)]).fetchall()
        return {'total': total, 'jokes': [self._row(r) for r in rows]}

    def random(self, comedian='', tag=''):
        got = self.search(comedian=comedian, tag=tag, sort='random', limit=1)['jokes']
        return got[0] if got else None

    def comedians(self):
        rows = self.db.execute(
            'SELECT comedian, COUNT(*) n, SUM(votes) votes FROM jokes'
            ' GROUP BY comedian COLLATE NOCASE ORDER BY n DESC, comedian').fetchall()
        return [dict(r) for r in rows]

    def tags(self):
        counts = {}
        for (t,) in self.db.execute('SELECT tags FROM jokes'):
            for tag in json.loads(t or '[]'):
                counts[tag] = counts.get(tag, 0) + 1
        return [{'tag': k, 'n': v} for k, v in
                sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def stats(self):
        n = self.db.execute('SELECT COUNT(*) FROM jokes').fetchone()[0]
        return {'jokes': n, 'comedians': len(self.comedians()), 'path': self.path}

    # ── sharing ──────────────────────────────────────────────────

    def export(self, ids=None, comedian='', tag='', name=''):
        """A pack: plain JSON anyone can import. Votes stay local — they are
        this book's opinion, not part of the joke."""
        if isinstance(ids, str):
            ids = [i for i in re.split(r'[,\s]+', ids) if i]
        if ids:
            jokes = [j for j in (self.get(i) for i in ids) if j]
        else:
            jokes = self.search(comedian=comedian, tag=tag, sort='old', limit=500)['jokes']
        keep = ('id', 'text', 'comedian', 'source', 'year', 'tags', 'added_by', 'created')
        return {'format': PACK_FORMAT, 'name': name or comedian or tag or 'jokes',
                'exported': time.time(), 'count': len(jokes),
                'jokes': [{k: j[k] for k in keep} for j in jokes]}

    def import_pack(self, pack, by=''):
        """Merge a pack in. Ids are recomputed, never trusted, so a tampered
        pack cannot overwrite a different joke."""
        if isinstance(pack, (str, bytes)):
            pack = json.loads(pack)
        if isinstance(pack, list):
            pack = {'jokes': pack}
        if not isinstance(pack, dict) or not isinstance(pack.get('jokes'), list):
            raise ValueError('not a jokes pack')
        added = skipped = 0
        errors = []
        for j in pack['jokes'][:5000]:
            try:
                out = self.add(j.get('text'), j.get('comedian'), j.get('source', ''),
                               j.get('year'), j.get('tags'),
                               by=j.get('added_by') or by, created=j.get('created'))
                if out['existed']:
                    skipped += 1
                else:
                    added += 1
            except Exception as e:  # noqa: BLE001 — one bad joke doesn't sink the pack
                errors.append(str(e))
        return {'added': added, 'skipped': skipped, 'errors': errors[:20]}
