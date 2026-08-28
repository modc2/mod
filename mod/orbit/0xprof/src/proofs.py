"""
Proofs as records: publish, check, price, sell, dispute.

A proof record is three things kept apart on purpose:

    statement     what is being claimed — the verification key and the public
                  signals. Always public: nobody can price a claim they cannot
                  read, and a buyer must be able to see what they are buying
                  *before* paying for it.
    proof         the bytes that make the claim true. Gated behind the price,
                  if there is one.
    verdicts      what each method said about the two together. Always public,
                  always per-method, never averaged.

The id is the hash of (system, statement, proof), so the same proof published
twice is one record — a market where the identical bytes can be listed by two
sellers at two prices is a market with a bug, not a competitive one.

WHAT A PURCHASE ACTUALLY GUARANTEES, EXACTLY
    That this box ran the proof through every method it has, published what
    each one said, and is holding the same bytes it checked. After you buy, you
    can run the same three methods yourself on the same bytes — and if they
    stop agreeing, `POST /proofs/{id}/refund` unwinds the sale against the
    seller's balance. What it cannot guarantee is that the *statement* is one
    you care about: a perfectly valid proof of an uninteresting claim is still
    valid. Reading the statement is the buyer's job, and it is free.
"""
import re
import time
from typing import Any, Dict, List, Optional

from . import market, storage, systems, verify

SLUG = re.compile(r'[^a-z0-9-]+')
MAX_PROOF_BYTES = 4 * 1024 * 1024
CHECK_LOG = 120

# ── who made the box check, and when ─────────────────────────────────
#
# The verdicts say what each method decided. They do not say who made the box
# go and decide it, and on a market that matters: a listing nobody has looked
# at since the day it was published is a different thing from one four
# addresses re-ran this week, even when both show the same five green rows.
# So every run is logged against the address that asked for it, along with the
# signature that authorised it, and the roster is that log folded by address.
#
# A person is not a verifier — `native` is. What an address can do is put its
# name on a run, which makes the run attributable rather than true, and through
# the browser put its name on an answer that *disagrees*. That second one is
# the only thing on this page that can catch this box lying, which is why the
# roster shows it in its own column instead of averaging it in.

KINDS = {
    'published': 'published it, which ran every method once',
    'republished': 'published the same bytes again, which ran the methods again',
    'rechecked': 'signed a re-verification — the methods ran again at their ask',
    'witnessed': 'ran it in their own browser and reported what they saw',
    'refund check': 'claimed a refund, which re-runs the methods before deciding',
}


class ProofError(Exception):
    pass


def slugify(text: str) -> str:
    return SLUG.sub('-', (text or '').lower()).strip('-')[:48]


def proof_id(system: str, statement: Dict[str, Any], proof: Dict[str, Any],
             public_signals: List[Any]) -> str:
    return storage.digest({'system': system, 'statement': statement,
                           'proof': proof, 'public_signals': public_signals})


