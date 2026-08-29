"""
Tests for the tdot GIS module.

Split into two groups:

* pure logic (geometry simplification, month arithmetic, aggregation) — always
  runs, no network;
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

from tdotgis import crime as C          # noqa: E402
from tdotgis import layers as L         # noqa: E402
from tdotgis import sources as S        # noqa: E402


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
        {'lat': '43.65', 'lng': '-79.38', 'name': 'ok'},
        {'lat': '0', 'lng': '0', 'name': 'null island'},
        {'lat': 'n/a', 'lng': '-79.38', 'name': 'garbage'},
        {'lng': '-79.38', 'name': 'missing lat'},
    ]
    fc = S.points_from_rows(rows, 'lat', 'lng', props=['name'])
    assert [f['properties']['name'] for f in fc['features']] == ['ok']


def test_geometry_km2_is_in_the_right_order_of_magnitude():
    # ~0.1° of longitude at Toronto's latitude is ~8 km; 0.05° of latitude ~5.6 km.
    box = [[[-79.40, 43.65], [-79.30, 43.65], [-79.30, 43.70], [-79.40, 43.70],
            [-79.40, 43.65]]]
    km2 = S.geometry_km2({'type': 'Polygon', 'coordinates': box})
    assert 35 < km2 < 55, km2


# ── month arithmetic ────────────────────────────────────────────────────────

def test_months_between_is_inclusive():
    assert C._months_between('2025-01-01', '2025-01-31') == 1
    assert C._months_between('2025-01-01', '2025-12-31') == 12
    assert C._months_between('2024-11-15', '2025-02-02') == 4


def test_prior_window_is_equal_length_and_adjacent():
    # A full calendar year compares against the full year before it.
    since, until = C._prior_window('2024-01-01', '2024-12-31')
    assert since == '2023-01-01'
    assert until.startswith('2023-12')

    # And the prior window always ends in the month before the current one.
    since, until = C._prior_window('2025-07-01', '2025-09-30')
    assert until.startswith('2025-06')
    assert since == '2025-04-01'


def test_prior_window_crosses_the_year_boundary():
    since, until = C._prior_window('2025-02-01', '2025-03-31')
    assert since == '2024-12-01'
    assert until.startswith('2025-01')


def test_clean_hood_name_strips_the_code_suffix():
    assert C._clean_hood_name('West Hill (136)') == 'West Hill'
    assert C._clean_hood_name('Danforth') == 'Danforth'
    assert C._clean_hood_name('') == ''


# ── aggregation (no network: the cube is synthesised) ───────────────────────

def _cube(monkeypatch, cube158):
    """Point the warehouse at a hand-built cube."""
    monkeypatch.setattr(C, 'warehouse', lambda *a, **k: {
        'cube158': cube158, 'cube140': {}, 'points': [],
        'total': 1, 'first_month': '2014-01', 'last_month': '2026-06',
    })


def test_aggregate_respects_the_category_filter_but_keeps_the_breakdown(monkeypatch):
    _cube(monkeypatch, {'001': {
        'assault': {'2025-01': 10, '2025-02': 5},
        'robbery': {'2025-01': 3},
    }})
    every = C.aggregate(since='2025-01-01')
    assert every['001']['incidents'] == 18

    only = C.aggregate(since='2025-01-01', category='assault')
    assert only['001']['incidents'] == 15
    # the mix is always the full breakdown, whatever the map is filtered to
    assert only['001']['robbery'] == 3


def test_aggregate_windows_by_month(monkeypatch):
    _cube(monkeypatch, {'001': {'assault': {'2024-12': 100, '2025-01': 7}}})
    assert C.aggregate(since='2025-01-01')['001']['incidents'] == 7
    assert C.aggregate(since='2024-01-01', until='2024-12-31')['001']['incidents'] == 100


def test_change_is_null_below_the_sample_floor(monkeypatch):
    _cube(monkeypatch, {
        'thin': {'assault': {'2024-01': 2, '2025-01': 20}},
        'thick': {'assault': {'2024-01': 100, '2025-01': 150}},
    })
    out = C.with_change(since='2025-01-01', until='2025-12-31')
    # 2 prior incidents is noise, not a 900% surge
    assert out['thin']['prior_incidents'] == 2
    assert out['thin']['change'] is None
    assert out['thick']['change'] == 50.0


def test_choropleth_keeps_areas_with_no_incidents(monkeypatch):
    _cube(monkeypatch, {'001': {'assault': {'2025-03': 40}}})
    boundaries = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature',
         'properties': {'AREA_LONG_CODE': '001', 'AREA_NAME': 'West Hill (136)'},
         'geometry': {'type': 'Polygon', 'coordinates': [
             [[-79.40, 43.65], [-79.30, 43.65], [-79.30, 43.70], [-79.40, 43.65]]]}},
        {'type': 'Feature',
         'properties': {'AREA_LONG_CODE': '999', 'AREA_NAME': 'Quiet Corner'},
         'geometry': {'type': 'Polygon', 'coordinates': [
             [[-79.20, 43.75], [-79.10, 43.75], [-79.10, 43.80], [-79.20, 43.75]]]}},
    ]}
    fc = C.choropleth(boundaries, since='2025-01-01', until='2025-12-31')
    assert fc['meta']['areas'] == 2
    assert fc['meta']['areas_with_data'] == 1

    by_area = {f['properties']['area']: f['properties'] for f in fc['features']}
    assert by_area['001']['incidents'] == 40
    assert by_area['001']['name'] == 'West Hill', 'the code suffix must be stripped'
    # zero is a real (and good) value on a crime map, not missing data
    assert by_area['999']['incidents'] == 0
    assert by_area['999']['per_km2'] == 0


def test_choropleth_zero_pads_numeric_area_codes(monkeypatch):
    _cube(monkeypatch, {'007': {'robbery': {'2025-05': 12}}})
    boundaries = {'features': [{
        'properties': {'AREA_LONG_CODE': '7', 'AREA_NAME': 'Seven'},
        'geometry': {'type': 'Polygon', 'coordinates': [
            [[-79.4, 43.6], [-79.3, 43.6], [-79.3, 43.7], [-79.4, 43.6]]]},
    }]}
    fc = C.choropleth(boundaries, since='2025-01-01')
    assert fc['features'][0]['properties']['incidents'] == 12


def test_trend_marks_a_partial_final_year(monkeypatch):
    _cube(monkeypatch, {'001': {'assault': {'2024-06': 5, '2026-01': 2}}})
    t = C.trend(area='1')
    assert t['area'] == '001'
    assert [row['year'] for row in t['series']] == [2024, 2026]
    # the warehouse's last month is 2026-06, so 2026 is still in progress
    assert t['partial_year'] == 2026


def test_trend_reports_no_partial_year_after_december(monkeypatch):
    monkeypatch.setattr(C, 'warehouse', lambda *a, **k: {
        'cube158': {'001': {'assault': {'2025-12': 4}}}, 'cube140': {},
        'points': [], 'total': 1, 'first_month': '2014-01', 'last_month': '2025-12',
    })
    assert C.trend(area='001')['partial_year'] is None


def test_incident_points_filter_by_window_and_category(monkeypatch):
    monkeypatch.setattr(C, 'warehouse', lambda *a, **k: {
        'cube158': {}, 'cube140': {}, 'total': 1,
        'first_month': '2014-01', 'last_month': '2026-06',
        'points': [
            ['2026-05-02', 'assault', 'Assault', 'Apartment', 'Danforth', -79.3, 43.6],
            ['2026-05-01', 'robbery', 'Robbery', 'Outside', 'Danforth', -79.3, 43.6],
            ['2024-01-01', 'assault', 'Assault', 'Outside', 'Danforth', -79.3, 43.6],
        ],
    })
    fc = C.incident_points(since='2026-01-01', category='assault')
    assert len(fc['features']) == 1
    p = fc['features'][0]['properties']
    assert p['date'] == '2026-05-02' and p['category'] == 'Assault'
    assert fc['features'][0]['geometry']['coordinates'] == [-79.3, 43.6]


# ── catalogue wiring ────────────────────────────────────────────────────────

def test_geographies_are_wired_to_boundaries():
    for name, geo in C.GEOGRAPHIES.items():
        assert geo['boundary'] in L.BOUNDARIES, f'{name} points at an unknown boundary'
        assert geo['cube'] and geo['join'] and geo['name']


def test_categories_map_back_to_source_labels():
    for key, cat in C.CATEGORIES.items():
        assert cat['label']
        if key != 'all':
            assert cat['match'], f'{key} has no CSI_CATEGORY to match'
            assert C._MATCH_TO_KEY[cat['match']] == key


def test_every_catalog_layer_is_loadable_or_parameterised():
    parameterised = {'crime', 'incidents'}
    for layer in L.LAYERS:
        assert layer['id'] in L.LOADERS or layer['id'] in parameterised, \
            f'{layer["id"]} has no loader'
        assert layer['source']['name'] and layer['source']['url']
        assert layer['category'] and layer['title'] and layer['description']


def test_catalog_groups_every_layer():
    # The catalogue is the hand-written layers *plus* every spec-driven one and
    # anything in the market — a dataset added from the browser is a first-class
    # layer, so it has to appear in a category like the rest.
    cat = L.catalog()
    grouped = [lid for c in cat['categories'] for lid in c['layers']]
    assert sorted(grouped) == sorted(l['id'] for l in L.all_layers())
    assert cat['count'] == len(grouped)
    assert {l['id'] for l in L.LAYERS} <= set(grouped)
    assert set(L.SPECS_BY_ID) <= set(grouped)


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

def test_district_lookup_accepts_aliases():
    import mod as m
    tdot = m.mod('tdot')()
    assert tdot.district('north_york')['name'] == 'North York'
    assert tdot.district('Old Toronto')['name'] == 'Old Toronto'
    assert tdot.district('downtown')['name'] == 'Old Toronto'
    assert tdot.district('SC')['name'] == 'Scarborough'
    assert 'error' in tdot.district('Mississauga')


def test_crime_rejects_unknown_parameters():
    import mod as m
    tdot = m.mod('tdot')()
    assert 'error' in tdot.crime(geography='nowhere')
    assert 'error' in tdot.crime(metric='vibes')
    assert 'error' in tdot.crime(category='jaywalking')


def test_breaks_are_quantiles_and_monotonic():
    import mod as m
    tdot = m.mod('tdot')()
    fc = {'features': [{'properties': {'incidents': v}}
                       for v in [10, 20, 30, 40, 50, 60, 70, 8000]]}
    b = tdot._breaks(fc, 'incidents')
    assert b['stops'] == sorted(b['stops'])
    assert b['min'] == 10 and b['max'] == 8000
    # quantiles, so the lone 8000 outlier does not become a class boundary
    assert b['stops'][-1] < 8000


def test_change_breaks_are_symmetric_and_clipped():
    import mod as m
    tdot = m.mod('tdot')()
    vals = [-79.0] + [0.0] * 50 + [5.0] * 50 + [61.6]
    fc = {'features': [{'properties': {'change': v}} for v in vals]}
    b = tdot._breaks(fc, 'change')
    assert b['diverging'] is True
    assert b['min'] == -b['max'], 'a diverging scale must be symmetric about zero'
    assert b['true_min'] == -79.0 and b['true_max'] == 61.6
    assert abs(b['max']) < 79.0, 'the extreme outlier must not set the domain'


def test_breaks_on_an_empty_field_do_not_crash():
    import mod as m
    tdot = m.mod('tdot')()
    b = tdot._breaks({'features': [{'properties': {'incidents': None}}]}, 'incidents')
    assert b['stops'] == [] and b['min'] is None


# ── live open data ──────────────────────────────────────────────────────────

@pytest.mark.network
def test_csi_dump_still_has_the_columns_the_engine_reads():
    rows = []
    for row in S.ckan_dump_rows(C.RESOURCE, C.FIELDS):
        rows.append(row)
        if len(rows) >= 5:
            break
    assert rows, 'the CSI dump returned no rows'
    for col in C.FIELDS:
        assert col in rows[0], f'CSI dump lost column {col!r}'


@pytest.mark.network
def test_csi_categories_still_match_the_source_labels():
    """A renamed CSI_CATEGORY would silently empty the map."""
    seen = set()
    for i, row in enumerate(S.ckan_dump_rows(C.RESOURCE, C.FIELDS)):
        seen.add((row.get('CSI_CATEGORY') or '').strip())
        if i > 20000:
            break
    unknown = seen - set(C._MATCH_TO_KEY) - {''}
    assert not unknown, f'unmapped CSI categories: {unknown}'
    assert len(seen & set(C._MATCH_TO_KEY)) == len(C._MATCH_TO_KEY)


@pytest.mark.network
def test_warehouse_covers_the_whole_record():
    cov = C.coverage()
    assert cov['total'] > 300_000, 'expected the full CSI record'
    assert cov['first_month'] == C.FIRST_MONTH
    assert cov['last_month'] > '2024-01'
    assert cov['points'] > 10_000


@pytest.mark.network
def test_choropleth_joins_to_real_boundaries():
    for geography, geo in C.GEOGRAPHIES.items():
        fc = L.boundary(geo['boundary'])
        out = C.choropleth(fc, geography=geography, since='2024-01-01')
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
def test_subway_lines_carry_official_ttc_colors():
    fc = L.ttc_lines()
    colors = {f['properties']['color'] for f in fc['features']}
    assert len(colors) >= 4, 'expected a colour per rapid-transit line'
    assert all(c.startswith('#') and len(c) == 7 for c in colors)


@pytest.mark.network
def test_trend_runs_from_the_first_year_to_the_last():
    t = C.trend()
    years = [row['year'] for row in t['series']]
    assert years == sorted(years)
    assert years[0] == C.FIRST_YEAR
    assert all(row['incidents'] > 0 for row in t['series'])


# ── point-in-polygon & area location ────────────────────────────────────────

SQUARE = {'type': 'Polygon',
          'coordinates': [[[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]]}

DONUT = {'type': 'Polygon',
         'coordinates': [[[0, 0], [0, 4], [4, 4], [4, 0], [0, 0]],
                         [[1, 1], [1, 3], [3, 3], [3, 1], [1, 1]]]}


def test_point_in_geometry_handles_inside_outside_and_holes():
    assert S.point_in_geometry(1, 1, SQUARE)
    assert not S.point_in_geometry(3, 1, SQUARE)
    assert S.point_in_geometry(0.5, 2, DONUT)
    assert not S.point_in_geometry(2, 2, DONUT), 'a hole is not inside'


def test_point_in_geometry_is_safe_on_junk():
    assert not S.point_in_geometry(1, 1, None)
    assert not S.point_in_geometry(1, 1, {'type': 'Point', 'coordinates': [1, 1]})


def test_area_locator_strips_leading_zeros_and_misses_cleanly():
    feats = [{'properties': {'AREA_LONG_CODE': '007'}, 'geometry': SQUARE},
             {'properties': {'AREA_LONG_CODE': '12'},
              'geometry': {'type': 'Polygon',
                           'coordinates': [[[10, 10], [10, 12], [12, 12], [12, 10], [10, 10]]]}}]
    at = S.area_locator(feats, 'AREA_LONG_CODE')
    assert at(1, 1) == '7'
    assert at(11, 11) == '12'
    assert at(50, 50) is None


# ── the score model ─────────────────────────────────────────────────────────

def test_registration_columns_are_read_the_way_the_city_writes_them():
    from tdotgis import score as SC
    assert SC._yn('YES') == 1.0 and SC._yn('no') == 0.0
    # Blank is missing, not "no" — the model is allowed to use the difference.
    assert SC._yn('') is None and SC._yn(None) is None
    assert SC._num('1,250') == 1250.0
    assert SC._num('n/a') is None


def test_nearby_counting_is_tight_enough_to_mean_the_building():
    from tdotgis import score as SC
    here = (-79.38, 43.65)
    grid = SC._grid([here, (-79.38 + SC.NEAR_DEGREES * 4, 43.65)])
    assert SC._count_near(grid, *here) == 1, 'a point 4 cells away is not this building'


def test_encode_folds_a_long_categorical_tail_under_the_estimator_cap():
    from tdotgis import score as SC
    pytest.importorskip('numpy')
    rows = [{'features': {'cat:manager': f'company {i}'}} for i in range(400)]
    # Every level is equally rare, so the tail bucket is what keeps this legal.
    _, levels = SC._encode(rows, ['cat:manager'])
    assert len(levels['cat:manager']) <= SC.MAX_LEVELS
    assert SC.OTHER_LEVEL in levels['cat:manager']


def test_encode_maps_unseen_categories_onto_the_tail_bucket():
    from tdotgis import score as SC
    import numpy as np
    train = [{'features': {'cat:x': 'a'}}] * 3 + [{'features': {'cat:x': 'b'}}]
    X, levels = SC._encode(train, ['cat:x'])
    assert not np.isnan(X[0, 0])
    # A manager the model never trained on must still encode to something.
    fresh, _ = SC._encode([{'features': {'cat:x': 'never seen'}}], ['cat:x'], levels)
    assert np.isnan(fresh[0, 0]) or fresh[0, 0] == levels['cat:x'].get(SC.OTHER_LEVEL)


def test_the_target_is_never_fed_back_in_as_a_feature():
    from tdotgis import score as SC
    # The evaluation's per-area sub-scores sum to SCORE, so a model given them
    # would look perfect and know nothing. Every feature has to be read from the
    # *registration* table; the evaluation is only ever the target.
    #
    # This checks source columns, not feature names: `elevators` is a legitimate
    # feature (NO_OF_ELEVATORS on the registration) that happens to share a name
    # with the ELEVATORS sub-score.
    leaks = {'SCORE', 'RESULTS_OF_SCORE', 'NO_OF_AREAS_EVALUATED', 'ENTRANCE_LOBBY',
             'STAIRWELLS', 'ELEVATORS', 'GRAFFITI', 'EXTERIOR_CLADDING',
             'BALCONY_GUARDS', 'INTERIOR_LIGHTING_LEVELS', 'PARKING_AREA'}
    sources = set(SC.COUNTS.values()) | set(SC.FLAGS.values()) | set(SC.CATEGORIES.values())
    assert not (sources & leaks), f'target leaked in via {sources & leaks}'


# ── the housing inventory ───────────────────────────────────────────────────

def test_housing_catalogue_is_well_formed():
    from tdotgis import housing as H
    for item in H.CATALOGUE:
        assert item['role'] in H.ROLES, item['title']
        assert item['title'] and item['what']
        # Anything not drawn owes the reader a reason.
        if item['role'] in ('table', 'closed'):
            assert item.get('note'), f'{item["title"]} is not mapped and says no why'
        if item['role'] == 'layer':
            assert item.get('layer'), f'{item["title"]} claims to be a layer with no id'


def test_every_housing_layer_role_points_at_a_real_layer():
    from tdotgis import housing as H
    known = {l['id'] for l in L.LAYERS} | set(L.SPECS_BY_ID)
    for item in H.CATALOGUE:
        if item['role'] in ('layer', 'feature') and item.get('layer'):
            assert item['layer'] in known, f'{item["title"]} → unknown layer {item["layer"]}'


def test_inventory_filters_by_role_and_rejects_a_bad_one():
    from tdotgis import housing as H
    closed = H.inventory(role='closed', live=False)
    assert closed['count'] and all(d['role'] == 'closed' for d in closed['datasets'])
    with pytest.raises(KeyError):
        H.inventory(role='mapped', live=False)


# ── live checks for the housing datasets ────────────────────────────────────

@pytest.mark.network
@pytest.mark.parametrize('layer_id', ['community_housing', 'subsidized_housing',
                                      'health_hazards', 'fire_violations',
                                      'retirement_homes', 'rental_demolitions'])
def test_new_housing_layers_place_their_features(layer_id):
    fc = L.get(layer_id)
    assert fc['features'], f'{layer_id} placed nothing'
    for f in fc['features'][:50]:
        assert f['geometry'] and f['geometry'].get('coordinates')


@pytest.mark.network
def test_ckan_datastore_picks_the_queryable_copy_not_the_csv():
    # This package publishes the same table four times; only one answers
    # datastore_search, and it is not the first by name.
    res = S.ckan_datastore('residential-health-hazards', 'Residential Health Inspections')
    assert res['datastore_active']
    assert S.ckan_records(res['id'], max_rows=1)


@pytest.mark.network
def test_every_catalogued_housing_dataset_still_exists():
    from tdotgis import housing as H
    inv = H.inventory(live=True)
    assert not inv['unavailable'], f'moved or withdrawn: {inv["unavailable"]}'


@pytest.mark.network
def test_the_model_beats_guessing_the_average():
    pytest.importorskip('sklearn')
    from tdotgis import score as SC
    r = SC.report()
    model, baseline = r['accuracy']['model'], r['accuracy']['baseline']
    assert model['mae'] < baseline['mae'], 'the model is no better than the mean'
    assert model['r2'] > 0.15, f'R² collapsed to {model["r2"]} — check the joins'
    assert r['target']['buildings_scored'] > 2000


@pytest.mark.network
def test_predictions_use_out_of_fold_values_for_evaluated_buildings():
    pytest.importorskip('sklearn')
    from tdotgis import score as SC
    fc = SC.predictions()
    scored = [f['properties'] for f in fc['features']
              if f['properties']['score'] is not None]
    assert scored
    # In-sample predictions would sit almost on top of the actual scores and
    # the residual spread would collapse; out-of-fold ones do not.
    spread = max(p['residual'] for p in scored) - min(p['residual'] for p in scored)
    assert spread > 20, f'residuals span only {spread} — predictions look in-sample'
    for p in scored:
        assert abs((p['score'] - p['predicted']) - p['residual']) < 0.11


@pytest.mark.network
def test_a_building_explanation_agrees_with_the_map():
    pytest.importorskip('sklearn')
    from tdotgis import score as SC
    worst = SC.outliers(limit=1)['buildings'][0]
    one = SC.building(worst['rsn'])
    # The explain view must not quietly switch to the in-sample model.
    assert abs(one['predicted'] - worst['predicted']) < 0.11
    assert one['drivers'] and all('label' in d for d in one['drivers'])


# ── mcp ─────────────────────────────────────────────────────────────────────
#
# The stdio and HTTP transports both dispatch through mcp_server.rpc, so these
# test the protocol once and the HTTP envelope separately.

def test_mcp_initialize_advertises_tools_and_echoes_protocol():
    from tdotgis import mcp_server as MCP
    r = MCP.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                 'params': {'protocolVersion': '2024-11-05'}})
    assert r['result']['serverInfo']['name'] == 'tdot'
    assert 'tools' in r['result']['capabilities']
    # A server that ignores the client's version breaks older clients.
    assert r['result']['protocolVersion'] == '2024-11-05'


def test_mcp_lists_every_tool_with_a_schema():
    from tdotgis import mcp_server as MCP
    from tdotgis import tools as T
    tools = MCP.rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert len(tools) == len(T.TOOLS)
    for t in tools:
        assert t['name'] and t['description']
        assert t['inputSchema']['type'] == 'object'


def test_mcp_notification_gets_no_reply():
    from tdotgis import mcp_server as MCP
    # A notification has no id; replying to one corrupts the stream.
    assert MCP.rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_mcp_unknown_method_is_a_jsonrpc_error():
    from tdotgis import mcp_server as MCP
    r = MCP.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'resources/list'})
    assert r['error']['code'] == -32601


def test_mcp_tool_failure_is_content_not_transport_error():
    from tdotgis import mcp_server as MCP
    r = MCP.rpc({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                 'params': {'name': 'tdot_layer_summary', 'arguments': {}}})
    # The model has to be able to read why it failed and try again, so a tool
    # that raised comes back as isError content rather than a JSON-RPC error.
    assert 'error' not in r
    assert r['result']['isError'] is True
    assert r['result']['content'][0]['text']


def test_map_driving_tools_are_flagged_and_carry_an_action():
    from tdotgis import tools as T
    drivers = [t for t in T.TOOLS if t.drives_map]
    assert {'tdot_show_layers', 'tdot_fly_to'} <= {t.name for t in drivers}
    # The console applies result['__map__']; a tool flagged as driving the map
    # that returns no action would silently do nothing.
    out = T.call_tool('tdot_show_layers', {'layers': ['crime']})
    assert out['__map__']['show'] == ['crime']


def test_mod_publishes_a_config_for_both_transports():
    import mod as m
    cfg = m.mod('tdot')().mcp()
    assert cfg['tools'] > 0
    servers = cfg['mcpServers']
    assert servers['tdot']['args'] == ['-m', 'tdotgis.mcp_server']
    assert servers['tdot-http']['url'].endswith('/mcp')
