"""Offline tests — parsing, resolution and wiring. Letterboxd is never hit.

The fixtures are hand-built to the shape of the real pages rather than being
scraped copies: small enough to read, and nobody's diary ends up in the repo.
Each one keeps the details that actually broke something — an apostrophe
entity in a title, a review with a spoiler warning, a bare-slug film that is
not the famous one.
"""

import json
import os
import sys
import urllib.error

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import letterboxd as lb  # noqa: E402
import taste  # noqa: E402

RSS = '''<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com"
     xmlns:tmdb="https://themoviedb.org" xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel>
  <title>Letterboxd - Ada Lens</title>
  <link>https://letterboxd.com/ada/</link>
  <item>
   <title>The President&#039;s Cake, 2025 - ★★★★</title>
   <link>https://letterboxd.com/ada/film/the-presidents-cake/</link>
   <guid isPermaLink="false">letterboxd-watch-1496417008</guid>
   <pubDate>Tue, 15 Sep 2026 09:03:49 +1200</pubDate>
   <letterboxd:watchedDate>2026-09-14</letterboxd:watchedDate>
   <letterboxd:rewatch>No</letterboxd:rewatch>
   <letterboxd:filmTitle>The President&#039;s Cake</letterboxd:filmTitle>
   <letterboxd:filmYear>2025</letterboxd:filmYear>
   <letterboxd:memberRating>4.0</letterboxd:memberRating>
   <letterboxd:memberLike>Yes</letterboxd:memberLike>
   <tmdb:movieId>1464883</tmdb:movieId>
   <description><![CDATA[ <p><img src="https://a.ltrbxd.com/cake.jpg"/></p> <p>Watched on Monday September 14, 2026.</p> ]]></description>
  </item>
  <item>
   <title>Parasite, 2019 - ★★★★½ (contains spoilers)</title>
   <link>https://letterboxd.com/ada/film/parasite-2019/</link>
   <guid isPermaLink="false">letterboxd-review-1496003141</guid>
   <pubDate>Tue, 15 Sep 2026 02:09:55 +1200</pubDate>
   <letterboxd:watchedDate>2026-09-12</letterboxd:watchedDate>
   <letterboxd:rewatch>Yes</letterboxd:rewatch>
   <letterboxd:filmTitle>Parasite</letterboxd:filmTitle>
   <letterboxd:filmYear>2019</letterboxd:filmYear>
   <letterboxd:memberRating>4.5</letterboxd:memberRating>
   <letterboxd:memberLike>Yes</letterboxd:memberLike>
   <tmdb:movieId>496243</tmdb:movieId>
   <description><![CDATA[ <p><img src="https://a.ltrbxd.com/par.jpg"/></p><p>Watched on Saturday September 12, 2026.</p><p>The stairs do the talking.</p><p>Second time through.</p> ]]></description>
  </item>
  <item>
   <title>CANNES 2026</title>
   <link>https://letterboxd.com/ada/list/cannes-2026/</link>
   <guid isPermaLink="false">letterboxd-list-1688929</guid>
   <pubDate>Tue, 4 Jul 2026 08:58:00 +1200</pubDate>
   <description><![CDATA[ <p>Everything from the Croisette.</p> ]]></description>
  </item>
 </channel>
</rss>'''

FILM_HTML = '''<html><head>
<script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"Parasite","dateCreated":"2019-05-21",
 "url":"https://letterboxd.com/film/parasite-2019/",
 "image":"https://a.ltrbxd.com/parasite.jpg",
 "description":"A family takes peculiar interest in the wealthy Parks.",
 "director":[{"@type":"Person","name":"Bong Joon Ho"}],
 "actor":[{"@type":"Person","name":"Song Kang-ho"},{"@type":"Person","name":"Lee Sun-kyun"}],
 "productionCompany":[{"@type":"Organization","name":"Barunson E&A"}],
 "countryOfOrigin":[{"@type":"Country","name":"South Korea"}],
 "inLanguage":["ko","en"],"genre":["Thriller","Comedy","Drama"],"duration":"PT2H13M",
 "aggregateRating":{"@type":"AggregateRating","ratingValue":4.52,"ratingCount":5845346,
                    "reviewCount":784881,"bestRating":5,"worstRating":0.5}}
/* ]]> */
</script></head><body></body></html>'''

GRID_HTML = '''<ul class="grid">
 <li><div class="react-component" data-component-class="LazyPoster"
   data-item-name="Spider-Man: Brand New Day (2026)"
   data-item-slug="spider-man-brand-new-day"
   data-item-link="/film/spider-man-brand-new-day/"></div></li>
 <li><div class="react-component" data-item-name="Se&#039;lah &amp; Sons (1999)"
   data-item-slug="selah-and-sons" data-item-link="/film/selah-and-sons/"></div></li>
 <li><div class="react-component" data-item-name="Spider-Man: Brand New Day (2026)"
   data-item-slug="spider-man-brand-new-day"
   data-item-link="/film/spider-man-brand-new-day/"></div></li>
</ul>'''


