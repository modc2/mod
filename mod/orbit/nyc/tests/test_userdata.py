"""The owner's data store: gating, validation, and layer integration."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nycgis import userdata as U


FC = {'type': 'FeatureCollection', 'features': [
    {'type': 'Feature', 'properties': {'name': 'A'},
     'geometry': {'type': 'Point', 'coordinates': [-73.98, 40.75]}},
    {'type': 'Feature', 'properties': {'name': 'B'},
     'geometry': {'type': 'Point', 'coordinates': [-73.95, 40.70]}},
]}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own store + cache and starts outside the write grant."""
    from nycgis import sources as S
    monkeypatch.setattr(U, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(S, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.delenv('NYC_DATA_WRITE', raising=False)


@pytest.fixture
def as_owner():
    token = U.grant_writer(True)
    yield
    U.reset_writer(token)


# ── the gate ─────────────────────────────────────────────────────────────

def test_write_needs_a_grant():
    with pytest.raises(U.AuthError, match='owner-only'):
        U.add({'title': 'Nope', 'geojson': FC})


def test_remove_needs_a_grant():
    with pytest.raises(U.AuthError):
        U.remove('anything')


def test_env_var_is_the_chat_agents_grant(monkeypatch):
    monkeypatch.setenv('NYC_DATA_WRITE', '1')
    rec = U.add({'title': 'Via Env', 'geojson': FC})
    assert rec['slug'] == 'via-env'


def test_verify_rejects_garbage():
    assert U.verify(None) is None
    assert U.verify('') is None
    assert U.verify('Bearer not-a-token') is None
    assert U.is_owner_token('Bearer not-a-token') is False


def test_identity_anonymous_is_nobody_not_an_error():
    ident = U.identity(None)
    assert ident['address'] is None
    assert ident['is_owner'] is False
    assert ident['writable'] is False
    assert ident['unlocks'] == []
    assert ident['mod'] == 'nyc'
    assert ident['token_max_age'] == U.TOKEN_MAX_AGE


def test_identity_garbage_token_is_nobody():
    ident = U.identity('Bearer not-a-token')
    assert ident['address'] is None
    assert ident['is_owner'] is False


def test_identity_owner_env_names_the_owner(monkeypatch):
    # Identity only REPORTS; writes still go through the token/grant gates.
    monkeypatch.setenv('NYC_OWNER', '0xABCDEF0123456789abcdef0123456789ABCDEF01')
    ident = U.identity(None)
    assert ident['owner'] == '0xabcdef0123456789abcdef0123456789abcdef01'
    assert ident['is_owner'] is False


# ── adding ───────────────────────────────────────────────────────────────

def test_add_inline_geojson_roundtrips(as_owner):
    rec = U.add({'title': 'My Points', 'description': 'two dots', 'geojson': FC})
    assert rec['kind'] == 'geojson'
    assert rec['geometry'] == 'point'
    assert rec['features'] == 2
    assert U.slugs() == ['my-points']
    assert len(U.data('my-points')['features']) == 2
    # the record on disk never carries the FeatureCollection itself
    assert '_fc' not in json.loads((U.DATA_DIR / 'my-points.json').read_text())


def test_exactly_one_source(as_owner):
    with pytest.raises(ValueError, match='exactly one'):
        U.add({'title': 'Two Sources', 'geojson': FC, 'url': 'https://x/y.geojson'})
    with pytest.raises(ValueError, match='exactly one'):
        U.add({'title': 'No Source'})


def test_builtin_ids_are_reserved(as_owner):
    with pytest.raises(ValueError, match='built-in'):
        U.add({'title': 'Parks', 'geojson': FC})


def test_bad_geojson_is_rejected(as_owner):
    with pytest.raises(ValueError, match='FeatureCollection'):
        U.add({'title': 'Bad', 'geojson': {'type': 'Feature'}})
    with pytest.raises(ValueError, match='no features'):
        U.add({'title': 'Empty', 'geojson': {'type': 'FeatureCollection', 'features': []}})


def test_url_must_be_https(as_owner):
    with pytest.raises(ValueError, match='https'):
        U.add({'title': 'Plain', 'url': 'http://example.com/x.geojson'})


def test_dataset_limit(as_owner, monkeypatch):
    monkeypatch.setattr(U, 'MAX_DATASETS', 1)
    U.add({'title': 'First One', 'geojson': FC})
    with pytest.raises(ValueError, match='limit'):
        U.add({'title': 'Second One', 'geojson': FC})


def test_url_kind_fetches_and_caches(as_owner, monkeypatch):
    monkeypatch.setattr(U, '_fetch_url', lambda url: FC)
    rec = U.add({'title': 'Remote Set', 'url': 'https://example.com/x.geojson'})
    assert rec['kind'] == 'url'
    assert rec['source']['portal'] == 'example.com'
    assert len(U.data('remote-set')['features']) == 2


# ── the layer surface ────────────────────────────────────────────────────

def test_catalog_entries_shape(as_owner):
    U.add({'title': 'My Points', 'geojson': FC})
    (entry,) = U.catalog_entries()
    assert entry['id'] == 'my-points'
    assert entry['category'] == 'Your data'
    assert entry['custom'] is True
    assert entry['endpoint'] == '/layers/my-points'
    assert entry['kind'] == 'point' and entry['geometry'] == 'point'


def test_layers_module_serves_saved_datasets(as_owner):
    from nycgis import layers as L
    U.add({'title': 'My Points', 'geojson': FC})
    cat = L.catalog()
    assert any(l['id'] == 'my-points' for l in cat['layers'])
    assert any(c['name'] == 'Your data' for c in cat['categories'])
    assert len(L.get('my-points')['features']) == 2
    # short browser cache: owner data can change at any moment
    assert 'max-age=120' in L.cache_control('my-points', 'default')


def test_scene_layer_ids_include_saved(as_owner):
    from nycgis import scene
    U.add({'title': 'My Points', 'geojson': FC})
    assert 'my-points' in scene._layer_ids()


def test_unknown_layer_still_raises(as_owner):
    from nycgis import layers as L
    with pytest.raises(KeyError):
        L.get('never-saved')


# ── removing and refreshing ──────────────────────────────────────────────

def test_remove_deletes_everything(as_owner):
    U.add({'title': 'My Points', 'geojson': FC})
    out = U.remove('my-points')
    assert out['removed'] == 'my-points'
    assert U.slugs() == []
    assert not (U.DATA_DIR / 'my-points.geojson').exists()


def test_remove_unknown_names_the_saved_list(as_owner):
    with pytest.raises(KeyError, match='no saved dataset'):
        U.remove('ghost')


def test_refresh_geojson_is_a_noop(as_owner):
    U.add({'title': 'My Points', 'geojson': FC})
    out = U.refresh('my-points')
    assert out['refreshed'] is False
    assert out['features'] == 2


# ── the tools ────────────────────────────────────────────────────────────

def test_tools_registered_and_annotated():
    from nycgis import tools
    by_name = {t.name: t for t in tools.TOOLS}
    assert {'nyc_data', 'nyc_add_data', 'nyc_remove_data'} <= set(by_name)
    assert by_name['nyc_add_data'].annotations['readOnlyHint'] is False
    assert by_name['nyc_remove_data'].annotations['destructiveHint'] is True
    assert by_name['nyc_layers'].annotations['readOnlyHint'] is True


def test_tool_roundtrip(as_owner):
    from nycgis import tools
    out = tools.call_tool('nyc_add_data', {'title': 'Tool Points',
                                           'geojson': FC})
    assert out['saved']['slug'] == 'tool-points'
    assert 'tool-points' in out['note']
    listing = tools.call_tool('nyc_data', {})
    assert listing['count'] == 1
    assert tools.call_tool('nyc_remove_data', {'slug': 'tool-points'})['removed'] == 'tool-points'
