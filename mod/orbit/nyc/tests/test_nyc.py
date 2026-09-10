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
from nycgis import traffic as TR        # noqa: E402


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


# ─────────────────────────────────────────────────────────────────── MCP
#
# The protocol surface, exercised through the same entry point both transports
# use. These are offline: they check the shape of the JSON-RPC contract, not
# what the city published today.

from nycgis import mcp_server as MCP    # noqa: E402
from nycgis import tools as T           # noqa: E402


def _rpc(method, params=None, id_=1):
    msg = {'jsonrpc': '2.0', 'id': id_, 'method': method}
    if params is not None:
        msg['params'] = params
    return MCP.handle_message(msg)


def test_initialize_declares_what_it_actually_serves():
    r = _rpc('initialize', {'protocolVersion': MCP.PROTOCOL_VERSION})['result']
    assert r['protocolVersion'] == MCP.PROTOCOL_VERSION
    assert r['serverInfo']['name'] == 'nyc'
    # Every declared capability must answer its list method, or a client will
    # advertise a surface to the model that then 404s on use.
    for cap, method in (('tools', 'tools/list'), ('prompts', 'prompts/list'),
                        ('resources', 'resources/list')):
        assert cap in r['capabilities']
        assert 'error' not in _rpc(method)


def test_protocol_version_is_negotiated_not_echoed():
    # An older version we speak is honoured...
    assert MCP.negotiate('2024-11-05') == '2024-11-05'
    # ...but a version we do not speak must not be parroted back, or the client
    # is told we agreed to something we cannot do.
    assert MCP.negotiate('1999-01-01') == MCP.PROTOCOL_VERSION
    assert MCP.negotiate(None) == MCP.PROTOCOL_VERSION


def test_notifications_are_never_answered():
    assert MCP.handle_message({'jsonrpc': '2.0',
                               'method': 'notifications/initialized'}) is None


def test_unknown_method_is_a_jsonrpc_error():
    assert _rpc('does/not/exist')['error']['code'] == -32601


def test_every_tool_is_listed_with_a_schema_and_annotations():
    listed = _rpc('tools/list')['result']['tools']
    assert len(listed) == len(T.TOOLS)
    for t in listed:
        assert t['name'] and t['title'] and t['description']
        assert t['inputSchema']['type'] == 'object'
        # Nothing here writes; a client that trusts the hint must not be lied to.
        assert t['annotations']['readOnlyHint'] is True


def test_tool_failure_is_a_result_not_a_transport_error():
    # A model has to be able to see and correct its own bad call, so a failing
    # tool comes back as isError content rather than a JSON-RPC error.
    r = _rpc('tools/call', {'name': 'nyc_no_such_tool', 'arguments': {}})
    assert 'error' not in r
    assert r['result']['isError'] is True
    assert 'nyc_no_such_tool' in r['result']['content'][0]['text']


def test_bad_argument_is_reported_back_to_the_caller():
    r = _rpc('tools/call', {'name': 'nyc_borough', 'arguments': {'bogus': 1}})
    assert r['result']['isError'] is True
    assert 'bogus' in r['result']['content'][0]['text']


def test_prompts_render_with_their_arguments_substituted():
    names = {p['name'] for p in _rpc('prompts/list')['result']['prompts']}
    assert 'neighborhood_report' in names
    r = _rpc('prompts/get', {'name': 'neighborhood_report',
                             'arguments': {'area': 'Bed-Stuy'}})['result']
    text = r['messages'][0]['content']['text']
    assert 'Bed-Stuy' in text and '{area}' not in text


def test_prompt_missing_a_required_argument_is_rejected():
    assert _rpc('prompts/get', {'name': 'neighborhood_report',
                                'arguments': {}})['error']['code'] == -32602


def test_caveats_resource_reads_without_touching_the_network():
    r = _rpc('resources/read', {'uri': 'nyc://atlas/caveats'})['result']
    body = r['contents'][0]['text']
    assert r['contents'][0]['mimeType'] == 'text/markdown'
    # The three exclusions are the whole point of the document.
    assert '$50,000' in body and '$50–$5,000' in body


def test_unknown_resource_is_an_error_not_an_empty_read():
    assert _rpc('resources/read',
                {'uri': 'nyc://atlas/nope'})['error']['code'] == -32602


def test_advertised_resources_all_resolve():
    for res in _rpc('resources/list')['result']['resources']:
        assert 'uri' in res and res['mimeType']


