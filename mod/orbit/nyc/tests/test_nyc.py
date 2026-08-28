"""
Tests for the nyc GIS module.

Split into two groups:

* pure logic (geometry simplification, key mapping, formatting) — always runs,
  no network;
* live open-data checks, marked ``network``, which hit the real portals. They
  are what catch an upstream schema change, so they are not mocked. Skip them
  with ``pytest -m "not network"``.
"""

import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))

from nycgis import layers as L          # noqa: E402
from nycgis import prices as P          # noqa: E402
from nycgis import sources as S         # noqa: E402


# ── geometry ────────────────────────────────────────────────────────────────

def test_rdp_keeps_endpoints_and_drops_collinear():
    line = [[0, 0], [1, 0.0000001], [2, 0], [3, 0]]
    out = S.rdp(line, 0.001)
    assert out[0] == [0, 0] and out[-1] == [3, 0]
    assert len(out) == 2


def test_rdp_keeps_a_real_corner():
    line = [[0, 0], [1, 1], [2, 0]]
    assert len(S.rdp(line, 0.001)) == 3


def test_rdp_handles_degenerate_input():
    assert S.rdp([], 0.1) == []
    assert S.rdp([[0, 0]], 0.1) == [[0, 0]]
    assert S.rdp([[0, 0], [1, 1]], 0.1) == [[0, 0], [1, 1]]


def test_simplify_polygon_stays_closed():
    ring = [[0, 0], [0, 1], [0.5, 1.0000001], [1, 1], [1, 0], [0, 0]]
    geom = S.simplify_geometry({'type': 'Polygon', 'coordinates': [ring]}, tol=0.0001)
    out = geom['coordinates'][0]
    assert out[0] == out[-1], 'ring must remain closed'
    assert len(out) >= 4, 'a valid ring needs at least 4 positions'


def test_simplify_drops_subtolerance_polygon():
    tiny = [[0, 0], [0, 1e-9], [1e-9, 1e-9], [1e-9, 0], [0, 0]]
    assert S.simplify_geometry({'type': 'Polygon', 'coordinates': [tiny]}, tol=0.01) is None


def test_simplify_rounds_and_dedupes():
    line = [[1.0000001, 2.0000001], [1.0000002, 2.0000002], [3.0, 4.0]]
    geom = S.simplify_geometry({'type': 'LineString', 'coordinates': line},
                               tol=0, precision=5)
    assert geom['coordinates'] == [[1.0, 2.0], [3.0, 4.0]]


def test_simplify_geojson_trims_properties():
    fc = {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature',
        'properties': {'keep': 1, 'drop': 2},
        'geometry': {'type': 'Point', 'coordinates': [1.234567, 2.345678]},
    }]}
    out = S.simplify_geojson(fc, keep=['keep'])
    assert out['features'][0]['properties'] == {'keep': 1}
    assert out['features'][0]['geometry']['coordinates'] == [1.23457, 2.34568]


def test_points_from_rows_rejects_bad_coordinates():
    rows = [
        {'lat': '40.7', 'lng': '-74.0', 'name': 'ok'},
        {'lat': '0', 'lng': '0', 'name': 'null island'},
        {'lat': 'n/a', 'lng': '-74.0', 'name': 'garbage'},
        {'lng': '-74.0', 'name': 'missing lat'},
    ]
    fc = S.points_from_rows(rows, 'lat', 'lng', props=['name'])
    assert [f['properties']['name'] for f in fc['features']] == ['ok']


# ── price logic ─────────────────────────────────────────────────────────────

def test_community_board_to_cdta():
    assert P.community_board_to_cdta('101') == 'MN01'
    assert P.community_board_to_cdta('305') == 'BK05'
    assert P.community_board_to_cdta('518') == 'SI18'
    # parks/airports/unknown catch-all boards have no polygon to join to
    assert P.community_board_to_cdta('164') is None
    assert P.community_board_to_cdta('') is None
    assert P.community_board_to_cdta('abc') is None


def test_type_clause_matches_on_prefix():
    clause = P._type_clause('houses')
    assert 'starts_with(building_class_category,"01")' in clause
    assert 'starts_with(building_class_category,"02")' in clause
    assert P._type_clause('all') is None


def test_where_excludes_nominal_transfers():
    where = P._where('nta', '2024-01-01', None, 'all')
    assert f'sale_price > {P.MIN_SALE_PRICE}' in where
    assert 'nta IS NOT NULL' in where


def test_ppsf_clause_bounds_both_ends():
    clause = P.ppsf_clause()
    assert f'>= {P.MIN_PPSF}' in clause
    assert f'<= {P.MAX_PPSF}' in clause


def test_prior_window_is_equal_length_and_adjacent():
    # A full calendar year compares against the full year before it.
    since, until = P._prior_window('2024-01-01', '2024-12-31')
    assert (since, until) == ('2023-01-01', '2023-12-31')

    # And the prior window always ends the day before the current one starts.
    since, until = P._prior_window('2025-07-01', '2025-09-29')
    assert until == '2025-06-30'
    assert since < until


