"""
The demand side: pay for a proof that does not exist yet.

A listing is somebody selling what they already made. A bounty is the opposite
and it is the harder half to get right, because the buyer has to describe what
they will accept *before* seeing it, precisely enough that a machine can decide
— otherwise "does this count?" is settled by argument, and a market settled by
argument is not a market.

A bounty spec is therefore mechanical:

    system        which proof system
    vkey          the verification key, verbatim — which pins the circuit, and
                  with it the exact statement shape being asked for
    require       constraints on the public signals: {index, equals} or
                  {index, min, max}
    status        the verdict the submission must reach, default `verified`,
                  which means two independent methods agreeing

Nothing else is negotiable, and nothing here reads the prover's mind. The
reward sits in escrow from the moment the bounty is posted, so a prover can
see the money before spending an hour on the witness; if it expires unclaimed
the escrow goes home.

STAKED ROUNDS

A bounty can also demand skin from the other side. Posted with a `stake`, it
runs in rounds: before submitting, a prover signs in by locking `stake`
credits (a dollar, by default) for one token — a numbered seat, and the
number is the order they signed in. The tokens stay locked until the round's
reset — the next UTC midnight by default, or the seventh one out for a weekly
round — and nothing settles early, because the lock is the mechanism: a
prover who has paid for a seat has until the reset, and a poster cannot yank
the reward the moment a proof looks likely.

At the reset every token liquidates and the proof is settled, all in one
motion:

    won      the accepted submission whose token has the lowest number wins —
             first to sign, not first to upload. The winner gets the reward,
             their own stake back, and the stakes of every holder who signed
             in and then submitted nothing. A seat you never used was a bluff,
             and bluffs are what the pot is made of.
    tried    a holder who submitted anything — even a proof that failed —
             liquidates at par. Trying honestly costs the effort, never the
             dollar.
    unwon    every token liquidates at par and the round resets: same escrow,
             same spec, fresh seats, next boundary — daily, until the bounty's
             own TTL finally sends the escrow home.
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from . import market, proofs, storage, systems

DEFAULT_TTL = 7 * 86_400
DEFAULT_STAKE = 1.0
RESETS = {'daily': 1, 'weekly': 7}     # settle boundaries, in UTC midnights


def next_reset(settle: str = 'daily', now: float = None) -> float:
    """The next UTC midnight, or the seventh one out for a weekly round.

    Boundaries are the clock's, not the bounty's: every daily round on this
    box settles at the same moment, which is what makes "the reset" a thing a
    prover can plan around rather than a per-bounty countdown.
    """
    if settle not in RESETS:
        raise BountyError(f"settle {settle!r}? — 'daily' or 'weekly'")
    now = time.time() if now is None else float(now)
    midnight = (int(now // 86_400) + 1) * 86_400
    return float(midnight + (RESETS[settle] - 1) * 86_400)


class BountyError(Exception):
    pass


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value), 0)
    except Exception:
        return None


def check_requirements(require: List[Dict[str, Any]],
                       public_signals: List[Any]) -> Dict[str, Any]:
    """Does this submission's public statement match what was asked for?

    Numeric where both sides parse as numbers, exact string match otherwise —
    because signals are field elements written as decimal strings, and
    '0x01' == '1' is true for one of those readings and false for the other.
    """
    failures = []
    for rule in require or []:
        index = int(rule.get('index', 0))
        if index >= len(public_signals):
            failures.append(f'signal {index} is missing — the submission has '
                            f'{len(public_signals)}')
            continue
        got = public_signals[index]
        got_num = _int_or_none(got)
        if 'equals' in rule:
            want = rule['equals']
            want_num = _int_or_none(want)
            same = (got_num == want_num if (got_num is not None and want_num is not None)
                    else str(got) == str(want))
            if not same:
                failures.append(f'signal {index} is {got}, must equal {want}')
        if 'min' in rule:
            if got_num is None or got_num < int(rule['min']):
                failures.append(f'signal {index} is {got}, must be at least {rule["min"]}')
        if 'max' in rule:
            if got_num is None or got_num > int(rule['max']):
                failures.append(f'signal {index} is {got}, must be at most {rule["max"]}')
    return {'ok': not failures, 'failures': failures}


def create(poster: str, system: str, reward: float, *, vkey: Dict[str, Any] = None,
           require: Optional[List[Dict[str, Any]]] = None, title: str = '',
           description: str = '', status: str = 'verified',
           ttl: float = DEFAULT_TTL, stake: float = 0.0,
           settle: str = 'daily') -> Dict[str, Any]:
    """Post a request and fund it. The escrow is the whole point.

    A `stake` above zero makes it a staked round: provers sign in for a token
    at that price, and nothing pays out until the reset boundary.
    """
    system = systems.resolve(system)
    if reward <= 0:
        raise BountyError('a bounty with no reward is a wish — fund it')
    if float(stake) < 0:
        raise BountyError('a negative stake would pay people to squat seats')
    if float(stake) > 0 and settle not in RESETS:
        raise BountyError(f"settle {settle!r}? — 'daily' or 'weekly'")
    if status not in ('verified', 'claimed'):
        raise BountyError("accept either 'verified' (two methods agree) or "
                          "'claimed' (one did)")
    if not vkey and system in ('groth16', 'plonk', 'fflonk'):
        raise BountyError(
            'a snark bounty must pin its verification key, or "a proof for this '
            'circuit" means nothing and any proof of anything would qualify')

    market.hold(poster, float(reward), 'bounty')
    bounty_id = f'{proofs.slugify(title) or system}-{uuid.uuid4().hex[:6]}'
    record = {
        'id': bounty_id,
        'poster': poster,
        'system': system,
        'title': title or f'{systems.get(system)["label"]} bounty',
        'description': description,
        'reward': round(float(reward), 6),
        'currency': 'credits',
        'vkey': vkey or {},
        'vkey_hash': storage.digest(vkey) if vkey else None,
        'require': require or [],
        'accept_status': status,
        'state': 'open',
        'created': time.time(),
        'expires': time.time() + float(ttl),
        'submissions': [],
        'winner': None,
        'paid': 0.0,
    }
    if float(stake) > 0:
        record.update({
            'stake': round(float(stake), 6),
            'settle': settle,
            'settles': next_reset(settle),
            'round': 1,
            'tokens': [],       # this round's seats, in signing order
            'pot': 0.0,
            'rounds': [],       # settled rounds, so the resets leave a trail
        })
    storage.put_record('bounties', bounty_id, record)
    return record


def get(bounty_id: str) -> Dict[str, Any]:
    got = storage.get_record('bounties', bounty_id)
    if not got:
        raise BountyError(f'no bounty {bounty_id}')
    return got


def search(state: str = None, system: str = None, poster: str = None,
           limit: int = 100) -> List[Dict[str, Any]]:
    out = storage.records('bounties', limit=1000)
    now = time.time()
    for record in out:            # expiry is a fact about the clock, not an event
        if record['state'] == 'open' and record['expires'] < now:
            record['state'] = 'expired'
    if state:
        out = [x for x in out if x['state'] == state]
    if system:
        out = [x for x in out if x['system'] == systems.resolve(system)]
    if poster:
        out = [x for x in out if (x.get('poster') or '').lower() == poster.lower()]
    return out[:limit]


def _locked(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in record.get('tokens') or [] if t.get('state') == 'locked']


def _return_tokens(record: Dict[str, Any], reason: str) -> float:
    """Liquidate every locked token at par, back to its holder."""
    returned = 0.0
    for token in _locked(record):
        market.unhold(token['holder'], float(token['stake']),
                      f'token liquidated — {reason}')
        token.update({'state': 'liquidated', 'returned': float(token['stake']),
                      'liquidated_at': time.time()})
        returned += float(token['stake'])
    record['pot'] = 0.0
    return round(returned, 6)


def join(bounty_id: str, prover: str) -> Dict[str, Any]:
    """Sign in for this round: lock the stake, take the next numbered token.

    The token is the whole entry ticket — no token, no submission — and its
    number is the order of signing, which is the tiebreak at settlement. The
    stake stays locked until the reset; there is no early exit, because a
    seat you could vacate the moment the round looked hard would be free.
    """
    record = _settle_if_due(get(bounty_id))
    if not record.get('stake'):
        raise BountyError('this bounty has no tokens — just submit')
    if record['state'] != 'open':
        raise BountyError(f'that bounty is {record["state"]}')
    if record['expires'] < time.time():
        raise BountyError('that bounty expired — the poster can reclaim the escrow')
    held = next((t for t in _locked(record)
                 if (t['holder'] or '').lower() == (prover or '').lower()), None)
    if held:
        raise BountyError(f'you already hold token #{held["seq"]} for round '
                          f'{record["round"]} — one seat per address')

    market.hold(prover, float(record['stake']),
                f'token for bounty {bounty_id} round {record["round"]}')
    token = {'holder': prover, 'seq': len(record.get('tokens') or []) + 1,
             'round': record['round'], 'at': time.time(),
             'stake': float(record['stake']), 'state': 'locked'}
    record.setdefault('tokens', []).append(token)
    record['pot'] = round(sum(t['stake'] for t in _locked(record)), 6)
    storage.put_record('bounties', bounty_id, record)
    return {'token': token, 'bounty': record,
            'settles': record['settles'],
            'note': f'token #{token["seq"]}, locked until the reset — '
                    'submit before it or the stake goes to whoever wins'}


def _settle_round(record: Dict[str, Any]) -> Dict[str, Any]:
    """The reset: liquidate every token and settle the proof, in one motion.

    Winner selection is by token number among accepted submissions — first to
    *sign*, not first to upload — because the seat is what was paid for, and a
    race decided at upload time would make the stake a spectator.
    """
    now = time.time()
    entries = [e for e in record['submissions']
               if e.get('round') == record['round']]
    accepted = sorted((e for e in entries if e.get('accepted')),
                      key=lambda e: (e.get('token_seq') or 10 ** 9, e['at']))
    summary = {'round': record['round'], 'tokens': len(record.get('tokens') or []),
               'submissions': len(entries), 'winner': None, 'settled_at': now}

    if accepted:
        winning = accepted[0]
        winner = winning['prover']
        payout = market.release(record['poster'], winner, float(record['reward']),
                                f'bounty {record["id"]} settled, round {record["round"]}')
        tried = {(e['prover'] or '').lower() for e in entries}
        forfeited = 0.0
        for token in _locked(record):
            if (token['holder'] or '').lower() in tried:
                market.unhold(token['holder'], float(token['stake']),
                              f'token liquidated — bounty {record["id"]} '
                              f'round {record["round"]}')
                token.update({'state': 'liquidated', 'returned': float(token['stake']),
                              'liquidated_at': now})
            else:   # a seat that never submitted was a bluff — pot to the winner
                market.release(token['holder'], winner, float(token['stake']),
                               f'token forfeited to winner — bounty {record["id"]} '
                               f'round {record["round"]}')
                token.update({'state': 'forfeited', 'to': winner,
                              'liquidated_at': now})
                forfeited += float(token['stake'])
        record['pot'] = 0.0
        winning['paid'] = payout['paid']
        summary['winner'] = winner
        record.update({'state': 'paid', 'winner': winner,
                       'paid': float(record['reward']), 'paid_at': now,
                       'forfeited_to_winner': round(forfeited, 6),
                       'winning_proof': winning['proof']})
    else:
        _return_tokens(record, f'bounty {record["id"]} round {record["round"]} unwon')
        if record['expires'] < now:      # the TTL, not the reset, ends the run
            market.unhold(record['poster'], float(record['reward']),
                          f'bounty {record["id"]} expired unwon')
            record.update({'state': 'expired', 'returned': float(record['reward']),
                           'expired_at': now})
        else:                            # daily reset: same escrow, fresh seats
            record.update({'round': record['round'] + 1, 'tokens': [],
                           'settles': next_reset(record.get('settle', 'daily'))})

    record.setdefault('rounds', []).append(summary)
    storage.put_record('bounties', record['id'], record)
    return record


def _settle_if_due(record: Dict[str, Any]) -> Dict[str, Any]:
    if (record.get('stake') and record['state'] == 'open'
            and float(record.get('settles') or 0) <= time.time()):
        return _settle_round(record)
    return record


def settle(bounty_id: str) -> Dict[str, Any]:
    """Run the reset, if it is due. Anyone may ask; the clock decides.

    Refusing to settle early is not caution, it is the product: the lock is
    what a prover bought with the stake, and a settlement that can be hurried
    by whoever is winning is an auction, not a round.
    """
    record = get(bounty_id)
    if not record.get('stake'):
        raise BountyError('this bounty has no rounds — submissions settle on the spot')
    if record['state'] != 'open':
        return record
    if float(record.get('settles') or 0) > time.time():
        remaining = int(float(record['settles']) - time.time())
        raise BountyError(f'the round settles at the reset, {remaining}s from now — '
                          'the lock is the mechanism, nobody can hurry it')
    return _settle_round(record)


def submit(bounty_id: str, prover: str, proof: Dict[str, Any],
           public_signals: Optional[List[Any]] = None,
           statement: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Answer a bounty. Verified against the poster's key, not the prover's.

    The submission is checked against the *bounty's* verification key even if
    the prover sent one, which is the only thing standing between this and a
    prover who submits a valid proof of a circuit they wrote themselves.

    On a staked bounty the token comes first and the payout comes last: no
    seat means no submission, and an accepted proof is *recorded* now but
    *settled* at the reset, when the tokens liquidate.
    """
    record = _settle_if_due(get(bounty_id))
    if record['state'] != 'open':
        raise BountyError(f'that bounty is {record["state"]}')
    if record['expires'] < time.time():
        record['state'] = 'expired'
        storage.put_record('bounties', bounty_id, record)
        raise BountyError('that bounty expired — the poster can reclaim the escrow')
    token = None
    if record.get('stake'):
        token = next((t for t in _locked(record)
                      if (t['holder'] or '').lower() == (prover or '').lower()), None)
        if not token:
            raise BountyError(
                f'this bounty runs in staked rounds — put in {record["stake"]} '
                f'credit(s) for a token first: POST /bounties/{bounty_id}/join')

    vkey = record.get('vkey') or statement or {}
    published = proofs.publish(
        record['system'], proof, vkey, public_signals or [],
        author=prover, title=f'submission to {record["title"]}',
        description=f'bounty {bounty_id}', price=0.0,
        tags=['bounty', record['system']])

    requirements = check_requirements(record.get('require'),
                                      published.get('public_signals') or [])
    accepted = (published['status'] == 'verified'
                or (record['accept_status'] == 'claimed'
                    and published['status'] in ('verified', 'claimed')))
    entry = {
        'proof': published['id'],
        'prover': prover,
        'at': time.time(),
        'status': published['status'],
        'why': published['why'],
        'requirements': requirements,
        'accepted': bool(accepted and requirements['ok']),
    }
    if token:
        entry.update({'round': record['round'], 'token_seq': token['seq']})
    record['submissions'].append(entry)

    if entry['accepted'] and not record.get('stake'):
        payout = market.release(record['poster'], prover, float(record['reward']),
                                f'bounty {bounty_id}')
        record.update({'state': 'paid', 'winner': prover,
                       'paid': float(record['reward']), 'paid_at': time.time(),
                       'winning_proof': published['id']})
        entry['paid'] = payout['paid']
    storage.put_record('bounties', bounty_id, record)
    out = {'bounty': record, 'submission': entry, 'proof': proofs.listing(published)}
    if token and entry['accepted']:
        out['note'] = ('accepted, not yet paid — the proof settles at the reset, '
                       'when the tokens liquidate. First token number among '
                       'accepted submissions wins')
    return out