@pytest.mark.network
def test_tool_call_returns_structured_content_alongside_text():
    r = _rpc('tools/call', {'name': 'nyc_borough',
                            'arguments': {'name': 'queens'}})['result']
    assert r['isError'] is False
    assert r['structuredContent']['name']
    assert r['content'][0]['type'] == 'text'


# ── traffic: projection ─────────────────────────────────────────────────────
#
# DOT publishes count locations in EPSG:2263 (state-plane feet), so the
# unprojection is load-bearing: get it wrong and every count location lands in
# the wrong borough while still looking like a plausible map. These fixtures
# are real rows from the file, checked against where the street actually is.

def test_state_plane_unprojects_to_the_right_place():
    cases = [
        # (wkt from the file, expected lng, lat, where it is)
        ('POINT (1035363.4 185093.4)', -73.8157, 40.6746),   # 122 Pl / Sutter Av
        ('POINT (988000 214000)', -73.9865, 40.7541),        # Times Square
        ('POINT (1000124.7052589084 208538.8773739439)',     # Borden Av Bridge
         -73.9427, 40.7390),
    ]
    for wkt, lng, lat in cases:
        got = S.wkt_point_to_lnglat(wkt)
        assert got is not None, wkt
        # ~10 m of tolerance: far tighter than the counts are located to, but
        # tight enough that a wrong parallel or a metres/feet mix-up fails.
        assert abs(got[0] - lng) < 0.0002, (wkt, got)
        assert abs(got[1] - lat) < 0.0002, (wkt, got)


def test_state_plane_rejects_points_outside_the_city():
    # A row projected from something other than state-plane feet lands in the
    # ocean; it must be dropped rather than drawn at a plausible-looking spot.
    assert S.wkt_point_to_lnglat('POINT (0 0)') is None
    assert S.wkt_point_to_lnglat('POINT (5000000 5000000)') is None
    assert S.wkt_point_to_lnglat('not wkt at all') is None
    assert S.wkt_point_to_lnglat('') is None


# ── traffic: speed feed ─────────────────────────────────────────────────────

def test_speed_snapshot_keeps_newest_per_link_and_drops_dark_sensors():
    line = '40.7100,-73.9900 40.7200,-73.9800'
    rows = [
        # same link twice — only the newer reading may survive
        {'link_id': '1', 'speed': '45.0', 'travel_time': '60', 'status': '0',
         'data_as_of': '2026-08-27T14:00:00.000', 'link_points': line,
         'borough': 'Queens', 'link_name': 'LIE EB', 'owner': 'DOT'},
        {'link_id': '1', 'speed': '5.0', 'travel_time': '600', 'status': '0',
         'data_as_of': '2026-08-27T13:00:00.000', 'link_points': line,
         'borough': 'Queens', 'link_name': 'LIE EB', 'owner': 'DOT'},
        # status -101 is a sensor that is not reporting; its speed is garbage
        {'link_id': '2', 'speed': '61.0', 'travel_time': '9', 'status': '-101',
         'data_as_of': '2026-08-27T14:00:00.000', 'link_points': line,
         'borough': 'Bronx', 'link_name': 'CBE WB', 'owner': 'DOT'},
    ]
    fc = TR._snapshot_from_rows(rows)
    assert len(fc['features']) == 1
    p = fc['features'][0]['properties']
    assert p['speed'] == 45.0 and p['link'] == '1'
    assert p['band'] == 'free' and p['direction'] == 'Eastbound'
    assert fc['meta']['links_reporting'] == 1
    assert fc['meta']['links_dark'] == 1


def test_speed_bands_split_stopped_from_moving():
    assert TR._band(0) == 'stopped'
    assert TR._band(9.9) == 'stopped'
    assert TR._band(10) == 'crawling'
    assert TR._band(24.9) == 'crawling'
    assert TR._band(25) == 'moving'
    assert TR._band(40) == 'free'


def test_link_points_survives_a_truncated_tail():
    # The feed cuts this column mid-coordinate on some rows; the good pairs
    # before the cut still make a drawable line.
    pts = TR._parse_link_points('40.71,-73.99 40.72,-73.98 40.73,-73.9')
    assert pts == [[-73.99, 40.71], [-73.98, 40.72], [-73.9, 40.73]]
    assert TR._parse_link_points('garbage 40.71,-73.99') == [[-73.99, 40.71]]
    assert TR._parse_link_points('') == []


