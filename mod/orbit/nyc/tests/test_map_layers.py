"""The two market/news map layers: name matching, geolocation, payloads."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nycgis import layers as L
from nycgis import news as N
from nycgis import realestate as RE


# ── for-sale: StreetEasy name → NTA polygon join ─────────────────────────

def test_norm_name_strips_parens_and_punctuation():
    assert RE.norm_name('Astoria (North)-Ditmars-Steinway') == 'astoria ditmars steinway'
    assert RE.norm_name("Bedford-Stuyvesant (West)") == 'bedford stuyvesant'
    assert RE.norm_name('SoHo & NoHo') == 'soho and noho'


SE_AREAS = [
    ('Harlem', 'harlem', 'Manhattan'),
    ('East Harlem', 'east harlem', 'Manhattan'),
    ('Murray Hill', 'murray hill', 'Manhattan'),
    ('Murray Hill', 'murray hill', 'Queens'),
    ('Astoria', 'astoria', 'Queens'),
]


def test_match_nta_prefers_the_longest_name():
    # "East Harlem (North)" contains both "Harlem" and "East Harlem" —
    # the more specific area must win.
    assert RE.match_nta('East Harlem (North)', 'Manhattan', SE_AREAS) == 'East Harlem'


def test_match_nta_is_borough_scoped():
    # Murray Hill exists in two boroughs; the join must not cross.
    assert RE.match_nta('Murray Hill', 'Queens', SE_AREAS) == 'Murray Hill'
    assert RE.match_nta('Murray Hill-Kips Bay', 'Manhattan', SE_AREAS) == 'Murray Hill'
    assert RE.match_nta('Murray Hill', 'Bronx', SE_AREAS) is None


def test_match_nta_whole_words_only():
    # "Astoria" must not match inside another word.
    assert RE.match_nta('Astorialand', 'Queens', SE_AREAS) is None
    assert RE.match_nta('Astoria (North)-Ditmars-Steinway', 'Queens', SE_AREAS) == 'Astoria'


# ── news: pinning headlines to places ────────────────────────────────────

GAZ = [
    {'name': 'east harlem', 'lng': -73.94, 'lat': 40.79,
     'place': 'East Harlem', 'precision': 'neighborhood'},
    {'name': 'harlem', 'lng': -73.945, 'lat': 40.81,
     'place': 'Harlem', 'precision': 'neighborhood'},
    {'name': 'times square', 'lng': -73.9855, 'lat': 40.758,
     'place': 'Times Square', 'precision': 'landmark'},
    {'name': 'brooklyn', 'lng': -73.9496, 'lat': 40.6501,
     'place': 'Brooklyn', 'precision': 'borough'},
]


def test_place_longest_match_wins():
    hit = N._place('Shooting in East Harlem leaves two hurt', GAZ)
    assert hit['place'] == 'East Harlem'


def test_place_neighborhood_beats_borough():
    # Entries are longest-first; a borough only sticks when nothing finer hits.
    hit = N._place('Brooklyn man arrested near Times Square', GAZ)
    assert hit['place'] == 'Times Square'
    hit = N._place('Brooklyn rents keep climbing', GAZ)
    assert hit['place'] == 'Brooklyn' and hit['precision'] == 'borough'


def test_place_names_no_place():
    assert N._place('City budget talks stall again', GAZ) is None


def test_jitter_is_deterministic_and_bounded():
    a = N._jitter('Some headline', 0.004)
    assert a == N._jitter('Some headline', 0.004)
    assert abs(a[0]) <= 0.002 and abs(a[1]) <= 0.002


def test_ring_center_picks_the_largest_ring():
    geom = {'type': 'MultiPolygon', 'coordinates': [
        [[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1], [0, 0]]],       # small
        [[[10, 10], [14, 10], [14, 14], [10, 14], [10, 10]]],     # large
    ]}
    assert N._ring_center(geom) == (12.0, 12.0)


# ── catalogue wiring (what the agent's nyc_map validates against) ────────

def test_new_layers_are_in_the_catalogue_and_loaders():
    ids = [l['id'] for l in L.LAYERS]
    assert 'forsale' in ids and 'news' in ids and 'sales' in ids
    assert 'forsale' in L.LOADERS and 'news' in L.LOADERS
    news = next(l for l in L.LAYERS if l['id'] == 'news')
    assert news['refresh_seconds'] == 900     # live layer: browser re-polls


# ── live payloads ────────────────────────────────────────────────────────

@pytest.mark.network
def test_forsale_choropleth_payload():
    fc = RE.forsale_choropleth()
    assert fc['type'] == 'FeatureCollection' and len(fc['features']) > 200
    assert len(fc['breaks']['stops']) >= 4
    assert fc['meta']['areas_with_data'] > 100
    priced = [f['properties'] for f in fc['features']
              if f['properties']['asking_price'] is not None]
    assert all(50_000 < p['asking_price'] < 20_000_000 for p in priced)
    # The price-cut share arrives as a 0–1 fraction and must leave as a percent.
    cuts = [p['price_cut_pct'] for p in priced if p['price_cut_pct'] is not None]
    assert cuts and all(0 <= c <= 100 for c in cuts)


@pytest.mark.network
def test_news_points_payload():
    fc = N.points()
    assert fc['type'] == 'FeatureCollection'
    assert fc['meta']['placed'] == len(fc['features'])
    for f in fc['features']:
        x, y = f['geometry']['coordinates']
        assert -74.4 < x < -73.5 and 40.4 < y < 41.0
        p = f['properties']
        assert p['title'] and p['url'].startswith('http')
        assert p['precision'] in ('neighborhood', 'landmark', 'borough')
