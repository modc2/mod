#!/usr/bin/env python3
"""algorand mcp — the Algorand protocol as MCP tools, read and write.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                      # stdio — one JSON message per line
    python3 mcp.py --http --port 51340  # Streamable HTTP — POST /mcp

The API server mounts `handle()` at /mcp too, so the tools, the REST routes
and the console can never drift apart.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us afterwards.
    sys.path.append(HERE)

import codec                                                   # noqa: E402
from chain import NETWORKS, AlgoError, Client, price_usd        # noqa: E402
import wallet                                                  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Algorand, whole. Accounts are 58-char base32 addresses or NFD names '
    '(alice.algo). algo_account is the first call for anyone: ALGO balance, '
    'the minimum balance locked by their opt-ins, spendable, USD, every ASA '
    'they hold, apps they opted into or created, and whether the account is '
    'rekeyed. Assets (ASAs) are numbered: algo_assets finds them by name or '
    'unit, algo_asset reads one (and a holder\'s balance). Smart contracts '
    'are numbered applications: algo_app reads global state decoded, boxes, '
    'its own account and optionally the disassembled TEAL; algo_box and '
    'algo_local_state read the rest. algo_simulate calls any app method '
    'with no key and no fee (ARC-4 signature like "add(uint64,uint64)uint64" '
    'or raw args) and returns the logs and decoded return — use it before '
    'any signed call. algo_tx, algo_history, algo_block, algo_network and '
    'algo_price cover the chain; algo_compile turns TEAL into bytecode; '
    'algo_rpc is the raw escape hatch (any algod or indexer GET). '
    'The write half signs with keys from a keystore on the host: algo_wallet '
    'generates/imports/selects keys (never returns a secret), algo_send moves '
    'ALGO, algo_asset_create mints an ASA, algo_asset_optin opts in (an '
    'account must opt in before it can receive an asset), algo_asset_send '
    'transfers one, algo_app_create deploys TEAL, algo_app_call signs an app '
    'call (pay_algo= funds it atomically), algo_app_update upgrades. Writes '
    'default to testnet; any other network refuses without confirm=true; '
    'over HTTP a write also needs token=.'
)

NET = {'type': 'string', 'enum': list(NETWORKS),
       'description': 'mainnet (default for reads), testnet (default for writes), betanet'}
WNET = {**NET, 'description': 'testnet (default) | mainnet — mainnet needs confirm=true'}
CONFIRM = {'type': 'boolean', 'description': 'required to sign anywhere but testnet'}
ACCOUNT = {'type': 'string', 'description': 'keystore address to sign with (default: selected)'}
WHO = {'type': 'string', 'description': 'an Algorand address or an NFD name like alice.algo'}
LIST = {'description': 'list, JSON array or comma-separated string'}


def _t(desc, props=None, required=(), handler=None):
    return {'description': desc,
            'inputSchema': {'type': 'object', 'properties': props or {},
                            'required': list(required)},
            'handler': handler}


def _c(a):
    return Client(a.get('network'))


def _rpc(a):
    path = a['path']
    if not path.startswith('/'):
        path = '/' + path
    if not path.startswith('/v2/') and path not in ('/health', '/versions', '/genesis'):
        raise AlgoError('path must be an algod/indexer /v2/... path')
    c = _c(a)
    params = a.get('params') or {}
    if isinstance(params, str):
        params = json.loads(params)
    return c.idx(path, **params) if a.get('indexer') else c.get(path, **params)


def _compile(a):
    r = _c({'network': a.get('network') or 'testnet'}).compile(a['source'])
    return {'hash': r['hash'], 'b64': r['b64'], 'bytes': len(r['program']),
            'logic_address': codec.logic_address(r['program'])}


def _w(fn, *keys):
    """Pass only the keys a write fn takes; the rest of the args are noise."""
    def run(a):
        return fn(**{k: v for k, v in a.items() if k in keys and v not in (None, '')})
    return run


TOOLS = {
    'algo_account': _t(
        'One account: ALGO balance, min-balance, spendable, USD, ASA holdings, '
        'apps opted-in/created, rekey, NFD name.',
        {'address': WHO, 'network': NET}, ['address'],
        lambda a: _c(a).account(a['address'])),
    'algo_resolve': _t(
        'NFD name → address, or address → its primary NFD name.',
        {'name': WHO, 'network': NET}, ['name'],
        lambda a: (lambda c, x: {'input': a['name'], 'address': x,
                                 'name': c.name_of(x)})(_c(a), _c(a).resolve(a['name']))),
    'algo_asset': _t(
        'An ASA by id (or USDC/USDT…): name, unit, decimals, supply, roles; '
        'holder= adds that account\'s balance and opt-in state.',
        {'asset': {'type': 'string'}, 'holder': WHO, 'network': NET}, ['asset'],
        lambda a: _c(a).asset(a['asset'], a.get('holder'))),
    'algo_assets': _t(
        'Search ASAs by name, unit, id or creator (indexer).',
        {'q': {'type': 'string'}, 'creator': WHO, 'limit': {'type': 'integer'},
         'network': NET}, [],
        lambda a: _c(a).assets(a.get('q'), a.get('creator'), a.get('limit') or 20)),
    'algo_app': _t(
        'A smart contract (application): creator, its account + balance, '
        'decoded global state, schema, boxes; program=true adds TEAL.',
        {'app_id': {'type': 'integer'}, 'program': {'type': 'boolean'}, 'network': NET},
        ['app_id'], lambda a: _c(a).app(a['app_id'], a.get('program'))),
    'algo_box': _t(
        'Read one box of an app. name= is text, or "b64:…", "addr:…", "int:…".',
        {'app_id': {'type': 'integer'}, 'name': {'type': 'string'}, 'network': NET},
        ['app_id', 'name'], lambda a: _c(a).box(a['app_id'], a['name'])),
    'algo_local_state': _t(
        'An account\'s local state in an app (and whether it opted in).',
        {'address': WHO, 'app_id': {'type': 'integer'}, 'network': NET},
        ['address', 'app_id'], lambda a: _c(a).local_state(a['address'], a['app_id'])),
    'algo_simulate': _t(
        'Call an app WITHOUT signing or paying: algod simulates it. method= is '
        'an ARC-4 signature ("get(address)uint64") with args as values; without '
        'method, args are raw (ints, "str:x", "b64:…", "addr:…"). Returns '
        'logs, decoded return, failure, budget, global state delta.',
        {'app_id': {'type': 'integer'}, 'method': {'type': 'string'},
         'args': LIST, 'sender': WHO, 'accounts': LIST, 'apps': LIST,
         'assets': LIST, 'boxes': LIST, 'on_complete': {'type': 'string'},
         'pay_algo': {'type': 'number'}, 'network': NET}, ['app_id'],
        _w(wallet.simulate_call, 'app_id', 'method', 'args', 'sender', 'accounts',
           'apps', 'assets', 'boxes', 'on_complete', 'pay_algo', 'network')),
    'algo_tx': _t(
        'One transaction by id — indexed history, or the pending pool.',
        {'txid': {'type': 'string'}, 'network': NET}, ['txid'],
        lambda a: _c(a).tx(a['txid'])),
    'algo_history': _t(
        'Recent transactions of an account; kind= pay|axfer|appl|acfg|keyreg, '
        'asset= filters to one ASA; next_token= pages forward.',
        {'address': WHO, 'limit': {'type': 'integer'}, 'kind': {'type': 'string'},
         'asset': {'type': 'integer'}, 'next_token': {'type': 'string'}, 'network': NET},
        ['address'],
        lambda a: _c(a).history(a['address'], a.get('limit') or 25,
                                a.get('kind'), a.get('asset'), a.get('next_token'))),
    'algo_block': _t(
        'A block (latest when round is omitted): proposer, payouts, txn mix.',
        {'round': {'type': 'integer'}, 'network': NET}, [],
        lambda a: _c(a).block(a.get('round'))),
    'algo_network': _t(
        'Chain status: round, protocol version, fee, supply, online stake, price.',
        {'network': NET}, [], lambda a: _c(a).network_info()),
    'algo_price': _t('ALGO/USD, 24h change, market cap.', {}, [],
                     lambda a: price_usd()),
    'algo_compile': _t(
        'Compile TEAL source to bytecode (+ its logic-sig address).',
        {'source': {'type': 'string'}, 'network': NET}, ['source'], _compile),
    'algo_asset_holders': _t(
        'Top holders of an ASA by balance (up to 20 by default).',
        {'asset': {'type': 'string'}, 'limit': {'type': 'integer'}, 'network': NET},
        ['asset'],
        lambda a: _c(a).asset_holders(a['asset'], a.get('limit') or 20)),
    'algo_rpc': _t(
        'Raw GET on algod (default) or the indexer (indexer=true), any /v2 path.',
        {'path': {'type': 'string'}, 'params': {'type': 'object'},
         'indexer': {'type': 'boolean'}, 'network': NET}, ['path'], _rpc),

    # ── writes ────────────────────────────────────────────────────
    'algo_wallet': _t(
        'The keystore: op=status|generate|import|select|forget. Import takes '
        'mnemonic= (25 words). Never returns a secret.',
        {'op': {'type': 'string'}, 'mnemonic': {'type': 'string'},
         'address': {'type': 'string'}, 'label': {'type': 'string'}, 'network': NET},
        [], _w(wallet.wallet, 'op', 'mnemonic', 'address', 'label', 'network')),
    'algo_send': _t(
        'Send ALGO. close_to= also closes the account out to that address.',
        {'to': WHO, 'amount_algo': {'type': 'number'}, 'note': {'type': 'string'},
         'close_to': WHO, 'account': ACCOUNT, 'network': WNET, 'confirm': CONFIRM},
        ['to', 'amount_algo'],
        _w(wallet.send, 'to', 'amount_algo', 'note', 'close_to', 'account',
           'network', 'confirm')),
    'algo_asset_create': _t(
        'Mint an ASA. total is in base units (total=1000000 with decimals=6 is '
        '1.0). Roles default to the signer; "none" locks a role forever.',
        {'name': {'type': 'string'}, 'unit': {'type': 'string'},
         'total': {'type': 'integer'}, 'decimals': {'type': 'integer'},
         'url': {'type': 'string'}, 'default_frozen': {'type': 'boolean'},
         'manager': WHO, 'reserve': WHO, 'freeze': WHO, 'clawback': WHO,
         'note': {'type': 'string'}, 'account': ACCOUNT, 'network': WNET,
         'confirm': CONFIRM}, ['name', 'unit', 'total'],
        _w(wallet.asset_create, 'name', 'unit', 'total', 'decimals', 'url',
           'default_frozen', 'manager', 'reserve', 'freeze', 'clawback', 'note',
           'account', 'network', 'confirm')),
    'algo_asset_optin': _t(
        'Opt the signer into an ASA (required before receiving it; locks 0.1 ALGO).',
        {'asset_id': {'type': 'string'}, 'account': ACCOUNT, 'network': WNET,
         'confirm': CONFIRM}, ['asset_id'],
        _w(wallet.asset_optin, 'asset_id', 'account', 'network', 'confirm')),
    'algo_asset_send': _t(
        'Transfer an ASA. amount is in whole units (decimals applied) unless raw=true.',
        {'asset_id': {'type': 'string'}, 'to': WHO, 'amount': {'type': 'number'},
         'raw': {'type': 'boolean'}, 'note': {'type': 'string'},
         'account': ACCOUNT, 'network': WNET, 'confirm': CONFIRM},
        ['asset_id', 'to', 'amount'],
        _w(wallet.asset_send, 'asset_id', 'to', 'amount', 'raw', 'note', 'account',
           'network', 'confirm')),
    'algo_app_create': _t(
        'Deploy a smart contract from TEAL source (compiled by algod) or '
        'bytecode. Declare the state schema up front — it cannot grow later.',
        {'approval': {'type': 'string', 'description': 'TEAL source'},
         'clear': {'type': 'string', 'description': 'TEAL source (default: approve)'},
         'approval_b64': {'type': 'string'}, 'clear_b64': {'type': 'string'},
         'approval_path': {'type': 'string'}, 'clear_path': {'type': 'string'},
         'global_ints': {'type': 'integer'}, 'global_bytes': {'type': 'integer'},
         'local_ints': {'type': 'integer'}, 'local_bytes': {'type': 'integer'},
         'extra_pages': {'type': 'integer'}, 'method': {'type': 'string'},
         'args': LIST, 'account': ACCOUNT, 'network': WNET, 'confirm': CONFIRM}, [],
        _w(wallet.app_create, 'approval', 'clear', 'approval_b64', 'clear_b64',
           'approval_path', 'clear_path', 'global_ints', 'global_bytes',
           'local_ints', 'local_bytes', 'extra_pages', 'method', 'args',
           'account', 'network', 'confirm')),
    'algo_app_call': _t(
        'A signed app call. method= ARC-4 signature or raw args; on_complete= '
        'noop|optin|closeout|clear|delete; pay_algo= sends ALGO to the app '
        'account in the same atomic group. Simulate first.',
        {'app_id': {'type': 'integer'}, 'method': {'type': 'string'}, 'args': LIST,
         'on_complete': {'type': 'string'}, 'accounts': LIST, 'apps': LIST,
         'assets': LIST, 'boxes': LIST, 'pay_algo': {'type': 'number'},
         'note': {'type': 'string'}, 'account': ACCOUNT, 'network': WNET,
         'confirm': CONFIRM}, ['app_id'],
        _w(wallet.app_call, 'app_id', 'method', 'args', 'on_complete', 'accounts',
           'apps', 'assets', 'boxes', 'pay_algo', 'note', 'account', 'network',
           'confirm')),
    'algo_app_update': _t(
        'Replace an app\'s programs (only if its approval program allows it).',
        {'app_id': {'type': 'integer'}, 'approval': {'type': 'string'},
         'clear': {'type': 'string'}, 'approval_b64': {'type': 'string'},
         'clear_b64': {'type': 'string'}, 'approval_path': {'type': 'string'},
         'clear_path': {'type': 'string'}, 'account': ACCOUNT, 'network': WNET,
         'confirm': CONFIRM}, ['app_id'],
        _w(wallet.app_update, 'app_id', 'approval', 'clear', 'approval_b64',
           'clear_b64', 'approval_path', 'clear_path', 'account', 'network',
           'confirm')),
}

WRITE_TOOLS = {'algo_wallet', 'algo_send', 'algo_asset_create', 'algo_asset_optin',
               'algo_asset_send', 'algo_app_create', 'algo_app_call', 'algo_app_update'}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, local=False):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot diverge. A write over HTTP (local=False) must present
    the token from ~/.mod/algorand/token; local callers hold the keystore
    anyway, so the token proves nothing there."""
    tool = TOOLS.get(name)
    if not tool:
        raise AlgoError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    if name in WRITE_TOOLS:
        token = args.pop('token', None)
        writes = name != 'algo_wallet' or (args.get('op') or 'status') not in ('status', 'list')
        if not local and writes:
            wallet.check_token(token)
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise AlgoError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params, local=False):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args, local=local)
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(out, default=str, indent=2)}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except AlgoError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body, local=False):
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
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'algorand', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, local=local)
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
            resp = handle(body, local=True)   # stdio is the host itself
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 51340)))
    else:
        serve_stdio()
