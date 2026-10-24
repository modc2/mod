"""library — the shared shelf of red and blue prompts: submit, explore, edit, fork.

The corpus already had two drawers — attacks (red) and defenses (blue) — that
only the operator wrote into. The library opens them to everyone without
adding a third store: a submitted red IS an attack and a submitted blue IS a
defense, so the moment somebody submits one it can be fired in a round, a
duel or a sweep. Nothing is copied, nothing drifts.

    side      red → attacks/<id>.json     blue → defenses/<id>.json

What the library adds to a record (all optional, all on the same file):

    author        free text — a handle, a wallet, nothing. Defaults 'anon'.
    tags          a few short labels to explore by
    forked_from   {side, id, name} — the parent, so lineage is walkable
    source        operator | submitted | fork | seed | builtin
    history       the last HISTORY_KEEP versions before each edit
    edit_hash     sha256 of the submitter's edit token (the token itself is
                  handed back exactly once and never stored)

WHO MAY DO WHAT, AND WHY IT NEEDS NO ACCOUNTS
    explore / read   anyone
    submit / fork    anyone (throttled per caller) — a fork is a new record
                     with its own author, so forking never touches the parent
    edit / relabel / delete
                     the operator (the server.secret bearer, or anyone when no
                     secret is set — a local box), OR whoever holds that
                     record's edit token. The console keeps the tokens it was
                     handed in localStorage, so "mine" works with no login
                     and no third party; lose the token and only the operator
                     can change it. Built-ins are never editable — fork them.

Relabelling (red ⇄ blue) moves the text across: a red's prompt becomes a
blue's system prompt and vice versa. A fork may relabel too (`as_side`), which
is the usual way: fork somebody's red, label it blue, and you have a defense
written against exactly that attack's framing.
"""

import hashlib
import hmac
import secrets
import threading
import time

from . import builtins as bimod, corpus, defense as defmod, store

SIDES = {'red': 'attack', 'blue': 'defense'}
KIND_SIDE = {v: k for k, v in SIDES.items()}
HISTORY_KEEP = 20
MAX_TEXT = 20000
MAX_NAME = 120
MAX_TAGS = 8
MAX_TAG = 32
# Open writes (submit / fork / token edits) per caller per window.
RATE_N, RATE_WINDOW = 30, 600

PRIVATE = ('edit_hash',)
SEED_IDS = {a['id'] for a in corpus.SEED_ATTACKS}


class LibraryError(store.StoreError):
    """A caller's mistake — same family as the store's, so every surface
    already turns it into a 400 / an MCP isError."""


class Forbidden(LibraryError):
    pass


# ── small helpers ────────────────────────────────────────────────

def _side(side):
    s = str(side or '').strip().lower()
    if s in ('attack', 'attacks'):
        s = 'red'
    if s in ('defense', 'defenses', 'defence'):
        s = 'blue'
    if s not in SIDES:
        raise LibraryError(f'side must be red or blue, not {side!r}')
    return s


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _clean_tags(tags):
    if isinstance(tags, str):
        tags = tags.split(',')
    out = []
    for t in tags or []:
        t = str(t).strip().lower().lstrip('#')[:MAX_TAG]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _clip(text, what, limit):
    text = '' if text is None else str(text)
    if len(text) > limit:
        raise LibraryError(f'{what} is {len(text)} characters — the cap is {limit}')
    return text


def _turns_text(turns):
    return '\n\n'.join(f"{t.get('role', 'user')}: {t.get('content', '')}"
                       for t in turns or [])


def text_of(rec, side):
    if side == 'red':
        return rec.get('prompt') or _turns_text(rec.get('turns'))
    return rec.get('system_prompt') or ''


def _source(rec, side):
    if rec.get('builtin'):
        return 'builtin'
    if rec.get('source'):
        return rec['source']
    if side == 'red' and rec.get('id') in SEED_IDS:
        return 'seed'
    return 'operator'


def public(rec):
    """A record as anyone may see it — never the edit hash."""
    return {k: v for k, v in rec.items() if k not in PRIVATE}


