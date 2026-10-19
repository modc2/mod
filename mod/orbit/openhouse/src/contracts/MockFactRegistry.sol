// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IFactRegistry} from "./OpenHouseProofs.sol";

/// @notice Stand-in for a STARK fact registry on test chains: the deployer
/// registers facts by hand. On a network with SHARP, point
/// OpenHouseProofs.setRegistry at the real one instead.
contract MockFactRegistry is IFactRegistry {
    address public owner;
    mapping(bytes32 => bool) public facts;

    event FactRegistered(bytes32 fact);

    constructor() {
        owner = msg.sender;
    }

    function register(bytes32 f) external {
        require(msg.sender == owner, "not owner");
        facts[f] = true;
        emit FactRegistered(f);
    }

    function isValid(bytes32 f) external view returns (bool) {
        return facts[f];
    }
}
