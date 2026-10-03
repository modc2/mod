"""MCP compatibility for Strat classes — any Strat becomes an MCP server.

The canonical Strat interface (base/mod.py) standardizes how ENGINES talk to a
strategy; this shim standardizes how AGENTS do. `serve(build, ...)` wraps a
strat factory in a stdio MCP server with four tools:

    strat_info       {}                          the recipe, watchlist, config
    strat_signal     {trades:[...], wallet_usdc?, open_positions?}
                                                 pure signal(): which orders
                                                 WOULD fire on these trades —
                                                 nothing is placed, ever
    strat_backtest   {trades:[...], capital?}    deterministic replay → curve,
                                                 roi, fees (Strat.backtest)
    strat_state      {}                          Strat.state() snapshot

There is deliberately no execute/tick tool: a strat served over MCP is a
read-only oracle about itself. Placing orders stays with the live engine and
its human-flipped switches.

The protocol implementation mirrors src/mcp.py — hand-rolled JSON-RPC over
stdio, one message per line, no SDK dependency — so a generated strat under
polymarket-user-strats/<id>/mod.py runs anywhere python3 runs.

Usage (what generated strats emit):

    from strats.mcp_compat import serve

    def build(capital=None): return MyStrat(StratConfig(...))

    if __name__ == "__main__":
        serve(build, name="polymarket-strat-x", description="...")

Claude Code registration:
    claude mcp add my-strat -- python3 /path/to/mod.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from .base.mod import OrderSide, Strat, TraderTrade

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

TRADES_SCHEMA = {
    'type': 'array',
    'description': ('Observed upstream trades. Each: {id, trader, timestamp(ms), '
                    'market, condition_id, token_id, side: BUY|SELL, size(shares), '
                    'price(0-1), outcome?}. Aliases conditionId/tokenId/asset accepted.'),
    'items': {'type': 'object'},
}


def trades_from_json(rows: Any) -> list[TraderTrade]:
    """Tolerant TraderTrade parser — accepts this module's snake_case, the
    console's camelCase, and data-api's `asset` for the token id."""
    out: list[TraderTrade] = []
    for i, r in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        side = str(r.get('side') or 'BUY').upper()
        out.append(TraderTrade(
            id=str(r.get('id') or f'trade-{i}'),
            trader=str(r.get('trader') or r.get('proxyWallet') or ''),
            timestamp=int(r.get('timestamp') or 0),
            market=str(r.get('market') or r.get('title') or r.get('slug') or ''),
            condition_id=str(r.get('condition_id') or r.get('conditionId') or ''),
            token_id=str(r.get('token_id') or r.get('tokenId') or r.get('asset') or ''),
            side=OrderSide.SELL if side == 'SELL' else OrderSide.BUY,
            size=float(r.get('size') or 0),
            price=float(r.get('price') or 0),
            outcome=r.get('outcome'),
        ))
    return out


def _plain(v: Any) -> Any:
    """Dataclasses (Order, BacktestResult, …) → JSON-safe structures."""
    if is_dataclass(v) and not isinstance(v, type):
        return _plain(asdict(v))
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, OrderSide):
        return v.value
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def serve(build: Callable[..., Strat], name: str, description: str = '',
          version: str = '1.0.0') -> None:
    """Serve one strat over stdio MCP. `build(capital=None)` must return a
    fresh Strat — a fresh instance per backtest keeps replays independent."""

    def t_info(args: dict) -> dict:
        s = build()
        return {
            'name': s.config.name,
            'description': description,
            'capital': s.config.capital,
            'watchlist': s.config.watchlist,
            'min_order_size': s.config.min_order_size,
            'max_order_size': s.config.max_order_size,
            'params': s.config.params,
            'class': type(s).__name__,
        }

    def t_signal(args: dict) -> dict:
        import time
        from .base.mod import SyncResult
        s = build(capital=args.get('capital'))
        snap = SyncResult(
            timestamp=int(time.time() * 1000),
            trader_trades=trades_from_json(args.get('trades')),
            wallet_usdc=float(args.get('wallet_usdc') or s.config.capital),
            open_positions={str(k): float(v) for k, v in
                            (args.get('open_positions') or {}).items()},
        )
        orders = s.signal(snap)
        return {'orders': _plain(orders), 'count': len(orders)}

    def t_backtest(args: dict) -> dict:
        trades = trades_from_json(args.get('trades'))
        if not trades:
            raise ValueError('trades required — the historical TraderTrade list to replay')
        s = build(capital=args.get('capital'))
        return _plain(s.backtest(trades))

    def t_state(args: dict) -> dict:
        return _plain(build().state())

    tools = {
        'strat_info': {
            'description': f'{description or name} — recipe, watchlist and sizing config.',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': t_info,
        },
        'strat_signal': {
            'description': ('Pure signal(): given observed trades, the orders this strat '
                            'WOULD place. Places nothing.'),
            'inputSchema': {'type': 'object', 'properties': {
                'trades': TRADES_SCHEMA,
                'wallet_usdc': {'type': 'number'},
                'open_positions': {'type': 'object'},
                'capital': {'type': 'number'},
            }, 'required': ['trades']},
            'handler': t_signal,
        },
        'strat_backtest': {
            'description': ('Deterministic replay of the strat over a historical trade list '
                            '→ pnl curve, roi, fees, notes. Touches no live wallet.'),
            'inputSchema': {'type': 'object', 'properties': {
                'trades': TRADES_SCHEMA,
                'capital': {'type': 'number', 'description': 'override the recipe capital'},
            }, 'required': ['trades']},
            'handler': t_backtest,
        },
        'strat_state': {
            'description': 'Strat.state() — positions, handled trades, watchlist.',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': t_state,
        },
    }

    def result(id_: Any, r: dict) -> dict:
        return {'jsonrpc': '2.0', 'id': id_, 'result': r}

    def error(id_: Any, code: int, message: str) -> dict:
        return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}

    def handle(body: Any):
        if not isinstance(body, dict) or not isinstance(body.get('method'), str):
            return error(body.get('id') if isinstance(body, dict) else None,
                         -32600, 'invalid request')
        method, id_, params = body['method'], body.get('id'), body.get('params') or {}
        if id_ is None or method.startswith('notifications/'):
            return None
        if method == 'initialize':
            v = str(params.get('protocolVersion') or '')
            return result(id_, {
                'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': name, 'version': version},
                'instructions': description,
            })
        if method == 'ping':
            return result(id_, {})
        if method == 'tools/list':
            return result(id_, {'tools': [
                {'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
                for n, t in tools.items()]})
        if method == 'tools/call':
            tname = str(params.get('name') or '')
            tool = tools.get(tname)
            if not tool:
                return error(id_, -32602, f'unknown tool: {tname}')
            try:
                r = tool['handler'](params.get('arguments') or {})
            except Exception as e:  # MCP: tool failure is a successful response carrying isError
                return result(id_, {'content': [{'type': 'text',
                                                 'text': f'{tname} failed: {type(e).__name__}: {e}'}],
                                    'isError': True})
            return result(id_, {'content': [{'type': 'text',
                                             'text': json.dumps(r, indent=2, default=str)}],
                                'structuredContent': r, 'isError': False})
        return error(id_, -32601, f'method not found: {method}')

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()
