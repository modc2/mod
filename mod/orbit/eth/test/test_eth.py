"""
What this module promises, checked.

Four things are worth testing here; the EVM's own behaviour is not one of them:

  * **it is a mod** — config.json and the class agree about what it can do, and
    the services it declares are the ones it ships;
  * **money cannot move by accident** — a non-testnet write is refused without
    an explicit confirm, on every face (engine, REST, MCP), and a locked key
    signs nothing;
  * **one caller cannot reach another's keys** — accounts and the deployment
    index are namespaced by the signing address, and every write route is 401
    without a token;
  * **a deploy really deploys** — against a real local EVM, compile → deploy →
    read → write → decoded logs, because that round trip is the module.

The last group skips when nothing is listening on :8545. A mocked RPC would
only prove this module can talk to a mock.
"""
import json
from pathlib import Path

import pytest

from conftest import ANVIL_ADDRESS, PASSWORD

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / 'config.json').read_text())


# ── it is a mod ──────────────────────────────────────────────────────

def test_config_declares_a_mod():
    assert CONFIG['name'] == 'eth'
    assert CONFIG['anchor'] == 'mod.py'
    assert CONFIG['auth'] == 'mod-protocol'
    assert CONFIG['route'] is True
    assert CONFIG['port'] != CONFIG['app_port']
    assert 'auth' in CONFIG['deps']


def test_config_fns_match_the_class(core):
    """A config that lists fns the class does not have is a lie the CLI tells."""
    for fn in CONFIG['fns']:
        assert hasattr(core.Mod, fn), f'config lists {fn}, the class has no such fn'
    public = [name for name in vars(core.Mod)
              if not name.startswith('_') and callable(getattr(core.Mod, name))]
    for fn in public:
        assert fn in CONFIG['fns'], f'the class exposes {fn}, config does not list it'


def test_the_two_services_exist():
    assert (ROOT / 'api' / 'api.py').is_file()
    assert (ROOT / 'app' / 'server.py').is_file()
    assert (ROOT / 'ecosystem.config.js').is_file()


# ── networks ─────────────────────────────────────────────────────────

def test_every_builtin_network_is_complete():
    import chains
    for name, spec in chains.BUILTIN.items():
        assert spec['rpc'].startswith('http'), name
        assert isinstance(spec['chain_id'], int), name
        assert isinstance(spec['testnet'], bool), name
        # An explorer is what makes a result checkable by a human; only the
        # local node is allowed not to have one.
        assert spec['explorer'] or name == 'local', name


def test_a_network_resolves_by_name_id_or_url():
    import chains
    assert chains.resolve('base')['chain_id'] == 8453
    assert chains.resolve('8453')['name'] == 'base'
    assert chains.resolve('https://example.org/rpc')['rpc'] == 'https://example.org/rpc'
    with pytest.raises(chains.ChainError):
        chains.resolve('not-a-chain')


def test_env_overrides_an_rpc(monkeypatch):
    import chains
    monkeypatch.setenv('ETH_RPC_SEPOLIA', 'https://my-node.example/v2/key')
    spec = chains.resolve('sepolia')
    assert spec['rpc'] == 'https://my-node.example/v2/key'
    assert spec['rpc_source'] == 'env'


def test_mainnet_is_anything_not_marked_testnet():
    import chains
    assert chains.is_mainnet(chains.resolve('mainnet'))
    assert chains.is_mainnet(chains.resolve('base'))
    assert not chains.is_mainnet(chains.resolve('sepolia'))
    assert not chains.is_mainnet(chains.resolve('local'))


# ── keys ─────────────────────────────────────────────────────────────

def test_an_account_is_encrypted_and_scoped(address, state_dir):
    import wallet
    made = wallet.create(address, 'alpha', PASSWORD)
    assert made['address'].startswith('0x')
    blob = json.loads((state_dir / 'accounts' / address / 'alpha.json').read_text())
    assert blob['version'] == 3 and 'ciphertext' in blob['crypto']
    assert PASSWORD not in json.dumps(blob)
    # somebody else's vault does not contain it
    assert [a['name'] for a in wallet.listing('0xsomeone-else')] == []


def test_a_weak_password_is_refused(address):
    import wallet
    with pytest.raises(wallet.WalletError):
        wallet.create(address, 'weak', 'short')


def test_a_locked_account_signs_nothing(address):
    import wallet
    wallet.create(address, 'beta', PASSWORD)
    wallet.lock(address, 'beta')
    with pytest.raises(wallet.WalletError, match='locked'):
        wallet.signer(address, 'beta')
    with pytest.raises(wallet.WalletError, match='wrong password'):
        wallet.signer(address, 'beta', 'not-the-password')
    assert wallet.signer(address, 'beta', PASSWORD).address


