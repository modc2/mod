"""Safe contract module - multi-signature wallet deployment."""

from mod.core.chain.mods.base import m.mod('chain.contracts')ule
from web3 import Web3
import mod as m


class Mod(m.mod('chain.contracts')ule):
    """Safe contract module.

    Deploys Safe singleton, proxy factory, and creates proxy wallets
    with N-of-M multisig support.
    """

    name = 'safe'
    contracts = ['Safe', 'SafeProxy', 'SafeProxyFactory']
    dependencies = []

    def deploy(self, network='testnet', key=None, owners=None, threshold=1, **deps):
        """Deploy Safe protocol contracts and create a proxy.

        Args:
            owners: List of owner addresses (defaults to [deployer])
            threshold: Number of required signatures

        Returns:
            Dict with singleton, factory, and proxy addresses
        """
        if key:
            self.set_key(key)
        if network:
            self.network = network

        # Deploy singleton
        singleton_addr = self.deploy_contract('Safe', [],
                                              contract_key='SafeSingleton')

        # Deploy proxy factory
        factory_addr = self.deploy_contract('SafeProxyFactory', [],
                                            contract_key='SafeProxyFactory')

        # Create proxy
        if owners is None:
            owners = [self.account.address]
        owners = [Web3.to_checksum_address(o) for o in owners]

        zero = '0x0000000000000000000000000000000000000000'
        singleton_abi = self.get_abi('Safe')
        singleton = self.w3.eth.contract(
            address=Web3.to_checksum_address(singleton_addr),
            abi=singleton_abi,
        )

        setup_data = singleton.encode_abi('setup', args=[
            owners, threshold, zero, b'', zero, zero, 0, zero
        ])

        import time
        salt_nonce = int(time.time() * 1000)

        factory = self.contract('SafeProxyFactory')
        tx = factory.functions.createProxyWithNonce(
            Web3.to_checksum_address(singleton_addr),
            setup_data,
            salt_nonce,
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
        })

        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        # Extract proxy address from ProxyCreation event
        proxy_addr = None
        for log in receipt.logs:
            if len(log.topics) >= 2:
                proxy_addr = '0x' + log.topics[1].hex()[-40:]
                break

        if proxy_addr:
            self.save_deployment('SafeProxy', proxy_addr, 'Safe')
            m.print(f'Safe proxy deployed at {proxy_addr}', color='green')

        return {
            'singleton': singleton_addr,
            'factory': factory_addr,
            'proxy': proxy_addr,
            'owners': [o for o in owners],
            'threshold': threshold,
        }

    def transfer_ownership(self, new_owner):
        # Safe doesn't use Ownable - it's governed by owners/threshold
        pass

    def owner(self, contract_key=None):
        # Return proxy owners
        proxy = self.contract('SafeProxy')
        if proxy:
            return proxy.functions.getOwners().call()
        return None