_hits = {}
_hits_lock = threading.Lock()


def throttle(caller):
    """At most RATE_N open writes per caller per RATE_WINDOW seconds."""
    if not caller:
        return
    now = time.time()
    with _hits_lock:
        recent = [t for t in _hits.get(caller, []) if now - t < RATE_WINDOW]
        if len(recent) >= RATE_N:
            raise LibraryError(f'slow down — {RATE_N} submissions per '
                               f'{RATE_WINDOW // 60} minutes per caller')
        recent.append(now)
        _hits[caller] = recent


# ── reading ──────────────────────────────────────────────────────

def _load(side, ident):
    if side == 'blue' and ident in bimod.BUILTIN:
        return dict(bimod.BUILTIN[ident])
    return store.get(SIDES[side], ident)


def _all(side):
    recs = store.listing(SIDES[side], limit=0)
    if side == 'blue':
        recs = [dict(d) for d in bimod.BUILTIN_DEFENSES] + recs
    return recs


def _scores(rounds=20):
    """Latest standings keyed by id — red breach rate, blue safety score."""
    try:
        board = bimod.board_across(rounds)
    except Exception:                                   # noqa: BLE001
        return {}, {}
    red = {r['attack']: r for r in board.get('red') or []}
    blue = {b['defense']: b for b in board.get('blue') or []}
    return red, blue


def _card(rec, side, forks, red_sc, blue_sc, full=False):
    text = text_of(rec, side)
    parent = rec.get('forked_from')
    card = {
        'id': rec.get('id'), 'side': side, 'kind': SIDES[side],
        'name': rec.get('name') or rec.get('id'),
        'author': rec.get('author') or ('system' if rec.get('builtin') else 'anon'),
        'tags': rec.get('tags') or [],
        'source': _source(rec, side),
        'forked_from': parent,
        'forks': forks.get((side, rec.get('id')), 0),
        'versions': len(rec.get('history') or []) + 1,
        'created': rec.get('created'), 'updated': rec.get('updated'),
        'builtin': bool(rec.get('builtin')),
        'has_token': bool(rec.get('edit_hash')),
        'text': text if full else text[:400],
        'truncated': (not full) and len(text) > 400,
    }
    if side == 'red':
        card.update(goal=rec.get('goal'), category=rec.get('category'),
                    technique=rec.get('technique'),
                    multi_turn=bool(rec.get('turns')))
        s = red_sc.get(rec.get('id'))
        card['score'] = {'breach_rate': s['breach_rate'], 'fired': s['fired']} \
            if s else None
    else:
        card.update(description=rec.get('description'),
                    rules=len(rec.get('input_rules') or [])
                    + len(rec.get('output_rules') or []),
                    self_check=bool(rec.get('self_check')))
        s = blue_sc.get(rec.get('id'))
        card['score'] = {'safety_score': s['safety_score'],
                         'rounds': s['rounds']} if s else None
    return card


def _fork_counts(records):
    counts = {}
    for side, rec in records:
        p = rec.get('forked_from') or {}
        if p.get('id'):
            key = (p.get('side'), p['id'])
            counts[key] = counts.get(key, 0) + 1
    return counts


