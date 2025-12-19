// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title PriceOracle
 * @dev Modular price oracle aggregating CoinGecko and CoinMarketCap
 */
contract PriceOracle is Ownable {
    
    struct PriceData {
        uint256 coinGeckoPrice;
        uint256 coinMarketCapPrice;
        uint256 timestamp;
        bool isValid;
    }
    
    mapping(address => PriceData) public prices;
    mapping(address => address) public priceFeeds; // Chainlink-style feeds
    
    address public coinGeckoAdapter;
    address public coinMarketCapAdapter;
    
    event PriceUpdated(address indexed asset, uint256 avgPrice, uint256 timestamp);
    event AdapterUpdated(string source, address adapter);
    
    constructor(address _coinGeckoAdapter, address _coinMarketCapAdapter) {
        coinGeckoAdapter = _coinGeckoAdapter;
        coinMarketCapAdapter = _coinMarketCapAdapter;
    }
    
    /**
     * @dev Get averaged price from both sources
     */
    function getPrice(address _asset) external view returns (uint256) {
        PriceData memory data = prices[_asset];
        require(data.isValid, "Price not available");
        require(block.timestamp - data.timestamp < 1 hours, "Price too stale");
        
        // Average the two prices
        return (data.coinGeckoPrice + data.coinMarketCapPrice) / 2;
    }
    
    /**
     * @dev Update price from both oracles
     */
    function updatePrice(address _asset, uint256 _cgPrice, uint256 _cmcPrice) external {
        require(msg.sender == coinGeckoAdapter || msg.sender == coinMarketCapAdapter || msg.sender == owner(), "Unauthorized");
        
        PriceData storage data = prices[_asset];
        
        if (msg.sender == coinGeckoAdapter) {
            data.coinGeckoPrice = _cgPrice;
        } else if (msg.sender == coinMarketCapAdapter) {
            data.coinMarketCapPrice = _cmcPrice;
        } else {
            // Owner can set both
            data.coinGeckoPrice = _cgPrice;
            data.coinMarketCapPrice = _cmcPrice;
        }
        
        data.timestamp = block.timestamp;
        data.isValid = true;
        
        uint256 avgPrice = (data.coinGeckoPrice + data.coinMarketCapPrice) / 2;
        emit PriceUpdated(_asset, avgPrice, block.timestamp);
    }
    
    /**
     * @dev Update oracle adapters
     */
    function updateAdapter(string memory _source, address _adapter) external onlyOwner {
        if (keccak256(bytes(_source)) == keccak256(bytes("coingecko"))) {
            coinGeckoAdapter = _adapter;
        } else if (keccak256(bytes(_source)) == keccak256(bytes("coinmarketcap"))) {
            coinMarketCapAdapter = _adapter;
        }
        emit AdapterUpdated(_source, _adapter);
    }
    
    /**
     * @dev Get individual source prices
     */
    function getSourcePrices(address _asset) external view returns (uint256 cgPrice, uint256 cmcPrice) {
        PriceData memory data = prices[_asset];
        return (data.coinGeckoPrice, data.coinMarketCapPrice);
    }
}
