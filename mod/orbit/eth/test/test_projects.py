"""Projects: the bytes go to the store, the index stays here.

Two things are worth proving and they pull in opposite directions:

    a save that reaches the store comes back with a CID, and that CID is
    fetchable by somebody who is not signed in at all;

    a save the store *refuses* still keeps the work, with the refusal
    attached — because losing somebody's source to an upload error would be
    a worse bug than not having a CID.

Store-touching tests skip when the store module is not reachable or will not
take an upload from this box. A mocked store would prove this module can talk
to a mock; the whole point of `store_link.py` is that it talks to the real one.
"""
import json

import pytest

import projects
from store_link import LINK, StoreError, local_token

SOURCE = '''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract Kept { uint256 public n = 7; }
'''


@pytest.fixture(scope='module')
def store_token():
    """This box's own token, and only if the store will actually take it."""
    try:
        token = local_token()
        state = LINK.status(token)
    except Exception as e:                       # pragma: no cover — env
        pytest.skip(f'no store to talk to: {e}')
    if not state.get('can_share'):
        pytest.skip(f'the store will not take an upload from this box: '
                    f'{state.get("blockers") or state.get("error")}')
    return token


# ── shapes, no network ───────────────────────────────────────────────

def test_a_filename_is_normalised_and_traversal_is_refused():
    assert projects._clean_files({'A': 'contract A {}'}) == {'A.sol': 'contract A {}'}
    with pytest.raises(projects.ProjectError):
        projects._clean_files({'../../etc/passwd': 'x'})


def test_the_entry_file_is_the_one_with_a_contract_in_it():
    files = {'IThing.sol': 'interface IThing { }',
             'Thing.sol': 'contract Thing { }'}
    assert projects._pick_entry(files, None) == 'Thing.sol'
    assert projects._pick_entry(files, 'IThing') == 'IThing.sol'


def test_contract_names_are_read_without_compiling():
    assert projects.contract_names({'a.sol': 'contract One {}\ncontract Two {}'}) \
        == ['One', 'Two']


def test_a_foreign_cid_has_to_look_like_one_of_ours():
    with pytest.raises(projects.ProjectError):
        projects.read_bundle({'kind': 'something.else/1', 'files': {'a.sol': 'x'}})
    with pytest.raises(projects.ProjectError):
        projects.read_bundle('not even json')
    good = projects.make_bundle('X', {'X.sol': 'contract X {}'}, 'X.sol')
    assert projects.read_bundle(good)['name'] == 'X'


# ── the local half, with no usable store ─────────────────────────────

def test_a_refused_upload_still_keeps_the_work(address):
    """No token at all is the simplest refusal the store can give."""
    row = projects.save(address, None, name='Orphan', source=SOURCE)
    assert row['cid'] is None
    assert row['store']['stored'] is False
    assert row['store']['reason']
    # The file is named after the contract, not after the project: a paste
    # with no filename still lands as Kept.sol rather than Contract.sol.
    assert projects.get(address, row['id'])['files']['Kept.sol'] == SOURCE


def test_two_contracts_of_the_same_name_are_two_projects(address):
    first = projects.save(address, None, name='Twin', source=SOURCE)
    second = projects.save(address, None, name='Twin', source=SOURCE)
    assert first['id'] != second['id']
    assert first['slug'] != second['slug']


def test_a_project_is_scoped_to_the_caller(address):
    row = projects.save(address, None, name='Mine', source=SOURCE)
    assert projects.find('0xsomebodyelse', row['slug']) is None
    with pytest.raises(projects.ProjectError):
        projects.get('0xsomebodyelse', row['id'])


def test_saving_over_a_project_keeps_its_slug(address):
    first = projects.save(address, None, name='Versioned', source=SOURCE)
    again = projects.save(address, None, project=first['id'],
                          source=SOURCE.replace('7', '8'), note='bumped')
    assert again['id'] == first['id']
    assert again['slug'] == first['slug']
    assert '8' in next(iter(again['files'].values()))


def test_sharing_without_a_store_says_so_rather_than_lying(address):
    row = projects.save(address, None, name='Unshareable', source=SOURCE)
    with pytest.raises(StoreError):
        projects.share(address, '', row['id'])


# ── the real round trip ──────────────────────────────────────────────

