"""
Tests for shelf.

The interesting assertions here are the negative ones. It is easy to write a
state browser that lists things; the whole question is whether it refuses to
read the file it must not read, refuses to walk out of its root, refuses to
call a healthy record corrupt, and refuses to delete something it only thinks
is garbage. Each of those has a test because each of them was, at some point in
writing this module, wrong.
"""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import blobs, keys, redact, snapshot, space  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    """A store shaped like the real one: blobs, records, an index, a secret."""
    root = tmp_path / 'store'
    (root / 'blobs').mkdir(parents=True)
    (root / 'thing' / 'records').mkdir(parents=True)

    payload = b'hello wasm'
    good = hashlib.sha256(payload).hexdigest()
    (root / 'blobs' / f'{good}.json').write_text(
        json.dumps({'b64': base64.b64encode(payload).decode()}))

    # A record filed under the blob's id — metadata, not bytes.
    (root / 'thing' / 'records' / f'{good}.json').write_text(
        json.dumps({'id': good, 'engine': 'wasm', 'bytes': len(payload)}))

    (root / 'thing' / 'index.json').write_text(json.dumps({'records': [good]}))
    (root / 'owner.json').write_text(json.dumps({'address': '0xdead'}))
    (root / 'plain.json').write_text(json.dumps(
        {'name': 'visible', 'api_key': 'sk-live-must-not-appear', 'n': 3}))
    return root, good, payload


# ── redaction ────────────────────────────────────────────────

def test_secret_fields_become_fingerprints_not_asterisks():
    out = redact.value('', {'name': 'ok', 'api_key': 'sk-live-secret'})
    assert out['name'] == 'ok'
    assert 'sk-live-secret' not in json.dumps(out)
    assert out['api_key'].startswith('sha256:')


def test_the_same_secret_fingerprints_the_same_way():
    """The property that makes a fingerprint worth more than a mask: you can
    tell two modules were handed one key without seeing it."""
    assert redact.fingerprint('shared') == redact.fingerprint('shared')
    assert redact.fingerprint('a') != redact.fingerprint('b')


def test_nested_secrets_are_caught_by_their_own_key():
    out = redact.value('', {'cfg': {'deep': {'password': 'hunter2'}}})
    assert 'hunter2' not in json.dumps(out)


def test_secret_files_are_recognised_by_name_and_by_folder():
    assert redact.sensitive_file('/x/server.secret')
    assert redact.sensitive_file('/x/id.pem')
    assert redact.sensitive_file('/x/vault/anything.json')
    assert not redact.sensitive_file('/x/listings/hello.json')


