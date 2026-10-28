#!/usr/bin/env python3
"""sui mcp — seventeen tools for a chain where nothing says what it is.

Sui's own ambiguity sets the order of these tools. An account address and an
object ID are both 32 bytes of hex and there is no way to tell them apart by
looking, so `sui_what` comes first: it asks the chain which one a string turned
out to be — or whether it is a coin type, a transaction digest or a SuiNS name —
and every other tool branches from its answer.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50740 # Streamable HTTP — POST /mcp

The API server mounts `handle()` at /mcp too, so the tools, the REST routes and
the console can never drift apart.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us afterwards.
    sys.path.append(HERE)

import keys as K                                            # noqa: E402
from bcs import SUI_TYPE                                    # noqa: E402
from chain import NETWORKS, SPEND_USD, Client               # noqa: E402
from keys import SuiError                                   # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Sui, read and write. On Sui an account address and an object ID are both '
    '32 bytes of hex and nothing distinguishes them, so start with sui_what for '
    'any string you did not generate: it identifies addresses, objects, '
    'packages, coin types, transaction digests and SuiNS names, and returns the '
    'detail that fits. Then branch: sui_portfolio for what an address holds '
    'priced in USD (it includes staked SUI, which appears in no balance call), '
    'sui_objects for NFTs and capabilities, sui_object for one object in full, '
    'sui_coin for a coin type\'s supply and market, sui_history + sui_tx for '
    'what happened (sui_tx decodes balance changes per owner and the commands '
    'that ran, not raw effects). sui_package reads a published package\'s '
    'callable functions — Move keeps its interface on chain, so you can see '
    'what a contract offers before calling it. sui_network, sui_validators and '
    'sui_stake cover the chain itself. Writing: sui_wallet manages an off-tree '
    'keystore and sui_transfer signs locally after simulating the transaction; '
    f'anything over ${SPEND_USD:,.0f} returns needs_confirm until you pass '
    'confirm=true, and dry_run=true always simulates without signing. Every '
    'tool takes network=mainnet|testnet|devnet and an optional rpc= override. '
    'Note that Mysten\'s public fullnodes have DROPPED JSON-RPC, so this module '
    'runs against a pool of third-party endpoints; set rpc= to your own node if '
    'you have one. sui_rpc is the escape hatch for any method not wrapped here.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_NET = _str('mainnet (default), testnet, devnet, or a full RPC url',
            enum=list(NETWORKS) + ['custom'])
_RPC = _str('override the RPC endpoint for this call — use your own node when '
            'the public pool throttles')
_ADDR = _str('a Sui address, 0x + up to 64 hex (short forms like 0x2 are fine)')
_COIN = _str(f'a coin type such as {SUI_TYPE} (the default), or a symbol — but a '
             'symbol resolves by liquidity and is not an identifier')
_COMMON = {'network': _NET, 'rpc': _RPC}


def _client(args):
    return Client(network=args.pop('network', None), rpc=args.pop('rpc', None))


# ── handlers ─────────────────────────────────────────────────────

def _t_what(a):
    return _client(a).what(a.get('query') or a.get('address') or a.get('id'))


def _t_balance(a):
    return _client(a).balance(a['address'], a.get('coin_type') or SUI_TYPE)


def _t_portfolio(a):
    c = _client(a)
    return c.portfolio(a['address'], min_usd=a.get('min_usd', 0.01),
                       include_dust=bool(a.get('include_dust')),
                       limit=a.get('limit', 100))


def _t_objects(a):
    c = _client(a)
    return c.objects(a['address'], type=a.get('type'), limit=a.get('limit', 50),
                     cursor=a.get('cursor'))


def _t_object(a):
    return _client(a).object(a.get('object_id') or a.get('id'))


def _t_coin(a):
    return _client(a).coin(a.get('coin_type') or a.get('coin') or a.get('symbol'))


def _t_price(a):
    return _client(a).price(a.get('ids') or a.get('coins') or a.get('symbol'))


def _t_history(a):
    c = _client(a)
    return c.history(a['address'], limit=a.get('limit', 20), cursor=a.get('cursor'),
                     direction=(a.get('direction') or 'both').lower(),
                     detail=bool(a.get('detail')))


def _t_tx(a):
    return _client(a).tx(a.get('digest') or a.get('tx'), events=bool(a.get('events')))


def _t_network(a):
    return _client(a).status()


def _t_validators(a):
    return _client(a).validators(limit=a.get('limit', 20),
                                 sort=(a.get('sort') or 'stake').lower())


def _t_stake(a):
    return _client(a).stakes(a['address'])


def _t_package(a):
    c = _client(a)
    return c.package(a.get('package') or a.get('package_id'), module=a.get('module'),
                     limit=a.get('limit', 40))


def _t_wallet(a):
    action = (a.get('action') or 'list').lower()
    if action in ('list', 'ls'):
        return K.wallets()
    if action in ('create', 'new', 'import'):
        import datetime
        if action == 'import' and not a.get('secret'):
            raise SuiError('import needs secret= (a suiprivkey1… string, base64 '
                           'flag||seed, hex, or a path to a sui.keystore file)')
        return K.create(a.get('name') or ('imported' if action == 'import'
                                          else 'default'),
                        secret=a.get('secret'), make_default=a.get('default'),
                        overwrite=bool(a.get('overwrite')),
                        created=datetime.datetime.now().isoformat(timespec='seconds'))
    if action in ('remove', 'rm', 'delete'):
        if not a.get('name'):
            raise SuiError('remove needs name=')
        return K.remove(a['name'])
    if action == 'default':
        if not a.get('name') and not a.get('default'):
            raise SuiError('default needs name=')
        return K.set_default(a.get('name') or a.get('default'))
    if action == 'export':
        return K.export(a.get('name'))
    raise SuiError(f'unknown wallet action {action!r} — list, create, import, '
                   'remove, default, export')


def _t_transfer(a):
    c = _client(a)
    return c.transfer(a['to'], a['amount'], coin_type=a.get('coin_type') or SUI_TYPE,
                      wallet=a.get('wallet'), secret=a.get('secret'),
                      confirm=bool(a.get('confirm')), dry_run=bool(a.get('dry_run')))


def _t_faucet(a):
    a.setdefault('network', 'testnet')
    return _client(a).faucet(a.get('address'), wallet=a.get('wallet'))


def _t_rpc(a):
    return _client(a).rpc(a['method'], a.get('params'))


# ── registry ─────────────────────────────────────────────────────

TOOLS = {
    'sui_what': {
        'description': 'What a string IS. On Sui an account address and an object '
                       'ID are both 32 bytes of hex with nothing to tell them '
                       'apart, so guessing from shape is impossible — this asks '
                       'the chain. Identifies addresses, objects, coins, NFTs, '
                       'packages, shared objects, transaction digests (base58) '
                       'and SuiNS names, and returns the detail that fits '
                       'whichever it turned out to be. Start here with anything '
                       'you did not generate yourself.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('an address, object ID, coin type, transaction digest, '
                          'or a name like example.sui'), **_COMMON},
            'required': ['query']},
        'handler': _t_what,
    },
    'sui_balance': {
        'description': 'One coin type for one address, or several addresses '
                       'comma-separated, with the USD value. SUI by default. This '
                       'is one coin and one coin only — for everything an address '
                       'holds use sui_portfolio, and remember staked SUI shows up '
                       'in neither until you call sui_stake.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('one address, or several separated by commas'),
            'coin_type': _COIN, **_COMMON}, 'required': ['address']},
        'handler': _t_balance,
    },
    'sui_portfolio': {
        'description': 'Everything an address holds, priced and sorted by USD: '
                       'every coin type, plus staked SUI and its accrued rewards, '
                       'plus a count of owned objects. Dust below min_usd is '
                       'counted and excluded rather than padding the list, and '
                       'coins with no market are listed with usd=null and add '
                       'nothing to the total — the total is what could be sold, '
                       'not what is nominally held.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR,
            'min_usd': _num('hide positions worth less than this (default 0.01)'),
            'include_dust': _bool('list every position regardless of value'),
            'limit': _num('maximum coin rows to return (default 100)'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_portfolio,
    },
    'sui_objects': {
        'description': 'Objects an address owns — NFTs with their display name and '
                       'image, coin objects, capabilities, receipts. Sui puts '
                       'everything in objects, so this is where an NFT, a game '
                       'item, an admin capability or a liquidity position lives. '
                       'Filter with type= to ask for one struct type.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR,
            'type': _str('only objects of this struct type, e.g. '
                         '0x2::coin::Coin<0x2::sui::SUI>'),
            'limit': _num('up to 50 per page (default 50)'),
            'cursor': _str('next_cursor from a previous page'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_objects,
    },
    'sui_object': {
        'description': 'One object in full: its type, version, digest, contents, '
                       'display fields, and — the part that matters — how it is '
                       'OWNED. Address-owned objects take Sui\'s fast path; shared '
                       'objects need consensus; immutable objects can never change '
                       'again; object-owned ones are dynamic fields or wrapped '
                       'items and are not directly usable.',
        'inputSchema': {'type': 'object', 'properties': {
            'object_id': _str('the object ID'), **_COMMON},
            'required': ['object_id']},
        'handler': _t_object,
    },
    'sui_coin': {
        'description': 'A coin type in full: decimals, name, total supply, market '
                       'cap, price, 24h change, pool liquidity and the deepest DEX. '
                       'Accepts a symbol, but only SUI, USDC and USDT are pinned — '
                       'anything else resolves to the deepest-liquidity match and '
                       'returns the coin type it picked, which you should read '
                       'back before trusting the number.',
        'inputSchema': {'type': 'object', 'properties': {
            'coin_type': _COIN, **_COMMON}, 'required': ['coin_type']},
        'handler': _t_coin,
    },
    'sui_price': {
        'description': 'USD price and 24h change for coins, by coin type or symbol, '
                       'comma-separated. Prices come from Sui AMM pools via '
                       'DexScreener. A coin with no indexed pool comes back with '
                       'usd=null and priced=false — that means no market was found, '
                       'never that the price is zero.',
        'inputSchema': {'type': 'object', 'properties': {
            'ids': _str('coin types or symbols, comma-separated — e.g. "SUI,DEEP" '
                        'or a full 0x…::module::NAME'), **_COMMON},
            'required': ['ids']},
        'handler': _t_price,
    },
    'sui_history': {
        'description': 'Recent transactions for an address, newest first, with the '
                       'net balance change FOR THAT ADDRESS on each one — the fast '
                       'way to answer "what has this wallet been doing". Sui\'s '
                       'filters are one-sided, so by default this queries both '
                       'sent and received and merges them; narrow with '
                       'direction=from or direction=to.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR,
            'limit': _num('up to 50 (default 20)'),
            'direction': _str('both (default), from, or to',
                              enum=['both', 'from', 'to']),
            'detail': _bool('include the transaction inputs'),
            'cursor': _str('next_cursor from a previous page'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_history,
    },
    'sui_tx': {
        'description': 'One transaction, decoded: success or the failure reason, '
                       'net balance change per owner with USD values, which Move '
                       'commands ran and what they targeted, objects created and '
                       'mutated, gas actually paid. Digests are base58 (~44 '
                       'characters, never 0x-prefixed).',
        'inputSchema': {'type': 'object', 'properties': {
            'digest': _str('the transaction digest, base58'),
            'events': _bool('include emitted events and their parsed JSON'),
            **_COMMON}, 'required': ['digest']},
        'handler': _t_tx,
    },
    'sui_network': {
        'description': 'The state of the chain: epoch and how far through it is, '
                       'checkpoint height, measured TPS, reference gas price, '
                       'validator count, total stake, protocol version and the SUI '
                       'price. Also reports which RPC endpoint answered.',
        'inputSchema': {'type': 'object', 'properties': dict(_COMMON)},
        'handler': _t_network,
    },
    'sui_validators': {
        'description': 'The validator set by stake, with each one\'s APY, '
                       'commission and share — and the Nakamoto coefficient, which '
                       'is how few validators would have to collude or fail '
                       'together to stop the chain (Sui needs two thirds of stake '
                       'to make progress, so holding one third is enough to halt '
                       'it).',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('how many to return (default 20)'),
            'sort': _str('stake (default) or apy', enum=['stake', 'apy']),
            **_COMMON}},
        'handler': _t_validators,
    },
    'sui_stake': {
        'description': 'Delegated SUI for an address: each position, its validator, '
                       'principal, estimated reward and whether it is active or '
                       'still pending. Staked SUI lives in a StakedSui object and '
                       'appears in NO balance call, so an address can look nearly '
                       'empty and control a large position.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR, **_COMMON}, 'required': ['address']},
        'handler': _t_stake,
    },
    'sui_package': {
        'description': 'What a published package can DO. Move keeps its interface '
                       'on chain, so unlike EVM bytecode you can read a contract\'s '
                       'modules and their callable functions with full type '
                       'signatures before calling anything. Without module= it '
                       'lists the modules; with module= it returns that module\'s '
                       'public and entry functions, their parameters and returns.',
        'inputSchema': {'type': 'object', 'properties': {
            'package': _str('the package ID — 0x2 is the Sui framework'),
            'module': _str('one module, for its function signatures'),
            'limit': _num('maximum functions to return (default 40)'),
            **_COMMON}, 'required': ['package']},
        'handler': _t_package,
    },
    'sui_wallet': {
        'description': 'The off-tree keystore at ~/.mod/sui/keys.json: list, '
                       'create, import, remove, default, export. Imports accept '
                       'every shape a Sui secret travels in — a suiprivkey1… '
                       'string, the base64 flag||seed from sui.keystore, hex, or a '
                       'path to a keystore file. Only action=export ever returns a '
                       'secret. Write-gated.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('list (default), create, import, remove, default, export',
                           enum=['list', 'create', 'import', 'remove', 'default',
                                 'export']),
            'name': _str('the wallet name'),
            'secret': _str('for import — suiprivkey1…, base64, hex, or a file path'),
            'default': _bool('make this the default wallet'),
            'overwrite': _bool('replace an existing wallet of the same name'),
            **_COMMON}},
        'handler': _t_wallet,
    },
    'sui_transfer': {
        'description': 'Send SUI, or any coin type with coin_type=, signed on this '
                       'box. The transaction is ALWAYS simulated first: that is '
                       'how the gas budget is set and how a doomed transfer is '
                       'caught before it costs anything. dry_run=true stops there '
                       f'and signs nothing. Anything worth more than ${SPEND_USD:,.0f} '
                       'comes back as needs_confirm with a full plan and moves '
                       'nothing until you call again with confirm=true. The '
                       'recipient may be a SuiNS name.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _str('recipient address, or a SuiNS name like example.sui'),
            'amount': _num('in whole coins (0.5 means half a SUI), not base units'),
            'coin_type': _COIN,
            'wallet': _str('which keystore wallet signs (default: the default one)'),
            'secret': _str('sign with this key instead of anything in the keystore'),
            'dry_run': _bool('simulate and report, signing and sending nothing'),
            'confirm': _bool('proceed past the value guard'),
            **_COMMON}, 'required': ['to', 'amount']},
        'handler': _t_transfer,
    },
    'sui_faucet': {
        'description': 'Test SUI from the testnet or devnet faucet. There is no '
                       'mainnet faucet. The faucet rate-limits by source IP, so a '
                       '429 here is this box being throttled rather than the '
                       'address being refused.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('who to fund (default: the keystore default wallet)'),
            'wallet': _str('fund this keystore wallet by name'),
            'network': _str('testnet (default) or devnet',
                            enum=['testnet', 'devnet'])}},
        'handler': _t_faucet,
    },
    'sui_rpc': {
        'description': 'Any Sui JSON-RPC method, raw — the escape hatch for '
                       'anything not wrapped above (dynamic fields, checkpoints, '
                       'events, move function argument types). Note that Mysten\'s '
                       'public fullnodes no longer serve JSON-RPC at all; this '
                       'goes to the third-party pool or to your rpc= override.',
        'inputSchema': {'type': 'object', 'properties': {
            'method': _str('e.g. suix_getDynamicFields'),
            'params': {'description': 'positional params, as a JSON array',
                       'type': ['array', 'string']},
            **_COMMON}, 'required': ['method']},
        'handler': _t_rpc,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot diverge."""
    tool = TOOLS.get(name)
    if not tool:
        raise SuiError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise SuiError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(out, default=str, indent=2)}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except SuiError as e:
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
            'serverInfo': {'name': 'sui', 'version': version()},
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
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50740)))
    else:
        serve_stdio()