def test_a_saved_project_gets_a_cid_and_a_version(address, store_token):
    row = projects.save(address, store_token, name='Round trip', source=SOURCE,
                        note='first')
    assert row['store']['stored'] is True
    assert row['cid']
    full = projects.get(address, row['id'])
    assert full['versions'][0]['cid'] == row['cid']

    again = projects.save(address, store_token, project=row['id'],
                          source=SOURCE.replace('7', '9'), note='second')
    assert again['cid'] != row['cid'], 'a new version is a new CID'
    assert len(projects.get(address, row['id'])['versions']) == 2


def test_a_shared_project_is_readable_with_no_token_at_all(address, store_token):
    row = projects.save(address, store_token, name='Public thing', source=SOURCE)
    shared = projects.share(address, store_token, row['id'])
    assert shared['public'] is True
    assert shared['open'].endswith(shared['cid'])

    # No token: exactly what a stranger following the link sends.
    opened = projects.open_bundle(None, shared['cid'])
    assert opened['kind'] == projects.KIND
    assert opened['files'] == {'Kept.sol': SOURCE}
    assert opened['name'] == 'Public thing'


def test_forking_makes_the_copy_yours(address, store_token):
    row = projects.save(address, store_token, name='Original', source=SOURCE)
    shared = projects.share(address, store_token, row['id'])

    other = '0x00000000000000000000000000000000deadbeef'
    fork = projects.fork(other, store_token, shared['cid'], name='My copy')
    assert fork['owner'] == other
    assert fork['origin_cid'] == shared['cid']
    assert fork['id'] != row['id']
    assert projects.find(other, fork['slug']) is not None
    # The original is untouched and still the author's.
    assert projects.get(address, row['id'])['owner'] == address


def test_unsharing_flips_it_back(address, store_token):
    row = projects.save(address, store_token, name='On and off', source=SOURCE)
    projects.share(address, store_token, row['id'])
    off = projects.unshare(address, store_token, row['id'])
    assert off['public'] is False
    assert projects.get(address, row['id'])['public'] is False


def test_deleting_leaves_the_stored_object_alone_by_default(address, store_token):
    row = projects.save(address, store_token, name='Tidy up', source=SOURCE)
    cid = row['cid']
    projects.delete(address, row['id'])
    assert projects.find(address, row['slug']) is None
    # Still in the store: forgetting your own row is not a takedown.
    assert LINK.fetch_json(store_token, cid)['kind'] == projects.KIND


# ── over HTTP ────────────────────────────────────────────────────────

def test_the_api_round_trips_a_project(client, token):
    made = client.post('/projects', json={'name': 'Over HTTP', 'source': SOURCE},
                       headers={'Authorization': f'Bearer {token}'})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body['entry'] == 'Kept.sol'
    assert 'store' in body

    listed = client.get('/projects', headers={'Authorization': f'Bearer {token}'})
    assert listed.status_code == 200
    assert any(p['id'] == body['id'] for p in listed.json()['projects'])

    one = client.get(f'/projects/{body["id"]}',
                     headers={'Authorization': f'Bearer {token}'})
    assert one.json()['files']['Kept.sol'] == SOURCE


def test_a_copy_saved_over_http_keeps_where_it_came_from(client, token):
    """Editing somebody's bundle and saving it is a fork by another route.

    The console does exactly this: type into a project you opened by CID and
    it becomes your copy, saved with the origin attached. Provenance that only
    survives the /fork button would be provenance the console loses.
    """
    made = client.post('/projects', json={'name': 'A copy', 'source': SOURCE,
                                          'origin_cid': 'QmNotARealCidButAString'},
                       headers={'Authorization': f'Bearer {token}'})
    assert made.status_code == 200, made.text
    assert made.json()['origin_cid'] == 'QmNotARealCidButAString'

    # And a later save of the same project does not quietly drop it.
    again = client.put(f'/projects/{made.json()["id"]}',
                       json={'name': 'A copy, edited'},
                       headers={'Authorization': f'Bearer {token}'})
    assert again.json()['origin_cid'] == 'QmNotARealCidButAString'


def test_projects_need_a_caller(client):
    assert client.get('/projects').status_code == 401
    assert client.post('/projects', json={'source': SOURCE}).status_code == 401


def test_store_status_is_open_and_never_raises(client):
    got = client.get('/store')
    assert got.status_code == 200
    assert 'reachable' in got.json()
