#!/usr/bin/env python3
"""selfinsure guide — what everything means, and a pool drafted from a sentence.

Pure functions, standard library, no model, no network. The agent (agent.py),
the MCP tools (si_explain, si_draft) and the app's /create page all read from
here, so a term is explained the same way everywhere.

    topics()                 every explainer, for a glossary
    explain(query)           the best explainer for a question (+ related ones)
    templates()              starter pools: bike theft, phones, pets, health…
    draft(text)              "a pool for 20 couriers, $8 a month, up to $600"
                             → create_pool arguments + a viability check
    check(terms, members)    could this pool actually pay? in plain words
"""

import re

FEE_CAP_BPS = 1000

# ── what everything means ─────────────────────────────────────────
# keys: words a person would use when asking. text: the answer, plainly.

TOPICS = [
    {'id': 'what', 'title': 'What selfinsure is',
     'keys': ['what', 'selfinsure', 'self', 'insure', 'insurance', 'mean', 'means',
              'about', 'idea', 'explain', 'overview', 'module', 'this'],
     'text': 'A group of people puts money into a shared pot, and when one of them '
             'has a loss the pot pays them. That is insurance with the insurance '
             'company taken out. Nobody profits from denying claims: the money that '
             'is not paid out in claims goes back to the members who paid it in. '
             'Claims are decided by adjudicators (AI agents or people) who must give '
             'a reason for every vote, and every movement of money is on a public '
             'ledger. The same pool can run here on this node (a local ledger) or '
             'as an Ethereum contract.',
     'link': '/'},
    {'id': 'mutual', 'title': 'Mutual',
     'keys': ['mutual', 'member', 'owned', 'cooperative', 'coop', 'company', 'insurer',
              'difference', 'versus', 'vs', 'normal', 'traditional'],
     'text': 'A mutual is owned by the people it insures. A normal insurer has '
             'shareholders: every claim it does not pay is profit for them. In a '
             'mutual there is no one on the other side — premiums stay pool money, '
             'and whatever is left after claims is returned to members.'},
    {'id': 'pool', 'title': 'Pool',
     'keys': ['pool', 'pot', 'fund', 'group', 'pools'],
     'text': 'A pool is one shared pot with one set of rules: what it covers, what '
             'it costs, how much one claim can pay, and who decides claims. Anyone '
             'can open one — only a name is required.',
     'link': '/pools'},
    {'id': 'create', 'title': 'How to create your own pool',
     'keys': ['create', 'make', 'start', 'open', 'set', 'setup', 'new', 'own', 'my',
              'build', 'launch', 'begin', 'how'],
     'text': '1. Pick what it covers and write it as a rule ("bike stolen while '
             'locked; not lost, not damaged"). Adjudicators judge claims against '
             'exactly that text.\n'
             '2. Set the premium (what each member pays per period) and the '
             'coverage (the most one claim can pay).\n'
             '3. Add a deductible and a waiting period so people do not join the day '
             'after a loss.\n'
             '4. Choose who decides claims: how many votes (quorum) and what share '
             'must say yes (threshold).\n'
             '5. Leave the operator fee at 0% unless the pool should pay you for '
             'running it (cap 10%).\n'
             '6. Create it. You get an OWNER KEY once — save it, nobody can recover '
             'it. Share the pool id with the people who should join.\n'
             'The Create page walks through each step and checks whether the numbers '
             'can actually pay out.',
     'link': '/create'},
    {'id': 'premium', 'title': 'Premium',
     'keys': ['premium', 'cost', 'price', 'pay', 'monthly', 'contribution', 'much'],
     'text': 'What each member pays per period (30 days by default) to stay '
             'covered. The whole premium lands in the pool — minus the operator fee, '
             'which is 0% unless the pool says otherwise.'},
    {'id': 'coverage', 'title': 'Coverage (per-claim cap)',
     'keys': ['coverage', 'cover', 'covered', 'cap', 'limit', 'maximum', 'max',
              'payout'],
     'text': 'The most a single claim can pay. 0 means uncapped, which also means '
             'one large claim can empty the whole pool — almost every pool should '
             'set it.'},
    {'id': 'deductible', 'title': 'Deductible',
     'keys': ['deductible', 'excess', 'first', 'bear'],
     'text': 'The first part of every claim the member pays themselves. A $50 '
             'deductible on a $400 loss means the pool pays $350. It keeps small '
             'claims from eating the pool and keeps premiums low.'},
    {'id': 'annual_cap', 'title': 'Annual cap',
     'keys': ['annual', 'yearly', 'year', 'per'],
     'text': 'The most one member can be paid in a rolling year, across all their '
             'claims. Stops one member from draining the pool with many claims.'},
    {'id': 'waiting', 'title': 'Waiting period',
     'keys': ['waiting', 'wait', 'period', 'days', 'join', 'before'],
     'text': 'Days after joining before a member may claim. Without it someone '
             'could join right after their bike is stolen and claim the next day.'},
    {'id': 'fee', 'title': 'Operator fee (the provider\'s profit)',
     'keys': ['fee', 'operator', 'profit', 'provider', 'cut', 'bps', 'basis',
              'commission', 'earn', 'money', 'make'],
     'text': 'The share of each premium the pool owner keeps for running it. It '
             'defaults to 0%, can never exceed 10%, and is published: anyone can see '
             'how much the operator has taken. On chain a fee raise needs 7 days of '
             'public notice; a cut is immediate. Compare US health insurers, which '
             'may keep 15-20% of premium.'},
    {'id': 'adjudicator', 'title': 'Adjudicators (who decides claims)',
     'keys': ['adjudicator', 'adjudicate', 'agent', 'agents', 'judge', 'decide',
              'vote', 'votes', 'ai', 'human', 'reviewer'],
     'text': 'An adjudicator reads a claim and votes accept or reject, with a '
             'reason the claimant can read. They can be AI agents or people. They '
             'cannot vote twice, cannot vote on their own claim, and cannot vote '
             'without a reason. With agent_policy=open anyone may register as one; '
             'with approved the owner admits them.'},
    {'id': 'quorum', 'title': 'Quorum and threshold',
     'keys': ['quorum', 'threshold', 'majority', 'votes', 'needed', 'unanimous',
              'percent'],
     'text': 'Quorum is how many votes a claim needs before it settles. Threshold '
             'is the share of those votes that must accept (0.5 = simple majority, '
             '0.66 = two thirds). A claim settles — and is paid — the moment both '
             'are met.'},
    {'id': 'claim', 'title': 'Filing a claim',
     'keys': ['claim', 'claims', 'file', 'filing', 'loss', 'happened', 'stolen',
              'accident', 'evidence', 'payout', 'paid', 'get'],
     'text': 'A member files a claim with an amount, a description and evidence '
             '(photos, receipts, a police report). It joins the adjudicators\' queue. '
             'When enough votes accept, the payout (after deductible and caps) leaves '
             'the pool in the same step. Claims are judged under the terms that were '
             'in force when they were filed.'},
    {'id': 'unfunded', 'title': 'Unfunded claims (when the pool is short)',
     'keys': ['unfunded', 'insolvent', 'short', 'broke', 'empty', 'enough', 'queue',
              'owed', 'solvency', 'solvent', 'fail', 'run', 'runs', 'out', 'bankrupt'],
     'text': 'A pool can accept a claim it cannot pay yet. That claim is not cut — '
             'it is recorded as owed and paid oldest-first from the next premiums '
             'in. The pool shows this openly as insolvency, and no surplus can be '
             'returned while any claim is unpaid.'},
    {'id': 'surplus', 'title': 'Surplus and rebates',
     'keys': ['surplus', 'rebate', 'refund', 'back', 'leftover', 'distribute',
              'return', 'returned', 'dividend'],
     'text': 'Money left after claims, above the reserve floor, belongs to members. '
             'The owner can distribute it pro rata to what each member put in minus '
             'what they were paid. This is what makes a mutual low- or no-profit.'},
    {'id': 'reserve', 'title': 'Reserve floor',
     'keys': ['reserve', 'floor', 'buffer', 'safety'],
     'text': 'An amount that can never be returned as surplus — a cushion so the '
             'pool is not emptied right before a big claim.'},
    {'id': 'keys', 'title': 'Owner key and member key',
     'keys': ['key', 'keys', 'owner', 'password', 'lost', 'recover', 'secret'],
     'text': 'Creating a pool returns an owner key; joining returns a member key. '
             'Each is shown exactly once and stored only as a hash, so nobody — '
             'not even this server — can recover it. The owner key changes terms '
             'and distributes surplus; the member key proves a claim is yours.'},
    {'id': 'oracle', 'title': 'Oracles (real-world data)',
     'keys': ['oracle', 'oracles', 'data', 'signed', 'hospital', 'bill', 'parametric',
              'automatic', 'advisory', 'required', 'weather'],
     'text': 'An oracle is a trusted source that signs a fact — a hospital bill, a '
             'rainfall reading. A pool can ignore it (none), show it beside votes '
             '(advisory), require it before paying and cap the payout at the verified '
             'amount (required), or pay on it alone with no votes (automatic — '
             'parametric cover).'},
    {'id': 'onchain', 'title': 'On chain vs on this node',
     'keys': ['chain', 'onchain', 'ethereum', 'contract', 'smart', 'blockchain',
              'deploy', 'crypto', 'node', 'local', 'offchain'],
     'text': 'Pools on this node keep a local, append-only ledger — fast and free, '
             'trust the node operator. The same rules exist as an Ethereum contract '
             '(SelfInsure.sol): then the fee cap, the notice period and every payout '
             'are enforced by code nobody can change, and the provider\'s profit is '
             'readable by anyone with no key.',
     'link': '/contract'},
    {'id': 'health', 'title': 'The US health template',
     'keys': ['health', 'medical', 'healthcare', 'doctor', 'hospital', 'aca', 'us',
              'template', 'preset'],
     'text': 'A ready-made health mutual: $400 per 30 days, up to $50,000 per claim, '
             '$250 deductible, $250,000 a year per member, 30-day wait, a $25,000 '
             'reserve, 2 approved adjudicators with two-thirds agreement, and a 0% '
             'operator fee. It is a starting point to show the shape — a real health '
             'pool needs many members and legal advice in your state.',
     'link': '/preset'},
    {'id': 'loss_ratio', 'title': 'Loss ratio',
     'keys': ['loss', 'ratio', 'mlr', 'efficiency'],
     'text': 'The share of premium that came back to members as paid claims. US law '
             'asks health insurers for 80-85%. In a mutual with a 0% fee everything '
             'that is not paid as claims is still the members\' money.'},
    {'id': 'risk', 'title': 'Is this safe? What can go wrong',
     'keys': ['safe', 'risk', 'risks', 'scam', 'trust', 'legal', 'regulated',
              'guarantee', 'wrong', 'danger'],
     'text': 'Coverage is a claim on the pool, not a promise from a big balance '
             'sheet. If many members claim at once, claims queue until premiums '
             'catch up. A small pool with a large cap is fragile. Insurance is '
             'regulated in most places — a pool among friends or a community is '
             'usually fine, selling cover to the public may need a licence. This '
             'tool shows the numbers honestly; it is not legal advice.'},
]

