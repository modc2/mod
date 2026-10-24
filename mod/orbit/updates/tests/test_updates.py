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


def test_adhoc_repo_view_is_read_only(up, monkeypatch):
    """updates(repo=...) on an untracked repo must NOT add it to the watchlist."""
    monkeypatch.setattr(up, 'commits',
                        lambda repo=None, branch=None, n=20, **k: [
                            _commit(up._parse_repo(repo), 't1', '2026-06-01')])
    up._load()                                          # seed state.json first
    before = open(up.state_path).read()
    res = up.updates(repo='torvalds/linux', branch='master', n=5)
    assert res['tracking'] == ['torvalds/linux']        # the view itself works
    assert 'torvalds/linux' not in up._load()['repos']  # but nothing was tracked
    assert open(up.state_path).read() == before         # state.json untouched


def test_branch_override_view_leaves_last_seen(up, monkeypatch):
    """Peeking at a tracked repo on another branch must not move its marker."""
    pages = {'dev': [_commit('modc2/mod', 'd1', '2026-06-02')],
             'main': [_commit('modc2/mod', 'x9', '2026-06-03')]}
    monkeypatch.setattr(up, 'commits',
                        lambda repo=None, branch=None, n=20, **k: pages[branch])
    up.updates(n=5)                                      # normal feed advances dev
    assert up._load()['repos']['modc2/mod']['last_seen'] == 'd1'
    up.updates(repo='modc2/mod', branch='main', n=5)     # other-branch peek
    assert up._load()['repos']['modc2/mod']['last_seen'] == 'd1'   # untouched
    assert up.poll()['new'] == 0                         # NEW detection undisturbed


def test_tracked_repo_view_still_advances_marker(up, monkeypatch):
    """An explicit view of a tracked repo on its tracked branch still marks seen."""
    monkeypatch.setattr(up, 'commits',
                        lambda repo=None, branch=None, n=20, **k: [
                            _commit('modc2/mod', 'd1', '2026-06-02')])
    up.updates(repo='modc2/mod', n=5)
    assert up._load()['repos']['modc2/mod']['last_seen'] == 'd1'


def test_displaced_repo_marker_not_advanced(up, monkeypatch):
    """Repo B's commits pushed out by repo A must keep last_seen=None after poll."""
    up.track('foo/bar')
    recent = '2026-06-{:02d}'.format
    pages = {
        'modc2/mod': [_commit('modc2/mod', f'm{i}', recent(30 - i)) for i in range(15)],
        'foo/bar':   [_commit('foo/bar',   f'f{i}', '2026-05-{:02d}'.format(15 - i)) for i in range(15)],
    }
    monkeypatch.setattr(up, 'commits', lambda repo=None, branch=None, n=20, **k: pages[up._parse_repo(repo)])

    up.updates(n=15, mark_seen=True)

    st = up._load()
    # modc2/mod filled all 15 slots; foo/bar's marker must still be None
    assert st['repos']['modc2/mod']['last_seen'] == 'm0'
    assert st['repos'].get('foo/bar', {}).get('last_seen') is None


def test_local_git_log_smoke(up):
    """Real local fallback against the checked-out repo's dev branch."""
    if not up.toplevel:
        pytest.skip('not in a git checkout')
    commits = up.commits(repo='modc2/mod', branch='dev', n=3, prefer_local=True)
    assert commits and all(c['full_sha'] and c['message'] for c in commits)
    assert commits[0]['repo'] == 'modc2/mod'


# --- daily digest -----------------------------------------------------------

def _stub_day(up, monkeypatch, commits, files):
    monkeypatch.setattr(up, 'commits', lambda repo=None, branch=None, n=20, **k: commits)
    monkeypatch.setattr(up, '_day_files', lambda repo, rows: files)


