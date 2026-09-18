#!/usr/bin/env python3
"""monero mcp — the whole module as tools an agent can hold.

Forty-five tools over the same `Mod` the CLI and the REST API use, so an agent
and a person are provably doing the same thing. The shape a model needs to
learn is small, and it is not the shape of any other chain:

    read the chain freely → but a balance needs YOUR key, not an address

Monero has no address lookup. There is no `xmr_address` tool because no such
call exists anywhere: amounts and recipients are encrypted on chain. Finding
money means scanning with a view key (xmr_scan, locally, in Python) or asking a
monero-wallet-rpc that already holds the wallet (xmr_balance). A model that
reaches for "get the balance of this address" has to be told that once, so
xmr_status and every relevant description say it.

Three safety properties are enforced here rather than trusted to the model,
which matters because these tools run unattended:

    **Relaying refuses without `confirm: true`.** Not a setting — an argument
    on the call that moves the money. xmr_send is a dry run by default and the
    dry run is real: monero-wallet-rpc builds and signs it, so the fee and
    weight are exact. Going from that to a broadcast takes broadcast=true AND
    confirm=true. Monero transactions are final once mined.

    **A wallet is only opened with its password.** Nothing is stored unlocked;
    every tool that touches a secret takes the password on the call.

    **Secrets are not free over HTTP.** Tools that spend, reveal a key, or scan
    with one need the module token. Scanning is guarded even though it only
    reads the chain: what it returns is exactly what a view key is meant to
    keep private.

Two transports, and they are not equal:

    stdio    python3 mcp.py — a local process on the box that owns the wallet
             directory, so every tool is available without a token.
    http     POST /mcp on the API — a remote caller, who must send the module
             token as `Authorization: Bearer …`. Reads do not need it.

Self-contained JSON-RPC 2.0 on the stdlib — no `mcp` package.

    python3 mcp.py                    # stdio
    python3 mcp.py --tools            # print the schema and exit
    curl -s localhost:8940/mcp | jq   # the same schema, over HTTP
"""
import importlib.util
import json
import os
import secrets as _secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(str(HERE))

CONFIG = json.loads((HERE / 'config.json').read_text())
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-06-18'

INSTRUCTIONS = (
    'Monero: explorer, encrypted local wallets, view-key scanning, real spends '
    'through monero-wallet-rpc, and swaps to 600+ assets. Start with xmr_status '
    '— it names the node, the chain height, how many wallets exist here, and '
    'whether a wallet RPC is running, which decides what you can do at all. '
    'READ THIS FIRST: Monero has no address balance and no address history. '
    'Nothing on chain links an address to a transaction, so there is no tool '
    'that takes an address and returns what it holds — do not look for one and '
    'do not tell the user their address is empty. Two things find money: '
    'xmr_scan walks a block window with the wallet\'s private view key on this '
    'box (bounded, roughly 0.3 blocks/second, and it cannot tell what has '
    'already been SPENT), or xmr_balance reads the true spendable amount from a '
    'monero-wallet-rpc that holds the wallet. '
    'WALLETS: xmr_wallet_create makes one and shows the seed phrase exactly '
    'once; xmr_wallet_restore takes a 25-word phrase; xmr_wallet_watch takes an '
    'address plus its private view key for scan-only use. Take payments with '
    'xmr_wallet_new_address (a subaddress) — that is the right way, and it is '
    'unlinkable. '
    'MONEY: xmr_send is a DRY RUN unless the call carries BOTH broadcast=true '
    'and confirm=true. The dry run is a real signed transaction that was not '
    'relayed, so the fee and weight it reports are exact; xmr_send_confirm '
    'publishes that exact transaction later. Sending needs a monero-wallet-rpc '
    'holding the wallet — xmr_rpc_status says whether one is there and prints '
    'the command to start it, and xmr_rpc_load_wallet hands it one of this '
    'module\'s wallets. Amounts are XMR by default; pass amount_atomic for '
    'piconero (1 XMR = 1e12). Monero transactions are final once mined. '
    'Reads (xmr_info, xmr_block, xmr_tx, xmr_mempool, xmr_price, xmr_supply, '
    'xmr_network, xmr_fee, xmr_ring, xmr_search, xmr_validate) need no wallet '
    'and no token.'
)


def _core():
    """This module's own monero/mod.py, by path — `import mod` is the protocol."""
    impl = HERE / 'monero' / 'mod.py'
    pkg_dir = str(HERE / 'monero')
    if pkg_dir not in sys.path:
        sys.path.append(pkg_dir)
    spec = importlib.util.spec_from_file_location('monero_core', impl)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _core()
_MOD = None


def core():
    """One Mod instance for the process — it caches the node and RPC clients."""
    global _MOD
    if _MOD is None:
        _MOD = CORE.Mod()
    return _MOD


class Refused(Exception):
    """A tool that will not run, with the reason a model can act on."""


# ── who is calling ───────────────────────────────────────────────────

def state_dir() -> Path:
    base = Path(os.environ.get('MONERO_STATE_DIR')
                or Path.home() / '.mod' / 'monero')
    base.mkdir(parents=True, exist_ok=True)
    return base


