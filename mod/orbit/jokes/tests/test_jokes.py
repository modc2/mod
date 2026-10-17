"""Offline tests — every one runs in a throwaway book under tmp_path."""

import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import jokebook  # noqa: E402

HEDBERG = "I used to do drugs. I still do, but I used to, too."


@pytest.fixture
def book(tmp_path):
    return jokebook.Jokebook(str(tmp_path / 'jokes.db'))


def test_config_parses_and_matches():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'jokes' and cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 51130
    for fn in cfg['api_fns']:
        assert fn in cfg['fns']


def test_anchor_loads_by_path(tmp_path):
    spec = importlib.util.spec_from_file_location(
        'jokes_anchor', os.path.join(MODULE_DIR, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    m = module.Mod(path=str(tmp_path / 'j.db'))
    assert m.health()['ok']
    j = m.add(HEDBERG, comedian='Mitch Hedberg', source='Mitch All Together')
    s = m.share(j['id'], host='https://example.org')
    assert s['url'] == f"https://example.org/jokes/?j={j['id']}"
    assert s['text'].endswith('— Mitch Hedberg — Mitch All Together')


def test_add_credits_and_dedupes(book):
    a = book.add(HEDBERG, 'Mitch Hedberg', tags='oneliner, drugs')
    b = book.add('  i used to do drugs.  I still do, but I used to, too. ',
                 'mitch hedberg', tags=['#Classic'])
    assert a['id'] == b['id'] and b['existed']
    assert book.get(a['id'])['tags'] == ['oneliner', 'drugs', 'classic']
    assert book.stats()['jokes'] == 1
    assert book.add('x', '')['comedian'] == 'unknown'
    with pytest.raises(ValueError):
        book.add('   ', 'Someone')


def test_search_filters_and_sorts(book):
    a = book.add(HEDBERG, 'Mitch Hedberg', tags='oneliner')
    book.add('My dog is the reason I own a dog.', 'Someone Else', tags='dogs')
    assert book.search('drugs')['total'] == 1
    assert book.search(comedian='mitch hedberg')['jokes'][0]['id'] == a['id']
    assert book.search(tag='dogs')['total'] == 1
    assert book.search(q='hedberg oneliner')['total'] == 1
    book.vote(a['id'], 1)
    assert book.search(sort='top')['jokes'][0]['id'] == a['id']
    assert book.random(comedian='Someone Else')['comedian'] == 'Someone Else'
    assert [c['comedian'] for c in book.comedians()] == ['Mitch Hedberg', 'Someone Else']


def test_vote_and_remove(book):
    j = book.add(HEDBERG, 'Mitch Hedberg')
    assert book.vote(j['id'], 5)['votes'] == 1
    assert book.vote(j['id'], -1)['votes'] == 0
    with pytest.raises(KeyError):
        book.vote('nope')
    assert book.remove(j['id'])['removed'] and book.get(j['id']) is None


def test_pack_round_trip_merges_without_dupes(book, tmp_path):
    a = book.add(HEDBERG, 'Mitch Hedberg', tags='oneliner')
    book.vote(a['id'])
    pack = book.export(comedian='Mitch Hedberg')
    assert pack['format'] == jokebook.PACK_FORMAT and pack['count'] == 1
    assert 'votes' not in pack['jokes'][0]

    other = jokebook.Jokebook(str(tmp_path / 'friend.db'))
    other.add('Their own joke.', 'Friend')
    assert other.import_pack(json.dumps(pack)) == {'added': 1, 'skipped': 0, 'errors': []}
    assert other.import_pack(pack)['skipped'] == 1
    got = other.get(a['id'])
    assert got['comedian'] == 'Mitch Hedberg' and got['votes'] == 0


def test_tampered_pack_cannot_overwrite(book):
    real = book.add(HEDBERG, 'Mitch Hedberg')
    bad = {'jokes': [{'id': real['id'], 'text': 'something else', 'comedian': 'Imposter'},
                     {'text': ''}]}
    r = book.import_pack(bad)
    assert r['added'] == 1 and len(r['errors']) == 1
    assert book.get(real['id'])['text'] == HEDBERG
    with pytest.raises(ValueError):
        book.import_pack({'nope': 1})
