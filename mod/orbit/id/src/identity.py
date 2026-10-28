"""One identity, many accounts — the rules for joining them, and for taking
them apart again.

The shape of it
---------------
An identity is a set of accounts and an append-only log of the signed events
that put them there. Nothing is a member because a database row says so; an
account is a member because there is a signature in the log saying it agreed to
join, and that signature can be re-checked at any time by anyone.

Who is allowed to do what
-------------------------
The interesting question is not "can this key sign?" — that part is easy — but
"who may add an account to *my* identity?" If proving control of a wallet were
enough, anyone could attach their wallet to your identity and stand next to you
in it. So every join needs two signatures:

    the joining account   proves it holds its own key, and
    a current member      proves it consents to the join.

The second signature is what a session is: proving control of a member account
mints a short-lived session, and the *proof that minted it* is copied into every
event that session authorises. The log therefore holds a complete chain of
consent — B joined, B signed for itself, A signed to allow it, A was already a
member at that point — and `audit()` walks the whole chain offline.

Genesis is the exception, and only because it has to be: the first account of a
brand-new identity has nobody to ask.

Merging is symmetric. Two identities that each already exist can only become one
if a member of each signs the same pair, in the same canonical order, so neither
side can be absorbed without knowing.

Leaving is one-sided on purpose. An account can always remove itself with its own
signature — being in someone's identity is not a thing you should need permission
to stop. Removing *someone else* requires the root account, the one that created
the identity.

Nothing is ever deleted. Unlinking appends an event; merging leaves the absorbed
log where it is and records an alias so its name still resolves. The history is
the evidence.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from . import accounts, chains, statement, store

PROTOCOL = statement.PROTOCOL
SESSION_TTL = 3600


class IdError(ValueError):
    """A request that the rules above do not allow."""


# ── naming ───────────────────────────────────────────────────────────────

def canonical(kind: str, handle: str) -> Tuple[str, str, str]:
    """(kind, address, "kind:address") — the one spelling used everywhere."""
    if accounts.is_service(kind):
        service = accounts.get(kind)
        return service.name, service.parse(handle), f'{service.name}:{service.parse(handle)}'
    chain = chains.get(kind)
    address = chain.parse(handle)
    return chain.name, address, f'{chain.name}:{address}'


def split(account: str) -> Tuple[str, str]:
    kind, _, address = account.partition(':')
    if not address:
        raise IdError(f'{account!r} is not a kind:address pair')
    return kind, address


def derive(account: str) -> str:
    """An identity is named after the account that created it — deterministically,
    so the same wallet starting over lands on the same name rather than a new one."""
    digest = hashlib.sha256(f'{PROTOCOL}|genesis|{account}'.encode()).hexdigest()
    return f'id_{digest[:16]}'


def strength_of(kind: str) -> str:
    return 'publication' if accounts.is_service(kind) else 'key'


# ── challenges ───────────────────────────────────────────────────────────

def challenge(kind: str, handle: str, op: str = 'link', id: str = None,
              other: str = None, name: str = None, target: str = None,
              ttl: int = statement.DEFAULT_TTL) -> Dict[str, Any]:
    """Ask for the exact text to sign (or to publish, for an account with no key)."""
    kind, address, account = canonical(kind, handle)
    on = store.follow(id) if id else None
    extra: Dict[str, str] = {}

    if op == 'link':
        if not on:
            on = store.resolve(account) or 'new'
    elif op == 'merge':
        if not on or not other:
            raise IdError('a merge names two identities — pass id and other')
        on, mate = merge_order(on, store.follow(other))
        extra['other'] = mate
    elif op in ('unlink', 'name', 'claim'):
        on = on or store.resolve(account)
        if not on:
            raise IdError(f'{account} does not belong to an identity yet')
        if op == 'unlink' and target:
            evicted = canonical(*split(target))[2] if ':' in target else target
            extra['target'] = evicted
        if op == 'name':
            if not name:
                raise IdError('pass the name you want to set')
            extra['name'] = str(name)[:64]
    else:
        raise IdError(f'unknown operation {op!r}')

    fields = statement.fields(op=op, identity=on, account=account,
                              id=on, ttl=ttl, extra=extra)
    fields['kind'] = kind
    fields['address'] = address
    fields['strength'] = strength_of(kind)
    store.put_challenge(fields)

    text = statement.render(fields)
    card = {
        'nonce': fields['nonce'], 'op': op, 'id': on, 'account': account,
        'kind': kind, 'address': address, 'strength': fields['strength'],
        'statement': text, 'token': statement.token(fields),
        'expires': fields['expires'], 'expires_in': int(ttl),
        **({'other': extra['other']} if 'other' in extra else {}),
        **({'removing': extra['target']} if 'target' in extra else {}),
    }
    if fields['strength'] == 'key':
        entry = chains.get(kind)
        card['sign_with'] = entry.wallets
        card['scheme'] = entry.scheme
        card['needs_pubkey'] = entry.needs_pubkey
        card['how'] = ('Sign the `statement` below with this wallet, then submit the '
                       'signature with this nonce.')
    else:
        service = accounts.get(kind)
        card['publish_to'] = service.where
        card['how'] = (f'Publish the one-line `token` to {service.where}, then submit '
                       'this nonce and (where it applies) the link to it.')
        card['hint'] = service.hint
    return card


# ── proofs ───────────────────────────────────────────────────────────────

def _take(nonce: str) -> Dict[str, Any]:
    fields = store.take_challenge(nonce)
    if fields is None:
        raise IdError(
            'that nonce is not outstanding — it was already used, or it expired, '
            'or this is a different host. Ask for a new challenge.')
    statement.check_fresh(fields)
    return fields


def _prove(fields: Dict[str, Any], signature: str = None, pubkey: str = None,
           source: str = None) -> Dict[str, Any]:
    """Turn a challenge plus a signature (or a publication) into a proof record."""
    text = statement.render(fields)
    kind, address = fields['kind'], fields['address']
    if fields['strength'] == 'key':
        if not signature:
            raise IdError(f'{kind} accounts prove themselves with a signature')
        result = chains.verify(kind, address, text, signature, pubkey=pubkey)
        proof = {'strength': 'key', 'kind': kind, 'account': fields['account'],
                 'statement': text, 'signature': signature,
                 'pubkey': result.get('pubkey'), 'scheme': result['scheme'],
                 'curve': result['curve'], 'detail': result['detail']}
        for optional in ('form', 'low_s', 'wrapping', 'weak'):
            if optional in result:
                proof[optional] = result[optional]
    else:
        result = accounts.verify(kind, address, statement.token(fields), source=source)
        proof = {'strength': 'publication', 'kind': kind, 'account': fields['account'],
                 'statement': statement.token(fields), 'source': result.get('source'),
                 'detail': result['detail'],
                 'caveat': result.get('caveat', 'holds only while that page is up')}
    proof.update({'nonce': fields['nonce'], 'op': fields['op'],
                  'fields': {k: v for k, v in fields.items() if k != 'extra'},
                  'extra': fields.get('extra') or {},
                  'proved_at': time.time()})
    return proof


def recheck_proof(proof: Dict[str, Any], live: bool = False) -> Dict[str, Any]:
    """Re-run a stored proof. Key proofs need nothing but this machine."""
    fields = dict(proof['fields'])
    fields['extra'] = proof.get('extra') or {}
    try:
        if statement.render(fields) != proof['statement']:
            return {'ok': False, 'why': 'the stored statement is not what these fields render to'}
        if proof['strength'] == 'key':
            chains.verify(proof['kind'], fields['address'], proof['statement'],
                          proof['signature'], pubkey=proof.get('pubkey'))
            return {'ok': True, 'how': 'signature re-verified offline'}
        if not live:
            return {'ok': None, 'how': 'publication proof — pass live=true to re-fetch it'}
        accounts.verify(proof['kind'], fields['address'], proof['statement'],
                        source=proof.get('source'))
        return {'ok': True, 'how': 're-fetched and still published'}
    except (chains.ProofError, chains.AddressError, accounts.ProofError,
            accounts.AccountError, KeyError, ValueError) as exc:
        return {'ok': False, 'why': f'{type(exc).__name__}: {exc}'}


# ── sessions: a member's consent, held briefly ───────────────────────────

def _mint(id: str, proof: Dict[str, Any]) -> Dict[str, Any]:
    token = secrets.token_urlsafe(24)
    sessions = store.blob('sessions')
    now = time.time()
    for key in [k for k, v in sessions.items() if v.get('expires_at', 0) < now]:
        sessions.pop(key)
    sessions[hashlib.sha256(token.encode()).hexdigest()] = {
        'id': id, 'account': proof['account'], 'issued_at': now,
        'expires_at': now + SESSION_TTL, 'proof': proof}
    store.save_blob('sessions', sessions)
    return {'session': token, 'expires_in': SESSION_TTL, 'id': id,
            'held_by': proof['account'],
            'means': 'this account has consented to add more accounts to this '
                     'identity for the next hour; the signature that minted it is '
                     'copied into every event it authorises'}


def session_of(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    found = store.blob('sessions').get(hashlib.sha256(token.encode()).hexdigest())
    if not found or found.get('expires_at', 0) < time.time():
        return None
    found = dict(found)
    found['id'] = store.follow(found['id'])
    return found


def drop_session(token: str) -> bool:
    sessions = store.blob('sessions')
    if sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None) is None:
        return False
    store.save_blob('sessions', sessions)
    return True


# ── replaying a log into a state ─────────────────────────────────────────

def _state(id: str) -> Dict[str, Any]:
    id = store.follow(id)
    if not store.exists(id):
        raise IdError(f'no identity {id}')
    state: Dict[str, Any] = {'id': id, 'name': None, 'root': None, 'accounts': {},
                             'absorbed': [], 'created_at': None, 'updated_at': None,
                             'merged_into': None}
    for event in store.read(id):
        state['updated_at'] = event.get('at')
        if state['created_at'] is None:
            state['created_at'] = event.get('at')
        op = event['op']
        if op in ('genesis', 'link'):
            state['accounts'][event['account']] = {
                'account': event['account'], 'kind': event['kind'],
                'address': event['address'], 'strength': event['strength'],
                'linked_at': event['at'], 'seq': event['seq'],
                'via': event.get('authorized_by', {}).get('account'),
                'proof': event['proofs'][0]}
            if op == 'genesis':
                state['root'] = event['account']
        elif op == 'unlink':
            state['accounts'].pop(event['account'], None)
        elif op == 'merge':
            for record in event['absorbed_accounts']:
                state['accounts'].setdefault(record['account'], record)
            state['absorbed'].append(event['absorbed'])
        elif op == 'name':
            state['name'] = event['name']
        elif op == 'merged_into':
            state['merged_into'] = event['into']
    if state['root'] not in state['accounts']:
        # the founder left: root falls to the earliest account still present
        remaining = sorted(state['accounts'].values(), key=lambda r: (r['linked_at'], r['account']))
        state['root'] = remaining[0]['account'] if remaining else None
    return state


def document(id: str, proofs: bool = True) -> Dict[str, Any]:
    """The identity as a portable, self-verifying document."""
    state = _state(id)
    members = sorted(state['accounts'].values(), key=lambda r: (r['linked_at'], r['account']))
    return {
        'protocol': PROTOCOL, 'id': state['id'], 'name': state['name'],
        'root': state['root'], 'created_at': state['created_at'],
        'updated_at': state['updated_at'],
        'accounts': [
            {k: v for k, v in record.items() if proofs or k != 'proof'}
            for record in members],
        'count': len(members),
        'by_strength': {
            'key': sum(1 for r in members if r['strength'] == 'key'),
            'publication': sum(1 for r in members if r['strength'] == 'publication')},
        'chains': sorted({r['kind'] for r in members if r['strength'] == 'key'}),
        'services': sorted({r['kind'] for r in members if r['strength'] == 'publication'}),
        'also_known_as': state['absorbed'],
        'merged_into': state['merged_into'],
        'events': len(store.events(state['id'])),
    }


def members(id: str) -> List[str]:
    return sorted(_state(id)['accounts'])


def whois(kind: str = None, handle: str = None, account: str = None) -> Dict[str, Any]:
    """Which identity does this account belong to, and what else is in it?"""
    if account:
        kind, handle = split(account)
    if not kind or not handle:
        raise IdError('pass an account, or a kind and a handle')
    _, _, account = canonical(kind, handle)
    id = store.resolve(account)
    if not id:
        return {'account': account, 'id': None,
                'found': False,
                'note': 'not linked to any identity on this host'}
    doc = document(id, proofs=False)
    doc.update({'found': True, 'account': account,
                'siblings': [a['account'] for a in doc['accounts'] if a['account'] != account]})
    return doc


def listing() -> List[Dict[str, Any]]:
    out = []
    for id in store.ids():
        try:
            doc = document(id, proofs=False)
        except IdError:
            continue
        if doc['merged_into']:
            continue
        out.append({'id': doc['id'], 'name': doc['name'], 'root': doc['root'],
                    'count': doc['count'], 'chains': doc['chains'],
                    'services': doc['services'], 'created_at': doc['created_at'],
                    'updated_at': doc['updated_at'],
                    'accounts': [a['account'] for a in doc['accounts']]})
    return sorted(out, key=lambda row: row['created_at'] or 0)


# ── applying an event ────────────────────────────────────────────────────

def _index_link(account: str, id: str) -> None:
    data = store.index()
    data['accounts'][account] = id
    store.save_index(data)


def _index_unlink(account: str) -> None:
    data = store.index()
    data['accounts'].pop(account, None)
    store.save_index(data)


def _record(op: str, id: str, proof: Dict[str, Any], **rest: Any) -> Dict[str, Any]:
    event = {'op': op, 'at': time.time(), 'protocol': PROTOCOL,
             'account': proof['account'], 'kind': proof['kind'],
             'address': proof['fields']['address'], 'strength': proof['strength'],
             'proofs': [proof]}
    event.update(rest)
    return store.append(id, event)


def submit(nonce: str, signature: str = None, pubkey: str = None,
           source: str = None, session: str = None) -> Dict[str, Any]:
    """One door for every operation — the challenge says which one this is."""
    fields = _take(nonce)
    try:
        proof = _prove(fields, signature=signature, pubkey=pubkey, source=source)
    except Exception:
        store.put_challenge(fields)   # a failed attempt should not burn the nonce
        raise
    op = fields['op']
    if op == 'claim':
        return _do_claim(fields, proof)
    if op == 'link':
        return _do_link(fields, proof, session)
    if op == 'unlink':
        return _do_unlink(fields, proof)
    if op == 'name':
        return _do_name(fields, proof)
    if op == 'merge':
        return _do_merge(fields, proof)
    raise IdError(f'unknown operation {op!r}')


def _do_claim(fields: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
    id = store.resolve(proof['account'])
    if not id:
        raise IdError(f'{proof["account"]} is not in an identity — link it first')
    minted = _mint(id, proof)
    minted.update({'ok': True, 'op': 'claim', 'identity': document(id, proofs=False)})
    return minted


def _do_link(fields: Dict[str, Any], proof: Dict[str, Any],
             session: str = None) -> Dict[str, Any]:
    account = proof['account']
    target = fields['id']
    already = store.resolve(account)

    if target == 'new':
        if already:
            target = already          # it joined an identity after the challenge was cut
        else:
            id = derive(account)
            if not store.exists(id):
                event = _record('genesis', id, proof)
                _index_link(account, id)
                out = {'ok': True, 'op': 'genesis', 'id': id, 'account': account,
                       'created': True, 'event': event['seq'],
                       'note': 'a new identity, named after the account that made it'}
                out.update(_mint(id, proof))
                out['identity'] = document(id, proofs=False)
                return out
            target = id               # this account founded one before and then left

    target = store.follow(target)
    if not store.exists(target):
        raise IdError(f'no identity {target}')
    state = _state(target)

    if already and already != target:
        raise IdError(
            f'{account} already belongs to {already}. Two identities that both '
            f'exist join with a merge, which both sides sign: '
            f'`m id/merge id={target} other={already}`')

    if account in state['accounts']:
        event = _record('link', target, proof, note='re-proved')
        out = {'ok': True, 'op': 'reproof', 'id': target, 'account': account,
               'event': event['seq'],
               'note': 'already a member — the fresh signature was appended anyway'}
        out.update(_mint(target, proof))
        return out

    held = session_of(session)
    if not held or held['id'] != target:
        raise IdError(
            f'joining {target} needs its consent as well as yours. Prove control of '
            f'an account already in it (`op=claim`) and pass the session you get back '
            f'— otherwise anyone could attach their wallet to someone else\'s identity.')

    event = _record('link', target, proof, authorized_by=held['proof'])
    _index_link(account, target)
    return {'ok': True, 'op': 'link', 'id': target, 'account': account,
            'authorized_by': held['account'], 'event': event['seq'],
            'identity': document(target, proofs=False),
            'note': f'{account} joined {target}, with {held["account"]} consenting'}


def _do_unlink(fields: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
    target = store.follow(fields['id'])
    state = _state(target)
    signer = proof['account']
    victim = (fields.get('extra') or {}).get('target') or signer
    if victim not in state['accounts']:
        raise IdError(f'{victim} is not in {target}')
    if victim != signer and signer != state['root']:
        raise IdError(f'only {victim} itself or the root account ({state["root"]}) '
                      f'can remove {victim}')
    if len(state['accounts']) == 1:
        raise IdError('that is the only account left — removing it would leave an '
                      'identity nobody can speak for. Merge it elsewhere instead.')
    event = _record('unlink', target, proof, removed=victim,
                    by='self' if victim == signer else 'root')
    _index_unlink(victim)
    return {'ok': True, 'op': 'unlink', 'id': target, 'removed': victim,
            'event': event['seq'], 'identity': document(target, proofs=False),
            'note': 'the account is out of the set; the log keeps the whole history'}


def _do_name(fields: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
    target = store.follow(fields['id'])
    state = _state(target)
    if proof['account'] != state['root']:
        raise IdError(f'only the root account ({state["root"]}) sets the display name')
    name = (fields.get('extra') or {})['name']
    event = _record('name', target, proof, name=name)
    return {'ok': True, 'op': 'name', 'id': target, 'name': name,
            'event': event['seq'], 'identity': document(target, proofs=False)}


# ── merging ──────────────────────────────────────────────────────────────

def merge_order(a: str, b: str) -> Tuple[str, str]:
    """Which identity survives: the older one, and its name is the one that stays."""
    a, b = store.follow(a), store.follow(b)
    if a == b:
        raise IdError(f'{a} is already the same identity')
    for id in (a, b):
        if not store.exists(id):
            raise IdError(f'no identity {id}')
    born = {id: (_state(id)['created_at'] or 0, id) for id in (a, b)}
    survivor, absorbed = sorted((a, b), key=lambda id: born[id])
    return survivor, absorbed


def _do_merge(fields: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
    survivor = store.follow(fields['id'])
    absorbed = store.follow((fields.get('extra') or {})['other'])
    if survivor == absorbed:
        return {'ok': True, 'op': 'merge', 'id': survivor, 'already': True,
                'note': 'these are already one identity'}
    side = store.resolve(proof['account'])
    if side not in (survivor, absorbed):
        raise IdError(f'{proof["account"]} is in {side or "no identity"} — a merge is '
                      f'signed by a member of each side')

    key = f'{survivor}|{absorbed}'
    pending = store.blob('merges')
    slot = pending.get(key) or {}
    slot[side] = proof
    slot['at'] = time.time()
    pending[key] = slot
    store.save_blob('merges', pending)

    if survivor not in slot or absorbed not in slot:
        waiting = absorbed if survivor in slot else survivor
        return {'ok': True, 'op': 'merge', 'stage': 'half-signed',
                'signed_by': proof['account'], 'waiting_on': waiting,
                'survivor': survivor, 'absorbed': absorbed,
                'note': f'one side has signed. Now a member of {waiting} has to sign '
                        f'the same pair — neither identity can be absorbed quietly.'}

    return apply_merge(survivor, absorbed, slot[survivor], slot[absorbed])


def apply_merge(survivor: str, absorbed: str, proof_a: Dict[str, Any],
                proof_b: Dict[str, Any]) -> Dict[str, Any]:
    state_b = _state(absorbed)
    moved = sorted(state_b['accounts'].values(), key=lambda r: (r['linked_at'], r['account']))
    event = _record('merge', survivor, proof_a, absorbed=absorbed,
                    absorbed_accounts=moved,
                    absorbed_name=state_b['name'],
                    proofs=[proof_a, proof_b])
    store.append(absorbed, {'op': 'merged_into', 'at': time.time(), 'protocol': PROTOCOL,
                            'into': survivor, 'account': proof_b['account'],
                            'kind': proof_b['kind'],
                            'address': proof_b['fields']['address'],
                            'strength': proof_b['strength'],
                            'proofs': [proof_a, proof_b]})

    data = store.index()
    for record in moved:
        data['accounts'][record['account']] = survivor
    data['aliases'][absorbed] = survivor
    store.save_index(data)

    pending = store.blob('merges')
    pending.pop(f'{survivor}|{absorbed}', None)
    store.save_blob('merges', pending)

    return {'ok': True, 'op': 'merge', 'id': survivor, 'absorbed': absorbed,
            'moved': [record['account'] for record in moved], 'event': event['seq'],
            'signed_by': [proof_a['account'], proof_b['account']],
            'identity': document(survivor, proofs=False),
            'note': f'{absorbed} is now {survivor}; the old name still resolves'}


# ── the audit ────────────────────────────────────────────────────────────

def audit(id: str, live: bool = False) -> Dict[str, Any]:
    """Replay the log and re-check every signature in it. This is the real answer
    to "is this identity what it says it is" — everything else is a cache."""
    id = store.follow(id)
    if not store.exists(id):
        raise IdError(f'no identity {id}')
    events = store.events(id)
    seen_nonces: Dict[str, int] = {}
    present: Dict[str, Dict[str, Any]] = {}
    root: Optional[str] = None
    rows: List[Dict[str, Any]] = []
    ok = True

    for event in events:
        problems: List[str] = []
        subject = event['proofs'][0] if event.get('proofs') else None
        if subject is None:
            problems.append('event carries no proof')
        else:
            verdict = recheck_proof(subject, live=live)
            if verdict['ok'] is False:
                problems.append(f'signature does not re-verify: {verdict["why"]}')
            fields = subject['fields']
            # the event header is a summary of the proof; a log edited by hand will
            # change the summary and leave the signature underneath it untouched
            for key, signed in (('account', subject.get('account')),
                                ('kind', subject.get('kind')),
                                ('address', fields.get('address')),
                                ('strength', subject.get('strength'))):
                if event.get(key) != signed:
                    problems.append(
                        f'event says {key}={event.get(key)!r}, but the signature '
                        f'underneath it is for {signed!r}')
            if fields.get('id') not in (id, 'new') and event['op'] not in ('merge', 'merged_into'):
                if store.follow(fields.get('id')) != id:
                    problems.append(f'signed for {fields.get("id")}, applied to {id}')
            signed_as = {'genesis': 'link', 'merged_into': 'merge'}.get(event['op'], event['op'])
            if fields.get('op') != signed_as:
                problems.append(f'signed a {fields.get("op")}, applied as a {event["op"]}')
            if float(fields.get('issued_at', 0)) > float(fields.get('expires_at', 0)):
                problems.append('challenge expired before it was issued')
            nonce = subject.get('nonce')
            if nonce in seen_nonces:
                problems.append(f'nonce reused from event {seen_nonces[nonce]}')
            seen_nonces[nonce] = event['seq']

        op = event['op']
        if op == 'genesis':
            if event['seq'] != 0:
                problems.append('genesis is not the first event')
            if id != derive(event['account']):
                problems.append(f'identity name {id} is not the one derived from '
                                f'{event["account"]} ({derive(event["account"])})')
            root = event['account']
            present[event['account']] = event
        elif op == 'link':
            consent = event.get('authorized_by')
            if event['account'] in present:
                pass                       # a re-proof of a member needs nobody's consent
            elif not consent:
                problems.append('joined without a member consenting')
            else:
                if consent['account'] not in present:
                    problems.append(f'consent came from {consent["account"]}, '
                                    f'which was not a member yet')
                verdict = recheck_proof(consent, live=live)
                if verdict['ok'] is False:
                    problems.append(f'consent signature does not re-verify: {verdict["why"]}')
            present[event['account']] = event
        elif op == 'unlink':
            victim = event.get('removed')
            if victim not in present:
                problems.append(f'{victim} was not a member')
            elif victim != event['account'] and event['account'] != root:
                problems.append('removal signed by neither the account nor root')
            present.pop(victim, None)
        elif op == 'merge':
            signers = {p['account'] for p in event.get('proofs', [])}
            if len(signers) < 2:
                problems.append('a merge needs a signature from each side')
            for record in event.get('absorbed_accounts', []):
                present.setdefault(record['account'], event)
        elif op == 'name':
            if event['account'] != root:
                problems.append('display name set by an account that is not root')

        ok = ok and not problems
        rows.append({'seq': event['seq'], 'op': op, 'account': event.get('account'),
                     'at': event.get('at'), 'strength': event.get('strength'),
                     'ok': not problems, 'problems': problems})

    return {'id': id, 'ok': ok, 'events': len(events), 'accounts': sorted(present),
            'root': root, 'live': live, 'checked': rows,
            'means': ('every signature in the log was re-verified from the stored '
                      'statement, offline. Publication proofs were '
                      + ('re-fetched.' if live else 'not re-fetched — pass live=true.'))}


# ── portability ──────────────────────────────────────────────────────────

def export(id: str) -> Dict[str, Any]:
    """Everything needed to re-check this identity somewhere else, and nothing more."""
    id = store.follow(id)
    doc = document(id)
    doc['events_log'] = store.events(id)
    doc['exported_at'] = time.time()
    doc['verify_with'] = 'm id/import file=<this file> — every proof is re-checked'
    return doc


def import_document(doc: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    """Take an identity from another host, and believe none of it until it checks."""
    if doc.get('protocol') != PROTOCOL:
        raise IdError(f'not a {PROTOCOL} document')
    id = doc.get('id')
    events = doc.get('events_log') or []
    if not id or not events:
        raise IdError('document carries no identity log')
    if store.exists(id) and not overwrite:
        raise IdError(f'{id} already exists here — pass overwrite=true to replace it '
                      'with the imported log')

    failures = []
    for event in events:
        for proof in event.get('proofs', []):
            verdict = recheck_proof(proof)
            if verdict['ok'] is False:
                failures.append({'seq': event.get('seq'), 'account': proof.get('account'),
                                 'why': verdict['why']})
    if failures:
        raise IdError(f'refused: {len(failures)} proof(s) in that document do not '
                      f'verify — {failures[:3]}')

    store.ensure()
    path = store.log_path(id)
    with path.open('w') as out:
        for event in events:
            out.write(json.dumps(event, sort_keys=True) + '\n')
    state = _state(id)
    data = store.index()
    for account in state['accounts']:
        data['accounts'][account] = id
    store.save_index(data)
    return {'ok': True, 'id': id, 'imported': len(events),
            'accounts': sorted(state['accounts']),
            'note': 'every signature was re-verified before anything was written'}


def rebuild() -> Dict[str, Any]:
    """Throw the index away and recompute it from the logs. Nothing is lost."""
    data = {'accounts': {}, 'aliases': {}, 'names': {}}
    for id in store.ids():
        state = _state(id)
        if state['merged_into']:
            data['aliases'][id] = state['merged_into']
    for id in store.ids():
        state = _state(id)
        if state['merged_into']:
            continue
        for account in state['accounts']:
            data['accounts'][account] = id
        if state['name']:
            data['names'][id] = state['name']
    store.save_index(data)
    return {'ok': True, **store.stats(),
            'note': 'the index is a cache of the logs — this proves it'}
