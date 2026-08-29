"""
openhouse mcp — Model Context Protocol endpoint (Streamable HTTP transport).

POST /mcp speaks JSON-RPC 2.0, so any MCP client (Claude, IDEs, agent
frameworks) can drive the rent-to-own protocol as a set of tools: read the
live deal, quote a payment split, record rent, check a renter's equity,
pull the cap table, and compare OpenHouse against every other on-chain
housing project — without a bespoke SDK.

Self-contained by design: the JSON-RPC handling is hand-rolled (no `mcp`
package) and every tool calls the same Mod instance the REST API serves,
so the numbers a model reads here are the numbers the site shows.

The router is built by ``build_router(get_mod, version)`` rather than
importing api.py — that keeps the dependency pointing one way (api.py →
this module) and lets the tests drive the tools against a Mod of their own.

Auth: none, deliberately. The openhouse REST surface is public and this is
the same surface; every write tool is a testnet bookkeeping entry, not a
signed transaction. Nothing here can move real money.

The file is named mcp_server.py, not mcp.py, so it can never shadow the
`mcp` package on sys.path — api/ is put on the path directly by uvicorn's
--app-dir, which would make a local mcp.py win for the whole process.
"""
import inspect
import json
from typing import Callable, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

# Echo the client's protocol version when we know it; otherwise pin the
# oldest revision whose feature set (plain-JSON Streamable HTTP, tools)
# this server fully implements.
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'OpenHouse is rent-to-own housing on-chain: the protocol takes 1-5% '
    '(owner-set, hard-capped in the contract) and the remaining 95-99% of '
    'every payment stays with the property, split between the renter\'s '
    'equity and the owner\'s income by whichever rent-to-own model the '
    'owner picked. Start with openhouse_terms (the live deal) and '
    'openhouse_quote (what one payment actually buys). No auth is needed. '
    'This deployment is on testnet: the tools that write '
    '(openhouse_pay_rent, openhouse_purchase, openhouse_set_terms, '
    'openhouse_claim_owner) record local bookkeeping entries, not signed '
    'on-chain transactions, and no real money or deed is involved.'
)


class ToolError(Exception):
    """A tool refused the call — reported to the client as isError, not 500."""


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ToolError(f'{key} required')
    return v


def _num(args: dict, key: str, default=None) -> float:
    raw = args.get(key)
    if raw is None or raw == '':
        if default is None:
            raise ToolError(f'{key} required')
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ToolError(f'{key} must be a number — got {raw!r}')


def _ok(result):
    """Mod methods report failure as {'error': ...}; surface it as a tool error."""
    if isinstance(result, dict) and 'error' in result:
        raise ToolError(str(result['error']))
    return result


# ── tool handlers (thin wrappers over the Mod instance) ──

def _t_status(args, oh):
    return oh.status()


def _t_property(args, oh):
    return oh.property()


def _t_terms(args, oh):
    return oh.terms()


def _t_models(args, oh):
    return oh.models()


def _t_quote(args, oh):
    return _ok(oh.quote(_num(args, 'amount'), kind=str(args.get('kind') or 'rent')))


def _t_rent_stats(args, oh):
    return oh.rent_stats()


def _t_rent_ledger(args, oh):
    ledger = oh.rent_ledger(str(args.get('renter') or ''))
    limit = int(_num(args, 'limit', 100))
    return {'payments': len(ledger), 'ledger': ledger[:max(limit, 1)]}


def _t_equity(args, oh):
    return oh.equity(_req(args, 'address'))


def _t_shareholders(args, oh):
    holders = oh.shareholders()
    return {'count': len(holders), 'shareholders': holders}


def _t_portfolio(args, oh):
    return oh.portfolio(_req(args, 'address'))


def _t_dividends(args, oh):
    history = oh.dividends()
    return {'distributions': len(history), 'history': history}


def _t_landscape(args, oh):
    return oh.compare(refresh=bool(args.get('refresh')))


def _t_source(args, oh):
    """Manifest by default; one file's full text when asked for by name.

    The three source files run to tens of thousands of tokens together, so
    handing back every byte on an unqualified call would blow a context
    window to answer "what's in here?".
    """
    files = oh.source()
    name = str(args.get('name') or '').strip()
    if not name:
        return {'files': [{k: v for k, v in f.items() if k != 'content'} for f in files]}
    for f in files:
        if f['name'] == name or f['name'].endswith('/' + name) or f['name'].split('/')[-1] == name:
            return f
    raise ToolError(f"no such source file: {name} — have "
                    f"{', '.join(f['name'] for f in files)}")


def _t_pay_rent(args, oh):
    return _ok(oh.pay_rent(_req(args, 'renter'), _num(args, 'amount'),
                           kind=str(args.get('kind') or 'rent')))


def _t_purchase(args, oh):
    return _ok(oh.purchase(_req(args, 'buyer'), int(_num(args, 'share_count')),
                           _num(args, 'payment', 0)))


