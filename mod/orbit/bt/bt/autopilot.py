"""
bt.autopilot — the trade desk running itself.

One cycle: bt builds a briefing from its own local index (market, wallet,
positions — instant, no chain round-trip), hands it to the **bt agent** running
in the `agent` module, and the agent answers with a trade plan as JSON. That
plan then goes through guardrails bt enforces itself — per-trade cap, daily
cap, TAO reserve, position count, liquidity floor, tradable universe — and what
survives is either placed (only when auto_execute is explicitly on) or parked
as a proposal you approve with one click.

The model never signs anything. It returns JSON; bt places the orders.

The agent's persona lives in ONE file, `agents/bt.agent.md`, which is also what
`install_agent()` uploads into the agent module's library — so the autopilot and
the agent you run by hand in the agent console are the same agent.

State: ~/.mod/bt/autopilot.json   (config + the last CYCLE_KEEP runs)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import history, tools

AGENT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'agents', 'bt.agent.md')
AGENT_NAME = 'bt'

CYCLE_KEEP = 40           # runs kept on disk
TICK_SEC = 30             # how often the loop wakes to check if a cycle is due

DEFAULTS: Dict[str, Any] = {
    'enabled': False,          # the loop only runs when you turn it on
    'auto_execute': False,     # OFF = propose only; nothing is signed
    'interval_min': 60,
    'wallet': 'default',
    'hotkey': 'default',
    'budget_tao': 1.0,         # total the desk may ever deploy
    'max_trade_tao': 0.25,     # per trade
    'max_daily_tao': 1.0,      # per rolling 24h
    'reserve_tao': 0.05,       # free TAO never spent (extrinsic fees)
    'max_positions': 5,
    'universe_top': 25,        # candidates: top N subnets by market cap
    'min_liquidity_tao': 500,  # skip pools too thin to exit
    'model': None,             # None = whatever the agent module picks
    'free': True,              # free models — no OpenRouter credits needed
    'steps': 4,
    'note': '',                # your own standing instruction to the agent
}

_lock = threading.RLock()
_thread: Optional[threading.Thread] = None
_running = False              # a cycle is in flight (cycles are not reentrant)


# ------------------------------------------------------------------ state

def _state_path() -> str:
    return os.path.join(history.data_dir(), 'autopilot.json')


def _load() -> Dict[str, Any]:
    try:
        with open(_state_path()) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state['config'] = {**DEFAULTS, **(state.get('config') or {})}
    state.setdefault('runs', [])
    return state


def _save(state: Dict[str, Any]) -> None:
    state['runs'] = state.get('runs', [])[-CYCLE_KEEP:]
    path = _state_path()
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, default=str)
    os.replace(tmp, path)


def config(**updates) -> Dict[str, Any]:
    """Read the desk config, or update the keys you pass."""
    with _lock:
        state = _load()
        for k, v in updates.items():
            if k not in DEFAULTS or v is None:
                continue
            want = DEFAULTS[k]
            try:
                if isinstance(want, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'yes', 'on')
                elif isinstance(want, int) and not isinstance(want, bool):
                    v = int(v)
                elif isinstance(want, float):
                    v = float(v)
                elif want is None or isinstance(want, str):
                    v = str(v)
            except (TypeError, ValueError):
                continue
            state['config'][k] = v
        if updates:
            _save(state)
        return dict(state['config'])


def runs(limit: int = 20) -> List[Dict]:
    with _lock:
        return list(reversed(_load()['runs'][-max(1, int(limit)):]))


def _record(run: Dict) -> Dict:
    with _lock:
        state = _load()
        state['runs'].append(run)
        _save(state)
    return run


def _update_run(run_id: str, mutate) -> Optional[Dict]:
    with _lock:
        state = _load()
        for run in state['runs']:
            if run.get('id') == run_id:
                mutate(run)
                _save(state)
                return run
    return None


# -------------------------------------------------------------- briefing

def _wallet_ctx(cfg: Dict) -> Dict:
    """Free TAO + open positions for the desk wallet, or why we can't trade."""
    ctx: Dict[str, Any] = {'wallet': cfg['wallet'], 'free_tao': 0.0,
                           'positions': [], 'error': None}
    wallets = {w['name']: w for w in tools.call_tool('bt_wallets', {})}
    w = wallets.get(cfg['wallet'])
    if not w:
        ctx['error'] = (f"no local wallet named '{cfg['wallet']}' — create one "
                        f"in the Trade tab before the desk can run")
        return ctx
    ctx['coldkey'] = w.get('coldkey')
    try:
        ctx['free_tao'] = float(tools.call_tool(
            'bt_balance', {'address': w['coldkey']})['tao'])
    except Exception as e:
        ctx['error'] = f'balance lookup failed: {type(e).__name__}: {e}'
    try:
        pos = tools.call_tool('bt_portfolio', {'wallet': cfg['wallet'],
                                               'hotkey': cfg['hotkey']}) or []
        ctx['positions'] = [{
            'netuid': p.get('netuid'),
            'alpha': p.get('alpha', p.get('stake')),
            'value_tao': p.get('tao_value', p.get('value')),
        } for p in pos if p.get('netuid') is not None]
    except Exception as e:
        ctx['error'] = ctx['error'] or f'portfolio failed: {type(e).__name__}: {e}'
    return ctx


