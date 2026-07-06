// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

/**
 * @title CoinMarketCapAdapter
 * @dev Oracle adapter for CoinMarketCap price feeds
 */
contract CoinMarketCapAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "CoinMarketCap";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => uint256) public assetIds; // CMC IDs
    
    address public priceOracle;
    
    event AssetAdded(address indexed asset, uint256 cmcId);
    event AssetRemoved(address indexed asset);
    
    constructor(address _priceOracle) {
        priceOracle = _priceOracle;
    }
    
    /**
     * @dev Add supported asset
     */
    function addAsset(address _asset, uint256 _cmcId) external onlyOwner {
        supportsAsset[_asset] = true;
        assetIds[_asset] = _cmcId;
        emit AssetAdded(_asset, _cmcId);
    }
    
    /**
     * @dev Remove supported asset
     */
    function removeAsset(address _asset) external onlyOwner {
        supportsAsset[_asset] = false;
        delete assetIds[_asset];
        emit AssetRemoved(_asset);
    }
    
    /**
     * @dev Get price (placeholder - requires off-chain oracle)
     */
    function getPrice(address _asset) external view override returns (uint256) {
        require(supportsAsset[_asset], "Asset not supported");
        // In production, this would call Chainlink or similar
        revert("Use updatePrice from off-chain oracle");
    }
    
    /**
     * @dev Update price from off-chain oracle
     */
    function updatePrice(address _asset, uint256 _price) external onlyOwner {
        require(supportsAsset[_asset], "Asset not supported");
        require(_price > 0, "Invalid price");
        
        // Forward to main oracle
        (bool success,) = priceOracle.call(
            abi.encodeWithSignature("updatePrice(address,uint256)", _asset, _price)
        );
        require(success, "Oracle update failed");
    }
}