def test_a_secret_file_is_never_opened(store, monkeypatch):
    """Stronger than redaction: assert the bytes are not read at all."""
    root, _good, _payload = store
    opened = []
    real_open = open

    def watched(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr('builtins.open', watched)
    out = keys.read('owner', root=str(root))
    assert out['secret'] is True
    assert out['value'] == redact.REDACTED
    assert not any(name.endswith('owner.json') for name in opened)


def test_redacted_document_still_reports_the_readable_fields(store):
    root, _good, _payload = store
    out = keys.read('plain', root=str(root))
    assert out['value']['name'] == 'visible'
    assert 'sk-live-must-not-appear' not in json.dumps(out)
    assert out['redacted'] is True


# ── containment ──────────────────────────────────────────────

@pytest.mark.parametrize('attack', [
    '../../.ssh/id_rsa', '../owner', '/etc/passwd', 'thing/../../../etc/passwd',
])
def test_keys_cannot_escape_their_root(store, attack):
    root, _good, _payload = store
    assert keys.key2path(str(root), attack) is None
    assert keys.read(attack, root=str(root))['found'] is False


# ── blob integrity ───────────────────────────────────────────

def test_records_are_not_mistaken_for_corrupt_blobs(store):
    """The bug this module shipped with once: a record filed under a blob id
    is not bytes that should hash to that id."""
    root, _good, _payload = store
    report = blobs.verify(str(root))
    assert report['records'] == 1
    assert report['corrupt'] == []
    assert report['healthy'] is True


def test_a_blob_whose_bytes_changed_is_caught(store):
    root, good, _payload = store
    path = root / 'blobs' / f'{good}.json'
    path.write_text(json.dumps({'b64': base64.b64encode(b'different').decode()}))
    report = blobs.verify(str(root))
    assert report['healthy'] is False
    assert report['corrupt'][0]['claimed'] == good
    assert report['corrupt'][0]['actual'] == hashlib.sha256(b'different').hexdigest()


def test_a_referenced_blob_is_not_an_orphan(store):
    root, _good, _payload = store
    assert blobs.orphans(str(root))['count'] == 0


def test_an_unreferenced_blob_is_an_orphan(store):
    root, _good, _payload = store
    loose = hashlib.sha256(b'nobody wants me').hexdigest()
    (root / 'blobs' / f'{loose}.json').write_text(
        json.dumps({'b64': base64.b64encode(b'nobody wants me').decode()}))
    report = blobs.orphans(str(root))
    assert [o['id'] for o in report['orphans']] == [loose]


def test_gc_is_dry_by_default_and_spares_young_blobs(store):
    root, _good, _payload = store
    loose = hashlib.sha256(b'fresh').hexdigest()
    target = root / 'blobs' / f'{loose}.json'
    target.write_text(json.dumps({'b64': base64.b64encode(b'fresh').decode()}))

    plan = blobs.gc(str(root))
    assert plan['deleted'] is False
    assert plan['skipped_young'] == 1        # written seconds ago
    assert target.exists()

    # Confirmed, but still age-gated: nothing goes without meeting the bar.
    blobs.gc(str(root), confirm=True)
    assert target.exists()

    # Aged past the gate, a confirmed sweep takes it.
    os.utime(target, (0, 0))
    done = blobs.gc(str(root), confirm=True)
    assert done['deleted'] is True and len(done['removed']) == 1
    assert not target.exists()


# ── snapshots ────────────────────────────────────────────────

def test_snapshot_is_deterministic(store):
    root, _good, _payload = store
    first = snapshot._tarball(str(root))[0]
    second = snapshot._tarball(str(root))[0]
    assert first == second, 'same tree must produce the same bytes, and so the same CID'


def test_snapshot_excludes_secrets_rather_than_redacting_them(store):
    root, _good, _payload = store
    blob, meta = snapshot._tarball(str(root))
    assert 'owner.json' in meta['skipped']
    assert b'0xdead' not in blob


def test_snapshot_round_trips_filenames_exactly(store, tmp_path):
    """A backup that renames `registry.json` to `registry` is not a backup."""
    import io
    import tarfile
    root, _good, _payload = store
    blob, _meta = snapshot._tarball(str(root))
    tar = tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz')
    names = {m.name for m in tar.getmembers() if m.isfile()}
    assert 'thing/index.json' in names
    assert 'plain.json' in names

    out = tmp_path / 'restored'
    out.mkdir()
    tar.extractall(out, filter='data')
    assert (out / 'thing' / 'index.json').read_bytes() == (root / 'thing' / 'index.json').read_bytes()


def test_import_mod_is_not_shadowed_by_our_own_mod_py():
    """The server puts this module's root on sys.path so it can import `src`.
    A bare `import mod` then finds shelf/mod.py, which has no `.mod()`, and
    pinning silently degraded to "localfs unavailable" — working from the CLI
    and not from the API, which is the shape of bug that costs an afternoon.
    """
    from src import protocol
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        package = protocol.protocol()
        assert package is not None, 'protocol package must resolve'
        assert hasattr(package, 'mod'), 'resolved the wrong `mod` — our own mod.py won'
    finally:
        sys.path.pop(0)


def test_the_envelope_survives_a_store_that_is_not_binary_safe():
    """localfs decodes bytes to str lossily; base64-in-JSON is why restore works."""
    blob = bytes(range(256))
    assert snapshot._unwrap(snapshot._wrap(blob, {})) == blob


# ── space ────────────────────────────────────────────────────

def test_space_counts_without_reading(tmp_path, monkeypatch):
    home = tmp_path / 'mod'
    (home / 'alpha').mkdir(parents=True)
    (home / 'alpha' / 'a.bin').write_bytes(b'x' * 100)
    (home / 'beta' / 'node_modules').mkdir(parents=True)
    (home / 'beta' / 'node_modules' / 'dep.js').write_bytes(b'y' * 500)

    def refuse(*_args, **_kwargs):
        raise AssertionError('space must not open files')

    monkeypatch.setattr('builtins.open', refuse)
    report = space.scan(home=str(home))
    rows = {r['module']: r for r in report['modules']}
    assert rows['alpha']['bytes'] == 100
    assert rows['beta']['vendor_bytes'] == 500, 'node_modules is vendored, not authored'
    assert rows['beta']['own_bytes'] == 0


def test_prefixes_and_keys_agree_with_the_disk(store):
    root, good, _payload = store
    prefixes = keys.prefixes(str(root))
    assert {p['prefix'] for p in prefixes['prefixes']} >= {'blobs', 'thing'}
    listed = keys.keys(root=str(root), prefix='thing')
    assert f'thing/records/{good}' in {k['key'] for k in listed['keys']}


def test_grep_finds_a_record_but_skips_secret_files(store):
    root, _good, _payload = store
    assert keys.grep('visible', root=str(root))['hits'], 'should find the plain record'
    hit = keys.grep('0xdead', root=str(root))
    assert hit['hits'] == [], 'owner.json is secret and must not be searched'
    assert hit['skipped'] >= 1
