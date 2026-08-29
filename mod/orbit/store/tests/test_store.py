"""
What has to be true.

The interesting tests are the ones about the grant: that a code works once,
that it stops working on the clock whether or not anyone used it, that two
callers racing for the same code produce exactly one winner, and that looking
at a code does not spend it. Everything else is bookkeeping around those.
"""
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer

import pytest

from src import grants, library
from src.library import StoreError

ALICE = '0xalice'
BOB = '0xbob'


def png(width=2, height=2, red=0xff):
    """A real PNG — small, but a decoder will accept it."""
    def chunk(tag, data):
        body = tag + data
        return (struct.pack('>I', len(data)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xffffffff))
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes([red, 0, 0, 255]) * width
                   for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


# ── what it will and will not store ──────────────────────────────────

def test_stores_a_png_and_addresses_it_by_content():
    record = library.put(png(), name='a.png', owner=ALICE)
    assert record['mime'] == 'image/png'
    assert len(record['id']) == 64
    assert library.read(record['id']) == png()


@pytest.mark.parametrize('payload', [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'not an image at all',
    b'%PDF-1.4 ...',
])
def test_refuses_anything_that_is_not_an_inert_image(payload):
    """SVG especially: it is a document that can carry script."""
    with pytest.raises(StoreError) as caught:
        library.put(payload, owner=ALICE)
    assert caught.value.status == 415


def test_a_png_renamed_to_jpg_is_still_stored_as_a_png():
    """The format comes from the bytes, never from the name."""
    record = library.put(png(), name='lies.jpg', owner=ALICE)
    assert record['mime'] == 'image/png'


def test_refuses_an_upload_over_the_ceiling(monkeypatch):
    monkeypatch.setattr(library, 'MAX_BYTES', 10)
    with pytest.raises(StoreError) as caught:
        library.put(png(), owner=ALICE)
    assert caught.value.status == 413


def test_empty_upload_is_refused():
    with pytest.raises(StoreError):
        library.put(b'', owner=ALICE)


# ── one blob, one row per owner ──────────────────────────────────────

def test_the_same_bytes_twice_from_one_owner_is_one_row():
    first = library.put(png(), name='a.png', owner=ALICE)
    second = library.put(png(), name='again.png', owner=ALICE)
    assert first['id'] == second['id']
    assert second['name'] == 'a.png'          # the first name wins
    assert len(library.listing(ALICE)) == 1


def test_two_owners_of_the_same_picture_get_their_own_rows():
    library.put(png(), owner=ALICE)
    library.put(png(), owner=BOB)
    assert len(library.listing(ALICE)) == 1
    assert len(library.listing(BOB)) == 1
    assert library.stats()['images'] == 1     # but one blob on disk


def test_one_owner_deleting_does_not_take_the_other_owners_bytes():
    record = library.put(png(), owner=ALICE)
    library.put(png(), owner=BOB)

    result = library.remove(record['id'], ALICE)
    assert result['blob_removed'] is False
    assert library.blob_path(record['id']).exists()
    assert library.read(record['id'])         # Bob's copy still resolves

    result = library.remove(record['id'], BOB)
    assert result['blob_removed'] is True
    assert not library.blob_path(record['id']).exists()


def test_you_cannot_delete_or_publish_someone_elses_row():
    record = library.put(png(), owner=ALICE)
    for call in (lambda: library.remove(record['id'], BOB),
                 lambda: library.publish(record['id'], BOB)):
        with pytest.raises(StoreError) as caught:
            call()
        assert caught.value.status == 404


# ── publishing ───────────────────────────────────────────────────────

def test_private_by_default_and_public_when_published():
    record = library.put(png(), owner=ALICE)
    assert record['public'] is False
    assert library.public_record(record['id']) is None

    library.publish(record['id'], ALICE)
    assert library.public_record(record['id'])['public'] is True

    library.publish(record['id'], ALICE, False)
    assert library.public_record(record['id']) is None


# ── grants: the part that has to be right ────────────────────────────

def test_a_grant_works_exactly_once():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)

    claimed = grants.claim(grant['code'])
    assert claimed['image'] == record['id']

    with pytest.raises(StoreError) as caught:
        grants.claim(grant['code'])
    assert caught.value.status == 410
    assert 'already been used' in str(caught.value)


