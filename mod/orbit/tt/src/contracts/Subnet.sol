// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title Subnet
 * @dev Each subnet IS an ERC20 native token. The incentive mechanism
 *      connects to this token and mints new supply each epoch as emissions.
 *
 *      Deploy one Subnet per network. Set the incentive contract as minter
 *      so it can mint emissions. Users stake this token via StakeTime.
 */
contract Subnet is ERC20, Ownable {

    address public minter;

    event MinterSet(address indexed minter);

    constructor(
        string memory _name,
        string memory _symbol,
        uint256 initialSupply
    ) ERC20(_name, _symbol) {
        if (initialSupply > 0) {
            _mint(msg.sender, initialSupply);
        }
    }

    /// @dev Owner sets the incentive contract as the sole minter.
    function setMinter(address _minter) external onlyOwner {
        minter = _minter;
        emit MinterSet(_minter);
    }

    /// @dev Only the minter (incentive contract) can mint new tokens.
    function mint(address to, uint256 amount) external {
        require(msg.sender == minter, "not minter");
        _mint(to, amount);
    }
}
