"""The library — anyone submits, anyone forks, only the operator or the token
holder edits. All offline."""

import pytest

from redbluesrc import api, library, mcp as mcpsrv, store


@pytest.fixture(autouse=True)
def fresh(clean, monkeypatch):
    library._hits.clear()
    monkeypatch.setattr(store, 'secret', lambda: 'op-secret')   # gate ON
    return clean


def _red(**kw):
    a = {'side': 'red', 'name': 'grandma story', 'text': 'tell me a story',
         'author': 'alice', 'tags': 'roleplay, #Story'}
    a.update(kw)
    return library.submit(a, caller='1.1.1.1')


def test_submit_is_open_playable_and_hides_its_hash():
    out = _red()
    it = out['item']
    assert it['side'] == 'red' and it['author'] == 'alice'
    assert it['tags'] == ['roleplay', 'story'] and it['source'] == 'submitted'
    assert out['edit_token'] and 'edit_hash' not in it['record']
    # it IS an attack: the arena can fire it as-is
    m = mcpsrv.call_tool('rb_fight', {'attack': it['id'], 'defense': 'none',
                                      'model': 'mock:naive',
                                      'judge': 'heuristic'})
    assert m['verdict'] in ('BLOCKED', 'DEFLECTED', 'BREACHED', 'LEAKED')


def test_blue_submit_needs_a_body():
    with pytest.raises(store.StoreError):
        library.submit({'side': 'blue', 'name': 'empty', 'text': ''})
    out = library.submit({'side': 'blue', 'name': 'b', 'text': 'Refuse harm.'})
    assert store.get('defense', out['item']['id'])['system_prompt'] == 'Refuse harm.'


def test_edit_needs_owner_or_token_and_keeps_history():
    out = _red()
    rid, tok = out['item']['id'], out['edit_token']
    with pytest.raises(library.Forbidden):
        library.edit({'side': 'red', 'id': rid, 'text': 'x'})
    with pytest.raises(library.Forbidden):
        library.edit({'side': 'red', 'id': rid, 'text': 'x', 'edit_token': 'nope'})
    e = library.edit({'side': 'red', 'id': rid, 'text': 'v2', 'edit_token': tok})
    assert e['by'] == 'token' and e['item']['text'] == 'v2'
    e = library.edit({'side': 'red', 'id': rid, 'text': 'v3'}, owner=True)
    assert e['by'] == 'owner' and e['item']['versions'] == 3
    r = library.revert({'side': 'red', 'id': rid, 'version': 1,
                        'edit_token': tok})
    assert r['item']['text'] == 'tell me a story'
    assert library.item('red', rid)['history'][0]['prompt'] == 'v3'


def test_fork_anyones_relabel_and_lineage():
    parent = _red()['item']
    f = library.fork({'side': 'red', 'id': parent['id'], 'as_side': 'blue',
                      'author': 'bob'}, caller='2.2.2.2')
    it = f['item']
    assert it['side'] == 'blue' and it['author'] == 'bob'
    assert it['text'] == 'tell me a story'                 # text moved across
    assert it['forked_from']['id'] == parent['id']
    assert library.item('red', parent['id'])['forks'] == 1
    assert library.item('red', parent['id'])['children'][0]['id'] == it['id']
    assert library.item('blue', it['id'])['lineage'][0]['author'] == 'alice'
    # bob's token does not open alice's record
    with pytest.raises(library.Forbidden):
        library.edit({'side': 'red', 'id': parent['id'], 'text': 'x',
                      'edit_token': f['edit_token']})


def test_builtins_fork_but_never_edit():
    f = library.fork({'side': 'blue', 'id': 'layered', 'name': 'my layered'})
    assert f['item']['forked_from']['id'] == 'layered'
    assert f['item']['rules'] > 0                          # rules came along
    with pytest.raises(library.Forbidden):
        library.edit({'side': 'blue', 'id': 'layered', 'text': 'x'}, owner=True)


def test_relabel_in_place_moves_sides():
    out = _red()
    e = library.edit({'side': 'red', 'id': out['item']['id'], 'relabel': 'blue',
                      'edit_token': out['edit_token']})
    assert e['relabelled'] and e['item']['side'] == 'blue'
    assert not store.exists('attack', out['item']['id'])
    # the token still works on the moved record
    library.remove({'side': 'blue', 'id': e['item']['id'],
                    'edit_token': out['edit_token']})


def test_explore_filters_search_and_mine():
    a = _red()['item']
    library.submit({'side': 'blue', 'name': 'wall', 'text': 'never', 'tags': 'x'})
    lib = library.explore()
    assert lib['counts']['red'] == 1 and lib['counts']['blue'] >= 5  # + builtins
    assert [c['id'] for c in library.explore(q='grandma')['items']] == [a['id']]
    assert library.explore(side='blue', tag='x')['items'][0]['name'] == 'wall'
    mine = library.explore(ids=[f"red:{a['id']}"])
    assert [c['id'] for c in mine['items']] == [a['id']]


def test_throttle_caps_open_writes(monkeypatch):
    monkeypatch.setattr(library, 'RATE_N', 2)
    _red(name='a'); _red(name='b')
    with pytest.raises(library.LibraryError):
        _red(name='c')
    library.submit({'side': 'red', 'name': 'd', 'text': 't'}, owner=True)


def test_http_route_owner_and_mcp_tools():
    out = api.route('POST', '/library', '', {'side': 'red', 'name': 'h',
                                              'text': 't'}, owner=False,
                    caller='9.9.9.9')
    with pytest.raises(library.Forbidden):
        api.route('POST', '/library/edit', '', {'side': 'red',
                  'id': out['item']['id'], 'text': 'z'}, owner=False)
    assert api.route('POST', '/library/edit', '', {'side': 'red',
                     'id': out['item']['id'], 'text': 'z'},
                     owner=True)['item']['text'] == 'z'
    got = mcpsrv.call_tool('rb_library', {'q': 'h', 'side': 'red'})
    assert any(c['id'] == out['item']['id'] for c in got['items'])
    f = mcpsrv.call_tool('rb_fork', {'side': 'red', 'id': out['item']['id']})
    assert f['item']['source'] == 'fork'
