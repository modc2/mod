#!/usr/bin/env python3
"""raydium mcp — seventeen tools over Solana's biggest AMM.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50790 # Streamable HTTP — POST /mcp

The API server mounts `handle()` at /mcp too, and every REST route dispatches
through `call_tool`, so the tools, the routes and the console cannot drift.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us afterwards.
    sys.path.append(HERE)

import ray                                                   # noqa: E402
from ray import RayError                                     # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Raydium, the biggest AMM on Solana, read end to end. Start with '
    'ray_overview for the size of the place, ray_pools to rank the book, and '
    'ray_pair when you have two tokens in mind — it shows every pool that '
    'trades them and how far apart they price it. Tokens: ray_search finds a '
    'mint by name, ray_token says where it trades and how deep. Trading: '
    'ray_quote prices a swap in whole tokens through Raydium\'s own router, '
    'and ray_swap_tx turns that quote into an UNSIGNED transaction — this '
    'server holds no keys and never signs; hand the bytes to a wallet, e.g. '
    'the solana module. Liquidity: ray_depth is the number TVL hides, the '
    'money actually within 0.5-10% of the price, integrated from the pool\'s '
    'liquidity line; ray_wallet reads a wallet\'s LP tokens AND its '
    'concentrated positions, which no portfolio API shows because they live in '
    'an NFT-derived account; ray_position reads one of those by its NFT. '
    'Symbols work anywhere a mint does (SOL, USDC, RAY, and anything liquid); '
    'ambiguous ones are refused rather than guessed. Reads need no key.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_TOKEN = _str('a mint address, or a symbol like SOL, USDC, RAY')
_POOL = _str('a Raydium pool address — ray_pools and ray_pair return them')


# ── handlers ─────────────────────────────────────────────────────

def _t_overview(a):
    return ray.overview()


def _t_pools(a):
    return ray.pools(type=a.get('type', 'all'), sort=a.get('sort', 'volume24h'),
                     order=a.get('order', 'desc'), limit=a.get('limit', 20),
                     page=a.get('page', 1), min_tvl=a.get('min_tvl'),
                     min_volume=a.get('min_volume'), search=a.get('search'),
                     full=bool(a.get('full')))


def _t_pool(a):
    return ray.pool(a['pool'], keys=bool(a.get('keys')))


def _t_pair(a):
    return ray.pair(a['token_a'], a.get('token_b'), sort=a.get('sort', 'liquidity'),
                    limit=a.get('limit', 10), type=a.get('type', 'all'))


def _t_token(a):
    return ray.token(a['token'], pools_limit=a.get('limit', 5))


def _t_price(a):
    ids = a.get('tokens') or a.get('token') or a.get('mints')
    px = ray.prices(ids)
    names = ray.mint_info(list(px))
    return {'prices': [{'mint': m, 'symbol': (names.get(m) or {}).get('symbol'),
                        'usd': v} for m, v in px.items()],
            'source': 'raydium — priced from its own pools, so a token that only '
                      'trades elsewhere may be missing'}


def _t_search(a):
    return ray.search(a['query'], limit=a.get('limit', 10))


def _t_mints(a):
    return ray.mints(search=a.get('search'), limit=a.get('limit', 50),
                     page=a.get('page', 1))


def _t_quote(a):
    return ray.quote(a['input'], a['output'], a['amount'],
                     slippage_bps=a.get('slippage_bps', 50),
                     mode=a.get('mode', 'in'))


def _t_swap_tx(a):
    return ray.swap_transaction(a['wallet'], a['input'], a['output'], a['amount'],
                                slippage_bps=a.get('slippage_bps', 50),
                                mode=a.get('mode', 'in'),
                                priority=a.get('priority', 'h'),
                                wrap_sol=a.get('wrap_sol'),
                                unwrap_sol=a.get('unwrap_sol'))


def _t_depth(a):
    bands = a.get('bands')
    if isinstance(bands, str):
        bands = [float(b) / 100 for b in bands.split(',') if b.strip()]
    elif isinstance(bands, list):
        bands = [float(b) / 100 for b in bands]
    return ray.depth(a['pool'], bands=bands, points=a.get('points', 48))


def _t_keys(a):
    return ray.pool_keys(a['pool'])


def _t_farms(a):
    return ray.farms(pool=a.get('pool'), ids=a.get('ids'), limit=a.get('limit', 20))


def _t_stake(a):
    return ray.stake_pools()


def _t_wallet(a):
    return ray.wallet(a['wallet'], min_usd=a.get('min_usd', 0.01),
                      limit=a.get('limit', 50))


def _t_position(a):
    return ray.position(a['nft_mint'])


def _t_api(a):
    params = a.get('params') or {}
    if isinstance(params, str):
        params = json.loads(params or '{}')
    return ray.raw_api(a['path'], **params)


# ── registry ─────────────────────────────────────────────────────

TOOLS = {
    'ray_overview': {
        'description': 'THE PROTOCOL IN ONE CALL: total value locked, 24h volume, '
                       'the turnover ratio between them, the RAY and SOL price, and '
                       'the priority fee Raydium is currently recommending. Start '
                       'here to know whether the numbers you are about to read are '
                       'a big day or a quiet one.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_overview,
    },
    'ray_pools': {
        'description': 'RANK THE BOOK. Every Raydium pool, sorted by volume (the '
                       'default), TVL, fees or APR over 24h/7d/30d, filtered by '
                       'minimum TVL or volume and by symbol. Note that sorting by '
                       'liquidity puts pools whose reserves are a worthless token '
                       'at the top — volume is the honest ranking. type= narrows to '
                       'concentrated (CLMM) or standard (constant-product) pools, '
                       'or to the ones running farms.',
        'inputSchema': {'type': 'object', 'properties': {
            'type': _str('all (default), concentrated/clmm, standard/amm, farm',
                         enum=sorted(set(ray.POOL_TYPES))),
            'sort': _str('volume24h (default), liquidity/tvl, fee24h, apr24h, and '
                         'the 7d/30d versions', enum=sorted(ray.SORT_FIELDS)),
            'order': _str('desc (default) or asc', enum=['desc', 'asc']),
            'limit': _num('rows to return, max 100 (default 20)'),
            'page': _num('page of the ranked book (default 1)'),
            'min_tvl': _num('drop pools holding less than this in USD'),
            'min_volume': _num('drop pools trading less than this in 24h USD'),
            'search': _str('keep only pools whose pair matches this text'),
            'full': _bool('include the 7d and 30d windows and the reward config'),
        }},
        'handler': _t_pools,
    },
    'ray_pool': {
        'description': 'ONE POOL IN FULL: both mints, price, reserves, fee rate, '
                       'and the 24h/7d/30d volume, fees and APR — plus the price '
                       'range it traded in each window, which is the fastest way to '
                       'see whether a concentrated range is about to go out of '
                       'range. Accepts a pool address OR an LP mint, because a '
                       'wallet holds the second and everything else quotes the '
                       'first. keys=true adds the vaults and program accounts.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _str('pool address, or the LP mint of a standard pool'),
            'keys': _bool('also return the on-chain accounts (see ray_keys)'),
        }, 'required': ['pool']},
        'handler': _t_pool,
    },
    'ray_pair': {
        'description': 'EVERY POOL THAT TRADES A PAIR, and what they disagree '
                       'about. Give it one token for all of that token\'s pools, or '
                       'two for the pair. Returns the deepest pool, the busiest '
                       'pool (rarely the same one), the aggregate TVL and volume, '
                       'and the price spread across the pools deep enough to '
                       'matter — a wide spread is an arbitrage, or a warning that '
                       'one of these pools is a trap.',
        'inputSchema': {'type': 'object', 'properties': {
            'token_a': _TOKEN, 'token_b': _str('optional second token of the pair'),
            'sort': _str('liquidity (default), volume24h, apr24h…',
                         enum=sorted(ray.SORT_FIELDS)),
            'type': _str('all (default), concentrated, standard',
                         enum=sorted(set(ray.POOL_TYPES))),
            'limit': _num('pools to return (default 10)'),
        }, 'required': ['token_a']},
        'handler': _t_pair,
    },
    'ray_token': {
        'description': 'A TOKEN AS RAYDIUM SEES IT: price, decimals, whether it is '
                       'on Raydium\'s verified list or merely trades there, how '
                       'many pools carry it, their total TVL and 24h volume, and '
                       'the deepest one. Use it before trading anything unfamiliar '
                       '— "verified_on_raydium: false" plus one thin pool is the '
                       'shape of a token you cannot sell.',
        'inputSchema': {'type': 'object', 'properties': {
            'token': _TOKEN, 'limit': _num('pools to list (default 5)'),
        }, 'required': ['token']},
        'handler': _t_token,
    },
    'ray_price': {
        'description': 'USD price for one or many tokens, by mint or symbol, priced '
                       'from Raydium\'s own pools. A token that trades only '
                       'elsewhere comes back null rather than wrong.',
        'inputSchema': {'type': 'object', 'properties': {
            'tokens': _str('comma-separated mints or symbols'),
        }, 'required': ['tokens']},
        'handler': _t_price,
    },
    'ray_search': {
        'description': 'FIND A TOKEN by symbol or name. Raydium\'s verified list is '
                       'only ~220 mints, so this also searches the open token index '
                       'and labels every row with where it came from: '
                       '"raydium-verified" is vouched for, "jupiter" is an open '
                       'index where anyone can list a symbol — check the liquidity '
                       'on those before trusting the name.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('a symbol, a name, or a mint address'),
            'limit': _num('rows (default 10)'),
        }, 'required': ['query']},
        'handler': _t_search,
    },
    'ray_mints': {
        'description': 'Raydium\'s verified mint list, paged — the set of tokens '
                       'Raydium itself vouches for, plus a count of the ones it '
                       'blacklists.',
        'inputSchema': {'type': 'object', 'properties': {
            'search': _str('filter by symbol or name'),
            'limit': _num('rows per page (default 50)'), 'page': _num('page'),
        }},
        'handler': _t_mints,
    },
    'ray_quote': {
        'description': 'WHAT A SWAP ACTUALLY GETS YOU, through Raydium\'s own '
                       'router. Amounts are in whole tokens, not base units: '
                       'amount=1 of SOL means one SOL. Returns the route hop by hop '
                       'with the pool and fee at each, the price impact, the worst '
                       'case after slippage, and vs_spot_pct — how far the whole '
                       'trade lands from the spot price, which is the number that '
                       'tells you to split the order. mode=out sizes the trade by '
                       'what you want to receive instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'input': _str('token you are spending — mint or symbol'),
            'output': _str('token you want — mint or symbol'),
            'amount': _num('in whole tokens: of the input for mode=in, of the '
                           'output for mode=out'),
            'slippage_bps': _num('tolerance in basis points (default 50 = 0.5%)'),
            'mode': _str('in (default) or out', enum=['in', 'out']),
        }, 'required': ['input', 'output', 'amount']},
        'handler': _t_quote,
    },
    'ray_swap_tx': {
        'description': 'BUILD THE SWAP, DO NOT SIGN IT. Takes a wallet address and '
                       'the same arguments as ray_quote, and returns base64 '
                       'transactions ready to sign — this server holds no keys and '
                       'will not hold any. Hand the bytes to something that signs: '
                       'the solana module\'s keystore, or a browser wallet. Wraps '
                       'and unwraps SOL automatically, and fails clearly if the '
                       'wallet has no account for the token it would spend. A quote '
                       'is a snapshot: build and send in the same minute.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _str('the address that will sign and pay'),
            'input': _str('token being spent'), 'output': _str('token wanted'),
            'amount': _num('in whole tokens'),
            'slippage_bps': _num('default 50'),
            'mode': _str('in (default) or out', enum=['in', 'out']),
            'priority': _str('priority fee tier: m, h (default), vh',
                             enum=['m', 'h', 'vh']),
            'wrap_sol': _bool('override SOL wrapping (default: on when spending SOL)'),
            'unwrap_sol': _bool('override unwrapping (default: on when receiving SOL)'),
        }, 'required': ['wallet', 'input', 'output', 'amount']},
        'handler': _t_swap_tx,
    },
    'ray_depth': {
        'description': 'THE NUMBER TVL HIDES: how much money sits within 0.5%, 1%, '
                       '2%, 5% and 10% of the current price, on each side. In a '
                       'concentrated pool most of the TVL can be parked in a range '
                       'the price left months ago, so a $7m pool can have $200k '
                       'behind the next 1% — this integrates the pool\'s published '
                       'liquidity line to say which. Standard pools get the '
                       'constant-product maths instead. The full-range total is '
                       'cross-checked against the reported reserves so you can see '
                       'when the line is stale.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL,
            'bands': _str('percentages to measure, comma-separated '
                          '(default 0.5,1,2,5,10)'),
            'points': _num('points of the liquidity curve to return (default 48)'),
        }, 'required': ['pool']},
        'handler': _t_depth,
    },
    'ray_keys': {
        'description': 'THE ON-CHAIN ACCOUNTS behind a pool: both vaults, the '
                       'authority, the config, the observation account, the market '
                       'accounts for AMM v4 pools and the address lookup table its '
                       'transactions use. This is what you need to build an '
                       'instruction against Raydium yourself instead of going '
                       'through the router.',
        'inputSchema': {'type': 'object', 'properties': {'pool': _POOL},
                        'required': ['pool']},
        'handler': _t_keys,
    },
    'ray_farms': {
        'description': 'EMISSION FARMS — the extra yield on top of trading fees. '
                       'By pool (it finds the farms on that pool\'s LP mint) or by '
                       'farm address. Returns each reward token, its rate per week, '
                       'its APR and when the emission ends, because a farm APR that '
                       'expires on Friday is not an APR.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _str('a standard pool address — its farms are looked up by LP mint'),
            'ids': _str('comma-separated farm addresses'),
            'limit': _num('rows (default 20)'),
        }},
        'handler': _t_farms,
    },
    'ray_stake': {
        'description': 'Single-sided RAY staking: the pool, its TVL and its APR.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_stake,
    },
    'ray_wallet': {
        'description': 'WHAT A WALLET HOLDS ON RAYDIUM — both kinds. LP tokens are '
                       'matched to their pools and valued by share of reserves. '
                       'Concentrated positions are the hard half: they are NFTs '
                       'with no balance and no price, and the money lives in an '
                       'account derived from the NFT, so no portfolio API shows '
                       'them. This derives that account for every NFT in the '
                       'wallet, decodes the ones that exist, and prices the token '
                       'amounts from the tick range and the live pool price. '
                       'out_of_range lists the positions currently earning nothing.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _str('the wallet address to read'),
            'min_usd': _num('hide dust below this (default 0.01)'),
            'limit': _num('NFTs to check, max 400 (default 50) — each batch of 100 '
                          'is one RPC call'),
        }, 'required': ['wallet']},
        'handler': _t_wallet,
    },
    'ray_position': {
        'description': 'ONE CONCENTRATED POSITION by the NFT mint that represents '
                       'it: its tick range as real prices, whether the pool price is '
                       'inside it, the token amounts it currently holds, the fees '
                       'the pool has checkpointed to it, and the USD value of both. '
                       'Read straight off chain — the position account is derived '
                       'from the NFT, not indexed anywhere.',
        'inputSchema': {'type': 'object', 'properties': {
            'nft_mint': _str('the position NFT mint held by the wallet'),
        }, 'required': ['nft_mint']},
        'handler': _t_position,
    },
    'ray_api': {
        'description': 'THE ESCAPE HATCH: any Raydium v3 API path, unwrapped, for '
                       'the endpoints not shaped into a tool here. e.g. '
                       'path=/main/chain-time, path=/pools/line/position with '
                       'params={"id":"<pool>"}.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': _str('a path on api-v3.raydium.io, starting with /'),
            'params': {'type': 'object', 'description': 'query parameters',
                       'additionalProperties': True},
        }, 'required': ['path']},
        'handler': _t_api,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer so a route and a
    tools/call cannot answer differently."""
    tool = TOOLS.get(name)
    if not tool:
        raise RayError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise RayError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {
            'content': [{'type': 'text', 'text': json.dumps(out, default=str,
                                                            indent=2)}],
            'structuredContent': out if isinstance(out, dict) else None,
            'isError': False})
    except RayError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
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
            'serverInfo': {'name': 'raydium', 'version': version()},
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
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50790)))
    else:
        serve_stdio()
