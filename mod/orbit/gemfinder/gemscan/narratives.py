"""
gemscan.narratives — the themes money rotates through, and how to spot them.

Each narrative has:
  keywords  matched (word-boundary, case-insensitive) against names, tickers
            and descriptions a source hands us
  cg        substrings of CoinGecko category ids — their 24h market-cap move
            is the crypto side of the narrative's heat
  stocks    seed tickers that ARE the narrative on the stock side; their day
            move + reddit mentions are the stock side of its heat
  hn        a Hacker News query; story count over 7 days is the
            "is the wider internet talking about it" signal

Edit freely: this list is the module's thesis. `m gemfinder/narratives` shows
the live heat for each.
"""
from __future__ import annotations

import re

NARRATIVES = {
    'ai': {
        'label': 'AI / agents',
        'keywords': ['ai', 'agent', 'agents', 'gpt', 'llm', 'neural', 'intelligence', 'inference', 'agi'],
        'cg': ['artificial-intelligence', 'ai-agents', 'ai-framework', 'ai-applications'],
        'stocks': ['NVDA', 'PLTR', 'SOUN', 'BBAI', 'AI', 'SMCI', 'TEM', 'CRWV', 'NBIS', 'APLD'],
        'hn': 'AI agents',
    },
    'quantum': {
        'label': 'Quantum computing',
        'keywords': ['quantum', 'qubit', 'qubits'],
        'cg': ['quantum'],
        'stocks': ['IONQ', 'RGTI', 'QBTS', 'QUBT', 'ARQQ', 'QMCO', 'LAES'],
        'hn': 'quantum computing',
    },
    'nuclear': {
        'label': 'Nuclear / uranium / power',
        'keywords': ['nuclear', 'uranium', 'reactor', 'smr', 'fusion'],
        'cg': ['energy'],
        'stocks': ['OKLO', 'SMR', 'NNE', 'CCJ', 'LEU', 'UEC', 'UUUU', 'VST', 'CEG', 'BWXT'],
        'hn': 'nuclear reactor',
    },
    'space': {
        'label': 'Space / satellites',
        'keywords': ['space', 'satellite', 'orbital', 'rocket', 'lunar', 'mars'],
        'cg': ['space'],
        'stocks': ['RKLB', 'ASTS', 'LUNR', 'RDW', 'PL', 'SPCE', 'BKSY', 'FLY'],
        'hn': 'satellite launch',
    },
    'defense': {
        'label': 'Defense / drones',
        'keywords': ['defense', 'defence', 'drone', 'drones', 'military', 'missile'],
        'cg': ['defense'],
        'stocks': ['KTOS', 'AVAV', 'RCAT', 'UMAC', 'ONDS', 'DPRO', 'PLTR', 'LMT'],
        'hn': 'drone warfare',
    },
    'robotics': {
        'label': 'Robotics / humanoids',
        'keywords': ['robot', 'robots', 'robotics', 'humanoid', 'autonomous'],
        'cg': ['robotics'],
        'stocks': ['TSLA', 'SERV', 'RR', 'ARBE', 'KITT', 'PDYN'],
        'hn': 'humanoid robot',
    },
    'btc_treasury': {
        'label': 'Crypto treasuries / miners',
        'keywords': ['bitcoin', 'btc', 'treasury', 'miner', 'mining', 'hashrate'],
        'cg': ['bitcoin-ecosystem', 'mining'],
        'stocks': ['MSTR', 'MARA', 'RIOT', 'CLSK', 'CIFR', 'IREN', 'HUT', 'WULF', 'BMNR', 'SBET', 'COIN'],
        'hn': 'bitcoin treasury',
    },
    'rwa': {
        'label': 'RWA / tokenization',
        'keywords': ['rwa', 'tokenized', 'tokenization', 'real world', 'treasuries', 'stablecoin'],
        'cg': ['real-world-assets', 'tokenized', 'stablecoin'],
        'stocks': ['CRCL', 'HOOD', 'COIN', 'GLXY'],
        'hn': 'tokenization stablecoin',
    },
    'depin': {
        'label': 'DePIN / decentralized compute',
        'keywords': ['depin', 'compute', 'gpu', 'wireless', 'storage', 'bittensor', 'tao'],
        'cg': ['depin', 'decentralized-compute', 'bittensor'],
        'stocks': ['CRWV', 'NBIS', 'APLD', 'IREN'],
        'hn': 'decentralized GPU compute',
    },
    'memes': {
        'label': 'Memes / culture',
        'keywords': ['meme', 'memecoin', 'pepe', 'doge', 'inu', 'frog', 'wojak', 'bonk'],
        'cg': ['meme'],
        'stocks': ['GME', 'AMC', 'OPEN', 'KSS', 'DNUT', 'GPRO'],
        'hn': 'meme stock',
    },
    'biotech': {
        'label': 'Biotech catalysts',
        'keywords': ['bio', 'biotech', 'therapeutics', 'pharma', 'fda', 'clinical', 'oncology', 'longevity', 'desci'],
        'cg': ['desci'],
        'stocks': ['HIMS', 'RXRX', 'TEM', 'NVAX', 'SAVA', 'CRSP'],
        'hn': 'FDA approval',
    },
    'gaming': {
        'label': 'Gaming / metaverse',
        'keywords': ['game', 'gaming', 'metaverse', 'esports'],
        'cg': ['gaming', 'metaverse', 'play-to-earn'],
        'stocks': ['RBLX', 'U', 'SKLZ', 'TTWO'],
        'hn': 'video game',
    },
    'privacy': {
        'label': 'Privacy / ZK',
        'keywords': ['privacy', 'zk', 'zero-knowledge', 'anonymous', 'zcash', 'monero'],
        'cg': ['privacy', 'zero-knowledge'],
        'stocks': [],
        'hn': 'zero knowledge proof',
    },
}

