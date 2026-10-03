// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Stand-ins for the two ports: the asset the treasury pays out, and the BLOC
/// token whose balances decide the split. Deliberately dumb — the thing under
/// test is the treasury, not these.
contract MockToken is ERC20Base {
    constructor(string memory n, string memory s, uint8 d) ERC20Base(n, s, d) {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}
