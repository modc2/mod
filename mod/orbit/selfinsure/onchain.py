#!/usr/bin/env python3
"""selfinsure onchain — the contract, and everything this module knows about it.

`contracts/src/SelfInsure.sol` is the mutual as a smart contract: the pot, the
members, the adjudicators, the claims, the FIFO of unfunded claims, the pro-rata
rebate, the fee cap, the seven-day fee notice, the optional oracle — and one
`transparency()` view that publishes all of it, including `operatorShareBps`,
the provider's profit as a share of every premium ever paid.

This file does three things and signs nothing:

    source / abi / compile   the code, the interface, and a build of it
    deploy                   through the eth module, which holds the keys
    transparency / claim     read a live pool back into plain numbers

Every deploy or read goes through the eth module (`chain.py` explains why —
a module holding both the ledger and the keys is the shape of every mutual
that became an insurer). Reads need no token; a deploy needs an account in
the eth module's keystore and, unless it is unlocked, its password.
"""

import json
import os
import subprocess

import chain as C

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACTS = os.path.join(HERE, 'contracts')
SRC = os.path.join(CONTRACTS, 'src')
OUT = os.path.join(CONTRACTS, 'out')

# name → (source path relative to src/, artifact file)
CONTRACT_FILES = {
    'SelfInsure': 'SelfInsure.sol',
    'SelfInsureFactory': 'SelfInsureFactory.sol',
    'SignedOracle': 'oracles/SignedOracle.sol',
}

ORACLE_MODES = ['none', 'advisory', 'required', 'automatic']
MAX_FEE_BPS = 1000
FEE_NOTICE_DAYS = 7
DAY = 86400

TERMS_FIELDS = ['premium', 'period', 'coverage', 'deductible', 'annualCap',
                'waitingPeriod', 'reserveFloor', 'feeBps', 'quorum', 'thresholdBps',
                'approvedAgentsOnly']
TRANSPARENCY_FIELDS = [
    'premiumsIn', 'donationsIn', 'feesAccrued', 'feesWithdrawn', 'paidOut',
    'distributed', 'rebatesUnclaimed', 'balance', 'held', 'openExposure',
    'unfundedOwed', 'reserveFloor', 'distributable', 'lossRatioBps',
    'operatorShareBps', 'memberShareBps', 'feeBps', 'pendingFeeBps',
    'pendingFeeAt', 'reconciles', 'solvent', 'members', 'agents', 'claims']
CLAIM_FIELDS = ['member', 'amount', 'title', 'evidence', 'filedAt', 'decidedAt',
                'state', 'paid', 'shortfall', 'accepts', 'rejects', 'exposure', 'frozen']
FROZEN_FIELDS = ['coverage', 'deductible', 'annualCap', 'quorum', 'thresholdBps',
                 'oracle', 'oracleMode']
CLAIM_STATES = ['open', 'accepted', 'rejected', 'withdrawn']


class OnchainError(C.ChainError):
    pass


# ── presets: the templates ───────────────────────────────────────