def test_an_unlock_expires(address, monkeypatch):
    import time
    import wallet
    wallet.create(address, 'gamma', PASSWORD)
    wallet.unlock(address, 'gamma', PASSWORD, ttl=60)
    assert wallet.signer(address, 'gamma').address
    monkeypatch.setattr(time, 'time', lambda: 10 ** 12)   # far past the ttl
    with pytest.raises(wallet.WalletError, match='locked'):
        wallet.signer(address, 'gamma')


def test_a_signature_recovers_to_the_signer(address):
    import wallet
    made = wallet.create(address, 'delta', PASSWORD)
    signed = wallet.sign_message(address, 'delta', 'gm', PASSWORD)
    assert signed['signature'].startswith('0x')
    assert wallet.verify_message('gm', signed['signature']) == \
        {'valid': True, 'address': made['address']}


def test_an_import_round_trips(address):
    import wallet
    from conftest import ANVIL_KEY
    got = wallet.import_key(address, 'imported', PASSWORD, ANVIL_KEY)
    assert got['address'] == ANVIL_ADDRESS
    assert wallet.export(address, 'imported', PASSWORD)['private_key'] == ANVIL_KEY


# ── amounts ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('given,expected', [
    ('0.1', 10 ** 17),           # a decimal string is the human unit
    (1, 1),                      # a bare integer is wei
    ('1gwei', 10 ** 9),
    ('2.5eth', 25 * 10 ** 17),
    ('100wei', 100),
    ('', 0),
])
def test_amounts_parse_the_way_people_write_them(given, expected):
    import ops
    assert ops.parse_amount(given) == expected


ROUTER_PARAMS = {'name': 'params', 'type': 'tuple', 'components': [
    {'name': 'tokenIn', 'type': 'address'},
    {'name': 'tokenOut', 'type': 'address'},
    {'name': 'fee', 'type': 'uint24'},
    {'name': 'recipient', 'type': 'address'},
    {'name': 'amountIn', 'type': 'uint256'},
    {'name': 'amountOutMinimum', 'type': 'uint256'},
    {'name': 'sqrtPriceLimitX96', 'type': 'uint160'},
]}


def test_a_struct_argument_coerces_field_by_field():
    """Every AMM router takes one — and its numbers arrive as strings."""
    import ops
    given = {'tokenIn': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
             'tokenOut': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
             'fee': '500', 'recipient': '0x' + '11' * 20,
             'amountIn': '123456789012345678901234', 'amountOutMinimum': '0',
             'sqrtPriceLimitX96': 0}
    out = ops._coerce_one('tuple', given, ROUTER_PARAMS)
    assert out[2] == 500 and out[4] == 123456789012345678901234
    assert out[0].startswith('0xC0')                  # checksummed, not lowercase
    # the same struct as a positional array is the same tuple
    assert ops._coerce_one('tuple', [given[c['name']] for c in ROUTER_PARAMS['components']],
                           ROUTER_PARAMS) == out


def test_a_struct_of_the_wrong_width_is_refused_not_silently_padded():
    import ops
    with pytest.raises(ops.OpError):
        ops._coerce_one('tuple', ['0x' + '11' * 20, '2'], ROUTER_PARAMS)


def test_an_array_of_structs_coerces_each_one():
    import ops
    spec = {'type': 'tuple[]', 'components': [{'name': 'a', 'type': 'uint256'},
                                              {'name': 'b', 'type': 'address'}]}
    out = ops._coerce_one('tuple[]', [['1', '0x' + '22' * 20]], spec)
    assert out[0][0] == 1 and out[0][1].startswith('0x22')


def test_from_units_never_goes_through_a_float():
    import ops
    assert ops.from_units(1234567890123456789) == '1.234567890123456789'
    assert ops.from_units(10 ** 18) == '1'
    assert ops.from_units(1) == '0.000000000000000001'


# ── the compiler ─────────────────────────────────────────────────────

@pytest.mark.parametrize('version,constraint,ok', [
    ('0.8.24', '^0.8.20', True),
    ('0.8.19', '^0.8.20', False),
    ('0.9.0', '^0.8.20', False),      # 0.x: ^ does not cross the minor
    ('0.8.24', '>=0.8.0 <0.9.0', True),
    ('0.9.1', '>=0.8.0 <0.9.0', False),
    ('0.8.24', '0.8.24', True),
    ('0.8.24', None, True),
])
def test_pragma_ranges(version, constraint, ok):
    import compiler
    assert compiler.satisfies(version, constraint) is ok


def test_pragma_is_read_off_the_source():
    import compiler
    assert compiler.pragma('pragma solidity ^0.8.20;\ncontract A {}') == '^0.8.20'


