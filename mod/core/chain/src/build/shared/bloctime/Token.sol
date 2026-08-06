// SPDX-License-Identifier: MIT
// Token — the ERC20 BlocTime stakes. Whole supply minted to the deployer.
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract Token is ERC20 {
    constructor(string memory name_, string memory symbol_, uint256 supply)
        ERC20(name_, symbol_)
    {
        _mint(msg.sender, supply);
    }
}