def briefing(cfg: Optional[Dict] = None) -> Dict:
    """Everything the agent gets to see, straight from the local index."""
    cfg = cfg or config()
    scr = tools.call_tool('bt_screener', {'sort_by': 'market_cap',
                                          'limit': int(cfg['universe_top']),
                                          'sparks': False})
    rows = [r for r in (scr.get('rows') or []) if r.get('netuid') != 0]
    market = [{
        'netuid': r['netuid'], 'name': r.get('name'), 'price': r.get('price'),
        'change_1h': r.get('change_1h'), 'change_24h': r.get('change_24h'),
        'change_7d': r.get('change_7d'), 'vol_24h': r.get('vol_24h'),
        'liquidity_tao': r.get('tao_in'),
    } for r in rows]
    return {'market': market, 'wallet': _wallet_ctx(cfg),
            'updated_at': scr.get('updated_at'), 'config': cfg}


def _brief_text(brief: Dict) -> str:
    cfg, w = brief['config'], brief['wallet']
    lines = ['BRIEFING — live Bittensor alpha markets (local index).', '',
             'MARKET  netuid · name · price τ · 1h · 24h · 7d · vol24h τ · liquidity τ']
    for r in brief['market']:
        def pct(v):
            return f'{v:+.1f}%' if isinstance(v, (int, float)) else '—'
        lines.append(
            f"#{r['netuid']:<4} {str(r['name'] or '')[:18]:<18} "
            f"{r['price']:.6f}  {pct(r['change_1h'])}  {pct(r['change_24h'])}  "
            f"{pct(r['change_7d'])}  {(r['vol_24h'] or 0):,.0f}  "
            f"{(r['liquidity_tao'] or 0):,.0f}")
    pos = w.get('positions') or []
    lines += ['', f"WALLET  '{w['wallet']}' free τ {w['free_tao']:.4f}"]
    lines.append('POSITIONS  ' + (', '.join(
        f"#{p['netuid']} worth τ {float(p.get('value_tao') or 0):.4f}"
        for p in pos) if pos else 'none'))
    spent = _spent_24h()
    lines += ['', 'DESK RULES (bt enforces these on top of your answer — '
                  'anything over the cap is cut, not rejected):',
              f"  max τ {cfg['max_trade_tao']} per trade, "
              f"τ {cfg['max_daily_tao']} per 24h (τ {spent:.4f} already used)",
              f"  keep τ {cfg['reserve_tao']} free for fees, "
              f"at most {cfg['max_positions']} open positions",
              f"  only subnets in the list above, liquidity ≥ τ {cfg['min_liquidity_tao']}",
              f"  execution: {'LIVE — trades are signed' if cfg['auto_execute'] else 'DRY RUN — trades are proposals a human approves'}"]
    if (cfg.get('note') or '').strip():
        lines += ['', 'STANDING INSTRUCTION FROM THE DESK OWNER:', '  ' + cfg['note'].strip()]
    lines += ['', 'Decide. Call finish with summary = ONE JSON object:',
              '{"thesis": "...", "trades": [{"action":"buy|sell|sell_all",'
              '"netuid":N,"amount_tao":X,"why":"..."}]}',
              'An empty trades list is a real answer.']
    return '\n'.join(lines)


# ----------------------------------------------------------------- agent

