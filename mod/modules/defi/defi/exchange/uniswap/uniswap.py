from typing import Optional, Dict, Any, List
from datetime import datetime
from web3 import Web3
from eth_abi import decode
import time

from ..exchange import BaseExchange


class Uniswap(BaseExchange):
    """Uniswap DEX implementation for Base chain with full on-chain scraping"""
    
    # Base chain Uniswap V3 contracts
    FACTORY_ADDRESS = '0x33128a8fC17869897dcE68Ed026d694621f6FDfD'
    QUOTER_ADDRESS = '0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a'
    
    # Event signatures
    SWAP_EVENT_SIGNATURE = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
    MINT_EVENT_SIGNATURE = '0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde'
    BURN_EVENT_SIGNATURE = '0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c'
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # Base chain RPC - using public endpoint
        self.rpc_url = self.config.get('rpc_url', 'https://mainnet.base.org')
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.factory_address = self.config.get('factory_address', self.FACTORY_ADDRESS)
        self.quoter_address = self.config.get('quoter_address', self.QUOTER_ADDRESS)
        
    def scrape_history(
        self,
        token_in: str,
        token_out: str,
        start_block: int,
        end_block: int,
        block_interval: int = 100,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Scrape Uniswap history on Base chain between any blocks using pure on-chain data
        
        Args:
            token_in: Input token address
            token_out: Output token address
            start_block: Starting block number
            end_block: Ending block number
            block_interval: Number of blocks to query at once (avoid rate limits)
        
        Returns:
            List of historical swap events with prices and volumes
        """
        try:
            pool_address = self._get_pool_address(token_in, token_out)
            if not pool_address:
                return []
            
            all_events = []
            current_block = start_block
            
            while current_block <= end_block:
                to_block = min(current_block + block_interval, end_block)
                
                # Get swap events
                swap_events = self._get_swap_events(pool_address, current_block, to_block)
                
                # Parse and process events
                for event in swap_events:
                    parsed_event = self._parse_swap_event(event, token_in, token_out)
                    if parsed_event:
                        all_events.append(parsed_event)
                
                current_block = to_block + 1
                time.sleep(0.1)  # Rate limiting
            
            return all_events
            
        except Exception as e:
            return [{'error': str(e)}]
    
    def get_price_at_block(
        self,
        token_in: str,
        token_out: str,
        block_number: int,
        amount_in: float = 1.0,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get price at specific block using on-chain slot0 data
        """
        try:
            pool_address = self._get_pool_address(token_in, token_out)
            if not pool_address:
                return {'price': 0, 'error': 'Pool not found'}
            
            # Get slot0 data (sqrtPriceX96, tick, etc)
            slot0_data = self._get_slot0(pool_address, block_number)
            
            if slot0_data:
                sqrt_price_x96 = slot0_data['sqrtPriceX96']
                price = self._sqrt_price_to_price(sqrt_price_x96)
                
                return {
                    'price': price,
                    'amount_out': amount_in * price,
                    'block_number': block_number,
                    'tick': slot0_data.get('tick'),
                    'liquidity': slot0_data.get('liquidity', 0)
                }
            
            return {'price': 0, 'error': 'Could not fetch slot0'}
            
        except Exception as e:
            return {'price': 0, 'error': str(e)}
    
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
        Get historical prices by scraping blocks at time intervals
        """
        try:
            # Convert timestamps to block numbers
            start_block = self._timestamp_to_block(int(start_time.timestamp()))
            end_block = self._timestamp_to_block(int(end_time.timestamp()))
            
            # Calculate block interval based on time interval
            interval_seconds = self._parse_interval(interval)
            blocks_per_interval = interval_seconds // 2  # Base has ~2 second blocks
            
            prices = []
            current_block = start_block
            
            while current_block <= end_block:
                price_data = self.get_price_at_block(token_in, token_out, current_block)
                
                if price_data.get('price', 0) > 0:
                    block_data = self.w3.eth.get_block(current_block)
                    price_data['timestamp'] = datetime.fromtimestamp(block_data['timestamp'])
                    prices.append(price_data)
                
                current_block += blocks_per_interval
                time.sleep(0.1)
            
            return prices
            
        except Exception as e:
            return [{'error': str(e)}]
    
    def _get_pool_address(self, token0: str, token1: str, fee: int = 3000) -> Optional[str]:
        """
        Compute pool address using CREATE2
        """
        try:
            # Sort tokens
            if int(token0, 16) > int(token1, 16):
                token0, token1 = token1, token0
            
            # Encode pool key
            pool_key = Web3.solidity_keccak(
                ['address', 'address', 'uint24'],
                [Web3.to_checksum_address(token0), Web3.to_checksum_address(token1), fee]
            )
            
            # Compute CREATE2 address
            init_code_hash = '0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54'
            
            pool_address = Web3.solidity_keccak(
                ['bytes1', 'address', 'bytes32', 'bytes32'],
                ['0xff', self.factory_address, pool_key, init_code_hash]
            )
            
            return Web3.to_checksum_address('0x' + pool_address.hex()[-40:])
            
        except Exception as e:
            return None
    
    def _get_swap_events(self, pool_address: str, from_block: int, to_block: int) -> List[Dict]:
        """
        Get swap events from pool using eth_getLogs
        """
        try:
            filter_params = {
                'fromBlock': hex(from_block),
                'toBlock': hex(to_block),
                'address': pool_address,
                'topics': [self.SWAP_EVENT_SIGNATURE]
            }
            
            logs = self.w3.eth.get_logs(filter_params)
            return logs
            
        except Exception as e:
            return []
    
    def _parse_swap_event(self, event: Dict, token_in: str, token_out: str) -> Optional[Dict[str, Any]]:
        """
        Parse swap event log
        """
        try:
            # Decode swap event data
            # event Swap(address indexed sender, address indexed recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
            
            data = event['data']
            topics = event['topics']
            
            # Decode indexed parameters
            sender = '0x' + topics[1].hex()[-40:]
            recipient = '0x' + topics[2].hex()[-40:]
            
            # Decode non-indexed parameters
            decoded = decode(
                ['int256', 'int256', 'uint160', 'uint128', 'int24'],
                bytes.fromhex(data[2:])
            )
            
            amount0 = decoded[0]
            amount1 = decoded[1]
            sqrt_price_x96 = decoded[2]
            liquidity = decoded[3]
            tick = decoded[4]
            
            price = self._sqrt_price_to_price(sqrt_price_x96)
            
            block_data = self.w3.eth.get_block(event['blockNumber'])
            
            return {
                'block_number': event['blockNumber'],
                'transaction_hash': event['transactionHash'].hex(),
                'timestamp': datetime.fromtimestamp(block_data['timestamp']),
                'sender': sender,
                'recipient': recipient,
                'amount0': amount0 / 1e18,
                'amount1': amount1 / 1e18,
                'price': price,
                'liquidity': liquidity,
                'tick': tick
            }
            
        except Exception as e:
            return None
    
    def _get_slot0(self, pool_address: str, block_number: int) -> Optional[Dict[str, Any]]:
        """
        Get slot0 data from pool at specific block
        """
        try:
            # slot0() function signature
            function_signature = '0x3850c7bd'
            
            result = self.w3.eth.call(
                {'to': pool_address, 'data': function_signature},
                block_number
            )
            
            # Decode slot0 return values
            decoded = decode(
                ['uint160', 'int24', 'uint16', 'uint16', 'uint16', 'uint8', 'bool'],
                result
            )
            
            return {
                'sqrtPriceX96': decoded[0],
                'tick': decoded[1],
                'observationIndex': decoded[2],
                'observationCardinality': decoded[3],
                'observationCardinalityNext': decoded[4],
                'feeProtocol': decoded[5],
                'unlocked': decoded[6]
            }
            
        except Exception as e:
            return None
    
    def _sqrt_price_to_price(self, sqrt_price_x96: int) -> float:
        """
        Convert sqrtPriceX96 to human readable price
        """
        try:
            price = (sqrt_price_x96 / (2 ** 96)) ** 2
            return price
        except:
            return 0.0
    
    def _timestamp_to_block(self, timestamp: int) -> int:
        """
        Estimate block number from timestamp (Base has ~2 second blocks)
        """
        try:
            latest_block = self.w3.eth.get_block('latest')
            latest_timestamp = latest_block['timestamp']
            latest_block_number = latest_block['number']
            
            time_diff = latest_timestamp - timestamp
            block_diff = time_diff // 2  # 2 second blocks on Base
            
            estimated_block = max(0, latest_block_number - block_diff)
            return int(estimated_block)
            
        except Exception as e:
            return 0
    
    def _parse_interval(self, interval: str) -> int:
        """
        Parse interval string to seconds
        """
        unit = interval[-1]
        value = int(interval[:-1])
        
        if unit == 's':
            return value
        elif unit == 'm':
            return value * 60
        elif unit == 'h':
            return value * 3600
        elif unit == 'd':
            return value * 86400
        else:
            return 3600  # default 1 hour
    
    def swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: float,
        slippage: float = 0.01,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a swap on Uniswap (requires private key configuration)
        """
        return {
            'success': False,
            'error': 'Swap execution requires private key configuration',
            'amount_out': 0
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
        Get current or historical price
        """
        if timestamp:
            block_number = self._timestamp_to_block(int(timestamp.timestamp()))
            return self.get_price_at_block(token_in, token_out, block_number, amount_in)
        else:
            latest_block = self.w3.eth.block_number
            return self.get_price_at_block(token_in, token_out, latest_block, amount_in)
