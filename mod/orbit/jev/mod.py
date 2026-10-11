"""jev — complete wrapper over jevgo, the Go client for TypeSafe AI's System One API.

System One evaluates a *state* (any JSON-serializable content) against named,
typed questions and answers each with a probability distribution:

    noul    yes/no        -> probability of yes (0-1)
    choice  pick one      -> chosen option, confidence, per-option probabilities
    score   rate levels   -> weighted position, confidence, per-level probabilities

Every call goes through one static Go binary built from src/ that imports
github.com/fgn/jevgo — client options, retries, timeouts and the full error
taxonomy are jevgo's, not a Python re-implementation, so this module can never
drift from the Go protocol client it wraps.

    m jev                                          # null call -> status()
    m jev/build                                    # go build src/ -> src/bin/jevbridge
    m jev/noul "refund demand, third email" instructions="Is the customer angry?"
    m jev/choice "I was charged twice" instructions="Which team?" options=billing,technical,other
    m jev/score "I was charged twice!!" instructions="How frustrated?" levels=Calm,Frustrated,Furious
    m jev/ask state="..." questions='{"urgent":{"kind":"noul","instructions":"Urgent?"}}'
    m jev/models                                   # ListModels
    m jev/status | m jev/test

Auth: TYPESAFE_API_KEY in the environment (TYPESAFE_BASE_URL and
TYPESAFE_DEFAULT_MODEL optional). The key is read by the Go binary and never
written anywhere in this module.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Every public method is a thin shell onto the bridge binary.
"""

import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')
BIN = os.path.join(SRC, 'bin', 'jevbridge')
GO = shutil.which('go') or '/usr/local/go/bin/go'


def _jsonish(v):
    """kwargs arrive as strings from the CLI; accept real values or JSON text."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def _list(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    v = _jsonish(v)
    if isinstance(v, (list, dict)):
        return v
    return [s.strip() for s in str(v).split(',') if s.strip()]


class Mod:
    description = """
    jev — typed questions over the Jev model, wrapped completely over jevgo
    (the Go client for TypeSafe AI's System One API). noul (yes/no
    probability), choice (option + distribution), score (ordered levels +
    distribution), models. One Go bridge binary does every network call.
    """

    def __init__(self, **kwargs):
        self.dir = HERE

    # ── plumbing ─────────────────────────────────────────────────

    def _bridge(self, op: str, payload: dict | None = None) -> dict:
        if not os.path.exists(BIN):
            built = self.build()
            if 'error' in built:
                return built
        proc = subprocess.run(
            [BIN, op],
            input=json.dumps(payload or {}),
            capture_output=True, text=True, timeout=300,
        )
        out = (proc.stdout or '').strip()
        try:
            return json.loads(out)
        except (ValueError, TypeError):
            return {'error': out or (proc.stderr or '').strip() or f'jevbridge {op} failed', 'code': 'bridge'}

    def _request(self, state, questions: dict, model=None, timeout_s=None, max_retries=None, extra=None) -> dict:
        req = {'state': _jsonish(state), 'questions': questions}
        if model:
            req['model'] = str(model)
        if timeout_s is not None:
            req['timeout_s'] = float(timeout_s)
        if max_retries is not None:
            req['max_retries'] = int(max_retries)
        if extra:
            req['extra'] = _jsonish(extra)
        return self._bridge('system_one', req)

    # ── the wrapped surface ──────────────────────────────────────

    def forward(self, state: str = '', **kwargs):
        """Null call -> status(); with a state, a noul question needs instructions=."""
        if not state:
            return self.status()
        return self.noul(state, **kwargs)

    def ask(self, state, questions, model=None, timeout_s=None, max_retries=None, extra=None) -> dict:
        """Full SystemOne: questions = {name: {kind: noul|choice|score|raw, instructions, criteria}}."""
        q = _jsonish(questions)
        if not isinstance(q, dict):
            return {'error': 'questions must be a dict (or JSON object string)', 'code': 'invalid_request'}
        return self._request(state, q, model, timeout_s, max_retries, extra)

    def noul(self, state, instructions: str, model=None, **kw) -> dict:
        """Yes/no: probability that `instructions` holds for `state`."""
        r = self._request(state, {'q': {'kind': 'noul', 'instructions': instructions}}, model, **kw)
        if 'error' in r:
            return r
        a = r['answers']['q']
        return {'noul': a['noul'], 'model': r['model'], 'usage': r['usage']}

    def choice(self, state, instructions: str, options, model=None, **kw) -> dict:
        """Pick one of `options` (list or 'a,b,c'): choice, confidence, probabilities."""
        opts = _list(options)
        criteria = opts if isinstance(opts, dict) else {str(o): None for o in opts}
        r = self._request(state, {'q': {'kind': 'choice', 'instructions': instructions, 'criteria': criteria}}, model, **kw)
        if 'error' in r:
            return r
        a = r['answers']['q']
        return {'choice': a['choice'], 'confidence': a['confidence'],
                'probabilities': a['probabilities'], 'model': r['model'], 'usage': r['usage']}

    def score(self, state, instructions: str, levels, model=None, **kw) -> dict:
        """Rate against ordered `levels` (list or 'low,mid,high'): score, confidence, distribution."""
        r = self._request(state, {'q': {'kind': 'score', 'instructions': instructions, 'criteria': _list(levels)}}, model, **kw)
        if 'error' in r:
            return r
        a = r['answers']['q']
        return {'score': a['score'], 'confidence': a['confidence'], 'legend': a.get('legend'),
                'probabilities': a['probabilities'], 'model': r['model'], 'usage': r['usage']}

    def models(self) -> dict:
        """ListModels — every model the API offers, with release dates."""
        return self._bridge('models')

    def info(self) -> dict:
        """What the client would use: base URL, default model, whether a key is set."""
        return self._bridge('info')

    # ── ops ──────────────────────────────────────────────────────

    def build(self) -> dict:
        """go mod tidy + go build src/ -> src/bin/jevbridge (needs network once, for jevgo)."""
        if not os.path.exists(GO):
            return {'error': f'go toolchain not found at {GO}', 'code': 'build'}
        env = {**os.environ, 'CGO_ENABLED': '0'}
        for cmd in (['mod', 'tidy'], ['build', '-o', BIN, './cmd/jevbridge']):
            proc = subprocess.run([GO] + cmd, cwd=SRC, env=env, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                return {'error': (proc.stderr or proc.stdout).strip(), 'code': 'build', 'cmd': 'go ' + ' '.join(cmd)}
        return {'built': BIN, 'size': os.path.getsize(BIN)}

    def status(self) -> dict:
        built = os.path.exists(BIN)
        out = {'module': 'jev', 'wraps': 'github.com/fgn/jevgo', 'built': built,
               'key_present': bool(os.environ.get('TYPESAFE_API_KEY'))}
        if built:
            out.update({k: v for k, v in self._bridge('info').items() if k != 'key_present'})
        return out

    def test(self) -> dict:
        """Offline checks: binary builds, info op answers, wire shapes are sane."""
        checks = {}
        built = self.build()
        checks['build'] = 'error' not in built
        if checks['build']:
            info = self._bridge('info')
            checks['info'] = info.get('client') == 'github.com/fgn/jevgo' and bool(info.get('base_url'))
            bad = self.ask('state', 'not-a-dict')
            checks['rejects_bad_questions'] = bad.get('code') == 'invalid_request'
        ok = all(checks.values())
        return {'ok': ok, 'checks': checks, **({} if ok else {'detail': built})}
