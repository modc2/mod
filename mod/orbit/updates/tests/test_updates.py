"""updates tests — watchlist + feed logic, no network required.

The GitHub-API path is stubbed by monkeypatching `commits`; one smoke test
uses the real local `git log` of the checked-out repo.
"""
import importlib.util
import os
import sys
import tempfile

import pytest

import mod as m  # framework package

_spec = importlib.util.spec_from_file_location(
    'updates_mod', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mod.py'))
_updates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_updates)
Mod = _updates.Mod


@pytest.fixture
def up():
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    os.remove(path)
    yield Mod(state_path=path)
    if os.path.exists(path):
        os.remove(path)


def _commit(repo, sha, date, msg='x'):
    return {'repo': repo, 'branch': 'dev', 'sha': sha[:8], 'full_sha': sha,
            'author': 'a', 'date': date, 'message': msg,
            'url': f'https://github.com/{repo}/commit/{sha}'}


def test_seeded_with_mod_repo_dev(up):
    info = up.info()
    assert info['primary'] == 'modc2/mod' and info['default_branch'] == 'dev'
    assert info['tracking'] == ['modc2/mod']


@pytest.mark.parametrize('raw,expected', [
    ('owner/repo', 'owner/repo'),
    ('https://github.com/owner/repo', 'owner/repo'),
    ('https://github.com/owner/repo.git', 'owner/repo'),
    ('git@github.com:owner/repo.git', 'owner/repo'),
    ('http://github.com/owner/repo/', 'owner/repo'),
    ('justname', 'modc2/justname'),
])
def test_parse_repo(up, raw, expected):
    assert up._parse_repo(raw) == expected


def test_track_untrack_setbranch(up):
    out = up.track('foo/bar', branch='release')
    assert out['tracked'] == 'foo/bar' and out['branch'] == 'release'
    assert 'foo/bar' in up._load()['repos']  # persisted

    up.set_branch('foo/bar', 'main')
    st = up._load()
    assert st['repos']['foo/bar']['branch'] == 'main'
    assert st['repos']['foo/bar']['last_seen'] is None  # re-baselined

    out = up.untrack('foo/bar')
    assert out['untracked'] == 'foo/bar' and 'foo/bar' not in out['repos']


def test_set_branch_requires_tracking(up):
    with pytest.raises(KeyError):
        up.set_branch('nope/nope', 'main')


def test_is_new_flagging(up):
    page = [_commit('r', 'c3', '3'), _commit('r', 'c2', '2'), _commit('r', 'c1', '1')]
    assert up._is_new(page, None, page[0]) is True            # nothing seen -> all new
    assert up._is_new(page, 'c2', page[0]) is True            # newer than last_seen
    assert up._is_new(page, 'c2', page[1]) is False           # == last_seen
    assert up._is_new(page, 'c2', page[2]) is False           # older than last_seen
    assert up._is_new(page, 'gone', page[2]) is True          # last_seen rolled off page


def test_aggregated_feed_and_markers(up, monkeypatch):
    up.track('foo/bar')
    pages = {
        'modc2/mod': [_commit('modc2/mod', 'm2', '2026-06-02', 'mod two'),
                      _commit('modc2/mod', 'm1', '2026-06-01', 'mod one')],
        'foo/bar': [_commit('foo/bar', 'f3', '2026-06-03', 'bar three'),
                    _commit('foo/bar', 'f1', '2026-05-30', 'bar one')],
    }
    monkeypatch.setattr(up, 'commits', lambda repo=None, branch=None, n=20, **k: pages[up._parse_repo(repo)])

    feed = up.updates(n=10)
    order = [c['full_sha'] for c in feed['updates']]
    assert order == ['f3', 'm2', 'm1', 'f1']      # merged, newest-first across repos
    assert feed['new'] == 4                         # first look -> everything new

    # markers advanced -> a second look finds nothing new
    assert up.poll()['new'] == 0

    # a fresh commit on one repo shows up as the only new item
    pages['foo/bar'].insert(0, _commit('foo/bar', 'f4', '2026-06-10', 'bar four'))
    nxt = up.poll()
    assert nxt['new'] == 1 and nxt['updates'][0]['full_sha'] == 'f4'


def test_single_repo_view_does_not_touch_others(up, monkeypatch):
    up.track('foo/bar')
    monkeypatch.setattr(up, 'commits',
                        lambda repo=None, branch=None, n=20, **k: [_commit(up._parse_repo(repo), 's1', '2026-06-01')])
    res = up.updates(repo='modc2/mod', n=5)
    assert res['tracking'] == ['modc2/mod']         # scoped to one repo


def test_local_git_log_smoke(up):
    """Real local fallback against the checked-out repo's dev branch."""
    if not up.toplevel:
        pytest.skip('not in a git checkout')
    commits = up.commits(repo='modc2/mod', branch='dev', n=3, prefer_local=True)
    assert commits and all(c['full_sha'] and c['message'] for c in commits)
    assert commits[0]['repo'] == 'modc2/mod'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