_TOPIC = {t['id']: t for t in TOPICS}

_STOP = set('a an the is are do does did i me my we to of in on for and or it '
            'this that what how can you your be with as at by'.split())
# words that carry meaning even though they are short / common
_KEEP = {'what', 'how', 'my', 'own'}


def _tokens(text):
    return [w for w in re.findall(r"[a-z0-9%$]+", (text or '').lower())]


def topics():
    return [{k: t[k] for k in ('id', 'title', 'text') if k in t} |
            ({'link': t['link']} if t.get('link') else {}) for t in TOPICS]


def topic(tid):
    return _TOPIC.get(tid)


def _score(t, toks):
    keys = set(t['keys'])
    title = set(_tokens(t['title']))
    body = set(_tokens(t['text']))
    s = 0.0
    for w in toks:
        if w in keys:
            s += 1.0 if w in _STOP else 3.0
        if w in title and w not in _STOP:
            s += 2.0
        if w in body and w not in _STOP and len(w) > 3:
            s += 0.3
    return s


def explain(query, n=3):
    """The best explainer for a question, plus related ones. Returns
    {'topic', 'related', 'score'} — topic is None when nothing fits."""
    toks = _tokens(query)
    if not toks:
        return {'topic': None, 'related': [], 'score': 0}
    ranked = sorted(((_score(t, toks), t) for t in TOPICS), key=lambda x: -x[0])
    best_s, best = ranked[0]
    if best_s < 3:
        return {'topic': None, 'related': [t['id'] for s, t in ranked[:n] if s > 0],
                'score': best_s}
    return {'topic': {k: best[k] for k in ('id', 'title', 'text', 'link') if k in best},
            'related': [t['id'] for s, t in ranked[1:n + 1] if s >= 3],
            'score': best_s}


