"""prefi MCP server — the local economy and its vibecoded agents as tools.

One JSON-RPC dispatch behind two transports:

* stdio:  ``python3 src/mcp_server.py``            (cwd = the prefi module dir)
* HTTP:   ``POST /mcp`` on the API (:50410), which imports handle_message()

    claude mcp add prefi -- python3 /root/mod/mod/orbit/prefi/src/mcp_server.py
    claude mcp add --transport http prefi http://localhost:50410/mcp

Everything here is play money (PAPER). The stdio transport talks to the
same store the API serves (``~/.mod/prefi/economy``), so an agent added
over stdio shows up in the console and is run by the API's loop. When
the API is up, prefer the HTTP transport — it is the process that owns
the clock.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _version() -> str:
    try:
        return str(json.loads((HERE.parent / 'config.json').read_text())
                   .get('version', '0.1'))
    except Exception:
        return '0.1'


SERVER_INFO = {'name': 'prefi', 'title': 'PreFi economy', 'version': _version()}
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'A simulated crypto economy with play money (PAPER): a price feed that '
    'ticks every few seconds, an exchange, and a one-minute prediction pool. '
    'Vibecoded agents — a sentence becomes a strategy — predict and trade '
    'every minute. Start with prefi_economy (what is live, the prices, the '
    'round), then prefi_agents. prefi_agent_create with prompt= writes an '
    'agent from a description; code= installs your own `decide(ctx)` '
    '(prefi_strategy_language explains the contract). You can also play '
    'directly: prefi_faucet, prefi_predict, prefi_trade under any address. '
    'Nothing here moves real money.')

_mod = None
_eco = None
_agents = None


def _m():
    global _mod
    if _mod is None:
        from mod import Mod
        cfg = {}
        try:
            cfg = json.loads((HERE.parent / 'config.json').read_text())
        except Exception:
            pass
        _mod = Mod(cfg)
    return _mod


def _economy():
    return _m().economy


def _agents_():
    return _m().agents


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc, **kw):
    return {'type': 'number', 'description': desc, **kw}


def _obj(props=None, required=None):
    out: Dict[str, Any] = {'type': 'object', 'properties': props or {}}
    if required:
        out['required'] = required
    return out


TOOLS: Dict[str, Dict[str, Any]] = {
    'prefi_economy': {
        'fn': lambda a: _economy().status(),
        'description': 'The economy right now: feed, tick, round and when it '
                       'closes, every asset price with 1m/1h change, pool stats.',
        'schema': _obj(), 'read_only': True,
    },
    'prefi_prices': {
        'fn': lambda a: {'prices': _economy().prices(),
                         'history': {s: _economy().history(s, int(a.get('ticks', 60)))
                                     for s in _economy().prices()}
                         if a.get('history') else None},
        'description': 'Current prices; history=true adds the last N ticks '
                       '([time, price]) per asset.',
        'schema': _obj({'history': {'type': 'boolean', 'default': False},
                        'ticks': {'type': 'integer', 'default': 60, 'maximum': 2880}}),
        'read_only': True,
    },
    'prefi_add_asset': {
        'fn': lambda a: _economy().add_asset(a['symbol'], a.get('price')),
        'description': 'List a new asset in the economy (anchored on the live '
                       'price if prefi can see one, else price= is required).',
        'schema': _obj({'symbol': _str('Ticker, e.g. DOGE'),
                        'price': _num('Starting price when no live quote exists')},
                       ['symbol']),
    },
    'prefi_agents': {
        'fn': lambda a: {'agents': _agents_().list()},
        'description': 'Every agent with status, run/error counts and its '
                       'leaderboard row (net worth, accuracy, trades).',
        'schema': _obj(), 'read_only': True,
    },
    'prefi_agent': {
        'fn': lambda a: _agents_().get(a['id']),
        'description': 'One agent in full: prompt, code, memory, recent run '
                       'log (actions and errors) and its account.',
        'schema': _obj({'id': _str('Agent id, name or address')}, ['id']),
        'read_only': True,
    },
    'prefi_agent_create': {
        'fn': lambda a: _agents_().create(
            a['name'], code=a.get('code'), prompt=a.get('prompt'),
            owner=a.get('owner'), model=a.get('model'),
            start=a.get('start', True), llm=a.get('llm', True)),
        'description': 'Create an agent. prompt= vibecodes a strategy from a '
                       'description (local claudecode writes it; a keyword '
                       'template if no model answers). code= installs your '
                       'own decide(ctx). The agent is funded and starts '
                       'running every minute unless start=false.',
        'schema': _obj({'name': _str('Display name'),
                        'prompt': _str('What the agent should do, in words'),
                        'code': _str('Python defining decide(ctx) — see prefi_strategy_language'),
                        'model': _str('Model hint for the writer (optional)'),
                        'owner': _str('Your address, for attribution (optional)'),
                        'start': {'type': 'boolean', 'default': True},
                        'llm': {'type': 'boolean', 'default': True,
                                'description': 'false = template only, never call a model'}},
                       ['name']),
    },
    'prefi_agent_update': {
        'fn': lambda a: _agents_().update(
            a['id'], name=a.get('name'), code=a.get('code'),
            prompt=a.get('prompt'), status=a.get('status'),
            revibe=a.get('revibe', False), model=a.get('model'),
            llm=a.get('llm', True)),
        'description': 'Edit an agent: new code, new prompt, status '
                       '(running|paused), or revibe=true to rewrite the code '
                       'from the prompt.',
        'schema': _obj({'id': _str('Agent id, name or address'),
                        'name': _str('New name'), 'code': _str('New decide(ctx) code'),
                        'prompt': _str('New prompt'),
                        'status': {'type': 'string', 'enum': ['running', 'paused']},
                        'revibe': {'type': 'boolean', 'default': False},
                        'model': _str('Model hint'),
                        'llm': {'type': 'boolean', 'default': True}},
                       ['id']),
    },
    'prefi_agent_delete': {
        'fn': lambda a: _agents_().delete(a['id']),
        'description': 'Remove an agent (its account and record stay in the ledger).',
        'schema': _obj({'id': _str('Agent id, name or address')}, ['id']),
        'destructive': True,
    },
    'prefi_agent_run': {
        'fn': lambda a: _agents_().run_one(a['id']),
        'description': 'Run one agent right now, outside the clock, and '
                       'return what it did.',
        'schema': _obj({'id': _str('Agent id, name or address')}, ['id']),
    },
    'prefi_vibecode': {
        'fn': lambda a: __import__('agents').vibecode(
            a['prompt'], model=a.get('model'), llm=a.get('llm', True)),
        'description': 'Preview: turn a description into validated strategy '
                       'code without creating an agent.',
        'schema': _obj({'prompt': _str('What the agent should do'),
                        'model': _str('Model hint'),
                        'llm': {'type': 'boolean', 'default': True}}, ['prompt']),
        'read_only': True,
    },
    'prefi_strategy_check': {
        'fn': lambda a: __import__('agents').check_strategy(a['code']),
        'description': 'Compile and dry-run decide(ctx) on a mock context; '
                       'returns the actions it produced or the error.',
        'schema': _obj({'code': _str('Python defining decide(ctx)')}, ['code']),
        'read_only': True,
    },
    'prefi_strategy_language': {
        'fn': lambda a: {'language': __import__('agents').LANGUAGE,
                         'templates': {k: v['code'] for k, v in
                                       __import__('agents').TEMPLATES.items()}},
        'description': 'The decide(ctx) contract — context fields, action '
                       'shapes, sandbox limits — and the built-in templates.',
        'schema': _obj(), 'read_only': True,
    },
    'prefi_faucet': {
        'fn': lambda a: _economy().faucet(a['address']),
        'description': 'Grant play money to an address: exchange cash and '
                       'pool PAPER (one grant an hour).',
        'schema': _obj({'address': _str('Any address string (0x… or a name)')},
                       ['address']),
    },
    'prefi_predict': {
        'fn': lambda a: _economy().predict(a['address'], a['asset'],
                                           float(a['price']), float(a['stake'])),
        'description': 'Call where an asset closes at the end of the current '
                       'minute, staking pool PAPER. Scored linearly, 1% miss = 0.',
        'schema': _obj({'address': _str('Caller address'),
                        'asset': _str('Asset symbol'),
                        'price': _num('Called close'),
                        'stake': _num('PAPER to stake (min 1)')},
                       ['address', 'asset', 'price', 'stake']),
    },
    'prefi_trade': {
        'fn': lambda a: _economy().trade(a['address'], a['asset'], a['side'],
                                         notional=a.get('notional'),
                                         qty=a.get('qty')),
        'description': 'Market order on the exchange with cash PAPER. '
                       'notional= in PAPER or qty= in units. Pays a spread, '
                       'moves the price.',
        'schema': _obj({'address': _str('Trader address'),
                        'asset': _str('Asset symbol'),
                        'side': {'type': 'string', 'enum': ['buy', 'sell']},
                        'notional': _num('PAPER notional'),
                        'qty': _num('Units (instead of notional)')},
                       ['address', 'asset', 'side']),
    },
    'prefi_account': {
        'fn': lambda a: _economy().account(a['address']),
        'description': 'Cash, holdings (with unrealised PnL), pool balance, '
                       'accuracy and net worth for an address.',
        'schema': _obj({'address': _str('Address')}, ['address']),
        'read_only': True,
    },
    'prefi_leaderboard': {
        'fn': lambda a: {'rows': _economy().leaderboard(
            int(a.get('limit', 50)), names=_agents_().names())},
        'description': 'Everyone ranked by net worth (cash + holdings + pool).',
        'schema': _obj({'limit': {'type': 'integer', 'default': 50}}),
        'read_only': True,
    },
    'prefi_round': {
        'fn': lambda a: _economy().paper.round(a.get('index')),
        'description': 'One minute-pool round: every pot, its entries, '
                       'provisional or final scores.',
        'schema': _obj({'index': {'type': 'integer',
                                  'description': 'Round index; omit for the current one'}}),
        'read_only': True,
    },
    'prefi_rounds': {
        'fn': lambda a: {'rounds': _economy().paper.rounds(int(a.get('limit', 20)))},
        'description': 'Recent minute-pool rounds, newest first.',
        'schema': _obj({'limit': {'type': 'integer', 'default': 20}}),
        'read_only': True,
    },
    'prefi_trades': {
        'fn': lambda a: {'trades': _economy().trades(a.get('address'),
                                                     int(a.get('limit', 100)))},
        'description': 'The exchange tape, newest first; address= filters.',
        'schema': _obj({'address': _str('Filter by trader'),
                        'limit': {'type': 'integer', 'default': 100}}),
        'read_only': True,
    },
    'prefi_tick': {
        'fn': lambda a: _m().economy_loop_step(),
        'description': 'Advance the economy one step now (tick the feed, '
                       'settle due rounds, run agents if a new minute began). '
                       'The API loop does this on its own; use for stdio-only setups.',
        'schema': _obj(),
    },
    'prefi_verify': {
        'fn': lambda a: _economy().paper.verify(),
        'description': 'Replay the minute pool\'s hash-chained log through the '
                       'wasm contract and prove the state root.',
        'schema': _obj(), 'read_only': True,
    },
}


def tool_list() -> list:
    out = []
    for name, t in TOOLS.items():
        out.append({'name': name, 'description': t['description'],
                    'inputSchema': t['schema'],
                    'annotations': {'readOnlyHint': bool(t.get('read_only')),
                                    'destructiveHint': bool(t.get('destructive')),
                                    'openWorldHint': False}})
    return out


def _err_result(msg: str) -> dict:
    return {'content': [{'type': 'text', 'text': msg}], 'isError': True}


def call_tool(name: str, args: dict) -> dict:
    t = TOOLS.get(name)
    if t is None:
        return _err_result(f'unknown tool {name!r}')
    try:
        out = t['fn'](args or {})
    except Exception as e:                            # noqa: BLE001
        return _err_result(f'{type(e).__name__}: {e}')
    if isinstance(out, dict) and 'error' in out and len(out) <= 3:
        return _err_result(str(out['error']))
    return {'content': [{'type': 'text', 'text': json.dumps(out, default=str)}],
            'structuredContent': out if isinstance(out, dict) else {'result': out}}


METHODS: Dict[str, Callable[[dict], Any]] = {
    'initialize': lambda p: {
        'protocolVersion': p.get('protocolVersion')
        if p.get('protocolVersion') in SUPPORTED else PROTOCOL_VERSION,
        'capabilities': {'tools': {'listChanged': False}},
        'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS},
    'ping': lambda p: {},
    'tools/list': lambda p: {'tools': tool_list()},
    'tools/call': lambda p: call_tool(p.get('name', ''), p.get('arguments') or {}),
}


def handle_message(msg: Any) -> Optional[Any]:
    """One JSON-RPC message (or batch) → response, or None for notifications."""
    if isinstance(msg, list):
        out = [r for r in (handle_message(m) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None,
                'error': {'code': -32600, 'message': 'invalid request'}}
    mid, method = msg.get('id'), msg.get('method', '')
    if method.startswith('notifications/') or (mid is None and method):
        return None
    fn = METHODS.get(method)
    if fn is None:
        return {'jsonrpc': '2.0', 'id': mid,
                'error': {'code': -32601, 'message': f'no method {method}'}}
    try:
        return {'jsonrpc': '2.0', 'id': mid, 'result': fn(msg.get('params') or {})}
    except Exception as e:                            # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return {'jsonrpc': '2.0', 'id': mid,
                'error': {'code': -32603, 'message': str(e)}}


def main() -> None:
    """stdio transport: one JSON-RPC message per line."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_message(json.loads(line))
        except json.JSONDecodeError:
            resp = {'jsonrpc': '2.0', 'id': None,
                    'error': {'code': -32700, 'message': 'parse error'}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
