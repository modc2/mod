"""Arweave storage module with wallet support for permanent data storage."""

import json
import os
from typing import Optional, Dict, Any
import requests
from pathlib import Path


class ArweaveWallet:
    """Arweave wallet for managing tokens and signing transactions."""
    
    def __init__(self, wallet_path: Optional[str] = None):
        self.wallet_path = wallet_path
        self.jwk = None
        if wallet_path and os.path.exists(wallet_path):
            self.load_wallet(wallet_path)
    
    def load_wallet(self, path: str):
        """Load wallet from JWK file."""
        with open(path, 'r') as f:
            self.jwk = json.load(f)
        return self.jwk
    
    def save_wallet(self, path: str):
        """Save wallet to JWK file."""
        if self.jwk:
            with open(path, 'w') as f:
                json.dump(self.jwk, f)
    
    def get_address(self) -> Optional[str]:
        """Get wallet address."""
        if self.jwk:
            # Address derivation would require crypto libraries
            return self.jwk.get('n', 'wallet_address')
        return None


class ArweaveClient:
    """Arweave client for permanent data storage (IPFS-like interface)."""
    
    def __init__(self, gateway: str = "https://arweave.net", wallet: Optional[ArweaveWallet] = None):
        self.gateway = gateway
        self.wallet = wallet or ArweaveWallet()
    
    def add(self, data: bytes, tags: Optional[Dict[str, str]] = None) -> str:
        """Add data to Arweave (like IPFS add)."""
        # Create transaction
        tx_data = {
            'data': data.decode('utf-8') if isinstance(data, bytes) else data,
            'tags': tags or {}
        }
        
        # In production, this would create and sign a proper Arweave transaction
        # For now, return a mock transaction ID
        tx_id = f"arweave_tx_{hash(str(data))}"
        return tx_id
    
    def cat(self, tx_id: str) -> bytes:
        """Retrieve data from Arweave (like IPFS cat)."""
        url = f"{self.gateway}/{tx_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.content
    
    def get(self, tx_id: str, output_path: Optional[str] = None) -> bytes:
        """Get data from Arweave and optionally save to file."""
        data = self.cat(tx_id)
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(data)
        return data
    
    def pin(self, tx_id: str) -> bool:
        """Pin content (already permanent on Arweave)."""
        # Arweave is permanent by default
        return True
    
    def get_balance(self) -> float:
        """Get wallet balance in AR tokens."""
        if not self.wallet or not self.wallet.get_address():
            return 0.0
        
        address = self.wallet.get_address()
        url = f"{self.gateway}/wallet/{address}/balance"
        try:
            response = requests.get(url)
            response.raise_for_status()
            # Balance is in winston (1 AR = 1e12 winston)
            winston = int(response.text)
            return winston / 1e12
        except:
            return 0.0
    
    def get_price(self, data_size: int) -> float:
        """Get price for storing data of given size."""
        url = f"{self.gateway}/price/{data_size}"
        try:
            response = requests.get(url)
            response.raise_for_status()
            winston = int(response.text)
            return winston / 1e12
        except:
            return 0.0


# IPFS-like interface
class Arweave:
    """Main Arweave interface with IPFS-like methods."""
    
    def __init__(self, wallet_path: Optional[str] = None, gateway: str = "https://arweave.net"):
        self.wallet = ArweaveWallet(wallet_path)
        self.client = ArweaveClient(gateway, self.wallet)
    
    def add(self, data: bytes, **kwargs) -> str:
        """Add data to Arweave."""
        return self.client.add(data, **kwargs)
    
    def cat(self, cid: str) -> bytes:
        """Retrieve data from Arweave."""
        return self.client.cat(cid)
    
    def get(self, cid: str, output: Optional[str] = None) -> bytes:
        """Get data from Arweave."""
        return self.client.get(cid, output)
    
    def pin_add(self, cid: str) -> bool:
        """Pin content (permanent by default)."""
        return self.client.pin(cid)
    
    def balance(self) -> float:
        """Get wallet balance."""
        return self.client.get_balance()
    
    def price(self, size: int) -> float:
        """Get storage price."""
        return self.client.get_price(size)
