"""
Blobs — checking that content-addressed bytes still hash to their own names.

A store that files bytes under the SHA-256 of themselves is making a promise:
the name is a proof. Nothing enforces it. The hash is computed once, at write
time, by the process doing the writing, and from then on the filename is taken
on faith by everything that reads it — including wasmland's verifier, which
replays an artifact and attests to the result. If the bytes under an id ever
stop hashing to that id, every receipt that cites it is attesting to something
other than what it ran, and nothing in the fleet would notice.

So this file recomputes them. That is the whole idea: the one check a
content-addressed store cannot skip is the one that reads the bytes back and
does the arithmetic again.

It also answers the two adjacent questions, because both turned out to be real
here. Blobs live at the shared `blobs/` prefix, deliberately outside any
module's namespace — bytes under their own hash belong to nobody. But an
earlier layout wrote them to `wasmland/blobs/` instead, and those copies are
still on disk: same bytes, two homes, one of them unreferenced. `strays()`
finds that shape, and `orphans()` finds ids that no record mentions at all.

Neither deletes anything. They report, and `m shelf/gc` will show you the
bytes it would reclaim; taking them is a separate verb with `confirm=True`,
because a garbage collector that is wrong once, unsupervised, is a data-loss
incident rather than a tidy-up.
"""
import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .keys import _resolve, path2key

HEX64 = re.compile(r'^[0-9a-f]{64}$')


def classify(path: str) -> Tuple[str, Optional[bytes]]:
    """What kind of content-addressed file this is, and the bytes it stands for.

    The distinction matters more than it looks. A directory of hex-named files
    can hold two different things, and checking one as if it were the other
    invents corruption that is not there — the first version of this file
    rehashed `wasmland/artifacts/<id>.json` and reported six healthy records as
    damaged, which is the failure mode an integrity checker can least afford.

        blob    `{"b64": ...}` or raw bytes. The name is a claim about the
                payload, and rehashing it either proves the claim or breaks it.
        record  a JSON object filed *under* a blob's id — metadata describing
                it. Its bytes were never supposed to hash to its name; the
                claim it makes is `doc['id'] == filename`, which is a different
                and much weaker one.

    Records are unwrapped by payload, never by envelope, so re-serialising the
    same bytes with different whitespace never looks like damage.
    """
    try:
        raw = open(path, 'rb').read()
    except OSError:
        return 'unreadable', None
    if not path.endswith('.json'):
        return 'blob', raw
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return 'blob', raw            # hex-named non-JSON is bytes
    if isinstance(doc, dict) and 'b64' in doc:
        try:
            return 'blob', base64.b64decode(doc['b64'])
        except Exception:
            return 'unreadable', None
    if isinstance(doc, dict):
        return 'record', raw
    return 'blob', raw


def _addressed_files(root: str):
    """Yield every hex64-named file under the root. Names, not directories.

    Judged per file rather than per folder because a folder is not a reliable
    signal — `wasmland/artifacts/` and `blobs/` look identical from the outside
    and hold different things.
    """
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            claimed = name.split('.')[0]
            if not HEX64.match(claimed):
                continue
            path = os.path.join(dirpath, name)
            if os.path.isfile(path):
                yield path, claimed


def verify(root: Optional[str] = None, limit: int = 0) -> Dict[str, Any]:
    """Rehash every content-addressed file and report the ones that disagree.

    `ok` counts blobs that still prove their own names. `corrupt` is the list
    that does not, and an entry there is a fact about this box that somebody
    needs to act on today: every receipt citing that id attested to bytes other
    than the ones now filed under it.

    `misfiled` is the softer sibling — a record whose `id` field disagrees with
    the name it is stored under. Not corruption, but the two will be read by
    different lookups and one of them is wrong.
    """
    root = _resolve(root)
    checked = ok = records = 0
    corrupt: List[Dict[str, Any]] = []
    misfiled: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    by_id: Dict[str, List[str]] = {}

    for path, claimed in _addressed_files(root):
        if limit and checked >= limit:
            break
        checked += 1
        kind, data = classify(path)
        key = path2key(root, path)

        if kind == 'unreadable' or data is None:
            unreadable.append(key)
            continue

        if kind == 'record':
            records += 1
            try:
                stated = (json.loads(data) or {}).get('id')
            except (ValueError, TypeError):
                stated = None
            if stated and stated != claimed:
                misfiled.append({'key': key, 'filed_under': claimed, 'says': stated})
            continue

        by_id.setdefault(claimed, []).append(key)
        actual = hashlib.sha256(data).hexdigest()
        if actual == claimed:
            ok += 1
        else:
            corrupt.append({'key': key, 'claimed': claimed, 'actual': actual,
                            'bytes': len(data)})

    duplicates = {bid: paths for bid, paths in by_id.items() if len(paths) > 1}
    return {
        'root': root,
        'checked': checked, 'blobs': ok + len(corrupt), 'ok': ok,
        'records': records,
        'corrupt': corrupt, 'misfiled': misfiled, 'unreadable': unreadable,
        'duplicates': duplicates,
        'healthy': not corrupt and not unreadable,
    }


