"""Tests for the compute inventory — the flattened answer to "what can I rent".

The catalog hides most of the fleet: the GPUs are hardware classes rather than
profiles, and the worker sizes live inside custom.v1's resources. These tests
pin that nothing stays hidden, that a shut class says which gate shut it, and
that no price or spec is invented where the catalog is silent.
"""
import importlib.util
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('cathedral_inventory', DIR / 'inventory.py')
inv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inv)


# The live catalog's real shape, trimmed: two profiles, three hardware classes,
# five shapes named in resources and two only described by a class.
CATALOG = {'profiles': [
    {'id': 'attest.v1', 'name': 'Attest', 'availability': 'live_testing',
     'lifetimes': ['one_shot'],
     'resources': {'hardware_class': 'tdx_cpu', 'cpu': 4, 'memory_gib': 16,
                   'gpu': {'mode': 'none'}},
     'pricing': {'unit': 'completed_receipt', 'amount_usd': 0.2},
     'hardware_classes': [
         {'id': 'tdx_cpu', 'execution_class': 'tdx_cpu', 'availability': 'live_testing',
          'evidence': 'intel_tdx_quote'},
         {'id': 'confidential_gpu', 'execution_class': 'cc_gpu',
          'profile_id': 'gcp-g4-rtx-pro-6000-sev-v1', 'availability': 'unavailable',
          'customer_enabled': False, 'provider': 'gcp', 'machine_type': 'g4-standard-48',
          'cpu_tee': 'amd_sev', 'gpu_type': 'nvidia_rtx_pro_6000_96gb', 'gpu_count': 1,
          'gpu_memory_gib': 96, 'provisioning_models': ['spot'],
          'network_egress': 'control_plane_only',
          'cathedral_evidence_status': 'NOT PROVEN', 'gpu_evidence_status': 'PASS',
          'verifier_log_digest_evidence_status': 'NOT PROVEN',
          'runtime': {'image': 'pytorch/pytorch@sha256:beef'},
          'operations': {'create': True, 'cancel': True, 'retry': True, 'receipt': True},
          'pricing': {'unit': 'verified_execution', 'amount_usd': 3.0,
                      'runtime_minutes_included': 10}},
     ]},
    {'id': 'custom.v1', 'name': 'Custom', 'availability': 'live_testing',
     'lifetimes': ['bounded_service', 'persistent'],
     'resources': [
         {'size': 'Sealed CPU Small', 'hardware_class': 'tdx_cpu', 'cpu': 4,
          'memory_gib': 16, 'price_usd_per_hour': 0.4},
         {'size': 'Sealed CPU XL', 'hardware_class': 'tdx_cpu', 'cpu': 44,
          'memory_gib': 176, 'price_usd_per_hour': 4.4},
     ],
     'pricing': {'unit': 'worker_hour'},
     'hardware_classes': [
         {'id': 'tdx_cpu', 'execution_class': 'tdx_cpu', 'availability': 'live_testing'},
         {'id': 'hybrid_gpu', 'execution_class': 'hybrid_gpu_preview',
          'availability': 'preview', 'capacity': 'provider_dependent',
          'requires_scope': 'workers:hybrid-gpu:rent',
          'billing': 'full_rate_prequoted_before_reservation'},
         {'id': 'confidential_gpu', 'execution_class': 'cc_gpu',
          'availability': 'unavailable', 'customer_enabled': False},
     ],
     'trust': {'hybrid_gpu_disclosure': 'Inputs become plaintext to the trusted GPU host.'}},
]}

SHUT = {'ready': False, 'gates': {
    'availability': 'unavailable', 'customer_enabled': False,
    'cathedral_evidence_status': 'NOT PROVEN',
    'verifier_log_digest_evidence_status': 'NOT PROVEN',
    'live_evidence_digest': 'sha256:' + 'a' * 64,
    'operations': {'create': True, 'cancel': True, 'retry': True, 'receipt': True}}}
OPEN = {'ready': True, 'gates': {}, 'runtime_image': 'pytorch/pytorch@sha256:beef'}


@pytest.fixture
def fleet():
    return inv.build(CATALOG, SHUT, source='https://cathedral.computer/v1/profiles')


def klass(fleet, execution):
    return next(c for c in fleet['classes'] if c['execution_class'] == execution)


# ── nothing stays hidden ─────────────────────────────────────────────────

def test_every_hardware_class_in_the_catalog_is_listed(fleet):
    assert [c['execution_class'] for c in fleet['classes']] == [
        'tdx_cpu', 'cc_gpu', 'hybrid_gpu_preview']


