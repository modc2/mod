// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "../interfaces/IPriceOracle.sol";

/**
 * @title PolymarketOracle
 * @notice Oracle adapter for Polymarket prediction market outcomes
 * @dev Uses Polymarket's conditional token framework (CTF) and UMA resolution
 */
contract PolymarketOracle is IPriceOracle {
    // Mapping of market ID to outcome data
    struct MarketOutcome {
        uint256 yesPrice;   // Price of YES tokens (0-100, representing probability)
        uint256 noPrice;    // Price of NO tokens
        uint256 volume24h;  // 24h trading volume
        uint256 updatedAt;  // Last update timestamp
        bool resolved;      // Whether market is resolved
        bool outcome;       // Final outcome (true = YES, false = NO)
    }

    mapping(bytes32 => MarketOutcome) public markets;
    address public updater; // Authorized price updater (off-chain oracle)
    uint256 public maxPriceAge = 300; // 5 minutes for active markets

    event MarketUpdated(bytes32 indexed marketId, uint256 yesPrice, uint256 volume, uint256 timestamp);
    event MarketResolved(bytes32 indexed marketId, bool outcome);
    event UpdaterChanged(address indexed oldUpdater, address indexed newUpdater);

    modifier onlyUpdater() {
        require(msg.sender == updater, "Not authorized");
        _;
    }

    constructor(address _updater) {
        require(_updater != address(0), "Invalid updater");
        updater = _updater;
    }

    /**
     * @notice Update market prices from off-chain Polymarket API
     * @param marketId Market identifier
     * @param yesPrice Current YES token price (0-100)
     * @param noPrice Current NO token price
     * @param volume24h 24h trading volume
     */
    function updateMarket(
        bytes32 marketId,
        uint256 yesPrice,
        uint256 noPrice,
        uint256 volume24h
    ) external onlyUpdater {
        require(yesPrice <= 100 && noPrice <= 100, "Invalid prices");
        require(!markets[marketId].resolved, "Market resolved");

        markets[marketId] = MarketOutcome({
            yesPrice: yesPrice,
            noPrice: noPrice,
            volume24h: volume24h,
            updatedAt: block.timestamp,
            resolved: false,
            outcome: false
        });

        emit MarketUpdated(marketId, yesPrice, volume24h, block.timestamp);
    }

    /**
     * @notice Resolve  market with final outcome
     * @param marketId Market identifier
     * @param outcome Final outcome (true = YES, false = NO)
     */
    function resolveMarket(bytes32 marketId, bool outcome) external onlyUpdater {
        require(!markets[marketId].resolved, "Already resolved");

        markets[marketId].resolved = true;
        markets[marketId].outcome = outcome;
        markets[marketId].updatedAt = block.timestamp;

        emit MarketResolved(marketId, outcome);
    }

    /**
     * @notice Get current market probability
     * @param asset Market ID
     * @param data Optional: bytes encoded bool for YES (true) or NO (false) side
     * @return price Probability as percentage (0-100) scaled to 1e18
     * @return timestamp Last update time
     * @return confidence Confidence based on volume and recency
     */
    function getPrice(
        bytes32 asset,
        bytes calldata data
    ) external view override returns (
        uint256 price,
        uint256 timestamp,
        uint8 confidence
    ) {
        MarketOutcome memory market = markets[asset];
        require(market.updatedAt > 0, "Market not found");
        require(block.timestamp - market.updatedAt <= maxPriceAge, "Price stale");

        // Decode which side (YES or NO)
        bool getYesPrice = data.length > 0 ? abi.decode(data, (bool)) : true;

        // Convert 0-100 percentage to 1e18 scale
        uint256 rawPrice = getYesPrice ? market.yesPrice : market.noPrice;
        price = (rawPrice * 1e18) / 100;

        timestamp = market.updatedAt;

        // Confidence based on volume and update recency
        uint256 age = block.timestamp - market.updatedAt;
        if (market.volume24h > 10000e18 && age < 60) {
            confidence = 90;
        } else if (market.volume24h > 1000e18 && age < 300) {
            confidence = 75;
        } else if (age < 600) {
            confidence = 60;
        } else {
            confidence = 40;
        }

        return (price, timestamp, confidence);
    }

    /**
     * @notice Get resolved market outcome
     */
    function getResolvedOutcome(bytes32 marketId) external view returns (
        bool resolved,
        bool outcome,
        uint256 resolvedAt
    ) {
        MarketOutcome memory market = markets[marketId];
        return (
            market.resolved,
            market.outcome,
            market.updatedAt
        );
    }

    function supportsAsset(bytes32 asset) external view override returns (bool) {
        return markets[asset].updatedAt > 0;
    }

    function getMetadata() external pure override returns (
        string memory name,
        string memory description,
        string memory oracleType
    ) {
        return (
            "Polymarket Oracle Adapter",
            "Real-time prediction market probabilities from Polymarket",
            "POLYMARKET"
        );
    }

    function setUpdater(address _updater) external onlyUpdater {
        require(_updater != address(0), "Invalid updater");
        address oldUpdater = updater;
        updater = _updater;
        emit UpdaterChanged(oldUpdater, _updater);
    }

    function setMaxPriceAge(uint256 _maxAge) external onlyUpdater {
        maxPriceAge = _maxAge;
    }
}
