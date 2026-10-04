import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import modsearch as engine  # noqa: E402

DOCS = [
    {'id': 'secretshare', 'text': "secretshare. Split a secret into N pieces so any K rebuild it (Shamir)."},
    {'id': 'bt', 'text': 'bt. Bittensor subnet prices, wallet portfolio and trading.'},
    {'id': 'musica', 'text': 'musica. Play and organise your music library and playlists.'},
    {'id': 'dns', 'text': 'dns. Authoritative DNS zones and records for the fleet.'},
]


class Down:
    name, model, floor = 'down', 'down/model', 0.1

    def embed(self, texts, kind):
        raise ConnectionError('liquidai not running')


class Fake:
    """Deterministic bag-of-words 'encoder' — tests the plumbing, not a model."""
    name, model, floor = 'fake', 'fake/model', 0.05
    VOCAB = ['music', 'songs', 'listen', 'secret', 'dns', 'records', 'bittensor', 'wallet']
    SYN = {'songs': 'music', 'listen': 'music', 'playlists': 'music'}

    def __init__(self):
        self.calls = []

    def embed(self, texts, kind):
        self.calls.append((kind, len(texts)))
        out = []
        for t in texts:
            ws = [self.SYN.get(w, w) for w in engine.tokens(t)]
            v = [float(ws.count(w)) for w in self.VOCAB] + [0.01]
            n = sum(x * x for x in v) ** 0.5
            out.append([x / n for x in v])
        return out


def test_lexical_fallback_keeps_answering(tmp_path):
    res = engine.search('dns records', DOCS, encoder=engine.Encoder([Down()], state=tmp_path))
    assert res['mode'] == 'lexical'
    assert res['results'][0]['id'] == 'dns'
    assert 'liquidai not running' in res['error']


def test_exact_name_wins(tmp_path):
    res = engine.search('musica', DOCS, encoder=engine.Encoder([], state=tmp_path))
    assert res['results'][0]['id'] == 'musica'


def test_chain_falls_through_and_hashes(tmp_path):
    fake = Fake()
    enc = engine.Encoder([Down(), fake], state=tmp_path)
    res = engine.search('listen to songs', DOCS, encoder=enc)
    assert res['mode'] == 'semantic' and res['backend'] == 'fake'
    top = res['results'][0]
    assert top['id'] == 'musica'
    assert len(top['hash']) == 64 and top['text_hash'] == engine.sha(DOCS[2]['text'])
    # docs encoded as documents, the query as a query — the model is asymmetric
    assert fake.calls == [('document', 4), ('query', 1)]
    # cached: docs are not re-encoded, and the hash is stable
    again = engine.search('listen to songs', DOCS, encoder=enc)
    assert fake.calls == [('document', 4), ('query', 1)]
    assert again['results'][0]['hash'] == top['hash']
    # queries never hit the disk cache
    assert len((tmp_path / 'vectors.jsonl').read_text().splitlines()) == 4


def test_embedding_hash_tracks_model_and_vector():
    v = [0.6, 0.8]
    h = engine.embedding_hash('m', 'document', v)
    assert h == engine.embedding_hash('m', 'document', [0.6, 0.8])
    assert h != engine.embedding_hash('other', 'document', v)
    assert h != engine.embedding_hash('m', 'query', v)
    assert h != engine.embedding_hash('m', 'document', [0.8, 0.6])


def test_mod_index_one_hash_per_mod(tmp_path):
    root = tmp_path / 'orbit'
    for name, desc in [('musica', 'play music'), ('dns', 'dns records')]:
        (root / name).mkdir(parents=True)
        (root / name / 'config.json').write_text(json.dumps({'name': name, 'description': desc}))
    enc = engine.Encoder([Fake()], state=tmp_path)
    idx = engine.ModIndex([root], enc, state=tmp_path)
    meta = idx.rebuild()
    assert meta['count'] == 2 and meta['model'] == 'fake/model'
    a = idx.get('musica')
    assert a['embedding_hash'] != idx.get('dns')['embedding_hash']
    assert idx.get('musica', vector=True)['vector']
    # re-describing a mod changes its hash; the other one keeps its own
    (root / 'musica' / 'config.json').write_text(json.dumps({'name': 'musica', 'description': 'listen to songs and wallet'}))
    dns_before = idx.get('dns')
    idx.rebuild()
    assert idx.get('musica')['embedding_hash'] != a['embedding_hash']
    assert idx.get('dns')['embedding_hash'] == dns_before['embedding_hash']
    assert idx.get('dns')['changed_at'] == dns_before['changed_at']
    # survives a restart without an encoder
    assert engine.ModIndex([root], engine.Encoder([], state=tmp_path), state=tmp_path).get('dns')


