#!/usr/bin/env python3
"""eth mcp — the whole module as tools an agent can hold.

Forty-two tools over the same engine the CLI and the REST API use, so an agent
and a person are provably doing the same thing. The shape a model needs to
learn is small:

    pick a network → have an account → read for free, or unlock and write
    write Solidity → save it (it gets a CID) → test it on a testnet → deploy

Two safety properties are enforced here rather than trusted to the model, which
matters because these tools run unattended:

    **A non-testnet write refuses without `confirm: true`.** Not a setting —
    an argument on the call that spends the money. If a model has not been told
    to spend real funds, it cannot do so by accident.

    **A key is only usable while it is unlocked.** eth_unlock holds it in
    memory for a bounded time; every write either rides that unlock or carries
    the password. Nothing is signed with a key the caller did not just prove
    they can open.

Two transports, and they are not equal:

    stdio    python3 mcp.py — a local process holding the box's own mod key,
             so it acts as this box's own identity and may read local files.
    http     POST /mcp on the API — a remote caller, who must send a
             mod-protocol token as `Authorization: Bearer …`. Anything that
             touches a key needs it; reads do not.

Self-contained JSON-RPC 2.0 on the stdlib — no `mcp` package.

    python3 mcp.py                    # stdio
    python3 mcp.py --tools            # print the schema and exit
    curl -s localhost:50750/mcp | jq  # the same schema, over HTTP
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(str(HERE))

import catalog  # noqa: E402
import chains  # noqa: E402
import compiler  # noqa: E402
import harness  # noqa: E402
import identity  # noqa: E402
import ledger  # noqa: E402
import ops  # noqa: E402
import projects  # noqa: E402
import store_link  # noqa: E402
import wallet  # noqa: E402

CONFIG = json.loads((HERE / 'config.json').read_text())
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-06-18'

INSTRUCTIONS = (
    'Ethereum reads, transfers and Solidity contract deployment across EVM '
    'chains. Start with eth_status — it names the default network, whether the '
    'RPC answers, which solc versions are available and how many accounts the '
    'caller has. The normal arc is: eth_networks to pick a chain (`local` is an '
    'anvil node and free; `sepolia` and `base-sepolia` are free public '
    'testnets; everything else spends real money), eth_new_account or '
    'eth_accounts to get a signer, eth_unlock once so the following writes need '
    'no password, then eth_deploy / eth_send / eth_write. '
    'DEPLOYING: eth_templates lists nine ready contracts (token, nft, multisig, '
    'escrow, splitter, storage, anchor, vault, counter) — pass `template` and '
    '`args` for the constructor, or pass your own `source`. eth_deploy compiles '
    'and deploys in one call and remembers the ABI, so eth_read and eth_write '
    'work on the address afterwards with no ABI argument. '
    'MONEY: any write on a chain that is not a testnet is REFUSED unless the '
    'call carries confirm=true. That is deliberate — do not set it unless the '
    'user asked for a real transaction on that chain. Amounts are human units '
    'by default ("0.1" is 0.1 ETH); write "1000000wei" or pass an integer for '
    'wei. Gas is estimated before every write, so a call that would revert '
    'fails without spending anything. '
    'CONTRACTS YOU ARE WRITING: eth_save_project puts Solidity in the store '
    'module and hands back a CID — that CID is both the version and the way to '
    'share it (eth_share_project publishes it; eth_open_project reads anyone '
    'else\'s by CID, no account needed; eth_fork_project copies one into your '
    'workspace). eth_test deploys to a testnet and runs a suite of real '
    'transactions against it, so "it works" means receipts rather than an '
    'opinion; eth_generate_tests writes the starter suite. Test before you '
    'deploy anywhere that costs money — a contract cannot be edited once it is '
    'out there. '
    'Reads (eth_balance, eth_block, eth_tx, eth_gas, eth_read, eth_logs, '
    'eth_token, eth_compile) need no account and no token.'
)


def _core():
    """This module's own mod.py, by path — `import mod` is the protocol."""
    spec = importlib.util.spec_from_file_location('eth_core', HERE / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _core()


class Refused(Exception):
    """A tool that will not run, with the reason a model can act on."""


# ── who is calling ───────────────────────────────────────────────────

class Ctx:
    """The caller and how much of this box they may touch.

    Over stdio the caller is the box: it holds the mod key, so it is the same
    address the API would derive from a token that key minted, and reading
    local files is fair game. Over HTTP the caller is whoever signed the token,
    and nothing else.
    """

    def __init__(self, token=None, local=True):
        self._token = identity.strip(token)
        self.local = bool(local)

    def owner(self, required=True):
        if self._token:
            try:
                return identity.from_token(self._token)
            except identity.AuthError as e:
                raise Refused(str(e))
        if self.local:
            return CORE.Mod().owner
        if identity.open_mode():
            return identity.OPEN_ADDRESS
        if required:
            raise Refused('this tool spends or exposes a key, so it needs you: '
                          'send a mod-protocol token as `Authorization: Bearer '
                          '<token>` on the MCP request (mint one with '
                          'm.mod("auth")().token({}))')
        return None

    def token(self, required=True):
        """The caller's own protocol token, for forwarding to the store.

        Over HTTP that is the token they sent; on stdio this process holds the
        box's own key and mints one. Never a token belonging to somebody else:
        the store's whitelist and quota apply to whoever is actually asking.
        """
        if self._token:
            return self._token
        if self.local:
            return store_link.local_token()
        if required:
            raise Refused('storing a project needs your identity — send a '
                          'mod-protocol token as `Authorization: Bearer <token>`')
        return None

    def filesystem(self, what):
        if not self.local:
            raise Refused(f'{what} names a path on the server\'s filesystem, '
                          'which an HTTP caller does not share — pass the '
                          'content inline instead')


LOCAL_CTX = Ctx()


# ── schema helpers ───────────────────────────────────────────────────

def _str(desc):
    return {'type': 'string', 'description': desc}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


def _arr(desc):
    return {'type': 'array', 'description': desc, 'items': {}}


_NETWORK = _str('chain to use — a name from eth_networks (local, mainnet, '
                'sepolia, base, base-sepolia, arbitrum, optimism, polygon…), a '
                'chain id, or an https rpc url. Defaults to ETH_NETWORK (local).')
_ACCOUNT = _str('which of your accounts signs — a name from eth_accounts')
_PASSWORD = _str('the account password; unnecessary if eth_unlock is still live')
_CONFIRM = _bool('REQUIRED (true) to spend on a chain that is not a testnet. '
                 'Only set this when the user has asked for a real transaction.')
_ADDRESS = _str('an 0x address, one of your account names, or an ENS name')
_VALUE = {'description': 'amount — a decimal string is the human unit ("0.1" = '
                         '0.1 ETH), an integer or a "…wei" suffix is wei',
          'type': ['string', 'number']}

_READ = {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': True}
_WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}
_SPEND = {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False,
          'openWorldHint': True}


# ── tools: looking around ────────────────────────────────────────────

def _t_status(a, ctx):
    owner = ctx.owner(required=False)
    out = {
        'network': chains.reachable(a.get('network')),
        'networks': [n['name'] for n in chains.summary()],
        'solc': compiler.status(),
        'templates': catalog.names(),
        'auth': {'signed_in': bool(owner), 'address': owner,
                 'open_mode': identity.open_mode()},
        'accounts': [r['name'] for r in wallet.listing(owner)] if owner else [],
    }
    if owner:
        out['index'] = ledger.counts(owner)
    if not out['accounts']:
        out['next'] = ('no signer yet — eth_new_account makes one, or '
                       'eth_import_account brings a key in')
    return out


def _t_networks(a, ctx):
    if a.get('check'):
        return {'networks': [chains.reachable(n['name']) for n in chains.summary()]}
    return {'networks': chains.summary(),
            'note': 'testnet=true costs nothing; the rest is real money'}


def _t_balance(a, ctx):
    return ops.balance(a['address'], a.get('network'), ctx.owner(required=False),
                       a.get('token'))


def _t_portfolio(a, ctx):
    return ops.portfolio(ctx.owner(), a.get('address'), a.get('networks'))


def _t_block(a, ctx):
    return ops.block(a.get('number', 'latest'), a.get('network'), a.get('full', False))


def _t_tx(a, ctx):
    if a.get('wait'):
        return ops.wait(a['hash'], a.get('network'), int(a.get('timeout') or 120))
    return ops.transaction(a['hash'], a.get('network'))


def _t_gas(a, ctx):
    return ops.fees(a.get('network'))


def _t_code(a, ctx):
    return ops.code(a['address'], a.get('network'), ctx.owner(required=False))


def _t_nonce(a, ctx):
    return ops.nonce(a['address'], a.get('network'), ctx.owner(required=False))


def _t_logs(a, ctx):
    return ops.logs(a.get('network'), a.get('address'),
                    a.get('from_block', 'latest'), a.get('to_block', 'latest'),
                    a.get('topics'), ctx.owner(required=False),
                    int(a.get('limit') or 200))


def _t_estimate(a, ctx):
    return ops.estimate(a.get('to'), a.get('data'), a.get('value', 0),
                        a.get('network'), a.get('from'), ctx.owner(required=False))


# ── tools: accounts ──────────────────────────────────────────────────

def _t_accounts(a, ctx):
    owner = ctx.owner()
    return {'owner': owner, 'accounts': wallet.listing(owner),
            'note': 'addresses only — a key never leaves this box through here'}


def _t_new_account(a, ctx):
    return wallet.create(ctx.owner(), a['name'], a['password'],
                         mnemonic=bool(a.get('mnemonic')))


def _t_import_account(a, ctx):
    return wallet.import_key(ctx.owner(), a['name'], a['password'], a['secret'])


def _t_unlock(a, ctx):
    return wallet.unlock(ctx.owner(), a['name'], a['password'],
                         int(a.get('ttl') or 300))


def _t_lock(a, ctx):
    return wallet.lock(ctx.owner(), a.get('name'))


def _t_sign(a, ctx):
    return wallet.sign_message(ctx.owner(), a['account'], a['message'],
                               a.get('password'))


# ── tools: spending ──────────────────────────────────────────────────

def _t_send(a, ctx):
    return ops.send(ctx.owner(), a['account'], a['to'], a.get('value', 0),
                    a.get('network'), a.get('password'),
                    confirm=bool(a.get('confirm')),
                    wait_for=a.get('wait', True))


def _t_transfer(a, ctx):
    return ops.token_transfer(ctx.owner(), a['account'], a['token'], a['to'],
                              a['amount'], a.get('network'), a.get('password'),
                              confirm=bool(a.get('confirm')))


def _t_approve(a, ctx):
    return ops.token_approve(ctx.owner(), a['account'], a['token'], a['spender'],
                             a['amount'], a.get('network'), a.get('password'),
                             confirm=bool(a.get('confirm')))


def _t_token(a, ctx):
    owner = ctx.owner(required=False)
    out = ops.token_info(a['token'], a.get('network'), owner)
    if a.get('holder'):
        out['holder'] = ops.token_balance(a['token'], a['holder'],
                                          a.get('network'), owner)
    return out


# ── tools: contracts ─────────────────────────────────────────────────

def _t_templates(a, ctx):
    if a.get('name'):
        out = catalog.describe(a['name'], compile_it=True)
        if a.get('source'):
            out['source'] = catalog.source(a['name'])
        return out
    return {'templates': catalog.listing(),
            'note': 'pass name= for its ABI and constructor, source=true for the code'}


def _t_compile(a, ctx):
    source = a.get('source')
    if a.get('template'):
        source = catalog.source(a['template'])
    if not source:
        raise Refused('give me `source` (Solidity) or `template` (a name from '
                      'eth_templates)')
    out = compiler.compile_sources({a.get('filename', 'Contract.sol'): source},
                                   version=a.get('solc'),
                                   optimize=a.get('optimize', True),
                                   runs=int(a.get('runs') or 200))
    for contract in out['contracts']:
        # An agent that wanted the bytes would be deploying; a 20KB hex blob in
        # a tool result is pure context burn.
        contract['bytecode'] = contract['bytecode'][:66] + '…'
        contract.pop('deployed_bytecode', None)
    return out


def _t_deploy(a, ctx):
    source = a.get('source')
    name = a.get('name')
    if a.get('template'):
        source = catalog.source(a['template'])
        name = name or catalog.describe(a['template'])['contract']
    if not source and not (a.get('abi') and a.get('bytecode')):
        raise Refused('give me `template`, or `source`, or `abi`+`bytecode`')
    out = ops.deploy(ctx.owner(), a['account'], network=a.get('network'),
                     source=source, contract=a.get('contract'),
                     abi=a.get('abi'), bytecode=a.get('bytecode'),
                     args=a.get('args') or [], value=a.get('value', 0),
                     password=a.get('password'), solc=a.get('solc'),
                     name=name, confirm=bool(a.get('confirm')),
                     note=a.get('note'))
    out.pop('receipt', None)              # the useful fields are already lifted
    if out.get('abi'):
        out['abi_functions'] = sorted(e.get('name') for e in out['abi']
                                      if e.get('type') == 'function')
        out.pop('abi')
    return out


def _t_contracts(a, ctx):
    owner = ctx.owner()
    return {
        'deployed': [{k: v for k, v in row.items()
                      if k not in ('abi', 'bytecode', 'source')}
                     for row in ledger.deployments(owner, a.get('network'))],
        'attached': [{k: v for k, v in row.items() if k != 'abi'}
                     for row in ledger.attached(owner, a.get('network'))],
    }


def _t_contract(a, ctx):
    return ops.interface(a['address'], a.get('network'), a.get('abi'),
                         ctx.owner(required=False))


def _t_attach(a, ctx):
    spec = chains.resolve(a.get('network'))
    abi = a['abi']
    if isinstance(abi, str):
        abi = json.loads(abi)
    row = ledger.attach(ctx.owner(), a['address'], abi, spec['name'],
                        spec.get('chain_id'), a.get('name'))
    return {k: v for k, v in row.items() if k != 'abi'} | {'functions': len(abi)}


def _t_read(a, ctx):
    return ops.read(a['address'], a['function'], a.get('args') or [],
                    a.get('network'), a.get('abi'), ctx.owner(required=False))


def _t_write(a, ctx):
    return ops.write(ctx.owner(), a['account'], a['address'], a['function'],
                     a.get('args') or [], a.get('network'), a.get('abi'),
                     a.get('value', 0), a.get('password'),
                     confirm=bool(a.get('confirm')))


def _t_history(a, ctx):
    return {'txs': ledger.txs(ctx.owner(), a.get('network'),
                              int(a.get('limit') or 50))}


# ── tools: projects, sharing, testing ────────────────────────────────

def _t_projects(a, ctx):
    owner = ctx.owner()
    return {'projects': projects.listing(owner, int(a.get('limit') or 50)),
            'counts': projects.counts(owner),
            'store': store_link.LINK.status(ctx.token(required=False))}


def _t_project(a, ctx):
    return projects.get(ctx.owner(), a['project'])


def _t_save_project(a, ctx):
    files = a.get('files')
    if isinstance(files, str):
        files = json.loads(files)
    return projects.save(ctx.owner(), ctx.token(), name=a.get('name'),
                         files=files, source=a.get('source'),
                         entry=a.get('entry'), project=a.get('project'),
                         note=a.get('note'), public=a.get('public'))


def _t_share_project(a, ctx):
    return projects.share(ctx.owner(), ctx.token(), a['project'])


def _t_open_project(a, ctx):
    return projects.open_bundle(ctx.token(required=False), a['cid'])


def _t_fork_project(a, ctx):
    return projects.fork(ctx.owner(), ctx.token(), a['cid'], a.get('name'))


def _t_forget_project(a, ctx):
    return projects.delete(ctx.owner(), a['project'], ctx.token(required=False),
                           bool(a.get('from_store')))


def _t_store(a, ctx):
    return store_link.LINK.status(ctx.token(required=False))


def _t_test(a, ctx):
    out = harness.run(ctx.owner(), a.get('account') or 'default',
                      network=a.get('network'), source=a.get('source'),
                      files=a.get('files'), project=a.get('project'),
                      contract=a.get('contract'), suites=a.get('suites'),
                      args=a.get('args'), value=a.get('value', 0),
                      password=a.get('password'), address=a.get('address'),
                      abi=a.get('abi'), confirm=bool(a.get('confirm')),
                      token=ctx.token(required=False))
    # A model reading this wants the failures in detail and the passes in
    # one line: the event list behind a case that passed is noise it has to
    # pay for.
    for suite in out.get('suites', []):
        for case in suite.get('cases', []):
            if case.get('ok'):
                case.pop('events', None)
    return out


def _t_generate_tests(a, ctx):
    abi = a.get('abi')
    if abi is None:
        files = a.get('files') or ({'Contract.sol': a['source']}
                                   if a.get('source') else None)
        if not files and a.get('project'):
            files = projects.get(ctx.owner(), a['project'])['files']
        if not files:
            raise Refused('give me `source`, `files`, `project` or an `abi`')
        compiled = compiler.compile_sources(files)
        deployable = [c for c in compiled['contracts'] if c['deployable']]
        chosen = next((c for c in deployable if c['name'] == a.get('contract')),
                      deployable[0] if deployable else None)
        if chosen is None:
            raise Refused('this source has nothing deployable in it')
        abi = chosen['abi']
    if isinstance(abi, str):
        abi = json.loads(abi)
    return harness.generate(abi, name=a.get('contract') or 'smoke')


def _t_test_runs(a, ctx):
    return {'runs': harness.runs(ctx.owner(), int(a.get('limit') or 20),
                                 a.get('project'))}


def _t_test_report(a, ctx):
    return harness.report(ctx.owner(), int(a['run']))


TOOLS = {
    'eth_status': {
        'description': 'Start here. The default network and whether its RPC '
                       'answers, every network name available, which solc '
                       'versions this box can compile with, the contract '
                       'templates that ship with the module, and — if you sent a '
                       'token — who you are and which accounts you have. Cheap, '
                       'and it tells you what is missing before anything fails.',
        'inputSchema': {'type': 'object', 'properties': {'network': _NETWORK}},
        'annotations': _READ, 'auth': False, 'handler': _t_status,
    },
    'eth_networks': {
        'description': 'Every chain this deployment can reach: name, chain id, '
                       'rpc, explorer, native currency, and `testnet`. Read '
                       'testnet before you write anything — testnet=true is free '
                       'and reversible, testnet=false is real money and every '
                       'write there needs confirm=true. `local` is an anvil/'
                       'hardhat node on this box. Pass check=true to ping each '
                       'one (slower, but tells you which are actually up).',
        'inputSchema': {'type': 'object', 'properties': {
            'check': _bool('ping every rpc instead of listing what is configured')}},
        'annotations': _READ, 'auth': False, 'handler': _t_networks,
    },
    'eth_balance': {
        'description': 'The native balance of an address, one of your account '
                       'names, or an ENS name. Pass `token` (an ERC-20 address) '
                       'for that token\'s balance instead. Returns both the '
                       'human amount and the raw wei, because only one of them '
                       'is safe to do arithmetic on.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'network': _NETWORK,
            'token': _str('an ERC-20 contract address to read instead of the '
                          'native currency')},
            'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_balance,
    },
    'eth_portfolio': {
        'description': 'Every account you have, across several chains at once — '
                       'the answer to "where is my money". Chains that fail to '
                       'answer are reported as errors rather than dropped, '
                       'because a missing chain reads as a zero balance.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('one address instead of all your accounts'),
            'networks': _arr('which chains to check (default: the common ones)')}},
        'annotations': _READ, 'handler': _t_portfolio,
    },
    'eth_block': {
        'description': 'A block by number, hash, or "latest"/"pending"/'
                       '"finalized". full=true expands the transactions instead '
                       'of listing hashes.',
        'inputSchema': {'type': 'object', 'properties': {
            'number': {'description': 'block number, hash, or a tag like latest',
                       'type': ['string', 'number']},
            'network': _NETWORK, 'full': _bool('expand the transactions')}},
        'annotations': _READ, 'auth': False, 'handler': _t_block,
    },
    'eth_tx': {
        'description': 'A transaction and its receipt, or that it is still '
                       'pending. Pass wait=true to block until it is mined '
                       '(returns status success/reverted, gas used, and the '
                       'contract address if it was a deployment).',
        'inputSchema': {'type': 'object', 'properties': {
            'hash': _str('the transaction hash'), 'network': _NETWORK,
            'wait': _bool('block until it is mined'),
            'timeout': _num('seconds to wait (default 120)')},
            'required': ['hash']},
        'annotations': _READ, 'auth': False, 'handler': _t_tx,
    },
    'eth_gas': {
        'description': 'What a transaction costs on this chain right now: base '
                       'fee, priority tip, the max fee this module would use, '
                       'and the cost of a plain transfer. Check it before a '
                       'mainnet write — gas is the difference between a $2 '
                       'transaction and a $200 one.',
        'inputSchema': {'type': 'object', 'properties': {'network': _NETWORK}},
        'annotations': _READ, 'auth': False, 'handler': _t_gas,
    },
    'eth_code': {
        'description': 'Whether there is a contract at an address, how big it '
                       'is, and whether this box already knows its ABI (in which '
                       'case eth_read and eth_write need no abi argument).',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'network': _NETWORK}, 'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_code,
    },
    'eth_nonce': {
        'description': 'The next nonce for an address, pending and confirmed. '
                       'The gap between the two is how many transactions are in '
                       'flight.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'network': _NETWORK}, 'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_nonce,
    },
    'eth_logs': {
        'description': 'Event logs, decoded into names and arguments when the '
                       'ABI is known. Narrow the block range — public RPCs '
                       'refuse wide ones, and "latest" to "latest" is one block.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('contract to filter on'), 'network': _NETWORK,
            'from_block': {'description': 'start block (number or tag)',
                           'type': ['string', 'number']},
            'to_block': {'description': 'end block (number or tag)',
                         'type': ['string', 'number']},
            'topics': _arr('raw topic filters, if you know them'),
            'limit': _num('how many to return (default 200)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_logs,
    },
    'eth_estimate': {
        'description': 'What a transaction would cost in gas, without sending '
                       'it. Also the cheapest way to find out that a call would '
                       'revert.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _ADDRESS, 'data': _str('calldata hex'), 'value': _VALUE,
            'from': _str('the sender to simulate as'), 'network': _NETWORK}},
        'annotations': _READ, 'auth': False, 'handler': _t_estimate,
    },

    'eth_accounts': {
        'description': 'Your accounts: names, addresses, and which are currently '
                       'unlocked. Keys never leave the box through this tool. '
                       'Accounts are scoped to the address that signed the '
                       'request — you cannot see anyone else\'s.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'handler': _t_accounts,
    },
    'eth_new_account': {
        'description': 'Make a new key, encrypted under a password this module '
                       'never stores. Set mnemonic=true to get a 12-word phrase '
                       'back — it is returned EXACTLY ONCE and cannot be '
                       'recovered, so show it to the user immediately. Losing '
                       'the password loses the account; there is no reset.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('what to call it (letters, digits, - _ .)'),
            'password': _str('at least 8 characters — the only thing protecting '
                             'the key file'),
            'mnemonic': _bool('also return a BIP-39 phrase (shown once)')},
            'required': ['name', 'password']},
        'annotations': _WRITE, 'handler': _t_new_account,
    },
    'eth_import_account': {
        'description': 'Bring in an existing private key (0x hex) or BIP-39 '
                       'mnemonic and store it encrypted under a password.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('what to call it'), 'password': _str('at least 8 characters'),
            'secret': _str('a private key or a 12/24-word mnemonic')},
            'required': ['name', 'password', 'secret']},
        'annotations': _WRITE, 'handler': _t_import_account,
    },
    'eth_unlock': {
        'description': 'Decrypt an account into memory for a bounded time so the '
                       'following writes need no password. Memory only — a '
                       'restart or the timeout forgets it. Do this once at the '
                       'start of a batch of transactions instead of passing the '
                       'password on every call.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _ACCOUNT, 'password': _str('the account password'),
            'ttl': _num('seconds to stay unlocked (default 300, capped at 900)')},
            'required': ['name', 'password']},
        'annotations': _WRITE, 'handler': _t_unlock,
    },
    'eth_lock': {
        'description': 'Forget an unlocked key now, without waiting for its '
                       'timeout. Omit `name` to lock everything.',
        'inputSchema': {'type': 'object', 'properties': {'name': _ACCOUNT}},
        'annotations': _WRITE, 'handler': _t_lock,
    },
    'eth_sign': {
        'description': 'Sign a message with one of your accounts (EIP-191 '
                       'personal_sign — the same shape a browser wallet '
                       'produces). Signing a message costs nothing and touches '
                       'no chain.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT, 'message': _str('the text to sign'),
            'password': _PASSWORD}, 'required': ['account', 'message']},
        'annotations': _WRITE, 'handler': _t_sign,
    },

    'eth_send': {
        'description': 'Send the native currency (ETH, POL, BNB…) from one of '
                       'your accounts. Gas is estimated first, so a transfer '
                       'that would fail is never sent. On a chain where '
                       'testnet=false this REFUSES without confirm=true — only '
                       'set that when the user asked for a real transaction.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT, 'to': _ADDRESS, 'value': _VALUE,
            'network': _NETWORK, 'password': _PASSWORD, 'confirm': _CONFIRM,
            'wait': _bool('block until mined (default true)')},
            'required': ['account', 'to', 'value']},
        'annotations': _SPEND, 'handler': _t_send,
    },
    'eth_deploy': {
        'description': 'Compile Solidity and deploy it, in one call. Pass '
                       '`template` (a name from eth_templates) or your own '
                       '`source`, plus `args` for the constructor. Returns the '
                       'address, the transaction, gas used and whether code is '
                       'really on chain — and remembers the ABI, so eth_read and '
                       'eth_write work on that address afterwards with no ABI '
                       'argument. Deploy to `local` or a testnet first: a '
                       'contract cannot be edited once it is out there. Needs '
                       'confirm=true on a non-testnet chain.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT,
            'template': _str('a template name from eth_templates'),
            'source': _str('Solidity source, if not using a template'),
            'contract': _str('which contract in the source to deploy, when it '
                             'declares more than one'),
            'args': _arr('constructor arguments, in order'),
            'value': _VALUE, 'network': _NETWORK, 'password': _PASSWORD,
            'solc': _str('a solc version to pin (default: read from the pragma)'),
            'name': _str('what to record it as'),
            'note': _str('a note to your future self'),
            'abi': _arr('a prebuilt ABI, instead of source'),
            'bytecode': _str('prebuilt creation bytecode, instead of source'),
            'confirm': _CONFIRM},
            'required': ['account']},
        'annotations': _SPEND, 'handler': _t_deploy,
    },
    'eth_write': {
        'description': 'Call a state-changing function on a contract. The ABI is '
                       'looked up from what this box deployed or has attached; '
                       'pass `abi` for anything else. Arguments are coerced to '
                       'the ABI types, so strings for uints are fine. The call '
                       'is simulated first — if it would revert you get the '
                       'reason instead of a wasted transaction.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT, 'address': _ADDRESS,
            'function': _str('the function name'),
            'args': _arr('its arguments, in order'),
            'value': _VALUE, 'network': _NETWORK, 'password': _PASSWORD,
            'abi': _arr('the contract ABI, if this box does not know it'),
            'confirm': _CONFIRM},
            'required': ['account', 'address', 'function']},
        'annotations': _SPEND, 'handler': _t_write,
    },
    'eth_read': {
        'description': 'Call a view/pure function. Free, keyless, changes '
                       'nothing — reach for this before eth_write to check state. '
                       'The ABI comes from what this box knows about the address '
                       'unless you pass one.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'function': _str('the function name'),
            'args': _arr('its arguments, in order'), 'network': _NETWORK,
            'abi': _arr('the contract ABI, if this box does not know it')},
            'required': ['address', 'function']},
        'annotations': _READ, 'auth': False, 'handler': _t_read,
    },
    'eth_contract': {
        'description': 'What can be done with a contract, split into reads '
                       '(free), writes (cost gas) and events. Use this to '
                       'discover function names and argument types before '
                       'calling eth_read or eth_write.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'network': _NETWORK,
            'abi': _arr('an ABI to use instead of the known one')},
            'required': ['address']},
        'annotations': _READ, 'auth': False, 'handler': _t_contract,
    },
    'eth_contracts': {
        'description': 'Everything you deployed through this module, and every '
                       'ABI you attached: address, chain, name, transaction, '
                       'compiler settings and when.',
        'inputSchema': {'type': 'object', 'properties': {'network': _NETWORK}},
        'annotations': _READ, 'handler': _t_contracts,
    },
    'eth_attach': {
        'description': 'Teach this box the ABI of a contract somebody else '
                       'deployed, so eth_read/eth_write can use it by address '
                       'from then on.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDRESS, 'abi': _arr('the ABI, as JSON'),
            'network': _NETWORK, 'name': _str('what to call it')},
            'required': ['address', 'abi']},
        'annotations': _WRITE, 'handler': _t_attach,
    },
    'eth_templates': {
        'description': 'The nine contracts that ship with this module — counter, '
                       'token (ERC-20), nft (ERC-721), storage (key→value '
                       'registry), anchor (timestamp a CID), vault (timelock), '
                       'escrow, splitter, multisig. Each is self-contained '
                       'Solidity with no imports. Pass `name` for its ABI and '
                       'constructor arguments, source=true for the code itself.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('one template'), 'source': _bool('include the Solidity')}},
        'annotations': _READ, 'auth': False, 'handler': _t_templates,
    },
    'eth_compile': {
        'description': 'Compile Solidity without deploying: ABI, constructor '
                       'signature, bytecode size and any warnings. Use it to '
                       'check code before spending gas on it. The solc version '
                       'comes from the pragma unless you pin one.',
        'inputSchema': {'type': 'object', 'properties': {
            'source': _str('Solidity source'),
            'template': _str('a template name, instead of source'),
            'filename': _str('what to call the file (affects error messages)'),
            'solc': _str('pin a compiler version, e.g. 0.8.24'),
            'optimize': _bool('run the optimizer (default true)'),
            'runs': _num('optimizer runs (default 200)')}},
        'annotations': _READ, 'auth': False, 'handler': _t_compile,
    },
    'eth_token': {
        'description': 'ERC-20 metadata — name, symbol, decimals, total supply — '
                       'and a holder\'s balance if you name one. Fails clearly '
                       'when the address is not a token, which is also how you '
                       'check that it is.',
        'inputSchema': {'type': 'object', 'properties': {
            'token': _str('the ERC-20 contract address'), 'network': _NETWORK,
            'holder': _str('an address to read the balance of')},
            'required': ['token']},
        'annotations': _READ, 'auth': False, 'handler': _t_token,
    },
    'eth_transfer': {
        'description': 'Move ERC-20 tokens. Amounts are in whole tokens by '
                       'default — the decimals are read from the contract, so '
                       '"1.5" means 1.5 tokens whatever its decimals are. Needs '
                       'confirm=true on a non-testnet chain.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT, 'token': _str('the ERC-20 address'),
            'to': _ADDRESS, 'amount': _VALUE, 'network': _NETWORK,
            'password': _PASSWORD, 'confirm': _CONFIRM},
            'required': ['account', 'token', 'to', 'amount']},
        'annotations': _SPEND, 'handler': _t_transfer,
    },
    'eth_approve': {
        'description': 'Let a spender move your ERC-20 tokens. Pass "max" for an '
                       'unlimited allowance — convenient, and worth telling the '
                       'user about, since it stays until revoked with an approve '
                       'of 0. Needs confirm=true on a non-testnet chain.',
        'inputSchema': {'type': 'object', 'properties': {
            'account': _ACCOUNT, 'token': _str('the ERC-20 address'),
            'spender': _ADDRESS, 'amount': _VALUE, 'network': _NETWORK,
            'password': _PASSWORD, 'confirm': _CONFIRM},
            'required': ['account', 'token', 'spender', 'amount']},
        'annotations': _SPEND, 'handler': _t_approve,
    },
    'eth_history': {
        'description': 'Every transaction this module has sent for you: kind, '
                       'chain, hash, function, status and gas. The local receipt '
                       'book — it knows about intent, which the chain does not.',
        'inputSchema': {'type': 'object', 'properties': {
            'network': _NETWORK, 'limit': _num('how many rows (default 50)')}},
        'annotations': _READ, 'handler': _t_history,
    },
    'eth_projects': {
        'description': 'Your contract projects — the source you are working on, '
                       'not the contracts already on a chain. Each row carries '
                       'its store CID (the shareable identity of its content), '
                       'which contracts it declares, and whether it is public. '
                       'Also reports whether the store module will accept an '
                       'upload from you, which is the thing that blocks sharing.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('how many (default 50)')}},
        'annotations': _READ, 'handler': _t_projects,
    },
    'eth_project': {
        'description': 'One project in full: every file, the entry contract, '
                       'the saved test suites, and every CID it has ever had. '
                       'Accepts an id, a slug or a CID.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('the project id, slug or CID')},
            'required': ['project']},
        'annotations': _READ, 'handler': _t_project,
    },
    'eth_save_project': {
        'description': 'Write Solidity into a project and store it. The bytes '
                       'go to the store module and come back as a CID, which is '
                       'how the project is shared and versioned — every save is '
                       'a new CID, and the old one still resolves. Pass '
                       '`project` to write a new version of one you already '
                       'have; leave it out to start a new one. If the store '
                       'refuses (your address is not whitelisted there), the '
                       'project is still kept locally and the refusal is '
                       'reported in `store.reason`.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('what to call it'),
            'source': _str('the Solidity, for a one-file project'),
            'files': {'type': 'object',
                      'description': 'several files as {"Name.sol": "source"}'},
            'entry': _str('which file holds the contract to deploy'),
            'project': _str('an existing project id or slug, to save over it'),
            'note': _str('what changed'),
            'public': _bool('publish it in the store immediately')}},
        'annotations': _WRITE, 'handler': _t_save_project,
    },
    'eth_share_project': {
        'description': 'Publish a project in the store and get the link. The '
                       'CID is the share: anyone holding it can open the '
                       'project without an account, because the store serves '
                       'public objects to anybody.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('the project id or slug')}, 'required': ['project']},
        'annotations': _WRITE, 'handler': _t_share_project,
    },
    'eth_open_project': {
        'description': 'Read a shared project out of the store by CID — files, '
                       'entry contract and any test suites that came with it. '
                       'Needs no account for a public CID. This is how you read '
                       'somebody else\'s contract before deploying it.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _str('the CID somebody shared')}, 'required': ['cid']},
        'annotations': _READ, 'auth': False, 'handler': _t_open_project,
    },
    'eth_fork_project': {
        'description': 'Copy a shared project into your own workspace so you '
                       'can change it. The copy is yours; `origin_cid` records '
                       'where it came from.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _str('the CID to fork'),
            'name': _str('what to call your copy')}, 'required': ['cid']},
        'annotations': _WRITE, 'handler': _t_fork_project,
    },
    'eth_forget_project': {
        'description': 'Drop a project from your index. The stored object is '
                       'left alone unless from_store=true — anyone you shared '
                       'the CID with may be holding it, so removing the content '
                       'is a separate act from tidying your own list.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('the project id or slug'),
            'from_store': _bool('also delete the object from the store')},
            'required': ['project']},
        'annotations': _WRITE, 'handler': _t_forget_project,
    },
    'eth_store': {
        'description': 'Where storage stands for you: is the store module up, '
                       'is your address allowed to upload, have you accepted '
                       'its terms, how much quota is left. Call this when a '
                       'save comes back with no CID — the reason will be here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ, 'auth': False, 'handler': _t_store,
    },
    'eth_test': {
        'description': 'Deploy the contract to a chain and run a test suite '
                       'against it — a real deploy, real transactions, real '
                       'receipts. Defaults to the testnet policy every write '
                       'here obeys: on a chain with testnet=false this refuses '
                       'without confirm=true, and you should not set it. '
                       'A suite is {"name", "args" (constructor), "cases": '
                       '[{"name", "fn", "args", and one of "expect", '
                       '"expect_gt"/"expect_gte"/"expect_lt"/"expect_lte", '
                       '"expect_event", "expect_contains", "expect_revert"}]}. '
                       'Whether a case is a free call or a signed transaction '
                       'is read off the ABI. Placeholders $deployer, $contract, '
                       '$zero and $account:<name> expand in arguments and '
                       'expectations, and "10**18" is accepted for large '
                       'numbers. With no suite, every zero-argument getter is '
                       'called — that proves the deploy, not the behaviour. '
                       'Slow: each write waits to be mined.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('a project of yours to test'),
            'source': _str('Solidity to test instead of a project'),
            'files': {'type': 'object', 'description': 'several files as '
                                                       '{"Name.sol": "source"}'},
            'contract': _str('which contract in the source'),
            'suites': {'description': 'one suite object or a list of them',
                       'type': ['array', 'object']},
            'args': _arr('constructor arguments, when the suite does not give them'),
            'value': _VALUE, 'account': _ACCOUNT, 'password': _PASSWORD,
            'network': _NETWORK,
            'address': _str('run against a contract already deployed here, '
                            'instead of deploying a fresh one'),
            'confirm': _CONFIRM},
            'required': ['account']},
        'annotations': _SPEND, 'handler': _t_test,
    },
    'eth_generate_tests': {
        'description': 'A starter suite read off the ABI: every zero-argument '
                       'getter, with no expectations attached. Deliberately '
                       'assertion-free — a generated expectation is a guess '
                       'dressed as a requirement. Edit it, then pass it to '
                       'eth_test.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('a project of yours'), 'source': _str('Solidity'),
            'files': {'type': 'object', 'description': '{"Name.sol": "source"}'},
            'abi': {'description': 'an ABI you already have',
                    'type': ['array', 'string']},
            'contract': _str('which contract in the source')}},
        'annotations': _READ, 'auth': False, 'handler': _t_generate_tests,
    },
    'eth_test_runs': {
        'description': 'Past test runs: what passed, what failed, on which '
                       'chain, and the CID of each full report.',
        'inputSchema': {'type': 'object', 'properties': {
            'project': _str('narrow to one project'),
            'limit': _num('how many (default 20)')}},
        'annotations': _READ, 'handler': _t_test_runs,
    },
    'eth_test_report': {
        'description': 'One test run in full — every case, why it passed or '
                       'failed, and the transaction hash behind each write.',
        'inputSchema': {'type': 'object', 'properties': {
            'run': _num('the run id from eth_test_runs')}, 'required': ['run']},
        'annotations': _READ, 'handler': _t_test_report,
    },
}


