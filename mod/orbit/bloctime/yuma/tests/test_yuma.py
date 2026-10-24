"""pytest yuma/tests — the paper engine against the shared fixtures, and the
Ledger's contract rules (registration, masking, tempo, claims)."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import yuma as Y  # noqa: E402

CASES = json.loads((HERE.parent / 'fixtures' / 'cases.json').read_text())
E = 10 ** 18


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_fixture_matches(case):
    p = {k: int(v) for k, v in case['params'].items()}
    S = [int(x) for x in case['stakes']]
    M = len(case['epochs'][0][0])
    B = [[0] * M for _ in S]
    for W, want in zip(case['epochs'], case['expected']):
        S_eff = [S[i] if sum(W[i]) else 0 for i in range(len(S))]
        r = Y.yuma(S_eff, W, B, p['kappa'], p['bond_alpha'])
        assert r['incentive'] == [int(x) for x in want['incentive']]
        assert r['dividends'] == [int(x) for x in want['dividends']]
        assert r['consensus'] == [int(x) for x in want['consensus']]
        assert B == [[int(x) for x in row] for row in want['bonds']]
        m, v, o = Y.pay(p['emission'], r['incentive'], r['dividends'], p['owner_cut_bps'], p['miner_share_bps'])
        assert m == [int(x) for x in want['miner_pay']]
        assert v == [int(x) for x in want['validator_pay']]
        assert o == int(want['owner_pay'])
        assert sum(m) + sum(v) + o == p['emission']  # every unit accounted for


def test_outlier_is_clipped():
    B = [[0, 0], [0, 0]]
    r = Y.yuma([900, 100], [[1, 0], [0, 1]], B)
    assert r['incentive'] == [Y.ONE, 0]
    assert r['dividends'] == [Y.ONE, 0]


class Clock:
    def __init__(self):
        self.t = 1_000_000

    def __call__(self):
        return self.t


@pytest.fixture
def led(tmp_path):
    c = Clock()
    L = Y.Ledger(tmp_path / 'l.json', clock=c, owner='own')
    L.clock_obj = c
    return L


def test_ledger_epoch_and_claim(led):
    led.set_params(owner_cut_bps=0, tempo=60)
    led.set_stake('alice', 10 * 30 * 86400 * E)    # 10 NAT for 30 days
    led.set_stake('bob', 1000 * 60 * E)            # 1000 NAT for a minute
    for v in ('alice', 'bob'):
        led.register_validator(v)
    for m in ('m1', 'm2'):
        led.register_miner(m)
    led.set_weights('alice', {0: 1})
    led.set_weights('bob', {1: 1})
    with pytest.raises(ValueError, match='not due'):
        led.epoch()
    led.clock_obj.t += 60
    r = led.epoch()
    assert r['incentive'] == [Y.ONE, 0]
    assert r['emission'] == 100 * E
    assert led.claim('m1')['claimed'] == 50 * E
    assert led.claim('alice')['claimed'] == 50 * E
    with pytest.raises(ValueError):
        led.claim('m2')
    # state survives a reload
    again = Y.Ledger(led.path, clock=led.clock_obj)
    assert again.state()['epoch_count'] == 1


def test_ledger_replacement_and_masking(led):
    led.set_params(max_validators=1, max_miners=2, owner_cut_bps=0)
    led.set_stake('a', 5)
    led.set_stake('b', 3)
    led.set_stake('c', 9)
    led.register_validator('a')
    with pytest.raises(ValueError, match='weakest'):
        led.register_validator('b')
    assert led.register_validator('c') == {'uid': 0, 'replaced': 'a'}
    led.register_miner('m1')
    led.register_miner('m2')
    with pytest.raises(ValueError, match='immune'):
        led.register_miner('m3')
    led.set_weights('c', [1, 5])
    led.epoch(force=True)
    assert led.register_miner('m3') == {'uid': 0, 'replaced': 'm1'}
    r = led.epoch(force=True)
    assert r['incentive'] == [0, Y.ONE]       # c's old row scored m1, not m3
    led.set_weights('c', [1, 1])
    assert led.epoch(force=True)['incentive'] == [Y.ONE // 2, Y.ONE // 2]


def test_stake_fn_is_live_source(tmp_path):
    power = {'v': 7}
    L = Y.Ledger(tmp_path / 'x.json', stake_fn=lambda a: power.get(a, 0))
    L.register_validator('v')
    L.register_miner('m')
    L.set_weights('v', [1])
    assert L.preview()['stake'] == [7]
    power['v'] = 0
    assert L.preview()['stake'] == [0]