def _agent_mod():
    """The agent module, or None if the fleet isn't importable here."""
    try:
        import mod as m
        return m.mod('agent')()
    except Exception:
        return None


def agent_source() -> Tuple[Dict[str, str], str]:
    """(front matter, system prompt) parsed out of agents/bt.agent.md."""
    text = open(AGENT_FILE).read()
    match = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    if not match:
        return {}, text
    front = {}
    for line in match.group(1).splitlines():
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            front[k.strip()] = v.strip().strip('\'"')
    return front, match.group(2).strip()


def install_agent() -> Dict:
    """Upload agents/bt.agent.md into the agent module's library."""
    mod = _agent_mod()
    if mod is None:
        raise RuntimeError('agent module not reachable from this process')
    text = open(AGENT_FILE).read()
    out = mod.forward(action='upload', text=text, filename='bt.agent.md')
    return {'installed': out.get('name'), 'kind': out.get('kind')}


def agent_status() -> Dict:
    """Is the brain wired up — module reachable, agent installed, model ready?"""
    mod = _agent_mod()
    if mod is None:
        return {'ready': False, 'installed': False,
                'hint': 'agent module not importable from the bt server '
                        '(needs the mod framework on PYTHONPATH)'}
    try:
        installed = AGENT_NAME in mod.agents.ls()
    except Exception:
        installed = False
    model = None
    try:
        model = mod.free_model() if config()['free'] else mod.model
    except Exception:
        pass
    return {'ready': True, 'installed': installed, 'agent': AGENT_NAME,
            'model': model,
            'hint': None if installed else
            'the bt agent is not in the agent module yet — install it to run '
            'the same persona there by hand'}


def _extract_json(text: str) -> Optional[Dict]:
    """First JSON object in a model answer, fences and prose tolerated."""
    if not text:
        return None
    text = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M)
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}' and depth:
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(text[start:i + 1])
                except ValueError:
                    continue
                if isinstance(out, dict):
                    return out
    return None


def _ask(brief: Dict, cfg: Dict) -> Dict:
    """Run the bt agent on the briefing. Returns {plan, model, steps, error}."""
    mod = _agent_mod()
    if mod is None:
        return {'error': 'agent module not reachable from this process'}
    kwargs: Dict[str, Any] = {
        'action': 'run', 'query': _brief_text(brief),
        'free': bool(cfg['free']), 'steps': int(cfg['steps']),
        # the fleet is host-only, and this run is not signed in as the host —
        # which is exactly right: the data is already in the briefing and the
        # agent has nothing to reach for. bt does every chain call itself.
        'tools': ['think'],
    }
    if cfg.get('model'):
        kwargs['model'] = cfg['model']
    try:
        if AGENT_NAME in mod.agents.ls():
            kwargs['agent'] = AGENT_NAME
        else:
            kwargs['prompt'] = agent_source()[1]
    except Exception:
        kwargs['prompt'] = agent_source()[1]
    try:
        steps = mod.forward(**kwargs) or []
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}
    answer = ''
    for step in steps:
        params = step.get('params') or {}
        answer = params.get('summary') or params.get('text') or answer
        if step.get('tool') == 'error':
            return {'error': str(step.get('error'))[:400]}
    plan = _extract_json(answer)
    if plan is None:
        return {'error': 'agent did not answer with a JSON plan',
                'answer': answer[:400]}
    return {'plan': plan, 'answer': answer}


# ------------------------------------------------------------ guardrails

def _spent_24h() -> float:
    """TAO actually placed by the desk in the last rolling 24h."""
    cut = time.time() - 86400
    total = 0.0
    for run in _load()['runs']:
        if run.get('t', 0) < cut:
            continue
        for t in run.get('trades', []):
            if t.get('status') == 'executed' and t.get('action') == 'buy':
                total += float(t.get('amount_tao') or 0)
    return total


def _deployed() -> float:
    """Net TAO the desk has put on the table (buys minus sells)."""
    total = 0.0
    for run in _load()['runs']:
        for t in run.get('trades', []):
            if t.get('status') != 'executed':
                continue
            amt = float(t.get('amount_tao') or 0)
            total += amt if t.get('action') == 'buy' else -amt
    return max(0.0, total)


