"""
starknet/strk20.py — the STRK20 privacy pool, read from the chain.

STRK20 (https://strk20.starknet.io/docs) is Starknet's privacy layer: one
pool contract holds ERC-20s as encrypted notes (UTXOs); private transfers
spend notes by publishing nullifiers and prove it with a Stwo STARK. DeFi
reaches the pool through anonymizer ("helper") contracts that expose one
entry point, `privacy_invoke`, which the pool calls inside an
`InvokeExternal` action and which returns a `Span<OpenNoteDeposit>` telling
the pool which open notes to credit.

Everything here is a public read — the pool's parameters, its event tape,
whether an address is registered, whether a note exists or a nullifier was
spent, which helpers the pool has called. The one builder,
invoke_action(), only *encodes* an InvokeExternal client action for a
helper; proving and submitting are the wallet's / SDK's job and need the
user's viewing key, which this module never asks for.
"""

import json
import os
import time
import urllib.request

import abi
import chain

POOLS = {
    # L2BEAT: PrivacyPool, Starknet mainnet. Verified live: get_version()=='2.1'.
    'mainnet': '0x040337b1af3c663e86e333bab5a4b28da8d4652a15a69beee2b677776ffe812a',
}
INVOKE_SELECTOR = chain.selector('privacy_invoke')
OPEN_NOTE_SALT = 1
DOCS = 'https://strk20.starknet.io/docs'
DOC_PAGES = {
    'what-is-strk20': 'What is STRK20?',
    'overview': 'Overview — choosing an integration route',
    'builder-privacy-overview': 'Builder Privacy Overview',
    'notes-and-nullifiers': 'Notes & Nullifiers',
    'viewing-keys': 'Encryption & Viewing Keys',
    'channels-and-subchannels': 'Channels & Note Discovery',
    'actions-and-proofs': 'Actions, Phases & Proofs',
    'compliance': 'Compliance & Auditing',
    'helpers/privacy-invoke': 'Anonymizer Contract Anatomy (privacy_invoke)',
    'helpers/swap-helper': 'Swap Helper',
    'helpers/vesu-lending-helper': 'Vesu Lending Helper',
    'helpers/escrow': 'Escrow',
    'starknet-wallet-api/overview': 'Starknet Wallet API',
    'starknet-wallet-api/starknet-js': 'starknet.js',
    'starknet-wallet-api/starknet-start-hook': 'starknet-start React hook',
    'sdk/getting-started': 'SDK: Getting Started',
    'sdk/register': 'SDK: Register',
    'sdk/deposit': 'SDK: Deposit',
    'sdk/transfer': 'SDK: Transfer',
    'sdk/withdraw': 'SDK: Withdraw',
    'sdk/deposit-transfer-surplus': 'SDK: Deposit + Transfer',
    'sdk/multi-op-batch': 'SDK: Multi-Operation Batches',
    'sdk/setup-requirements': 'SDK: Channels & Setup Requirements',
    'sdk/note-discovery': 'SDK: Discovering Notes',
    'sdk/discovery-providers': 'SDK: Discovery Providers',
    'sdk/proving-config': 'SDK: Proving Configuration',
    'app/anonymous-airdrop': 'App: Anonymous Airdrop',
}
_docs = {}   # page -> (fetched_at, text)


def pool(network=None):
    """Pool address: STRK20_POOL[_<NETWORK>] env first, then the known one."""
    network = network or chain.DEFAULT_NETWORK
    addr = os.environ.get(f'STRK20_POOL_{network.upper()}') or \
        (os.environ.get('STRK20_POOL') if network == chain.DEFAULT_NETWORK
         else None) or POOLS.get(network)
    if not addr:
        raise ValueError(f'no STRK20 pool known on {network} — set '
                         f'STRK20_POOL_{network.upper()}=0x...')
    return addr


def _read(fn, args=None, network=None):
    return abi.read(pool(network), fn, args, network=network)['result']