def publish(system: Optional[str], proof: Dict[str, Any],
            statement: Optional[Dict[str, Any]] = None,
            public_signals: Optional[List[Any]] = None, *,
            author: str = '', title: str = '', description: str = '',
            price: float = 0.0, tags: Optional[List[str]] = None,
            methods: Optional[List[str]] = None,
            public_proof: bool = None) -> Dict[str, Any]:
    """Take a proof in, check it with everything, and file it.

    Verification happens at publish time rather than on demand because a
    listing whose verdicts are computed when somebody looks is a listing that
    can be true at browse time and false at buy time.
    """
    if not isinstance(proof, dict) or not proof:
        raise ProofError('no proof object')
    system = systems.resolve(system) if system else systems.sniff(
        {'proof': proof, 'vkey': statement})
    statement = statement or {}
    public_signals = list(public_signals or [])
    if len(storage.canonical(proof)) > MAX_PROOF_BYTES:
        raise ProofError('that proof is larger than 4 MB — publish the artifact '
                         'to the store and list its id instead')

    record_id = proof_id(system, statement, proof, public_signals)
    existing = storage.get_record('proofs', record_id)

    outcome = verify.verify(system, proof, statement, public_signals,
                           method_names=methods, by=author)
    verdicts = verify.merge((existing or {}).get('verdicts') or [], outcome['verdicts'])
    if price and float(price) < 0:
        raise ProofError('a negative price is not a discount')

    # The listing belongs to whoever published it first. A second person with
    # the same bytes is a republisher: they are recorded, and they do not get
    # to retitle or reprice somebody else's listing out from under its buyers.
    spec = systems.get(system)
    mine = not existing or (existing.get('author') or '').lower() == (author or '').lower()
    keep = existing or {}
    record = {
        'id': record_id,
        'system': system,
        'family': spec['family'],
        'zero_knowledge': spec['zero_knowledge'],
        'title': (title or keep.get('title') if mine else keep.get('title')
                  ) or f'{spec["label"]} proof',
        'description': (description or keep.get('description') if mine
                        else keep.get('description')) or '',
        'tags': ([t.strip().lower() for t in (tags or []) if str(t).strip()][:12]
                 if mine else []) or keep.get('tags') or [],
        'author': keep.get('author') or author,
        'price': round(float((price if mine else None) or keep.get('price') or 0), 6),
        'currency': 'credits',
        'statement': statement,
        'public_signals': public_signals,
        'proof': proof,
        'public_proof': bool(public_proof if (public_proof is not None and mine)
                             else keep.get('public_proof',
                                           not float(price or 0)) if existing
                             else not float(price or 0)),
        'verdicts': verdicts,
        'created': (existing or {}).get('created') or time.time(),
        'updated': time.time(),
        'republished_by': sorted(set(((existing or {}).get('republished_by') or [])
                                     + ([author] if existing and author
                                        and author != existing.get('author') else []))),
        'sales': (existing or {}).get('sales') or 0,
        'earned': (existing or {}).get('earned') or 0.0,
        'cid': (existing or {}).get('cid'),
        'checks': (existing or {}).get('checks') or [],
    }
    record.update(verify.consensus(verdicts))
    log_check(record, by=author, kind='published' if mine else 'republished',
              methods=[v['method'] for v in outcome['verdicts']])
    record['cid'] = record['cid'] or storage.pin(storage.canonical(
        {'system': system, 'statement': statement, 'proof': proof,
         'public_signals': public_signals}).encode())
    storage.put_record('proofs', record_id, record)
    return record


def checks_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The check log, with the publish that started it guaranteed to be in it.

    Records published before the log existed have no entry for their own first
    run — but the author and the timestamp are on the record and that run
    certainly happened, so it is reconstructed rather than lost. It carries no
    signature, because it never had one.
    """
    log = list(record.get('checks') or [])
    if not any(entry.get('kind') in ('published', 'republished') for entry in log):
        if record.get('author'):
            log.insert(0, {'by': record['author'], 'kind': 'published',
                           'at': record.get('created') or record.get('updated') or 0,
                           'status': record.get('status'), 'saw': record.get('status'),
                           'agree': list(record.get('agree') or []),
                           'disagree': list(record.get('disagree') or []),
                           'methods': [v.get('method') for v in record.get('verdicts') or []
                                       if v.get('method') in verify.AUTHORITATIVE],
                           'signature': ''})
    return log


def roster(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The check log folded by address — one row per person, newest first."""
    rows: Dict[str, Dict[str, Any]] = {}
    for entry in checks_of(record):
        who = entry.get('by') or 'anonymous'
        row = rows.setdefault(who.lower(), {
            'address': who, 'checks': 0, 'signed': 0, 'witnessed': 0,
            'kinds': [], 'first': entry.get('at') or 0, 'last': 0,
            'saw': '', 'signature': '', 'contested': False})
        row['checks'] += 1
        row['signed'] += 1 if entry.get('signature') else 0
        row['witnessed'] += 1 if entry.get('kind') == 'witnessed' else 0
        if entry.get('kind') and entry['kind'] not in row['kinds']:
            row['kinds'].append(entry['kind'])
        row['first'] = min(row['first'] or entry.get('at') or 0, entry.get('at') or 0)
        row['last'] = max(row['last'], entry.get('at') or 0)
        row['saw'] = entry.get('saw') or row['saw']
        row['signature'] = entry.get('signature') or row['signature']
        # Somebody's own verifier saying invalid about a proof this box likes is
        # the loudest thing an address can say here, so it stays on their row.
        if entry.get('kind') == 'witnessed' and entry.get('saw') == 'invalid':
            row['contested'] = True
    return sorted(rows.values(), key=lambda r: r['last'], reverse=True)


