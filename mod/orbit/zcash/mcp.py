#!/usr/bin/env python3
"""zcash mcp — the module as tools an agent can hold.

The same `Mod` object the REST API and the web console call, exposed as MCP
tools, so a person clicking the console and a model calling a tool are running
the identical code and get the identical answer.

Two transports:

    stdio   python3 mcp.py — a local process on this box. It can read the
            module token itself, so every tool is available.
    http    POST /mcp on the API server (api.py, default :8930, and through
            the app at /zcash/api/mcp). Reads are open — the chain is public.
            Anything that spends, or that decrypts a shielded note, needs the
            module token as `Authorization: Bearer <token>`.

The gate is deliberately the same set of functions api.py guards on its REST
routes: a gate on the REST surface alone would be no gate at all, because
/mcp reaches every function by name.

Three things a model should know before it calls anything here, all of them
enforced rather than documented:

    **Sending is a dry run.** zec_send and zec_bridge_send build and sign a
    real transaction and then stop; nothing is published unless the call
    carries broadcast=true. Every response says which mode it ran in.

    **Shielded spending needs a prover, and the prover is local.** This
    module derives real Sapling and Orchard keys, hands out zs1/unified
    addresses and decrypts the notes they receive in pure Python — but a
    spend carries a zk-SNARK proof, which Python will not produce. A local
    light client does: zec_shielded_backend builds it once, zec_shielded_sync
    scans this wallet's notes, and zec_shielded_send proves and broadcasts.
    No full node. zec_capabilities is the honest map.

    **A viewing key reads a lifetime of payments.** Shielded reads are gated
    like spends, because handing out an incoming viewing key exposes every
    payment an address ever received.

Self-contained JSON-RPC 2.0 on the standard library — no `mcp` package.

    python3 mcp.py                    # stdio
    python3 mcp.py --tools            # print the schema and exit
    python3 mcp.py --http --port 8930 # the API server, which mounts /mcp
    curl -s localhost:8930/mcp | jq   # the same schema, over HTTP
"""

import importlib.util
import json
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / 'config.json').read_text())

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-06-18'

SERVER_NAME = 'zcash'

INSTRUCTIONS = (
    'Zcash: block explorer, HD wallet, real transparent sends, Sapling '
    'shielded addresses and note decryption, and a bridge to 30+ chains. '
    'Start with zec_capabilities — it is the honest map of what works and what '
    'needs a prover. Both shielded pools, Sapling and Orchard, are read here. '
    'LOOKING: zec_search takes a height, a txid or any address and works out '
    'which it is; zec_info, zec_price and zec_network cover the chain itself; '
    'zec_validate names an address\'s pool before you pay it. '
    'HOLDING: zec_wallets lists wallets, zec_wallet shows one\'s addresses, '
    'zec_balance prices it. Creating a wallet returns the seed phrase exactly '
    'once. Every wallet operation that touches the seed needs the password '
    'that encrypted it — it is not stored anywhere. '
    'SPENDING: zec_send pays a transparent (t1/t3) address. It is a DRY RUN '
    'unless the call carries broadcast=true — the transaction is built and '
    'fully signed either way, so a dry run tells you the fee, the inputs and '
    'the txid you would get. Do not pass broadcast=true unless the user asked '
    'for a real payment. Fees are ZIP-317; zec_estimate_fee prices one first. '
    'SHIELDED: this module derives Sapling and Orchard keys, gives out zs1 '
    'and unified addresses and decrypts notes with the viewing key '
    '(zec_shielded_address, zec_shielded_scan, zec_shielded_scan_tx). '
    'SHIELDED SPENDING works here, in three steps and in this order: '
    'zec_shielded_backend (action=install) builds a local light client once '
    'per host, since a spend needs a zk-SNARK proof; zec_shielded_sync '
    '(action=start) scans the chain for this wallet\'s notes in the '
    'background and action=status says how far it got; then zec_shielded_send '
    'proves and broadcasts — DRY RUN unless broadcast=true. '
    'zec_shielded_spendable is what can actually be sent, as against '
    'zec_shielded_scan which reports what was RECEIVED. '
    'BRIDGING: zec_bridge_chains lists destinations, zec_bridge_quote prices '
    'one for free, zec_bridge_send does the whole thing from a wallet (dry '
    'run unless broadcast=true), zec_bridge_status tracks it by deposit '
    'address. '
    'Reads need no token. Spending, wallet secrets and shielded reads need '
    'the module token (`m zcash/token`) as a bearer header over HTTP; a stdio '
    'server on the box reads it itself.'
)


# ── the module ───────────────────────────────────────────────────────

