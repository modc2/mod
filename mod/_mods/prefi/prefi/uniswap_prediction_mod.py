"""Uniswap V3 Prediction Market Modification.

Integrates Uniswap V3 price feeds with the prediction market in a modular way.
"""

from typing import Any, Dict, Optional
from datetime import datetime
from prefi.mod import BaseMod
from prefi.prediction_market import PredictionMarket, Direction
from prefi.uniswap_v3_module import PriceFeedModule, LiquidityModule, SwapModule


class UniswapV3PredictionMod(BaseMod):
    """Modular Uniswap V3 Prediction Market.
    
    Combines prediction market functionality with Uniswap V3 integration
    for real-time price feeds and liquidity management.
    """
    
    description = """
    Uniswap V3 Integrated Prediction Market.
    
    Features:
    - Real-time price feeds from Uniswap V3 pools
    - Modular architecture for easy customization
    - Support for any token pair with Uniswap V3 liquidity
    - Configurable prediction duration
    - Automatic settlement using on-chain prices
    - Proportional reward distribution
    
    Modules:
    - PriceFeedModule: Fetch prices from Uniswap V3
    - LiquidityModule: Manage liquidity positions
    - SwapModule: Execute token swaps
    """
    
    version = "2.0.0"
    author = "DeFi Innovation Team"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize Uniswap V3 prediction market.
        
        Args:
            config: Configuration with:
                - uniswap_factory: Factory contract address
                - uniswap_router: Router contract address
                - uniswap_quoter: Quoter contract address
                - reward_pool_percentage: Reward distribution percentage
                - prediction_duration_hours: Duration in hours (default: 24)
                - enable_liquidity_module: Enable liquidity features
                - enable_swap_module: Enable swap features
        """
        super().__init__(config)
        
        # Initialize Uniswap V3 modules
        factory = self.config.get('uniswap_factory', '0x1F98431c8aD98523631AE4a59f267346ea31F984')
        router = self.config.get('uniswap_router', '0xE592427A0AEce92De3Edee1F18E0157C05861564')
        quoter = self.config.get('uniswap_quoter', '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6')
        
        self.price_feed = PriceFeedModule(factory, router, quoter)
        
        if self.config.get('enable_liquidity_module', False):
            position_manager = self.config.get('position_manager', '0xC36442b4a4522E871399CD717aBDD847Ab11FE88')
            self.liquidity_module = LiquidityModule(factory, router, quoter, position_manager)
        else:
            self.liquidity_module = None
        
        if self.config.get('enable_swap_module', False):
            self.swap_module = SwapModule(factory, router, quoter)
        else:
            self.swap_module = None
        
        # Initialize prediction market
        reward_percentage = self.config.get('reward_pool_percentage', 0.95)
        self.market = PredictionMarket(reward_pool_percentage=reward_percentage)
        
        # Configurable prediction duration
        self.prediction_duration_hours = self.config.get('prediction_duration_hours', 24)
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute modular prediction market operations.
        
        Args:
            action: Operation to perform
            module: Optional module to use (price_feed, liquidity, swap)
            **kwargs: Action-specific parameters
        
        Returns:
            Operation results
        """
        action = kwargs.get('action')
        module = kwargs.get('module', 'market')
        
        # Route to appropriate module
        if module == 'price_feed':
            return self._execute_price_feed(action, **kwargs)
        elif module == 'liquidity' and self.liquidity_module:
            return self._execute_liquidity(action, **kwargs)
        elif module == 'swap' and self.swap_module:
            return self._execute_swap(action, **kwargs)
        else:
            return self._execute_market(action, **kwargs)
    
    def _execute_price_feed(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute price feed operations."""
        if action == 'get_price':
            token0 = kwargs.get('token0')
            token1 = kwargs.get('token1')
            fee = kwargs.get('fee', 3000)
            
            price = self.price_feed.get_pool_price(token0, token1, fee)
            return {
                'success': True,
                'price': price,
                'token0': token0,
                'token1': token1,
                'fee': fee
            }
        elif action == 'get_pool':
            token0 = kwargs.get('token0')
            token1 = kwargs.get('token1')
            fee = kwargs.get('fee', 3000)
            
            pool = self.price_feed.get_pool_address(token0, token1, fee)
            return {
                'success': True,
                'pool_address': pool,
                'token0': token0,
                'token1': token1
            }
        else:
            return {'success': False, 'error': f'Unknown price feed action: {action}'}
    
    def _execute_liquidity(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute liquidity operations."""
        if action == 'add_liquidity':
            return self.liquidity_module.add_liquidity(
                kwargs.get('token0'),
                kwargs.get('token1'),
                kwargs.get('amount0'),
                kwargs.get('amount1'),
                kwargs.get('fee', 3000),
                kwargs.get('tick_lower'),
                kwargs.get('tick_upper')
            )
        elif action == 'remove_liquidity':
            return self.liquidity_module.remove_liquidity(
                kwargs.get('position_id'),
                kwargs.get('liquidity')
            )
        else:
            return {'success': False, 'error': f'Unknown liquidity action: {action}'}
    
    def _execute_swap(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute swap operations."""
        if action == 'swap':
            return self.swap_module.execute_swap(
                kwargs.get('token_in'),
                kwargs.get('token_out'),
                kwargs.get('amount_in'),
                kwargs.get('fee', 3000),
                kwargs.get('slippage', 0.01)
            )
        elif action == 'quote':
            amount_out = self.swap_module.quote_swap(
                kwargs.get('token_in'),
                kwargs.get('token_out'),
                kwargs.get('amount_in'),
                kwargs.get('fee', 3000)
            )
            return {'success': True, 'amount_out': amount_out}
        else:
            return {'success': False, 'error': f'Unknown swap action: {action}'}
    
    def _execute_market(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute prediction market operations."""
        if action == 'deposit':
            return self.market.deposit(kwargs.get('user_address'), kwargs.get('amount'))
        
        elif action == 'open_position':
            # Get current price from Uniswap V3
            token0 = kwargs.get('token0')
            token1 = kwargs.get('token1')
            fee = kwargs.get('fee', 3000)
            
            current_price = self.price_feed.get_pool_price(token0, token1, fee)
            
            return self.market.open_position(
                user_address=kwargs.get('user_address'),
                amount=kwargs.get('amount'),
                direction=kwargs.get('direction'),
                current_price=current_price,
                asset=f"{token0}/{token1}",
                start_time=kwargs.get('start_time')
            )
        
        elif action == 'settle_position':
            # Get exit price from Uniswap V3
            position_id = kwargs.get('position_id')
            
            # In production, fetch token pair from position
            token0 = kwargs.get('token0')
            token1 = kwargs.get('token1')
            fee = kwargs.get('fee', 3000)
            
            exit_price = self.price_feed.get_pool_price(token0, token1, fee)
            
            return self.market.settle_position(
                position_id=position_id,
                exit_price=exit_price,
                current_time=kwargs.get('current_time')
            )
        
        elif action == 'distribute_rewards':
            return self.market.distribute_rewards(
                asset=kwargs.get('asset'),
                settlement_time=datetime.fromisoformat(kwargs.get('settlement_time'))
            )
        
        elif action == 'get_balance':
            balance = self.market.get_user_balance(kwargs.get('user_address'))
            return {'success': True, 'balance': balance}
        
        elif action == 'get_positions':
            positions = self.market.get_active_positions(kwargs.get('user_address'))
            return {'success': True, 'positions': positions, 'count': len(positions)}
        
        else:
            return {
                'success': False,
                'error': f'Unknown action: {action}',
                'available_actions': [
                    'deposit', 'open_position', 'settle_position',
                    'distribute_rewards', 'get_balance', 'get_positions'
                ],
                'available_modules': ['market', 'price_feed', 'liquidity', 'swap']
            }
