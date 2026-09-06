"""The test runner, tested.

Most of this file is about the *judgement*: given what the chain returned, did
the case pass. That part is pure and gets exercised directly, because a runner
whose comparison is subtly wrong reports green suites forever and is worse than
no runner at all.

The rest deploys to the local anvil node and asserts on real receipts —
skipped, not mocked, when there is no chain. A mocked revert is a string; a
real one is what solc actually emitted, which is the thing the `expect_revert`
matcher has to survive.
"""
import pytest

import harness

CTX = {'owner': '0xabc', 'account': 'x', 'address': '0x' + '11' * 20,
       'network': 'local', 'deployer': '0x' + '22' * 20, 'confirm': False}

COUNTER = '''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract Counter {
    uint256 public count;
    address public owner;
    event Bumped(address indexed who, uint256 to);
    constructor(uint256 start) { count = start; owner = msg.sender; }
    function bump() external { count += 1; emit Bumped(msg.sender, count); }
    function setTo(uint256 v) external { require(v < 100, "too big"); count = v; }
}
'''


# ── reading values ───────────────────────────────────────────────────

@pytest.mark.parametrize('text,expected', [
    ('42', 42), ('0x2a', 42), ('1_000', 1000), ('10**18', 10 ** 18),
    ('-7', -7), ('gm', None), ('', None), ('0xnothex', None),
])
def test_numbers_people_actually_write(text, expected):
    assert harness._big(text) == expected


def test_an_address_compares_without_its_checksum():
    upper = '0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    assert harness.same(upper, upper.lower())


def test_a_string_and_the_number_it_spells_are_one_value():
    assert harness.same('1000', 1000)
    assert harness.same(b'\x01\x02', '0x0102')
    assert not harness.same('gm', 'ok')


def test_a_bool_is_not_a_number():
    """True == 1 in Python, and an expect_gt on a bool would silently pass."""
    assert harness._numeric(True) is None
    assert harness.same(True, True)
    assert not harness.same(True, 'true')


# ── placeholders ─────────────────────────────────────────────────────

def test_placeholders_expand_everywhere_in_a_case():
    got = harness.expand({'to': '$deployer', 'list': ['$contract', '$zero', 5]}, CTX)
    assert got['to'] == CTX['deployer']
    assert got['list'] == [CTX['address'], harness.ZERO, 5]


def test_an_unknown_placeholder_is_an_error_not_a_string():
    with pytest.raises(harness.TestError):
        harness.expand('$whatever', CTX)


def test_a_missing_account_placeholder_names_the_account():
    with pytest.raises(harness.TestError) as e:
        harness.expand('$account:nope', CTX)
    assert 'nope' in str(e.value)


# ── the verdict ──────────────────────────────────────────────────────

def check(case, result, events=None, receipt=None):
    return harness._check(case, result, receipt, events or [], CTX)


def test_a_case_with_no_expectation_passes_but_says_so():
    ok, why = check({'fn': 'x'}, 5)
    assert ok and 'no expectation' in why


def test_expect_compares_after_normalising():
    assert check({'expect': 5}, '5')[0]
    assert check({'expect': '$deployer'}, CTX['deployer'].upper())[0]
    ok, why = check({'expect': 5}, 6)
    assert not ok and '6' in why


def test_the_numeric_comparisons():
    assert check({'expect_gt': 0}, 5)[0]
    assert check({'expect_gte': 5}, 5)[0]
    assert check({'expect_lt': '10**18'}, 5)[0]
    assert not check({'expect_lt': 5}, 5)[0]


def test_a_numeric_comparison_on_a_string_fails_loudly():
    ok, why = check({'expect_gt': 0}, 'gm')
    assert not ok and 'number' in why


def test_expect_event_names_what_did_fire():
    events = [{'event': 'Transfer', 'args': {'value': 5}}]
    assert check({'expect_event': 'Transfer'}, None, events)[0]
    ok, why = check({'expect_event': 'Approval'}, None, events)
    assert not ok and 'Transfer' in why


