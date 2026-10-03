// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @dev The staked asset of a BlocTime instance. The deployer gets the seed
///      supply; ownership is then handed to the Treasury, which is the only
///      thing that can mint — 1 token per $1 deposited. burnFrom (allowance-
///      gated, from ERC20Burnable) is how the Treasury redeems.
contract NativeToken is ERC20, ERC20Burnable, Ownable {
    constructor(uint256 initialSupply) ERC20("NativeToken", "NTV") Ownable(msg.sender) {
        _mint(msg.sender, initialSupply);
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}
