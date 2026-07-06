"""Debit contract module - EIP-712 signature-based debit system."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Debit contract module.

    EIP-712 signature-based debit with multisig authority approvals.
    """

    name = 'debit'
    contracts = ['Debit']
    dependencies = ['market']

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy Debit.

        Args:
            deps['market']: Market contract address

        Returns:
            Debit contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        market_address = deps.get('market')
        if not market_address:
            deployment = self.get_deployment()
            market_address = deployment.get('contracts', {}).get('Market', {}).get('address')
        if not market_address:
            raise ValueError('Market address required to deploy Debit')

        address = self.deploy_contract('Debit',
                                       [Web3.to_checksum_address(market_address)],
                                       contract_key='Debit')
        return address

    # ==================== INTERACTION ====================

    def execute_debit(self, client, provider, amount, deadline=0, signature=b''):
        """Execute a debit transaction."""
        client = Web3.to_checksum_address(client)
        provider = Web3.to_checksum_address(provider)
        return self.send_tx('executeDebit',
                            [client, provider, amount, deadline, signature],
                            contract_key='Debit')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='Debit')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'Debit')
