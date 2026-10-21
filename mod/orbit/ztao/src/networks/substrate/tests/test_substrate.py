"""Substrate adapter. Offline: SCALE codec, ink! result decoding, the
recipient/subnet felt rule, build wiring, fee math. ZTAO_NATIVE=1: the
cargo suite. ZTAO_LIVE=1: a full epoch on substrate-contracts-node."""
import hashlib
import json
import os

import pytest

from ztao_src.networks.substrate import client as C

HERE = os.path.dirname(os.path.abspath(__file__))


def test_compact_matches_scale_spec():
    # parity-scale-codec reference vectors
    assert C.compact(0) == b'\x00'
    assert C.compact(1) == b'\x04'
    assert C.compact(63) == b'\xfc'
    assert C.compact(64) == b'\x01\x01'
    assert C.compact(16383) == b'\xfd\xff'
    assert C.compact(16384) == b'\x02\x00\x01\x00'
    assert C.compact(1 << 30) == b'\x03\x00\x00\x00\x40'
    for n in (0, 1, 63, 64, 16384, 1 << 30, 10 ** 12, 2 ** 64 - 1):
        assert C.Reader(C.compact(n)).compact() == n


def test_felts_are_big_endian_32_bytes():
    assert C.felt32('0x1') == b'\0' * 31 + b'\x01'
    assert C.felt32(255) == b'\0' * 31 + b'\xff'
    assert C.Reader(C.felt32('0xabc')).felt() == '0xabc'


def test_signature_layout():
    s = {'c0': '0x1', 's': ['0x2', '0x3'], 'image': {'x': '0x4', 'y': '0x5'}}
    enc = C.signature(s)
    # c0 | compact(2) | s0 | s1 | image.x | image.y | None
    assert len(enc) == 32 + 1 + 64 + 64 + 1 and enc[32] == 8 and enc[-1] == 0
    hp = C.signature({**s, 'hp': {'x': '0x6', 'y': '0x7'}})
    assert hp[-65] == 1 and hp[-1] == 7


def test_results_decode_and_errors_raise():
    tag = C.felt32('0xbeef')
    assert C.decode_return('submit', b'\0\0' + tag) == '0xbeef'
    assert C.decode_return('epoch', b'\0' + C.u(7, 64)) == 7
    with pytest.raises(C.SubstrateError, match='already claimed'):
        C.decode_return('claim', b'\0\x01' + bytes([C.ERRORS.index('AlreadyClaimed')]))
    with pytest.raises(C.SubstrateError, match='LangError'):
        C.decode_return('epoch', b'\x01\x01')
    assert C.decode_return('settle', b'\0\0' + bytes([90]) + C.u(360, 128)) == (90, 360)


def test_error_codes_mirror_the_contract():
    src = open(os.path.join(HERE, '..', 'contract', 'src', 'lib.rs')).read()
    body = src.split('pub enum Error {', 1)[1].split('}', 1)[0]
    names = [w.strip().rstrip(',') for w in body.split('\n') if w.strip()]
    assert names == C.ERRORS


def test_dry_run_decoder():
    raw = (C.compact(5) + C.compact(6) + C.compact(7) + C.compact(8)
           + b'\x01' + C.u(99, 128) + C.compact(0)
           + b'\x00' + C.u(1, 32) + C.compact(3) + b'abc')
    r = C.decode_dry_run(raw)
    assert r['gas_consumed'] == {'ref_time': 5, 'proof_size': 6}
    assert r['gas_required'] == {'ref_time': 7, 'proof_size': 8}
    assert r['storage_deposit'] == 99 and r['flags'] == C.REVERT
    assert r['data'] == b'abc'


def test_bytes_felt_matches_contract_rule():
    acct = bytes(range(32))
    d = bytearray(hashlib.blake2b(acct, digest_size=32).digest())
    d[0] &= 0x07
    assert C.bytes_felt(acct) == int.from_bytes(d, 'big') < 2 ** 251


def test_build_wiring():
    crate = C.CRATE
    toml = open(os.path.join(crate, 'Cargo.toml')).read()
    assert '[patch.crates-io]' in toml and '../tools/getrandom-stub' in toml
    assert 'build = "build.rs"' in toml
    # the shared verifier, not a fork: build.rs reads near/contract
    br = open(os.path.join(crate, 'build.rs')).read()
    assert '../../near/contract/src' in br
    for f in ('lsag.rs', 'fp.rs', 'poseidon.rs'):
        assert f in br
        assert not os.path.exists(os.path.join(crate, 'src', f))


def test_selectors_from_metadata():
    if not os.path.exists(C.META):
        pytest.skip('contract not built')
    sel = C.selectors()
    for m in C.MESSAGES:
        assert m in sel and len(sel[m]) == 4, m
    assert len(sel['@new']) == 4


def test_adapter_shape(nets):
    a = nets.chain('substrate')
    assert a.nets == ('substrate', 'substrate-testnet') and a.max_ring == 64
    assert a.cost_fields == ('weight_ms', 'proof_kb', 'bytes')
    rows = [r for r in a.default_costs['rows'] if 'weight_ms' in r]
    assert {r['ring'] for r in rows} >= {4, 32, 64}
    assert all(r['weight_ms'] < a.default_costs['limit_ms'] for r in rows)


def test_fee_is_weight_times_price_plus_length(nets):
    a = nets.chain('substrate')
    m = {'gas': {'astr_per_ms': 0.327, 'astr_per_byte': 2.35e-5,
                 'multiplier': 0.1, 'base_ms': 0.094}}
    fee = a.fee({'weight_ms': 29.3, 'bytes': 583}, m)
    assert fee == pytest.approx((29.3 + 0.094) * 0.327 * 0.1 + (583 + 150) * 2.35e-5)
    assert a.fee({}, m) is None and a.load({'weight_ms': 3}) == 3


@pytest.mark.native
def test_native_suite(native_suite):
    native_suite('substrate', 13)


@pytest.mark.live
def test_epoch_on_local_net(live_epoch):
    live_epoch('substrate')
