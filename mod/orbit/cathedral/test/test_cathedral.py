"""Tests for orbit/cathedral — key resolution, the spend guard, gates, ledger.

Everything here runs offline: HTTP is stubbed, and the vault/ledger are
redirected into tmp_path so a test run never touches ~/.mod/cathedral.
"""
import importlib.util
import json
from pathlib import Path

import pytest

MOD_PY = Path(__file__).resolve().parent.parent / 'mod.py'
spec = importlib.util.spec_from_file_location('cathedral_mod', MOD_PY)
cathedral = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cathedral)


# The live catalog's real shape: cc_gpu is not a top-level profile, it hangs
# off each profile's hardware_classes. Gates here are set to their PASSing
# values; individual tests knock one out at a time.
CATALOG = {'profiles': [
    {'id': 'attest.v1', 'availability': 'live_testing', 'pricing': {'amount_usd': 0.2},
     'hardware_classes': [
         {'id': 'tdx_cpu', 'execution_class': 'tdx_cpu', 'availability': 'live_testing'},
         {'id': 'confidential_gpu', 'execution_class': 'cc_gpu',
          'profile_id': cathedral.CC_GPU_PROFILE, 'availability': 'available',
          'customer_enabled': True, 'cathedral_evidence_status': 'PASS',
          'verifier_log_digest_evidence_status': 'PASS',
          'live_evidence_digest': 'sha256:' + 'a' * 64,
          'operations': {'create': True, 'get': True, 'cancel': True, 'retry': True, 'receipt': True},
          'runtime': {'image': 'pytorch/pytorch@sha256:beef'},
          'pricing': {'amount_usd': 3.0}},
     ]},
    {'id': 'custom.v1', 'availability': 'live_testing', 'resources': [
        {'cpu': 4, 'memory_gib': 16, 'price_usd_per_hour': 0.4},
        {'cpu': 8, 'memory_gib': 32, 'price_usd_per_hour': 0.8},
        {'cpu': 22, 'memory_gib': 88, 'price_usd_per_hour': 2.2},
        {'cpu': 44, 'memory_gib': 176, 'price_usd_per_hour': 4.4},
    ]},
]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test may reach the network — the catalog is served from CATALOG."""
    monkeypatch.setattr(cathedral.Mod, 'profiles', lambda self: CATALOG)
    monkeypatch.setattr(cathedral.requests, 'request',
                        lambda *a, **kw: pytest.fail('unstubbed HTTP call'))
    monkeypatch.setattr(cathedral.requests, 'get',
                        lambda *a, **kw: pytest.fail('unstubbed HTTP call'))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the vault + ledger at tmp_path."""
    monkeypatch.setattr(cathedral, 'KEYS_PATH', tmp_path / 'keys.json')
    monkeypatch.setattr(cathedral, 'LEDGER_PATH', tmp_path / 'ledger.json')
    monkeypatch.delenv('CATHEDRAL_API_KEY', raising=False)
    return tmp_path


def vault(store, mapping):
    (store / 'keys.json').write_text(json.dumps({
        acct: {'key': key, 'fingerprint': cathedral.Mod.fingerprint(key)}
        for acct, key in mapping.items()
    }))


# ── whose key pays ───────────────────────────────────────────────────────

def test_explicit_key_wins(store):
    vault(store, {'default': 'cat_sk_stored'})
    assert cathedral.Mod(key='cat_sk_explicit').key() == 'cat_sk_explicit'


def test_account_key_is_isolated(store):
    vault(store, {'alice': 'cat_sk_alice', 'bob': 'cat_sk_bob'})
    assert cathedral.Mod(account='alice').key() == 'cat_sk_alice'
    assert cathedral.Mod(account='bob').key() == 'cat_sk_bob'


def test_env_only_answers_for_default_account(store, monkeypatch):
    """A named account must never fall through to ambient credentials — that
    would bill the wrong person, the one thing this mod must not do."""
    monkeypatch.setenv('CATHEDRAL_API_KEY', 'cat_sk_env')
    assert cathedral.Mod().key() == 'cat_sk_env'
    with pytest.raises(ValueError, match='no Cathedral API key'):
        cathedral.Mod(account='carol').key()


def test_missing_key_names_the_fix(store):
    with pytest.raises(ValueError, match='cathedral/login'):
        cathedral.Mod().key()


def test_no_key_is_a_clean_error_not_a_traceback(store):
    """The commonest first-run state must not crash a paying call."""
    out = cathedral.Mod().credits()
    assert out['error'] == 'no api key' and 'cathedral/login' in out['detail']
    assert cathedral.Mod().rent(image='nginx', max_spend_usd=5.0)['pays'] is None


def test_fingerprint_is_stable_and_hides_the_key():
    fp = cathedral.Mod.fingerprint('cat_sk_secret')
    assert fp == cathedral.Mod.fingerprint('cat_sk_secret')
    assert fp.startswith('cat:') and 'secret' not in fp
    assert fp != cathedral.Mod.fingerprint('cat_sk_other')


def test_accounts_never_leaks_secrets(store):
    vault(store, {'alice': 'cat_sk_alice'})
    out = cathedral.Mod().accounts()
    assert 'cat_sk_alice' not in json.dumps(out)
    assert out['accounts'][0]['fingerprint'].startswith('cat:')


def test_login_rejects_a_non_cathedral_key(store):
    assert 'error' in cathedral.Mod().login('sk-not-cathedral')


def test_logout_forgets_only_that_account(store):
    vault(store, {'alice': 'cat_sk_alice', 'bob': 'cat_sk_bob'})
    cathedral.Mod(account='alice').logout()
    assert cathedral.Mod(account='bob').key() == 'cat_sk_bob'
    with pytest.raises(ValueError):
        cathedral.Mod(account='alice').key()


# ── spend guard ──────────────────────────────────────────────────────────

def test_guard_blocks_spend_over_the_threshold(store):
    cat = cathedral.Mod(key='cat_sk_x')
    out = cat.rent(image='nginx', max_spend_usd=5.0)
    assert out['error'] == 'confirmation required'
    assert out['price_usd'] == 5.0 and out['pays'].startswith('cat:')


def test_guard_passes_with_yes(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, m_, p, b=None, **kw: seen.update(body=b, path=p) or {'worker_id': 'wrk_1'})
    out = cathedral.Mod(key='cat_sk_x').rent(image='nginx', max_spend_usd=5.0, yes=True)
    assert out['worker_id'] == 'wrk_1'
    assert seen['body']['budget'] == {'max_spend_usd': 5.0, 'auto_stop': True}


def test_a_default_cpu_run_needs_no_confirmation(store, monkeypatch):
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, *a, **kw: {'worker_id': 'wrk_2'})
    assert cathedral.Mod(key='cat_sk_x').run()['worker_id'] == 'wrk_2'