def _core():
    """Load zcash/mod.py by path.

    Not `import mod`: at the top of sys.path that name is the protocol's own
    package, and inside this tree it is whichever mod.py got there first —
    including app/mod.py, the Next shim with no wallet functions on it.
    """
    spec = importlib.util.spec_from_file_location(
        'zcash_core', str(HERE / 'zcash' / 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = None


def get_mod():
    """The one Mod instance this process uses. api.py shares it, so the REST
    routes, the console and the tools cannot drift apart."""
    global _MOD
    if _MOD is None:
        _MOD = _core().Mod()
    return _MOD


class Refused(Exception):
    """A tool that will not run, with the reason a model can act on."""


# ── the token ────────────────────────────────────────────────────────

def secret_path() -> Path:
    base = Path(os.environ.get('ZCASH_STATE_DIR') or Path.home() / '.mod' / 'zcash')
    base.mkdir(parents=True, exist_ok=True)
    return base / 'server.secret'


def server_token() -> str:
    """The module token, minted on first use. Shared with api.py's REST gate."""
    path = secret_path()
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text().strip()


class Ctx:
    """Who is calling, and how much of this box they may touch.

    Over stdio the caller is the box itself — it could read
    ~/.mod/zcash/server.secret with `cat`, so demanding it back is theatre.
    Over HTTP the caller is whoever holds the token, and nothing else.
    """

    def __init__(self, token=None, local=True):
        raw = (token or '').strip()
        if raw.lower().startswith('bearer '):
            raw = raw[7:].strip()
        self.token = raw
        self.local = bool(local)

    def authorized(self) -> bool:
        if self.local:
            return True
        if not self.token:
            return False
        return secrets.compare_digest(self.token, server_token())

    def require(self, name):
        if self.authorized():
            return
        raise Refused(
            f'{name} can spend, delete, or decrypt shielded notes, so it needs '
            f'the module token: send `Authorization: Bearer <token>` with the '
            f'MCP request. Read it with `m zcash/token`, or from '
            f'{secret_path()}. Reads (the explorer, wallet addresses and '
            f'balances, bridge quotes) need nothing.')


LOCAL_CTX = Ctx()


# ── schema helpers ───────────────────────────────────────────────────

def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _int(desc):
    return {'type': 'integer', 'description': desc}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_WALLET = _str('wallet name, as listed by zec_wallets')
_PASSWORD = _str('the password the wallet was created with — it decrypts the '
                 'seed and is never stored')
_BROADCAST = _bool('false (default) builds and signs the transaction without '
                   'publishing it; true actually sends the money. Only set it '
                   'when the user asked for a real payment.')

_READ = {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': True}
_WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}
_SPEND = {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False,
          'openWorldHint': True}

_NUMERIC = ('height', 'inputs', 'outputs', 'addresses', 'strength', 'birthday',
            'from_height', 'to_height', 'blocks', 'account', 'amount',
            'amount_zatoshi', 'fee_zatoshi', 'slippage_bps')
_FLAGS = ('broadcast', 'utxos', 'detail', 'shielded', 'confirm')


def _coerce(args: dict) -> dict:
    """JSON gives us the right types; a query string and a sloppy client do
    not. Numbers and flags arrive as strings often enough to be worth fixing
    here rather than failing three layers down in the signer."""
    out = dict(args or {})
    for key in _NUMERIC:
        v = out.get(key)
        if isinstance(v, str) and v.strip() != '':
            try:
                out[key] = float(v) if ('.' in v or 'e' in v.lower()) else int(v)
            except ValueError:
                raise Refused(f'{key} must be a number, got {v!r}')
    for key in _FLAGS:
        v = out.get(key)
        if isinstance(v, str):
            out[key] = v.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return out


def _pick(args: dict, *names):
    return {k: args[k] for k in names if args.get(k) is not None}


# ── handlers: the chain ──────────────────────────────────────────────

def _t_info(a):
    return get_mod().info()


def _t_search(a):
    return get_mod().search(query=a['query'])


def _t_block(a):
    return get_mod().block(**_pick(a, 'height', 'hash'))


def _t_tx(a):
    return get_mod().tx(txid=a['txid'])


def _t_address(a):
    return get_mod().address(addr=a['address'])


def _t_price(a):
    return get_mod().price()


def _t_network(a):
    return get_mod().network()


def _t_validate(a):
    return get_mod().validate(addr=a['address'])


def _t_estimate_fee(a):
    return get_mod().estimate_fee(inputs=int(a.get('inputs') or 1),
                                  outputs=int(a.get('outputs') or 2))


def _t_capabilities(a):
    return get_mod().capabilities()


def _t_status(a):
    return get_mod().status()


# ── handlers: wallets ────────────────────────────────────────────────

def _t_wallets(a):
    return get_mod().wallet_list()


def _t_wallet(a):
    return get_mod().wallet_info(name=a['wallet'])


def _t_balance(a):
    mod = get_mod()
    out = mod.wallet_balance(name=a['wallet'])
    if a.get('utxos') and 'error' not in out:
        utxos = mod.wallet_utxos(name=a['wallet'])
        out['utxos'] = utxos.get('utxos', [])
        out['utxo_count'] = utxos.get('utxo_count')
    return out


def _t_wallet_create(a):
    mod = get_mod()
    if a.get('mnemonic'):
        return mod.wallet_restore(
            name=a['wallet'], password=a['password'], mnemonic=a['mnemonic'],
            addresses=int(a.get('addresses') or 1),
            passphrase=a.get('passphrase') or '',
            birthday=int(a['birthday']) if a.get('birthday') is not None else None)
    return mod.wallet_create(
        name=a['wallet'], password=a['password'],
        addresses=int(a.get('addresses') or 1),
        strength=int(a.get('strength') or 256),
        passphrase=a.get('passphrase') or '')


def _t_new_address(a):
    mod = get_mod()
    if a.get('shielded'):
        return mod.shielded_new_address(name=a['wallet'], password=a['password'],
                                        label=a.get('label') or '')
    return mod.wallet_new_address(name=a['wallet'], password=a['password'],
                                  label=a.get('label') or '')


def _t_import_key(a):
    return get_mod().wallet_import(name=a['wallet'], password=a['password'],
                                   wif=a['wif'], label=a.get('label') or '')


def _t_label(a):
    return get_mod().wallet_label(name=a['wallet'], address=a['address'],
                                  label=a.get('label') or '')


def _t_reveal(a):
    return get_mod().wallet_reveal(name=a['wallet'], password=a['password'])


def _t_delete(a):
    if not a.get('confirm'):
        raise Refused('deleting a wallet erases the encrypted seed on this box. '
                      'If the seed phrase is not written down somewhere else, '
                      'the funds are gone. Call again with confirm=true.')
    return get_mod().wallet_delete(name=a['wallet'], password=a['password'])


# ── handlers: shielded ───────────────────────────────────────────────

def _t_shielded_address(a):
    return get_mod().shielded_address(name=a['wallet'])


def _t_shielded_scan(a):
    mod = get_mod()
    kwargs = {'name': a['wallet'], 'password': a['password']}
    for key in ('from_height', 'blocks'):
        if a.get(key) is not None:
            kwargs[key] = int(a[key])
    if a.get('detail'):
        if a.get('to_height') is not None:
            kwargs['to_height'] = int(a['to_height'])
        if a.get('account') is not None:
            kwargs['account'] = int(a['account'])
        return mod.shielded_scan(**kwargs)
    return mod.shielded_balance(**kwargs)


def _t_shielded_scan_tx(a):
    return get_mod().shielded_scan_tx(
        txid=a['txid'], name=a.get('wallet'), password=a.get('password'),
        viewing_key=a.get('viewing_key'),
        account=int(a['account']) if a.get('account') is not None else None)


def _t_shielded_export(a):
    return get_mod().shielded_export(
        name=a['wallet'], password=a['password'],
        account=int(a['account']) if a.get('account') is not None else None)


def _t_shielded_upgrade(a):
    return get_mod().shielded_upgrade(
        name=a['wallet'], password=a['password'],
        birthday=int(a['birthday']) if a.get('birthday') is not None else None)


def _t_shielded_send(a):
    return get_mod().shielded_send(
        name=a['wallet'], password=a['password'], to=a['to'],
        amount=float(a['amount']), memo=a.get('memo'),
        broadcast=bool(a.get('broadcast')), from_address=a.get('from_address'),
        account=int(a['account']) if a.get('account') is not None else None)


def _t_shielded_backend(a):
    """The prover: where it is, or how to get one."""
    m = get_mod()
    if a.get('action') == 'install':
        return m.shielded_backend_install(force=bool(a.get('force')))
    return m.shielded_backend()


def _t_shielded_sync(a):
    """The light client's scan: start it, watch it, stop it."""
    m = get_mod()
    action = a.get('action') or 'status'
    if action == 'start':
        return m.shielded_sync_start(
            name=a['wallet'], password=a.get('password'),
            birthday=int(a['birthday']) if a.get('birthday') is not None else None)
    if action == 'stop':
        return m.shielded_sync_stop(name=a['wallet'])
    return m.shielded_sync_status(name=a['wallet'])


def _t_shielded_spendable(a):
    return get_mod().shielded_spendable(name=a['wallet'])


def _t_shielded_shield(a):
    return get_mod().shielded_shield(
        name=a['wallet'], password=a['password'],
        broadcast=bool(a.get('broadcast')))


def _t_shielded_node(a):
    mod = get_mod()
    action = (a.get('action') or 'status').lower()
    if action in ('import', 'import_key'):
        return mod.shielded_node_import(
            name=a['wallet'], password=a['password'],
            rescan=a.get('rescan') or 'whenkeyisnew',
            account=int(a['account']) if a.get('account') is not None else None)
    if action in ('status', 'operation'):
        if not a.get('operation_id'):
            raise Refused('status needs operation_id — the opid a node-side send '
                          'returned')
        return mod.shielded_operation(operation_id=a['operation_id'])
    raise Refused(f'unknown action {action!r} — import or status')


# ── handlers: spending and bridging ──────────────────────────────────

def _t_send(a):
    return get_mod().send(
        name=a['wallet'], password=a['password'], to=a['to'],
        amount=float(a['amount']) if a.get('amount') is not None else None,
        broadcast=bool(a.get('broadcast')),
        amount_zatoshi=int(a['amount_zatoshi']) if a.get('amount_zatoshi') is not None else None,
        from_address=a.get('from_address'),
        fee_zatoshi=int(a['fee_zatoshi']) if a.get('fee_zatoshi') is not None else None)


def _t_broadcast(a):
    return get_mod().broadcast_raw(raw_transaction=a['raw_transaction'])


def _t_bridge_chains(a):
    return get_mod().bridge_chains()


def _t_bridge_quote(a):
    return get_mod().bridge_quote(
        to_asset=a['to_asset'], amount=float(a['amount']),
        recipient=a['recipient'], refund_to=a['refund_to'],
        from_asset=a.get('from_asset') or 'ZEC')


def _t_bridge_start(a):
    return get_mod().bridge_start(
        to_asset=a['to_asset'], amount=float(a['amount']),
        recipient=a['recipient'], refund_to=a['refund_to'],
        from_asset=a.get('from_asset') or 'ZEC',
        slippage_bps=int(a.get('slippage_bps') or 100))


def _t_bridge_payment(a):
    return get_mod().bridge_payment(
        from_asset=a['from_asset'], amount=float(a['amount']),
        deposit_address=a['deposit_address'])


def _t_bridge_networks(a):
    return get_mod().bridge_networks()


def _t_bridge_status(a):
    return get_mod().bridge_status(deposit_address=a['deposit_address'])


def _t_bridge_maya(a):
    return get_mod().bridge_maya(
        to_asset=a.get('to_asset'),
        amount=float(a['amount']) if a.get('amount') is not None else None,
        destination=a.get('destination'))


def _t_bridge_send(a):
    return get_mod().bridge_send(
        name=a['wallet'], password=a['password'], to_asset=a['to_asset'],
        amount=float(a['amount']), recipient=a['recipient'],
        broadcast=bool(a.get('broadcast')),
        slippage_bps=int(a.get('slippage_bps') or 100),
        refund_to=a.get('refund_to'))


def _t_bridge_shielded_plan(a):
    return get_mod().bridge_shielded_plan()


def _t_bridge_shielded_address(a):
    return get_mod().bridge_shielded_address(
        address=a.get('address'), name=a.get('wallet') or a.get('name'))


def _t_bridge_shielded_in(a):
    return get_mod().bridge_shielded_in(
        from_asset=a['from_asset'], amount=float(a['amount']),
        refund_to=a['refund_to'], recipient=a.get('recipient'),
        name=a.get('wallet') or a.get('name'),
        reserve=bool(a.get('reserve')),
        via_transparent=a.get('via_transparent'),
        accept_public_leg=bool(a.get('accept_public_leg')),
        slippage_bps=int(a.get('slippage_bps') or 100))


def _t_bridge_shielded_out(a):
    return get_mod().bridge_shielded_out(
        name=a['wallet'], password=a['password'], to_asset=a['to_asset'],
        amount=float(a['amount']), recipient=a['recipient'],
        broadcast=bool(a.get('broadcast')), refund_to=a.get('refund_to'),
        slippage_bps=int(a.get('slippage_bps') or 100),
        from_address=a.get('from_address'))


def _t_learn(a):
    return get_mod().learn(topic=a.get('topic'), level=a.get('level'),
                           path=a.get('path'),
                           glossary=bool(a.get('glossary')))


def _t_explain(a):
    return get_mod().explain(term=a['term'])


def _t_ask(a):
    return get_mod().ask(question=a['question'],
                         ground=a.get('ground', True) is not False)


# ── registry ─────────────────────────────────────────────────────────
#
# `fns` names the module functions a tool reaches. api.py gates the same
# functions on its REST routes, and tests/test_mcp.py asserts the two agree —
# so a tool cannot quietly become a way around the REST gate.

TOOLS = {
    'zec_capabilities': {
        'description': 'What this module can and cannot do, and why. Transparent '
                       'sending is real; Sapling addresses and note decryption are '
                       'real, and so are Orchard keys, addresses and note '
                       'decryption; creating a shielded output needs a zk-SNARK '
                       'proof Python will not produce, so spending needs the local '
                       'light client or a node. Also reports which shielded pools '
                       'can be read and whether a prover is available. '
                       'Read this before promising a user a shielded payment.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['capabilities'], 'handler': _t_capabilities,
    },
    'zec_info': {
        'description': 'The chain at a glance: tip height and hash, total '
                       'transactions, difficulty and 24h hashrate, mempool depth, '
                       'ZEC price, market cap and circulating supply against the 21M '
                       'cap. Supply carries a `circulation_source` because the '
                       'upstream explorer reports an impossible number for Zcash and '
                       'this derives a sane one; `stale` marks a cached answer served '
                       'through an upstream failure.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['info'], 'handler': _t_info,
    },
    'zec_search': {
        'description': 'One string in, whatever it turned out to be out: a block '
                       'height or hash, a txid, a transparent address, or a shielded '
                       'or unified address. Start here when you do not already know '
                       'which of those you are holding. A shielded address comes back '
                       'with its pool and receivers rather than a balance, because '
                       'there is no public balance to report.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('a height, block hash, txid, or any Zcash address')},
            'required': ['query']},
        'annotations': _READ, 'fns': ['search'], 'handler': _t_search,
    },
    'zec_block': {
        'description': 'One block: height, hash, time, size, transaction count, '
                       'total in and out, difficulty and the miner reward. With no '
                       'arguments it returns the current tip.',
        'inputSchema': {'type': 'object', 'properties': {
            'height': _int('block height — omit both arguments for the tip'),
            'hash': _str('block hash instead of a height')}},
        'annotations': _READ, 'fns': ['block'], 'handler': _t_block,
    },
    'zec_tx': {
        'description': 'One transaction: value in and out, fee, size, version, and '
                       'how much of it is shielded — the Sapling spend and output '
                       'counts and the net value moved into or out of the shielded '
                       'pool. The amounts and recipients of shielded outputs are '
                       'encrypted; zec_shielded_scan_tx opens the ones a viewing key '
                       'of yours can read.',
        'inputSchema': {'type': 'object', 'properties': {
            'txid': _str('the transaction id')}, 'required': ['txid']},
        'annotations': _READ, 'fns': ['tx'], 'handler': _t_tx,
    },
    'zec_address': {
        'description': 'Balance, received and spent totals, transaction count and '
                       'UTXO count for a TRANSPARENT (t1/t3) address. Shielded '
                       'addresses have no public balance — pass one to zec_validate '
                       'or zec_search instead, and read your own with '
                       'zec_shielded_scan.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('a t1 or t3 address')}, 'required': ['address']},
        'annotations': _READ, 'fns': ['address'], 'handler': _t_address,
    },
    'zec_price': {
        'description': 'ZEC price in USD with market cap, market dominance and '
                       'circulating supply. Cached for 30 seconds and marked `stale` '
                       'with an age when the upstream is down and the last good copy '
                       'is being served.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['price'], 'handler': _t_price,
    },
    'zec_network': {
        'description': 'Network health: block height and time, difficulty, 24h '
                       'hashrate, reachable nodes, chain size, mempool depth, and the '
                       'live consensus branch id — which is what says whether this '
                       'module is building transactions for the network upgrade the '
                       'chain is actually on.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['network'], 'handler': _t_network,
    },
    'zec_validate': {
        'description': 'Is this a real Zcash address, and what kind? Names the pool '
                       '(transparent p2pkh/p2sh, Sapling zs1, unified u1), lists a '
                       'unified address\'s receivers and which one a payment from '
                       'here would land on, and says plainly whether this module can '
                       'pay it. Check a destination with this before spending: '
                       'transparent sends are the only ones it can make.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('any Zcash address')}, 'required': ['address']},
        'annotations': _READ, 'fns': ['validate'], 'handler': _t_validate,
    },
    'zec_estimate_fee': {
        'description': 'The ZIP-317 conventional fee for a transparent transaction '
                       'of a given shape, plus its estimated size. Zcash fees are set '
                       'by input and output counts, not by a fee market, so this is '
                       'exact rather than a guess.',
        'inputSchema': {'type': 'object', 'properties': {
            'inputs': _int('number of inputs (default 1)'),
            'outputs': _int('number of outputs (default 2 — payment plus change)')}},
        'annotations': _READ, 'fns': ['estimate_fee'], 'handler': _t_estimate_fee,
    },
    'zec_status': {
        'description': 'Whether this module\'s three services are up (the mod '
                       'protocol port, the web app and the REST API behind it), the '
                       'chain tip it can see, and how many wallets are on the box. '
                       'The diagnostic to run when another tool says it cannot reach '
                       'anything.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['status'], 'handler': _t_status,
    },

    # ── wallets ──
    'zec_wallets': {
        'description': 'Every wallet on this box, by name, with the directory they '
                       'are stored in. Names from here are what every other wallet '
                       'tool takes as `wallet`. No password needed.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['wallet_list'], 'handler': _t_wallets,
    },
    'zec_wallet': {
        'description': 'One wallet\'s addresses, labels, derivation paths, shielded '
                       'account and birthday height. Public information — no password '
                       'and no network call. For what it holds, use zec_balance.',
        'inputSchema': {'type': 'object', 'properties': {'wallet': _WALLET},
                        'required': ['wallet']},
        'annotations': _READ, 'fns': ['wallet_info'], 'handler': _t_wallet,
    },
    'zec_balance': {
        'description': 'What a wallet holds: the confirmed transparent balance of '
                       'every address in it, totalled in ZEC, zatoshi and USD. Pass '
                       'utxos=true to also list the individual spendable outputs, '
                       'which is what a send actually consumes. This is TRANSPARENT '
                       'value only — shielded notes are found by zec_shielded_scan.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET,
            'utxos': _bool('also list every spendable output, not just the totals')},
            'required': ['wallet']},
        'annotations': _READ, 'fns': ['wallet_balance', 'wallet_utxos'],
        'handler': _t_balance,
    },
    'zec_wallet_create': {
        'description': 'Create a BIP39 HD wallet, or restore one by passing '
                       '`mnemonic`. Derives transparent addresses (m/44\'/133\') and '
                       'a Sapling account (m/32\'/133\') from the same seed, and '
                       'encrypts the seed with the password at rest. THE SEED PHRASE '
                       'IS RETURNED EXACTLY ONCE — show it to the user and tell them '
                       'it is the only copy. When restoring, pass `birthday` (the '
                       'height the wallet first received funds) if it is known: a '
                       'shielded scan starts there, and the default is today, which '
                       'finds nothing older.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _str('a name for the new wallet'),
            'password': _str('encrypts the seed at rest — there is no recovery if it '
                             'is lost'),
            'mnemonic': _str('a BIP39 seed phrase to restore from; omit to create a '
                             'new one'),
            'addresses': _int('how many receive addresses to derive (default 1)'),
            'strength': _int('entropy bits for a new seed: 256 (24 words, default) '
                             'or 128 (12 words)'),
            'passphrase': _str('optional BIP39 passphrase — a different wallet, not '
                               'a password'),
            'birthday': _int('for a restore: the chain height to scan shielded notes '
                             'from')},
            'required': ['wallet', 'password']},
        'annotations': _WRITE, 'fns': ['wallet_create', 'wallet_restore'],
        'handler': _t_wallet_create,
    },
    'zec_new_address': {
        'description': 'Derive the next receive address for a wallet — transparent '
                       'by default, or a fresh diversified Sapling address with '
                       'shielded=true. A new address per payer is the usual privacy '
                       'hygiene; both kinds come from the same seed, so nothing needs '
                       'backing up again.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'shielded': _bool('derive a shielded (zs1/unified) address instead of a '
                              'transparent one'),
            'label': _str('a note to remember what this address is for')},
            'required': ['wallet', 'password']},
        'annotations': _WRITE,
        'fns': ['wallet_new_address', 'shielded_new_address'],
        'handler': _t_new_address,
    },
    'zec_import_key': {
        'description': 'Import a WIF private key into a wallet, creating the wallet '
                       'if it does not exist. The imported key is stored alongside '
                       'the HD addresses and spends like them — but it is NOT '
                       'recoverable from the seed phrase, so its own backup still '
                       'matters.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _str('wallet to import into — created if new'),
            'password': _PASSWORD,
            'wif': _str('the WIF-encoded private key'),
            'label': _str('a note to remember what this key is')},
            'required': ['wallet', 'password', 'wif']},
        'annotations': _WRITE, 'fns': ['wallet_import'], 'handler': _t_import_key,
    },
    'zec_label': {
        'description': 'Set or change the label on one of a wallet\'s addresses. '
                       'Local bookkeeping only — nothing about it is on chain.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'address': _str('the address to label'),
            'label': _str('the new label')},
            'required': ['wallet', 'address', 'label']},
        'annotations': _WRITE, 'fns': ['wallet_label'], 'handler': _t_label,
    },
    'zec_wallet_reveal': {
        'description': 'Print the wallet\'s seed phrase and any imported private '
                       'keys. Anyone holding this output owns the funds forever, '
                       'including after the wallet file is deleted — do not call it '
                       'to "check" something, and do not repeat the output anywhere '
                       'it will be logged.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD},
            'required': ['wallet', 'password']},
        'annotations': _SPEND, 'fns': ['wallet_reveal'], 'handler': _t_reveal,
    },
    'zec_wallet_delete': {
        'description': 'Delete a wallet file from this box. The password must verify '
                       'first, and confirm=true is required, because this erases the '
                       'only encrypted copy of the seed — if the phrase is not '
                       'written down elsewhere, the funds are unrecoverable.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'confirm': _bool('REQUIRED (true) — acknowledges that the seed on this '
                             'box is destroyed')},
            'required': ['wallet', 'password']},
        'annotations': _SPEND, 'fns': ['wallet_delete'], 'handler': _t_delete,
    },

    # ── shielded ──
    'zec_shielded_address': {
        'description': 'A wallet\'s shielded receive addresses: the zs1 Sapling form '
                       'and the ZIP-316 unified address that carries it alongside a '
                       'transparent receiver. Hand out the unified one where you can '
                       '— any wallet can pay it. Public information, no password. '
                       'The unified address carries both shielded pools, Sapling and '
                       'Orchard, plus a transparent receiver. That transparent '
                       'receiver is why it is not a private address to hand a '
                       'bridge: a sender may pick it. Use '
                       'zec_bridge_shielded_address for the shielded-only form.',
        'inputSchema': {'type': 'object', 'properties': {'wallet': _WALLET},
                        'required': ['wallet']},
        'annotations': _READ, 'fns': ['shielded_address'],
        'handler': _t_shielded_address,
    },
    'zec_shielded_scan': {
        'description': 'Find the shielded payments a wallet received, by trial-'
                       'decrypting every Sapling output in a block range with its '
                       'incoming viewing key (and its own sends with the outgoing '
                       'key). Returns the totals; detail=true returns each note with '
                       'its value and memo. Defaults to the wallet birthday through '
                       'the tip, capped at roughly 4000 blocks without a node. '
                       'SPEND DETECTION NEEDS A NODE: without ZCASH_RPC_URL this is '
                       'what was RECEIVED, and unspent value comes back null rather '
                       'than a wrong number.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'from_height': _int('start height (default: the wallet birthday)'),
            'to_height': _int('end height (default: the tip) — with detail=true'),
            'blocks': _int('scan this many blocks back from the end instead'),
            'detail': _bool('return every decrypted note, not just the totals'),
            'account': _int('ZIP-32 account index (default: the wallet\'s)')},
            'required': ['wallet', 'password']},
        'annotations': _READ,
        'fns': ['shielded_scan', 'shielded_balance'], 'handler': _t_shielded_scan,
    },
    'zec_shielded_scan_tx': {
        'description': 'Open one transaction\'s shielded outputs with a viewing key '
                       '— either a wallet\'s (name + password) or a `zxviews...` '
                       'extended full viewing key you paste in. Notes the key can '
                       'read come back decrypted with value and memo; the rest stay '
                       'encrypted, as they should. Use it to confirm a specific '
                       'payment landed without scanning a range.',
        'inputSchema': {'type': 'object', 'properties': {
            'txid': _str('the transaction to open'),
            'wallet': _str('scan with this wallet\'s viewing key'),
            'password': _PASSWORD,
            'viewing_key': _str('a zxviews... extended full viewing key, instead of '
                                'a wallet'),
            'account': _int('ZIP-32 account index')},
            'required': ['txid']},
        'annotations': _READ, 'fns': ['shielded_scan_tx'],
        'handler': _t_shielded_scan_tx,
    },
    'zec_shielded_export': {
        'description': 'Export the account\'s Sapling keys: the extended spending '
                       'key (secret-extended-key-main) and the extended full viewing '
                       'key (zxviews). This is the escape hatch for spending — import '
                       'the spending key into Zashi, Ywallet, zingo or zcashd, which '
                       'can produce the Groth16 proof this module cannot. The '
                       'spending key IS the money; the viewing key reveals every '
                       'payment ever received.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'account': _int('ZIP-32 account index (default: the wallet\'s)')},
            'required': ['wallet', 'password']},
        'annotations': _SPEND, 'fns': ['shielded_export'],
        'handler': _t_shielded_export,
    },
    'zec_shielded_upgrade': {
        'description': 'Give a wallet created before shielded support its Sapling '
                       'account, derived from the seed it already has. Nothing is '
                       'lost and no new backup is needed — the same phrase covers '
                       'both halves. Set `birthday` to the height worth scanning '
                       'from if the wallet is old.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'birthday': _int('height to scan shielded notes from (default: today)')},
            'required': ['wallet', 'password']},
        'annotations': _WRITE, 'fns': ['shielded_upgrade'],
        'handler': _t_shielded_upgrade,
    },
    'zec_shielded_send': {
        'description': 'Send shielded ZEC for real — a zs1 or unified address, '
                       'proved locally and broadcast. Needs the local light client: '
                       'run zec_shielded_backend action=install once per host, then '
                       'zec_shielded_sync action=start and wait for it to report '
                       'synced. If either step is missing this returns exactly which '
                       'one and how to fix it, rather than failing obscurely. DRY RUN '
                       'unless broadcast=true — the dry run checks the address, the '
                       'spendable balance and the sync state and prices the fee.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'to': _str('a zs1 or unified address'),
            'amount': _num('ZEC to send'),
            'memo': _str('an encrypted memo for the recipient (shielded only)'),
            'from_address': _str('which of the wallet\'s shielded addresses pays'),
            'account': _int('ZIP-32 account index'),
            'broadcast': _BROADCAST},
            'required': ['wallet', 'password', 'to', 'amount']},
        'annotations': _SPEND, 'fns': ['shielded_send'], 'handler': _t_shielded_send,
    },
    'zec_shielded_backend': {
        'description': 'The shielded prover — the thing that makes a shielded SEND '
                       'possible at all. action=status (default) says whether a '
                       'local zk-SNARK prover is installed on this host and where; '
                       'action=install builds it, once, from source (a Zcash light '
                       'client over zcash_client_backend and zcash_proofs — several '
                       'minutes of CPU, and it needs cargo). Nothing else about '
                       'shielded sending works until this reports installed.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('status (default) or install', enum=['status', 'install']),
            'force': _bool('rebuild even if it is already installed')},
            'required': []},
        'annotations': _WRITE,
        'fns': ['shielded_backend', 'shielded_backend_install'],
        'handler': _t_shielded_backend,
    },
    'zec_shielded_sync': {
        'description': "The light client's scan of this wallet's shielded notes — "
                       'the step between having keys and being able to spend. '
                       'action=start sets the light client up from the wallet seed '
                       '(needs the password the first time) and scans in the '
                       'background from the wallet birthday; it returns immediately. '
                       'action=status (default) reports how far it got and whether '
                       'it can spend yet; action=stop halts it. A wallet with an old '
                       'birthday can take a long time to scan the first time.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('start, status (default) or stop',
                           enum=['start', 'status', 'stop']),
            'wallet': _WALLET, 'password': _PASSWORD,
            'birthday': _int('block to scan from, overriding the wallet birthday')},
            'required': ['wallet']},
        'annotations': _WRITE,
        'fns': ['shielded_sync_start', 'shielded_sync_status',
                'shielded_sync_stop'],
        'handler': _t_shielded_sync,
    },
    'zec_shielded_spendable': {
        'description': 'The shielded balance that can actually be SPENT, per pool, as '
                       "the light client's own note commitment tree sees it. This is "
                       'the number zec_shielded_send is checked against — unlike '
                       'zec_shielded_scan, which reports what was received and cannot '
                       'always tell what has since been spent.',
        'inputSchema': {'type': 'object', 'properties': {'wallet': _WALLET},
                        'required': ['wallet']},
        'annotations': _READ, 'fns': ['shielded_spendable'],
        'handler': _t_shielded_spendable,
    },
    'zec_shielded_shield': {
        'description': "Move a wallet's TRANSPARENT balance into the shielded pool, "
                       'so it can then be spent privately. Shielding creates a '
                       'shielded output, so it needs the same local prover a send '
                       'does. DRY RUN unless broadcast=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD, 'broadcast': _BROADCAST},
            'required': ['wallet', 'password']},
        'annotations': _SPEND, 'fns': ['shielded_shield'],
        'handler': _t_shielded_shield,
    },
    'zec_shielded_node': {
        'description': 'The node-backed half of shielded spending. action=import '
                       'hands this wallet\'s Sapling spending key to the configured '
                       'zcashd/zebrad node so it can prove and sign; action=status '
                       'reads the result of a node-side send by its operation id. '
                       'Both need ZCASH_RPC_URL — see zec_capabilities.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('import (give the node the key) or status (read an opid)',
                           enum=['import', 'status']),
            'wallet': _WALLET, 'password': _PASSWORD,
            'rescan': _str('for import: no, whenkeyisnew (default) or yes'),
            'operation_id': _str('for status: the opid the node returned'),
            'account': _int('ZIP-32 account index')},
            'required': ['action']},
        'annotations': _WRITE,
        'fns': ['shielded_node_import', 'shielded_operation'],
        'handler': _t_shielded_node,
    },

    # ── spending ──
    'zec_send': {
        'description': 'Pay a transparent (t1/t3) address from a wallet. Builds a '
                       'real NU5 v5 transaction, selects UTXOs, signs it with '
                       'ZIP-244 and prices it with ZIP-317 — then STOPS unless the '
                       'call carries broadcast=true. A dry run returns the fee, the '
                       'inputs chosen, the change and the signed hex, so you can show '
                       'the user exactly what would happen before it does. This '
                       'module cannot pay a shielded address; zec_validate says so '
                       'before you try.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'to': _str('destination t1/t3 address'),
            'amount': _num('ZEC to send'),
            'amount_zatoshi': _int('exact amount in zatoshi, instead of `amount`'),
            'from_address': _str('spend only from this address of the wallet'),
            'fee_zatoshi': _int('override the ZIP-317 fee (rarely a good idea)'),
            'broadcast': _BROADCAST},
            'required': ['wallet', 'password', 'to']},
        'annotations': _SPEND, 'fns': ['send'], 'handler': _t_send,
    },
    'zec_broadcast': {
        'description': 'Publish an already-signed raw transaction hex to the network '
                       '— for example the one a zec_send dry run produced. '
                       'Irreversible the moment it is accepted.',
        'inputSchema': {'type': 'object', 'properties': {
            'raw_transaction': _str('signed transaction hex')},
            'required': ['raw_transaction']},
        'annotations': _SPEND, 'fns': ['broadcast_raw'], 'handler': _t_broadcast,
    },

    # ── bridge ──
    'zec_bridge_chains': {
        'description': 'Every chain and asset ZEC can be bridged to or from, across '
                       'the two routes this module uses: NEAR Intents (~35 chains, no '
                       'API key) and Maya Protocol. Asset names look like ETH, '
                       'eth:USDC, base:ETH, BTC, SOL — that spelling is what the '
                       'quote and send tools take.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['bridge_chains'], 'handler': _t_bridge_chains,
    },
    'zec_bridge_quote': {
        'description': 'Price a bridge without reserving anything: amount out, rate, '
                       'fees, ETA and the deadline. Costs nothing and moves nothing. '
                       'Set from_asset to bridge INTO ZEC, in which case `recipient` '
                       'is your t-address. Quote before every bridge — ZEC routes are '
                       'thin and the rate moves.',
        'inputSchema': {'type': 'object', 'properties': {
            'to_asset': _str('destination asset — ETH, eth:USDC, BTC, base:ETH, …'),
            'amount': _num('how much of the source asset to bridge'),
            'recipient': _str('address on the destination chain (EIP-55 checked for '
                              'EVM chains)'),
            'refund_to': _str('where funds return if the swap fails — a ZEC t-address '
                              'when bridging out'),
            'from_asset': _str('source asset (default ZEC)')},
            'required': ['to_asset', 'amount', 'recipient', 'refund_to']},
        'annotations': _READ, 'fns': ['bridge_quote'], 'handler': _t_bridge_quote,
    },
    'zec_bridge_start': {
        'description': 'Reserve a real deposit address for a bridge and hold the '
                       'quote. Nothing moves until that address is funded — you can '
                       'do it from a wallet here with zec_send, or from anywhere '
                       'else. Use zec_bridge_send instead to quote and pay in one '
                       'step; use this when the ZEC is somewhere else.',
        'inputSchema': {'type': 'object', 'properties': {
            'to_asset': _str('destination asset — ETH, eth:USDC, BTC, …'),
            'amount': _num('how much of the source asset to bridge'),
            'recipient': _str('address on the destination chain'),
            'refund_to': _str('where funds return if the swap fails'),
            'from_asset': _str('source asset (default ZEC)'),
            'slippage_bps': _int('slippage tolerance in basis points (default 100)')},
            'required': ['to_asset', 'amount', 'recipient', 'refund_to']},
        'annotations': _WRITE, 'fns': ['bridge_start'], 'handler': _t_bridge_start,
    },
    'zec_learn': {
        'description': 'Plain-language Zcash lessons for someone starting from '
                       'zero: what Zcash is, why one wallet has two kinds of '
                       'address, how shielded notes work, what this module can '
                       'and cannot do, how bridging works and what it leaks. '
                       'No arguments lists them; `topic` opens one in full; '
                       '`path` follows a reading order. Read a lesson before '
                       'explaining Zcash to a user from memory — this text is '
                       'written to match what this module actually does.',
        'inputSchema': {'type': 'object', 'properties': {
            'topic': _str('a lesson id or a phrase to match, e.g. '
                          '"private-bridging" or "shielded notes"'),
            'level': _str('start, core or deep'),
            'path': _str('a reading order: beginner, sending, privacy, '
                         'bridging or developer'),
            'glossary': _bool('true returns every defined term instead')}},
        'annotations': _READ, 'fns': ['learn'], 'handler': _t_learn,
    },
    'zec_explain': {
        'description': 'Define one Zcash term in a sentence or two, with the '
                       'lesson that puts it in context. Understands the way '
                       'beginners phrase things — "zaddr", "gas", "seed", '
                       '"snark" all resolve.',
        'inputSchema': {'type': 'object', 'properties': {
            'term': _str('the word to define')}, 'required': ['term']},
        'annotations': _READ, 'fns': ['explain'], 'handler': _t_explain,
    },
    'zec_ask': {
        'description': 'Ask a question about Zcash or this module in plain '
                       'words and get an answer grounded in the written '
                       'lessons plus live reads. Returns the answer, the '
                       'lessons it came from, the glossary terms involved, and '
                       '`actions` — the exact calls to make next. It never '
                       'calls anything that spends, deletes or reveals a '
                       'secret; those come back as actions for the caller to '
                       'run deliberately. Good for "why did nothing send", '
                       '"can I bridge into a shielded address", "is my viewing '
                       'key safe to share".',
        'inputSchema': {'type': 'object', 'properties': {
            'question': _str('the question, in plain language'),
            'ground': _bool('true (default) lets it call read-only functions '
                            'to put live data in the answer')},
            'required': ['question']},
        'annotations': _READ, 'fns': ['ask'], 'handler': _t_ask,
    },
    'zec_bridge_shielded_plan': {
        'description': 'Which shielded bridge directions work on this '
                       'deployment right now, what each one needs, and exactly '
                       'what each one leaks. Bridging INTO the shielded pool '
                       'works with nothing extra; bridging OUT needs a proving '
                       'node and cannot be made private. Also reports which '
                       'shielded pools this module can read, which is what '
                       'decides whether a recipient address is safe to use.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['bridge_shielded_plan'],
        'handler': _t_bridge_shielded_plan,
    },
    'zec_bridge_shielded_address': {
        'description': 'The address a bridge should be given so the payment '
                       'lands SHIELDED. Give it a zs1, a u1 or a wallet name. '
                       'A bare zs1 is wrapped into a unified address (the '
                       'router rejects zs1). A unified address that also '
                       'carries a transparent receiver comes back re-encoded '
                       'without it — otherwise the solver picks the cheap '
                       'transparent receiver and pays you in the clear — and '
                       'so does one carrying a pool this module cannot '
                       'decrypt. The response says what was removed and why.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('a zs1 or u1 address'),
            'wallet': _str('a wallet name, to use its own shielded address')}},
        'annotations': _READ, 'fns': ['bridge_shielded_address'],
        'handler': _t_bridge_shielded_address,
    },
    'zec_bridge_shielded_in': {
        'description': 'Bridge an asset from another chain STRAIGHT INTO the '
                       'shielded pool — the funds arrive as an encrypted note, '
                       'with no transparent hop and no second transaction, '
                       'whenever the router will pay a shielded address. '
                       'Whether it will is the ROUTER\'s decision and it has '
                       'changed: when it refuses, nothing is reserved and this '
                       'returns the two-leg fallback instead (bridge to a '
                       'transparent address you own, then zec_shielded_shield) '
                       'with `shielded: false` and both legs labelled. Read '
                       'that field before telling anyone the money lands '
                       'private. `refund_to` is on the ORIGIN chain: a refund '
                       'cannot be paid into the shielded pool. Quotes only '
                       'unless reserve=true; reserving returns a deposit '
                       'address the user funds themselves from the origin '
                       'chain, since it is not on Zcash and this module cannot '
                       'pay it.',
        'inputSchema': {'type': 'object', 'properties': {
            'from_asset': _str("what you are paying with: 'eth:USDC', 'ETH', "
                               "'BTC', 'base:ETH' — zec_bridge_chains lists them"),
            'amount': _num('how much of from_asset to send'),
            'refund_to': _str('your address on the ORIGIN chain, where the '
                              'money returns if the swap fails'),
            'recipient': _str('your own zs1 or u1 Zcash address'),
            'wallet': _str('a wallet name instead, to use its shielded address'),
            'reserve': _bool('false (default) only quotes; true reserves a real '
                             'deposit address and starts the clock'),
            'slippage_bps': _int('slippage tolerance in basis points (100 = 1%)'),
            'via_transparent': _str('a t-address the USER owns, used only if '
                                    'the router refuses the shielded address: '
                                    'the ZEC lands there in the open and they '
                                    'shield it afterwards. A `wallet` already '
                                    'supplies one'),
            'accept_public_leg': _bool('false (default). reserve=true on the '
                                       'fallback route needs this too, because '
                                       'someone who asked to reserve a SHIELDED '
                                       'bridge has not agreed to fund a public '
                                       'one — ask them first')},
            'required': ['from_asset', 'amount', 'refund_to']},
        'annotations': _WRITE, 'fns': ['bridge_shielded_in'],
        'handler': _t_bridge_shielded_in,
    },
    'zec_bridge_shielded_out': {
        'description': 'Bridge shielded ZEC out to another chain. Read the '
                       'privacy field aloud before using this: leaving the '
                       'shielded pool is PUBLIC — the solver deposit address '
                       'is transparent, so the amount is in the clear and '
                       'links to the destination address by timing. It also '
                       'needs a Groth16 proof: with ZCASH_RPC_URL set the node '
                       'proves and sends; without one this reserves the '
                       'deposit address and returns the exact payment to make '
                       'from a proving wallet, which is a completable state, '
                       'not a failure. Dry run unless broadcast=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'to_asset': _str("what to receive: 'ETH', 'eth:USDC', 'BTC'"),
            'amount': _num('how much ZEC to send from the shielded pool'),
            'recipient': _str('your address on the destination chain'),
            'refund_to': _str('a t-address of this wallet for refunds; '
                              'defaults to its first. It cannot be shielded — '
                              'no solver can refund into the pool.'),
            'from_address': _str('which shielded address to spend from; '
                                 'defaults to the account\'s first'),
            'broadcast': _BROADCAST,
            'slippage_bps': _int('slippage tolerance in basis points')},
            'required': ['wallet', 'password', 'to_asset', 'amount', 'recipient']},
        'annotations': _SPEND, 'fns': ['bridge_shielded_out'],
        'handler': _t_bridge_shielded_out,
    },
    'zec_bridge_send': {
        'description': 'Bridge ZEC out of a wallet in one step: reserve the deposit '
                       'address and pay it. DRY RUN unless broadcast=true — a dry run '
                       'reserves nothing, costs nothing, and shows both the quote and '
                       'the funding transaction that would be signed. With '
                       'broadcast=true the ZEC leaves the wallet and the response '
                       'carries the deposit address to track with zec_bridge_status.',
        'inputSchema': {'type': 'object', 'properties': {
            'wallet': _WALLET, 'password': _PASSWORD,
            'to_asset': _str('destination asset — ETH, eth:USDC, BTC, …'),
            'amount': _num('ZEC to bridge'),
            'recipient': _str('address on the destination chain'),
            'refund_to': _str('refund address (default: the wallet\'s first address)'),
            'slippage_bps': _int('slippage tolerance in basis points (default 100)'),
            'broadcast': _BROADCAST},
            'required': ['wallet', 'password', 'to_asset', 'amount', 'recipient']},
        'annotations': _SPEND, 'fns': ['bridge_send'], 'handler': _t_bridge_send,
    },
    'zec_bridge_payment': {
        'description': 'The exact EVM transaction that funds a bridge deposit '
                       'address. Bridging INTO ZEC is paid on the origin chain, '
                       'where this module holds no keys — so instead of signing '
                       'it hands back what to sign: chain id, to, value and '
                       'ERC-20 calldata, with the amount in base units. Give it '
                       'the origin asset, the amount_in and the deposit address '
                       'from zec_bridge_start, and pass the result to any '
                       'EIP-1193 wallet (MetaMask) or EVM signer.',
        'inputSchema': {'type': 'object', 'properties': {
            'from_asset': _str("the origin asset of the quote, e.g. 'eth:USDC' "
                               "or 'base:ETH'"),
            'amount': _num('exactly the amount_in the quote asked for'),
            'deposit_address': _str('the deposit address the router reserved')},
            'required': ['from_asset', 'amount', 'deposit_address']},
        'annotations': _READ, 'fns': ['bridge_payment'], 'handler': _t_bridge_payment,
    },
    'zec_bridge_networks': {
        'description': 'EVM networks a browser wallet can be pointed at to fund a '
                       'ZEC bridge deposit — chain ids, RPC and explorer for each, '
                       'plus the EVM chains the router reaches for which no chain '
                       'id is verified here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'fns': ['bridge_networks'], 'handler': _t_bridge_networks,
    },
    'zec_bridge_status': {
        'description': 'Track a bridge by the deposit address it was given: whether '
                       'the deposit arrived, whether the swap settled, and the '
                       'destination transaction when it has one.',
        'inputSchema': {'type': 'object', 'properties': {
            'deposit_address': _str('the deposit address from the quote or send')},
            'required': ['deposit_address']},
        'annotations': _READ, 'fns': ['bridge_status'], 'handler': _t_bridge_status,
    },
    'zec_bridge_maya': {
        'description': 'The Maya Protocol route for ZEC: its health with no '
                       'arguments, or a quote when given all three. Maya halts ZEC '
                       'trading fairly often and this reports that plainly rather '
                       'than quoting a route that will not execute — check it before '
                       'choosing Maya over the NEAR Intents route.',
        'inputSchema': {'type': 'object', 'properties': {
            'to_asset': _str('destination asset for a quote'),
            'amount': _num('ZEC to swap'),
            'destination': _str('destination address')}},
        'annotations': _READ, 'fns': ['bridge_maya'], 'handler': _t_bridge_maya,
    },
}