# ── starter pools ─────────────────────────────────────────────────
# Off-chain pools in USD. Each `about` is written as a rule, because that is
# the text adjudicators judge claims against.

TEMPLATES = [
    {'id': 'bike', 'title': 'Bike theft', 'keys': ['bike', 'bicycle', 'courier',
                                                   'cyclist', 'ebike', 'scooter'],
     'terms': {'name': 'Bike theft mutual', 'premium': 8, 'period_days': 30,
               'coverage': 600, 'deductible': 50, 'waiting_days': 14, 'quorum': 2,
               'threshold': 0.66, 'annual_cap': 1200},
     'about': 'Covers: a member\'s bike stolen while locked to a fixed object. '
              'Needs: a photo of the bike, proof of ownership, and a police report '
              'number. Not covered: bikes left unlocked, lost, or damaged.'},
    {'id': 'phone', 'title': 'Phone damage and theft', 'keys': ['phone', 'phones',
                                                                 'screen', 'cracked',
                                                                 'mobile', 'laptop',
                                                                 'device'],
     'terms': {'name': 'Phone mutual', 'premium': 5, 'period_days': 30,
               'coverage': 300, 'deductible': 40, 'waiting_days': 14, 'quorum': 1,
               'threshold': 0.5, 'annual_cap': 600},
     'about': 'Covers: repair or replacement of a member\'s phone after accidental '
              'damage or theft. Needs: photos and a repair quote or police report. '
              'Not covered: loss, wear, or phones bought after the loss.'},
    {'id': 'pet', 'title': 'Pet vet bills', 'keys': ['pet', 'pets', 'dog', 'cat',
                                                     'vet', 'animal'],
     'terms': {'name': 'Pet vet mutual', 'premium': 15, 'period_days': 30,
               'coverage': 2000, 'deductible': 100, 'waiting_days': 30, 'quorum': 2,
               'threshold': 0.66, 'annual_cap': 4000},
     'about': 'Covers: vet bills for accidents and sudden illness of a member\'s '
              'registered pet. Needs: the itemised vet invoice. Not covered: '
              'routine care, vaccines, or conditions known before joining.'},
    {'id': 'income', 'title': 'Sick days for freelancers', 'keys': ['freelance',
                                                                     'freelancer',
                                                                     'income', 'sick',
                                                                     'gig', 'work',
                                                                     'wages'],
     'terms': {'name': 'Freelancer sick-day mutual', 'premium': 20, 'period_days': 30,
               'coverage': 1000, 'deductible': 0, 'waiting_days': 30, 'quorum': 2,
               'threshold': 0.66, 'annual_cap': 3000},
     'about': 'Covers: income lost when a member cannot work for 3+ consecutive '
              'days due to illness or injury, paid at the member\'s stated daily '
              'rate. Needs: a doctor\'s note. Not covered: planned leave.'},
    {'id': 'weather', 'title': 'Rain-out cover', 'keys': ['rain', 'weather', 'market',
                                                          'stall', 'farm', 'crop',
                                                          'event', 'outdoor', 'storm'],
     'terms': {'name': 'Rain-out mutual', 'premium': 10, 'period_days': 30,
               'coverage': 300, 'deductible': 0, 'waiting_days': 7, 'quorum': 1,
               'threshold': 0.5},
     'about': 'Covers: a member\'s lost day of trading when the outdoor market they '
              'trade at is cancelled for weather. Needs: the organiser\'s '
              'cancellation notice. Not covered: choosing not to attend.'},
    {'id': 'health', 'title': 'Community health', 'keys': ['health', 'medical',
                                                           'hospital', 'doctor',
                                                           'healthcare', 'surgery'],
     'terms': {'name': 'Community health mutual', 'premium': 400, 'period_days': 30,
               'coverage': 50000, 'deductible': 250, 'waiting_days': 30, 'quorum': 2,
               'threshold': 0.66, 'annual_cap': 250000, 'reserve_floor': 25000,
               'agent_policy': 'approved'},
     'about': 'Covers: medically necessary care for a member, paid against the '
              'itemised bill. Needs: the bill and the provider\'s details. Not '
              'covered: cosmetic procedures, care before the waiting period ends.'},
    {'id': 'custom', 'title': 'Start from scratch', 'keys': [],
     'terms': {'name': '', 'premium': 10, 'period_days': 30, 'coverage': 500,
               'deductible': 0, 'waiting_days': 14, 'quorum': 1, 'threshold': 0.5},
     'about': ''},
]

