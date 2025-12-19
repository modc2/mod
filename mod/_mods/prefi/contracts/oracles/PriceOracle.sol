// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./IPriceOracleAdapter.sol";

/**
 * @title PriceOracle
 * @dev Modular price oracle with pluggable adapters
 */
contract PriceOracle is Ownable {
    
    struct PriceData {
        uint256 price;
        uint256 timestamp;
        bool isValid;
    }
    
    // Asset => Adapter => PriceData
    mapping(address => mapping(address => PriceData)) public adapterPrices;
    
    // List of active adapters
    address[] public adapters;
    mapping(address => bool) public isAdapter;
    
    // Asset => aggregated price cache
    mapping(address => PriceData) public prices;
    
    uint256 public constant MAX_PRICE_AGE = 1 hours;
    uint256 public constant MIN_ADAPTERS = 1;
    
    event AdapterAdded(address indexed adapter, string name);
    event AdapterRemoved(address indexed adapter);
    event PriceUpdated(address indexed asset, address indexed adapter, uint256 price, uint256 timestamp);
    event AggregatedPriceUpdated(address indexed asset, uint256 price, uint256 timestamp);
    
    constructor() {}
    
    /**
     * @dev Add a new oracle adapter
     */
    function addAdapter(address _adapter) external onlyOwner {
        require(_adapter != address(0), "Invalid adapter");
        require(!isAdapter[_adapter], "Adapter already exists");
        
        adapters.push(_adapter);
        isAdapter[_adapter] = true;
        
        string memory name = IPriceOracleAdapter(_adapter).adapterName();
        emit AdapterAdded(_adapter, name);
    }
    
    /**
     * @dev Remove an oracle adapter
     */
    function removeAdapter(address _adapter) external onlyOwner {
        require(isAdapter[_adapter], "Adapter does not exist");
        require(adapters.length > MIN_ADAPTERS, "Cannot remove last adapter");
        
        isAdapter[_adapter] = false;
        
        // Remove from array
        for (uint256 i = 0; i < adapters.length; i++) {
            if (adapters[i] == _adapter) {
                adapters[i] = adapters[adapters.length - 1];
                adapters.pop();
                break;
            }
        }
        
        emit AdapterRemoved(_adapter);
    }
    
    /**
     * @dev Update price from a specific adapter
     */
    function updatePrice(address _asset, uint256 _price) external {
        require(isAdapter[msg.sender], "Unauthorized adapter");
        require(_price > 0, "Invalid price");
        
        adapterPrices[_asset][msg.sender] = PriceData({
            price: _price,
            timestamp: block.timestamp,
            isValid: true
        });
        
        emit PriceUpdated(_asset, msg.sender, _price, block.timestamp);
        
        // Update aggregated price
        _updateAggregatedPrice(_asset);
    }
    
    /**
     * @dev Get aggregated price from all adapters
     */
    function getPrice(address _asset) external view returns (uint256) {
        PriceData memory data = prices[_asset];
        require(data.isValid, "Price not available");
        require(block.timestamp - data.timestamp < MAX_PRICE_AGE, "Price too stale");
        
        return data.price;
    }
    
    /**
     * @dev Get price from specific adapter
     */
    function getAdapterPrice(address _asset, address _adapter) external view returns (uint256) {
        require(isAdapter[_adapter], "Invalid adapter");
        PriceData memory data = adapterPrices[_asset][_adapter];
        require(data.isValid, "Price not available");
        
        return data.price;
    }
    
    /**
     * @dev Get all adapter addresses
     */
    function getAdapters() external view returns (address[] memory) {
        return adapters;
    }
    
    /**
     * @dev Get number of active adapters
     */
    function getAdapterCount() external view returns (uint256) {
        return adapters.length;
    }
    
    /**
     * @dev Internal function to update aggregated price
     */
    function _updateAggregatedPrice(address _asset) internal {
        uint256 sum = 0;
        uint256 count = 0;
        
        // Average prices from all valid adapters
        for (uint256 i = 0; i < adapters.length; i++) {
            PriceData memory data = adapterPrices[_asset][adapters[i]];
            
            if (data.isValid && block.timestamp - data.timestamp < MAX_PRICE_AGE) {
                sum += data.price;
                count++;
            }
        }
        
        if (count > 0) {
            uint256 avgPrice = sum / count;
            prices[_asset] = PriceData({
                price: avgPrice,
                timestamp: block.timestamp,
                isValid: true
            });
            
            emit AggregatedPriceUpdated(_asset, avgPrice, block.timestamp);
        }
    }
    
    /**
     * @dev Manual aggregation trigger (owner only)
     */
    function aggregatePrice(address _asset) external onlyOwner {
        _updateAggregatedPrice(_asset);
    }
}
