#!/usr/bin/env python3
"""solana mcp — twenty-two tools: reading the chain, moving value on it, and programs.

An agent looking at Solana has one recurring problem: an address is 32 opaque
bytes and it will not tell you what it is. So the tools are ordered around
identification first — `sol_account` says whether a string is a wallet, a mint,
a token account or a program, and every other tool takes it from there.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50710 # Streamable HTTP — POST /mcp

The API server mounts `handle()` at /mcp as well, so the tools, the REST routes
and the console can never drift apart.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything that imports us.
    sys.path.append(HERE)

import keys as K                                            # noqa: E402
import program as P                                         # noqa: E402
from chain import NETWORKS, SPEND_USD, Client               # noqa: E402
from keys import SolError, need_address                     # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Solana, read and write. An address is opaque — start with sol_account to '
    'find out whether a string is a wallet, a token mint, a token account, a '
    'stake account or a program, then branch: sol_portfolio for what a wallet '
    'holds priced in USD, sol_token for a mint\'s supply, authorities and '
    'liquidity, sol_history + sol_tx for what actually happened (sol_tx returns '
    'net SOL and token movement per owner, not raw account indexes). '
    'sol_price and sol_quote answer "what is it worth" and "what would I '
    'actually get" — quotes come from Jupiter and include price impact. '
    'sol_network, sol_validators and sol_stake cover the chain itself. '
    'Writing: sol_wallet manages an off-tree keystore and sol_transfer signs '
    f'locally; anything over ${SPEND_USD:,.0f} returns needs_confirm until you '
    'pass confirm=true. Every tool takes network=mainnet|devnet|testnet '
    '(default mainnet) and an optional rpc= override. sol_rpc is the escape '
    'hatch for any JSON-RPC method not wrapped here.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_NET = _str('mainnet (default), devnet, testnet, or a full RPC url',
            enum=list(NETWORKS) + ['custom'])
_RPC = _str('override the RPC endpoint for this call — use your own node when '
            'the public one throttles')
_ADDR = _str('a base58 Solana address')
_COMMON = {'network': _NET, 'rpc': _RPC}


def _client(args):
    return Client(network=args.pop('network', None), rpc=args.pop('rpc', None))


# ── handlers ─────────────────────────────────────────────────────

def _t_balance(a):
    return _client(a).balance(a['address'])


def _t_portfolio(a):
    c = _client(a)
    return c.portfolio(a['address'], min_usd=a.get('min_usd', 0.01),
                       include_dust=bool(a.get('include_dust')),
                       limit=a.get('limit', 200))


def _t_account(a):
    return _client(a).account(a['address'])


def _t_token(a):
    return _client(a).token(a['mint'])


def _t_price(a):
    return _client(a).price(a.get('ids') or a.get('id') or a.get('tokens'))


def _t_history(a):
    c = _client(a)
    return c.history(a['address'], limit=a.get('limit', 20), before=a.get('before'),
                     until=a.get('until'), detail=bool(a.get('detail')))


def _t_tx(a):
    return _client(a).tx(a['signature'], logs=bool(a.get('logs')))


def _t_quote(a):
    c = _client(a)
    return c.quote(a.get('input') or a.get('input_mint'),
                   a.get('output') or a.get('output_mint'),
                   a['amount'], slippage_bps=a.get('slippage_bps', 50))


def _t_network(a):
    return _client(a).status()


def _t_validators(a):
    c = _client(a)
    return c.validators(limit=a.get('limit', 20), sort=a.get('sort', 'stake'),
                        delinquent=a.get('delinquent'))


def _t_stake(a):
    return _client(a).stakes(a['address'])


def _t_wallet(a):
    action = (a.get('action') or 'list').lower()
    if action in ('list', 'ls'):
        return K.wallets()
    if action in ('create', 'new'):
        import datetime
        return K.create(a.get('name') or 'default', make_default=a.get('default'),
                        overwrite=bool(a.get('overwrite')),
                        created=datetime.datetime.now().isoformat(timespec='seconds'))
    if action == 'import':
        import datetime
        if not a.get('secret'):
            raise SolError('import needs secret= (keypair array, base58, hex, or a '
                           'path to a Solana CLI keypair file)')
        return K.create(a.get('name') or 'imported', secret=a['secret'],
                        make_default=a.get('default'),
                        overwrite=bool(a.get('overwrite')),
                        created=datetime.datetime.now().isoformat(timespec='seconds'))
    if action in ('remove', 'delete', 'rm'):
        return K.remove(a.get('name') or '')
    if action == 'default':
        return K.set_default(a.get('name') or '')
    if action == 'export':
        return K.export(a.get('name'))
    if action in ('show', 'balance'):
        _, address = K.signer(a.get('name'))
        return _client(a).portfolio(address)
    raise SolError(f'unknown action {action!r} — list, create, import, remove, '
                   'default, export, show')


def _t_transfer(a):
    c = _client(a)
    return c.transfer(a['to'], a['amount'], mint=a.get('mint'),
                      wallet=a.get('wallet'), secret=a.get('secret'),
                      memo=a.get('memo'), confirm=bool(a.get('confirm')),
                      wait=a.get('wait', True))


def _t_swap(a):
    c = _client(a)
    return c.swap(a.get('input') or a.get('input_mint'),
                  a.get('output') or a.get('output_mint'),
                  a['amount'], slippage_bps=a.get('slippage_bps', 50),
                  wallet=a.get('wallet'), secret=a.get('secret'),
                  confirm=bool(a.get('confirm')), wait=a.get('wait', True),
                  priority_lamports=a.get('priority_lamports'),
                  dry_run=bool(a.get('dry_run')))


def _t_airdrop(a):
    c = _client(a)
    return c.airdrop(a.get('address'), sol_amount=a.get('sol', 1),
                     wallet=a.get('wallet'))


def _t_rpc(a):
    c = _client(a)
    params = a.get('params')
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = [params]
    if params is not None and not isinstance(params, list):
        params = [params]
    return {'network': c.network, 'method': a['method'],
            'result': c.call(a['method'], params or [])}


def _t_program(a):
    c = _client(a)
    address = a.get('program') or a.get('address')
    out = P.program_info(c, address, code=bool(a.get('code')),
                         strings=a.get('strings', True), idl=a.get('idl', True))
    if a.get('accounts') and out.get('exists'):
        doc = None
        try:
            doc, _src = P.load_idl(c, out['program'])
        except SolError:
            pass
        out['program_accounts'] = P.program_accounts(
            c, out['program'], idl=doc, limit=a.get('limit', 25),
            account_type=a.get('account_type'))
    return out


def _t_idl(a):
    c = _client(a)
    address = need_address(a.get('program') or a.get('address'), 'program')
    action = str(a.get('action') or 'get').lower()
    if action in ('set', 'save', 'put', 'upload'):
        return P.save_idl(address, a.get('idl'))
    if action in ('clear', 'remove', 'delete'):
        path = P.local_idl_path(address)
        existed = os.path.exists(path)
        if existed:
            os.remove(path)
        return {'program': address, 'removed': existed,
                'note': 'the on-chain IDL, if there is one, is untouched'}
    doc, source = P.load_idl(c, address)
    if not doc:
        raise SolError(f'no IDL for {address} — anchor programs publish one at '
                       f'{P.idl_address(address)}, and this program has not. '
                       'Send one with action=set and every tool here will use '
                       'it.', status=404)
    out = {'program': address, 'source': source, **(P.idl_summary(doc) or {})}
    if a.get('full'):
        out['full'] = doc
    return out


def _t_deploy(a):
    action = str(a.get('action') or 'deploy').lower()
    if action in ('status', 'job', 'watch'):
        return P.job(a.get('job') or a.get('id'))
    if action in ('list', 'jobs'):
        return P.job_list(a.get('limit', 20))
    c = _client(a)
    return P.deploy(c, path=a.get('path'), data=a.get('data'),
                    clone=a.get('clone'),
                    clone_network=a.get('clone_network', 'mainnet'),
                    program=a.get('program'), buffer=a.get('buffer'),
                    wallet=a.get('wallet'), secret=a.get('secret'),
                    max_data_len=a.get('max_data_len'),
                    confirm=bool(a.get('confirm')), wait=a.get('wait', 25),
                    name=a.get('name'))


def _t_invoke(a):
    c = _client(a)
    return P.invoke(c, a.get('program') or a.get('address'), ix=a.get('ix'),
                    args=a.get('args'), accounts=a.get('accounts'),
                    data=a.get('data'), wallet=a.get('wallet'),
                    secret=a.get('secret'), payer=a.get('payer'),
                    send=bool(a.get('send')), force=bool(a.get('force')),
                    idl=a.get('idl'))


def _t_pda(a):
    return P.pda(a.get('seeds') or [], a.get('program') or a.get('address'))


def _t_authority(a):
    c = _client(a)
    return P.authority(c, str(a.get('action') or 'set').lower(),
                       account=a.get('account') or a.get('program'),
                       new_authority=a.get('new_authority'),
                       recipient=a.get('recipient'), wallet=a.get('wallet'),
                       secret=a.get('secret'), payer_wallet=a.get('payer_wallet'),
                       confirm=bool(a.get('confirm')))


# ── registry ─────────────────────────────────────────────────────

TOOLS = {
    'sol_account': {
        'description': 'What an address IS. Solana addresses are opaque — the same '
                       '32 bytes could be a wallet, a token mint, a token account, a '
                       'stake account or a program. This decodes it and returns the '
                       'right detail for whichever it turned out to be, plus SOL held '
                       'and its USD value. Start here with any address you did not '
                       'generate yourself.',
        'inputSchema': {'type': 'object',
                        'properties': {'address': _ADDR, **_COMMON},
                        'required': ['address']},
        'handler': _t_account,
    },
    'sol_balance': {
        'description': 'SOL balance for an address, or several at once (comma-'
                       'separated), with the USD value at the current price. For what '
                       'a wallet holds in TOKENS, use sol_portfolio instead — SOL '
                       'alone is usually the small half.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('one address, or several separated by commas'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_balance,
    },
    'sol_portfolio': {
        'description': 'Everything a wallet holds, priced and sorted by USD value: '
                       'SOL plus every SPL token across both token programs, merged '
                       'per mint. Dust below min_usd is counted and excluded rather '
                       'than padding the list, and tokens with no market price are '
                       'reported as a count — the total is what could be sold, not '
                       'what is nominally held.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR,
            'min_usd': _num('hide positions worth less than this (default 0.01)'),
            'include_dust': _bool('list every position regardless of value'),
            'limit': _num('maximum token rows to return (default 200)'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_portfolio,
    },
    'sol_token': {
        'description': 'A token mint in full: decimals, circulating supply, market '
                       'cap, price and 24h change, pool liquidity, holder count, and '
                       'the two authorities that matter — whether anyone can still '
                       'mint more of it or freeze your account. Returns an explicit '
                       'risk list for exactly those.',
        'inputSchema': {'type': 'object',
                        'properties': {'mint': _str('the mint address'), **_COMMON},
                        'required': ['mint']},
        'handler': _t_token,
    },
    'sol_price': {
        'description': 'USD price and 24h change for tokens, by mint address or by '
                       'symbol. Symbols are ambiguous on Solana — anyone can mint a '
                       'token called USDC — so a symbol resolves to the deepest-'
                       'liquidity match and the mint it chose comes back with the '
                       'price. Check it before trusting the number.',
        'inputSchema': {'type': 'object', 'properties': {
            'ids': _str('mints or symbols, comma-separated — e.g. "SOL,JUP" or a mint'),
            **_COMMON}, 'required': ['ids']},
        'handler': _t_price,
    },
    'sol_history': {
        'description': 'Recent transactions for an address, newest first: signature, '
                       'age, success, fee. With detail=true each one is also '
                       'summarised — the net SOL and token change FOR THIS ADDRESS — '
                       'which is the fast way to answer "what has this wallet been '
                       'doing". Page with before=<last signature>.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _ADDR,
            'limit': _num('1-100, default 20'),
            'before': _str('signature to page backwards from'),
            'until': _str('stop when this signature is reached'),
            'detail': _bool('fetch and summarise each transaction (slower)'),
            **_COMMON}, 'required': ['address']},
        'handler': _t_history,
    },
    'sol_tx': {
        'description': 'One transaction, decoded into what actually happened: who '
                       'paid the fee, the net SOL change per account, the net token '
                       'change per OWNER and mint, which programs ran and what they '
                       'were asked to do. This is the tool for "did my transfer land" '
                       'and "what did this swap really cost".',
        'inputSchema': {'type': 'object', 'properties': {
            'signature': _str('the base58 transaction signature'),
            'logs': _bool('include the raw program log lines'),
            **_COMMON}, 'required': ['signature']},
        'handler': _t_tx,
    },
    'sol_quote': {
        'description': 'What a swap would really get you: Jupiter\'s best route for '
                       'selling one token for another, with the output amount, the '
                       'worst case after slippage, price impact and the AMMs in the '
                       'route. A QUOTE ONLY — this module does not sign swaps. Use it '
                       'to price a size before deciding, since impact on a thin pair '
                       'is the whole story.',
        'inputSchema': {'type': 'object', 'properties': {
            'input': _str('mint or symbol to sell — e.g. SOL'),
            'output': _str('mint or symbol to buy — e.g. USDC'),
            'amount': _num('how much of the input token to sell, in whole units'),
            'slippage_bps': _num('slippage tolerance in basis points (default 50)'),
            **_COMMON}, 'required': ['input', 'output', 'amount']},
        'handler': _t_quote,
    },
    'sol_network': {
        'description': 'The state of the chain: slot, block height, epoch and how far '
                       'through it, real TPS split into vote and non-vote, node '
                       'version and health, circulating supply, market cap, inflation '
                       'and the SOL price. Use it to sanity-check an RPC endpoint '
                       'before trusting anything else it says.',
        'inputSchema': {'type': 'object', 'properties': dict(_COMMON)},
        'handler': _t_network,
    },
    'sol_validators': {
        'description': 'The validator set by stake: activated stake, share, '
                       'commission and whether the node is delinquent — plus the '
                       'Nakamoto coefficient, the number of validators that would '
                       'have to collude to halt the chain. Answers "who runs Solana" '
                       'and "how concentrated is it" in one call.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('how many to return, by stake (default 20)'),
            'sort': _str('stake (default) or commission', enum=['stake', 'commission']),
            'delinquent': _bool('true for only delinquent, false for only healthy'),
            **_COMMON}},
        'handler': _t_validators,
    },
    'sol_stake': {
        'description': 'Stake accounts a wallet can withdraw from: which validator '
                       'each delegates to, how much, and whether it is active, '
                       'activating, deactivating or already cooled down. Staked SOL '
                       'does not show up in sol_balance, so this is the missing half '
                       'of "how much SOL does this wallet control".',
        'inputSchema': {'type': 'object',
                        'properties': {'address': _str('the withdraw authority — '
                                                       'normally the wallet address'),
                                       **_COMMON},
                        'required': ['address']},
        'handler': _t_stake,
    },
    'sol_wallet': {
        'description': 'The local keystore, at ~/.mod/solana/keys.json with mode 0600 '
                       'and off the source tree. action=list|create|import|remove|'
                       'default|export|show. Secrets are only ever returned by an '
                       'explicit export. Creating a wallet here is what makes '
                       'sol_transfer able to sign without a secret in every call.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('list (default), create, import, remove, default, export, '
                           'show', enum=['list', 'create', 'import', 'remove',
                                         'default', 'export', 'show']),
            'name': _str('wallet name (default "default")'),
            'secret': _str('for import: a keypair JSON array, base58 secret, hex '
                           'seed, or a path to a Solana CLI keypair file'),
            'default': _bool('make this the wallet transfers sign with'),
            'overwrite': _bool('replace an existing wallet of the same name'),
            **_COMMON}},
        'handler': _t_wallet,
    },
    'sol_transfer': {
        'description': f'SEND VALUE. Transfers SOL, or an SPL token if you pass '
                       f'mint=, signed locally with a keystore wallet or a secret you '
                       f'supply. Creates the recipient\'s token account when it does '
                       f'not exist, which is the usual reason a token transfer fails. '
                       f'Anything worth more than ${SPEND_USD:,.0f} comes back as '
                       f'needs_confirm with a full plan and moves nothing — call '
                       f'again with confirm=true. Test on network=devnet first; '
                       f'a landed transfer cannot be undone.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _str('recipient wallet address (not a token account)'),
            'amount': _num('how much to send, in whole units — SOL, or tokens'),
            'mint': _str('send this SPL token instead of SOL'),
            'wallet': _str('which keystore wallet signs (default: the default one)'),
            'secret': _str('sign with this secret instead of the keystore'),
            'memo': _str('attach an on-chain memo'),
            'confirm': _bool('acknowledge the value guard and actually send'),
            'wait': _bool('wait for confirmation before returning (default true)'),
            **_COMMON}, 'required': ['to', 'amount']},
        'handler': _t_transfer,
    },
    'sol_swap': {
        'description': 'TRADE one token for another, for real. Jupiter prices the '
                       'best route across every Solana DEX and builds the '
                       'transaction; this signs it with a key from the local '
                       'keystore and sends it. Mainnet only, because that is where '
                       'the liquidity is. Read sol_quote first — it is the same '
                       'route without the signature — and expect the same guard as '
                       f'a transfer: over ${SPEND_USD:,.0f} you get needs_confirm '
                       'until you pass confirm=true. dry_run=true returns the route '
                       'it would have signed.',
        'inputSchema': {'type': 'object', 'properties': {
            'input': _str('mint or symbol to sell — e.g. SOL'),
            'output': _str('mint or symbol to buy — e.g. USDC'),
            'amount': _num('how much of the input token to sell, in whole units'),
            'slippage_bps': _num('slippage tolerance in basis points (default 50 = 0.5%)'),
            'wallet': _str('which keystore wallet signs (default: the default one)'),
            'secret': _str('sign with this key instead of the keystore — keypair '
                           'array, base58, hex, or a path'),
            'confirm': _bool('yes, actually trade it — required over the USD guard'),
            'dry_run': _bool('price and build nothing: return the route only'),
            'priority_lamports': _num('cap on the priority fee, in lamports'),
            'wait': _bool('wait for confirmation (default true)'),
            **_COMMON}, 'required': ['input', 'output', 'amount']},
        'handler': _t_swap,
    },
    'sol_airdrop': {
        'description': 'Free test SOL from the devnet or testnet faucet, into a '
                       'keystore wallet or any address. Refuses on mainnet, where no '
                       'faucet exists. This is how you get a funded wallet to '
                       'rehearse a transfer against before doing it for real.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('where to send it (default: the keystore wallet)'),
            'sol': _num('how much, default 1 — faucets cap around 2'),
            'wallet': _str('keystore wallet to fund, if no address is given'),
            **_COMMON}},
        'handler': _t_airdrop,
    },
    'sol_rpc': {
        'description': 'Any Solana JSON-RPC method, raw. The escape hatch for the '
                       'long tail this module does not wrap — getBlock, '
                       'getProgramAccounts, getTokenLargestAccounts, simulateTransaction '
                       'and the rest. Pass params as a JSON array exactly as the '
                       'Solana docs specify.',
        'inputSchema': {'type': 'object', 'properties': {
            'method': _str('e.g. getBlock, getTokenLargestAccounts'),
            'params': {'type': 'array', 'description': 'positional params, per the '
                                                       'Solana JSON-RPC docs'},
            **_COMMON}, 'required': ['method']},
        'handler': _t_rpc,
    },
    'sol_program': {
        'description': 'What is DEPLOYED at an address: which loader owns it, who '
                       'holds the upgrade authority (or whether it is frozen '
                       'forever), how big the code is, when it last changed, which '
                       'syscalls it imports — sol_invoke_signed_ means it can sign '
                       'for PDAs and move tokens — and, if it publishes an anchor '
                       'IDL, every instruction it accepts with their arguments and '
                       'accounts. accounts=true also lists the state accounts it '
                       'owns, decoded through that IDL. This is the tool for "what '
                       'is this program and what can I call on it".',
        'inputSchema': {'type': 'object', 'properties': {
            'program': _str('the program address'),
            'code': _bool('include the deployed ELF as base64'),
            'strings': _bool('include readable strings from .rodata (default true)'),
            'idl': _bool('look up the anchor IDL (default true)'),
            'accounts': _bool('also list the accounts this program owns'),
            'account_type': _str('with accounts=true: only this IDL account type'),
            'limit': _num('with accounts=true: how many to return (default 25)'),
            **_COMMON}, 'required': ['program']},
        'handler': _t_program,
    },
    'sol_idl': {
        'description': 'The interface of an anchor program: instruction names, '
                       'argument types, account lists and error codes. Reads the IDL '
                       'the program published on chain, or the one saved here. '
                       'action=set stores an IDL for a program that never published '
                       'one — after that sol_invoke can call it by name instead of '
                       'by raw bytes, which is the difference between playing with a '
                       'program and guessing at it.',
        'inputSchema': {'type': 'object', 'properties': {
            'program': _str('the program address'),
            'action': _str('get (default), set, clear',
                           enum=['get', 'set', 'clear']),
            'idl': {'type': 'object', 'description': 'for action=set: the IDL JSON',
                    'additionalProperties': True},
            'full': _bool('return the whole IDL document, not the summary'),
            **_COMMON}, 'required': ['program']},
        'handler': _t_idl,
    },
    'sol_deploy': {
        'description': 'DEPLOY A PROGRAM. Takes an ELF from a .so on this box '
                       '(path=), from base64 (data=), or from a program already live '
                       'on another cluster (clone=<address or one of memo, '
                       'spl_token, ata, name_service> — the fastest way to get '
                       'something real to call when there is no Rust toolchain '
                       'around), opens a buffer, writes it in chunks, and deploys. '
                       'Pass program= with an address you already control to UPGRADE '
                       'it in place instead. Runs as a background job because a real '
                       'deploy is hundreds of transactions: this returns a job id, '
                       'and action=status job=<id> follows it. Devnet unless you say '
                       'otherwise; mainnet needs confirm=true because it spends real '
                       'SOL and the rent is only refundable if you close the '
                       'program. The generated program keypair is saved to the '
                       'keystore before it is used — lose it and the address is '
                       'gone.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('deploy (default), status, list',
                           enum=['deploy', 'status', 'list']),
            'job': _str('for action=status: the job id'),
            'path': _str('a .so file on this box — target/deploy/yours.so'),
            'data': _str('the ELF as base64'),
            'clone': _str('copy the deployed bytes of this program from another '
                          'cluster'),
            'clone_network': _str('which cluster to clone FROM (default mainnet)'),
            'program': _str('upgrade this existing program instead of deploying a '
                            'new one — you must hold its upgrade authority'),
            'buffer': _str('resume from a buffer a previous attempt left behind'),
            'wallet': _str('keystore wallet that pays and becomes the upgrade '
                           'authority'),
            'secret': _str('sign with this secret instead of the keystore'),
            'max_data_len': _num('bytes to reserve for future upgrades '
                                 '(default: twice the ELF)'),
            'name': _str('keystore name for the generated program keypair'),
            'confirm': _bool('required on mainnet'),
            'wait': _num('seconds to block before returning the job (default 25)'),
            **_COMMON}},
        'handler': _t_deploy,
    },
    'sol_invoke': {
        'description': 'CALL A PROGRAM. Builds one instruction and simulates it '
                       'against live cluster state — logs, compute units, the '
                       'accounts as they would look afterwards, and a plain reason '
                       'when it fails (an anchor error code becomes the name and '
                       'message from its IDL). With an IDL it takes ix=<instruction '
                       'name> plus args and accounts BY NAME, fills in the sysvars, '
                       'the wallet and any PDA whose seeds the IDL declares, and '
                       'tells you which accounts it still needs. Without one, pass '
                       'data= as hex, base64 or text:<literal> and list the accounts '
                       'yourself. Nothing is signed until send=true, and a call that '
                       'fails simulation is not sent at all unless you add '
                       'force=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'program': _str('the program to call'),
            'ix': _str('instruction name from the IDL — omit to send raw data'),
            'args': {'type': 'object', 'description': 'instruction arguments by name, '
                                                      'borsh-encoded from the IDL',
                     'additionalProperties': True},
            'accounts': {'description': 'with an IDL: {"account_name": "<address>"} '
                                        'or {"name": {"seeds": [...]}} to derive a '
                                        'PDA. Without one: a list, each entry an '
                                        'address optionally prefixed "w:" writable, '
                                        '"s:" signer, "ws:" both. "self" means the '
                                        'signing wallet.'},
            'data': _str('raw instruction data — hex, base64, or text:<literal>'),
            'wallet': _str('keystore wallet that signs and pays'),
            'secret': _str('sign with this secret instead of the keystore'),
            'payer': _str('simulate as this address without holding its key'),
            'send': _bool('actually sign and send it (default false: simulate only)'),
            'force': _bool('send even though the simulation failed'),
            'idl': {'type': 'object', 'description': 'use this IDL for this call only',
                    'additionalProperties': True},
            **_COMMON}, 'required': ['program']},
        'handler': _t_invoke,
    },
    'sol_pda': {
        'description': 'Derive a program address from seeds — the off-curve address '
                       'a program can sign for. Seeds are text, addresses (any '
                       'base58 32-byte string is read as its bytes), integers as '
                       '{"u64": 7}, or raw {"hex": "00ff"}. Returns the address, the '
                       'bump, and how each seed was read, because a PDA that comes '
                       'out wrong is almost always a seed encoded the other way.',
        'inputSchema': {'type': 'object', 'properties': {
            'program': _str('the program the address is derived for'),
            'seeds': {'type': 'array', 'description': 'the seeds, in order',
                      'items': {}},
            **_COMMON}, 'required': ['program', 'seeds']},
        'handler': _t_pda,
    },
    'sol_authority': {
        'description': 'Who may replace a program\'s code. action=set hands the '
                       'upgrade authority to another key, action=revoke makes the '
                       'program immutable forever, action=close deletes a program or '
                       'a leftover deploy buffer and returns its rent. All three are '
                       'irreversible and all three return a plan with needs_confirm '
                       'until you pass confirm=true. Revoking is what "this contract '
                       'cannot rug you" actually means on Solana.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('set, revoke or close',
                           enum=['set', 'revoke', 'close']),
            'account': _str('the program, or a buffer address'),
            'new_authority': _str('for action=set: who gets it'),
            'recipient': _str('for action=close: where the rent goes '
                              '(default: the signer)'),
            'wallet': _str('keystore wallet holding the current authority'),
            'secret': _str('sign with this secret instead of the keystore'),
            'payer_wallet': _str('pay the fee from this wallet — an authority key '
                                 'is a permission and often holds no SOL'),
            'confirm': _bool('yes, really — required for all three'),
            **_COMMON}, 'required': ['action', 'account']},
        'handler': _t_authority,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so /tools/<name> and
    an MCP tools/call cannot diverge."""
    tool = TOOLS.get(name)
    if not tool:
        raise SolError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise SolError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        text = json.dumps(out, default=str, indent=2)
        return _result(id_, {'content': [{'type': 'text', 'text': text}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except SolError as e:
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
            'serverInfo': {'name': 'solana', 'version': version()},
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
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50710)))
    else:
        serve_stdio()