def explore(side='all', q=None, tag=None, author=None, source=None,
            sort='new', limit=200, ids=None):
    """Every red and blue on the shelf, as cards — filter, search, sort.

    `ids` is a list of "side:id" strings (the console's "mine" view: the
    records whose edit tokens this browser holds)."""
    sides = list(SIDES) if str(side or 'all') == 'all' else [_side(side)]
    records = [(s, r) for s in SIDES for r in _all(s)]
    forks = _fork_counts(records)
    red_sc, blue_sc = _scores()
    words = [w for w in str(q or '').lower().split() if w]
    want = set(ids) if ids else None
    cards, counts = [], {'red': 0, 'blue': 0, 'all': 0}
    for s, rec in records:
        if want is not None and f'{s}:{rec.get("id")}' not in want:
            continue
        if tag and tag.lower().lstrip('#') not in (rec.get('tags') or []):
            continue
        if author and str(author).lower() not in \
                str(rec.get('author') or 'anon').lower():
            continue
        if source and _source(rec, s) != source:
            continue
        if words:
            hay = ' '.join(str(x) for x in (
                rec.get('id'), rec.get('name'), text_of(rec, s), rec.get('goal'),
                rec.get('description'), rec.get('author'), rec.get('technique'),
                rec.get('category'), ' '.join(rec.get('tags') or []))).lower()
            if not all(w in hay for w in words):
                continue
        counts[s] += 1
        counts['all'] += 1
        if s in sides:
            cards.append(_card(rec, s, forks, red_sc, blue_sc))

    def score_key(c):
        sc = c['score'] or {}
        return sc.get('breach_rate', sc.get('safety_score', -1))

    order = {'new': lambda c: c.get('updated') or c.get('created') or 0,
             'forks': lambda c: (c['forks'], c.get('updated') or 0),
             'score': score_key,
             'name': lambda c: str(c['name']).lower()}
    key = order.get(sort, order['new'])
    cards.sort(key=key, reverse=(sort != 'name'))
    tags = {}
    for _, rec in records:
        for t in rec.get('tags') or []:
            tags[t] = tags.get(t, 0) + 1
    limit = int(limit or 0)
    return {'items': cards[:limit] if limit else cards, 'counts': counts,
            'tags': sorted(tags.items(), key=lambda kv: -kv[1])[:40],
            'side': side or 'all', 'sort': sort}


def item(side, ident):
    """One record in full, with its lineage up and its forks down."""
    side = _side(side)
    rec = _load(side, ident)
    records = [(s, r) for s in SIDES for r in _all(s)]
    forks = _fork_counts(records)
    red_sc, blue_sc = _scores()
    card = _card(rec, side, forks, red_sc, blue_sc, full=True)
    card['record'] = public(rec)
    card['history'] = [dict(h) for h in reversed(rec.get('history') or [])]
    # walk up — parents can be deleted, so a missing one ends the walk
    lineage, seen, p = [], set(), rec.get('forked_from')
    while p and p.get('id') and (p.get('side'), p['id']) not in seen and \
            len(lineage) < 20:
        seen.add((p.get('side'), p['id']))
        try:
            parent = _load(_side(p.get('side')), p['id'])
        except store.StoreError:
            lineage.append(dict(p, missing=True))
            break
        lineage.append({'side': p.get('side'), 'id': p['id'],
                        'name': parent.get('name'),
                        'author': parent.get('author') or 'anon'})
        p = parent.get('forked_from')
    card['lineage'] = lineage
    card['children'] = [
        {'side': s, 'id': r.get('id'), 'name': r.get('name'),
         'author': r.get('author') or 'anon'}
        for s, r in records
        if (r.get('forked_from') or {}).get('id') == rec.get('id')
        and (r.get('forked_from') or {}).get('side') == side]
    return card


# ── writing ──────────────────────────────────────────────────────

