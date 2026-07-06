"""
store access layer — timed grants, data pools, QR auth handoff, and a
CID-agnostic object ACL. SQLite-backed, lives OFF-CHAIN in the store's private
dir (default ~/.mod/store/access.db), never in committed config.

Why a separate module: `api/api.py` owns HTTP + protocol-auth; this owns the
*authorization model*. It is pure (no FastAPI deps) so it is unit-testable and
reusable. The dstore SQLite index (`~/.store-mod/store.db`) stays the source of
truth for *bytes stored*; this DB is the source of truth for *who may see them*.

Concepts
    acl          one row per CID: owner, visibility (private|public), and the
                 CID *scheme* + optional external gateway url. The scheme/url
                 pair is what makes the store CID-agnostic — an object can be a
                 native localfs/filecoin/hippius CID or any external system
                 (arweave tx, ipfs from another node, s3 key, …) referenced by
                 url. Unknown CIDs (no row) are treated as public for
                 back-compat with objects stored before this layer existed.
    grants       address → address, time-bounded read/write access to one CID
                 or to *all* of the grantor's objects (cid='*'). expires=NULL
                 means no expiry.
    pools        named groups; members hold a role (owner|editor|viewer) with
                 optional timed membership. Objects added to a pool are mutually
                 readable by every (non-expired) member.
    handoffs     one-time, short-TTL codes that carry an existing bearer token
                 from one device to another (the QR computer↔phone transfer).
"""
import secrets
import sqlite3
import time
from typing import Optional

# Roles that may mutate a pool (add members/objects).
WRITE_ROLES = ('owner', 'editor')

# One-time access tickets default to a very short life — they are meant to be
# scanned/clicked immediately and never reused.
DEFAULT_TICKET_TTL = 10


def now() -> int:
    return int(time.time())


def new_id(n: int = 8) -> str:
    return secrets.token_hex(n)


def infer_scheme(cid: str) -> str:
    """Best-effort CID namespace label. Never raises; defaults to 'custom'.

    The store never *rejects* a CID on scheme — this is purely descriptive so
    other CID systems can coexist. Explicit `scheme:rest` prefixes win.
    """
    if not cid:
        return 'custom'
    c = cid.strip()
    # Explicit namespace, e.g. "ar://…", "ipfs://…", "arweave:…".
    for sep in ('://', ':'):
        if sep in c:
            pre = c.split(sep, 1)[0].lower()
            if pre in ('ipfs', 'ipns'):
                return 'ipfs'
            if pre in ('ar', 'arweave'):
                return 'arweave'
            if pre and pre.isalnum() and len(pre) <= 12:
                return pre
            break
    # IPFS v0 (Qm…, base58, 46 chars) / v1 (bafy…, bafk…).
    if (c.startswith('Qm') and len(c) == 46) or c.startswith(('bafy', 'bafk', 'bafz')):
        return 'ipfs'
    # Arweave tx ids: 43-char base64url.
    if len(c) == 43 and all(ch.isalnum() or ch in '-_' for ch in c):
        return 'arweave'
    return 'custom'


