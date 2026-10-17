"""Test fixtures — every test runs offline, against a target whose score is known.

Two things are set before `rvb` is imported anywhere, and both matter:

    RVB_DIR      a scratch store, so a test run never touches the operator's
                corpus in ~/.mod/rvb — store.DIR is read at import time.
    the target  models.DEFAULT is forced to a mock, so a test that forgets to
                pass `model=` fails offline instead of quietly spending a real
                model call (the CLI backend is keyless, which makes that
                mistake silent and slow rather than loud).
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix='rvb-tests-')
os.environ['RVB_DIR'] = _TMP
os.environ.setdefault('RVB_MODEL', 'mock:naive')
os.environ.setdefault('RVB_KEEP_ROUNDS', '5')

import pytest                                      # noqa: E402

from rvb import corpus, models, store       # noqa: E402


@pytest.fixture(autouse=True)
def no_live_target(monkeypatch):
    """A test that reaches a real backend is a bug in the test."""
    monkeypatch.setattr(models, 'DEFAULT', 'mock:naive')
    monkeypatch.setattr(models, 'JUDGE_MODEL', 'mock:naive')


@pytest.fixture
def seeded():
    """The shipped corpus, in the scratch store."""
    corpus.seed_store()
    return store.listing('attack', limit=0)


@pytest.fixture
def clean():
    """An empty store for the tests that count what is in it."""
    for kind in store.KINDS:
        for rec in store.listing(kind, limit=0):
            store.delete(kind, rec['id'])
    return store