# ── policy ───────────────────────────────────────────────────────────

def _guarded_fns():
    """The functions api.py gates on its REST routes.

    Read out of the already-imported api module when there is one, never by
    importing it: mcp.py must work as a bare stdio server on a box with no
    fastapi, and importing api from here would load a *second* copy of this
    module with its own Mod instance.
    """
    api = sys.modules.get('api')
    guarded = getattr(api, 'GUARDED_FNS', None)
    return set(guarded) if guarded else set(_FALLBACK_GUARDED)


# Mirrors api.GUARDED_FNS. Kept here so the stdio server enforces the same
# policy without importing fastapi; tests/test_mcp.py asserts they agree.
_FALLBACK_GUARDED = {
    'wallet_create', 'wallet_restore', 'wallet_new_address', 'wallet_import',
    'wallet_reveal', 'wallet_delete', 'wallet_label',
    'send', 'broadcast_raw', 'bridge_start', 'bridge_send',
    'bridge_shielded_in', 'bridge_shielded_out',
    'shielded_new_address', 'shielded_upgrade', 'shielded_export',
    'shielded_scan', 'shielded_balance', 'shielded_scan_tx',
    'shielded_send', 'shielded_node_import', 'shielded_operation',
    'shielded_backend_install', 'shielded_sync_start', 'shielded_sync_status',
    'shielded_sync_stop', 'shielded_spendable', 'shielded_shield',
}


