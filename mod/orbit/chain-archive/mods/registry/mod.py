"""Registry contract module - mod registration on-chain."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Registry contract module.

    Minimal registry for managing mod metadata on-chain.
    """

    name = 'registry'
    contracts = ['Registry']
    dependencies = []

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy Registry.

        Returns:
            Registry contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        address = self.deploy_contract('Registry', [], contract_key='Registry')
        return address

    # ==================== INTERACTION ====================

    def register_mod(self, name, data):
        """Register a new mod."""
        return self.send_tx('registerMod', [name, data], contract_key='Registry')

    def update_mod(self, mod_id, data):
        """Update mod data."""
        return self.send_tx('updateMod', [mod_id, data], contract_key='Registry')

    def remove_mod(self, mod_id):
        """Remove a mod."""
        return self.send_tx('removeMod', [mod_id], contract_key='Registry')

    def get_mod(self, mod_id):
        """Get mod info."""
        info = self.call('getMod', [mod_id], contract_key='Registry')
        return {'owner': info[0], 'name': info[1], 'data': info[2]}

    def get_user_mods(self, address):
        """Get user's mod IDs."""
        address = Web3.to_checksum_address(address)
        return self.call('getUserMods', [address], contract_key='Registry')

    def is_name_taken(self, creator, name):
        """Check if name is taken for creator."""
        creator = Web3.to_checksum_address(creator)
        return self.call('isNameTaken', [creator, name], contract_key='Registry')

    def transfer_ownership(self, new_owner):
        # Registry doesn't have Ownable - it uses per-mod ownership
        pass

    def owner(self, contract_key=None):
        # Registry has no global owner
        return None
