// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

interface IAave {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
}

interface ICompound {
    function supply(address asset, uint256 amount) external;
    function withdraw(address asset, uint256 amount) external;
}

contract LendingAggregator is Ownable, ReentrancyGuard {
    mapping(string => address) public protocols;
    mapping(address => mapping(address => uint256)) public userDeposits;
    
    event Deposit(address indexed user, address indexed token, uint256 amount, string protocol);
    event Withdraw(address indexed user, address indexed token, uint256 amount, string protocol);
    
    constructor() {
        // Base mainnet addresses - update with actual addresses
        protocols["aave"] = address(0); // Aave V3 Pool on Base
        protocols["compound"] = address(0); // Compound on Base
    }
    
    function depositToProtocol(address token, uint256 amount, string memory protocol) external nonReentrant {
        require(amount > 0, "Amount must be greater than 0");
        require(protocols[protocol] != address(0), "Protocol not supported");
        
        IERC20(token).transferFrom(msg.sender, address(this), amount);
        IERC20(token).approve(protocols[protocol], amount);
        
        if (keccak256(bytes(protocol)) == keccak256(bytes("aave"))) {
            IAave(protocols[protocol]).supply(token, amount, msg.sender, 0);
        } else if (keccak256(bytes(protocol)) == keccak256(bytes("compound"))) {
            ICompound(protocols[protocol]).supply(token, amount);
        }
        
        userDeposits[msg.sender][token] += amount;
        emit Deposit(msg.sender, token, amount, protocol);
    }
    
    function withdrawFromProtocol(address token, uint256 amount, string memory protocol) external nonReentrant {
        require(amount > 0, "Amount must be greater than 0");
        require(userDeposits[msg.sender][token] >= amount, "Insufficient balance");
        
        if (keccak256(bytes(protocol)) == keccak256(bytes("aave"))) {
            IAave(protocols[protocol]).withdraw(token, amount, msg.sender);
        } else if (keccak256(bytes(protocol)) == keccak256(bytes("compound"))) {
            ICompound(protocols[protocol]).withdraw(token, amount);
        }
        
        userDeposits[msg.sender][token] -= amount;
        emit Withdraw(msg.sender, token, amount, protocol);
    }
    
    function updateProtocol(string memory name, address protocolAddress) external onlyOwner {
        protocols[name] = protocolAddress;
    }
}