def guard(plan: Dict, brief: Dict, cfg: Dict) -> List[Dict]:
    """Turn the agent's trades into desk orders: clamped, checked, or rejected.

    Every trade comes back — a rejected one keeps its reason so the run log
    shows what the model wanted and why the desk said no.
    """
    market = {r['netuid']: r for r in brief['market']}
    held = {p['netuid']: p for p in brief['wallet'].get('positions') or []}
    free = float(brief['wallet'].get('free_tao') or 0)
    budget_left = max(0.0, float(cfg['budget_tao']) - _deployed())
    daily_left = max(0.0, float(cfg['max_daily_tao']) - _spent_24h())
    spendable = max(0.0, free - float(cfg['reserve_tao']))
    open_slots = int(cfg['max_positions']) - len(held)

    out: List[Dict] = []
    for raw in (plan.get('trades') or [])[:8]:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get('action') or '').lower().strip()
        trade = {'action': action, 'netuid': raw.get('netuid'),
                 'amount_tao': raw.get('amount_tao'),
                 'why': str(raw.get('why') or '')[:280],
                 'status': 'proposed', 'note': None}
        out.append(trade)

        def reject(why: str):
            trade['status'] = 'rejected'
            trade['note'] = why

        if action not in ('buy', 'sell', 'sell_all'):
            reject(f'unknown action {action!r}')
            continue
        try:
            netuid = int(trade['netuid'])
        except (TypeError, ValueError):
            reject('no netuid')
            continue
        trade['netuid'] = netuid
        row = market.get(netuid)
        trade['name'] = (row or {}).get('name') or (f'subnet {netuid}')

        if action == 'sell_all':
            if netuid not in held:
                reject('nothing held in that subnet')
            else:
                trade['amount_tao'] = held[netuid].get('value_tao')
            continue

        try:
            amount = float(trade['amount_tao'])
        except (TypeError, ValueError):
            reject('no amount')
            continue
        if amount <= 0:
            reject('amount must be positive')
            continue

        capped = min(amount, float(cfg['max_trade_tao']))
        if action == 'buy':
            if row is None:
                reject('outside the tradable universe')
                continue
            if (row.get('liquidity_tao') or 0) < float(cfg['min_liquidity_tao']):
                reject(f"liquidity τ {row.get('liquidity_tao') or 0:,.0f} below "
                       f"the τ {cfg['min_liquidity_tao']} floor")
                continue
            if netuid not in held and open_slots <= 0:
                reject(f"already at {cfg['max_positions']} open positions")
                continue
            capped = min(capped, budget_left, daily_left, spendable)
            if capped < 0.001:
                reject('no room left: budget {:.3f} · daily {:.3f} · free {:.3f}'
                       .format(budget_left, daily_left, spendable))
                continue
            budget_left -= capped
            daily_left -= capped
            spendable -= capped
            if netuid not in held:
                open_slots -= 1
        else:  # sell
            worth = float((held.get(netuid) or {}).get('value_tao') or 0)
            if worth <= 0:
                reject('nothing held in that subnet')
                continue
            capped = min(capped, worth)

        if abs(capped - amount) > 1e-9:
            trade['note'] = f'cut from τ {amount:g} to the desk cap'
        trade['amount_tao'] = round(capped, 6)
    return out


# ------------------------------------------------------------- execution

def _place(trade: Dict, cfg: Dict) -> Dict:
    """Sign one order. This is the only place the desk touches the chain."""
    base = {'wallet': cfg['wallet'], 'hotkey': cfg['hotkey'],
            'netuid': trade['netuid']}
    if trade['action'] == 'sell_all':
        return tools.call_tool('bt_sell_all', base)
    tool = 'bt_buy' if trade['action'] == 'buy' else 'bt_sell'
    return tools.call_tool(tool, {**base, 'amount_tao': float(trade['amount_tao'])})


def _execute(trade: Dict, cfg: Dict) -> Dict:
    try:
        result = _place(trade, cfg)
        ok = bool(result.get('ok'))
        trade['status'] = 'executed' if ok else 'failed'
        trade['note'] = None if ok else 'extrinsic not confirmed'
        trade['result'] = result
    except Exception as e:
        trade['status'] = 'failed'
        trade['note'] = f'{type(e).__name__}: {e}'
    trade['executed_at'] = int(time.time())
    return trade