def _fields_for(side, a, base=None):
    """The side-specific record body from a submission/edit payload.

    `text` is the one field both sides share: a red's prompt, a blue's system
    prompt. Side-specific fields pass through when given."""
    rec = dict(base or {})
    if 'name' in a and a['name'] is not None:
        rec['name'] = _clip(a['name'], 'name', MAX_NAME).strip()
    if 'author' in a and a['author'] is not None:
        rec['author'] = _clip(a['author'], 'author', 80).strip() or 'anon'
    if 'tags' in a and a['tags'] is not None:
        rec['tags'] = _clean_tags(a['tags'])
    text = a.get('text')
    if side == 'red':
        if text is None:
            text = a.get('prompt')
        if text is not None:
            rec['prompt'] = _clip(text, 'text', MAX_TEXT)
            rec.pop('turns', None)
        if a.get('turns'):
            turns = a['turns']
            if not isinstance(turns, list):
                raise LibraryError('turns must be a [{role, content}] list')
            _clip(_turns_text(turns), 'turns', MAX_TEXT)
            rec['turns'] = turns
            rec.pop('prompt', None)
        for k in ('goal', 'category', 'technique'):
            if a.get(k) is not None:
                rec[k] = _clip(a[k], k, 400)
        if a.get('markers') is not None:
            m = a['markers']
            rec['markers'] = [s.strip() for s in m.split(',')] \
                if isinstance(m, str) else list(m)
            rec['markers'] = [s for s in rec['markers'] if s][:20]
        rec.setdefault('category', 'custom')
        rec.setdefault('markers', [])
        if not (rec.get('prompt') or '').strip() and not rec.get('turns'):
            raise LibraryError('a red needs its prompt text')
    else:
        if text is None:
            text = a.get('system_prompt')
        if text is not None:
            rec['system_prompt'] = _clip(text, 'text', MAX_TEXT)
        if a.get('description') is not None:
            rec['description'] = _clip(a['description'], 'description', 400)
        for k in ('input_rules', 'output_rules'):
            if a.get(k) is not None:
                rec[k] = a[k]
        if a.get('self_check') is not None:
            rec['self_check'] = bool(a['self_check'])
        if a.get('max_input_chars') is not None:
            rec['max_input_chars'] = int(a['max_input_chars'] or 0)
        try:
            rec = defmod.normalise(rec)
        except defmod.DefenseError as e:
            raise LibraryError(str(e))
    return rec


def _convert(rec, from_side, to_side):
    """Carry a record's body across a relabel: the text moves, the side's own
    machinery (markers / rules) does not, because it would not mean anything
    on the other side."""
    if from_side == to_side:
        return {k: v for k, v in rec.items()
                if k not in ('id', 'created', 'updated', 'history', 'edit_hash',
                             'builtin', 'source', 'forked_from', 'author')}
    text = text_of(rec, from_side)
    keep = {'name': rec.get('name'), 'tags': rec.get('tags') or []}
    if to_side == 'blue':
        keep.update(system_prompt=text,
                    description=rec.get('goal') and f'against: {rec["goal"]}')
    else:
        keep.update(prompt=text, goal=rec.get('description'),
                    category='custom')
    return {k: v for k, v in keep.items() if v is not None}


def _mint(side, rec, author, source, caller=None, owner=False):
    if not owner:
        throttle(caller)
    kind = SIDES[side]
    rec = dict(rec)
    rec['id'] = store.unique_id(kind, rec.get('id') or
                                store.slug(rec.get('name'), '') or 'untitled')
    if side == 'blue' and rec['id'] in bimod.BUILTIN:
        rec['id'] = store.unique_id(kind, rec['id'] + '-fork')
    rec['kind'] = kind
    rec['author'] = (str(author).strip()[:80] if author else '') or 'anon'
    rec['source'] = source
    rec.pop('builtin', None)
    rec.pop('history', None)
    token = secrets.token_urlsafe(24)
    rec['edit_hash'] = _hash(token)
    saved = store.put(kind, rec)
    return {'item': item(side, saved['id']), 'edit_token': token,
            'note': 'keep edit_token — it is the only way for anyone but the '
                    'operator to edit or delete this later; it is not stored'}


def submit(a, caller=None, owner=False):
    """Put a new red or blue on the shelf. Open to anyone (throttled)."""
    side = _side(a.get('side'))
    if not str(a.get('name') or '').strip():
        raise LibraryError('give it a name')
    rec = _fields_for(side, a)
    return _mint(side, rec, a.get('author'),
                 'operator' if owner and not a.get('author') else 'submitted',
                 caller=caller, owner=owner)


