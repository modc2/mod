"""near tests — offline units always; chain reads unless NEAR_OFFLINE=1."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.append(HERE)

import chain                                                  # noqa: E402
import mcp                                                    # noqa: E402

OFFLINE = os.environ.get('NEAR_OFFLINE', '') not in ('', '0')
needs_chain = pytest.mark.skipif(OFFLINE, reason='NEAR_OFFLINE=1')


# ── offline ──────────────────────────────────────────────────────

def test_account_id_grammar():
    assert chain.is_account_id('root.near')
    assert chain.is_account_id('usdt.tether-token.near')
    assert chain.is_account_id('a' * 64)          # implicit
    assert not chain.is_account_id('x')           # too short
    assert not chain.is_account_id('Bad.Near')    # no uppercase
    assert not chain.is_account_id('a..b')        # empty label


def test_near_conversion():
    assert chain.near('1' + '0' * 24) == 1.0
    assert chain.near(None) == 0.0
    assert chain.near('junk') == 0.0


def test_wasm_export_parser():
    # A minimal module exporting one function "hello":
    # (module (func (export "hello")))
    wasm = bytes.fromhex(
        '0061736d01000000010401600000030201000705010568656c6c6f00000a040102000b')
    assert chain._wasm_exports(wasm) == ['hello']
    assert chain._wasm_exports(b'not wasm') == []


def test_decode_action_shapes():
    a = chain._decode_action({'Transfer': {'deposit': '2' + '0' * 24}})
    assert a == {'type': 'Transfer', 'near': 2.0}
    b = chain._decode_action({'FunctionCall': {
        'method_name': 'ft_transfer', 'deposit': '1', 'gas': 30 * 10 ** 12,
        'args': 'eyJhIjoxfQ=='}})
    assert b['method'] == 'ft_transfer' and b['tgas'] == 30.0 and b['args'] == {'a': 1}


def test_mcp_protocol_offline():
    init = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['serverInfo']['name'] == 'near'
    assert init['result']['protocolVersion'] == '2025-06-18'
    tools = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    names = {t['name'] for t in tools['result']['tools']}
    assert names == set(mcp.TOOLS)
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    bad = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'})
    assert bad['error']['code'] == -32601


def test_tool_missing_arg_is_an_error():
    with pytest.raises(chain.NearError):
        mcp.call_tool('near_account', {})
    with pytest.raises(chain.NearError):
        mcp.call_tool('no_such_tool', {})


def test_route_table_covers_tools():
    import api
    assert set(api.ROUTE_TOOLS.values()) == set(mcp.TOOLS)


# ── chain ────────────────────────────────────────────────────────

@needs_chain
def test_account_read():
    out = mcp.call_tool('near_account', {'account_id': 'root.near'})
    assert out['balance']['total_near'] > 0
    assert out['account_id'] == 'root.near'


@needs_chain
def test_contract_methods_from_wasm():
    out = mcp.call_tool('near_contract', {'account_id': 'wrap.near'})
    assert out['is_contract'] and 'ft_balance_of' in out['methods']


@needs_chain
def test_view_call():
    out = mcp.call_tool('near_view', {'contract': 'wrap.near',
                                      'method': 'ft_metadata'})
    assert out['result']['symbol'] == 'wNEAR'


@needs_chain
def test_network_and_validators():
    n = mcp.call_tool('near_network', {})
    assert n['chain_id'] == 'mainnet' and n['block_height'] > 100_000_000
    v = mcp.call_tool('near_validators', {'limit': 5})
    assert v['nakamoto_coefficient'] >= 1 and len(v['validators']) == 5


@needs_chain
def test_mcp_tool_call_end_to_end():
    resp = mcp.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call',
                       'params': {'name': 'near_block', 'arguments': {}}})
    body = json.loads(resp['result']['content'][0]['text'])
    assert not resp['result']['isError'] and body['height'] > 0


# ── wallet: crypto, borsh, gates — all offline ───────────────────

import wallet                                                 # noqa: E402


def test_base58_roundtrip():
    for raw in (b'\0\0abc', b'\xff' * 32, b'', b'\x01'):
        assert wallet.b58decode(wallet.b58encode(raw)) == raw
    with pytest.raises(chain.NearError):
        wallet.b58decode('0OIl')                 # not in the alphabet


def test_ed25519_rfc8032_vector():
    seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc4'
                         '4449c5697b326919703bac031cae7f60')
    pub = 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
    assert wallet.ed25519_public(seed).hex() == pub


def test_ed25519_pure_matches_nacl():
    if not wallet._NaclKey:
        pytest.skip('pynacl not installed — nothing to compare against')
    import os as _os
    seed, msg = _os.urandom(32), _os.urandom(80)
    nacl_pub = wallet.ed25519_public(seed)
    nacl_sig = wallet.ed25519_sign(seed, msg)
    held, wallet._NaclKey = wallet._NaclKey, None
    try:
        assert wallet.ed25519_public(seed) == nacl_pub
        assert wallet.ed25519_sign(seed, msg) == nacl_sig
    finally:
        wallet._NaclKey = held


def test_key_string_roundtrip():
    seed = b'\x07' * 32
    secret = wallet.secret_to_str(seed)
    assert secret.startswith('ed25519:')
    assert wallet.seed_from_secret(secret) == seed
    assert wallet.pub_from_str(wallet.key_to_str(wallet.ed25519_public(seed))) \
        == wallet.ed25519_public(seed)


def test_yocto_is_exact():
    assert wallet._yocto(1) == 10 ** 24
    assert wallet._yocto(0.1) == 10 ** 23          # no binary-float noise
    assert wallet._yocto('2.5') == 25 * 10 ** 23


def test_borsh_transfer_layout():
    tx = wallet.serialize_tx('a.testnet', b'\x11' * 32, 7, 'b.testnet',
                             b'\x22' * 32, [wallet.a_transfer(10 ** 24)])
    assert tx[:4] == (9).to_bytes(4, 'little') and tx[4:13] == b'a.testnet'
    assert tx[13] == 0 and tx[14:46] == b'\x11' * 32         # ed25519 pubkey
    assert int.from_bytes(tx[46:54], 'little') == 7          # nonce
    assert tx[-17] == 3                                      # Transfer tag
    assert int.from_bytes(tx[-16:], 'little') == 10 ** 24
    signed = wallet.sign_tx(b'\x01' * 32, tx)
    assert len(signed) == len(tx) + 65                       # tag + 64-byte sig


def test_add_key_permission_borsh():
    full = wallet.a_add_key(b'\x11' * 32)
    assert full[0] == 5 and full[-1] == 1                    # FullAccess
    scoped = wallet.a_add_key(b'\x11' * 32, receiver='app.testnet',
                              methods=['set_status'],
                              allowance_yocto=10 ** 24)
    assert scoped[0] == 5 and b'app.testnet' in scoped and b'set_status' in scoped


def test_mainnet_guard():
    with pytest.raises(chain.NearError):
        wallet.guard('mainnet', False)
    wallet.guard('mainnet', True)
    wallet.guard('testnet', False)


def test_keystore_and_token_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(wallet, 'MOD_DIR', str(tmp_path))
    monkeypatch.setattr(wallet, 'KEYS_FILE', str(tmp_path / 'keys.json'))
    monkeypatch.setattr(wallet, 'TOKEN_FILE', str(tmp_path / 'token'))
    # no wallet yet → remote writes refuse outright
    with pytest.raises(chain.NearError):
        wallet.check_token('whatever')
    out = wallet.wallet('generate', network='testnet')
    assert out['ok'] and len(out['account_id']) == 64        # implicit = hex pub
    status = wallet.wallet_status()
    assert status['count'] == 1
    assert 'secret_key' not in json.dumps(status)            # never leaks
    token = wallet.read_token()
    assert token and wallet.check_token(token) is None
    with pytest.raises(chain.NearError):
        wallet.check_token('wrong')
    # the same key round-trips through import
    with open(wallet.KEYS_FILE) as f:
        secret = list(json.load(f)['accounts'].values())[0]['secret_key']
    re = wallet.wallet('import', account_id='again.testnet', secret_key=secret)
    assert re['public_key'] == out['public_key']


def test_remote_write_needs_token(tmp_path, monkeypatch):
    monkeypatch.setattr(wallet, 'TOKEN_FILE', str(tmp_path / 'token'))
    wallet.ensure_token()
    with pytest.raises(chain.NearError) as e:
        mcp.call_tool('near_send', {'to': 'x.testnet', 'amount_near': 1})
    assert e.value.status == 403
    # read-shaped wallet ops stay open
    assert 'accounts' in mcp.call_tool('near_wallet', {})


def test_wasm_loader_rejects_junk():
    with pytest.raises(chain.NearError):
        wallet._load_wasm(wasm='bm90IHdhc20=')               # b64("not wasm")
    with pytest.raises(chain.NearError):
        wallet._load_wasm()
