"""Pure-logic tests for the tenant-side housing engine."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nycgis import housing as H


# ── number parsing: spreadsheet uploads arrive with commas ──────────────

def test_n_strips_commas_and_survives_junk():
    assert H._n('1,234') == 1234
    assert H._n('12') == 12
    assert H._n(12.0) == 12
    assert H._n(None) == 0
    assert H._n('') == 0
    assert H._n('N/A') == 0


# ── address splitting ────────────────────────────────────────────────────

def test_split_address_basic():
    assert H.split_address('184 Eldert St') == ('184', 'ELDERT ST')


def test_split_address_keeps_queens_hyphen_numbers():
    assert H.split_address('177-06 Wexford Terrace') == \
        ('177-06', 'WEXFORD TERRACE')


def test_split_address_rejects_street_only():
    with pytest.raises(ValueError):
        H.split_address('Broadway')
    with pytest.raises(ValueError):
        H.split_address('')


# ── street matching must survive ST/STREET suffix drift ─────────────────

def test_street_clause_drops_known_suffix():
    assert H._street_clause('streetname', 'ELDERT ST') == \
        "upper(streetname) like 'ELDERT %'"
    assert H._street_clause('streetname', 'ELDERT STREET') == \
        "upper(streetname) like 'ELDERT %'"


def test_street_clause_without_suffix_is_prefix_match():
    assert H._street_clause('streetname', 'BROADWAY') == \
        "upper(streetname) like 'BROADWAY%'"


def test_street_clause_escapes_quotes():
    assert H._street_clause('streetname', "O'BRIEN AVENUE") == \
        "upper(streetname) like 'O''BRIEN %'"


# ── borough + status validation raise instead of guessing ───────────────

def test_boro_aliases_and_rejects_unknown():
    assert H._boro('bk') == 'BROOKLYN'
    assert H._boro('Richmond') == 'STATEN ISLAND'
    assert H._boro('') is None
    with pytest.raises(ValueError):
        H._boro('jersey')


def test_lotteries_rejects_unknown_status():
    with pytest.raises(ValueError):
        H.lotteries(status='pending')


def test_day_trims_socrata_timestamps():
    assert H._day('2026-10-05T00:00:00.000') == '2026-10-05'
    assert H._day(None) == ''


def test_esc_doubles_single_quotes():
    assert H._esc("L'IDEA") == "L''IDEA"
