"""Zoo tests — the claims the model catalog makes, checked offline.

No test here touches the network. Remote sources are exercised through
fixtures shaped like their real answers (copied from live responses on
2026-09-29), and the crawl machinery through a fake source, because what
needs guarding is resumption, dedupe, rate-limit handling and file choice —
none of which should depend on whether HuggingFace is up this minute.
"""

import io
import json
import os
import sys
import tarfile
import tempfile
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(scope='module')
def Z():
    tmp = tempfile.mkdtemp(prefix='infer-zoo-test-')
    import engine
    engine.STATE_DIR = tmp
    engine.MODEL_DIR = os.path.join(tmp, 'models')
    engine.REGISTRY = os.path.join(tmp, 'registry.json')
    import zoo
    zoo._cache.clear()
    return zoo


# ── choosing a file ──────────────────────────────────────────────

def test_default_file_prefers_full_precision():
    from zoo.base import default_file
    files = [['onnx/model_q4.onnx', 10], ['onnx/model_fp16.onnx', 20],
             ['onnx/model.onnx', 40], ['onnx/model_quantized.onnx', 11],
             ['onnx/model.onnx_data', 999], ['README.md', 1]]
    assert default_file(files) == 'onnx/model.onnx'


def test_default_file_sentence_transformers_layout():
    from zoo.base import default_file
    files = [['onnx/model_O4.onnx', 5], ['onnx/model_qint8_arm64.onnx', 3],
             ['onnx/model.onnx', 90]]
    assert default_file(files) == 'onnx/model.onnx'


def test_default_file_none_without_onnx():
    from zoo.base import default_file
    assert default_file([['model.safetensors', 1]]) is None


def test_companions_do_not_cross_variants():
    """model.onnx and model_fp16.onnx_data share a prefix, not a model."""
    from zoo.base import companions
    paths = ['onnx/model.onnx', 'onnx/model.onnx_data', 'onnx/model_fp16.onnx',
             'onnx/model_fp16.onnx_data']
    assert companions('onnx/model.onnx', paths) == ['onnx/model.onnx_data']
    assert companions('onnx/model_fp16.onnx', paths) == ['onnx/model_fp16.onnx_data']


def test_domain_vocabulary():
    from zoo.base import domain_of
    assert domain_of('image-text-to-text') == 'multimodal'
    assert domain_of('automatic-speech-recognition') == 'audio'
    assert domain_of('object-detection') == 'vision'
    assert domain_of('fill-mask') == 'text'
    assert domain_of('node:Abs', 'node') == 'operator'
    assert domain_of(None) == 'other'


# ── rate limits ─────────────────────────────────────────────────

def test_ratelimit_header_schedules_a_pause():
    """HF says r=<left>;t=<reset>. Near zero, the client must wait the reset
    out *before* it is refused — ignoring it got this IP blocked once."""
    from zoo import base
    base._hosts.clear()
    w = base._note_limits('hf.test', {'ratelimit': '"api";r=2;t=41'}, 200)
    assert w == 42 and base._hosts['hf.test']['next'] > time.time() + 40
    base._hosts.clear()
    assert base._note_limits('hf.test', {'ratelimit': '"api";r=400;t=41'}, 200) == 0
    assert 'hf.test' not in base._hosts


def test_429_without_headers_still_backs_off():
    from zoo import base
    base._hosts.clear()
    assert base._note_limits('x.test', {}, 429) == 30
    assert base._note_limits('y.test', {'retry-after': '7'}, 429) == 7


# ── weights stored beside the graph ─────────────────────────────

def _external_model(tmp):
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    w = numpy_helper.from_array(np.arange(4096, dtype='float32').reshape(64, 64), 'W')
    g = helper.make_graph([helper.make_node('MatMul', ['x', 'W'], ['y'])], 'g',
                          [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 64])],
                          [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 64])], [w])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid('', 17)])
    path = os.path.join(tmp, 'model.onnx')
    onnx.save(m, path, save_as_external_data=True, location='model.onnx_data',
              size_threshold=0)
    return path