def cancel(bounty_id: str, caller: str) -> Dict[str, Any]:
    """Take the escrow back. Only yours, only while nobody has won it."""
    record = get(bounty_id)
    if (record['poster'] or '').lower() != (caller or '').lower() and caller != 'open-mode':
        raise BountyError(f'bounty {bounty_id} belongs to {record["poster"]}')
    if record['state'] == 'paid':
        raise BountyError('it has already been won — that money is spent')
    if record['state'] == 'cancelled':
        return record
    _return_tokens(record, f'bounty {bounty_id} cancelled')   # seats first
    returned = market.unhold(record['poster'], float(record['reward']),
                             f'bounty {bounty_id} cancelled')
    record.update({'state': 'cancelled', 'cancelled_at': time.time(),
                   'returned': returned['returned']})
    storage.put_record('bounties', bounty_id, record)
    return record


def sweep() -> Dict[str, Any]:
    """Run every reset that is due, and return the escrow on what expired.

    Called on a timer or by hand; an expired bounty that still holds its
    poster's credits — or a reset that is past and unsettled while provers'
    stakes sit locked — is the module quietly keeping money it has no claim
    to.
    """
    returned, settled, now = [], [], time.time()
    for record in storage.records('bounties', limit=1000):
        if record['state'] != 'open':
            continue
        if record.get('stake') and float(record.get('settles') or 0) <= now:
            record = _settle_round(record)
            settled.append({'bounty': record['id'], 'state': record['state'],
                            'round': (record['rounds'][-1]['round']
                                      if record.get('rounds') else None),
                            'winner': record.get('winner')})
        if record['state'] == 'open' and record['expires'] < now:
            _return_tokens(record, f'bounty {record["id"]} expired')
            market.unhold(record['poster'], float(record['reward']),
                          f'bounty {record["id"]} expired')
            record.update({'state': 'expired', 'returned': float(record['reward']),
                           'expired_at': now})
            storage.put_record('bounties', record['id'], record)
            returned.append({'bounty': record['id'], 'to': record['poster'],
                             'amount': record['reward']})
    return {'expired': len(returned), 'settled': len(settled),
            'returned': returned, 'rounds': settled}
