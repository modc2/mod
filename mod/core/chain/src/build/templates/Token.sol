// SPDX-License-Identifier: MIT
// Token — an ERC20 with a fixed supply minted to the deployer.
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract MyToken is ERC20, Ownable {
    constructor(string memory name_, string memory symbol_, uint256 supply)
        ERC20(name_, symbol_)
    {
        _mint(msg.sender, supply * 10 ** decimals());
    }

    /// Owner can mint more later — delete this for a hard-capped token.
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}