def test_expect_event_can_pin_its_arguments():
    events = [{'event': 'Bumped', 'args': {'to': 6, 'who': CTX['deployer']}}]
    assert check({'expect_event': {'name': 'Bumped', 'args': {'to': '6'}}},
                 None, events)[0]
    assert not check({'expect_event': {'name': 'Bumped', 'args': {'to': 7}}},
                     None, events)[0]


def test_every_expectation_on_a_case_has_to_hold():
    events = [{'event': 'Bumped', 'args': {'to': 6}}]
    assert check({'expect_gt': 1, 'expect_lt': 10}, 6)[0]
    assert not check({'expect_gt': 1, 'expect_lt': 3}, 6)[0]
    assert not check({'expect_gt': 1, 'expect_event': 'Nope'}, 6, events)[0]


# ── the suite itself ─────────────────────────────────────────────────

def test_a_case_needs_a_function():
    with pytest.raises(harness.TestError):
        harness._fn_of({'name': 'nameless'})
    assert harness._fn_of({'call': 'total'}) == ('total', 'read')
    assert harness._fn_of({'send': 'mint'}) == ('mint', 'write')


def test_the_generated_suite_asserts_nothing_and_says_why():
    abi = [{'type': 'function', 'name': 'count', 'inputs': [],
            'stateMutability': 'view'},
           {'type': 'function', 'name': 'balanceOf',
            'inputs': [{'type': 'address'}], 'stateMutability': 'view'},
           {'type': 'function', 'name': 'bump', 'inputs': [],
            'stateMutability': 'nonpayable'},
           {'type': 'constructor', 'inputs': [{'name': 'start', 'type': 'uint256'}]}]
    suite = harness.generate(abi)
    assert [c['fn'] for c in suite['cases']] == ['count'], \
        'only the free, zero-argument getters'
    assert not any('expect' in k for c in suite['cases'] for k in c)
    # A constructor that takes arguments must not be guessed at.
    assert suite['args'] is None
    assert 'uint256 start' in suite['note']


def test_a_malformed_suite_is_the_authors_problem():
    with pytest.raises(harness.TestError):
        harness._normalise_suites('{not json')
    with pytest.raises(harness.TestError):
        harness._normalise_suites([['not an object']])
    assert harness._normalise_suites(None)[0]['auto'] is True


# ── against a real chain ─────────────────────────────────────────────

def test_a_suite_runs_against_a_real_deploy(chain_up, funded, address):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    report = harness.run(
        address, funded, network='local', source=COUNTER,
        suites=[{
            'name': 'counter',
            'args': [5],
            'cases': [
                {'name': 'starts where told', 'fn': 'count', 'expect': 5},
                {'name': 'the deployer owns it', 'fn': 'owner',
                 'expect': '$deployer'},
                {'name': 'bump fires its event', 'fn': 'bump',
                 'expect_event': 'Bumped'},
                {'name': 'and moves the state', 'fn': 'count', 'expect': 6},
                {'name': 'the guard holds', 'fn': 'setTo', 'args': [500],
                 'expect_revert': 'too big'},
            ],
        }],
        token=None, store_report=False)

    assert report['ok'], report['suites'][0]['cases']
    assert report['passed'] == 5 and report['failed'] == 0
    assert report['testnet'] is True
    suite = report['suites'][0]
    assert suite['address'].startswith('0x')
    assert suite['deploy']['hash'].startswith('0x')
    # Reads are free; the one write that actually landed has a receipt. The
    # reverting case never got sent — the gas estimate caught it — so it has
    # a revert message and no hash, which is the cheaper way to be right.
    assert sum(1 for c in suite['cases'] if c.get('hash')) == 1
    guard = next(c for c in suite['cases'] if c['name'] == 'the guard holds')
    assert guard.get('hash') is None and 'too big' in guard['revert']