PRESETS = {
    'health': {
        'title': 'US health mutual',
        'about': ('Member-owned health mutual. Covers medically necessary care billed '
                  'by a licensed provider: emergency, inpatient, outpatient, surgery, '
                  'diagnostics, prescriptions, maternity, mental health. A claim is the '
                  "provider's itemised bill (or EOB) for the member, filed within 90 "
                  'days of service; the oracle, when one is set, verifies the bill and '
                  'its amount. Not covered: elective cosmetic procedures, care outside '
                  'a licensed setting, costs already paid by another plan. Adjudicators '
                  'judge against this text and nothing else.'),
        'terms': {'premium': 400, 'period_days': 30, 'coverage': 50_000, 'deductible': 250,
                  'annual_cap': 250_000, 'waiting_days': 30, 'reserve_floor': 25_000,
                  'fee_bps': 0, 'quorum': 2, 'threshold_bps': 6600,
                  'approved_agents_only': True},
        'why': {
            'premium': "roughly a single adult's marketplace premium per month",
            'coverage': 'one hospitalisation, most surgeries',
            'deductible': 'low, because the point is to be used',
            'annual_cap': 'per member per policy year',
            'waiting_days': 'stops joining on the way to the ER',
            'reserve_floor': "never distributed — the pool's own backstop",
            'fee_bps': f'the operator keeps NOTHING; the contract caps it at {MAX_FEE_BPS / 100:g}%',
            'quorum': 'two adjudicators, both must agree (66%)',
            'approved_agents_only': 'the pool admits who judges its claims',
        },
        'oracle': ('recommended: required. A SignedOracle whose reporters are the '
                   "provider's billing system or an auditor; the verified bill caps "
                   'the payout and an unverified bill cannot be paid.'),
        'asset': 'a USD stablecoin (USDC, 6 decimals) so a $400 premium is $400 all year',
    },
    'parametric': {
        'title': 'Parametric cover (oracle decides)',
        'about': ('Pays a fixed amount when a measurable event happens — a flight '
                  'delayed over 3 hours, rainfall under the threshold, a grid outage. '
                  'The oracle attests the event; no adjudicator is needed.'),
        'terms': {'premium': 20, 'period_days': 30, 'coverage': 500, 'deductible': 0,
                  'annual_cap': 0, 'waiting_days': 1, 'reserve_floor': 0,
                  'fee_bps': 0, 'quorum': 1, 'threshold_bps': 5000,
                  'approved_agents_only': False},
        'oracle': 'automatic — the attestation alone settles the claim',
        'asset': 'any',
    },
    'mutual': {
        'title': 'Plain mutual (the defaults)',
        'about': 'Members pay in, adjudicators judge, surplus comes back.',
        'terms': {'premium': 50, 'period_days': 30, 'coverage': 2_000, 'deductible': 0,
                  'annual_cap': 0, 'waiting_days': 0, 'reserve_floor': 0,
                  'fee_bps': 0, 'quorum': 1, 'threshold_bps': 5000,
                  'approved_agents_only': False},
        'oracle': 'optional',
        'asset': 'any',
    },
}


def preset(kind='health', decimals=6, **overrides):
    """A template's terms, in the asset's base units, ready for the constructor.
    Amounts in the preset are whole units (dollars); `decimals` scales them."""
    if kind not in PRESETS:
        raise OnchainError(f'{kind!r} is not a preset — {", ".join(PRESETS)}', status=404)
    p = PRESETS[kind]
    human = dict(p['terms'])
    for k, v in overrides.items():
        if v not in (None, '') and k in human:
            human[k] = type(human[k])(v) if not isinstance(human[k], bool) else \
                (str(v).lower() in ('1', 'true', 'yes') if isinstance(v, str) else bool(v))
    return {'preset': kind, 'title': p['title'], 'about': p['about'],
            'terms_human': human, 'decimals': int(decimals),
            'terms': terms_to_chain(human, decimals),
            'why': p.get('why', {}), 'oracle': p['oracle'], 'asset': p['asset']}


def terms_to_chain(h, decimals):
    """Human terms (dollars, days, bps) → the Terms struct in field order."""
    fee = int(h.get('fee_bps', 0) or 0)
    if not 0 <= fee <= MAX_FEE_BPS:
        raise OnchainError(f'fee_bps must be 0..{MAX_FEE_BPS} — the contract will not '
                           'deploy above the cap either')
    q = int(h.get('quorum', 1) or 1)
    th = int(h.get('threshold_bps', 5000) or 5000)
    if q < 1 or not 0 < th <= 10000:
        raise OnchainError('quorum must be ≥ 1 and threshold_bps in 1..10000')
    return [
        C.to_base(h.get('premium', 0) or 0, decimals),
        int(float(h.get('period_days', 30) or 30) * DAY),
        C.to_base(h.get('coverage', 0) or 0, decimals),
        C.to_base(h.get('deductible', 0) or 0, decimals),
        C.to_base(h.get('annual_cap', 0) or 0, decimals),
        int(float(h.get('waiting_days', 0) or 0) * DAY),
        C.to_base(h.get('reserve_floor', 0) or 0, decimals),
        fee, q, th, bool(h.get('approved_agents_only', False)),
    ]