def fork(a, caller=None, owner=False):
    """Copy somebody's red or blue into a new record of your own.

    as_side relabels it on the way (red → blue or blue → red). Any field given
    alongside (name, text, tags, …) is applied to the copy, so a fork can be an
    edit in one call. The parent is never touched."""
    side = _side(a.get('side'))
    src_id = a.get('id')
    if not src_id:
        raise LibraryError('fork needs the id of the record to fork')
    parent = _load(side, src_id)
    to = _side(a.get('as_side') or a.get('to') or side)
    body = _convert(parent, side, to)
    body['name'] = a.get('name') or f'{parent.get("name") or src_id} (fork)'
    patch = {k: v for k, v in a.items()
             if k not in ('side', 'id', 'as_side', 'to', 'author', 'edit_token')}
    rec = _fields_for(to, patch, base=body)
    rec['forked_from'] = {'side': side, 'id': parent.get('id'),
                          'name': parent.get('name'),
                          'author': parent.get('author') or
                          ('system' if parent.get('builtin') else 'anon')}
    return _mint(to, rec, a.get('author'), 'fork', caller=caller, owner=owner)


def _may_edit(rec, token, owner):
    if rec.get('builtin'):
        raise Forbidden('built-ins are permanent — fork it instead')
    if owner:
        return 'owner'
    h = rec.get('edit_hash')
    if token and h and hmac.compare_digest(h, _hash(token)):
        return 'token'
    raise Forbidden('not yours to change — send the edit_token you were '
                    'given at submit/fork time, or fork it instead')


def edit(a, caller=None, owner=False):
    """Change a record in place, keeping the previous version in its history.
    `relabel` (or as_side) moves it to the other side under the same id when
    that id is free there."""
    side = _side(a.get('side'))
    ident = a.get('id')
    if not ident:
        raise LibraryError('edit needs an id')
    rec = _load(side, ident)
    who = _may_edit(rec, a.get('edit_token'), owner)
    if who == 'token':
        throttle(caller)
    snapshot = {k: v for k, v in rec.items()
                if k not in ('history', 'edit_hash', 'id', 'kind', 'created')}
    snapshot['side'] = side
    snapshot['at'] = rec.get('updated') or rec.get('created')
    to = _side(a.get('relabel') or a.get('as_side') or side)
    base = rec if to == side else dict(_convert(rec, side, to),
                                       **{k: rec[k] for k in (
                                           'id', 'created', 'author', 'source',
                                           'forked_from', 'edit_hash')
                                          if k in rec})
    patch = {k: v for k, v in a.items()
             if k not in ('side', 'id', 'edit_token', 'relabel', 'as_side')}
    new = _fields_for(to, patch, base=base)
    new['history'] = ((rec.get('history') or []) + [snapshot])[-HISTORY_KEEP:]
    new['kind'] = SIDES[to]
    if to != side:
        if store.exists(SIDES[to], rec['id']):
            new['id'] = store.unique_id(SIDES[to], rec['id'])
        store.put(SIDES[to], new)
        store.delete(SIDES[side], rec['id'])
    else:
        store.put(SIDES[side], new)
    return {'item': item(to, new['id']), 'by': who,
            'relabelled': to != side}


def revert(a, caller=None, owner=False):
    """Restore version `version` (1 = oldest kept) as a new edit — reverting is
    itself an edit, so it can be reverted."""
    side = _side(a.get('side'))
    rec = _load(side, a.get('id') or '')
    _may_edit(rec, a.get('edit_token'), owner)
    hist = rec.get('history') or []
    n = int(a.get('version') or 0)
    if not 1 <= n <= len(hist):
        raise LibraryError(f'version must be 1..{len(hist)}')
    old = hist[n - 1]
    old_side = old.get('side', side)
    patch = {'side': side, 'id': rec['id'], 'edit_token': a.get('edit_token'),
             'name': old.get('name'), 'tags': old.get('tags') or [],
             'text': text_of(old, old_side)}
    if old_side == side == 'blue':
        patch.update(input_rules=old.get('input_rules') or [],
                     output_rules=old.get('output_rules') or [],
                     self_check=old.get('self_check'))
    if old_side == side == 'red' and old.get('turns'):
        patch.pop('text')
        patch['turns'] = old['turns']
    return edit(patch, caller=caller, owner=owner)


def remove(a, owner=False):
    side = _side(a.get('side'))
    rec = _load(side, a.get('id') or '')
    _may_edit(rec, a.get('edit_token'), owner)
    out = store.delete(SIDES[side], rec['id'])
    return dict(out, side=side)
