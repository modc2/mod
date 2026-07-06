// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title PreFiToken - Reward token for the PreFi trading protocol
/// @notice Minted 1:1 per dollar of profit captured by the treasury. Burnable.
contract PreFiToken is ERC20, ERC20Burnable, Ownable {
    mapping(address => bool) public minters;
    uint256 public totalMinted;

    event MinterSet(address indexed account, bool allowed);

    constructor() ERC20("PreFi", "PREFI") {}

    modifier onlyMinter() {
        require(minters[msg.sender], "not minter");
        _;
    }

    function mint(address to, uint256 amount) external onlyMinter {
        totalMinted += amount;
        _mint(to, amount);
    }

    function setMinter(address account, bool allowed) external onlyOwner {
        minters[account] = allowed;
        emit MinterSet(account, allowed);
    }
}
