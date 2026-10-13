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


def ids() -> list:
    return list(NARRATIVES)
