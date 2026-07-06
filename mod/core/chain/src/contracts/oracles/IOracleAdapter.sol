// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title IOracleAdapter
 * @dev Interface for oracle price feed adapters
 * Standardizes price fetching across different oracle providers (Chainlink, Pyth, etc.)
 */
interface IOracleAdapter {
    /**
     * @dev Get the latest price for a token
     * @param token Address of the token to get price for
     * @return price Latest price with decimals (e.g., 8 decimals for USD)
     * @return decimals Number of decimals in the price
     * @return timestamp Timestamp of the price update
     */
    function getPrice(address token) external view returns (
        uint256 price,
        uint8 decimals,
        uint256 timestamp
    );

    /**
     * @dev Check if price feed exists for a token
     * @param token Address of the token
     * @return exists True if price feed is available
     */
    function hasPriceFeed(address token) external view returns (bool exists);
}
