"""
Snapshots — a module's state, frozen under a name that proves what it holds.

The fleet already has a content-addressed pin store (`localfs`), and half the
modules here already speak CIDs. What nothing does is take the state directory
those modules write into and put *it* under a CID, which is the difference
between "I have a backup somewhere" and "this exact tree, and here is the name
that proves it".

A snapshot is a deterministic tar of one root, pinned to localfs. Deterministic
matters: entries are sorted, and the mtimes, uids and permissions that vary
between machines are zeroed, so tarring the same tree twice gives the same
bytes and therefore the same CID. Two boxes with identical state produce one
id, and a snapshot that has not changed does not consume a second pin.

Secret files are excluded, not redacted. A snapshot is bytes leaving this
directory — potentially to a pinning service, certainly to any mod that can
read a CID — and a redacted secret is still a decision about how much of a
secret to hand over. Excluding is the only answer that stays right when the
snapshot is copied somewhere this module cannot see. What is skipped is listed
in the manifest, so a restore knows exactly what it will not be getting back.
"""
import gzip
import io
import os
import tarfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import protocol, redact
from .keys import _resolve


def _localfs():
    """The CID pinner, if this box has one. Optional by design — every other
    verb in this module works without it.

    Resolved through `src/protocol.py` rather than a bare `import mod`: the
    server puts this module's root on sys.path, where our own mod.py shadows
    the protocol package and turns a working pin into a silent "unavailable".
    """
    return protocol.mod('localfs')


# The pinned object is a JSON envelope, not the tarball itself. localfs is not
# binary-safe: `get(put(raw_bytes))` comes back as a `str` that has been decoded
# with a lossy codec, and no encoding recovers the original — a restored
# snapshot would be quietly corrupt, which is the worst way for a backup to
# fail. Base64 inside JSON is the same shape wasmland already uses for blobs,
# and it survives the round trip exactly.
ENVELOPE = 'shelf/snapshot/1'


def _wrap(blob: bytes, meta: Dict[str, Any]) -> Dict[str, Any]:
    import base64
    return {'format': ENVELOPE, 'b64': base64.b64encode(blob).decode(), **meta}


def _unwrap(doc: Any) -> Optional[bytes]:
    """The tarball inside an envelope, or None if this is not one of ours."""
    import base64
    if isinstance(doc, (bytes, bytearray)):     # a store that *is* binary-safe
        return bytes(doc)
    if not isinstance(doc, dict) or 'b64' not in doc:
        return None
    try:
        return base64.b64decode(doc['b64'])
    except Exception:
        return None


def _members(root: str) -> Tuple[List[str], List[str]]:
    """(files to include, keys skipped for being secret), both sorted."""
    include, skipped = [], []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            if redact.sensitive_file(path):
                skipped.append(os.path.relpath(path, root))
            else:
                include.append(path)
    return sorted(include), sorted(skipped)


def _tarball(root: str) -> Tuple[bytes, Dict[str, Any]]:
    """A byte-identical tar for a byte-identical tree.

    Written in two steps on purpose. `tarfile.open(mode='w:gz')` stamps the
    current time into the gzip header, which would give the same tree a
    different CID on every call and defeat the entire point; so the tar is
    built uncompressed and gzipped separately with `mtime=0`.
    """
    include, skipped = _members(root)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for path in include:
            # The real relative path, not the store key. `path2key` strips the
            # `.json` a key never carries — correct for addressing the store,
            # silently lossy for a backup, which restored `registry.json` as
            # `registry`. A snapshot copies files, so it keeps filenames.
            info = tar.gettarinfo(path, arcname=os.path.relpath(path, root))
            # Everything that varies between machines but says nothing about
            # the contents is zeroed: same tree, same bytes, same name.
            info.mtime, info.uid, info.gid = 0, 0, 0
            info.uname, info.gname = '', ''
            info.mode = 0o644
            with open(path, 'rb') as handle:
                tar.addfile(info, handle)

    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', compresslevel=6, mtime=0) as zipped:
        zipped.write(raw.getvalue())
    return packed.getvalue(), {'files': len(include), 'skipped': skipped}


