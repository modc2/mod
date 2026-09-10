"""Tests that pass on a machine with no GPU and no FreeToken installed.

That constraint is the point rather than a compromise: the half of this module
that drives an engine is stdlib over HTTP, so it is testable anywhere, and the
half that touches the machine is tested by asserting on the commands it *would*
run instead of running them.
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from src import boxes, catalog, client, engine, install, preflight, state  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the operator's real ~/.mod/freetoken."""
    monkeypatch.setenv('FREETOKEN_DIR', str(tmp_path / 'state'))
    yield


# ── a stand-in engine ────────────────────────────────────────────────

class _Fake(http.server.BaseHTTPRequestHandler):
    """Enough of a FreeToken server to prove the client speaks to one."""

    calls: list = []

    def log_message(self, *_a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, events):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        for event in events:
            self.wfile.write(f'data: {json.dumps(event)}\n\n'.encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.close_connection = True

    def do_GET(self):
        type(self).calls.append(('GET', self.path))
        if self.path == '/health':
            return self._json(200, {'status': 'ok', 'model': 'Qwen/Qwen3.6-35B-A3B',
                                    'version': '0.9.0'})
        if self.path == '/v1/models':
            return self._json(200, {'object': 'list',
                                    'data': [{'id': 'Qwen3.6-35B-A3B'}]})
        if self.path == '/v1/stats':
            return self._json(200, {'decode_tokens_per_s': 41.2})
        if self.path == '/v1/cache/status':
            return self._json(200, {'moe': {'slots': 1024}, 'kv': {'tokens': 131072}})
        if self.path.startswith('/v1/requests'):
            return self._json(200, {'entries': [], 'next_cursor': 0})
        if self.path == '/engine/status':
            return self._json(200, {'state': 'running', 'model': 'Qwen3.6-35B-A3B',
                                    'port': 1919})
        self._json(404, {'error': 'nope'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        type(self).calls.append(('POST', self.path, body))
        if self.path == '/v1/chat/completions' and body.get('stream'):
            return self._sse([{'choices': [{'delta': {'content': word}}]}
                              for word in ('a ', 'MoE ', 'model')])
        if self.path == '/v1/chat/completions':
            return self._json(200, {'choices': [{'message': {'role': 'assistant',
                                                             'content': 'a MoE model'}}]})
        if self.path == '/v1/cache/rebuild':
            return self._json(200, {'rebuilt': body})
        if self.path == '/engine/start':
            return self._json(200, {'started': body})
        self._json(404, {'error': 'nope'})


@pytest.fixture
def fake():
    _Fake.calls = []
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    yield {'name': 'fake', 'url': url, 'daemon': url, 'token': None}
    server.shutdown()


# ── preflight ────────────────────────────────────────────────────────

def test_preflight_answers_without_a_gpu():
    report = preflight.report()
    assert isinstance(report['can_serve_here'], bool)
    assert {'os', 'arch', 'python', 'nvidia_gpu', 'driver', 'cuda_toolkit'} <= {
        c['check'] for c in report['checks']}
    for check in report['checks']:
        assert set(check) >= {'check', 'ok', 'found', 'want', 'blocking'}


def test_preflight_blocking_only_counts_hard_requirements():
    report = preflight.report()
    advisory = {c['check'] for c in report['checks'] if not c['blocking']}
    assert 'uv' in advisory and 'host_ram' in advisory
    assert report['can_serve_here'] == (not report['blocking'])


# ── boxes ────────────────────────────────────────────────────────────

def test_a_fresh_state_dir_seeds_the_local_box():
    listing = boxes.listing()
    assert listing['default'] == 'local'
    assert listing['boxes'][0]['url'] == f'http://127.0.0.1:{boxes.SERVE_PORT}'


def test_add_use_and_drop():
    boxes.add('gpu', url='gpu.lan:1919', note='the loud one')
    assert boxes.get('gpu')['url'] == 'http://gpu.lan:1919'      # scheme filled in
    boxes.use('gpu')
    assert boxes.default_name() == 'gpu'
    boxes.drop('gpu')
    with pytest.raises(KeyError):
        boxes.get('gpu')
    assert boxes.default_name() == 'local'                       # default fell back


def test_a_daemon_only_box_infers_the_serve_port():
    added = boxes.add('d', daemon='http://gpu.lan:1900')
    assert boxes.get('d')['url'] == f'http://gpu.lan:{boxes.SERVE_PORT}'
    assert added['token'] is None


def test_the_token_is_never_returned():
    boxes.add('secret', url='http://gpu.lan:1919', token='hunter2')
    assert boxes.get('secret')['token'] == 'hunter2'             # kept on disk
    listed = [b for b in boxes.listing()['boxes'] if b['name'] == 'secret'][0]
    assert listed['token'] == '••••'
    assert 'hunter2' not in json.dumps(boxes.listing())


def test_resolve_takes_a_name_a_url_or_nothing():
    boxes.add('gpu', url='http://gpu.lan:1919', use=True)
    assert boxes.resolve()['name'] == 'gpu'
    assert boxes.resolve('gpu')['url'] == 'http://gpu.lan:1919'
    ad_hoc = boxes.resolve('http://other:1919')
    assert ad_hoc['url'] == 'http://other:1919' and ad_hoc['note'] == 'ad hoc'


def test_a_box_needs_at_least_one_url():
    with pytest.raises(ValueError):
        boxes.add('empty')


# ── the client ───────────────────────────────────────────────────────

def test_probe_reads_the_engine(fake):
    card = client.probe(fake)
    assert card['up'] and card['model'] == 'Qwen/Qwen3.6-35B-A3B'
    assert card['steerable'] and card['engine']['state'] == 'running'
    assert card['ms'] is not None


def test_probe_never_raises_on_a_dead_box():
    card = client.probe({'name': 'gone', 'url': 'http://127.0.0.1:9'}, timeout=0.4)
    assert card['up'] is False and card['error']
    assert card['model'] is None


def test_chat_uses_the_served_name_when_none_is_given(fake):
    answer = client.chat(fake, [{'role': 'user', 'content': 'what is a MoE model'}])
    assert answer['choices'][0]['message']['content'] == 'a MoE model'
    sent = [c for c in _Fake.calls if c[0] == 'POST'][0][2]
    assert sent['model'] == 'Qwen3.6-35B-A3B'                    # read, not guessed


def test_unreachable_is_its_own_error():
    with pytest.raises(client.Unreachable):
        client.health({'name': 'x', 'url': 'http://127.0.0.1:9'}, timeout=0.4)


def test_refused_carries_the_status(fake):
    with pytest.raises(client.Refused) as caught:
        client.call(fake['url'], '/v1/nope')
    assert caught.value.status == 404


def test_cache_rebuild_understands_k_and_m_suffixes(fake):
    client.cache_rebuild(fake, moe=2048, kv='200k')
    sent = [c for c in _Fake.calls if c[1] == '/v1/cache/rebuild'][0][2]
    assert sent == {'moe': 2048, 'kv': 200_000, 'wait': 300}


def test_cache_rebuild_refuses_an_empty_request(fake):
    with pytest.raises(ValueError):
        client.cache_rebuild(fake)


def test_a_box_without_a_daemon_says_so():
    with pytest.raises(client.Unreachable, match='no daemon url'):
        client.engine_status({'name': 'x', 'url': 'http://x:1919', 'daemon': None})


def test_the_daemon_token_goes_out_as_x_ft_token(fake):
    fake = {**fake, 'token': 'hunter2'}

    class _Capture(http.server.BaseHTTPRequestHandler):
        seen = None

        def log_message(self, *_a):
            pass

        def do_GET(self):
            type(self).seen = self.headers      # case-insensitive, as HTTP is
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client.health({'name': 'x', 'url': f'http://127.0.0.1:{server.server_address[1]}',
                   'token': 'hunter2'})
    server.shutdown()
    assert _Capture.seen['X-FT-Token'] == 'hunter2'


# ── the serve flags ──────────────────────────────────────────────────

def test_serve_argv_is_just_the_model_by_default():
    assert engine.serve_argv('Qwen/Qwen3.6-35B-A3B') == [
        'serve', '--model', 'Qwen/Qwen3.6-35B-A3B']


def test_serve_argv_maps_underscores_to_the_documented_flags():
    argv = engine.serve_argv('m', moe_backend='hybrid', memory_ratio=0.85,
                             attn='trtllm', graph=256)
    assert '--moe-backend' in argv and argv[argv.index('--moe-backend') + 1] == 'hybrid'
    assert '--memory-ratio' in argv and '--attention-backend' in argv
    assert '--cuda-graph-max-bs' in argv


def test_serve_argv_emits_switches_without_a_value():
    argv = engine.serve_argv('m', moe_cache_auto=True, enable_cache_report=False)
    assert '--moe-cache-auto' in argv
    assert '--enable-cache-report' not in argv
    assert 'True' not in argv


def test_serve_argv_rejects_a_typo_rather_than_passing_it_on():
    with pytest.raises(ValueError, match='not a `ft serve` flag'):
        engine.serve_argv('m', moe_backned='hybrid')


def test_serve_argv_needs_a_model():
    with pytest.raises(ValueError):
        engine.serve_argv('')


def test_truthy_reads_cli_strings():
    assert engine.truthy('1') and engine.truthy('yes')
    assert not engine.truthy('false') and not engine.truthy('0')
    assert not engine.truthy('')


# ── starting an engine that cannot start ─────────────────────────────

def test_start_refuses_a_machine_that_fails_preflight(monkeypatch):
    monkeypatch.setattr(preflight, 'report', lambda: {
        'can_serve_here': False, 'verdict': 'no GPU', 'blocking': ['nvidia_gpu'],
        'checks': [{'check': 'nvidia_gpu', 'ok': False, 'blocking': True,
                    'found': None, 'want': 'a GPU'}]})
    result = engine.start('Qwen/Qwen3.6-35B-A3B')
    assert result['ok'] is False and result['blocking'] == ['nvidia_gpu']
    assert 'force=1' in result['override']
    assert not (state.logs() / 'serve.pid').exists()


def test_start_without_ft_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(preflight, 'report',
                        lambda: {'can_serve_here': True, 'verdict': 'ok',
                                 'blocking': [], 'checks': []})
    monkeypatch.setattr(install, 'ft_bin', lambda: None)
    result = engine.start('Qwen/Qwen3.6-35B-A3B')
    assert result['ok'] is False and 'm freetoken/install' in result['why']


def test_status_and_stop_on_an_engine_that_was_never_started():
    assert engine.status()['running'] is False
    assert engine.stop()['ok'] is False


# ── install, without installing ──────────────────────────────────────

def test_the_install_plan_targets_a_venv_of_its_own():
    plan = install.plan()
    assert plan['venv'].endswith('/venv')
    assert any('freetoken[accel]' in step for step in plan['steps'])
    assert all('sudo' not in step for step in plan['steps'])


def test_a_source_install_clones_the_upstream_repo():
    plan = install.plan(source=True)
    assert any('github.com/FlashML-org/FreeToken' in step for step in plan['steps'])
    assert any(' -e ' in step for step in plan['steps'])


def test_install_dry_run_runs_nothing():
    result = install.install(dry=True)
    assert result['dry_run'] and result['steps']
    assert not (state.logs() / 'install.pid').exists()


def test_status_reports_absence_plainly():
    status = install.status()
    assert set(status) >= {'installed', 'ft', 'version', 'installing', 'log'}
    assert status['installing'] is False


def test_run_without_ft_is_an_answer_not_a_crash(monkeypatch):
    monkeypatch.setattr(install, 'ft_bin', lambda: None)
    result = install.run(['ctl', 'health'])
    assert result['ok'] is False and 'not installed' in result['why']


# ── the catalog ──────────────────────────────────────────────────────

def test_the_known_good_table_matches_upstream_docs():
    families = {m['family'] for m in catalog.known()}
    assert {'DeepSeek-V4', 'GLM-5.2', 'gpt-oss', 'Gemma-4'} <= families
    for model in catalog.known():
        assert model['checkpoints'] and all('/' in c for c in model['checkpoints'])
    assert 'Qwen/Qwen3.6-35B-A3B' in [c for m in catalog.known() for c in m['checkpoints']]


def test_moe_only_drops_the_dense_family():
    assert all(m['moe'] for m in catalog.known(moe_only=True))
    assert 'Qwen3.6 dense' not in {m['family'] for m in catalog.known(moe_only=True)}


def test_every_moe_backend_is_described():
    assert set(catalog.MOE_BACKENDS) == {'auto', 'fused', 'offload', 'cpu', 'hybrid'}


def test_local_scan_tells_ftw_from_safetensors(tmp_path, monkeypatch):
    ftw = tmp_path / 'models' / 'converted'
    raw = tmp_path / 'models' / 'raw'
    ftw.mkdir(parents=True)
    raw.mkdir(parents=True)
    (ftw / 'freetoken-00000.ftw').write_bytes(b'x' * 16)
    (raw / 'model.safetensors').write_bytes(b'x' * 16)
    (tmp_path / 'models' / 'empty').mkdir()
    monkeypatch.setenv('HF_HOME', str(tmp_path / 'no-hf-cache'))   # not the real one
    monkeypatch.setenv('FREETOKEN_MODELS', str(tmp_path / 'models'))
    found = {Path(entry['path']).name: entry['kind'] for entry in catalog.local()}
    assert found == {'converted': 'ftw', 'raw': 'safetensors'}


def test_catalog_marks_what_is_already_on_disk(tmp_path, monkeypatch):
    snap = (tmp_path / 'hub' / 'models--Qwen--Qwen3.6-35B-A3B' / 'snapshots' / 'abc')
    snap.mkdir(parents=True)
    (snap / 'model.safetensors').write_bytes(b'x')
    monkeypatch.setenv('HF_HOME', str(tmp_path))
    table = catalog.catalog(size=False)
    qwen = [f for f in table['known_good'] if f['family'].startswith('Qwen3.6 /')][0]
    assert 'Qwen/Qwen3.6-35B-A3B' in qwen['local']


# ── the module surface ───────────────────────────────────────────────

def test_the_anchor_exposes_what_config_json_advertises():
    sys.path.insert(0, str(HERE))
    import mod as anchor
    declared = json.loads((HERE / 'config.json').read_text())['fns']
    have = {f for f in dir(anchor.Mod) if not f.startswith('_')}
    assert set(declared) <= have, f'missing: {set(declared) - have}'


def test_info_works_with_no_engine_anywhere():
    import mod as anchor
    card = anchor.Mod().info()
    assert card['name'] == 'freetoken'
    assert card['wraps']['repo'] == 'https://github.com/FlashML-org/FreeToken'
    assert isinstance(card['this_machine']['can_serve_here'], bool)
    assert card['known_good_families']


def test_health_sorts_the_boxes_up_from_the_boxes_down(fake):
    import mod as anchor
    boxes.add('fake', url=fake['url'], daemon=fake['daemon'], use=True)
    boxes.add('gone', url='http://127.0.0.1:9')
    report = anchor.Mod().health()
    assert 'fake' in report['boxes_up'] and 'gone' in report['boxes_down']


def test_ask_returns_the_text_and_start_explains_a_remote_without_a_daemon(fake):
    import mod as anchor
    module = anchor.Mod()
    boxes.add('fake', url=fake['url'], use=True)
    assert module.ask('what is a MoE model') == 'a MoE model'
    boxes.add('remote', url='http://gpu.lan:1919', use=True)
    result = module.start('Qwen/Qwen3.6-35B-A3B')
    assert result['ok'] is False and 'ft daemon' in result['why']


def test_start_goes_through_the_daemon_when_there_is_one(fake):
    import mod as anchor
    boxes.add('fake', url=fake['url'], daemon=fake['daemon'], use=True)
    result = anchor.Mod().start('Qwen/Qwen3.6-35B-A3B', moe_backend='hybrid')
    assert result['via'] == 'daemon'
    assert result['args'] == ['--moe-backend', 'hybrid']         # model is not repeated
    assert result['result']['started']['model'] == 'Qwen/Qwen3.6-35B-A3B'


# ── the HTTP surface ─────────────────────────────────────────────────

@pytest.fixture
def http_client():
    fastapi_testclient = pytest.importorskip('fastapi.testclient')
    from src import api
    return fastapi_testclient.TestClient(api.app)


def test_the_api_describes_itself_with_no_engine_anywhere(http_client):
    body = http_client.get('/').json()
    assert body['name'] == 'freetoken'
    assert body['wraps']['license'] == 'Apache-2.0'
    assert 'provider' in body['endpoints']


def test_reads_work_from_anywhere_writes_do_not(http_client):
    """TestClient is not loopback, which is exactly the case worth asserting on."""
    assert http_client.get('/preflight').status_code == 200
    assert http_client.get('/boxes?probe=false').status_code == 200
    assert http_client.post('/boxes', json={'name': 'x', 'url': 'http://x:1919'}
                            ).status_code == 403
    assert http_client.post('/start', json={'model': 'x'}).status_code == 403
    assert http_client.post('/install', json={}).status_code == 403


def test_a_dead_box_is_a_502_not_a_500(http_client):
    boxes.add('gone', url='http://127.0.0.1:9', use=True)
    response = http_client.get('/stats')
    assert response.status_code == 502
    assert response.json()['error'] == 'Unreachable'


def test_the_provider_surface_forwards_a_stream_chunk_for_chunk(fake, http_client):
    boxes.add('fake', url=fake['url'], use=True)
    with http_client.stream('POST', '/v1/chat/completions',
                            json={'model': 'Qwen3.6-35B-A3B', 'stream': True,
                                  'messages': [{'role': 'user', 'content': 'hi'}]}) as r:
        assert r.status_code == 200
        assert r.headers['x-freetoken-box'] == 'fake'
        body = ''.join(r.iter_text())
    deltas = [json.loads(line[5:])['choices'][0]['delta']['content']
              for line in body.splitlines()
              if line.startswith('data:') and '[DONE]' not in line]
    assert ''.join(deltas) == 'a MoE model'


def test_the_provider_surface_forwards_a_plain_request(fake, http_client):
    boxes.add('fake', url=fake['url'], use=True)
    body = http_client.post('/v1/chat/completions',
                            json={'model': 'Qwen3.6-35B-A3B',
                                  'messages': [{'role': 'user', 'content': 'hi'}]}).json()
    assert body['choices'][0]['message']['content'] == 'a MoE model'
    forwarded = [c for c in _Fake.calls if c[1] == '/v1/chat/completions'][0][2]
    assert forwarded['model'] == 'Qwen3.6-35B-A3B'               # unchanged in flight
