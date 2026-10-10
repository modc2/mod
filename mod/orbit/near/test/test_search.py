"""Semantic search over contracts — every test is offline: the engine is
fed synthetic rows, so nothing here touches the chain or the store."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault('NEAR_SCRAPE', '0')

import search  # noqa: E402
from search import CONCEPTS, VectorIndex, index_rows, tokens  # noqa: E402

ROWS = [
    {'account_id': 'usdt.tether-token.near', 'label': 'USDt (native)',
     'category': 'token'},
    {'account_id': 'contract.main.burrow.near', 'label': 'Burrow lending',
     'category': 'defi'},
    {'account_id': 'v2.ref-finance.near', 'label': 'Ref Finance DEX',
     'category': 'defi'},
    {'account_id': 'meta-pool.near', 'label': 'Meta Pool stNEAR',
     'category': 'staking'},
    {'account_id': 'mytoken.factory.near', 'label': None, 'category': None,
     'methods': ['ft_transfer', 'ft_balance_of', 'storage_deposit']},
]


def _top(q, limit=3):
    ix = index_rows(ROWS)
    return [doc for doc, _, _ in ix.query(q, limit=limit)]


def test_concept_query_reaches_contract_it_shares_no_chars_with():
    # "stablecoin" and "usdt.tether-token.near" share no token — only the
    # concept lexicon connects them.
    assert _top('stablecoin')[0] == 'usdt.tether-token.near'


def test_meaning_beats_string():
    assert _top('lending protocol')[0] == 'contract.main.burrow.near'
    assert _top('swap exchange')[0] == 'v2.ref-finance.near'
    assert _top('liquid staking')[0] == 'meta-pool.near'


def test_method_names_are_searchable():
    # The unlabeled scraped contract is only findable by its interface.
    assert _top('ft_transfer')[0] == 'mytoken.factory.near'


def test_trigram_rescues_a_typo():
    ix = index_rows(ROWS)
    hits = ix.query('burow')
    assert hits and hits[0][0] == 'contract.main.burrow.near'


def test_why_explains_the_match():
    ix = index_rows(ROWS)
    _, _, why = ix.query('lending')[0]
    assert 'lending' in why


def test_empty_and_garbage_queries():
    ix = index_rows(ROWS)
    assert ix.query('') == []
    assert ix.query('zzqqxxvv') == []


def test_engine_is_generic():
    # The reusable core: no NEAR anywhere.
    ix = VectorIndex(lexicon={'fruit': ['apple', 'banana']})
    ix.add('doc-a', [('apple', 1.0), ('pie', 1.0)])
    ix.add('doc-b', [('server', 1.0), ('rack', 1.0)])
    hits = ix.query('fruit')
    assert [h[0] for h in hits] == ['doc-a']


def test_search_tool_substring_still_wins(monkeypatch):
    # The old exact/substring behaviour must survive the semantics.
    monkeypatch.setattr(search, 'corpus_rows', lambda net: ROWS)
    search._cache.clear()
    r = search.search('ref-finance', network='mainnet', limit=5)
    assert r['results'][0]['account_id'] == 'v2.ref-finance.near'
    assert 'account id' in r['results'][0]['matched']
    assert 'methods' not in r['results'][0]


def test_search_tool_refuses_empty_q(monkeypatch):
    monkeypatch.setattr(search, 'corpus_rows', lambda net: ROWS)
    search._cache.clear()
    import pytest
    from chain import NearError
    with pytest.raises(NearError):
        search.search('', network='mainnet')


def test_concepts_have_no_duplicate_terms_across_bags():
    # A term claimed by many bags dilutes both — keep overlaps deliberate.
    ix = VectorIndex(lexicon=CONCEPTS)
    assert all(len(v) <= 2 for v in ix.concept_of.values())


def test_tokens():
    assert tokens('token.v2.ref-finance.near') == \
        ['token', 'v2', 'ref', 'finance', 'near']