def _referenced(root: str, skip: Set[str]) -> Dict[str, int]:
    """Every 64-hex id mentioned anywhere in the root's records.

    Deliberately crude: it reads records as text and pulls out hashes with a
    regex rather than knowing any module's schema. A garbage collector that has
    to be taught each new record shape is a garbage collector that eventually
    deletes a live blob because somebody added a field it had not been told
    about. Text is the schema every module already agrees on.
    """
    seen: Dict[str, int] = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            # A blob does not cite itself, and its base64 payload is a haystack
            # of characters that a hex regex should never be turned loose on.
            if path in skip:
                continue
            try:
                if os.path.getsize(path) > 4_000_000:
                    continue
                body = open(path, 'r', encoding='utf-8', errors='ignore').read()
            except OSError:
                continue
            for match in re.findall(r'[0-9a-f]{64}', body):
                seen[match] = seen.get(match, 0) + 1
    return seen


def orphans(root: Optional[str] = None) -> Dict[str, Any]:
    """Blobs that no record refers to, and the bytes they are holding.

    An orphan is not proof of garbage — a blob can be written before the record
    that will cite it, and a few seconds of that race is normal — so every
    entry carries its age, and `gc` refuses to touch anything young.
    """
    root = _resolve(root)
    # Only actual blobs can be orphaned. A record filed under an id is itself a
    # reference to that id, so records stay in the haystack, not the needles.
    blob_paths = {path for path, _cid in _addressed_files(root)
                  if classify(path)[0] == 'blob'}
    refs = _referenced(root, skip=blob_paths)

    now = time.time()
    found, total = [], 0
    for path in sorted(blob_paths):
        bid = os.path.basename(path).split('.')[0]
        if refs.get(bid):
            continue
        st = os.stat(path)
        total += st.st_size
        found.append({'id': bid, 'key': path2key(root, path),
                      'bytes': st.st_size,
                      'age_days': round((now - st.st_mtime) / 86400, 2)})
    found.sort(key=lambda r: r['bytes'], reverse=True)
    return {'root': root, 'orphans': found, 'count': len(found),
            'reclaimable_bytes': total}


def strays(root: Optional[str] = None) -> Dict[str, Any]:
    """The same bytes filed in more than one place.

    Content addressing makes this detectable and makes it safe to fix: two
    files whose names are the same hash *are* the same bytes, so one of them is
    redundant by definition. The shared `blobs/` prefix is the canonical home;
    a copy anywhere else is a leftover from a layout that has since moved.
    """
    report = verify(root)
    canonical_prefix = 'blobs/'
    out = []
    for bid, paths in report['duplicates'].items():
        canonical = [p for p in paths if p.startswith(canonical_prefix)]
        extra = [p for p in paths if not p.startswith(canonical_prefix)]
        if canonical and extra:
            out.append({'id': bid, 'canonical': canonical[0], 'copies': extra})
    return {'root': report['root'], 'strays': out, 'count': len(out),
            'note': 'copies outside the shared blobs/ prefix; the canonical one is kept'}


def gc(root: Optional[str] = None, confirm: bool = False,
       min_age_days: float = 1.0) -> Dict[str, Any]:
    """Report — or, with `confirm=True`, delete — orphaned blobs.

    Dry by default, and age-gated even when confirmed, because the one thing
    worse than a full disk is a garbage collector that won a race against the
    writer that was about to reference the blob it just deleted.
    """
    report = orphans(root)
    stale = [o for o in report['orphans'] if o['age_days'] >= min_age_days]
    young = len(report['orphans']) - len(stale)
    plan = {'root': report['root'], 'candidates': stale, 'count': len(stale),
            'skipped_young': young, 'min_age_days': min_age_days,
            'bytes': sum(o['bytes'] for o in stale), 'deleted': False}
    if not confirm:
        plan['note'] = 'dry run — pass confirm=True to delete'
        return plan

    removed, failed = [], []
    for entry in stale:
        path = os.path.join(_resolve(root), entry['key'])
        for candidate in (path, path + '.json'):
            if os.path.isfile(candidate):
                try:
                    os.remove(candidate)
                    removed.append(entry['key'])
                except OSError as exc:
                    failed.append({'key': entry['key'], 'error': str(exc)})
                break
    plan.update({'deleted': True, 'removed': removed, 'failed': failed})
    return plan
