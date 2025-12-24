"""
Whitelist Manager for X402 Middleware
Supports both on-chain (smart contract) and off-chain (JSON/API) whitelist sources
"""

import json
import logging
import requests
from typing import List, Optional, Set
from web3 import Web3
from threading import Lock
import time

logger = logging.getLogger("x402.whitelist")

# ABI for whitelist smart contract
WHITELIST_ABI = [
    {"inputs": [{"name": "_address", "type": "address"}], "name": "isWhitelisted", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getWhitelistedAddresses", "outputs": [{"name": "", "type": "address[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "_address", "type": "address"}], "name": "addToWhitelist", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "_address", "type": "address"}], "name": "removeFromWhitelist", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]


class WhitelistManager:
    """
    Manages wallet whitelist from multiple sources:
    - On-chain: Smart contract on Base/Ethereum
    - Off-chain: Local JSON file or remote API
    """
    
    def __init__(
        self,
        mode: str = "offchain",
        contract_address: Optional[str] = None,
        rpc_url: str = "https://mainnet.base.org",
        offchain_file: Optional[str] = None,
        offchain_url: Optional[str] = None,
        cache_ttl: int = 300,
    ):
        self.mode = mode
        self.contract_address = contract_address
        self.rpc_url = rpc_url
        self.offchain_file = offchain_file
        self.offchain_url = offchain_url
        self.cache_ttl = cache_ttl
        
        self._cache: Set[str] = set()
        self._cache_timestamp: float = 0
        self._lock = Lock()
        
        self.w3: Optional[Web3] = None
        self.contract = None
        
        if mode == "onchain" and contract_address:
            self._init_web3()
        
        logger.info(f"WhitelistManager initialized in {mode} mode")
    
    def _init_web3(self):
        """Initialize Web3 connection and contract."""
        try:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if self.w3.is_connected() and self.contract_address:
                self.contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(self.contract_address),
                    abi=WHITELIST_ABI
                )
                logger.info(f"Connected to contract at {self.contract_address}")
        except Exception as e:
            logger.error(f"Failed to initialize Web3: {e}")
    
    def _refresh_cache(self):
        """Refresh the whitelist cache."""
        now = time.time()
        if now - self._cache_timestamp < self.cache_ttl:
            return
        
        with self._lock:
            if now - self._cache_timestamp < self.cache_ttl:
                return
            
            try:
                if self.mode == "onchain":
                    self._cache = self._fetch_onchain()
                else:
                    self._cache = self._fetch_offchain()
                self._cache_timestamp = now
                logger.info(f"Whitelist cache refreshed: {len(self._cache)} addresses")
            except Exception as e:
                logger.error(f"Failed to refresh whitelist: {e}")
    
    def _fetch_onchain(self) -> Set[str]:
        """Fetch whitelist from smart contract."""
        if not self.contract:
            return set()
        
        try:
            addresses = self.contract.functions.getWhitelistedAddresses().call()
            return {addr.lower() for addr in addresses}
        except Exception as e:
            logger.error(f"Failed to fetch on-chain whitelist: {e}")
            return self._cache
    
    def _fetch_offchain(self) -> Set[str]:
        """Fetch whitelist from file or URL."""
        addresses = set()
        
        if self.offchain_file:
            try:
                with open(self.offchain_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        addresses.update(addr.lower() for addr in data)
                    elif isinstance(data, dict) and 'addresses' in data:
                        addresses.update(addr.lower() for addr in data['addresses'])
            except Exception as e:
                logger.error(f"Failed to read whitelist file: {e}")
        
        if self.offchain_url:
            try:
                resp = requests.get(self.offchain_url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    addresses.update(addr.lower() for addr in data)
                elif isinstance(data, dict) and 'addresses' in data:
                    addresses.update(addr.lower() for addr in data['addresses'])
            except Exception as e:
                logger.error(f"Failed to fetch whitelist from URL: {e}")
        
        return addresses
    
    def is_whitelisted(self, address: str) -> bool:
        """Check if an address is whitelisted."""
        self._refresh_cache()
        
        normalized = address.lower()
        
        if self.mode == "onchain" and self.contract:
            try:
                return self.contract.functions.isWhitelisted(
                    Web3.to_checksum_address(address)
                ).call()
            except Exception as e:
                logger.error(f"On-chain check failed, using cache: {e}")
        
        return normalized in self._cache
    
    def get_all_whitelisted(self) -> List[str]:
        """Get all whitelisted addresses."""
        self._refresh_cache()
        return list(self._cache)
    
    def add_to_whitelist(self, address: str):
        """Add address to local cache (for offchain mode)."""
        if self.mode == "offchain":
            with self._lock:
                self._cache.add(address.lower())
                if self.offchain_file:
                    self._save_offchain()
    
    def remove_from_whitelist(self, address: str):
        """Remove address from local cache (for offchain mode)."""
        if self.mode == "offchain":
            with self._lock:
                self._cache.discard(address.lower())
                if self.offchain_file:
                    self._save_offchain()
    
    def _save_offchain(self):
        """Save whitelist to file."""
        try:
            with open(self.offchain_file, 'w') as f:
                json.dump(list(self._cache), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save whitelist: {e}")
