"""pytest glue for test_bitevo.py.

The tests are written for `m bitevo/test` (run_tests) and RETURN a result
dict instead of asserting — under bare pytest a failing test would pass
silently. This hook turns {'passed': False} into a real failure.

Live-LLM tests (real spend) only run with BITEVO_LIVE=1.
"""

import os

import pytest

LIVE = {'test_backend_forward', 'test_miner_forward', 'test_validator_score',
        'test_epoch_local', 'test_multi_backend_epoch'}


def pytest_collection_modifyitems(config, items):
    if os.environ.get('BITEVO_LIVE') == '1':
        return
    skip = pytest.mark.skip(reason='live LLM call — set BITEVO_LIVE=1')
    for item in items:
        if item.name in LIVE:
            item.add_marker(skip)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    args = {a: pyfuncitem.funcargs[a] for a in pyfuncitem._fixtureinfo.argnames}
    res = pyfuncitem.obj(**args)
    if isinstance(res, dict) and res.get('passed') is False:
        pytest.fail(f"{res.get('test')}: {res.get('detail')}", pytrace=False)
    return True  # handled; also skips pytest's non-None-return warning
