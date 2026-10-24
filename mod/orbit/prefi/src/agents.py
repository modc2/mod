"""Vibecoded agents — describe a trader in a sentence, get a strategy
that predicts and trades every minute in the local economy.

An agent is a stored record: a name, a derived address, the prompt it
was written from, and a strategy — a tiny Python program that defines
``decide(ctx)`` and returns a list of actions. The program runs in a
sandbox (AST-screened: no imports, no dunders, no I/O; a short builtin
whitelist; a wall-clock cap) once per economy round, with this context:

    ctx = {
      'round': int, 'time': int, 'name': str,
      'prices':  {'BTC': 110234.5, ...},          # now
      'history': {'BTC': [..., px], ...},         # last 60 ticks, oldest first
      'change':  {'BTC': 0.0012, ...},            # 1-minute return
      'cash': 812.0, 'holdings': {'BTC': 0.002},  # exchange ledger
      'balance': 950.0,                           # minute-pool PAPER
      'equity': 1034.2, 'memory': {...},          # memory persists per agent
    }

and returns actions such as

    [{'op': 'predict', 'asset': 'BTC', 'price': 110300.0, 'stake': 10},
     {'op': 'buy',  'asset': 'ETH', 'notional': 50},
     {'op': 'sell', 'asset': 'ETH', 'qty': 0.01},
     {'op': 'note', 'text': 'momentum up'}]

``vibecode(prompt)`` asks the local claudecode agent (:51260) to write
``decide`` for that prompt; when no LLM answers, a keyword-matched
template is used so the loop never blocks on a model. Either way the
code is compiled, dry-run on a mock context and refused if it fails.
"""

import ast
import hashlib
import json
import math
import os
import random
import re
import threading
import time as time_mod
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import identity
except ImportError:                                   # imported as a package
    from . import identity

MAX_CODE = 6000
MAX_ACTIONS = 20
RUN_TIMEOUT = 3.0
LOG_KEEP = 60
HISTORY_TICKS = 60
CLAUDECODE_URL = os.environ.get('PREFI_CLAUDECODE', 'http://127.0.0.1:51260')

SAFE_BUILTINS = {
    'abs': abs, 'min': min, 'max': max, 'sum': sum, 'len': len,
    'range': range, 'round': round, 'sorted': sorted, 'enumerate': enumerate,
    'zip': zip, 'float': float, 'int': int, 'str': str, 'bool': bool,
    'dict': dict, 'list': list, 'tuple': tuple, 'set': set, 'any': any,
    'all': all, 'reversed': reversed, 'isinstance': isinstance,
    'True': True, 'False': False, 'None': None,
}
BANNED_NAMES = {'exec', 'eval', 'open', 'compile', 'globals', 'locals',
                'getattr', 'setattr', 'delattr', 'vars', 'dir', 'input',
                'breakpoint', 'exit', 'quit', 'help', 'memoryview', 'type',
                'super', 'object', 'classmethod', 'staticmethod', 'property'}


class StrategyError(ValueError):
    pass


# ── Sandbox ──────────────────────────────────────────────────────────

class _Screen(ast.NodeVisitor):
    def visit_Import(self, node):
        raise StrategyError('imports are not allowed — math and random are '
                            'already in scope')

    visit_ImportFrom = visit_Import

    def visit_Attribute(self, node):
        if node.attr.startswith('_'):
            raise StrategyError(f'private attribute {node.attr!r}')
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith('__') or node.id in BANNED_NAMES:
            raise StrategyError(f'name {node.id!r} is not allowed')
        self.generic_visit(node)

    def visit_Global(self, node):
        raise StrategyError('global is not allowed')

    visit_Nonlocal = visit_Global

    def visit_ClassDef(self, node):
        raise StrategyError('classes are not allowed — just def decide(ctx)')

    def visit_AsyncFunctionDef(self, node):
        raise StrategyError('async is not allowed')

    visit_Await = visit_AsyncFunctionDef
    visit_Yield = visit_AsyncFunctionDef
    visit_YieldFrom = visit_AsyncFunctionDef


