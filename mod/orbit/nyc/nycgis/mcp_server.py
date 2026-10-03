"""
nyc.mcp_server — the Model Context Protocol server over NYC open data.

This module is the *single* JSON-RPC engine behind both MCP transports:

* **stdio** — ``python3 -m nycgis.mcp_server``, newline-delimited JSON-RPC 2.0
  on stdin/stdout, which is what a desktop MCP client launches;
* **streamable HTTP** — ``POST /mcp`` on the module's API, which imports
  :func:`handle_message` from here rather than reimplementing the dispatch.

Keeping one dispatch matters: the two transports drifted apart when they each
carried their own copy, and a tool that worked over stdio would 404 over HTTP.
Everything below the transport line — capabilities, tools, prompts, resources —
is transport-agnostic on purpose.

Connect a client:

    claude mcp add nyc -- python3 -m nycgis.mcp_server        # stdio
    claude mcp add --transport http nyc https://modc2.com/nyc/api/mcp

or, in an ``mcpServers`` config block:

    {"nyc": {"command": "python3", "args": ["-m", "nycgis.mcp_server"],
             "cwd": "/root/mod/mod/orbit/nyc"}}

The server is read-only and needs no credentials of any kind: every tool reads
public, key-free city and state open data.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Dict, List, Optional

from . import tools

SERVER_INFO = {'name': 'nyc', 'title': 'NYC Atlas', 'version': '2.0.0'}

# The version we speak. A client that asks for an older one we still understand
# gets that version back; anything unrecognised is answered with our own, which
# the spec defines as the signal for "negotiate down or disconnect".
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED_PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')

CAPABILITIES = {
    'tools': {'listChanged': False},
    'prompts': {'listChanged': False},
    'resources': {'listChanged': False, 'subscribe': False},
}

INSTRUCTIONS = (
    'NYC open-data tools. For housing questions use nyc_housing / nyc_prices '
    '/ nyc_trend / nyc_sales (recorded deeds, 2016-present). For transit, '
    'parks, flood zones, crashes and boundaries use nyc_layers then '
    'nyc_layer. For ANY other topic — 311, crime, schools, health, budgets, '
    'permits — search the whole portal with nyc_find_datasets, read the '
    'columns with nyc_dataset, then aggregate with nyc_query (SoQL). '
    'Everything is public, key-free city and state open data; cite the '
    'dataset a number came from. Read the nyc://atlas/caveats resource before '
    'quoting a housing figure — three exclusions shape every price on this '
    'server.')


def negotiate(client_version: Optional[str]) -> str:
    """Pick the protocol version to answer an ``initialize`` with."""
    return client_version if client_version in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION


# ─────────────────────────────────────────────────────────────── resources

CAVEATS = """# What the NYC Atlas housing numbers exclude

Three decisions shape every price this server reports. Quote a figure without
them and it will be wrong in a way that looks reasonable.

1. **Sales under $50,000 are dropped.** A large share of rows in the DOF
   rolling-sales file are $0 or nominal deed transfers — family transfers, LLC
   restructurings, estate filings. They are not market prices.

2. **$/ft² is filtered to $50–$5,000 per row.** For condo and co-op units the
   city's file often reports the *whole building's* square footage rather than
   the unit's, so a $1M apartment in a 250,000 ft² tower computes to $4/ft².
   Rows are filtered individually; an area left with fewer than 5 usable rows
   reports no $/ft² at all rather than a noisy one.

3. **Price change needs 5+ sales on each side** of the comparison window.

Areas with no qualifying sales report `null`, never zero — "no data" and
"cheapest" are different answers.