def test_merge_weights_medians_by_sale_count():
    a = {'sales': 100, 'median_price': 1000, 'median_ppsf': 10, 'total_value': 100}
    b = {'sales': 900, 'median_price': 2000, 'median_ppsf': 20, 'total_value': 900}
    out = P._merge(a, b)
    assert out['sales'] == 1000
    assert out['total_value'] == 1000
    # weighted toward b, which has 9x the volume
    assert out['median_price'] == 1900
    assert out['median_ppsf'] == 19


def test_sum_years_combines_series():
    groups = [
        [{'year': 2024, 'sales': 10, 'median_price': 100, 'median_ppsf': 5, 'total_value': 1000}],
        [{'year': 2024, 'sales': 90, 'median_price': 200, 'median_ppsf': None, 'total_value': 9000}],
    ]
    out = P._sum_years(groups)
    assert len(out) == 1
    assert out[0]['sales'] == 100
    assert out[0]['median_price'] == 190      # count-weighted
    assert out[0]['median_ppsf'] == 5         # only one side reported it


def test_roll_up_zips_folds_members_into_parent():
    boundaries = {'features': [
        {'properties': {'modzcta': '10001', 'zcta': '10001, 10118, 10119'}},
    ]}
    stats = {'10001': {'sales': 10, 'median_price': 1000, 'total_value': 10},
             '10118': {'sales': 10, 'median_price': 3000, 'total_value': 30}}
    out = P._roll_up_zips(boundaries, stats)
    assert set(out) == {'10001'}
    assert out['10001']['sales'] == 20
    assert out['10001']['median_price'] == 2000


def test_geographies_and_metrics_are_wired_to_boundaries():
    for name, geo in P.GEOGRAPHIES.items():
        assert geo['boundary'] in L.BOUNDARIES, f'{name} points at an unknown boundary'
        assert geo['column'] and geo['join'] and geo['name']


def test_every_catalog_layer_is_loadable_or_parameterised():
    parameterised = {'housing_prices', 'sales'}
    for layer in L.LAYERS:
        assert layer['id'] in L.LOADERS or layer['id'] in parameterised, \
            f'{layer["id"]} has no loader'
        assert layer['source']['name'] and layer['source']['url']
        assert layer['category'] and layer['title'] and layer['description']


def test_catalog_groups_every_layer():
    cat = L.catalog()
    grouped = [lid for c in cat['categories'] for lid in c['layers']]
    assert sorted(grouped) == sorted(l['id'] for l in L.LAYERS)
    assert cat['count'] == len(L.LAYERS)


# ── choropleth assembly (no network: boundaries are synthesised) ────────────

def test_choropleth_keeps_areas_with_no_sales(monkeypatch):
    boundaries = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature',
         'properties': {'nta2020': 'BK0101', 'ntaname': 'Greenpoint', 'boroname': 'Brooklyn'},
         'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [0, 1], [1, 1], [0, 0]]]}},
        {'type': 'Feature',
         'properties': {'nta2020': 'BK9999', 'ntaname': 'Cemetery', 'boroname': 'Brooklyn'},
         'geometry': {'type': 'Polygon', 'coordinates': [[[2, 2], [2, 3], [3, 3], [2, 2]]]}},
    ]}
    monkeypatch.setattr(P, 'with_change', lambda *a, **k: {
        'BK0101': {'sales': 500, 'median_price': 1_650_000, 'median_ppsf': 751,
                   'avg_price': 2_000_000, 'total_value': 1e9, 'price_change': 6.5,
                   'prior_median_price': 1_550_000, 'ppsf_sales': 190},
    })
    fc = P.choropleth(boundaries, geography='nta')
    assert fc['meta']['areas'] == 2
    assert fc['meta']['areas_with_data'] == 1

    by_area = {f['properties']['area']: f['properties'] for f in fc['features']}
    assert by_area['BK0101']['median_price'] == 1_650_000
    # the empty area survives with an explicit zero rather than vanishing
    assert by_area['BK9999']['sales'] == 0
    assert by_area['BK9999']['median_price'] is None
    assert by_area['BK9999']['name'] == 'Cemetery'


# ── cache ───────────────────────────────────────────────────────────────────

def test_cached_falls_back_to_stale_on_upstream_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(S, 'CACHE_DIR', tmp_path)
    key = 'test-stale'
    assert S.cached(key, 3600, lambda: {'v': 1}) == {'v': 1}

    def boom():
        raise RuntimeError('upstream down')

    # ttl=-1 forces a refresh; the refresh fails, so the stale value stands.
    assert S.cached(key, -1, boom) == {'v': 1}


def test_cached_raises_when_there_is_nothing_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(S, 'CACHE_DIR', tmp_path)

    def boom():
        raise RuntimeError('upstream down')

    with pytest.raises(RuntimeError):
        S.cached('test-cold', 3600, boom)


def test_cache_clear_scopes_by_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(S, 'CACHE_DIR', tmp_path)
    S.cached('geo-a', 3600, lambda: 1)
    S.cached('geo-b', 3600, lambda: 2)
    S.cached('other', 3600, lambda: 3)
    assert S.cache_clear('geo-')['cleared'] == 2
    assert S.cache_read('other', None) == 3