def compile_strategy(code: str):
    """Source → the `decide` callable, or StrategyError."""
    code = (code or '').replace('\r\n', '\n').strip() + '\n'
    if len(code) > MAX_CODE:
        raise StrategyError(f'strategy longer than {MAX_CODE} chars')
    try:
        tree = ast.parse(code, '<strategy>')
    except SyntaxError as err:
        raise StrategyError(f'syntax error line {err.lineno}: {err.msg}')
    _Screen().visit(tree)
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    if 'decide' not in names:
        raise StrategyError('define `def decide(ctx):` that returns a list '
                            'of actions')
    env = {'__builtins__': dict(SAFE_BUILTINS), 'math': math,
           'random': random}
    exec(compile(tree, '<strategy>', 'exec'), env)        # noqa: S102
    fn = env['decide']
    if not callable(fn):
        raise StrategyError('decide is not a function')
    return fn


def run_strategy(fn, ctx: Dict, timeout: float = RUN_TIMEOUT) -> List[Dict]:
    """Call `decide(ctx)` on a worker thread with a wall-clock cap, then
    normalise what it returned into action dicts."""
    out: Dict[str, Any] = {}

    def worker():
        try:
            out['value'] = fn(ctx)
        except Exception as err:                        # noqa: BLE001
            out['error'] = f'{type(err).__name__}: {err}'

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise StrategyError(f'decide() ran past {timeout}s')
    if 'error' in out:
        raise StrategyError(out['error'])
    return normalize_actions(out.get('value'))


def normalize_actions(value) -> List[Dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, tuple):
        value = [value]
    if not isinstance(value, list):
        raise StrategyError('decide() must return a list of actions')
    acts = []
    for item in value[:MAX_ACTIONS]:
        if isinstance(item, (list, tuple)) and item:
            keys = {'predict': ('asset', 'price', 'stake'),
                    'buy': ('asset', 'notional'), 'sell': ('asset', 'notional'),
                    'note': ('text',)}.get(str(item[0]).lower())
            if not keys:
                raise StrategyError(f'unknown action {item[0]!r}')
            item = dict(zip(('op',) + keys, item))
        if not isinstance(item, dict):
            raise StrategyError(f'action is not a dict: {item!r}')
        op = str(item.get('op') or item.get('action') or '').lower()
        if op == 'predict':
            acts.append({'op': 'predict', 'asset': str(item['asset']).upper(),
                         'price': float(item['price']),
                         'stake': float(item.get('stake', 1))})
        elif op in ('buy', 'sell'):
            row = {'op': op, 'asset': str(item['asset']).upper()}
            if item.get('qty') is not None:
                row['qty'] = float(item['qty'])
            else:
                row['notional'] = float(item.get('notional', 0))
            acts.append(row)
        elif op == 'note':
            acts.append({'op': 'note', 'text': str(item.get('text', ''))[:200]})
        elif op in ('hold', 'pass', ''):
            continue
        else:
            raise StrategyError(f'unknown action {op!r}')
    return acts


# ── Templates — what a prompt becomes when no model answers ──────────

