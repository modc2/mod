"""infer tests — the claims this module makes about itself, checked.

Everything runs against a temporary store, so a test run never touches the
models somebody is actually working on. The fixtures are built with onnx's own
helper rather than exported from torch: the point is to test the optimizer, and
a graph built by hand is the same three ops every time on any box.
"""

import json
import os
import sys
import tempfile

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(scope='session')
def E():
    """The engine, pointed at a throwaway store."""
    tmp = tempfile.mkdtemp(prefix='infer-test-')
    os.environ['INFER_DIR'] = tmp
    import engine
    engine.STATE_DIR = tmp
    engine.MODEL_DIR = os.path.join(tmp, 'models')
    engine.REGISTRY = os.path.join(tmp, 'registry.json')
    return engine


def _mlp_bytes(hidden=128, unused=True):
    """A Gemm/Relu/Gemm graph, optionally carrying a weight nothing reads.

    The dangling initializer is deliberate: `slim` exists to find exactly that,
    and a fixture that never had one could not tell whether it worked.
    """
    rng = np.random.default_rng(0)
    w1 = rng.standard_normal((16, hidden)).astype('float32')
    b1 = np.zeros(hidden, 'float32')
    w2 = rng.standard_normal((hidden, 4)).astype('float32')
    b2 = np.zeros(4, 'float32')
    inits = [numpy_helper.from_array(w1, 'w1'), numpy_helper.from_array(b1, 'b1'),
             numpy_helper.from_array(w2, 'w2'), numpy_helper.from_array(b2, 'b2')]
    if unused:
        inits.append(numpy_helper.from_array(
            rng.standard_normal((8, 8)).astype('float32'), 'orphan'))
    nodes = [helper.make_node('Gemm', ['x', 'w1', 'b1'], ['h']),
             helper.make_node('Relu', ['h'], ['a']),
             helper.make_node('Gemm', ['a', 'w2', 'b2'], ['y'])]
    graph = helper.make_graph(
        nodes, 'mlp',
        [helper.make_tensor_value_info('x', TensorProto.FLOAT, ['batch', 16])],
        [helper.make_tensor_value_info('y', TensorProto.FLOAT, ['batch', 4])],
        inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)])
    model.ir_version = 9
    return model.SerializeToString()


@pytest.fixture(scope='session')
def mlp(E):
    return E.store(_mlp_bytes(), name='test-mlp', source='test')


# ── the store ────────────────────────────────────────────────────

def test_store_is_content_addressed(E):
    a = E.store(_mlp_bytes(), name='same')
    b = E.store(_mlp_bytes(), name='same')
    assert a['id'] == b['id'], 'identical bytes must be one entry'
    assert a['sha256'].startswith(a['id'])


def test_resolve_by_name_prefix_and_path(E, mlp):
    assert E.resolve('test-mlp')[0]['id'] == mlp['id']
    assert E.resolve(mlp['id'][:6])[0]['id'] == mlp['id']
    rec, path = E.resolve(mlp['id'])
    assert os.path.isfile(path)
    assert E.resolve(path)[1] == path, 'a path should work without being stored'


def test_unknown_model_is_404(E):
    with pytest.raises(E.InferError) as e:
        E.resolve('nothing-by-that-name')
    assert e.value.status == 404


def test_garbage_is_refused(E):
    with pytest.raises(E.InferError):
        E.add(data=__import__('base64').b64encode(b'not a model').decode())


# ── reading ──────────────────────────────────────────────────────

def test_inspect_reads_the_graph(E, mlp):
    info = E.inspect(mlp['id'])
    assert info['ops'] == {'Gemm': 2, 'Relu': 1}
    assert info['inputs'][0]['shape'] == ['batch', 16], 'symbolic dims stay symbolic'
    assert info['inputs'][0]['dtype'] == 'float32'
    assert info['params'] > 0 and info['weight_bytes'] > 0
    assert info['arch'] == 'feed-forward'
    assert info['portable'] is True


def test_no_plan_ever_includes_the_one_pass_that_breaks_a_browser(E, mlp):
    """`all` is the only pass measured to be unloadable in onnxruntime-web."""
    for target in ('local', 'web'):
        assert 'all' not in E.plan(mlp['id'], target=target)['plan']
    assert 'extended' in E.plan(mlp['id'], target='web')['plan'], \
        'fusion is browser-safe — the wasm backend registers the contrib ops'
    assert any('nchwc' in w for w in E.plan(mlp['id'], target='web')['why'])


# ── the passes ───────────────────────────────────────────────────

def test_slim_drops_the_orphan_weight(E):
    fat = E.store(_mlp_bytes(unused=True), name='fat')
    rep = E.optimize(fat['id'], ['slim'], check=False)
    step = rep['passes'][0]
    assert step['ok'] and step['bytes']['after'] < step['bytes']['before']
    kept = {i.name for i in onnx.load(E.resolve(rep['result']['id'])[1])
            .graph.initializer}
    assert 'orphan' not in kept and 'w1' in kept


def test_lossless_passes_do_not_move_the_numbers(E, mlp):
    rep = E.optimize(mlp['id'], ['slim', 'extended'], samples=3)
    assert rep['parity']['max_abs_err'] == 0, rep['parity']
    assert rep['parity']['ok'] is True


