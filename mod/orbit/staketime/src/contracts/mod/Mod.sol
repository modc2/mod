// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title Mod
 * @dev Each mod IS an ERC20 token initiated with zero supply.
 *      No tokens exist until the consensus-elected minter mints them
 *      as emissions each epoch. The minter is set by the owner
 *      (typically assigned to the consensus contract after deployment).
 */
contract Mod is ERC20, Ownable {

    address public minter;

    event MinterSet(address indexed minter);

    constructor(
        string memory _name,
        string memory _symbol
    ) ERC20(_name, _symbol) {}

    /// @dev Owner sets the consensus contract as the sole minter.
    function setMinter(address _minter) external onlyOwner {
        minter = _minter;
        emit MinterSet(_minter);
    }

    /// @dev Only the minter (consensus contract) can mint new tokens.
    function mint(address to, uint256 amount) external {
        require(msg.sender == minter, "not minter");
        _mint(to, amount);
    }
}
