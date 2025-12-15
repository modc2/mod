// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/**
 * @title PaymentTokenWhitelist
 * @dev Modular whitelist for managing valid payment tokens
 * Market owner can whitelist/delist ERC20 tokens as valid currencies
 */
contract PaymentTokenWhitelist is Ownable {
    mapping(address => bool) public whitelistedTokens;
    address[] public tokenList;
    
    event TokenWhitelisted(address indexed token);
    event TokenDelisted(address indexed token);
    
    /**
     * @dev Whitelist a new ERC20 token as valid payment currency
     */
    function whitelistToken(address token) external onlyOwner {
        require(token != address(0), "Invalid token address");
        require(!whitelistedTokens[token], "Token already whitelisted");
        
        // Verify it's a valid ERC20 by checking totalSupply
        try IERC20(token).totalSupply() returns (uint256) {
            whitelistedTokens[token] = true;
            tokenList.push(token);
            emit TokenWhitelisted(token);
        } catch {
            revert("Invalid ERC20 token");
        }
    }
    
    /**
     * @dev Remove token from whitelist
     */
    function delistToken(address token) external onlyOwner {
        require(whitelistedTokens[token], "Token not whitelisted");
        
        whitelistedTokens[token] = false;
        
        // Remove from array
        for (uint256 i = 0; i < tokenList.length; i++) {
            if (tokenList[i] == token) {
                tokenList[i] = tokenList[tokenList.length - 1];
                tokenList.pop();
                break;
            }
        }
        
        emit TokenDelisted(token);
    }
    
    /**
     * @dev Check if token is whitelisted
     */
    function isTokenWhitelisted(address token) external view returns (bool) {
        return whitelistedTokens[token];
    }
    
    /**
     * @dev Get all whitelisted tokens
     */
    function getWhitelistedTokens() external view returns (address[] memory) {
        return tokenList;
    }
    
    /**
     * @dev Get count of whitelisted tokens
     */
    function getWhitelistedTokenCount() external view returns (uint256) {
        return tokenList.length;
    }
}