def test_quantization_shrinks_it_and_says_that_it_moved(E, mlp):
    rep = E.optimize(mlp['id'], ['int8'], samples=3)
    assert rep['size']['ratio'] > 1.5, 'int8 weights should be much smaller'
    assert rep['parity']['max_abs_err'] > 0, 'and it should admit the cost'


def test_an_unknown_pass_is_refused_before_anything_runs(E, mlp):
    with pytest.raises(E.InferError) as e:
        E.optimize(mlp['id'], ['warp-speed'])
    assert 'no such pass' in e.value.message


def test_optimize_records_its_lineage(E, mlp):
    rep = E.optimize(mlp['id'], ['slim'], check=False)
    child = E.registry()[rep['result']['id']]
    assert child['parent'] == mlp['id'] and child['passes'] == ['slim']


# ── measuring ────────────────────────────────────────────────────

def test_bench_reports_percentiles_and_the_shape_it_used(E, mlp):
    b = E.bench(mlp['id'], runs=5, warmup=1, batch=4)
    assert b['ms']['p50'] > 0 and b['ms']['p99'] >= b['ms']['p50']
    assert b['inputs'][0]['shape'] == [4, 16], 'batch must fill the symbolic dim'
    assert b['throughput_per_s'] > 0


def test_bench_refuses_a_provider_this_box_does_not_have(E, mlp):
    with pytest.raises(E.InferError):
        E.bench(mlp['id'], runs=1, provider='TotallyRealExecutionProvider')


def test_parity_of_a_model_with_itself_is_exact(E, mlp):
    p = E.parity(mlp['id'], mlp['id'], samples=2)
    assert p['max_abs_err'] == 0 and p['verdict'] == 'identical'


def test_shapes_override_beats_the_declared_one(E, mlp):
    b = E.bench(mlp['id'], runs=2, warmup=0, shapes={'x': '7,16'})
    assert b['inputs'][0]['shape'] == [7, 16]


# ── portability, the part that is easy to get wrong ──────────────

def _with_domain(E, ref, domain, name):
    model = onnx.load(E.resolve(ref)[1])
    model.graph.node[1].domain = domain
    model.opset_import.append(helper.make_opsetid(domain, 1))
    return E.store(model.SerializeToString(), name=name)


def test_portable_blocks_the_layout_ops_a_browser_really_cannot_load(E, mlp):
    """Measured in a browser: com.microsoft.nchwc.Conv is not registered."""
    bad = _with_domain(E, mlp['id'], 'com.microsoft.nchwc', 'nchwc-op')
    out = E.portable(bad['id'])
    assert out['portable'] is False
    assert 'com.microsoft.nchwc.Relu' in out['blocked_ops']


def test_portable_allows_contrib_ops_but_says_so(E, mlp):
    """The opposite failure, and the one this module got wrong first.

    onnxruntime-web's wasm build DOES register com.microsoft — a graph with
    FusedConv and one with BiasGelu both ran there. Calling those non-portable
    would send people to a slower binary for no reason.
    """
    ok = _with_domain(E, mlp['id'], 'com.microsoft', 'contrib-op')
    out = E.portable(ok['id'])
    assert out['portable'] is True
    assert 'com.microsoft.Relu' in out['contrib_ops']
    assert out['cautions'], 'portable, but not silently'


def test_an_unknown_custom_domain_is_still_blocked(E, mlp):
    bad = _with_domain(E, mlp['id'], 'com.acme.secret', 'custom-op')
    assert E.portable(bad['id'])['portable'] is False


def test_portable_flags_an_opset_the_browser_cannot_run(E, mlp):
    model = onnx.load(E.resolve(mlp['id'])[1])
    del model.opset_import[:]
    model.opset_import.append(helper.make_opsetid('', E.WEB_OPSET + 3))
    future = E.store(model.SerializeToString(), name='too-new')
    assert E.portable(future['id'])['portable'] is False


# ── the surfaces agree ───────────────────────────────────────────

def test_every_declared_fn_and_tool_exists(E):
    import mcp
    import mod
    cfg = json.load(open(os.path.join(os.path.dirname(HERE), 'config.json')))
    for name in cfg['fns']:
        assert hasattr(mod.Mod, name), f'config.json promises fn {name}'
    assert sorted(cfg['tools']) == sorted(mcp.TOOLS), 'config and mcp disagree'
    for name, tool in mcp.TOOLS.items():
        assert name.startswith('infer_') and tool['description']


def test_mcp_speaks_json_rpc(E, mlp):
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                      'params': {'name': 'infer_inspect',
                                 'arguments': {'model': mlp['id']}}})
    assert out['result']['isError'] is False
    assert out['result']['structuredContent']['ops'] == {'Gemm': 2, 'Relu': 1}
    assert mcp.handle({'jsonrpc': '2.0', 'id': 2,
                       'method': 'notifications/whatever'}) is None
    assert mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'})['error']


def test_a_missing_argument_is_an_error_result_not_a_crash(E):
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                      'params': {'name': 'infer_inspect', 'arguments': {}}})
    assert out['result']['isError'] is True


def test_api_routes_reach_the_same_engine(E, mlp):
    import api
    assert api.route('GET', '/health', '', {})['ok'] is True
    got = api.route('GET', '/inspect', f"model={mlp['id']}", {})
    assert got['id'] == mlp['id']
    with pytest.raises(E.InferError):
        api.route('GET', '/inspect', '', {})
