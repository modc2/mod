"""
govmod — modular governance for multi-option, staked disputes.

Players stake on the *options* of a question (two or many). A *token* is then
used to vote over those options and pick a winner; the winning option's backers
split the whole pot pro-rata to their stake. Everything about *how* the verdict
is reached is modular and agreed by EVERY player before the case goes live:

  - options      the choices being voted on (>= 2; "cats/dogs", "yes/no/abstain", …)
  - token        which token's holders vote (default: bloctime; any chain token)
  - time_limit   how long voting runs, measured in BLOCKS (BlocTime is the clock)
  - threshold    quorum — minimum participating weight for a verdict to count
  - min_stake    the agreed buy-in every backer must meet
  - verdict_mode HOW the winner is chosen:
                   "token"    -> token-weighted vote, option with most weight wins
                   "multisig" -> an M-of-N signer set picks the option

Mutual agreement is enforced by a *terms hash*: the opener sets the terms; each
player can only `join` by agreeing to that exact hash (optionally proven with a
signature). Until the opener `activate`s the case, players may join/leave and the
opener may amend or cancel.

The clock is BlocTime by default: `time_limit` is a number of chain blocks and a
case's `deadline` is an absolute block height (activation block + time_limit).
Like core/webchain this module keeps a local index (~/.mod/govmod/cases.json)
mirroring the rules so the flow is usable before/independently of an on-chain
deploy. Settlement (paying the pot to the winners) is an explicit on-chain hook.

CLI:
    m govmod/open question="best pet?" options='["cats","dogs","birds"]' \\
        stake=1000 option=cats token=bloctime threshold=5000 time_limit=200000
    m govmod/join 0 option=dogs stake=1000 key=bob     # another player backs an option
    m govmod/activate 0                                 # opener locks entries, voting opens
    m govmod/vote 0 option=cats key=carol              # token-weighted vote (mode=token)
    m govmod/sign_verdict 0 option=cats key=arbiter    # signer picks (mode=multisig)
    m govmod/tally 0                                    # current standing
    m govmod/resolve 0                                  # finalize; winners split the pot
"""
import json
import mod as m

MODES = ('token', 'multisig')
PRIVACY = ('public', 'sealed')
ZERO = '0x' + '0' * 40


