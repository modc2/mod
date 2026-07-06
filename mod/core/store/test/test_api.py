"""End-to-end API tests through FastAPI's TestClient.

Auth verification is stubbed (token string == signer address) so we exercise the
real wiring — put → ACL → read-gating → grants → pools → handoff → register —
against the localfs backend (content-addressed, no daemon needed).
"""
import importlib
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
STORE = os.path.dirname(os.path.dirname(__file__))
sys.path[:0] = [REPO, STORE]

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import mod as m  # noqa: E402

ALICE = "0x" + "a" * 40
BOB = "0x" + "b" * 40
CAROL = "0x" + "c" * 40


@pytest.fixture
def client(monkeypatch):
    priv = tempfile.mkdtemp(prefix="store_priv_")
    store = tempfile.mkdtemp(prefix="store_data_")
    os.environ["STORE_PRIVATE_DIR"] = priv
    # Fresh import so module-level PRIVATE_DIR/ACCESS pick up the temp dir.
    import api.api as a
    importlib.reload(a)
    # Isolate the dstore index to a temp path and stub signature verification.
    a.store_mod = m.mod("dstore")(store_path=store)
    monkeypatch.setattr(a.AUTH, "verify", lambda token: {"key": token})
    return TestClient(a.app)


def H(addr):
    return {"Authorization": f"Bearer {addr}"}


def _upload(client, addr, content=b"hello", public=False, pool=None):
    data = {"backend": "localfs", "public": "true" if public else "false"}
    if pool:
        data["pool"] = pool
    r = client.post("/put", headers=H(addr),
                    files={"file": ("f.txt", content)}, data=data)
    assert r.status_code == 200, r.text
    return r.json()["results"]["localfs"]["cid"]


def test_put_private_by_default_then_publish(client):
    cid = _upload(client, ALICE)
    # Anonymous read of a private object is blocked.
    assert client.get(f"/get?cid={cid}").status_code == 403
    # Owner reads fine (token in query, mirroring a QR/link).
    assert client.get(f"/get?cid={cid}&token={ALICE}").status_code == 200
    # A stranger cannot.
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403
    # Publish → anonymous read now works.
    assert client.post("/publish", headers=H(ALICE), json={"cid": cid, "public": True}).status_code == 200
    assert client.get(f"/get?cid={cid}").status_code == 200


def test_public_upload_is_open(client):
    cid = _upload(client, ALICE, public=True)
    assert client.get(f"/get?cid={cid}").status_code == 200


def test_timed_grant_unlocks_read(client):
    cid = _upload(client, ALICE)
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403
    g = client.post("/grants", headers=H(ALICE),
                    json={"grantee": BOB, "cid": cid, "ttl_seconds": 3600})
    assert g.status_code == 200, g.text
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200
    # Bob sees it under /shared.
    shared = client.get("/shared", headers=H(BOB)).json()
    assert any(o["cid"] == cid for o in shared["objects"])
    # Revoke → blocked again.
    client.delete(f"/grants/{g.json()['id']}", headers=H(ALICE))
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403


def test_cannot_grant_object_you_dont_own(client):
    cid = _upload(client, ALICE)
    r = client.post("/grants", headers=H(BOB), json={"grantee": CAROL, "cid": cid})
    assert r.status_code == 403


def test_pool_mutual_access(client):
    cid = _upload(client, ALICE)
    pool = client.post("/pools", headers=H(ALICE), json={"name": "team"}).json()
    pid = pool["id"]
    client.post(f"/pools/{pid}/members", headers=H(ALICE), json={"address": BOB, "role": "viewer"})
    client.post(f"/pools/{pid}/objects", headers=H(ALICE), json={"cid": cid})
    # Bob, a member, can now read Alice's private object.
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200
    # Non-member cannot.
    assert client.get(f"/get?cid={cid}&token={CAROL}").status_code == 403
    # Bob (viewer) may not add members.
    assert client.post(f"/pools/{pid}/members", headers=H(BOB),
                       json={"address": CAROL}).status_code == 403


def test_pool_upload_direct(client):
    pool = client.post("/pools", headers=H(ALICE), json={"name": "p"}).json()
    pid = pool["id"]
    client.post(f"/pools/{pid}/members", headers=H(ALICE), json={"address": BOB})
    cid = _upload(client, ALICE, pool=pid)
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200


def test_handoff_roundtrip(client):
    r = client.post("/handoff", headers=H(ALICE), json={"ttl_seconds": 120}).json()
    claimed = client.get(f"/handoff/{r['code']}").json()
    assert claimed["token"] == ALICE
    # Single use.
    assert client.get(f"/handoff/{r['code']}").status_code == 404