def test_assemble_fetches_what_the_graph_names():
    from zoo.base import assemble, external_locations
    tmp = tempfile.mkdtemp()
    path = _external_model(tmp)
    main = open(path, 'rb').read()
    assert external_locations(main) == ['model.onnx_data']
    asked = []

    def get(p):
        asked.append(p)
        return open(os.path.join(tmp, os.path.basename(p)), 'rb').read()
    out = assemble(main, get, onnx_path='onnx/model.onnx')
    assert asked == ['onnx/model.onnx_data']
    assert len(out) > 4096 * 4 and not external_locations(out)


def test_assemble_refuses_over_the_limit():
    from zoo.base import ZooError, assemble
    tmp = tempfile.mkdtemp()
    main = open(_external_model(tmp), 'rb').read()
    with pytest.raises(ZooError) as e:
        assemble(main, lambda p: b'\0' * 20000, max_bytes=10000)
    assert e.value.status == 413


def test_from_archive_finds_onnx_in_a_tarball():
    from zoo.base import from_archive
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for name, data in (('a/model.onnx', b'onnx'), ('a/readme.md', b'hi')):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    assert from_archive(buf.getvalue()) == {'a/model.onnx': b'onnx'}


def test_unpack_zipped_onnx_with_external_weights():
    """Qualcomm ships <name>.onnx.zip holding a graph and its weights."""
    import zipfile
    from zoo.base import default_file, external_locations, unpack
    tmp = tempfile.mkdtemp()
    path = _external_model(tmp)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.write(path, 'pkg/model.onnx')
        z.write(os.path.join(tmp, 'model.onnx_data'), 'pkg/model.onnx_data')
    out = unpack(buf.getvalue(), 'x.onnx.zip')
    assert not external_locations(out) and len(out) > 16384
    assert default_file([['a_w8a8.onnx.zip', 1], ['a_float.onnx.zip', 9]]) == 'a_float.onnx.zip'


# ── remote rows, from real response shapes ──────────────────────

HF_MODEL = {
    'id': 'onnx-community/all-MiniLM-L6-v2-ONNX', 'downloads': 12345, 'likes': 9,
    'pipeline_tag': 'feature-extraction', 'library_name': 'transformers.js',
    'tags': ['transformers.js', 'onnx', 'bert', 'license:apache-2.0', 'region:us',
             'base_model:sentence-transformers/all-MiniLM-L6-v2'],
    'siblings': [{'rfilename': 'README.md'}, {'rfilename': 'onnx/model.onnx'},
                 {'rfilename': 'onnx/model_fp16.onnx'}, {'rfilename': 'onnx/model_q4.onnx'},
                 {'rfilename': 'tokenizer.json'}],
    'lastModified': '2026-01-01T00:00:00.000Z'}


def test_hf_row_mapping():
    from zoo.hf import row
    r = row(HF_MODEL)
    assert r['key'] == 'huggingface:onnx-community/all-MiniLM-L6-v2-ONNX'
    assert r['author'] == 'onnx-community' and r['license'] == 'apache-2.0'
    assert [f[0] for f in r['files']] == ['onnx/model.onnx', 'onnx/model_fp16.onnx',
                                          'onnx/model_q4.onnx']
    assert r['domain'] == 'text' and r['variants'] == 3
    assert 'onnx' not in r['tags'] and not any(t.startswith('region:') for t in r['tags'])


def test_hf_row_skips_repos_without_onnx():
    from zoo.hf import row
    assert row({'id': 'a/b', 'siblings': [{'rfilename': 'model.safetensors'}]}) is None


# ── the catalog machinery ───────────────────────────────────────

class _Fake:
    """Three pages; dies after the second unless told not to."""
    name, title, kind, home, note, expect = 'fake', 'fake', 'remote', '', '', None
    die = True

    def describe(self):
        return {'name': self.name, 'kind': self.kind}

    def scrape(self, job, state):
        from zoo.base import entry
        page = state.get('page', 0)
        while page < 3:
            if page == 2 and self.die:
                raise RuntimeError('network went away')
            rows = [entry('fake', f'm{page}-{i}', task='image-classification',
                          files=[['model.onnx', 100 * i]], downloads=i)
                    for i in range(1, 4)]
            page += 1
            yield rows, {'page': page}, page == 3, 9