# ── traffic: hourly volume ──────────────────────────────────────────────────

def _volume_rows(vols_by_hour, bins=(0, 15, 30, 45)):
    """Rows shaped like the grouped SoQL response, one per (hour, bin)."""
    return [{'segmentid': '1', 'street': 'TEST ST', 'fromst': 'A', 'tost': 'B',
             'direction': 'NB', 'boro': 'Queens',
             'wktgeom': 'POINT (1000124.7 208538.9)',
             'hh': str(h), 'mm': str(mm),
             # each bin carries an equal share of the hour's total
             'v': str(vols_by_hour[h] / len(bins)), 'n': '10'}
            for h in range(24) for mm in bins]


def test_volume_sums_bins_rather_than_averaging_them():
    # 4 bins of 100 vehicles each is 400 vehicles that hour, not 100. This is
    # the trap: averaging `vol` understates every location by the bin count.
    fc = TR._volume_from_rows(_volume_rows({h: 400 for h in range(24)}))
    assert len(fc['features']) == 1
    p = fc['features'][0]['properties']
    assert p['profile'][0] == 400
    assert p['daily'] == 400 * 24


def test_volume_handles_ten_minute_bins_without_assuming_four():
    # Six 10-minute bins summing to 600 is 600 vehicles/hour, same as four
    # 15-minute bins would be — the maths must not hard-code 4.
    rows = _volume_rows({h: 600 for h in range(24)}, bins=(0, 10, 20, 30, 40, 50))
    p = TR._volume_from_rows(rows)['features'][0]['properties']
    assert p['profile'][0] == 600


def test_volume_finds_the_peak_and_the_lull():
    vols = {h: 1000 for h in range(24)}
    vols[3] = 100      # the lull
    vols[17] = 5000    # the evening peak
    p = TR._volume_from_rows(_volume_rows(vols))['features'][0]['properties']
    assert p['peak_hour'] == 17 and p['peak_vph'] == 5000
    assert p['calm_hour'] == 3 and p['calm_vph'] == 100
    assert p['pm_peak_vph'] == 5000
    # Quiet hours are at or under half the peak; 1000 < 2500, so all but 17.
    assert 3 in p['quiet_hours'] and 17 not in p['quiet_hours']


def test_volume_drops_locations_without_a_full_day():
    # A location counted 09:00-17:00 would otherwise report 9AM as its
    # "calmest hour" purely because the night is missing from the file.
    rows = [r for r in _volume_rows({h: 100 for h in range(24)})
            if 9 <= int(r['hh']) <= 17]
    fc = TR._volume_from_rows(rows)
    assert fc['features'] == []
    assert fc['meta']['partial_locations'] == 1


def test_speed_snapshot_never_goes_backwards_in_time(monkeypatch, tmp_path):
    # Socrata answers this dataset from replicas that are out of sync: the same
    # ordered query returns a 13:01 snapshot or a 14:36 one depending on which
    # one takes it. A refresh must not walk the map back in time.
    monkeypatch.setattr(S, 'CACHE_DIR', tmp_path)
    newer = {'type': 'FeatureCollection', 'features': [],
             'meta': {'as_of': '2026-08-27T14:36:06.000', 'links_reporting': 9}}
    older = {'type': 'FeatureCollection', 'features': [],
             'meta': {'as_of': '2026-08-27T13:01:07.000', 'links_reporting': 4}}
    S.cache_write(TR.SPEED_CACHE_KEY, newer)
    # expire the TTL so speeds() actually refetches
    import os, time as _t
    p = tmp_path / 'traffic-speeds.json'
    os.utime(p, (_t.time() - 99999, _t.time() - 99999))

    monkeypatch.setattr(TR, '_fetch_speeds', lambda *a, **k: older)
    assert TR.speeds()['meta']['as_of'] == newer['meta']['as_of']

    # ...but a genuinely newer reading still replaces it, so this converges
    # on the latest rather than pinning to whatever was seen first.
    os.utime(p, (_t.time() - 99999, _t.time() - 99999))
    newest = {'type': 'FeatureCollection', 'features': [],
              'meta': {'as_of': '2026-08-27T15:10:00.000', 'links_reporting': 12}}
    monkeypatch.setattr(TR, '_fetch_speeds', lambda *a, **k: newest)
    assert TR.speeds()['meta']['as_of'] == newest['meta']['as_of']
