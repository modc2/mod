"""One SQLite file of reviews and the verdicts on their findings.

    ~/.mod/reviewer/reviews.db          (REVIEWER_DIR moves it)

A review row keeps the target it was run on, the engine, the score at the
time, and its findings as JSON. Verdicts (fixed / ignored / open, with a
note) live beside the findings so a re-run can re-score without them.
"""

import hashlib
import json
import os
import sqlite3
import time

import rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id        TEXT PRIMARY KEY,
    ts        REAL NOT NULL,
    target    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    engine    TEXT NOT NULL,
    score     INTEGER NOT NULL,
    grade     TEXT NOT NULL,
    counts    TEXT NOT NULL,
    findings  TEXT NOT NULL,
    summary   TEXT,
    meta      TEXT
);
CREATE INDEX IF NOT EXISTS reviews_ts ON reviews(ts DESC);
CREATE INDEX IF NOT EXISTS reviews_target ON reviews(target);
CREATE TABLE IF NOT EXISTS verdicts (
    review_id TEXT NOT NULL,
    idx       INTEGER NOT NULL,
    verdict   TEXT NOT NULL,
    note      TEXT,
    ts        REAL NOT NULL,
    PRIMARY KEY (review_id, idx)
);
"""


def default_path():
    d = os.environ.get('REVIEWER_DIR') or os.path.join(os.path.expanduser('~'), '.mod', 'reviewer')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'reviews.db')


class Store:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ── writes ───────────────────────────────────────────────────

    def save(self, target, kind, engine, findings, summary=None, meta=None):
        ts = time.time()
        rid = hashlib.sha256(f'{target}|{engine}|{ts}'.encode()).hexdigest()[:12]
        sc = rules.score(findings)
        self.db.execute(
            'INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (rid, ts, target, kind, engine, sc['score'], sc['grade'],
             json.dumps(sc['counts']), json.dumps(findings), summary,
             json.dumps(meta or {})))
        self.db.commit()
        return self.get(rid)

    def verdict(self, review_id, idx, verdict='fixed', note=''):
        if verdict not in ('open', 'fixed', 'ignored'):
            raise ValueError('verdict must be open|fixed|ignored')
        r = self.db.execute('SELECT findings FROM reviews WHERE id=?', (review_id,)).fetchone()
        if not r:
            raise KeyError(f'no review {review_id}')
        n = len(json.loads(r['findings']))
        idx = int(idx)
        if not 0 <= idx < n:
            raise ValueError(f'index out of range (0..{n - 1})')
        if verdict == 'open':
            self.db.execute('DELETE FROM verdicts WHERE review_id=? AND idx=?', (review_id, idx))
        else:
            self.db.execute('INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?,?)',
                            (review_id, idx, verdict, note or '', time.time()))
        # keep the stored score in step with the verdicts
        rev = self.get(review_id)
        self.db.execute('UPDATE reviews SET score=?, grade=?, counts=? WHERE id=?',
                        (rev['score'], rev['grade'], json.dumps(rev['counts']), review_id))
        self.db.commit()
        return rev

    def delete(self, review_id):
        n = self.db.execute('DELETE FROM reviews WHERE id=?', (review_id,)).rowcount
        self.db.execute('DELETE FROM verdicts WHERE review_id=?', (review_id,))
        self.db.commit()
        return {'id': review_id, 'deleted': bool(n)}

    # ── reads ────────────────────────────────────────────────────

    def get(self, review_id):
        r = self.db.execute('SELECT * FROM reviews WHERE id=?', (review_id,)).fetchone()
        if not r:
            return None
        findings = json.loads(r['findings'])
        vs = {v['idx']: v for v in self.db.execute(
            'SELECT idx, verdict, note FROM verdicts WHERE review_id=?', (review_id,))}
        for i, f in enumerate(findings):
            f['index'] = i
            v = vs.get(i)
            f['verdict'] = v['verdict'] if v else 'open'
            f['note'] = v['note'] if v else ''
        sc = rules.score(findings)
        return {'id': r['id'], 'ts': r['ts'], 'target': r['target'], 'kind': r['kind'],
                'engine': r['engine'], 'score': sc['score'], 'grade': sc['grade'],
                'counts': sc['counts'], 'findings': findings, 'summary': r['summary'],
                'meta': json.loads(r['meta'] or '{}')}

    def list(self, target='', engine='', limit=50, offset=0):
        q = 'SELECT id, ts, target, kind, engine, score, grade, counts, summary FROM reviews'
        where, args = [], []
        if target:
            where.append('target = ?')
            args.append(target)
        if engine:
            where.append('engine = ?')
            args.append(engine)
        if where:
            q += ' WHERE ' + ' AND '.join(where)
        q += ' ORDER BY ts DESC LIMIT ? OFFSET ?'
        args += [max(1, min(int(limit), 500)), max(0, int(offset))]
        out = []
        for r in self.db.execute(q, args):
            d = dict(r)
            d['counts'] = json.loads(d['counts'])
            d['summary'] = (d['summary'] or '')[:280]
            out.append(d)
        return out

    def stats(self):
        r = self.db.execute('SELECT COUNT(*) n, COUNT(DISTINCT target) t, AVG(score) s, MAX(ts) last FROM reviews').fetchone()
        return {'reviews': r['n'], 'targets': r['t'],
                'avg_score': round(r['s'], 1) if r['s'] is not None else None,
                'last_ts': r['last']}
