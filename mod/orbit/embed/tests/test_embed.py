"""The tests, which are mostly this module checking itself against onnxruntime.

Run: python3 -m pytest -q tests        or       m embed/test

The tests that matter are the ones marked `reference` — home-made code that is
wrong in a self-consistent way passes every test it writes for itself, so the
protobuf writer and the numpy interpreter are checked against an implementation
that shares no code with them. Those skip if onnxruntime is not installed, and
the rest of the suite still runs.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from src import check, compress, data, evaluate, onnxfile, quantize, runtime, text, zoo

ORT = check.available()['available']
reference = pytest.mark.skipif(not ORT, reason='onnxruntime not installed')


# ── the file format ──────────────────────────────────────────────────

def test_varints_round_trip():
    for value in (0, 1, 127, 128, 300, 2 ** 31, 2 ** 62):
        assert onnxfile._varint(onnxfile._put_varint(value), 0)[0] == value


def test_negative_attribute_survives_the_wire():
    """Softmax(axis=-1) is stored as an unsigned 64-bit varint and must come back
    as -1, not as 18446744073709551615 — a bug that only shows up at run time."""
    node = onnxfile.Node('Softmax', ['x'], ['y'],
                         attributes=[onnxfile.Attribute.i('axis', -1)])
    assert onnxfile.Node.decode(node.encode()).attr('axis') == -1


def test_model_round_trips_through_bytes():
    model = onnxfile.load(zoo.ensure('bow-64'))
    again = onnxfile.Model.decode(model.encode())
    assert again.summary()['ops'] == model.summary()['ops']
    assert again.opset == model.opset
    for name, array in model.tensors().items():
        assert np.array_equal(again.tensors()[name], array)


def test_a_scalar_keeps_rank_zero():
    """np.ascontiguousarray promotes a 0-d array to shape (1,). Left alone, that
    wrote `dims: 1` onto every scalar Constant in a real model, and the rank
    change broke shape inference at the first Concat that consumed one — a file
    this module could read back perfectly and no other runtime would load."""
    scalar = onnxfile.Tensor('s', np.array(1, dtype=np.int64))
    assert onnxfile.Tensor.decode(scalar.encode()).array.shape == ()


def test_tensor_dtypes_survive():
    for array in (np.arange(6, dtype=np.float32).reshape(2, 3),
                  np.arange(6, dtype=np.int8).reshape(3, 2),
                  np.arange(4, dtype=np.float16),
                  np.arange(3, dtype=np.int64)):
        back = onnxfile.Tensor.decode(onnxfile.Tensor('t', array).encode())
        assert back.array.dtype == array.dtype
        assert np.array_equal(back.array, array)


# ── the arithmetic ───────────────────────────────────────────────────

def test_quantization_error_ranks_as_expected():
    w = np.random.default_rng(0).normal(0, 1, (256, 32)).astype(np.float32)
    errors = {row['method'] + ('-pc' if row['per_channel'] else ''):
              row['error']['rel_rmse'] for row in quantize.compare(w, axis=1)['methods']}
    assert errors['float32'] == 0
    assert errors['float16'] < errors['int8-pc'] < errors['int8'] < errors['int4']


def test_per_channel_axis_follows_onnx_not_numpy():
    """ONNX's `axis` indexes the scales; numpy's reduces over. One scale per
    column of a (rows, columns) weight is axis=1 and has `columns` entries."""
    w = np.random.default_rng(1).normal(0, 1, (64, 8)).astype(np.float32)
    _, scale, _ = quantize.quantize_int8(w, axis=1)
    assert scale.shape == (8,)
    _, scale, _ = quantize.quantize_int8(w, axis=0)
    assert scale.shape == (64,)


def test_a_column_with_an_outlier_only_hurts_itself_per_channel():
    w = np.random.default_rng(2).normal(0, 0.1, (128, 4)).astype(np.float32)
    w[0, 0] = 40.0
    per_tensor = quantize.round_trip(w, 'int8')
    per_channel = quantize.round_trip(w, 'int8', axis=1)
    clean = slice(1, None), slice(1, None)
    assert quantize.error(w[clean], per_channel['restored'][clean])['rel_rmse'] < \
        quantize.error(w[clean], per_tensor['restored'][clean])['rel_rmse'] / 10


def test_int4_packing_round_trips():
    w = np.random.default_rng(3).normal(0, 1, (33,)).astype(np.float32)
    packed, scale, shape = quantize.pack_int4(w)
    assert packed.nbytes == 17                       # 33 values, two per byte
    back = quantize.unpack_int4(packed, scale, shape)
    assert back.shape == w.shape
    assert quantize.error(w, back)['cosine'] > 0.95


# ── the models ───────────────────────────────────────────────────────

def test_hashing_is_stable_across_processes():
    assert text.fnv1a('coffee') == 2385279649        # not Python's salted hash()
    assert text.token_ids('coffee coffee').tolist() == [6817, 6817]


def test_building_is_deterministic():
    first = zoo.path('bow-64').read_bytes() if zoo.path('bow-64').exists() \
        else zoo.build_bow().read_bytes()
    zoo.path('bow-64').unlink()
    assert zoo.build_bow().read_bytes() == first


def test_the_embedder_puts_related_sentences_closer():
    model = zoo.load('bow-64')
    grind = evaluate.embed(model, 'a burr grinder gives an even grind')
    weigh_a = evaluate.embed(model, 'weigh the beans and weigh the water')
    weigh_b = evaluate.embed(model, 'weigh the ingredients, cups lie')
    assert text.cosine(weigh_a, weigh_b) > text.cosine(grind, weigh_a)


def test_the_classifier_learned_something():
    scored = evaluate.sentiment(zoo.load('sent-mlp'))
    assert scored['accuracy'] > 0.8                  # 0.88 as built; 0.5 is chance


def test_retrieval_baseline_holds():
    assert evaluate.retrieval(zoo.load('bow-64'))['top1_accuracy'] >= 0.75


# ── compression, end to end ──────────────────────────────────────────

@pytest.mark.parametrize('method', ['float16', 'int8', 'int8-per-channel'])
def test_compression_shrinks_and_keeps_the_answers(method, tmp_path):
    source = zoo.ensure('bow-64')
    target = tmp_path / f'{method}.onnx'
    report = compress.compress_file(source, target, method)
    assert report['file_bytes_after'] < report['file_bytes_before']
    scored = evaluate.retrieval(onnxfile.load(target),
                                reference=onnxfile.load(source))
    assert scored['agreement_with_float'] == 1.0     # free, at this size


def test_four_bits_is_not_free(tmp_path):
    """The point of having a method that breaks: a sweep where nothing ever
    moves teaches that compression is free, which is not true."""
    source = zoo.ensure('bow-64')
    target = tmp_path / 'int4.onnx'
    compress.compress_file(source, target, 'int4-sim')
    scored = evaluate.retrieval(onnxfile.load(target),
                                reference=onnxfile.load(source))
    assert scored['agreement_with_float'] < 1.0


def test_small_tensors_are_left_alone(tmp_path):
    source = zoo.ensure('bow-64')
    report = compress.compress_file(source, tmp_path / 'x.onnx', 'int8')
    eps = next(r for r in report['tensors'] if r['tensor'] == 'eps')
    assert eps['action'] == 'left as float32'


def test_dequantize_lands_before_its_consumer(tmp_path):
    source = zoo.ensure('sent-mlp')
    compress.compress_file(source, tmp_path / 'q.onnx', 'int8')
    nodes = onnxfile.load(tmp_path / 'q.onnx').graph.nodes
    produced = set()
    for node in nodes:                               # a graph in topological order
        for name in node.inputs:
            assert name not in {n.outputs[0] for n in nodes} or name in produced
        produced.update(node.outputs)


def test_sweep_reports_every_method():
    report = evaluate.sweep('sent-mlp')
    assert [r['method'] for r in report['results']] == list(compress.METHODS)
    assert all('agreement_with_float' in r for r in report['results'])


# ── the runtime ──────────────────────────────────────────────────────

def test_unsupported_ops_are_named_not_guessed():
    model = onnxfile.load(zoo.ensure('bow-64'))
    model.graph.nodes.append(onnxfile.Node('Attention', ['vector'], ['z']))
    assert runtime.unsupported(model) == ['Attention']
    with pytest.raises(runtime.Missing, match='Attention'):
        runtime.run(model, {'input_ids': np.array([1, 2], dtype=np.int64)})


# ── against a runtime we did not write ───────────────────────────────

@reference
def test_our_files_load_in_onnxruntime():
    for name in ('bow-64', 'sent-mlp'):
        result = check.check(name)
        assert result['ok'], result


@reference
def test_every_compressed_file_loads_in_onnxruntime():
    result = check.check_all()
    assert result['ok'], [r for r in result['results'] if not r['ok']]


@reference
@pytest.mark.skipif(not zoo.path('minilm').exists(),
                    reason='minilm not pulled — m embed/pull name=minilm')
def test_a_real_transformer_survives_the_round_trip(tmp_path):
    """The models built here are small and written by this module, so they
    exercise only the corner of ONNX this module emits. A 90 MB transformer from
    somebody else's exporter is the test that actually finds things."""
    import onnxruntime

    source = zoo.path('minilm')
    plain = tmp_path / 'plain.onnx'
    onnxfile.save(onnxfile.load(source), plain)
    onnxruntime.InferenceSession(str(plain), providers=['CPUExecutionProvider'])

    compressed = tmp_path / 'int8.onnx'
    report = compress.compress_file(source, compressed, 'int8-per-channel')
    assert report['file_ratio'] > 3.5

    def embed_one(path):
        session = onnxruntime.InferenceSession(str(path),
                                               providers=['CPUExecutionProvider'])
        ids = np.array([[101, 2632, 3945, 2003, 6659, 102]], dtype=np.int64)
        return session.run(None, {'input_ids': ids,
                                  'attention_mask': np.ones_like(ids),
                                  'token_type_ids': np.zeros_like(ids)})[0].mean(1)[0]

    assert quantize.error(embed_one(source), embed_one(compressed))['cosine'] > 0.99