def test_a_grant_dies_on_the_clock_even_if_nobody_used_it():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)

    conn = library.connect()
    conn.execute('UPDATE grants SET expires=? WHERE code=?',
                 (time.time() - 1, grant['code']))
    conn.close()

    with pytest.raises(StoreError) as caught:
        grants.claim(grant['code'])
    assert caught.value.status == 410
    assert 'expired' in str(caught.value)


def test_racing_claims_produce_exactly_one_winner():
    """
    Twenty threads, one code. This is the case a read-then-write gets wrong,
    and it is the ordinary case: a QR code on a screen in front of a room.
    """
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)

    winners, losers = [], []
    start = threading.Barrier(20)

    def go():
        start.wait()
        try:
            winners.append(grants.claim(grant['code']))
        except StoreError as error:
            losers.append(error)

    threads = [threading.Thread(target=go) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    assert len(losers) == 19
    assert all(error.status == 410 for error in losers)


def test_peeking_at_a_code_does_not_spend_it():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)

    assert grants.peek(grant['code'])['live'] is True
    assert grants.peek(grant['code'])['live'] is True
    assert grants.claim(grant['code'])           # still claimable


def test_a_spent_code_says_used_rather_than_unknown():
    """410 not 404 — the holder can tell 'beaten to it' from 'typo'."""
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)
    grants.claim(grant['code'])

    assert grants.peek(grant['code'])['claimed'] is True
    with pytest.raises(StoreError) as caught:
        grants.claim(grant['code'])
    assert caught.value.status == 410

    with pytest.raises(StoreError) as caught:
        grants.claim('a-code-that-never-existed')
    assert caught.value.status == 404


def test_a_grant_does_not_need_the_image_to_be_public():
    """The whole point: share one picture without publishing it."""
    record = library.put(png(), owner=ALICE)
    assert library.public_record(record['id']) is None
    grant = grants.create(record['id'], ALICE, 60)
    assert grants.claim(grant['code'])['image'] == record['id']


def test_you_cannot_mint_a_grant_over_someone_elses_picture():
    record = library.put(png(), owner=ALICE)
    with pytest.raises(StoreError) as caught:
        grants.create(record['id'], BOB, 60)
    assert caught.value.status == 404


@pytest.mark.parametrize('ttl', [0, -5, grants.MAX_TTL + 1, 'soon', None])
def test_ttl_is_bounded(ttl):
    record = library.put(png(), owner=ALICE)
    with pytest.raises(StoreError) as caught:
        grants.create(record['id'], ALICE, ttl)
    assert caught.value.status == 400


def test_revoking_kills_a_live_code():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)
    grants.revoke(grant['code'], ALICE)
    with pytest.raises(StoreError) as caught:
        grants.claim(grant['code'])
    assert caught.value.status == 404


def test_only_the_owner_revokes():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)
    with pytest.raises(StoreError):
        grants.revoke(grant['code'], BOB)
    assert grants.peek(grant['code'])['live'] is True


def test_listing_shows_live_codes_only_unless_asked():
    record = library.put(png(), owner=ALICE)
    spent = grants.create(record['id'], ALICE, 60)
    grants.create(record['id'], ALICE, 60)
    grants.claim(spent['code'])

    assert len(grants.listing(ALICE)) == 1
    assert len(grants.listing(ALICE, include_dead=True)) == 2


def test_deleting_a_picture_takes_its_grants_with_it():
    record = library.put(png(), owner=ALICE)
    grant = grants.create(record['id'], ALICE, 60)
    library.remove(record['id'], ALICE)
    with pytest.raises(StoreError) as caught:
        grants.claim(grant['code'])
    assert caught.value.status == 404


def test_sweep_forgets_only_what_stopped_mattering():
    record = library.put(png(), owner=ALICE)
    live = grants.create(record['id'], ALICE, 3600)
    old = grants.create(record['id'], ALICE, 60)

    conn = library.connect()
    conn.execute('UPDATE grants SET expires=? WHERE code=?',
                 (time.time() - 30 * 86400, old['code']))
    conn.close()

    assert grants.sweep()['swept'] == 1
    assert grants.peek(old['code']) is None
    assert grants.peek(live['code'])['live'] is True


