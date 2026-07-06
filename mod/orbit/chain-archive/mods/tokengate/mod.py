"""TokenGate contract module - token whitelist and oracle management."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """TokenGate contract module.

    Manages token whitelist and per-token oracle adapters.
    """

    name = 'tokengate'
    contracts = ['TokenGate']
    dependencies = ['oracle']

    ETH_SENTINEL = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy TokenGate.

        Args:
            deps['oracle']: Oracle contract address (required)

        Returns:
            TokenGate contract address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        oracle_address = deps.get('oracle')
        if not oracle_address:
            deployment = self.get_deployment()
            oracle_address = (deployment.get('contracts', {}).get('Oracle', {}).get('address')
                              or deployment.get('contracts', {}).get('ManualPriceOracle', {}).get('address'))
        if not oracle_address:
            raise ValueError('Oracle address required to deploy TokenGate')

        address = self.deploy_contract('TokenGate',
                                       [Web3.to_checksum_address(oracle_address)],
                                       contract_key='TokenGate')
        return address

    def setup(self, network='testnet', **deps):
        """Whitelist tokens after deployment."""
        deployment = self.get_deployment()
        contracts = deployment.get('contracts', {})

        for token_key in ['USDC', 'USDT', 'DAI']:
            token_info = contracts.get(token_key)
            if token_info:
                self.send_tx('whitelistToken',
                             [Web3.to_checksum_address(token_info['address'])],
                             contract_key='TokenGate')
                m.print(f'Whitelisted {token_key}', color='green')

        # Whitelist ETH sentinel
        self.send_tx('whitelistToken',
                     [Web3.to_checksum_address(self.ETH_SENTINEL)],
                     contract_key='TokenGate')
        m.print('Whitelisted ETH sentinel', color='green')

    # ==================== INTERACTION ====================

    def whitelist_token(self, token_address):
        """Whitelist a token."""
        return self.send_tx('whitelistToken',
                            [Web3.to_checksum_address(token_address)],
                            contract_key='TokenGate')

    def delist_token(self, token_address):
        """Delist a token."""
        return self.send_tx('delistToken',
                            [Web3.to_checksum_address(token_address)],
                            contract_key='TokenGate')

    def is_whitelisted(self, token_address):
        """Check if token is whitelisted."""
        return self.call('isTokenWhitelisted',
                         [Web3.to_checksum_address(token_address)],
                         contract_key='TokenGate')

    def get_token_price(self, token_address):
        """Get token price from oracle."""
        info = self.call('getTokenPrice',
                         [Web3.to_checksum_address(token_address)],
                         contract_key='TokenGate')
        return {'price': info[0], 'decimals': info[1], 'timestamp': info[2]}

    def get_whitelisted_tokens(self):
        """Get all whitelisted tokens."""
        return self.call('getWhitelistedTokens', contract_key='TokenGate')

    def transfer_ownership(self, new_owner):
        return super().transfer_ownership(new_owner, contract_key='TokenGate')

    def owner(self, contract_key=None):
        return super().owner(contract_key=contract_key or 'TokenGate')