def create(root: Optional[str] = None, pin: bool = True) -> Dict[str, Any]:
    """Snapshot a root. Returns the CID if localfs is here, the bytes' hash if not."""
    import hashlib
    root = _resolve(root)
    if not os.path.isdir(root):
        return {'root': root, 'ok': False, 'error': 'no such root'}

    blob, meta = _tarball(root)
    digest = hashlib.sha256(blob).hexdigest()
    out = {
        'root': root, 'ok': True, 'sha256': digest, 'bytes': len(blob),
        'files': meta['files'], 'skipped_secrets': meta['skipped'],
        'created': time.time(), 'cid': None,
    }

    if pin:
        pinner = _localfs()
        if pinner is None:
            out['note'] = 'localfs unavailable — snapshot hashed but not pinned'
        else:
            try:
                # Deliberately no timestamp in the envelope: a `created` field
                # would make every snapshot of an unchanged tree a new CID and
                # quietly undo the determinism the tar works to preserve.
                out['cid'] = pinner.put(_wrap(blob, {
                    'root': os.path.basename(root.rstrip('/')),
                    'sha256': digest, 'files': meta['files'],
                    'skipped_secrets': meta['skipped'],
                }))
            except Exception as exc:
                out['note'] = f'pin failed: {exc}'
    return out


def _fetch(cid: str) -> Tuple[Optional[tarfile.TarFile], Optional[str]]:
    """(open tarball, error). One fetch path for both restore and inspect."""
    pinner = _localfs()
    if pinner is None:
        return None, 'localfs unavailable — cannot fetch a CID'
    try:
        doc = pinner.get(cid)
    except Exception as exc:
        return None, f'cannot fetch {cid}: {exc}'
    blob = _unwrap(doc)
    if blob is None:
        return None, f'{cid} is not a shelf snapshot'
    try:
        return tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz'), None
    except tarfile.TarError as exc:
        return None, f'not a snapshot: {exc}'


def restore(cid: str, root: Optional[str] = None, confirm: bool = False,
            overwrite: bool = False) -> Dict[str, Any]:
    """Unpack a snapshot back into a root.

    Dry by default: it fetches, opens and lists what it would write, and only
    a second call with `confirm=True` touches the disk. Existing files are left
    alone unless `overwrite=True`, so the common case — restoring what was lost
    without clobbering what survived — is the default one.
    """
    root = _resolve(root)
    tar, error = _fetch(cid)
    if error:
        return {'ok': False, 'error': error}

    planned, conflicts, unsafe = [], [], []
    for member in tar.getmembers():
        if not member.isfile():
            continue
        target = os.path.normpath(os.path.join(root, member.name))
        # A tar can name `../../.ssh/authorized_keys`. Anything that lands
        # outside the root is dropped, not sanitised: a snapshot that tries is
        # not one to be clever about.
        if not (target == root or target.startswith(os.path.realpath(root) + os.sep)
                or target.startswith(root + os.sep)):
            unsafe.append(member.name)
            continue
        (conflicts if os.path.exists(target) else planned).append(member.name)

    plan = {'ok': True, 'cid': cid, 'root': root, 'would_write': len(planned),
            'conflicts': conflicts, 'unsafe': unsafe, 'written': 0,
            'restored': False}
    if not confirm:
        plan['note'] = 'dry run — pass confirm=True to write'
        return plan

    written = 0
    for member in tar.getmembers():
        if not member.isfile() or member.name in unsafe:
            continue
        target = os.path.normpath(os.path.join(root, member.name))
        if os.path.exists(target) and not overwrite:
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            continue
        with open(target, 'wb') as handle:
            handle.write(source.read())
        written += 1
    plan.update({'restored': True, 'written': written})
    return plan


def inspect(cid: str) -> Dict[str, Any]:
    """What is in a snapshot, without writing any of it."""
    tar, error = _fetch(cid)
    if error:
        return {'ok': False, 'error': error}
    members = [{'key': m.name, 'bytes': m.size} for m in tar.getmembers() if m.isfile()]
    members.sort(key=lambda r: r['bytes'], reverse=True)
    return {'ok': True, 'cid': cid, 'files': len(members),
            'bytes': sum(m['bytes'] for m in members), 'entries': members[:200]}