def signature_spent(record: Dict[str, Any], signature: str) -> bool:
    """Has this exact signature already bought a run of the methods?

    The check log is the replay list. It has to be: a signature is valid for
    ten minutes, and without this one signed message could be posted a thousand
    times and every one of those runs would be attributed to its signer.
    """
    if not signature:
        return False
    return any(entry.get('signature') == signature for entry in record.get('checks') or [])


def log_check(record: Dict[str, Any], *, by: str, kind: str, signature: str = '',
              methods: Optional[List[str]] = None, saw: str = '') -> Dict[str, Any]:
    entry = {
        'by': by or 'anonymous',
        'kind': kind,
        'at': time.time(),
        'status': record.get('status'),
        'saw': saw or record.get('status'),
        'agree': list(record.get('agree') or []),
        'disagree': list(record.get('disagree') or []),
        'methods': list(methods or []),
        'signature': signature or '',
    }
    record['checks'] = (list(record.get('checks') or []) + [entry])[-CHECK_LOG:]
    record['verifiers'] = roster(record)
    record['last_checked'] = entry['at']
    return entry


def get(record_id: str) -> Dict[str, Any]:
    got = storage.get_record('proofs', record_id)
    if not got:
        raise ProofError(f'no proof {record_id[:16]}')
    return got


def view(record_id: str, caller: str = '') -> Dict[str, Any]:
    """The record as the caller is allowed to see it.

    The proof bytes are the only thing a price can hide, and even then their
    hash is published — so a seller cannot swap the bytes after the verdicts
    were computed without changing the id of the thing they are selling.
    """
    record = dict(get(record_id))
    record['checks'] = checks_of(record)
    record['verifiers'] = roster(record)
    allowed = record.get('public_proof') or market.entitled(
        caller, record_id, record.get('author', ''), float(record.get('price') or 0))
    record['locked'] = not allowed
    if not allowed:
        record['proof_hash'] = storage.digest(record['proof'])
        record['proof'] = {'locked': True,
                           'unlock': f'POST /proofs/{record_id}/buy',
                           'price': record['price'],
                           'sha256': record['proof_hash'],
                           'note': 'the statement and every verdict above are free '
                                   'to read; the price buys the bytes'}
    return record


def listing(record: Dict[str, Any]) -> Dict[str, Any]:
    """The short form for index pages — no proof bytes at all."""
    keep = ('id', 'system', 'family', 'title', 'description', 'tags', 'author',
            'price', 'status', 'why', 'agree', 'disagree', 'contested',
            'zero_knowledge', 'public_signals', 'created', 'updated', 'sales',
            'earned', 'cid', 'public_proof', 'last_checked')
    out = {k: record.get(k) for k in keep}
    out['methods'] = [{'method': v.get('method'), 'status': v.get('status'),
                       'ms': v.get('ms'), 'by': v.get('by'), 'at': v.get('at')}
                      for v in record.get('verdicts') or []]
    # The roster travels with the listing rather than waiting for a click: who
    # has re-run this, and whether anyone's own verifier disagreed, is exactly
    # the thing you want before you open something, not after.
    people = roster(record)
    out['verifiers'] = people[:6]
    out['verifier_count'] = len(people)
    out['checks'] = len(checks_of(record))
    return out


