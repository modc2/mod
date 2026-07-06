// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

/**
 * @title BinanceAdapter
 * @dev Oracle adapter for Binance Oracle (off-chain price updates)
 */
contract BinanceAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "Binance";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => string) public symbols; // asset => Binance symbol
    mapping(address => uint256) public prices; // Cached prices
    mapping(address => uint256) public lastUpdate;
    
    address public priceOracle;
    address public priceUpdater; // Authorized off-chain updater
    uint256 public constant MAX_PRICE_AGE = 5 minutes;
    
    event AssetAdded(address indexed asset, string symbol);
    event AssetRemoved(address indexed asset);
    event PriceUpdated(address indexed asset, uint256 price, uint256 timestamp);
    event UpdaterChanged(address indexed oldUpdater, address indexed newUpdater);
    
    constructor(address _priceOracle, address _priceUpdater) {
        priceOracle = _priceOracle;
        priceUpdater = _priceUpdater;
    }
    
    modifier onlyUpdater() {
        require(msg.sender == priceUpdater || msg.sender == owner(), "Not authorized");
        _;
    }
    
    /**
     * @dev Add supported asset
     */
    function addAsset(address _asset, string memory _symbol) external onlyOwner {
        require(_asset != address(0), "Invalid asset");
        require(bytes(_symbol).length > 0, "Invalid symbol");
        
        supportsAsset[_asset] = true;
        symbols[_asset] = _symbol;
        emit AssetAdded(_asset, _symbol);
    }
    
    /**
     * @dev Remove supported asset
     */
    function removeAsset(address _asset) external onlyOwner {
        supportsAsset[_asset] = false;
        delete symbols[_asset];
        delete prices[_asset];
        delete lastUpdate[_asset];
        emit AssetRemoved(_asset);
    }
    
    /**
     * @dev Get cached price
     */
    function getPrice(address _asset) external view override returns (uint256) {
        require(supportsAsset[_asset], "Asset not supported");
        require(prices[_asset] > 0, "Price not available");
        require(block.timestamp - lastUpdate[_asset] < MAX_PRICE_AGE, "Price stale");
        
        return prices[_asset];
    }
    
    /**
     * @dev Update price from off-chain oracle (authorized updater only)
     */
    function updatePrice(address _asset, uint256 _price) external onlyUpdater {
        require(supportsAsset[_asset], "Asset not supported");
        require(_price > 0, "Invalid price");
        
        prices[_asset] = _price;
        lastUpdate[_asset] = block.timestamp;
        
        emit PriceUpdated(_asset, _price, block.timestamp);
        
        // Forward to main oracle
        (bool success,) = priceOracle.call(
            abi.encodeWithSignature("updatePrice(address,uint256)", _asset, _price)
        );
        require(success, "Oracle update failed");
    }
    
    /**
     * @dev Batch update prices
     */
    function batchUpdatePrices(
        address[] calldata _assets,
        uint256[] calldata _prices
    ) external onlyUpdater {
        require(_assets.length == _prices.length, "Length mismatch");
        
        for (uint256 i = 0; i < _assets.length; i++) {
            if (supportsAsset[_assets[i]] && _prices[i] > 0) {
                prices[_assets[i]] = _prices[i];
                lastUpdate[_assets[i]] = block.timestamp;
                
                emit PriceUpdated(_assets[i], _prices[i], block.timestamp);
                
                (bool success,) = priceOracle.call(
                    abi.encodeWithSignature("updatePrice(address,uint256)", _assets[i], _prices[i])
                );
                require(success, "Oracle update failed");
            }
        }
    }
    
    /**
     * @dev Change price updater
     */
    function setPriceUpdater(address _newUpdater) external onlyOwner {
        require(_newUpdater != address(0), "Invalid updater");
        address oldUpdater = priceUpdater;
        priceUpdater = _newUpdater;
        emit UpdaterChanged(oldUpdater, _newUpdater);
    }
    
    /**
     * @dev Get price info
     */
    function getPriceInfo(address _asset) external view returns (
        uint256 price,
        uint256 timestamp,
        bool isStale
    ) {
        price = prices[_asset];
        timestamp = lastUpdate[_asset];
        isStale = block.timestamp - timestamp >= MAX_PRICE_AGE;
    }
}