def test_a_class_named_by_two_profiles_is_listed_once_crediting_both(fleet):
    assert klass(fleet, 'tdx_cpu')['profiles'] == ['attest.v1', 'custom.v1']


def test_every_worker_size_becomes_a_shape_at_its_own_hourly_rate(fleet):
    shapes = {s['name']: s for s in klass(fleet, 'tdx_cpu')['shapes']}
    assert set(shapes) == {'Attest', 'Sealed CPU Small', 'Sealed CPU XL'}
    assert (shapes['Sealed CPU XL']['price_usd'], shapes['Sealed CPU XL']['unit']) == (4.4, 'hour')
    assert (shapes['Sealed CPU XL']['cpu'], shapes['Sealed CPU XL']['memory_gib']) == (44, 176)
    assert (shapes['Attest']['price_usd'], shapes['Attest']['unit']) == (0.2, 'execution')


def test_each_shape_names_the_endpoint_that_orders_it(fleet):
    orders = {s['name']: s['order'] for c in fleet['classes'] for s in c['shapes']}
    assert orders['Attest'] == 'run'
    assert orders['Sealed CPU Small'] == 'rent'
    assert orders['nvidia rtx pro 6000 96gb'] == 'gpu'


def test_the_gpu_shape_carries_the_hardware_the_catalog_states(fleet):
    hw = klass(fleet, 'cc_gpu')['hardware']
    assert hw['gpu_type'] == 'nvidia_rtx_pro_6000_96gb' and hw['gpu_memory_gib'] == 96
    assert hw['machine_type'] == 'g4-standard-48' and hw['cpu_tee'] == 'amd_sev'
    assert hw['provisioning'] == ['spot']
    shape = klass(fleet, 'cc_gpu')['shapes'][0]
    assert (shape['price_usd'], shape['unit'], shape['minutes_included']) == (3.0, 'execution', 10)


def test_totals_count_the_whole_fleet(fleet):
    assert fleet['totals'] == {
        'hardware_classes': 3, 'shapes': 5, 'orderable_shapes': 4,
        'live': 1, 'preview': 1, 'unavailable': 1,
        'cheapest_usd': 0.2, 'dearest_usd': 4.4}


# ── a shut class says who shut it ────────────────────────────────────────

def test_a_gated_gpu_is_unavailable_and_names_every_failing_gate(fleet):
    gpu = klass(fleet, 'cc_gpu')
    assert gpu['status'] == 'unavailable'
    assert 'availability=unavailable' in gpu['blockers']
    assert 'customer_enabled=false' in gpu['blockers']       # not Python's False
    assert 'cathedral_evidence_status=NOT PROVEN' in gpu['blockers']
    assert gpu['shapes'][0]['orderable'] is False


def test_a_passing_gate_opens_the_gpu():
    gpu = klass(inv.build(CATALOG, OPEN), 'cc_gpu')
    assert gpu['status'] == 'live' and gpu['blockers'] == []
    assert gpu['shapes'][0]['orderable'] is True


def test_evidence_is_split_into_proven_and_not(fleet):
    ev = klass(fleet, 'cc_gpu')['evidence']
    assert ev['pass'] == ['gpu_evidence_status']
    assert ev['unproven'] == ['cathedral_evidence_status', 'verifier_log_digest_evidence_status']


def test_the_hybrid_preview_is_shown_as_preview_with_its_disclosure(fleet):
    hybrid = klass(fleet, 'hybrid_gpu_preview')
    assert hybrid['status'] == 'preview'
    assert hybrid['hardware']['requires_scope'] == 'workers:hybrid-gpu:rent'
    assert hybrid['notes'] == ['Inputs become plaintext to the trusted GPU host.']


# ── no invention ─────────────────────────────────────────────────────────

def test_an_unpriced_shape_is_quoted_not_guessed(fleet):
    shape = klass(fleet, 'hybrid_gpu_preview')['shapes'][0]
    assert shape['price_usd'] is None and shape['quoted'] is True


def test_a_silent_catalog_leaves_the_specs_empty(fleet):
    # The tdx_cpu class states nothing but its evidence kind; invent no machine.
    assert klass(fleet, 'tdx_cpu')['hardware'] == {}


def test_an_unreachable_catalog_passes_the_error_through():
    assert inv.build({'error': 'unreachable'}, None) == {'error': 'unreachable'}
    assert inv.build({}, None)['classes'] == []


def test_no_gate_verdict_falls_back_to_the_catalogs_own_availability():
    gpu = klass(inv.build(CATALOG, None), 'cc_gpu')
    assert gpu['status'] == 'unavailable' and 'customer_enabled=false' in gpu['blockers']