class Access:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._init()

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        conn = self._db()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS acl (
                cid        TEXT PRIMARY KEY,
                owner      TEXT,
                scheme     TEXT,
                backend    TEXT,
                url        TEXT,
                visibility TEXT DEFAULT 'private',
                semhash    TEXT,
                created    INTEGER
            );
            CREATE TABLE IF NOT EXISTS pins (
                cid     TEXT NOT NULL,
                backend TEXT NOT NULL,
                owner   TEXT,
                created INTEGER,
                PRIMARY KEY (cid, backend)
            );
            CREATE TABLE IF NOT EXISTS grants (
                id      TEXT PRIMARY KEY,
                grantor TEXT NOT NULL,
                grantee TEXT NOT NULL,
                cid     TEXT,           -- specific CID, or '*' = all grantor objects
                scope   TEXT DEFAULT 'read',
                created INTEGER,
                expires INTEGER          -- unix secs; NULL = no expiry
            );
            CREATE INDEX IF NOT EXISTS idx_grants_grantee ON grants(grantee);
            CREATE INDEX IF NOT EXISTS idx_grants_grantor ON grants(grantor);
            CREATE TABLE IF NOT EXISTS pools (
                id          TEXT PRIMARY KEY,
                name        TEXT,
                owner       TEXT NOT NULL,
                description TEXT,
                created     INTEGER
            );
            CREATE TABLE IF NOT EXISTS pool_members (
                pool_id TEXT NOT NULL,
                address TEXT NOT NULL,
                role    TEXT DEFAULT 'viewer',
                added   INTEGER,
                expires INTEGER,
                PRIMARY KEY (pool_id, address)
            );
            CREATE TABLE IF NOT EXISTS pool_objects (
                pool_id  TEXT NOT NULL,
                cid      TEXT NOT NULL,
                backend  TEXT,
                scheme   TEXT,
                key      TEXT,
                added_by TEXT,
                added    INTEGER,
                PRIMARY KEY (pool_id, cid)
            );
            CREATE TABLE IF NOT EXISTS handoffs (
                code    TEXT PRIMARY KEY,
                token   TEXT NOT NULL,
                address TEXT,
                created INTEGER,
                expires INTEGER,
                claimed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tickets (
                code    TEXT PRIMARY KEY,
                cid     TEXT NOT NULL,
                backend TEXT,
                issuer  TEXT,
                created INTEGER,
                expires INTEGER,
                claimed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS refs (
                cid     TEXT NOT NULL,
                ref_cid TEXT NOT NULL,
                created INTEGER,
                PRIMARY KEY (cid, ref_cid)
            );
            CREATE INDEX IF NOT EXISTS idx_refs_ref_cid ON refs(ref_cid);
        ''')
        # Migration: add semhash to acl tables created before semantic hashing.
        cols = {r[1] for r in conn.execute('PRAGMA table_info(acl)').fetchall()}
        if 'semhash' not in cols:
            conn.execute('ALTER TABLE acl ADD COLUMN semhash TEXT')
        conn.commit()
        conn.close()

    # ── ACL (visibility + CID-agnostic scheme/url + semantic hash) ──

    def set_acl(self, cid, owner=None, scheme=None, backend=None, url=None,
                visibility='private', semhash=None):
        owner = owner.lower() if owner else None
        scheme = scheme or infer_scheme(cid)
        conn = self._db()
        # Preserve an existing owner/visibility unless explicitly provided.
        existing = conn.execute('SELECT owner, visibility FROM acl WHERE cid=?', (cid,)).fetchone()
        if existing:
            owner = owner or existing['owner']
            visibility = visibility or existing['visibility']
        conn.execute(
            '''INSERT INTO acl (cid, owner, scheme, backend, url, visibility, semhash, created)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(cid) DO UPDATE SET
                 owner=excluded.owner, scheme=excluded.scheme, backend=excluded.backend,
                 url=COALESCE(excluded.url, acl.url), visibility=excluded.visibility,
                 semhash=COALESCE(excluded.semhash, acl.semhash)''',
            (cid, owner, scheme, backend, url, visibility, semhash, now()),
        )
        conn.commit()
        conn.close()
        return self.get_acl(cid)

    def all_semhashes(self) -> dict:
        """{cid: semhash_hex} for every object that has a semantic hash."""
        conn = self._db()
        rows = conn.execute("SELECT cid, semhash FROM acl WHERE semhash IS NOT NULL AND semhash != ''").fetchall()
        conn.close()
        return {r['cid']: r['semhash'] for r in rows}

    def get_acl(self, cid) -> Optional[dict]:
        conn = self._db()
        row = conn.execute('SELECT * FROM acl WHERE cid=?', (cid,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def set_visibility(self, cid, public: bool):
        conn = self._db()
        conn.execute('UPDATE acl SET visibility=? WHERE cid=?',
                     ('public' if public else 'private', cid))
        conn.commit()
        conn.close()
        return self.get_acl(cid)

    def visibility(self, cid) -> str:
        acl = self.get_acl(cid)
        # Unknown CID ⇒ public (back-compat with pre-ACL objects).
        return acl['visibility'] if acl else 'public'

    # ── refs (CID → CID composition, detected at upload time) ───────
    # When an object's content embeds other CIDs already known to the store
    # (a manifest/bundle referencing pre-existing objects), it is "mapped
    # from" them. Recorded one-directional (cid references ref_cid) so both
    # "what was this built from" and "what was built from this" are cheap.

    def set_refs(self, cid, ref_cids: list):
        conn = self._db()
        conn.execute('DELETE FROM refs WHERE cid=?', (cid,))
        for ref_cid in {r for r in ref_cids if r and r != cid}:
            conn.execute('INSERT OR IGNORE INTO refs (cid, ref_cid, created) VALUES (?,?,?)',
                         (cid, ref_cid, now()))
        conn.commit()
        conn.close()

    def refs_out(self, cid) -> list:
        """CIDs this object's content references (what it was mapped from)."""
        conn = self._db()
        rows = conn.execute('SELECT ref_cid FROM refs WHERE cid=?', (cid,)).fetchall()
        conn.close()
        return [r['ref_cid'] for r in rows]

    def refs_in(self, cid) -> list:
        """CIDs whose content references this one (what was mapped from it)."""
        conn = self._db()
        rows = conn.execute('SELECT cid FROM refs WHERE ref_cid=?', (cid,)).fetchall()
        conn.close()
        return [r['cid'] for r in rows]

    # ── grants (timed access) ──────────────────────────────────────

    def create_grant(self, grantor, grantee, cid=None, scope='read',
                     ttl_seconds=None, expires_at=None) -> dict:
        grantor, grantee = grantor.lower(), grantee.lower()
        cid = cid or '*'
        scope = scope if scope in ('read', 'write') else 'read'
        expires = expires_at
        if expires is None and ttl_seconds:
            expires = now() + int(ttl_seconds)
        gid = new_id()
        conn = self._db()
        conn.execute(
            'INSERT INTO grants (id, grantor, grantee, cid, scope, created, expires) VALUES (?,?,?,?,?,?,?)',
            (gid, grantor, grantee, cid, scope, now(), expires),
        )
        conn.commit()
        conn.close()
        return self.get_grant(gid)

    def get_grant(self, gid) -> Optional[dict]:
        conn = self._db()
        row = conn.execute('SELECT * FROM grants WHERE id=?', (gid,)).fetchone()
        conn.close()
        return self._grant_view(row) if row else None

    def revoke_grant(self, gid, grantor=None) -> bool:
        conn = self._db()
        if grantor:
            cur = conn.execute('DELETE FROM grants WHERE id=? AND grantor=?', (gid, grantor.lower()))
        else:
            cur = conn.execute('DELETE FROM grants WHERE id=?', (gid,))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0

    def grants_by(self, grantor) -> list:
        conn = self._db()
        rows = conn.execute('SELECT * FROM grants WHERE grantor=? ORDER BY created DESC',
                            (grantor.lower(),)).fetchall()
        conn.close()
        return [self._grant_view(r) for r in rows]

    def grants_to(self, grantee, active_only=True) -> list:
        conn = self._db()
        rows = conn.execute('SELECT * FROM grants WHERE grantee=? ORDER BY created DESC',
                            (grantee.lower(),)).fetchall()
        conn.close()
        out = [self._grant_view(r) for r in rows]
        return [g for g in out if not g['expired']] if active_only else out

    def grants_on(self, cid, owner=None) -> list:
        """All grants covering `cid` (specific or wildcard from its owner)."""
        conn = self._db()
        if owner:
            rows = conn.execute(
                "SELECT * FROM grants WHERE cid=? OR (cid='*' AND grantor=?) ORDER BY created DESC",
                (cid, owner.lower()),
            ).fetchall()
        else:
            rows = conn.execute('SELECT * FROM grants WHERE cid=? ORDER BY created DESC', (cid,)).fetchall()
        conn.close()
        return [self._grant_view(r) for r in rows]

    def pools_containing(self, cid) -> list:
        """Pools that contain `cid`, each with its (live) member list."""
        conn = self._db()
        rows = conn.execute(
            '''SELECT DISTINCT p.* FROM pools p JOIN pool_objects o ON o.pool_id = p.id
               WHERE o.cid=?''', (cid,)).fetchall()
        out = []
        for p in rows:
            members = conn.execute('SELECT * FROM pool_members WHERE pool_id=?', (p['id'],)).fetchall()
            d = dict(p)
            d['members'] = [self._member_view(mm) for mm in members]
            out.append(d)
        conn.close()
        return out

    def _has_grant(self, grantee, cid, owner, scope='read') -> bool:
        """True if grantee holds a live grant covering `cid` from `owner`."""
        conn = self._db()
        rows = conn.execute(
            '''SELECT * FROM grants WHERE grantee=? AND grantor=?
               AND (cid=? OR cid='*')''',
            (grantee.lower(), (owner or '').lower(), cid),
        ).fetchall()
        conn.close()
        t = now()
        for r in rows:
            if r['expires'] is not None and r['expires'] < t:
                continue
            if scope == 'write' and r['scope'] != 'write':
                continue
            return True
        return False

    @staticmethod
    def _grant_view(r) -> dict:
        d = dict(r)
        d['expired'] = d['expires'] is not None and d['expires'] < now()
        d['expires_in'] = None if d['expires'] is None else max(0, d['expires'] - now())
        return d

    # ── pools (mutual access) ──────────────────────────────────────

    def create_pool(self, owner, name=None, description=None) -> dict:
        owner = owner.lower()
        pid = new_id(6)
        conn = self._db()
        conn.execute('INSERT INTO pools (id, name, owner, description, created) VALUES (?,?,?,?,?)',
                     (pid, name or pid, owner, description, now()))
        conn.execute('INSERT INTO pool_members (pool_id, address, role, added, expires) VALUES (?,?,?,?,?)',
                     (pid, owner, 'owner', now(), None))
        conn.commit()
        conn.close()
        return self.get_pool(pid)

    def get_pool(self, pid) -> Optional[dict]:
        conn = self._db()
        p = conn.execute('SELECT * FROM pools WHERE id=?', (pid,)).fetchone()
        if not p:
            conn.close()
            return None
        members = conn.execute('SELECT * FROM pool_members WHERE pool_id=?', (pid,)).fetchall()
        objects = conn.execute('SELECT * FROM pool_objects WHERE pool_id=? ORDER BY added DESC', (pid,)).fetchall()
        conn.close()
        d = dict(p)
        d['members'] = [self._member_view(m) for m in members]
        d['objects'] = [dict(o) for o in objects]
        return d

    def list_pools_for(self, address) -> list:
        """Pools the address owns or is a (live) member of, with summary counts."""
        addr = address.lower()
        conn = self._db()
        rows = conn.execute(
            '''SELECT DISTINCT p.* FROM pools p
               LEFT JOIN pool_members m ON m.pool_id = p.id
               WHERE p.owner=? OR m.address=? ORDER BY p.created DESC''',
            (addr, addr),
        ).fetchall()
        out = []
        for p in rows:
            pid = p['id']
            mc = conn.execute('SELECT COUNT(*) FROM pool_members WHERE pool_id=?', (pid,)).fetchone()[0]
            oc = conn.execute('SELECT COUNT(*) FROM pool_objects WHERE pool_id=?', (pid,)).fetchone()[0]
            role = self.member_role(pid, addr)
            d = dict(p)
            d.update(member_count=mc, object_count=oc, role=role)
            out.append(d)
        conn.close()
        return out

    def member_role(self, pid, address) -> Optional[str]:
        """Live role of an address in a pool; None if absent/expired."""
        conn = self._db()
        r = conn.execute('SELECT * FROM pool_members WHERE pool_id=? AND address=?',
                         (pid, address.lower())).fetchone()
        conn.close()
        if not r:
            return None
        if r['expires'] is not None and r['expires'] < now():
            return None
        return r['role']

    def is_member(self, pid, address) -> bool:
        return self.member_role(pid, address) is not None

    def add_member(self, pid, address, role='viewer', ttl_seconds=None, expires_at=None) -> dict:
        role = role if role in ('owner', 'editor', 'viewer') else 'viewer'
        expires = expires_at
        if expires is None and ttl_seconds:
            expires = now() + int(ttl_seconds)
        conn = self._db()
        conn.execute(
            '''INSERT INTO pool_members (pool_id, address, role, added, expires) VALUES (?,?,?,?,?)
               ON CONFLICT(pool_id, address) DO UPDATE SET role=excluded.role, expires=excluded.expires''',
            (pid, address.lower(), role, now(), expires),
        )
        conn.commit()
        conn.close()
        return self.get_pool(pid)

    def remove_member(self, pid, address) -> bool:
        conn = self._db()
        cur = conn.execute('DELETE FROM pool_members WHERE pool_id=? AND address=?', (pid, address.lower()))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0

    def add_object(self, pid, cid, backend=None, scheme=None, key=None, added_by=None) -> dict:
        conn = self._db()
        conn.execute(
            '''INSERT INTO pool_objects (pool_id, cid, backend, scheme, key, added_by, added)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(pool_id, cid) DO UPDATE SET backend=excluded.backend,
                 scheme=excluded.scheme, key=excluded.key''',
            (pid, cid, backend, scheme or infer_scheme(cid), key, (added_by or '').lower(), now()),
        )
        conn.commit()
        conn.close()
        return self.get_pool(pid)

    def remove_object(self, pid, cid) -> bool:
        conn = self._db()
        cur = conn.execute('DELETE FROM pool_objects WHERE pool_id=? AND cid=?', (pid, cid))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0

    def delete_pool(self, pid) -> bool:
        """Delete a pool and all its membership + object rows (owner-enforced in API)."""
        conn = self._db()
        conn.execute('DELETE FROM pool_objects WHERE pool_id=?', (pid,))
        conn.execute('DELETE FROM pool_members WHERE pool_id=?', (pid,))
        cur = conn.execute('DELETE FROM pools WHERE id=?', (pid,))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0

    def pools_with_cid_for(self, address, cid) -> bool:
        """True if `address` is a live member of any pool containing `cid`."""
        conn = self._db()
        rows = conn.execute(
            '''SELECT m.expires FROM pool_objects o
               JOIN pool_members m ON m.pool_id = o.pool_id
               WHERE o.cid=? AND m.address=?''',
            (cid, address.lower()),
        ).fetchall()
        conn.close()
        t = now()
        return any(r['expires'] is None or r['expires'] >= t for r in rows)

    @staticmethod
    def _member_view(r) -> dict:
        d = dict(r)
        d['expired'] = d['expires'] is not None and d['expires'] < now()
        d['expires_in'] = None if d['expires'] is None else max(0, d['expires'] - now())
        return d

    # ── access decision ────────────────────────────────────────────

    def can_read(self, address: Optional[str], cid: str) -> bool:
        acl = self.get_acl(cid)
        if acl is None:
            return True  # unknown object ⇒ public (back-compat)
        if acl['visibility'] == 'public':
            return True
        if not address:
            return False
        addr = address.lower()
        if acl['owner'] and addr == acl['owner']:
            return True
        if self._has_grant(addr, cid, acl['owner'], 'read'):
            return True
        if self.pools_with_cid_for(addr, cid):
            return True
        return False

    def can_write(self, address: Optional[str], cid: str) -> bool:
        acl = self.get_acl(cid)
        if not address:
            return False
        addr = address.lower()
        if acl and acl['owner'] and addr == acl['owner']:
            return True
        if acl and self._has_grant(addr, cid, acl['owner'], 'write'):
            return True
        return False

    def shared_with(self, address: str) -> dict:
        """Everything reachable by `address` beyond their own objects.

        Returns explicit CIDs (specific grants + pool objects) plus the set of
        wildcard grantors whose *entire* object set is shared — the API resolves
        those CIDs from the dstore index.
        """
        addr = address.lower()
        cids, grantors = {}, set()
        for g in self.grants_to(addr, active_only=True):
            if g['cid'] == '*':
                grantors.add(g['grantor'])
            else:
                cids[g['cid']] = {'via': 'grant', 'from': g['grantor'], 'expires': g['expires']}
        conn = self._db()
        rows = conn.execute(
            '''SELECT o.* FROM pool_objects o
               JOIN pool_members m ON m.pool_id = o.pool_id
               WHERE m.address=? AND (m.expires IS NULL OR m.expires >= ?)''',
            (addr, now()),
        ).fetchall()
        conn.close()
        for o in rows:
            cids[o['cid']] = {'via': 'pool', 'pool': o['pool_id'], 'backend': o['backend'],
                              'scheme': o['scheme'], 'key': o['key']}
        return {'cids': cids, 'wildcard_grantors': sorted(grantors)}

    # ── QR auth handoff (computer ↔ phone) ─────────────────────────

    def create_handoff(self, token, address=None, ttl_seconds=120) -> dict:
        code = secrets.token_urlsafe(9)
        expires = now() + int(ttl_seconds)
        conn = self._db()
        conn.execute('INSERT INTO handoffs (code, token, address, created, expires, claimed) VALUES (?,?,?,?,?,0)',
                     (code, token, (address or '').lower(), now(), expires))
        conn.commit()
        conn.close()
        return {'code': code, 'expires': expires, 'expires_in': ttl_seconds}

    def claim_handoff(self, code) -> Optional[dict]:
        """One-time claim: returns {token, address} once, then never again."""
        conn = self._db()
        r = conn.execute('SELECT * FROM handoffs WHERE code=?', (code,)).fetchone()
        if not r:
            conn.close()
            return None
        if r['claimed'] or (r['expires'] is not None and r['expires'] < now()):
            conn.close()
            return None
        conn.execute('UPDATE handoffs SET claimed=1 WHERE code=?', (code,))
        conn.commit()
        conn.close()
        return {'token': r['token'], 'address': r['address']}

    # ── one-time access tickets (single-use, anti-replay) ──────────

    def create_ticket(self, cid, backend=None, issuer=None,
                      ttl_seconds=DEFAULT_TICKET_TTL) -> dict:
        """Mint a single-use, short-lived ticket granting one fetch of `cid`."""
        code = secrets.token_urlsafe(12)
        ttl = max(1, int(ttl_seconds or DEFAULT_TICKET_TTL))
        expires = now() + ttl
        conn = self._db()
        conn.execute(
            'INSERT INTO tickets (code, cid, backend, issuer, created, expires, claimed) VALUES (?,?,?,?,?,?,0)',
            (code, cid, backend, (issuer or '').lower(), now(), expires),
        )
        conn.commit()
        conn.close()
        return {'code': code, 'cid': cid, 'backend': backend,
                'expires': expires, 'expires_in': ttl}

    def claim_ticket(self, code) -> Optional[dict]:
        """Redeem a ticket exactly once.

        The claim is atomic: `UPDATE … WHERE code=? AND claimed=0` and we require
        the update to have touched exactly one row. A replayed code — even two
        requests racing in parallel — finds `claimed=1` and gets None, so a
        ticket can never be used twice.
        """
        conn = self._db()
        row = conn.execute('SELECT * FROM tickets WHERE code=?', (code,)).fetchone()
        if not row:
            conn.close()
            return None
        if row['expires'] is not None and row['expires'] < now():
            conn.close()
            return None
        cur = conn.execute('UPDATE tickets SET claimed=1 WHERE code=? AND claimed=0', (code,))
        conn.commit()
        won = cur.rowcount == 1
        conn.close()
        if not won:
            return None  # already claimed → replay rejected
        return {'cid': row['cid'], 'backend': row['backend'], 'issuer': row['issuer']}

    def tickets_by(self, issuer, active_only=True) -> list:
        conn = self._db()
        rows = conn.execute('SELECT * FROM tickets WHERE issuer=? ORDER BY created DESC',
                            (issuer.lower(),)).fetchall()
        conn.close()
        t = now()
        out = []
        for r in rows:
            d = dict(r)
            d['expired'] = d['expires'] is not None and d['expires'] < t
            d['expires_in'] = None if d['expires'] is None else max(0, d['expires'] - t)
            d['used'] = bool(d['claimed'])
            if active_only and (d['used'] or d['expired']):
                continue
            out.append(d)
        return out

    # ── pin management ─────────────────────────────────────────────

    def add_pin(self, cid, backend, owner=None) -> dict:
        conn = self._db()
        conn.execute(
            '''INSERT INTO pins (cid, backend, owner, created) VALUES (?,?,?,?)
               ON CONFLICT(cid, backend) DO UPDATE SET owner=excluded.owner''',
            (cid, backend, (owner or '').lower(), now()),
        )
        conn.commit()
        conn.close()
        return {'cid': cid, 'backend': backend, 'pinned': True}

    def remove_pin(self, cid, backend=None) -> int:
        conn = self._db()
        if backend:
            cur = conn.execute('DELETE FROM pins WHERE cid=? AND backend=?', (cid, backend))
        else:
            cur = conn.execute('DELETE FROM pins WHERE cid=?', (cid,))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n

    def pins_for(self, owner) -> list:
        conn = self._db()
        rows = conn.execute('SELECT * FROM pins WHERE owner=? ORDER BY created DESC',
                            (owner.lower(),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def is_pinned(self, cid, backend=None) -> bool:
        conn = self._db()
        if backend:
            r = conn.execute('SELECT 1 FROM pins WHERE cid=? AND backend=?', (cid, backend)).fetchone()
        else:
            r = conn.execute('SELECT 1 FROM pins WHERE cid=?', (cid,)).fetchone()
        conn.close()
        return r is not None

    # ── housekeeping ───────────────────────────────────────────────

    def prune(self) -> dict:
        """Delete expired grants, memberships, handoffs, and tickets. Best-effort."""
        t = now()
        conn = self._db()
        g = conn.execute('DELETE FROM grants WHERE expires IS NOT NULL AND expires < ?', (t,)).rowcount
        m = conn.execute('DELETE FROM pool_members WHERE expires IS NOT NULL AND expires < ? AND role != "owner"', (t,)).rowcount
        h = conn.execute('DELETE FROM handoffs WHERE (expires IS NOT NULL AND expires < ?) OR claimed=1', (t,)).rowcount
        k = conn.execute('DELETE FROM tickets WHERE (expires IS NOT NULL AND expires < ?) OR claimed=1', (t,)).rowcount
        conn.commit()
        conn.close()
        return {'grants': g, 'members': m, 'handoffs': h, 'tickets': k}
