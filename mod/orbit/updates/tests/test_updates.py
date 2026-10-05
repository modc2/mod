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
