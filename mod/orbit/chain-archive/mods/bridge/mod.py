"""Bridge contract module - cross-chain bridgeable token."""

from mod.core.chain.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Bridge contract module.

    ERC20 token with owner-controlled mint/burn for cross-chain bridging.
    """

    name = 'bridge'
    contracts = ['BridgeableToken']
    dependencies = []

    def deploy(self, network='testnet', key=None, name='Bridge Token',
               symbol='BRG', initial_supply=0, **deps):
        """Deploy BridgeableToken.

        Args:
            name: Token name
            symbol: Token symbol
            initial_supply: Initial supply (0 for bridge tokens)

        Returns:
            BridgeableToken contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        address = self.deploy_contract('BridgeableToken',
                                       [name, symbol, initial_supply],
                                       contract_key='Bridge')
        return address

    # ==================== INTERACTION ====================

    def bridge_mint(self, to, amount, bridge_id=''):
        """Mint tokens for bridge deposit."""
        to = Web3.to_checksum_address(to)
        return self.send_tx('bridgeMint', [to, amount, bridge_id],
                            contract_key='Bridge')

    def bridge_burn(self, from_addr, amount, bridge_id=''):
        """Burn tokens for bridge withdrawal."""
        from_addr = Web3.to_checksum_address(from_addr)
        return self.send_tx('bridgeBurn', [from_addr, amount, bridge_id],
                            contract_key='Bridge')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='Bridge')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'Bridge')
