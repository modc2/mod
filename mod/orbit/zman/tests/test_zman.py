"""zman end to end, in a throwaway store: bond → subscribe → acquire → rent → coupon."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mod import Mod  # noqa: E402

CITY = '0xC17Y'


@pytest.fixture
def z(tmp_path):
    z = Mod(store=tmp_path)
    assert z.claim_city(key=CITY)['success']
    return z


def _building(z, **kw):
    z.issue_bond('HDC-2026A', 10_000_000, 4.0, 30, key=CITY)
    z.subscribe('HDC-2026A', '0xALICE', 6_000_000)
    z.subscribe('HDC-2026A', '0xBOB', 4_000_000)
    args = dict(name='1520 Sedgwick Ave', borough='bronx', units=100, price=8_000_000,
                monthly_rent=150_000, series='HDC-2026A', key=CITY, partner='Bronx CLT')
    args.update(kw)
    return z.acquire(**args)


def test_city_seat_is_first_writer_and_gates_writes(z):
    assert 'error' in z.claim_city(key='0xOTHER')
    assert 'error' in z.issue_bond('X', 1, 1, 1, key='0xOTHER')


def test_cannot_oversell_or_overspend(z):
    z.issue_bond('S', 100, 3, 10, key=CITY)
    assert 'error' in z.subscribe('S', '0xA', 101)
    z.subscribe('S', '0xA', 50)
    r = z.acquire('x', 'queens', 1, 60, 1, 'S', key=CITY)
    assert 'error' in r and 'available' in r['error']


def test_acquire_seats_city_in_both_openhouse_seats(z):
    r = _building(z)
    assert r['success']
    b = r['building']
    assert b['terms']['owner'] == CITY and b['terms']['fee_pct'] == 0.0
    assert b['terms']['home_price'] == 8_000_000
    assert b['civic']['chartered'] and b['civic']['authority']['key'] == CITY
    assert z.bond('HDC-2026A')['available'] == 2_000_000


def test_rent_splits_to_equity_and_bondholders(z):
    bid = _building(z)['building']['id']
    r = z.pay_rent(bid, '0xTENANT', 1500)
    assert r['credit'] == 750 and r['debt_service'] == 750
    assert z.bond('HDC-2026A')['collected'] == 750
    c = z.pay_coupon('HDC-2026A', key=CITY)
    assert c['to'] == {'0xALICE': 450.0, '0xBOB': 300.0}
    assert z.holder('0xalice')['received'] == 450.0
    assert 'error' in z.pay_coupon('HDC-2026A', key=CITY)   # nothing left owed


def test_rent_freeze_blocks_increases_only(z):
    bid = _building(z)['building']['id']
    assert 'freeze' in z.set_rent(bid, 160_000, key=CITY)['error'].lower()
    assert z.set_rent(bid, 140_000, key=CITY)['success']
    z.set_freeze(False, key=CITY)
    assert z.set_rent(bid, 160_000, key=CITY)['success']


def test_civic_pause_stops_rent(z):
    bid = _building(z)['building']['id']
    z.house(bid).civic_override('pause', key=CITY, reason='code enforcement')
    assert 'Civic pause' in z.pay_rent(bid, '0xT', 100)['error']


def test_coverage_and_status(z):
    _building(z)
    cov = z.coverage('HDC-2026A')
    # 150k/mo x 12 x 50% = 900k/yr vs ~578k/yr on $10M @4% / 30y
    assert cov['annual_scheduled_to_bonds'] == 900_000
    assert 1.5 < cov['dscr'] < 1.6
    s = z.status()
    assert s['homes'] == 100 and s['by_borough']['bronx'] == 100
    assert z.plan()['progress']['homes_acquired'] == 100