def test_search_filters(client):
    c1 = _upload(client, ALICE, content=b"alpha")
    _upload(client, ALICE, content=b"beta")
    # publish c1 so we can filter by visibility
    client.post("/publish", headers=H(ALICE), json={"cid": c1, "public": True})
    r = client.get("/search?q=f.txt", headers=H(ALICE)).json()
    assert r["count"] >= 2
    pub = client.get("/search?visibility=public", headers=H(ALICE)).json()
    assert all(o["visibility"] == "public" for o in pub["objects"])
    assert any(o["cid"] == c1 for o in pub["objects"])


def test_ticket_one_time_serves_then_404(client):
    cid = _upload(client, ALICE)  # private
    # Stranger cannot mint a ticket for it.
    assert client.post("/tickets", headers=H(BOB), json={"cid": cid}).status_code == 403
    t = client.post("/tickets", headers=H(ALICE), json={"cid": cid, "ttl_seconds": 10}).json()
    assert t["expires_in"] == 10
    # First redemption serves the bytes (no auth needed — the code is the capability).
    r1 = client.get(f"/ticket/{t['code']}")
    assert r1.status_code == 200 and r1.content == b"hello"
    # Replay → 404.
    assert client.get(f"/ticket/{t['code']}").status_code == 404


def test_ticket_default_ttl_10(client):
    cid = _upload(client, ALICE)
    t = client.post("/tickets", headers=H(ALICE), json={"cid": cid}).json()
    assert t["expires_in"] == 10


def test_pool_delete_endpoint(client):
    cid = _upload(client, ALICE)
    pid = client.post("/pools", headers=H(ALICE), json={"name": "x"}).json()["id"]
    client.post(f"/pools/{pid}/members", headers=H(ALICE), json={"address": BOB})
    client.post(f"/pools/{pid}/objects", headers=H(ALICE), json={"cid": cid})
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200
    # Non-owner cannot delete.
    assert client.delete(f"/pools/{pid}", headers=H(BOB)).status_code == 403
    assert client.delete(f"/pools/{pid}", headers=H(ALICE)).status_code == 200
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403


def test_semantic_hash_and_search(client):
    c1 = _upload(client, ALICE, content=b"the quick brown fox jumps over the lazy dog")
    _upload(client, ALICE, content=b"a fast auburn fox leaps above the sleepy hound")
    c3 = _upload(client, ALICE, content=b"quarterly financial market trading report numbers")
    # every object got a 1-bit semantic hash
    objs = client.get("/list", headers=H(ALICE)).json()["objects"]
    assert all(o.get("semhash") for o in objs)
    # query closest to c1 → c1 ranks first and scores higher than the unrelated c3
    r = client.get("/search?semantic_q=the+quick+brown+fox", headers=H(ALICE)).json()
    by_cid = {o["cid"]: o for o in r["objects"]}
    assert r["objects"][0]["cid"] == c1
    assert r["objects"][0]["similarity"] is not None
    assert by_cid[c1]["similarity"] > by_cid[c3]["similarity"]
    # results are sorted by ascending Hamming distance
    dists = [o["distance"] for o in r["objects"]]
    assert dists == sorted(dists)


def test_pins_management(client):
    cid = _upload(client, ALICE)
    client.post("/pin", headers=H(ALICE), json={"cid": cid, "backend": "localfs"})
    pins = client.get("/pins", headers=H(ALICE)).json()["pins"]
    assert any(p["cid"] == cid for p in pins)
    client.request("DELETE", f"/pin?cid={cid}", headers=H(ALICE))
    assert all(p["cid"] != cid for p in client.get("/pins", headers=H(ALICE)).json()["pins"])


def test_preview_truncation_and_text(client):
    big = b"X" * 5000
    cid = _upload(client, ALICE, content=big)
    pv = client.get(f"/preview?cid={cid}&max_bytes=1000&token={ALICE}").json()
    assert pv["is_text"] is True and pv["size"] == 5000
    assert pv["truncated"] is True and len(pv["text"]) == 1000


