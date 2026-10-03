"""
tests for fleet providers — other model mods as agent providers

covers:
    - discovery by reading source (chat + models ⇒ provider; native skipped)
    - the worker's shape adapters (model ids, completion text)
    - a real worker process against a fake module (models, complete, a crash)
    - Mod plumbing: `mod:<name>` builds a FleetModel, key_info is keyless,
      the model default is the module's own, and runs are host-only

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_fleet.py -v
"""
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import fleet as fl
from src.fleet import Fleet, FleetModel, Worker, is_fleet, name_of
from src.fleet_worker import ids_of, text_of


def _mod(root: Path, name: str, body: str, cfg: dict = None):
    d = root / name
    d.mkdir(parents=True)
    (d / 'config.json').write_text(json.dumps(cfg or {'name': name, 'description': f'{name} models. more'}))
    (d / 'mod.py').write_text(textwrap.dedent(body))
    return d


CHAT_MOD = '''
class Mod:
    def models(self, kind='any'):
        return [{'id': 'a', 'kind': 'chat'}, {'id': 'img', 'kind': 'image'}, 'b']
    def chat(self, messages, model=None, max_tokens=None):
        return {'choices': [{'message': {'content': 'hi from ' + str(model)}}]}
'''


class TestDiscovery:
    def test_chat_and_models_make_a_provider(self, tmp_path):
        _mod(tmp_path, 'llmy', CHAT_MOD, {'name': 'llmy', 'description': 'LLM router. rest', 'default_model': 'a'})
        _mod(tmp_path, 'tooly', 'class Mod:\n    def ask(self, q):\n        pass\n')
        _mod(tmp_path, 'venice', CHAT_MOD)          # native: never listed twice
        rows = Fleet(tmp_path).scan()
        assert list(rows) == ['llmy']
        assert rows['llmy']['key'] == 'mod:llmy'
        assert rows['llmy']['hint'] == 'LLM router'
        assert rows['llmy']['default_model'] == 'a'

    def test_prefix_helpers(self):
        assert is_fleet('mod:chutes') and not is_fleet('openrouter') and not is_fleet(None)
        assert name_of('mod:chutes') == 'chutes'


class TestShapes:
    def test_ids(self):
        assert ids_of(['a', 'a', 'b']) == ['a', 'b']
        assert ids_of({'data': [{'id': 'x'}, {'id': 'y', 'type': 'image'}]}) == ['x']
        assert ids_of({'m1': {}, 'm2': {}}) == ['m1', 'm2']
        assert ids_of({'served': [{'repo': 'r'}, {'key': 'k'}], 'note': 'x'}) == ['k']

    def test_text(self):
        assert text_of('x') == 'x'
        assert text_of({'choices': [{'message': {'content': 'c'}}]}) == 'c'
        assert text_of({'ok': True, 'text': 't'}) == 't'
        assert text_of(iter(['a', 'b'])) == 'ab'
        with pytest.raises(RuntimeError):
            text_of({'ok': False, 'error': 'no key'})
        with pytest.raises(RuntimeError):
            text_of({'error': 'boom'})


@pytest.fixture
def fake_fleet(tmp_path, monkeypatch):
    """A worker that loads a fake module instead of the framework's."""
    shim = tmp_path / 'shim'
    shim.mkdir()
    # a stand-in `mod` package: m.mod(name) imports <root>/<name>/mod.py
    (shim / 'mod.py').write_text(textwrap.dedent(f'''
        import importlib.util
        def mod(name):
            spec = importlib.util.spec_from_file_location(name, {str(tmp_path)!r} + '/' + name + '/mod.py')
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            return m.Mod
    '''))
    monkeypatch.setenv('MOD_ROOT', str(shim))
    monkeypatch.setattr(fl, 'LOG_DIR', tmp_path / 'logs')
    _mod(tmp_path, 'llmy', CHAT_MOD)
    _mod(tmp_path, 'broken', 'raise ImportError("nope")\n')
    workers = fl.Workers()
    monkeypatch.setattr(fl, 'WORKERS', workers)
    yield workers
    for w in workers._w.values():
        w.stop()


class TestWorker:
    def test_models_and_complete(self, fake_fleet):
        w = fake_fleet.get('llmy')
        assert w.request('models', 30) == ['a', 'b']
        assert w.request('complete', 30, prompt='yo', model='a') == 'hi from a'
        assert w.alive()

    def test_fleet_model_forward(self, fake_fleet):
        assert FleetModel('mod:llmy').forward('yo', model='b') == 'hi from b'

    def test_load_failure_is_an_error_not_a_hang(self, fake_fleet):
        with pytest.raises(RuntimeError, match='could not load the broken module'):
            FleetModel('mod:broken').forward('yo')


class TestModPlumbing:
    @pytest.fixture
    def mod(self):
        from src.mod import Mod
        m = Mod.__new__(Mod)
        m._clients, m._client_why, m._session_keys = {}, {}, {}
        m._owner = '0xowner'
        m._co_owners = ()
        m.auth = None
        return m

    def test_builds_a_fleet_client(self, mod):
        c = mod._client('mod:llmy')
        assert isinstance(c, FleetModel) and c.name == 'llmy'
        assert mod.is_free_provider('mod:llmy')     # never billed to agent credit

    def test_key_info_is_keyless(self, mod):
        info = mod.key_info('mod:llmy')
        assert info['keyless'] and info['fleet'] and info['configured']

    def test_model_default_is_the_modules(self, mod, monkeypatch):
        monkeypatch.setattr(FleetModel, 'default_model', lambda self: 'dm')
        assert mod._model_for('mod:llmy', None) == 'dm'
        assert mod._model_for('mod:llmy', 'x') == 'x'
        # a model id from another provider's list is NOT swapped on a fleet mod
        any_or = mod.MODELS.get('openrouter', ['z'])[0]
        assert mod._model_for('mod:llmy', any_or) == any_or

    def test_runs_are_host_only(self, mod):
        with pytest.raises(PermissionError, match='only the host'):
            mod.run('hi', provider='mod:llmy', key='0xsomeoneelse')
