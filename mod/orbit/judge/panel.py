"""panel — the core of the judge module, no HTTP, no CLI.

A panel is a multisig of agent judges. The creator sets the params:
which judges sit on it, the approval threshold, and the quorum. To
judge an input, every judge scores it 0-100; the (weighted) average
must reach the threshold or the verdict is FAIL. Judges that error
don't vote — and if fewer than `min_votes` judges manage to vote,
the verdict fails closed.

Judge kinds:
  rule — deterministic, offline: length bounds, required / forbidden words.
  llm  — an agent: asks an OpenAI-compatible chat endpoint to score the
         input against the judge's criteria prompt.

State is one SQLite file in ~/.mod/judge/ (override with JUDGE_DIR).
"""

import json
import os
import re
import sqlite3
import time
import urllib.request

DEFAULT_LLM_URL = os.environ.get('JUDGE_LLM_URL',
                                 'http://localhost:50600/v1/chat/completions')


def _store_dir():
    return os.path.expanduser(os.environ.get('JUDGE_DIR', '~/.mod/judge'))


class Panels:

    def __init__(self, path=None):
        if path is None:
            d = _store_dir()
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, 'judge.db')
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS panels (
                name      TEXT PRIMARY KEY,
                creator   TEXT NOT NULL,
                threshold REAL NOT NULL,
                min_votes INTEGER NOT NULL,
                judges    TEXT NOT NULL,
                created   REAL NOT NULL,
                updated   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verdicts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                panel    TEXT NOT NULL,
                input    TEXT NOT NULL,
                scores   TEXT NOT NULL,
                average  REAL,
                threshold REAL NOT NULL,
                approved INTEGER NOT NULL,
                reason   TEXT NOT NULL,
                created  REAL NOT NULL
            );
        """)

    # ── panels (creator sets the params) ─────────────────────────

    def create(self, name, creator, judges, threshold=60.0, min_votes=None):
        if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
            return {'error': 'name must be lowercase letters/digits/-/_'}
        if not creator:
            return {'error': 'creator is required'}
        judges = self._check_judges(judges)
        if isinstance(judges, dict):
            return judges
        threshold = float(threshold)
        if not 0 <= threshold <= 100:
            return {'error': 'threshold must be 0-100'}
        # Fail-closed default: every judge must vote, like a full multisig.
        min_votes = len(judges) if min_votes is None else int(min_votes)
        if not 1 <= min_votes <= len(judges):
            return {'error': f'min_votes must be 1-{len(judges)}'}
        now = time.time()
        try:
            self.db.execute(
                'INSERT INTO panels VALUES (?,?,?,?,?,?,?)',
                (name, creator, threshold, min_votes, json.dumps(judges), now, now))
            self.db.commit()
        except sqlite3.IntegrityError:
            return {'error': f'panel {name!r} already exists'}
        return self.get(name)

    def get(self, name):
        r = self.db.execute('SELECT * FROM panels WHERE name=?', (name,)).fetchone()
        if not r:
            return {'error': f'no panel {name!r}'}
        p = dict(r)
        p['judges'] = json.loads(p['judges'])
        return p

    def list(self):
        out = []
        for r in self.db.execute('SELECT * FROM panels ORDER BY name'):
            p = dict(r)
            p['judges'] = json.loads(p['judges'])
            p['verdicts'] = self.db.execute(
                'SELECT COUNT(*) FROM verdicts WHERE panel=?', (p['name'],)).fetchone()[0]
            out.append(p)
        return out

    def update(self, name, creator, judges=None, threshold=None, min_votes=None):
        p = self.get(name)
        if 'error' in p:
            return p
        if creator != p['creator']:
            return {'error': 'only the creator can change a panel'}
        if judges is not None:
            judges = self._check_judges(judges)
            if isinstance(judges, dict):
                return judges
            p['judges'] = judges
            if min_votes is None:
                p['min_votes'] = min(p['min_votes'], len(judges))
        if threshold is not None:
            threshold = float(threshold)
            if not 0 <= threshold <= 100:
                return {'error': 'threshold must be 0-100'}
            p['threshold'] = threshold
        if min_votes is not None:
            min_votes = int(min_votes)
            if not 1 <= min_votes <= len(p['judges']):
                return {'error': f'min_votes must be 1-{len(p["judges"])}'}
            p['min_votes'] = min_votes
        self.db.execute(
            'UPDATE panels SET threshold=?, min_votes=?, judges=?, updated=? WHERE name=?',
            (p['threshold'], p['min_votes'], json.dumps(p['judges']), time.time(), name))
        self.db.commit()
        return self.get(name)

    def remove(self, name, creator):
        p = self.get(name)
        if 'error' in p:
            return p
        if creator != p['creator']:
            return {'error': 'only the creator can remove a panel'}
        self.db.execute('DELETE FROM panels WHERE name=?', (name,))
        self.db.commit()
        return {'removed': name}

    def _check_judges(self, judges):
        """Validate a judges list; return the cleaned list or an {'error': ...}."""
        if isinstance(judges, str):
            try:
                judges = json.loads(judges)
            except Exception:
                return {'error': 'judges must be a JSON list'}
        if not isinstance(judges, list) or not judges:
            return {'error': 'judges must be a non-empty list'}
        names = set()
        for j in judges:
            if not isinstance(j, dict) or not j.get('name'):
                return {'error': 'each judge needs a name'}
            if j['name'] in names:
                return {'error': f'duplicate judge name {j["name"]!r}'}
            names.add(j['name'])
            kind = j.get('kind', 'llm')
            if kind not in ('llm', 'rule'):
                return {'error': f'judge {j["name"]!r}: kind must be llm or rule'}
            if kind == 'llm' and not j.get('prompt'):
                return {'error': f'judge {j["name"]!r}: llm judge needs a criteria prompt'}
            if float(j.get('weight', 1)) <= 0:
                return {'error': f'judge {j["name"]!r}: weight must be > 0'}
        return judges

    # ── judging ──────────────────────────────────────────────────

    def judge(self, panel, input):
        """Put one input before the panel and record the verdict."""
        p = self.get(panel)
        if 'error' in p:
            return p
        if not input:
            return {'error': 'nothing to judge'}
        scores = [self._score(j, input) for j in p['judges']]
        voted = [s for s in scores if s['score'] is not None]
        if len(voted) < p['min_votes']:
            approved, average = False, None
            reason = (f'quorum not met: {len(voted)}/{len(scores)} judges voted, '
                      f'{p["min_votes"]} required — fails closed')
        else:
            total_w = sum(s['weight'] for s in voted)
            average = sum(s['score'] * s['weight'] for s in voted) / total_w
            approved = average >= p['threshold']
            reason = (f'average {average:.1f} '
                      f'{">=" if approved else "<"} threshold {p["threshold"]:g}')
        now = time.time()
        cur = self.db.execute(
            'INSERT INTO verdicts (panel, input, scores, average, threshold,'
            ' approved, reason, created) VALUES (?,?,?,?,?,?,?,?)',
            (panel, input, json.dumps(scores), average, p['threshold'],
             int(approved), reason, now))
        self.db.commit()
        return {'id': cur.lastrowid, 'panel': panel, 'approved': approved,
                'average': average, 'threshold': p['threshold'],
                'reason': reason, 'scores': scores, 'created': now}

    def verdict(self, id):
        r = self.db.execute('SELECT * FROM verdicts WHERE id=?', (id,)).fetchone()
        if not r:
            return {'error': f'no verdict {id}'}
        v = dict(r)
        v['scores'] = json.loads(v['scores'])
        v['approved'] = bool(v['approved'])
        return v

    def verdicts(self, panel='', limit=50, offset=0):
        q, args = 'SELECT * FROM verdicts', []
        if panel:
            q, args = q + ' WHERE panel=?', [panel]
        q += ' ORDER BY id DESC LIMIT ? OFFSET ?'
        rows = self.db.execute(q, args + [int(limit), int(offset)]).fetchall()
        out = []
        for r in rows:
            v = dict(r)
            v['scores'] = json.loads(v['scores'])
            v['approved'] = bool(v['approved'])
            out.append(v)
        return out

    def stats(self):
        n_p = self.db.execute('SELECT COUNT(*) FROM panels').fetchone()[0]
        n_v = self.db.execute('SELECT COUNT(*) FROM verdicts').fetchone()[0]
        return {'panels': n_p, 'verdicts': n_v}

    # ── the judges themselves ────────────────────────────────────

    def _score(self, j, text):
        """One judge's vote: {name, score 0-100 | None, weight, reason}."""
        kind = j.get('kind', 'llm')
        try:
            if kind == 'rule':
                score, reason = _rule_score(j, text)
            else:
                score, reason = _llm_score(j, text)
            score = max(0.0, min(100.0, float(score)))
        except Exception as e:
            score, reason = None, f'judge error: {e}'
        return {'name': j['name'], 'kind': kind, 'score': score,
                'weight': float(j.get('weight', 1)), 'reason': reason}