_PATTERNS = {nid: re.compile(r'(?<![a-z0-9])(' + '|'.join(re.escape(k) for k in n['keywords']) + r')(?![a-z0-9])',
                             re.I)
             for nid, n in NARRATIVES.items()}
_SEED = {}
for _nid, _n in NARRATIVES.items():
    for _t in _n['stocks']:
        _SEED.setdefault(_t, []).append(_nid)


def match(text: str = '', symbol: str = '', asset: str = '') -> list:
    """Narrative ids a candidate belongs to: seed ticker (stocks) or keyword hit in its text."""
    hits = list(_SEED.get(symbol.upper(), [])) if asset == 'stock' else []
    t = text or ''
    for nid, pat in _PATTERNS.items():
        if nid not in hits and pat.search(t):
            hits.append(nid)
    return hits


# ── typed themes ─────────────────────────────────────────────────────────
# The user types something dope ("quantum", "frog coins", "nuclear drones",
# "solana agents") and it becomes a 14th narrative for that scan: keywords from
# the words typed (prefix match, so "robot" also hits "robotics"), CoinGecko
# categories whose name contains one of them, an HN query, and no seed stocks
# (Yahoo search supplies those live).

CUSTOM_ID = 'q'
STOP = {'the', 'a', 'an', 'of', 'and', 'or', 'for', 'in', 'on', 'to', 'with', 'coin', 'coins', 'token', 'tokens',
        'stock', 'stocks', 'crypto', 'cryptos', 'play', 'plays', 'gem', 'gems', 'best', 'new', 'hot', 'top',
        'next', 'something', 'dope', 'cool', 'good', 'moon', 'mooning', 'pump', 'pumping', 'is', 'are', 'what'}
MAX_WORDS = 6
# what a short word also means when it names a CoinGecko category or a coin description
ALIASES = {'ai': ['artificial intelligence'], 'zk': ['zero knowledge', 'zero-knowledge'], 'btc': ['bitcoin'],
           'eth': ['ethereum'], 'sol': ['solana'], 'rwa': ['real world assets', 'real-world assets'],
           'defi': ['decentralized finance'], 'depin': ['decentralized physical'], 'meme': ['memes', 'memecoin'],
           'quantum': ['qubit'], 'nuclear': ['uranium'], 'drone': ['drones', 'uav'], 'drones': ['drone', 'uav'],
           'robot': ['robotics', 'humanoid'], 'robots': ['robotics', 'humanoid'], 'space': ['satellite', 'orbital'],
           'privacy': ['anonymous', 'private'], 'gaming': ['game', 'games', 'play to earn'],
           'bio': ['biotech', 'therapeutics'], 'cat': ['cats', 'kitty'], 'dog': ['dogs', 'doge', 'inu'],
           'frog': ['frogs', 'pepe'], 'cute': ['kawaii']}


def keywords(q: str) -> list:
    """'the next quantum computing plays' -> ['quantum', 'computing']. Keeps 2-letter words
    ('ai', 'zk'), drops filler; a single filler word ("dope") is still searched as itself."""
    words = [w for w in re.findall(r'[a-z0-9][a-z0-9\-\.]*', (q or '').lower()) if len(w) >= 2]
    kept = [w for w in words if w not in STOP]
    out = []
    for w in kept or (words if len(words) == 1 else []):
        if w not in out:
            out.append(w)
    return out[:MAX_WORDS]


def custom(q: str, categories: list = ()) -> dict:
    """A narrative built from typed text, in the same shape as NARRATIVES entries (+ a compiled pattern)."""
    kws = keywords(q)
    if not kws:
        raise ValueError('type a theme: a word or two, e.g. quantum, frog, nuclear drones')
    terms = []
    for k in kws:
        for t in [k] + ALIASES.get(k, []):
            if t not in terms:
                terms.append(t)
    # short words must stand alone ("ai" is not "airline"); longer ones may be a prefix ("robot" -> robotics)
    alts = [re.escape(t) + (r'(?![a-z0-9])' if len(t) <= 3 else '') for t in terms]
    pat = re.compile(r'(?<![a-z0-9])(' + '|'.join(alts) + r')', re.I)
    # categories the words name, biggest first so the two we fetch coins for are the ones that matter
    cats = [c for c in categories
            if c.get('id') and pat.search(f"{c.get('id', '')} {c.get('name', '')}".replace('-', ' '))]
    cats.sort(key=lambda c: -(c.get('market_cap') or 0))
    return {'id': CUSTOM_ID, 'label': f'"{q.strip()}"', 'keywords': kws, 'terms': terms,
            'cg': [c['id'] for c in cats[:6]], 'stocks': [], 'hn': ' '.join(kws), 'pattern': pat,
            'query': q.strip()}


def hit(theme: dict, text: str = '', symbol: str = '', name: str = '') -> bool:
    return bool(theme['pattern'].search(f'{symbol} {name} {text}'))


def ids() -> list:
    return list(NARRATIVES)
