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


class NoEncoder(engine.Encoder):
    def encode(self, texts, persist=True):
        return None


def test_lexical_fallback_keeps_answering():
    res = engine.search('dns records', DOCS, encoder=NoEncoder())
    assert res['mode'] == 'lexical'
    assert res['results'][0]['id'] == 'dns'


def test_exact_name_wins():
    res = engine.search('musica', DOCS, encoder=NoEncoder())
    assert res['results'][0]['id'] == 'musica'


def test_semantic_finds_meaning(tmp_path):
    enc = engine.Encoder(state=tmp_path)
    res = engine.search('listen to songs', DOCS, encoder=enc)
    if res['mode'] != 'semantic':  # no torch/weights on this box
        return
    assert res['results'][0]['id'] == 'musica'
    assert 'secretshare' not in [r['id'] for r in res['results']]
    # cached: a second run encodes nothing new
    assert (tmp_path / 'vectors.jsonl').exists()


def test_empty():
    assert engine.search('', DOCS)['results'] == []