def module_token() -> str:
    """The same server.secret api.py guards its own functions with.

    One secret for the module, not one per surface: a person who has unlocked
    the web app can point an agent at the same box without minting anything.
    """
    path = state_dir() / 'server.secret'
    if not path.exists():
        path.write_text(_secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text().strip()


class Ctx:
    """The caller and how much of this box they may touch.

    Over stdio the caller is the box: it already has the wallet files and the
    node config on disk, so a token would be theatre. Over HTTP the caller is
    whoever holds the module token, and without it they get the explorer.
    """

    def __init__(self, token=None, local=True):
        self._token = (token or '').removeprefix('Bearer ').strip()
        self.local = bool(local)

    def authorized(self) -> bool:
        if self.local:
            return True
        if not self._token:
            return False
        return _secrets.compare_digest(self._token, module_token())

    def require(self, what='this tool'):
        if not self.authorized():
            raise Refused(
                f'{what} spends, reveals a key, or uses one, so it needs the '
                'module token: send it as `Authorization: Bearer <token>` on '
                'the MCP request. Read it on the box with `m monero/token` or '
                f'`cat {state_dir() / "server.secret"}`.')


LOCAL_CTX = Ctx()


# ── schema helpers ───────────────────────────────────────────────────

def _str(desc):
    return {'type': 'string', 'description': desc}


def _int(desc):
    return {'type': 'integer', 'description': desc}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_NAME = _str('the wallet name, from xmr_wallets')
_PASSWORD = _str('the wallet password — it is never stored, so every tool that '
                 'opens the encrypted file asks for it')
_ADDRESS = _str('a Monero address (95 chars standard or sub, 106 integrated)')
_ACCOUNT = _int('wallet-rpc account index (default 0)')
_PRIORITY = _int('fee priority: 0 slow, 1 normal (default), 2 high, 3 priority')
_CONFIRM = _bool('REQUIRED (true) alongside broadcast to actually relay. Only '
                 'set this when the user has asked for a real transaction — '
                 'Monero transactions are final once mined.')

_READ = {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': True}
_WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}
_SPEND = {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False,
          'openWorldHint': True}


def _unwrap(result, what):
    """Module functions report failures in-band; a tool should raise them."""
    if isinstance(result, dict) and result.get('error'):
        raise Refused(f'{what}: {result["error"]}')
    return result


# ── tools: looking around ────────────────────────────────────────────

def _t_status(a, ctx):
    m = core()
    out = {}
    try:
        info = m.daemon.info()
        out['chain'] = {'height': info.get('height'), 'network': info.get('network'),
                        'difficulty': info.get('difficulty'),
                        'hard_fork_version': info.get('hard_fork_version'),
                        'source': info.get('source')}
        out['node'] = m.daemon.node_info()
    except Exception as e:
        out['chain'] = {'error': str(e)}
    wallets = m.wallet_list()
    out['wallets'] = {'count': len(wallets.get('wallets') or []),
                      'names': [w.get('name') for w in wallets.get('wallets') or []],
                      'dir': wallets.get('dir')}
    rpc = m.rpc_status()
    out['wallet_rpc'] = {'available': rpc.get('available'), 'url': rpc.get('url'),
                         'wallet': rpc.get('wallet'), 'height': rpc.get('height')}
    out['auth'] = {'authorized': ctx.authorized(),
                   'transport': 'stdio' if ctx.local else 'http'}
    out['no_address_balance'] = (
        'Monero has no address lookup. To see funds: xmr_scan with a wallet\'s '
        'view key (finds received outputs, cannot see spends), or xmr_balance '
        'through a monero-wallet-rpc holding the wallet (the true spendable '
        'amount).')
    if not out['wallets']['count']:
        out['next'] = ('no wallet on this box yet — xmr_wallet_create makes one, '
                       'xmr_wallet_restore brings a seed phrase in, '
                       'xmr_wallet_watch adds an address + view key to scan')
    elif not rpc.get('available'):
        out['next'] = ('no monero-wallet-rpc, so xmr_balance and xmr_send are '
                       'unavailable; xmr_rpc_status prints the command to start '
                       'one. Scanning and everything read-only still works.')
    return out


def _t_info(a, ctx):
    return _unwrap(core().info(), 'xmr_info')


def _t_block(a, ctx):
    return _unwrap(core().block(height=a.get('height'), hash=a.get('hash')),
                   'xmr_block')


def _t_tx(a, ctx):
    return _unwrap(core().tx(a['txid']), 'xmr_tx')


def _t_mempool(a, ctx):
    return _unwrap(core().mempool(int(a.get('limit') or 25)), 'xmr_mempool')


def _t_price(a, ctx):
    return _unwrap(core().price(), 'xmr_price')


def _t_supply(a, ctx):
    return _unwrap(core().supply(), 'xmr_supply')


def _t_network(a, ctx):
    return _unwrap(core().network(), 'xmr_network')


def _t_fee(a, ctx):
    return _unwrap(core().fee(int(a.get('priority') or 1),
                              int(a.get('size_bytes') or 1500)), 'xmr_fee')


def _t_ring(a, ctx):
    return _unwrap(core().ring(int(a['index']), int(a.get('count') or 1)),
                   'xmr_ring')


def _t_search(a, ctx):
    return _unwrap(core().search(a['query']), 'xmr_search')


def _t_validate(a, ctx):
    # An invalid address is the answer, not a failure — do not raise on it.
    return core().validate(a['address'])


def _t_capabilities(a, ctx):
    return core().capabilities()


def _t_selftest(a, ctx):
    return core().test()


# ── tools: keys and addresses ────────────────────────────────────────

def _t_seed_new(a, ctx):
    ctx.require('xmr_seed_new')
    return _unwrap(core().seed_new(), 'xmr_seed_new')


def _t_keys_from_seed(a, ctx):
    ctx.require('xmr_keys_from_seed')
    return _unwrap(core().keys_from_seed(a.get('seed_phrase'), a.get('seed_hex'),
                                         a.get('network') or 'mainnet'),
                   'xmr_keys_from_seed')


def _t_subaddress(a, ctx):
    return _unwrap(core().subaddress(a.get('address'), a.get('view_secret_key'),
                                     int(a.get('major') or 0),
                                     int(a.get('minor') or 1),
                                     a.get('network') or 'mainnet'),
                   'xmr_subaddress')


def _t_integrated(a, ctx):
    return _unwrap(core().integrated(a['address'], a.get('payment_id')),
                   'xmr_integrated')


# ── tools: wallets ───────────────────────────────────────────────────

def _t_wallets(a, ctx):
    out = core().wallet_list()
    out['note'] = ('names and addresses only — a seed phrase never leaves this '
                   'box except through xmr_wallet_reveal')
    return out


def _t_wallet(a, ctx):
    return _unwrap(core().wallet_info(a['name']), 'xmr_wallet')


def _t_wallet_create(a, ctx):
    ctx.require('xmr_wallet_create')
    return _unwrap(core().wallet_create(a['name'], a['password'],
                                        a.get('network') or 'mainnet',
                                        a.get('restore_height')),
                   'xmr_wallet_create')


def _t_wallet_restore(a, ctx):
    ctx.require('xmr_wallet_restore')
    return _unwrap(core().wallet_restore(a['name'], a['password'],
                                         a['seed_phrase'],
                                         a.get('network') or 'mainnet',
                                         a.get('restore_height')),
                   'xmr_wallet_restore')


def _t_wallet_watch(a, ctx):
    ctx.require('xmr_wallet_watch')
    return _unwrap(core().wallet_watch(a['name'], a['password'], a['address'],
                                       a['view_secret_key'],
                                       a.get('restore_height')),
                   'xmr_wallet_watch')


def _t_wallet_new_address(a, ctx):
    ctx.require('xmr_wallet_new_address')
    return _unwrap(core().wallet_new_address(a['name'], a['password'],
                                             a.get('label') or '',
                                             int(a.get('major') or 0)),
                   'xmr_wallet_new_address')


def _t_wallet_integrated(a, ctx):
    return _unwrap(core().wallet_integrated(a['name'], a.get('payment_id')),
                   'xmr_wallet_integrated')


def _t_wallet_label(a, ctx):
    ctx.require('xmr_wallet_label')
    return _unwrap(core().wallet_label(a['name'], a['address'], a['label']),
                   'xmr_wallet_label')


def _t_wallet_reveal(a, ctx):
    ctx.require('xmr_wallet_reveal')
    if not a.get('confirm'):
        raise Refused('xmr_wallet_reveal prints the 25-word seed phrase and the '
                      'spend key in the tool result, where anyone reading this '
                      'conversation can see them. Pass confirm=true only if the '
                      'user asked for the seed phrase.')
    return _unwrap(core().wallet_reveal(a['name'], a['password']),
                   'xmr_wallet_reveal')


def _t_wallet_delete(a, ctx):
    ctx.require('xmr_wallet_delete')
    if not a.get('confirm'):
        raise Refused('xmr_wallet_delete removes the encrypted wallet file. If '
                      'the seed phrase is not written down elsewhere the money '
                      'is gone. Pass confirm=true to proceed.')
    return _unwrap(core().wallet_delete(a['name'], a['password']),
                   'xmr_wallet_delete')


def _t_wallet_restore_height(a, ctx):
    ctx.require('xmr_wallet_restore_height')
    return _unwrap(core().wallet_restore_height(a['name'], int(a['height'])),
                   'xmr_wallet_restore_height')


def _t_scan(a, ctx):
    ctx.require('xmr_scan')
    result = _unwrap(core().wallet_scan(
        a['name'], a['password'], a.get('start_height'),
        int(a.get('blocks') or 20), int(a.get('subaddresses') or 5),
        float(a.get('budget_seconds') or 120)), 'xmr_scan')
    # The window is bounded, so the model has to be told how to continue rather
    # than concluding "no funds" from one 20-block look at a 3.7M-block chain.
    result['continue'] = (
        f"this scanned {result.get('from_height')}–{result.get('to_height')}. "
        f"Call again with start_height={result.get('next_start_height')} to go "
        f"on. Nothing found in a window means nothing arrived in THOSE blocks.")
    result['cannot_see_spends'] = (
        'a view key finds outputs you received; it cannot tell which were '
        'already spent. For a true spendable balance use xmr_balance.')
    return result


# ── tools: spending, via monero-wallet-rpc ───────────────────────────

def _t_rpc_status(a, ctx):
    return core().rpc_status()


def _t_balance(a, ctx):
    ctx.require('xmr_balance')
    return _unwrap(core().balance(int(a.get('account') or 0)), 'xmr_balance')


def _t_transfers(a, ctx):
    ctx.require('xmr_transfers')
    return _unwrap(core().transfers(
        bool(a.get('incoming', True)), bool(a.get('outgoing', True)),
        bool(a.get('pending', True)), bool(a.get('failed', False)),
        int(a.get('account') or 0)), 'xmr_transfers')


def _relay_gate(a, what):
    """broadcast alone is not enough — the call has to say confirm too."""
    if a.get('broadcast') and not a.get('confirm'):
        raise Refused(
            f'{what} was asked to broadcast but the call does not carry '
            'confirm=true. This relays a real transaction and Monero spends '
            'are irreversible once mined. Either drop broadcast to see the '
            'exact fee and weight of a signed dry run first, or add '
            'confirm=true because the user asked to send the money.')


def _t_send(a, ctx):
    ctx.require('xmr_send')
    _relay_gate(a, 'xmr_send')
    return _unwrap(core().send(
        a['to'], a.get('amount'), bool(a.get('broadcast')),
        int(a.get('priority') or 1), int(a.get('account') or 0),
        a.get('payment_id'), a.get('amount_atomic'), bool(a.get('sweep'))),
        'xmr_send')


def _t_send_confirm(a, ctx):
    ctx.require('xmr_send_confirm')
    if not a.get('confirm'):
        raise Refused('xmr_send_confirm relays the transaction xmr_send built. '
                      'It moves real money and cannot be undone. Pass '
                      'confirm=true only because the user asked to send it.')
    return _unwrap(core().send_confirm(a['tx_metadata']), 'xmr_send_confirm')


def _t_sweep(a, ctx):
    ctx.require('xmr_sweep')
    _relay_gate(a, 'xmr_sweep')
    return _unwrap(core().sweep(a['to'], bool(a.get('broadcast')),
                                int(a.get('priority') or 1),
                                int(a.get('account') or 0)), 'xmr_sweep')


def _t_broadcast_raw(a, ctx):
    ctx.require('xmr_broadcast_raw')
    if not a.get('confirm'):
        raise Refused('xmr_broadcast_raw pushes an already-signed transaction '
                      'to the network. There is no dry run and no recall. Pass '
                      'confirm=true only because the user asked to relay it.')
    return _unwrap(core().broadcast_raw(a['tx_hex']), 'xmr_broadcast_raw')


def _t_rpc_open(a, ctx):
    ctx.require('xmr_rpc_open')
    return _unwrap(core().rpc_open(a['filename'], a.get('password') or ''),
                   'xmr_rpc_open')


def _t_rpc_load_wallet(a, ctx):
    ctx.require('xmr_rpc_load_wallet')
    return _unwrap(core().rpc_load_wallet(a['name'], a['password'],
                                          a.get('rpc_password') or '',
                                          a.get('filename')),
                   'xmr_rpc_load_wallet')


def _t_key_images(a, ctx):
    ctx.require('xmr_key_images')
    return _unwrap(core().key_images(), 'xmr_key_images')


# ── tools: swaps ─────────────────────────────────────────────────────

def _t_bridge_routes(a, ctx):
    return core().bridge_routes()


def _t_bridge_assets(a, ctx):
    return _unwrap(core().bridge_assets(a.get('search'),
                                        int(a.get('limit') or 100)),
                   'xmr_bridge_assets')


def _t_bridge_quote(a, ctx):
    return _unwrap(core().bridge_quote(a['to_asset'], float(a['amount']),
                                       a.get('from_asset') or 'XMR',
                                       a.get('rate_type') or 'float'),
                   'xmr_bridge_quote')


def _t_bridge_start(a, ctx):
    ctx.require('xmr_bridge_start')
    if not a.get('confirm'):
        raise Refused('xmr_bridge_start reserves a swap with a custodial '
                      'provider and locks a rate against a recipient address. '
                      'Nothing moves until the deposit is funded, but the order '
                      'is real. Pass confirm=true because the user asked for '
                      'this swap — and check xmr_bridge_quote first.')
    return _unwrap(core().bridge_start(a['to_asset'], float(a['amount']),
                                       a['recipient'], a['refund_to'],
                                       a.get('from_asset') or 'XMR',
                                       a.get('rate_type') or 'float',
                                       a.get('recipient_memo')),
                   'xmr_bridge_start')


def _t_bridge_status(a, ctx):
    return _unwrap(core().bridge_status(a['order_id']), 'xmr_bridge_status')


TOOLS = {
    # ── looking around ───────────────────────────────────────────────
    'xmr_status': {
        'description': 'Start here. The node this box is using and the chain '
                       'height it reports, the wallets stored here, whether a '
                       'monero-wallet-rpc is running (which decides whether you '
                       'can read a balance or send at all), and whether your '
                       'token was accepted. Cheap, and it says what is missing '
                       'before anything fails.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_status,
    },
    'xmr_info': {
        'description': 'Chain overview: height, difficulty, hashrate, mempool '
                       'size and the XMR price in one call.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_info,
    },
    'xmr_block': {
        'description': 'One block by height or hash — the latest when neither is '
                       'given. Timestamp, size, miner reward, transaction ids.',
        'inputSchema': {'type': 'object', 'properties': {
            'height': _int('block height'),
            'hash': _str('block hash (64 hex chars)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_block,
    },
    'xmr_tx': {
        'description': 'A transaction. Note what this can and cannot say: '
                       'Monero encrypts amounts and recipients, so what comes '
                       'back is the SHAPE of the spend — ring size, input and '
                       'output counts, fee, confirmations — never who paid whom '
                       'or how much. Do not infer amounts from it.',
        'inputSchema': {'type': 'object',
                        'properties': {'txid': _str('transaction id (64 hex)')},
                        'required': ['txid']},
        'annotations': _READ, 'auth': False, 'handler': _t_tx,
    },
    'xmr_mempool': {
        'description': 'Transactions waiting to be mined, newest first, with '
                       'their fees and weights.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _int('how many to return (default 25)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_mempool,
    },
    'xmr_price': {
        'description': 'XMR price and market data.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_price,
    },
    'xmr_supply': {
        'description': 'Circulating supply and the tail emission — Monero has no '
                       'fixed cap; it settles at 0.6 XMR per block forever.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_supply,
    },
    'xmr_network': {
        'description': 'Consensus and node health: height, difficulty, hashrate, '
                       'hard fork version, block size limit, and which node this '
                       'box is talking to.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_network,
    },
    'xmr_fee': {
        'description': 'What a transaction of a given size would cost right now, '
                       'in piconero, XMR and USD, at each of the four fee '
                       'priorities.',
        'inputSchema': {'type': 'object', 'properties': {
            'priority': _PRIORITY,
            'size_bytes': _int('transaction size to price (default 1500, about '
                               'a normal 2-output spend)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_fee,
    },
    'xmr_ring': {
        'description': 'The real outputs sitting at a range of global output '
                       'indices — the pool a ring signature draws decoys from. '
                       'Useful for showing what privacy actually looks like: a '
                       'spend names 16 of these and proves it owns one without '
                       'saying which.',
        'inputSchema': {'type': 'object', 'properties': {
            'index': _int('global output index to start at'),
            'count': _int('how many outputs (default 1)')},
            'required': ['index']},
        'annotations': _READ, 'auth': False, 'handler': _t_ring,
    },
    'xmr_search': {
        'description': 'One box for a height, a 64-hex id (transaction, else '
                       'block) or an address. An address comes back parsed, not '
                       'with a balance — there is none to give.',
        'inputSchema': {'type': 'object',
                        'properties': {'query': _str('height, hash or address')},
                        'required': ['query']},
        'annotations': _READ, 'auth': False, 'handler': _t_search,
    },
    'xmr_validate': {
        'description': 'Check an address and say what kind it is: standard, '
                       'subaddress or integrated, and which network. An invalid '
                       'address returns valid=false with the reason — that is an '
                       'answer, not an error.',
        'inputSchema': {'type': 'object', 'properties': {'address': _ADDRESS},
                        'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_validate,
    },
    'xmr_capabilities': {
        'description': 'What this module does, what it needs help for, and what '
                       'it refuses to fake — spent-output detection and '
                       'transaction building are deliberately not implemented in '
                       'Python here, and this says exactly why. Read it before '
                       'concluding the module is broken.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_capabilities,
    },
    'xmr_selftest': {
        'description': 'Run the module self-test: crypto primitives against '
                       'known vectors, seed phrase round trip, the scanner, a '
                       'wallet create/restore in a temp dir, the node and the '
                       'swap provider. Says which parts are degraded.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_selftest,
    },

    # ── keys and addresses ───────────────────────────────────────────
    'xmr_seed_new': {
        'description': 'Generate a fresh 25-word seed phrase and the address it '
                       'produces. NOTHING IS SAVED — the phrase exists only in '
                       'this tool result, so anyone who can read it owns the '
                       'money. Use xmr_wallet_create for a wallet the module '
                       'keeps encrypted on disk.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _WRITE, 'auth': True, 'handler': _t_seed_new,
    },
    'xmr_keys_from_seed': {
        'description': 'Derive the four keys (view/spend, public/private) and '
                       'the address from a 25-word phrase or a 64-hex seed. '
                       'Pure maths on this box; nothing is sent anywhere.',
        'inputSchema': {'type': 'object', 'properties': {
            'seed_phrase': _str('25-word Monero seed phrase'),
            'seed_hex': _str('64-hex private spend key / seed'),
            'network': _str('mainnet (default), stagenet or testnet')}},
        'annotations': _WRITE, 'auth': True, 'handler': _t_keys_from_seed,
    },
    'xmr_subaddress': {
        'description': 'Derive a subaddress from a main address and its PRIVATE '
                       'VIEW key. Public maths — no spend key involved, so this '
                       'works for a watch-only setup.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS,
            'view_secret_key': _str('64-hex private view key for that address'),
            'major': _int('account index (default 0)'),
            'minor': _int('subaddress index within the account (default 1)'),
            'network': _str('mainnet (default), stagenet or testnet')},
            'required': ['address', 'view_secret_key']},
        'annotations': _READ, 'auth': False, 'handler': _t_subaddress,
    },
    'xmr_integrated': {
        'description': 'Fold an 8-byte payment id into an address. Prefer a '
                       'subaddress (xmr_wallet_new_address): integrated '
                       'addresses reveal the base address they were built from, '
                       'subaddresses are unlinkable.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS,
            'payment_id': _str('16-hex payment id; random when omitted')},
            'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_integrated,
    },

    # ── wallets ──────────────────────────────────────────────────────
    'xmr_wallets': {
        'description': 'Wallets stored on this box: name, address, network, '
                       'whether it is view-only, and its restore height. '
                       'Addresses only — no key leaves through here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_wallets,
    },
    'xmr_wallet': {
        'description': 'One wallet: its addresses, subaddresses with labels, '
                       'network, restore height. No password needed because no '
                       'secret is returned — and no balance, because Monero has '
                       'none to look up (xmr_scan or xmr_balance).',
        'inputSchema': {'type': 'object', 'properties': {'name': _NAME},
                        'required': ['name']},
        'annotations': _READ, 'auth': False, 'handler': _t_wallet,
    },
    'xmr_wallet_create': {
        'description': 'Create an encrypted wallet (AES-256-GCM behind '
                       'PBKDF2-SHA256) and return its 25-word seed phrase EXACTLY '
                       'ONCE. Tell the user to write the phrase down: it is the '
                       'only way back into the money, and this module will not '
                       'show it again without the password. Defaults the restore '
                       'height to the current tip, so a new wallet scans from '
                       'today rather than from 2014.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('a name for the wallet on this box'),
            'password': _str('encrypts the wallet file — not recoverable'),
            'network': _str('mainnet (default), stagenet or testnet'),
            'restore_height': _int('block to start scanning from; defaults to '
                                   'the current chain tip')},
            'required': ['name', 'password']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_create,
    },
    'xmr_wallet_restore': {
        'description': 'Restore a wallet from its 25-word seed phrase. Set '
                       'restore_height to roughly when the wallet was first used '
                       '— scanning from 0 through Python is not practical.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('a name for the wallet on this box'),
            'password': _str('encrypts the restored wallet file'),
            'seed_phrase': _str('the 25-word Monero seed phrase'),
            'network': _str('mainnet (default), stagenet or testnet'),
            'restore_height': _int('block to start scanning from')},
            'required': ['name', 'password', 'seed_phrase']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_restore,
    },
    'xmr_wallet_watch': {
        'description': 'Add a view-only wallet: an address plus its private view '
                       'key. It can scan for incoming payments and can never '
                       'spend — the right thing to hand a machine that only has '
                       'to watch for money arriving.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('a name for the wallet on this box'),
            'password': _str('encrypts the stored view key'),
            'address': _ADDRESS,
            'view_secret_key': _str('64-hex private view key'),
            'restore_height': _int('block to start scanning from')},
            'required': ['name', 'password', 'address', 'view_secret_key']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_watch,
    },
    'xmr_wallet_new_address': {
        'description': 'Derive the next subaddress — THE way to take a payment. '
                       'Each one is unlinkable from the others and from the main '
                       'address, so give a fresh one per payer.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'password': _PASSWORD,
            'label': _str('a note for who this address is for'),
            'major': _int('account index (default 0)')},
            'required': ['name', 'password']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_new_address,
    },
    'xmr_wallet_integrated': {
        'description': 'An integrated address for this wallet (main address + '
                       'payment id). Needs no password. A subaddress from '
                       'xmr_wallet_new_address is usually the better choice.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME,
            'payment_id': _str('16-hex payment id; random when omitted')},
            'required': ['name']},
        'annotations': _READ, 'auth': False, 'handler': _t_wallet_integrated,
    },
    'xmr_wallet_label': {
        'description': 'Label one of the wallet\'s subaddresses, so a later scan '
                       'says who paid rather than which index did.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'address': _ADDRESS, 'label': _str('the label')},
            'required': ['name', 'address', 'label']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_label,
    },
    'xmr_wallet_reveal': {
        'description': 'Reveal the seed phrase and private keys. This puts them '
                       'in the conversation, where anyone reading it can spend '
                       'the wallet — needs confirm=true and only because the '
                       'user asked to back the wallet up.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'password': _PASSWORD,
            'confirm': _bool('REQUIRED (true): acknowledges printing secrets')},
            'required': ['name', 'password']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_wallet_reveal,
    },
    'xmr_wallet_delete': {
        'description': 'Delete a wallet file (the password must verify). If the '
                       'seed phrase is not written down elsewhere, the money is '
                       'unrecoverable. Needs confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'password': _PASSWORD,
            'confirm': _bool('REQUIRED (true): the file is not recoverable')},
            'required': ['name', 'password']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_wallet_delete,
    },
    'xmr_wallet_restore_height': {
        'description': 'Set where scanning starts for this wallet. Raise it to '
                       'skip blocks from before the wallet existed; lower it if '
                       'a payment older than the current setting is missing.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'height': _int('block height to scan from')},
            'required': ['name', 'height']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_wallet_restore_height,
    },
    'xmr_scan': {
        'description': 'Scan a window of blocks for outputs belonging to this '
                       'wallet, using its private view key ON THIS BOX — the key '
                       'is never sent anywhere. This is how you find received '
                       'money in Monero; there is no address lookup. '
                       'DELIBERATELY BOUNDED: the chain is 3.7M blocks and this '
                       'is Python at roughly 0.3 blocks/second through a public '
                       'node, so it takes a window at a time and returns '
                       'next_start_height to continue from. An empty window '
                       'means nothing arrived in THOSE blocks — never report it '
                       'as an empty wallet. It also cannot see what has been '
                       'SPENT; xmr_balance can.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'password': _PASSWORD,
            'start_height': _int('first block to scan; defaults to the wallet\'s '
                                 'restore height'),
            'blocks': _int('window size (default 20) — check the reported rate '
                           'before asking for a big one'),
            'subaddresses': _int('how many subaddresses per account to check '
                                 '(default 5)'),
            'budget_seconds': _num('stop after this long and report how far it '
                                   'got (default 120)')},
            'required': ['name', 'password']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_scan,
    },

    # ── spending ─────────────────────────────────────────────────────
    'xmr_rpc_status': {
        'description': 'Is a monero-wallet-rpc reachable, and which wallet does '
                       'it hold? Sending and true balances need one. When there '
                       'is none, this prints the exact command to start it.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_rpc_status,
    },
    'xmr_balance': {
        'description': 'The real spendable balance, from a monero-wallet-rpc '
                       'holding the wallet. Unlike a scan it knows which outputs '
                       'were already spent, because that wallet holds the key '
                       'images. Needs the RPC — xmr_rpc_status first, '
                       'xmr_rpc_load_wallet to give it one of this module\'s '
                       'wallets.',
        'inputSchema': {'type': 'object', 'properties': {'account': _ACCOUNT}},
        'annotations': _READ, 'auth': True, 'handler': _t_balance,
    },
    'xmr_transfers': {
        'description': 'Payment history from monero-wallet-rpc: in, out, pending '
                       'and optionally failed.',
        'inputSchema': {'type': 'object', 'properties': {
            'incoming': _bool('include received (default true)'),
            'outgoing': _bool('include sent (default true)'),
            'pending': _bool('include unconfirmed (default true)'),
            'failed': _bool('include failed (default false)'),
            'account': _ACCOUNT}},
        'annotations': _READ, 'auth': True, 'handler': _t_transfers,
    },
    'xmr_send': {
        'description': 'Send XMR. DRY RUN BY DEFAULT, and the dry run is real: '
                       'monero-wallet-rpc builds and signs the transaction but '
                       'does not relay it, so the fee, weight and txid it '
                       'reports are exactly what the network would see. Relaying '
                       'takes BOTH broadcast=true and confirm=true — one alone '
                       'is refused. Amount is in XMR; use amount_atomic for '
                       'piconero (1 XMR = 1e12). Show the user the dry run '
                       'before spending anything.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _ADDRESS,
            'amount': _num('amount in XMR'),
            'amount_atomic': _int('amount in piconero, instead of `amount`'),
            'broadcast': _bool('relay it (default false = signed dry run)'),
            'confirm': _CONFIRM,
            'priority': _PRIORITY,
            'account': _ACCOUNT,
            'payment_id': _str('16-hex payment id, if the recipient asked for one'),
            'sweep': _bool('send everything unlocked instead of an amount')},
            'required': ['to']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_send,
    },
    'xmr_send_confirm': {
        'description': 'Relay the exact transaction a previous xmr_send dry run '
                       'produced, by its tx_metadata. Publishes what was shown '
                       'rather than building a second one that might differ in '
                       'fee or inputs. Needs confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'tx_metadata': _str('the tx_metadata string from xmr_send'),
            'confirm': _CONFIRM},
            'required': ['tx_metadata']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_send_confirm,
    },
    'xmr_sweep': {
        'description': 'Send everything unlocked in an account to one address. '
                       'Same rules as xmr_send: dry run unless broadcast=true '
                       'and confirm=true together.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _ADDRESS,
            'broadcast': _bool('relay it (default false = signed dry run)'),
            'confirm': _CONFIRM,
            'priority': _PRIORITY,
            'account': _ACCOUNT},
            'required': ['to']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_sweep,
    },
    'xmr_broadcast_raw': {
        'description': 'Push an already-signed transaction hex to the network '
                       'through the node. No dry run exists for this and there '
                       'is no recall. Needs confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'tx_hex': _str('the signed transaction, hex'),
            'confirm': _CONFIRM},
            'required': ['tx_hex']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_broadcast_raw,
    },
    'xmr_rpc_open': {
        'description': 'Open a wallet file that monero-wallet-rpc already has on '
                       'its own disk, by filename.',
        'inputSchema': {'type': 'object', 'properties': {
            'filename': _str('wallet filename known to the RPC'),
            'password': _str('that wallet\'s password, if it has one')},
            'required': ['filename']},
        'annotations': _WRITE, 'auth': True, 'handler': _t_rpc_open,
    },
    'xmr_rpc_load_wallet': {
        'description': 'Hand one of this module\'s wallets to monero-wallet-rpc '
                       'so it can spend. The seed phrase is decrypted here and '
                       'passed over loopback — the one moment it leaves the '
                       'encrypted file, so the RPC should be one the user runs '
                       'themselves. The RPC then rescans from the restore '
                       'height, and balance/send work once it has caught up.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _NAME, 'password': _PASSWORD,
            'rpc_password': _str('password to set on the wallet inside the RPC'),
            'filename': _str('filename for the RPC to use (defaults to `name`)')},
            'required': ['name', 'password']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_rpc_load_wallet,
    },
    'xmr_key_images': {
        'description': 'Export signed key images from monero-wallet-rpc. These '
                       'are what a view-only wallet needs to learn what has '
                       'already been spent — the one thing view-key scanning '
                       'cannot work out for itself.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': True, 'handler': _t_key_images,
    },

    # ── swaps ────────────────────────────────────────────────────────
    'xmr_bridge_routes': {
        'description': 'How XMR can and cannot leave Monero, and why every '
                       'option is custodial: ring signatures mean no light '
                       'client can prove a Monero payment to another chain, so '
                       'there is no trustless bridge to offer. Read this before '
                       'promising a swap is safe.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_bridge_routes,
    },
    'xmr_bridge_assets': {
        'description': 'Assets XMR can be swapped against (600+). Search by '
                       'ticker or name; assets are chain-qualified where it '
                       'matters, like "TRX:USDT" or "ETH:USDC".',
        'inputSchema': {'type': 'object', 'properties': {
            'search': _str('filter by ticker or name'),
            'limit': _int('how many to return (default 100)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_bridge_assets,
    },
    'xmr_bridge_quote': {
        'description': 'Price a swap without reserving anything: rate, output '
                       'amount, minimum and maximum. Nothing is committed and no '
                       'address is issued.',
        'inputSchema': {'type': 'object', 'properties': {
            'to_asset': _str('what to receive, e.g. BTC, ETH, TRX:USDT'),
            'amount': _num('how much to send'),
            'from_asset': _str('what to send (default XMR)'),
            'rate_type': _str('"float" (default) or "fixed"')},
            'required': ['to_asset', 'amount']},
        'annotations': _READ, 'auth': False, 'handler': _t_bridge_quote,
    },
    'xmr_bridge_start': {
        'description': 'Reserve a deposit address for a swap. Nothing moves '
                       'until the deposit is funded, but the order is real and '
                       'the provider takes custody in between. Always quote '
                       'first, always set refund_to, and needs confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'to_asset': _str('what to receive, e.g. BTC, ETH, TRX:USDT'),
            'amount': _num('how much to send'),
            'recipient': _str('address on the destination chain'),
            'refund_to': _str('YOUR address, where funds come back if the swap '
                              'fails — do not omit this'),
            'from_asset': _str('what to send (default XMR)'),
            'rate_type': _str('"float" (default) or "fixed"'),
            'recipient_memo': _str('memo/tag, for chains that need one'),
            'confirm': _bool('REQUIRED (true) to place the order')},
            'required': ['to_asset', 'amount', 'recipient', 'refund_to']},
        'annotations': _SPEND, 'auth': True, 'handler': _t_bridge_start,
    },
    'xmr_bridge_status': {
        'description': 'Track a swap by its order id: state, amounts, deposit '
                       'and payout transactions.',
        'inputSchema': {'type': 'object', 'properties': {
            'order_id': _str('the id from xmr_bridge_start')},
            'required': ['order_id']},
        'annotations': _READ, 'auth': False, 'handler': _t_bridge_status,
    },
}


