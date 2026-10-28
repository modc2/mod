"""
What this module promises, checked.

The one that matters is the gate: a mark may only be changed by the owner of
the module it is drawn on. Everything else — glyph limits, image ceilings, the
config.json mirror — is detail around that.
"""
import base64
import json

import pytest

import identity
import marks

# A real 1x1 transparent PNG — small enough to inline, real enough that the
# bytes that come back out can be compared to the bytes that went in.
PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ'
       'DwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
PNG_DATA_URL = f'data:image/png;base64,{PNG}'


# -- resolution --------------------------------------------------------

def test_resolves_a_module_to_its_group():
    assert identity.resolve('demo')[:2] == ('core', 'demo')       # core wins
    assert identity.resolve('orbit/demo')[:2] == ('orbit', 'demo')
    assert identity.resolve('core/demo')[:2] == ('core', 'demo')


def test_unknown_module_is_a_404_not_a_new_mark():
    with pytest.raises(identity.UnknownModule):
        identity.resolve('nope-not-here')
    with pytest.raises(identity.UnknownModule):
        identity.resolve('../../etc')
    with pytest.raises(identity.UnknownModule):
        identity.resolve('mods/demo')


def test_owner_comes_from_the_target_modules_own_manifest(keys):
    who = identity.owners('orbit/demo')
    assert who['owner'] == keys['owner_address']
    assert who['source'] == 'config.json'


def test_co_owners_come_from_off_chain_state(keys):
    who = identity.owners('orbit/shared')
    assert who['owner'] is None
    assert who['co_owners'] == [keys['owner_address']]
    assert identity.may_write(keys['owner_address'], 'orbit/shared')
    assert not identity.may_write(keys['stranger_address'], 'orbit/shared')


def test_a_module_with_no_owner_is_not_open_season(keys):
    # No declared owner, no co-owner, unclaimed deployment: nobody may write.
    assert identity.owners('orbit/orphan')['addresses'] == []
    assert not identity.may_write(keys['stranger_address'], 'orbit/orphan')


# -- the gate ----------------------------------------------------------

def test_write_needs_a_token(client, clean):
    r = client.post('/logo/orbit/demo', json={'glyph': 'X'})
    assert r.status_code == 401
    assert 'sign in' in r.json()['error']


def test_write_refuses_a_stranger(client, keys, clean):
    r = client.post('/logo/orbit/demo', json={'glyph': 'X'},
                    headers={'authorization': f'Bearer {keys["stranger_token"]}'})
    assert r.status_code == 401
    body = r.json()['error']
    assert keys['stranger_address'] in body and keys['owner_address'] in body


def test_write_refuses_a_forged_token(client, keys, clean):
    forged = keys['owner_token'][:-4] + 'aaaa'
    r = client.post('/logo/orbit/demo', json={'glyph': 'X'},
                    headers={'authorization': f'Bearer {forged}'})
    assert r.status_code == 401


def test_owner_of_one_module_cannot_paint_another(client, keys, clean):
    # The stranger owns core/demo, so the SAME token that works there is
    # refused on orbit/demo. This is the property the whole module exists for.
    ok = client.post('/logo/core/demo', json={'glyph': 'S'},
                     headers={'authorization': f'Bearer {keys["stranger_token"]}'})
    assert ok.status_code == 200
    no = client.post('/logo/orbit/demo', json={'glyph': 'S'},
                     headers={'authorization': f'Bearer {keys["stranger_token"]}'})
    assert no.status_code == 401


