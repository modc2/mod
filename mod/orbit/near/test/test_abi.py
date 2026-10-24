"""near abi tests — the panic reader and ABI parser offline; wrap.near live
unless NEAR_OFFLINE=1."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.append(HERE)

import abi                                                    # noqa: E402
import chain                                                  # noqa: E402
import mcp                                                    # noqa: E402

OFFLINE = os.environ.get('NEAR_OFFLINE', '') not in ('', '0')
needs_chain = pytest.mark.skipif(OFFLINE, reason='NEAR_OFFLINE=1')

# Real strings the RPC answered during development, verbatim.
PROHIBITED = ('wasm execution failed with error: HostError(ProhibitedInView '
              '{ method_name: "predecessor_account_id" })')
MISSING_OLD = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
               '"panicked at \'Failed to deserialize input from JSON.: Error(\\"missing '
               'field `receiver_id`\\", line: 1, column: 2)\', src/lib.rs:43:1" })')
MISSING_NEW = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
               '"Failed to deserialize input from JSON. Error: `missing field `signed` '
               'at line 1 column 2`" })')
NULL_TYPE = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
             '"panicked at \'Failed to deserialize input from JSON.: Error(\\"invalid '
             'type: null, expected a string\\", line: 1, column: 18)\', src/lib.rs:43:1" })')
INT_TYPE = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
            '"panicked at \'Failed to deserialize input from JSON.: Error(\\"invalid '
            'type: integer `5`, expected a string\\", line: 1, column: 15)\', src/lib.rs:43:1" })')
ACCT_VALUE = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
              '"Failed to deserialize input from JSON. Error: `invalid value: \\"x\\", '
              'the Account ID is too short at line 1 column 13`" })')
OLD_ACCT = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
            '"panicked at \'Failed to deserialize input from JSON.: Error(\\"The account '
            'ID is invalid\\", line: 1, column: 44)\', ref-exchange/src/views.rs:360" })')
ONE_YOCTO = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
             '"panicked at \'assertion failed: `(left == right)`\\n  left: `0`,\\n right: '
             '`1`: Requires attached deposit of exactly 1 yoctoNEAR\', near-sdk-3.1.0/src/utils/mod.rs:22:5" })')
POSITIVE = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
            '"panicked at \'Requires positive attached deposit\', src/w_near.rs:15:9" })')
INIT = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
        '"The contract has already been initialized" })')
NOT_FOUND = 'wasm execution failed with error: MethodResolveError(MethodNotFound)'
BUSINESS = ('wasm execution failed with error: HostError(GuestPanic { panic_msg: '
            '"Value does not correspond to a role" })')


# ── offline: reading panics ──────────────────────────────────────

def test_panic_reader_kinds():
    assert abi.read_panic(None)['kind'] == 'view'
    p = abi.read_panic(PROHIBITED)
    assert p['kind'] == 'change' and p['host_fn'] == 'predecessor_account_id'
    assert abi.read_panic(INIT)['kind'] == 'init'
    assert abi.read_panic(NOT_FOUND)['kind'] == 'missing'
    assert abi.read_panic(BUSINESS)['kind'] == 'view?'


def test_panic_reader_deposits():
    p = abi.read_panic(ONE_YOCTO)
    assert p['kind'] == 'change' and p['deposit'] == '1 yocto'
    p = abi.read_panic(POSITIVE)
    assert p['kind'] == 'change' and p['deposit'] == 'positive'


def test_panic_reader_fields_both_sdk_formats():
    assert abi.read_panic(MISSING_OLD)['missing'] == 'receiver_id'
    assert abi.read_panic(MISSING_NEW)['missing'] == 'signed'


def test_panic_reader_types_and_columns():
    p = abi.read_panic(NULL_TYPE)
    assert p['type_for']['type'] == 'string' and p['column'] == 18
    assert abi.read_panic(INT_TYPE)['type_for']['type'] == 'string'
    p = abi.read_panic(ACCT_VALUE)
    assert p['type_for']['type'] == 'account_id' and p['column'] == 13
    p = abi.read_panic(OLD_ACCT)                 # the pre-2022 near-account-id text
    assert p['type_for']['type'] == 'account_id' and p['column'] == 44


def test_serde_type_labels():
    assert abi._serde_type('a string') == 'string'
    assert abi._serde_type('u64') == 'number'
    assert abi._serde_type('a boolean') == 'boolean'
    assert abi._serde_type('a sequence') == 'array'
    assert abi._serde_type('struct Config') == 'object'
    assert abi._serde_type('a map') == 'object'


def test_field_at_column_maps_into_the_probe_layout():
    args = {'token': 'x', 'receiver_id': 'root.near', 'amount': '0'}
    # json.dumps(args) == '{"token": "x", "receiver_id": "root.near", "amount": "0"}'
    assert abi._field_at(args, 13) == 'token'
    assert abi._field_at(args, 35) == 'receiver_id'
    assert abi._field_at(args, 55) == 'amount'
    assert abi._field_at({}, 5) is None


def test_name_guesses():
    assert abi._guess('account_id') == ('account_id', 'root.near')
    assert abi._guess('receiver_id') == ('account_id', 'root.near')
    assert abi._guess('amount') == ('u128', '0')
    assert abi._guess('limit') == ('number', 0)
    assert abi._guess('registration_only') == ('boolean', False)
    assert abi._guess('token_id') == ('string', 'x')
    assert abi._guess('token_ids') == ('array', [])
    assert abi._guess('config') == ('object', {})
    assert abi._guess('zzz_mystery') == (None, None)


def test_name_hints():
    assert abi._name_hint('get_status') == 'view'
    assert abi._name_hint('set_status') == 'change'
    assert abi._name_hint('ft_transfer_call') == 'change'
    assert abi._name_hint('hello') is None


# ── offline: the embedded near-abi document ──────────────────────

NEAR_ABI_DOC = {
    'schema_version': '0.4.0',
    'metadata': {'name': 'demo', 'version': '1.2.3',
                 'build': {'compiler': 'rustc 1.97.1', 'builder': 'cargo-near 0.11'}},
    'body': {
        'functions': [
            {'name': 'new', 'kind': 'call', 'modifiers': ['init'],
             'params': {'serialization_type': 'json', 'args': [
                 {'name': 'owner_id', 'type_schema': {'$ref': '#/definitions/AccountId'}},
                 {'name': 'config', 'type_schema': {'$ref': '#/definitions/Config'}}]}},
            {'name': 'ft_balance_of', 'kind': 'view',
             'params': {'serialization_type': 'json', 'args': [
                 {'name': 'account_id', 'type_schema': {
                     'description': 'NEAR Account Identifier.\n\nblah', 'type': 'string'}}]},
             'result': {'serialization_type': 'json',
                        'type_schema': {'$ref': '#/definitions/U128'}}},
            {'name': 'ft_transfer', 'kind': 'call', 'modifiers': ['payable'],
             'params': {'serialization_type': 'json', 'args': [
                 {'name': 'receiver_id', 'type_schema': {'$ref': '#/definitions/AccountId'}},
                 {'name': 'amount', 'type_schema': {'$ref': '#/definitions/U128'}},
                 {'name': 'memo', 'type_schema': {'type': ['string', 'null']}}]}},
            {'name': 'set_mode', 'kind': 'call',
             'params': {'serialization_type': 'json', 'args': [
                 {'name': 'mode', 'type_schema': {'$ref': '#/definitions/Mode'}},
                 {'name': 'flag', 'type_schema': {'type': 'boolean'}},
                 {'name': 'n', 'type_schema': {'type': 'integer', 'format': 'uint64'}},
                 {'name': 'ids', 'type_schema': {'type': 'array', 'items': True}}]}},
            {'name': 'on_cb', 'kind': 'call', 'modifiers': ['private'],
             'params': {'serialization_type': 'borsh', 'args': []}},
        ],
        'root_schema': {'definitions': {
            'AccountId': {'type': 'string', 'description': 'NEAR Account Identifier.'},
            'U128': {'type': 'string'},
            'Config': {'type': 'object', 'properties': {'fee': True, 'name': {'type': 'string'}},
                       'additionalProperties': False},
            'Mode': {'type': 'string', 'enum': ['fast', 'slow']},
        }},
    },
}


def test_parse_near_abi():
    out = abi.parse_near_abi(NEAR_ABI_DOC)
    m = {x['name']: x for x in out['methods']}
    assert m['new']['kind'] == 'init'
    assert [a['type'] for a in m['new']['args']] == ['account_id', 'object']
    assert m['new']['args'][1]['example'] == {'fee': None, 'name': None}
    bal = m['ft_balance_of']
    assert bal['kind'] == 'view' and bal['args'][0]['type'] == 'account_id'
    assert bal['result'] == 'U128'
    tr = m['ft_transfer']
    assert tr['kind'] == 'change' and tr['payable'] and tr['deposit'] == 'positive'
    assert [(a['name'], a['type'], a['required']) for a in tr['args']] == [
        ('receiver_id', 'account_id', True), ('amount', 'u128', True), ('memo', 'string', False)]
    sm = m['set_mode']
    assert sm['args'][0]['options'] == ['fast', 'slow'] and sm['args'][0]['example'] == 'fast'
    assert [a['type'] for a in sm['args']] == ['string', 'boolean', 'number', 'array']
    assert m['on_cb']['private'] and m['on_cb']['serialization'] == 'borsh'
    assert out['metadata']['version'] == '1.2.3'


def test_merge_prefers_abi_then_probe_then_standard():
    probe = {'name': 'ft_transfer', 'kind': 'change', 'deposit': '1 yocto', 'calls': 3,
             'args': [{'name': 'receiver_id', 'type': 'account_id', 'required': True},
                      {'name': 'amount', 'type': 'string', 'required': True}]}
    hist = {'seen': 2, 'deposit_yocto': 1, 'self_called': False,
            'args': {'receiver_id': 'bob.near', 'amount': '5000000000000000000000000', 'memo': None}}
    m = abi._merge('ft_transfer', None, probe, abi.NEP['ft_transfer'], hist)
    assert m['kind'] == 'change' and m['confidence'] == 0.9
    assert m['standard'] == 'nep141' and m['deposit'] == '1 yocto'
    names = [a['name'] for a in m['args']]
    assert names == ['receiver_id', 'amount', 'memo']
    assert m['args'][0]['example'] == 'bob.near'           # history fills examples
    assert m['args'][2]['required'] is False               # the standard says memo is optional
    assert m['seen'] == 2 and 'history' in m['source']

    # a soft "view?" with a change-shaped name leans change at low confidence
    m = abi._merge('add_admin', None, {'name': 'add_admin', 'kind': 'view?', 'args': [],
                                       'calls': 2}, None, None)
    assert m['kind'] == 'change' and m['confidence'] == 0.5
    # ...and with a neutral name stays a likely view
    m = abi._merge('whatever', None, {'name': 'whatever', 'kind': 'view?', 'args': [],
                                      'calls': 2}, None, None)
    assert m['kind'] == 'view' and m['confidence'] == 0.6
    # nothing known at all → the name decides, or nothing does
    assert abi._merge('get_x', None, None, None, None)['kind'] == 'view'
    assert abi._merge('zzz', None, None, None, None)['kind'] == 'unknown'


def test_tool_and_route_exist():
    import api
    assert 'near_abi' in mcp.TOOLS
    assert api.ROUTE_TOOLS['/abi'] == 'near_abi'
    assert 'account_id' in mcp.TOOLS['near_abi']['inputSchema']['required']


def test_zstd_roundtrip_when_a_decoder_exists():
    import shutil
    import subprocess
    if not shutil.which('zstd'):
        pytest.skip('no zstd binary')
    blob = subprocess.run(['zstd', '-c'], input=b'{"a":1}', capture_output=True).stdout
    assert abi._zstd(blob) == b'{"a":1}'


# ── live ─────────────────────────────────────────────────────────

@needs_chain
def test_wrap_near_interface_is_inferred():
    out = mcp.call_tool('near_abi', {'account_id': 'wrap.near', 'refresh': True})
    m = {x['name']: x for x in out['methods']}
    assert m['ft_metadata']['kind'] == 'view' and m['ft_metadata']['args'] == []
    bal = m['ft_balance_of']
    assert bal['kind'] == 'view'
    assert bal['args'][0]['name'] == 'account_id' and bal['args'][0]['type'] == 'account_id'
    tr = m['ft_transfer']
    assert tr['kind'] == 'change' and tr['deposit'] == '1 yocto'
    assert [a['name'] for a in tr['args']][:2] == ['receiver_id', 'amount']
    assert m['storage_deposit']['kind'] == 'change' and m['storage_deposit']['payable']
    assert m['new']['kind'] == 'init'
    assert 'nep141' in out['standards']
    # second call is served from the store
    again = mcp.call_tool('near_abi', {'account_id': 'wrap.near'})
    assert again.get('cached') is True
    # one method + template
    one = mcp.call_tool('near_abi', {'account_id': 'wrap.near', 'method': 'ft_balance_of'})
    assert one['args_template'] == {'account_id': 'root.near'}


@needs_chain
def test_embedded_abi_is_decoded():
    out = mcp.call_tool('near_abi', {'account_id': 'intents.near', 'refresh': True})
    assert 'abi' in out['sources'], out.get('abi_error')
    m = {x['name']: x for x in out['methods']}
    assert m['fee']['kind'] == 'view' and m['fee']['confidence'] == 1.0
    assert m['execute_intents']['kind'] == 'change'
    assert m['execute_intents']['args'][0]['name'] == 'signed'
    assert m['new']['kind'] == 'init'
    assert out['metadata']['version']


@needs_chain
def test_view_on_a_change_method_now_refuses():
    with pytest.raises(chain.NearError) as e:
        mcp.call_tool('near_view', {'contract': 'wrap.near', 'method': 'storage_deposit'})
    assert 'change method' in str(e.value)
    with pytest.raises(chain.NearError) as e:
        mcp.call_tool('near_view', {'contract': 'wrap.near', 'method': 'ft_balance_of'})
    assert 'missing' in str(e.value)