_TEMPLATE = {t['id']: t for t in TEMPLATES}

DEFAULTS = {'premium': 10, 'period_days': 30, 'coverage': 500, 'deductible': 0,
            'annual_cap': None, 'waiting_days': 14, 'quorum': 1, 'threshold': 0.5,
            'fee_bps': 0, 'agent_policy': 'open', 'reserve_floor': 0, 'unit': 'USD'}


def templates():
    return [{'id': t['id'], 'title': t['title'],
             'terms': {**DEFAULTS, **t['terms'], 'about': t['about']}}
            for t in TEMPLATES]


def template(tid):
    t = _TEMPLATE.get(tid)
    return {'id': t['id'], 'title': t['title'],
            'terms': {**DEFAULTS, **t['terms'], 'about': t['about']}} if t else None


def _pick_template(text):
    toks = set(_tokens(text))
    best, hits = None, 0
    for t in TEMPLATES:
        n = len(toks & set(t['keys']))
        if n > hits:
            best, hits = t, n
    return best


# ── a pool from a sentence ────────────────────────────────────────

_N = r'\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?'
_PERIOD = {'day': 1, 'daily': 1, 'week': 7, 'weekly': 7, 'wk': 7, 'month': 30,
           'monthly': 30, 'mo': 30, 'quarter': 91, 'year': 365, 'yearly': 365,
           'yr': 365, 'annually': 365, 'annual': 365}
