"""Smoke tests for the github module.

The pure stages (expand, tf-idf rank, doc building, auth plumbing) run offline.
Anything that touches api.github.com is marked network and skipped unless
GITHUB_TEST_NETWORK=1 — the anonymous limit is 10 searches/minute and a test
suite should not spend it.
"""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, ROOT)

import mod as m

NETWORK = pytest.mark.skipif(not os.environ.get('GITHUB_TEST_NETWORK'),
                             reason='set GITHUB_TEST_NETWORK=1 to spend rate limit')


@pytest.fixture(scope='module')
def gh():
    return m.mod('github')()


def test_info(gh):
    info = gh.info()
    assert info['name'] == 'github'
    assert info['keyless'] is True
    assert 'search' in info['fns']


def test_expand_drops_stopwords_and_adds_topics(gh):
    plan = gh.expand('a library that lets me run untrusted wasm in a sandbox')
    assert 'the' not in plan['terms'] and 'a' not in plan['terms']
    assert 'wasm' in plan['terms']
    assert any('webassembly' in q for q in plan['queries'])
    assert 'topic:webassembly' in plan['topics']
    assert len(plan['queries']) <= 6


def test_expand_survives_a_query_with_only_stopwords(gh):
    plan = gh.expand('the a of')
    assert plan['queries'] and plan['queries'][0]


def test_expand_never_repeats_a_query(gh):
    # a duplicate is a wasted call out of ten per minute
    for q in ('vector database in rust', 'wasm', 'a p2p sync tool for encrypted files'):
        qs = gh.expand(q)['queries']
        assert len(qs) == len(set(qs)), q


def test_tfidf_ranks_the_on_topic_repo_first(gh):
    repos = [
        {'name': 'someone/notes-app', 'description': 'a todo list in electron',
         'topics': ['todo'], 'stars': 90000, 'language': 'JavaScript'},
        {'name': 'bytecodealliance/wasmtime', 'description':
            'a fast and secure runtime for WebAssembly with sandboxed execution',
         'topics': ['webassembly', 'sandbox'], 'stars': 15000, 'language': 'Rust'},
    ]
    out = gh.rank('run untrusted webassembly sandboxed', repos, readmes=0, dense=False)
    # the popularity prior must not float the 90k-star irrelevant repo
    assert out[0]['name'] == 'bytecodealliance/wasmtime'


def test_rank_explains_itself(gh):
    repos = [{'name': 'a/b', 'description': 'vector database', 'topics': ['vector'],
              'stars': 10, 'language': 'Rust'}]
    out = gh.rank('vector database', repos, readmes=0, dense=False, explain=True)
    why = out[0]['why']
    assert set(why) >= {'semantic', 'topic_overlap', 'popularity', 'ranker'}
    assert why['ranker'] == 'tfidf'


def test_archived_repos_are_demoted(gh):
    base = {'description': 'wasm sandbox runtime', 'topics': ['webassembly'],
            'stars': 100, 'language': 'Rust'}
    live = gh.rank('wasm sandbox', [dict(base, name='a/live')], readmes=0, dense=False)
    dead = gh.rank('wasm sandbox', [dict(base, name='a/dead', archived=True)],
                   readmes=0, dense=False)
    assert dead[0]['score'] < live[0]['score']


def test_rank_of_nothing_is_nothing(gh):
    assert gh.rank('anything', [], dense=False) == []


def test_split_accepts_url_and_shorthand(gh):
    assert gh._split('owner/repo') == ('owner', 'repo')
    assert gh._split('https://github.com/owner/repo') == ('owner', 'repo')
    assert gh._split('https://github.com/owner/repo.git') == ('owner', 'repo')
    with pytest.raises(ValueError):
        gh._split('not-a-repo')


def test_access_is_read_open(gh):
    acc = gh.access()
    assert 'search' in acc['open_reads']
    assert set(acc['roles']) == {'write', 'admin'}


def test_writes_need_a_token(gh):
    with pytest.raises(PermissionError):
        gh._authorize({}, need='write')


@NETWORK
def test_search_returns_ranked_repos(gh):
    out = gh.search('run untrusted wasm in a sandbox', n=5)
    assert out['results'] and len(out['results']) <= 5
    assert out['results'] == sorted(out['results'], key=lambda r: -r['score'])
    assert out['candidates'] >= len(out['results'])


@NETWORK
def test_repo_and_readme_are_keyless(gh):
    r = gh.repo('bytecodealliance/wasmtime')
    assert r['name'] == 'bytecodealliance/wasmtime'
    assert gh.readme('bytecodealliance/wasmtime', n=200)