def test_daily_buckets_commits_by_utc_day(up, monkeypatch):
    cs = [_commit('modc2/mod', 'c3', '2026-09-10T01:00:00+00:00'),
          _commit('modc2/mod', 'c2', '2026-09-09T23:59:00Z'),
          _commit('modc2/mod', 'c1', '2026-09-09T08:00:00Z')]
    _stub_day(up, monkeypatch, cs, ['mod/orbit/polymarket/a.py'])
    d = up.daily(days=7)
    assert [x['date'] for x in d['days']] == ['2026-09-10', '2026-09-09']   # newest day first
    assert [x['commits'] for x in d['days']] == [1, 2]
    assert d['branch'] == 'dev' and d['tz'] == 'UTC'


@pytest.mark.parametrize('path,module', [
    ('mod/orbit/polymarket/src/api.py', 'polymarket'),
    ('mod/core/registry/mod.py', 'core/registry'),
    ('core/api/config.json', 'core/api'),
    ('docs/whitepaper.md', 'docs'),
    ('README.md', 'root'),
])
def test_module_attribution(up, path, module):
    assert up._module_of(path) == module


def test_day_rolls_files_up_by_module(up, monkeypatch):
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')],
              ['mod/orbit/polymarket/a.py', 'mod/orbit/polymarket/b.py', 'core/api/config.json'])
    day = up.daily(days=1)['days'][0]
    assert day['files'] == 3
    assert day['modules'] == [{'name': 'polymarket', 'files': 2}, {'name': 'core/api', 'files': 1}]


def test_every_style_names_the_branch(up, monkeypatch):
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')],
              ['mod/orbit/polymarket/a.py'])
    post = up.daily(days=1)['days'][0]['post']
    for style, text in post.items():
        assert 'dev' in text and 'modc2/mod' in text, style
    assert '`dev`' in post['discord']                    # discord renders it as code
    assert post['discord'].endswith('>')                 # <link> = no embed card


def test_tweet_trims_modules_to_fit_280(up, monkeypatch):
    files = [f'mod/orbit/m{i}/f{j}.py' for i in range(30) for j in range(3)]
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')], files)
    day = up.daily(days=1)['days'][0]
    assert len(day['modules']) == 30
    tweet = day['post']['twitter']
    assert day['chars']['twitter'] <= up.TWEET_MAX      # link weighted at 23 chars
    assert '+25 more' in tweet                           # 5 shown, rest summarised


def test_markdown_overflow_appends_and_n_more(up, monkeypatch):
    files = [f'mod/orbit/m{i}/f.py' for i in range(20)]
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')], files)
    day = up.daily(days=1)['days'][0]
    assert len(day['modules']) == 20
    md = day['post']['markdown']
    assert '…and 5 more modules' in md


def test_post_goes_out_once_per_day(up, monkeypatch):
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')],
              ['mod/orbit/polymarket/a.py'])
    first = up.post()
    assert first['date'] == '2026-09-10' and first['skip'] is False and first['marked'] is True
    assert first['style'] == 'twitter' and first['chars'] <= 280

    again = up.post()                                    # same day -> don't repeat yourself
    assert again['skip'] is True and again['already_posted'] is True
    assert up.post(force=True)['skip'] is False          # unless asked to

    up.mark_posted('2026-09-10', posted=False)           # un-post and it is pending again
    assert up.daily(days=1)['days'][0]['posted'] is False


def test_post_selects_a_named_day(up, monkeypatch):
    cs = [_commit('modc2/mod', 'c2', '2026-09-10T01:00:00Z'),
          _commit('modc2/mod', 'c1', '2026-09-09T01:00:00Z')]
    _stub_day(up, monkeypatch, cs, ['mod/orbit/defi/a.rs'])
    assert up.post(date='2026-09-09', style='discord')['date'] == '2026-09-09'
    assert up.post(date='2020-01-01')['skip'] is True     # a day with no commits


def test_daily_smoke_over_local_dev_branch(up):
    """Real digest over the checked-out repo — exercises `git show` file rollup."""
    if not up.toplevel:
        pytest.skip('not in a git checkout')
    d = up.daily(days=2, n=40)
    assert d['repo'] == 'modc2/mod' and d['branch'] == 'dev' and d['days']
    day = d['days'][0]
    assert day['commits'] >= 1 and day['files'] >= 1 and day['modules']
    assert day['post']['twitter'].startswith('modc2/mod · dev · ')