def test_owner_writes(client, keys, clean):
    r = client.post('/logo/orbit/demo', json={'glyph': 'X'},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['logo'] == {'kind': 'glyph', 'glyph': 'X',
                            'updated': body['logo']['updated'],
                            'by': keys['owner_address']}
    assert body['by'] == keys['owner_address']


def test_the_token_may_ride_in_x_mod_token(client, keys, clean):
    """A console proxying this API already spends Authorization on its own
    session; the owner's signed token rides alongside instead of displacing it."""
    r = client.post('/logo/orbit/demo', json={'glyph': 'Y'},
                    headers={'authorization': 'Bearer some-other-sessions-token',
                             'x-mod-token': keys['owner_token']})
    assert r.status_code == 200, r.text
    assert r.json()['logo']['glyph'] == 'Y'


def test_co_owner_writes(client, keys, clean):
    r = client.post('/logo/orbit/shared', json={'glyph': 'C'},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 200, r.text


def test_unknown_module_is_404_even_with_a_good_token(client, keys, clean):
    r = client.post('/logo/orbit/ghost', json={'glyph': 'G'},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 404


# -- reads are public --------------------------------------------------

def test_read_needs_nothing(client, keys, clean):
    client.post('/logo/orbit/demo', json={'glyph': 'X'},
                headers={'authorization': f'Bearer {keys["owner_token"]}'})
    r = client.get('/logo/orbit/demo')
    assert r.status_code == 200
    assert r.json()['logo']['glyph'] == 'X'


def test_unset_module_reads_as_the_cube(client, clean):
    assert client.get('/logo/orbit/orphan').json()['logo']['kind'] == 'cube'


def test_marks_lists_only_what_was_set(client, keys, clean):
    client.post('/logo/orbit/demo', json={'glyph': 'X'},
                headers={'authorization': f'Bearer {keys["owner_token"]}'})
    listed = [m['module'] for m in client.get('/marks').json()['marks']]
    assert listed == ['orbit/demo']


def test_whoami_says_what_a_token_may_write(client, keys, clean):
    client.post('/logo/orbit/demo', json={'glyph': 'X'},
                headers={'authorization': f'Bearer {keys["owner_token"]}'})
    r = client.get('/whoami', headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.json()['address'] == keys['owner_address']
    assert r.json()['owns'] == ['orbit/demo']
    anon = client.get('/whoami')
    assert anon.status_code == 200 and anon.json()['address'] is None


# -- what we will store ------------------------------------------------

def test_a_glyph_is_short(client, keys, clean):
    r = client.post('/logo/orbit/demo', json={'glyph': 'WORDMARK'},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 400
    assert 'characters' in r.json()['error']


def test_a_url_is_http(client, keys, clean):
    for bad in ('javascript:alert(1)', 'data:image/png;base64,AAAA', 'not a url'):
        r = client.post('/logo/orbit/demo', json={'url': bad},
                        headers={'authorization': f'Bearer {keys["owner_token"]}'})
        assert r.status_code == 400, bad


def test_an_image_round_trips_with_a_locked_down_csp(client, keys, clean):
    r = client.post('/logo/orbit/demo', json={'dataUrl': PNG_DATA_URL},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 200, r.text
    logo = r.json()['logo']
    assert logo['kind'] == 'image'
    assert logo['src'].startswith('/logo/_api/logo/orbit/demo/image?v=')

    img = client.get('/logo/orbit/demo/image')
    assert img.status_code == 200
    assert img.headers['content-type'].startswith('image/png')
    assert img.headers['content-security-policy'].startswith("default-src 'none'")
    assert img.headers['x-content-type-options'] == 'nosniff'


def test_an_unsupported_image_type_is_refused(client, keys, clean):
    r = client.post('/logo/orbit/demo',
                    json={'dataUrl': 'data:text/html;base64,PGgxPmhpPC9oMT4='},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 400
    assert 'unsupported' in r.json()['error']


def test_an_oversized_image_is_refused(client, keys, clean, monkeypatch):
    monkeypatch.setattr(marks, 'MAX_IMAGE_BYTES', 8)
    r = client.post('/logo/orbit/demo', json={'dataUrl': PNG_DATA_URL},
                    headers={'authorization': f'Bearer {keys["owner_token"]}'})
    assert r.status_code == 400
    assert 'limit' in r.json()['error']


def test_reset_goes_back_to_the_cube(client, keys, clean):
    auth = {'authorization': f'Bearer {keys["owner_token"]}'}
    client.post('/logo/orbit/demo', json={'glyph': 'X'}, headers=auth)
    r = client.request('DELETE', '/logo/orbit/demo', headers=auth)
    assert r.status_code == 200
    assert r.json()['logo']['kind'] == 'cube'
    assert client.get('/logo/orbit/demo').json()['logo']['kind'] == 'cube'


def test_a_missing_upload_falls_back_to_the_cube(client, keys, clean, state_dir):
    client.post('/logo/orbit/demo', json={'dataUrl': PNG_DATA_URL},
                headers={'authorization': f'Bearer {keys["owner_token"]}'})
    (state_dir / 'marks' / 'orbit' / 'demo.png').unlink()
    assert client.get('/logo/orbit/demo').json()['logo']['kind'] == 'cube'
    assert client.get('/logo/orbit/demo/image').status_code == 404


# -- the config.json mirror --------------------------------------------

def _manifest(tree_dir, group, name):
    return (tree_dir / group / name / 'config.json')


def test_a_short_mark_is_mirrored_into_the_manifest(client, keys, clean, tree_dir):
    auth = {'authorization': f'Bearer {keys["owner_token"]}'}
    path = _manifest(tree_dir, 'orbit', 'demo')
    before = path.read_text()

    client.post('/logo/orbit/demo', json={'glyph': 'X'}, headers=auth)
    assert json.loads(path.read_text())['logo'] == 'X'

    client.post('/logo/orbit/demo', json={'url': 'https://example.com/m.png'},
                headers=auth)
    assert json.loads(path.read_text())['logo'] == 'https://example.com/m.png'

    client.request('DELETE', '/logo/orbit/demo', headers=auth)
    assert 'logo' not in json.loads(path.read_text())
    # Round trip left the rest of the manifest exactly as it was.
    assert path.read_text() == before


def test_the_mirror_never_breaks_a_manifest(tree_dir):
    """A manifest mid-write is left alone rather than half-spliced."""
    path = _manifest(tree_dir, 'orbit', 'demo')
    good = path.read_text()
    path.write_text('{"name": "demo", ')                  # truncated JSON
    assert marks.mirror_to_config(path.parent, {'kind': 'glyph', 'glyph': 'X'}) is False
    assert path.read_text() == '{"name": "demo", '
    path.write_text(good)


def test_the_mirror_is_advisory_not_the_source_of_truth(client, keys, clean, tree_dir):
    """A read-only manifest must not fail the save — the mark still lands."""
    path = _manifest(tree_dir, 'orbit', 'demo')
    path.chmod(0o444)
    try:
        r = client.post('/logo/orbit/demo', json={'glyph': 'X'},
                        headers={'authorization': f'Bearer {keys["owner_token"]}'})
        assert r.status_code == 200
        assert client.get('/logo/orbit/demo').json()['logo']['glyph'] == 'X'
    finally:
        path.chmod(0o644)


# -- the module's own faces --------------------------------------------

def test_health_and_status(client):
    assert client.get('/health').json()['ok'] is True
    body = client.get('/status').json()
    assert body['auth']['open_mode'] is False
    assert 'owner' in body['auth']['rule']


def test_the_api_alias_is_the_same_api(client):
    assert client.get('/logo/_api/health').json()['ok'] is True
    assert client.get('/_api/health').json()['ok'] is True