def search(system: str = None, status: str = None, tag: str = None,
           author: str = None, q: str = None, free: bool = None,
           for_sale: bool = None, limit: int = 100) -> List[Dict[str, Any]]:
    out = storage.records('proofs', limit=1000)
    if system:
        out = [x for x in out if x.get('system') == systems.resolve(system)]
    if status:
        out = [x for x in out if x.get('status') == status]
    if tag:
        out = [x for x in out if tag.lower() in (x.get('tags') or [])]
    if author:
        out = [x for x in out if (x.get('author') or '').lower() == author.lower()]
    if free is True:
        out = [x for x in out if not x.get('price')]
    if for_sale is True:
        out = [x for x in out if float(x.get('price') or 0) > 0]
    if q:
        needle = q.lower()
        out = [x for x in out
               if needle in (x.get('title') or '').lower()
               or needle in (x.get('description') or '').lower()
               or needle in (x.get('id') or '')
               or any(needle in t for t in x.get('tags') or [])]
    return [listing(x) for x in out[:limit]]


def recheck(record_id: str, method_names: Optional[List[str]] = None, *,
            by: str = '', signature: str = '', kind: str = 'rechecked') -> Dict[str, Any]:
    """Run the methods again — the same proof, a fresh answer, under a name.

    Worth doing when a method that was unavailable comes back (the RPC was
    down, node was not installed), and worth doing on anything expensive
    before you buy it. Verdicts accumulate; they do not replace each other
    except per method.

    A re-verification is published: it overwrites what the listing says a
    method thinks, and it goes on the record as *your* run. That is why the
    HTTP door asks for a signature over this proof's id before it gets here —
    an unattributed rewrite of somebody's listing is a thing a market should
    not offer, and an attributed one is a thing you should have to mean.
    """
    record = get(record_id)
    if signature and signature_spent(record, signature):
        raise ProofError('that signature has already been spent on this proof — '
                         'sign a fresh challenge. Letting one replay would mean '
                         'anybody holding it could keep running the methods in '
                         'your name for as long as it stayed fresh')
    outcome = verify.verify(record['system'], record['proof'], record.get('statement'),
                            record.get('public_signals'), method_names=method_names,
                            by=by)
    record['verdicts'] = verify.merge(record.get('verdicts') or [], outcome['verdicts'])
    record.update(verify.consensus(record['verdicts']))
    record['updated'] = time.time()
    log_check(record, by=by, kind=kind, signature=signature,
              methods=[v['method'] for v in outcome['verdicts']])
    storage.put_record('proofs', record_id, record)
    return record