# --- MCP server + the X account bridge ---------------------------------------

def _rpc(up, method, local=True, **params):
    return up._mcp_rpc({'jsonrpc': '2.0', 'id': 1, 'method': method,
                        'params': params or None}, local=local)


def _call(up, name, local=True, **args):
    return _rpc(up, 'tools/call', local=local, name=name, arguments=args)


def test_mcp_initialize_and_tools_list(up):
    init = _rpc(up, 'initialize')['result']
    assert init['serverInfo']['name'] == 'updates' and init['protocolVersion']
    tools = {t['name'] for t in _rpc(up, 'tools/list')['result']['tools']}
    assert {'updates', 'poll', 'daily', 'post', 'x_status', 'post_to_x'} <= tools
    assert up._mcp_rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert _rpc(up, 'nope')['error']['code'] == -32601


def test_mcp_call_filters_args_and_wraps_result(up, monkeypatch):
    monkeypatch.setattr(up, 'commits',
                        lambda repo=None, branch=None, n=20, **k: [
                            _commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')])
    out = _call(up, 'updates', n=5, junk='ignored')['result']
    assert out['isError'] is False
    assert out['structuredContent']['count'] == 1
    assert 'c1' in out['content'][0]['text']
    assert _call(up, 'no_such_tool')['error']['code'] == -32602


def test_mcp_tool_exception_is_tool_error_not_rpc_error(up):
    out = _call(up, 'set_branch', repo='foo/bar', branch='main')['result']
    assert out['isError'] is True and 'not tracked' in out['content'][0]['text']


def test_post_to_x_refuses_gateway_callers(up):
    out = _call(up, 'post_to_x', local=False)['result']
    assert out['isError'] is True and 'local-only' in out['content'][0]['text']
    # reads are still fine over the gateway
    assert _rpc(up, 'tools/list', local=False)['result']['tools']


def test_post_to_x_dry_run_sends_nothing(up, monkeypatch):
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')],
              ['mod/orbit/polymarket/a.py'])
    monkeypatch.setattr(up, '_x', lambda *a, **k: pytest.fail('dry_run must not call x-api'))
    out = up.post_to_x(dry_run=True)
    assert out['sent'] is False and out['text'] and out['chars'] <= 280
    assert up.daily(days=1)['days'][0]['posted'] is False     # nothing marked


def test_post_to_x_tweets_and_marks_only_on_success(up, monkeypatch):
    _stub_day(up, monkeypatch, [_commit('modc2/mod', 'c1', '2026-09-10T01:00:00Z')],
              ['mod/orbit/polymarket/a.py'])
    calls = []

    def fake_x(method, path, body=None):
        calls.append((method, path, body))
        return {'data': {'id': '1234567890'}}
    monkeypatch.setattr(up, '_x', fake_x)
    out = up.post_to_x()
    assert calls == [('POST', '/posts', {'text': out['text']})]
    assert out['sent'] is True and out['marked'] is True
    assert out['url'] == 'https://x.com/i/status/1234567890'
    assert up.post()['skip'] is True                          # once per day holds

    # x-api down -> the day is NOT marked posted
    up.mark_posted('2026-09-10', posted=False)

    def down(method, path, body=None):
        raise RuntimeError('x-api unreachable')
    monkeypatch.setattr(up, '_x', down)
    with pytest.raises(RuntimeError):
        up.post_to_x()
    assert up.daily(days=1)['days'][0]['posted'] is False


def test_post_to_x_text_override_marks_nothing(up, monkeypatch):
    monkeypatch.setattr(up, '_x', lambda m_, p, body=None: {'data': {'id': '7'}})
    out = up.post_to_x(text='hello world')
    assert out['sent'] is True and 'marked' not in out


def test_x_status_reports_unreachable_api(up, monkeypatch):
    def down(method, path, body=None):
        raise RuntimeError('x-api unreachable at http://localhost:50350')
    monkeypatch.setattr(up, '_x', down)
    st = up.x_status()
    assert st['connected'] is False and 'unreachable' in st['error']

    monkeypatch.setattr(up, '_x', lambda m_, p, body=None: {'reads': True, 'writes': False})
    st = up.x_status()
    assert st['connected'] is True and st['can_read'] is True and st['can_post'] is False


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))