def test_a_failing_expectation_reports_both_sides(chain_up, funded, address):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    report = harness.run(
        address, funded, network='local', source=COUNTER,
        suites=[{'name': 'wrong', 'args': [1], 'cases': [
            {'name': 'wishful', 'fn': 'count', 'expect': 99},
            {'name': 'no such function', 'fn': 'teleport'},
            {'name': 'expected a revert that never came', 'fn': 'setTo',
             'args': [1], 'expect_revert': True},
        ]}],
        token=None, store_report=False)

    assert report['ok'] is False
    why = {c['name']: c['why'] for c in report['suites'][0]['cases']}
    assert '99' in why['wishful'] and '1' in why['wishful']
    assert 'not in the ABI' in why['no such function']
    assert 'went through' in why['expected a revert that never came']


def test_a_run_is_recorded_and_readable_afterwards(chain_up, funded, address):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    report = harness.run(address, funded, network='local', source=COUNTER,
                         args=[0], token=None, store_report=False)
    assert report['id']
    kept = harness.report(address, report['id'])
    assert kept['report']['passed'] == report['passed']
    assert any(r['id'] == report['id'] for r in harness.runs(address))
    # Another caller cannot read it.
    with pytest.raises(Exception):
        harness.report('0xsomebodyelse', report['id'])


def test_the_api_runs_a_suite(client, token, chain_up, funded):
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    got = client.post('/test', json={
        'source': COUNTER, 'account': funded, 'network': 'local', 'args': [3],
        'suites': [{'name': 'via http', 'args': [3],
                    'cases': [{'name': 'three', 'fn': 'count', 'expect': 3}]}],
        'store_report': False,
    }, headers={'Authorization': f'Bearer {token}'})
    assert got.status_code == 200, got.text
    assert got.json()['ok'] is True


def test_generating_a_suite_needs_no_account(client):
    got = client.post('/test/generate', json={'source': COUNTER})
    assert got.status_code == 200, got.text
    assert [c['fn'] for c in got.json()['cases']] == ['count', 'owner']


def test_running_a_suite_needs_one(client):
    assert client.post('/test', json={'source': COUNTER,
                                      'account': 'x'}).status_code == 401


# ── the message an empty testnet account gets ────────────────────────

def test_an_unfunded_account_is_told_it_is_unfunded(monkeypatch):
    """Both RPC dialects for "no money" have to read as no money.

    `gas required exceeds allowance (1897)` is a real Base Sepolia answer to a
    deploy from an empty account. Taken literally it looks like a gas-limit
    problem and sends people to tune gas, which cannot help.
    """
    import ops

    def chain(balance_wei, gas_price=10 ** 9):
        eth = type('Eth', (), {'get_balance': staticmethod(lambda _: balance_wei),
                               'gas_price': gas_price})
        return type('W3', (), {'eth': eth})()

    spec = {'name': 'base-sepolia', 'currency': 'ETH', 'testnet': True}
    who = '0x' + '11' * 20
    broke = chain(11_387_358_461)               # ~11 gwei: not nothing, not enough

    for dialect in ('gas required exceeds allowance (1897)',
                    'insufficient funds for gas * price + value'):
        said = ops._cannot_pay(Exception(dialect), broke, who, spec)
        assert said and 'cannot pay' in said
        assert '0.000000011387358461' in said
        assert 'faucet' in said

    # A real revert is left alone — it is not a funding problem.
    assert ops._cannot_pay(Exception('execution reverted: too big'), broke,
                           who, spec) is None

    # And neither is a funded account that got the same phrasing: the balance
    # decides, not the wording, or a reverting call on the wrong node would be
    # reported to a solvent caller as poverty.
    rich = chain(10 ** 18)
    assert ops._cannot_pay(Exception('gas required exceeds allowance (1897)'),
                           rich, who, spec) is None
