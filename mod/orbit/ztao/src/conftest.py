"""Shared pytest setup: loads src/ as the `ztao_src` package (via mod.py)
so every test can `from ztao_src... import ...`, plus the opt-in gates for
the slow tiers. See pytest.ini."""
import importlib.util
import os

import pytest

SRC = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location('ztao_mod',
                                               os.path.join(SRC, 'mod.py'))
MODPY = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MODPY)


def pytest_collection_modifyitems(config, items):
    for item in items:
        for mark, env in (('native', 'ZTAO_NATIVE'), ('live', 'ZTAO_LIVE')):
            if item.get_closest_marker(mark) and not os.environ.get(env):
                item.add_marker(pytest.mark.skip(reason=f'set {env}=1'))


@pytest.fixture(scope='session')
def modpy():
    return MODPY


@pytest.fixture(scope='session')
def nets():
    return MODPY.NETS


@pytest.fixture(scope='session')
def native_suite():
    """check(chain, min_passed): the chain's own suite passes in full (and
    lands in the console's TESTS cache)."""
    def check(chain, min_passed):
        r = MODPY.Mod().test(chain)[chain]
        assert r['ok'], r.get('log', '')[-1500:]
        assert r['failed'] in (0, None) and r['passed'] >= min_passed, r
        return r
    return check


@pytest.fixture(scope='session')
def live_epoch():
    """check(chain): a full anonymous epoch on the chain's local net —
    3 tags scored, one claimed, the replayed claim rejected."""
    def check(chain):
        r = MODPY.Mod().simulate(chain=chain)
        assert r['ok'], r
        assert r['double_claim'].startswith('rejected'), r['double_claim']
        tags = r['tally']['tags']
        assert len(tags) == 3 and sum(t['claimed'] for t in tags) == 1
        return r
    return check
