"""
OpenHouse Module - Collective Asset Ownership Platform

This module provides a Python interface for interacting with the OpenHouse
smart contract system, enabling fractional ownership of real-world assets.

Features:
- Deploy OpenHouse contracts
- Purchase and manage shares
- Distribute dividends
- Query shareholder information
- Manage authority and governance

Author: OpenHouse Development Team
License: MIT
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import time


class PropertyStatus(Enum):
    """Property lifecycle status."""
    PENDING = "pending"
    ACTIVE = "active"
    FUNDED = "funded"
    DISTRIBUTING = "distributing"
    CLOSED = "closed"


@dataclass
class ShareholderInfo:
    """Represents shareholder information."""
    address: str
    share_count: int
    contribution: Decimal
    ownership_percentage: float
    join_timestamp: int = 0
    dividends_claimed: Decimal = Decimal("0")


@dataclass
class PropertyDetails:
    """Represents property information."""
    address: str
    description: str
    total_shares: int
    share_price: Decimal
    available_shares: int
    is_active: bool
    status: PropertyStatus = PropertyStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DividendRecord:
    """Represents a dividend distribution record."""
    timestamp: int
    total_amount: Decimal
    per_share_amount: Decimal
    recipients_count: int


class OpenHouseMod:
    """
    ANCHOR CLASS - OpenHouse Collective Asset Ownership Platform
    
    Provides high-level Python interface for OpenHouse smart contract
    operations including share management, dividend distribution, and
    governance functions.
    """
    
    description = """
    OpenHouse - Collective Asset Ownership Platform
    
    A blockchain-based system for fractional ownership of real-world assets.
    Enables transparent, secure, and automated management of collectively
    owned properties through smart contracts.
    
    Key Features:
    - Fractional ownership through tokenized shares
    - Automated dividend distribution
    - Transparent on-chain governance
    - Legal entity oversight
    - Secure and auditable transactions
    - Real-time portfolio tracking
    - Multi-property management
    
    Use Cases:
    - Residential real estate investment
    - Commercial property ownership
    - Alternative asset pooling
    - Community-owned infrastructure
    """
    
    def __init__(self, contract_address: Optional[str] = None, web3_provider: Optional[Any] = None):
        """
        Initialize OpenHouse module.
        
        Args:
            contract_address: Deployed contract address (optional)
            web3_provider: Web3 provider instance (optional)
        """
        self.contract_address = contract_address
        self.web3_provider = web3_provider
        self.contract = None
        self._shareholders: Dict[str, ShareholderInfo] = {}
        self._properties: Dict[str, PropertyDetails] = {}
        self._dividend_history: List[DividendRecord] = []
        self._event_handlers: Dict[str, List[Callable]] = {}
        
    # ==================== CORE OPERATIONS ====================
    
    def deploy_contract(self, 
                       authority_address: str,
                       property_details: str,
                       total_shares: int,
                       share_price: Decimal) -> Dict[str, Any]:
        """
        Deploy a new OpenHouse contract.
        
        Args:
            authority_address: Legal entity managing the property
            property_details: Description of the property
            total_shares: Total number of shares available
            share_price: Price per share in wei
            
        Returns:
            Dictionary containing deployment information
        """
        prop = PropertyDetails(
            address=self.contract_address or "pending",
            description=property_details,
            total_shares=total_shares,
            share_price=share_price,
            available_shares=total_shares,
            is_active=True,
            status=PropertyStatus.ACTIVE
        )
        self._properties[self.contract_address or "default"] = prop
        
        return {
            "status": "deployed",
            "contract_address": self.contract_address,
            "authority": authority_address,
            "total_shares": total_shares,
            "share_price": str(share_price),
            "timestamp": int(time.time())
        }
    
    def purchase_shares(self, buyer_address: str, share_count: int, payment: Decimal) -> Dict[str, Any]:
        """
        Purchase shares in the property.
        
        Args:
            buyer_address: Address of the buyer
            share_count: Number of shares to purchase
            payment: Payment amount in wei
            
        Returns:
            Transaction receipt and share information
        """
        cost = share_count * self.get_share_price()
        
        if payment < cost:
            raise ValueError(f"Insufficient payment. Required: {cost}, Provided: {payment}")
        
        available = self.get_available_shares()
        if share_count > available:
            raise ValueError(f"Insufficient shares. Available: {available}, Requested: {share_count}")
        
        # Update or create shareholder
        if buyer_address in self._shareholders:
            sh = self._shareholders[buyer_address]
            sh.share_count += share_count
            sh.contribution += cost
        else:
            self._shareholders[buyer_address] = ShareholderInfo(
                address=buyer_address,
                share_count=share_count,
                contribution=cost,
                ownership_percentage=0.0,
                join_timestamp=int(time.time())
            )
        
        # Update ownership percentages
        self._recalculate_ownership()
        self._emit_event("SharesPurchased", {"buyer": buyer_address, "shares": share_count})
        
        return {
            "status": "success",
            "buyer": buyer_address,
            "shares_purchased": share_count,
            "cost": str(cost),
            "refund": str(payment - cost) if payment > cost else "0",
            "new_balance": self._shareholders[buyer_address].share_count
        }
    
    def distribute_dividends(self, total_amount: Decimal) -> Dict[str, Any]:
        """
        Distribute dividends to all shareholders.
        
        Args:
            total_amount: Total dividend amount to distribute
            
        Returns:
            Distribution details for each shareholder
        """
        shareholders = self.get_all_shareholders()
        total_allocated = sum(s.share_count for s in shareholders)
        
        if total_allocated == 0:
            return {"status": "no_shareholders", "distributions": []}
        
        per_share = total_amount / total_allocated
        distributions = []
        
        for shareholder in shareholders:
            if shareholder.share_count > 0:
                dividend = per_share * shareholder.share_count
                shareholder.dividends_claimed += dividend
                distributions.append({
                    "address": shareholder.address,
                    "shares": shareholder.share_count,
                    "dividend": str(dividend),
                    "ownership_pct": f"{shareholder.ownership_percentage:.2f}%"
                })
        
        record = DividendRecord(
            timestamp=int(time.time()),
            total_amount=total_amount,
            per_share_amount=per_share,
            recipients_count=len(distributions)
        )
        self._dividend_history.append(record)
        self._emit_event("DividendsDistributed", {"total": str(total_amount)})
        
        return {
            "status": "success",
            "total_distributed": str(total_amount),
            "per_share": str(per_share),
            "distributions": distributions
        }
    
    # ==================== QUERY OPERATIONS ====================
    
    def get_shareholder_info(self, address: str) -> ShareholderInfo:
        """
        Get detailed information about a shareholder.
        
        Args:
            address: Shareholder's address
            
        Returns:
            ShareholderInfo object with ownership details
        """
        if address in self._shareholders:
            return self._shareholders[address]
        return ShareholderInfo(
            address=address,
            share_count=0,
            contribution=Decimal("0"),
            ownership_percentage=0.0
        )
    
    def get_property_details(self) -> PropertyDetails:
        """Get current property and contract information."""
        key = self.contract_address or "default"
        if key in self._properties:
            return self._properties[key]
        return PropertyDetails(
            address=self.contract_address or "Not deployed",
            description="Property details",
            total_shares=1000,
            share_price=Decimal("0.1"),
            available_shares=1000,
            is_active=True
        )
    
    def get_all_shareholders(self) -> List[ShareholderInfo]:
        """Get list of all shareholders."""
        return list(self._shareholders.values())
    
    def get_share_price(self) -> Decimal:
        """Get current share price."""
        prop = self.get_property_details()
        return prop.share_price
    
    def get_available_shares(self) -> int:
        """Get number of available shares for purchase."""
        prop = self.get_property_details()
        allocated = sum(s.share_count for s in self._shareholders.values())
        return prop.total_shares - allocated
    
    def get_dividend_history(self) -> List[Dict[str, Any]]:
        """Get history of dividend distributions."""
        return [{
            "timestamp": r.timestamp,
            "total": str(r.total_amount),
            "per_share": str(r.per_share_amount),
            "recipients": r.recipients_count
        } for r in self._dividend_history]
    
    def get_portfolio_summary(self, address: str) -> Dict[str, Any]:
        """Get portfolio summary for an address."""
        sh = self.get_shareholder_info(address)
        prop = self.get_property_details()
        return {
            "address": address,
            "shares": sh.share_count,
            "ownership_pct": f"{sh.ownership_percentage:.2f}%",
            "contribution": str(sh.contribution),
            "dividends_claimed": str(sh.dividends_claimed),
            "current_value": str(sh.share_count * prop.share_price),
            "property_status": prop.status.value
        }
    
    # ==================== GOVERNANCE ====================
    
    def record_management_action(self, action: str, details: Optional[Dict] = None) -> Dict[str, Any]:
        """Record a property management action (authority only)."""
        self._emit_event("ManagementAction", {"action": action, "details": details})
        return {
            "status": "recorded",
            "action": action,
            "details": details,
            "timestamp": int(time.time())
        }
    
    def transfer_authority(self, new_authority: str) -> Dict[str, Any]:
        """Transfer authority to new legal entity (authority only)."""
        self._emit_event("AuthorityTransferred", {"new_authority": new_authority})
        return {
            "status": "transferred",
            "new_authority": new_authority,
            "timestamp": int(time.time())
        }
    
    def toggle_active_status(self) -> Dict[str, Any]:
        """Toggle contract active status (authority only)."""
        prop = self.get_property_details()
        prop.is_active = not prop.is_active
        return {"status": "toggled", "is_active": prop.is_active}
    
    def get_contract_balance(self) -> Decimal:
        """Get current contract balance."""
        return sum(s.contribution for s in self._shareholders.values())
    
    # ==================== EVENT SYSTEM ====================
    
    def on_event(self, event_name: str, handler: Callable):
        """Register event handler."""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)
    
    def _emit_event(self, event_name: str, data: Dict[str, Any]):
        """Emit event to registered handlers."""
        if event_name in self._event_handlers:
            for handler in self._event_handlers[event_name]:
                handler(data)
    
    # ==================== UTILITIES ====================
    
    def _recalculate_ownership(self):
        """Recalculate ownership percentages for all shareholders."""
        total = sum(s.share_count for s in self._shareholders.values())
        for sh in self._shareholders.values():
            sh.ownership_percentage = (sh.share_count / total * 100) if total > 0 else 0.0
    
    @staticmethod
    def calculate_ownership_percentage(shares: int, total_allocated: int) -> float:
        """Calculate ownership percentage."""
        if total_allocated == 0:
            return 0.0
        return (shares / total_allocated) * 100
    
    @staticmethod
    def calculate_dividend(shares: int, total_allocated: int, total_dividend: Decimal) -> Decimal:
        """Calculate dividend for shareholder."""
        if total_allocated == 0:
            return Decimal("0")
        return (total_dividend * shares) / total_allocated


# Module metadata
__version__ = "2.0.0"
__author__ = "OpenHouse Development Team"
__license__ = "MIT"
__description__ = "Collective Asset Ownership Platform - Enhanced Smart Contract Interface"
