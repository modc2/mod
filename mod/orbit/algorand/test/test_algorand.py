"""algorand tests. Offline tests pin the wire formats; online tests (skipped
with ALGORAND_OFFLINE=1) read the live chain and prove algod accepts our
signatures without spending anything."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(HERE)

import codec  # noqa: E402

online = pytest.mark.skipif(os.environ.get('ALGORAND_OFFLINE') == '1',
                            reason='ALGORAND_OFFLINE=1')


@pytest.fixture(autouse=True)
def keystore(tmp_path, monkeypatch):
    import wallet
    monkeypatch.setattr(wallet, 'HOME', str(tmp_path))
    monkeypatch.setattr(wallet, 'KEYS', str(tmp_path / 'keys.json'))
    monkeypatch.setattr(wallet, 'TOKEN', str(tmp_path / 'token'))
    return tmp_path


# ── wire formats ─────────────────────────────────────────────────

def test_msgpack_canonical_matches_reference():
    msgpack = pytest.importorskip('msgpack')
    o = {'b': 1, 'a': [1, b'xx', 's' * 40, -5, 300, 70000, 2 ** 40],
         'z': 0, 'e': b'', 'f': False, 'm': {'y': True, 'x': None}}
    want = msgpack.packb({'a': o['a'], 'b': 1, 'm': {'y': True}}, use_bin_type=True)
    assert codec.msgpack(o) == want
    assert codec.unmsgpack(want) == {'a': o['a'], 'b': 1, 'm': {'y': True}}


def test_address_roundtrip_and_checksum():
    pk = bytes(range(32))
    a = codec.encode_address(pk)
    assert len(a) == 58 and codec.decode_address(a) == pk
    bad = a[:-1] + ('A' if a[-1] != 'A' else 'B')
    assert not codec.is_address(bad)
    assert codec.is_address(codec.ZERO_ADDRESS)
    assert codec.decode_address(codec.ZERO_ADDRESS) == b'\0' * 32


def test_known_derived_addresses():
    # App 1's account and the ARC-4 docs' selector are published values.
    assert codec.app_address(1) == 'WCS6TVPJRBSARHLN2326LRU5BYVJZUKI2VJ53CAWKYYHDE455ZGKANWMGM'
    assert codec.method_selector('add(uint64,uint64)uint64').hex() == 'fe6bdf69'


def test_pure_python_ed25519_matches_nacl():
    seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
    # RFC 8032 test vector 1
    assert codec._py_public(seed).hex() == \
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    sig = codec._py_sign(seed, b'')
    assert sig.hex().startswith('e5564300c360ac72')
    assert sig == codec.sign(seed, b'')


def test_mnemonic_roundtrip_and_checksum():
    seed = bytes(range(1, 33))
    m = codec.to_mnemonic(seed)
    assert len(m.split()) == 25
    assert codec.from_mnemonic(m) == seed
    words = m.split()
    words[3] = 'zoo' if words[3] != 'zoo' else 'abandon'
    with pytest.raises(codec.CodecError):
        codec.from_mnemonic(' '.join(words))


def test_abi_encode_decode():
    assert codec.abi_encode('uint64', 5) == (5).to_bytes(8, 'big')
    assert codec.abi_encode('string', 'hi') == b'\x00\x02hi'
    assert codec.abi_encode('bool', True) == b'\x80'
    assert codec.abi_decode('string', b'\x00\x02hi') == 'hi'
    assert codec.abi_decode('address', b'\0' * 32) == codec.ZERO_ADDRESS
    assert codec.parse_signature('f(uint64,(byte,bool))void') == \
        ('f', ['uint64', '(byte,bool)'], 'void')


def test_app_args():
    assert codec.app_arg(7) == (7).to_bytes(8, 'big')
    assert codec.app_arg('int:7') == (7).to_bytes(8, 'big')
    assert codec.app_arg('str:hi') == b'hi'
    assert codec.app_arg('0x0102') == b'\x01\x02'
    assert codec.app_arg('addr:' + codec.ZERO_ADDRESS) == b'\0' * 32


def test_signed_txn_verifies():
    seed = bytes(32)
    pk = codec.public_key(seed)
    t = {'type': 'pay', 'snd': pk, 'rcv': pk, 'amt': 1, 'fee': 1000, 'fv': 1,
         'lv': 2, 'gh': b'\1' * 32}
    env = codec.sign_txn(seed, t)
    assert codec.verify(pk, b'TX' + codec.msgpack(t), env['sig'])
    assert len(codec.txid(t)) == 52


def test_group_id_stable():
    t1 = {'type': 'pay', 'amt': 1, 'fv': 1}
    t2 = {'type': 'pay', 'amt': 2, 'fv': 1}
    g = codec.group_id([t1, t2])
    assert len(g) == 32 and g == codec.group_id([dict(t1, grp=g), t2])


# ── keystore + gates ─────────────────────────────────────────────

def test_wallet_never_returns_secrets(keystore):
    import wallet
    out = wallet.wallet('generate')
    blob = json.dumps(out)
    seed = json.load(open(keystore / 'keys.json'))['accounts'][out['address']]['seed']
    assert seed not in blob and 'mnemonic' not in blob
    assert oct(os.stat(keystore / 'keys.json').st_mode)[-3:] == '600'
    m = wallet.export_mnemonic()['mnemonic']
    wallet.wallet('forget', address=out['address'])
    again = wallet.wallet('import', mnemonic=m)
    assert again['address'] == out['address']


def test_mainnet_write_needs_confirm():
    import wallet
    from chain import AlgoError
    with pytest.raises(AlgoError) as e:
        wallet.guard('mainnet', False)
    assert e.value.status == 403
    assert wallet.guard('testnet', False) == 'testnet'
    assert wallet.guard('mainnet', 'true') == 'mainnet'


def test_http_write_needs_token():
    import mcp
    import wallet
    from chain import AlgoError
    with pytest.raises(AlgoError) as e:
        mcp.call_tool('algo_wallet', {'op': 'generate'})
    assert e.value.status == 401
    out = mcp.call_tool('algo_wallet', {'op': 'generate', 'token': wallet.token()})
    assert out['address']
    assert mcp.call_tool('algo_wallet', {'op': 'status'})['selected'] == out['address']


def test_mcp_protocol():
    import mcp
    init = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['serverInfo']['name'] == 'algorand'
    tools = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    names = {t['name'] for t in tools['result']['tools']}
    assert {'algo_account', 'algo_simulate', 'algo_app_call'} <= names
    with open(os.path.join(HERE, 'config.json')) as f:
        assert set(json.load(f)['tools']) == names


# ── live chain ───────────────────────────────────────────────────

@online
def test_live_reads():
    from chain import Client
    c = Client('mainnet')
    n = c.network_info()
    assert n['genesis_id'] == 'mainnet-v1.0' and n['round'] > 0
    assert c.asset('USDC')['unit'] == 'USDC'
    assert c.resolve('algo.algo') and codec.is_address(c.resolve('algo.algo'))
    b = c.block()
    assert b['round'] > 0


@online
def test_live_signature_accepted_by_algod():
    """An unfunded key's payment is rejected for overspend — which algod only
    checks after the signature verified. Proves encoding + signing end to end."""
    import wallet
    from chain import AlgoError, Client
    addr = wallet.wallet('generate')['address']
    c = Client('testnet')
    _, seed = wallet.signer()
    t = wallet.pay_txn(c, addr, addr, 0.1)
    with pytest.raises(AlgoError) as e:
        wallet.send_group(c, addr, seed, [t], wait=False)
    assert 'overspend' in str(e.value)


@online
def test_live_simulate_app_create():
    import wallet
    from chain import Client
    c = Client('testnet')
    src = ('#pragma version 10\nbyte 0x151f7c75\nint 42\nitob\nconcat\nlog\nint 1')
    ap = c.compile(src)['program']
    cl = c.compile('#pragma version 10\nint 1')['program']
    t, _ = wallet.appl_txn(c, wallet.FEE_SINK['testnet'], approval=ap, clear=cl)
    grp = c.simulate([t])['txn-groups'][0]
    assert not grp.get('failure-message')
    log = grp['txn-results'][0]['txn-result']['logs'][0]
    assert wallet._abi_return([log], 'uint64') == 42
