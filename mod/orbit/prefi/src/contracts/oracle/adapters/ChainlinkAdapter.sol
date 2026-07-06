// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

interface AggregatorV3Interface {
    function latestRoundData() external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    );
    function decimals() external view returns (uint8);
}

/**
 * @title ChainlinkAdapter
 * @dev Oracle adapter for Chainlink price feeds
 */
contract ChainlinkAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "Chainlink";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => address) public priceFeeds; // asset => Chainlink feed
    
    address public priceOracle;
    uint256 public constant PRICE_DECIMALS = 18;
    
    event AssetAdded(address indexed asset, address indexed priceFeed);
    event AssetRemoved(address indexed asset);
    event PriceUpdated(address indexed asset, uint256 price);
    
    constructor(address _priceOracle) {
        priceOracle = _priceOracle;
    }
    
    /**
     * @dev Add supported asset with Chainlink feed
     */
    function addAsset(address _asset, address _priceFeed) external onlyOwner {
        require(_asset != address(0), "Invalid asset");
        require(_priceFeed != address(0), "Invalid price feed");
        
        supportsAsset[_asset] = true;
        priceFeeds[_asset] = _priceFeed;
        emit AssetAdded(_asset, _priceFeed);
    }
    
    /**
     * @dev Remove supported asset
     */
    function removeAsset(address _asset) external onlyOwner {
        supportsAsset[_asset] = false;
        delete priceFeeds[_asset];
        emit AssetRemoved(_asset);
    }
    
    /**
     * @dev Get price from Chainlink feed
     */
    function getPrice(address _asset) external view override returns (uint256) {
        require(supportsAsset[_asset], "Asset not supported");
        
        address feed = priceFeeds[_asset];
        require(feed != address(0), "No price feed");
        
        AggregatorV3Interface priceFeed = AggregatorV3Interface(feed);
        
        (, int256 price, , uint256 updatedAt, ) = priceFeed.latestRoundData();
        require(price > 0, "Invalid price");
        require(updatedAt > 0, "Price not updated");
        require(block.timestamp - updatedAt < 1 hours, "Price stale");
        
        uint8 feedDecimals = priceFeed.decimals();
        
        // Normalize to 18 decimals
        if (feedDecimals < PRICE_DECIMALS) {
            return uint256(price) * (10 ** (PRICE_DECIMALS - feedDecimals));
        } else if (feedDecimals > PRICE_DECIMALS) {
            return uint256(price) / (10 ** (feedDecimals - PRICE_DECIMALS));
        }
        
        return uint256(price);
    }
    
    /**
     * @dev Update price in main oracle
     */
    function updatePrice(address _asset) external {
        require(supportsAsset[_asset], "Asset not supported");
        
        uint256 price = this.getPrice(_asset);
        
        (bool success,) = priceOracle.call(
            abi.encodeWithSignature("updatePrice(address,uint256)", _asset, price)
        );
        require(success, "Oracle update failed");
        
        emit PriceUpdated(_asset, price);
    }
}
