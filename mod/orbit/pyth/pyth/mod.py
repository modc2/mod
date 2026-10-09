"""Pyth Network Price Feed Integration Module"""

import requests
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PriceFeed:
    """Pyth price feed data structure"""
    id: str
    symbol: str
    asset_type: str
    description: str
    base: str
    quote: str


class BaseMod:
    """Pyth Network Price Feed Module - Multi-chain support"""

    description = 'Pyth Network Price Feed Integration with multi-chain support'

    PYTH_CONTRACTS = {
        'base': '0x8250f4aF4B972684F7b336503E2D6dFeDeB1487a',
        'ethereum': '0x4305FB66699C3B2702D4d05CF36551390A4c69C6',
        'arbitrum': '0xff1a0f4744e8582DF1aE09D5611b887B6a12925C',
        'optimism': '0xff1a0f4744e8582DF1aE09D5611b887B6a12925C',
        'polygon': '0xff1a0f4744e8582DF1aE09D5611b887B6a12925C',
        'avalanche': '0x4305FB66699C3B2702D4d05CF36551390A4c69C6',
        'bsc': '0x4D7E825f80bDf85e913E0DD2A2D54927e9dE1594',
    }

    PYTH_API_BASE = 'https://hermes.pyth.network'
    PYTH_BENCHMARKS_API = 'https://benchmarks.pyth.network/v1/shims/tradingview'

    def __init__(self, chain: str = 'base'):
        self.chain = chain.lower()
        self.pyth_contract = self.PYTH_CONTRACTS.get(self.chain)
        if not self.pyth_contract:
            raise ValueError(
                f'Unsupported chain: {chain}. Supported: {list(self.PYTH_CONTRACTS.keys())}'
            )

    def get_all_price_feeds(self, query: Optional[str] = None, asset_type: Optional[str] = None) -> List[PriceFeed]:
        try:
            url = f'{self.PYTH_API_BASE}/v2/price_feeds'
            params = {}
            if query:
                params['query'] = query
            if asset_type:
                params['asset_type'] = asset_type
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            feeds = []
            for feed in data:
                feeds.append(PriceFeed(
                    id=feed.get('id', ''),
                    symbol=feed.get('attributes', {}).get('symbol', ''),
                    asset_type=feed.get('attributes', {}).get('asset_type', ''),
                    description=feed.get('attributes', {}).get('description', ''),
                    base=feed.get('attributes', {}).get('base', ''),
                    quote=feed.get('attributes', {}).get('quote', ''),
                ))
            return feeds
        except Exception as e:
            return [{'error': str(e)}]

    def get_price_feeds_by_type(self, asset_type: str = 'crypto') -> List[PriceFeed]:
        return self.get_all_price_feeds(asset_type=asset_type)

    def get_latest_price(self, price_feed_id: str) -> Dict:
        try:
            url = f'{self.PYTH_API_BASE}/v2/updates/price/latest'
            params = {'ids[]': price_feed_id}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    @staticmethod
    def decode_price(raw: dict) -> dict:
        try:
            entry = raw['parsed'][0]
            p = entry['price']
            factor = 10 ** p['expo']
            return {
                'price': int(p['price']) * factor,
                'conf': int(p['conf']) * factor,
                'publish_time': p['publish_time'],
                'id': entry['id'],
            }
        except (KeyError, IndexError, TypeError, ValueError) as e:
            return {'error': f'decode_price failed: {e}', 'raw': raw}

    def get_price_by_symbol(self, symbol: str) -> Optional[Dict]:
        feeds = self.get_all_price_feeds(query=symbol)
        for feed in feeds:
            if isinstance(feed, PriceFeed) and feed.symbol.upper() == symbol.upper():
                raw = self.get_latest_price(feed.id)
                if 'error' in raw:
                    return raw
                return self.decode_price(raw)
        return {'error': f'Symbol {symbol} not found'}

    def list_crypto_feeds(self) -> List[Dict[str, str]]:
        crypto_feeds = self.get_price_feeds_by_type('crypto')
        return [
            {'id': feed.id, 'symbol': feed.symbol, 'description': feed.description,
             'base': feed.base, 'quote': feed.quote}
            for feed in crypto_feeds
            if isinstance(feed, PriceFeed)
        ]

    def list_equity_feeds(self) -> List[Dict[str, str]]:
        equity_feeds = self.get_price_feeds_by_type('equity')
        return [
            {'id': feed.id, 'symbol': feed.symbol, 'description': feed.description,
             'base': feed.base, 'quote': feed.quote}
            for feed in equity_feeds
            if isinstance(feed, PriceFeed)
        ]

    def list_fx_feeds(self) -> List[Dict[str, str]]:
        fx_feeds = self.get_price_feeds_by_type('fx')
        return [
            {'id': feed.id, 'symbol': feed.symbol, 'description': feed.description,
             'base': feed.base, 'quote': feed.quote}
            for feed in fx_feeds
            if isinstance(feed, PriceFeed)
        ]

    def get_supported_chains(self) -> List[str]:
        return list(self.PYTH_CONTRACTS.keys())

    def get_chain_contract(self, chain=None):
        target_chain = chain.lower() if chain else self.chain
        return self.PYTH_CONTRACTS.get(target_chain, 'Chain not supported')

    def switch_chain(self, chain):
        chain = chain.lower()
        if chain not in self.PYTH_CONTRACTS:
            raise ValueError(
                f'Unsupported chain: {chain}. Supported: {list(self.PYTH_CONTRACTS.keys())}'
            )
        self.chain = chain
        self.pyth_contract = self.PYTH_CONTRACTS[chain]
        return f'Switched to {chain} - Contract: {self.pyth_contract}'

    def get_feed_info(self) -> Dict:
        return {
            'chain': self.chain,
            'pyth_contract': self.pyth_contract,
            'supported_chains': self.get_supported_chains(),
            'api_base': self.PYTH_API_BASE,
            'total_feeds': len(self.get_all_price_feeds()),
        }

    def forward(self, **kwargs):
        if 'symbol' in kwargs:
            return self.get_price_by_symbol(kwargs['symbol'])
        if 'feed_id' in kwargs:
            raw = self.get_latest_price(kwargs['feed_id'])
            return self.decode_price(raw)
        if 'chain' in kwargs:
            return self.switch_chain(kwargs['chain'])
        return self.get_feed_info()


Mod = BaseMod