# --- merge_main: owner-only dev -> main, against throwaway git repos ---------

import subprocess as _sp


def _g(cwd, *a):
    return _sp.run(('git', '-c', 'user.name=t', '-c', 'user.email=t@t') + a, cwd=cwd,
                   check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """origin (bare) + a clone on dev; main and dev both exist upstream."""
    origin, work = tmp_path / 'origin.git', tmp_path / 'work'
    _g(tmp_path, 'init', '--bare', '-b', 'main', str(origin))
    _g(tmp_path, 'clone', str(origin), str(work))
    for k, v in (('user.name', 't'), ('user.email', 't@t')):
        _g(work, 'config', k, v)
    (work / 'a.txt').write_text('base\n')
    _g(work, 'add', '.'); _g(work, 'commit', '-m', 'base')
    _g(work, 'push', 'origin', 'HEAD:main')
    _g(work, 'checkout', '-b', 'dev'); _g(work, 'push', 'origin', 'dev')
    return work


def _commit_file(work, name, text, branch='dev'):
    _g(work, 'checkout', branch)
    (work / name).write_text(text)
    _g(work, 'add', '.'); _g(work, 'commit', '-m', f'{branch}: {name}')


def _remote_file(work, ref, name):
    _g(work, 'fetch', 'origin')
    return _g(work, 'show', f'{ref}:{name}')


def test_merge_main_fast_forward(up, repo):
    _commit_file(repo, 'b.txt', 'dev\n')
    r = up.merge_main(root=str(repo))
    assert r['ok'] and r['mode'] == 'fast-forward' and r['pushed'] and r['commits'] == 1
    assert _g(repo, 'rev-parse', 'origin/main') == _g(repo, 'rev-parse', 'dev')
    assert up.merge_main(root=str(repo))['mode'] == 'up-to-date'


def test_merge_main_clean_merge_keeps_main_only_work(up, repo):
    _commit_file(repo, 'm.txt', 'main\n', branch='main'); _g(repo, 'push', 'origin', 'main')
    _commit_file(repo, 'b.txt', 'dev\n')
    r = up.merge_main(root=str(repo))
    assert r['ok'] and r['mode'] == 'merge'
    assert _remote_file(repo, 'origin/main', 'm.txt') == 'main'
    assert _remote_file(repo, 'origin/main', 'b.txt') == 'dev'


def test_merge_main_conflict_forces_dev(up, repo):
    _commit_file(repo, 'a.txt', 'main side\n', branch='main'); _g(repo, 'push', 'origin', 'main')
    _commit_file(repo, 'a.txt', 'dev side\n')
    dry = up.merge_main(root=str(repo), dry=True)
    assert dry['ok'] and dry['mode'] == 'forced' and not dry['pushed']
    r = up.merge_main(root=str(repo))
    assert r['ok'] and r['mode'] == 'forced' and r['pushed']
    assert _remote_file(repo, 'origin/main', 'a.txt') == 'dev side'
    # main's own history survives: its old tip is an ancestor of the new one
    assert _sp.run(['git', 'merge-base', '--is-ancestor', dry['target_sha'], 'origin/main'],
                   cwd=repo).returncode == 0


def test_merge_main_rejected_push_is_never_forced(up, repo):
    """A protected main (GitHub GH006) must fail cleanly, not retry with --force."""
    hook = repo.parent / 'origin.git' / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "error: GH006: Protected branch update failed for refs/heads/main." >&2\nexit 1\n')
    hook.chmod(0o755)
    before = _g(repo, 'rev-parse', 'origin/main')
    _commit_file(repo, 'b.txt', 'dev\n')
    r = up.merge_main(root=str(repo))
    assert not r['ok'] and r['step'] == 'push'
    assert r['error'].startswith('GitHub refused: main is a protected branch')
    assert r['error'].count('GH006') == 1                 # one attempt, not two
    assert _g(repo.parent / 'origin.git', 'rev-parse', 'main') == before


def test_merge_main_never_touches_worktree(up, repo):
    _commit_file(repo, 'b.txt', 'dev\n')
    (repo / 'dirty.txt').write_text('uncommitted')
    up.merge_main(root=str(repo))
    assert _g(repo, 'rev-parse', '--abbrev-ref', 'HEAD') == 'dev'
    assert (repo / 'dirty.txt').read_text() == 'uncommitted'


def test_merge_main_owner_token_gate(up, monkeypatch):
    monkeypatch.setenv('UPDATES_OWNER', '0xABC')
    with pytest.raises(PermissionError):
        up._owner_from_token({})
    import time as _t

    class FakeAuth:
        def __init__(self, key): self.key = key
        def __call__(self): return self
        def verify(self, tok): return {'key': self.key, 'time': str(_t.time())}
    monkeypatch.setattr(m, 'mod', lambda name: FakeAuth('0xabc'))
    assert up._owner_from_token({'Authorization': 'Bearer x'}) == '0xabc'
    monkeypatch.setattr(m, 'mod', lambda name: FakeAuth('0xdef'))
    with pytest.raises(PermissionError):
        up._owner_from_token({'Authorization': 'Bearer x'})


def test_activity_zero_fills_and_buckets_local(up, monkeypatch):
    Mod._ACT_CACHE.clear()
    today = up._today()
    monkeypatch.setattr(up, '_is_local', lambda repo: True)
    monkeypatch.setattr(up, '_local_activity', lambda branch, since: [
        {'sha': 'a', 'date': today + 'T01:00:00Z', 'files': ['mod/orbit/x/a.py', 'mod/orbit/x/b.py']},
        {'sha': 'b', 'date': today + 'T02:00:00Z', 'files': ['mod/orbit/x/a.py', 'core/api/z.py']},
        {'sha': 'c', 'date': '1999-01-01T00:00:00Z', 'files': ['old.py']},   # outside the window
    ])
    a = up.activity(days=5)
    assert len(a['days']) == 5 and a['days'][-1]['date'] == today and a['source'] == 'git'
    last = a['days'][-1]
    assert (last['commits'], last['files'], last['modules']) == (2, 3, 2)
    assert last['top'][0] == {'name': 'x', 'files': 2}
    assert all(d['commits'] == 0 for d in a['days'][:-1])
    assert a['totals'] == {'commits': 2, 'active_days': 1, 'files': 3, 'modules': 2}


def test_activity_remote_is_commit_counts_only(up, monkeypatch):
    Mod._ACT_CACHE.clear()
    today = up._today()
    monkeypatch.setattr(up, '_is_local', lambda repo: False)
    monkeypatch.setattr(up, 'commits', lambda *a, **k: [_commit('o/r', 'f' * 40, today + 'T03:00:00Z')])
    a = up.activity(repo='o/r', days=3)
    assert a['source'] == 'api' and a['days'][-1]['commits'] == 1
    assert a['days'][-1]['files'] is None and a['totals']['files'] is None


def test_activity_smoke_over_local_dev_branch(up):
    Mod._ACT_CACHE.clear()
    a = up.activity(days=14)
    assert len(a['days']) == 14 and a['source'] == 'git'


# --- mod-protocol auth: every write is box-or-owner ------------------------

def _fake_auth(monkeypatch, key):
    import time as _t

    class FakeAuth:
        def __call__(self): return self
        def verify(self, tok): return {'key': key, 'time': str(_t.time())}
    monkeypatch.setattr(m, 'mod', lambda name: FakeAuth())


def test_whoami_standings(up, monkeypatch):
    monkeypatch.setenv('UPDATES_OWNER', '0xABC')
    gw = {'X-Forwarded-For': '1.2.3.4'}
    assert up.whoami({}, '127.0.0.1')['standing'] == 'box'
    assert up.whoami(gw, '127.0.0.1')['standing'] == 'anon'
    assert up.whoami({}, '10.0.0.9')['can_write'] is False
    _fake_auth(monkeypatch, '0xabc')
    w = up.whoami({**gw, 'Authorization': 'Bearer t'}, '127.0.0.1')
    assert w['standing'] == 'owner' and w['can_write'] and w['key'] == '0xabc'
    _fake_auth(monkeypatch, '0xdef')
    w = up.whoami({**gw, 'Authorization': 'Bearer t'}, '127.0.0.1')
    assert w['standing'] == 'signed' and not w['can_write']


def test_bad_token_is_reported_not_trusted(up, monkeypatch):
    class Boom:
        def __call__(self): return self
        def verify(self, tok): raise ValueError('Invalid signature')
    monkeypatch.setattr(m, 'mod', lambda name: Boom())
    w = up.whoami({'X-Forwarded-For': '1.2.3.4', 'Authorization': 'Bearer junk'}, '127.0.0.1')
    assert w['standing'] == 'anon' and 'invalid token' in w['error']


def test_mcp_writes_need_a_writer(up, monkeypatch):
    out = up._mcp_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': 'track', 'arguments': {'repo': 'foo/bar'}}},
                      local=False, writer=False)['result']
    assert out['isError'] and 'owner only' in out['content'][0]['text']
    seen = {}
    monkeypatch.setattr(up, 'updates', lambda **k: seen.update(k) or {'ok': 1})
    up._mcp_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                 'params': {'name': 'updates', 'arguments': {'mark_seen': True}}},
                local=False, writer=False)
    assert seen['mark_seen'] is False          # a reader can't advance markers
    up._mcp_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                 'params': {'name': 'track', 'arguments': {'repo': 'foo/bar'}}},
                local=False, writer=True)
    assert 'foo/bar' in up._load()['repos']


