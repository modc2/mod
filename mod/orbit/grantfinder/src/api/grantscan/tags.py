"""Tags — every grant gets a normalised, grouped tag set.

The agent proposes tags; this module normalises them and adds the ones a
keyword scan of the record can prove (ecosystem, topic, mechanism, status,
amount bucket), so filtering works even when the agent forgot one.
"""
import re

# group -> {tag: regex over name+funder+summary+eligibility+ecosystem}
VOCAB = {
    'category': {
        'open-source': r'open[- ]?source|\bfoss\b|\bfloss\b|\boss\b',
        'crypto': r'blockchain|crypto|web3|\bdao\b|on-?chain|token|defi|ethereum|bitcoin',
    },
    'ecosystem': {e: r'\b' + p + r'\b' for e, p in {
        'ethereum': r'ethereum|\beth\b|\besp\b', 'bitcoin': r'bitcoin|\bbtc\b', 'lightning': 'lightning',
        'solana': 'solana|superteam', 'polkadot': r'polkadot|kusama|web3 foundation',
        'cosmos': r'cosmos|interchain', 'near': 'near', 'starknet': r'starknet|starkware',
        'arbitrum': 'arbitrum', 'optimism': r'optimism|superchain', 'base': r'base (?:builder|ecosystem)|coinbase',
        'polygon': 'polygon', 'zksync': r'zksync|matter labs|zk ?stack', 'scroll': 'scroll',
        'linea': 'linea', 'sui': 'sui', 'aptos': 'aptos', 'avalanche': r'avalanche|avax',
        'ton': r'ton|the open network', 'stellar': 'stellar', 'cardano': r'cardano|catalyst',
        'algorand': 'algorand', 'tezos': 'tezos', 'filecoin': r'filecoin|protocol labs|ipfs',
        'celo': 'celo', 'gnosis': 'gnosis', 'zcash': r'zcash|\bzcg\b', 'monero': 'monero',
        'bittensor': 'bittensor', 'mina': 'mina', 'aleo': 'aleo', 'nostr': 'nostr',
        'icp': r'internet computer|dfinity', 'hedera': 'hedera', 'aztec': 'aztec',
    }.items()},
    'topic': {
        'zk': r'zero[- ]knowledge|\bzk|snark|stark\b|zkp',
        'privacy': r'privacy|private|anonym',
        'security': r'security|audit|vulnerab|supply[- ]chain|bug bount|resilien',
        'defi': r'\bdefi\b|lending|\bdex\b|liquidity|stablecoin',
        'infrastructure': r'infrastructure|\binfra\b|critical|protocol development|client',
        'devtools': r'developer tool|devtool|tooling|sdk|library|libraries|compiler',
        'public-goods': r'public goods?|commons|\bpgf\b',
        'research': r'research|academic|scien|university|phd',
        'education': r'educat|tutorial|course|learn|onboard|documentation|docs\b',
        'ai': r'\bai\b|artificial intelligence|machine learning|\bml\b|\bllm|model weights|open weights',
        'community': r'community|events?|meetup|hackathon|ambassador',
        'climate': r'climate|regenerat|\brefi\b|carbon|environment',
        'accessibility': r'accessib|a11y',
        'internet': r'internet|\bdns\b|network protocol|\bngi\b|decentrali[sz]ed web',
    },
    'mechanism': {
        'retroactive': r'retro(?:active|pgf|funding)?\b',
        'quadratic': r'quadratic|matching pool|\bqf\b',
        'bounty': r'bount',
        'fellowship': r'fellowship|stipend|mentorship|internship|summer of',
        'milestone': r'milestone',
        'equity-free': r'equity[- ]free|non[- ]dilutive|no equity',
        'credits': r'credits?\b(?! card)',
    },
}

STATUS = ('open', 'rolling', 'upcoming', 'closed', 'unknown')
GROUP_OF = {t: g for g, tags in VOCAB.items() for t in tags}
_COMPILED = {t: re.compile(p, re.I) for g in VOCAB.values() for t, p in g.items()}


def norm(tag: str) -> str:
    t = re.sub(r'[^a-z0-9+]+', '-', str(tag).lower()).strip('-')
    return {'opensource': 'open-source', 'oss': 'open-source', 'foss': 'open-source',
            'web3': 'crypto', 'blockchain': 'crypto', 'zero-knowledge': 'zk', 'zkp': 'zk',
            'retro': 'retroactive', 'retropgf': 'retroactive', 'qf': 'quadratic',
            'public-good': 'public-goods', 'eth': 'ethereum', 'btc': 'bitcoin',
            'machine-learning': 'ai', 'infra': 'infrastructure', 'quadratic-funding': 'quadratic',
            'retroactive-funding': 'retroactive', 'retro-funding': 'retroactive', 'bounties': 'bounty',
            'developer-tools': 'devtools', 'developer-tooling': 'devtools', 'zk-proofs': 'zk',
            'rolling-program': 'rolling', 'stipends': 'stipend', 'fellowships': 'fellowship'}.get(t, t)


def bucket(usd_max) -> str:
    try:
        v = float(usd_max)
    except (TypeError, ValueError):
        return ''
    if v <= 0:
        return ''
    return ('under-10k' if v < 10_000 else '10k-50k' if v < 50_000
            else '50k-250k' if v < 250_000 else '250k-plus')


def tag(g: dict, category: str = '') -> list:
    """The full tag list for a grant record (sorted, unique)."""
    # status lives in status-*; the agent's own "open"/"rolling" tags would contradict it later
    out = {norm(t) for t in (g.get('tags') or []) if str(t).strip()} - set(STATUS)
    out |= {norm(c) for c in (g.get('categories') or []) if str(c).strip()}
    if category:
        out.add(category)
    text = ' '.join(str(g.get(k) or '') for k in
                    ('name', 'funder', 'summary', 'eligibility', 'ecosystem', 'amount_text'))
    for t, rx in _COMPILED.items():
        if rx.search(text):
            out.add(t)
    st = g.get('status') if g.get('status') in STATUS else 'unknown'
    out.add('status-' + st)
    b = bucket(g.get('amount_max_usd'))
    if b:
        out.add('amount-' + b)
    out.discard('')
    return sorted(out)


def group(t: str) -> str:
    if t.startswith('status-'):
        return 'status'
    if t.startswith('amount-'):
        return 'amount'
    return GROUP_OF.get(t, 'other')
