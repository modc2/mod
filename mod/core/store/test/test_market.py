"""Marketplace tests: list → browse → like → acquire (free + BlocTime-priced)
→ delist/takedown. Auth verification is stubbed exactly like test_api.py."""
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

ALICE = "0x" + "a" * 40   # seller
BOB = "0x" + "b" * 40     # buyer
CAROL = "0x" + "c" * 40   # admin (owner) in the takedown test


@pytest.fixture
def a(monkeypatch):
    priv = tempfile.mkdtemp(prefix="store_priv_")
    store = tempfile.mkdtemp(prefix="store_data_")
    os.environ["STORE_PRIVATE_DIR"] = priv
    import api.api as a
    importlib.reload(a)
    a.store_mod = m.mod("dstore")(store_path=store)
    monkeypatch.setattr(a.AUTH, "verify", lambda token: {"key": token})
    # Deterministic chain: nobody holds BlocTime unless a test overrides this.
    monkeypatch.setattr(a.ONCHAIN, "bloctime_balance", lambda addr: 0)
    monkeypatch.setattr(a.ONCHAIN, "is_bloctime_holder", lambda addr: False)
    return a


@pytest.fixture
def client(a):
    return TestClient(a.app)


def H(addr):
    return {"Authorization": f"Bearer {addr}"}


def _upload(client, addr, content=b"hello", public=False):
    client.post("/terms/accept", headers=H(addr))
    r = client.post("/put", headers=H(addr),
                    files={"file": ("f.txt", content)},
                    data={"backend": "localfs", "public": "true" if public else "false"})
    assert r.status_code == 200, r.text
    return r.json()["results"]["localfs"]["cid"]


def _list(client, addr, cid, **kw):
    body = {"cid": cid, "title": "dope drop", **kw}
    r = client.post("/market/list", json=body, headers=H(addr))
    assert r.status_code == 200, r.text
    return r.json()


def test_list_browse_anonymous(client):
    cid = _upload(client, ALICE, public=True)
    _list(client, ALICE, cid, description="fresh bytes", tags=["art", "data"])
    r = client.get("/market").json()
    assert r["count"] == 1
    l = r["listings"][0]
    assert l["title"] == "dope drop" and l["seller"] == ALICE
    assert l["tags"] == ["art", "data"] and r["tags"] == {"art": 1, "data": 1}
    assert l["price_bloc"] == 0 and l["can_read"] is True  # public + free


def test_cannot_list_object_you_dont_own(client):
    cid = _upload(client, ALICE)
    client.post("/terms/accept", headers=H(BOB))
    r = client.post("/market/list", json={"cid": cid, "title": "steal"}, headers=H(BOB))
    assert r.status_code == 403


def test_free_acquire_grants_read(client):
    cid = _upload(client, ALICE)  # private
    _list(client, ALICE, cid)
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403
    r = client.post("/market/acquire", json={"cid": cid}, headers=H(BOB))
    assert r.status_code == 200 and r.json()["acquired"] is True
    # The grant is real: read works, and it shows under /shared + /market/mine.
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200
    mine = client.get("/market/mine", headers=H(BOB)).json()
    assert [x["cid"] for x in mine["acquired"]] == [cid]
    # Downloads counted once per unique acquirer.
    client.post("/market/acquire", json={"cid": cid}, headers=H(BOB))
    assert client.get("/market").json()["listings"][0]["downloads"] == 1


def test_priced_acquire_needs_bloctime(client, a, monkeypatch):
    cid = _upload(client, ALICE)
    _list(client, ALICE, cid, price_bloc=5)
    r = client.post("/market/acquire", json={"cid": cid}, headers=H(BOB))
    assert r.status_code == 402
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 403
    # Bob stakes up: holdings ARE the ticket.
    monkeypatch.setattr(a.ONCHAIN, "bloctime_balance", lambda addr: 9.0)
    r = client.post("/market/acquire", json={"cid": cid}, headers=H(BOB))
    assert r.status_code == 200
    assert client.get(f"/get?cid={cid}&token={BOB}").status_code == 200


def test_like_toggle_and_sorting(client):
    c1 = _upload(client, ALICE, content=b"one", public=True)
    c2 = _upload(client, ALICE, content=b"two", public=True)
    _list(client, ALICE, c1, title="first")
    _list(client, ALICE, c2, title="second")
    r = client.post("/market/like", json={"cid": c2}, headers=H(BOB))
    assert r.json() == {"cid": c2, "liked": True, "likes": 1}
    top = client.get("/market?sort=top").json()["listings"]
    assert top[0]["cid"] == c2 and top[0]["likes"] == 1
    assert client.get("/market", headers=H(BOB)).json()["listings"][0]["liked"] is True
    # Toggle off.
    assert client.post("/market/like", json={"cid": c2}, headers=H(BOB)).json()["liked"] is False


def test_delist_and_admin_takedown(client, a):
    cid = _upload(client, ALICE, public=True)
    _list(client, ALICE, cid)
    # A stranger can't delist.
    assert client.delete(f"/market/list?cid={cid}", headers=H(BOB)).status_code == 403
    # Make CAROL the admin → moderation delist works and is audit-logged.
    (a.PRIVATE_DIR / "owner.json").write_text('{"owner": "%s"}' % CAROL)
    r = client.delete(f"/market/list?cid={cid}&reason=spam", headers=H(CAROL))
    assert r.status_code == 200 and r.json()["takedown"] is True
    assert client.get("/market").json()["count"] == 0
    td = client.get("/takedowns", headers=H(CAROL)).json()
    assert td["count"] == 1 and td["takedowns"][0]["reason"] == "spam"


def test_search_and_filters(client):
    c1 = _upload(client, ALICE, content=b"one", public=True)
    c2 = _upload(client, ALICE, content=b"two", public=True)
    _list(client, ALICE, c1, title="neon skyline pack", tags=["art"])
    _list(client, ALICE, c2, title="orderbook dataset", tags=["data"], price_bloc=2)
    assert client.get("/market?q=skyline").json()["count"] == 1
    assert client.get("/market?tag=data").json()["count"] == 1
    free = client.get("/market?free=1").json()
    assert free["count"] == 1 and free["listings"][0]["cid"] == c1