def test_http_writes_gated_by_real_protocol_token(up, monkeypatch):
    """End to end: gateway-shaped requests, a token minted by m.mod('auth')."""
    import json as _j
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    key = m.key()
    monkeypatch.setenv('UPDATES_OWNER', key.address)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), up._make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'

    def post(path, body, **hdr):
        req = urllib.request.Request(base + path, _j.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json',
                                              'X-Forwarded-For': '1.2.3.4', **hdr})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, _j.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _j.loads(e.read())
    try:
        code, j = post('/api/track', {'repo': 'foo/bar'})
        assert code == 403 and 'owner only' in j['error']
        assert 'foo/bar' not in up._load()['repos']
        tok = m.mod('auth')().token('updates', key=key)
        code, j = post('/api/track', {'repo': 'foo/bar'}, Authorization='Bearer ' + tok)
        assert code == 200, j
        assert 'foo/bar' in up._load()['repos']
        req = urllib.request.Request(base + '/api/whoami',
                                     headers={'Authorization': 'Bearer ' + tok,
                                              'X-Forwarded-For': '1.2.3.4'})
        with urllib.request.urlopen(req) as r:
            assert _j.loads(r.read())['standing'] == 'owner'
    finally:
        httpd.shutdown()


# --- Changes tab: uncommitted work -> owner commits to dev --------------------

