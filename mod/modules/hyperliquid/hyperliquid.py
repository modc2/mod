"""Hyperliquid Smart Contract Module - Python Implementation"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from decimal import Decimal
import json


@dataclass
class Position:
    """Represents a trading position"""
    symbol: str
    size: Decimal
    entry_price: Decimal
    side: str  # 'long' or 'short'
    leverage: int = 1
    
    def pnl(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized PnL"""
        if self.side == 'long':
            return (current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - current_price) * self.size


@dataclass
class Order:
    """Represents a trading order"""
    symbol: str
    size: Decimal
    price: Decimal
    side: str  # 'buy' or 'sell'
    order_type: str = 'limit'  # 'limit' or 'market'
    status: str = 'pending'  # 'pending', 'filled', 'cancelled'
    order_id: Optional[str] = None


class Hyperliquid:
    """Hyperliquid Trading Smart Contract - Immutable State Management"""
    
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None):
        """Initialize Hyperliquid contract"""
        self._api_key: Optional[str] = api_key
        self._secret: Optional[str] = secret
        self._positions: Dict[str, Position] = {}
        self._orders: List[Order] = []
        self._balance: Decimal = Decimal('0')
        self._locked: bool = False
    
    # STATE MODIFIERS (Smart Contract Functions)
    
    def deposit(self, amount: Decimal) -> Dict[str, Any]:
        """Deposit funds into the contract"""
        assert not self._locked, "Contract is locked"
        assert amount > 0, "Amount must be positive"
        
        self._balance += amount
        return {
            'success': True,
            'new_balance': float(self._balance),
            'deposited': float(amount)
        }
    
    def withdraw(self, amount: Decimal) -> Dict[str, Any]:
        """Withdraw funds from the contract"""
        assert not self._locked, "Contract is locked"
        assert amount > 0, "Amount must be positive"
        assert self._balance >= amount, "Insufficient balance"
        
        self._balance -= amount
        return {
            'success': True,
            'new_balance': float(self._balance),
            'withdrawn': float(amount)
        }
    
    def open_position(self, symbol: str, size: Decimal, price: Decimal, 
                     side: str, leverage: int = 1) -> Dict[str, Any]:
        """Open a new trading position"""
        assert not self._locked, "Contract is locked"
        assert side in ['long', 'short'], "Side must be 'long' or 'short'"
        assert leverage > 0, "Leverage must be positive"
        
        required_margin = (size * price) / leverage
        assert self._balance >= required_margin, "Insufficient margin"
        
        position = Position(
            symbol=symbol,
            size=size,
            entry_price=price,
            side=side,
            leverage=leverage
        )
        
        self._positions[symbol] = position
        self._balance -= required_margin
        
        return {
            'success': True,
            'position': {
                'symbol': symbol,
                'size': float(size),
                'entry_price': float(price),
                'side': side,
                'leverage': leverage
            },
            'remaining_balance': float(self._balance)
        }
    
    def close_position(self, symbol: str, exit_price: Decimal) -> Dict[str, Any]:
        """Close an existing position"""
        assert not self._locked, "Contract is locked"
        assert symbol in self._positions, f"No position found for {symbol}"
        
        position = self._positions[symbol]
        pnl = position.pnl(exit_price)
        margin_returned = (position.size * position.entry_price) / position.leverage
        
        self._balance += margin_returned + pnl
        del self._positions[symbol]
        
        return {
            'success': True,
            'pnl': float(pnl),
            'exit_price': float(exit_price),
            'new_balance': float(self._balance)
        }
    
    def place_order(self, symbol: str, size: Decimal, price: Decimal, 
                   side: str, order_type: str = 'limit') -> Dict[str, Any]:
        """Place a new order"""
        assert not self._locked, "Contract is locked"
        assert side in ['buy', 'sell'], "Side must be 'buy' or 'sell'"
        assert order_type in ['limit', 'market'], "Invalid order type"
        
        order = Order(
            symbol=symbol,
            size=size,
            price=price,
            side=side,
            order_type=order_type,
            order_id=f"order_{len(self._orders)}"
        )
        
        self._orders.append(order)
        
        return {
            'success': True,
            'order_id': order.order_id,
            'order': {
                'symbol': symbol,
                'size': float(size),
                'price': float(price),
                'side': side,
                'type': order_type
            }
        }
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order"""
        assert not self._locked, "Contract is locked"
        
        for order in self._orders:
            if order.order_id == order_id and order.status == 'pending':
                order.status = 'cancelled'
                return {'success': True, 'order_id': order_id, 'status': 'cancelled'}
        
        return {'success': False, 'error': 'Order not found or already processed'}
    
    # VIEW FUNCTIONS (Read-only)
    
    def get_balance(self) -> Decimal:
        """Get current balance"""
        return self._balance
    
    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        """Get all open positions"""
        return {
            symbol: {
                'size': float(pos.size),
                'entry_price': float(pos.entry_price),
                'side': pos.side,
                'leverage': pos.leverage
            }
            for symbol, pos in self._positions.items()
        }
    
    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get specific position"""
        if symbol in self._positions:
            pos = self._positions[symbol]
            return {
                'size': float(pos.size),
                'entry_price': float(pos.entry_price),
                'side': pos.side,
                'leverage': pos.leverage
            }
        return None
    
    def get_orders(self) -> List[Dict[str, Any]]:
        """Get all orders"""
        return [
            {
                'order_id': order.order_id,
                'symbol': order.symbol,
                'size': float(order.size),
                'price': float(order.price),
                'side': order.side,
                'type': order.order_type,
                'status': order.status
            }
            for order in self._orders
        ]
    
    def calculate_pnl(self, symbol: str, current_price: Decimal) -> Optional[Decimal]:
        """Calculate PnL for a position"""
        if symbol in self._positions:
            return self._positions[symbol].pnl(current_price)
        return None
    
    # ADMIN FUNCTIONS
    
    def lock(self) -> None:
        """Lock the contract (emergency stop)"""
        self._locked = True
    
    def unlock(self) -> None:
        """Unlock the contract"""
        self._locked = False
    
    def is_locked(self) -> bool:
        """Check if contract is locked"""
        return self._locked
    
    def get_state(self) -> Dict[str, Any]:
        """Get complete contract state"""
        return {
            'balance': float(self._balance),
            'positions': self.get_positions(),
            'orders': self.get_orders(),
            'locked': self._locked
        }
    
    def __repr__(self) -> str:
        return f"Hyperliquid(balance={self._balance}, positions={len(self._positions)}, orders={len(self._orders)})"
