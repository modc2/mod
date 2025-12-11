"""Prediction Market Modification Module.

Integrates the prediction market smart contract with the BaseMod framework.
"""

from typing import Any, Dict
from prefi.mod import BaseMod
from prefi.prediction_market import PredictionMarket, Direction
from datetime import datetime


class PredictionMarketMod(BaseMod):
    """Prediction Market Smart Contract Modification.
    
    This modification implements a 24-hour prediction market where users
    can lock liquidity to predict asset price movements and earn rewards
    for profitable trades.
    """
    
    description = """
    24-Hour Prediction Market Smart Contract.
    
    Users lock liquidity to predict whether an asset will go up or down
    in the next 24 hours. The smart contract automatically settles positions
    after exactly one day and distributes rewards to profitable traders.
    
    Features:
    - Lock liquidity for 24-hour predictions
    - Support for any asset and future dates
    - Automatic settlement after 24 hours
    - Proportional reward distribution to winners
    - Multi-user support with individual balances
    """
    
    version = "1.0.0"
    author = "DeFi Team"
    
    def __init__(self, config: Dict[str, Any] = None) -> None:
        """Initialize the prediction market modification.
        
        Args:
            config: Configuration dictionary with optional:
                - reward_pool_percentage: Percentage of losing pool to distribute (default: 0.95)
        """
        super().__init__(config)
        reward_percentage = self.config.get('reward_pool_percentage', 0.95)
        self.market = PredictionMarket(reward_pool_percentage=reward_percentage)
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute prediction market operations.
        
        Args:
            action: Operation to perform (deposit, open_position, settle_position, 
                   distribute_rewards, get_balance, get_positions)
            **kwargs: Action-specific parameters
        
        Returns:
            Dictionary with operation results
        """
        action = kwargs.get('action')
        
        if action == 'deposit':
            return self._deposit(**kwargs)
        elif action == 'open_position':
            return self._open_position(**kwargs)
        elif action == 'settle_position':
            return self._settle_position(**kwargs)
        elif action == 'distribute_rewards':
            return self._distribute_rewards(**kwargs)
        elif action == 'get_balance':
            return self._get_balance(**kwargs)
        elif action == 'get_positions':
            return self._get_positions(**kwargs)
        else:
            return {
                'success': False,
                'error': f'Unknown action: {action}',
                'available_actions': [
                    'deposit', 'open_position', 'settle_position',
                    'distribute_rewards', 'get_balance', 'get_positions'
                ]
            }
    
    def _deposit(self, user_address: str, amount: float, **kwargs) -> Dict[str, Any]:
        """Deposit funds to user balance."""
        return self.market.deposit(user_address, amount)
    
    def _open_position(self, user_address: str, amount: float, direction: str,
                      current_price: float, asset: str, 
                      start_time: str = None, **kwargs) -> Dict[str, Any]:
        """Open a new 24-hour prediction position.
        
        Args:
            user_address: User's wallet address
            amount: Amount to lock
            direction: 'up' or 'down'
            current_price: Current asset price
            asset: Asset symbol (e.g., 'BTC', 'ETH')
            start_time: Optional ISO format datetime string
        """
        start_dt = None
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
            except:
                return {'success': False, 'error': 'Invalid start_time format. Use ISO format.'}
        
        return self.market.open_position(
            user_address=user_address,
            amount=amount,
            direction=direction,
            current_price=current_price,
            asset=asset,
            start_time=start_dt
        )
    
    def _settle_position(self, position_id: str, exit_price: float,
                        current_time: str = None, **kwargs) -> Dict[str, Any]:
        """Settle a position after 24 hours.
        
        Args:
            position_id: Position ID to settle
            exit_price: Asset price at settlement
            current_time: Optional ISO format datetime string
        """
        current_dt = None
        if current_time:
            try:
                current_dt = datetime.fromisoformat(current_time)
            except:
                return {'success': False, 'error': 'Invalid current_time format. Use ISO format.'}
        
        return self.market.settle_position(
            position_id=position_id,
            exit_price=exit_price,
            current_time=current_dt
        )
    
    def _distribute_rewards(self, asset: str, settlement_time: str, **kwargs) -> Dict[str, Any]:
        """Distribute rewards to profitable traders.
        
        Args:
            asset: Asset symbol
            settlement_time: ISO format datetime string
        """
        try:
            settlement_dt = datetime.fromisoformat(settlement_time)
        except:
            return {'success': False, 'error': 'Invalid settlement_time format. Use ISO format.'}
        
        return self.market.distribute_rewards(
            asset=asset,
            settlement_time=settlement_dt
        )
    
    def _get_balance(self, user_address: str, **kwargs) -> Dict[str, Any]:
        """Get user's available balance."""
        balance = self.market.get_user_balance(user_address)
        return {
            'success': True,
            'user_address': user_address,
            'balance': balance
        }
    
    def _get_positions(self, user_address: str = None, **kwargs) -> Dict[str, Any]:
        """Get active positions."""
        positions = self.market.get_active_positions(user_address)
        return {
            'success': True,
            'positions': positions,
            'count': len(positions)
        }
