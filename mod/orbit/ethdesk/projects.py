"""
Contract projects: write one, keep it, share it.

A **project** is a name, one or more Solidity files, an entry contract, the
compiler settings that build it, and the test suites that check it. It is the
unit a person actually works on — `/compile` takes a blob of text, but nobody
works on a blob of text twice.

Where a project lives is the point of this file. The bytes go to the **store
module**, addressed by CID; this sqlite table is an *index*, not the storage:

    cid        what the store gave back for the current bundle. This is the
               identity of the project's content, and the thing you hand
               someone to share it.
    body       the same bundle, cached, so the console still renders your work
               when the store is asleep, refuses you, or the box is offline.
               A cache is allowed to be stale; it is never the source of truth.
    versions   every CID this project has ever had, newest first. Saving does
               not overwrite: content addressing means the old bundle is still
               there under its own CID, so history is free and free is the
               right price for "what did this look like on Tuesday".

The asymmetry with `ledger.py` is deliberate. A deployment is a fact about a
chain; a project is a draft. Drafts change, get forked, get shared, and get
deleted — so they get a CID and a version list, and deployments get neither.

Opening a shared project needs no account: `open_bundle` reads a public CID
straight out of the store with whatever token the caller had (including none).
Forking it writes a *new* project owned by the person who forked, with
`origin_cid` recording where it came from — the same way any honest fork works.
"""
import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

import ledger
from store_link import LINK, StoreError

KIND = 'eth.project/1'
MAX_FILES = 24
MAX_FILE_BYTES = 512_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  entry TEXT,
  cid TEXT,
  origin_cid TEXT,
  public INTEGER NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  note TEXT,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  UNIQUE(owner, slug)
);
CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner, updated DESC);
CREATE INDEX IF NOT EXISTS projects_cid ON projects(cid);

CREATE TABLE IF NOT EXISTS project_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  cid TEXT NOT NULL,
  size INTEGER,
  note TEXT,
  created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_project ON project_versions(project_id, created DESC);
