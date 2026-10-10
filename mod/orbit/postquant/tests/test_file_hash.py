"""file= — committing a local file's SHA3-256 to a key, and the gate that
keeps that strictly local.

The node hashing its own disk is a feature for the operator's shell and an
oracle for anyone else, so the one invariant that matters here is the last
test: a caller marked remote is refused the path form but keeps hash=.
"""

import hashlib

import pytest

import mcp as mcpsrv
from state import StateError


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


@pytest.fixture(scope='module')
def carol():
    call('pq_wallet', action='create', name='carol')
    call('pq_faucet', wallet='carol', amount='200')
    return call('pq_wallet', action='show', name='carol')


@pytest.fixture()
def artifact(tmp_path):
    p = tmp_path / 'model.bin'
    p.write_bytes(b'weights ' * 4096)
    return p


def test_set_check_roundtrip(carol, artifact):
    digest = hashlib.sha3_256(artifact.read_bytes()).hexdigest()

    r = call('pq_set', key='fs/model.bin', file=str(artifact), hours=24,
             wallet='carol')
    assert r['receipt']['ok'] is True
    assert r['committed']['value'] == digest
    assert r['committed']['file_bytes'] == artifact.stat().st_size
    assert str(artifact) in r['committed']['of']

    e = call('pq_get', key='fs/model.bin')
    assert e['value_kind'] == 'hash'
    assert e['value'] == digest

    c = call('pq_check', key='fs/model.bin', file=str(artifact))
    assert c['matches'] is True
    assert c['file']['bytes'] == artifact.stat().st_size

    artifact.write_bytes(b'tampered')
    assert call('pq_check', key='fs/model.bin',
                file=str(artifact))['matches'] is False


def test_quote_prices_the_digest_not_the_file(carol, artifact):
    q = call('pq_quote', key='fs/model.bin', file=str(artifact),
             wallet='carol')
    assert q['value_bytes'] == 32                 # the file never hits chain


def test_file_refuses_raw(carol, artifact):
    with pytest.raises(StateError) as e:
        call('pq_set', key='fs/raw', file=str(artifact), value_kind='raw',
             wallet='carol')
    assert e.value.code == 'bad_value'


def test_file_plus_data_is_ambiguous(carol, artifact):
    with pytest.raises(StateError) as e:
        call('pq_set', key='fs/x', file=str(artifact), data='x',
             wallet='carol')
    assert e.value.code == 'bad_args'


def test_unreadable_path_is_a_caller_error(carol):
    with pytest.raises(StateError) as e:
        call('pq_set', key='fs/none', file='/no/such/file', wallet='carol')
    assert e.value.code == 'bad_file'
    assert e.value.status == 400


def test_remote_callers_are_refused_the_path_form(carol, artifact):
    digest = hashlib.sha3_256(artifact.read_bytes()).hexdigest()
    mcpsrv.set_remote(True)
    try:
        for tool in ('pq_set', 'pq_quote', 'pq_check'):
            with pytest.raises(StateError) as e:
                call(tool, key='fs/model.bin', file=str(artifact))
            assert e.value.code == 'local_only'
            assert e.value.status == 403
        # hash= stays open remotely — the caller hashed it where it lives
        assert call('pq_check', key='fs/model.bin',
                    hash=digest)['matches'] is True
    finally:
        mcpsrv.set_remote(False)
