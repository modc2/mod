"""
Tests for the city-data domain tools (311, crime, collisions, restaurants,
trees, evictions, permits, air) and the harvested open-data catalog.

All offline: they check the registry wiring, the input normalisation that
guards the SoQL strings, and the catalog's search scoring over a fixture —
not what the city published today (the live checks live in test_nyc.py's
``network`` group).
"""

import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))

from nycgis import catalog as CAT      # noqa: E402
from nycgis import citydata as CD      # noqa: E402
from nycgis import tools as T          # noqa: E402

NEW_TOOLS = ['nyc_catalog', 'nyc_311', 'nyc_collisions', 'nyc_restaurants',
             'nyc_trees', 'nyc_evictions', 'nyc_permits', 'nyc_air']


# ── registry wiring ─────────────────────────────────────────────────────────

def test_city_data_tools_are_registered():
    names = {t['name'] for t in T.list_tools()}
    missing = [n for n in NEW_TOOLS + ['nyc_crime'] if n not in names]
    assert not missing, f'tools missing from the registry: {missing}'


def test_city_data_tool_schemas_are_valid_objects():
    by_name = {t['name']: t for t in T.list_tools()}
    for name in NEW_TOOLS:
        schema = by_name[name]['inputSchema']
        assert schema['type'] == 'object'
        assert schema['properties'], f'{name} declares no parameters'
        for pname, prop in schema['properties'].items():
            assert prop.get('type'), f'{name}.{pname} has no type'
            assert prop.get('description'), f'{name}.{pname} has no description'
        assert by_name[name]['description']
        # read-only like everything else in this registry
        assert by_name[name]['annotations']['readOnlyHint'] is True


def test_city_data_tools_reject_unknown_arguments():
    for name in NEW_TOOLS:
        with pytest.raises(ValueError, match='bogus'):
            T.call_tool(name, {'bogus_argument': 1, 'limit': 5}
                        if name != 'nyc_air' else {'bogus_argument': 1})


def test_groups_cover_the_new_domains():
    g = T.groups()
    assert 'nyc_collisions' in g['safety'] and 'nyc_crime' in g['safety']
    assert set(g['city_life']) >= {'nyc_311', 'nyc_restaurants',
                                   'nyc_trees', 'nyc_air'}
    assert {'nyc_evictions', 'nyc_permits'} <= set(g['housing'])
    assert 'nyc_catalog' in g['open_data']


# ── input normalisation (these strings end up inside SoQL) ──────────────────

def test_borough_aliases_normalise():
    assert CD.borough_norm('brooklyn') == 'BROOKLYN'
    assert CD.borough_norm('BK') == 'BROOKLYN'
    assert CD.borough_norm('Staten Island') == 'STATEN ISLAND'
    assert CD.borough_norm('richmond') == 'STATEN ISLAND'
    assert CD.borough_norm('the bronx') == 'BRONX'
    assert CD.borough_norm('') is None
    with pytest.raises(ValueError, match='unknown borough'):
        CD.borough_norm('hoboken')


def test_single_quotes_are_escaped_in_like_clauses():
    clause = CD._like('dba', "Lucali's")
    assert "''" in clause and clause.count("'") % 2 == 0


def test_date_arguments_are_validated_not_interpolated():
    assert CD._day('2026-01-05', 'since') == '2026-01-05'
    assert CD._day('2026-01-05T12:00:00', 'since') == '2026-01-05'
    with pytest.raises(ValueError, match='since'):
        CD._day("2026' OR 1=1", 'since')


def test_unknown_group_by_is_rejected_with_the_options():
    with pytest.raises(ValueError, match='type, borough, zip'):
        CD.complaints_311(group_by='vibes')
    with pytest.raises(ValueError, match='severity'):
        CD.crime(severity='parking ticket')
    with pytest.raises(ValueError, match='residential'):
        CD.evictions(kind='both')
    with pytest.raises(ValueError, match='census'):
        CD.trees(source='satellite')
    with pytest.raises(ValueError, match='borough'):
        CD.trees(source='living', borough='queens')


def test_blank_group_keys_become_an_honest_bucket():
    assert CD._label('') == '(blank)'
    assert CD._label(None) == '(blank)'
    assert CD._label('Unspecified') == 'Unspecified'


def test_every_curated_result_cites_its_dataset():
    for ds in (CD.D311, CD.COLLISIONS, CD.RESTAURANTS, CD.TREES_2015,
               CD.EVICTIONS, CD.PERMITS, CD.AIR):
        src = CD._src(ds)[0]
        assert src['dataset'] == ds
        assert src['name'] and ds in src['url']


