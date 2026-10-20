"""The credentialed rails, end to end, against a strict stand-in for api.x.com.

`test_x.py` runs the backend with no credentials, so it can only prove the
refusals. Here the backend gets dummy keys and `X_BASE_URL` points it at
`tests/mock_x.py`, which rejects any read without the bearer and any
account action whose OAuth 1.0a signature it can't independently reproduce.
So every tool below has really been signed, sent and parsed — the only thing
not exercised is X itself.
"""
import os
import socket
import subprocess
import sys
import time

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from x.mod import Mod  # noqa: E402

BINARY = os.path.join(ROOT, 'x-rs', 'target', 'release', 'x-api')
MOCK = os.path.join(ROOT, 'tests', 'mock_x.py')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait(url):
    for _ in range(100):
        try:
            requests.get(url, timeout=1)
            return
        except requests.ConnectionError:
            time.sleep(0.05)
    pytest.fail(f'{url} did not come up')


@pytest.fixture(scope='module')
def mock():
    port = free_port()
    proc = subprocess.Popen([sys.executable, MOCK, str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    wait(f'{url}/_mock/log')
    yield url
    proc.kill()


@pytest.fixture(scope='module')
def server(mock):
    if not os.path.exists(BINARY):
        pytest.skip(f'{BINARY} not built — run `m x/build`')
    port = free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('X_', 'TWITTER_'))}
    env.update(PORT=str(port), HOME='/tmp/x-upstream-test-home', X_BASE_URL=mock,
               X_BEARER_TOKEN='mock', X_API_KEY='k', X_API_SECRET='s',
               X_ACCESS_TOKEN='at', X_ACCESS_SECRET='as')
    proc = subprocess.Popen([BINARY], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    wait(f'{url}/health')
    yield url
    proc.kill()


@pytest.fixture(scope='module')
def mod(server):
    return Mod(server_url=server)


def last(mock, method=None, path=None):
    """The most recent upstream request matching method/path."""
    for r in reversed(requests.get(f'{mock}/_mock/log').json()):
        if (method is None or r['method'] == method) and (path is None or r['path'] == path):
            return r
    raise AssertionError(f'no upstream {method} {path}')


# ----------------------------------------------------------------- reads

def test_both_rails_report_configured(mod):
    s = mod.auth_status()
    assert s['reads'] and s['writes'] and s['bearer'] and s['user_context']


def test_search_joins_authors_and_passes_the_query_verbatim(mod, mock):
    out = mod.search('mcp -is:retweet lang:en', max_results=25)
    assert len(out['data']) == 3 and out['includes']['users']
    up = last(mock, 'GET', '/2/tweets/search/recent')
    assert up['auth'] == 'Bearer' and up['ok']
    assert up['query']['query'] == 'mcp -is:retweet lang:en'
    assert up['query']['max_results'] == '25'
    assert up['query']['expansions'] == 'author_id'


def test_counts(mod, mock):
    out = mod.counts('bittensor', granularity='hour')
    assert out['meta']['total_tweet_count'] > 0
    assert last(mock, 'GET', '/2/tweets/counts/recent')['query']['granularity'] == 'hour'


def test_get_post_uses_the_api_when_keyed(mod, mock):
    out = mod.get_post('https://x.com/jack/status/1900000000000000001?s=20')
    assert out['data']['id'] == '1900000000000000001'
    assert last(mock, 'GET', '/2/tweets/1900000000000000001')['ok']


def test_user_profile(mod):
    assert mod.user('@jack')['data']['id'] == '12'


def test_timeline_resolves_the_handle_first(mod, mock):
    out = mod.timeline('jack', max_results=5, exclude='replies')
    assert {t['author_id'] for t in out['data']} == {'12'}
    up = last(mock, 'GET', '/2/users/12/tweets')
    assert up['query']['exclude'] == 'replies' and up['query']['max_results'] == '5'


def test_followers_and_following(mod, mock):
    assert mod.followers('jack', max_results=50)['meta']['result_count'] == 2
    assert last(mock, 'GET', '/2/users/12/followers')['query']['max_results'] == '50'
    assert mod.following('jack')['data']


def test_unknown_user_is_a_404_not_a_crash(server):
    r = requests.get(f'{server}/users/nobody_here/timeline')
    assert r.status_code == 404
    assert 'Could not find user' in r.json()['error']


# ----------------------------------------------------------------- user context (signed)

def test_me_is_signed_oauth1(mod, mock):
    assert mod.me()['data']['username'] == 'modprotocol'
    up = last(mock, 'GET', '/2/users/me')
    assert up['auth'] == 'OAuth' and up['ok']


def test_my_mentions_signs_a_query_with_commas(mod, mock):
    """tweet.fields etc. carry commas — the signed bytes must be the wire bytes."""
    assert mod.mentions(max_results=7)['data']
    up = last(mock, 'GET', '/2/users/999/mentions')
    assert up['auth'] == 'OAuth' and up['ok']
    assert up['query']['max_results'] == '7'


def test_post_with_reply_quote_and_poll(mod, mock):
    out = mod.post('hello + goodbye & 100% ✓', reply_to='https://x.com/jack/status/1900000000000000001',
                   quote_post_id='1900000000000000003', poll_options=['yes', 'no'],
                   poll_duration_minutes=60)
    assert out['data']['id'] == '1900000000000000042'
    up = last(mock, 'POST', '/2/tweets')
    assert up['auth'] == 'OAuth' and up['ok']
    assert up['body'] == {
        'text': 'hello + goodbye & 100% ✓',
        'reply': {'in_reply_to_tweet_id': '1900000000000000001'},
        'quote_tweet_id': '1900000000000000003',
        'poll': {'options': ['yes', 'no'], 'duration_minutes': 60},
    }


def test_delete_post(mod, mock):
    assert mod.delete_post('1900000000000000042')['data']['deleted'] is True
    assert last(mock, 'DELETE', '/2/tweets/1900000000000000042')['ok']


def test_like_and_repost_target_my_id(mod, mock):
    assert mod.like('1900000000000000001')['data']['liked'] is True
    assert last(mock, 'POST', '/2/users/999/likes')['body'] == {'tweet_id': '1900000000000000001'}
    assert mod.repost('1900000000000000001')['data']['retweeted'] is True
    assert last(mock, 'POST', '/2/users/999/retweets')['ok']


def test_follow_resolves_both_ends(mod, mock):
    assert mod.follow('@jack')['data']['following'] is True
    up = last(mock, 'POST', '/2/users/999/following')
    assert up['ok'] and up['body'] == {'target_user_id': '12'}


def test_every_upstream_request_authenticated(mock):
    log = requests.get(f'{mock}/_mock/log').json()
    assert log and all(r['ok'] for r in log), [r for r in log if not r['ok']]


# ----------------------------------------------------------------- input hygiene

@pytest.mark.parametrize('bad', ['..%2F..%2Ftweets', 'a b', 'x' * 40])
def test_handles_cannot_reaim_upstream_paths(server, bad):
    r = requests.get(f'{server}/users/{bad}/timeline')
    assert r.status_code == 400, r.text


def test_non_numeric_post_ids_are_refused(mod):
    with pytest.raises(RuntimeError, match='not a post id'):
        mod.get_post('../users/me')


# ----------------------------------------------------------------- write gate

def test_cross_origin_browser_writes_are_refused(server, mock):
    before = len(requests.get(f'{mock}/_mock/log').json())
    evil = {'Origin': 'https://evil.example'}
    assert requests.post(f'{server}/posts', json={'text': 'pwned'}, headers=evil).status_code == 403
    assert requests.post(f'{server}/posts/1/like', headers=evil).status_code == 403
    assert requests.delete(f'{server}/posts/1', headers=evil).status_code == 403
    assert requests.post(f'{server}/users/jack/follow', headers=evil).status_code == 403
    assert requests.post(f'{server}/forward', json={'action': 'post', 'text': 'pwned'},
                         headers=evil).status_code == 403
    assert requests.post(f'{server}/mcp', headers=evil, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'post', 'arguments': {'text': 'pwned'}}}).status_code == 403
    assert requests.post(f'{server}/auth/keys', json={'bearer_token': 'x'},
                         headers=evil).status_code == 403
    assert len(requests.get(f'{mock}/_mock/log').json()) == before, 'a refused write reached upstream'


def test_cross_origin_reads_still_work(server):
    r = requests.get(f'{server}/search?q=mcp', headers={'Origin': 'https://elsewhere.example'})
    assert r.status_code == 200


def test_the_app_itself_may_write(server):
    host = server.split('://', 1)[1]
    r = requests.post(f'{server}/posts', json={'text': 'from the app'},
                      headers={'Origin': server, 'Host': host, 'Sec-Fetch-Site': 'same-origin'})
    assert r.status_code == 200, r.text


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('10.255.255.255', 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith('127.') else ip


def test_writes_from_another_machine_are_refused(server):
    ip = lan_ip()
    if not ip:
        pytest.skip('no non-loopback interface to call from')
    remote = server.replace('127.0.0.1', ip)
    assert requests.get(f'{remote}/search?q=mcp').status_code == 200
    r = requests.post(f'{remote}/posts', json={'text': 'from the LAN'})
    assert r.status_code == 403 and 'loopback' in r.json()['error']
