#!/usr/bin/env python3
"""near search — semantic search over contracts, local and zero-dep.

Ask "stablecoin" and get USDt and USDC; ask "lending" and get Burrow; ask
"ft_transfer" and get every token whose interface this module ever parsed.
The lookup bar stops being an exact-name gate: a query that is not an
account is still an answer.

Three local signals, layered — no model download, no API, no third party:

  concepts   a small domain lexicon maps what people mean onto what
             contracts are called (stablecoin→usdt/usdc/tether,
             lending→borrow/burrow, swap→dex/amm/ref) — applied to the
             corpus and the query alike, so matching happens in concept
             space, not just string space.
  tf-idf     token vectors over everything the module knows about a
             contract — account-id parts, curated label/category/blurb,
             WASM method names from every interface it ever read — ranked
             by cosine similarity, rare terms weighing more.
  trigrams   character n-grams rescue typos and part-words: an unknown
             query token borrows the vocabulary token it most resembles.

The engine (VectorIndex) is generic: feed it (id, weighted terms) docs and
a lexicon, get ranked matches with the matched terms explained. Any module
with a corpus to search can lift it whole.
"""

import math
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

_TOKEN = re.compile(r'[a-z0-9]+')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')

# What people mean → what contracts are called. Each concept is one bag of
# equivalent terms; any term in a bag also emits the bag's name, so a query
# term and a corpus term meet even when they share no characters.
CONCEPTS = {
    'stablecoin': ['usdt', 'usdc', 'usd', 'tether', 'dai', 'frax', 'stable',
                   'peg', 'dollar'],
    'token': ['ft', 'nep141', 'fungible', 'coin', 'currency', 'mint',
              'transfer', 'balance'],
    'nft': ['nep171', 'collectible', 'nonfungible', 'mbase', 'paras'],
    'swap': ['dex', 'amm', 'exchange', 'trade', 'pool', 'liquidity', 'ref',
             'router', 'intents', 'solver'],
    'lending': ['borrow', 'lend', 'loan', 'collateral', 'burrow', 'supply',
                'margin', 'repay', 'liquidate'],
    'staking': ['stake', 'unstake', 'validator', 'delegate', 'epoch',
                'restake', 'liquid', 'stnear', 'linear', 'metapool'],
    'wrapped': ['wrap', 'wnear', 'unwrap', 'deposit', 'withdraw'],
    'oracle': ['price', 'feed', 'priceoracle', 'report'],
    'bridge': ['aurora', 'evm', 'rainbow', 'omni', 'crosschain', 'relay'],
    'social': ['profile', 'post', 'follow', 'graph', 'widget'],
    'dao': ['governance', 'vote', 'proposal', 'council', 'sputnik', 'astro'],
    'game': ['play', 'arcade', 'hot', 'sweat', 'reward', 'earn'],
    'infra': ['registrar', 'factory', 'multisig', 'lockup', 'keypom',
              'linkdrop', 'platform'],
    'defi': ['finance', 'yield', 'vault', 'farm', 'apy'],
}

# One sentence of meaning per curated contract — this is where "lending
# protocol" learns to land on Burrow. Curation is the semantic layer the
# chain itself cannot provide; everything else is scraped or parsed.
BLURBS = {
    'wrap.near': 'wrapped near wnear the fungible token form of near itself, '
                 'deposit to wrap and withdraw to unwrap, what every dex trades',
    'usdt.tether-token.near': 'tether usdt native stablecoin dollar '
                              'pegged fungible token',
    '17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1':
        'circle usdc native stablecoin dollar pegged fungible token',
    'token.sweat': 'sweat economy move to earn walking steps reward token',
    'game.hot.tg': 'hot telegram tap game mining reward token',
    'token.v2.ref-finance.near': 'ref finance governance reward token of the dex',
    'aurora': 'aurora evm ethereum virtual machine on near, solidity '
              'contracts bridge platform',
    'v2.ref-finance.near': 'ref finance dex amm swap pools liquidity '
                           'exchange trading',
    'intents.near': 'near intents cross chain swap solver settlement '
                    'exchange trading',
    'contract.main.burrow.near': 'burrow lending borrow supply collateral '
                                 'interest money market defi',
    'meta-pool.near': 'meta pool stnear liquid staking stake near earn yield',
    'linear-protocol.near': 'linear protocol liquid staking stnear stake yield',
    'social.near': 'near social on chain social graph profiles posts widgets',
    'near': 'account registrar where named accounts are created, the root '
            'contract infra',
    'poolv1.near': 'staking pool factory validators delegate infra',
    'priceoracle.near': 'price oracle feeds asset prices to defi infra',
    'wrap.testnet': 'wrapped near wnear testnet fungible token',
    'usdt.fakes.testnet': 'fake tether usdt testnet stablecoin token',
    'usdc.fakes.testnet': 'fake circle usdc testnet stablecoin token',
    'ref-finance-101.testnet': 'ref finance testnet dex amm swap exchange',
    'priceoracle.testnet': 'price oracle testnet feeds prices infra',
    'guest-book.testnet': 'guest book example hello world tutorial contract',
}