# ── catalog search scoring ──────────────────────────────────────────────────

FIXTURE = {
    'domain': 'data.cityofnewyork.us',
    'harvested': '2026-10-07T00:00:00Z',
    'count': 5,
    'datasets': [
        {'id': 'aaaa-0001', 'name': 'Evictions',
         'description': 'Marshal-executed evictions since 2017.',
         'category': 'City Government', 'updated': '2026-10-01',
         'rows': None, 'url': 'https://x/d/aaaa-0001'},
        {'id': 'aaaa-0002', 'name': 'Housing Court Filings',
         'description': 'Cases that may end in evictions.',
         'category': 'Housing & Development', 'updated': '2026-09-01',
         'rows': None, 'url': 'https://x/d/aaaa-0002'},
        {'id': 'aaaa-0003', 'name': 'Public Restroom Ratings',
         'description': 'Ratings of park restrooms.',
         'category': 'Recreation', 'updated': '2026-10-05',
         'rows': None, 'url': 'https://x/d/aaaa-0003'},
        {'id': 'aaaa-0004', 'name': 'Rat Sightings',
         'description': '311 rodent complaints as points.',
         'category': 'Health', 'updated': '2020-01-01',
         'rows': None, 'url': 'https://x/d/aaaa-0004'},
        {'id': 'aaaa-0005', 'name': 'Tree Census 2015',
         'description': 'Every street tree, with rat burrow notes.',
         'category': 'Environment', 'updated': '2026-10-02',
         'rows': None, 'url': 'https://x/d/aaaa-0005'},
    ],
}


@pytest.fixture
def fixture_catalog(monkeypatch):
    monkeypatch.setattr(CAT, 'harvest', lambda domain='nyc', refresh=False: FIXTURE)


def test_exact_name_match_outranks_description_mentions(fixture_catalog):
    r = CAT.search('evictions', 'nyc')
    ids = [d['id'] for d in r['datasets']]
    # the dataset NAMED Evictions first, the one that merely mentions it second
    assert ids[:2] == ['aaaa-0001', 'aaaa-0002']
    assert r['datasets'][0]['score'] > r['datasets'][1]['score']


def test_name_token_hit_beats_description_hit(fixture_catalog):
    r = CAT.search('rat', 'nyc')
    ids = [d['id'] for d in r['datasets']]
    assert ids[0] == 'aaaa-0004'          # "Rat" in the name
    assert 'aaaa-0005' in ids             # "rat" only in the description
    # short tokens must not prefix-match: "rat" is not "Ratings"
    assert 'aaaa-0003' not in ids


def test_prefix_matching_needs_four_characters(fixture_catalog):
    r = CAT.search('evict', 'nyc')        # stem of "Evictions"
    assert r['datasets'][0]['id'] == 'aaaa-0001'


def test_category_filter_narrows_the_corpus(fixture_catalog):
    r = CAT.search('rat', 'nyc', category='health')
    assert [d['id'] for d in r['datasets']] == ['aaaa-0004']


def test_search_result_keeps_the_find_datasets_shape(fixture_catalog):
    r = CAT.search('evictions', 'nyc')
    assert {'domain', 'query', 'count', 'datasets'} <= set(r)
    d = r['datasets'][0]
    assert {'id', 'name', 'description', 'updated', 'rows',
            'category', 'url'} <= set(d)


def test_search_local_declines_without_a_harvest(monkeypatch):
    monkeypatch.setattr(CAT, 'has_harvest', lambda domain='nyc': False)
    assert CAT.search_local('anything', 'nyc') is None


def test_stats_counts_categories(fixture_catalog, monkeypatch):
    monkeypatch.setattr(CAT.S, 'cache_age', lambda key: 60.0)
    s = CAT.stats('nyc')
    assert s['datasets'] == 5
    cats = {c['category']: c['datasets'] for c in s['by_category']}
    assert cats['City Government'] == 1 and len(cats) == 5


def test_browse_filters_by_category_newest_first(fixture_catalog):
    r = CAT.browse('nyc', 'health')
    assert [d['id'] for d in r['datasets']] == ['aaaa-0004']
    everything = CAT.browse('nyc', '')
    updated = [d['updated'] for d in everything['datasets']]
    assert updated == sorted(updated, reverse=True)
