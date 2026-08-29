#!/usr/bin/env python3
"""selfinsure mcp — sixteen tools, and the adjudicator's seat is the point.

An agent is not a spectator here. `si_queue` is a work queue of claims waiting
on a decision, `si_claim` is the case file, and `si_vote` is the decision — with
a reason attached, because the claimant reads it. A pool with `agent_policy=open`
lets any agent register itself and start adjudicating within one call.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50740 # Streamable HTTP — POST /mcp
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us afterwards.
    sys.path.append(HERE)

import pool as P                                             # noqa: E402
from pool import FEE_CAP_BPS, SelfInsureError                # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Mutual insurance pools that agents adjudicate. Anyone opens a pool with '
    'si_create_pool; members join and pay premiums with si_join and si_premium; '
    'every premium lands in the pool itself, not in a house account, and the '
    'operator fee is 0 by default and capped at '
    f'{FEE_CAP_BPS / 100:g}%. '
    'If you are here to adjudicate: si_register_agent once per pool (it returns '
    'an agent key, shown once), then si_queue for claims waiting on a decision, '
    'si_claim for the case file and evidence, si_vote to accept or reject with '
    'a reason. A claim settles the instant the pool\'s quorum is met and the '
    'payout leaves the pool in that same call. You cannot vote twice, cannot '
    'vote on your own claim, and cannot vote without a reason. '
    'If you are acting for a member: si_quote before joining (it tells you '
    'whether the pool could actually pay), si_join, si_claim_file when something '
    'happens, si_member for your position. '
    'The honest bit: a pool can accept a claim it cannot fund. That claim goes '
    'to state=unfunded and is paid from the next premiums in, oldest first. '
    'si_pool reports it as insolvency rather than hiding it, and si_distribute '
    'refuses to return surplus while any claim is unpaid. Surplus goes back to '
    'members pro rata — that is what makes these low- to no-profit.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_POOL = _str('pool id (e.g. bike-theft-4a1f), or its name if unambiguous')
_CLAIM = _str('claim id, e.g. bike-theft-4a1f#3')


# ── handlers ─────────────────────────────────────────────────────

def _t_pools(a):
    return P.pools(q=a.get('q'), state=a.get('state'), limit=a.get('limit', 100))


def _t_pool(a):
    return P.pool_info(a['pool'], full=a.get('full', True))


def _t_create(a):
    return P.create_pool(**a)


def _t_terms(a):
    return P.set_terms(a.pop('pool'), owner_key=a.pop('owner_key', None), **a)


def _t_quote(a):
    return P.quote(a['pool'], amount=a.get('amount'))


def _t_join(a):
    return P.join(a['pool'], a.get('name'), premium=a.get('premium'),
                  note=a.get('note'))


def _t_premium(a):
    return P.premium(a['pool'], member=a.get('member'),
                     member_key=a.get('member_key'), amount=a.get('amount'))


def _t_donate(a):
    return P.donate(a['pool'], a.get('amount'), name=a.get('name', 'donor'))


def _t_member(a):
    return P.member(a['pool'], member=a.get('member'))


def _t_register_agent(a):
    return P.register_agent(a['pool'], a.get('name'), kind=a.get('kind', 'ai'),
                            model=a.get('model', ''), about=a.get('about', ''))


def _t_admit(a):
    return P.admit_agent(a['pool'], a.get('agent'), owner_key=a.get('owner_key'),
                         active=a.get('active', True))


def _t_agents(a):
    return P.agents(a['pool'])


def _t_file(a):
    return P.file_claim(a['pool'], member=a.get('member'),
                        member_key=a.get('member_key'), amount=a.get('amount'),
                        title=a.get('title', ''), detail=a.get('detail', ''),
                        evidence=a.get('evidence'))


def _t_queue(a):
    return P.claims(pool=a.get('pool'), state=a.get('state', 'open'),
                    member=a.get('member'), limit=a.get('limit', 50))


def _t_claim(a):
    return P.claim(pool=a.get('pool'), claim=a.get('claim'))


def _t_vote(a):
    return P.vote(pool=a.get('pool'), claim=a.get('claim'), agent=a.get('agent'),
                  agent_key=a.get('agent_key'), accept=a.get('accept'),
                  reason=a.get('reason', ''))


def _t_withdraw(a):
    return P.withdraw_claim(pool=a.get('pool'), claim=a.get('claim'),
                            member_key=a.get('member_key'), reason=a.get('reason', ''))


def _t_distribute(a):
    return P.distribute(a['pool'], owner_key=a.get('owner_key'),
                        amount=a.get('amount'), confirm=bool(a.get('confirm')))


def _t_fees(a):
    return P.withdraw_fees(a['pool'], owner_key=a.get('owner_key'),
                           amount=a.get('amount'))


def _t_ledger(a):
    return P.ledger(pool=a.get('pool'), kind=a.get('kind'), limit=a.get('limit', 100))


def _t_stats(a):
    return P.stats()


TOOLS = {
    'si_pools': {
        'description': 'Every pool: what it covers, what it costs, who adjudicates '
                       'it, what it holds and whether it could pay. Start here. Each '
                       'row carries a solvency verdict in plain words, so a pool that '
                       'is technically live but broke does not read the same as a '
                       'healthy one.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by name, id or description'),
            'state': _str('open (taking members) or closed', enum=['open', 'closed']),
            'limit': _num('max rows (default 100)')}},
        'handler': _t_pools,
    },
    'si_pool': {
        'description': 'One pool in full: terms, the money (premiums in, claims paid, '
                       'surplus returned, operator fees), every member, every '
                       'adjudicator with their voting record, and every claim. The '
                       'money block is the one to read — loss_ratio is the share of '
                       'premium that came back to members as claims, and any '
                       'unfunded_claims figure above zero means the pool has accepted '
                       'more than it can pay.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'full': _bool('include members, agents and claims (default true)')},
            'required': ['pool']},
        'handler': _t_pool,
    },
    'si_create_pool': {
        'description': 'Open a pool. Anyone can — there is no application and no '
                       'underwriter. Only a name is required; everything else has a '
                       'mutual-shaped default. fee_bps is the one to think about: it '
                       'is the operator\'s cut of each premium, defaults to 0 (every '
                       f'cent stays in the pool) and cannot exceed {FEE_CAP_BPS / 100:g}%. '
                       'quorum and threshold decide how many agents must weigh in and '
                       'what share must accept. RETURNS THE OWNER KEY ONCE — it is '
                       'stored hashed and cannot be recovered.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('what the pool is for, e.g. "Courier bike theft"'),
            'about': _str('what is covered and what is not — adjudicators will judge '
                          'against this text, so write it like a rule, not a slogan'),
            'premium': _num('cost per period per member (default 0 — a pool can be '
                            'funded entirely by donations)'),
            'period_days': _num('how long one premium covers (default 30)'),
            'coverage': _num('maximum payout for a single claim (0 = uncapped, which '
                             'means one claim can drain the pool)'),
            'deductible': _num('the first N of any claim the member bears (default 0)'),
            'annual_cap': _num('maximum total paid to one member per rolling year'),
            'unit': _str('unit of account for display, e.g. USD (default USD)'),
            'fee_bps': _num(f'operator fee in basis points, 0..{FEE_CAP_BPS} '
                            '(default 0 = no profit to the operator)'),
            'quorum': _num('agent votes needed before a claim settles (default 1)'),
            'threshold': _num('share of those votes that must accept, 0..1 '
                              '(default 0.5 = simple majority)'),
            'waiting_days': _num('days after joining before a member can claim '
                                 '(default 0)'),
            'agent_policy': _str('open = any agent may register and adjudicate; '
                                 'approved = the owner admits them by hand',
                                 enum=['open', 'approved']),
            'reserve_floor': _num('an amount that can never be distributed as surplus'),
            'owner': _str('your handle, shown on the pool')},
            'required': ['name']},
        'handler': _t_create,
    },
    'si_set_terms': {
        'description': 'Change a pool\'s terms (owner key required). Never '
                       'retroactive: claims already filed are judged, and paid, under '
                       'the terms frozen at filing. Set state=closed to stop taking '
                       'new members without disturbing existing coverage.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'owner_key': _str('the owner key from si_create_pool'),
            'about': _str('revised description of what is covered'),
            'premium': _num('new premium'), 'coverage': _num('new per-claim cap'),
            'deductible': _num('new deductible'), 'annual_cap': _num('new annual cap'),
            'period_days': _num('new period'), 'waiting_days': _num('new waiting period'),
            'quorum': _num('new quorum'), 'threshold': _num('new accept threshold'),
            'fee_bps': _num(f'new operator fee, 0..{FEE_CAP_BPS}'),
            'agent_policy': _str('open or approved', enum=['open', 'approved']),
            'reserve_floor': _num('new reserve floor'),
            'state': _str('open or closed', enum=['open', 'closed'])},
            'required': ['pool', 'owner_key']},
        'handler': _t_terms,
    },
    'si_quote': {
        'description': 'What joining this pool would cost and what it would actually '
                       'pay on a claim of a given size — after the deductible and the '
                       'per-claim cap, which is usually less than the headline. Also '
                       'answers the question a brochure never does: funded_today says '
                       'whether the pool holds enough right now to pay that claim.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL,
            'amount': _num('the loss you want quoted (default: the pool\'s coverage cap)')},
            'required': ['pool']},
        'handler': _t_quote,
    },
    'si_join': {
        'description': 'Join a pool and pay the first premium. RETURNS THE MEMBER KEY '
                       'ONCE — it is what proves a later claim is yours, and it is '
                       'stored hashed. If the pool has a waiting period the response '
                       'says when coverage actually starts.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'name': _str('your name or handle in this pool'),
            'premium': _num('amount to pay now (default: the pool\'s premium)'),
            'note': _str('anything the pool should know about what you are insuring')},
            'required': ['pool', 'name']},
        'handler': _t_join,
    },
    'si_premium': {
        'description': 'Pay a premium into a pool you belong to. It extends your '
                       'coverage period, and if the pool owes anyone an accepted-but-'
                       'unfunded claim, your money pays that down first, oldest claim '
                       'first, before it becomes surplus.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'member': _str('your member id or name'),
            'member_key': _str('the member key from si_join'),
            'amount': _num('default: the pool\'s premium')},
            'required': ['pool', 'member', 'member_key']},
        'handler': _t_premium,
    },
    'si_donate': {
        'description': 'Put money into a pool without buying coverage — seed capital, '
                       'a backstop, a grant. It buys no claim rights and no share of '
                       'the surplus; it simply raises what the pool can pay. Like a '
                       'premium, it settles any unfunded claims on the way in.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'amount': _num('how much'),
            'name': _str('who to credit in the ledger')},
            'required': ['pool', 'amount']},
        'handler': _t_donate,
    },
    'si_member': {
        'description': 'One member\'s position: what they have paid in, what has come '
                       'back as claims and rebates, whether coverage is live and paid '
                       'up, their claims, and their pro-rata share of the surplus if '
                       'the pool distributed today. No key needed — members can see '
                       'each other, which is what keeps a mutual honest.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'member': _str('member id or name')},
            'required': ['pool', 'member']},
        'handler': _t_member,
    },
    'si_register_agent': {
        'description': 'Register yourself as an adjudicator on a pool. This is the '
                       'entry point for an agent: on an open pool you can vote '
                       'immediately, on an approved pool the owner must admit you '
                       'first. Say honestly whether you are ai or human and which '
                       'model you are — claimants see it on every ballot. RETURNS THE '
                       'AGENT KEY ONCE.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'name': _str('the name your votes will carry'),
            'kind': _str('ai or human', enum=['ai', 'human']),
            'model': _str('which model you are, if ai'),
            'about': _str('how you will judge — the standard you hold claims to')},
            'required': ['pool', 'name']},
        'handler': _t_register_agent,
    },
    'si_admit_agent': {
        'description': 'Owner admits or suspends an adjudicator on an '
                       'agent_policy=approved pool. Suspending does not erase past '
                       'votes or reopen settled claims.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'agent': _str('agent id or name'),
            'owner_key': _str('the owner key'),
            'active': _bool('true to admit, false to suspend (default true)')},
            'required': ['pool', 'agent', 'owner_key']},
        'handler': _t_admit,
    },
    'si_agents': {
        'description': 'Who judges this pool, and how they judge. Each adjudicator '
                       'carries their record: votes cast, accept rate, and '
                       'concordance — how often they landed on the side the pool '
                       'settled on. A rubber stamp and a reflexive rejecter both show '
                       'up in those two numbers.',
        'inputSchema': {'type': 'object', 'properties': {'pool': _POOL},
                        'required': ['pool']},
        'handler': _t_agents,
    },
    'si_claim_file': {
        'description': 'File a claim against a pool you belong to (member key '
                       'required). Write the detail for the agents who will read it '
                       'and list evidence as urls or CIDs. The response tells you the '
                       'most it could pay after deductible and caps, how many votes it '
                       'needs, and whether the pool could fund it today.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'member': _str('your member id or name'),
            'member_key': _str('the member key from si_join'),
            'amount': _num('what you are claiming'),
            'title': _str('one line the adjudicators see first'),
            'detail': _str('what happened, in enough detail to be judged'),
            'evidence': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'urls, CIDs or references backing the claim'}},
            'required': ['pool', 'member', 'member_key', 'amount', 'title']},
        'handler': _t_file,
    },
    'si_queue': {
        'description': 'The adjudication queue — claims waiting on a decision, newest '
                       'first, across every pool or one. This is what an agent polls. '
                       'Pass state=paid, rejected or unfunded to review history '
                       'instead. Each row shows votes so far against the quorum, so '
                       'you can see which claims your vote would settle.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _str('one pool, or omit for every pool'),
            'state': _str('open (default), paid, rejected, unfunded, withdrawn',
                          enum=['open', 'paid', 'rejected', 'unfunded', 'withdrawn']),
            'member': _str('only this member\'s claims'),
            'limit': _num('max rows (default 50)')}},
        'handler': _t_queue,
    },
    'si_claim': {
        'description': 'The case file for one claim: the full account, the evidence, '
                       'the frozen terms it will be judged under, and every ballot '
                       'cast so far with its reason. Read this before si_vote — '
                       'already_voted tells you if you are in there.',
        'inputSchema': {'type': 'object', 'properties': {
            'claim': _CLAIM, 'pool': _str('optional; inferred from the claim id')},
            'required': ['claim']},
        'handler': _t_claim,
    },
    'si_vote': {
        'description': 'Accept or reject a claim as a registered adjudicator. THE '
                       'CORE OF THE MODULE. A reason is mandatory — the claimant '
                       'reads it. When your vote completes the quorum the claim '
                       'settles in the same call: on accept the payout leaves the pool '
                       'immediately, and if the pool is short, the claim is recorded '
                       'as unfunded for the amount it still owes rather than quietly '
                       'reduced. You cannot vote twice, and you cannot vote on a claim '
                       'filed by a member with your name.',
        'inputSchema': {'type': 'object', 'properties': {
            'claim': _CLAIM, 'agent': _str('your agent id or name'),
            'agent_key': _str('the agent key from si_register_agent'),
            'accept': _bool('true to pay the claim, false to refuse it'),
            'reason': _str('why — judged against the pool\'s stated terms. Required.'),
            'pool': _str('optional; inferred from the claim id')},
            'required': ['claim', 'agent', 'agent_key', 'accept', 'reason']},
        'handler': _t_vote,
    },
    'si_withdraw_claim': {
        'description': 'A member withdraws their own claim before it settles — the '
                       'right move when you got the amount wrong or recovered the '
                       'loss. Once agents have settled it, it is final.',
        'inputSchema': {'type': 'object', 'properties': {
            'claim': _CLAIM, 'member_key': _str('the member key'),
            'reason': _str('why you are withdrawing'),
            'pool': _str('optional; inferred from the claim id')},
            'required': ['claim', 'member_key']},
        'handler': _t_withdraw,
    },
    'si_distribute': {
        'description': 'Return surplus to members, pro rata to what each paid in and '
                       'has not already had back. This is the mechanism that makes a '
                       'pool low- or no-profit: money that did not become claims goes '
                       'back to the people who paid it, not to the operator. It '
                       'refuses while any claim is unfunded, holds back every open '
                       'claim at its full payable amount plus the reserve floor, and '
                       'previews the split unless confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'owner_key': _str('the owner key'),
            'amount': _num('how much to return (default: everything free to return)'),
            'confirm': _bool('false (default) previews the split and moves nothing')},
            'required': ['pool', 'owner_key']},
        'handler': _t_distribute,
    },
    'si_withdraw_fees': {
        'description': 'Withdraw the operator fee a pool has accrued, if it set one. '
                       'Held apart from the pot and withdrawn explicitly so members '
                       'can see it in the ledger. On a fee_bps=0 pool this always '
                       'returns nothing, which is the intended shape.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _POOL, 'owner_key': _str('the owner key'),
            'amount': _num('default: everything accrued')},
            'required': ['pool', 'owner_key']},
        'handler': _t_fees,
    },
    'si_ledger': {
        'description': 'Every movement in append-only order: premiums, fees, payouts, '
                       'votes, distributions. A pool\'s balance is not a number you '
                       'have to take on trust — it is the sum of these lines.',
        'inputSchema': {'type': 'object', 'properties': {
            'pool': _str('one pool, or omit for all'),
            'kind': _str('one event type, e.g. payout, premium, vote, distribution'),
            'limit': _num('most recent N (default 100)')}},
        'handler': _t_ledger,
    },
    'si_stats': {
        'description': 'The whole deployment at a glance: pools, members, agents, '
                       'claims, accept rate, and — the number that decides whether '
                       'this is really a mutual — operator_share, the fraction of all '
                       'premium that went to operators rather than staying with '
                       'members.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_stats,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer so a route and a
    tools/call cannot drift apart."""
    tool = TOOLS.get(name)
    if not tool:
        raise SelfInsureError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, '') and not (
                required == 'accept' and isinstance(args.get('accept'), bool)):
            raise SelfInsureError(f'{name} needs {required}')
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
    except SelfInsureError as e:
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
            'serverInfo': {'name': 'selfinsure', 'version': version()},
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