INDEX_TTL = float(os.environ.get('NEAR_SEARCH_TTL', 60))


def tokens(text):
    return _TOKEN.findall(str(text or '').lower())


def _trigrams(tok):
    padded = f'  {tok} '
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


class VectorIndex:
    """TF-IDF cosine over weighted term bags, with a concept lexicon and a
    trigram fallback for unknown query terms. Generic on purpose — docs are
    (doc_id, [(term, weight), ...]) and nothing here knows about NEAR."""

    def __init__(self, lexicon=None):
        # term -> concept names, both directions: the concept name itself
        # and every member term emit the same concept marker.
        self.concept_of = {}
        for name, members in (lexicon or {}).items():
            for term in [name] + list(members):
                self.concept_of.setdefault(term, set()).add(name)
        self.docs = {}          # doc_id -> {term: weight}
        self.df = {}            # term -> docs containing it
        self._norm = {}

    def _expand(self, pairs, concept_weight=0.6):
        """A term bag plus the concept markers its terms light up."""
        bag = {}
        for term, w in pairs:
            bag[term] = max(bag.get(term, 0.0), w)
            for concept in self.concept_of.get(term, ()):
                key = '§' + concept          # marker, never a real token
                bag[key] = max(bag.get(key, 0.0), w * concept_weight)
        return bag

    def add(self, doc_id, pairs):
        bag = self._expand(pairs)
        self.docs[doc_id] = bag
        for term in bag:
            self.df[term] = self.df.get(term, 0) + 1

    def _idf(self, term):
        n = len(self.docs) or 1
        return math.log(1 + n / (1 + self.df.get(term, 0)))

    def _weights(self, bag):
        return {t: w * self._idf(t) for t, w in bag.items()}

    def norm(self, doc_id):
        if doc_id not in self._norm:
            v = self._weights(self.docs[doc_id])
            self._norm[doc_id] = math.sqrt(sum(w * w for w in v.values())) or 1.0
        return self._norm[doc_id]

    def _nearest_term(self, tok):
        """The vocabulary term a typo most resembles, by trigram Dice."""
        grams = _trigrams(tok)
        best, best_s = None, 0.0
        for term in self.df:
            if term.startswith('§') or abs(len(term) - len(tok)) > 3:
                continue
            tg = _trigrams(term)
            s = 2 * len(grams & tg) / (len(grams) + len(tg))
            if s > best_s:
                best, best_s = term, s
        return (best, best_s) if best_s >= 0.5 else (None, 0.0)

    def query(self, text, limit=10):
        """Ranked [(doc_id, score, matched_terms)]. Query terms unknown to
        the vocabulary borrow their nearest trigram neighbour, discounted."""
        pairs, seen = [], set()
        for tok in tokens(text):
            if tok in seen:
                continue
            seen.add(tok)
            if tok in self.df or self.concept_of.get(tok):
                pairs.append((tok, 1.0))
            else:
                near_term, sim = self._nearest_term(tok)
                if near_term:
                    pairs.append((near_term, sim))
        if not pairs:
            return []
        qbag = self._weights(self._expand(pairs))
        qnorm = math.sqrt(sum(w * w for w in qbag.values())) or 1.0
        hits = []
        for doc_id, bag in self.docs.items():
            dv = self._weights(bag)
            shared = qbag.keys() & dv.keys()
            if not shared:
                continue
            score = sum(qbag[t] * dv[t] for t in shared) / (qnorm * self.norm(doc_id))
            why = sorted({t.lstrip('§') for t in shared},
                         key=lambda t: -(qbag.get(t, 0) or qbag.get('§' + t, 0)))
            hits.append((doc_id, round(score, 4), why[:6]))
        hits.sort(key=lambda h: -h[1])
        return hits[:limit]


