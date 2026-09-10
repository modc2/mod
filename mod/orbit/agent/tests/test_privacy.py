"""
tests for module visibility — the public audit surface and the private seal

covers:
    - default visibility is public, for every module, with no state file
    - the audit walk: what it lists and what it withholds (secrets, build dirs)
    - the traversal guard on the file reader
    - seal -> "push" -> restore, and that the blob carries no plaintext
    - a foreign key opens nothing
    - the fleet-wide switch, and that it also moves the default
    - the api routes: reads open, writes owner-gated

Nothing here touches the real fleet: every case runs against a throwaway
module directory in /tmp with its own state dir.

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_privacy.py -v
"""
import os
import sys
import json
import shutil
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.privacy.mod import Privacy, SealError, SEAL_NAME


@pytest.fixture
def scratch():
    """A fake module directory + its own privacy state, torn down after."""
    tmp = Path(tempfile.mkdtemp(prefix='privacy-test-'))
    mod_dir = tmp / 'demo'
    (mod_dir / 'src').mkdir(parents=True)
    (mod_dir / 'config.json').write_text('{"name": "demo"}\n')
    (mod_dir / 'mod.py').write_text('def hello():\n    return "world"\n')
    (mod_dir / 'src' / 'a.py').write_text('A = 1\n')
    (mod_dir / '.env').write_text('OPENAI_API_KEY=sk-nope\n')
    (mod_dir / 'server.secret').write_text('deadbeef\n')
    (mod_dir / 'node_modules').mkdir()
    (mod_dir / 'node_modules' / 'dep.js').write_text('x' * 4096)

    p = Privacy(state=str(tmp / 'state'))
    p.dirpath = lambda name: mod_dir          # stand in for the fleet lookup
    p.modules = lambda: ['demo']
    p._git(mod_dir, 'init', '-q')
    p._git(mod_dir, 'config', 'user.email', 't@t')
    p._git(mod_dir, 'config', 'user.name', 't')
    p._git(mod_dir, 'add', '-A', '.')
    yield p, mod_dir
    shutil.rmtree(tmp, ignore_errors=True)


# ── visibility defaults ──────────────────────────────────────────────

def test_everything_is_public_with_no_state(scratch):
    p, _ = scratch
    assert not p._vis_path.exists()
    assert p.visibility('demo') == 'public'
    assert p.is_public('demo')
    assert p.ls()['default'] == 'public'


def test_flip_one_module_and_back(scratch):
    p, _ = scratch
    p.set('demo', 'private')
    assert p.visibility('demo') == 'private'
    p.set('demo', 'public')
    assert p.visibility('demo') == 'public'
    # back on the default means no override left behind
    assert json.loads(p._vis_path.read_text())['modules'] == {}


def test_fleet_switch_moves_the_default(scratch):
    p, _ = scratch
    r = p.set_all('private', seal=False)
    assert r['default'] == 'private'
    assert p.visibility('anything-new-at-all') == 'private'
    p.set_all('public', seal=False)
    assert p.visibility('anything-new-at-all') == 'public'


# ── the audit surface ────────────────────────────────────────────────

def test_tree_lists_source_and_withholds_secrets(scratch):
    p, _ = scratch
    paths = {f['path'] for f in p.tree('demo')['files']}
    assert paths == {'config.json', 'mod.py', 'src/a.py'}
    assert '.env' not in paths and 'server.secret' not in paths
    assert not any(x.startswith('node_modules') for x in paths)


def test_read_returns_source(scratch):
    p, _ = scratch
    got = p.read('demo', 'mod.py')
    assert got['text'] == 'def hello():\n    return "world"\n'
    assert got['binary'] is False


@pytest.mark.parametrize('path', [
    '../../../etc/passwd', '/etc/passwd', '~/.ssh/id_rsa',
    '.env', 'server.secret', 'node_modules/dep.js', '',
])
def test_reader_refuses_what_it_should(scratch, path):
    p, _ = scratch
    with pytest.raises((ValueError, PermissionError, KeyError)):
        p.read('demo', path)


def test_private_modules_are_closed_to_audit(scratch):
    p, _ = scratch
    p.set('demo', 'private')
    with pytest.raises(PermissionError):
        p.tree('demo')
    with pytest.raises(PermissionError):
        p.read('demo', 'mod.py')
    # …but still listed. That it exists is not the secret.
    entry = p.ls()['modules'][0]
    assert entry['name'] == 'demo' and entry['visibility'] == 'private'
    assert entry['description'] == ''


# ── sealing ──────────────────────────────────────────────────────────

