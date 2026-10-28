"""Offline tests for bt.git — URL parsing, GraphQL row mapping, the local index.

Nothing touches GitHub: `git._req` is replaced with canned responses.
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'
os.environ.setdefault('BT_DATA_DIR', '/tmp/bt-test-default')

from bt import git, tools  # noqa: E402

NOW = time.time()


def iso(ago_days):
    return git._iso(NOW - ago_days * 86400)


def gql_repo(name='o/r', stars=40, commits30=12):
    return {
        'nameWithOwner': name, 'description': 'a subnet', 'url': f'https://github.com/{name}',
        'homepageUrl': '', 'stargazerCount': stars, 'forkCount': 7,
        'watchers': {'totalCount': 3}, 'openIssues': {'totalCount': 5},
        'closedIssues': {'totalCount': 9}, 'openPRs': {'totalCount': 2},
        'mergedPRs': {'totalCount': 30}, 'createdAt': iso(400), 'pushedAt': iso(1),
        'isArchived': False, 'isFork': False, 'diskUsage': 1234,
        'licenseInfo': {'spdxId': 'MIT'}, 'primaryLanguage': {'name': 'Python'},
        'languages': {'totalSize': 1000, 'edges': [
            {'size': 750, 'node': {'name': 'Python', 'color': '#3572A5'}},
            {'size': 250, 'node': {'name': 'Rust', 'color': '#dea584'}}]},
        'repositoryTopics': {'nodes': [{'topic': {'name': 'bittensor'}}]},
        'releases': {'totalCount': 4, 'nodes': [{'tagName': 'v1.2', 'publishedAt': iso(3)}]},
        'owner': {'__typename': 'Organization', 'login': 'o', 'repositories': {
            'totalCount': 6, 'nodes': [{'name': 'r', 'stargazerCount': stars,
                                        'pushedAt': iso(1), 'description': None}]}},
        'defaultBranchRef': {'name': 'main', 'target': {
            'all': {'totalCount': 500}, 'd7': {'totalCount': 3},
            'd30': {'totalCount': commits30, 'nodes': [
                {'author': {'email': 'a@x', 'user': {'login': 'alice'}}},
                {'author': {'email': 'b@x', 'user': {'login': 'bob'}}},
                {'author': {'email': 'a2@x', 'user': {'login': 'alice'}}},
                {'author': {'email': 'x', 'user': {'login': 'dependabot[bot]'}}},
                {'author': {'email': 'carol@x', 'user': None}}]},
            'd90': {'totalCount': 40},
            'last': {'nodes': [{'oid': 'abcdef1234567890', 'committedDate': iso(1),
                                'messageHeadline': 'fix it', 'author': {'name': 'Alice',
                                                                        'user': {'login': 'alice'}}}]}}},
    }


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(git, 'token', lambda: 'tkn')
    calls = []

    def req(url, body=None):
        calls.append((url, body))
        if url.endswith('/graphql'):
            q = body['query']
            if 'repositoryOwner' in q:       # org URL resolution
                return 200, {}, {'data': {'repositoryOwner': {'repositories': {'nodes': [
                    {'nameWithOwner': 'org/old', 'stargazerCount': 0, 'pushedAt': iso(200)},
                    {'nameWithOwner': 'org/live', 'stargazerCount': 0, 'pushedAt': iso(1)}]}}}}
            data, errors = {}, []
            for i, line in enumerate(l for l in q.splitlines() if 'repository(owner' in l):
                name = line.split('name: "')[1].split('"')[0]
                owner = line.split('owner: "')[1].split('"')[0]
                if name == 'missing':
                    data[f'r{i}'] = None
                    errors.append({'path': [f'r{i}'], 'message': 'Could not resolve'})
                else:
                    data[f'r{i}'] = gql_repo(f'{owner}/{name}')
            return 200, {}, {'data': data, 'errors': errors}
        if '/contributors' in url:
            return 200, {'Link': '<https://api.github.com/x?per_page=1&anon=1&page=26>; rel="last"'}, [{}]
        if url.endswith('/stats/participation'):
            return 200, {}, {'all': list(range(52))}
        return 404, {}, None

    monkeypatch.setattr(git, '_req', req)
    return calls


def test_parse_url():
    assert git.parse_url('https://github.com/o/r') == ('o', 'o/r')
    assert git.parse_url('https://github.com/o/r.git') == ('o', 'o/r')
    assert git.parse_url('https://github.com/entrius/gittensor/tree/main') == ('entrius', 'entrius/gittensor')
    assert git.parse_url('https://github.com/orgs/ditto/repositories') == ('ditto', None)
    assert git.parse_url('https://github.com/CookingTao/') == ('CookingTao', None)
    assert git.parse_url('https://gitlab.com/o/r') == (None, None)
    assert git.parse_url(None) == (None, None)


def test_fetch_batch_maps_everything(fake):
    rows = git.fetch_batch([{'netuid': 1, 'github': 'https://github.com/o/r'},
                            {'netuid': 2, 'github': 'https://github.com/orgs/org/repositories'},
                            {'netuid': 3, 'github': 'https://github.com/o/missing'},
                            {'netuid': 4, 'github': None}])
    by = {r['netuid']: r for r in rows}
    r = by[1]
    assert r['repo'] == 'o/r' and r['stars'] == 40 and r['forks'] == 7
    assert r['commits_total'] == 500 and r['commits_30d'] == 12 and r['commits_90d'] == 40
    assert r['authors_30d'] == 3                  # alice, bob, carol@x; bot dropped, alice once
    assert r['bot_pct_30d'] == 20.0
    assert r['contributors'] == 26 and r['weekly'] == list(range(52))
    assert r['languages'][0] == {'name': 'Python', 'color': '#3572A5', 'pct': 75.0}
    assert r['latest_release'] == 'v1.2' and r['owner_repos'] == 6
    assert r['last_commit_sha'] == 'abcdef1234' and r['last_commit_author'] == 'alice'
    # org URL -> most-starred, ties broken by most recent push
    assert by[2]['repo'] == 'org/live' and by[2]['resolved_from'].endswith('/repositories')
    assert 'Could not resolve' in by[3]['error']
    assert 'no github repo' in by[4]['error']
    # one GraphQL query for the whole batch (+1 for the org resolution)
    assert sum(1 for u, _ in fake if u.endswith('/graphql')) == 2


def test_store_keeps_last_good_on_error(fake):
    git.store(git.fetch_batch([{'netuid': 1, 'github': 'https://github.com/o/r'}]))
    git.store([{'netuid': 1, 'url': 'https://github.com/o/r', 'error': 'github 502'}])
    r = git.repo(1)
    assert r['stars'] == 40 and r['error'] == 'github 502'
    assert r['weekly'] == list(range(52))
    # a changed URL that fails wipes the old repo's numbers
    git.store([{'netuid': 1, 'url': 'https://github.com/o/new', 'error': 'gone'}])
    assert git.repo(1)['stars'] is None


def test_growth_and_board(fake):
    rows = git.fetch_batch([{'netuid': 1, 'github': 'https://github.com/o/r'},
                            {'netuid': 2, 'github': 'https://github.com/o/s'}])
    git.store(rows, now=NOW - 8 * 86400)
    rows[0]['stars'] = 55
    rows[0]['commits_total'] = 520
    git.store(rows, now=NOW)
    r = git.repo(1)
    assert r['stars_gain_7d'] == 15 and r['commits_gain_7d'] == 20
    assert r['stars_gain_30d'] is None            # no snapshot that old
    assert len(r['history']) == 2
    b = git.board(sort_by='stars')
    assert [x['netuid'] for x in b['rows']] == [1, 2]
    assert b['with_repo'] == 2 and 0 < b['rows'][0]['dev_score'] <= 100
    with pytest.raises(ValueError):
        git.board(sort_by='bogus')


def test_dev_score_shape():
    base = {'repo': 'o/r', 'stars': 0, 'commits_30d': 0, 'last_commit_ts': NOW - 400 * 86400}
    busy = dict(base, stars=500, commits_30d=200, commits_90d=500, authors_30d=10,
                contributors=50, forks=100, last_commit_ts=NOW)
    assert git.dev_score(base, NOW) == 0
    assert git.dev_score(busy, NOW) > 60
    assert git.dev_score(dict(busy, archived=1), NOW) < git.dev_score(busy, NOW)
    assert git.dev_score(dict(busy, bot_pct_30d=95), NOW) < git.dev_score(busy, NOW)
    assert git.dev_score({'repo': None}) is None


def test_tools_registered():
    for name in ('bt_git', 'bt_git_board', 'bt_git_refresh', 'bt_git_status'):
        t = tools.TOOL_MAP[name]
        assert t.local and not t.mutates and t.group == 'Dev'
    for name in ('bt_subnet_profile', 'bt_subnet_profiles'):
        assert tools.TOOL_MAP[name].local
    assert 'code' in tools.SUBNET_TABS