def _dirty(repo):
    """a modified file, a new file in a module dir, a deletion, and a pre-staged
    stray that the owner did NOT pick."""
    (repo / 'mod' / 'orbit' / 'foo').mkdir(parents=True)
    _commit_file(repo, 'gone.txt', 'bye\n'); _g(repo, 'push', 'origin', 'dev')
    (repo / 'a.txt').write_text('base\nmore\n')
    (repo / 'mod' / 'orbit' / 'foo' / 'new.py').write_text('x = 1\ny = 2\n')
    (repo / 'gone.txt').unlink()
    (repo / 'stray.txt').write_text('staged by someone else\n')
    _g(repo, 'add', 'stray.txt')


def test_pending_lists_and_groups_uncommitted_work(up, repo):
    _dirty(repo)
    p = up.pending(root=str(repo))
    by = {f['path']: f for f in p['files']}
    assert by['a.txt']['status'] == 'modified' and by['a.txt']['add'] == 1
    assert by['mod/orbit/foo/new.py']['status'] == 'new' and by['mod/orbit/foo/new.py']['add'] == 2
    assert by['gone.txt']['status'] == 'deleted' and by['gone.txt']['rem'] == 1
    assert by['stray.txt']['staged']
    assert p['count'] == 4 and p['branch'] == 'dev' and p['ahead'] == 0
    assert {g['name'] for g in p['modules']} >= {'foo', 'root'}


