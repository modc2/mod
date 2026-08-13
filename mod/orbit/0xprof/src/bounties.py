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
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from . import market, proofs, storage, systems

DEFAULT_TTL = 7 * 86_400


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
           ttl: float = DEFAULT_TTL) -> Dict[str, Any]:
    """Post a request and fund it. The escrow is the whole point."""
    system = systems.resolve(system)
    if reward <= 0:
        raise BountyError('a bounty with no reward is a wish — fund it')
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


def submit(bounty_id: str, prover: str, proof: Dict[str, Any],
           public_signals: Optional[List[Any]] = None,
           statement: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Answer a bounty. Verified against the poster's key, not the prover's.

    The submission is checked against the *bounty's* verification key even if
    the prover sent one, which is the only thing standing between this and a
    prover who submits a valid proof of a circuit they wrote themselves.
    """
    record = get(bounty_id)
    if record['state'] != 'open':
        raise BountyError(f'that bounty is {record["state"]}')
    if record['expires'] < time.time():
        record['state'] = 'expired'
        storage.put_record('bounties', bounty_id, record)
        raise BountyError('that bounty expired — the poster can reclaim the escrow')

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
    record['submissions'].append(entry)

    if entry['accepted']:
        payout = market.release(record['poster'], prover, float(record['reward']),
                                f'bounty {bounty_id}')
        record.update({'state': 'paid', 'winner': prover,
                       'paid': float(record['reward']), 'paid_at': time.time(),
                       'winning_proof': published['id']})
        entry['paid'] = payout['paid']
    storage.put_record('bounties', bounty_id, record)
    return {'bounty': record, 'submission': entry, 'proof': proofs.listing(published)}


def cancel(bounty_id: str, caller: str) -> Dict[str, Any]:
    """Take the escrow back. Only yours, only while nobody has won it."""
    record = get(bounty_id)
    if (record['poster'] or '').lower() != (caller or '').lower() and caller != 'open-mode':
        raise BountyError(f'bounty {bounty_id} belongs to {record["poster"]}')
    if record['state'] == 'paid':
        raise BountyError('it has already been won — that money is spent')
    if record['state'] == 'cancelled':
        return record
    returned = market.unhold(record['poster'], float(record['reward']),
                             f'bounty {bounty_id} cancelled')
    record.update({'state': 'cancelled', 'cancelled_at': time.time(),
                   'returned': returned['returned']})
    storage.put_record('bounties', bounty_id, record)
    return record


def sweep() -> Dict[str, Any]:
    """Return the escrow on everything that expired unclaimed.

    Called on a timer or by hand; an expired bounty that still holds its
    poster's credits is the module quietly keeping money it has no claim to.
    """
    returned, now = [], time.time()
    for record in storage.records('bounties', limit=1000):
        if record['state'] == 'open' and record['expires'] < now:
            market.unhold(record['poster'], float(record['reward']),
                          f'bounty {record["id"]} expired')
            record.update({'state': 'expired', 'returned': float(record['reward']),
                           'expired_at': now})
            storage.put_record('bounties', record['id'], record)
            returned.append({'bounty': record['id'], 'to': record['poster'],
                             'amount': record['reward']})
    return {'expired': len(returned), 'returned': returned}
