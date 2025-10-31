from typing import Optional, Dict, Any, List
from datetime import datetime
import requests
from ..exchange import BaseExchange


class Uniswap(BaseExchange):
    """Uniswap DEX implementation (Ethereum/EVM)"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_url = self.config.get('api_url', 'https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3')
        self.rpc_url = self.config.get('rpc_url', 'https://eth-mainnet.g.alchemy.com/v2/')
        self.router_address = self.config.get('router_address', '0xE592427A0AEce92De3Edee1F18E0157C05861564')
        self.private_key = self.config.get('private_key')
        self.version = self.config.get('version', 'v3')
        
    def swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: float,
        slippage: float = 0.01,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a swap on Uniswap
        """
        try:
            # Get quote
            price_data = self.get_price(token_in, token_out, amount_in)
            expected_out = price_data['amount_out']
            min_out = expected_out * (1 - slippage)
            
            # Build swap parameters
            swap_params = {
                'tokenIn': token_in,
                'tokenOut': token_out,
                'fee': kwargs.get('fee', 3000),  # 0.3% default
                'recipient': kwargs.get('recipient', self.config.get('wallet_address')),
                'deadline': kwargs.get('deadline', int(datetime.now().timestamp()) + 300),
                'amountIn': int(amount_in * 1e18),  # Convert to wei
                'amountOutMinimum': int(min_out * 1e18),
                'sqrtPriceLimitX96': kwargs.get('sqrtPriceLimitX96', 0)
            }
            
            # Execute swap
            result = self._execute_swap(swap_params)
            
            return {
                'success': result.get('success', False),
                'amount_out': result.get('amount_out', expected_out),
                'tx_hash': result.get('tx_hash', ''),
                'price': price_data['price'],
                'gas_used': result.get('gas_used', 0)
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'amount_out': 0,
                'tx_hash': None,
                'price': 0
            }
    
    def get_price(
        self,
        token_in: str,
        token_out: str,
        amount_in: float = 1.0,
        timestamp: Optional[datetime] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get price from Uniswap
        """
        try:
            if timestamp:
                # Historical price
                return self._get_historical_price(token_in, token_out, amount_in, timestamp)
            else:
                # Current price via GraphQL
                query = '''
                {
                  pool(id: "%s") {
                    token0Price
                    token1Price
                    liquidity
                    volumeUSD
                    token0 {
                      id
                      symbol
                    }
                    token1 {
                      id
                      symbol
                    }
                  }
                }
                '''
                
                pool_id = kwargs.get('pool_id') or self._find_pool(token_in, token_out)
                
                response = requests.post(
                    self.api_url,
                    json={'query': query % pool_id}
                )
                data = response.json()
                
                if 'data' in data and data['data']['pool']:
                    pool = data['data']['pool']
                    
                    # Determine price direction
                    if pool['token0']['id'].lower() == token_in.lower():
                        price = float(pool['token0Price'])
                    else:
                        price = float(pool['token1Price'])
                    
                    amount_out = amount_in * price
                    
                    return {
                        'price': price,
                        'amount_out': amount_out,
                        'timestamp': datetime.now(),
                        'liquidity': float(pool['liquidity']),
                        'volume_24h': float(pool.get('volumeUSD', 0))
                    }
                else:
                    raise Exception('Pool not found')
        except Exception as e:
            return {
                'price': 0,
                'amount_out': 0,
                'timestamp': timestamp or datetime.now(),
                'error': str(e)
            }
    
    def get_historical_prices(
        self,
        token_in: str,
        token_out: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = '1h',
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get historical prices from Uniswap via The Graph
        """
        try:
            pool_id = kwargs.get('pool_id') or self._find_pool(token_in, token_out)
            
            query = '''
            {
              poolHourDatas(
                where: {
                  pool: "%s",
                  periodStartUnix_gte: %d,
                  periodStartUnix_lte: %d
                },
                orderBy: periodStartUnix,
                orderDirection: asc
              ) {
                periodStartUnix
                token0Price
                token1Price
                volumeUSD
                liquidity
                high
                low
              }
            }
            ''' % (pool_id, int(start_time.timestamp()), int(end_time.timestamp()))
            
            response = requests.post(
                self.api_url,
                json={'query': query}
            )
            data = response.json()
            
            if 'data' in data and 'poolHourDatas' in data['data']:
                return [
                    {
                        'price': float(item['token0Price']),
                        'amount_out': float(item['token0Price']),
                        'timestamp': datetime.fromtimestamp(item['periodStartUnix']),
                        'volume': float(item.get('volumeUSD', 0)),
                        'liquidity': float(item.get('liquidity', 0)),
                        'high': float(item.get('high', item['token0Price'])),
                        'low': float(item.get('low', item['token0Price']))
                    }
                    for item in data['data']['poolHourDatas']
                ]
            return []
        except Exception as e:
            return []
    
    def _execute_swap(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute swap transaction on-chain"""
        # Implement actual Web3 transaction signing and sending
        # This is a placeholder
        return {
            'success': True,
            'amount_out': 0,
            'tx_hash': '0x...',
            'gas_used': 150000
        }
    
    def _find_pool(self, token_in: str, token_out: str, fee: int = 3000) -> str:
        """Find pool address for token pair"""
        query = '''
        {
          pools(
            where: {
              token0: "%s",
              token1: "%s",
              feeTier: %d
            }
          ) {
            id
          }
        }
        ''' % (token_in.lower(), token_out.lower(), fee)
        
        response = requests.post(self.api_url, json={'query': query})
        data = response.json()
        
        if 'data' in data and data['data']['pools']:
            return data['data']['pools'][0]['id']
        return ''
    
    def _get_historical_price(self, token_in: str, token_out: str, amount_in: float, timestamp: datetime) -> Dict[str, Any]:
        """Get historical price at specific timestamp"""
        prices = self.get_historical_prices(token_in, token_out, timestamp, timestamp, '1h')
        if prices:
            price_data = prices[0]
            return {
                'price': price_data['price'],
                'amount_out': amount_in * price_data['price'],
                'timestamp': timestamp,
                'liquidity': price_data.get('liquidity'),
                'volume_24h': price_data.get('volume')
            }
        return {'price': 0, 'amount_out': 0, 'timestamp': timestamp}