TEMPLATES: Dict[str, Dict[str, str]] = {
    'momentum': {
        'match': 'momentum|trend|breakout|follow|chase|ride',
        'code': '''# Momentum: call the minute continuing the last minute's move,
# buy what is rising, sell what is falling.
def decide(ctx):
    acts = []
    for asset, px in ctx['prices'].items():
        h = ctx['history'].get(asset) or []
        if len(h) < 12:
            continue
        ret = px / h[-12] - 1          # last ~minute of ticks
        call = px * (1 + ret * 0.5)    # expect half of it to persist
        acts.append({'op': 'predict', 'asset': asset, 'price': call, 'stake': 5})
        held = ctx['holdings'].get(asset, 0) * px
        if ret > 0.001 and ctx['cash'] > 60 and held < 200:
            acts.append({'op': 'buy', 'asset': asset, 'notional': 50})
        elif ret < -0.001 and held > 10:
            acts.append({'op': 'sell', 'asset': asset, 'notional': min(held, 50)})
    return acts
'''},
    'meanrevert': {
        'match': 'mean|revert|reversion|fade|contrarian|counter|dip|oversold',
        'code': '''# Mean reversion: a move is more likely to come back than carry on.
def decide(ctx):
    acts = []
    for asset, px in ctx['prices'].items():
        h = ctx['history'].get(asset) or []
        if len(h) < 24:
            continue
        mean = sum(h[-24:]) / 24
        dev = px / mean - 1
        call = px - (px - mean) * 0.5
        acts.append({'op': 'predict', 'asset': asset, 'price': call, 'stake': 5})
        held = ctx['holdings'].get(asset, 0) * px
        if dev < -0.002 and ctx['cash'] > 60:
            acts.append({'op': 'buy', 'asset': asset, 'notional': 50})
        elif dev > 0.002 and held > 10:
            acts.append({'op': 'sell', 'asset': asset, 'notional': min(held, 50)})
    return acts
'''},
    'hodl': {
        'match': 'hodl|hold|buy and hold|long only|accumulate|dca|dollar cost',
        'code': '''# Accumulate: buy a little of everything each minute, call flat.
def decide(ctx):
    acts = []
    n = max(1, len(ctx['prices']))
    for asset, px in ctx['prices'].items():
        acts.append({'op': 'predict', 'asset': asset, 'price': px, 'stake': 2})
        if ctx['cash'] > 20 * n:
            acts.append({'op': 'buy', 'asset': asset, 'notional': 10})
    return acts
'''},
    'random': {
        'match': 'random|coin|dice|noise|monkey|chaos|gamble',
        'code': '''# Noise trader: random calls and random flow — the baseline to beat.
def decide(ctx):
    acts = []
    for asset, px in ctx['prices'].items():
        call = px * (1 + random.gauss(0, 0.002))
        acts.append({'op': 'predict', 'asset': asset, 'price': call, 'stake': 3})
        held = ctx['holdings'].get(asset, 0) * px
        if random.random() < 0.3 and ctx['cash'] > 40:
            acts.append({'op': 'buy', 'asset': asset, 'notional': 20})
        elif random.random() < 0.3 and held > 10:
            acts.append({'op': 'sell', 'asset': asset, 'notional': min(held, 20)})
    return acts
'''},
    'flat': {
        'match': 'flat|unchanged|same|stay|conservative|careful|safe|steady',
        'code': '''# Flat caller: the close is the current price. Hard to beat on a
# one-minute horizon — the efficient-market baseline.
def decide(ctx):
    return [{'op': 'predict', 'asset': a, 'price': px, 'stake': 5}
            for a, px in ctx['prices'].items()]
'''},
    'whale': {
        'match': 'whale|big|aggressive|all in|yolo|degen|max|large',
        'code': '''# Whale: concentrate on the single most volatile asset and lean hard.
def decide(ctx):
    best, bestv = None, -1
    for asset, h in ctx['history'].items():
        if len(h) < 12:
            continue
        rets = [abs(h[i] / h[i - 1] - 1) for i in range(1, len(h))]
        v = sum(rets) / len(rets)
        if v > bestv:
            best, bestv = asset, v
    if best is None:
        return []
    px = ctx['prices'][best]
    h = ctx['history'][best]
    ret = px / h[-12] - 1
    acts = [{'op': 'predict', 'asset': best, 'price': px * (1 + ret), 'stake': 25}]
    held = ctx['holdings'].get(best, 0) * px
    if ret > 0 and ctx['cash'] > 100:
        acts.append({'op': 'buy', 'asset': best, 'notional': ctx['cash'] * 0.5})
    elif ret < 0 and held > 10:
        acts.append({'op': 'sell', 'asset': best, 'notional': held})
    return acts
'''},
}
DEFAULT_TEMPLATE = 'momentum'


def template_for(prompt: str) -> str:
    text = (prompt or '').lower()
    for name, tpl in TEMPLATES.items():
        if re.search(tpl['match'], text):
            return name
    return DEFAULT_TEMPLATE


def mock_ctx(prices: Dict[str, float] = None) -> Dict:
    prices = prices or {'BTC': 110_000.0, 'ETH': 4_000.0, 'SOL': 200.0}
    rng = random.Random(7)
    history = {}
    for sym, px in prices.items():
        series, p = [], px * 0.99
        for _ in range(HISTORY_TICKS):
            p *= 1 + rng.gauss(0, 0.0005)
            series.append(p)
        series[-1] = px
        history[sym] = series
    return {'round': 1, 'time': int(time_mod.time()), 'name': 'dry-run',
            'prices': dict(prices), 'history': history,
            'change': {s: 0.0 for s in prices}, 'cash': 1000.0,
            'holdings': {}, 'balance': 1000.0, 'equity': 1000.0,
            'memory': {}}


