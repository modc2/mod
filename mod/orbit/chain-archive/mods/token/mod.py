"""Token contract module - ERC20 token deployment and interaction."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3


class Mod(m.mod('chain.contracts')ule):
    """Token (ERC20) contract module.

    Deploys mock tokens for testnet/local (USDC, USDT, DAI, NativeToken).
    On mainnet, uses existing token addresses.
    """

    name = 'token'
    contracts = ['Token']
    dependencies = []

    # Real mainnet token addresses
    MAINNET_TOKENS = {
        'base': {
            'USDT': '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2',
            'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
            'DAI': '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',
        },
        'ethereum': {
            'USDT': '0xdAC17F958D2ee523a2206206994597C13D831ec7',
            'USDC': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
            'DAI': '0x6B175474E89094C44Da98b954EedeAC495271d0F',
        },
        'arbitrum': {
            'USDT': '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
            'USDC': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
            'DAI': '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',
        },
        'polygon': {
            'USDT': '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
            'USDC': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
            'DAI': '0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063',
        },
    }

    MAINNET_NETWORKS = ['ethereum', 'base', 'arbitrum', 'polygon']

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy token contracts.

        On testnet/ganache: deploys mock USDC, USDT, DAI, NativeToken.
        On mainnet: saves existing addresses to config.

        Returns:
            Dict of token name -> address
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        addresses = {}
        is_mainnet = network in self.MAINNET_NETWORKS

        if is_mainnet:
            tokens = self.MAINNET_TOKENS.get(network, {})
            for name, addr in tokens.items():
                self.save_deployment(name, addr, 'ERC20')
                addresses[name] = addr
        else:
            supply = Web3.to_wei(1_000_000, 'ether')
            for name, symbol in [('USDC', 'USDC'), ('USDT', 'USDT'), ('DAI', 'DAI')]:
                addr = self.deploy_contract(
                    'Token', [f'{name} Token', symbol, supply],
                    contract_key=name,
                )
                addresses[name] = addr

            # Native token
            addr = self.deploy_contract(
                'Token', ['Native Token', 'NAT', supply],
                contract_key='NativeToken',
            )
            addresses['NativeToken'] = addr

        return addresses

    # ==================== INTERACTION ====================

    def balance_of(self, address, token_key='USDC'):
        """Get token balance."""
        address = Web3.to_checksum_address(address)
        return self.call('balanceOf', [address], contract_key=token_key)

    def transfer(self, to, amount, token_key='USDC'):
        """Transfer tokens."""
        to = Web3.to_checksum_address(to)
        return self.send_tx('transfer', [to, amount], contract_key=token_key)

    def approve(self, spender, amount, token_key='USDC'):
        """Approve spender."""
        spender = Web3.to_checksum_address(spender)
        return self.send_tx('approve', [spender, amount], contract_key=token_key)
