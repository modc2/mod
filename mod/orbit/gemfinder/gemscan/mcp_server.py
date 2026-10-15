"""
gemscan.mcp_server — the one JSON-RPC dispatch behind both MCP transports:

* stdio:  ``python3 -m gemscan.mcp_server``  (cwd = the gemfinder module dir)
* HTTP:   ``POST /mcp`` on the module server, which imports handle_message()

    claude mcp add gemfinder -- python3 -m gemscan.mcp_server     # from orbit/gemfinder
    claude mcp add --transport http gemfinder http://localhost:51320/mcp

Strictly read-only (the one write, clearing the cache, stays on REST where it
is owner-gated). Keyless — every upstream is a public feed.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import engine, http, narratives, semantic


def _version() -> str:
    """config.json is the one place the version lives."""
    try:
        return json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text()).get('version', '0')
    except Exception:
        return '0'


SERVER_INFO = {'name': 'gemfinder', 'title': 'Gemfinder', 'version': _version()}
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'Narrative gem scanner for crypto tokens and stocks, built on keyless public feeds '
    '(CoinGecko, DexScreener, reddit via ApeWisdom, Yahoo Finance, Hacker News). '
    'gem_scan ranks candidates 0-100 by attention (attention, velocity, narrative heat, '
    'momentum, size) and sorts by upside = score x theme-fit x room-to-run; `pick` is the '
    'single gem most likely to go up. gem_find turns ANY typed theme ("quantum", "frog", '
    '"nuclear drones") into a live scan matched by words and by meaning. Before trusting a '
    'result, read its `flags` (rug/illiquidity/already-mooned risks) and `why`, and use '
    'gem_review to see how past picks actually priced. This measures attention, not value — '
    'it is a research lens, never financial advice. Scans are cached ~10 min; fresh=true '
    're-hits every feed and is slow (10-30s).')


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


_ASSET = _str('Universe to scan', enum=['all', 'crypto', 'stock'], default='all')
_SORT = _str('upside = most likely to go up (default); score = raw attention', enum=['upside', 'score'], default='upside')
_FRESH = {'type': 'boolean', 'default': False, 'description': 'Skip the 10-min scan cache and re-hit every feed (slow)'}
_K = {'type': 'integer', 'default': 15, 'minimum': 1, 'maximum': 100, 'description': 'How many gems to return'}


def _scan(a: dict) -> dict:
    return engine.scan(asset=a.get('asset', 'all'), k=int(a.get('k', 15)),
                       narrative=a.get('narrative') or None, min_score=float(a.get('min_score', 0)),
                       q=a.get('q') or None, fresh=bool(a.get('fresh')), sort=a.get('sort', 'upside'),
                       save=bool(a.get('save', True)))


def _find(a: dict) -> dict:
    res = _scan({**a, 'q': a['q']})
    return {'theme': res.get('theme'), 'pick': res.get('pick'), 'cached': res.get('cached'),
            'age_s': res.get('age_s'), 'gems': res.get('gems', [])}


TOOLS: Dict[str, Dict[str, Any]] = {
    'gem_scan': {
        'fn': _scan,
        'description': 'Scan every feed and return ranked gems with score breakdown (`parts`), upside, '
                       'theme fit, reasons (`why`) and risk `flags`, plus `pick` — the one gem most '
                       'likely to go up — and per-source status. Cached ~10 min unless fresh=true.',
        'schema': {'type': 'object', 'properties': {
            'asset': _ASSET, 'k': _K,
            'q': _str('Optional typed theme ("quantum", "frog", "nuclear drones") — matched by words AND meaning'),
            'narrative': _str('Restrict to one built-in narrative id (see gem_narratives)'),
            'min_score': {'type': 'number', 'default': 0, 'description': 'Drop gems scoring below this (0-100)'},
            'sort': _SORT, 'fresh': _FRESH,
        }},
        'read_only': True,
    },
    'gem_find': {
        'fn': _find,
        'description': 'Type anything dope and it becomes a live theme: words + semantic meaning are matched '
                       'against every candidate, and CoinGecko/DexScreener/Yahoo search pull in more. Returns '
                       'the theme (keywords, nearest narrative, match counts), the pick, and the gems that fit.',
        'schema': {'type': 'object', 'required': ['q'], 'properties': {
            'q': _str('The theme, e.g. "quantum", "frog", "nuclear drones", "solana agents"'),
            'asset': _ASSET, 'k': _K, 'sort': _SORT, 'fresh': _FRESH,
        }},
        'read_only': True,
    },
    'gem_review': {
        'fn': lambda a: engine.review(int(a.get('n', 12)), fresh=bool(a.get('fresh'))),
        'description': 'Did past picks go up? Every gem listed in the last n scans priced now vs when first '
                       'listed, with hit rate and median return. Hours of data, not a track record.',
        'schema': {'type': 'object', 'properties': {
            'n': {'type': 'integer', 'default': 12, 'minimum': 1, 'maximum': 100,
                  'description': 'How many past scans to price'},
            'fresh': {'type': 'boolean', 'default': False, 'description': 'Skip the 5-min review cache'},
        }},
        'read_only': True,
    },
    'gem_narratives': {
        'fn': lambda a: engine.narratives(asset=a.get('asset', 'all'), hn=bool(a.get('hn', True))),
        'description': 'Heat 0-100 per built-in narrative (quantum, AI agents, memes, nuclear, ...) for '
                       'crypto and stocks, with the raw drivers behind each number.',
        'schema': {'type': 'object', 'properties': {
            'asset': _ASSET,
            'hn': {'type': 'boolean', 'default': True, 'description': 'Include Hacker News buzz in the heat'},
        }},
        'read_only': True,
    },
    'gem_history': {
        'fn': lambda a: {'scans': engine.history(int(a.get('n', 10)))},
        'description': 'Past scans, newest first, each with its picks and the prices at scan time.',
        'schema': {'type': 'object', 'properties': {
            'n': {'type': 'integer', 'default': 10, 'minimum': 1, 'maximum': 100}}},
        'read_only': True,
    },
    'gem_sources': {
        'fn': lambda a: _sources(),
        'description': 'The feeds a scan draws on and what each contributes.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
    'gem_status': {
        'fn': lambda a: {'module': 'gemfinder', 'version': _version(), 'scan_cache': engine.cache_stats(),
                         'http_cache': http.stats(), 'narratives': narratives.ids(),
                         'semantic': semantic.status()},
        'description': 'Cache state, known narrative ids and whether semantic (embedding) matching is up.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
}


def _sources() -> dict:
    from .server import source_list
    return source_list()


def tool_list() -> list:
    return [{'name': n, 'description': t['description'], 'inputSchema': t['schema'],
             'annotations': {'readOnlyHint': t['read_only'], 'openWorldHint': True,
                             'destructiveHint': False}}
            for n, t in TOOLS.items()]


def call_tool(name: str, args: dict) -> dict:
    t = TOOLS.get(name)
    if not t:
        return _err_result(f'unknown tool {name!r}')
    try:
        out = t['fn'](args or {})
    except Exception as e:      # bad args and dead upstreams both go back to the model
        return _err_result(f'{type(e).__name__}: {e}')
    return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, default=str)}],
            'structuredContent': out if isinstance(out, dict) else {'result': out},
            'isError': False}


def _err_result(msg: str) -> dict:
    return {'content': [{'type': 'text', 'text': msg}], 'isError': True}


METHODS: Dict[str, Callable[[dict], Any]] = {
    'initialize': lambda p: {
        'protocolVersion': p.get('protocolVersion') if p.get('protocolVersion') in SUPPORTED else PROTOCOL_VERSION,
        'capabilities': {'tools': {'listChanged': False}},
        'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS},
    'ping': lambda p: {},
    'tools/list': lambda p: {'tools': tool_list()},
    'tools/call': lambda p: call_tool(p.get('name', ''), p.get('arguments') or {}),
}


def handle_message(msg: Any) -> Optional[Any]:
    """One JSON-RPC message (or batch) -> response, or None for notifications."""
    if isinstance(msg, list):
        out = [r for r in (handle_message(m) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
    mid, method = msg.get('id'), msg.get('method', '')
    if mid is None:
        return None                                   # notification
    fn = METHODS.get(method)
    if fn is None:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'no method {method}'}}
    try:
        return {'jsonrpc': '2.0', 'id': mid, 'result': fn(msg.get('params') or {})}
    except Exception as e:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_message(json.loads(line))
        except json.JSONDecodeError:
            resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
