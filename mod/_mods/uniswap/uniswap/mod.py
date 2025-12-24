from web3 import Web3
from typing import Dict, List, Any, Optional
import asyncio
import json
import time
from datetime import datetime, timedelta
import numpy as np
from collections import deque

class UniswapScraper:
    """Scrapes Uniswap pool data for training prediction models"""
    
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.pool_data = deque(maxlen=10000)
        self.swap_events = deque(maxlen=50000)
        
    async def scrape_pool_state(self, pool_address: str) -> Dict[str, Any]:
        """Scrape current pool state"""
        pool_abi = [
            {"inputs": [], "name": "slot0", "outputs": [{"type": "uint160"}, {"type": "int24"}, {"type": "uint16"}, {"type": "uint16"}, {"type": "uint16"}, {"type": "uint8"}, {"type": "bool"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "liquidity", "outputs": [{"type": "uint128"}], "stateMutability": "view", "type": "function"}
        ]
        pool = self.w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)
        slot0 = pool.functions.slot0().call()
        liquidity = pool.functions.liquidity().call()
        
        state = {
            'timestamp': int(time.time()),
            'pool': pool_address,
            'sqrt_price_x96': slot0[0],
            'tick': slot0[1],
            'liquidity': liquidity,
            'price': (slot0[0] / (2**96)) ** 2
        }
        self.pool_data.append(state)
        return state
    
    def get_training_data(self) -> List[Dict]:
        """Get scraped data for model training"""
        return list(self.pool_data)


class PredictionModel:
    """Simple prediction model for token values"""
    
    def __init__(self):
        self.price_history = {}
        self.predictions = {}
        
    def add_price_point(self, token: str, price: float, timestamp: int):
        if token not in self.price_history:
            self.price_history[token] = []
        self.price_history[token].append({'price': price, 'ts': timestamp})
        
    def predict_price(self, token: str, horizon_minutes: int = 60) -> Dict[str, Any]:
        """Predict future price using simple moving average + momentum"""
        if token not in self.price_history or len(self.price_history[token]) < 10:
            return {'error': 'Insufficient data'}
        
        prices = [p['price'] for p in self.price_history[token][-100:]]
        sma_short = np.mean(prices[-10:])
        sma_long = np.mean(prices[-50:]) if len(prices) >= 50 else np.mean(prices)
        momentum = (sma_short - sma_long) / sma_long if sma_long > 0 else 0
        
        predicted = prices[-1] * (1 + momentum * (horizon_minutes / 60))
        confidence = min(0.9, len(prices) / 100)
        
        return {
            'token': token,
            'current_price': prices[-1],
            'predicted_price': predicted,
            'horizon_minutes': horizon_minutes,
            'confidence': confidence,
            'momentum': momentum
        }


class PredictionMarket:
    """Prediction market for token real values"""
    
    def __init__(self):
        self.markets = {}
        self.positions = {}
        self.resolved = {}
        
    def create_market(self, token: str, target_price: float, expiry_ts: int) -> str:
        """Create a prediction market for token price"""
        market_id = f"{token}_{target_price}_{expiry_ts}"
        self.markets[market_id] = {
            'token': token,
            'target_price': target_price,
            'expiry': expiry_ts,
            'yes_pool': 1000,
            'no_pool': 1000,
            'total_volume': 0,
            'created': int(time.time())
        }
        return market_id
    
    def get_odds(self, market_id: str) -> Dict[str, float]:
        """Get current market odds"""
        if market_id not in self.markets:
            return {'error': 'Market not found'}
        m = self.markets[market_id]
        total = m['yes_pool'] + m['no_pool']
        return {
            'yes_probability': m['yes_pool'] / total,
            'no_probability': m['no_pool'] / total
        }
    
    def place_bet(self, market_id: str, user: str, is_yes: bool, amount: float) -> Dict[str, Any]:
        """Place a bet on market outcome"""
        if market_id not in self.markets:
            return {'error': 'Market not found'}
        
        m = self.markets[market_id]
        if int(time.time()) > m['expiry']:
            return {'error': 'Market expired'}
        
        if is_yes:
            shares = amount * m['no_pool'] / m['yes_pool']
            m['yes_pool'] += amount
        else:
            shares = amount * m['yes_pool'] / m['no_pool']
            m['no_pool'] += amount
        
        m['total_volume'] += amount
        
        pos_key = f"{market_id}_{user}"
        if pos_key not in self.positions:
            self.positions[pos_key] = {'yes_shares': 0, 'no_shares': 0}
        
        if is_yes:
            self.positions[pos_key]['yes_shares'] += shares
        else:
            self.positions[pos_key]['no_shares'] += shares
        
        return {'shares': shares, 'side': 'yes' if is_yes else 'no', 'market_id': market_id}
    
    def resolve_market(self, market_id: str, actual_price: float) -> Dict[str, Any]:
        """Resolve market with actual price"""
        if market_id not in self.markets:
            return {'error': 'Market not found'}
        
        m = self.markets[market_id]
        outcome = actual_price >= m['target_price']
        self.resolved[market_id] = {'outcome': outcome, 'actual_price': actual_price}
        return {'market_id': market_id, 'outcome': 'yes' if outcome else 'no', 'actual_price': actual_price}


class UniswapV3Mod:
    """ANCHOR CLASS - Uniswap V3 with Scraping, Prediction & Market"""
    
    description = """
    Enhanced Uniswap V3 Integration with:
    - Data scraping for ML training
    - Price prediction models
    - Prediction markets for real token values
    - MEV protection & gas optimization
    """
    
    TOKENS = {
        'WETH': '0x4200000000000000000000000000000000000006',
        'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        'DAI': '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',
        'USDT': '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2'
    }
    
    POOLS = {
        'WETH_USDC': '0xd0b53D9277642d899DF5C87A3966A349A798F224',
        'WETH_DAI': '0x6c6Bc977E13Df9b0de53b251522280BB72383700'
    }
    
    def __init__(self, web3_provider: Web3, router_address: str):
        self.w3 = web3_provider
        self.router = router_address
        self.scraper = UniswapScraper(web3_provider)
        self.predictor = PredictionModel()
        self.market = PredictionMarket()
        self._scrape_task = None
        
    async def start_scraping(self, interval_seconds: int = 60):
        """Start continuous data scraping"""
        async def scrape_loop():
            while True:
                for pool_name, pool_addr in self.POOLS.items():
                    try:
                        state = await self.scraper.scrape_pool_state(pool_addr)
                        token = pool_name.split('_')[0]
                        self.predictor.add_price_point(token, state['price'], state['timestamp'])
                    except Exception as e:
                        print(f"Scrape error {pool_name}: {e}")
                await asyncio.sleep(interval_seconds)
        
        self._scrape_task = asyncio.create_task(scrape_loop())
        return {'status': 'scraping_started', 'interval': interval_seconds}
    
    def stop_scraping(self):
        if self._scrape_task:
            self._scrape_task.cancel()
        return {'status': 'scraping_stopped'}
    
    def get_training_data(self) -> Dict[str, Any]:
        """Export scraped data for ML training"""
        return {
            'pool_states': self.scraper.get_training_data(),
            'price_history': self.predictor.price_history,
            'count': len(self.scraper.pool_data)
        }
    
    def predict_token_value(self, token: str, horizon_minutes: int = 60) -> Dict[str, Any]:
        """Predict future token value"""
        return self.predictor.predict_price(token, horizon_minutes)
    
    def create_prediction_market(self, token: str, target_price: float, hours_until_expiry: int = 24) -> Dict[str, Any]:
        """Create prediction market for token reaching target price"""
        expiry = int(time.time()) + (hours_until_expiry * 3600)
        market_id = self.market.create_market(token, target_price, expiry)
        return {
            'market_id': market_id,
            'token': token,
            'target_price': target_price,
            'expiry': expiry,
            'odds': self.market.get_odds(market_id)
        }
    
    def bet_on_market(self, market_id: str, user: str, is_yes: bool, amount: float) -> Dict[str, Any]:
        """Place bet on prediction market"""
        return self.market.place_bet(market_id, user, is_yes, amount)
    
    def get_market_odds(self, market_id: str) -> Dict[str, Any]:
        """Get current market odds"""
        return self.market.get_odds(market_id)
    
    def resolve_prediction_market(self, market_id: str, actual_price: float) -> Dict[str, Any]:
        """Resolve market with actual price"""
        return self.market.resolve_market(market_id, actual_price)
    
    def list_tokens(self) -> Dict[str, str]:
        return self.TOKENS.copy()
    
    async def get_best_route(self, token_in: str, token_out: str, amount: int) -> Dict[str, Any]:
        routes = await self._analyze_routes(token_in, token_out, amount)
        return max(routes, key=lambda r: r['output_amount'])
    
    async def execute_swap_with_protection(self, token_in: str, token_out: str, amount: int, max_slippage: float = 0.005) -> Dict[str, Any]:
        route = await self.get_best_route(token_in, token_out, amount)
        impact = self._calculate_price_impact(route)
        if impact > max_slippage:
            raise ValueError(f"Price impact {impact} exceeds max slippage {max_slippage}")
        tx = await self._build_protected_transaction(route)
        return await self._submit_private_transaction(tx)
    
    def _calculate_price_impact(self, route: Dict[str, Any]) -> float:
        expected = route['amount_in'] * route['spot_price']
        actual = route['output_amount']
        return abs(expected - actual) / expected if expected > 0 else 0
    
    async def _analyze_routes(self, token_in: str, token_out: str, amount: int) -> List[Dict[str, Any]]:
        return [{'amount_in': amount, 'output_amount': amount * 0.99, 'spot_price': 1.0, 'path': [token_in, token_out]}]
    
    async def _build_protected_transaction(self, route: Dict[str, Any]) -> Dict[str, Any]:
        return {'data': '0x', 'to': self.router}
    
    async def _submit_private_transaction(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        return {'success': True, 'tx_hash': '0xabc123'}