def needs_auth(name) -> bool:
    """True when a tool reaches any function the REST gate guards."""
    tool = TOOLS.get(name)
    if not tool:
        return True
    guarded = _guarded_fns()
    return any(fn in guarded for fn in tool['fns'])


OPEN_TOOLS = sorted(n for n in TOOLS if not any(
    fn in _FALLBACK_GUARDED for fn in TOOLS[n]['fns']))


# ── description ──────────────────────────────────────────────────────

def version():
    return CONFIG.get('version', '0.0.0')


def tool_list():
    return [{'name': name, 'description': tool['description'],
             'inputSchema': tool['inputSchema'],
             'annotations': tool['annotations']}
            for name, tool in TOOLS.items()]


def default_url():
    return (CONFIG.get('urls', {}).get('mcp')
            or f"http://localhost:{os.environ.get('ZCASH_REST_PORT', 8930)}/mcp")


def client_config(url=None):
    endpoint = url or default_url()
    return {
        'http': {'mcpServers': {SERVER_NAME: {
            'type': 'http', 'url': endpoint,
            'headers': {'Authorization': 'Bearer <module token — m zcash/token>'}}}},
        'stdio': {'mcpServers': {SERVER_NAME: {
            'command': 'python3', 'args': [str(HERE / 'mcp.py')]}}},
        'claude_cli': f'claude mcp add --transport http zcash {endpoint} '
                      f'--header "Authorization: Bearer $(m zcash/token)"',
        'curl': f"curl -s {endpoint} -H 'content-type: application/json' -d "
                f"'{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}}'",
    }


