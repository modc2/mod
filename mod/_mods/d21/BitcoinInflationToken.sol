// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./ERC20.sol";
import "./Ownable.sol";
import "./InflationCurve.sol";

contract BitcoinInflationToken is ERC20, Ownable, InflationCurve {
    
    constructor() 
        ERC20("Bitcoin Inflation Token", "BIT", 18)
        Ownable()
        InflationCurve()
    {}
    
    function distribute(address to) public onlyOwner returns (uint256) {
        uint256 reward = calculateReward(block.number);
        require(reward > 0, "No more tokens to distribute");
        
        _mint(to, reward);
        emit TokensDistributed(to, reward, block.number);
        
        return reward;
    }
    
    function revolution(address newOwner) public {
        revolution(newOwner, balanceOf[msg.sender], totalSupply);
    }
}