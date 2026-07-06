// SPDX-License-Identifier: LGPL-3.0-only
pragma solidity ^0.8.0;

/**
 * @title SafeProxy - Generic proxy contract allows to execute all transactions applying the code of a master contract.
 * @dev Proxy contract that delegates all calls to a master contract (singleton).
 * Uses EIP-1967 storage slots for implementation address.
 */
contract SafeProxy {
    // Storage slot with the address of the current implementation.
    // This is the keccak-256 hash of "eip1967.proxy.implementation" subtracted by 1
    bytes32 private constant _IMPLEMENTATION_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    /**
     * @dev Constructor function sets address of singleton contract.
     * @param _singleton Singleton address.
     */
    constructor(address _singleton) {
        require(_singleton != address(0), "SafeProxy: Invalid singleton address");
        assembly {
            sstore(_IMPLEMENTATION_SLOT, _singleton)
        }
    }

    /**
     * @dev Fallback function forwards all transactions and returns all received return data.
     */
    fallback() external payable {
        assembly {
            let _singleton := sload(_IMPLEMENTATION_SLOT)

            // Copy msg.data. We take full control of memory in this inline assembly
            // block because it will not return to Solidity code. We overwrite the
            // Solidity scratch pad at memory position 0.
            calldatacopy(0, 0, calldatasize())

            // Call the implementation.
            // out and outsize are 0 because we don't know the size yet.
            let result := delegatecall(gas(), _singleton, 0, calldatasize(), 0, 0)

            // Copy the returned data.
            returndatacopy(0, 0, returndatasize())

            switch result
            // delegatecall returns 0 on error.
            case 0 {
                revert(0, returndatasize())
            }
            default {
                return(0, returndatasize())
            }
        }
    }

    /**
     * @dev Receive function accepts ETH.
     */
    receive() external payable {}
}
