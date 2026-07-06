// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

/**
 * @title CoinGeckoAdapter
 * @dev Oracle adapter for CoinGecko price feeds
 */
contract CoinGeckoAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "CoinGecko";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => string) public assetIds; // CoinGecko IDs
    
    address public priceOracle;
    
    event AssetAdded(address indexed asset, string coinGeckoId);
    event AssetRemoved(address indexed asset);
    
    constructor(address _priceOracle) {
        priceOracle = _priceOracle;
    }
    
    /**
     * @dev Add supported asset
     */
    function addAsset(address _asset, string memory _coinGeckoId) external onlyOwner {
        supportsAsset[_asset] = true;
        assetIds[_asset] = _coinGeckoId;
        emit AssetAdded(_asset, _coinGeckoId);
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
