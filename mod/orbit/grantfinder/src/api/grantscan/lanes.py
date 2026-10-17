"""Lanes — one agent run each. A lane is a slice of the grant landscape
small enough for one run to cover properly; a sweep runs every lane.

Add a lane = add a row. `category` is the top-level tag every grant the lane
finds gets; the brief is what the agent is told to dig through.
"""

LANES = [
    # ── open source ──────────────────────────────────────────────────
    {'id': 'oss-foundations', 'category': 'open-source', 'title': 'OSS foundations & funds',
     'brief': 'Non-profit funds that pay open source maintainers and projects: NLnet (NGI calls), '
              'Sovereign Tech Agency/Fund, Open Technology Fund, Mozilla, Linux Foundation, Apache, '
              'OSI, Python Software Foundation, NumFOCUS, Rust Foundation, OpenJS, Eclipse, GNOME, KDE, '
              'Prototype Fund, FLOSS/fund, Open Collective funds, and any similar ones you find.'},
    {'id': 'oss-corporate', 'category': 'open-source', 'title': 'Corporate OSS funds',
     'brief': 'Grant and sponsorship programs run by companies for open source: GitHub (Secure Open '
              'Source Fund, Accelerator), Google (Open Source Peer Bonus, GSoC stipends), Microsoft '
              'FOSS Fund, Sentry, Vercel OSS program, AWS, Cloudflare Project Alexandria, Stripe, '
              'Anthropic/OpenAI researcher or open source credits, FUTO, Bloomberg, and similar.'},
    {'id': 'oss-security', 'category': 'open-source', 'title': 'OSS security & infrastructure',
     'brief': 'Funding for open source security, supply chain and critical infrastructure: OpenSSF '
              '(Alpha-Omega), Sovereign Tech Agency (Bug Resilience, Fellowship), OSTIF, Internet '
              'Society Foundation, Prossimo/ISRG, Open Source Security Foundation, CISA, and similar.'},
    {'id': 'oss-public', 'category': 'open-source', 'title': 'Public & research money for OSS',
     'brief': 'Government, EU and science-funder programs that fund open source software: EU NGI '
              '(Zero, Core, Commons), Horizon calls that require open source, NSF POSE, NASA ROSES '
              'open source, Chan Zuckerberg Initiative EOSS, Sloan Foundation, Wellcome, Schmidt, '
              'UK/German/French/Dutch national programs, Code for Science & Society, and similar.'},
    {'id': 'oss-ai', 'category': 'open-source', 'title': 'Open source AI',
     'brief': 'Grants for open source / open weights AI, datasets, safety and tooling: compute grants, '
              'Hugging Face, Mozilla AI, LAION, AI safety funds that fund open tools, Open Philanthropy '
              'open calls, and similar.'},
    {'id': 'oss-fellowships', 'category': 'open-source', 'title': 'OSS fellowships & stipends',
     'brief': 'Paid programs for individual contributors to open source: GSoC, Outreachy, LFX '
              'Mentorship, MLH Fellowship, Igalia Coding Experience, Season of Docs, Summer of Nix, '
              'and similar stipended programs.'},
    # ── crypto ───────────────────────────────────────────────────────
    {'id': 'crypto-ethereum', 'category': 'crypto', 'title': 'Ethereum & L2 ecosystems',
     'brief': 'Grants from Ethereum and its rollups/L2s: Ethereum Foundation ESP, Optimism (Grants '
              'Council, RetroFunding), Arbitrum (DAO grants, Questbook), Base (Builder Grants), '
              'Polygon, zkSync, Scroll, Linea, Starknet, Taiko, Mantle, Celo, Gnosis, and similar.'},
    {'id': 'crypto-alt-l1', 'category': 'crypto', 'title': 'Other L1 ecosystems',
     'brief': 'Grants from non-Ethereum chains: Solana Foundation and Superteam, Polkadot (Web3 '
              'Foundation, treasury), Cosmos/Interchain, NEAR, Sui, Aptos, Avalanche, TON, Stellar '
              '(SCF), Cardano (Catalyst), Algorand, Tezos, Hedera, Internet Computer (DFINITY), '
              'Filecoin/Protocol Labs, Mina, Aleo, Bittensor, and similar.'},
    {'id': 'crypto-bitcoin', 'category': 'crypto', 'title': 'Bitcoin & privacy',
     'brief': 'Grants for Bitcoin, Lightning, and privacy coins: OpenSats, Brink, Spiral, HRF Bitcoin '
              'Development Fund, Btrust, Vinteum, Summer of Bitcoin, Zcash (ZCG), Monero CCS, Nostr '
              'funds, and similar.'},
    {'id': 'crypto-public-goods', 'category': 'crypto', 'title': 'Crypto public goods funding',
     'brief': 'Public goods and community funding rails: Gitcoin Grants rounds, Protocol Guild, '
              'Optimism RetroPGF, Octant epochs, clr.fund, Giveth, Drips, Allo-based rounds, '
              'Funding the Commons, Zuzalu/pop-up city funds, and similar.'},
    {'id': 'crypto-defi', 'category': 'crypto', 'title': 'DeFi & protocol grants',
     'brief': 'Grants from DeFi protocols and DAOs: Uniswap Foundation, Aave Grants DAO, Compound, '
              'MakerDAO/Sky, Lido, Curve, Balancer, Chainlink, The Graph, ENS public goods, Safe, '
              'dYdX, 1inch, and similar.'},
    {'id': 'crypto-zk-research', 'category': 'crypto', 'title': 'ZK, cryptography & research',
     'brief': 'Grants for zero-knowledge, cryptography and protocol research: EF PSE / Privacy & '
              'Scaling Explorations, zkSync/Matter Labs, Aztec, 0xPARC, Succinct, RISC Zero, '
              'Starknet research, IC3, a16z crypto research, and similar.'},
]

BY_ID = {l['id']: l for l in LANES}
CATEGORIES = sorted({l['category'] for l in LANES})


def pick(lanes=None, category=None) -> list:
    """Lanes by id list / comma string, or by category; default all."""
    if isinstance(lanes, str):
        lanes = [s.strip() for s in lanes.split(',') if s.strip()]
    if lanes:
        bad = [l for l in lanes if l not in BY_ID]
        if bad:
            raise ValueError(f'unknown lane(s): {", ".join(bad)} — see lanes()')
        return [BY_ID[l] for l in lanes]
    if category:
        return [l for l in LANES if l['category'] == category]
    return list(LANES)
