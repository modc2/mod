// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// The asset block. Every protocol on the canvas starts with one of these
/// feeding an `erc20` port somewhere downstream.
contract ModToken is ERC20Base, Owned {
    bool public mintable;

    constructor(
        string memory name_,
        string memory symbol_,
        uint8 decimals_,
        uint256 initialSupply,
        address mintTo,
        bool mintable_,
        address owner_
    ) ERC20Base(name_, symbol_, decimals_) Owned(owner_) {
        mintable = mintable_;
        if (initialSupply > 0) {
            _mint(mintTo == address(0) ? msg.sender : mintTo, initialSupply);
        }
    }

    function mint(address to, uint256 amount) external onlyOwner {
        require(mintable, "NOT_MINTABLE");
        _mint(to, amount);
    }

    function burn(uint256 amount) external {
        _burn(msg.sender, amount);
    }
}