# ── over HTTP ────────────────────────────────────────────────────────

@pytest.fixture
def server():
    """The real API on a loopback port, so anonymous == the local owner."""
    from src.api.api import Handler
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()
    httpd.server_close()


def fetch(url, method='GET', data=None, headers=None):
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers or {})


def test_http_upload_grant_and_single_claim(server):
    status, body, _ = fetch(f'{server}/upload?name=a.png', 'POST', png())
    assert status == 201
    import json
    image_id = json.loads(body)['id']

    status, body, _ = fetch(f'{server}/grant', 'POST',
                            json.dumps({'id': image_id,
                                        'ttl_seconds': 30}).encode())
    assert status == 201
    grant = json.loads(body)
    assert grant['url'].endswith(grant['code'])

    status, body, headers = fetch(f'{server}/g/{grant["code"]}')
    assert status == 200
    assert body == png()
    assert headers['Content-Type'] == 'image/png'
    assert 'no-store' in headers['Cache-Control']
    assert headers['X-Content-Type-Options'] == 'nosniff'

    status, _, _ = fetch(f'{server}/g/{grant["code"]}')
    assert status == 410


def test_head_does_not_burn_a_grant(server):
    """A link checker must not spend the code."""
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']
    _, body, _ = fetch(f'{server}/grant', 'POST',
                       json.dumps({'id': image_id}).encode())
    code = json.loads(body)['code']

    assert fetch(f'{server}/g/{code}', 'HEAD')[0] == 200
    assert fetch(f'{server}/g/{code}', 'HEAD')[0] == 200
    assert fetch(f'{server}/g/{code}')[0] == 200      # still there
    assert fetch(f'{server}/g/{code}')[0] == 410      # now spent


def test_rendering_the_qr_does_not_burn_the_grant(server):
    import json
    from src import qr
    if not qr.available():
        pytest.skip('no segno on this box')
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']
    _, body, _ = fetch(f'{server}/grant', 'POST',
                       json.dumps({'id': image_id}).encode())
    code = json.loads(body)['code']

    status, svg, headers = fetch(f'{server}/g/{code}/qr')
    assert status == 200
    assert headers['Content-Type'] == 'image/svg+xml'
    assert b'<svg' in svg
    assert fetch(f'{server}/g/{code}')[0] == 200      # unspent


def test_private_bytes_are_not_served_and_do_not_leak_their_existence(server):
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']

    private = fetch(f'{server}/i/{image_id}')[0]
    never_existed = fetch(f'{server}/i/{"0" * 64}')[0]
    assert private == never_existed == 404

    fetch(f'{server}/publish', 'POST', json.dumps({'id': image_id}).encode())
    status, served, _ = fetch(f'{server}/i/{image_id}')
    assert status == 200 and served == png()


def test_the_api_refuses_an_oversized_body_on_the_header(server, monkeypatch):
    monkeypatch.setattr(library, 'MAX_BYTES', 10)
    assert fetch(f'{server}/upload', 'POST', png())[0] == 413


# ── the page a scan lands on ──────────────────────────────────────────
# The point of the page is that it is inert: it can be fetched by a link
# preview, a prefetcher, a scanner that opens the URL twice, and the code is
# still worth exactly one look afterwards.

def test_the_qr_and_the_share_url_point_at_the_page_not_the_bytes(server):
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']
    _, body, _ = fetch(f'{server}/grant', 'POST',
                       json.dumps({'id': image_id}).encode())
    grant = json.loads(body)

    assert grant['url'] == grant['page_url']
    assert grant['page_url'].endswith(f'/v/{grant["code"]}')
    assert grant['bytes_url'].endswith(f'/g/{grant["code"]}')

    from src import qr
    if qr.available():
        # The picture of the link has to encode the SAME link the copy button
        # hands over, or the person who scans and the person who pastes get
        # two different things — and one of them spends the code on sight.
        assert grant['qr_svg'] == qr.svg(grant['page_url'])
        assert grant['qr_svg'] != qr.svg(grant['bytes_url'])


