"""
Tests for the population / density layer and the shareable report.

Pure logic runs offline. The ``network`` tests stream the real Census bulk
files and DCP tables — they catch an upstream column rename, which would
otherwise surface as a silently empty map.
"""

import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))

from nycgis import demographics as D    # noqa: E402
from nycgis import report as R          # noqa: E402


# ── pure logic ──────────────────────────────────────────────────────────────

def test_derive_rates_from_counts():
    s = D._derive({'population': 1000, 'land_sqmi': 0.5, 'housing_units': 400,
                   'vacant_units': 40, 'households': 360, 'renter_households': 270,
                   'rb_total': 270, 'rb_nc': 20, 'rb30a': 50, 'rb30b': 25,
                   'rb30c': 25, 'rb50': 25, 'pov_total': 1000, 'pov_a': 100,
                   'pov_b': 50, 'new_units_since_2020': 30,
                   'census_units_2020': 370, 'median_income': 50_000,
                   'median_sale_price': 500_000})
    assert s['density'] == 2000
    assert s['vacancy_pct'] == 10.0
    assert s['renter_pct'] == 75.0
    assert s['rent_burden_pct'] == 50.0      # 125 of the 250 computed
    assert s['severe_burden_pct'] == 10.0
    assert s['poverty_pct'] == 15.0
    assert s['new_units_per_1k'] == 30.0
    assert s['price_to_income'] == 10.0
    assert 'rb30a' not in s and 'pov_a' not in s   # intermediates dropped


def test_zero_population_is_no_data_not_lowest_density():
    assert D._derive({'population': 0, 'land_sqmi': 2.0})['density'] is None


def test_rollup_sums_counts_and_weights_medians():
    rows = [{'k': 'A', 'population': 100, 'households': 10, 'median_income': 10_000},
            {'k': 'A', 'population': 300, 'households': 30, 'median_income': 50_000},
            {'k': 'B', 'population': 5, 'households': 0, 'median_income': 99_999}]
    g = D._rollup(rows, lambda r: r['k'])
    assert g['A']['population'] == 400
    assert g['A']['median_income'] == 40_000         # household-weighted
    assert g['B']['median_income'] is None           # no weight, no median


def test_quantile_breaks_are_strictly_increasing():
    b = D.quantile_breaks([1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 100], 6)
    assert b['min'] == 1 and b['max'] == 100
    assert all(x < y for x, y in zip(b['stops'], b['stops'][1:]))


def test_report_formatting():
    assert R.fmt(None, 'usd') == '–'
    assert R.fmt(80483, 'usd') == '$80,483'
    assert R.fmt(52.44, 'pct') == '52.4%'
    assert R._short(18535, 'int') == '18.5k'
    assert R._short(1_200_000, 'usd') == '$1.2M'


# ── live sources ────────────────────────────────────────────────────────────

@pytest.mark.network
def test_citywide_totals_are_plausible():
    c = D.stats('borough')['city']
    assert 8_000_000 < c['population'] < 9_000_000
    assert 25_000 < c['density'] < 32_000
    assert c['medians'] == 'exact'                   # Census's own, not a roll-up
    assert 50_000 < c['median_income'] < 120_000
    assert set(D.stats('borough')['areas']) == set(D.NYC_COUNTIES.values())


@pytest.mark.network
def test_density_layer_and_report_render():
    fc = D.choropleth('density', 'tract')
    assert len(fc['features']) > 2000
    assert fc['breaks']['stops'][0] == fc['breaks']['min']
    page = R.html_report()
    assert page.startswith('<!doctype html>') and '<svg' in page
    assert 'http' not in page.split('<main>')[0].replace('http://www.w3.org', '')