def test_every_shipped_template_compiles():
    import catalog
    import compiler
    for name in catalog.names():
        built = compiler.compile_source(catalog.source(name), f'{name}.sol')
        deployable = [c for c in built['contracts'] if c['deployable']]
        assert len(deployable) == 1, f'{name} should declare exactly one contract'
        assert deployable[0]['abi'], name
        # The 24,576-byte limit is EIP-170; a template that cannot be deployed
        # to mainnet is not a template.
        assert deployable[0]['size'] < 24576, name


def test_the_catalog_describes_the_real_contract():
    import catalog
    for row in catalog.listing():
        assert row['contract'], row['name']
        assert row['title'] and row['summary'] and row['use'], row['name']


def test_broken_solidity_fails_with_the_compiler_message():
    import compiler
    with pytest.raises(compiler.CompileError) as caught:
        compiler.compile_source('pragma solidity ^0.8.20; contract X { oops }')
    assert caught.value.errors


# ── the money guard ──────────────────────────────────────────────────

def test_a_mainnet_write_needs_confirm():
    import chains
    import ops
    with pytest.raises(ops.OpError, match='confirm=true'):
        ops.guard(chains.resolve('mainnet'), confirm=False)
    ops.guard(chains.resolve('mainnet'), confirm=True)     # explicit is fine
    ops.guard(chains.resolve('sepolia'), confirm=False)    # testnets are free


def test_the_guard_runs_before_the_key_is_touched(address):
    """A refusal on the wrong chain must not depend on having an account."""
    import ops
    with pytest.raises(ops.OpError, match='not a testnet'):
        ops.send(address, 'no-such-account', ANVIL_ADDRESS, '1', network='mainnet')


# ── the index ────────────────────────────────────────────────────────

def test_deployments_are_private_and_abis_are_not(address):
    import ledger
    ledger.record_deployment(address, name='Thing', network='local',
                             chain_id=31337, address='0xdeadbeef',
                             abi=[{'type': 'function', 'name': 'f'}])
    assert [d['name'] for d in ledger.deployments(address)] == ['Thing']
    assert ledger.deployments('0xsomebody-else') == []
    # …but the interface is public, which is what lets two callers use one token
    assert ledger.abi_for('0xdeadbeef', 31337)['name'] == 'Thing'


# ── the API refuses politely ─────────────────────────────────────────

def test_open_routes_need_no_token(client):
    assert client.get('/health').json()['ok'] is True
    body = client.get('/status').json()
    assert body['service'] == 'eth'
    assert len(body['templates']) == 9
    assert client.get('/networks').json()['networks']
    assert client.get('/endpoints').json()['endpoints']


@pytest.mark.parametrize('method,path,body', [
    ('get', '/me', None),
    ('get', '/accounts', None),
    ('post', '/accounts', {'name': 'x', 'password': 'password123'}),
    ('post', '/deploy', {'account': 'x', 'template': 'counter', 'args': [0]}),
    ('post', '/send', {'account': 'x', 'to': ANVIL_ADDRESS, 'value': '1'}),
    ('get', '/history', None),
    ('get', '/contracts', None),
])
def test_every_key_touching_route_is_401_without_a_token(client, method, path, body):
    response = getattr(client, method)(path, **({'json': body} if body else {}))
    assert response.status_code == 401, f'{method.upper()} {path}'


def test_a_signed_caller_sees_only_their_own(client, token, address):
    response = client.get('/me', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    assert response.json()['address'] == address


def test_the_api_says_no_to_mainnet_without_confirm(client, token):
    response = client.post('/deploy', headers={'Authorization': f'Bearer {token}'},
                           json={'account': 'alpha', 'template': 'counter',
                                 'args': [0], 'network': 'mainnet'})
    assert response.status_code == 400
    assert 'confirm' in response.json()['detail']


def test_compile_is_open_and_returns_an_abi(client):
    response = client.post('/compile', json={'template': 'counter'})
    assert response.status_code == 200
    contract = response.json()['contracts'][0]
    assert contract['name'] == 'Counter'
    assert any(e['type'] == 'constructor' for e in contract['abi'])
    assert 'bytecode' not in contract          # not asked for; not shipped


# ── MCP ──────────────────────────────────────────────────────────────

def test_every_tool_is_well_formed():
    import mcp
    for name, tool in mcp.TOOLS.items():
        assert name.startswith('eth_'), name
        assert len(tool['description']) > 60, f'{name} needs a real description'
        assert tool['inputSchema']['type'] == 'object', name
        assert callable(tool['handler']), name
        assert 'readOnlyHint' in tool['annotations'], name


def test_read_tools_are_open_and_write_tools_are_not():
    import mcp
    for name in ('eth_status', 'eth_balance', 'eth_block', 'eth_read',
                 'eth_compile', 'eth_templates'):
        assert not mcp.needs_auth(name), f'{name} should need no token'
    for name in ('eth_accounts', 'eth_deploy', 'eth_send', 'eth_write',
                 'eth_history'):
        assert mcp.needs_auth(name), f'{name} must need a token'


def test_the_schema_and_the_config_agree():
    import mcp
    doc = mcp.describe()
    assert doc['count'] == len(mcp.TOOLS)
    assert set(CONFIG['mcp']['tools']) <= set(mcp.TOOLS)


def test_an_http_tool_call_without_a_token_is_refused(client):
    response = client.post('/mcp', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'eth_accounts', 'arguments': {}}})
    result = response.json()['result']
    # A refusal is a successful JSON-RPC response carrying isError, per spec.
    assert result['isError'] is True
    assert 'token' in result['content'][0]['text']


