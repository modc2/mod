// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title IPriceOracleAdapter
 * @dev Interface for modular price oracle adapters
 */
interface IPriceOracleAdapter {
    /**
     * @dev Get price for an asset
     * @param _asset Address of the asset
     * @return price Price in USD with 18 decimals
     */
    function getPrice(address _asset) external view returns (uint256 price);
    
    /**
     * @dev Check if adapter supports an asset
     * @param _asset Address of the asset
     * @return supported True if asset is supported
     */
    function supportsAsset(address _asset) external view returns (bool supported);
    
    /**
     * @dev Get adapter name/identifier
     * @return name Name of the adapter
     */
    function adapterName() external view returns (string memory name);
}
