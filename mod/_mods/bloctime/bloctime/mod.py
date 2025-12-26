"""BlocTime Protocol Python Interface

Provides easy interaction with BlocTime smart contracts.
"""

from web3 import Web3
from typing import Dict, Any, Optional, List
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
            name: Contract identifier (e.g., 'staking', 'marketplace', 'registry', 'token', 'whitelist', 'bid_system', 'integration')
            address: Contract address
            abi: Contract ABI
        """
        self.contracts[name] = self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=abi
        )
        return self.contracts[name]

    def load_all_contracts(self, addresses: Dict[str, str], abis: Dict[str, list]):
        """Load all BlocTime contracts at once.
        
        Args:
            addresses: Dict mapping contract names to addresses
            abis: Dict mapping contract names to ABIs
        """
        for name in ['staking', 'marketplace', 'registry', 'token', 'bloctime_token', 'whitelist', 'bid_system', 'integration']:
            if name in addresses and name in abis:
                self.load_contract(name, addresses[name], abis[name])

    # ==================== STAKING FUNCTIONS ====================

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

    def get_multiplier(self, block_count: int) -> int:
        """Get staking multiplier for block count.
        
        Args:
            block_count: Number of blocks to check
            
        Returns:
            Multiplier in basis points (10000 = 1x)
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        return staking.functions.getMultiplier(block_count).call()

    def pending_rewards(self, address: Optional[str] = None) -> int:
        """Get pending rewards for address.
        
        Args:
            address: Address to query (defaults to connected account)
            
        Returns:
            Pending reward amount in wei
        """
        staking = self.contracts.get('staking')
        if not staking:
            raise ValueError('Staking contract not loaded')
        addr = address or self.account.address
        return staking.functions.pendingRewards(addr).call()

    # ==================== REGISTRY FUNCTIONS ====================

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

    def update_module(self, module_id: int, price_per_block: int, max_users: int) -> Dict[str, Any]:
        """Update module parameters.
        
        Args:
            module_id: Module ID to update
            price_per_block: New price per block
            max_users: New max users
            
        Returns:
            Transaction receipt
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        
        tx = registry.functions.updateModule(
            module_id, price_per_block, max_users
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def deactivate_module(self, module_id: int) -> Dict[str, Any]:
        """Deactivate a module.
        
        Args:
            module_id: Module ID to deactivate
            
        Returns:
            Transaction receipt
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        
        tx = registry.functions.deactivateModule(module_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_module(self, module_id: int) -> Dict[str, Any]:
        """Get module information.
        
        Args:
            module_id: Module ID to query
            
        Returns:
            Module information dictionary
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        
        info = registry.functions.getModule(module_id).call()
        return {
            'owner': info[0],
            'price_per_block': info[1],
            'max_users': info[2],
            'current_users': info[3],
            'active': info[4],
            'ipfs_hash': info[5]
        }

    def is_module_available(self, module_id: int) -> bool:
        """Check if module is available for rent.
        
        Args:
            module_id: Module ID to check
            
        Returns:
            True if available
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        return registry.functions.isModuleAvailable(module_id).call()

    # ==================== MARKETPLACE FUNCTIONS ====================

    def rent_module(self, module_id: int, blocks: int, payment_token: str) -> Dict[str, Any]:
        """Rent a module for specified blocks.
        
        Args:
            module_id: Module ID to rent
            blocks: Number of blocks to rent
            payment_token: ERC20 token address for payment
            
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
        
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(payment_token),
            abi=[{"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]
        )
        approve_tx = token.functions.approve(
            marketplace.address, cost
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
        self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        # Rent
        tx = marketplace.functions.rent(module_id, blocks, payment_token).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def list_rental(self, rental_id: int, from_block: int, to_block: int, price: int, payment_token: str) -> Dict[str, Any]:
        """List rental period for sale.
        
        Args:
            rental_id: Rental ID to list
            from_block: Start block of listing period
            to_block: End block of listing period
            price: Listing price in wei
            payment_token: Payment token address
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        tx = marketplace.functions.listFractionalForSale(
            rental_id, from_block, to_block, price, payment_token
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
        payment_token = listing_info[5]
        
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(payment_token),
            abi=[{"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]
        )
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

    def get_rental(self, rental_id: int) -> Dict[str, Any]:
        """Get rental information.
        
        Args:
            rental_id: Rental ID to query
            
        Returns:
            Rental information dictionary
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        info = marketplace.functions.getRental(rental_id).call()
        return {
            'renter': info[0],
            'module_id': info[1],
            'start_block': info[2],
            'paid_blocks': info[3],
            'payment_token': info[4],
            'active': info[5]
        }

    def get_listing(self, listing_id: int) -> Dict[str, Any]:
        """Get listing information.
        
        Args:
            listing_id: Listing ID to query
            
        Returns:
            Listing information dictionary
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        info = marketplace.functions.getListing(listing_id).call()
        return {
            'seller': info[0],
            'rental_id': info[1],
            'from_block': info[2],
            'to_block': info[3],
            'price': info[4],
            'payment_token': info[5],
            'active': info[6]
        }

    # ==================== BID SYSTEM FUNCTIONS ====================

    def create_bid(self, rental_id: int, from_block: int, to_block: int, bid_amount: int, payment_token: str) -> Dict[str, Any]:
        """Create a bid on a rental slot.
        
        Args:
            rental_id: Target rental ID
            from_block: Start block of desired range
            to_block: End block of desired range
            bid_amount: Bid amount in wei
            payment_token: Payment token address
            
        Returns:
            Transaction receipt
        """
        bid_system = self.contracts.get('bid_system')
        if not bid_system:
            raise ValueError('Bid system contract not loaded')
        
        # Approve tokens
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(payment_token),
            abi=[{"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]
        )
        approve_tx = token.functions.approve(
            bid_system.address, bid_amount
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
        self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        # Create bid
        tx = bid_system.functions.createBid(
            rental_id, from_block, to_block, bid_amount, payment_token
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def accept_bid(self, bid_id: int) -> Dict[str, Any]:
        """Accept a bid on your rental.
        
        Args:
            bid_id: Bid ID to accept
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        tx = marketplace.functions.acceptBid(bid_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def reject_bid(self, bid_id: int) -> Dict[str, Any]:
        """Reject a bid on your rental.
        
        Args:
            bid_id: Bid ID to reject
            
        Returns:
            Transaction receipt
        """
        marketplace = self.contracts.get('marketplace')
        if not marketplace:
            raise ValueError('Marketplace contract not loaded')
        
        tx = marketplace.functions.rejectBid(bid_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def cancel_bid(self, bid_id: int) -> Dict[str, Any]:
        """Cancel your own bid.
        
        Args:
            bid_id: Bid ID to cancel
            
        Returns:
            Transaction receipt
        """
        bid_system = self.contracts.get('bid_system')
        if not bid_system:
            raise ValueError('Bid system contract not loaded')
        
        tx = bid_system.functions.cancelBid(bid_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_bid(self, bid_id: int) -> Dict[str, Any]:
        """Get bid information.
        
        Args:
            bid_id: Bid ID to query
            
        Returns:
            Bid information dictionary
        """
        bid_system = self.contracts.get('bid_system')
        if not bid_system:
            raise ValueError('Bid system contract not loaded')
        
        info = bid_system.functions.getBid(bid_id).call()
        return {
            'bidder': info[0],
            'rental_id': info[1],
            'from_block': info[2],
            'to_block': info[3],
            'bid_amount': info[4],
            'payment_token': info[5],
            'active': info[6],
            'accepted': info[7]
        }

    # ==================== INTEGRATION & SYSTEM FUNCTIONS ====================

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

    def validate_module_registration(self, module_id: int) -> Dict[str, Any]:
        """Validate module registration.
        
        Args:
            module_id: Module ID to validate
            
        Returns:
            Validation result
        """
        integration = self.contracts.get('integration')
        if not integration:
            raise ValueError('Integration contract not loaded')
        
        result = integration.functions.validateModuleRegistration(module_id).call()
        return {
            'valid': result[0],
            'reason': result[1]
        }

    def validate_rental_flow(self, rental_id: int) -> Dict[str, Any]:
        """Validate rental flow.
        
        Args:
            rental_id: Rental ID to validate
            
        Returns:
            Validation result
        """
        integration = self.contracts.get('integration')
        if not integration:
            raise ValueError('Integration contract not loaded')
        
        result = integration.functions.validateRentalFlow(rental_id).call()
        return {
            'valid': result[0],
            'reason': result[1]
        }

    # ==================== WHITELIST FUNCTIONS ====================

    def whitelist_token(self, token_address: str) -> Dict[str, Any]:
        """Whitelist a payment token (owner only).
        
        Args:
            token_address: ERC20 token address to whitelist
            
        Returns:
            Transaction receipt
        """
        whitelist = self.contracts.get('whitelist')
        if not whitelist:
            raise ValueError('Whitelist contract not loaded')
        
        tx = whitelist.functions.whitelistToken(token_address).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def delist_token(self, token_address: str) -> Dict[str, Any]:
        """Delist a payment token (owner only).
        
        Args:
            token_address: ERC20 token address to delist
            
        Returns:
            Transaction receipt
        """
        whitelist = self.contracts.get('whitelist')
        if not whitelist:
            raise ValueError('Whitelist contract not loaded')
        
        tx = whitelist.functions.delistToken(token_address).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def is_token_whitelisted(self, token_address: str) -> bool:
        """Check if token is whitelisted.
        
        Args:
            token_address: Token address to check
            
        Returns:
            True if whitelisted
        """
        whitelist = self.contracts.get('whitelist')
        if not whitelist:
            raise ValueError('Whitelist contract not loaded')
        return whitelist.functions.isTokenWhitelisted(token_address).call()

    def get_whitelisted_tokens(self) -> List[str]:
        """Get all whitelisted tokens.
        
        Returns:
            List of token addresses
        """
        whitelist = self.contracts.get('whitelist')
        if not whitelist:
            raise ValueError('Whitelist contract not loaded')
        return whitelist.functions.getWhitelistedTokens().call()

    # ==================== LEGACY & TEST FUNCTIONS ====================

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

    def forward(self, x=1, y=2):
        """Legacy function for backward compatibility."""
        return x + y
