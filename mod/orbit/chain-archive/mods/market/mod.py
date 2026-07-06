"""Market contract module - stable token market with credit/debit."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Market contract module.

    ERC20 stable token with credit (buy), debit (spend), and withdrawal.
    """

    name = 'market'
    contracts = ['Market']
    dependencies = ['treasury', 'tokengate']

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy Market.

        Args:
            deps['treasury']: Treasury contract address
            deps['tokengate']: TokenGate contract address

        Returns:
            Market contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        treasury_address = deps.get('treasury')
        tokengate_address = deps.get('tokengate')

        deployment = self.get_deployment()
        contracts = deployment.get('contracts', {})
        if not treasury_address:
            treasury_address = contracts.get('Treasury', {}).get('address')
        if not tokengate_address:
            tokengate_address = contracts.get('TokenGate', {}).get('address')

        if not treasury_address:
            raise ValueError('Treasury address required to deploy Market')
        if not tokengate_address:
            raise ValueError('TokenGate address required to deploy Market')

        address = self.deploy_contract('Market', [
            'BlocTime Market Token',
            'BTMT',
            Web3.to_checksum_address(treasury_address),
            Web3.to_checksum_address(tokengate_address),
        ], contract_key='Market')

        return address

    def setup(self, network='testnet', **deps):
        """Set debit contract after deployment."""
        debit_address = deps.get('debit')
        if not debit_address:
            deployment = self.get_deployment()
            debit_address = deployment.get('contracts', {}).get('Debit', {}).get('address')
        if debit_address:
            self.send_tx('setDebitContract',
                         [Web3.to_checksum_address(debit_address)],
                         contract_key='Market')
            m.print('Market: Debit contract authorized', color='green')

    # ==================== INTERACTION ====================

    def credit(self, payment_token, stable_amount):
        """Buy stable tokens with payment token."""
        payment_token = Web3.to_checksum_address(payment_token)
        return self.send_tx('credit', [payment_token, stable_amount],
                            contract_key='Market')

    def balance_of(self, address):
        """Get market token balance."""
        address = Web3.to_checksum_address(address)
        return self.call('balanceOf', [address], contract_key='Market')

    def total_supply(self):
        """Get total supply."""
        return self.call('totalSupply', contract_key='Market')

    def treasury_address(self):
        """Get treasury address."""
        return self.call('treasury', contract_key='Market')

    def total_treasury_fees(self):
        """Get total treasury fees accrued."""
        return self.call('totalTreasuryFeesAccrued', contract_key='Market')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='Market')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'Market')
