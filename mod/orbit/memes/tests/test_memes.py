"""Offline tests — parsing and wiring only, no meme site is hit."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import sites  # noqa: E402


def test_config_parses_and_matches():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'memes'
    assert cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 50900
    for fn in cfg['api_fns']:
        assert fn in cfg['fns']


def test_anchor_loads_by_path():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'memes_anchor', os.path.join(MODULE_DIR, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    m = module.Mod()
    assert m.port == 50900
    health = m.health()
    assert health['ok'] and health['sources'] == sites.SOURCES


def test_reddit_parser_normalizes_and_filters():
    data = {'data': {'children': [
        {'data': {'id': 'a1', 'title': 'good', 'permalink': '/r/memes/a1/',
                  'url': 'https://i.redd.it/a1.jpg', 'score': 5,
                  'subreddit': 'memes', 'over_18': False, 'created_utc': 1}},
        {'data': {'id': 'a2', 'title': 'nsfw', 'permalink': '/r/memes/a2/',
                  'url': 'https://i.redd.it/a2.png', 'score': 9,
                  'subreddit': 'memes', 'over_18': True, 'created_utc': 2}},
        {'data': {'id': 'a3', 'title': 'video, no preview', 'permalink': '/x/',
                  'url': 'https://v.redd.it/a3', 'score': 7,
                  'subreddit': 'memes', 'over_18': False, 'created_utc': 3}},
    ]}}
    out = sites._reddit_posts(data, nsfw=False)
    assert [m['id'] for m in out] == ['a1']
    assert out[0]['source'] == 'reddit' and out[0]['author'] == 'r/memes'
    assert len(sites._reddit_posts(data, nsfw=True)) == 2


def test_ninegag_parser_picks_best_image():
    data = {'data': {'posts': [
        {'id': 'g1', 'title': 't', 'url': 'https://9gag.com/gag/g1',
         'nsfw': 0, 'upVoteCount': 3, 'creationTs': 1,
         'images': {'image460': {'url': 'small.jpg'},
                    'image700': {'url': 'big.jpg'}}},
        {'id': 'g2', 'title': 'no image', 'url': '', 'nsfw': 0, 'images': {}},
    ]}}
    out = sites._ninegag_posts(data, nsfw=False)
    assert len(out) == 1 and out[0]['image'] == 'big.jpg'


def test_kym_parser_finds_entries_and_gates_sensitive():
    # Trimmed from the live search page markup (2026-09): the anchor carries
    # data-title + href, the direct image follows as data-image.
    page = ('<a class="photo" alt="x" data-title="Doge" href="/sensitive/memes/doge">'
            '<img loading="lazy" data-image="https://i.kym-cdn.com/doge.jpg" src="t">'
            '</a>'
            '<a data-title="Can I Get Uhhh" href="/memes/can-i-get-uhhh">'
            '<img data-image="https://i.kym-cdn.com/uhhh.jpg" src="t"></a>'
            '<a href="/memes/popular">nav, no data-title</a>')
    orig = sites._get
    sites._get = lambda *a, **k: page
    try:
        sfw = sites.knowyourmeme_search('doge')
        assert [m['id'] for m in sfw] == ['can-i-get-uhhh']
        both = sites.knowyourmeme_search('doge', nsfw=True)
        assert [m['id'] for m in both] == ['doge', 'can-i-get-uhhh']
        assert both[0]['url'] == 'https://knowyourmeme.com/memes/doge'
        assert both[0]['image'] == 'https://i.kym-cdn.com/doge.jpg'
        assert both[0]['nsfw'] is True
    finally:
        sites._get = orig


def test_fanout_survives_a_dead_source():
    jobs = {'up': lambda: [sites._meme('reddit', '1', 'a', 'u', 'i.jpg', 5)],
            'down': lambda: (_ for _ in ()).throw(RuntimeError('blocked'))}
    out = sites._fan(jobs, limit=10)
    assert out['count'] == 1
    assert 'down' in out['errors'] and 'RuntimeError' in out['errors']['down']


def test_fanout_dedupes_by_image_and_ranks():
    jobs = {'a': lambda: [sites._meme('reddit', '1', 'lo', 'u', 'same.jpg', 5),
                          sites._meme('reddit', '2', 'hi', 'u', 'other.jpg', 9)],
            'b': lambda: [sites._meme('ninegag', '3', 'dupe', 'u', 'same.jpg', 7)]}
    out = sites._fan(jobs, limit=10)
    assert out['count'] == 2
    assert [m['score'] for m in out['memes']] == [9, 5]


def test_search_validates_input():
    assert 'error' in sites.search('')
    assert 'error' in sites.search('doge', source='myspace')


def test_serve_api_routes_exist():
    import serve
    for fn in serve.API_FNS:
        assert fn in {'health', 'info', 'sources', 'search', 'trending',
                      'random', 'templates', 'readme'}
    assert serve.api('health', {})['ok'] is True
    assert os.path.exists(os.path.join(serve.WEB_DIR, 'index.html'))
