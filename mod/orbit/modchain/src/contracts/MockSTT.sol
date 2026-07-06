// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @dev Test-only ERC20 standing in for the real StakeTime token.
contract MockSTT is ERC20 {
    constructor() ERC20("Mock StakeTime", "STT") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
