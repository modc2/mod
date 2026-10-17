import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def r2o(tmp_path, monkeypatch):
    monkeypatch.setenv('RENT2OWN_DATA', str(tmp_path))
    spec = importlib.util.spec_from_file_location('rent2own_mod', os.path.join(HERE, 'mod.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


def test_quote_flat_price(r2o):
    q = r2o.quote(price=300000, rent=2000, credit=0.25, months=36, full=True)
    end = q['at_exercise']
    assert len(q['schedule']) == 36
    assert end['equity'] == 18000
    assert end['strike'] == 300000
    assert end['balance'] == 282000
    assert end['total_paid'] == 72000
    assert end['rent_as_rent'] == 54000


def test_quote_appreciation_and_fee(r2o):
    q = r2o.quote(price=100000, rent=1000, credit=0.5, months=12,
                  option_fee=5000, appreciation=0.03)
    assert q['at_exercise']['equity'] == 11000
    assert q['at_exercise']['strike'] == 103000
    assert [r['month'] for r in q['schedule']] == [12]


def test_quote_validates(r2o):
    with pytest.raises(ValueError):
        r2o.quote(rent=1000)
    with pytest.raises(ValueError):
        r2o.quote(price=1, rent=1, credit=2)


def test_ledger_roundtrip(r2o):
    a = r2o.save(name='elm', price=200000, rent=1500, months=3)
    assert r2o.save(name='elm', price=200000, rent=1500, months=3)['id'] == a['id']
    assert r2o.health()['agreements'] == 1
    for _ in range(3):
        r2o.pay(id=a['id'])
    with pytest.raises(ValueError):
        r2o.pay(id=a['id'])
    full = r2o.agreement(id=a['id'])
    assert all(r['paid'] for r in full['schedule'])
    assert r2o.agreements()[0]['months_paid'] == 3
    r2o.remove(id=a['id'])
    assert r2o.agreements() == []