"""


class ProjectError(Exception):
    """Something about the project itself is wrong — a 400, not a 500."""


def connect() -> sqlite3.Connection:
    conn = ledger.connect()
    conn.executescript(SCHEMA)
    return conn


# ── shapes ───────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return slug or 'contract'


def default_filename(source: str) -> str:
    """`contract Vault {` → `Vault.sol`.

    A one-file project saved from a paste has no filename, and calling every
    such file Contract.sol makes a workspace of six projects unreadable. The
    contract's own name is the one piece of information the source actually
    carries about what it is.
    """
    match = re.search(r'^\s*(?:abstract\s+)?contract\s+([A-Za-z_]\w*)',
                      source or '', re.M)
    return f'{match.group(1)}.sol' if match else 'Contract.sol'


def _clean_files(files: Any, source: Optional[str] = None,
                 entry: Optional[str] = None) -> Dict[str, str]:
    """{path: solidity} — accepting the one-file shortcut people actually use."""
    if not files and source:
        files = {entry or default_filename(source): source}
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except json.JSONDecodeError:
            raise ProjectError('`files` must be {filename: solidity}')
    if not isinstance(files, dict) or not files:
        raise ProjectError('a project needs at least one .sol file')
    if len(files) > MAX_FILES:
        raise ProjectError(f'{len(files)} files is more than a project holds '
                           f'({MAX_FILES})')
    out: Dict[str, str] = {}
    for path, text in files.items():
        path = str(path).strip().lstrip('/')
        if not path or '..' in path:
            raise ProjectError(f'{path!r} is not a filename this module writes')
        if not path.endswith('.sol'):
            path += '.sol'
        text = '' if text is None else str(text)
        if len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise ProjectError(f'{path} is larger than {MAX_FILE_BYTES} bytes')
        out[path] = text
    if not any(t.strip() for t in out.values()):
        raise ProjectError('every file in this project is empty')
    return out


def _pick_entry(files: Dict[str, str], entry: Optional[str]) -> str:
    if entry:
        entry = entry if entry.endswith('.sol') else entry + '.sol'
        if entry in files:
            return entry
    # The file with a contract in it beats the first file alphabetically: a
    # project whose entry is its interface header compiles to nothing.
    for path, text in files.items():
        if re.search(r'^\s*contract\s+\w+', text or '', re.M):
            return path
    return next(iter(files))


def bundle_of(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        body = json.loads(row.get('body') or '{}')
    except json.JSONDecodeError:
        body = {}
    return body if isinstance(body, dict) else {}


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    body = bundle_of(out)
    out['files'] = body.get('files') or {}
    out['tests'] = body.get('tests') or []
    out['settings'] = body.get('settings') or {}
    out['public'] = bool(out.get('public'))
    out.pop('body', None)
    return out


def summary(row: Dict[str, Any]) -> Dict[str, Any]:
    """A list row: everything but the source, which is the expensive part."""
    out = {k: v for k, v in row.items() if k != 'files'}
    files = row.get('files') or {}
    out['file_count'] = len(files)
    out['bytes'] = sum(len(t or '') for t in files.values())
    out['contracts'] = contract_names(files)
    return out


def contract_names(files: Dict[str, str]) -> List[str]:
    """Every `contract X` declared, in declaration order, without compiling.

    A regex, not a parser — this is for a dropdown label, and the compiler is
    the thing that gets to be authoritative about what is deployable.
    """
    names: List[str] = []
    for text in (files or {}).values():
        for match in re.finditer(r'^\s*contract\s+([A-Za-z_]\w*)', text or '', re.M):
            if match.group(1) not in names:
                names.append(match.group(1))
    return names


def make_bundle(name: str, files: Dict[str, str], entry: str,
                tests: Optional[List[dict]] = None,
                settings: Optional[dict] = None, author: Optional[str] = None,
                note: Optional[str] = None,
                origin_cid: Optional[str] = None) -> Dict[str, Any]:
    """The JSON object that goes to the store. This *is* the project."""
    return {
        'kind': KIND,
        'name': name,
        'entry': entry,
        'files': files,
        'tests': tests or [],
        'settings': settings or {},
        'author': author,
        'note': note,
        'origin_cid': origin_cid,
        'written': int(time.time()),
        'by': 'orbit/eth',
    }


def read_bundle(payload: Any) -> Dict[str, Any]:
    """Validate something fetched from the store as one of ours.

    Sources fetched by CID are other people's content: a bundle that came out
    of the store is checked exactly as hard as one that came off the wire.
    """
    if not isinstance(payload, dict):
        raise ProjectError('that CID is not an eth project (it is not JSON)')
    if payload.get('kind') != KIND:
        raise ProjectError(f'that CID holds {payload.get("kind") or "something"}'
                           f', not an {KIND} bundle')
    files = _clean_files(payload.get('files'))
    entry = _pick_entry(files, payload.get('entry'))
    tests = payload.get('tests')
    return {
        'kind': KIND,
        'name': str(payload.get('name') or 'contract')[:120],
        'entry': entry,
        'files': files,
        'tests': tests if isinstance(tests, list) else [],
        'settings': payload.get('settings') if isinstance(payload.get('settings'), dict) else {},
        'author': payload.get('author'),
        'note': payload.get('note'),
        'origin_cid': payload.get('origin_cid'),
        'written': payload.get('written'),
    }


# ── the store round trip ─────────────────────────────────────────────

def _push(token: Optional[str], slug: str, bundle: Dict[str, Any],
          public: bool) -> Dict[str, Any]:
    """Bundle → store → CID. A refusal is reported, never fatal.

    Losing somebody's source because the store is asleep, or because their
    address is not on its whitelist, would be a worse bug than not having a
    CID. So the save always lands locally and this returns *why* there is no
    CID, which the console renders next to the project.
    """
    if not token:
        return {'cid': None, 'stored': False,
                'reason': 'not signed in, so there is nothing to store it under'}
    try:
        out = LINK.put_json(token, f'{slug}.eth.json', bundle, public=public)
        return {'cid': out.get('cid'), 'stored': True, 'size': out.get('size'),
                'public': public}
    except StoreError as e:
        return {'cid': None, 'stored': False, 'reason': e.message,
                'status': e.status}


# ── writing ──────────────────────────────────────────────────────────

def save(owner: str, token: Optional[str] = None, name: Optional[str] = None,
         files: Any = None, source: Optional[str] = None,
         entry: Optional[str] = None, tests: Optional[List[dict]] = None,
         settings: Optional[dict] = None, note: Optional[str] = None,
         project: Optional[str] = None, public: Optional[bool] = None,
         origin_cid: Optional[str] = None) -> Dict[str, Any]:
    """Create a project, or write a new version of one.

    `project` is an existing id or slug. Without it a new project is created
    under a slug derived from the name, made unique rather than clobbering:
    two contracts called Token are two projects, not one overwritten one.
    """
    owner = (owner or '').lower()
    existing = find(owner, project) if project else None
    if project and existing is None:
        raise ProjectError(f'no project {project!r} of yours')

    if existing:
        # No files in the request means "everything but the source changed" —
        # a rename, a note, a new suite. Dropping to an empty project because
        # the caller only sent a name would be a very expensive convenience.
        cleaned = (_clean_files(files, source, entry) if (files or source)
                   else dict(existing.get('files') or {}))
        name = name or existing['name']
        entry = _pick_entry(cleaned, entry or existing.get('entry'))
        tests = existing.get('tests') if tests is None else tests
        settings = existing.get('settings') if settings is None else settings
        public = existing['public'] if public is None else bool(public)
        origin_cid = origin_cid or existing.get('origin_cid')
    else:
        cleaned = _clean_files(files, source, entry)
        name = (name or '').strip() or (contract_names(cleaned) or ['contract'])[0]
        entry = _pick_entry(cleaned, entry)
        public = bool(public)

    bundle = make_bundle(name, cleaned, entry, tests=tests, settings=settings,
                         author=owner, note=note, origin_cid=origin_cid)
    slug = existing['slug'] if existing else _free_slug(owner, slugify(name))
    pushed = _push(token, slug, bundle, public)

    now = int(time.time())
    with connect() as conn:
        if existing:
            conn.execute(
                'UPDATE projects SET name=?, entry=?, cid=COALESCE(?, cid), '
                'public=?, body=?, note=?, updated=?, origin_cid=? WHERE id=?',
                (name, entry, pushed['cid'], int(public), json.dumps(bundle),
                 note, now, origin_cid, existing['id']))
            pid = existing['id']
        else:
            cursor = conn.execute(
                'INSERT INTO projects (owner, slug, name, entry, cid, origin_cid,'
                ' public, body, note, created, updated) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (owner, slug, name, entry, pushed['cid'], origin_cid,
                 int(public), json.dumps(bundle), note, now, now))
            pid = cursor.lastrowid
        if pushed['cid']:
            conn.execute('INSERT INTO project_versions (project_id, cid, size, '
                         'note, created) VALUES (?,?,?,?,?)',
                         (pid, pushed['cid'], pushed.get('size'), note, now))
        row = conn.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()

    out = _row(row)
    out['store'] = pushed
    return out


def _free_slug(owner: str, base: str) -> str:
    with connect() as conn:
        taken = {r['slug'] for r in
                 conn.execute('SELECT slug FROM projects WHERE owner=?', (owner,))}
    if base not in taken:
        return base
    n = 2
    while f'{base}-{n}' in taken:
        n += 1
    return f'{base}-{n}'


def share(owner: str, token: str, project: str) -> Dict[str, Any]:
    """Make a project public in the store and hand back the link.

    If the current bundle never reached the store (asleep, unauthorised) this
    pushes it now, so "share" means the same thing whether or not the last save
    happened to land.
    """
    row = find(owner, project)
    if row is None:
        raise ProjectError(f'no project {project!r} of yours')
    cid = row.get('cid')
    if not cid:
        bundle = make_bundle(row['name'], row['files'], row['entry'],
                             tests=row.get('tests'), settings=row.get('settings'),
                             author=owner, note=row.get('note'),
                             origin_cid=row.get('origin_cid'))
        pushed = _push(token, row['slug'], bundle, True)
        if not pushed['cid']:
            raise StoreError(pushed.get('reason') or 'the store refused the '
                             'upload, so there is nothing to share yet',
                             pushed.get('status', 503))
        cid = pushed['cid']
        with connect() as conn:
            conn.execute('INSERT INTO project_versions (project_id, cid, size, '
                         'note, created) VALUES (?,?,?,?,?)',
                         (row['id'], cid, pushed.get('size'), 'shared',
                          int(time.time())))
    else:
        LINK.publish(token, cid, True)
    with connect() as conn:
        conn.execute('UPDATE projects SET public=1, cid=?, updated=? WHERE id=?',
                     (cid, int(time.time()), row['id']))
    return {'id': row['id'], 'slug': row['slug'], 'name': row['name'],
            'cid': cid, 'public': True, **share_links(cid)}


def unshare(owner: str, token: str, project: str) -> Dict[str, Any]:
    row = find(owner, project)
    if row is None:
        raise ProjectError(f'no project {project!r} of yours')
    if row.get('cid'):
        LINK.publish(token, row['cid'], False)
    with connect() as conn:
        conn.execute('UPDATE projects SET public=0, updated=? WHERE id=?',
                     (int(time.time()), row['id']))
    return {'id': row['id'], 'cid': row.get('cid'), 'public': False}


def share_links(cid: str) -> Dict[str, str]:
    """Where a CID can be opened. Both are the same bundle, two audiences.

    The console link opens the project *in this module* — editable, testable,
    deployable. The store link is the raw object, for anyone who would rather
    have the JSON than the tool.
    """
    return {
        'open': f'/eth/?open={cid}',
        'store': f'/api/store/get?cid={cid}',
        'command': f'm eth/open cid={cid}',
    }


def fork(owner: str, token: Optional[str], cid: str,
         name: Optional[str] = None) -> Dict[str, Any]:
    """Copy someone's shared project into your own workspace.

    The fork is yours from the first byte — your slug, your CID once you save,
    your right to delete it. `origin_cid` is the only thing carried over, and
    it is a fact about provenance, not a claim on the copy.
    """
    bundle = open_bundle(token, cid)
    out = save(owner, token, name=name or bundle['name'], files=bundle['files'],
               entry=bundle['entry'], tests=bundle.get('tests'),
               settings=bundle.get('settings'),
               note=f'forked from {cid}', origin_cid=cid)
    out['forked_from'] = cid
    return out


def open_bundle(token: Optional[str], cid: str) -> Dict[str, Any]:
    """Read a project out of the store by CID. No account required.

    Public objects come back without a token, which is exactly what makes a
    share link work for a stranger.
    """
    cid = (cid or '').strip()
    if not cid:
        raise ProjectError('which CID?')
    payload = LINK.fetch_json(token, cid)
    bundle = read_bundle(payload)
    bundle['cid'] = cid
    bundle.update(share_links(cid))
    return bundle


def delete(owner: str, project: str, token: Optional[str] = None,
           from_store: bool = False) -> Dict[str, Any]:
    """Forget a project here. The store keeps its copy unless asked.

    Deleting the CID as well is opt-in because content addressing means anyone
    you shared with may be holding it — removing your local index entry is a
    tidy-up, removing the object is a takedown, and they are different acts.
    """
    row = find(owner, project)
    if row is None:
        raise ProjectError(f'no project {project!r} of yours')
    removed = None
    if from_store and row.get('cid') and token:
        try:
            removed = LINK.remove(token, row['cid'])
        except StoreError as e:
            removed = {'error': e.message}
    with connect() as conn:
        conn.execute('DELETE FROM project_versions WHERE project_id=?', (row['id'],))
        conn.execute('DELETE FROM projects WHERE id=?', (row['id'],))
    return {'deleted': row['id'], 'slug': row['slug'], 'cid': row.get('cid'),
            'store': removed}


# ── reading ──────────────────────────────────────────────────────────

def find(owner: str, project: Any) -> Optional[Dict[str, Any]]:
    """By id, by slug, or by CID — whichever the caller had to hand."""
    owner = (owner or '').lower()
    key = str(project or '').strip()
    if not key:
        return None
    with connect() as conn:
        row = None
        if key.isdigit():
            row = conn.execute('SELECT * FROM projects WHERE id=? AND owner=?',
                               (int(key), owner)).fetchone()
        if row is None:
            row = conn.execute('SELECT * FROM projects WHERE owner=? AND '
                               '(slug=? OR cid=? OR name=?)',
                               (owner, key, key, key)).fetchone()
    return _row(row) if row else None


def get(owner: str, project: Any) -> Dict[str, Any]:
    row = find(owner, project)
    if row is None:
        raise ProjectError(f'no project {project!r} of yours')
    row['versions'] = versions(row['id'])
    row.update(share_links(row['cid']) if row.get('cid') else {})
    return row


def listing(owner: str, limit: int = 100) -> List[Dict[str, Any]]:
    owner = (owner or '').lower()
    with connect() as conn:
        rows = conn.execute('SELECT * FROM projects WHERE owner=? '
                            'ORDER BY updated DESC LIMIT ?',
                            (owner, int(limit))).fetchall()
    return [summary(_row(r)) for r in rows]


def versions(project_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('SELECT cid, size, note, created FROM '
                            'project_versions WHERE project_id=? '
                            'ORDER BY created DESC LIMIT ?',
                            (int(project_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def counts(owner: str) -> Dict[str, int]:
    owner = (owner or '').lower()
    with connect() as conn:
        return {
            'projects': conn.execute('SELECT COUNT(*) c FROM projects WHERE owner=?',
                                     (owner,)).fetchone()['c'],
            'shared': conn.execute('SELECT COUNT(*) c FROM projects WHERE owner=? '
                                   'AND public=1', (owner,)).fetchone()['c'],
        }
