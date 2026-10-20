"""Mainnet dollars and the 1 h epoch floor (core/fees.py), offline: the
quote is stubbed, so no RPC or price feed is touched."""
import pytest

from ztensor_src.core import fees

# what each adapter's mainnet() returned once, plus a token price
QUOTE = {'at': 1, 'chains': {
    'starknet': {'block_seconds': 1.8, 'usd': 0.06, 'block_capacity': None,
                 'gas': {'l2_strk': 1.5e-8, 'l1_data_strk': 4e-8}},
    'near': {'block_seconds': 0.6, 'usd': 5.0, 'block_capacity': 1000.0,
             'capacity_unit': 'Tgas', 'tx_cap': 300,
             'gas': {'yocto_per_gas': 100_000_000}},
    'eth': {'block_seconds': 12.0, 'usd': 2400.0, 'block_capacity': 60_000_000,
            'capacity_unit': 'gas', 'tx_cap': 1 << 24,
            'gas': {'base_gwei': 0.1, 'tip_gwei': 0.0, 'gwei': 0.1}},
    'solana': {'block_seconds': 0.4, 'usd': 100.0, 'block_capacity': 60_000_000,
               'capacity_unit': 'CU', 'tx_cap': 1_400_000,
               'gas': {'lamports_per_sig': 5000, 'micro_lamports_per_cu': 0}},
}}


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(fees, 'quote', lambda nets, fresh=False: QUOTE)


def test_min_tempo_is_an_hour_rounded_up():
    assert fees.min_tempo(12) == 300
    assert fees.min_tempo(0.6) == 6000          # not 6001 from float noise
    assert fees.min_tempo(7) == 515
    assert fees.min_tempo(None) is None


def test_every_priced_adapter_prices_its_own_cost(nets):
    want = {'starknet': {'steps': 16069, 'ec_op': 19},
            'near': {'tgas': 37.0}, 'eth': {'gas': 2_300_000},
            'solana': {'cu': 40_000_000, 'txs': 47}}
    for a in nets:
        if not a.token:
            continue
        n = a.fee(want[a.chain], QUOTE['chains'][a.chain])
        assert n and n > 0, a.chain
    eth, near = nets.chain('eth'), nets.chain('near')
    assert eth.fee({'gas': 1e9}, QUOTE['chains']['eth']) == pytest.approx(0.1)
    # 1 Tgas at 1e8 yocto/gas = 1e-4 NEAR
    assert near.fee({'tgas': 1}, QUOTE['chains']['near']) == pytest.approx(1e-4)
    sol = nets.chain('solana').fee({'cu': 1, 'txs': 1}, QUOTE['chains']['solana'])
    assert sol == pytest.approx(2 * 5000 / 1e9)


def test_public_deploy_refuses_an_epoch_under_an_hour(modpy, stub):
    with pytest.raises(ValueError, match='minimum epoch'):
        modpy.Mod().deploy('eth-sepolia', tempo=299)


def test_public_deploy_defaults_to_the_hour(modpy, nets, stub, monkeypatch):
    seen = {}
    a = nets.chain('near')
    monkeypatch.setattr(a, 'deploy', lambda net, tempo=None, **k:
                        seen.update(tempo=tempo) or {'ok': True})
    modpy.Mod().deploy('near-testnet')
    assert seen['tempo'] == 6000


def test_economics_shape(modpy, stub):
    e = modpy.Mod().economics()
    assert e['min_epoch_s'] == 3600
    for c, row in e['chains'].items():
        assert row['min_tempo'] == fees.min_tempo(row['block_seconds'])
        for k, act in row['actions'].items():
            assert act['usd'] is None or act['usd'] > 0, (c, k)
        assert all(r['usd'] > 0 for r in row['by_ring']), c
