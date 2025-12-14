"""BlocTime Protocol Python Interface

Provides easy interaction with BlocTime smart contracts.
"""

from web3 import Web3
from typing import Dict, Any, Optional
import json
import os


class Mod:
    """BlocTime Protocol Interface for Python."""

    def __init__(self, rpc_url: str = 'http://localhost:8545'):
        """Initialize BlocTime interface.
        
        Args:
            rpc_url: Ethereum RPC endpoint
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.contracts = {}
        self.account = None

    def connect(self, private_key: str):
        """Connect wallet using private key.
        
        Args:
            private_key: Private key for signing transactions
        """
        self.account = self.w3.eth.account.from_key(private_key)
        return self.account.address

    def load_contract(self, name: str, address: str, abi: list):
        """Load a contract interface.
        
        Args:
            name: Contract identifier (e.g., 'staking', 'marketplace')
            address: Contract address
            abi: Contract ABI
        """
        self.contracts[name] = self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=abi
        )
        return self.contracts[name]

    # Staking Functions
    def stake(self, amount: int, lock_blocks: int) -> Dict[str, Any]:
        """Stake tokens to earn BlocTime.
        
        Args:
            amount: Amount to stake (in wei)
            lock_blocks: Number of blocks to lock
            
        Returns:
            Transaction receipt
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        
        # Approve tokens first
        token = self.contracts.get('token')
        if token:
            approve_tx = token.functions.approve(
                staking.address, amount
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
            self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        # Stake
        tx = staking.functions.stake(amount, lock_blocks).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def unstake(self) -> Dict[str, Any]:
        """Unstake tokens after lock period.
        
        Returns:
            Transaction receipt
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        
        tx = staking.functions.unstake().build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def claim_rewards(self) -> Dict[str, Any]:
        """Claim treasury rewards.
        
        Returns:
            Transaction receipt
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        
        tx = staking.functions.claimRewards().build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_stake_info(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Get staking information for an address.
        
        Args:
            address: Address to query (defaults to connected account)
            
        Returns:
            Stake information dictionary
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        
        addr = address or self.account.address
        info = staking.functions.getStakeInfo(addr).call()
        return {
            'amount': info[0],
            'start_block': info[1],
            'lock_blocks': info[2],
            'bloctime_balance': info[3],
            'blocks_remaining': info[4],
            'pending_rewards': info[5]
        }

    # Marketplace Functions
    def register_module(self, price_per_block: int, max_users: int, ipfs_hash: str) -> Dict[str, Any]:
        """Register a new module.
        
        Args:
            price_per_block: Price per block in wei
            max_users: Maximum concurrent users
            ipfs_hash: IPFS hash of module metadata
            
        Returns:
            Transaction receipt
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        
        tx = registry.functions.registerModule(
            price_per_block, max_users, ipfs_hash
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def rent_module(self, module_id: int, blocks: int) -> Dict[str, Any]:
        """Rent a module for specified blocks.
        
        Args:
            module_id: Module ID to rent
            blocks: Number of blocks to rent
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        # Calculate cost and approve
        registry = self.contracts.get('registry')
        module_info = registry.functions.getModule(module_id).call()
        cost = module_info[1] * blocks
        
        token = self.contracts.get('token')
        if token:
            approve_tx = token.functions.approve(
                marketplace.address, cost
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
            self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        # Rent
        tx = marketplace.functions.rent(module_id, blocks).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def list_rental(self, rental_id: int, from_block: int, to_block: int, price: int) -> Dict[str, Any]:
        """List rental period for sale.
        
        Args:
            rental_id: Rental ID to list
            from_block: Start block of listing period
            to_block: End block of listing period
            price: Listing price in wei
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        tx = marketplace.functions.listFractionalForSale(
            rental_id, from_block, to_block, price
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def buy_listing(self, listing_id: int) -> Dict[str, Any]:
        """Buy a rental listing.
        
        Args:
            listing_id: Listing ID to purchase
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        # Get listing price and approve
        listing_info = marketplace.functions.getListing(listing_id).call()
        price = listing_info[4]
        
        token = self.contracts.get('token')
        if token:
            approve_tx = token.functions.approve(
                marketplace.address, price
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
            self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        # Buy
        tx = marketplace.functions.buy(listing_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    # View Functions
    def get_system_stats(self) -> Dict[str, Any]:
        """Get comprehensive system statistics.
        
        Returns:
            System statistics dictionary
        """
        integration = self.contracts.get('integration')
        if not integration:
            raise ValueError('Integration contract not loaded')
        
        stats = integration.functions.getSystemStats().call()
        return {
            'total_modules': stats[0],
            'total_rentals': stats[1],
            'total_staked': stats[2],
            'total_bloctime': stats[3],
            'treasury_balance': stats[4]
        }

    def health_check(self) -> Dict[str, Any]:
        """Check system health.
        
        Returns:
            Health check results
        """
        integration = self.contracts.get('integration')
        if not integration:
            raise ValueError('Integration contract not loaded')
        
        health = integration.functions.healthCheck().call()
        return {
            'marketplace_healthy': health[0],
            'registry_healthy': health[1],
            'staking_healthy': health[2],
            'status': health[3]
        }

    # Test deployment commands
    def test_deploy_ganache(self):
        """Test deployment on Ganache network."""
        print("🚀 Testing deployment on Ganache...")
        result = os.system('cd /Users/homie/mod/mod/_mods/bloctime && npm run deploy:ganache')
        if result == 0:
            print("✅ Ganache deployment successful!")
        else:
            print("❌ Ganache deployment failed!")
        return result

    def test_deploy_base(self):
        """Test deployment on Base network."""
        print("🚀 Testing deployment on Base...")
        result = os.system('cd /Users/homie/mod/mod/_mods/bloctime && npm run deploy:base')
        if result == 0:
            print("✅ Base deployment successful!")
        else:
            print("❌ Base deployment failed!")
        return result

    def test_all_deployments(self):
        """Test deployments on both Ganache and Base."""
        print("🎯 Running all deployment tests...\n")
        ganache_result = self.test_deploy_ganache()
        print("\n" + "="*50 + "\n")
        base_result = self.test_deploy_base()
        print("\n" + "="*50)
        print("\n📊 Deployment Test Summary:")
        print(f"Ganache: {'✅ PASSED' if ganache_result == 0 else '❌ FAILED'}")
        print(f"Base: {'✅ PASSED' if base_result == 0 else '❌ FAILED'}")
        return ganache_result == 0 and base_result == 0

    # Legacy compatibility
    def forward(self, x=1, y=2):
        """Legacy function for backward compatibility."""
        return x + y