def _token_name(addr):
    a = chain.felt(addr)
    for k, t in chain.TOKENS.items():
        if chain.felt(t['address']) == a:
            return k
    return None


# ---------------------------------------------------------------- reads

def state(network=None):
    """The pool's live parameters."""
    network = network or chain.DEFAULT_NETWORK
    addr = pool(network)
    version = _read('get_version', network=network)
    fee = _read('get_fee_amount', network=network)
    holdings = {}
    for name in ('strk', 'eth', 'usdc'):
        try:
            b = chain.balance(addr, name, network=network)
            holdings[name] = b['amount']
        except Exception as e:
            holdings[name] = {'error': str(e)}
    return {
        'network': network,
        'pool': chain.felt(addr),
        'class_hash': chain.class_hash(addr, network=network),
        'version': abi.short_string(version) or version,
        'paused': _read('is_paused', network=network),
        'fee_amount': fee,
        'fee_amount_1e18': fee / 1e18,
        'fee_collector': _read('get_fee_collector', network=network),
        'proof_validity_blocks': _read('get_proof_validity_blocks',
                                       network=network),
        'auditor_public_key': _read('get_auditor_public_key', network=network),
        'screener_public_key': _read('get_screener_public_key', network=network),
        'upgrade_delay_s': _read('get_upgrade_delay', network=network),
        'public_holdings': holdings,
        'invoke_selector': INVOKE_SELECTOR,
        'explorer': f'https://voyager.online/contract/{addr}',
        'docs': DOCS,
    }


def activity(name=None, limit=20, network=None):
    """The pool's event tape, newest first, plus a count by event type.
    name filters: Deposit, Withdrawal, NoteUsed, EncNoteCreated,
    OpenNoteCreated, OpenNoteDeposited, ExternalContractInvoked,
    ViewingKeySet."""
    out = abi.events(pool(network), name=name, limit=limit, network=network)
    counts = {}
    for e in out['events']:
        counts[e['event']] = counts.get(e['event'], 0) + 1
        f = e.get('fields') or {}
        if 'token' in f and _token_name(f['token']):
            f['token_symbol'] = _token_name(f['token'])
    out['counts'] = counts
    return out


def user(address, limit=10, network=None):
    """Is this address registered in the pool, how many channels point at
    it, and its PUBLIC edges: deposits it made and withdrawals paid to it.
    Everything in between is encrypted — that is the point."""
    network = network or chain.DEFAULT_NETWORK
    a = chain.felt(address)
    pk = _read('get_public_key', {'user_addr': a}, network=network)
    registered = int(pk, 16) != 0
    out = {'address': a, 'network': network, 'registered': registered,
           'public_viewing_key': pk if registered else None}
    if registered:
        out['incoming_channels'] = _read('get_num_of_channels',
                                         {'recipient_addr': a}, network=network)
        out['enc_private_key'] = _read('get_enc_private_key',
                                       {'user_addr': a}, network=network)
    out['open_note_screening_policy'] = _read(
        'get_open_note_screening_policy', {'depositor': a}, network=network)
    p = pool(network)
    out['deposits'] = abi.events(p, 'Deposit', limit=limit, keys=[a],
                                 network=network)['events']
    out['withdrawals_to'] = abi.events(p, 'Withdrawal', limit=limit, keys=[a],
                                       network=network)['events']
    if registered:
        reg = abi.events(p, 'ViewingKeySet', limit=1, keys=[a],
                         network=network)['events']
        out['registered_at'] = reg[0]['block_number'] if reg else None
    return out


def note(note_id, network=None):
    """One note cell by id. Open notes (salt 1) carry a plaintext token and
    amount; encrypted notes are a packed ciphertext with token 0x0."""
    n = _read('get_note', [note_id], network=network)
    empty = int(n['packed_value'], 16) == 0 and int(n['token'], 16) == 0
    return {'note_id': chain.felt(note_id), 'exists': not empty,
            'kind': None if empty else
            ('open' if int(n['token'], 16) else 'encrypted'),
            **n, 'token_symbol': _token_name(n['token'])}


