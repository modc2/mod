"""
OpenHouse — the landscape.

Every other on-chain housing project we could find, on one axis that actually
matters: **who ends up owning the house.** Almost all of them tokenize the
*investor* side — an absentee holder buys a slice of a rental and the person
living there still walks away with receipts. OpenHouse is on the other side of
that trade: the occupant's payment is the thing that buys the house.

Two kinds of data live here, and they are deliberately kept apart:

  PEERS   editorial facts, each carrying its own source URL. Anything we could
          not verify is None, and renders as "—". We would rather show a gap
          than a confident wrong number.
  live()  numbers pulled from public, keyless endpoints at read time — RealT's
          community API and CoinGecko. Cached 15 minutes so the marketing page
          can't hammer somebody else's API.

Sources are current as of 2026-08-03.
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

CACHE_TTL = 15 * 60
HTTP_TIMEOUT = 12

REALT_API = 'https://api.realtoken.community/v1/token'
COINGECKO_API = 'https://api.coingecko.com/api/v3/coins/markets'


# ── The axis ───────────────────────────────────────────────────────
# Every project gets sorted into one of these. The categories are the argument:
# five ways to put housing on a chain, and only one of them ends with the
# resident owning the place.
CATEGORIES = {
    'occupant-equity': {
        'label': 'Occupant equity',
        'blurb': "The person living there accrues ownership by paying to live there.",
    },
    'investor-fractional': {
        'label': 'Investor fractional',
        'blurb': "Outside holders buy slices of a rental. The tenant is the yield.",
    },
    'homeowner-liquidity': {
        'label': 'Homeowner liquidity',
        'blurb': "An existing owner sells equity or future appreciation to investors.",
    },
    'title': {
        'label': 'Title & conveyance',
        'blurb': "Moves the deed on-chain. Whole homes, not fractions.",
    },
    'synthetic': {
        'label': 'Synthetic exposure',
        'blurb': "Price exposure to housing indices. No property, no keys.",
    },
    'infrastructure': {
        'label': 'Tokenization infra',
        'blurb': "Rails other people issue property tokens on.",
    },
    'offchain-rto': {
        'label': 'Off-chain rent-to-own',
        'blurb': "The same promise as OpenHouse, made by a company instead of a contract.",
    },
}


# ── The peers ──────────────────────────────────────────────────────
PEERS = [
    {
        'id': 'realt',
        'name': 'RealT',
        'chain': 'Gnosis / Ethereum',
        'category': 'investor-fractional',
        'thesis': 'Tokenize single-family rentals; stream net rent to holders in stablecoin.',
        'wrapper': 'One Wyoming/Delaware LLC per property; token = LLC membership interest',
        'min_ticket': '~$50 / token',
        'occupant_equity': False,
        'equity_to': 'Outside token holders',
        'take': 'No holding fee; ~8–12% of gross rent to property management; 2.5–3.9% on secondary DEX trades',
        'status': 'liquidating',
        'status_note': (
            'Announced voluntary liquidation of its U.S. structures on 2 July 2026 after '
            'Detroit filed what its Corporation Counsel called the largest nuisance abatement '
            'suit in the city\'s history — 100+ tokenized properties sitting vacant with unpaid '
            'taxes, water bills and blight fines. Distributions suspended; ~700 Detroit homes '
            'under a court-approved fiduciary; sale proceeds go to repairs, taxes and legal '
            'costs before holders see anything. ~$140M was raised.'
        ),
        'url': 'https://realt.co',
        'cg': None,
        'live_api': 'realt',
        'sources': [
            'https://cryptobriefing.com/realt-crypto-real-estate-collapse/',
            'https://outliermedia.org/realt-lawsuit-detroit-sues-crypto-landlord/',
            'https://www.michiganpublic.org/social-justice/2026-03-20/the-model-no-longer-works-crypto-landlords-detroit-enterprise-is-crumbling',
        ],
    },
    {
        'id': 'lofty',
        'name': 'Lofty',
        'chain': 'Algorand',
        'category': 'investor-fractional',
        'thesis': 'Fractional rentals with daily rent streaming and a built-in secondary marketplace.',
        'wrapper': 'Per-property LLC with a token-holder DAO over property decisions',
        'min_ticket': '~$50',
        'occupant_equity': False,
        'equity_to': 'Outside token holders',
        'take': None,
        'status': 'live',
        'status_note': 'Operating; the largest still-running investor-side platform after RealT\'s wind-down.',
        'url': 'https://www.lofty.ai',
        'cg': None,
        'live_api': None,
        'sources': [
            'https://algorand.co/case-studies/lofty-transform-real-estate-industry',
            'https://www.nftgators.com/loftys-real-estate-tokenization-platform-sets-new-tvl-record-at-37-6m/',
        ],
    },
    {
        'id': 'binaryx',
        'name': 'BinaryX',
        'chain': 'BNB Chain',
        'category': 'investor-fractional',
        'thesis': 'Fractional shares in overseas rental villas and apartments.',
        'wrapper': 'Per-property SPV',
        'min_ticket': '~$50',
        'occupant_equity': False,
        'equity_to': 'Outside token holders',
        'take': None,
        'status': 'live',
        'status_note': None,
        'url': 'https://binaryx.com',
        'cg': 'binaryx',
        'live_api': None,
        'sources': ['https://binaryx.com/blog/arrived-homes-alternatives'],
    },
    {
        'id': 'homium',
        'name': 'Homium',
        'chain': 'Avalanche',
        'category': 'homeowner-liquidity',
        'thesis': 'Tokenized shared-appreciation home equity loans — no monthly payment, repaid out of appreciation.',
        'wrapper': 'Digital securities issued via Securitize',
        'min_ticket': None,
        'occupant_equity': False,
        'equity_to': 'Investors buy the homeowner\'s future appreciation',
        'take': None,
        'status': 'live',
        'status_note': (
            'The nearest thing to an occupant-side protocol — but it runs the other way: '
            'the resident already owns the home and sells appreciation off. Equity flows '
            'out of the house, not into the resident.'
        ),
        'url': 'https://homium.com',
        'cg': None,
        'live_api': None,
        'sources': [
            'https://www.avax.network/about/blog/homium-issues-first-home-equity-loans-on-avalanche',
            'https://securitize.io/learn/blog/homium-launches-tokenized-home-equity-with-partner-securitize',
        ],
    },
    {
        'id': 'vesta',
        'name': 'Vesta Equity',
        'chain': 'Provenance',
        'category': 'homeowner-liquidity',
        'thesis': 'Home Equity Investments matched directly between homeowner and investor.',
        'wrapper': 'HEI agreement settled on-chain in a yield-bearing stablecoin',
        'min_ticket': None,
        'occupant_equity': False,
        'equity_to': 'Investors buy a slice of an existing owner\'s equity',
        'take': None,
        'status': 'live',
        'status_note': 'Closed the first natively-tokenized HEI in January 2026 — a $100k transaction.',
        'url': 'https://vestaequity.net',
        'cg': None,
        'live_api': None,
        'sources': ['https://www.einpresswire.com/article/882631883/vesta-equity-transacts-first-ever-on-chain-home-equity-investment'],
    },
    {
        'id': 'propy',
        'name': 'Propy',
        'chain': 'Base / Ethereum',
        'category': 'title',
        'thesis': 'Put the deed itself on-chain — title transfer and escrow, not yield slices.',
        'wrapper': 'Recorded title conveyance; NFT represents the property record',
        'min_ticket': 'Whole home',
        'occupant_equity': False,
        'equity_to': 'Whoever buys the home outright',
        'take': None,
        'status': 'live',
        'status_note': 'Solves conveyance, not affordability — you still need the whole purchase price.',
        'url': 'https://propy.com',
        'cg': 'propy',
        'live_api': None,
        'sources': ['https://eco.com/support/en/articles/15254024-tokenized-real-estate-2026-realt-lofty-propy-compared'],
    },
    {
        'id': 'roofstock',
        'name': 'Roofstock onChain',
        'chain': 'Ethereum',
        'category': 'title',
        'thesis': 'A whole rental home wrapped as an LLC and sold as a single NFT for USDC.',
        'wrapper': 'Single-member Wyoming LLC; "Home onChain" NFT = sole membership interest',
        'min_ticket': 'Whole home',
        'occupant_equity': False,
        'equity_to': 'The single NFT holder',
        'take': None,
        'status': 'quiet',
        'status_note': (
            'Sold a handful of homes as NFTs in 2022–23; we found no evidence of recent '
            'transactions. Roofstock One — a separate product — has shut down.'
        ),
        'url': 'https://onchain.roofstock.com',
        'cg': None,
        'live_api': None,
        'sources': [
            'https://tokenizelist.com/platforms/roofstock-onchain/',
            'https://nftnow.com/news/roofstock-onchain-origin-story-sell-third-property-via-nft-marketplace/',
        ],
    },
    {
        'id': 'parcl',
        'name': 'Parcl',
        'chain': 'Solana',
        'category': 'synthetic',
        'thesis': 'Trade housing-market price indices as perpetuals.',
        'wrapper': 'None — synthetic exposure to a price feed',
        'min_ticket': 'Any size',
        'occupant_equity': False,
        'equity_to': 'Nobody — there is no house',
        'take': None,
        'status': 'live',
        'status_note': 'Bets on housing prices. Does not house anyone.',
        'url': 'https://parcl.co',
        'cg': 'parcl',
        'live_api': None,
        'sources': ['https://www.rwa.io/post/the-future-of-smart-contracts-for-rental-properties'],
    },
    {
        'id': 'landshare',
        'name': 'Landshare',
        'chain': 'BNB Chain',
        'category': 'investor-fractional',
        'thesis': 'Tokenized property vault with an RWA token wrapper.',
        'wrapper': 'RWA token backed by a property portfolio',
        'min_ticket': None,
        'occupant_equity': False,
        'equity_to': 'Outside token holders',
        'take': None,
        'status': 'live',
        'status_note': None,
        'url': 'https://landshare.io',
        'cg': 'landshare',
        'live_api': None,
        'sources': ['https://www.zoniqx.com/resources/top-real-estate-tokenization-platforms-in-2025-and-2026'],
    },
    {
        'id': 'blocksquare',
        'name': 'Blocksquare',
        'chain': 'Ethereum',
        'category': 'infrastructure',
        'thesis': 'Issuance rails — split any property into 100,000 tokens and let others run the marketplace.',
        'wrapper': 'Protocol + notarized off-chain agreement per property',
        'min_ticket': 'Varies by issuer',
        'occupant_equity': False,
        'equity_to': 'Whoever the issuer sells to',
        'take': None,
        'status': 'live',
        'status_note': 'Infrastructure, not a housing model — it will happily tokenize either side.',
        'url': 'https://blocksquare.io',
        'cg': 'blocksquare',
        'live_api': None,
        'sources': ['https://blocksquare.io/products/tokenization-protocol'],
    },
    {
        'id': 'divvy',
        'name': 'Divvy Homes',
        'chain': 'Off-chain',
        'category': 'offchain-rto',
        'thesis': 'Rent-to-own at venture scale: Divvy buys the home, you rent it and build a down payment.',
        'wrapper': 'Corporate lease with a purchase option; equity credit is a company promise',
        'min_ticket': 'Deposit + lease',
        'occupant_equity': True,
        'equity_to': 'The resident — as a down-payment credit held by the company',
        'take': None,
        'status': 'acquired',
        'status_note': (
            'The direct comparable, and the cautionary tale. Once valued above $2B, sold to '
            'Maymont/Brookfield for roughly $1B in 2025 — described as a bloodbath for investors '
            'and employee equity. The residents\' credit sat on one company\'s balance sheet, '
            'so its solvency was their problem.'
        ),
        'url': 'https://divvyhomes.com',
        'cg': None,
        'live_api': None,
        'sources': [
            'https://finance.yahoo.com/news/rent-own-startup-divvy-homes-214259917.html',
            'https://www.resiclubanalytics.com/p/a-bloodbath-for-investors-and-employee-equity-rent-to-own-startup-divvy-homes-is-being-acquired-by-m',
        ],
    },
    {
        'id': 'landis',
        'name': 'Landis',
        'chain': 'Off-chain',
        'category': 'offchain-rto',
        'thesis': 'Rent-to-own paired with credit coaching toward a mortgage.',
        'wrapper': 'Lease + purchase option; savings held by the company',
        'min_ticket': 'Deposit + lease',
        'occupant_equity': True,
        'equity_to': 'The resident — as savings toward a down payment',
        'take': None,
        'status': 'live',
        'status_note': 'Same shape as OpenHouse, entirely on trust — the credit is a ledger you can\'t read.',
        'url': 'https://landis.com',
        'cg': None,
        'live_api': None,
        'sources': ['https://lendedu.com/blog/rent-to-own-companies/'],
    },
]


# ── Live rails ─────────────────────────────────────────────────────

def _get_json(url, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={
        'accept': 'application/json',
        'user-agent': 'openhouse-mod/2.0 (+https://github.com/mod-protocol)',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _realt_live():
    """RealT's community API is public and keyless — ~10 fields per token.

    Rent and yield sit behind an API key, so we only claim what we can see:
    how many property tokens exist and what they cost. Tokens prefixed OLD-
    are retired listings, which is itself worth counting right now.
    """
    rows = _get_json(REALT_API)
    prices = sorted(float(t['tokenPrice']) for t in rows if t.get('tokenPrice'))
    old = sum(1 for t in rows if str(t.get('shortName', '')).startswith('OLD-'))
    if not prices:
        return {'tokens': len(rows), 'source': REALT_API}
    return {
        'tokens': len(rows),
        'retired_tokens': old,
        'min_token_price': round(prices[0], 2),
        'median_token_price': round(prices[len(prices) // 2], 2),
        'max_token_price': round(prices[-1], 2),
        'currency': 'USD',
        'source': REALT_API,
    }


def _coingecko_live(ids):
    if not ids:
        return {}
    url = f"{COINGECKO_API}?vs_currency=usd&ids={','.join(ids)}"
    rows = _get_json(url)
    return {
        c['id']: {
            'symbol': str(c.get('symbol', '')).upper(),
            'price_usd': c.get('current_price'),
            'market_cap_usd': c.get('market_cap'),
            'change_24h_pct': c.get('price_change_percentage_24h'),
            'ath_change_pct': c.get('ath_change_percentage'),
            'source': 'https://www.coingecko.com',
        }
        for c in rows
    }


def live(cache_path=None, force=False):
    """Pull what the peers publish openly. Never raises — a dead upstream
    degrades to `errors` and whatever the last good cache held."""
    cache_path = Path(cache_path) if cache_path else None
    if cache_path and cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text())
            if time.time() - cached.get('fetched', 0) < CACHE_TTL:
                return {**cached, 'cached': True}
        except (json.JSONDecodeError, OSError):
            pass

    out = {'fetched': int(time.time()), 'cached': False, 'errors': {}}
    try:
        out['realt'] = _realt_live()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        out['errors']['realt'] = str(e)
    try:
        out['tokens'] = _coingecko_live([p['cg'] for p in PEERS if p.get('cg')])
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        out['errors']['coingecko'] = str(e)

    if cache_path:
        try:
            cache_path.write_text(json.dumps(out, indent=2))
        except OSError:
            pass
    return out


# ── The comparison ─────────────────────────────────────────────────

def peers(cache_path=None, refresh=False):
    """The landscape, with live numbers stitched onto the projects that publish any."""
    data = live(cache_path, force=refresh)
    tokens = data.get('tokens', {})
    out = []
    for p in PEERS:
        row = dict(p)
        row['category_label'] = CATEGORIES[p['category']]['label']
        if p.get('cg') and p['cg'] in tokens:
            row['token'] = tokens[p['cg']]
        if p.get('live_api') == 'realt' and 'realt' in data:
            row['live'] = data['realt']
        out.append(row)
    return {
        'peers': out,
        'categories': CATEGORIES,
        'fetched': data.get('fetched'),
        'cached': data.get('cached'),
        'errors': data.get('errors', {}),
    }


def compare(terms, cache_path=None, refresh=False):
    """OpenHouse against the field. `terms` is Mod.terms() — the live deal.

    Returns the honest version: what we do that nobody else does, and every
    place the shipped-and-audited incumbents are still ahead of us.
    """
    land = peers(cache_path, refresh)
    rows = land['peers']

    us = {
        'id': 'openhouse',
        'name': 'OpenHouse',
        'chain': 'Base',
        'category': 'occupant-equity',
        'category_label': CATEGORIES['occupant-equity']['label'],
        'thesis': 'Every rent payment is recorded as principal toward the home the payer lives in.',
        'wrapper': 'Contract-held ledger of principal; quarterly on-chain ownership checkpoint',
        'min_ticket': 'One rent payment',
        'occupant_equity': True,
        'equity_to': 'The resident — %s%% of every payment, enforced in code' % terms.get('equity_pct_of_rent', 0),
        'take': '%s%% protocol fee, hard-capped at %s%% in the contract'
                % (terms.get('fee_pct'), terms.get('fee_band', {}).get('max_pct')),
        'status': 'testnet',
        'status_note': 'Base Sepolia. No legal wrapper, no audit, no real home, no mainnet date.',
        'url': '',
        'sources': [],
    }

    occupant_side = [r for r in rows if r['occupant_equity']]
    on_chain_occupant = [r for r in occupant_side if r['chain'] != 'Off-chain']

    return {
        'openhouse': us,
        'peers': rows,
        'categories': CATEGORIES,
        # The one-line finding, computed rather than asserted.
        'headline': {
            'total': len(rows),
            'occupant_side': len(occupant_side),
            'occupant_side_onchain': len(on_chain_occupant),
            'claim': (
                '%d of %d comparable projects give the resident equity — and %s'
                % (
                    len(occupant_side), len(rows),
                    'none of those are on-chain' if not on_chain_occupant
                    else '%d are on-chain' % len(on_chain_occupant),
                )
            ),
        },
        # Where we are genuinely behind. Shipped beats designed.
        'behind': [
            'Testnet only — Base Sepolia, test ETH, no mainnet launch date.',
            'No legal wrapper. The peers put each home in an LLC; our principal ledger '
            'has no entity behind it yet, so on-chain equity is not yet a deed.',
            'No audit. RealT and Lofty carry real securities counsel; we carry a .sol file.',
            'Zero properties. Lofty and RealT tokenized hundreds of real homes.',
            'No secondary market. Peers offer liquidity; principal here is illiquid by design.',
            'Single-property contract. Multi-home portfolios are still on the roadmap.',
        ],
        # And what the field's own history says for the model.
        'evidence': [
            {
                'claim': 'Investor-side tokenization can fail the house itself.',
                'detail': 'RealT raised ~$140M, then entered voluntary liquidation on 2 July 2026 '
                          'after Detroit sued over 100+ vacant tokenized homes with unpaid taxes and '
                          'blight fines. Thousands of holders owned slices of houses nobody was '
                          'responsible for living in.',
                'source': 'https://cryptobriefing.com/realt-crypto-real-estate-collapse/',
            },
            {
                'claim': 'Off-chain rent-to-own puts the resident\'s credit on a company balance sheet.',
                'detail': 'Divvy Homes, once valued above $2B, sold for roughly $1B in 2025. Residents\' '
                          'accrued down-payment credit was only ever as good as the company holding it.',
                'source': 'https://finance.yahoo.com/news/rent-own-startup-divvy-homes-214259917.html',
            },
        ],
        'fetched': land.get('fetched'),
        'cached': land.get('cached'),
        'errors': land.get('errors', {}),
    }