def needs_auth(name) -> bool:
    return bool(TOOLS.get(name, {}).get('auth'))


def version() -> str:
    return CONFIG.get('version', '1.0.0')


def tool_list():
    return [{'name': name, 'description': tool['description'],
             'inputSchema': tool['inputSchema'],
             'annotations': tool['annotations']}
            for name, tool in TOOLS.items()]


def client_config(url=None):
    endpoint = url or CONFIG.get('urls', {}).get('mcp', 'http://localhost:8940/mcp')
    return {
        'http': {'mcpServers': {'monero': {'type': 'http', 'url': endpoint,
                                           'headers': {'Authorization': 'Bearer <module token>'}}}},
        'stdio': {'mcpServers': {'monero': {'command': 'python3',
                                            'args': [str(HERE / 'mcp.py')]}}},
        'claude_cli': f'claude mcp add --transport http monero {endpoint} '
                      f'--header "Authorization: Bearer <token>"',
        'token': f'`m monero/token`, or cat {state_dir() / "server.secret"}',
    }


def describe(url=None):
    """Everything about this server in one document — what GET /mcp serves, so
    the schema is never something you have to run a client to see."""
    return {
        'server': {'name': 'monero', 'version': version(),
                   'description': CONFIG.get('description', '')[:400]},
        'protocol': {'default': DEFAULT_PROTOCOL_VERSION,
                     'supported': list(SUPPORTED_PROTOCOL_VERSIONS),
                     'jsonrpc': '2.0',
                     'methods': ['initialize', 'ping', 'tools/list', 'tools/call']},
        'transports': {
            'http': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                     'url': url or CONFIG.get('urls', {}).get(
                         'mcp', 'http://localhost:8940/mcp'),
                     'note': 'Streamable HTTP. Tools that use a key need the '
                             'module token.'},
            'stdio': {'command': f'python3 {HERE / "mcp.py"}',
                      'note': 'runs on the box that owns the wallet files, so '
                              'every tool is available without a token'},
        },
        'auth': {
            'module_token': 'Authorization: Bearer <token from ~/.mod/monero/'
                            'server.secret>. Needed for every tool that spends, '
                            'reveals a key, or scans with one. Open to anyone: '
                            + ', '.join(n for n in TOOLS if not needs_auth(n)),
            'stdio': 'a stdio server already sits on the wallet directory, so '
                     'local tools need no token',
            'passwords': 'a wallet password is never stored — it is passed per '
                         'call and used to open the encrypted file',
        },
        'safety': {
            'relaying': 'xmr_send and xmr_sweep are signed dry runs unless the '
                        'call carries BOTH broadcast=true and confirm=true; '
                        'xmr_send_confirm and xmr_broadcast_raw need confirm=true',
            'secrets': 'xmr_wallet_reveal and xmr_wallet_delete need '
                       'confirm=true, because one prints a seed phrase into the '
                       'conversation and the other destroys the only copy',
            'scanning': 'the view key never leaves this box — xmr_scan walks '
                        'blocks locally',
            'not_faked': 'spent-output detection and transaction building are '
                         'not reimplemented here; see xmr_capabilities for why',
        },
        'instructions': INSTRUCTIONS,
        'count': len(TOOLS),
        'tools': [{'name': name, 'description': tool['description'],
                   'inputSchema': tool['inputSchema'],
                   'annotations': tool['annotations'],
                   'auth': 'token' if needs_auth(name) else 'none',
                   'transports': ['stdio', 'http']}
                  for name, tool in TOOLS.items()],
        'config': client_config(url),
    }


