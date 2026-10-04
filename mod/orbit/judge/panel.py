"""panel — the core of the judge module, no HTTP, no CLI.

A panel is a multisig of agent judges. The creator sets the params:
which judges sit on it, the approval threshold, and the quorum. To
judge an input, every judge scores it 0-100; the (weighted) average
must reach the threshold or the verdict is FAIL. Judges that error
don't vote — and if fewer than `min_votes` judges manage to vote,
the verdict fails closed.

Judge kinds:
  rule  — deterministic, offline: length bounds, required / forbidden words.
  llm   — asks an OpenAI-compatible chat endpoint to score the input
          against the judge's criteria prompt.
  agent — an agent-protocol agent (orbit/agent POST /run with an
          agent_type); its finish summary is parsed as the score.
  panel — another panel sits as one judge: its vote is that panel's
          weighted average. Judges of judges — cycles fail closed.
          The agent/panel kinds and the judge market live in market.py.

Every judge gets a keyring the first time it sits on a panel: one keypair
per key type in keys.py (classical ed25519 plus the quantum-resistant
ml-dsa-65 and wots-sha256). Every vote is signed by every key in the ring,
so the append-only verdict record is tamper-evident — see verify_verdict.
Secrets stay in the local store; panels publish only public keys.

State is one SQLite file in ~/.mod/judge/ (override with JUDGE_DIR).
"""

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request