def test_object_info_access_roster(client):
    cid = _upload(client, ALICE)  # private
    g = client.post("/grants", headers=H(ALICE), json={"grantee": BOB, "cid": cid, "ttl_seconds": 600})
    info = client.get(f"/object?cid={cid}", headers=H(ALICE)).json()
    assert info["owner"] == ALICE.lower()
    assert info["visibility"] == "private"
    assert info["stored_at"] is not None
    assert info["is_owner"] is True
    assert any(gr["grantee"] == BOB.lower() for gr in info["grants"])
    # Bob (a grantee) can read but only sees basics, not the roster.
    binfo = client.get(f"/object?cid={cid}&token={BOB}", headers={}).json() \
        if False else client.get(f"/object?cid={cid}", headers=H(BOB)).json()
    assert binfo["is_owner"] is False and binfo["grants"] == []


def test_bloctime_holder_is_authorized(client, monkeypatch):
    """A BlocTime holder is treated as whitelisted even with an owner set."""
    import api.api as a
    # Set an owner so access is no longer open, and whitelist nobody.
    (a.PRIVATE_DIR / "owner.json").write_text('{"owner": "%s"}' % ("0x" + "f" * 40))
    # CAROL is not the owner / not whitelisted → blocked from /put …
    assert client.post("/put", headers=H(CAROL), files={"file": ("f.txt", b"x")},
                       data={"backend": "localfs"}).status_code == 403
    # … until BlocTime says she's a holder.
    monkeypatch.setattr(a.ONCHAIN, "is_bloctime_holder", lambda addr: addr == CAROL)
    r = client.post("/put", headers=H(CAROL), files={"file": ("f.txt", b"x")}, data={"backend": "localfs"})
    assert r.status_code == 200, r.text
    me = client.get("/me", headers=H(CAROL)).json()
    assert me["bloctime"] is True and me["via"] == "bloctime" and me["authorized"] is True


def test_onchain_status_degrades_gracefully(client, monkeypatch):
    import api.api as a
    # No real chain here → status reports unregistered with an error, never raises.
    monkeypatch.setattr(a.ONCHAIN, "chain", lambda: (_ for _ in ()).throw(RuntimeError("no rpc")))
    s = client.get("/onchain").json()
    assert s["registered"] is False and s["network"] == "testnet"


def test_register_external_cid_agnostic(client):
    body = {"cid": "ar://tx123", "url": "https://arweave.net/tx123", "public": True}
    r = client.post("/register", headers=H(ALICE), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["scheme"] == "arweave"
    # Shows up in the owner's listing with scheme annotated.
    objs = client.get("/list", headers=H(ALICE)).json()["objects"]
    assert any(o["cid"] == "ar://tx123" and o["scheme"] == "arweave" for o in objs)
    # /get redirects to the external gateway.
    resp = client.get("/get?cid=ar://tx123", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "arweave.net" in resp.headers["location"]


def test_json_manifest_maps_to_referenced_cids(client):
    """A JSON object whose content embeds a CID it was built from shows up as
    a graph edge in both directions once uploaded."""
    part_cid = _upload(client, ALICE, content=b"part one", public=True)
    manifest = ('{"parts": ["%s"]}' % part_cid).encode()
    r = client.post("/put", headers=H(ALICE), files={"file": ("bundle.json", manifest)},
                    data={"backend": "localfs", "public": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["refs"] == [part_cid]
    manifest_cid = r.json()["results"]["localfs"]["cid"]

    # The manifest's graph points OUT to the part it references…
    minfo = client.get(f"/object?cid={manifest_cid}", headers=H(ALICE)).json()
    assert [l["cid"] for l in minfo["links"]["out"]] == [part_cid]
    assert minfo["links"]["in"] == []

    # …and the part's graph shows the manifest pointing IN at it.
    pinfo = client.get(f"/object?cid={part_cid}", headers=H(ALICE)).json()
    assert [l["cid"] for l in pinfo["links"]["in"]] == [manifest_cid]
    assert pinfo["links"]["out"] == []


def test_graph_hides_private_refs_from_non_owner(client):
    """The graph must not leak the existence of a private object to someone
    who can't otherwise read it."""
    part_cid = _upload(client, ALICE, content=b"secret part")  # private
    manifest = ('{"parts": ["%s"]}' % part_cid).encode()
    r = client.post("/put", headers=H(ALICE), files={"file": ("bundle.json", manifest)},
                    data={"backend": "localfs", "public": "true"})
    manifest_cid = r.json()["results"]["localfs"]["cid"]

    # Bob can read the (public) manifest but not the private part it links to.
    minfo = client.get(f"/object?cid={manifest_cid}", headers=H(BOB)).json()
    assert minfo["links"]["out"] == []
    # Alice, the owner, sees the full edge.
    aowner = client.get(f"/object?cid={manifest_cid}", headers=H(ALICE)).json()
    assert [l["cid"] for l in aowner["links"]["out"]] == [part_cid]