def test_opening_the_page_does_not_spend_the_code(server):
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']
    _, body, _ = fetch(f'{server}/grant', 'POST',
                       json.dumps({'id': image_id}).encode())
    code = json.loads(body)['code']

    for _ in range(3):
        status, page, headers = fetch(f'{server}/v/{code}')
        assert status == 200
        assert headers['Content-Type'].startswith('text/html')
        assert b'<!doctype html>' in page.lower()
    assert grants.peek(code)['live'] is True
    assert fetch(f'{server}/g/{code}')[0] == 200     # the one look, unspent
    assert fetch(f'{server}/g/{code}')[0] == 410


def test_the_page_says_nothing_about_whether_the_code_is_real(server):
    """Otherwise the page becomes the probe the bytes route refuses to be."""
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']
    _, body, _ = fetch(f'{server}/grant', 'POST',
                       json.dumps({'id': image_id}).encode())
    code = json.loads(body)['code']

    real = fetch(f'{server}/v/{code}')
    invented = fetch(f'{server}/v/nosuchcodeatall')
    assert real[0] == invented[0] == 200
    assert real[1] == invented[1]

    published = fetch(f'{server}/p/{image_id}')
    never = fetch(f'{server}/p/{"0" * 64}')
    assert published[0] == never[0] == 200
    assert published[1] == never[1]


def test_a_published_record_carries_both_a_page_and_the_bytes(server):
    import json
    _, body, _ = fetch(f'{server}/upload?public=true', 'POST', png())
    record = json.loads(body)
    assert record['page_url'].endswith(f'/p/{record["id"]}')
    assert record['bytes_url'].endswith(f'/i/{record["id"]}')
    assert record['url'] == record['page_url']

    _, body, _ = fetch(f'{server}/upload?name=b.png', 'POST', png(red=0x11))
    private = json.loads(body)
    assert 'url' not in private and 'page_url' not in private


# ── looking at your own private picture ───────────────────────────────

def test_you_can_read_the_bytes_of_a_picture_you_own(server):
    """The console needs this: without it the owner of a private picture is
    the one person who cannot see it."""
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    image_id = json.loads(body)['id']

    status, served, headers = fetch(f'{server}/b/{image_id}')
    assert status == 200 and served == png()
    assert 'no-store' in headers['Cache-Control']
    assert headers['X-Content-Type-Options'] == 'nosniff'
    # ...and it is not the public door: /i/ still refuses the same id.
    assert fetch(f'{server}/i/{image_id}')[0] == 404
    assert fetch(f'{server}/b/{"0" * 64}')[0] == 404


# ── mcp ───────────────────────────────────────────────────────────────

def rpc(server, method, params=None, id_=1):
    import json
    body = json.dumps({'jsonrpc': '2.0', 'id': id_, 'method': method,
                       'params': params or {}}).encode()
    status, raw, _ = fetch(f'{server}/mcp', 'POST', body,
                           {'Content-Type': 'application/json'})
    return status, json.loads(raw or b'{}')


def tool(server_url, tool_name, **arguments):
    import json
    status, response = rpc(server_url, 'tools/call',
                           {'name': tool_name, 'arguments': arguments})
    assert status == 200, response
    result = response['result']
    if result['isError']:
        return {'error': result['content'][0]['text']}
    # `structuredContent` is what a client reads when it wants the data, and
    # content[0] is no longer reliably the JSON — tools that return a picture
    # put the image block first, on purpose.
    if 'structuredContent' in result:
        return result['structuredContent']
    text = next(b['text'] for b in result['content'] if b['type'] == 'text')
    return json.loads(text)


def test_mcp_initializes_and_lists_its_tools(server):
    status, response = rpc(server, 'initialize',
                           {'protocolVersion': '2025-06-18'})
    assert status == 200
    assert response['result']['protocolVersion'] == '2025-06-18'
    assert response['result']['serverInfo']['name'] == 'store'

    _, response = rpc(server, 'tools/list')
    names = {t['name'] for t in response['result']['tools']}
    assert {'store_add', 'store_grant', 'store_peek', 'store_claim'} <= names
    for schema in response['result']['tools']:
        assert schema['inputSchema']['type'] == 'object'
        assert schema['description']

    # A notification is answered with nothing at all, not with an error.
    import json
    status, raw, _ = fetch(f'{server}/mcp', 'POST',
                           json.dumps({'jsonrpc': '2.0',
                                       'method': 'notifications/initialized'}).encode(),
                           {'Content-Type': 'application/json'})
    assert status == 202 and raw == b''


