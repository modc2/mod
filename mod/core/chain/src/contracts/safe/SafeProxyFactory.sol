// SPDX-License-Identifier: LGPL-3.0-only
pragma solidity ^0.8.0;

import "./SafeProxy.sol";
import "./ISafe.sol";

/**
 * @title SafeProxyFactory - Factory for creating Safe proxy instances
 * @dev Allows creation of Safe proxy instances using CREATE2 for deterministic addresses
 */
contract SafeProxyFactory {
    event ProxyCreation(address indexed proxy, address singleton);

    /**
     * @dev Allows to retrieve the creation code used for the Proxy deployment.
     * @return The creation code for the Proxy.
     */
    function proxyCreationCode() public pure returns (bytes memory) {
        return type(SafeProxy).creationCode;
    }

    /**
     * @dev Allows to create new proxy contact using CREATE2.
     * @param _singleton Address of singleton contract.
     * @param initializer Payload for message call sent to new proxy contract.
     * @param saltNonce Nonce that will be used to generate the salt to calculate the address of the new proxy contract.
     */
    function createProxyWithNonce(
        address _singleton,
        bytes memory initializer,
        uint256 saltNonce
    ) public returns (SafeProxy proxy) {
        bytes32 salt = keccak256(abi.encodePacked(keccak256(initializer), saltNonce));
        bytes memory deploymentData = abi.encodePacked(type(SafeProxy).creationCode, uint256(uint160(_singleton)));

        assembly {
            proxy := create2(0x0, add(0x20, deploymentData), mload(deploymentData), salt)
        }
        require(address(proxy) != address(0), "SafeProxyFactory: Create2 call failed");

        if (initializer.length > 0) {
            assembly {
                if eq(call(gas(), proxy, 0, add(initializer, 0x20), mload(initializer), 0, 0), 0) {
                    revert(0, 0)
                }
            }
        }
        emit ProxyCreation(address(proxy), _singleton);
    }

    /**
     * @dev Allows to create new proxy contact and execute a message call to the new proxy within one transaction.
     * @param _singleton Address of singleton contract.
     * @param initializer Payload for message call sent to new proxy contract.
     */
    function createProxy(address _singleton, bytes memory initializer) public returns (SafeProxy proxy) {
        proxy = new SafeProxy(_singleton);
        if (initializer.length > 0) {
            assembly {
                if eq(call(gas(), proxy, 0, add(initializer, 0x20), mload(initializer), 0, 0), 0) {
                    revert(0, 0)
                }
            }
        }
        emit ProxyCreation(address(proxy), _singleton);
    }

    /**
     * @dev Returns address of new proxy created by CREATE2.
     * @param _singleton Address of singleton contract.
     * @param initializer Payload for message call sent to new proxy contract.
     * @param saltNonce Nonce that will be used to generate the salt.
     * @return Predicted address of new proxy.
     */
    function calculateCreateProxyWithNonceAddress(
        address _singleton,
        bytes memory initializer,
        uint256 saltNonce
    ) external view returns (address) {
        bytes32 salt = keccak256(abi.encodePacked(keccak256(initializer), saltNonce));
        bytes memory deploymentData = abi.encodePacked(type(SafeProxy).creationCode, uint256(uint160(_singleton)));
        bytes32 hash = keccak256(
            abi.encodePacked(bytes1(0xff), address(this), salt, keccak256(deploymentData))
        );
        return address(uint160(uint256(hash)));
    }
}