def terms_from_chain(t, decimals):
    if isinstance(t, dict):
        t = [t.get(k) for k in TERMS_FIELDS]
    t = list(t)
    return {
        'premium': C.human_str(t[0], decimals), 'period_days': t[1] / DAY,
        'coverage': C.human_str(t[2], decimals), 'deductible': C.human_str(t[3], decimals),
        'annual_cap': C.human_str(t[4], decimals) if t[4] else None,
        'waiting_days': t[5] / DAY, 'reserve_floor': C.human_str(t[6], decimals),
        'fee_bps': t[7], 'fee_pct': t[7] / 100, 'quorum': t[8],
        'threshold_bps': t[9], 'threshold_pct': t[9] / 100,
        'approved_agents_only': bool(t[10]),
    }


def config_tuple(name, about, asset=None, owner=None, oracle=None, oracle_mode='none',
                 terms=None):
    """The Config struct the constructor / factory.open / initialize take."""
    if not (name or '').strip():
        raise OnchainError('a pool needs a name')
    mode = str(oracle_mode or 'none').lower()
    if mode not in ORACLE_MODES:
        raise OnchainError(f"oracle_mode is one of {', '.join(ORACLE_MODES)}")
    zero = '0x0000000000000000000000000000000000000000'
    return [name.strip(), about or '', asset or zero, owner or zero, oracle or zero,
            ORACLE_MODES.index(mode), list(terms)]


# ── source, abi, build ───────────────────────────────────────────

def sources():
    """Every .sol under contracts/src, keyed the way solc resolves imports."""
    out = {}
    for root, _, files in os.walk(SRC):
        for f in files:
            if f.endswith('.sol'):
                path = os.path.join(root, f)
                out[os.path.relpath(path, SRC)] = open(path).read()
    return out


def source(name='SelfInsure'):
    rel = CONTRACT_FILES.get(name)
    if not rel:
        raise OnchainError(f'{name!r} is not a contract here — {", ".join(CONTRACT_FILES)}',
                           status=404)
    return open(os.path.join(SRC, rel)).read()


def artifact(name='SelfInsure'):
    """The forge build output, if this box has run one."""
    rel = CONTRACT_FILES.get(name)
    if not rel:
        raise OnchainError(f'no contract {name!r}', status=404)
    path = os.path.join(OUT, os.path.basename(rel), f'{name}.json')
    try:
        with open(path) as f:
            a = json.load(f)
    except FileNotFoundError:
        return None
    return {'abi': a.get('abi'), 'bytecode': (a.get('bytecode') or {}).get('object'),
            'compiler': (a.get('metadata') or {}).get('compiler', {}).get('version'),
            'built': path}


def abi(name='SelfInsure'):
    a = artifact(name)
    if a and a.get('abi'):
        return a['abi']
    return compile(name)['abi']