def attest(record_id: str, by: str, ok: bool, method: str = 'browser',
           detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Record what someone else's verifier saw.

    This is a claim about a verification, not a verification, and the consensus
    layer treats it that way: it can never promote a proof, only contest one.
    Kept because the interesting failure — this box lying — is exactly the one
    its own methods cannot detect.
    """
    if method not in ('browser', 'attest'):
        raise ProofError('attestations are witness methods: browser or attest')
    record = get(record_id)
    verdict = {'method': method, 'by': by or 'anonymous', 'system': record['system'],
               'status': 'valid' if ok else 'invalid', 'ok': bool(ok),
               'at': time.time(), 'detail': detail or {}, 'authoritative': False}
    record['verdicts'] = verify.merge(record.get('verdicts') or [], [verdict])
    record.update(verify.consensus(record['verdicts']))
    record['updated'] = time.time()
    log_check(record, by=by or 'anonymous', kind='witnessed', methods=[method],
              saw=verdict['status'])
    storage.put_record('proofs', record_id, record)
    return record


def buy(record_id: str, buyer: str) -> Dict[str, Any]:
    record = get(record_id)
    receipt = market.buy(buyer, record_id, record.get('author') or '',
                         float(record.get('price') or 0), sold_as=record.get('status'))
    if receipt.get('charged'):
        record['sales'] = int(record.get('sales') or 0) + 1
        record['earned'] = round(float(record.get('earned') or 0)
                                 + float(receipt['charged']), 6)
        storage.put_record('proofs', record_id, record)
    return {**receipt, 'proof': view(record_id, buyer)}


def refund(record_id: str, buyer: str) -> Dict[str, Any]:
    """Unwind a sale of a proof that no longer verifies.

    Rechecked first, deliberately: the refund decision is made against a fresh
    run of the methods rather than against a status that was written when the
    proof was published and may be stale.
    """
    record = recheck(record_id, by=buyer, kind='refund check')
    return market.refund(buyer, record_id, record['status'], record['why'])


def unpublish(record_id: str, caller: str) -> Dict[str, Any]:
    record = get(record_id)
    if (record.get('author') or '').lower() != (caller or '').lower() and caller != 'open-mode':
        raise ProofError(f'proof {record_id[:12]} belongs to {record.get("author")}')
    if int(record.get('sales') or 0) > 0:
        raise ProofError('somebody bought this — delisting it would take away what '
                         'they paid for. Drop the price to 0 or leave it up')
    storage.drop_record('proofs', record_id)
    return {'unpublished': record_id}


def people(limit: int = 50) -> List[Dict[str, Any]]:
    """Everyone who has made this box check something, across the whole market.

    Sorted by signed re-verifications rather than by total checks, because
    publishing your own proof is not a service to anyone else and re-running
    somebody else's is. The `contested` column is the one worth reading: an
    address whose own browser has disagreed with this box is either wrong or
    the most useful person here.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for record in storage.records('proofs', limit=5000):
        for entry in checks_of(record):
            who = entry.get('by') or 'anonymous'
            row = rows.setdefault(who.lower(), {
                'address': who, 'checks': 0, 'signed': 0, 'published': 0,
                'witnessed': 0, 'contested': 0, 'proofs': [], 'last': 0})
            row['checks'] += 1
            row['signed'] += 1 if entry.get('signature') else 0
            row['published'] += 1 if entry.get('kind') in ('published', 'republished') else 0
            row['witnessed'] += 1 if entry.get('kind') == 'witnessed' else 0
            if entry.get('kind') == 'witnessed' and entry.get('saw') == 'invalid':
                row['contested'] += 1
            if record['id'] not in row['proofs']:
                row['proofs'].append(record['id'])
            row['last'] = max(row['last'], entry.get('at') or 0)
    out = sorted(rows.values(),
                 key=lambda r: (r['signed'], r['witnessed'], r['checks'], r['last']),
                 reverse=True)
    for row in out:
        row['proof_count'] = len(row['proofs'])
        row['proofs'] = row['proofs'][:12]
    return out[:limit]


def stats() -> Dict[str, Any]:
    all_proofs = storage.records('proofs', limit=5000)
    by_status: Dict[str, int] = {}
    by_system: Dict[str, int] = {}
    for record in all_proofs:
        by_status[record.get('status', 'unverified')] = by_status.get(
            record.get('status', 'unverified'), 0) + 1
        by_system[record.get('system', '?')] = by_system.get(record.get('system', '?'), 0) + 1
    return {
        'proofs': len(all_proofs),
        'by_status': by_status,
        'by_system': by_system,
        'for_sale': sum(1 for r in all_proofs if float(r.get('price') or 0) > 0),
        'volume': round(sum(float(r.get('earned') or 0) for r in all_proofs), 6),
        'disputed': [r['id'] for r in all_proofs if r.get('status') == 'disputed'][:20],
        'checks': sum(len(checks_of(r)) for r in all_proofs),
        'verifiers': len({(c.get('by') or '').lower()
                          for r in all_proofs for c in checks_of(r)}),
    }
