"""Unit tests for the store access layer: timed grants, data pools, QR auth
handoff, and the CID-agnostic ACL. Pure SQLite, no network / no FastAPI."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))  # store dir
from api.access import Access, infer_scheme  # noqa: E402

ALICE = '0x' + 'a' * 40
BOB = '0x' + 'b' * 40
CAROL = '0x' + 'c' * 40
CID = 'QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG'
CID2 = 'bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi'


@pytest.fixture
def acc(tmp_path):
    return Access(tmp_path / 'access.db')


# ── CID-agnostic scheme inference ──────────────────────────────────

def test_infer_scheme():
    assert infer_scheme(CID) == 'ipfs'
    assert infer_scheme(CID2) == 'ipfs'
    assert infer_scheme('ar://abc') == 'arweave'
    assert infer_scheme('arweave:tx') == 'arweave'
    assert infer_scheme('s3://bucket/key') == 's3'
    assert infer_scheme('AbCdEf0123456789-_AbCdEf0123456789-_AbCdEfg') == 'arweave'  # 43-char
    assert infer_scheme('whatever') == 'custom'
    assert infer_scheme('') == 'custom'  # never raises


# ── ACL / visibility ───────────────────────────────────────────────

def test_visibility_default_and_publish(acc):
    # Unknown CID is public (back-compat with pre-ACL objects).
    assert acc.visibility('never-seen') == 'public'
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    assert acc.visibility(CID) == 'private'      # new objects default private
    assert acc.can_read(None, CID) is False      # anon cannot read private
    assert acc.can_read(ALICE, CID) is True      # owner can
    acc.set_visibility(CID, True)
    assert acc.can_read(None, CID) is True        # now public


def test_acl_preserves_owner_on_reupsert(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs', visibility='public')
    # A later set_acl without owner must not wipe the owner.
    acc.set_acl(CID, backend='localfs')
    assert acc.get_acl(CID)['owner'] == ALICE


def test_external_url_roundtrip(acc):
    acc.set_acl('arweave:tx123', owner=ALICE, scheme='arweave',
                backend='external', url='https://arweave.net/tx123', visibility='public')
    acl = acc.get_acl('arweave:tx123')
    assert acl['url'] == 'https://arweave.net/tx123'
    assert acl['scheme'] == 'arweave'


# ── grants (timed) ─────────────────────────────────────────────────

def test_grant_specific_cid(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    assert acc.can_read(BOB, CID) is False
    acc.create_grant(ALICE, BOB, cid=CID, scope='read')
    assert acc.can_read(BOB, CID) is True
    assert acc.can_read(CAROL, CID) is False   # grant is per-grantee


def test_grant_wildcard_all_objects(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.set_acl(CID2, owner=ALICE, backend='localfs')
    acc.create_grant(ALICE, BOB, cid='*')
    assert acc.can_read(BOB, CID) is True
    assert acc.can_read(BOB, CID2) is True


def test_grant_expiry(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    g = acc.create_grant(ALICE, BOB, cid=CID, ttl_seconds=1)
    assert acc.can_read(BOB, CID) is True
    # Force the grant into the past and re-check.
    conn = acc._db()
    conn.execute('UPDATE grants SET expires=? WHERE id=?', (int(time.time()) - 5, g['id']))
    conn.commit()
    conn.close()
    assert acc.can_read(BOB, CID) is False
    assert acc.grants_to(BOB, active_only=True) == []


def test_grant_write_scope(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.create_grant(ALICE, BOB, cid=CID, scope='read')
    assert acc.can_write(BOB, CID) is False     # read grant ≠ write
    acc.create_grant(ALICE, CAROL, cid=CID, scope='write')
    assert acc.can_write(CAROL, CID) is True


def test_grant_revoke(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    g = acc.create_grant(ALICE, BOB, cid=CID)
    assert acc.revoke_grant(g['id'], grantor=BOB) is False   # not the grantor
    assert acc.revoke_grant(g['id'], grantor=ALICE) is True
    assert acc.can_read(BOB, CID) is False


# ── data pools ─────────────────────────────────────────────────────

def test_pool_membership_and_mutual_access(acc):
    p = acc.create_pool(ALICE, name='research')
    pid = p['id']
    assert acc.member_role(pid, ALICE) == 'owner'
    acc.add_member(pid, BOB, role='editor')
    acc.add_member(pid, CAROL, role='viewer')
    # Alice pools a private object; every member can now read it.
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.add_object(pid, CID, added_by=ALICE)
    assert acc.can_read(BOB, CID) is True
    assert acc.can_read(CAROL, CID) is True
    # A non-member still cannot.
    assert acc.can_read('0x' + 'd' * 40, CID) is False


def test_pool_timed_membership(acc):
    p = acc.create_pool(ALICE)
    pid = p['id']
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.add_object(pid, CID, added_by=ALICE)
    acc.add_member(pid, BOB, role='viewer', ttl_seconds=1)
    assert acc.can_read(BOB, CID) is True
    conn = acc._db()
    conn.execute('UPDATE pool_members SET expires=? WHERE pool_id=? AND address=?',
                 (int(time.time()) - 5, pid, BOB))
    conn.commit()
    conn.close()
    assert acc.member_role(pid, BOB) is None      # expired
    assert acc.can_read(BOB, CID) is False         # access lapses with membership


def test_pool_list_and_counts(acc):
    p = acc.create_pool(ALICE, name='shared')
    pid = p['id']
    acc.add_member(pid, BOB)
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.add_object(pid, CID, added_by=ALICE)
    pools_for_bob = acc.list_pools_for(BOB)
    assert len(pools_for_bob) == 1
    assert pools_for_bob[0]['member_count'] == 2
    assert pools_for_bob[0]['object_count'] == 1
    assert pools_for_bob[0]['role'] == 'viewer'


def test_pool_remove_object(acc):
    p = acc.create_pool(ALICE)
    pid = p['id']
    acc.add_member(pid, BOB)
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.add_object(pid, CID, added_by=ALICE)
    assert acc.can_read(BOB, CID) is True
    acc.remove_object(pid, CID)
    assert acc.can_read(BOB, CID) is False


# ── QR auth handoff ────────────────────────────────────────────────

def test_handoff_single_use(acc):
    h = acc.create_handoff('TOKEN-XYZ', address=ALICE, ttl_seconds=120)
    code = h['code']
    claimed = acc.claim_handoff(code)
    assert claimed['token'] == 'TOKEN-XYZ'
    assert claimed['address'] == ALICE
    # Second claim must fail (single-use).
    assert acc.claim_handoff(code) is None


def test_handoff_expiry(acc):
    h = acc.create_handoff('TOKEN', address=ALICE, ttl_seconds=120)
    conn = acc._db()
    conn.execute('UPDATE handoffs SET expires=? WHERE code=?', (int(time.time()) - 5, h['code']))
    conn.commit()
    conn.close()
    assert acc.claim_handoff(h['code']) is None
    assert acc.claim_handoff('does-not-exist') is None


# ── housekeeping ───────────────────────────────────────────────────

def test_prune_removes_expired(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    g = acc.create_grant(ALICE, BOB, cid=CID, ttl_seconds=1)
    p = acc.create_pool(ALICE)
    acc.add_member(p['id'], CAROL, ttl_seconds=1)
    past = int(time.time()) - 5
    conn = acc._db()
    conn.execute('UPDATE grants SET expires=? WHERE id=?', (past, g['id']))
    conn.execute('UPDATE pool_members SET expires=? WHERE address=?', (past, CAROL))
    conn.commit()
    conn.close()
    res = acc.prune()
    assert res['grants'] == 1
    assert res['members'] == 1
    # Owner membership is never pruned.
    assert acc.member_role(p['id'], ALICE) == 'owner'


def test_pool_delete(acc):
    p = acc.create_pool(ALICE, name="temp")
    pid = p["id"]
    acc.add_member(pid, BOB)
    acc.set_acl(CID, owner=ALICE, backend="localfs")
    acc.add_object(pid, CID, added_by=ALICE)
    assert acc.delete_pool(pid) is True
    assert acc.get_pool(pid) is None
    assert acc.list_pools_for(BOB) == []
    assert acc.can_read(BOB, CID) is False   # pool access gone


# ── one-time tickets (single-use, anti-replay) ─────────────────────

def test_ticket_single_use(acc):
    t = acc.create_ticket(CID, backend="localfs", issuer=ALICE, ttl_seconds=10)
    assert t["expires_in"] == 10
    claimed = acc.claim_ticket(t["code"])
    assert claimed["cid"] == CID
    # Replay: second claim must fail.
    assert acc.claim_ticket(t["code"]) is None


def test_ticket_default_ttl_is_10(acc):
    t = acc.create_ticket(CID, issuer=ALICE)
    assert t["expires_in"] == 10


def test_ticket_expiry(acc):
    t = acc.create_ticket(CID, issuer=ALICE, ttl_seconds=10)
    conn = acc._db()
    conn.execute("UPDATE tickets SET expires=? WHERE code=?", (int(time.time()) - 1, t["code"]))
    conn.commit()
    conn.close()
    assert acc.claim_ticket(t["code"]) is None       # expired
    assert acc.claim_ticket("nonexistent") is None


def test_ticket_no_concurrent_double_claim(acc):
    """Two parallel claims of the same code → exactly one wins (anti-replay)."""
    import threading
    t = acc.create_ticket(CID, issuer=ALICE, ttl_seconds=30)
    results = []
    lock = threading.Lock()

    def grab():
        r = acc.claim_ticket(t["code"])
        with lock:
            results.append(r)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wins = [r for r in results if r is not None]
    assert len(wins) == 1


def test_tickets_by_active_only(acc):
    t1 = acc.create_ticket(CID, issuer=ALICE, ttl_seconds=30)
    acc.create_ticket(CID2, issuer=ALICE, ttl_seconds=30)
    acc.claim_ticket(t1["code"])                        # used → excluded
    active = acc.tickets_by(ALICE, active_only=True)
    assert len(active) == 1 and active[0]["cid"] == CID2


def test_pin_management(acc):
    acc.add_pin(CID, "localfs", owner=ALICE)
    acc.add_pin(CID2, "filecoin", owner=ALICE)
    assert acc.is_pinned(CID) is True
    assert len(acc.pins_for(ALICE)) == 2
    assert acc.remove_pin(CID) == 1
    assert acc.is_pinned(CID) is False
    assert len(acc.pins_for(ALICE)) == 1


def test_semhash_persist_and_query(acc):
    acc.set_acl(CID, owner=ALICE, backend="localfs", semhash="deadbeefdeadbeef")
    assert acc.get_acl(CID)["semhash"] == "deadbeefdeadbeef"
    # set_acl without semhash must preserve it.
    acc.set_acl(CID, owner=ALICE, backend="localfs")
    assert acc.get_acl(CID)["semhash"] == "deadbeefdeadbeef"
    assert acc.all_semhashes()[CID] == "deadbeefdeadbeef"


def test_grants_on_and_pools_containing(acc):
    acc.set_acl(CID, owner=ALICE, backend="localfs")
    acc.create_grant(ALICE, BOB, cid=CID)
    acc.create_grant(ALICE, CAROL, cid="*")
    on = acc.grants_on(CID, owner=ALICE)
    assert {g["grantee"] for g in on} == {BOB, CAROL}
    p = acc.create_pool(ALICE, name="pp")
    acc.add_object(p["id"], CID, added_by=ALICE)
    pcs = acc.pools_containing(CID)
    assert len(pcs) == 1 and pcs[0]["id"] == p["id"]


def test_shared_with_aggregates_grants_and_pools(acc):
    acc.set_acl(CID, owner=ALICE, backend='localfs')
    acc.set_acl(CID2, owner=CAROL, backend='localfs')
    acc.create_grant(ALICE, BOB, cid=CID)         # specific
    acc.create_grant(CAROL, BOB, cid='*')          # wildcard
    p = acc.create_pool(ALICE)
    acc.add_member(p['id'], BOB)
    pooled = 'bafkreih' + 'q' * 52
    acc.add_object(p['id'], pooled, added_by=ALICE)
    s = acc.shared_with(BOB)
    assert CID in s['cids']
    assert pooled in s['cids']
    assert CAROL.lower() in s['wildcard_grantors']
