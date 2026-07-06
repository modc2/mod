// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "../interfaces/IPriceOracle.sol";

interface AggregatorV3Interface {
    function decimals() external view returns (uint8);
    function description() external view returns (string memory);
    function version() external view returns (uint256);
    function latestRoundData() external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    );
}

/**
 * @title ChainlinkPriceOracle
 * @notice Price oracle using Chainlink price feeds
 */
contract ChainlinkPriceOracle is IPriceOracle {
    // Mapping of asset identifier to Chainlink feed address
    mapping(bytes32 => address) public priceFeeds;
    uint256 public maxPriceAge = 3600; // 1 hour staleness tolerance

    event FeedAdded(bytes32 indexed asset, address feed);
    event FeedRemoved(bytes32 indexed asset);

    constructor() {}

    /**
     * @notice Add Chainlink price feed for an asset
     * @param asset Asset identifier (e.g., keccak256("ETH/USD"))
     * @param feed Chainlink aggregator address
     */
    function addFeed(bytes32 asset, address feed) external {
        require(feed != address(0), "Invalid feed");
        priceFeeds[asset] = feed;
        emit FeedAdded(asset, feed);
    }

    /**
     * @notice Remove price feed
     */
    function removeFeed(bytes32 asset) external {
        delete priceFeeds[asset];
        emit FeedRemoved(asset);
    }

    /**
     * @notice Get price from Chainlink feed
     * @param asset Asset identifier
     * @param data Not used for Chainlink
     */
    function getPrice(
        bytes32 asset,
        bytes calldata data
    ) external view override returns (
        uint256 price,
        uint256 timestamp,
        uint8 confidence
    ) {
        address feed = priceFeeds[asset];
        require(feed != address(0), "Feed not found");

        AggregatorV3Interface priceFeed = AggregatorV3Interface(feed);

        (
            uint80 roundId,
            int256 answer,
            ,
            uint256 updatedAt,
            uint80 answeredInRound
        ) = priceFeed.latestRoundData();

        require(answer > 0, "Invalid price");
        require(answeredInRound >= roundId, "Stale price");
        require(block.timestamp - updatedAt <= maxPriceAge, "Price too old");

        // Scale to 1e18
        uint8 decimals = priceFeed.decimals();
        if (decimals < 18) {
            price = uint256(answer) * (10 ** (18 - decimals));
        } else if (decimals > 18) {
            price = uint256(answer) / (10 ** (decimals - 18));
        } else {
            price = uint256(answer);
        }

        timestamp = updatedAt;
        confidence = 99; // Very high confidence for Chainlink

        return (price, timestamp, confidence);
    }

    function supportsAsset(bytes32 asset) external view override returns (bool) {
        return priceFeeds[asset] != address(0);
    }

    function getMetadata() external pure override returns (
        string memory name,
        string memory description,
        string memory oracleType
    ) {
        return (
            "Chainlink Price Feed Oracle",
            "Decentralized oracle network with cryptographic proof of data integrity",
            "CHAINLINK"
        );
    }

    function setMaxPriceAge(uint256 _maxPriceAge) external {
        maxPriceAge = _maxPriceAge;
    }
}
