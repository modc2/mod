import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import x402agent as agent  # noqa: E402


def test_split_picks_strips_line_and_dedupes():
    text = 'Two fit:\n- **a** cheap\n\nPICKS: https://a.io/x, https://b.io/y., https://a.io/x'
    ans, urls = agent.split_picks(text)
    assert 'PICKS' not in ans and ans.startswith('Two fit')
    assert urls == ['https://a.io/x', 'https://b.io/y']


def test_split_picks_empty_and_missing():
    assert agent.split_picks('nothing fits\nPICKS:') == ('nothing fits', [])
    assert agent.split_picks('no line at all') == ('no line at all', [])


def test_picks_drops_invented_urls():
    idx = {'https://a.io/x': {'url': 'https://a.io/x', 'raw': {}, 'offers': [], 'name': 'A'}}

    def lookup(u):
        if u not in idx:
            raise ValueError('not indexed')
        return idx[u]
    out = agent.picks(['https://made.up/z', 'https://a.io/x'], lookup)
    assert [p['url'] for p in out] == ['https://a.io/x'] and 'raw' not in out[0]


def test_bad_session_refused():
    try:
        agent._cmd('q', 1, 'sonnet', 'not a session; rm')
    except ValueError:
        return
    raise AssertionError('accepted a bad session id')