def approve(run_id: str, index: int) -> Dict:
    """Place one proposed trade from a past run — the one-click approval."""
    cfg = config()
    with _lock:
        state = _load()
        run = next((r for r in state['runs'] if r.get('id') == run_id), None)
        if run is None:
            raise ValueError(f'no run {run_id}')
        trades = run.get('trades') or []
        if not 0 <= index < len(trades):
            raise ValueError(f'run {run_id} has no trade {index}')
        trade = trades[index]
        if trade.get('status') != 'proposed':
            raise ValueError(f'trade is already {trade.get("status")}')
        trade['status'] = 'placing'
        _save(state)
    _execute(trade, cfg)
    _update_run(run_id, lambda r: r['trades'].__setitem__(index, trade))
    return trade


# ---------------------------------------------------------------- cycles

def cycle(trigger: str = 'manual') -> Dict:
    """One full pass: briefing → agent → guardrails → place or propose."""
    global _running
    with _lock:
        if _running:
            raise RuntimeError('a cycle is already running')
        _running = True
    t0 = time.time()
    cfg = config()
    run: Dict[str, Any] = {
        'id': f'{int(t0)}-{trigger}', 't': int(t0), 'trigger': trigger,
        'auto_execute': bool(cfg['auto_execute']), 'trades': [],
        'thesis': None, 'error': None, 'model': None, 'ms': 0}
    try:
        brief = briefing(cfg)
        run['market_seen'] = len(brief['market'])
        run['free_tao'] = brief['wallet'].get('free_tao')
        if brief['wallet'].get('error'):
            run['error'] = brief['wallet']['error']
            return _record({**run, 'ms': int((time.time() - t0) * 1000)})
        answer = _ask(brief, cfg)
        if answer.get('error'):
            run['error'] = answer['error']
            return _record({**run, 'ms': int((time.time() - t0) * 1000)})
        plan = answer['plan']
        run['thesis'] = str(plan.get('thesis') or '')[:600]
        run['trades'] = guard(plan, brief, cfg)
        if cfg['auto_execute']:
            for trade in run['trades']:
                if trade['status'] == 'proposed':
                    _execute(trade, cfg)
    except Exception as e:
        run['error'] = f'{type(e).__name__}: {e}'
    finally:
        with _lock:
            _running = False
    run['ms'] = int((time.time() - t0) * 1000)
    return _record(run)


def cycle_async(trigger: str = 'manual') -> Dict:
    """Kick a cycle off in the background — a run takes about a minute."""
    with _lock:
        if _running:
            return {'started': False, 'reason': 'a cycle is already running'}
    threading.Thread(target=lambda: cycle(trigger), name='bt-autopilot-run',
                     daemon=True).start()
    return {'started': True}


def _due(cfg: Dict, last: Optional[Dict]) -> bool:
    if not cfg['enabled']:
        return False
    if last is None:
        return True
    return time.time() - last.get('t', 0) >= int(cfg['interval_min']) * 60


def status() -> Dict:
    with _lock:
        state = _load()
    cfg, rs = state['config'], state['runs']
    last = rs[-1] if rs else None
    proposals = sum(1 for r in rs for t in r.get('trades', [])
                    if t.get('status') == 'proposed')
    return {
        'config': cfg,
        'running': _running,
        'loop': _thread is not None and _thread.is_alive(),
        'last_run': last.get('t') if last else None,
        'next_run': (last.get('t', 0) + int(cfg['interval_min']) * 60
                     if cfg['enabled'] and last else None),
        'open_proposals': proposals,
        'deployed_tao': round(_deployed(), 6),
        'spent_24h_tao': round(_spent_24h(), 6),
        'agent': agent_status(),
    }


def _loop() -> None:
    while True:
        try:
            state = _load()
            last = state['runs'][-1] if state['runs'] else None
            if _due(state['config'], last) and not _running:
                out = cycle('scheduled')
                print(f"[bt.autopilot] cycle {out['id']}: "
                      f"{len(out['trades'])} trades, "
                      f"{out.get('error') or 'ok'}", flush=True)
        except Exception as e:
            print(f'[bt.autopilot] cycle failed: {type(e).__name__}: {e}',
                  flush=True)
        time.sleep(TICK_SEC)


def start() -> None:
    """Start the desk loop (idempotent). It sits idle until enabled."""
    global _thread
    if os.environ.get('BT_NO_AUTOPILOT') == '1':
        return
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name='bt-autopilot', daemon=True)
    _thread.start()