def _rule_score(j, text):
    """Deterministic judge: starts at 100, loses points per violated rule."""
    score, faults = 100.0, []
    n = len(text)
    if 'min_len' in j and n < int(j['min_len']):
        score -= 50; faults.append(f'shorter than {j["min_len"]}')
    if 'max_len' in j and n > int(j['max_len']):
        score -= 50; faults.append(f'longer than {j["max_len"]}')
    low = text.lower()
    for w in j.get('require', []):
        if w.lower() not in low:
            score -= 50; faults.append(f'missing {w!r}')
    for w in j.get('forbid', []):
        if w.lower() in low:
            score -= 50; faults.append(f'contains {w!r}')
    return max(score, 0.0), ('; '.join(faults) or 'all rules pass')


def _llm_score(j, text):
    """Agent judge: ask an OpenAI-compatible endpoint to score 0-100."""
    url = j.get('url') or DEFAULT_LLM_URL
    system = (f'You are {j["name"]!r}, one judge on a review panel. '
              f'Criteria: {j["prompt"]} '
              'Score the INPUT from 0 (worst) to 100 (best) against the criteria. '
              'Reply with ONLY a JSON object: {"score": <number>, "reason": "<one line>"}')
    body = {'model': j.get('model', 'auto'),
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': f'INPUT:\n{text}'}],
            'temperature': 0, 'max_tokens': 200}
    headers = {'Content-Type': 'application/json'}
    key_env = j.get('api_key_env')
    if key_env and os.environ.get(key_env):
        headers['Authorization'] = f'Bearer {os.environ[key_env]}'
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=float(j.get('timeout', 60))) as r:
        data = json.loads(r.read().decode())
    content = data['choices'][0]['message']['content']
    m = re.search(r'\{.*\}', content, re.S)
    if m:
        try:
            v = json.loads(m.group(0))
            return float(v['score']), str(v.get('reason', ''))[:400]
        except Exception:
            pass
    m = re.search(r'-?\d+(\.\d+)?', content)
    if not m:
        raise ValueError(f'no score in reply: {content[:120]!r}')
    return float(m.group(0)), content.strip()[:400]
