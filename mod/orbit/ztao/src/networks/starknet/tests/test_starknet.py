"""Starknet adapter. Offline: calldata layout, selectors, network table.
ZTAO_NATIVE=1: snforge suite. ZTAO_LIVE=1: a full epoch on starknet-devnet."""
import pytest

from ztao_src.networks.starknet import adapter as A


def test_selector_is_starknet_keccak():
    # starknet_keccak('transfer'), the well-known ERC-20 entry point
    assert A.selector('transfer') == \
        '0x83afd3f4caedc6eebf44246fe54e38c95e3179a5ec9ea81740eca5b482d12e'
    assert int(A.selector('submit'), 16) < 2 ** 250


def test_sig_calldata_layout():
    sig = {'ring': [{'x': '0x1', 'y': '0x2'}, {'x': '0x3', 'y': '0x4'}],
           'c0': '0x9', 's': ['0x5', '0x6'], 'image': {'x': '0x7', 'y': '0x8'}}
    ring, rest = A.sig_calldata(sig)
    assert ring == [2, 1, 2, 3, 4]               # Array<(x, y)>: len + pairs
    assert rest == [9, 2, 5, 6, 7, 8]            # c0, Array<s>, image


def test_networks_and_caps(nets):
    a = nets.chain('starknet')
    assert a.nets == ('devnet', 'sepolia', 'mainnet') and a.local == 'devnet'
    assert a.max_ring == 64
    assert set(A.NETWORKS) == set(a.nets)
    assert A.NETWORKS['devnet']['rpc'].startswith('http://127.0.0.1')


def test_public_net_needs_an_account(nets):
    with pytest.raises(ValueError, match='account='):
        nets.chain('starknet')._ctx('sepolia', accounts_file='/nonexistent')


def test_source_hash_is_stable_and_skips_target(nets):
    a = nets.chain('starknet')
    assert a._src_hash() == a._src_hash() and len(a._src_hash()) == 64


@pytest.mark.native
def test_snforge_suite(native_suite):
    native_suite('starknet', 10)


@pytest.mark.live
def test_epoch_on_devnet(live_epoch):
    r = live_epoch('starknet')
    assert r['network'] == 'devnet'