def test_crawl_resumes_from_its_cursor(Z):
    fake = _Fake()
    Z.SOURCES['fake'] = fake
    try:
        with pytest.raises(RuntimeError):
            Z._run(fake)
        m = Z.meta('fake')
        assert m['complete'] is False and m['state'] == {'page': 2}
        assert len(Z.rows('fake')[0]) == 6          # readable mid-crawl
        fake.die = False
        Z._run(fake)                                 # resumes at page 2
        m = Z.meta('fake')
        assert m['complete'] and m['count'] == 9 and m['resumed']
        assert not m['stopped_by_user']
        assert len(Z.rows('fake')[0]) == 9
    finally:
        Z.SOURCES.pop('fake')


def test_search_filters_and_facets(Z):
    Z._ensure_local()
    r = Z.search(source='builtin', domain='audio', limit=100)
    names = {m['name'] for m in r['models']}
    assert {'kws-conv1d', 'tcn', 'conformer-lite'} <= names
    assert set(r['facets']['domain']) == {'audio'}
    r = Z.search(q='gelu', source='onnx-tests', limit=5)
    assert r['count'] > 0 and all('gelu' in m['key'].lower() for m in r['models'])
    with pytest.raises(Z.ZooError):
        Z.search(source='nope')


def test_local_sources_are_populated_offline(Z):
    st = {s['name']: s for s in Z.status()['sources']}
    assert st['builtin']['count'] == len(Z.ARCHS) >= 38
    assert st['onnx-tests']['count'] > 1000


def test_every_onnx_helper_builder_runs():
    """The torch-free ones are the floor: they must work on any box."""
    import onnxruntime as ort
    import numpy as np
    from zoo.builtin import ARCHS, build
    for name, (how, *_rest) in ARCHS.items():
        if how != 'onnx':
            continue
        s = ort.InferenceSession(build(name), providers=['CPUExecutionProvider'])
        feed = {i.name: np.random.default_rng(0).standard_normal(
            [d if isinstance(d, int) else 2 for d in i.shape]).astype('float32')
            for i in s.get_inputs()}
        assert s.run(None, feed), name


def test_plant_builtin_is_content_addressed(Z):
    torch = pytest.importorskip('torch')  # noqa: F841
    a = Z.plant('builtin:gru')
    b = Z.plant('builtin:gru')
    assert a['id'] == b['id'] and a['zoo'] == 'builtin:gru'
    assert a['arch'] == 'recurrent'


def test_plant_onnx_test_model(Z):
    r = Z.plant('onnx-tests:node/test_abs/model.onnx')
    assert r['name'] == 'abs' and r['source'] == 'zoo:onnx-tests:node/test_abs/model.onnx'


def test_unknown_key_is_a_404(Z):
    with pytest.raises(Z.ZooError) as e:
        Z.get('builtin:not-an-arch')
    assert e.value.status == 404


def test_kaggle_dead_token_moves_to_next_ordering(monkeypatch):
    """A page token Kaggle refuses mid-pass ends that ordering, not the crawl;
    page 1 refusing is a real error."""
    from zoo import kaggle
    from zoo.base import ZooError
    monkeypatch.setattr(kaggle, 'PASSES', [(None, 'a'), (None, 'b')])
    monkeypatch.setattr(kaggle, '_sleep', lambda *a, **k: None)
    inst = {'framework': 'onnx', 'slug': 'x', 'versionNumber': 1}

    def fake_http(url, **kw):
        if 'pageToken=dead' in url:
            raise ZooError('HTTP 500', 500)
        order = 'a' if 'sortBy=a' in url else 'b'
        return {'models': [{'ref': f'o/{order}', 'slug': order, 'instances': [inst]}],
                'nextPageToken': 'dead' if order == 'a' else None}
    monkeypatch.setattr(kaggle, 'http', fake_http)

    class J:
        stopped = False
        def say(self, m): pass
    pages = list(kaggle.Kaggle().scrape(J(), {}))
    keys = [r['key'] for p in pages for r in p[0]]
    assert keys == ['kaggle:o/a/onnx/x/1', 'kaggle:o/b/onnx/x/1']
    assert pages[-1][2] is True and pages[-1][1]['gaps'] == ['a@1']
