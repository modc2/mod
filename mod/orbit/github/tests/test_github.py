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


# --- root push -------------------------------------------------------------
# These run against a throwaway repo in tmp_path, never the real one, and never
# reach a remote: the point is the gating (cooldown, guards, porcelain parsing),
# not git itself.

@pytest.fixture
def repo(gh, tmp_path, monkeypatch):
    # never let a test write the real ~/.mod/github/root.json
    monkeypatch.setattr(gh, 'root_path', str(tmp_path / 'root.json'))
    import subprocess
    d = tmp_path / 'repo'
    d.mkdir()
    for args in (('init', '-b', 'main'), ('config', 'user.email', 't@t'),
                 ('config', 'user.name', 't')):
        subprocess.run(('git',) + args, cwd=d, capture_output=True)
    (d / 'a.txt').write_text('one\n')
    subprocess.run(['git', 'add', '-A'], cwd=d, capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=d, capture_output=True)
    return str(d)


def test_root_paths_keeps_the_leading_status_column():
    from mod.orbit.github.mod import Mod
    # ' M path' (unstaged) and 'M  path' (staged) must both yield 'path'
    assert Mod._root_paths(' M mod/a.py\nM  mod/b.py\n?? c/\n') == [
        'mod/a.py', 'mod/b.py', 'c/']
    assert Mod._root_paths('R  old.py -> new.py') == ['new.py']


def test_root_reports_pending_work(gh, repo):
    open(os.path.join(repo, 'b.txt'), 'w').write('two\n')
    st = gh.root(repo=repo)
    assert st['branch'] == 'main' and st['dirty'] is True
    assert st['pending_files'] == 1 and 'b.txt' in st['sample']


def test_root_push_dry_never_commits(gh, repo):
    open(os.path.join(repo, 'b.txt'), 'w').write('two\n')
    out = gh.root_push(repo=repo, dry=True)
    assert out['dry'] and out['would_commit'] == 1
    assert gh.root(repo=repo)['pending_files'] == 1


def test_root_push_skips_a_clean_tree(gh, repo):
    assert gh.root_push(repo=repo, dry=True)['skipped'] == 'clean'


def test_root_push_honours_the_cooldown(gh, repo, monkeypatch):
    import time as _t
    monkeypatch.setattr(gh, '_root_state', lambda: {
        'enabled': True, 'every': 3600, 'last_push': _t.time() - 10,
        'last_error': None, 'history': []})
    open(os.path.join(repo, 'b.txt'), 'w').write('two\n')
    out = gh.root_push(repo=repo)
    assert out['skipped'] == 'cooldown' and out['next_push_in'] > 3500
    # force ignores it — dry, so still no commit
    assert gh.root_push(repo=repo, force=True, dry=True)['would_commit'] == 1


def test_root_push_guards_an_implausible_diff(gh, repo, monkeypatch):
    import mod.orbit.github.mod as gmod
    monkeypatch.setattr(gmod, 'ROOT_MAX_FILES', 0)
    open(os.path.join(repo, 'b.txt'), 'w').write('two\n')
    with pytest.raises(ValueError, match='exceeds'):
        gh.root_push(repo=repo, dry=True)


def test_root_rejects_a_non_repo(gh, tmp_path):
    with pytest.raises(ValueError, match='not a git repo'):
        gh.root(repo=str(tmp_path))
