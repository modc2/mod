"""store — attacks, defenses and match records on disk under ~/.mod/rvb.

Four directories and no database. An attack and a defense are documents you
edit and re-run, so they are files you can read. A round is a receipt — what
was fired at what, what came back, and how it was judged — so it is a file you
can keep and argue with.

    ~/.mod/rvb/attacks/<id>.json      red team: one prompt, one goal
    ~/.mod/rvb/defenses/<id>.json     blue team: one pipeline
    ~/.mod/rvb/rounds/<id>.json       one tournament, written as it goes
    ~/.mod/rvb/server.secret          optional bearer for the write routes

None of this is committed. A defense's system prompt is the blue team's
working answer to an open attack surface, and an attack corpus is a list of
things that got through — both belong in the operator's home directory, not in
a config file in a git repository.
"""

import json
import os
import re
import time
import uuid

DIR = os.environ.get('RVB_DIR', os.path.expanduser('~/.mod/rvb'))
ATTACKS = os.path.join(DIR, 'attacks')
DEFENSES = os.path.join(DIR, 'defenses')
ROUNDS = os.path.join(DIR, 'rounds')
KEEP = int(os.environ.get('RVB_KEEP_ROUNDS', 300))
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')

KINDS = {'attack': ATTACKS, 'defense': DEFENSES, 'round': ROUNDS}


class StoreError(Exception):
    pass


def _ensure():
    for d in KINDS.values():
        os.makedirs(d, exist_ok=True)


def check_id(value, what='id'):
    value = str(value or '').strip()
    if not ID.match(value):
        raise StoreError(f'{value!r} is not a usable {what} — letters, digits, '
                         '. _ - and at most 64 characters')
    return value


def slug(text, prefix=''):
    """A readable id from a name. Collisions get a suffix, never an overwrite."""
    base = re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')[:40]
    base = (prefix + base) if base else (prefix + uuid.uuid4().hex[:8])
    return base


def _write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, default=str, indent=2)
    os.replace(tmp, path)


def _dir(kind):
    try:
        return KINDS[kind]
    except KeyError:
        raise StoreError(f'no such kind {kind!r} — one of {", ".join(KINDS)}')


def path_of(kind, ident):
    return os.path.join(_dir(kind), check_id(ident) + '.json')


def exists(kind, ident):
    return os.path.isfile(path_of(kind, ident))


def unique_id(kind, wanted):
    """Never silently replace somebody else's artifact."""
    ident = check_id(wanted)
    if not exists(kind, ident):
        return ident
    for n in range(2, 500):
        candidate = f'{ident}-{n}'
        if not exists(kind, candidate):
            return candidate
    return f'{ident}-{uuid.uuid4().hex[:6]}'


def put(kind, record):
    _ensure()
    ident = check_id(record.get('id'))
    record = dict(record, id=ident)
    record.setdefault('created', int(time.time()))
    record['updated'] = int(time.time())
    _write(path_of(kind, ident), record)
    return record


def get(kind, ident):
    try:
        with open(path_of(kind, ident)) as f:
            return json.load(f)
    except FileNotFoundError:
        raise StoreError(f'no {kind} {ident!r} — list them to see what exists')
    except json.JSONDecodeError as e:
        raise StoreError(f'{kind} {ident!r} is not readable json: {e}')


def find(kind, ident):
    """get, but None instead of raising."""
    try:
        return get(kind, ident)
    except StoreError:
        return None


def delete(kind, ident):
    path = path_of(kind, ident)
    if not os.path.isfile(path):
        raise StoreError(f'no {kind} {ident!r}')
    os.remove(path)
    return {'deleted': ident, 'kind': kind}


def listing(kind, limit=200, **match):
    """Every record of a kind, newest first, optionally filtered by field."""
    _ensure()
    out = []
    for name in os.listdir(_dir(kind)):
        if not name.endswith('.json'):
            continue
        try:
            with open(os.path.join(_dir(kind), name)) as f:
                rec = json.load(f)
        except Exception:
            continue
        if any(rec.get(k) != v for k, v in match.items() if v is not None):
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get('created', 0), reverse=True)
    return out[:int(limit)] if limit else out


def prune(keep=KEEP):
    """Drop the oldest round records. Attacks and defenses are never pruned —
    they are the corpus, and the corpus is the point."""
    _ensure()
    rounds = listing('round', limit=0)
    dropped = []
    for rec in rounds[int(keep):]:
        try:
            os.remove(path_of('round', rec['id']))
            dropped.append(rec['id'])
        except Exception:
            pass
    return {'kept': min(len(rounds), int(keep)), 'dropped': len(dropped),
            'ids': dropped}


def counts():
    _ensure()
    return {k: len([n for n in os.listdir(d) if n.endswith('.json')])
            for k, d in KINDS.items()}


def secret():
    try:
        with open(os.path.join(DIR, 'server.secret')) as f:
            return f.read().strip() or None
    except Exception:
        return None