def compile(name='SelfInsure', how='auto'):
    """Build. `forge` if the box has it (offline, pinned solc), else the eth
    module's /compile with the same sources. Either way the result carries the
    compiler version, so a deployed bytecode can be reproduced later."""
    if how in ('auto', 'forge'):
        forge = _which('forge') or os.path.expanduser('~/.foundry/bin/forge')
        if os.path.exists(forge):
            r = subprocess.run([forge, 'build', '--silent'], cwd=CONTRACTS,
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                a = artifact(name)
                if a:
                    return {'contract': name, 'via': 'forge', **a}
            elif how == 'forge':
                raise OnchainError(f'forge build failed: {(r.stderr or r.stdout)[-800:]}',
                                   status=500)
    r = C._request('ethereum', '/compile', method='POST',
                   body={'sources': sources(), 'full': True})
    for c in r.get('contracts') or []:
        if c.get('name') == name:
            return {'contract': name, 'via': 'eth', 'abi': c.get('abi'),
                    'bytecode': c.get('bytecode'),
                    'compiler': (r.get('compiler') or {}).get('version')}
    raise OnchainError(f'the eth module compiled the sources but returned no {name}',
                       status=502)


def _which(cmd):
    for d in os.environ.get('PATH', '').split(os.pathsep):
        p = os.path.join(d, cmd)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def describe():
    """What the contract is, for someone deciding whether to deploy it."""
    a = artifact('SelfInsure')
    return {
        'contracts': {
            'SelfInsure': 'the mutual: members, premiums, claims, agents, oracle, rebates, '
                          'transparency()',
            'SelfInsureFactory': 'anyone opens a pool (EIP-1167 clone of one audited '
                                 'bytecode); openHealth() deploys the health template',
            'SignedOracle': 'real-world data signed by named reporters — a billing '
                            'system, an auditor, a Chainlink Functions consumer',
        },
        'guarantees': [
            f'operator fee hard-capped at {MAX_FEE_BPS / 100:g}% of premium (MAX_FEE_BPS, constant)',
            f'a fee raise is announced on chain and applies {FEE_NOTICE_DAYS} days later; cuts are immediate',
            'transparency().operatorShareBps = fees / gross premium — the provider\'s profit, public',
            'every premium is credited to the pot; fees are the only part that leaves',
            'surplus is distributed pro rata to net contribution and pulled by members',
            'distribute() reverts while any accepted claim is unpaid',
            'an unfunded claim is recorded as owed and paid FIFO from the next money in',
            'terms are frozen per claim at filing — a change cannot reach an open claim',
            'agents cannot vote twice, vote without a reason, or vote on their own claim',
            'oracle is optional: none / advisory / required (gates + caps) / automatic',
            'transparency().reconciles proves the contract holds what the books say',
        ],
        'presets': {k: v['title'] for k, v in PRESETS.items()},
        'built': bool(a), 'compiler': (a or {}).get('compiler'),
        'source_files': sorted(sources()),
        'foundry': os.path.join(CONTRACTS, 'foundry.toml'),
    }


# ── deploy (through the eth module) ──────────────────────────────

def deploy(account, network=None, password=None, name=None, about='', asset=None,
           owner=None, oracle=None, oracle_mode='none', preset_='health', decimals=None,
           terms=None, confirm=False, **overrides):
    """Deploy one SelfInsure pool. Returns the address, the tx, and the
    transparency view read straight back so the first thing anyone sees is
    the operator share: 0."""
    net = network or C.chain_spec('ethereum')['default_network']
    dec = int(decimals) if decimals not in (None, '') else (
        int(C.asset('ethereum', net, asset)['decimals']) if asset else 18)
    if terms is None:
        terms = preset(preset_, dec, **overrides)['terms']
    else:
        terms = terms_to_chain(terms, dec) if isinstance(terms, dict) else list(terms)
    cfg = config_tuple(name or PRESETS.get(preset_, {}).get('title', 'Mutual'),
                       about or PRESETS.get(preset_, {}).get('about', ''),
                       asset, owner, oracle, oracle_mode, terms)
    r = _deploy('SelfInsure', account, net, [cfg], password, confirm,
                note=f'selfinsure pool {cfg[0]!r}')
    out = {'contract': 'SelfInsure', 'address': r.get('address'), 'network': net,
           'txid': r.get('hash') or r.get('transaction_hash'), 'explorer': r.get('explorer'),
           'decimals': dec, 'config': {'name': cfg[0], 'asset': cfg[2], 'owner': cfg[3],
                                       'oracle': cfg[4], 'oracle_mode': ORACLE_MODES[cfg[5]],
                                       'terms': terms_from_chain(terms, dec)},
           'compiler': (r.get('compiler') or {}).get('version') or r.get('compiler')}
    if out['address']:
        try:
            out['transparency'] = transparency(out['address'], net, decimals=dec)
        except C.ChainError as e:
            out['transparency_error'] = e.message
    return out


def deploy_factory(account, network=None, password=None, confirm=False):
    net = network or C.chain_spec('ethereum')['default_network']
    r = _deploy('SelfInsureFactory', account, net, [], password, confirm,
                note='selfinsure factory')
    return {'contract': 'SelfInsureFactory', 'address': r.get('address'), 'network': net,
            'txid': r.get('hash') or r.get('transaction_hash'), 'explorer': r.get('explorer'),
            'note': 'open pools with open(config) or openHealth(name, about, asset, unit, '
                    'oracle, mode); every pool is listed in all()'}


def deploy_oracle(account, network=None, password=None, owner=None, confirm=False):
    net = network or C.chain_spec('ethereum')['default_network']
    zero = '0x0000000000000000000000000000000000000000'
    r = _deploy('SignedOracle', account, net, [owner or zero], password, confirm,
                note='selfinsure signed oracle')
    return {'contract': 'SignedOracle', 'address': r.get('address'), 'network': net,
            'txid': r.get('hash') or r.get('transaction_hash'), 'explorer': r.get('explorer'),
            'note': 'name reporters with setReporter(address, true, label); a reporter '
                    'signs digest(pool, claimId, ok, verifiedAmount, dataHash, expiry) '
                    'and anyone relays it with submit()'}


def _deploy(contract, account, network, args, password, confirm, note=None):
    if not account:
        raise OnchainError('account= is the eth module keystore account that signs the '
                           'deploy — selfinsure holds no keys')
    body = {'account': account, 'sources': sources(), 'contract': contract,
            'args': args, 'network': network, 'password': password,
            'confirm': bool(confirm), 'name': contract, 'note': note, 'wait': True}
    return C._request('ethereum', '/deploy', method='POST', token=C.eth_token(), body=body)


# ── read a live pool ─────────────────────────────────────────────

def read(address, function, args=None, network=None, contract='SelfInsure'):
    address = C.need_address('ethereum', address)
    r = C._request('ethereum', f'/contracts/{address}/read', method='POST',
                   body={'function': function, 'args': args or [], 'network': network,
                         'abi': abi(contract)})
    return r.get('result')


def _struct(value, fields):
    if isinstance(value, dict):
        return {k: value.get(k) for k in fields}
    if isinstance(value, (list, tuple)):
        return dict(zip(fields, value))
    return value


def transparency(address, network=None, decimals=None):
    """The whole pool as a person would read it, straight off the chain: the
    money, the provider's share of it, and whether the numbers reconcile."""
    net = network or C.chain_spec('ethereum')['default_network']
    t = _struct(read(address, 'transparency', network=net), TRANSPARENCY_FIELDS)
    asset = read(address, 'asset', network=net)
    zero = '0x0000000000000000000000000000000000000000'
    if decimals is None:
        info = C.asset('ethereum', net, None if asset.lower() == zero else asset)
        decimals, symbol = info['decimals'], info['symbol']
    else:
        symbol = 'ETH' if asset.lower() == zero else 'TOKEN'
    h = lambda k: C.human_str(t[k], decimals)  # noqa: E731
    fee_now = int(t['feeBps'])
    terms = terms_from_chain(read(address, 'terms', network=net), decimals)
    oracle = read(address, 'oracle', network=net)
    mode = int(read(address, 'oracleMode', network=net) or 0)
    return {
        'address': address, 'network': net, 'name': read(address, 'name', network=net),
        'asset': None if asset.lower() == zero else asset, 'symbol': symbol,
        'decimals': decimals, 'owner': read(address, 'owner', network=net),
        'oracle': None if oracle.lower() == zero else oracle,
        'oracle_mode': ORACLE_MODES[mode] if mode < len(ORACLE_MODES) else mode,
        'money': {
            'premiums_in': h('premiumsIn'), 'donations_in': h('donationsIn'),
            'balance': h('balance'), 'held_on_chain': h('held'),
            'paid_in_claims': h('paidOut'), 'returned_to_members': h('distributed'),
            'rebates_unclaimed': h('rebatesUnclaimed'),
            'open_exposure': h('openExposure'), 'unfunded_claims': h('unfundedOwed'),
            'reserve_floor': h('reserveFloor'), 'distributable': h('distributable'),
            'loss_ratio': int(t['lossRatioBps']) / 10000,
        },
        'provider': {
            # This block is the point. An insurer's medical loss ratio is a
            # regulatory filing; here it is a view function.
            'fee_bps_now': fee_now, 'fee_pct_now': fee_now / 100,
            'fee_cap_bps': MAX_FEE_BPS,
            'profit_accrued': h('feesAccrued'), 'profit_withdrawn': h('feesWithdrawn'),
            'profit_share_of_premium': int(t['operatorShareBps']) / 10000,
            'member_share_of_premium': int(t['memberShareBps']) / 10000,
            'pending_fee_bps': int(t['pendingFeeBps']) or None,
            'pending_fee_at': int(t['pendingFeeAt']) or None,
            'notice': f'{FEE_NOTICE_DAYS} days before any raise takes effect',
        },
        'solvency': {
            'reconciles': bool(t['reconciles']), 'solvent': bool(t['solvent']),
            'verdict': ('insolvent — accepted claims are owed; the next premiums pay them'
                        if int(t['unfundedOwed']) else
                        'every open claim could be paid in full today'
                        if bool(t['solvent']) else
                        'thin — if every open claim were accepted the pool would be short'),
        },
        'terms': terms,
        'counts': {'members': int(t['members']), 'agents': int(t['agents']),
                   'claims': int(t['claims'])},
    }


def claim(address, claim_id, network=None, decimals=None):
    net = network or C.chain_spec('ethereum')['default_network']
    c = _struct(read(address, 'claim', [int(claim_id)], network=net), CLAIM_FIELDS)
    f = _struct(c.get('frozen'), FROZEN_FIELDS)
    if decimals is None:
        asset = read(address, 'asset', network=net)
        zero = '0x0000000000000000000000000000000000000000'
        decimals = C.asset('ethereum', net, None if asset.lower() == zero else asset)['decimals']
    ballots = read(address, 'ballots', [int(claim_id)], network=net) or []
    state = int(c['state'])
    out = {
        'id': int(claim_id), 'pool': address, 'member': c['member'],
        'amount': C.human_str(c['amount'], decimals), 'title': c['title'],
        'evidence': c['evidence'], 'filed': int(c['filedAt']),
        'decided': int(c['decidedAt']) or None,
        'state': CLAIM_STATES[state] if state < 4 else state,
        'paid': C.human_str(c['paid'], decimals), 'owed': C.human_str(c['shortfall'], decimals),
        'accepts': int(c['accepts']), 'rejects': int(c['rejects']),
        'terms': {'coverage': C.human_str(f['coverage'], decimals),
                  'deductible': C.human_str(f['deductible'], decimals),
                  'annual_cap': C.human_str(f['annualCap'], decimals),
                  'quorum': int(f['quorum']), 'threshold_bps': int(f['thresholdBps']),
                  'oracle': f['oracle'], 'oracle_mode': ORACLE_MODES[int(f['oracleMode'])]},
        'ballots': [_struct(b, ['agent', 'accept', 'reason', 'at']) for b in ballots],
    }
    if int(f['oracleMode']):
        o = read(address, 'oracleView', [int(claim_id)], network=net)
        o = _struct(o, ['attested', 'ok', 'verified', 'dataHash'])
        out['oracle'] = {'attested': bool(o['attested']), 'ok': bool(o['ok']),
                         'verified_amount': C.human_str(o['verified'], decimals),
                         'data_hash': o['dataHash']}
    return out


def pool_calls():
    """The write calls a member, an agent or the operator makes — with the eth
    module's /contracts/{address}/write, or any wallet — in one place."""
    return {
        'member': {
            'join(uint256 amount)': 'payable; ETH: amount == msg.value. ERC-20: approve first',
            'payPremium(uint256 amount)': 'top up; one period of coverage per premium',
            'fileClaim(uint256 amount, string title, string evidence) → id': 'evidence is a URI/CID',
            'withdrawClaim(uint256 id)': 'before it settles',
            'claimRebate()': 'pull your share of any distribution',
        },
        'agent': {
            'registerAgent(string name, string kind, string model)': 'kind: ai | human',
            'vote(uint256 id, bool accept, string reason)': 'reason is mandatory',
            'settle(uint256 id)': 'anyone: re-check a claim waiting on the oracle',
        },
        'anyone': {
            'donate(uint256 amount)': 'seed or backstop the pot; no coverage, no surplus',
            'settleBacklog(uint256 maxSteps)': 'pay unfunded claims from the pot, oldest first',
            'transparency()': 'everything, including operatorShareBps',
        },
        'owner': {
            'setTerms(Terms)': 'anything but the fee; never retroactive',
            'proposeFee(uint16 bps)': f'raise = {FEE_NOTICE_DAYS}-day notice; cut = immediate; cap {MAX_FEE_BPS}',
            'applyFee()': 'after the notice (anyone may call)',
            'setOracle(address, mode)': 'none | advisory | required | automatic',
            'admitAgent(address, bool)': 'on approvedAgentsOnly pools',
            'distribute(uint256 amount)': 'return surplus; 0 = everything free',
            'withdrawFees(address to, uint256 amount)': 'the operator\'s cut, and only that',
            'setClosed(bool)': 'stop taking members',
        },
    }
