// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IOracleAdapter.sol";

/**
 * @title Chainlink Price Feed Interface
 */
interface AggregatorV3Interface {
    function decimals() external view returns (uint8);
    function latestRoundData() external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    );
}

/**
 * @title ChainlinkAdapter
 * @dev Oracle adapter for Chainlink price feeds
 */
contract ChainlinkAdapter is IOracleAdapter {
    // Mapping from token address to Chainlink price feed address
    mapping(address => address) public priceFeeds;
    
    address public owner;
    
    event PriceFeedSet(address indexed token, address indexed priceFeed);
    event PriceFeedRemoved(address indexed token);
    event ContractSetOwnerless();

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }
    
    constructor() {
        owner = msg.sender;
    }
    
    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setPriceFeed, removePriceFeed, transferOwnership.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        owner = address(0);
    }

    /**
     * @dev Set Chainlink price feed for a token
     * @param token Token address
     * @param priceFeed Chainlink price feed address
     */
    function setPriceFeed(address token, address priceFeed) external onlyOwner {
        require(token != address(0), "Invalid token");
        require(priceFeed != address(0), "Invalid price feed");
        
        priceFeeds[token] = priceFeed;
        emit PriceFeedSet(token, priceFeed);
    }
    
    /**
     * @dev Remove price feed for a token
     * @param token Token address
     */
    function removePriceFeed(address token) external onlyOwner {
        require(priceFeeds[token] != address(0), "Price feed not set");
        
        delete priceFeeds[token];
        emit PriceFeedRemoved(token);
    }
    
    /**
     * @dev Get the latest price for a token from Chainlink
     * @param token Address of the token
     * @return price Latest price
     * @return decimals Number of decimals
     * @return timestamp Last update timestamp
     */
    function getPrice(address token) external view override returns (
        uint256 price,
        uint8 decimals,
        uint256 timestamp
    ) {
        address priceFeed = priceFeeds[token];
        require(priceFeed != address(0), "Price feed not set");
        
        AggregatorV3Interface feed = AggregatorV3Interface(priceFeed);
        
        (
            /*uint80 roundId*/,
            int256 answer,
            /*uint256 startedAt*/,
            uint256 updatedAt,
            /*uint80 answeredInRound*/
        ) = feed.latestRoundData();
        
        require(answer > 0, "Invalid price");
        require(updatedAt > 0, "Price not updated");
        
        return (
            uint256(answer),
            feed.decimals(),
            updatedAt
        );
    }
    
    /**
     * @dev Check if price feed exists for a token
     * @param token Address of the token
     * @return exists True if price feed is available
     */
    function hasPriceFeed(address token) external view override returns (bool exists) {
        return priceFeeds[token] != address(0);
    }
    
    /**
     * @dev Transfer ownership
     * @param newOwner New owner address
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Invalid owner");
        owner = newOwner;
    }
}
