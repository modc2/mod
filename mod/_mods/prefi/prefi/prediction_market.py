"""Prediction Market Smart Contract for 24-Hour Trading.

This smart contract allows users to lock liquidity and predict whether an asset
will go up or down in the next 24 hours. Profitable traders are rewarded.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import hashlib


class Direction(Enum):
    """Trading direction enum."""
    UP = "up"
    DOWN = "down"


class Position:
    """Represents a locked position in the prediction market."""
    
    def __init__(self, user_address: str, amount: float, direction: Direction, 
                 entry_price: float, start_time: datetime, asset: str):
        self.user_address = user_address
        self.amount = amount
        self.direction = direction
        self.entry_price = entry_price
        self.start_time = start_time
        self.exit_time = start_time + timedelta(days=1)
        self.asset = asset
        self.exit_price: Optional[float] = None
        self.settled = False
        self.position_id = self._generate_id()
    
    def _generate_id(self) -> str:
        """Generate unique position ID."""
        data = f"{self.user_address}{self.start_time}{self.amount}{self.asset}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def is_profitable(self) -> bool:
        """Check if position is profitable."""
        if self.exit_price is None:
            return False
        
        if self.direction == Direction.UP:
            return self.exit_price > self.entry_price
        else:
            return self.exit_price < self.entry_price
    
    def calculate_profit(self) -> float:
        """Calculate profit/loss percentage."""
        if self.exit_price is None:
            return 0.0
        
        price_change = (self.exit_price - self.entry_price) / self.entry_price
        
        if self.direction == Direction.DOWN:
            price_change = -price_change
        
        return price_change * self.amount


class PredictionMarket:
    """24-Hour Prediction Market Smart Contract.
    
    This contract manages positions where users lock liquidity to predict
    asset price movements over exactly 24 hours.
    """
    
    def __init__(self, reward_pool_percentage: float = 0.95):
        """Initialize the prediction market.
        
        Args:
            reward_pool_percentage: Percentage of losing positions distributed to winners (0-1)
        """
        self.positions: Dict[str, Position] = {}
        self.active_positions: List[str] = []
        self.settled_positions: List[str] = []
        self.total_locked_liquidity: float = 0.0
        self.reward_pool_percentage = reward_pool_percentage
        self.user_balances: Dict[str, float] = {}
        
    def open_position(self, user_address: str, amount: float, direction: str,
                     current_price: float, asset: str, 
                     start_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Open a new 24-hour prediction position.
        
        Args:
            user_address: User's wallet address
            amount: Amount of liquidity to lock
            direction: "up" or "down" prediction
            current_price: Current asset price at entry
            asset: Asset symbol (e.g., "BTC", "ETH")
            start_time: Optional start time (defaults to now)
        
        Returns:
            Dictionary with position details
        """
        if amount <= 0:
            return {"success": False, "error": "Amount must be positive"}
        
        # Check user balance
        user_balance = self.user_balances.get(user_address, 0.0)
        if user_balance < amount:
            return {"success": False, "error": "Insufficient balance"}
        
        try:
            dir_enum = Direction.UP if direction.lower() == "up" else Direction.DOWN
        except:
            return {"success": False, "error": "Invalid direction. Use 'up' or 'down'"}
        
        start = start_time or datetime.now()
        position = Position(user_address, amount, dir_enum, current_price, start, asset)
        
        # Lock liquidity
        self.user_balances[user_address] -= amount
        self.total_locked_liquidity += amount
        
        self.positions[position.position_id] = position
        self.active_positions.append(position.position_id)
        
        return {
            "success": True,
            "position_id": position.position_id,
            "user_address": user_address,
            "amount": amount,
            "direction": direction,
            "entry_price": current_price,
            "exit_time": position.exit_time.isoformat(),
            "asset": asset
        }
    
    def settle_position(self, position_id: str, exit_price: float,
                       current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Settle a position after 24 hours.
        
        Args:
            position_id: ID of the position to settle
            exit_price: Asset price at exit time
            current_time: Optional current time (defaults to now)
        
        Returns:
            Dictionary with settlement results
        """
        if position_id not in self.positions:
            return {"success": False, "error": "Position not found"}
        
        position = self.positions[position_id]
        
        if position.settled:
            return {"success": False, "error": "Position already settled"}
        
        now = current_time or datetime.now()
        
        # Check if 24 hours have passed
        if now < position.exit_time:
            return {
                "success": False, 
                "error": "Position cannot be settled before exit time",
                "exit_time": position.exit_time.isoformat()
            }
        
        position.exit_price = exit_price
        position.settled = True
        
        self.active_positions.remove(position_id)
        self.settled_positions.append(position_id)
        
        is_profitable = position.is_profitable()
        profit = position.calculate_profit()
        
        return {
            "success": True,
            "position_id": position_id,
            "profitable": is_profitable,
            "profit": profit,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "direction": position.direction.value
        }
    
    def distribute_rewards(self, asset: str, settlement_time: datetime) -> Dict[str, Any]:
        """Distribute rewards to profitable traders for a specific asset and time.
        
        Args:
            asset: Asset symbol to distribute rewards for
            settlement_time: Time of settlement
        
        Returns:
            Dictionary with reward distribution details
        """
        # Get all settled positions for this asset around this time
        relevant_positions = [
            p for p in self.settled_positions
            if self.positions[p].asset == asset and
            abs((self.positions[p].exit_time - settlement_time).total_seconds()) < 3600
        ]
        
        if not relevant_positions:
            return {"success": False, "error": "No positions to distribute"}
        
        winners = []
        losers = []
        total_winning_amount = 0.0
        total_losing_amount = 0.0
        
        for pos_id in relevant_positions:
            position = self.positions[pos_id]
            if position.is_profitable():
                winners.append(position)
                total_winning_amount += position.amount
            else:
                losers.append(position)
                total_losing_amount += position.amount
        
        if not winners:
            # Return funds to losers if no winners
            for position in losers:
                self.user_balances[position.user_address] = \
                    self.user_balances.get(position.user_address, 0.0) + position.amount
                self.total_locked_liquidity -= position.amount
            
            return {
                "success": True,
                "winners": 0,
                "total_distributed": 0.0,
                "message": "No winners, funds returned"
            }
        
        # Calculate reward pool from losers
        reward_pool = total_losing_amount * self.reward_pool_percentage
        
        # Distribute proportionally to winners
        rewards_distributed = {}
        
        for position in winners:
            # Return original amount
            base_return = position.amount
            
            # Add proportional share of reward pool
            reward_share = (position.amount / total_winning_amount) * reward_pool
            
            total_return = base_return + reward_share
            
            self.user_balances[position.user_address] = \
                self.user_balances.get(position.user_address, 0.0) + total_return
            
            rewards_distributed[position.user_address] = {
                "original_amount": position.amount,
                "reward": reward_share,
                "total_return": total_return
            }
            
            self.total_locked_liquidity -= position.amount
        
        return {
            "success": True,
            "winners": len(winners),
            "losers": len(losers),
            "total_distributed": reward_pool,
            "rewards": rewards_distributed
        }
    
    def deposit(self, user_address: str, amount: float) -> Dict[str, Any]:
        """Deposit funds to user balance."""
        if amount <= 0:
            return {"success": False, "error": "Amount must be positive"}
        
        self.user_balances[user_address] = \
            self.user_balances.get(user_address, 0.0) + amount
        
        return {
            "success": True,
            "user_address": user_address,
            "deposited": amount,
            "new_balance": self.user_balances[user_address]
        }
    
    def get_user_balance(self, user_address: str) -> float:
        """Get user's available balance."""
        return self.user_balances.get(user_address, 0.0)
    
    def get_active_positions(self, user_address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all active positions, optionally filtered by user."""
        positions = []
        for pos_id in self.active_positions:
            position = self.positions[pos_id]
            if user_address is None or position.user_address == user_address:
                positions.append({
                    "position_id": pos_id,
                    "user_address": position.user_address,
                    "amount": position.amount,
                    "direction": position.direction.value,
                    "entry_price": position.entry_price,
                    "exit_time": position.exit_time.isoformat(),
                    "asset": position.asset
                })
        return positions