def _t_set_terms(args, oh):
    fields = ('model', 'fee_pct', 'credit_pct', 'option_fee_pct',
              'home_price', 'monthly_rent', 'owner', 'treasury')
    kwargs = {k: args[k] for k in fields if args.get(k) is not None}
    if not kwargs:
        raise ToolError('nothing to set — pass at least one of: ' + ', '.join(fields))
    return _ok(oh.set_terms(**kwargs))


def _t_claim_owner(args, oh):
    return _ok(oh.claim_owner(_req(args, 'address')))


TOOLS = {
    'openhouse_terms': {
        'description': 'The live deal: rent-to-own model, protocol fee, the '
                       'share of each payment credited as renter equity vs '
                       'owner income, home price, monthly payment and the '
                       '1-5% fee band the contract enforces. Start here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_terms,
    },
    'openhouse_quote': {
        'description': 'Split a payment the way the contract would, without '
                       "recording it: protocol fee, renter equity, owner "
                       "income. kind='option' credits the whole net amount "
                       'as equity. Equity is clamped so it never runs past '
                       'the home price.',
        'inputSchema': {'type': 'object', 'properties': {
            'amount': {'type': 'number', 'description': 'payment amount in ETH'},
            'kind': {'type': 'string', 'enum': ['rent', 'option'], 'description': "'rent' splits by the model, 'option' is all equity (default rent)"},
        }, 'required': ['amount']},
        'handler': _t_quote,
    },
    'openhouse_models': {
        'description': 'The rent-to-own presets an owner can start from '
                       '(full credit / hybrid / classic / lease), the 1-5% '
                       'protocol fee band, and the published take rates of '
                       'the platforms OpenHouse is measured against.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_models,
    },
    'openhouse_status': {
        'description': 'Everything at a glance: whether a property is '
                       'deployed, the live terms, aggregate rent, cap-table '
                       'size, shares sold vs available, and dividends paid.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'openhouse_property': {
        'description': 'The property itself: description, total shares, '
                       'share price, shares still available, active flag and '
                       'contract address. Reports deployed=false honestly '
                       'when nothing has been fractionalized yet.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_property,
    },
    'openhouse_rent_stats': {
        'description': 'Where the rent actually went across every recorded '
                       'payment: gross, protocol fees, renter equity, owner '
                       'income, the percentage that stayed with the property '
                       'and how much of the home is paid off.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_rent_stats,
    },
    'openhouse_rent_ledger': {
        'description': 'Individual rent payments, newest first, each with its '
                       'three-way split. Filter to one renter with `renter`.',
        'inputSchema': {'type': 'object', 'properties': {
            'renter': {'type': 'string', 'description': '0x address to filter by (default: everyone)'},
            'limit': {'type': 'integer', 'description': 'max payments returned (default 100)'},
        }},
        'handler': _t_rent_ledger,
    },
    'openhouse_equity': {
        'description': "A renter's stake: payments made, rent paid, principal "
                       'credited, fees paid, percent of the home owned and '
                       'what is left to own outright.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x renter address'},
        }, 'required': ['address']},
        'handler': _t_equity,
    },
    'openhouse_shareholders': {
        'description': 'The public cap table: every holder with share count, '
                       'contribution, ownership percentage and dividends '
                       'claimed.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_shareholders,
    },
    'openhouse_portfolio': {
        'description': "One address's position: shares, ownership percent, "
                       'contribution, dividends claimed and current value at '
                       'the live share price.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x holder address'},
        }, 'required': ['address']},
        'handler': _t_portfolio,
    },
    'openhouse_dividends': {
        'description': 'Distribution history: when rent was redistributed to '
                       'holders, how much, per share, and to how many.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_dividends,
    },
    'openhouse_landscape': {
        'description': 'OpenHouse against every other on-chain housing '
                       'project, sorted by who ends up owning the house — '
                       'with live token/property numbers from public '
                       'endpoints, the sourced evidence, and an honest list '
                       'of where the field is ahead of us. Cached; pass '
                       'refresh=true to re-fetch (slower, hits third-party APIs).',
        'inputSchema': {'type': 'object', 'properties': {
            'refresh': {'type': 'boolean', 'description': 'bypass the cache and re-fetch live numbers (default false)'},
        }},
        'handler': _t_landscape,
    },
    'openhouse_source': {
        'description': 'Read the actual implementation. With no arguments '
                       'returns the manifest of readable files (name, '
                       'language, size); with `name` returns that one file in '
                       'full — including the Solidity that holds the shares.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'file to read in full, e.g. OpenHouse.sol (default: list the manifest)'},
        }},
        'handler': _t_source,
    },
    'openhouse_pay_rent': {
        'description': 'WRITES. Record a rent payment and split it into '
                       'protocol fee, renter equity and owner income, then '
                       "return the renter's updated equity. kind='option' "
                       'credits the whole net payment as equity. Testnet '
                       'bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'renter': {'type': 'string', 'description': '0x paying address'},
            'amount': {'type': 'number', 'description': 'payment amount in ETH'},
            'kind': {'type': 'string', 'enum': ['rent', 'option'], 'description': "'rent' (default) or 'option'"},
        }, 'required': ['renter', 'amount']},
        'handler': _t_pay_rent,
    },
    'openhouse_purchase': {
        'description': 'WRITES. Buy shares in the deployed property; payment '
                       'is computed from the live share price when omitted. '
                       'Fails if no property is deployed or too few shares '
                       'remain. Testnet bookkeeping — no funds move.',
        'inputSchema': {'type': 'object', 'properties': {
            'buyer': {'type': 'string', 'description': '0x buyer address'},
            'share_count': {'type': 'integer', 'description': 'number of shares to buy'},
            'payment': {'type': 'number', 'description': 'payment amount (default: share_count x share price)'},
        }, 'required': ['buyer', 'share_count']},
        'handler': _t_purchase,
    },
    'openhouse_set_terms': {
        'description': 'WRITES. Set the deal: pick a model preset and/or tune '
                       'the dials. fee_pct is rejected outside the 1-5% band '
                       'and credit_pct outside 0-100. Once an owner address '
                       'is recorded, only that address may change the terms — '
                       'pass it as `owner`.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': {'type': 'string', 'enum': ['full_credit', 'hybrid', 'classic', 'lease'], 'description': 'preset to start from'},
            'fee_pct': {'type': 'number', 'description': 'protocol take, 1-5'},
            'credit_pct': {'type': 'number', 'description': 'share of the post-fee payment credited as equity, 0-100'},
            'option_fee_pct': {'type': 'number', 'description': 'upfront option fee, % of home price'},
            'home_price': {'type': 'number', 'description': 'price to own outright, ETH'},
            'monthly_rent': {'type': 'number', 'description': 'scheduled monthly payment, ETH'},
            'owner': {'type': 'string', 'description': '0x address making the change — required once an owner is recorded'},
            'treasury': {'type': 'string', 'description': '0x protocol fee sink'},
        }},
        'handler': _t_set_terms,
    },
    'openhouse_claim_owner': {
        'description': 'WRITES. Claim the owner seat while it is still empty '
                       '(first writer wins). After this, only that address '
                       'can change the terms.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x address to record as owner'},
        }, 'required': ['address']},
        'handler': _t_claim_owner,
    },
}


