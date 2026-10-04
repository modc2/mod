"""market — agent-protocol judges, panels of panels, and the judge market.

Extends panel.py without owning it: `Panels` dispatches here for the two
composable judge kinds, and `Market` is a listing board kept in the same
SQLite file.

  agent judge — the judge is an agent speaking the agent protocol
      (orbit/agent): the input is posed as a run (POST /run with an
      agent_type) and the run's finish summary is parsed as
      {"score": 0-100, "reason": "..."}.
  panel judge — the judge IS another panel: its vote is that panel's
      weighted average, so panels compose into panels of panels. The
      sub-verdict is recorded (and signed) like any other; a cycle
      fails closed as a judge error.
  market — publish a judge spec under (name, author), browse and
      search listings, install one onto a panel you created.
"""

import json
import os
import re
import time
import urllib.request

# The agent protocol: orbit/agent's blocking run endpoint.
DEFAULT_AGENT_URL = os.environ.get('JUDGE_AGENT_URL',
                                   'http://localhost:50117/run')


# ── the two composable judge kinds ──────────────────────────────


def check_judge(panels, j, kind, panel_name=None):
    """Kind-specific validation for agent/panel judges; None when fine."""
    if kind == 'agent':
        if not j.get('agent_type'):
            return {'error': f'judge {j["name"]!r}: agent judge needs an agent_type'}
        if not j.get('prompt'):
            return {'error': f'judge {j["name"]!r}: agent judge needs a criteria prompt'}
    if kind == 'panel':
        sub = j.get('panel')
        if not sub:
            return {'error': f'judge {j["name"]!r}: panel judge needs the panel it delegates to'}
        if panel_name and sub == panel_name:
            return {'error': f'judge {j["name"]!r}: a panel cannot sit on its own bench'}
        if 'error' in panels.get(sub):
            return {'error': f'judge {j["name"]!r}: no panel {sub!r} to delegate to'}
    return None


def agent_score(j, text):
    """Agent-protocol judge: pose the input as a run, read the finish summary."""
    url = j.get('url') or DEFAULT_AGENT_URL
    query = (f'You are {j["name"]!r}, one judge on a review panel. '
             f'Criteria: {j["prompt"]} '
             'Score the INPUT from 0 (worst) to 100 (best) against the criteria. '
             'Finish with ONLY a JSON object: '
             '{"score": <number>, "reason": "<one line>"}'
             f'\n\nINPUT:\n{text}')
    # A scoring run needs no tools — a tight step budget keeps it quick
    # (the fleet default provider can be a local CPU model).
    body = {'query': query, 'agent_type': j['agent_type'],
            'steps': int(j.get('steps', 3)), 'temperature': 0,
            'free': bool(j.get('free', True))}
    for k in ('model', 'provider', 'key'):
        if j.get(k):
            body[k] = j[k]
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=float(j.get('timeout', 300))) as r:
        data = json.loads(r.read().decode())
    if data.get('error'):
        raise ValueError(f'agent run failed: {data["error"]}')
    answer = _answer_of(data.get('result'))
    if not answer:
        raise ValueError('agent run produced no answer')
    return parse_score(answer)


def _answer_of(result):
    """A run's answer, by the protocol's own rule: the finish step's
    params.summary, else the last response step."""
    if isinstance(result, str):
        return result
    if not isinstance(result, list):
        return ''
    summary = response = ''
    for s in result:
        if not isinstance(s, dict):
            continue
        if s.get('tool') == 'finish':
            summary = s.get('params', {}).get('summary', '') or summary
        elif s.get('tool') == 'response' and s.get('result'):
            response = str(s['result'])
    return summary or response


def panel_score(panels, j, text, seen):
    """A panel sitting as one judge: its vote is the sub-panel's average."""
    sub = j['panel']
    if sub in seen:
        raise ValueError(f'panel cycle: {sub!r} is already deliberating')
    v = panels.judge(sub, text, _seen=seen)
    if 'error' in v:
        raise ValueError(v['error'])
    if v['average'] is None:
        raise ValueError(f'sub-panel {sub!r} verdict #{v["id"]}: {v["reason"]}')
    return v['average'], (f'sub-panel {sub!r} verdict #{v["id"]} '
                          f'{"PASS" if v["approved"] else "FAIL"}: {v["reason"]}')


def parse_score(content):
    """{"score": n, "reason": ...} out of a judge's reply, bare-number fallback."""
    m = re.search(r'\{[^{}]*"score"[^{}]*\}', content, re.S)
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


# ── the judge market ────────────────────────────────────────────

