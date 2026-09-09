#!/usr/bin/env python3
"""near mcp — twelve read tools for the NEAR protocol.

NEAR's account model is its personality: accounts are human-readable names
(`alice.near`) or 64-hex implicit accounts, an account and a contract are the
same thing, and access keys carry per-contract permissions. The tools follow
that grain — near_account for who, near_contract for what it can be asked
(parsed from the WASM export section, since NEAR keeps no ABI on chain), and
near_view to actually ask it.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50910 # Streamable HTTP — POST /mcp

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

from chain import NETWORKS, Client, NearError, is_account_id   # noqa: E402
import wallet                                                  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'NEAR Protocol, whole. Accounts are names (alice.near, app.testnet) or '
    '64-hex implicit accounts, and an account and a contract are the same '
    'object — start with near_account for balances (liquid, staked, storage-'
    'reserved, USD), then near_keys for its access keys and their per-contract '
    'permissions. If it is a contract, near_contract lists its callable methods '
    'parsed straight from the deployed WASM (NEAR stores no ABI), and near_view '
    'calls any view method with JSON args. near_ft reads a NEP-141 token; '
    'near_history and near_tx cover transactions; near_network, '
    'near_validators and near_block cover the chain; near_rpc is the raw '
    'escape hatch. '
    'The write half signs with keys from a keystore on the host: near_wallet '
    'manages keys (generate/import/select), near_create_account mints a '
    'funded *.testnet account via the faucet or a sub-account of the signer, '
    'near_deploy ships a compiled .wasm to the signer\'s own account '
    '(redeploying is how you upgrade) with an optional init call in the same '
    'transaction, near_call sends a signed change call with gas and deposit, '
    'near_send transfers NEAR, near_key adds or deletes access keys. Write '
    'tools default to network=testnet, and A NON-TESTNET WRITE REFUSES '
    'WITHOUT confirm=true — that is deliberate; do not set it unless the '
    'human asked for a mainnet action in so many words. Remote (HTTP) callers '
    'additionally need token= from ~/.mod/near/token on the host; stdio '
    'callers are local and exempt. Secret keys never leave the keystore.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc):
    return {'type': 'number', 'description': desc}


_NET = _str('mainnet (default) or testnet, or a full RPC url',
            enum=list(NETWORKS) + ['custom'])
_RPC = _str('override the RPC endpoint for this call — your own node skips '
            'the public pool')
_ACCT = _str('a NEAR account ID — a name like alice.near or 64 hex chars '
             'for an implicit account')
_COMMON = {'network': _NET, 'rpc': _RPC}


def _client(args):
    return Client(network=args.get('network'), rpc=args.get('rpc'))


TOOLS = {
    'near_account': {
        'description': 'An account: total/liquid/staked balance in NEAR and USD, '
                       'storage used and the NEAR it locks, whether a contract is '
                       'deployed. On NEAR an account IS its contract, so this is '
                       'the first call for any name you are handed.',
        'inputSchema': {'type': 'object',
                        'properties': {'account_id': _ACCT, **_COMMON},
                        'required': ['account_id']},
        'handler': lambda a: _client(a).account(a['account_id']),
    },
    'near_keys': {
        'description': 'The access keys on an account — FullAccess keys, and '
                       'FunctionCall keys with the contract, methods and gas '
                       'allowance each is limited to. This is how NEAR does '
                       'session auth, so reading it tells you which apps an '
                       'account has signed into.',
        'inputSchema': {'type': 'object',
                        'properties': {'account_id': _ACCT, **_COMMON},
                        'required': ['account_id']},
        'handler': lambda a: _client(a).keys(a['account_id']),
    },
    'near_contract': {
        'description': 'What a contract can be asked to do: its callable methods, '
                       'parsed from the export section of the deployed WASM — '
                       'NEAR keeps no ABI on chain, but every method is an '
                       'exported function. Follow up with near_view to call one.',
        'inputSchema': {'type': 'object',
                        'properties': {'account_id': _ACCT, **_COMMON},
                        'required': ['account_id']},
        'handler': lambda a: _client(a).contract(a['account_id']),
    },
    'near_view': {
        'description': 'Call a view method on a contract with JSON args and get '
                       'the decoded result plus any logs. Free, no key, no gas. '
                       'A change method called this way refuses — that is how '
                       'you tell them apart.',
        'inputSchema': {'type': 'object', 'properties': {
            'contract': _ACCT,
            'method': _str('the method name, e.g. ft_balance_of'),
            'args': {'description': 'arguments as a JSON object',
                     'type': ['object', 'string']},
            **_COMMON}, 'required': ['contract', 'method']},
        'handler': lambda a: _client(a).view(a['contract'], a['method'],
                                             a.get('args')),
    },
    'near_ft': {
        'description': 'A NEP-141 fungible token by contract: symbol, name, '
                       'decimals, total supply — and an account\'s balance '
                       'scaled by those decimals if account_id is given. '
                       'usdt.tether-token.near and wrap.near are the classics.',
        'inputSchema': {'type': 'object', 'properties': {
            'contract': _str('the token contract, e.g. wrap.near'),
            'account_id': _ACCT, **_COMMON}, 'required': ['contract']},
        'handler': lambda a: _client(a).ft(a['contract'], a.get('account_id')),
    },
    'near_history': {
        'description': 'Recent transactions for an account, newest first, via the '
                       'NearBlocks indexer — the RPC layer has no by-account '
                       'query at all. The free indexer rate-limits by IP, so a '
                       '502 here means throttled, not missing.',
        'inputSchema': {'type': 'object', 'properties': {
            'account_id': _ACCT,
            'limit': _num('how many, max 25 (the indexer page size)'),
            **_COMMON}, 'required': ['account_id']},
        'handler': lambda a: _client(a).history(a['account_id'],
                                                a.get('limit') or 25),
    },
    'near_tx': {
        'description': 'One transaction, decoded: actions with amounts in NEAR '
                       'and function-call args, success or the exact failure, '
                       'total fee burnt. The RPC needs the sender to find the '
                       'shard; omit sender= and the indexer resolves it.',
        'inputSchema': {'type': 'object', 'properties': {
            'hash': _str('the transaction hash, base58'),
            'sender': _str('the signer account — optional, resolved via the '
                           'indexer when omitted'),
            **_COMMON}, 'required': ['hash']},
        'handler': lambda a: _client(a).tx(a['hash'], a.get('sender')),
    },
    'near_block': {
        'description': 'A block by height or hash, or the latest final block '
                       'when neither is given: height, time, author, gas price, '
                       'chunk count.',
        'inputSchema': {'type': 'object', 'properties': {
            'block_id': _str('height (a number) or block hash — omit for the '
                             'latest final block'),
            **_COMMON}},
        'handler': lambda a: _client(a).block(a.get('block_id')),
    },
    'near_network': {
        'description': 'The state of the chain in one call: chain id, latest '
                       'block height and time, protocol version, gas price, '
                       'validator count, total stake and the NEAR price.',
        'inputSchema': {'type': 'object', 'properties': _COMMON},
        'handler': lambda a: _client(a).status(),
    },
    'near_validators': {
        'description': 'The current validator set by stake: each validator\'s '
                       'stake, share of the total and block-production uptime, '
                       'plus the Nakamoto coefficient — how few validators '
                       'control a third of stake.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('rows to return (default 30, max 200)'), **_COMMON}},
        'handler': lambda a: _client(a).validators(a.get('limit') or 30),
    },
    'near_price': {
        'description': 'NEAR/USD spot, 24h change and market cap, from '
                       'CoinGecko, cached briefly.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': lambda a: Client().price(),
    },
    'near_rpc': {
        'description': 'Any NEAR JSON-RPC method, raw — the escape hatch for '
                       'anything not wrapped above (EXPERIMENTAL_changes, '
                       'chunk, light client proofs). Method names and params '
                       'as docs.near.org/api/rpc describes them.',
        'inputSchema': {'type': 'object', 'properties': {
            'method': _str('e.g. EXPERIMENTAL_protocol_config'),
            'params': {'description': 'params — a JSON object or array',
                       'type': ['object', 'array', 'string']},
            **_COMMON}, 'required': ['method']},
        'handler': lambda a: _client(a).rpc(a['method'], a.get('params')),
    },
}

# ── write tools — signed, gated, testnet by default ──────────────

_CONFIRM = {'type': 'boolean', 'description':
            'required true for any non-testnet write — it spends real NEAR. '
            'Do not set it unless the human asked for a mainnet action.'}
_TOKEN = _str('remote-write token from ~/.mod/near/token on the host — '
              'HTTP callers only, local stdio callers are exempt')
_WNET = _str('testnet (default for writes) or mainnet', enum=list(NETWORKS))
_SIGNER = _str('which stored account signs — omit for the network default')
_WRITE_COMMON = {'network': _WNET, 'rpc': _RPC, 'confirm': _CONFIRM,
                 'token': _TOKEN}

WRITE_TOOLS = {
    'near_wallet': {
        'description': 'The local keystore (~/.mod/near/, never the repo): '
                       'op=status lists accounts (public halves only — no '
                       'tool returns a secret key), op=generate mints a '
                       'keypair (named, or an implicit hex account), '
                       'op=import takes a NEAR CLI secret key '
                       '(ed25519:base58), op=select picks the default signer '
                       'per network, op=forget drops a key from the store.',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('status | generate | import | select | forget',
                       enum=['status', 'generate', 'import', 'select', 'forget']),
            'account_id': _ACCT,
            'secret_key': _str('ed25519:base58 secret key (op=import)'),
            'network': _WNET, 'token': _TOKEN}},
        'handler': lambda a: wallet.wallet(a.get('op') or 'status',
                                           a.get('account_id'),
                                           a.get('secret_key'),
                                           a.get('network') or 'testnet'),
    },
    'near_create_account': {
        'description': 'A new named account. A fresh *.testnet name is minted '
                       'and funded (~10 N) by the public faucet — free, no '
                       'signer needed. Otherwise the name must be a '
                       'sub-account of the signer (app.you.near from '
                       'you.near), created, keyed and funded in one '
                       'transaction. Either way the new key lands in the '
                       'keystore ready to sign.',
        'inputSchema': {'type': 'object', 'properties': {
            'new_account_id': _str('the account to create, e.g. myapp.testnet'),
            'initial_near': _num('NEAR to fund a sub-account with (default 0.1)'),
            'public_key': _str('start the account with this key instead of '
                               'generating one (ed25519:base58)'),
            'account_id': _SIGNER, **_WRITE_COMMON},
            'required': ['new_account_id']},
        'handler': lambda a: wallet.create_account(
            a['new_account_id'], a.get('initial_near') or 0.1,
            a.get('public_key'), a.get('account_id'),
            a.get('network') or 'testnet', a.get('rpc'),
            bool(a.get('confirm'))),
    },
    'near_deploy': {
        'description': 'Deploy a compiled contract to the signer\'s OWN '
                       'account — on NEAR an account is its contract, and '
                       'deploying over old code is how you upgrade in place '
                       '(state survives). Takes the .wasm as base64 (wasm=), '
                       'a path on the host (wasm_path=) or a URL (wasm_url=), '
                       'plus an optional init method batched into the same '
                       'transaction. Follow with near_contract to see the '
                       'methods the chain now serves.',
        'inputSchema': {'type': 'object', 'properties': {
            'wasm': _str('the compiled .wasm, base64'),
            'wasm_path': _str('path to the .wasm on this host'),
            'wasm_url': _str('URL to fetch the .wasm from'),
            'init_method': _str('optional init to call in the same tx, '
                                'e.g. new'),
            'init_args': {'description': 'JSON args for the init call',
                          'type': ['object', 'string']},
            'account_id': _SIGNER, **_WRITE_COMMON}},
        'handler': lambda a: wallet.deploy(
            a.get('wasm'), a.get('wasm_path'), a.get('wasm_url'),
            a.get('account_id'), a.get('init_method'), a.get('init_args'),
            a.get('network') or 'testnet', a.get('rpc'),
            bool(a.get('confirm'))),
    },
    'near_call': {
        'description': 'A signed change call on any contract: method, JSON '
                       'args, gas (Tgas, default 100, max 300) and an '
                       'attached deposit in NEAR. Returns the decoded result, '
                       'logs, fee and explorer link. For free reads use '
                       'near_view instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'contract': _ACCT,
            'method': _str('the change method, e.g. set_status'),
            'args': {'description': 'arguments as a JSON object',
                     'type': ['object', 'string']},
            'gas_tgas': _num('gas in Tgas (default 100, max 300)'),
            'deposit_near': _num('NEAR attached to the call (default 0)'),
            'account_id': _SIGNER, **_WRITE_COMMON},
            'required': ['contract', 'method']},
        'handler': lambda a: wallet.call(
            a['contract'], a['method'], a.get('args'), a.get('gas_tgas'),
            a.get('deposit_near') or 0, a.get('account_id'),
            a.get('network') or 'testnet', a.get('rpc'),
            bool(a.get('confirm'))),
    },
    'near_send': {
        'description': 'Transfer NEAR from a stored account to any account.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _ACCT, 'amount_near': _num('amount in NEAR'),
            'account_id': _SIGNER, **_WRITE_COMMON},
            'required': ['to', 'amount_near']},
        'handler': lambda a: wallet.send(
            a['to'], a['amount_near'], a.get('account_id'),
            a.get('network') or 'testnet', a.get('rpc'),
            bool(a.get('confirm'))),
    },
    'near_key': {
        'description': 'Access keys on the signer\'s account — NEAR\'s '
                       'session auth. op=add grants FullAccess, or scope it '
                       'with contract= (+ methods=, allowance_near=) for a '
                       'FunctionCall key; op=delete revokes one. Deleting '
                       'the signing key itself needs confirm=true — it locks '
                       'you out.',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('add or delete', enum=['add', 'delete']),
            'public_key': _str('the key, ed25519:base58'),
            'contract': _str('scope an added key to this contract '
                             '(FunctionCall permission)'),
            'methods': _str('comma-separated method names the scoped key may '
                            'call (default: any)'),
            'allowance_near': _num('gas allowance in NEAR for a scoped key'),
            'account_id': _SIGNER, **_WRITE_COMMON},
            'required': ['op', 'public_key']},
        'handler': lambda a: wallet.key(
            a['op'], a.get('public_key'), a.get('contract'), a.get('methods'),
            a.get('allowance_near'), a.get('account_id'),
            a.get('network') or 'testnet', a.get('rpc'),
            bool(a.get('confirm'))),
    },
}
TOOLS.update(WRITE_TOOLS)


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, local=False):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot diverge. A write tool called over HTTP (local=False)
    must present the token from ~/.mod/near/token; local callers — stdio MCP,
    `m near/...` — hold the keystore anyway, so the token proves nothing."""
    tool = TOOLS.get(name)
    if not tool:
        raise NearError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    if name in WRITE_TOOLS:
        token = args.pop('token', None)
        writes = name != 'near_wallet' or \
            (args.get('op') or 'status') not in ('status', 'list')
        if not local and writes:
            wallet.check_token(token)
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise NearError(f'{name} needs {required}')
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
    except NearError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
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
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'near', 'version': version()},
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
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50910)))
    else:
        serve_stdio()
