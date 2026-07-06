"""BlocTime contract module - staking token deployment and interaction."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """BlocTime contract module.

    Unified staking + token contract. Stake native tokens for blocks,
    mint bloctime tokens based on duration multiplier.
    """

    name = 'bloctime'
    contracts = ['BlocTime']
    dependencies = ['token']

    DEFAULT_POINTS = [
        {'blocks': 0, 'multiplier': 10000},
        {'blocks': 10000, 'multiplier': 15000},
        {'blocks': 50000, 'multiplier': 20000},
        {'blocks': 100000, 'multiplier': 30000},
    ]

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy BlocTime.

        Args:
            deps['token']: Dict with 'NativeToken' address, or NativeToken address string

        Returns:
            BlocTime contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        # Resolve native token address
        native_token = deps.get('NativeToken') or deps.get('token')
        if isinstance(native_token, dict):
            native_token = native_token.get('NativeToken')
        if not native_token:
            deployment = self.get_deployment()
            native_token = deployment.get('contracts', {}).get('NativeToken', {}).get('address')
        if not native_token:
            raise ValueError('NativeToken address required to deploy BlocTime')

        address = self.deploy_contract('BlocTime', [
            Web3.to_checksum_address(native_token),
            'BlocTime Token',
            'BLOC',
            100000,  # maxLockBlocks
            5000,    # distributionPercentage
        ], contract_key='BlocTime')

        return address

    def setup(self, network='testnet', **deps):
        """Set multiplier points after deployment."""
        points = [(p['blocks'], p['multiplier']) for p in self.DEFAULT_POINTS]
        self.send_tx('setPoints', [points], contract_key='BlocTime')
        m.print('BlocTime multiplier points set', color='green')

    # ==================== INTERACTION ====================

    def stake(self, amount, lock_blocks):
        """Stake tokens."""
        return self.send_tx('stake', [amount, lock_blocks], contract_key='BlocTime')

    def unstake(self, stake_id):
        """Unstake tokens."""
        return self.send_tx('unstake', [stake_id], contract_key='BlocTime')

    def get_stake_position(self, address, stake_id=0):
        """Get stake position info."""
        address = Web3.to_checksum_address(address)
        info = self.call('getStakePosition', [address, stake_id], contract_key='BlocTime')
        return {
            'amount': info[0],
            'start_block': info[1],
            'lock_blocks': info[2],
            'bloctime_balance': info[3],
            'blocks_remaining': info[4],
        }

    def get_user_stake_ids(self, address):
        """Get all stake IDs for a user."""
        address = Web3.to_checksum_address(address)
        return self.call('getUserStakeIds', [address], contract_key='BlocTime')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='BlocTime')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'BlocTime')