SEED_LISTINGS = [
    ('concise',
     'Offline rule judge: fails walls of text (over 1200 characters).',
     {'kind': 'rule', 'max_len': 1200}, ['style', 'offline']),
    ('clarity',
     'LLM judge: is the input clear, specific and unambiguous?',
     {'kind': 'llm',
      'prompt': 'Is the input clear, specific and unambiguous? Reward '
                'concrete, well-structured writing; punish vagueness and filler.'},
     ['quality']),
    ('reviewer',
     'Agent-protocol judge: an orbit/agent agent reviews the input as a '
     'proposed change.',
     {'kind': 'agent', 'agent_type': 'default',
      'prompt': 'Review the input as a proposed change: is it correct, '
                'safe and complete?'},
     ['agent', 'review']),
]


class Market:
    """The judge market: a listing board in the panels' own SQLite file.

    Publishing is open — a listing is just a spec, it runs nothing until
    someone seats it. Re-publishing the same (name, author) updates the
    listing in place. Installing appends the spec to a panel's bench,
    which only that panel's creator can do."""

    def __init__(self, panels):
        self.panels = panels
        self.db = panels.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS market (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                author      TEXT NOT NULL,
                description TEXT NOT NULL,
                tags        TEXT NOT NULL,
                spec        TEXT NOT NULL,
                installs    INTEGER NOT NULL DEFAULT 0,
                created     REAL NOT NULL,
                updated     REAL NOT NULL,
                UNIQUE(name, author)
            );
        """)
        self._seed()

    def _seed(self):
        if self.db.execute('SELECT COUNT(*) FROM market').fetchone()[0]:
            return
        for name, desc, spec, tags in SEED_LISTINGS:
            self.publish(name, 'judge', spec, desc, tags)

    def publish(self, name, author, spec, description='', tags=None):
        """List a judge. Same (name, author) again = update your listing."""
        if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
            return {'error': 'name must be lowercase letters/digits/-/_'}
        if not author:
            return {'error': 'author is required'}
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except Exception:
                return {'error': 'spec must be a JSON object'}
        if not isinstance(spec, dict):
            return {'error': 'spec must be a JSON object'}
        spec = dict(spec, name=name)
        checked = self.panels._check_judges([spec])
        if isinstance(checked, dict):
            return checked
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        now = time.time()
        self.db.execute(
            'INSERT INTO market (name, author, description, tags, spec,'
            ' installs, created, updated) VALUES (?,?,?,?,?,0,?,?)'
            ' ON CONFLICT(name, author) DO UPDATE SET'
            ' description=excluded.description, tags=excluded.tags,'
            ' spec=excluded.spec, updated=excluded.updated',
            (name, author, str(description or ''),
             json.dumps(list(tags or [])), json.dumps(checked[0]), now, now))
        self.db.commit()
        r = self.db.execute('SELECT * FROM market WHERE name=? AND author=?',
                            (name, author)).fetchone()
        return self._row(r)

    def list(self, q='', kind=''):
        """Listings, most installed first; q searches name/author/desc/tags."""
        ql = str(q or '').lower().strip()
        out = []
        for r in self.db.execute(
                'SELECT * FROM market ORDER BY installs DESC, name'):
            d = self._row(r)
            if kind and d['kind'] != kind:
                continue
            hay = ' '.join([d['name'], d['author'], d['description'],
                            ' '.join(d['tags'])]).lower()
            if ql and ql not in hay:
                continue
            out.append(d)
        return out

    def get(self, id):
        r = self.db.execute('SELECT * FROM market WHERE id=?', (id,)).fetchone()
        if not r:
            return {'error': f'no listing {id}'}
        return self._row(r)

    def unpublish(self, id, author):
        d = self.get(id)
        if 'error' in d:
            return d
        if author != d['author']:
            return {'error': 'only the author can unpublish a listing'}
        self.db.execute('DELETE FROM market WHERE id=?', (d['id'],))
        self.db.commit()
        return {'unpublished': d['id'], 'name': d['name']}

    def install(self, id, panel, creator, name=None, weight=None):
        """Seat a listing on a panel — the panel's creator only. A name
        clash on the bench auto-suffixes rather than failing the install."""
        d = self.get(id)
        if 'error' in d:
            return d
        p = self.panels.get(panel)
        if 'error' in p:
            return p
        judge = dict(d['spec'])
        if name:
            judge['name'] = str(name)
        taken = {j['name'] for j in p['judges']}
        base, n = judge['name'], 2
        while judge['name'] in taken:
            judge['name'] = f'{base}-{n}'
            n += 1
        if weight is not None:
            judge['weight'] = float(weight)
        out = self.panels.update(panel, creator, judges=p['judges'] + [judge])
        if 'error' in out:
            return out
        self.db.execute('UPDATE market SET installs = installs + 1 WHERE id=?',
                        (d['id'],))
        self.db.commit()
        return {'installed': judge['name'], 'listing': d['id'], 'panel': out}

    def _row(self, r):
        d = dict(r)
        d['tags'] = json.loads(d['tags'])
        d['spec'] = json.loads(d['spec'])
        d['kind'] = d['spec'].get('kind', 'llm')
        return d