def test_mcp_shares_a_picture_end_to_end(server):
    import base64
    added = tool(server, 'store_add',
                 base64=base64.b64encode(png()).decode(), name='via-mcp.png')
    assert added['mime'] == 'image/png'
    assert not added['public']

    grant = tool(server, 'store_grant', id=added['id'], ttl_seconds=30)
    assert grant['show_the_person'] == grant['page_url']
    assert grant['page_url'].endswith(f'/v/{grant["code"]}')

    # Peeking is the safe question and claiming is the destructive one, and
    # the split has to survive: peek twice, then claim, then peek again.
    assert tool(server, 'store_peek', code=grant['code'])['live'] is True
    assert tool(server, 'store_peek', code=grant['code'])['live'] is True
    claimed = tool(server, 'store_claim', code=grant['code'])
    assert claimed['burned'] is True and claimed['bytes'] == len(png())
    assert tool(server, 'store_claim', code=grant['code'])['error']
    assert tool(server, 'store_peek', code=grant['code'])['claimed'] is True


def test_mcp_refusals_come_back_as_readable_tool_errors(server):
    """A model should get the sentence, not a transport failure."""
    answer = tool(server, 'store_grant', id='0' * 64)
    assert 'nothing here matches' in answer['error']

    status, response = rpc(server, 'tools/call',
                           {'name': 'store_nonsense', 'arguments': {}})
    assert status == 200 and response['error']['code'] == -32602


def test_mcp_never_hands_over_someone_elses_pictures(server):
    """The MCP layer does not re-implement ownership — it is handed an owner
    and every read is scoped to it, exactly like the browser's."""
    import json
    _, body, _ = fetch(f'{server}/upload', 'POST', png())
    mine = json.loads(body)['id']
    library.put(png(red=0x22), name='theirs.png', owner='0xsomeoneelse')

    ids = {image['id'] for image in tool(server, 'store_images')['images']}
    assert mine in ids and len(ids) == 1


# ── the documentation is generated, not typed twice ───────────────────

def test_the_docs_describe_the_routes_that_actually_exist(server):
    import json
    _, raw, _ = fetch(f'{server}/docs')
    document = json.loads(raw)
    paths = {(e['method'], e['path']) for e in document['endpoints']}
    assert ('GET', '/v/{code}') in paths and ('GET', '/g/{code}') in paths
    assert ('GET', '/p/{id}') in paths and ('GET', '/i/{id}') in paths
    assert document['sharing']['ways'][1]['page'] == '/v/<code>'
    assert document['mcp']['tools']

    _, raw, _ = fetch(f'{server}/docs?section=sharing')
    assert set(json.loads(raw)) == {'sharing'}
    assert fetch(f'{server}/docs?section=nope')[0] == 404


def test_the_mcp_schema_endpoint_matches_the_tools_it_serves(server):
    import json
    _, raw, _ = fetch(f'{server}/mcp/schema')
    schema = json.loads(raw)
    _, listed = rpc(server, 'tools/list')
    assert [t['name'] for t in schema['tools']] == \
           [t['name'] for t in listed['result']['tools']]
    assert schema['client_config']['mcpServers']['store']['args'][0].endswith(
        'mcp.py')
    # GET /mcp is a mistake worth a sentence rather than a 404.
    assert fetch(f'{server}/mcp')[0] == 405


# ── saying which picture, and for how long ───────────────────────────
#
# The resolver is the whole difference between a module you can use by hand
# and one you can only use by pasting hashes, so the cases that matter are the
# ambiguous ones: it must refuse rather than pick, because the verbs behind it
# publish permanently and delete permanently.

def test_a_picture_can_be_named_by_prefix_name_or_latest():
    from src import resolve
    first = library.put(png(red=0x11), name='sunset.png', owner=ALICE)
    time.sleep(0.01)
    second = library.put(png(red=0x22), name='ocean.png', owner=ALICE)

    assert resolve.image(first['id'], ALICE) == first['id']
    assert resolve.image(first['id'][:8], ALICE) == first['id']
    assert resolve.image('sunset.png', ALICE) == first['id']
    assert resolve.image('SUNSET.PNG', ALICE) == first['id']
    assert resolve.image('latest', ALICE) == second['id']
    assert resolve.image('last', ALICE) == second['id']


