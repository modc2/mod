"""Every chain adapter honours networks/base.py — run for each package in
networks/, so a new chain is checked the moment its folder exists."""
import inspect
import os

import pytest

from ztao_src import ROOT
from ztao_src.networks import REQUIRED, Network, adapter_classes

CLASSES = adapter_classes()
IDS = [c.chain for c in CLASSES]


def test_every_chain_is_discovered():
    assert {'starknet', 'near', 'eth', 'solana'} <= set(IDS)
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize('cls', CLASSES, ids=IDS)
def test_adapter_overrides_the_whole_contract(cls):
    missing = [m for m in REQUIRED if getattr(cls, m) is getattr(Network, m)]
    assert not missing, f'{cls.chain} does not implement {missing}'


@pytest.mark.parametrize('cls', CLASSES, ids=IDS)
def test_action_signatures_match_the_base(cls):
    for m in ('submit', 'score', 'settle', 'claim', 'register', 'members', 'tally'):
        want = list(inspect.signature(getattr(Network, m)).parameters)
        got = list(inspect.signature(getattr(cls, m)).parameters)
        assert got[:len(want)] == want, f'{cls.chain}.{m}{got}'


@pytest.mark.parametrize('cls', CLASSES, ids=IDS)
def test_adapter_metadata(cls):
    assert cls.chain and cls.title and cls.nets
    assert 3 <= cls.max_ring <= 64
    assert cls.sim_tempo > 0 and cls.cost_fields
    for k in ('name', 'local', 'verifier', 'deploying', 'color'):
        assert cls.ui.get(k), f'{cls.chain}.ui[{k!r}]'


def test_network_names_are_unique_and_resolve(nets):
    seen = {}
    for a in nets:
        for n in a.nets:
            assert n not in seen, f'{n} claimed by {seen[n]} and {a.chain}'
            seen[n] = a.chain
            assert nets.net(n) is a
    with pytest.raises(ValueError):
        nets.net('nope')


@pytest.mark.parametrize('cls', CLASSES, ids=IDS)
def test_published_sources_exist(cls, nets):
    a = nets.chain(cls.chain)
    for _, paths in a.sources:
        for rel, _ in paths:
            assert os.path.exists(os.path.join(a.dir, rel)), rel


@pytest.mark.parametrize('cls', CLASSES, ids=IDS)
def test_each_chain_ships_its_own_tests(cls, nets):
    d = os.path.join(nets.chain(cls.chain).dir, 'tests')
    assert any(f.startswith('test_') and f.endswith('.py')
               for f in os.listdir(d)), f'no tests in {d}'


def test_files_and_source_whitelist(modpy):
    m = modpy.Mod()
    paths = [f['path'] for g in m.files() for f in g['files']]
    assert 'src/networks/near/contract/src/lsag.rs' in paths
    assert 'src/networks/starknet/contract/src/lsag.cairo' in paths
    assert all(os.path.exists(os.path.join(ROOT, p)) for p in paths)
    assert 'fn verify' in m.source('src/networks/near/contract/src/lsag.rs')['text']
    for bad in ('data/keys.json', 'config.json', '../x'):
        with pytest.raises(ValueError):
            m.source(bad)


def test_chains_map_to_local_nets(modpy):
    assert modpy.CHAINS['starknet'] == 'devnet'
    assert modpy.CHAINS['near'] == 'near'
    assert [n['chain'] for n in modpy.Mod().networks()] == IDS


def test_local_chain_ports_do_not_collide():
    """Every local node gets its own ports (NEAR's p2p once sat on anvil's)."""
    from ztao_src.networks.eth import client as eth
    from ztao_src.networks.near import client as near
    from ztao_src.networks.solana import client as sol
    from ztao_src.networks.starknet import adapter as sn
    used = [sn.DEVNET_PORT, near.SANDBOX_PORT, near.SANDBOX_NET_PORT,
            eth.ANVIL_PORT, sol.RPC_PORT, sol.RPC_PORT + 1, sol.RPC_PORT + 2,
            sol.RPC_PORT + 3, *range(sol.RPC_PORT + 7, sol.RPC_PORT + 35)]
    assert len(used) == len(set(used)), sorted(used)