# ── request shapes ───────────────────────────────────────────────────────

def test_run_builds_a_one_shot_attest_body(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, m_, p, b=None, **kw: seen.update(body=b, kw=kw) or {'worker_id': 'w'})
    cathedral.Mod(key='cat_sk_x').run(image='python:3.12-slim', command=['python', '-c', 'print(1)'], minutes=3)
    body = seen['body']
    assert body['profile'] == 'attest.v1'
    assert body['lifetime'] == {'mode': 'one_shot', 'reuse': 'forbidden', 'max_runtime_minutes': 3}
    assert body['workload']['command'] == ['python', '-c', 'print(1)']
    assert body['network'] == {'egress': 'none'}
    assert seen['kw']['idempotency_key'].startswith('run-')


def test_argv_accepts_list_json_shell_and_bare_python():
    assert cathedral._argv(['a', 'b']) == ['a', 'b']
    assert cathedral._argv('["a","b"]') == ['a', 'b']
    assert cathedral._argv('sh -c ls') == ['sh', '-c', 'ls']
    assert cathedral._argv('print(6*7)') == ['python', '-c', 'print(6*7)']
    assert cathedral._argv(None)[:2] == ['python', '-c']


def test_hybrid_gpu_quotes_a_ceiling(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, m_, p, b=None, **kw: seen.update(body=b) or {'worker_id': 'w'})
    cathedral.Mod(key='cat_sk_x').rent(image='img', gpu='H100', accepted_max_hourly_usd=4.0,
                                       max_spend_usd=5.0, yes=True)
    assert seen['body']['resources']['gpu'] == {
        'mode': 'hybrid', 'type': 'H100', 'provider': 'cloud', 'accepted_max_hourly_usd': 4.0}


# ── confidential-GPU gates ───────────────────────────────────────────────

def _catalog_with(**overrides) -> dict:
    """CATALOG with the cc_gpu hardware-class entry patched."""
    gpu = dict(CATALOG['profiles'][0]['hardware_classes'][1], **overrides)
    attest = dict(CATALOG['profiles'][0],
                  hardware_classes=[CATALOG['profiles'][0]['hardware_classes'][0], gpu])
    return {'profiles': [attest, CATALOG['profiles'][1]]}


def test_cc_gpu_is_found_under_hardware_classes():
    entry = cathedral._cc_gpu_entry(CATALOG)
    assert entry['profile_id'] == cathedral.CC_GPU_PROFILE


def test_gpu_ready_reads_every_gate(store):
    out = cathedral.Mod(key='cat_sk_x').gpu_ready()
    assert out['ready'] is True and out['runtime_image'] == 'pytorch/pytorch@sha256:beef'


@pytest.mark.parametrize('field,bad', [
    ('availability', 'unavailable'),
    ('customer_enabled', False),
    ('cathedral_evidence_status', 'NOT PROVEN'),
    ('verifier_log_digest_evidence_status', 'NOT PROVEN'),
    ('live_evidence_digest', None),
    ('operations', {'create': True, 'cancel': True, 'retry': True, 'receipt': False}),
])
def test_any_failed_gate_blocks_the_gpu(store, monkeypatch, field, bad):
    catalog = _catalog_with(**{field: bad})
    monkeypatch.setattr(cathedral.Mod, 'profiles', lambda self: catalog)
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda *a, **kw: pytest.fail('submitted a job through a failed gate'))
    cat = cathedral.Mod(key='cat_sk_x')
    assert cat.gpu_ready()['ready'] is False
    assert 'not submittable' in cat.gpu(command=['python3', '-c', 'print(1)'], yes=True)['error']


