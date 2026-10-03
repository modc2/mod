"""
newsagg.mcp_server — the one JSON-RPC dispatch behind both MCP transports:

* stdio:  ``python3 -m newsagg.mcp_server``  (cwd = the news module dir)
* HTTP:   ``POST /mcp`` on the module server, which imports handle_message()

    claude mcp add news -- python3 -m newsagg.mcp_server      # from orbit/news
    claude mcp add --transport http news http://localhost:51120/mcp

Read-only except the feed list. No credentials anywhere.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict, Optional

from . import cache, engine, sources, store

SERVER_INFO = {'name': 'news', 'title': 'News', 'version': '0.1.0'}
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'Keyless news aggregator. news_search fans a query out to open sources '
    '(GDELT, Hacker News, the user\'s RSS feeds; reddit/google opt-in), merges '
    'duplicate stories and ranks by relevance, freshness and how many outlets '
    'carry it. Then news_read a result url for the article text before '
    'summarising — headlines alone mislead. Cite outlet + url for every claim.')


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


TOOLS: Dict[str, Dict[str, Any]] = {
    'news_search': {
        'fn': lambda a: engine.search(a['query'], k=a.get('k', 20), sources=a.get('sources'),
                                      hours=a.get('hours', 72)),
        'description': 'Search the news for a query across sources; returns de-duplicated stories '
                       'ranked by relevance, recency and coverage, each with outlet, url, age and why.',
        'schema': {'type': 'object', 'required': ['query'], 'properties': {
            'query': _str('What to look for, e.g. "bittensor dtao" or "EU AI act"'),
            'k': {'type': 'integer', 'default': 20, 'minimum': 1, 'maximum': 100},
            'hours': {'type': 'number', 'default': 72, 'description': 'Time window; 0 = any age'},
            'sources': {'type': 'array', 'items': {'type': 'string', 'enum': list(sources.SOURCES)},
                        'description': 'Subset of sources; omit for defaults, ["all"] for every one'},
        }},
        'read_only': True,
    },
    'news_read': {
        'fn': lambda a: engine.read(a['url'], a.get('max_chars', 8000)),
        'description': 'Fetch one article and return its readable text.',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {
            'url': _str('Article url (from news_search)'),
            'max_chars': {'type': 'integer', 'default': 8000},
        }},
        'read_only': True,
    },
    'news_sources': {
        'fn': lambda a: {n: {'default': s['default'], 'docs': s['docs']} for n, s in sources.SOURCES.items()},
        'description': 'List the sources a search can use and which are on by default.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
    'news_feeds': {
        'fn': lambda a: {'feeds': store.feeds()},
        'description': 'List the RSS/Atom feeds the "feeds" source pulls from.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
    'news_add_feed': {
        'fn': lambda a: store.add(a['url'], a.get('name', '')),
        'description': 'Add an RSS/Atom feed (validated by parsing it first).',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {
            'url': _str('Feed url'), 'name': _str('Display name')}},
        'read_only': False,
    },
    'news_remove_feed': {
        'fn': lambda a: store.remove(a['url']),
        'description': 'Remove a feed by url or name.',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {'url': _str('Feed url or name')}},
        'read_only': False,
    },
    'news_status': {
        'fn': lambda a: {'cache': cache.stats(), 'feeds': len(store.feeds()),
                         'sources': list(sources.SOURCES)},
        'description': 'Cache size and configuration.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
}


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