# ── the NEAR corpus ──────────────────────────────────────────────

def corpus_rows(network):
    """Everything searchable about every contract this module knows: the
    curated registry, the scraped directory, and the method names of every
    interface ever parsed — merged per account id."""
    from chain import KNOWN_CONTRACTS
    rows = {}
    for cid, label, cat in KNOWN_CONTRACTS.get(network, []):
        rows[cid] = {'account_id': cid, 'label': label, 'category': cat}
    try:
        import directory
        d = directory.get(network)
        with d.lock:
            stored = {cid: dict(c) for cid, c in d.state['contracts'].items()}
        for cid, c in stored.items():
            r = rows.setdefault(cid, {'account_id': cid})
            r.update({'live': c.get('live'), 'storage_bytes': c.get('bytes'),
                      'balance_near': c.get('near'), 'via': c.get('src'),
                      'methods': c.get('fns')})
    except Exception:
        pass
    return list(rows.values())


def index_rows(rows, lexicon=CONCEPTS, blurbs=BLURBS):
    """Rows → a VectorIndex. Field weights say what a match is worth:
    the name people use beats the account id beats the method soup."""
    ix = VectorIndex(lexicon=lexicon)
    for r in rows:
        cid = r['account_id']
        pairs = [(cid, 3.0)]
        id_toks = tokens('implicit hex account' if _HEX64.match(cid) else cid)
        pairs += [(t, 2.0) for t in id_toks]
        pairs += [(t, 3.0) for t in tokens(r.get('label'))]
        pairs += [(t, 2.5) for t in tokens(r.get('category'))]
        pairs += [(t, 1.5) for t in tokens(blurbs.get(cid))]
        for m in (r.get('methods') or [])[:64]:
            pairs += [(m.lower(), 1.2)] + [(t, 0.8) for t in tokens(m)]
        ix.add(cid, pairs)
    return ix


_cache = {}                      # network -> {'at', 'n', 'ix', 'rows'}
_cache_lock = threading.Lock()


def _index(network):
    with _cache_lock:
        held = _cache.get(network)
        if held and time.time() - held['at'] < INDEX_TTL:
            return held['ix'], held['rows']
    rows = corpus_rows(network)
    ix = index_rows(rows)
    with _cache_lock:
        _cache[network] = {'at': time.time(), 'ix': ix,
                           'rows': {r['account_id']: r for r in rows}}
    return ix, _cache[network]['rows']


def search(q, network=None, limit=10):
    """The tool: plain words in, ranked contracts out, each with the terms
    that matched so the ranking explains itself."""
    network = network or os.environ.get('NEAR_NETWORK') or 'mainnet'
    q = str(q or '').strip()
    if not q:
        from chain import NearError
        raise NearError('q is required — try "stablecoin", "lending", '
                        '"swap", or a method name like ft_transfer')
    limit = max(1, min(int(limit or 10), 50))
    ix, rows = _index(network)
    ql = q.lower()
    hits = ix.query(ql, limit=limit * 3)
    scored = {cid: (s, why) for cid, s, why in hits}
    # An exact or substring hit on the account id must always surface,
    # whatever the vector said — that is the old behaviour, kept.
    for cid in rows:
        if ql in cid:
            s, why = scored.get(cid, (0.0, []))
            scored[cid] = (max(s, 0.9 if cid == ql else 0.6),
                           ['account id'] + [w for w in why if w != cid])
    out = []
    for cid, (s, why) in sorted(scored.items(), key=lambda kv: -kv[1][0]):
        r = dict(rows.get(cid) or {'account_id': cid})
        r.pop('methods', None)
        r.update({'score': round(s, 4), 'matched': why})
        out.append(r)
    return {'network': network, 'q': q, 'count': min(len(out), limit),
            'results': out[:limit],
            'note': 'ranked locally — tf-idf cosine over account ids, curated '
                    'labels and parsed method names, a concept lexicon so '
                    '"stablecoin" finds usdt, trigrams so typos still land. '
                    'No model, no API: the index rebuilds from the local '
                    'directory store.'}


if __name__ == '__main__':
    import json
    q = ' '.join(sys.argv[1:]) or 'stablecoin'
    print(json.dumps(search(q), indent=2))