def test_mod_text_matches_hub_formula():
    # page.tsx modText(): [name, description, ...fns.slice(0, 12)], whitespace
    # collapsed, case-insensitive repeats dropped, joined with ". ", no tier word
    assert engine.mod_text('bt', 'subnets', ['a', 'b']) == 'bt. subnets. a. b'
    assert engine.mod_text('x', '', []) == 'x'
    assert engine.mod_text('audit', 'Audit', ['run', 'run', ' forward\n']) == 'audit. run. forward'
    assert engine.mod_text('n', '  many\n\t spaces  ') == 'n. many spaces'
    assert engine.mod_text('n', 'd', [str(i) for i in range(20)]).count('. ') == 1 + 12
    assert len(engine.mod_text('n', 'w ' * 2000)) == engine.MAX_TEXT


def test_fleet_docs_every_mod_no_tier_word_core_wins(tmp_path):
    orbit, core = tmp_path / 'orbit', tmp_path / 'core'
    for p in ['orbit/cfg', 'orbit/stub', 'orbit/apionly/api', 'orbit/readme',
              'orbit/_private', 'orbit/empty', 'orbit/dup', 'core/dup']:
        (tmp_path / p).mkdir(parents=True)
    (orbit / 'cfg' / 'config.json').write_text(json.dumps({'name': 'cfg', 'description': 'has a config', 'fns': ['go']}))
    (orbit / 'stub' / 'mod.py').write_text('class Mod:\n    description = """stub blurb"""\n')
    (orbit / 'readme' / 'mod.py').write_text('class Mod:\n    pass\n')
    (orbit / 'readme' / 'README.md').write_text('# readme\n\nreads the docs\n')
    (orbit / '_private' / 'mod.py').write_text('')
    (orbit / 'markup').mkdir()
    (orbit / 'markup' / 'mod.py').write_text('class Mod:\n    description = """<div align="center">"""\n')
    (orbit / 'markup' / 'README.md').write_text('<div>\n\n# markup\n[![ci](x)](y)\nturns markup into prose\n')
    (orbit / 'dup' / 'config.json').write_text(json.dumps({'name': 'dup', 'description': 'orbit copy'}))
    (core / 'dup' / 'config.json').write_text(json.dumps({'name': 'dup', 'description': 'core copy'}))
    docs = {d['id']: d for d in engine.fleet_docs([orbit, core])}
    assert set(docs) == {'cfg', 'stub', 'apionly', 'readme', 'markup', 'dup'}
    assert docs['cfg']['text'] == 'cfg. has a config. go'
    assert docs['stub']['text'] == 'stub. stub blurb'
    assert docs['readme']['text'] == 'readme. reads the docs'
    assert docs['apionly']['text'] == 'apionly'
    assert docs['markup']['text'] == 'markup. turns markup into prose'
    assert docs['dup']['text'] == 'dup. core copy' and docs['dup']['path'].endswith('core/dup')
    assert not any(w in d['text'].split('. ') for d in docs.values() for w in ('orbit', 'core'))


def test_fleet_docs_covers_the_real_orbit():
    # every dir under orbit/ the fleet would treat as a mod gets exactly one doc
    orbit = Path(__file__).resolve().parents[2]
    want = {d.name for d in orbit.iterdir() if engine._is_mod_dir(d)}
    got = {Path(d['path']).name for d in engine.fleet_docs([orbit])}
    assert want and want == got
    assert all(not d['text'].endswith(('. orbit', '. core')) for d in engine.fleet_docs([orbit]))


def test_empty():
    assert engine.search('', DOCS)['results'] == []