def nullifier(value, network=None):
    """Has this nullifier been published (i.e. its note spent)?"""
    return {'nullifier': chain.felt(value),
            'spent': _read('nullifier_exists', [value], network=network)}


def channel(recipient, index=0, network=None):
    """The encrypted channel record at `index` of a recipient's incoming
    list — ephemeral pubkey + ciphertexts, readable only with their key."""
    return {'recipient': chain.felt(recipient), 'index': int(index),
            **_read('get_channel_info',
                    {'recipient_addr': recipient, 'channel_index': int(index)},
                    network=network)}


# ---------------------------------------------------------------- helpers

def _returns_deposits(f):
    """Does the first return value decode as Span<OpenNoteDeposit>?"""
    outs = [o['type'] for o in f.get('outputs', [])]
    if not outs:
        return False
    t = outs[0]
    if t.startswith('(') and t.endswith(')'):
        t = abi._split(t[1:-1])[0]
    base, args = abi._generic(t)
    return base.endswith('Span') and bool(args) and \
        args[0].endswith('OpenNoteDeposit')


def _sig(f):
    return {'signature': abi.signature(f),
            'inputs': [{'name': i['name'], 'type': abi.short(i['type'])}
                       for i in f.get('inputs', [])],
            'returns': [abi.short(o['type']) for o in f.get('outputs', [])]}


def helper(address, network=None):
    """Inspect an anonymizer contract. Two kinds exist on mainnet:
    - invoke: exposes privacy_invoke, called by an InvokeExternal action;
    - compute: exposes privacy_compute + privacy_invoke_with_computation,
      called by a ComputeAndInvoke action (the shadow/sub-account route).
    conforms = the entry point returns Span<OpenNoteDeposit> as the pool
    requires."""
    c, ch = abi.codec(address, network=network)
    out = {'address': chain.felt(address), 'class_hash': ch,
           'functions': sorted(c.fns)}
    inv = c.fns.get('privacy_invoke')
    comp = c.fns.get('privacy_invoke_with_computation')
    out['privacy_invoke'] = bool(inv)
    if inv:
        out.update(kind='invoke', **_sig(inv), conforms=_returns_deposits(inv),
                   action='InvokeExternal')
    elif comp:
        out.update(kind='compute', **_sig(comp),
                   conforms=_returns_deposits(comp), action='ComputeAndInvoke')
        if 'privacy_compute' in c.fns:
            out['compute'] = _sig(c.fns['privacy_compute'])
    else:
        out.update(kind=None, conforms=False,
                   note='not an anonymizer: no privacy_invoke or '
                        'privacy_invoke_with_computation entry point')
    return out


def helpers(limit=200, network=None):
    """Every anonymizer contract the pool has invoked (from its
    ExternalContractInvoked events), with call counts and each one's
    privacy_invoke signature."""
    raw = abi.fetch_events(pool(network),
                           [[chain.selector('ExternalContractInvoked')]],
                           limit=int(limit), network=network)
    seen = {}
    for e in raw['events']:
        addr, sel = chain.felt(e['keys'][1]), chain.felt(e['keys'][2])
        s = seen.setdefault(addr, {'address': addr, 'calls': 0,
                                   'last_block': e['block_number'],
                                   'selectors': set()})
        s['calls'] += 1
        s['selectors'].add(sel)
    out = []
    for s in seen.values():
        try:
            h = helper(s['address'], network=network)
            names = {chain.selector(n): n for n in h['functions']}
        except Exception as e:
            h, names = {'error': str(e)}, {}
        out.append({**s, 'selectors': [names.get(x, x) for x in s['selectors']],
                    'kind': h.get('kind'),
                    'signature': h.get('signature'),
                    'conforms': h.get('conforms'),
                    'class_hash': h.get('class_hash')})
    out.sort(key=lambda x: -x['calls'])
    return {'pool': chain.felt(pool(network)), 'helpers': out,
            'invocations_scanned': len(raw['events']),
            'from_block': raw['from_block'], 'to_block': raw['to_block']}