# ── JSON-RPC 2.0 ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, ctx=None):
    """Run one tool. Raises Refused/ValueError with a message worth reading."""
    tool = TOOLS.get(name)
    if not tool:
        raise Refused(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    return tool['handler'](dict(args or {}), ctx if ctx is not None else Ctx())


def _call(id_, params, ctx):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')

    def failed(text):
        # A tool failure is a *successful* JSON-RPC response carrying isError,
        # per the MCP spec, so the model reads the reason and retries instead
        # of the transport dying under it.
        return _result(id_, {'content': [{'type': 'text', 'text': text}],
                             'isError': True})
    try:
        result = call_tool(name, args, ctx)
    except Refused as e:
        return failed(str(e) if str(e).startswith(name) else f'{name}: {e}')
    except KeyError as e:
        return failed(f'{name}: missing argument {e}')
    except TypeError as e:
        return failed(f'{name}: bad arguments — {e}')
    except ValueError as e:
        return failed(f'{name}: {e}')
    except Exception as e:
        return failed(f'{name} failed: {type(e).__name__}: {e}')
    text = result if isinstance(result, str) else json.dumps(result, indent=2,
                                                             default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, ctx=None):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        asked = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': (asked if asked in SUPPORTED_PROTOCOL_VERSIONS
                                else DEFAULT_PROTOCOL_VERSION),
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'monero', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, ctx)
    return _error(id_, -32601, f'method not found: {method}')


# ── transports ───────────────────────────────────────────────────────

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            response = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            response = handle(body, LOCAL_CTX)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--tools' in argv or '--schema' in argv:
        print(json.dumps(describe(), indent=2))
    elif '--http' in argv:
        # The HTTP transport lives in the API server, so there is one mounting
        # of these tools and not two that can disagree.
        import uvicorn

        from api import app
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i]) if i > 0 else int(
            os.environ.get('MONERO_REST_PORT') or 8940)
        uvicorn.run(app, host='127.0.0.1', port=port)
    else:
        serve_stdio()