def test_an_ambiguous_reference_is_an_error_naming_the_candidates():
    """Never a guess: the caller may have been about to delete something."""
    from src import resolve
    library.put(png(red=0x11), name='shot.png', owner=ALICE)
    library.put(png(red=0x22), name='shot.png', owner=ALICE)
    with pytest.raises(StoreError) as caught:
        resolve.image('shot.png', ALICE)
    assert caught.value.status == 409
    assert 'matches 2 pictures' in str(caught.value)


def test_a_reference_never_reaches_somebody_elses_private_picture():
    from src import resolve
    mine = library.put(png(red=0x11), name='mine.png', owner=ALICE)
    library.put(png(red=0x22), name='theirs.png', owner=BOB)

    assert resolve.image('mine.png', ALICE) == mine['id']
    with pytest.raises(StoreError) as caught:
        resolve.image('theirs.png', ALICE)
    assert caught.value.status == 404


def test_a_published_picture_is_reachable_by_reference_only_when_asked_for():
    from src import resolve
    theirs = library.put(png(red=0x33), name='open.png', owner=BOB, public=True)
    # The read-only callers may see it; the ones that change something may not.
    assert resolve.image('open.png', ALICE, public_too=True) == theirs['id']
    with pytest.raises(StoreError):
        resolve.image('open.png', ALICE)


def test_a_prefix_shorter_than_four_is_refused_rather_than_matched():
    from src import resolve
    library.put(png(), name='a.png', owner=ALICE)
    with pytest.raises(StoreError) as caught:
        resolve.image('ab', ALICE)
    assert caught.value.status == 400


@pytest.mark.parametrize('written,seconds', [
    (30, 30), ('30', 30), ('30s', 30), ('5m', 300), ('2h', 7200), ('1d', 86400),
    ('  90  ', 90), ('1M', 60),
])
def test_durations_can_be_written_the_way_people_say_them(written, seconds):
    from src import resolve
    assert resolve.ttl(written) == seconds


@pytest.mark.parametrize('written', ['9days', 'soon', '', None])
def test_a_duration_that_is_not_one_falls_back_or_refuses(written):
    from src import resolve
    if written in ('', None):
        assert resolve.ttl(written) == grants.DEFAULT_TTL
    else:
        with pytest.raises(StoreError):
            resolve.ttl(written)


@pytest.mark.parametrize('written', ['0s', '2d', '-5'])
def test_a_duration_outside_the_bounds_says_what_the_bounds_are(written):
    from src import resolve
    with pytest.raises(StoreError) as caught:
        resolve.ttl(written)
    assert '1s to 1d' in str(caught.value)


def test_a_code_prefix_resolves_only_among_your_own_grants():
    from src import resolve
    mine = library.put(png(), name='a.png', owner=ALICE)
    code = grants.create(mine['id'], ALICE, 60)['code']

    assert resolve.code(code[:8], ALICE) == code
    # Bob holds the whole code — he just cannot go prefix-hunting for it.
    with pytest.raises(StoreError) as caught:
        resolve.code(code[:8], BOB)
    assert caught.value.status == 404


def test_a_live_code_wins_a_prefix_over_a_spent_one():
    """`revoke abc1` means the one you are still worried about."""
    from src import resolve
    image = library.put(png(), name='a.png', owner=ALICE)
    conn = library.connect()
    try:
        now = time.time()
        for code, claimed in (('zzzz-spent', now), ('zzzz-live', None)):
            conn.execute(
                'INSERT INTO grants (code, image, owner, ttl, created, '
                'expires, claimed) VALUES (?,?,?,?,?,?,?)',
                (code, image['id'], ALICE, 60, now, now + 60, claimed))
    finally:
        conn.close()
    assert resolve.code('zzzz', ALICE) == 'zzzz-live'


# ── the QR that used to be null ──────────────────────────────────────