def invoke_action(helper_address, args=None, network=None):
    """Build the InvokeExternal client action for a helper: ABI-encode
    `args` against the helper's own privacy_invoke signature, then wrap it
    in the pool's ClientAction enum (also encoded from the pool's ABI). The
    output drops into the SDK's action list or a hand-rolled prover input —
    it is not a transaction, and nothing is signed or sent."""
    h = helper(helper_address, network=network)
    if h['kind'] != 'invoke':
        raise ValueError(h.get('note') or
                         f'{h["address"]} is a {h["kind"]} helper — it is '
                         f'driven by a ComputeAndInvoke action, whose '
                         f'additional data is helper-specific; only '
                         f'privacy_invoke helpers can be encoded here')
    hc, _ = abi.codec(helper_address, network=network)
    calldata = hc.encode_inputs('privacy_invoke', args)
    pc, _ = abi.codec(pool(network), network=network)
    action = {'InvokeExternal': {'contract_address': chain.felt(helper_address),
                                 'calldata': calldata}}
    felts = pc.encode('privacy::actions::ClientAction', action)
    return {'helper': chain.felt(helper_address),
            'signature': h['signature'], 'conforms': h['conforms'],
            'calldata': [hex(x) for x in calldata],
            'client_action': {'InvokeExternal': {
                'contract_address': chain.felt(helper_address),
                'calldata': [hex(x) for x in calldata]}},
            'client_action_felts': [hex(x) for x in felts],
            'phase': 7,
            'notes': [
                'InvokeExternal is phase 7: at most one per pool transaction.',
                'The pool withdraws inputs to the helper first (Withdraw '
                'action, to_addr = helper), then calls privacy_invoke.',
                'Credit outputs to an open note created earlier in the same '
                'transaction (CreateOpenNote) — pass its note_id in args.',
                'Prove with the Privacy SDK / a proving service; this module '
                'never holds viewing keys.']}


# ---------------------------------------------------------------- docs

def docs(page=None, q=None):
    """The STRK20 docs as markdown, straight from strk20.starknet.io.
    No page: the index. page=: one page. q=: search every page."""
    if q:
        hits = []
        terms = [t.lower() for t in str(q).split()]
        for p in DOC_PAGES:
            try:
                text = _doc(p)
            except Exception:
                continue
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                i = min((low.find(t) for t in terms if t in low), default=0)
                hits.append({'page': p, 'title': DOC_PAGES[p], 'score': score,
                             'snippet': text[max(0, i - 120):i + 280].strip()})
        hits.sort(key=lambda h: -h['score'])
        return {'q': q, 'hits': hits[:8]}
    if not page:
        return {'source': DOCS, 'pages': [{'page': p, 'title': t,
                                           'url': f'{DOCS}/{p}'}
                                          for p, t in DOC_PAGES.items()]}
    page = str(page).strip('/').removesuffix('.md')
    if page.startswith('docs/'):
        page = page[5:]
    return {'page': page, 'url': f'{DOCS}/{page}', 'markdown': _doc(page)}


def _doc(page):
    hit = _docs.get(page)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    req = urllib.request.Request(f'{DOCS}/{page}.md',
                                 headers={'User-Agent': 'mod-starknet/0.2'})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode('utf-8', 'replace')
    if text.lstrip().startswith('<'):
        raise ValueError(f'no docs page {page!r} — see docs() for the index')
    _docs[page] = (time.time(), text)
    return text


if __name__ == '__main__':
    print(json.dumps(state(), indent=2, default=str))