def check_strategy(code: str) -> Dict:
    """Compile + dry-run; the gate every save goes through."""
    fn = compile_strategy(code)
    acts = run_strategy(fn, mock_ctx())
    return {'ok': True, 'actions': acts}


# ── Vibecode — a prompt becomes a program ────────────────────────────

LANGUAGE = '''Write ONE Python function `decide(ctx)` for a trading agent that runs
once per minute in a simulated crypto economy. Return ONLY a single
```python fenced block, nothing else — no prose, no imports, no classes.

Available in scope: `math`, `random`, and these builtins only:
abs min max sum len range round sorted enumerate zip float int str bool
dict list tuple set any all reversed isinstance. No imports, no I/O, no
attributes starting with `_`. Must finish in under 1 second.

ctx (dict):
  ctx['prices']   {asset: price_now}
  ctx['history']  {asset: [price, ...]}  last 60 ticks (5s apart), oldest first
  ctx['change']   {asset: one-minute return as a fraction}
  ctx['cash']     exchange cash in PAPER (fake money)
  ctx['holdings'] {asset: qty held}
  ctx['balance']  prediction-pool PAPER available to stake
  ctx['equity']   cash + holdings value
  ctx['memory']   a dict that persists between runs — store state here
  ctx['round'], ctx['time'], ctx['name']

Return a list of action dicts (at most 20):
  {'op': 'predict', 'asset': 'BTC', 'price': <called close>, 'stake': <PAPER>}
      — call where the asset closes at the end of THIS minute; the pool
        scores accuracy linearly, a 1% miss scores zero. One per asset.
  {'op': 'buy',  'asset': 'ETH', 'notional': <PAPER>}   market buy
  {'op': 'sell', 'asset': 'ETH', 'notional': <PAPER>}   or 'qty': <units>
  {'op': 'note', 'text': '...'}                         a log line

Every buy pays a 0.1% spread and moves the price by notional/liquidity.
Keep stakes small relative to balance; never stake or spend more than
you have. Empty list = do nothing this minute.
'''


def extract_code(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'```(?:python|py)?\s*\n(.*?)```', text, re.S)
    if m:
        return m.group(1).strip()
    if 'def decide' in text:
        return text[text.index('def decide'):].strip()
    return None


