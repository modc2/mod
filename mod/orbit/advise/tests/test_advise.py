"""Tests for advise — offline, and against a throwaway store.

Two things are worth guarding and they are both about the seam: an outsider
can read and file, and an outsider cannot decide. Everything else here is
bounds checking around those.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

STORE = tempfile.mkdtemp(prefix='advise-test-')
os.environ['ADVISE_STORE'] = STORE

import recs as store                                           # noqa: E402
import scan                                                    # noqa: E402

_spec = importlib.util.spec_from_file_location('advise_anchor',
                                               os.path.join(HERE, 'mod.py'))
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)

OUTSIDER = {'address': '', 'handle': 'anon:deadbeef', 'signed': False}
SIGNED = {'address': '0x1111111111111111111111111111111111111111',
          'handle': '0x1111111111111111111111111111111111111111', 'signed': True}


@pytest.fixture(autouse=True)
def clean_store():
    store.STORE = STORE
    shutil.rmtree(STORE, ignore_errors=True)
    os.makedirs(STORE, exist_ok=True)
    yield
    shutil.rmtree(STORE, ignore_errors=True)


@pytest.fixture
def mod():
    return _anchor.Mod(local=False)


# ── the read half ────────────────────────────────────────────────────

def test_modules_lists_this_module(mod):
    names = [m['module'] for m in mod.modules()['modules']]
    assert 'advise' in names
    assert 'build' in names


def test_private_modules_are_absent(monkeypatch):
    monkeypatch.setattr(scan, 'private_names', lambda: {'advise'})
    assert 'advise' not in [m['module'] for m in scan.modules()['modules']]
    with pytest.raises(scan.ScanError):
        scan.module_dir('advise')


def test_tree_prunes_dependency_dirs(mod):
    paths = [f['path'] for f in mod.tree(module='advise', depth=4)['files']]
    assert 'mod.py' in paths
    assert not any('__pycache__' in p or 'node_modules' in p for p in paths)


def test_read_is_bounded_and_redacted(mod):
    out = mod.file(module='advise', path='scan.py', start=1, lines=5)
    assert out['lines'] == 5 and out['truncated'] is True
    assert 'MAX_FILE_BYTES' in scan.redact('MAX_FILE_BYTES = 200_000')
    assert '«redacted»' in scan.redact('api_key = "sk-abcdefghijklmnop123456"')
    assert '«redacted»' in scan.redact('0x' + 'a' * 64)


def test_path_cannot_escape_the_module(mod):
    with pytest.raises(scan.ScanError):
        mod.file(module='advise', path='../build/config.json')


def test_secret_shaped_files_are_not_published():
    assert not scan._readable('.env')
    assert not scan._readable('signer.key')
    assert not scan._readable('secrets.json')
    assert scan._readable('mod.py')


def test_grep_returns_anchors(mod):
    out = mod.grep(module='advise', query='def brief')
    assert out['count'] >= 1
    assert out['hits'][0]['path'].endswith('.py') and out['hits'][0]['line'] > 0


def test_brief_is_one_call_with_everything(mod):
    b = mod.brief(module='advise')
    assert b['config']['name'] == 'advise'
    assert b['stats']['files'] > 3 and 'python' in b['stats']['languages']
    assert b['tree']['count'] > 3
    assert b['open_recommendations'] == []
    assert 'owner' in b and 'how_to_file' in b


# ── filing ───────────────────────────────────────────────────────────

def _file_one(mod, **kw):
    args = {'module': 'advise', 'title': 'cache the module list',
            'summary': 'modules() walks the tree on every call',
            'change': 'memoise it for 30s',
            'anchors': [{'path': 'scan.py', 'line': 140, 'note': 'modules()'}],
            'kind': 'performance', 'severity': 'low'}
    args.update(kw)
    return mod.recommend(**args)


def test_anyone_can_file_and_it_changes_nothing(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    out = _file_one(mod)
    rec = store.get(out['filed'])
    assert rec['status'] == 'pending'
    assert rec['author'] == 'anon:deadbeef' and rec['author_signed'] is False
    assert rec['relay'] is None
    assert rec['anchors'][0]['exists'] is True        # the file really is there


def test_a_wrong_anchor_is_kept_but_marked(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: SIGNED)
    out = _file_one(mod, anchors=[{'path': 'nope.py', 'line': 1}])
    assert store.get(out['filed'])['anchors'][0]['exists'] is False


def test_filing_validates(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: SIGNED)
    with pytest.raises(ValueError):
        mod.recommend(module='advise', title='')
    with pytest.raises(store.RecError):
        _file_one(mod, kind='vibes')
    with pytest.raises(store.RecError):
        _file_one(mod, title='x' * 201)
    with pytest.raises(scan.ScanError):
        _file_one(mod, module='no-such-module')


def test_unsigned_callers_hit_a_tighter_quota(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    for _ in range(store.MAX_PENDING_ANON):
        _file_one(mod)
    with pytest.raises(store.RecError):
        _file_one(mod)


# ── the decision ─────────────────────────────────────────────────────

def test_an_outsider_cannot_decide(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    rec_id = _file_one(mod)['filed']
    with pytest.raises(store.Denied):
        mod.approve(id=rec_id)
    with pytest.raises(store.Denied):
        mod.reject(id=rec_id)
    assert store.get(rec_id)['status'] == 'pending'


def test_another_signed_address_cannot_decide(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: SIGNED)
    rec_id = _file_one(mod)['filed']
    stranger = {'address': '0x2222222222222222222222222222222222222222',
                'handle': '0x2222', 'signed': True}
    with pytest.raises(store.Denied):
        store.decide(rec_id, stranger, 'approved')


def test_the_owner_approves_and_it_relays(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    rec_id = _file_one(mod)['filed']
    owner = store.get(rec_id)['owner']
    relayed = {}

    def fake_relay(rec, token=None):
        relayed['body'] = store._relay_body(rec)
        return {'ok': True, 'suggestion_id': 'sg_test', 'at': 0}

    monkeypatch.setattr(store, 'relay_to_build', fake_relay)
    monkeypatch.setattr(_anchor.identity, 'caller',
                        lambda **kw: {'address': owner, 'handle': owner, 'signed': True})
    out = mod.approve(id=rec_id, note='keep the cache small')
    assert out['status'] == 'approved'
    assert out['relay']['suggestion_id'] == 'sg_test'
    # The relayed text carries the attribution and the anchors, because the
    # owner should never lose track of whose idea it was.
    assert 'anon:deadbeef (no account)' in relayed['body']
    assert 'scan.py:140' in relayed['body']
    assert len(relayed['body']) <= store.RELAY_BODY_MAX


def test_a_failed_relay_does_not_undo_the_approval(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: SIGNED)
    rec_id = _file_one(mod)['filed']
    owner = store.get(rec_id)['owner']

    def boom(rec, token=None):
        raise store.RecError('build is not answering')

    monkeypatch.setattr(store, 'relay_to_build', boom)
    monkeypatch.setattr(_anchor.identity, 'caller',
                        lambda **kw: {'address': owner, 'handle': owner, 'signed': True})
    out = mod.approve(id=rec_id)
    assert out['status'] == 'approved'
    assert out['relay']['ok'] is False and 'not answering' in out['relay']['error']


def test_relay_body_drops_the_patch_before_it_overflows():
    rec = {'id': 'rc_00000000', 'module': 'advise', 'author': 'anon:x',
           'author_signed': False, 'agent': '', 'title': 't', 'kind': 'bug',
           'severity': 'high', 'summary': 's', 'rationale': 'r', 'change': 'c',
           'anchors': [], 'evidence': [], 'confidence': None, 'effort': '',
           'patch': 'x' * 30_000}
    body = store._relay_body(rec)
    assert len(body) <= store.RELAY_BODY_MAX
    assert 'patch omitted' in body


def test_author_can_withdraw_until_it_is_decided(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    rec_id = _file_one(mod)['filed']
    assert mod.withdraw(id=rec_id)['status'] == 'withdrawn'
    with pytest.raises(store.RecError):
        mod.withdraw(id=rec_id)


def test_inbox_needs_a_signature_and_shows_worst_first(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    _file_one(mod, severity='low', title='low one')
    _file_one(mod, severity='critical', title='critical one')
    with pytest.raises(store.Denied):
        mod.inbox()
    owner = scan.owner_of('advise')
    monkeypatch.setattr(_anchor.identity, 'caller',
                        lambda **kw: {'address': owner, 'handle': owner, 'signed': True})
    box = mod.inbox()
    assert box['count'] == 2
    assert box['recommendations'][0]['title'] == 'critical one'


def test_queue_and_thread_are_public(mod, monkeypatch):
    monkeypatch.setattr(_anchor.identity, 'caller', lambda **kw: OUTSIDER)
    rec_id = _file_one(mod)['filed']
    mod.comment(id=rec_id, body='the same applies to tree()')
    assert mod.recs(status='pending')['count'] == 1
    assert len(mod.rec(id=rec_id)['comments']) == 1


# ── the surfaces agree ───────────────────────────────────────────────

def test_mcp_tools_dispatch_into_the_same_class():
    import mcp as server
    names = {t['name'] for t in server.tool_list()}
    assert {'advise_brief', 'advise_recommend', 'advise_approve'} <= names
    out = server.call_tool('advise_modules', {'q': 'advise'}, local=False)
    assert any(m['module'] == 'advise' for m in out['modules'])
    err = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                         'params': {'name': 'advise_approve',
                                    'arguments': {'id': 'rc_00000000'}}})
    assert err['result']['isError'] is True


def test_config_publishes_every_fn():
    with open(os.path.join(HERE, 'config.json')) as f:
        cfg = json.load(f)
    mod = _anchor.Mod()
    for fn in cfg['fns']:
        assert callable(getattr(mod, fn)), fn
    import serve as srv
    assert set(cfg['api_fns']) == set(srv.API_FNS)