# ── module surface ──────────────────────────────────────────────────────────

def test_borough_lookup_accepts_aliases():
    import mod as m
    nyc = m.mod('nyc')()
    assert nyc.borough('bk')['name'] == 'Brooklyn'
    assert nyc.borough('Staten Island')['name'] == 'Staten Island'
    assert nyc.borough('The Bronx')['name'] == 'The Bronx'
    assert nyc.borough('Richmond')['name'] == 'Staten Island'
    assert 'error' in nyc.borough('Hoboken')


def test_housing_rejects_unknown_parameters():
    import mod as m
    nyc = m.mod('nyc')()
    assert 'error' in nyc.housing(geography='nowhere')
    assert 'error' in nyc.housing(metric='vibes')


def test_breaks_are_quantiles_and_monotonic():
    import mod as m
    nyc = m.mod('nyc')()
    fc = {'features': [{'properties': {'median_price': v}}
                       for v in [100, 200, 300, 400, 500, 600, 700, 8000]]}
    b = nyc._breaks(fc, 'median_price')
    assert b['stops'] == sorted(b['stops'])
    assert b['min'] == 100 and b['max'] == 8000
    # quantiles, so the lone 8000 outlier does not become a class boundary
    assert b['stops'][-1] < 8000


def test_price_change_breaks_are_symmetric_and_clipped():
    import mod as m
    nyc = m.mod('nyc')()
    vals = [-79.0] + [0.0] * 50 + [5.0] * 50 + [61.6]
    fc = {'features': [{'properties': {'price_change': v}} for v in vals]}
    b = nyc._breaks(fc, 'price_change')
    assert b['diverging'] is True
    assert b['min'] == -b['max'], 'a diverging scale must be symmetric about zero'
    assert b['true_min'] == -79.0 and b['true_max'] == 61.6
    assert abs(b['max']) < 79.0, 'the extreme outlier must not set the domain'


# ── live open data ──────────────────────────────────────────────────────────

@pytest.mark.network
def test_rolling_sales_schema_is_unchanged():
    """The columns the price engine depends on still exist and still parse."""
    rows = S.soql(P.DOMAIN, P.DATASET, limit=1)
    assert rows, 'rolling sales returned no rows'
    for col in ('nta', 'zip_code', 'borough', 'community_board', 'sale_price',
                'sale_date', 'gross_square_feet', 'building_class_category',
                'latitude', 'longitude'):
        assert col in rows[0], f'rolling sales lost column {col!r}'


@pytest.mark.network
def test_sqft_still_needs_the_comma_workaround():
    """If DOF ever stores gross_square_feet as a number, SQFT_EXPR can go."""
    rows = S.soql(P.DOMAIN, P.DATASET,
                  select='gross_square_feet',
                  where='gross_square_feet IS NOT NULL', limit=200)
    values = [r['gross_square_feet'] for r in rows]
    assert any(isinstance(v, str) for v in values)


@pytest.mark.network
def test_aggregate_returns_plausible_prices():
    stats = P.aggregate('nta', since='2024-01-01', property_type='residential')
    assert len(stats) > 150, 'expected most of the 262 NTAs to report sales'
    priced = [s['median_price'] for s in stats.values() if s['median_price']]
    assert min(priced) > 50_000
    assert max(priced) < 50_000_000
    ppsf = [s['median_ppsf'] for s in stats.values() if s['median_ppsf']]
    # the whole-building square-footage bug produced $4/ft² medians
    assert min(ppsf) >= P.MIN_PPSF, f'implausible $/ft² leaked through: {min(ppsf)}'
    assert max(ppsf) <= P.MAX_PPSF


@pytest.mark.network
def test_choropleth_joins_to_real_boundaries():
    for geography in P.GEOGRAPHIES:
        fc = L.boundary(P.GEOGRAPHIES[geography]['boundary'])
        out = P.choropleth(fc, geography=geography, since='2024-01-01')
        assert out['meta']['areas_with_data'] > 0, \
            f'{geography} joined to nothing — check the key mapping'


@pytest.mark.network
@pytest.mark.parametrize('layer_id', sorted(L.LOADERS))
def test_every_layer_loads_valid_geojson(layer_id):
    fc = L.get(layer_id)
    assert fc['type'] == 'FeatureCollection'
    assert fc['features'], f'{layer_id} returned no features'
    for f in fc['features'][:50]:
        assert f['geometry'] and f['geometry'].get('coordinates')


@pytest.mark.network
def test_subway_lines_carry_official_colors():
    fc = L.subway_lines()
    colors = {f['properties']['color'] for f in fc['features']}
    assert len(colors) > 5, 'expected many distinct MTA route colours'
    assert all(c.startswith('#') and len(c) == 7 for c in colors)


@pytest.mark.network
def test_trend_all_covers_many_areas_in_one_query():
    series = P.trend_all('nta', 'residential')
    assert len(series) > 150
    sample = next(iter(series.values()))
    years = [row['year'] for row in sample]
    assert years == sorted(years)