def _claudecode_write(prompt: str, model: str = None,
                      timeout: int = 240) -> Dict:
    """Ask the local claudecode agent-contract server to write the
    strategy. Returns {'code'} or {'error'}."""
    import urllib.request
    import urllib.error
    query = (LANGUAGE + '\nThe agent to write, in the author\'s words:\n\n'
             + prompt.strip() + '\n\nReturn only the fenced python block.')
    body = {'query': query, 'timeout': timeout,
            'goal': 'You write small, correct, self-contained Python trading '
                    'strategies. Output only a fenced python code block.'}
    if model:
        body['model'] = model
    req = urllib.request.Request(
        CLAUDECODE_URL.rstrip('/') + '/run',
        data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as err:
        return {'error': f'claudecode unreachable: {err}'}
    if data.get('error'):
        return {'error': str(data['error'])}
    steps = data.get('result') or []
    text = ''
    for step in reversed(steps):
        if step.get('tool') == 'finish':
            text = (step.get('params') or {}).get('summary') or ''
            if text:
                break
        if step.get('tool') == 'response' and step.get('result'):
            text = step['result']
            break
    code = extract_code(text)
    if not code:
        return {'error': 'the model returned no python block',
                'raw': text[:2000], 'task_id': data.get('task_id')}
    return {'code': code, 'task_id': data.get('task_id'), 'raw': text[:2000]}


def vibecode(prompt: str, model: str = None, llm: bool = True,
             timeout: int = 240) -> Dict:
    """Prompt → {'code', 'source': 'claudecode'|'template', ...}. The code
    is already validated; a model answer that fails the dry-run falls
    back to the template rather than returning broken code."""
    prompt = (prompt or '').strip()
    if not prompt:
        raise StrategyError('prompt required')
    notes = []
    if llm and os.environ.get('PREFI_VIBECODE_LLM', '1') not in ('0', 'no'):
        got = _claudecode_write(prompt, model=model, timeout=timeout)
        if 'code' in got:
            try:
                check = check_strategy(got['code'])
                return {'code': got['code'], 'source': 'claudecode',
                        'task_id': got.get('task_id'),
                        'dry_run': check['actions']}
            except StrategyError as err:
                notes.append(f'model code rejected: {err}')
        else:
            notes.append(got['error'])
    name = template_for(prompt)
    code = TEMPLATES[name]['code']
    check = check_strategy(code)
    return {'code': code, 'source': 'template', 'template': name,
            'notes': notes, 'dry_run': check['actions']}


# ── Registry + runner ────────────────────────────────────────────────

def address_for(agent_id: str) -> str:
    digest = hashlib.sha256(f'prefi-agent:{agent_id}'.encode()).hexdigest()
    return identity.normalize('0x' + digest[:40])


class Agents:

    def __init__(self, store_dir, economy):
        self.dir = Path(store_dir) / 'economy'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'agents.json'
        self.lock_path = self.dir / 'agents.lock'
        self.economy = economy
        self._rlock = threading.RLock()
        self._lock_depth = 0
        self._agents: Optional[Dict[str, Dict]] = None
        self._compiled: Dict[str, Any] = {}
        self._last_round: Optional[int] = None

    @contextmanager
    def _lock(self):
        # Re-entrant: the RLock serialises threads, the depth counter keeps
        # a nested call from opening a second fd — flock would block on it.
        with self._rlock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            self._lock_depth = 1
            try:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except (ImportError, OSError):        # pragma: no cover
                    pass
                yield
            finally:
                self._lock_depth = 0
                os.close(fd)

    def _all(self) -> Dict[str, Dict]:
        if self._agents is None:
            with self._lock():
                if self.path.exists():
                    try:
                        self._agents = json.loads(self.path.read_text())
                    except (json.JSONDecodeError, OSError):
                        self._agents = {}
                else:
                    self._agents = {}
        return self._agents

    def _save(self):
        tmp = self.path.with_suffix('.json.tmp')
        with open(tmp, 'w') as fh:
            json.dump(self._agents, fh)
        os.replace(tmp, self.path)

    # ── CRUD ─────────────────────────────────────────────────────────

    def create(self, name: str, code: str = None, prompt: str = None,
               owner: str = None, model: str = None, start: bool = True,
               llm: bool = True) -> Dict:
        name = (name or '').strip()[:48]
        if not name:
            raise StrategyError('name required')
        gen = None
        if not code:
            if not prompt:
                raise StrategyError('give code= or prompt= (vibecode)')
            gen = vibecode(prompt, model=model, llm=llm)
            code = gen['code']
        check = check_strategy(code)
        agent_id = uuid.uuid4().hex[:10]
        now = int(time_mod.time())
        rec = {'id': agent_id, 'name': name, 'address': address_for(agent_id),
               'prompt': prompt or '', 'code': code,
               'source': (gen or {}).get('source', 'user'),
               'template': (gen or {}).get('template'),
               'task_id': (gen or {}).get('task_id'),
               'model': model or '', 'owner': owner or '',
               'status': 'running' if start else 'paused',
               'created': now, 'updated': now, 'runs': 0, 'errors': 0,
               'actions': 0, 'last_run': None, 'last_error': None,
               'memory': {}, 'log': [],
               'notes': (gen or {}).get('notes') or []}
        with self._lock():
            self._all()[agent_id] = rec
            self._save()
        # register + fund on arrival — an agent is born with play money
        try:
            self.economy.paper.register(rec['address'], name=name, kind='agent')
            self.economy.faucet(rec['address'])
        except Exception as err:                      # pragma: no cover
            rec['last_error'] = f'funding: {err}'
        out = self.view(rec)
        out['dry_run'] = check['actions']
        return out

    def update(self, agent_id: str, name: str = None, code: str = None,
               prompt: str = None, status: str = None,
               revibe: bool = False, model: str = None,
               llm: bool = True) -> Dict:
        rec = self._get(agent_id)
        if revibe:
            gen = vibecode(prompt or rec['prompt'], model=model or rec['model']
                           or None, llm=llm)
            code = gen['code']
            rec['source'] = gen['source']
            rec['template'] = gen.get('template')
            rec['task_id'] = gen.get('task_id')
            rec['notes'] = gen.get('notes') or []
        if code is not None:
            check_strategy(code)
            rec['code'] = code
            self._compiled.pop(agent_id, None)
            if not revibe:
                rec['source'] = 'user'
        if name:
            rec['name'] = name.strip()[:48]
        if prompt is not None:
            rec['prompt'] = prompt
        if status:
            if status not in ('running', 'paused'):
                raise StrategyError("status must be 'running' or 'paused'")
            rec['status'] = status
        rec['updated'] = int(time_mod.time())
        with self._lock():
            self._save()
        return self.view(rec)

    def delete(self, agent_id: str) -> Dict:
        rec = self._get(agent_id)                 # id, name or address
        with self._lock():
            self._all().pop(rec['id'], None)
            self._compiled.pop(rec['id'], None)
            self._save()
        return {'deleted': rec['id'], 'name': rec['name']}

    def _get(self, agent_id: str) -> Dict:
        rec = self._all().get(agent_id)
        if rec is None:
            # allow lookup by name or address
            for r in self._all().values():
                if r['name'] == agent_id or r['address'] == str(agent_id).lower():
                    return r
            raise StrategyError(f'no agent {agent_id!r}')
        return rec

    def view(self, rec: Dict, full: bool = True) -> Dict:
        out = {k: v for k, v in rec.items() if k not in ('log', 'memory')}
        if full:
            out['log'] = rec['log'][-LOG_KEEP:]
            out['memory'] = rec['memory']
            try:
                out['account'] = self.economy.account(rec['address'])
            except Exception as err:                  # pragma: no cover
                out['account'] = {'error': str(err)}
        else:
            out.pop('code', None)
        return out

    def list(self) -> List[Dict]:
        rows = []
        names = self.names()
        board = {r['address']: r for r in
                 self.economy.leaderboard(10_000, names=names)}
        for rec in self._all().values():
            row = self.view(rec, full=False)
            row['board'] = board.get(rec['address'])
            rows.append(row)
        rows.sort(key=lambda r: -(r['board'] or {}).get('net_worth', 0))
        return rows

    def get(self, agent_id: str) -> Dict:
        return self.view(self._get(agent_id))

    def names(self) -> Dict[str, str]:
        return {r['address']: r['name'] for r in self._all().values()}

    # ── Running ──────────────────────────────────────────────────────

    def context(self, rec: Dict, now: float = None) -> Dict:
        eco = self.economy
        now = int(now or time_mod.time())
        prices = {s: p['price'] for s, p in eco.prices().items()}
        history = {s: [px for _, px in eco.history(s, HISTORY_TICKS)]
                   for s in prices}
        change = {s: p.get('change_1m') or 0.0 for s, p in eco.prices().items()}
        acct = eco.account(rec['address'])
        return {'round': eco.paper.current_index(now), 'time': now,
                'name': rec['name'], 'prices': prices, 'history': history,
                'change': change, 'cash': acct['cash'],
                'holdings': {s: h['qty'] for s, h in acct['holdings'].items()},
                'balance': acct['pool'].get('balance', 0.0),
                'equity': acct['equity'], 'memory': rec.get('memory') or {}}

    def _fn(self, rec: Dict):
        fn = self._compiled.get(rec['id'])
        if fn is None:
            fn = compile_strategy(rec['code'])
            self._compiled[rec['id']] = fn
        return fn

    def apply(self, rec: Dict, actions: List[Dict],
              now: float = None) -> List[Dict]:
        eco = self.economy
        results = []
        for act in actions:
            op = act['op']
            try:
                if op == 'predict':
                    r = eco.predict(rec['address'], act['asset'], act['price'],
                                    act['stake'], now=now)
                    if 'error' in r:
                        results.append(dict(act, error=r['error']))
                    else:
                        results.append(dict(act, round=r['round'],
                                            balance=r['balance']))
                elif op in ('buy', 'sell'):
                    r = eco.trade(rec['address'], act['asset'], op,
                                  notional=act.get('notional'),
                                  qty=act.get('qty'))
                    results.append(dict(act, price=r['price'], qty=r['qty'],
                                        notional=r['notional'], cash=r['cash']))
                else:
                    results.append(dict(act))
            except Exception as err:                  # noqa: BLE001
                results.append(dict(act, error=str(err)))
        return results

    def run_one(self, agent_id: str, now: float = None, force: bool = True) \
            -> Dict:
        rec = self._get(agent_id)
        now = int(now or time_mod.time())
        ctx = self.context(rec, now)
        entry = {'time': now, 'round': ctx['round'], 'actions': [],
                 'error': None}
        try:
            fn = self._fn(rec)
            actions = run_strategy(fn, ctx)
            # top up when the faucet allows — an agent never starves
            if ctx['cash'] < 1 or ctx['balance'] < 1:
                try:
                    self.economy.faucet(rec['address'])
                except Exception:
                    pass
            entry['actions'] = self.apply(rec, actions, now=now)
            rec['memory'] = ctx['memory'] if isinstance(ctx['memory'], dict) \
                else {}
            rec['actions'] += len(entry['actions'])
        except StrategyError as err:
            entry['error'] = str(err)
            rec['errors'] += 1
            rec['last_error'] = str(err)
        rec['runs'] += 1
        rec['last_run'] = now
        rec['log'].append(entry)
        if len(rec['log']) > LOG_KEEP:
            del rec['log'][:len(rec['log']) - LOG_KEEP]
        with self._lock():
            self._save()
        return entry

    def run_all(self, now: float = None) -> Dict:
        now = int(now or time_mod.time())
        ran, errors = [], 0
        for rec in list(self._all().values()):
            if rec['status'] != 'running':
                continue
            entry = self.run_one(rec['id'], now)
            ran.append({'id': rec['id'], 'name': rec['name'],
                        'actions': len(entry['actions']),
                        'error': entry['error']})
            errors += 1 if entry['error'] else 0
        self._last_round = self.economy.paper.current_index(now)
        return {'time': now, 'round': self._last_round, 'ran': ran,
                'errors': errors}

    def due(self, now: float = None) -> bool:
        """A new round has begun since the last run_all."""
        idx = self.economy.paper.current_index(now)
        return self._last_round != idx


class Loop:
    """The clock: tick the feed every `tick` seconds, run every running
    agent once per round. One thread, daemon, never raises."""

    def __init__(self, economy, agents: Agents):
        self.economy = economy
        self.agents = agents
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()
        self.last: Dict[str, Any] = {'tick': None, 'run': None, 'error': None}

    def start(self):
        if self.thread and self.thread.is_alive():
            return self
        self.stop.clear()
        self.thread = threading.Thread(target=self._loop, name='prefi-economy',
                                       daemon=True)
        self.thread.start()
        return self

    def step(self, now: float = None) -> Dict:
        now = now or time_mod.time()
        out = {'tick': None, 'run': None}
        out['tick'] = self.economy.tick(now)
        if self.agents.due(now):
            out['run'] = self.agents.run_all(now)
        return out

    def _loop(self):
        while not self.stop.is_set():
            try:
                res = self.step()
                self.last['tick'] = res['tick'] and res['tick']['time']
                if res['run']:
                    self.last['run'] = res['run']
                self.last['error'] = None
            except Exception as err:                  # noqa: BLE001
                self.last['error'] = str(err)
            tick = 5
            try:
                tick = int(self.economy.config()['tick'])
            except Exception:
                pass
            # wake just after each round boundary too, so agents fire
            # within a second of the minute starting
            now = time_mod.time()
            interval = 60
            try:
                interval = int(self.economy.config()['interval'])
            except Exception:
                pass
            to_boundary = interval - (now % interval) + 0.5
            self.stop.wait(max(0.5, min(tick, to_boundary)))

    def status(self) -> Dict:
        return {'alive': bool(self.thread and self.thread.is_alive()),
                **self.last}