def test_ascii_qr_is_returned_rather_than_printed():
    """segno's terminal() writes to a stream and returns None."""
    from src import qr
    if not qr.available():
        pytest.skip('segno is not installed')
    art = qr.ascii_art('http://example.test/v/abc')
    assert isinstance(art, str) and '\n' in art and len(art) > 100


# ── the MCP surface an agent actually meets ──────────────────────────

def test_mcp_accepts_a_batch_and_answers_only_the_requests(server):
    import json
    status, body, _ = fetch(f'{server}/mcp', 'POST', json.dumps([
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-06-18'}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    ]).encode(), {'Content-Type': 'application/json'})
    assert status == 200
    replies = json.loads(body)
    assert isinstance(replies, list)
    assert [r['id'] for r in replies] == [1, 2]      # the notification is silent


def test_a_batch_of_only_notifications_is_answered_with_no_body(server):
    import json
    status, body, _ = fetch(f'{server}/mcp', 'POST', json.dumps([
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
    ]).encode(), {'Content-Type': 'application/json'})
    assert status == 202 and body == b''


def test_mcp_declares_the_capabilities_it_actually_serves(server):
    _, response = rpc(server, 'initialize', {'protocolVersion': '2025-06-18'})
    capabilities = response['result']['capabilities']
    assert set(capabilities) == {'tools', 'resources', 'prompts'}
    # Declaring one means answering its list method.
    for method, key in (('tools/list', 'tools'),
                        ('resources/list', 'resources'),
                        ('prompts/list', 'prompts')):
        _, listed = rpc(server, method)
        assert listed['result'][key], f'{method} declared but empty'


def test_claiming_over_mcp_returns_the_picture_because_the_code_is_gone(server):
    """The response is the only copy anyone gets — it has to carry the bytes."""
    import base64
    import json
    added = tool(server, 'store_add',
                 base64=base64.b64encode(png()).decode(), name='once.png')
    grant = tool(server, 'store_grant', id='once.png', ttl_seconds='45s')
    assert grant['ttl'] == 45

    _, response = rpc(server, 'tools/call', {
        'name': 'store_claim', 'arguments': {'code': grant['code']}})
    blocks = response['result']['content']
    image = next(b for b in blocks if b['type'] == 'image')
    assert image['mimeType'] == 'image/png'
    assert base64.b64decode(image['data']) == png()
    assert response['result']['structuredContent']['burned'] is True
    assert added['id'] == response['result']['structuredContent']['image']


def test_view_shows_the_picture_and_peek_still_spends_nothing(server):
    import base64
    added = tool(server, 'store_add',
                 base64=base64.b64encode(png()).decode(), name='look.png')
    _, response = rpc(server, 'tools/call', {
        'name': 'store_view', 'arguments': {'id': 'look.png'}})
    image = next(b for b in response['result']['content']
                 if b['type'] == 'image')
    assert base64.b64decode(image['data']) == png()

    grant = tool(server, 'store_grant', id=added['id'][:8], **{'for': '2m'})
    assert grant['ttl'] == 120
    assert tool(server, 'store_peek', code=grant['code'])['verdict'] == 'still good'
    assert tool(server, 'store_peek', code=grant['code'])['live'] is True


def test_mcp_tools_take_a_name_a_prefix_or_latest(server):
    import base64
    tool(server, 'store_add', base64=base64.b64encode(png(red=0x11)).decode(),
         name='first.png')
    second = tool(server, 'store_add',
                  base64=base64.b64encode(png(red=0x22)).decode(),
                  name='second.png')
    assert tool(server, 'store_image', id='first.png')['name'] == 'first.png'
    assert tool(server, 'store_image', id='latest')['id'] == second['id']
    assert tool(server, 'store_image',
                picture=second['id'][:6])['name'] == 'second.png'


def test_store_share_stores_and_mints_in_one_call(server):
    import base64
    shared = tool(server, 'store_share',
                  base64=base64.b64encode(png()).decode(), name='hand.png',
                  **{'for': '90s'})
    assert shared['ttl'] == 90
    assert shared['picture'] == 'hand.png'
    assert shared['show_the_person'] == shared['page_url']
    # It shares privately. Publishing is the other thing, and is not implied.
    assert tool(server, 'store_image', id='hand.png')['public'] is False


def test_resources_list_the_docs_and_your_own_pictures(server):
    import base64
    added = tool(server, 'store_add',
                 base64=base64.b64encode(png()).decode(), name='res.png')
    _, response = rpc(server, 'resources/list')
    uris = [r['uri'] for r in response['result']['resources']]
    assert 'store://docs' in uris
    assert f'store://image/{added["id"]}' in uris

    _, response = rpc(server, 'resources/read',
                      {'uri': f'store://image/{added["id"]}'})
    content = response['result']['contents'][0]
    assert content['mimeType'] == 'image/png'
    assert base64.b64decode(content['blob']) == png()


def test_reading_a_resource_that_is_not_there_is_a_jsonrpc_error(server):
    _, response = rpc(server, 'resources/read', {'uri': 'store://nope'})
    assert response['error']['code'] == -32002


def test_the_prompts_name_the_tool_they_want_and_the_one_they_do_not(server):
    _, response = rpc(server, 'prompts/get', {
        'name': 'share_with_one_person',
        'arguments': {'picture': 'a.png', 'how_long': '2m'}})
    text = response['result']['messages'][0]['content']['text']
    assert 'store_share' in text and '2m' in text
    assert 'Do NOT use store_publish' in text

    _, response = rpc(server, 'prompts/get', {'name': 'share_with_one_person'})
    assert response['error']['code'] == -32602      # picture is required


def test_an_event_stream_client_gets_one_event(server):
    """Streamable HTTP: a client may accept only text/event-stream."""
    import json
    status, body, headers = fetch(
        f'{server}/mcp', 'POST',
        json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'ping'}).encode(),
        {'Accept': 'text/event-stream'})
    assert status == 200
    assert headers['Content-Type'] == 'text/event-stream'
    assert body.startswith(b'event: message\ndata: ')
    assert json.loads(body.split(b'data: ', 1)[1])['id'] == 7


def test_a_session_id_is_echoed_and_a_disconnect_is_not_an_error(server):
    import json
    status, _, headers = fetch(
        f'{server}/mcp', 'POST',
        json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}).encode(),
        {'Mcp-Session-Id': 'abc-123'})
    assert status == 200 and headers['Mcp-Session-Id'] == 'abc-123'
    assert fetch(f'{server}/mcp', 'DELETE')[0] == 204