def test_pending_diff_serves_only_pending_paths(up, repo):
    _dirty(repo)
    d = up.pending_diff('mod/orbit/foo/new.py', root=str(repo))
    assert '+x = 1' in d['diff'] and '/dev/null' in d['diff']
    assert '+more' in up.pending_diff('a.txt', root=str(repo))['diff']
    with pytest.raises(ValueError):
        up.pending_diff('.git/config', root=str(repo))      # never an arbitrary file


def test_commit_pending_commits_only_what_was_picked(up, repo):
    _dirty(repo)
    r = up.commit_pending(paths=['a.txt', 'gone.txt'], modules=['foo'], root=str(repo))
    assert r['ok'] and r['pushed'] and r['files'] == 3, r
    assert _g(repo, 'rev-parse', 'HEAD') == _g(repo.parent / 'origin.git', 'rev-parse', 'dev')
    shipped = set(_g(repo, 'show', '--name-only', '--format=', 'HEAD').split())
    assert shipped == {'a.txt', 'gone.txt', 'mod/orbit/foo/new.py'}
    # the stray someone else staged is still staged and still uncommitted
    left = up.pending(root=str(repo))
    assert [f['path'] for f in left['files']] == ['stray.txt'] and left['files'][0]['staged']


def test_commit_pending_refuses_oversize_wrong_branch_and_junk(up, repo, monkeypatch):
    _dirty(repo)
    monkeypatch.setattr(_updates, 'PENDING_MAX_FILE', 5)
    r = up.commit_pending(paths=['a.txt'], root=str(repo))
    assert not r['ok'] and r['step'] == 'size' and 'a.txt' in r['error']
    monkeypatch.setattr(_updates, 'PENDING_MAX_FILE', 50_000_000)
    assert up.commit_pending(paths=['nope.txt'], root=str(repo))['step'] == 'select'
    _g(repo, 'checkout', '-q', 'main')
    r = up.commit_pending(root=str(repo))
    assert not r['ok'] and r['step'] == 'branch'
    _g(repo, 'checkout', '-q', 'dev')


def test_commit_pending_rejected_push_keeps_local_commit(up, repo):
    _dirty(repo)
    hook = repo.parent / 'origin.git' / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho nope >&2\nexit 1\n'); hook.chmod(0o755)
    r = up.commit_pending(paths=['a.txt'], message='hello', root=str(repo))
    assert r['ok'] and not r['pushed'] and 'nope' in r['push_error']
    assert _g(repo, 'log', '-1', '--format=%s') == 'hello'


def test_http_commit_pending_is_owner_only(up, monkeypatch, repo):
    import json as _j
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    monkeypatch.setenv('UPDATES_OWNER', '0xABC')
    up._toplevel = str(repo)
    _dirty(repo)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), up._make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    gw = {'X-Forwarded-For': '1.2.3.4'}

    def call(path, body=None):
        req = urllib.request.Request(base + path, None if body is None else _j.dumps(body).encode(),
                                     method='GET' if body is None else 'POST',
                                     headers={'Content-Type': 'application/json', **gw})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, _j.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _j.loads(e.read())
    try:
        code, j = call('/api/pending')
        assert code == 200 and j['count'] == 4               # the list is public
        assert call('/api/pending_diff?path=a.txt')[0] == 403  # contents are not
        code, j = call('/api/commit_pending', {'paths': ['a.txt']})
        assert code == 403
        assert _g(repo, 'log', '-1', '--format=%s') == 'dev: gone.txt'
        _fake_auth(monkeypatch, '0xabc')
        gw['Authorization'] = 'Bearer t'
        code, j = call('/api/commit_pending', {'paths': ['a.txt'], 'push': False})
        assert code == 200 and j['ok'] and not j['pushed'], j
    finally:
        httpd.shutdown()
