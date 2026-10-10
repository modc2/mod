#!/usr/bin/env python3
"""starknet mcp — Starknet and its STRK20 privacy pool as MCP tools.

Starknet keeps every contract's ABI on chain, so the tools lean on that:
starknet_contract says what a contract is, starknet_read calls any function
by name with typed JSON args and decodes the answer, starknet_events
unpacks its event tape. The strk20_* tools are the same machinery pointed
at the privacy pool (https://strk20.starknet.io/docs): its parameters, its
public edges, notes and nullifiers, and the anonymizer contracts it calls
through privacy_invoke.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 api/mcp.py                     # stdio — one JSON message per line
    python3 api/mcp.py --http --port 51020 # Streamable HTTP — POST /mcp

The API server mounts handle() at /mcp, so the tools, the REST routes and
the app share one implementation.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: never shadow a caller's own modules.
    sys.path.append(HERE)

import abi      # noqa: E402
import chain    # noqa: E402
import strk20   # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Starknet, whole, read-only. Every contract publishes its ABI on chain, '
    'so start with starknet_contract for any address (functions, events, '
    'class hash), then starknet_read to call a function by name with JSON '
    'args — structs as objects, enums as {"Variant": payload}, u256 as one '
    'number, arrays as lists — and get the decoded result. starknet_events '
    'decodes a contract\'s event tape. starknet_account / starknet_balance '
    'for wallets; starknet_block, starknet_tx for the chain; starknet_rpc is '
    'the raw JSON-RPC escape hatch. '
    'STRK20 is Starknet\'s privacy pool (encrypted UTXO notes, nullifiers, '
    'Stwo proofs). strk20_pool is its live state; strk20_activity its public '
    'tape (Deposit, Withdrawal, NoteUsed, EncNoteCreated, OpenNoteDeposited, '
    'ExternalContractInvoked); strk20_user whether an address is registered '
    'and its public deposits/withdrawals; strk20_note / strk20_nullifier '
    'check one note or spend. DeFi reaches the pool through anonymizer '
    'contracts exposing privacy_invoke, which returns Span<OpenNoteDeposit>: '
    'strk20_helpers lists every one the pool has called, strk20_helper '
    'inspects one, strk20_invoke_action ABI-encodes the InvokeExternal client '
    'action for one. strk20_docs reads/searches the official docs. Nothing '
    'here signs or submits: private transactions need the user\'s viewing '
    'key and a STARK proof, which belong in their wallet or the Privacy SDK. '
    'Every tool takes network=mainnet|sepolia (mainnet default).'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc):
    return {'type': 'number', 'description': desc}


_NET = _str('mainnet (default) or sepolia', enum=list(chain.NETWORKS))
_ADDR = _str('a Starknet address (0x felt)')
_ARGS = {'description': 'function arguments: an object keyed by parameter '
                        'name, or a positional list. Structs are objects, '
                        'enums "Variant" or {"Variant": payload}, u256 a '
                        'number or 0x string, arrays lists, ByteArray a string.',
         'type': ['object', 'array', 'string']}


def _schema(props=None, required=()):
    return {'type': 'object',
            'properties': {**(props or {}), 'network': _NET},
            'required': list(required)}


def _n(a):
    return a.get('network')


def _tx(a):
    out = {'transaction': chain.tx(a['hash'], network=_n(a))}
    try:
        out['receipt'] = chain.receipt(a['hash'], network=_n(a))
    except chain.RpcError as e:
        out['receipt'] = {'error': str(e)}
    return out


def _encode(a):
    if a.get('contract'):
        return abi.encode(a['contract'], a['function'], a.get('args'),
                          network=_n(a))
    return {'name': a['function'], 'selector': chain.selector(a['function'])}


TOOLS = {
    # ── chain ────────────────────────────────────────────────────
    'starknet_status': {
        'description': 'The network: chain id, head block, RPC spec version '
                       'and which endpoint answered. Cheap liveness check.',
        'inputSchema': _schema(),
        'handler': lambda a: chain.status(network=_n(a)),
    },
    'starknet_block': {
        'description': 'A block by number, hash or "latest": header plus '
                       'transaction hashes (full=true for whole transactions).',
        'inputSchema': _schema({
            'id': _str('latest (default), a block number, or a 0x block hash'),
            'full': {'type': 'boolean',
                     'description': 'include full transactions'}}),
        'handler': lambda a: chain.block(a.get('id') or 'latest',
                                         full=bool(a.get('full')),
                                         network=_n(a)),
    },
    'starknet_tx': {
        'description': 'A transaction and its receipt (status, fee, events, '
                       'messages) by hash.',
        'inputSchema': _schema({'hash': _str('transaction hash')}, ['hash']),
        'handler': _tx,
    },
    'starknet_account': {
        'description': 'One view of an address: ETH and STRK balances, nonce, '
                       'deployed class hash (or that it is undeployed).',
        'inputSchema': _schema({'address': _ADDR}, ['address']),
        'handler': lambda a: chain.account(a['address'], network=_n(a)),
    },
    'starknet_balance': {
        'description': 'ERC-20 balance of an address. token is eth, strk, usdc '
                       'or any token contract address (decimals read live).',
        'inputSchema': _schema({'address': _ADDR,
                                'token': _str('eth (default) | strk | usdc | 0x...')},
                               ['address']),
        'handler': lambda a: chain.balance(a['address'], a.get('token') or 'eth',
                                           network=_n(a)),
    },
    'starknet_contract': {
        'description': 'What a contract is, from the ABI it publishes on '
                       'chain: class hash, every function with its typed '
                       'signature and view/external mutability, its events, '
                       'and whether it is a STRK20 privacy_invoke helper. '
                       'The first call for any contract address.',
        'inputSchema': _schema({'address': _ADDR}, ['address']),
        'handler': lambda a: abi.iface(a['address'], network=_n(a)),
    },
    'starknet_read': {
        'description': 'Call any contract function by name with typed args '
                       '(ABI-encoded for you) and get the decoded result — '
                       'structs, enums, u256, arrays, strings — plus the raw '
                       'felts. Executes against latest state; nothing is '
                       'written, so external functions can be simulated too.',
        'inputSchema': _schema({
            'contract': _ADDR,
            'function': _str('function name, e.g. balance_of'),
            'args': _ARGS,
            'block_id': _str('latest (default), pending, number or hash')},
            ['contract', 'function']),
        'handler': lambda a: abi.read(a['contract'], a['function'],
                                      a.get('args'),
                                      block_id=a.get('block_id') or 'latest',
                                      network=_n(a)),
    },
    'starknet_encode': {
        'description': 'Calldata for a function call without sending it: '
                       'ABI-encodes args into felts plus the entry-point '
                       'selector — what a wallet needs to sign an invoke. '
                       'Without contract, just the starknet_keccak selector '
                       'of the name (offline).',
        'inputSchema': _schema({'contract': _ADDR,
                                'function': _str('function / entry-point name'),
                                'args': _ARGS}, ['function']),
        'handler': _encode,
    },
    'starknet_events': {
        'description': 'A contract\'s events, newest first, decoded by its '
                       'ABI into named fields. name filters to one event '
                       '(e.g. Transfer), keys adds key filters after it (e.g. '
                       'an address that is an indexed field).',
        'inputSchema': _schema({
            'address': _ADDR,
            'name': _str('event name, e.g. Transfer'),
            'keys': {'type': 'array', 'items': {'type': 'string'},
                     'description': 'further key filters, in key order'},
            'limit': _num('events to return (default 20, max 200)'),
            'from_block': _num('page forward from this block instead of '
                               'walking back from the head')},
            ['address']),
        'handler': lambda a: abi.events(a['address'], a.get('name'),
                                        limit=min(int(a.get('limit') or 20), 200),
                                        keys=a.get('keys'),
                                        from_block=a.get('from_block'),
                                        network=_n(a)),
    },
    'starknet_storage': {
        'description': 'One raw storage slot of a contract.',
        'inputSchema': _schema({'address': _ADDR,
                                'key': _str('storage key (felt)')},
                               ['address', 'key']),
        'handler': lambda a: {'value': chain.storage(a['address'], a['key'],
                                                     network=_n(a))},
    },
    'starknet_rpc': {
        'description': 'Raw Starknet JSON-RPC (spec 0.8+), e.g. '
                       'starknet_getStateUpdate, starknet_estimateFee, '
                       'starknet_getClass. The escape hatch when no tool fits.',
        'inputSchema': _schema({'method': _str('starknet_* method'),
                                'params': {'description': 'params list or object',
                                           'type': ['array', 'object']}},
                               ['method']),
        'handler': lambda a: {'result': chain.rpc(a['method'],
                                                  a.get('params') or [],
                                                  network=_n(a))},
    },

    # ── STRK20 privacy pool ──────────────────────────────────────
    'strk20_pool': {
        'description': 'The STRK20 privacy pool, live: address, version, '
                       'paused, fee and fee collector, proof validity window '
                       '(blocks), auditor and screener public keys, and the '
                       'public token balances it holds.',
        'inputSchema': _schema(),
        'handler': lambda a: strk20.state(network=_n(a)),
    },
    'strk20_activity': {
        'description': 'The pool\'s public event tape, newest first, decoded: '
                       'Deposit (who shielded what), Withdrawal (who received '
                       'what), NoteUsed (a nullifier), EncNoteCreated, '
                       'OpenNoteCreated, OpenNoteDeposited (a helper credited '
                       'an open note), ExternalContractInvoked, ViewingKeySet. '
                       'Senders, recipients and amounts inside the pool stay '
                       'encrypted.',
        'inputSchema': _schema({
            'event': _str('filter to one event name'),
            'limit': _num('events to return (default 20, max 200)')}),
        'handler': lambda a: strk20.activity(a.get('event'),
                                             limit=min(int(a.get('limit') or 20), 200),
                                             network=_n(a)),
    },
    'strk20_user': {
        'description': 'One address and the pool: registered (has a viewing '
                       'key), its public viewing key, incoming channel count, '
                       'open-note screening policy, and its public edges — '
                       'deposits it made and withdrawals paid to it.',
        'inputSchema': _schema({'address': _ADDR,
                                'limit': _num('edges per side (default 10)')},
                               ['address']),
        'handler': lambda a: strk20.user(a['address'],
                                         limit=int(a.get('limit') or 10),
                                         network=_n(a)),
    },
    'strk20_note': {
        'description': 'One note cell by note_id: whether it exists, open '
                       '(plaintext token) or encrypted (packed ciphertext).',
        'inputSchema': _schema({'note_id': _str('note id (felt)')}, ['note_id']),
        'handler': lambda a: strk20.note(a['note_id'], network=_n(a)),
    },
    'strk20_nullifier': {
        'description': 'Whether a nullifier has been published, i.e. whether '
                       'the note it belongs to has been spent.',
        'inputSchema': _schema({'nullifier': _str('nullifier (felt)')},
                               ['nullifier']),
        'handler': lambda a: strk20.nullifier(a['nullifier'], network=_n(a)),
    },
    'strk20_helpers': {
        'description': 'Every anonymizer contract the pool has called (from '
                       'its ExternalContractInvoked events): call counts, '
                       'kind (invoke = privacy_invoke, compute = '
                       'privacy_invoke_with_computation), entry-point '
                       'signature, and whether it returns '
                       'Span<OpenNoteDeposit> as the pool requires. The map '
                       'of private DeFi on Starknet.',
        'inputSchema': _schema({'limit': _num('invocations to scan (default 200)')}),
        'handler': lambda a: strk20.helpers(limit=int(a.get('limit') or 200),
                                            network=_n(a)),
    },
    'strk20_helper': {
        'description': 'Inspect one contract as a STRK20 anonymizer: its '
                       'privacy_invoke (or compute) signature, parameter '
                       'types, and whether it conforms to the pattern.',
        'inputSchema': _schema({'address': _ADDR}, ['address']),
        'handler': lambda a: strk20.helper(a['address'], network=_n(a)),
    },
    'strk20_invoke_action': {
        'description': 'Build the InvokeExternal client action for a '
                       'privacy_invoke helper: args are ABI-encoded against '
                       'the helper\'s own signature and wrapped in the pool\'s '
                       'ClientAction enum. Returns calldata and the action '
                       'felts for the Privacy SDK or a prover. Encodes only — '
                       'nothing is proven, signed or sent.',
        'inputSchema': _schema({'helper': _ADDR, 'args': _ARGS}, ['helper']),
        'handler': lambda a: strk20.invoke_action(a['helper'], a.get('args'),
                                                  network=_n(a)),
    },
    'strk20_docs': {
        'description': 'The official STRK20 docs (strk20.starknet.io) as '
                       'markdown. No args: the page index. page= one page, '
                       'e.g. helpers/privacy-invoke, sdk/deposit, '
                       'notes-and-nullifiers. q= search every page.',
        'inputSchema': {'type': 'object', 'properties': {
            'page': _str('page path, e.g. helpers/privacy-invoke'),
            'q': _str('search terms')}},
        'handler': lambda a: strk20.docs(a.get('page'), a.get('q')),
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


class ToolError(ValueError):
    pass


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer (POST /tools/<name>)."""
    tool = TOOLS.get(name)
    if not tool:
        raise ToolError(f'no tool named {name!r} — {", ".join(TOOLS)}')
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise ToolError(f'{name} needs {required}')
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
    except chain.RpcError as e:
        msg = json.dumps({'error': str(e), 'code': e.code, 'data': e.data})
    except Exception as e:
        msg = f'{type(e).__name__}: {e}'
    return _result(id_, {'content': [{'type': 'text', 'text': msg}],
                         'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications).
    A JSON array is a batch."""
    if isinstance(body, list):
        out = [r for r in (handle(b) for b in body) if r is not None]
        return out or None
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
            'serverInfo': {'name': 'starknet', 'version': version()},
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
        with open(os.path.join(os.path.dirname(HERE), 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'],
             'inputSchema': t['inputSchema'],
             'annotations': {'readOnlyHint': True, 'openWorldHint': True}}
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
        api.serve(int(argv[i]) if i > 0 else api.PORT)
    else:
        serve_stdio()
