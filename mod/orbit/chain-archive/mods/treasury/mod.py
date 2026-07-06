"""Treasury contract module - proportional withdrawal treasury."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Treasury contract module.

    Distributes funds proportionally based on governance token holdings.
    """

    name = 'treasury'
    contracts = ['Treasury']
    dependencies = ['tokengate', 'bloctime']

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy Treasury.

        Args:
            deps['tokengate']: TokenGate contract address
            deps['bloctime']: BlocTime contract address (for governance token)

        Returns:
            Treasury contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        tokengate_address = deps.get('tokengate')
        if not tokengate_address:
            deployment = self.get_deployment()
            tokengate_address = deployment.get('contracts', {}).get('TokenGate', {}).get('address')
        if not tokengate_address:
            raise ValueError('TokenGate address required to deploy Treasury')

        owner_percentage = 2000  # 20% in basis points
        address = self.deploy_contract('Treasury',
                                       [owner_percentage, Web3.to_checksum_address(tokengate_address)],
                                       contract_key='Treasury')
        return address

    def setup(self, network='testnet', **deps):
        """Set governance token after deployment."""
        bloctime_address = deps.get('bloctime')
        if not bloctime_address:
            deployment = self.get_deployment()
            bloctime_address = deployment.get('contracts', {}).get('BlocTime', {}).get('address')
        if bloctime_address:
            self.send_tx('setGovernanceToken',
                         [Web3.to_checksum_address(bloctime_address)],
                         contract_key='Treasury')
            m.print('Treasury governance token set to BlocTime', color='green')

    # ==================== INTERACTION ====================

    def fund(self, token_address, amount):
        """Fund treasury with tokens."""
        token_address = Web3.to_checksum_address(token_address)
        return self.send_tx('fundTreasury', [token_address, amount],
                            contract_key='Treasury')

    def withdraw(self, token_address):
        """Withdraw proportional share."""
        token_address = Web3.to_checksum_address(token_address)
        return self.send_tx('withdrawToken', [token_address],
                            contract_key='Treasury')

    def get_claimable(self, holder, token_address):
        """Get claimable amount for holder."""
        holder = Web3.to_checksum_address(holder)
        token_address = Web3.to_checksum_address(token_address)
        return self.call('getClaimableAmount', [holder, token_address],
                         contract_key='Treasury')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='Treasury')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'Treasury')