import keys
import market

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
            CREATE TABLE IF NOT EXISTS judge_keys (
                panel   TEXT NOT NULL,
                judge   TEXT NOT NULL,
                ktype   TEXT NOT NULL,
                public  TEXT NOT NULL,
                secret  TEXT NOT NULL,
                state   TEXT NOT NULL,
                created REAL NOT NULL,
                PRIMARY KEY (panel, judge, ktype)
            );
        """)

    # ── panels (creator sets the params) ─────────────────────────

    def create(self, name, creator, judges, threshold=60.0, min_votes=None):
        if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
            return {'error': 'name must be lowercase letters/digits/-/_'}
        if not creator:
            return {'error': 'creator is required'}
        judges = self._check_judges(judges, name)
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
        self._ensure_keys(name, judges)
        return self.get(name)

    def get(self, name):
        r = self.db.execute('SELECT * FROM panels WHERE name=?', (name,)).fetchone()
        if not r:
            return {'error': f'no panel {name!r}'}
        p = dict(r)
        p['judges'] = json.loads(p['judges'])
        for j in p['judges']:
            j['keys'] = self._public_ring(name, j['name'])
        return p

    def list(self):
        out = []
        for r in self.db.execute('SELECT * FROM panels ORDER BY name'):
            p = dict(r)
            p['judges'] = json.loads(p['judges'])
            for j in p['judges']:
                j['keys'] = self._public_ring(p['name'], j['name'])
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
            judges = self._check_judges(judges, name)
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
        # The stored row holds only the judge params — rings live in judge_keys.
        stored = [{k: v for k, v in j.items() if k != 'keys'} for j in p['judges']]
        self.db.execute(
            'UPDATE panels SET threshold=?, min_votes=?, judges=?, updated=? WHERE name=?',
            (p['threshold'], p['min_votes'], json.dumps(stored), time.time(), name))
        self.db.commit()
        self._ensure_keys(name, stored)
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

    def _check_judges(self, judges, panel_name=None):
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
            j.pop('keys', None)  # rings are issued here, never client-supplied
            if j['name'] in names:
                return {'error': f'duplicate judge name {j["name"]!r}'}
            names.add(j['name'])
            kind = j.get('kind', 'llm')
            if kind not in ('llm', 'rule', 'agent', 'panel'):
                return {'error': f'judge {j["name"]!r}: kind must be llm, rule, agent or panel'}
            if kind == 'llm' and not j.get('prompt'):
                return {'error': f'judge {j["name"]!r}: llm judge needs a criteria prompt'}
            err = market.check_judge(self, j, kind, panel_name)
            if err:
                return err
            if float(j.get('weight', 1)) <= 0:
                return {'error': f'judge {j["name"]!r}: weight must be > 0'}
        return judges

    # ── keyrings: one keypair per key type, per judge ────────────

    def _ensure_keys(self, panel, judges):
        """Issue the missing keys for every judge on the panel. A judge keeps
        its ring across panel updates; only brand-new (panel, judge, type)
        combinations generate. Keys are never deleted — verdicts outlive
        panels, and so must the keys that signed them."""
        for j in judges:
            have = {r['ktype'] for r in self.db.execute(
                'SELECT ktype FROM judge_keys WHERE panel=? AND judge=?',
                (panel, j['name']))}
            for ktype in keys.available():
                if ktype in have:
                    continue
                k = keys.generate(ktype)
                self.db.execute(
                    'INSERT INTO judge_keys VALUES (?,?,?,?,?,?,?)',
                    (panel, j['name'], ktype, k['public'],
                     json.dumps(k['secret']), json.dumps(k['state']), time.time()))
        self.db.commit()

    def _public_ring(self, panel, judge):
        """The judge's keyring, public side only — secrets never leave."""
        ring, kinds = {}, keys.kinds()
        for r in self.db.execute(
                'SELECT ktype, public, secret, state FROM judge_keys'
                ' WHERE panel=? AND judge=?', (panel, judge)):
            info = kinds.get(r['ktype'], {})
            entry = {'public': r['public'],
                     'fingerprint': keys.fingerprint(r['public']),
                     'quantum_resistant': bool(info.get('quantum_resistant')),
                     'algo': info.get('algo')}
            state = json.loads(r['state'])
            if 'next' in state:  # one-time-leaf schemes report what's left
                height = int(json.loads(r['secret'])['height'])
                entry['sigs_left'] = 2 ** height - int(state['next'])
            ring[r['ktype']] = entry
        return ring

    # ── judging ──────────────────────────────────────────────────

    def judge(self, panel, input, _seen=None):
        """Put one input before the panel and record the verdict. `_seen`
        is the chain of panels already deliberating this input — panel-kind
        judges thread it down so a panel can never (transitively) sit on
        its own bench at judge time."""
        p = self.get(panel)
        if 'error' in p:
            return p
        if not input:
            return {'error': 'nothing to judge'}
        seen = (_seen or frozenset()) | {panel}
        scores = [self._score(j, input, seen) for j in p['judges']]
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
        for s in scores:
            s['sigs'] = self._sign_vote(panel, input, s, now)
        cur = self.db.execute(
            'INSERT INTO verdicts (panel, input, scores, average, threshold,'
            ' approved, reason, created) VALUES (?,?,?,?,?,?,?,?)',
            (panel, input, json.dumps(scores), average, p['threshold'],
             int(approved), reason, now))
        self.db.commit()
        return {'id': cur.lastrowid, 'panel': panel, 'approved': approved,
                'average': average, 'threshold': p['threshold'],
                'reason': reason, 'scores': scores, 'created': now}

    def _sign_vote(self, panel, input, score, created):
        """Sign one judge's vote with every key in its ring. Each entry
        carries the public key it was made under, so a verdict stays
        verifiable on its own even after the panel is gone. State updates
        (one-time leaves) ride the verdict's commit."""
        msg = _vote_msg(panel, input, score, created)
        sigs = {}
        for r in self.db.execute(
                'SELECT ktype, public, secret, state FROM judge_keys'
                ' WHERE panel=? AND judge=?', (panel, score['name'])):
            state = json.loads(r['state'])
            try:
                sig, note = keys.sign(r['ktype'], json.loads(r['secret']),
                                      state, msg)
            except Exception as e:  # a broken key must not block the verdict
                sig, note = None, f'signing error: {e}'
            entry = {'pub': r['public']}
            if sig is not None:
                entry['sig'] = sig
            if note:
                entry['note'] = note
            sigs[r['ktype']] = entry
            self.db.execute(
                'UPDATE judge_keys SET state=? WHERE panel=? AND judge=? AND ktype=?',
                (json.dumps(state), panel, score['name'], r['ktype']))
        return sigs

    def verify_verdict(self, id):
        """Re-check every signature on one verdict against the stored record.
        verified is True only when no signature fails AND every judge's vote
        carries at least one valid quantum-resistant signature — a single
        bad signature means the record was altered, and a vote attested
        only classically doesn't count as verified."""
        v = self.verdict(id)
        if 'error' in v:
            return v
        judges, all_ok, kinds = [], True, keys.kinds()
        for s in v['scores']:
            msg = _vote_msg(v['panel'], v['input'], s, v['created'])
            checks, qr_good = {}, 0
            for ktype, e in (s.get('sigs') or {}).items():
                if 'sig' not in e:
                    checks[ktype] = {'ok': None, 'note': e.get('note', 'unsigned')}
                    continue
                ok = keys.verify(ktype, e['pub'], e['sig'], msg)
                checks[ktype] = {'ok': ok,
                                 'fingerprint': keys.fingerprint(e['pub'])}
                if ok and kinds.get(ktype, {}).get('quantum_resistant'):
                    qr_good += 1
                all_ok = all_ok and ok
            all_ok = all_ok and qr_good > 0
            judges.append({'judge': s['name'], 'checks': checks})
        return {'id': v['id'], 'panel': v['panel'], 'verified': all_ok,
                'judges': judges}

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

    def _score(self, j, text, seen=frozenset()):
        """One judge's vote: {name, score 0-100 | None, weight, reason}."""
        kind = j.get('kind', 'llm')
        try:
            if kind == 'rule':
                score, reason = _rule_score(j, text)
            elif kind == 'agent':
                score, reason = market.agent_score(j, text)
            elif kind == 'panel':
                score, reason = market.panel_score(self, j, text, seen)
            else:
                score, reason = _llm_score(j, text)
            score = max(0.0, min(100.0, float(score)))
        except Exception as e:
            score, reason = None, f'judge error: {e}'
        return {'name': j['name'], 'kind': kind, 'score': score,
                'weight': float(j.get('weight', 1)), 'reason': reason}


def _vote_msg(panel, input, score, created):
    """The canonical bytes a vote signature covers: the vote's own fields
    plus the panel, a hash of the input, and the verdict's timestamp.
    Keys sorted, no whitespace — byte-identical at sign and verify time."""
    return json.dumps(
        {'panel': panel,
         'input_sha256': hashlib.sha256(input.encode()).hexdigest(),
         'judge': score['name'], 'kind': score['kind'], 'score': score['score'],
         'weight': score['weight'], 'reason': score['reason'],
         'created': created},
        sort_keys=True, separators=(',', ':')).encode()


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
