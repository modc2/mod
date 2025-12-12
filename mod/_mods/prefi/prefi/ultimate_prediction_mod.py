"""Ultimate Prediction Market Module - Super Modular with Full Features.

Integrates:
- Uniswap V3 price feeds
- USD profit calculation
- Treasury allocation (N% locked)
- Token minting on profits
- Token burning on losses
"""

from typing import Any, Dict, Optional
from datetime import datetime
from prefi.mod import BaseMod
from prefi.prediction_market import PredictionMarket, Direction
from prefi.uniswap_v3_module import PriceFeedModule


class UltimatePredictionMod(BaseMod):
    """Ultimate Modular Prediction Market.
    
    Features:
    - USD-based profit calculation via Uniswap V3
    - Configurable treasury allocation (N%)
    - Token minting for profitable trades
    - Token burning for losing trades
    - Super modular architecture
    """
    
    description = """
    Ultimate Prediction Market - Super Modular Edition.
    
    Features:
    - Calculate profits in USD value using Uniswap V3 oracles
    - Lock N% of profits into treasury for protocol use
    - Mint tokens to users on profitable trades (proportional to USD profit)
    - Burn tokens from users on losing trades (proportional to USD loss)
    - Real-time Uniswap V3 price feeds for any token pair
    - Fully configurable and extensible
    - Multi-module architecture for easy customization
    """
    
    version = "4.0.0"
    author = "DeFi Innovation Team - Ultimate Edition"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize Ultimate Prediction Market.
        
        Args:
            config: Configuration with:
                - uniswap_factory: Uniswap V3 factory address
                - uniswap_router: Uniswap V3 router address
                - uniswap_quoter: Uniswap V3 quoter address
                - treasury_address: Treasury wallet address
                - treasury_percentage: % locked to treasury (default: 10)
                - reward_pool_percentage: % to winners (default: 90)
                - usd_token: USD stablecoin address (USDC)
                - token_mint_ratio: Tokens minted per $1 profit (default: 1000)
                - token_burn_ratio: Tokens burned per $1 loss (default: 1000)
                - prediction_duration_hours: Duration in hours (default: 24)
        """
        super().__init__(config)
        
        # Uniswap V3 integration
        factory = self.config.get('uniswap_factory', '0x1F98431c8aD98523631AE4a59f267346ea31F984')
        router = self.config.get('uniswap_router', '0xE592427A0AEce92De3Edee1F18E0157C05861564')
        quoter = self.config.get('uniswap_quoter', '0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6')
        
        self.price_feed = PriceFeedModule(factory, router, quoter)
        
        # Treasury configuration - MODULAR
        self.treasury_address = self.config.get('treasury_address', '0x0000000000000000000000000000000000000000')
        self.treasury_percentage = self.config.get('treasury_percentage', 10)  # N% to treasury
        self.reward_pool_percentage = self.config.get('reward_pool_percentage', 90)
        
        # USD token for profit calculation
        self.usd_token = self.config.get('usd_token', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48')  # USDC
        
        # Token minting/burning ratios - MODULAR
        self.token_mint_ratio = self.config.get('token_mint_ratio', 1000)  # 1000 tokens per $1
        self.token_burn_ratio = self.config.get('token_burn_ratio', 1000)
        
        # State tracking
        self.user_token_balances: Dict[str, float] = {}
        self.treasury_balance: float = 0.0
        
        # Prediction market
        self.market = PredictionMarket(reward_pool_percentage=self.reward_pool_percentage / 100)
        
        # Duration - MODULAR
        self.prediction_duration_hours = self.config.get('prediction_duration_hours', 24)
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute ultimate prediction market operations.
        
        Args:
            action: Operation to perform
            **kwargs: Action-specific parameters
        
        Returns:
            Operation results with full details
        """
        action = kwargs.get('action')
        
        if action == 'deposit':
            return self.market.deposit(kwargs.get('user_address'), kwargs.get('amount'))
        
        elif action == 'open_position':
            return self._open_position_with_usd(**kwargs)
        
        elif action == 'settle_position':
            return self._settle_with_tokenomics(**kwargs)
        
        elif action == 'distribute_rewards':
            return self._distribute_with_treasury(**kwargs)
        
        elif action == 'get_balance':
            balance = self.market.get_user_balance(kwargs.get('user_address'))
            return {'success': True, 'balance': balance}
        
        elif action == 'get_token_balance':
            user = kwargs.get('user_address')
            return {
                'success': True,
                'token_balance': self.user_token_balances.get(user, 0.0),
                'user_address': user
            }
        
        elif action == 'get_treasury_balance':
            return {
                'success': True,
                'treasury_balance': self.treasury_balance,
                'treasury_address': self.treasury_address,
                'treasury_percentage': self.treasury_percentage
            }
        
        elif action == 'get_usd_price':
            token0 = kwargs.get('token0')
            token1 = kwargs.get('token1')
            fee = kwargs.get('fee', 3000)
            amount = kwargs.get('amount', 1.0)
            
            price = self.price_feed.get_pool_price(token0, token1, fee)
            usd_value = self._get_usd_value(token0, amount)
            
            return {
                'success': True,
                'price': price,
                'usd_value': usd_value,
                'token0': token0,
                'token1': token1,
                'amount': amount
            }
        
        elif action == 'get_config':
            return {
                'success': True,
                'config': {
                    'treasury_percentage': self.treasury_percentage,
                    'reward_pool_percentage': self.reward_pool_percentage,
                    'token_mint_ratio': self.token_mint_ratio,
                    'token_burn_ratio': self.token_burn_ratio,
                    'prediction_duration_hours': self.prediction_duration_hours,
                    'usd_token': self.usd_token,
                    'treasury_address': self.treasury_address
                }
            }
        
        elif action == 'update_config':
            return self._update_config(**kwargs)
        
        else:
            return {
                'success': False,
                'error': f'Unknown action: {action}',
                'available_actions': [
                    'deposit', 'open_position', 'settle_position',
                    'distribute_rewards', 'get_balance', 'get_token_balance',
                    'get_treasury_balance', 'get_usd_price', 'get_config', 'update_config'
                ]
            }
    
    def _get_usd_value(self, token: str, amount: float) -> float:
        """Calculate USD value using Uniswap V3."""
        if token == self.usd_token:
            return amount
        
        try:
            price = self.price_feed.get_pool_price(token, self.usd_token, 3000)
            return amount * price
        except:
            return 0.0
    
    def _open_position_with_usd(self, **kwargs) -> Dict[str, Any]:
        """Open position with USD tracking."""
        token0 = kwargs.get('token0')
        token1 = kwargs.get('token1')
        fee = kwargs.get('fee', 3000)
        amount = kwargs.get('amount')
        
        # Get current price from Uniswap V3
        current_price = self.price_feed.get_pool_price(token0, token1, fee)
        
        # Calculate USD value at entry
        usd_value = self._get_usd_value(token0, amount)
        
        # Open position
        result = self.market.open_position(
            user_address=kwargs.get('user_address'),
            amount=amount,
            direction=kwargs.get('direction'),
            current_price=current_price,
            asset=f"{token0}/{token1}",
            start_time=kwargs.get('start_time')
        )
        
        if result['success']:
            result['usd_value_at_entry'] = usd_value
            result['token0'] = token0
            result['token1'] = token1
            result['fee'] = fee
        
        return result
    
    def _settle_with_tokenomics(self, **kwargs) -> Dict[str, Any]:
        """Settle position with token minting/burning based on USD profit/loss."""
        position_id = kwargs.get('position_id')
        token0 = kwargs.get('token0')
        token1 = kwargs.get('token1')
        fee = kwargs.get('fee', 3000)
        
        # Get exit price from Uniswap V3
        exit_price = self.price_feed.get_pool_price(token0, token1, fee)
        
        # Settle position
        result = self.market.settle_position(
            position_id=position_id,
            exit_price=exit_price,
            current_time=kwargs.get('current_time')
        )
        
        if not result['success']:
            return result
        
        # Get position details
        positions = [p for p in self.market.positions.values() if p.position_id == position_id]
        if not positions:
            return result
        
        position = positions[0]
        user = position.user_address
        
        # Calculate USD profit/loss and mint/burn tokens
        if result['profitable']:
            # PROFITABLE TRADE - MINT TOKENS
            profit_usd = self._get_usd_value(token0, result['profit'])
            tokens_to_mint = profit_usd * self.token_mint_ratio
            
            self.user_token_balances[user] = self.user_token_balances.get(user, 0.0) + tokens_to_mint
            
            result['profit_usd'] = profit_usd
            result['tokens_minted'] = tokens_to_mint
            result['new_token_balance'] = self.user_token_balances[user]
            result['action'] = 'MINTED'
        else:
            # LOSING TRADE - BURN TOKENS
            loss_usd = self._get_usd_value(token0, abs(result['profit']))
            tokens_to_burn = loss_usd * self.token_burn_ratio
            
            current_tokens = self.user_token_balances.get(user, 0.0)
            actual_burn = min(tokens_to_burn, current_tokens)
            
            self.user_token_balances[user] = current_tokens - actual_burn
            
            result['loss_usd'] = loss_usd
            result['tokens_burned'] = actual_burn
            result['tokens_attempted_burn'] = tokens_to_burn
            result['new_token_balance'] = self.user_token_balances[user]
            result['action'] = 'BURNED'
        
        return result
    
    def _distribute_with_treasury(self, **kwargs) -> Dict[str, Any]:
        """Distribute rewards with N% locked to treasury."""
        asset = kwargs.get('asset')
        settlement_time = datetime.fromisoformat(kwargs.get('settlement_time'))
        
        # Get base distribution
        result = self.market.distribute_rewards(asset, settlement_time)
        
        if not result['success']:
            return result
        
        # Calculate treasury allocation (N% of total pool)
        total_distributed = result.get('total_distributed', 0.0)
        treasury_amount = total_distributed * (self.treasury_percentage / 100)
        winner_amount = total_distributed * (self.reward_pool_percentage / 100)
        
        if treasury_amount > 0:
            self.treasury_balance += treasury_amount
            result['treasury_allocation'] = treasury_amount
            result['treasury_percentage'] = self.treasury_percentage
            result['total_treasury_balance'] = self.treasury_balance
            result['winner_pool'] = winner_amount
        
        return result
    
    def _update_config(self, **kwargs) -> Dict[str, Any]:
        """Update modular configuration."""
        updated = []
        
        if 'treasury_percentage' in kwargs:
            val = kwargs['treasury_percentage']
            if 0 <= val <= 50:
                self.treasury_percentage = val
                self.reward_pool_percentage = 100 - val
                updated.append('treasury_percentage')
        
        if 'token_mint_ratio' in kwargs:
            self.token_mint_ratio = kwargs['token_mint_ratio']
            updated.append('token_mint_ratio')
        
        if 'token_burn_ratio' in kwargs:
            self.token_burn_ratio = kwargs['token_burn_ratio']
            updated.append('token_burn_ratio')
        
        if 'prediction_duration_hours' in kwargs:
            self.prediction_duration_hours = kwargs['prediction_duration_hours']
            updated.append('prediction_duration_hours')
        
        return {
            'success': True,
            'updated_fields': updated,
            'current_config': {
                'treasury_percentage': self.treasury_percentage,
                'reward_pool_percentage': self.reward_pool_percentage,
                'token_mint_ratio': self.token_mint_ratio,
                'token_burn_ratio': self.token_burn_ratio,
                'prediction_duration_hours': self.prediction_duration_hours
            }
        }
