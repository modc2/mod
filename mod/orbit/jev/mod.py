"""jev — complete wrapper over jevgo, the Go client for TypeSafe AI's System One
API, served over the mod protocol with crypto pay-per-question and no KYC.

System One evaluates a *state* (any JSON-serializable content) against named,
typed questions and answers each with a probability distribution:

    noul    yes/no        -> probability of yes (0-1)
    choice  pick one      -> chosen option, confidence, per-option probabilities
    score   rate levels   -> weighted position, level, confidence, per-level probabilities

Every call goes through one static Go binary built from src/ that imports
github.com/fgn/jevgo — client options, retries, headers, logging, tracing and
the full error taxonomy are jevgo's, not a Python re-implementation, so this
module can never drift from the Go protocol client it wraps.

    m jev                                          # null call -> status()
    m jev/build                                    # go build src/ -> src/bin/jevbridge
    m jev/noul "refund demand, third email" instructions="Is the customer angry?"
    m jev/choice "I was charged twice" instructions="Which team?" options=billing,technical,other
    m jev/score "I was charged twice!!" instructions="How frustrated?" levels=Calm,Frustrated,Furious
    m jev/ask state="..." questions='{"urgent":{"kind":"noul","instructions":"Urgent?"}}'
    m jev/encode state="..." questions='{...}'      # offline: validate + exact wire body
    m jev/models                                   # ListModels
    m jev/serve                                    # console + API + MCP on :50127 (pm2: background=1)
    m jev/price · m jev/pricing pay_to=0x… price_usd=0.001 · m jev/grant 0x… usd=1

Auth to TypeSafe: TYPESAFE_API_KEY in the environment (TYPESAFE_BASE_URL,
TYPESAFE_DEFAULT_MODEL, TYPESAFE_LOG_LEVEL optional). The key is read by the
Go binary and never written anywhere in this module.

Paying: callers that are not this box pay in USDC per question — x402 (HTTP
402, sign, retry) or prepaid credits under a mod-protocol token. See jevpay.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Every public method is a thin shell onto the bridge binary.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')
BIN = os.path.join(SRC, 'bin', 'jevbridge')
GO = shutil.which('go') or '/usr/local/go/bin/go'
PM2_NAME = 'jev'

if HERE not in sys.path:
    sys.path.append(HERE)

import jevpay                                             # noqa: E402
import jevrelay                                           # noqa: E402

OPTION_KEYS = ('model', 'timeout_s', 'max_retries', 'retry', 'headers', 'log_level',
               'include_headers', 'include_raw', 'trace', 'base_url', 'extra')


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


def _config():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f)
    except Exception:                                     # noqa: BLE001
        return {}


class Mod:
    description = """
    jev — typed questions over the Jev model, wrapped completely over jevgo
    (the Go client for TypeSafe AI's System One API). noul (yes/no
    probability), choice (option + distribution), score (ordered levels +
    distribution), models. One Go bridge binary does every network call.
    Served over the mod protocol: callers pay per question in USDC (x402 or
    prepaid credits under a mod token), no account, no KYC.
    """

    def __init__(self, **kwargs):
        self.dir = HERE
        cfg = _config()
        self.port = int(kwargs.get('port') or cfg.get('port') or 50127)
        self.base = cfg.get('base_path') or '/jev'
        self.paywall = jevpay.Gate('jev')     # for m.serve('jev', paywall=Mod().paywall)

    # ── plumbing ─────────────────────────────────────────────────

    def _bridge(self, op: str, payload: dict | None = None) -> dict:
        if not os.path.exists(BIN):
            built = self.build()
            if 'error' in built:
                return built
        try:
            proc = subprocess.run(
                [BIN, op],
                input=json.dumps(payload or {}),
                capture_output=True, text=True, timeout=330,
            )
        except subprocess.TimeoutExpired:
            return {'error': f'jevbridge {op}: no answer in 330s', 'code': 'timeout'}
        out = (proc.stdout or '').strip()
        try:
            return json.loads(out)
        except (ValueError, TypeError):
            return {'error': out or (proc.stderr or '').strip() or f'jevbridge {op} failed', 'code': 'bridge'}

    def _options(self, kw: dict) -> dict:
        """Everything jevgo can be configured with, from kwargs → wire fields."""
        req = {}
        for k in OPTION_KEYS:
            v = kw.get(k)
            if v is None or v == '':
                continue
            if k in ('retry', 'headers', 'extra'):
                v = _jsonish(v)
                if not isinstance(v, dict):
                    raise ValueError(f'{k} must be a JSON object')
            elif k == 'timeout_s':
                v = float(v)
            elif k == 'max_retries':
                v = int(v)
            elif k in ('include_headers', 'include_raw', 'trace'):
                v = v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'yes', 'on')
            else:
                v = str(v)
            req[k] = v
        return req

    def _request(self, state, questions: dict, **kw) -> dict:
        try:
            req = {'state': _jsonish(state), 'questions': questions, **self._options(kw)}
        except ValueError as e:
            return {'error': str(e), 'code': 'invalid_request'}
        if jevrelay.backend() == 'relay':
            # No TypeSafe key here: jevgo still validates and encodes the exact
            # wire body, and a Jev x402 seller answers it (see jevrelay).
            enc = self._bridge('encode', req)
            if 'error' in enc:
                return enc
            return jevrelay.system_one(enc['body'])
        return self._bridge('system_one', req)

    @staticmethod
    def _one(r, name='q'):
        if 'error' in r:
            return r
        a = r['answers'][name]
        meta = {k: r[k] for k in ('model', 'usage', 'request_id', 'attempts', 'relay') if k in r}
        for k in ('headers', 'raw', 'trace'):
            if k in r:
                meta[k] = r[k]
        return a, meta

    # ── the wrapped surface ──────────────────────────────────────

    def forward(self, state: str = '', **kwargs):
        """Null call -> status(); with a state, a noul question needs instructions=."""
        if not state:
            return self.status()
        return self.noul(state, **kwargs)

    def ask(self, state, questions, **kw) -> dict:
        """Full SystemOne: questions = {name: {kind: noul|choice|score|raw, instructions, criteria}}.
        Options: model, timeout_s, max_retries, retry{}, headers{}, log_level,
        include_headers, include_raw, trace, extra{}."""
        q = _jsonish(questions)
        if not isinstance(q, dict) or not q:
            return {'error': 'questions must be a non-empty dict (or JSON object string)', 'code': 'invalid_request'}
        return self._request(state, q, **kw)

    def noul(self, state, instructions: str, criteria=None, **kw) -> dict:
        """Yes/no: probability that `instructions` holds for `state`.
        criteria = {true: ..., false: ...} optionally describes the outcomes."""
        q = {'kind': 'noul', 'instructions': _jsonish(instructions)}
        c = _jsonish(criteria)
        if isinstance(c, dict):
            q['criteria'] = c
        r = self._one(self._request(state, {'q': q}, **kw))
        if isinstance(r, dict):
            return r
        a, meta = r
        return {'noul': a['noul'], **meta}

    def choice(self, state, instructions: str, options, **kw) -> dict:
        """Pick one of `options` (list, 'a,b,c' or {option: description}): choice, confidence, probabilities."""
        opts = _list(options)
        criteria = opts if isinstance(opts, dict) else {str(o): None for o in (opts or [])}
        if not criteria:
            return {'error': 'options must name at least one option', 'code': 'invalid_request'}
        r = self._one(self._request(state, {'q': {'kind': 'choice', 'instructions': _jsonish(instructions),
                                                  'criteria': criteria}}, **kw))
        if isinstance(r, dict):
            return r
        a, meta = r
        return {'choice': a['choice'], 'confidence': a['confidence'], 'probabilities': a['probabilities'], **meta}

    def score(self, state, instructions: str, levels, **kw) -> dict:
        """Rate against ordered `levels` (list or 'low,mid,high'): score, level, confidence, distribution."""
        lv = _list(levels)
        if not isinstance(lv, list) or not lv:
            return {'error': 'levels must be a non-empty ordered list', 'code': 'invalid_request'}
        r = self._one(self._request(state, {'q': {'kind': 'score', 'instructions': _jsonish(instructions),
                                                  'criteria': lv}}, **kw))
        if isinstance(r, dict):
            return r
        a, meta = r
        return {'score': a['score'], 'level': a.get('level'), 'confidence': a['confidence'],
                'legend': a.get('legend'), 'probabilities': a['probabilities'], **meta}

    def encode(self, state, questions, **kw) -> dict:
        """Offline: validate exactly as SystemOne would and return the wire body. Never sends."""
        q = _jsonish(questions)
        if not isinstance(q, dict) or not q:
            return {'error': 'questions must be a non-empty dict (or JSON object string)', 'code': 'invalid_request'}
        try:
            req = {'state': _jsonish(state), 'questions': q, **self._options(kw)}
        except ValueError as e:
            return {'error': str(e), 'code': 'invalid_request'}
        return self._bridge('encode', req)

    def models(self, **kw) -> dict:
        """ListModels — every model the API offers, with release dates."""
        if jevrelay.backend() == 'relay':
            via = jevrelay.status()
            return {'models': [{'name': 'jev-latest', 'description': f'served by the Jev seller {via.get("via", "?")}'
                                                                     ' over x402 (this node has no TypeSafe key)'}],
                    'relay': via}
        try:
            return self._bridge('models', self._options(kw))
        except ValueError as e:
            return {'error': str(e), 'code': 'invalid_request'}

    def info(self) -> dict:
        """What the client would use: base URL, default model, retry defaults, whether a key is set,
        and which backend answers (direct jevgo, or relay through a Jev x402 seller)."""
        return {**self._bridge('info'), 'backend': jevrelay.backend()}

    def version(self) -> dict:
        cfg = _config()
        return {'module': cfg.get('version', '0.2.0'), **self._bridge('version')}

    # ── paying ───────────────────────────────────────────────────

    def price(self) -> dict:
        """What a question costs here, on which chains, paid to whom, and how credits work."""
        return jevpay.public()

    def pay(self) -> dict:
        """Alias of price()."""
        return jevpay.public()

    def pricing(self, **changes) -> dict:
        """Owner: set pay_to=0x…, price_usd, networks=base,polygon, facilitator, enabled,
        free_local, min_topup_usd, max_questions, timeout_s, backend=auto|direct|relay,
        upstreams=url,url, relay_timeout_s. No args = show the full policy."""
        if changes:
            return jevpay.set_policy(**changes)
        p = jevpay.policy()
        return {**p, 'file': jevpay.POLICY, 'public': jevpay.public(p)}

    def relay(self) -> dict:
        """Which backend answers and, when relaying, which Jev x402 seller and at what price."""
        return jevrelay.status()

    def wallet(self, create: bool = False) -> dict:
        """Owner: the node wallet that pays Jev sellers for local calls and credit spends
        when there is no TypeSafe key. create=1 makes one (fund it with USDC on Base)."""
        return jevrelay.wallet(str(create).lower() in ('1', 'true', 'yes', 'on'))

    def grant(self, address: str, usd: float = 0.0, note: str = 'grant') -> dict:
        """Owner: give an address prepaid credits (negative usd takes them back)."""
        if not str(address).startswith('0x') or len(str(address)) != 42:
            raise jevpay.PayError('address must be a 0x EVM address')
        return jevpay.credit(address, jevpay.units(usd) if float(usd) >= 0 else -jevpay.units(-float(usd)), 'grant', note)

    def balance(self, address: str) -> dict:
        return jevpay.balance(address)

    def ledger(self, address: str = None, limit: int = 100) -> dict:
        return {'ledger': jevpay.ledger(address, int(limit))}

    def payments(self, limit: int = 100) -> dict:
        return {'payments': jevpay.payments(int(limit)), **jevpay.stats()}

    def stats(self) -> dict:
        return jevpay.stats()

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

    def serve(self, port=None, host='0.0.0.0', background=False) -> dict:
        """Console + API + MCP on one port. Foreground by default (this is what
        `m.serve('jev')` / the pm2 entry runs); background=1 puts it under pm2."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port, host)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', HERE,
               '--', os.path.join(HERE, 'serve.py'), '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'app': f'http://localhost:{port}{self.base}/',
                'api': f'http://localhost:{port}{self.base}/api/noul', 'mcp': f'http://localhost:{port}{self.base}/mcp',
                **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def readme(self) -> str:
        try:
            with open(os.path.join(HERE, 'README.md')) as f:
                return f.read()
        except OSError:
            return ''

    def status(self) -> dict:
        built = os.path.exists(BIN)
        out = {'module': 'jev', 'wraps': 'github.com/fgn/jevgo', 'built': built,
               'key_present': bool(os.environ.get('TYPESAFE_API_KEY')),
               'pay': jevpay.public(), 'ledger': jevpay.stats()}
        if built:
            out.update({k: v for k, v in self._bridge('info').items() if k != 'key_present'})
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}{self.base}/api/health', timeout=2) as r:
                out['server'] = {'running': True, 'port': self.port, **json.loads(r.read())}
        except Exception as e:                            # noqa: BLE001
            out['server'] = {'running': False, 'port': self.port, 'hint': 'm jev/serve background=1', 'why': str(e)[:120]}
        return out

    def test(self) -> dict:
        """Offline checks: the bridge builds and validates, the pay gate charges
        correctly (facilitator mocked), the server routes. Runs tests/."""
        checks = {}
        built = self.build()
        checks['build'] = 'error' not in built
        if checks['build']:
            info = self._bridge('info')
            checks['info'] = info.get('client') == 'github.com/fgn/jevgo' and bool(info.get('base_url'))
            checks['rejects_bad_questions'] = self.ask('state', 'not-a-dict').get('code') == 'invalid_request'
            enc = self.encode('hello', {'q': {'kind': 'noul', 'instructions': 'greeting?'}})
            checks['encode'] = enc.get('valid') is True and enc.get('body', {}).get('questions', {}).get('q', {}).get('type') == 'noul'
        proc = subprocess.run([sys.executable, '-m', 'pytest', '-q', os.path.join(HERE, 'tests')],
                              capture_output=True, text=True, cwd=HERE, timeout=300)
        checks['pytest'] = proc.returncode == 0
        ok = all(checks.values())
        out = {'ok': ok, 'checks': checks, 'pytest': (proc.stdout or '').strip().splitlines()[-1:]}
        if not ok:
            out['detail'] = {'build': built, 'pytest': (proc.stdout + proc.stderr)[-2000:]}
        return out
