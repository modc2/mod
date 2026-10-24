"""Offline tests: compiler determinism, rpc dispatch, runtime guards, registry."""
import json
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from mod2mcp import compiler, rpc  # noqa: E402


class Toy:
    """A stand-in Mod: one view, one write, one denied, one hidden."""

    def get_price(self, coin: str, window: int = 24) -> dict:
        """Price of a coin over a window."""
        return {'coin': coin, 'window': window, 'price': 1.0}

    def transfer(self, to: str, amount: float) -> dict:
        return {'to': to, 'amount': amount}

    def serve(self):          # lifecycle — never in the ABI
        raise RuntimeError('should not be exposed')

    def _hidden(self):
        pass


def manifest(**kw):
    return compiler.compile_contract('toy', Toy(), **kw)


def test_abi_shape_and_deny():
    m = manifest()
    names = [f['name'] for f in m['functions']]
    assert names == ['get_price', 'transfer']
    assert 'serve' not in names and '_hidden' not in names
    assert m['address'].startswith('0x') and len(m['address']) == 42


def test_mutability_and_annotations():
    m = manifest()
    by = {f['name']: f for f in m['functions']}
    assert by['get_price']['mutability'] == 'view'
    assert by['transfer']['mutability'] == 'write'
    tools = {t['name']: t for t in m['tools']}
    assert tools['toy_get_price']['annotations']['readOnlyHint'] is True
    assert tools['toy_transfer']['annotations']['readOnlyHint'] is False


def test_schema_types_required_defaults():
    m = manifest()
    schema = {t['fn']: t['inputSchema'] for t in m['tools']}['get_price']
    assert schema['required'] == ['coin']
    assert schema['properties']['coin']['type'] == 'string'
    assert schema['properties']['window'] == {'type': 'integer', 'default': 24}


def test_address_deterministic_and_interface_sensitive():
    assert manifest()['address'] == manifest()['address']

    class Toy2(Toy):
        def get_price(self, coin: str, window: int = 24, fiat: str = 'usd') -> dict:
            return {}

    changed = compiler.compile_contract('toy', Toy2())
    assert changed['address'] != manifest()['address']
    # docstring changes don't move the address
    class Toy3(Toy):
        pass
    Toy3.get_price.__doc__ = 'different words, same interface'
    assert compiler.compile_contract('toy', Toy3())['address'] == manifest()['address']


def test_readonly_drops_writes():
    m = manifest(readonly=True)
    assert [f['name'] for f in m['functions']] == ['get_price']


def test_include_exclude():
    assert [f['name'] for f in manifest(include=['transfer'])['functions']] == ['transfer']
    assert [f['name'] for f in manifest(exclude=['transfer'])['functions']] == ['get_price']


def test_rpc_roundtrip():
    m = manifest()
    toy = Toy()
    fn_by_tool = {t['name']: t['fn'] for t in m['tools']}
    server = rpc.McpServer('toy', '0', 'toy contract', lambda: m['tools'],
                           lambda name, a: getattr(toy, fn_by_tool[name])(**a))

    init = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
    assert init['result']['serverInfo']['name'] == 'toy'

    tl = server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
    listed = tl['result']['tools']
    assert [t['name'] for t in listed] == ['toy_get_price', 'toy_transfer']
    assert all('fn' not in t for t in listed)   # internal key never crosses the wire

    call = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                          'params': {'name': 'toy_get_price', 'arguments': {'coin': 'tao'}}})
    out = call['result']['structuredContent']
    assert out == {'coin': 'tao', 'window': 24, 'price': 1.0}
    assert call['result']['isError'] is False

    bad = server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                         'params': {'name': 'toy_get_price', 'arguments': {}}})
    assert bad['result']['isError'] is True     # missing arg reverts to the model

    assert server.handle({'jsonrpc': '2.0', 'method': 'notified'}) is None
    nope = server.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'resources/list'})
    assert nope['error']['code'] == -32601


def test_runtime_guards_and_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('MOD2MCP_HOME', str(tmp_path))
    import importlib
    from mod2mcp import runtime as rt
    importlib.reload(rt)

    toy = Toy()
    monkeypatch.setitem(rt._instances, 'toy', toy)
    monkeypatch.setattr(rt, '_config_fns', lambda m: None)

    # deny + readonly guards fire before the mod is touched
    with pytest.raises(PermissionError):
        rt.call('toy', 'serve')
    with pytest.raises(PermissionError):
        rt.call('toy', '_hidden')
    with pytest.raises(PermissionError):
        rt.call('toy', 'transfer', {'to': 'x', 'amount': 1}, readonly=True)
    assert rt.call('toy', 'get_price', {'coin': 'tao'})['coin'] == 'tao'

    # deploy/undeploy persist through the registry file
    out = rt.deploy('toy', readonly=True)
    assert out['address'].startswith('0x') and out['functions'] == 1
    assert json.loads((tmp_path / 'deployments.json').read_text())['toy']['readonly'] is True
    assert rt.deployed_manifest('toy')['readonly'] is True
    assert rt.undeploy('toy')['existed'] is True
    assert rt.deployments() == {}