class Mod:
    description = ('modular governance: players stake multiple options; a token vote or '
                  'an M-of-N multisig picks the winning option whose backers split the pot, '
                  'with BlocTime blocks as the clock')

    def __init__(self, key='govmod', network='testnet', index_path=None):
        self.key = m.key(key)
        self.network = network
        self.index_path = m.abspath(index_path or '~/.mod/govmod/cases.json')
        self._chain = None

    # --- lazy chain dep (clock + token power) -------------------------------

    @property
    def chain(self):
        if self._chain is None:
            self._chain = m.mod('chain')(network=self.network, key='govmod')
        return self._chain

    def block(self, now=None) -> int:
        """Current block height — the BlocTime clock. `now` overrides it (tests /
        deterministic resolution). Falls back to 0 if the chain is unreachable."""
        if now is not None:
            return int(now)
        try:
            return int(self.chain.w3.eth.block_number)
        except Exception:
            return 0

    def power(self, address, token='bloctime') -> int:
        """Voting weight of `address` in `token`. Default token is BlocTime
        (time-weighted stake). Any other name routes to the chain token balance.
        Best-effort: 0 if the chain is unavailable."""
        addr = self._addr(address)
        try:
            if token in ('bloctime', 'bloc', ''):
                return int(self.chain.bloctime_balance(addr))
            return int(self.chain.balance(addr, token=token))
        except Exception:
            return 0

    # --- storage ------------------------------------------------------------

    def _load(self) -> dict:
        return m.get(self.index_path, {})

    def _save(self, idx: dict):
        m.put(self.index_path, idx)

    def _addr(self, key=None) -> str:
        """Resolve a caller to a lowercase address. Accepts a 0x address or a
        local key name; defaults to this module's key. An API layer would instead
        recover the signer from a protocol-auth token (see core/store)."""
        if key is None:
            return self.key.address.lower()
        if isinstance(key, str) and key.startswith('0x') and len(key) == 42:
            return key.lower()
        return m.key(key).address.lower()

    # --- terms hash (the thing every player must agree on) ------------------

    @staticmethod
    def terms_of(case: dict) -> dict:
        """The canonical, agreed-upon subset of a case — the *rules*, not who
        staked what. Every player signs off on exactly this; change any field and
        the terms hash changes."""
        return {
            'question': case['question'],
            'options': case['options'],
            'token': case['token'],
            'threshold': case['threshold'],
            'time_limit': case['time_limit'],
            'min_stake': case['min_stake'],
            'verdict_mode': case['verdict_mode'],
            'signers': case['signers'],
            'required_sigs': case['required_sigs'],
            'privacy': case['privacy'],
        }

    def terms_hash(self, case: dict) -> str:
        return m.hash(json.dumps(self.terms_of(case), sort_keys=True))

    def _get(self, idx: dict, case_id) -> dict:
        cid = str(case_id)
        if cid not in idx:
            raise KeyError(f'no case {cid}')
        return idx[cid]

    def _option(self, case, option) -> str:
        """Resolve a label/index to one of the case's options."""
        opts = case['options']
        s = str(option)
        if s in opts:
            return s
        low = {o.lower(): o for o in opts}
        if s.lower() in low:
            return low[s.lower()]
        if s.isdigit() and int(s) < len(opts):
            return opts[int(s)]
        raise ValueError(f'option must be one of: {opts}')

    # --- open / join / activate (mutual agreement) --------------------------

    def open(self, question, options, stake=None, option=None, token='bloctime',
             threshold=0, time_limit=200000, min_stake=0, verdict_mode='token',
             signers=None, required_sigs=None, privacy='public', key=None) -> dict:
        """Open a case over >= 2 options and propose the full terms.

        The opener may immediately back one option by passing `stake` (+`option`).
        The case sits in `open` status — collecting players — until the opener
        `activate`s it.
          options      list of >= 2 choice labels to be voted on
          token        voting token (default bloctime); time_limit is in BLOCKS
          threshold    quorum: min participating weight for a valid verdict
          min_stake    agreed buy-in every backer must meet
          verdict_mode 'token' (weighted vote) or 'multisig' (M-of-N signers)
          signers      multisig signer addresses (required for verdict_mode=multisig)
          required_sigs M in M-of-N (default: majority of signers)
          privacy      'public' (open ballots) or 'sealed' (commit-reveal: votes
                       hidden until the case resolves; only aggregate stats shown).
                       Sealed voting applies to verdict_mode=token.
        """
        assert verdict_mode in MODES, f'verdict_mode must be one of {MODES}'
        assert privacy in PRIVACY, f'privacy must be one of {PRIVACY}'
        if privacy == 'sealed':
            assert verdict_mode == 'token', 'sealed (private) voting applies to verdict_mode=token'
        if isinstance(options, str):
            options = json.loads(options) if options.strip().startswith('[') else \
                [o.strip() for o in options.split(',')]
        options = [str(o) for o in options]
        assert len(options) >= 2, 'need at least 2 options'
        assert len(set(options)) == len(options), 'options must be unique'

        signers = [s.lower() for s in (signers or [])]
        if verdict_mode == 'multisig':
            assert signers, 'verdict_mode=multisig requires signers='
            required_sigs = int(required_sigs) if required_sigs is not None else (len(signers) // 2 + 1)
            assert 1 <= required_sigs <= len(signers), 'required_sigs out of range'
        else:
            required_sigs = int(required_sigs or 0)

        opener = self._addr(key)
        idx = self._load()
        cid = str(len(idx) and max(int(k) for k in idx) + 1 or 0)
        case = {
            'id': cid, 'status': 'open',
            'question': str(question), 'options': options,
            'opener': opener,
            'token': token, 'threshold': int(threshold), 'time_limit': int(time_limit),
            'min_stake': int(min_stake),
            'verdict_mode': verdict_mode, 'signers': signers, 'required_sigs': required_sigs,
            'privacy': privacy,
            'stakes': {},  # addr -> {option, amount}
            'opened_block': self.block(), 'activated_block': None, 'deadline': None,
            'votes': {},    # public mode:  voter -> {option, weight}
            'ballots': {},  # sealed mode:  nullifier -> {commitment, weight, revealed}
            'sigs': {}, 'winning_option': None, 'winners': None,
        }
        case['terms_hash'] = self.terms_hash(case)
        idx[cid] = case
        self._save(idx)
        if stake is not None:
            assert option is not None, 'pass option= alongside stake= to back an option'
            self.join(cid, option=option, stake=stake, key=key)
            case = self._get(self._load(), cid)
        return case

    def join(self, case_id, option, stake, key=None, accept_hash=None, sig=None) -> dict:
        """Agree to the open terms and stake an option.

        Mutual agreement is enforced: if `accept_hash` is given it must equal the
        case's terms_hash, and if `sig` is given it must be a valid signature by
        the joining player over that hash. Re-joining before activation updates
        your option/stake. Stake must meet the agreed min_stake.
        """
        addr = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'open', f"case {case['id']} is {case['status']}, not joinable"
        opt = self._option(case, option)
        amount = int(stake)
        assert amount > 0 and amount >= case['min_stake'], \
            f"stake must be >= min_stake ({case['min_stake']})"

        th = case['terms_hash']
        if accept_hash is not None and accept_hash != th:
            raise ValueError('terms hash mismatch — the terms changed since you read them')
        if sig is not None and not m.verify(th, sig, addr):
            raise PermissionError('agreement signature does not match the joining player')

        case['stakes'][addr] = {'option': opt, 'amount': amount}
        self._save(idx)
        return case

    def leave(self, case_id, key=None) -> dict:
        """A player withdraws their stake before activation (refundable)."""
        addr = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'open', 'can only leave before activation'
        if addr not in case['stakes']:
            raise KeyError(f'{addr} has no stake in case {case["id"]}')
        refund = case['stakes'].pop(addr)['amount']
        self._save(idx)
        return {'id': case['id'], 'left': addr, 'refund': {addr: refund}}

    def activate(self, case_id, key=None, now=None) -> dict:
        """Opener locks entries and opens voting → status `active`, with the
        deadline set to current_block + time_limit. Requires a real contest:
        >= 2 players backing >= 2 distinct options."""
        addr = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'open', f"case {case['id']} is {case['status']}"
        assert addr == case['opener'], 'only the opener can activate'
        backed = {s['option'] for s in case['stakes'].values()}
        assert len(case['stakes']) >= 2, 'need >= 2 players to activate'
        assert len(backed) >= 2, 'need >= 2 distinct options backed to activate'
        case['status'] = 'active'
        case['activated_block'] = self.block(now)
        case['deadline'] = case['activated_block'] + case['time_limit']
        self._save(idx)
        return case

    def amend(self, case_id, key=None, **terms) -> dict:
        """Opener revises terms while still `open` (pre-activation). Recomputes
        the terms hash, invalidating any prior agreements. Only agreed-terms
        fields may change."""
        addr = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'open', 'can only amend before activation'
        assert addr == case['opener'], 'only the opener can amend'
        allowed = set(self.terms_of(case))
        for fld, val in terms.items():
            if fld not in allowed:
                raise KeyError(f'cannot amend field: {fld}')
            if fld in ('threshold', 'time_limit', 'min_stake', 'required_sigs'):
                val = int(val)
            if fld == 'signers':
                val = [s.lower() for s in val]
            if fld == 'options':
                val = [str(o) for o in val]
                assert len(val) >= 2 and len(set(val)) == len(val), 'options invalid'
            case[fld] = val
        case['terms_hash'] = self.terms_hash(case)
        self._save(idx)
        return case

    def cancel(self, case_id, key=None) -> dict:
        """Opener cancels an un-activated case; all stakes are refundable."""
        addr = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'open', 'can only cancel before activation'
        assert addr == case['opener'], 'only the opener can cancel'
        case['status'] = 'cancelled'
        self._save(idx)
        refund = {a: s['amount'] for a, s in case['stakes'].items()}
        return {'id': case['id'], 'status': 'cancelled', 'refund': refund}

    # --- verdict: token mode ------------------------------------------------

    def vote(self, case_id, option, key=None, weight=None) -> dict:
        """Cast / update a token-weighted vote for an option (verdict_mode=token).

        One vote per address (re-voting overwrites). Weight defaults to the
        voter's `token` power at vote time; pass `weight` to override (testing /
        snapshot semantics).
        """
        voter = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['verdict_mode'] == 'token', 'this case is resolved by multisig, not token vote'
        assert case['privacy'] == 'public', 'this case is sealed; use commit()/reveal()'
        assert case['status'] == 'active', f"case {case['id']} is {case['status']}, voting not open"
        if self.block() > case['deadline']:
            raise ValueError(f"voting closed at block {case['deadline']}")
        opt = self._option(case, option)
        w = int(weight) if weight is not None else self.power(voter, case['token'])
        assert w > 0, f'{voter} has zero {case["token"]} weight'
        case['votes'][voter] = {'option': opt, 'weight': w}
        self._save(idx)
        return {'id': case['id'], 'voter': voter, 'option': opt, 'weight': w, 'tally': self._tally(case)}

    # --- verdict: token mode, SEALED (commit-reveal, votes hidden) -----------
    #
    # Privacy model: a voter commits an opaque hash of (option, salt) computed
    # CLIENT-SIDE — the coordinator never sees the option. Ballots are stored
    # under a *nullifier* (keccak(voter, case)) instead of the address, so the
    # published index reveals neither who voted nor how. While voting is open
    # only AGGREGATE stats (ballot count, committed weight) are exposed. After
    # the deadline voters reveal their openings; the per-option tally is computed
    # and revealed only at `resolve` (settlement). Eligibility/weight is checked
    # via `verify_eligibility` — a pluggable zk hook (Semaphore membership / SNARK
    # proof of token holding); the default trusts the coordinator's on-chain
    # power lookup. The tally is additively homomorphic, so a production build can
    # swap commitments for Pedersen weight commitments and open only the SUM.

    def commitment(self, option, salt) -> str:
        """Commit hash a voter computes locally: keccak256(option : salt). Keep
        `salt` high-entropy (see gen_salt) so the commitment can't be guessed."""
        return '0x' + self._keccak(f'{option}:{salt}'.encode()).hex()

    seal = commitment

    @staticmethod
    def gen_salt() -> str:
        import secrets
        return '0x' + secrets.token_hex(32)

    def _nullifier(self, addr, case_id) -> str:
        """Anonymizing, one-per-voter tag. Stronger anonymity (unlinkable to the
        address) is the job of the zk eligibility proof; this binds one ballot per
        eligible address without storing the address."""
        return '0x' + self._keccak(bytes.fromhex(self._addr(addr)[2:]) + str(case_id).encode()).hex()

    def verify_eligibility(self, case, proof, addr) -> bool:
        """Hook: prove the committer is an eligible voter without revealing them.
        STUB — returns True and lets the caller's on-chain power stand in. Wire to
        Semaphore (Merkle membership + nullifier) or a SNARK that proves
        `power(addr) >= weight` for trustless, coordinator-blind eligibility."""
        return True

    def commit(self, case_id, commitment, weight=None, key=None, proof=None) -> dict:
        """Cast a SEALED ballot (verdict_mode=token, privacy=sealed).

        `commitment` is keccak(option:salt) computed off the wire — the option is
        never sent. One ballot per voter (re-commit before the deadline replaces).
        Returns only aggregate stats; never echoes the option.
        """
        voter = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['privacy'] == 'sealed', 'this case is public; use vote()'
        assert case['status'] == 'active', f"case {case['id']} is {case['status']}, voting not open"
        if self.block() > case['deadline']:
            raise ValueError(f"commit phase closed at block {case['deadline']}; reveal now")
        if not self.verify_eligibility(case, proof, voter):
            raise PermissionError('eligibility proof failed')
        w = int(weight) if weight is not None else self.power(voter, case['token'])
        assert w > 0, f'{voter} has zero {case["token"]} weight'
        nf = self._nullifier(voter, case['id'])
        c = commitment if str(commitment).startswith('0x') else '0x' + str(commitment)
        case['ballots'][nf] = {'commitment': c.lower(), 'weight': w, 'revealed': None}
        self._save(idx)
        return {'id': case['id'], 'nullifier': nf, 'sealed': True,
                'ballots': len(case['ballots']),
                'committed_weight': sum(b['weight'] for b in case['ballots'].values())}

    def reveal(self, case_id, option, salt, key=None) -> dict:
        """Open a sealed ballot after the deadline: prove (option, salt) matches
        your commitment. The opening is verified and recorded but the resulting
        per-option tally stays hidden until `resolve`. Unrevealed ballots do not
        count (standard commit-reveal liveness)."""
        voter = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['privacy'] == 'sealed', 'this case is public; nothing to reveal'
        assert case['status'] == 'active', f"case {case['id']} is {case['status']}"
        if self.block() <= case['deadline']:
            raise ValueError(f"reveal opens after the deadline (block {case['deadline']})")
        nf = self._nullifier(voter, case['id'])
        b = case['ballots'].get(nf)
        if not b:
            raise KeyError('no sealed ballot found for you in this case')
        opt = self._option(case, option)
        if self.commitment(opt, salt).lower() != b['commitment']:
            raise ValueError('opening does not match your commitment')
        b['revealed'] = {'option': opt}
        self._save(idx)
        return {'id': case['id'], 'revealed': True,
                'revealed_ballots': sum(1 for x in case['ballots'].values() if x['revealed'])}

    # --- verdict: multisig mode ---------------------------------------------

    def sign_verdict(self, case_id, option, key=None, sig=None) -> dict:
        """An authorized signer endorses an option (verdict_mode=multisig).

        One endorsement per signer (re-signing overwrites). If `sig` is given it
        must be a valid signature by the signer over "<terms_hash>:<option>".
        """
        signer = self._addr(key)
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['verdict_mode'] == 'multisig', 'this case is resolved by token vote, not multisig'
        assert case['status'] == 'active', f"case {case['id']} is {case['status']}, not open for signing"
        assert signer in case['signers'], f'{signer} is not an authorized signer'
        opt = self._option(case, option)
        if sig is not None and not m.verify(f"{case['terms_hash']}:{opt}", sig, signer):
            raise PermissionError('verdict signature does not match the signer')
        case['sigs'][signer] = opt
        self._save(idx)
        return {'id': case['id'], 'signer': signer, 'option': opt, 'counts': self._sig_counts(case),
                'required': case['required_sigs']}

    # --- tally / resolve ----------------------------------------------------

    def _tally(self, case) -> dict:
        """Per-option weight. For sealed cases this only sees REVEALED ballots —
        and resolve() is the first place it is ever exposed."""
        t = {o: 0 for o in case['options']}
        if case.get('privacy') == 'sealed':
            for b in case['ballots'].values():
                if b['revealed']:
                    t[b['revealed']['option']] += int(b['weight'])
        else:
            for v in case['votes'].values():
                t[v['option']] = t.get(v['option'], 0) + int(v['weight'])
        return t

    def _sealed_done(self, case) -> bool:
        return case['status'] in ('resolved', 'no_verdict', 'cancelled')

    def _view(self, case) -> dict:
        """What outsiders may see. For an unresolved sealed case, ballots are
        collapsed to aggregates so neither voters nor their choices leak."""
        if case.get('privacy') != 'sealed' or self._sealed_done(case):
            return case
        b = case['ballots']
        c = dict(case)
        c['ballots'] = {
            'sealed': True, 'count': len(b),
            'committed_weight': sum(x['weight'] for x in b.values()),
            'revealed': sum(1 for x in b.values() if x['revealed']),
        }
        return c

    def _sig_counts(self, case) -> dict:
        c = {o: 0 for o in case['options']}
        for s, opt in case['sigs'].items():
            if s in case['signers']:
                c[opt] = c.get(opt, 0) + 1
        return c

    def _pot(self, case) -> int:
        return sum(s['amount'] for s in case['stakes'].values())

    @staticmethod
    def _leaders(scores: dict):
        """(top_option, top_score, is_tie) over an option->score map."""
        top = max(scores.values()) if scores else 0
        leaders = [o for o, v in scores.items() if v == top]
        return (leaders[0], top, len(leaders) > 1)

    def _payout_winning(self, case, winning_option) -> dict:
        """Split the whole pot pro-rata among the winning option's backers."""
        pot = self._pot(case)
        backers = {a: s['amount'] for a, s in case['stakes'].items()
                   if s['option'] == winning_option}
        total = sum(backers.values())
        if total == 0:
            return {}
        payout = {a: pot * amt // total for a, amt in backers.items()}
        remainder = pot - sum(payout.values())
        if remainder:  # give rounding dust to the largest backer (deterministic)
            top = max(backers, key=lambda a: (backers[a], a))
            payout[top] += remainder
        return payout

    def tally(self, case_id, now=None) -> dict:
        """Current standing without finalizing. For a sealed case that has not
        resolved, this returns ONLY aggregate statistics — never the per-option
        breakdown or who voted."""
        case = self._get(self._load(), case_id)
        block = self.block(now)
        out = {'id': case['id'], 'status': case['status'], 'verdict_mode': case['verdict_mode'],
               'options': case['options'], 'deadline': case['deadline'], 'block': block,
               'threshold': case['threshold'], 'pot': self._pot(case)}
        if case['privacy'] == 'sealed' and not self._sealed_done(case):
            b = case['ballots']
            revealed_w = sum(x['weight'] for x in b.values() if x['revealed'])
            out.update(
                privacy='sealed',
                phase=('commit' if block <= (case['deadline'] or 0) else 'reveal')
                if case['status'] == 'active' else case['status'],
                ballots=len(b),
                committed_weight=sum(x['weight'] for x in b.values()),
                revealed_ballots=sum(1 for x in b.values() if x['revealed']),
                revealed_weight=revealed_w,
                quorum_met=revealed_w >= case['threshold'],
                note='per-option tally hidden until the case resolves')
            return out
        if case['verdict_mode'] == 'token':
            t = self._tally(case)
            lead, top, tie = self._leaders(t)
            out.update(tally=t, total_weight=sum(t.values()),
                       quorum_met=sum(t.values()) >= case['threshold'],
                       leader=None if tie else lead, tie=tie)
        else:
            c = self._sig_counts(case)
            lead, top, tie = self._leaders(c)
            out.update(sig_counts=c, required_sigs=case['required_sigs'],
                       leader=lead if top >= case['required_sigs'] and not tie else None)
        return out

    def resolve(self, case_id, now=None) -> dict:
        """Finalize the winning option and split the pot.

        token mode    requires the deadline to have passed; the option with the
                      most weight wins iff total weight >= threshold and there is
                      no tie for first; else no_verdict.
        multisig mode the first option to reach required_sigs wins as soon as it
                      does; after the deadline with no option at quorum → no_verdict.
        A no_verdict refunds every backer their own stake; otherwise the winning
        option's backers split the whole pot pro-rata.
        """
        idx = self._load()
        case = self._get(idx, case_id)
        assert case['status'] == 'active', f"case {case['id']} is {case['status']}, cannot resolve"
        block = self.block(now)
        winning = None
        reason = ''

        if case['verdict_mode'] == 'token':
            if block <= case['deadline']:
                raise ValueError(f"voting open until block {case['deadline']} (now {block})")
            t = self._tally(case)
            total = sum(t.values())
            lead, top, tie = self._leaders(t)
            if total < case['threshold']:
                reason = f'quorum not met ({total} < {case["threshold"]})'
            elif tie:
                reason = f'tie for first at {top}'
            else:
                winning, reason = lead, f'{lead} won with {top} of {total}'
            case['result_tally'] = t
        else:  # multisig
            c = self._sig_counts(case)
            need = case['required_sigs']
            lead, top, tie = self._leaders(c)
            if top >= need and not tie:
                winning, reason = lead, f'{lead} reached {top}/{need} signatures'
            elif top >= need and tie:
                reason = f'multiple options tied at {top} signatures'
            elif block > case['deadline']:
                reason = f'no option reached {need} signatures by block {case["deadline"]}'
            else:
                raise ValueError(f'need {need} signatures on one option, or wait for block {case["deadline"]}')
            case['result_counts'] = c

        if winning:
            payout = self._payout_winning(case, winning)
            if not payout:  # winning option had no backers — nothing to award
                winning, reason = None, reason + ' (winning option had no backers)'
        if winning:
            winners = sorted(a for a, s in case['stakes'].items() if s['option'] == winning)
            case.update(status='resolved', winning_option=winning, winners=winners,
                        resolved_block=block, reason=reason)
        else:
            payout = {a: s['amount'] for a, s in case['stakes'].items()}
            case.update(status='no_verdict', winning_option=None, winners=None,
                        resolved_block=block, reason=reason)

        # on-chain settlement hook: move the pot to the winners (see chain.transfer)
        case['settlement'] = self._settle(case, payout)
        self._save(idx)
        return {'id': case['id'], 'status': case['status'], 'winning_option': winning,
                'winners': case['winners'], 'reason': reason, 'pot': self._pot(case), 'payout': payout}

    def _settle(self, case, payout) -> dict:
        """Hook: pay out the pot on chain — gas-minimal by construction.

        Participation (agreement + votes) is entirely off-chain, so the ONLY gas
        is moving money. We compute the payout off-chain and hand the contract a
        single 32-byte Merkle root (~1 SSTORE + 1 event); each winner pulls their
        share with a proof (O(1), gas paid by the claimer — no unbounded payout
        loop, no OOG). For a single recipient we skip the tree and recommend one
        direct transfer (cheaper than any claim). Left off (onchain=False) so the
        local index stays usable standalone."""
        return {'onchain': False, 'token': case['token'], **self.settle_plan(case, payout)}

    # --- gas-minimal settlement (Merkle claim distribution) -----------------

    @staticmethod
    def _keccak(b: bytes) -> bytes:
        from eth_utils import keccak
        return keccak(b)

    def _leaf(self, addr: str, amount: int) -> bytes:
        """keccak256(abi.encodePacked(address(20), uint256(32))) — OZ-compatible."""
        return self._keccak(bytes.fromhex(addr[2:]) + int(amount).to_bytes(32, 'big'))

    def _hash_pair(self, a: bytes, b: bytes) -> bytes:
        lo, hi = (a, b) if a <= b else (b, a)   # sorted pairs (OpenZeppelin MerkleProof)
        return self._keccak(lo + hi)

    def _merkle_layers(self, leaves):
        layers = [list(leaves)]
        while len(layers[-1]) > 1:
            cur, nxt = layers[-1], []
            for i in range(0, len(cur), 2):
                b = cur[i + 1] if i + 1 < len(cur) else cur[i]
                nxt.append(self._hash_pair(cur[i], b))
            layers.append(nxt)
        return layers

    def _payout_leaves(self, payout: dict):
        # deterministic order so root/proofs are reproducible
        items = sorted(payout.items())
        return items, [self._leaf(a, amt) for a, amt in items]

    def merkle_root(self, payout: dict) -> str:
        """32-byte distribution root for a {addr: amount} payout. This is the only
        word the settlement contract must store."""
        if not payout:
            return '0x' + '00' * 32
        _, leaves = self._payout_leaves(payout)
        return '0x' + self._merkle_layers(leaves)[-1][0].hex()

    def merkle_proof(self, payout: dict, address: str) -> dict:
        """Proof a recipient submits to `claim(addr, amount, proof)`."""
        addr = self._addr(address)
        items, leaves = self._payout_leaves(payout)
        idx = next((i for i, (a, _) in enumerate(items) if a == addr), None)
        if idx is None:
            raise KeyError(f'{addr} is not in the payout')
        layers, proof = self._merkle_layers(leaves), []
        for layer in layers[:-1]:
            sib = idx ^ 1
            proof.append('0x' + (layer[sib] if sib < len(layer) else layer[idx]).hex())
            idx //= 2
        return {'address': addr, 'amount': items[next(i for i, (a, _) in enumerate(items) if a == addr)][1],
                'proof': proof, 'root': self.merkle_root(payout)}

    def settle_plan(self, case, payout=None) -> dict:
        """Cheapest on-chain settlement for a resolved/no-verdict case.

        <=1 recipient -> a single `transfer` (no tree). Otherwise a Merkle-claim
        distribution: store one root, winners pull with a proof. Net-of-stake
        flows are reported so a trustful settler can move only the deltas."""
        if isinstance(case, (str, int)):
            case = self._get(self._load(), case)
        if payout is None:
            payout = (self._payout_winning(case, case['winning_option'])
                      if case.get('winning_option')
                      else {a: s['amount'] for a, s in case['stakes'].items()})
        # net delta vs each address's own stake (what actually has to move)
        staked = {a: s['amount'] for a, s in case['stakes'].items()}
        net = {a: payout.get(a, 0) - staked.get(a, 0)
               for a in set(payout) | set(staked)}
        net = {a: d for a, d in net.items() if d}
        if len(payout) <= 1:
            method = 'transfer'
        else:
            method = 'merkle-claim'
        return {
            'method': method,
            'recipients': len(payout),
            'payout': payout,
            'net_flows': net,                 # losers (-) fund winners (+)
            'merkle_root': self.merkle_root(payout) if method == 'merkle-claim' else None,
        }

    # --- views --------------------------------------------------------------

    def case(self, case_id, raw=False) -> dict:
        """Fetch a case. Sealed, unresolved cases come back redacted (aggregate
        ballots only); pass raw=True for the full record (coordinator-only)."""
        c = self._get(self._load(), case_id)
        return c if raw else self._view(c)

    def cases(self, status=None, player=None) -> dict:
        idx = self._load()
        out = {}
        for cid, c in idx.items():
            if status and c['status'] != status:
                continue
            if player and self._addr(player) not in c['stakes'] and self._addr(player) != c['opener']:
                continue
            out[cid] = self._view(c)
        return out

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        idx = self._load()
        by_status = {}
        for c in idx.values():
            by_status[c['status']] = by_status.get(c['status'], 0) + 1
        return {
            'name': 'govmod',
            'description': self.description,
            'network': self.network,
            'clock': 'bloctime (chain blocks)',
            'verdict_modes': list(MODES),
            'privacy_modes': list(PRIVACY),
            'default_token': 'bloctime',
            'multi_option': True,
            'cases': len(idx),
            'by_status': by_status,
            'index': self.index_path,
        }
