"""solana module tests.

Split deliberately: the offline half pins the cryptography and the wire format,
which must be exactly right or a transfer is either rejected or — worse —
accepted and wrong. The online half checks that the live shapes still parse.

    pytest -q test                 # everything
    SOLANA_OFFLINE=1 pytest -q     # only the parts that need no network
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import chain as C          # noqa: E402
import keys as K           # noqa: E402
import mcp as M            # noqa: E402
import program as P        # noqa: E402
from keys import SolError  # noqa: E402

OFFLINE = os.environ.get('SOLANA_OFFLINE') == '1'
online = pytest.mark.skipif(OFFLINE, reason='SOLANA_OFFLINE=1')

WHALE = '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
EMPTY = '3kUtfMU3p1PAFD1DD2vm6T8HUDijB73bjBnrc9eL5BSa'
MEMO = 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'
WHIRLPOOL = 'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc'

# A minimal anchor IDL in each dialect, so the encoder is pinned against both.
IDL_LEGACY = {
    'version': '0.1.0', 'name': 'demo',
    'instructions': [{'name': 'setValue',
                      'accounts': [{'name': 'state', 'isMut': True,
                                    'isSigner': False},
                                   {'name': 'authority', 'isMut': False,
                                    'isSigner': True}],
                      'args': [{'name': 'value', 'type': 'u64'},
                               {'name': 'label', 'type': 'string'},
                               {'name': 'who', 'type': 'publicKey'}]}],
    'accounts': [{'name': 'State', 'type': {'kind': 'struct', 'fields': [
        {'name': 'value', 'type': 'u64'},
        {'name': 'label', 'type': 'string'},
        {'name': 'flag', 'type': 'bool'},
        {'name': 'maybe', 'type': {'option': 'u32'}},
        {'name': 'list', 'type': {'vec': 'u16'}}]}}],
}
IDL_NEW = {
    'address': MEMO, 'metadata': {'name': 'demo', 'version': '0.1.0',
                                  'spec': '0.1.0'},
    'instructions': [{'name': 'set_value', 'discriminator': [1, 2, 3, 4, 5, 6, 7, 8],
                      'accounts': [{'name': 'state', 'writable': True,
                                    'pda': {'seeds': [
                                        {'kind': 'const', 'value': list(b'state')},
                                        {'kind': 'account', 'path': 'authority'}]}},
                                   {'name': 'authority', 'signer': True},
                                   {'name': 'system_program',
                                    'address': C.SYSTEM}],
                      'args': [{'name': 'value', 'type': 'u64'}]}],
    'types': [], 'accounts': [],
}


# ── base58 ───────────────────────────────────────────────────────

def test_base58_roundtrips_including_leading_zeros():
    for raw in (b'', b'\x00', b'\x00\x00hello', os.urandom(32), bytes(32)):
        assert K.b58decode(K.b58encode(raw)) == raw


def test_base58_leading_zeros_become_ones():
    # The all-zero pubkey is the System Program, and it must encode to exactly
    # 32 '1's — get this wrong and every transaction references the wrong program.
    assert K.b58encode(bytes(32)) == C.SYSTEM
    assert K.b58decode(C.SYSTEM) == bytes(32)


def test_is_address_rejects_non_addresses():
    assert K.is_address(WHALE)
    assert not K.is_address('hello world')      # not base58 at all
    assert not K.is_address('abc')              # base58 but too short
    assert not K.is_address('0OIl')             # the excluded characters
    with pytest.raises(SolError):
        K.need_address('nope')


# ── ed25519 ──────────────────────────────────────────────────────

def test_pure_python_signer_matches_the_fast_backend():
    """The fallback is only safe if it is byte-identical to libsodium."""
    seed = bytes(range(32))
    assert K._pure_pubkey(seed) == K.pubkey_of(seed)
    assert K._pure_sign(seed, b'mod protocol') == K.sign(seed, b'mod protocol')


def test_rfc8032_test_vector():
    seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc4'
                         '4449c5697b326919703bac031cae7f60')
    assert K.pubkey_of(seed).hex() == ('d75a980182b10ab7d54bfed3c964073a'
                                       '0ee172f3daa62325af021a68f707511a')
    assert K.sign(seed, b'').hex() == (
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
        '5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')


def test_on_curve_separates_keys_from_derived_addresses():
    assert K.on_curve(K.pubkey_of(os.urandom(32)))
    ata, bump = K.find_program_address(
        [K.b58decode(WHALE), K.b58decode(C.TOKEN), K.b58decode(USDC)], C.ATA_PROGRAM)
    assert not K.on_curve(K.b58decode(ata))     # a PDA has no private key
    assert 0 <= bump <= 255


def test_associated_token_address_matches_the_known_value():
    """Pinned against what the chain actually reports for this owner+mint."""
    ata, _ = K.find_program_address(
        [K.b58decode(WHALE), K.b58decode(C.TOKEN), K.b58decode(USDC)], C.ATA_PROGRAM)
    assert ata == 'FGETo8T8wMcN2wCjav8VK6eh3dLk63evNDPxzLSJra8B'


# ── keystore ─────────────────────────────────────────────────────

def test_secrets_are_accepted_in_every_shape_they_travel_in(tmp_path):
    seed = os.urandom(32)
    keypair = list(seed + K.pubkey_of(seed))
    path = tmp_path / 'id.json'
    path.write_text(json.dumps(keypair))
    for form in (seed, keypair, json.dumps(keypair), K.b58encode(bytes(keypair)),
                 K.b58encode(seed), seed.hex(), str(path)):
        assert K.parse_secret(form) == seed


def test_an_inconsistent_keypair_is_rejected_rather_than_used():
    seed = os.urandom(32)
    bad = list(seed) + list(os.urandom(32))     # public half does not match
    with pytest.raises(SolError, match='inconsistent'):
        K.parse_secret(bad)


def test_keystore_writes_are_private_and_survive_a_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(K, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'))
    made = K.create('hot')
    assert K.is_address(made['address'])
    assert oct(os.stat(K.KEY_FILE).st_mode)[-3:] == '600'
    assert K.wallets()['default'] == 'hot'
    seed, address = K.signer()
    assert address == made['address']
    assert K.b58encode(K.pubkey_of(seed)) == address
    with pytest.raises(SolError, match='already exists'):
        K.create('hot')
    assert K.remove('hot')['address'] == made['address']
    with pytest.raises(SolError):
        K.signer('hot')


def test_listing_wallets_never_returns_a_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(K, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'))
    K.create('hot')
    assert 'secret' not in json.dumps(K.wallets())


# ── transaction encoding ─────────────────────────────────────────

def test_shortvec_lengths():
    assert C._compact(0) == b'\x00'
    assert C._compact(127) == b'\x7f'
    assert C._compact(128) == b'\x80\x01'
    assert C._compact(256) == b'\x80\x02'


def test_message_orders_accounts_the_way_the_runtime_requires():
    """Signers first, then writables, programs last — the header counts are
    derived from this order, so a wrong order fails signature verification."""
    payer = K.b58encode(K.pubkey_of(os.urandom(32)))
    dest = K.b58encode(K.pubkey_of(os.urandom(32)))
    data = (2).to_bytes(4, 'little') + (1000).to_bytes(8, 'little')
    msg = C._message(payer, [(C.SYSTEM, [(payer, True, True), (dest, False, True)],
                              data)], C.SYSTEM)
    assert msg[0] == 1          # one required signature
    assert msg[1] == 0          # no readonly signers
    assert msg[2] == 1          # one readonly unsigned: the system program
    assert msg[3] == 3          # three accounts
    keys = [msg[4 + 32 * i:4 + 32 * (i + 1)] for i in range(3)]
    assert keys[0] == K.b58decode(payer)                 # fee payer is always first
    assert keys[2] == bytes(32)                          # program id is last
    assert keys[1] == K.b58decode(dest)


def test_message_deduplicates_an_account_used_twice():
    payer = K.b58encode(K.pubkey_of(os.urandom(32)))
    msg = C._message(payer, [(C.SYSTEM, [(payer, True, True)], b'\x00'),
                             (C.SYSTEM, [(payer, True, True)], b'\x01')], C.SYSTEM)
    assert msg[3] == 2          # payer + system program, not four entries


def test_a_signature_over_our_message_verifies():
    seed = os.urandom(32)
    payer = K.b58encode(K.pubkey_of(seed))
    msg = C._message(payer, [(C.SYSTEM, [(payer, True, True)], b'')], C.SYSTEM)
    sig = K.sign(seed, msg)
    assert len(sig) == 64
    try:
        from nacl.signing import VerifyKey
    except ImportError:
        pytest.skip('no independent verifier installed')
    VerifyKey(K.pubkey_of(seed)).verify(msg, sig)   # raises if it does not


# ── signing somebody else's transaction ──────────────────────────
#
# A router hands back a finished transaction with an empty signature slot. This
# is the part that must be exactly right: sign the wrong bytes and the cluster
# rejects it, sign the right bytes into the wrong slot and it rejects it too.


def _built_tx(pubkey, other, signers=1, versioned=True):
    """A transaction shaped the way Jupiter returns one."""
    header = bytes([signers, 0, 1])
    body = header + C._compact(2) + pubkey + other + b'\x00' * 40
    message = (b'\x80' + body) if versioned else body
    return C._compact(signers) + b'\x00' * 64 * signers + message


def test_our_signature_lands_in_our_slot_and_covers_the_message():
    seed = os.urandom(32)
    pubkey = K.pubkey_of(seed)
    other = K.pubkey_of(os.urandom(32))
    raw = _built_tx(pubkey, other)
    signed = C._sign_wire(raw, seed, K.b58encode(pubkey))

    assert len(signed) == len(raw)
    assert signed[65:] == raw[65:]            # the message is untouched
    try:
        from nacl.signing import VerifyKey
    except ImportError:
        pytest.skip('no independent verifier installed')
    VerifyKey(pubkey).verify(raw[65:], signed[1:65])


def test_a_legacy_message_signs_the_same_way():
    seed = os.urandom(32)
    pubkey = K.pubkey_of(seed)
    raw = _built_tx(pubkey, K.pubkey_of(os.urandom(32)), versioned=False)
    assert C._sign_wire(raw, seed, K.b58encode(pubkey))[1:65] != b'\x00' * 64


def test_signing_a_transaction_we_are_not_a_signer_on_is_refused():
    seed = os.urandom(32)
    stranger = K.pubkey_of(os.urandom(32))
    raw = _built_tx(K.pubkey_of(os.urandom(32)), stranger)
    with pytest.raises(SolError, match='not a signer'):
        C._sign_wire(raw, seed, K.b58encode(stranger))


def test_a_transaction_needing_a_second_signer_is_refused_not_half_signed():
    seed = os.urandom(32)
    pubkey = K.pubkey_of(seed)
    raw = _built_tx(pubkey, K.pubkey_of(os.urandom(32)), signers=2)
    with pytest.raises(SolError, match='second signer'):
        C._sign_wire(raw, seed, K.b58encode(pubkey))


def test_a_truncated_transaction_is_an_error_not_a_slice():
    with pytest.raises(SolError):
        C._sign_wire(C._compact(1) + b'\x00' * 20, os.urandom(32), 'x')


def test_swap_refuses_a_network_jupiter_cannot_route(monkeypatch, tmp_path):
    """Devnet has no Jupiter liquidity; failing early beats failing at send."""
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'), raising=False)
    with pytest.raises(SolError, match='mainnet'):
        C.Client(network='devnet').swap('SOL', USDC, 1)


def test_swap_with_no_price_asks_instead_of_guessing(monkeypatch):
    """A throttled price API must not read as a small trade."""
    c = C.Client()
    monkeypatch.setattr(c, '_route', lambda *a, **k: (
        {'outAmount': '1000000', 'otherAmountThreshold': '990000',
         'priceImpactPct': '0.01', 'routePlan': []},
        C.WSOL, USDC, {}, 9, 6))
    monkeypatch.setattr(c, 'prices', lambda mints: {})
    monkeypatch.setattr(C, 'signer', lambda w, s: (b'\x00' * 32, WHALE))
    out = c.swap('SOL', USDC, 1)
    assert out['sent'] is False and out['needs_confirm'] is True
    assert 'price API' in out['reason']


def test_swap_is_write_gated_like_a_transfer():
    import api
    assert 'sol_swap' in api.WRITE_TOOLS and '/swap' in api.WRITE_ROUTES


# ── plumbing ─────────────────────────────────────────────────────

def test_unknown_network_is_refused_before_any_request():
    with pytest.raises(SolError, match='unknown network'):
        C.Client(network='mainet')


def test_client_accepts_a_url_as_the_network():
    c = C.Client(network='https://example.invalid')
    assert c.rpc == 'https://example.invalid' and c.network == 'custom'


def test_sol_conversion():
    assert C.sol(C.LAMPORTS) == 1
    assert C.sol(0) == 0 and C.sol(None) == 0


def test_major_symbols_are_pinned_not_searched():
    """Anyone can mint a token called USDC; these three must never be looked up."""
    assert C.MAJORS['USDC'] == USDC
    assert C.MAJORS['SOL'] == C.WSOL
    c = C.Client()
    c.jup = lambda *a, **k: pytest.fail('a pinned symbol hit the network')
    assert c.resolve('usdc') == USDC
    assert c.resolve(WHALE) == WHALE            # an address resolves to itself


def test_mainnet_has_no_faucet():
    with pytest.raises(SolError, match='no mainnet faucet'):
        C.Client('mainnet').airdrop(WHALE)


# ── mcp registry ─────────────────────────────────────────────────

def test_every_tool_is_well_formed_and_reachable():
    assert len(M.TOOLS) == 22
    for name, tool in M.TOOLS.items():
        assert name.startswith('sol_')
        assert len(tool['description']) > 80, name
        assert tool['inputSchema']['type'] == 'object'
        assert callable(tool['handler'])
        for req in tool['inputSchema'].get('required', []):
            assert req in tool['inputSchema']['properties'], (name, req)


def test_tools_list_matches_the_declared_config():
    with open(os.path.join(HERE, 'config.json')) as f:
        cfg = json.load(f)
    assert sorted(cfg['tools']) == sorted(M.TOOLS)
    assert cfg['port'] == 50710


def test_initialize_negotiates_the_protocol_version():
    r = M.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '2025-06-18'}})
    assert r['result']['protocolVersion'] == '2025-06-18'
    assert r['result']['serverInfo']['name'] == 'solana'
    assert 'sol_account' in r['result']['instructions']
    # An unknown version falls back rather than echoing something we do not speak.
    r = M.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '1999-01-01'}})
    assert r['result']['protocolVersion'] == M.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    assert M.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_unknown_method_and_malformed_body_are_json_rpc_errors():
    assert M.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'nope'})['error']['code'] == -32601
    assert M.handle('not a dict')['error']['code'] == -32600


def test_a_missing_required_argument_is_an_error_not_a_crash():
    r = M.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                  'params': {'name': 'sol_account', 'arguments': {}}})
    assert r['result']['isError'] is True
    assert 'address' in r['result']['content'][0]['text']


def test_tool_errors_come_back_as_tool_errors_not_transport_errors():
    r = M.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                  'params': {'name': 'sol_account',
                             'arguments': {'address': 'not-an-address'}}})
    assert r['result']['isError'] is True
    assert 'error' not in r          # the JSON-RPC envelope itself succeeded


# ── borsh and IDLs ───────────────────────────────────────────────

def test_borsh_encodes_every_primitive_the_way_anchor_reads_it():
    assert P.encode('u8', 255) == b'\xff'
    assert P.encode('u64', 1) == b'\x01' + bytes(7)
    assert P.encode('i64', -1) == b'\xff' * 8
    assert P.encode('u64', '18446744073709551615') == b'\xff' * 8
    assert P.encode('bool', True) == b'\x01'
    assert P.encode('string', 'hi') == b'\x02\x00\x00\x00hi'
    assert P.encode('pubkey', C.SYSTEM) == bytes(32)
    assert P.encode({'option': 'u32'}, None) == b'\x00'
    assert P.encode({'option': 'u32'}, 7) == b'\x01\x07\x00\x00\x00'
    assert P.encode({'vec': 'u8'}, [1, 2]) == b'\x02\x00\x00\x00\x01\x02'
    assert P.encode({'array': ['u8', 3]}, [1, 2, 3]) == b'\x01\x02\x03'


def test_a_fixed_array_of_the_wrong_length_is_refused():
    with pytest.raises(SolError, match='fixed array of 3'):
        P.encode({'array': ['u8', 3]}, [1, 2])


def test_borsh_roundtrips_a_whole_struct():
    value = {'value': 42, 'label': 'hello', 'flag': True, 'maybe': None,
             'list': [1, 2, 3]}
    types = P.idl_types(IDL_LEGACY)
    blob = P._encode_defined(IDL_LEGACY['accounts'][0], value, types)
    back = P._decode_defined(IDL_LEGACY['accounts'][0], P._Reader(blob), types)
    assert back == value


def test_decoding_past_the_end_says_so_instead_of_returning_junk():
    with pytest.raises(SolError, match='ran out mid-field'):
        P.decode('u64', P._Reader(b'\x01\x02'))


def test_anchor_discriminators_match_the_known_hashes():
    # These are the bytes anchor puts at the front of every call and account,
    # and they are the difference between calling a program and being ignored.
    assert P.discriminator('global', 'initialize').hex() == 'afaf6d1f0d989bed'
    assert P.discriminator('account', 'State').hex() == \
        P.discriminator('account', 'State').hex()
    assert len(P.discriminator('global', 'set_value')) == 8


def test_instruction_data_is_discriminator_then_args_in_idl_order():
    blob, ix = P.instruction_data(IDL_LEGACY, 'setValue',
                                  {'value': 1, 'label': 'x', 'who': C.SYSTEM})
    assert ix['name'] == 'setValue'
    assert blob[:8] == P.discriminator('global', 'set_value')
    assert blob[8:16] == b'\x01' + bytes(7)
    assert blob[16:21] == b'\x01\x00\x00\x00x'
    assert blob[21:] == bytes(32)


def test_an_idl_supplied_discriminator_wins_over_the_hash():
    blob, _ = P.instruction_data(IDL_NEW, 'set_value', {'value': 0})
    assert blob[:8] == bytes([1, 2, 3, 4, 5, 6, 7, 8])


def test_a_missing_argument_names_itself():
    with pytest.raises(SolError, match='needs the argument label'):
        P.instruction_data(IDL_LEGACY, 'setValue', {'value': 1})


def test_an_unknown_instruction_lists_the_real_ones():
    with pytest.raises(SolError, match='setValue'):
        P.instruction_data(IDL_LEGACY, 'nope', {})


def test_accounts_come_from_the_idl_in_order_with_their_flags():
    metas, names, hows, resolved = P._idl_accounts(
        IDL_NEW['instructions'][0], {}, WHALE, MEMO, {'value': 1}, IDL_NEW)
    assert names == ['state', 'authority', 'system_program']
    assert [m[1] for m in metas] == [False, True, False]      # only the signer
    assert [m[2] for m in metas] == [True, False, False]      # only the state
    assert metas[1][0] == WHALE and metas[2][0] == C.SYSTEM
    assert hows == ['pda', 'wallet', 'idl']
    # the PDA the IDL declares, derived from its own seeds
    assert metas[0][0] == P.find_program_address(
        [b'state', K.b58decode(WHALE)], MEMO)[0]


def test_an_account_the_idl_cannot_derive_is_asked_for_by_name():
    ix = {'name': 'go', 'accounts': [{'name': 'vault', 'writable': True}],
          'args': []}
    with pytest.raises(SolError, match='needs vault'):
        P._idl_accounts(ix, {}, None, MEMO, {}, IDL_NEW)


def test_the_wallet_only_fills_accounts_whose_name_means_the_caller():
    ix = {'name': 'go', 'args': [],
          'accounts': [{'name': 'newAccount', 'signer': True}]}
    with pytest.raises(SolError, match='newAccount'):
        P._idl_accounts(ix, {}, WHALE, MEMO, {}, IDL_NEW)


def test_an_anchor_error_code_becomes_its_name():
    idl = {'errors': [{'code': 6000, 'name': 'TooSmall', 'msg': 'the amount is tiny'}]}
    said = P._explain({'InstructionError': [0, {'Custom': 6000}]}, idl)
    assert 'TooSmall' in said and 'tiny' in said


# ── instruction data the caller writes by hand ───────────────────

def test_raw_data_is_read_in_every_form_a_caller_might_send():
    assert P._bytes_of('0x00ff') == b'\x00\xff'
    assert P._bytes_of('00ff') == b'\x00\xff'
    assert P._bytes_of([0, 255]) == b'\x00\xff'
    assert P._bytes_of('text:hello') == b'hello'
    assert P._bytes_of({'text': 'hello'}) == b'hello'
    assert P._bytes_of('base64:aGk=') == b'hi'
    # A memo is not base58 by accident: text: is the way to say so.
    with pytest.raises(SolError, match='text:'):
        P._bytes_of('hello world')


def test_seeds_are_read_as_the_program_wrote_them():
    assert P.seed_bytes('vault') == b'vault'
    assert P.seed_bytes(WHALE) == K.b58decode(WHALE)      # address-shaped
    assert P.seed_bytes({'u64': 1}) == b'\x01' + bytes(7)
    assert P.seed_bytes({'string': WHALE}) == WHALE.encode()   # forced to text


def test_a_derived_address_is_off_the_curve_and_reports_its_seeds():
    out = P.pda(['vault', {'u64': 7}], MEMO)
    assert not K.on_curve(K.b58decode(out['address']))
    assert [s['as'] for s in out['seeds']] == ['text', 'bytes']
    assert 0 <= out['bump'] <= 255


def test_a_seed_over_32_bytes_is_refused_with_its_index():
    with pytest.raises(SolError, match='seed 0'):
        P.pda(['x' * 33], MEMO)


# ── the loader ───────────────────────────────────────────────────

def test_write_uses_bincode_lengths_not_borsh_ones():
    # The one place on Solana where a Vec length is eight bytes. Get it wrong
    # and every chunk comes back InvalidInstructionData.
    _program, accounts, data = P.write_ix(WHALE, WHALE, 900, b'abc')
    assert data == (b'\x01\x00\x00\x00' + (900).to_bytes(4, 'little') +
                    (3).to_bytes(8, 'little') + b'abc')
    assert accounts[0][2] is True and accounts[1][1] is True   # buffer w, auth s


def test_the_deploy_instruction_has_the_eight_accounts_in_order():
    _p, accounts, data = P.deploy_ix(WHALE, MEMO, EMPTY, WHALE, 1000)
    assert data == b'\x02\x00\x00\x00' + (1000).to_bytes(8, 'little')
    assert [a[0] for a in accounts] == [
        WHALE, P.programdata_of(MEMO), MEMO, EMPTY, P.RENT, P.CLOCK, C.SYSTEM,
        WHALE]
    assert accounts[4][1] is False and accounts[7][1] is True


def test_revoking_the_authority_is_the_absence_of_an_account():
    _p, keep, _d = P.set_authority_ix(MEMO, WHALE, EMPTY)
    _p, drop, _d = P.set_authority_ix(MEMO, WHALE, None)
    assert len(keep) == 3 and len(drop) == 2


def test_programdata_is_a_pda_of_the_program_id():
    assert P.programdata_of(MEMO) == P.find_program_address(
        [K.b58decode(MEMO)], P.UPGRADEABLE)[0]


def test_an_irreversible_authority_change_asks_first():
    plan = P.authority.__doc__
    assert 'irreversible' in plan


def test_deploying_nothing_says_what_the_sources_are():
    with pytest.raises(SolError, match='clone='):
        P.elf_source(None)


def test_an_unknown_job_is_a_404_not_a_crash():
    with pytest.raises(SolError) as e:
        P.job('deploy-nope')
    assert e.value.status == 404


# ── ELF ──────────────────────────────────────────────────────────

def _fake_elf(machine=247):
    head = bytearray(64)
    head[0:4] = b'\x7fELF'
    head[4], head[5] = 2, 1                       # 64-bit, little endian
    head[16:18] = (3).to_bytes(2, 'little')       # DYN
    head[18:20] = machine.to_bytes(2, 'little')
    head[24:32] = (8).to_bytes(8, 'little')       # entry
    return bytes(head)


def test_a_non_elf_is_rejected_before_anything_is_paid_for():
    out = P.elf_info(b'not an elf at all')
    assert out['valid'] is False and 'cargo build-sbf' in out['problem']


def test_both_solana_machine_numbers_are_recognised():
    assert P.elf_info(_fake_elf(247))['machine_name'] == 'BPF'
    assert P.elf_info(_fake_elf(263))['machine_name'] == 'SBPF'
    wrong = P.elf_info(_fake_elf(62))             # x86-64
    assert wrong['valid'] is False and '247' in wrong['problem']


def test_elf_length_ignores_the_deploy_padding_without_eating_the_headers():
    raw = _fake_elf()
    assert P.elf_length(raw) == 64
    assert P.elf_length(raw + bytes(5000)) == 64   # padding is not the program


def test_strings_are_deduplicated_and_capped():
    blob = b'\x00'.join([b'hello there'] * 30 + [b'short', b'a longer literal'])
    out = P._strings(blob)
    assert out[0] == 'hello there' and out.count('hello there') == 1
    assert 'short' not in out                      # under the minimum length


# ── the write gate covers the new routes ─────────────────────────

def test_simulating_is_public_but_sending_is_not():
    import api
    assert api.CONDITIONAL['sol_invoke']({'send': True})
    assert not api.CONDITIONAL['sol_invoke']({})
    assert api.CONDITIONAL['sol_idl']({'action': 'set'})
    assert not api.CONDITIONAL['sol_idl']({'action': 'get'})
    assert 'sol_deploy' in api.WRITE_TOOLS and 'sol_authority' in api.WRITE_TOOLS


def test_sending_a_call_over_mcp_is_gated_too():
    # /mcp reaches every tool by name, so a gate that only covers the REST
    # routes is not a gate. Simulation stays open; signing does not.
    import api
    body = {'method': 'tools/call',
            'params': {'name': 'sol_invoke', 'arguments': {'program': MEMO,
                                                           'send': True}}}
    args = body['params']['arguments']
    assert api.CONDITIONAL['sol_invoke'](args)
    assert not api.CONDITIONAL['sol_invoke']({'program': MEMO})


def test_deploying_from_off_box_needs_the_token():
    import api
    with pytest.raises(SolError) as e:
        api.route('POST', '/deploy', '', {'clone': 'memo'}, '8.8.8.8', {})
    assert e.value.status in (401, 403)


def test_reading_a_program_needs_nothing():
    import api
    assert '/program' not in api.WRITE_ROUTES


# ── live ─────────────────────────────────────────────────────────

@online
def test_network_status_is_live_and_sane():
    s = C.Client().status()
    assert s['healthy'] and s['slot'] > 300_000_000
    assert 0 < s['sol_price_usd'] < 100_000
    assert 0 <= s['epoch_progress_pct'] <= 100


@online
def test_account_identifies_each_kind_it_claims_to():
    c = C.Client()
    assert c.account(WHALE)['kind'] == 'wallet'
    assert c.account(USDC)['kind'] == 'mint'
    assert c.account(C.TOKEN)['kind'] == 'program'
    unused = c.account(EMPTY)
    assert unused['exists'] is False and unused['kind'] == 'unused'


@online
def test_token_reports_supply_authorities_and_risk():
    t = C.Client().token(USDC)
    assert t['symbol'] == 'USDC' and t['decimals'] == 6
    assert t['supply'] > 1_000_000
    # USDC keeps both authorities live; the module must say so out loud.
    assert t['mint_authority'] and t['freeze_authority']
    assert len(t['risk']) >= 2


@online
def test_a_mint_is_refused_by_the_wallet_shaped_tools():
    with pytest.raises(SolError, match='not a token mint'):
        C.Client().token(WHALE)


@online
def test_portfolio_totals_add_up():
    p = C.Client().portfolio(WHALE, min_usd=1)
    assert p['sol'] > 0 and p['total_usd'] > 0
    assert abs(p['total_usd'] - (p['sol_usd'] + p['token_usd'])) < 1
    assert p['tokens'] == sorted(p['tokens'], key=lambda t: -(t['value_usd'] or 0))


@online
def test_history_and_tx_agree_on_the_same_transaction():
    c = C.Client()
    h = c.history(WHALE, limit=3)
    assert h['transactions']
    sig = h['transactions'][0]['signature']
    t = c.tx(sig)
    assert t['signature'] == sig
    assert t['slot'] == h['transactions'][0]['slot']
    assert t['fee_sol'] >= 0 and t['fee_payer']


@online
def test_a_bad_signature_is_a_404_not_a_crash():
    with pytest.raises(SolError):
        C.Client().tx('x' * 88)


@online
def test_quote_prices_the_size_not_the_mid():
    q = C.Client().quote('SOL', 'USDC', 1)
    assert q['sell']['mint'] == C.WSOL and q['buy']['mint'] == USDC
    assert q['buy']['amount'] > 0 and q['route']
    assert q['worst_case_out'] <= q['buy']['amount']


@online
def test_validators_and_the_nakamoto_coefficient():
    v = C.Client().validators(limit=5)
    assert v['validators'] > 100 and v['nakamoto_coefficient'] >= 1
    assert len(v['top']) == 5
    assert v['top'] == sorted(v['top'], key=lambda r: -r['stake_sol'])


@online
def test_a_transfer_from_an_empty_wallet_fails_before_it_signs(tmp_path, monkeypatch):
    monkeypatch.setattr(K, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'))
    K.create('broke')
    with pytest.raises(SolError, match='not enough'):
        C.Client('devnet').transfer(EMPTY, 1, wallet='broke')


@online
def test_the_value_guard_holds_a_large_transfer_and_explains_itself(tmp_path,
                                                                    monkeypatch):
    """The guard must fire before the balance check, so an over-guard amount is
    reported as needing confirmation rather than as insufficient funds."""
    monkeypatch.setattr(K, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'))
    monkeypatch.setattr(C, 'SPEND_USD', 0.0001)
    K.create('hot')
    c = C.Client('devnet')
    monkeypatch.setattr(c, '_plan_sol',
                        lambda *a, **k: {'kind': 'sol', '_instructions': []})
    out = c.transfer(EMPTY, 1, wallet='hot')
    assert out['sent'] is False and out['needs_confirm'] is True
    assert 'confirm=true' in out['reason']


@online
def test_the_signed_bytes_are_accepted_by_a_real_node(tmp_path, monkeypatch):
    """The end-to-end proof we can run without a funded wallet: a devnet node
    verifies our signature over our serialized message. It then fails on the
    empty balance, which is the expected next objection, not an encoding one."""
    import base64
    monkeypatch.setattr(K, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(K, 'KEY_FILE', str(tmp_path / 'keys.json'))
    made = K.create('hot')
    seed, sender = K.signer('hot')
    c = C.Client('devnet')
    blockhash = c.call('getLatestBlockhash')['value']['blockhash']
    data = (2).to_bytes(4, 'little') + (1000).to_bytes(8, 'little')
    msg = C._message(sender, [(C.SYSTEM, [(sender, True, True), (EMPTY, False, True)],
                               data)], blockhash)
    wire = bytes([1]) + K.sign(seed, msg) + msg
    out = c.call('simulateTransaction', [base64.b64encode(wire).decode(),
                                         {'encoding': 'base64', 'sigVerify': True,
                                          'commitment': 'processed'}])['value']
    assert made['address'] == sender
    assert out['err'] == 'AccountNotFound'      # not SignatureFailure


@online
def test_a_deployed_program_reports_its_loader_and_what_it_imports():
    p = P.program_info(C.Client(), MEMO)
    assert p['exists'] and p['executable']
    assert p['loader'] == P.LOADER2 and p['immutable'] is True
    assert p['elf']['valid'] and p['elf']['code_bytes'] > 1000
    assert 'sol_log_' in p['elf']['syscalls']


@online
def test_an_upgradeable_program_names_who_can_replace_it():
    p = P.program_info(C.Client(), WHIRLPOOL, strings=False, idl=False)
    assert p['loader'] == P.UPGRADEABLE
    assert p['programdata'] == P.programdata_of(WHIRLPOOL)
    assert p['upgrade_authority'] and p['last_deployed_slot'] > 0
    assert p['headroom_bytes'] >= 0


@online
def test_an_anchor_idl_is_fetched_and_decompressed():
    idl, source = P.load_idl(C.Client(), WHIRLPOOL)
    assert source == 'chain'
    summary = P.idl_summary(idl)
    assert len(summary['instructions']) > 30
    assert any(i['name'].startswith('swap') for i in summary['instructions'])
    # and the interface is usable, not just readable
    blob, _ix = P.instruction_data(idl, 'initialize_config', {
        'fee_authority': WHALE, 'collect_protocol_fees_authority': WHALE,
        'reward_emissions_super_authority': WHALE,
        'default_protocol_fee_rate': 300})
    assert len(blob) == 8 + 32 * 3 + 2


@online
def test_a_program_with_no_idl_says_so_rather_than_guessing():
    p = P.program_info(C.Client(), MEMO)
    assert p['idl'] is None and 'no IDL' in p['idl_note']


@online
def test_a_call_is_simulated_against_the_real_cluster_before_it_is_signed():
    out = P.invoke(C.Client(), 'memo', data='text:mod solana test',
                   accounts=[f's:{WHALE}'], payer=WHALE)
    assert out['sent'] is False
    assert out['simulation']['ok'] is True
    assert any('mod solana test' in line for line in out['simulation']['logs'])
    assert out['simulation']['units'] > 0


@online
def test_a_call_that_would_fail_comes_back_as_the_reason_it_fails():
    out = P.invoke(C.Client(), C.TOKEN, data='0xff', accounts=[f'w:{WHALE}'],
                   payer=WHALE)
    assert out['simulation']['ok'] is False
    assert out['simulation']['reason']
    assert out['sent'] is False and 'would have failed' in out['note']


@online
def test_the_program_tool_answers_the_same_as_the_function():
    a = M.call_tool('sol_program', {'program': 'memo', 'strings': False,
                                    'idl': False})
    b = P.program_info(C.Client(), MEMO, strings=False, idl=False)
    assert a['program'] == b['program'] == MEMO
    assert a['loader'] == b['loader']
