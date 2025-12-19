// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title Oracle
 * @dev Aggregator oracle that manages multiple approved oracle sources
 */
contract Oracle is Ownable {
    
    struct OracleData {
        address oracleAddress;
        string name;
        bool isActive;
        uint256 weight; // Weight for weighted average (basis points, 10000 = 100%)
    }
    
    struct PriceData {
        uint256 price;
        uint256 timestamp;
        uint256 confidence; // Confidence score based on oracle consensus
    }
    
    // Asset => aggregated price data
    mapping(address => PriceData) public prices;
    
    // List of approved oracles
    address[] public approvedOracles;
    mapping(address => OracleData) public oracleInfo;
    mapping(address => bool) public isApprovedOracle;
    
    // Asset => Oracle => Price
    mapping(address => mapping(address => uint256)) public oraclePrices;
    mapping(address => mapping(address => uint256)) public oracleTimestamps;
    
    uint256 public constant MAX_PRICE_AGE = 1 hours;
    uint256 public constant MIN_ORACLES_REQUIRED = 2;
    uint256 public constant CONFIDENCE_THRESHOLD = 9500; // 95% agreement required
    
    event OracleApproved(address indexed oracle, string name, uint256 weight);
    event OracleRemoved(address indexed oracle);
    event OracleUpdated(address indexed oracle, string name, uint256 weight, bool isActive);
    event PriceUpdated(address indexed asset, address indexed oracle, uint256 price, uint256 timestamp);
    event AggregatedPriceUpdated(address indexed asset, uint256 price, uint256 confidence, uint256 timestamp);
    
    constructor() {}
    
    /**
     * @dev Approve a new oracle source
     */
    function approveOracle(address _oracle, string memory _name, uint256 _weight) external onlyOwner {
        require(_oracle != address(0), "Invalid oracle address");
        require(!isApprovedOracle[_oracle], "Oracle already approved");
        require(_weight > 0 && _weight <= 10000, "Invalid weight");
        
        approvedOracles.push(_oracle);
        oracleInfo[_oracle] = OracleData({
            oracleAddress: _oracle,
            name: _name,
            isActive: true,
            weight: _weight
        });
        isApprovedOracle[_oracle] = true;
        
        emit OracleApproved(_oracle, _name, _weight);
    }
    
    /**
     * @dev Remove an approved oracle
     */
    function removeOracle(address _oracle) external onlyOwner {
        require(isApprovedOracle[_oracle], "Oracle not approved");
        require(approvedOracles.length > MIN_ORACLES_REQUIRED, "Cannot remove - minimum oracles required");
        
        isApprovedOracle[_oracle] = false;
        oracleInfo[_oracle].isActive = false;
        
        // Remove from array
        for (uint256 i = 0; i < approvedOracles.length; i++) {
            if (approvedOracles[i] == _oracle) {
                approvedOracles[i] = approvedOracles[approvedOracles.length - 1];
                approvedOracles.pop();
                break;
            }
        }
        
        emit OracleRemoved(_oracle);
    }
    
    /**
     * @dev Update oracle configuration
     */
    function updateOracle(address _oracle, string memory _name, uint256 _weight, bool _isActive) external onlyOwner {
        require(isApprovedOracle[_oracle], "Oracle not approved");
        require(_weight > 0 && _weight <= 10000, "Invalid weight");
        
        oracleInfo[_oracle].name = _name;
        oracleInfo[_oracle].weight = _weight;
        oracleInfo[_oracle].isActive = _isActive;
        
        emit OracleUpdated(_oracle, _name, _weight, _isActive);
    }
    
    /**
     * @dev Update price from an approved oracle
     */
    function updatePrice(address _asset, uint256 _price) external {
        require(isApprovedOracle[msg.sender], "Unauthorized oracle");
        require(oracleInfo[msg.sender].isActive, "Oracle not active");
        require(_price > 0, "Invalid price");
        
        oraclePrices[_asset][msg.sender] = _price;
        oracleTimestamps[_asset][msg.sender] = block.timestamp;
        
        emit PriceUpdated(_asset, msg.sender, _price, block.timestamp);
        
        // Trigger aggregation
        _aggregatePrice(_asset);
    }
    
    /**
     * @dev Get aggregated price for an asset
     */
    function getPrice(address _asset) external view returns (uint256) {
        PriceData memory data = prices[_asset];
        require(data.price > 0, "Price not available");
        require(block.timestamp - data.timestamp < MAX_PRICE_AGE, "Price too stale");
        require(data.confidence >= CONFIDENCE_THRESHOLD, "Low confidence price");
        
        return data.price;
    }
    
    /**
     * @dev Get price with metadata
     */
    function getPriceWithMetadata(address _asset) external view returns (
        uint256 price,
        uint256 timestamp,
        uint256 confidence,
        uint256 oracleCount
    ) {
        PriceData memory data = prices[_asset];
        uint256 count = _getActiveOracleCount(_asset);
        return (data.price, data.timestamp, data.confidence, count);
    }
    
    /**
     * @dev Get price from specific oracle
     */
    function getOraclePrice(address _asset, address _oracle) external view returns (uint256 price, uint256 timestamp) {
        require(isApprovedOracle[_oracle], "Oracle not approved");
        return (oraclePrices[_asset][_oracle], oracleTimestamps[_asset][_oracle]);
    }
    
    /**
     * @dev Get all approved oracles
     */
    function getApprovedOracles() external view returns (address[] memory) {
        return approvedOracles;
    }
    
    /**
     * @dev Get active oracle count for asset
     */
    function _getActiveOracleCount(address _asset) internal view returns (uint256) {
        uint256 count = 0;
        for (uint256 i = 0; i < approvedOracles.length; i++) {
            address oracle = approvedOracles[i];
            if (oracleInfo[oracle].isActive && 
                oraclePrices[_asset][oracle] > 0 &&
                block.timestamp - oracleTimestamps[_asset][oracle] < MAX_PRICE_AGE) {
                count++;
            }
        }
        return count;
    }
    
    /**
     * @dev Internal aggregation logic - weighted average with outlier detection
     */
    function _aggregatePrice(address _asset) internal {
        uint256 weightedSum = 0;
        uint256 totalWeight = 0;
        uint256 validOracleCount = 0;
        
        // First pass: collect valid prices
        uint256[] memory validPrices = new uint256[](approvedOracles.length);
        uint256[] memory weights = new uint256[](approvedOracles.length);
        
        for (uint256 i = 0; i < approvedOracles.length; i++) {
            address oracle = approvedOracles[i];
            OracleData memory info = oracleInfo[oracle];
            
            if (info.isActive && 
                oraclePrices[_asset][oracle] > 0 &&
                block.timestamp - oracleTimestamps[_asset][oracle] < MAX_PRICE_AGE) {
                
                validPrices[validOracleCount] = oraclePrices[_asset][oracle];
                weights[validOracleCount] = info.weight;
                validOracleCount++;
            }
        }
        
        require(validOracleCount >= MIN_ORACLES_REQUIRED, "Insufficient oracle data");
        
        // Calculate weighted average
        for (uint256 i = 0; i < validOracleCount; i++) {
            weightedSum += validPrices[i] * weights[i];
            totalWeight += weights[i];
        }
        
        uint256 aggregatedPrice = weightedSum / totalWeight;
        
        // Calculate confidence based on price variance
        uint256 confidence = _calculateConfidence(validPrices, validOracleCount, aggregatedPrice);
        
        // Update aggregated price
        prices[_asset] = PriceData({
            price: aggregatedPrice,
            timestamp: block.timestamp,
            confidence: confidence
        });
        
        emit AggregatedPriceUpdated(_asset, aggregatedPrice, confidence, block.timestamp);
    }
    
    /**
     * @dev Calculate confidence score based on price agreement
     */
    function _calculateConfidence(
        uint256[] memory _prices,
        uint256 _count,
        uint256 _avgPrice
    ) internal pure returns (uint256) {
        if (_count == 0) return 0;
        
        uint256 maxDeviation = 0;
        
        for (uint256 i = 0; i < _count; i++) {
            uint256 deviation = _prices[i] > _avgPrice 
                ? ((_prices[i] - _avgPrice) * 10000) / _avgPrice
                : ((_avgPrice - _prices[i]) * 10000) / _avgPrice;
            
            if (deviation > maxDeviation) {
                maxDeviation = deviation;
            }
        }
        
        // Confidence = 100% - max deviation
        return maxDeviation >= 10000 ? 0 : 10000 - maxDeviation;
    }
    
    /**
     * @dev Manual aggregation trigger (owner only)
     */
    function manualAggregate(address _asset) external onlyOwner {
        _aggregatePrice(_asset);
    }
}
