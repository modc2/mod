#!/usr/bin/env python3
"""debank mcp — eighteen tools that answer "what does this address own?"

The tools are ordered the way the question actually gets answered: start at
`debank_portfolio` (the total and which chains carry it), then drill — tokens,
DeFi positions, NFTs — on the chains that matter. `debank_approvals` is the
other half of a wallet review: what someone else is still allowed to take.

Self-contained JSON-RPC 2.0 on the stdlib, no `mcp` package:

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50720 # Streamable HTTP — POST /mcp

api.py mounts `handle()` at /mcp, so the tools, the REST routes and the console
are the same code and cannot drift.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import client as C                                          # noqa: E402
from client import Client, DebankError                       # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'DeBank indexes what EVM addresses own across every chain — token balances, '
    'DeFi positions (including debt), NFTs, transaction history and live token '
    'approvals. Start with debank_portfolio: it returns the net worth and which '
    'chains carry it, which tells you where to drill with debank_tokens and '
    'debank_protocols (pass chain= to spend one unit instead of scanning all of '
    'them). Amounts come back already multiplied out into USD, biggest first, '
    'with dust below min_usd dropped and counted rather than silently cut. '
    'debank_approvals is the risk view — standing permissions ranked by what the '
    'spender could take today; it is per chain by design. Addresses must be 0x '
    'hex, not ENS. BYOK: every call spends the CALLER\'S DeBank units and needs '
    'an AccessKey from cloud.debank.com (debank_set_key); only debank_chains '
    'answers signed-out.'
)


def _client(a):
    """Per-call keys never leave this process and are never echoed back."""
    if not isinstance(a, dict):
        return Client()
    return Client(key=a.pop('key', None))


# ── tools ──

def _t_portfolio(a):
    return _client(a).portfolio(a['id'], min_usd=a.get('min_usd', 1.0))


def _t_chains_used(a):
    return _client(a).chains_used(a['id'])


def _t_tokens(a):
    c = _client(a)
    return c.tokens(a['id'], chain=a.get('chain'), min_usd=a.get('min_usd', 1.0),
                    limit=a.get('limit', 100), all_tokens=a.get('all_tokens', False))


def _t_protocols(a):
    c = _client(a)
    return c.protocols(a['id'], chain=a.get('chain'), min_usd=a.get('min_usd', 1.0),
                       limit=a.get('limit', 50), detail=a.get('detail', False))


def _t_nfts(a):
    c = _client(a)
    return c.nfts(a['id'], chain=a.get('chain'), limit=a.get('limit', 50),
                  all_nfts=a.get('all_nfts', False))


def _t_history(a):
    c = _client(a)
    return c.history(a['id'], chain=a.get('chain'), start_time=a.get('start_time'),
                     page_count=a.get('page_count', 20), token_id=a.get('token_id'))


def _t_approvals(a):
    c = _client(a)
    return c.approvals(a['id'], chain=a.get('chain'), min_usd=a.get('min_usd', 0.0),
                       limit=a.get('limit', 100))


def _t_net_curve(a):
    return _client(a).net_curve(a['id'], chain=a.get('chain'))


def _t_position(a):
    return _client(a).protocol_position(a['id'], a['protocol'])


def _t_protocol(a):
    c = _client(a)
    return c.protocol(id=a.get('protocol'), chain=a.get('chain'),
                      limit=a.get('limit', 100))


def _t_token(a):
    return _client(a).token(a['chain'], a['token'])


def _t_token_price(a):
    return _client(a).token_price_history(a['chain'], a['token'], date_at=a.get('date'))


def _t_holders(a):
    c = _client(a)
    if a.get('protocol'):
        return c.protocol_holders(a['protocol'], start=a.get('start', 0),
                                  limit=a.get('limit', 20))
    if not (a.get('chain') and a.get('token')):
        raise DebankError('give protocol=, or chain= and token=', status=400)
    return c.token_holders(a['chain'], a['token'], start=a.get('start', 0),
                           limit=a.get('limit', 20))


def _t_gas(a):
    return _client(a).gas(a['chain'])


def _t_chains(a):
    return _client(a).chains(q=a.get('q'), refresh=a.get('refresh', False))


def _t_account(a):
    return _client(a).account()


def _t_set_key(a):
    return C.set_key(a.get('key'), persist=a.get('persist', True))


def _t_raw(a):
    return _client(a).raw(a['path'], params=a.get('params'), public=a.get('public', False))


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_ID = _str('the EVM address to look up, 0x + 40 hex characters (not an ENS name)')
_CHAIN = _str('DeBank chain id — eth, bsc, matic, arb, op, base, avax, xdai, ftm, '
              'era, scrl … Common names are translated (ethereum, polygon, '
              'arbitrum, gnosis). Omit to scan EVERY chain, which costs more '
              'units and is slower; debank_chains lists them all.')
_MIN_USD = _num('drop rows worth less than this in USD (default 1). Dropped rows '
                'are counted in hidden_below_min_usd, never silently cut. Use 0 '
                'to see everything including dust and spam.')

TOOLS = {
    'debank_portfolio': {
        'description': 'Net worth of an address across every chain, and which chains '
                       'carry it. This is the first call: the chain list it returns '
                       'is what you pass as `chain` to every other tool, so you drill '
                       'into the two chains that hold the money instead of scanning '
                       'sixty that hold nothing. Covers wallet tokens AND DeFi '
                       'positions — the total is not just the token balances.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'min_usd': _MIN_USD}, 'required': ['id']},
        'handler': _t_portfolio,
    },
    'debank_tokens': {
        'description': 'Token balances held directly in the wallet, priced and ranked '
                       'biggest first — amount, unit price and USD value per row, plus '
                       'the total. Excludes tokens locked in protocols (those are '
                       'debank_protocols). Spam tokens are filtered out unless '
                       'all_tokens=true; their prices are fiction, so treat any total '
                       'that includes them as fiction too.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _CHAIN, 'min_usd': _MIN_USD,
            'limit': _num('rows to return (default 100)'),
            'all_tokens': _bool('include unverified/spam tokens (default false)'),
        }, 'required': ['id']},
        'handler': _t_tokens,
    },
    'debank_protocols': {
        'description': 'Open DeFi positions per protocol, with the net USD in each: '
                       'supplied plus unclaimed rewards MINUS borrowed. Lending debt '
                       'is real and is subtracted — a position can be worth less than '
                       'its deposits, and health_rate is carried through where the '
                       'protocol reports one. Pass detail=true to get the individual '
                       'assets and debt tokens inside each position.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _CHAIN, 'min_usd': _MIN_USD,
            'limit': _num('protocols to return (default 50)'),
            'detail': _bool('include the token legs of every position (default false)'),
        }, 'required': ['id']},
        'handler': _t_protocols,
    },
    'debank_approvals': {
        'description': 'Live token approvals — every standing permission this address '
                       'has granted a contract to move its tokens, ranked by '
                       'exposure_usd: what that spender could take TODAY at current '
                       'balances and prices. `unlimited` flags the infinite-allowance '
                       'grants. This is the wallet-hygiene view; per chain by design, '
                       'so run it on the chains debank_portfolio says hold value.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _CHAIN,
            'min_usd': _num('minimum exposure to report (default 0 — show everything)'),
            'limit': _num('rows to return (default 100)'),
        }, 'required': ['id', 'chain']},
        'handler': _t_approvals,
    },
    'debank_history': {
        'description': 'Recent transactions, decoded: what was sent, what came back, '
                       'which protocol it went through, gas in USD, and whether it '
                       'reverted. Newest first, 20 per page (DeBank\'s cap) — page '
                       'back by passing the returned oldest_time as start_time.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _CHAIN,
            'start_time': _num('unix seconds — return transactions OLDER than this; '
                               'use the oldest_time from the previous page'),
            'page_count': _num('rows per page, max 20'),
            'token_id': _str('only transactions touching this token (needs chain)'),
        }, 'required': ['id']},
        'handler': _t_history,
    },
    'debank_nfts': {
        'description': 'NFTs held by an address, valued at floor price where DeBank '
                       'has one, biggest first. Floor prices are estimates and thin '
                       'collections have none — a 0 here means unpriced, not worthless.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _CHAIN,
            'limit': _num('rows to return (default 50)'),
            'all_nfts': _bool('include unverified collections (default false)'),
        }, 'required': ['id']},
        'handler': _t_nfts,
    },
    'debank_net_curve': {
        'description': "Net worth over time — DeBank's own daily curve for the "
                       'address, oldest point first, with the change over the window. '
                       'Use it to see whether a portfolio is growing or bleeding '
                       'before reading any single position.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'chain': _str('one chain instead of the all-chain total')},
            'required': ['id']},
        'handler': _t_net_curve,
    },
    'debank_position': {
        'description': "One address's full position in one protocol, unsummarized — "
                       'every pool, every token leg, exactly as DeBank returns it. Use '
                       'after debank_protocols names the protocol you care about.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID,
            'protocol': _str('DeBank protocol id, e.g. aave3, uniswap3, curve — the '
                             '`protocol` field from debank_protocols'),
        }, 'required': ['id', 'protocol']},
        'handler': _t_position,
    },
    'debank_chains_used': {
        'description': 'Which chains an address has ever transacted on, with when it '
                       'first appeared on each. One cheap call — use it to bound a '
                       'scan before running per-chain tools.',
        'inputSchema': {'type': 'object', 'properties': {'id': _ID}, 'required': ['id']},
        'handler': _t_chains_used,
    },
    'debank_protocol': {
        'description': 'The protocol catalog: one protocol by id, or every protocol on '
                       'a chain ranked by TVL. This is where protocol ids come from '
                       'when you want to query a position directly.',
        'inputSchema': {'type': 'object', 'properties': {
            'protocol': _str('protocol id, e.g. aave3 — omit to list a chain'),
            'chain': _CHAIN,
            'limit': _num('protocols to return when listing (default 100)'),
        }},
        'handler': _t_protocol,
    },
    'debank_token': {
        'description': 'Token metadata and current USD price: symbol, name, decimals, '
                       'whether DeBank has verified it, and the price it uses in every '
                       'other tool.',
        'inputSchema': {'type': 'object', 'properties': {
            'chain': _CHAIN,
            'token': _str('token contract address, or the chain id itself for the '
                          'native coin (e.g. token="eth" on chain="eth")'),
        }, 'required': ['chain', 'token']},
        'handler': _t_token,
    },
    'debank_token_price': {
        'description': "A token's closing price on a past date (UTC). Use it to value "
                       'a historical position, or to check what a trade in '
                       'debank_history was actually worth at the time.',
        'inputSchema': {'type': 'object', 'properties': {
            'chain': _CHAIN, 'token': _str('token contract address'),
            'date': _str('YYYY-MM-DD, UTC — omit for the latest close'),
        }, 'required': ['chain', 'token']},
        'handler': _t_token_price,
    },
    'debank_holders': {
        'description': 'The biggest holders of a token (chain + token) or the biggest '
                       'depositors in a protocol (protocol), largest first. Whale '
                       'discovery: the addresses it returns go straight into '
                       'debank_portfolio.',
        'inputSchema': {'type': 'object', 'properties': {
            'protocol': _str('protocol id — ranks depositors by USD'),
            'chain': _CHAIN, 'token': _str('token contract address'),
            'start': _num('offset into the ranking (default 0)'),
            'limit': _num('rows, max 100 (default 20)'),
        }},
        'handler': _t_holders,
    },
    'debank_gas': {
        'description': 'The current gas market on a chain: slow / normal / fast with '
                       'gwei and the seconds each is expected to take. Read before '
                       'timing a transaction.',
        'inputSchema': {'type': 'object', 'properties': {'chain': _CHAIN},
                        'required': ['chain']},
        'handler': _t_gas,
    },
    'debank_chains': {
        'description': 'Every chain DeBank indexes — id, name, native token, EVM '
                       'community id and explorer — plus the alias table this module '
                       'accepts. The one tool that answers WITHOUT a key (it falls '
                       "back to DeBank's public catalog), so it is a safe first call.",
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by name or id'),
            'refresh': _bool('bypass the 10-minute cache'),
        }},
        'handler': _t_chains,
    },
    'debank_account': {
        'description': 'Whether the caller\'s AccessKey works, where it was resolved '
                       'from, and the remaining unit balance if the plan exposes it. '
                       'Call this first when anything returns 401 or 403.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_account,
    },
    'debank_set_key': {
        'description': 'Store a DeBank Cloud AccessKey in the off-tree keystore '
                       '(~/.mod/debank/key.json, 0600) so later calls need no key '
                       'argument. The key is never returned by any tool — only a '
                       'masked form. Keys come from cloud.debank.com and are billed in '
                       'units per call.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the AccessKey'),
            'persist': _bool('write it to disk (default true; false = this process only)'),
        }, 'required': ['key']},
        'handler': _t_set_key,
    },
    'debank_raw': {
        'description': 'Escape hatch: call any DeBank Cloud route directly with the '
                       "caller's key attached, for anything not normalized above — new "
                       'endpoints, or fields the summaries drop.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': _str('path under https://pro-openapi.debank.com, e.g. '
                         '/v1/user/simple_protocol_list'),
            'params': {'type': 'object', 'description': 'query parameters'},
            'public': _bool('use the keyless api.debank.com host instead (catalog only)'),
        }, 'required': ['path']},
        'handler': _t_raw,
    },
}


# ── JSON-RPC 2.0 ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool. Raises DebankError/ValueError with a readable message."""
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    return tool['handler'](dict(args or {}))


def _call(id_, params):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args)
    except DebankError as e:
        # A tool failure is a *successful* JSON-RPC response carrying isError, per
        # the MCP spec, so the model reads the hint and retries instead of dying.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), indent=2)}],
                             'structuredContent': e.dict(), 'isError': True})
    except KeyError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: missing argument {e}'}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: bad arguments — {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if isinstance(body, list):
        return [r for r in (handle(m) for m in body) if r is not None] or None
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'debank', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


# ── transports ──

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50720)))
    else:
        serve_stdio()