Source: NYC DOF Citywide Rolling Sales (`w2pb-icbu`), ~845,000 recorded deeds
from 2016 to the present. Underlying numeric columns are stored as TEXT.
"""


def _resources() -> List[Dict[str, Any]]:
    return [
        {'uri': 'nyc://atlas/layers',
         'name': 'layer_catalogue',
         'title': 'Map layer catalogue',
         'description': 'Every map layer: id, title, mark form, source dataset.',
         'mimeType': 'application/json'},
        {'uri': 'nyc://atlas/housing-options',
         'name': 'housing_options',
         'title': 'Housing choropleth options',
         'description': 'Valid metric / geography / property_type values for '
                        'nyc_housing and nyc_trend.',
         'mimeType': 'application/json'},
        {'uri': 'nyc://atlas/boroughs',
         'name': 'boroughs',
         'title': 'The five boroughs',
         'description': 'Population, area and slug for each borough.',
         'mimeType': 'application/json'},
        {'uri': 'nyc://atlas/caveats',
         'name': 'data_caveats',
         'title': 'Housing data caveats',
         'description': 'The three exclusions that shape every price figure. '
                        'Read before quoting a number.',
         'mimeType': 'text/markdown'},
    ]


def _read_resource(uri: str) -> Dict[str, Any]:
    if uri == 'nyc://atlas/caveats':
        return {'uri': uri, 'mimeType': 'text/markdown', 'text': CAVEATS}
    if uri == 'nyc://atlas/layers':
        payload = tools.get_nyc().layers()
    elif uri == 'nyc://atlas/housing-options':
        payload = tools.get_nyc().options()
    elif uri == 'nyc://atlas/boroughs':
        payload = tools.get_nyc().boroughs()
    else:
        raise KeyError(f'unknown resource {uri!r}')
    return {'uri': uri, 'mimeType': 'application/json',
            'text': json.dumps(payload, indent=2, default=str)}


# ───────────────────────────────────────────────────────────────── prompts

PROMPTS: List[Dict[str, Any]] = [
    {'name': 'neighborhood_report',
     'title': 'Neighborhood report',
     'description': 'A grounded brief on one NYC neighborhood: what homes sell '
                    'for, where that has gone over time, and what is around it.',
     'arguments': [{'name': 'area', 'description':
                    'Neighborhood, ZIP or address, e.g. "Bed-Stuy" or "11216"',
                    'required': True}],
     'template': (
         'Write a short report on {area} in New York City.\n\n'
         'Ground every number in a tool call:\n'
         '1. nyc_where to place {area} and confirm which neighborhood it is in.\n'
         '2. nyc_housing (geography=nta) for what homes there sell for now, and '
         'how that ranks against the rest of the city.\n'
         '3. nyc_trend for the yearly price history of that area.\n'
         '4. nyc_layers + nyc_layer for what is nearby — subway access, parks, '
         'and whether it sits in a hurricane evacuation zone.\n\n'
         'Read nyc://atlas/caveats first. Lead with the figure, name the '
         'dataset it came from, and say plainly when the data is too thin to '
         'support a claim.')},
    {'name': 'compare_areas',
     'title': 'Compare two areas',
     'description': 'Put two neighborhoods side by side on price, trajectory '
                    'and transit.',
     'arguments': [
         {'name': 'a', 'description': 'First neighborhood or ZIP', 'required': True},
         {'name': 'b', 'description': 'Second neighborhood or ZIP', 'required': True}],
     'template': (
         'Compare {a} and {b} in New York City.\n\n'
         'Use nyc_housing for current prices and nyc_trend for each area\'s '
         'yearly history, then nyc_layer for subway access. Build one small '
         'table: median price, median $/ft2, sales count, 5-year change, '
         'nearest subway. Note where a difference is inside the noise — an '
         'area with a handful of sales is not comparable to one with hundreds. '
         'Read nyc://atlas/caveats before quoting prices.')},
    {'name': 'explore_open_data',
     'title': 'Explore the open-data portal',
     'description': 'Find and aggregate a dataset on any NYC topic — 311, '
                    'crime, schools, permits, budgets.',
     'arguments': [{'name': 'topic', 'description':
                    'What you want to know, e.g. "noise complaints in 2026"',
                    'required': True}],
     'template': (
         'Answer this about New York City using the open-data portal: {topic}\n\n'
         'Work in this order — do not guess a dataset id:\n'
         '1. nyc_find_datasets to locate candidate datasets.\n'
         '2. nyc_dataset on the best one to read its real column names.\n'
         '3. nyc_query with $select/$where/$group to aggregate server-side. '
         'Never pull raw rows to count them yourself.\n\n'
         'Report the figure, the dataset name and id, and how current the data '
         'is. If the portal has nothing that answers this, say so.')},
]

_PROMPTS_BY_NAME = {p['name']: p for p in PROMPTS}


def _prompt_list() -> List[Dict[str, Any]]:
    return [{k: v for k, v in p.items() if k != 'template'} for p in PROMPTS]


def _prompt_get(name: str, args: Optional[Dict] = None) -> Dict[str, Any]:
    p = _PROMPTS_BY_NAME.get(str(name))
    if not p:
        raise KeyError(f'unknown prompt {name!r}; known: {sorted(_PROMPTS_BY_NAME)}')
    args = dict(args or {})
    missing = [a['name'] for a in p.get('arguments', [])
               if a.get('required') and not args.get(a['name'])]
    if missing:
        raise ValueError(f'missing required argument(s): {missing}')
    text = p['template'].format(**{a['name']: args.get(a['name'], '')
                                   for a in p.get('arguments', [])})
    return {'description': p['description'],
            'messages': [{'role': 'user',
                          'content': {'type': 'text', 'text': text}}]}


# ─────────────────────────────────────────────────────────────── dispatch

def _err(id_: Any, code: int, message: str) -> Dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _ok(id_: Any, result: Any) -> Dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _call_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run one tool. A tool that raises is reported as an *executing* result with
    ``isError`` set, not as a JSON-RPC error: the spec draws that line so the
    model gets to see the failure and correct itself (a bad SoQL clause, a
    dataset id that does not exist) instead of the client swallowing it.
    """
    name = params.get('name')
    args = params.get('arguments') or {}
    try:
        out = tools.call_tool(name, args)
    except KeyError as e:
        return {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
    except Exception as e:
        return {'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}],
                'isError': True}
    result: Dict[str, Any] = {
        'content': [{'type': 'text', 'text': json.dumps(out, indent=2, default=str)}],
        'isError': False,
    }
    # Structured output rides along whenever the tool returned an object, so a
    # client that can consume JSON does not have to re-parse the text block.
    if isinstance(out, dict):
        result['structuredContent'] = out
    return result


def handle_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Handle one JSON-RPC message. Returns the reply, or ``None`` for a
    notification (no ``id``), which by definition is never answered.
    """
    method = msg.get('method')
    id_ = msg.get('id')
    params = msg.get('params') or {}

    if id_ is None:
        return None                      # notification: initialized, cancelled, …

    if method == 'initialize':
        return _ok(id_, {
            'protocolVersion': negotiate(params.get('protocolVersion')),
            'capabilities': CAPABILITIES,
            'serverInfo': SERVER_INFO,
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _ok(id_, {})
    if method == 'tools/list':
        return _ok(id_, {'tools': tools.list_tools()})
    if method == 'tools/call':
        return _ok(id_, _call_tool(params))
    if method == 'prompts/list':
        return _ok(id_, {'prompts': _prompt_list()})
    if method == 'prompts/get':
        try:
            return _ok(id_, _prompt_get(params.get('name'), params.get('arguments')))
        except (KeyError, ValueError) as e:
            return _err(id_, -32602, str(e))
    if method == 'resources/list':
        return _ok(id_, {'resources': _resources()})
    if method == 'resources/templates/list':
        return _ok(id_, {'resourceTemplates': []})
    if method == 'resources/read':
        try:
            return _ok(id_, {'contents': [_read_resource(str(params.get('uri')))]})
        except KeyError as e:
            return _err(id_, -32602, str(e))
        except Exception as e:
            return _err(id_, -32603, f'{type(e).__name__}: {e}')
    return _err(id_, -32601, f'method not found: {method}')


# ─────────────────────────────────────────────────────────── stdio transport

def _write(msg: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            reply = handle_message(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            if msg.get('id') is not None:
                _write(_err(msg['id'], -32603, 'internal error'))
            continue
        if reply is not None:
            _write(reply)


if __name__ == '__main__':
    main()