def test_seal_publishes_ciphertext_only(scratch):
    p, mod_dir = scratch
    r = p.seal('demo')
    assert r['sealed'] and r['files'] == 3

    # what a push carries: the blob and the managed ignore, nothing else
    _, tracked = p._git(mod_dir, 'ls-files')
    assert set(tracked.split()) == {'.gitignore', SEAL_NAME}

    blob = (mod_dir / SEAL_NAME).read_bytes()
    for secret in (b'def hello', b'A = 1', b'sk-nope', b'deadbeef'):
        assert secret not in blob

    # the working tree is untouched — the module still has to run
    assert (mod_dir / 'mod.py').exists()


def test_reseal_of_unchanged_source_is_a_no_op(scratch):
    p, mod_dir = scratch
    p.seal('demo')
    before = (mod_dir / SEAL_NAME).read_bytes()
    assert p.seal('demo')['unchanged'] is True
    assert (mod_dir / SEAL_NAME).read_bytes() == before


def test_edit_then_reseal_produces_a_new_blob(scratch):
    p, mod_dir = scratch
    p.seal('demo')
    (mod_dir / 'mod.py').write_text('def hello():\n    return "changed"\n')
    r = p.seal('demo')
    assert r['unchanged'] is False


def test_restore_is_the_clone_side(scratch, tmp_path):
    p, mod_dir = scratch
    p.seal('demo')

    # a fresh clone: only what git tracked
    clone = tmp_path / 'clone'
    clone.mkdir()
    for f in ('.gitignore', SEAL_NAME):
        shutil.copy(mod_dir / f, clone / f)
    p.dirpath = lambda name: clone

    p.restore('demo')
    assert (clone / 'mod.py').read_text() == 'def hello():\n    return "world"\n'
    assert (clone / 'src' / 'a.py').read_text() == 'A = 1\n'
    # the secrets were never in the blob, so they do not come back
    assert not (clone / '.env').exists()


def test_a_foreign_key_opens_nothing(scratch, tmp_path):
    p, mod_dir = scratch
    p.seal('demo')
    stranger = Privacy(state=str(tmp_path / 'other-host'))
    stranger.dirpath = lambda name: mod_dir
    stranger.master_key()
    with pytest.raises(SealError):
        stranger.restore('demo', force=True)


def test_unseal_puts_the_tree_back_under_git(scratch):
    p, mod_dir = scratch
    p.seal('demo')
    p.unseal('demo')
    assert not (mod_dir / SEAL_NAME).exists()
    _, tracked = p._git(mod_dir, 'ls-files')
    assert 'mod.py' in tracked.split()


def test_restore_will_not_clobber_a_working_tree(scratch):
    p, _ = scratch
    p.seal('demo')
    with pytest.raises(SealError):
        p.restore('demo')          # source is still on disk


# ── the key ──────────────────────────────────────────────────────────

def test_passphrase_wrapped_key_needs_the_passphrase(tmp_path):
    p = Privacy(state=str(tmp_path / 'state'))
    p.master_key('correct horse')
    assert p.key_state() == {'exists': True, 'passphrase': True,
                             'created': p.key_state()['created']}
    p._key = None
    with pytest.raises(SealError):
        p.master_key()
    p._key = None
    with pytest.raises(SealError):
        p.master_key('wrong horse')
    p._key = None
    assert len(p.master_key('correct horse')) == 32


def test_key_export_import_round_trip(tmp_path):
    a = Privacy(state=str(tmp_path / 'a'))
    b = Privacy(state=str(tmp_path / 'b'))
    exported = a.key_export() if a._key_path.exists() else (
        a.master_key() and a.key_export())
    b.key_import(exported)
    assert b.master_key() == a.master_key()


# ── the api routes ───────────────────────────────────────────────────

def test_api_reads_are_open_and_writes_are_gated():
    from fastapi.testclient import TestClient
    from src.api.api import app
    c = TestClient(app)

    r = c.get('/modules').json()
    assert r['default'] in ('public', 'private')
    assert isinstance(r['modules'], list)

    # no key at all over HTTP is a stranger, never the host
    for url, body in (('/modules/visibility', {'visibility': 'private'}),
                      ('/modules/agent/visibility', {'visibility': 'private'}),
                      ('/modules/agent/seal', {}),
                      ('/privacy/key', {'op': 'export'})):
        assert c.post(url, json=body).json()['code'] == 401

    # an unverifiable key is not the owner either
    assert c.post('/modules/agent/visibility',
                  json={'visibility': 'private', 'key': '0x' + 'ab' * 20}
                  ).json()['code'] in (401, 403)