def test_an_http_tool_call_reads_without_a_token(client):
    response = client.post('/mcp', json={
        'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'eth_templates', 'arguments': {}}})
    result = response.json()['result']
    assert result['isError'] is False
    assert len(result['structuredContent']['templates']) == 9


def test_initialize_negotiates_a_protocol_version():
    import mcp
    got = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '2024-11-05'}})
    assert got['result']['protocolVersion'] == '2024-11-05'
    fallback = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                           'params': {'protocolVersion': 'nonsense'}})
    assert fallback['result']['protocolVersion'] == mcp.DEFAULT_PROTOCOL_VERSION


# ── against a real chain ─────────────────────────────────────────────

def test_reading_a_live_chain(chain_up):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    import ops
    fees = ops.fees('local')
    assert fees['network'] == 'local'
    assert int(fees['gas_price']) > 0
    assert ops.block('latest', 'local')['number'] >= 0
    assert ops.balance(ANVIL_ADDRESS, 'local')['wei'] != '0'


def test_deploy_read_write_and_logs(chain_up, funded, address):
    """The whole module in one test: compile → deploy → read → write → events."""
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    import catalog
    import ops

    deployed = ops.deploy(address, funded, network='local',
                          source=catalog.source('counter'), args=[7],
                          name='Counter')
    assert deployed['status'] == 'success'
    assert deployed['code_on_chain'] is True
    at = deployed['address']

    assert ops.read(at, 'value', network='local', owner=address)['result'] == 7

    written = ops.write(address, funded, at, 'add', [5], network='local')
    assert written['status'] == 'success'
    assert ops.read(at, 'value', network='local', owner=address)['result'] == 12

    # The ABI came from the deployment, so the event decodes without being told
    events = ops.logs('local', at, from_block=0, owner=address)['decoded']
    assert events and events[-1]['event'] == 'Changed'
    assert events[-1]['args']['value'] == 12

    # …and the interface is discoverable from the address alone
    iface = ops.interface(at, 'local', owner=address)
    assert 'value' in [f['name'] for f in iface['reads']]
    assert 'add' in [f['name'] for f in iface['writes']]


def test_a_reverting_call_is_never_sent(chain_up, funded, address):
    """Gas estimation is the guard: a revert costs nothing here."""
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    import catalog
    import ops
    import wallet
    deployed = ops.deploy(address, funded, network='local',
                          source=catalog.source('counter'), args=[0])
    other = wallet.create(address, f'other-{deployed["nonce"]}', PASSWORD)
    # '0.01', not '1': a bare integer is wei, and one wei buys no gas at all —
    # the call would then fail for lack of funds rather than for the revert
    # this test is about.
    ops.send(address, funded, other['address'], '0.01', network='local')
    wallet.unlock(address, f'other-{deployed["nonce"]}', PASSWORD, 300)
    with pytest.raises(ops.OpError, match='revert'):
        # reset() is deployer-only, and this account is not the deployer
        ops.write(address, f'other-{deployed["nonce"]}', deployed['address'],
                  'reset', [], network='local')


def test_an_erc20_moves_in_whole_tokens(chain_up, funded, address):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    import catalog
    import ops
    token = ops.deploy(address, funded, network='local',
                       source=catalog.source('token'),
                       args=['Test', 'TST', 18, 1000], name='Token')['address']
    info = ops.token_info(token, 'local')
    assert (info['symbol'], info['decimals']) == ('TST', 18)
    assert info['supply'] == '1000'

    to = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8'
    moved = ops.token_transfer(address, funded, token, to, '12.5', network='local')
    assert moved['status'] == 'success'
    assert ops.token_balance(token, to, 'local')['balance'] == '12.5'
