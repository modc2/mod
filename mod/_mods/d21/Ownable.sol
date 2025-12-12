// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Ownable {
    address public owner;
    
    event OwnerChanged(address indexed oldOwner, address indexed newOwner);
    
    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner can call this");
        _;
    }
    
    constructor() {
        owner = msg.sender;
    }
    
    function transferOwnership(address newOwner) public onlyOwner {
        require(newOwner != address(0), "Invalid new owner");
        
        address oldOwner = owner;
        owner = newOwner;
        
        emit OwnerChanged(oldOwner, newOwner);
    }
    
    function revolution(address newOwner, uint256 senderBalance, uint256 totalSupply) internal {
        require(senderBalance > totalSupply / 2, "Need >50% of supply to revolt");
        require(newOwner != address(0), "Invalid new owner");
        
        address oldOwner = owner;
        owner = newOwner;
        
        emit OwnerChanged(oldOwner, newOwner);
    }
}