# ── the contract between config.json and the anchor ──────────────

def _config():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        return json.load(f)


def test_config_parses_and_matches_the_anchor():
    cfg = _config()
    assert cfg['name'] == 'boxd'
    assert cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 50940
    for fn in cfg['api_fns']:
        assert fn in cfg['fns'], f'{fn} is served but not declared'


def test_anchor_loads_by_path_and_declares_what_it_has():
    """The orbit loader imports this file by path, not as a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'boxd_anchor', os.path.join(MODULE_DIR, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    m = module.Mod()
    assert m.port == 50940
    cfg = _config()
    for fn in cfg['fns']:
        assert callable(getattr(m, fn, None)), f'config declares {fn}, anchor lacks it'
    assert m.health()['ok'] is True          # no network in health, by design
    assert m.forward() == m.info()           # the null call


# ── the member feed ──────────────────────────────────────────────

def test_parse_rss_reads_a_watch():
    out = lb.parse_rss(RSS)
    assert out['member'] == {'name': 'Ada Lens', 'url': 'https://letterboxd.com/ada/',
                             'user': 'ada'}
    watch = out['entries'][0]
    assert watch['kind'] == 'watch'
    assert watch['film'] == "The President's Cake"   # entity-decoded by the parser
    assert watch['year'] == 2025
    assert watch['rating'] == 4.0
    assert watch['stars'] == '★★★★'
    assert watch['liked'] is True
    assert watch['rewatch'] is False
    assert watch['watched'] == '2026-09-14'
    assert watch['slug'] == 'the-presidents-cake'
    assert watch['tmdb'] == '1464883'
    assert watch['poster'] == 'https://a.ltrbxd.com/cake.jpg'
    assert 'review' not in watch or watch['review'] is None


def test_parse_rss_reads_a_review_without_the_boilerplate():
    review = lb.parse_rss(RSS)['entries'][1]
    assert review['kind'] == 'review'
    assert review['rewatch'] is True
    assert review['spoilers'] is True
    assert review['stars'] == '★★★★½'
    # The poster <img> and the "Watched on ..." line are boilerplate; the
    # member's actual words are what is left.
    assert review['review'].startswith('The stairs do the talking.')
    assert 'Watched on' not in review['review']
    assert '<p>' not in review['review']
    assert 'Second time through.' in review['review']


def test_parse_rss_keeps_lists_apart_from_films():
    entries = lb.parse_rss(RSS)['entries']
    lists = [e for e in entries if e['kind'] == 'list']
    assert len(lists) == 1
    assert lists[0]['title'] == 'CANNES 2026'
    assert lists[0]['note'] == 'Everything from the Croisette.'
    assert 'film' not in lists[0]


def test_stars_render_halves():
    assert lb.stars(4.5) == '★★★★½'
    assert lb.stars(3.0) == '★★★'
    assert lb.stars(0.5) == '½'
    assert lb.stars(None) is None


def test_diary_filters_by_kind_and_reports_its_own_window(monkeypatch):
    monkeypatch.setattr(lb, 'fetch', lambda *a, **k: (RSS, {'cached': False}))
    assert len(lb.diary('ada')['entries']) == 3
    assert len(lb.diary('ada', kind='review')['entries']) == 1
    assert len(lb.watches('ada')['entries']) == 2      # lists dropped
    assert 'window' in lb.diary('ada')['coverage']


def test_a_member_is_required():
    with pytest.raises(ValueError):
        lb.diary(None)


# ── one film ─────────────────────────────────────────────────────

def test_parse_film_reads_the_json_ld():
    f = lb.parse_film(FILM_HTML)
    assert f['title'] == 'Parasite'
    assert f['year'] == 2019
    assert f['slug'] == 'parasite-2019'
    assert f['directors'] == ['Bong Joon Ho']
    assert f['cast'] == ['Song Kang-ho', 'Lee Sun-kyun']
    assert f['genres'] == ['Thriller', 'Comedy', 'Drama']
    assert f['runtime'] == 133                 # PT2H13M
    assert f['rating'] == 4.52
    assert f['ratings'] == 5845346
    assert f['countries'] == ['South Korea']


def test_parse_film_says_so_when_the_markup_moves():
    with pytest.raises(LookupError):
        lb.parse_film('<html><body>no ld+json here</body></html>')


def test_slugify_handles_punctuation():
    assert lb.slugify("The President's Cake") == 'the-presidents-cake'
    assert lb.slugify('Se7en', 1995) == 'se7en-1995'
    assert lb.slugify('WALL·E') == 'wall-e'


def test_film_tries_the_year_before_the_bare_slug(monkeypatch):
    """The regression that matters: /film/parasite/ is a 1982 creature feature.

    Trying the bare slug first returns the wrong film with no error at all,
    which is worse than failing.
    """
    asked = []

    def fake_fetch(path, kind='film', fresh=False):
        asked.append(path)
        if path != '/film/parasite-2019/':
            raise LookupError('404')
        return FILM_HTML, {'cached': False}

    monkeypatch.setattr(lb, 'fetch', fake_fetch)
    assert lb.film('parasite', 2019)['year'] == 2019
    assert asked[0] == '/film/parasite-2019/'


def test_film_without_a_year_takes_the_bare_slug(monkeypatch):
    monkeypatch.setattr(lb, 'fetch',
                        lambda path, *a, **k: (FILM_HTML, {'asked': path}))
    assert lb.film('Parasite')['source']['asked'] == '/film/parasite/'


def test_film_needs_a_title(monkeypatch):
    monkeypatch.setattr(lb, 'fetch',
                        lambda *a, **k: (_ for _ in ()).throw(LookupError('404')))
    with pytest.raises(LookupError):
        lb.film('a-film-that-is-not-there', 1899)


# ── the poster wall ──────────────────────────────────────────────

def test_parse_grid_splits_year_off_and_dedupes():
    got = lb.parse_grid(GRID_HTML)
    assert len(got) == 2                       # the repeat is dropped
    assert got[0] == {'title': 'Spider-Man: Brand New Day', 'year': 2026,
                      'slug': 'spider-man-brand-new-day',
                      'url': 'https://letterboxd.com/film/spider-man-brand-new-day/'}
    assert got[1]['title'] == "Se'lah & Sons"  # entities decoded


# ── being shut out is an answer, not an empty list ───────────────

def _http_error(code):
    def boom(*a, **k):
        raise urllib.error.HTTPError('https://letterboxd.com/x/', code, 'no', {}, None)
    return boom


def test_a_403_with_a_cold_cache_raises_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(lb, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(lb, 'MIN_INTERVAL', 0)
    monkeypatch.setattr(lb.urllib.request, 'urlopen', _http_error(403))
    with pytest.raises(lb.Blocked):
        lb.fetch('/ada/rss/', 'rss')


def test_a_403_with_a_warm_cache_answers_and_admits_it_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(lb, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(lb, 'MIN_INTERVAL', 0)
    lb._store('https://letterboxd.com/ada/rss/', RSS)
    monkeypatch.setattr(lb.urllib.request, 'urlopen', _http_error(429))
    text, meta = lb.fetch('/ada/rss/', 'rss', fresh=True)
    assert text == RSS
    assert meta['stale'] is True and meta['cached'] is True


def test_a_404_is_a_lookup_error_not_a_block(monkeypatch, tmp_path):
    monkeypatch.setattr(lb, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(lb, 'MIN_INTERVAL', 0)
    monkeypatch.setattr(lb.urllib.request, 'urlopen', _http_error(404))
    with pytest.raises(LookupError):
        lb.fetch('/nobody/rss/', 'rss')


def test_the_cache_is_used_before_the_network(monkeypatch, tmp_path):
    monkeypatch.setattr(lb, 'CACHE_DIR', str(tmp_path))
    lb._store('https://letterboxd.com/ada/rss/', RSS)
    monkeypatch.setattr(lb.urllib.request, 'urlopen', _http_error(500))
    text, meta = lb.fetch('/ada/rss/', 'rss')
    assert text == RSS and meta['cached'] is True and not meta.get('stale')


# ── the arithmetic ───────────────────────────────────────────────

def test_profile_counts_only_what_was_rated():
    entries = lb.parse_rss(RSS)['entries']
    p = taste.profile(entries)
    assert p['rated'] == 2
    assert p['mean'] == 4.25                   # (4.0 + 4.5) / 2, lists ignored
    assert p['histogram'] == {4.0: 1, 4.5: 1} or p['histogram'] == {'4.0': 1, '4.5': 1}


def test_overlap_finds_the_shared_film_and_the_split():
    a = lb.parse_rss(RSS)['entries']
    b = [dict(e) for e in a if e.get('slug') == 'parasite-2019']
    b[0]['rating'] = 2.0                       # same film, very different view
    out = taste.overlap(a, b, a='ada', b='bo')
    split = out['disagreements'][0]
    assert split['slug'] == 'parasite-2019'
    assert abs(split['gap']) == 2.5
    assert split['ada'] == 4.5 and split['bo'] == 2.0


def test_overlap_of_strangers_is_empty_not_an_error():
    a = lb.parse_rss(RSS)['entries']
    out = taste.overlap(a, [], a='ada', b='nobody')
    assert out['disagreements'] == []