def describe(url=None):
    """Everything about this server in one document — what GET /mcp serves and
    what the console renders, so the schema is never something you have to run
    a client to see."""
    guarded = _guarded_fns()
    return {
        'server': {'name': SERVER_NAME, 'version': version(),
                   'description': CONFIG.get('description', '')[:400]},
        'protocol': {'default': DEFAULT_PROTOCOL_VERSION,
                     'supported': list(SUPPORTED_PROTOCOL_VERSIONS),
                     'jsonrpc': '2.0',
                     'methods': ['initialize', 'ping', 'tools/list', 'tools/call']},
        'transports': {
            'http': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                     'url': url or default_url(),
                     'note': 'Streamable HTTP, one JSON-RPC message (or a batch '
                             'array) per POST. Tools that spend or decrypt need '
                             'the module token.'},
            'stdio': {'command': f'python3 {HERE / "mcp.py"}',
                      'note': 'runs on this box as its owner — every tool '
                              'available, no token needed'},
        },
        'auth': {
            'token': 'Authorization: Bearer <the contents of '
                     f'{secret_path()}>, printed by `m zcash/token`',
            'open': OPEN_TOOLS,
            'guarded': sorted(n for n in TOOLS if needs_auth(n)),
            'why': 'the same functions api.py guards on its REST routes — '
                   'spending, wallet secrets, and shielded reads, because an '
                   'incoming viewing key exposes every payment an address ever '
                   'received',
        },
        'safety': {
            'dry_run': 'zec_send, zec_bridge_send and zec_shielded_send build and '
                       'sign but do not publish unless broadcast=true; the response '
                       'always names the mode it ran in',
            'shielded': 'Sapling and Orchard addresses, note decryption and '
                        'viewing keys are real; creating a shielded output needs a '
                        'zk-SNARK proof, so spending goes through the local light '
                        'client or a node — zec_capabilities is the authoritative '
                        'map',
            'passwords': 'a wallet password is never stored; it is passed per call '
                         'and only decrypts the seed for that call',
        },
        'instructions': INSTRUCTIONS,
        'count': len(TOOLS),
        'tools': [{'name': name, 'description': tool['description'],
                   'inputSchema': tool['inputSchema'],
                   'annotations': tool['annotations'],
                   'fns': tool['fns'],
                   'auth': 'token' if any(f in guarded for f in tool['fns']) else 'none',
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
    """Run one tool by name. Shared with the REST layer, so a route and a
    tools/call cannot diverge. Raises Refused with a reason worth reading."""
    tool = TOOLS.get(name)
    if not tool:
        raise Refused(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    ctx = ctx or LOCAL_CTX
    if needs_auth(name):
        ctx.require(name)
    args = _coerce(args)
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise Refused(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params, ctx):
    name = str((params or {}).get('name') or '')
    args = (params or {}).get('arguments') or {}
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
        return failed(f'{name}: {e}')
    except KeyError as e:
        return failed(f'{name}: missing argument {e}')
    except TypeError as e:
        return failed(f'{name}: bad arguments — {e}')
    except Exception as e:
        return failed(f'{name} failed: {type(e).__name__}: {e}')

    text = result if isinstance(result, str) else json.dumps(result, indent=2,
                                                             default=str)
    # Module functions report user-level failures in-band (bad address, wrong
    # password, no proving node) rather than raising. Flag those as isError so
    # a model does not read a refusal as a result -- but return the whole
    # payload, because the useful half is often the part next to the error:
    # shielded_send answers with the key to import elsewhere.
    failed_in_band = isinstance(result, dict) and bool(result.get('error'))
    out = {'content': [{'type': 'text', 'text': text}], 'isError': failed_in_band}
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
            'serverInfo': {'name': SERVER_NAME, 'version': version()},
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
        # of these tools rather than two that can disagree.
        import uvicorn

        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i]) if i > 0 else int(
            os.environ.get('ZCASH_REST_PORT') or os.environ.get('PORT') or 8930)
        uvicorn.run(api.app, host=os.environ.get('ZCASH_API_HOST', '127.0.0.1'),
                    port=port)
    else:
        serve_stdio()