def _rpc_result(id_, result) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_,
                         'error': {'code': code, 'message': message}},
                        status_code=status)


def _tool_error(id_, message: str) -> JSONResponse:
    # Tool failures (bad args, a rejected fee, a home already paid off) are
    # *successful* JSON-RPC responses carrying isError — per MCP spec — so
    # the client model can read the message and correct course.
    return _rpc_result(id_, {'content': [{'type': 'text', 'text': message}],
                             'isError': True})


async def _call_tool(id_, params: dict, get_mod: Callable) -> JSONResponse:
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _rpc_error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _rpc_error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args, get_mod())
        if inspect.isawaitable(result):
            result = await result
    except ToolError as e:
        return _tool_error(id_, f'{name}: {e}')
    except Exception as e:
        return _tool_error(id_, f'{name} failed: {type(e).__name__}: {e}')
    out = {'content': [{'type': 'text',
                        'text': json.dumps(result, indent=2, default=str)}],
           'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _rpc_result(id_, out)


def build_router(get_mod: Callable, version: str = '2.1.0') -> APIRouter:
    """Mount the MCP endpoint over a Mod accessor.

    Args:
        get_mod: zero-arg callable returning the openhouse Mod instance —
                 called per tool call so the lazy singleton stays lazy.
        version: reported to clients as serverInfo.version.
    """
    router = APIRouter()

    @router.get('/mcp')
    def mcp_get():
        """Streamable HTTP without SSE: nothing to GET — clients must POST."""
        return Response(status_code=405, media_type='text/plain',
                        content='POST JSON-RPC 2.0 messages to this endpoint')

    @router.post('/mcp')
    async def mcp_post(request: Request):
        """MCP Streamable HTTP endpoint: one JSON-RPC message per POST."""
        try:
            body = json.loads((await request.body()) or b'')
        except Exception:
            return _rpc_error(None, -32700,
                              'parse error: body is not valid JSON', status=400)
        if not isinstance(body, dict) or not isinstance(body.get('method'), str):
            id_ = body.get('id') if isinstance(body, dict) else None
            return _rpc_error(id_, -32600,
                              'invalid request: expected a JSON-RPC 2.0 object '
                              'with a method', status=400)
        method, id_ = body['method'], body.get('id')
        params = body.get('params') or {}
        # Notifications (no id, or notifications/*) get an empty 202 per spec.
        if id_ is None or method.startswith('notifications/'):
            return Response(status_code=202)
        if method == 'initialize':
            client_ver = str(params.get('protocolVersion') or '')
            return _rpc_result(id_, {
                'protocolVersion': client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'openhouse', 'version': version},
                'instructions': INSTRUCTIONS,
            })
        if method == 'ping':
            return _rpc_result(id_, {})
        if method == 'tools/list':
            return _rpc_result(id_, {'tools': [
                {'name': n, 'description': t['description'],
                 'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]})
        if method == 'tools/call':
            return await _call_tool(id_, params, get_mod)
        return _rpc_error(id_, -32601, f'method not found: {method}')

    return router
