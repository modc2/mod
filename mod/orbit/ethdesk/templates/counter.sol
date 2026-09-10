// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Counter — the smallest thing worth deploying.
/// @notice Deploy this first. It proves the account, the network, the gas
/// estimate and the receipt path all work, for about 200k gas.
contract Counter {
    uint256 public value;
    address public immutable deployer;

    event Changed(address indexed by, uint256 value);

    constructor(uint256 start) {
        value = start;
        deployer = msg.sender;
    }

    function increment() external {
        value += 1;
        emit Changed(msg.sender, value);
    }

    function add(uint256 amount) external {
        value += amount;
        emit Changed(msg.sender, value);
    }

    function reset() external {
        require(msg.sender == deployer, "not the deployer");
        value = 0;
        emit Changed(msg.sender, 0);
    }
}
