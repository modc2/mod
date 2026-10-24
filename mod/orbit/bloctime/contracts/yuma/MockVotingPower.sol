// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @dev Test stand-in for BlocTime.getVotingPower — lets fixtures pin exact stakes.
contract MockVotingPower {
    mapping(address => uint256) public power;

    function set(address who, uint256 amount) external {
        power[who] = amount;
    }

    function getVotingPower(address who) external view returns (uint256) {
        return power[who];
    }
}