# ── the manual ───────────────────────────────────────────────────────

def test_docs_answer_a_browser_with_a_page_and_a_program_with_json(server):
    import json
    status, body, headers = fetch(f'{server}/docs',
                                  headers={'Accept': 'text/html'})
    assert status == 200
    assert headers['Content-Type'].startswith('text/html')
    assert b'<!doctype html>' in body.lower()

    status, body, headers = fetch(f'{server}/docs',
                                  headers={'Accept': 'application/json'})
    assert status == 200
    assert headers['Content-Type'] == 'application/json'
    assert 'sharing' in json.loads(body)

    # No Accept at all is a program too — curl says nothing and wants data.
    _, body, _ = fetch(f'{server}/docs')
    assert 'endpoints' in json.loads(body)


def test_the_docs_page_is_rendered_from_the_docs_data(server):
    """One source. A route that changes must not be stale on the page."""
    from src import docs
    status, body, _ = fetch(f'{server}/docs?format=html')
    assert status == 200
    import html
    page = body.decode()
    for endpoint in docs.endpoints():
        assert html.escape(endpoint['path']) in page
    for tool_name in docs.mcp.TOOLS:
        assert tool_name in page


def test_every_documented_section_can_be_asked_for_on_its_own(server):
    import json
    from src import docs
    whole = docs.document()
    for section in ('sharing', 'naming', 'endpoints', 'cli', 'env', 'safety',
                    'mcp'):
        assert section in whole
        _, body, _ = fetch(f'{server}/docs?section={section}',
                           headers={'Accept': 'application/json'})
        assert list(json.loads(body)) == [section]


def test_the_docs_describe_the_tools_that_actually_exist(server):
    """The manual is generated, and this is what keeps it that way."""
    from src import docs, mcp
    documented = {tool['name'] for tool in docs.document()['mcp']['tools']}
    assert documented == set(mcp.TOOLS)
