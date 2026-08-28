// SPDX-License-Identifier: MIT
// Counter — the hello world of contracts. Anyone can bump it.
pragma solidity ^0.8.20;

contract Counter {
    uint256 public count;
    address public owner;

    event Bumped(address indexed by, uint256 count);

    constructor(uint256 start) {
        count = start;
        owner = msg.sender;
    }

    function bump() external {
        count += 1;
        emit Bumped(msg.sender, count);
    }

    function reset() external {
        require(msg.sender == owner, "not owner");
        count = 0;
    }
}