_UNITS = ('USDC', 'USDT', 'DAI', 'ETH', 'SOL', 'EUR', 'GBP', 'USD')


def _num(m, i=1):
    v = float(m.group(i).replace(',', ''))
    if m.lastindex and m.lastindex >= i + 1 and m.group(i + 1):
        v *= 1000
    return int(v) if v == int(v) else v


def _find(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m
    return None


def parse_terms(text):
    """Every term a sentence states explicitly. Nothing is guessed here."""
    t = ' ' + (text or '') + ' '
    out = {}
    m = _find([_N + r'\s*(?:usd|usdc|dollars|eur|euros)?\s*(?:/|a|an|per|each|every)\s*'
                    r'(day|week|wk|month|mo|quarter|year|yr)\b',
               _N + r'\s*(?:usd|usdc|dollars)?\s*(daily|weekly|monthly|yearly|annually)\b',
               r'premiums?\s*(?:of|is|=|:|at)?\s*' + _N], t)
    if m:
        out['premium'] = _num(m)
        per = m.group(3) if m.lastindex and m.lastindex >= 3 else None
        if per:
            out['period_days'] = _PERIOD.get(per.lower(), 30)
    m = _find([r'(?:up\s+to|cover(?:s|age|ing)?\s*(?:of|up\s+to|is|=|:|max)?|'
               r'pays?\s*(?:out)?\s*(?:up\s+to)?|max(?:imum)?\s*(?:payout|claim|cover)?'
               r'\s*(?:of)?|limit\s*(?:of)?|cap(?:ped)?\s*(?:at|of)?)\s*' + _N +
               r'(?!\s*(?:%|days?|votes?|members?|people|/|a\s+(?:month|week|year)|'
               r'per|each|deductible))'], t)
    if m:
        out['coverage'] = _num(m)
    m = _find([r'deductible\s*(?:of|is|=|:)?\s*' + _N, _N + r'\s*(?:usd\s*)?deductible',
               r'excess\s*(?:of)?\s*' + _N], t)
    if m:
        out['deductible'] = _num(m)
    elif re.search(r'\bno\s+deductible\b', t, re.I):
        out['deductible'] = 0
    m = _find([r'(?:annual|yearly)\s*(?:cap|limit|max(?:imum)?)\s*(?:of|is)?\s*' + _N,
               _N + r'\s*(?:a|per)\s*year\s*(?:cap|limit|max)'], t)
    if m:
        out['annual_cap'] = _num(m)
    if re.search(r'\b(?:no|zero|0%?)\s+(?:operator\s+)?fee', t, re.I):
        out['fee_bps'] = 0
    else:
        m = _find([r'(\d+(?:\.\d+)?)\s*%\s*(?:operator\s+)?(?:fee|cut|commission)',
                   r'(?:fee|cut|commission)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*%'], t)
        if m:
            out['fee_bps'] = int(round(float(m.group(1)) * 100))
    m = _find([r'quorum\s*(?:of|is|=|:)?\s*(\d+)',
               r'(\d+)\s*(?:votes?|adjudicators?|judges?|reviewers?|approvers?)\b'], t)
    if m:
        out['quorum'] = int(m.group(1))
    if re.search(r'\bunanimous', t, re.I):
        out['threshold'] = 1.0
    elif re.search(r'\btwo[- ]thirds\b', t, re.I):
        out['threshold'] = 0.66
    else:
        m = re.search(r'(\d+)\s*%\s*(?:must\s+)?(?:approve|accept|agree|majority|yes)', t, re.I)
        if m:
            out['threshold'] = max(0.01, min(1.0, int(m.group(1)) / 100))
        elif re.search(r'\bsimple majority\b|\bmajority\b', t, re.I):
            out['threshold'] = 0.5
    m = _find([r'(\d+)[- ]?days?\s*(?:waiting|wait)', r'wait(?:ing)?\s*(?:period)?\s*'
               r'(?:of|is|=|:)?\s*(\d+)\s*days?'], t)
    if m:
        out['waiting_days'] = int(m.group(1))
    elif re.search(r'\bno\s+wait', t, re.I):
        out['waiting_days'] = 0
    m = _find([r'reserve\s*(?:floor)?\s*(?:of|is|=|:)?\s*' + _N], t)
    if m:
        out['reserve_floor'] = _num(m)
    if re.search(r'\b(?:approved|trusted|only (?:people|judges|agents) I (?:approve|pick|choose)'
                 r'|hand[- ]picked|invite[- ]only)\b', t, re.I):
        out['agent_policy'] = 'approved'
    elif re.search(r'\b(?:anyone|any agent)\s+(?:can|may)\s+(?:judge|adjudicate|vote)\b', t, re.I):
        out['agent_policy'] = 'open'
    for u in _UNITS:
        if re.search(r'\b' + u + r'\b', t, re.I):
            out['unit'] = u
            break
    m = re.search(r'(\d+)\s+(?!days?\b|votes?\b|adjudicators?\b|judges?\b|%)'
                  r'(?:[a-z]+\s+)?(members?|people|friends|neighbou?rs|couriers|riders|families|'
                  r'households|workers|colleagues|students|owners|traders|freelancers|'
                  r'[a-z]+ers)\b', t, re.I)
    if m:
        out['members'] = int(m.group(1))
    m = re.search(r'["“]([^"”]{3,60})["”]', text or '')
    if m:
        out['name'] = m.group(1).strip()
    return out


def _subject(text):
    """'a pool for couriers covering bike theft, $8 a month' → 'bike theft'."""
    m = re.search(r'\b(?:covering|covers|cover for|against|insur(?:e|ing|ance) for|'
                  r'protect(?:ion)? (?:for|against)|pool for|mutual for|fund for)\s+'
                  r'(?:our |my |their )?([a-z][a-z \-\'’]{2,40}?)(?=\s*(?:,|\.|;|$|'
                  r'\bwith\b|\bat\b|\bfor\b|\bup to\b|\bthat\b|\bwhere\b|\$|\d))',
                  text or '', re.I)
    return m.group(1).strip() if m else ''


def draft(text, members=None):
    """Plain English → create_pool arguments, what was assumed, and a check
    of whether those numbers can pay. Never creates anything."""
    said = parse_terms(text)
    tpl = _pick_template(text)
    base = dict(DEFAULTS)
    if tpl:
        base.update(tpl['terms'])
        base['about'] = tpl['about']
    terms = {**base, **{k: v for k, v in said.items() if k != 'members'}}
    subject = _subject(text)
    if not said.get('name'):
        if subject:
            terms['name'] = subject[:1].upper() + subject[1:] + ' mutual'
        elif not terms.get('name'):
            terms['name'] = 'My mutual'
    if not terms.get('about'):
        terms['about'] = (f'Covers: {subject or "the loss this pool exists for"}. '
                          'Needs: evidence of the loss. Not covered: anything that '
                          'happened before joining. (Edit this — adjudicators judge '
                          'claims against exactly this text.)')
    terms['fee_bps'] = max(0, min(FEE_CAP_BPS, int(terms.get('fee_bps') or 0)))
    n = int(members or said.get('members') or 0)
    assumed = sorted(k for k in ('premium', 'period_days', 'coverage', 'deductible',
                                 'waiting_days', 'quorum', 'threshold', 'fee_bps')
                     if k not in said)
    args = {k: terms[k] for k in ('name', 'about', 'premium', 'period_days', 'coverage',
                                  'deductible', 'annual_cap', 'waiting_days', 'quorum',
                                  'threshold', 'fee_bps', 'agent_policy', 'reserve_floor',
                                  'unit') if terms.get(k) is not None}
    return {'create_args': args, 'template': tpl['id'] if tpl else None,
            'stated': sorted(k for k in said if k != 'members'),
            'assumed': assumed,
            'check': check(args, n or None),
            'next': 'Review the terms, then create it with si_create_pool (or the '
                    'Create page). The owner key is shown once — save it.'}


# ── could it pay? ─────────────────────────────────────────────────

def check(terms, members=None):
    """Plain-words viability of a set of terms for a pool of `members`."""
    assumed_members = not members
    n = int(members or 10)
    prem = float(terms.get('premium') or 0)
    period = float(terms.get('period_days') or 30) or 30
    cov = float(terms.get('coverage') or 0)
    ded = float(terms.get('deductible') or 0)
    fee = int(terms.get('fee_bps') or 0)
    unit = terms.get('unit') or 'USD'
    per_year = prem * 365.0 / period
    into_pool = n * per_year * (1 - fee / 10000)
    full_claims = (into_pool / cov) if cov else None
    notes, level = [], 'ok'

    def warn(msg, lvl='warn'):
        nonlocal level
        notes.append(msg)
        if lvl == 'bad' or level == 'ok':
            level = lvl

    if prem <= 0:
        warn('There is no premium, so the pool only has what people donate.')
    if not cov:
        warn('Coverage is uncapped: one large claim can empty the whole pool. '
             'Set a per-claim maximum.', 'bad')
    elif full_claims is not None:
        if full_claims < 1:
            warn(f'{n} members together pay in about {into_pool:,.0f} {unit} a year — '
                 f'less than ONE full claim of {cov:,.0f}. Raise the premium, find '
                 'more members, or lower the coverage.', 'bad')
        elif full_claims / n < 0.05:
            warn(f'The pool can pay about {full_claims:.1f} full claims a year — '
                 f'fine if fewer than 1 in {max(1, round(n / full_claims))} members '
                 'has a loss each year.')
    if cov and ded >= cov:
        warn('The deductible is as large as the coverage, so no claim can pay out.', 'bad')
    if not terms.get('waiting_days'):
        warn('No waiting period: someone could join the day after a loss and claim.')
    if int(terms.get('quorum') or 1) == 1:
        notes.append('One vote settles a claim. Fine for a small trusted group; '
                     'use 2+ votes once strangers can join.')
    if fee:
        notes.append(f'You keep {fee / 100:g}% of every premium — about '
                     f'{n * per_year * fee / 10000:,.0f} {unit} a year at {n} members. '
                     'Members can see this number.')
    else:
        notes.append('0% operator fee: every unit of premium stays the members\' money.')
    headline = (f'{n}{" (assumed)" if assumed_members else ""} members x '
                f'{prem:,.2f} {unit} every {period:g} days = about {into_pool:,.0f} '
                f'{unit} a year into the pool'
                + (f', enough for {full_claims:.1f} full claims of {cov:,.0f}.'
                   if full_claims is not None else '.'))
    return {'level': level, 'members': n, 'members_assumed': assumed_members,
            'per_member_per_year': round(per_year, 2),
            'into_pool_per_year': round(into_pool, 2),
            'full_claims_per_year': round(full_claims, 2) if full_claims is not None else None,
            'claim_example': ({'loss': cov, 'pays': max(0.0, min(cov, cov - ded))}
                              if cov else None),
            'headline': headline, 'notes': notes}
