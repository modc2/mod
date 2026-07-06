// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../oracles/IOracleAdapter.sol";

/**
 * @title TokenGate
 * @dev Enhanced TokenGate with oracle adapter registry per token
 * Allows registering different oracle adapters for different tokens
 * Memory optimized: efficient token deletion with complete storage cleanup
 * Integrated with Market.sol for modular token management
 */
contract TokenGate is Ownable {
    // Default oracle adapter for price feeds
    IOracleAdapter public defaultOracle;
    
    // Token-specific oracle adapters (token => oracle)
    mapping(address => IOracleAdapter) private tokenOracles;
    
    // Whitelisted tokens
    mapping(address => bool) private whitelistedTokens;
    
    // Token list with index mapping for O(1) deletion
    address[] private tokenList;
    mapping(address => uint256) private tokenIndex;
    
    // Events
    event DefaultOracleUpdated(address indexed newOracle);
    event TokenOracleRegistered(address indexed token, address indexed oracle);
    event TokenOracleRemoved(address indexed token);
    event TokenWhitelisted(address indexed token);
    event TokenDelisted(address indexed token);
    event ContractSetOwnerless();

    constructor(address _defaultOracle) {
        require(_defaultOracle != address(0), "Invalid oracle");
        defaultOracle = IOracleAdapter(_defaultOracle);
    }
    
    // ========== ADMIN ==========

    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setDefaultOracle, registerTokenOracle, removeTokenOracle, whitelistToken, batchWhitelistTokens, delistToken.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    // ========== ORACLE MANAGEMENT ==========

    /**
     * @dev Update default oracle adapter
     */
    function setDefaultOracle(address _oracle) external onlyOwner {
        require(_oracle != address(0), "Invalid oracle");
        defaultOracle = IOracleAdapter(_oracle);
        emit DefaultOracleUpdated(_oracle);
    }
    
    /**
     * @dev Register oracle adapter for specific token
     * @param token Token address
     * @param oracle Oracle adapter address for this token
     */
    function registerTokenOracle(address token, address oracle) external onlyOwner {
        require(token != address(0), "Invalid token");
        require(oracle != address(0), "Invalid oracle");
        
        tokenOracles[token] = IOracleAdapter(oracle);
        emit TokenOracleRegistered(token, oracle);
    }
    
    /**
     * @dev Remove token-specific oracle (falls back to default)
     * @param token Token address
     */
    function removeTokenOracle(address token) external onlyOwner {
        require(address(tokenOracles[token]) != address(0), "No token oracle set");
        delete tokenOracles[token];
        emit TokenOracleRemoved(token);
    }
    
    /**
     * @dev Get oracle for a specific token (token-specific or default)
     */
    function getOracleForToken(address token) public view returns (IOracleAdapter) {
        IOracleAdapter tokenOracle = tokenOracles[token];
        return address(tokenOracle) != address(0) ? tokenOracle : defaultOracle;
    }
    
    // ========== TOKEN WHITELIST MANAGEMENT ==========
    
    /**
     * @dev Whitelist a token (must have oracle price feed)
     */
    function whitelistToken(address token) external onlyOwner {
        require(token != address(0), "Invalid token");
        require(!whitelistedTokens[token], "Already whitelisted");
        
        IOracleAdapter oracle = getOracleForToken(token);
        require(oracle.hasPriceFeed(token), "No oracle price feed");
        
        whitelistedTokens[token] = true;
        tokenIndex[token] = tokenList.length;
        tokenList.push(token);
        emit TokenWhitelisted(token);
    }
    
    /**
     * @dev Batch whitelist tokens
     */
    function batchWhitelistTokens(address[] calldata tokens) external onlyOwner {
        for (uint256 i = 0; i < tokens.length; i++) {
            require(tokens[i] != address(0), "Invalid token");
            
            if (!whitelistedTokens[tokens[i]]) {
                IOracleAdapter oracle = getOracleForToken(tokens[i]);
                require(oracle.hasPriceFeed(tokens[i]), "No oracle price feed");
                
                whitelistedTokens[tokens[i]] = true;
                tokenIndex[tokens[i]] = tokenList.length;
                tokenList.push(tokens[i]);
                emit TokenWhitelisted(tokens[i]);
            }
        }
    }
    
    /**
     * @dev Delist a token from whitelist - COMPLETE STORAGE CLEANUP
     * Removes all storage associated with the token for maximum memory efficiency
     */
    function delistToken(address token) external onlyOwner {
        require(whitelistedTokens[token], "Not whitelisted");
        
        // Clear whitelist status
        delete whitelistedTokens[token];
        
        // Efficient removal from tokenList using swap-and-pop
        uint256 index = tokenIndex[token];
        uint256 lastIndex = tokenList.length - 1;
        
        if (index != lastIndex) {
            address lastToken = tokenList[lastIndex];
            tokenList[index] = lastToken;
            tokenIndex[lastToken] = index;
        }
        
        tokenList.pop();
        
        // Clear token index mapping
        delete tokenIndex[token];
        
        // Clear token-specific oracle if exists (complete cleanup)
        if (address(tokenOracles[token]) != address(0)) {
            delete tokenOracles[token];
        }
        
        emit TokenDelisted(token);
    }
    
    // ========== VIEW FUNCTIONS ==========
    
    function getTokenList() external view returns (address[] memory) {
        return tokenList;
    }
    
    function isTokenWhitelisted(address token) public view returns (bool) {
        return whitelistedTokens[token];
    }
    
    /**
     * @dev Get current price for a token from its registered oracle
     */
    function getTokenPrice(address token) external view returns (uint256 price, uint8 decimals, uint256 timestamp) {
        require(whitelistedTokens[token], "Token not whitelisted");
        IOracleAdapter oracle = getOracleForToken(token);
        return oracle.getPrice(token);
    }
}