def test_gpu_is_blocked_when_the_catalog_has_no_cc_gpu(store, monkeypatch):
    monkeypatch.setattr(cathedral.Mod, 'profiles', lambda self: {'profiles': []})
    assert cathedral.Mod(key='cat_sk_x').gpu_ready()['ready'] is False


def test_gpu_pins_the_catalog_image(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, m_, p, b=None, **kw: seen.update(body=b) or {'worker_id': 'w'})
    cathedral.Mod(key='cat_sk_x').gpu(command=['python3', '-c', 'print(1)'], yes=True)
    body = seen['body']
    assert body['workload']['image'] == 'pytorch/pytorch@sha256:beef'
    assert body['execution_class'] == 'cc_gpu' and body['profile'] == cathedral.CC_GPU_PROFILE
    assert body['network'] == {'egress': 'control_plane_only'}
    assert body['resources']['gpu']['mode'] == 'confidential'


# ── prices + ledger ──────────────────────────────────────────────────────

def test_estimate_prices_from_the_live_catalog():
    cat = cathedral.Mod(key='cat_sk_x')
    assert cat.estimate('attest.v1')['usd'] == 0.20
    assert cat.estimate('cc_gpu')['usd'] == 3.00
    assert cat.estimate('custom.v1', minutes=90, shape='8x32')['usd'] == 1.20
    assert 'error' in cat.estimate('nope')
    assert 'error' in cat.estimate('custom.v1', shape='999x999')


def test_a_catalog_price_hike_moves_the_spend_guard(store, monkeypatch):
    """The guard must quote what Cathedral charges today, not a stale table."""
    dearer = {'profiles': [dict(CATALOG['profiles'][0], pricing={'amount_usd': 0.75}),
                           CATALOG['profiles'][1]]}
    monkeypatch.setattr(cathedral.Mod, 'profiles', lambda self: dearer)
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda *a, **kw: pytest.fail('spent $0.75 without asking'))
    out = cathedral.Mod(key='cat_sk_x').run(max_spend_usd=1.0)
    assert out['error'] == 'confirmation required' and out['price_usd'] == 0.75


def test_prices_fall_back_when_cathedral_is_unreachable(store, monkeypatch):
    monkeypatch.setattr(cathedral.Mod, 'profiles', lambda self: {'error': 'unreachable'})
    sheet = cathedral.Mod(key='cat_sk_x').prices()
    assert sheet['per_execution']['attest.v1']['usd'] == 0.20
    assert sheet['source'] == 'built-in table' and 'live' in sheet


def test_ledger_records_the_payer_not_the_key(store, monkeypatch):
    monkeypatch.setattr(cathedral.Mod, '_call',
                        lambda self, *a, **kw: {'worker_id': 'wrk_9'})
    cat = cathedral.Mod(key='cat_sk_secret', account='alice')
    cat.run()
    led = cat.ledger()
    assert led['runs'] == 1 and led['reserved_usd'] == 0.20
    row = led['entries'][0]
    assert row['account'] == 'alice' and row['worker_id'] == 'wrk_9' and row['state'] == 'accepted'
    assert 'cat_sk_secret' not in json.dumps(led)


def test_settling_a_worker_updates_its_row(store, monkeypatch):
    monkeypatch.setattr(cathedral.Mod, '_call', lambda self, *a, **kw: {'worker_id': 'wrk_9'})
    cat = cathedral.Mod(key='cat_sk_x')
    cat.run()
    cat._settle('wrk_9', 'completed', {'charge_usd': 0.20})
    row = cat.ledger()['entries'][0]
    assert row['state'] == 'completed' and row['charged_usd'] == 0.20
    assert cat.ledger()['charged_usd'] == 0.20


def test_worker_id_survives_response_shapes():
    assert cathedral._worker_id({'worker_id': 'a'}) == 'a'
    assert cathedral._worker_id({'id': 'b'}) == 'b'
    assert cathedral._worker_id({'worker': {'worker_id': 'c'}}) == 'c'
    assert cathedral._worker_id({}) is None


# ── mod protocol surface ─────────────────────────────────────────────────

def test_fns_are_all_real_methods():
    cat = cathedral.Mod(key='cat_sk_x')
    for fn in cathedral.Mod.fns:
        assert callable(getattr(cat, fn, None)), fn


def test_config_fns_match_the_class():
    config = json.loads((MOD_PY.parent / 'config.json').read_text())
    assert set(config['fns']) == set(cathedral.Mod.fns)
    assert config['port'] == 50390


def test_forward_rejects_unknown_fns(store):
    assert 'error' in cathedral.Mod(key='cat_sk_x').forward('rm_rf')


def test_info_works_without_a_key(store):
    out = cathedral.Mod().info()
    assert 'no Cathedral API key' in out['key']
    assert out['prices']['per_execution']['attest.v1']['usd'] == 0.20
