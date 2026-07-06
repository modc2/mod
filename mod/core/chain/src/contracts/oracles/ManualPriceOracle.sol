// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IOracleAdapter.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title ManualPriceOracle
 * @dev Oracle adapter where owner manually sets token prices
 * Allows for daily or on-demand price updates without external oracle dependencies
 */
contract ManualPriceOracle is IOracleAdapter, Ownable {
    struct PriceData {
        uint256 price;
        uint8 decimals;
        uint256 timestamp;
        bool exists;
    }
    
    // Mapping from token address to price data
    mapping(address => PriceData) public prices;
    
    event PriceUpdated(address indexed token, uint256 price, uint8 decimals, uint256 timestamp);
    event PriceRemoved(address indexed token);
    event ContractSetOwnerless();

    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setPrice, batchSetPrices, removePrice.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    /**
     * @dev Set price for a token (owner only)
     * @param token Token address
     * @param price Price value
     * @param decimals Number of decimals in the price (typically 8 for USD)
     */
    function setPrice(address token, uint256 price, uint8 decimals) external onlyOwner {
        require(token != address(0), "Invalid token");
        require(price > 0, "Invalid price");
        
        prices[token] = PriceData({
            price: price,
            decimals: decimals,
            timestamp: block.timestamp,
            exists: true
        });
        
        emit PriceUpdated(token, price, decimals, block.timestamp);
    }
    
    /**
     * @dev Batch set prices for multiple tokens
     * @param tokens Array of token addresses
     * @param priceValues Array of price values
     * @param decimalsArray Array of decimals for each price
     */
    function batchSetPrices(
        address[] calldata tokens,
        uint256[] calldata priceValues,
        uint8[] calldata decimalsArray
    ) external onlyOwner {
        require(
            tokens.length == priceValues.length && tokens.length == decimalsArray.length,
            "Array length mismatch"
        );
        
        for (uint256 i = 0; i < tokens.length; i++) {
            require(tokens[i] != address(0), "Invalid token");
            require(priceValues[i] > 0, "Invalid price");
            
            prices[tokens[i]] = PriceData({
                price: priceValues[i],
                decimals: decimalsArray[i],
                timestamp: block.timestamp,
                exists: true
            });
            
            emit PriceUpdated(tokens[i], priceValues[i], decimalsArray[i], block.timestamp);
        }
    }
    
    /**
     * @dev Remove price for a token
     * @param token Token address
     */
    function removePrice(address token) external onlyOwner {
        require(prices[token].exists, "Price not set");
        
        delete prices[token];
        emit PriceRemoved(token);
    }
    
    /**
     * @dev Get the latest price for a token
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
        PriceData memory data = prices[token];
        require(data.exists, "Price not set");
        
        return (data.price, data.decimals, data.timestamp);
    }
    
    /**
     * @dev Check if price feed exists for a token
     * @param token Address of the token
     * @return exists True if price is set
     */
    function hasPriceFeed(address token) external view override returns (bool exists) {
        return prices[token].exists;
    }
}
