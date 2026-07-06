"""Perms contract module - permission system with parent key control."""

from mod.core.chain.chain.mods.base import m.mod('chain.contracts')ule
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Perms contract module.

    Permission system with parent key control and configurable limits.
    """

    name = 'perms'
    contracts = ['Perms']
    dependencies = []

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy Perms.

        Returns:
            Perms contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        address = self.deploy_contract('Perms', [], contract_key='Perms')
        return address

    # ==================== INTERACTION ====================

    def add_key(self, parent_key, child_key):
        """Add a child key to a parent key."""
        return self.send_tx('addKey', [parent_key, child_key], contract_key='Perms')

    def remove_key(self, parent_key, child_key):
        """Remove a child key."""
        return self.send_tx('removeKey', [parent_key, child_key], contract_key='Perms')

    def get_keys(self, parent_key):
        """Get all child keys for a parent key."""
        return self.call('getKeys', [parent_key], contract_key='Perms')

    def set_keys(self, parent_key, child_keys):
        """Set all child keys for a parent key."""
        return self.send_tx('setKeys', [parent_key, child_keys], contract_key='Perms')

    def transfer_ownership(self, new_owner):
        # Perms uses custom owner pattern, not OZ Ownable
        from web3 import Web3
        new_owner = Web3.to_checksum_address(new_owner)
        return self.send_tx('transferContractOwnership', [new_owner],
                            contract_key='Perms')

    def owner(self, contract_key=None):
        return self.call('owner', contract_key=contract_key or 'Perms')