def needs_auth(name):
    return TOOLS.get(name, {}).get('auth', True)


def version():
    return CONFIG.get('version', '1.0.0')


def tool_list():
    return [{'name': name, 'description': tool['description'],
             'inputSchema': tool['inputSchema'],
             'annotations': tool['annotations']}
            for name, tool in TOOLS.items()]


def client_config(url=None):
    endpoint = url or CONFIG.get('urls', {}).get('mcp', 'http://localhost:50750/mcp')
    return {
        'http': {'mcpServers': {'ethdesk': {'type': 'http', 'url': endpoint,
                                        'headers': {'Authorization': 'Bearer <mod-protocol token>'}}}},
        'stdio': {'mcpServers': {'ethdesk': {'command': 'python3',
                                         'args': [str(HERE / 'mcp.py')]}}},
        'claude_cli': f'claude mcp add --transport http eth {endpoint} '
                      f'--header "Authorization: Bearer <token>"',
    }


def describe(url=None):
    """Everything about this server in one document — what GET /mcp serves and
    what the console renders, so the schema is never something you have to run
    a client to see."""
    return {
        'server': {'name': 'ethdesk', 'version': version(),
                   'description': CONFIG.get('description', '')[:400]},
        'protocol': {'default': DEFAULT_PROTOCOL_VERSION,
                     'supported': list(SUPPORTED_PROTOCOL_VERSIONS),
                     'jsonrpc': '2.0',
                     'methods': ['initialize', 'ping', 'tools/list', 'tools/call']},
        'transports': {
            'http': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                     'url': url or CONFIG.get('urls', {}).get('mcp',
                                                              'http://localhost:50750/mcp'),
                     'note': 'Streamable HTTP. Tools that use a key need a token.'},
            'stdio': {'command': f'python3 {HERE / "mcp.py"}',
                      'note': "runs as this box's own identity — every tool available"},
        },
        'auth': {
            'protocol_token': 'Authorization: Bearer <mod-protocol token>. Needed '
                              'for every tool that touches a key or your private '
                              'index. Open to anyone: '
                              + ', '.join(n for n in TOOLS if not needs_auth(n)),
            'stdio': "a stdio server acts as this box's own mod identity, so "
                     'local tools need no token',
            'passwords': 'an account password is never stored — pass it per call '
                         'or hold an eth_unlock',
        },
        'safety': {
            'mainnet': 'any write on a chain with testnet=false is refused '
                       'unless the call carries confirm=true',
            'estimation': 'every write is gas-estimated first, so a transaction '
                          'that would revert is not sent',
            'keys': 'keystore-v3 under the caller\'s address; a key is usable '
                    'only while unlocked or when the password is supplied',
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


def call_tool(name, args, ctx=None, owner=None):
    """Run one tool. Raises Refused/ValueError with a message worth reading."""
    tool = TOOLS.get(name)
    if not tool:
        raise Refused(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    if ctx is None:
        ctx = Ctx()
        if owner:
            ctx.owner = lambda required=True, _o=owner: _o
    return tool['handler'](dict(args or {}), ctx)


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
        return failed(f'{name}: {e}')
    except (ops.OpError, wallet.WalletError, chains.ChainError,
            compiler.CompileError, identity.AuthError) as e:
        return failed(f'{name}: {e}')
    except KeyError as e:
        return failed(f'{name}: missing argument {e}')
    except TypeError as e:
        return failed(f'{name}: bad arguments — {e}')
    except FileNotFoundError as e:
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
            'serverInfo': {'name': 'ethdesk', 'version': version()},
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

        from api.api import app
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i]) if i > 0 else int(os.environ.get(
            'ETHDESK_API_PORT', CONFIG.get('port', 50750)))
        uvicorn.run(app, host='0.0.0.0', port=port)
    else:
        serve_stdio()
