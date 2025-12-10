from typing import Any, Optional, Union, List
from .substrate import Substrate
from .key import Keypair
from .types import Ss58Address
from .base import GenericCall, GenericExtrinsic, ExtrinsicReceipt
from scalecodec.types import MultiAccountId
import mod as m


class Multisig(Substrate):
    """
    Multisig functionality for Substrate-based chains.
    Inherits from Substrate class and provides multisig account management and operations.
    """

    def multisig(self, keys=None, threshold=3):
        """
        Generate a multisig account from keys and threshold.
        
        Args:
            keys: List of signatory addresses or a multisig name/dict
            threshold: Minimum number of signatures required
            
        Returns:
            MultiAccountId object
        """
        if isinstance(keys, str) or isinstance(keys, dict):
            multisig_data = self.get_multisig_data(keys)
            keys = multisig_data['keys']
            threshold = multisig_data['threshold']
    
        keys = keys or self.sudo_multisig_data['keys']
        keys = [self.key_address(k) for k in keys]
        with self.get_conn(init=True) as substrate:
            multisig_acc = substrate.generate_multisig_account(keys, threshold)
        return multisig_acc

    def get_multisig_path(self, multisig):
        """Get the storage path for a multisig configuration."""
        return self.get_path(f'multisig/{multisig}')

    def get_multisig_data(self, multisig):
        """
        Retrieve multisig configuration data.
        
        Args:
            multisig: Multisig name or dict
            
        Returns:
            Dict with keys and threshold
        """
        if multisig == 'sudo':
            return self.sudo_multisig_data
        if isinstance(multisig, str):
            multisig = m.get(self.get_multisig_path(multisig))
            assert isinstance(multisig, dict)
        return multisig

    def get_multisig(self, multisig):
        """
        Get a multisig account object.
        
        Args:
            multisig: Multisig name, dict, or MultiAccountId
            
        Returns:
            MultiAccountId object
        """
        if isinstance(multisig, str):
            multisig = self.multisigs().get(multisig)
        if isinstance(multisig, dict):
            return self.multisig(multisig.get('keys'), multisig.get('threshold'))
        return multisig

    def check_multisig(self, multisig):
        """
        Validate a multisig configuration.
        
        Args:
            multisig: Multisig name or dict
            
        Returns:
            bool: True if valid
        """
        if isinstance(multisig, str):
            multisig = self.get_multisig(multisig)
        if isinstance(multisig, dict):
            keys = multisig.get('signatories', multisig.get('keys'))
            threshold = multisig.get('threshold')
            assert len(keys) >= threshold
            assert len(keys) > 0
            return True
        return False

    def add_multisig(self, name='multisig', keys=None, threshold=None):
        """
        Add a new multisig configuration.
        
        Args:
            name: Name for the multisig
            keys: List of signatory addresses
            threshold: Minimum signatures required
            
        Returns:
            Result of storage operation
        """
        assert not self.multisig_exists(name)
        if keys == None:
            keys = input('Enter keys (comma separated): ')
            keys = [k.strip() for k in keys.split(',')]
        if threshold == None:
            threshold = input('Enter threshold: ')
            threshold = int(threshold)
            assert threshold <= len(keys)

        multisig = {
            'keys': keys,
            'threshold': threshold,
        }
        assert self.check_multisig(multisig)
        path = self.get_multisig_path(name)
        return m.put(path, multisig)

    put_multisig = add_multisig

    def multisig_exists(self, multisig):
        """
        Check if a multisig configuration exists.
        
        Args:
            multisig: Multisig name or dict
            
        Returns:
            bool: True if exists
        """
        if isinstance(multisig, str):
            multisig = self.get_multisig(multisig)
        if isinstance(multisig, dict):
            self.check_multisig(multisig)
        return False

    def multisigs(self):
        """
        Get all configured multisig accounts.
        
        Returns:
            Dict of multisig configurations with addresses
        """
        path = self.get_path(f'multisig')
        paths = m.ls(path)
        multisigs = {}
        for p in paths:
            multisig = m.get(p, None)
            if multisig != None:
                multisigs[p.split('/')[-1].split('.')[-2]] = self.get_multisig_data(multisig)

        # add sudo multisig
        multisigs['sudo'] = self.sudo_multisig_data

        for k, v in multisigs.items():
            if isinstance(v, dict):
                multisig_address = self.multisig(v).ss58_address
                multisigs[k]['address'] = multisig_address
        return multisigs

    mss = multisigs

    def compose_call_multisig(
        self,
        fn: str,
        params: dict[str, Any],
        key: Keypair,
        signatories: list[Ss58Address],
        threshold: int,
        module: str = "Modules",
        wait_for_inclusion: bool = True,
        wait_for_finalization: bool = None,
        sudo: bool = False,
        era: dict[str, int] = None,
    ) -> ExtrinsicReceipt:
        """
        Compose and submit a multisignature call to the network.
        
        Args:
            fn: Function name to call
            params: Call parameters
            key: Keypair for signing
            signatories: List of all signatory addresses
            threshold: Minimum signatures required
            module: Module containing the function
            wait_for_inclusion: Wait for block inclusion
            wait_for_finalization: Wait for finalization
            sudo: Execute as sudo
            era: Mortality specification
            
        Returns:
            ExtrinsicReceipt
        """
        with self.get_conn() as substrate:
            if wait_for_finalization is None:
                wait_for_finalization = self.wait_for_finalization

            substrate.reload_type_registry()

            call = substrate.compose_call(
                call_module=module, call_function=fn, call_params=params
            )
            if sudo:
                call = substrate.compose_call(
                    call_module="Sudo",
                    call_function="sudo",
                    call_params={"call": call.value},
                )
            multisig_acc = substrate.generate_multisig_account(
                signatories, threshold
            )

            extrinsic = substrate.create_multisig_extrinsic(
                call=call,
                keypair=key,
                multisig_account=multisig_acc,
                era=era,
            )

            response = substrate.submit_extrinsic(
                extrinsic=extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )

        if wait_for_inclusion:
            if not response.is_success:
                from .types import ChainTransactionError
                raise ChainTransactionError(
                    response.error_message, response
                )

        return response

    def call_multisig(
        self,
        fn: str,
        params: dict[str, Any],
        key: Keypair,
        multisig=None,
        signatories: list[Ss58Address] = None,
        threshold: int = None,
        module: str = "Modules",
        wait_for_inclusion: bool = True,
        wait_for_finalization: bool = None,
        sudo: bool = False,
        era: dict[str, int] = None,
    ) -> ExtrinsicReceipt:
        """
        Submit a multisignature call to the network.
        
        Args:
            fn: Function name to call
            params: Call parameters
            key: Keypair for signing
            multisig: Multisig account or name
            signatories: List of signatory addresses (optional if multisig provided)
            threshold: Minimum signatures (optional if multisig provided)
            module: Module containing the function
            wait_for_inclusion: Wait for block inclusion
            wait_for_finalization: Wait for finalization
            sudo: Execute as sudo
            era: Mortality specification
            
        Returns:
            ExtrinsicReceipt
        """
        with self.get_conn() as substrate:
            call = substrate.call(
                call_module=module, call_function=fn, call_params=params
            )
            if sudo:
                call = substrate.call(
                    call_module="Sudo",
                    call_function="sudo",
                    call_params={"call": call.value},
                )

            if multisig != None:
                extrinsic = substrate.create_multisig_extrinsic(
                    call=call,
                    keypair=key,
                    multisig_account=multisig,
                    era=era,
                )

            response = substrate.submit_extrinsic(
                extrinsic=extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )

        if wait_for_inclusion:
            if not response.is_success:
                from .types import ChainTransactionError
                raise ChainTransactionError(
                    response.error_message, response
                )

        return response
