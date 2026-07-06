// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPriceOracle.sol";

/**
 * @title PreFiModular
 * @notice Modular prediction market supporting multiple oracle types
 * @dev Supports Uniswap V3, Chainlink, Polymarket, and custom oracles
 */
contract PreFiModular is ReentrancyGuard, Pausable, Ownable {
    using SafeERC20 for IERC20;

    enum OracleType {
        UNISWAP_V3,
        CHAINLINK,
        POLYMARKET,
        CUSTOM
    }

    struct Prediction {
        address player;
        uint256 predictedValue;  // Can be price, probability, or custom value
        uint256 stakedAmount;
        uint256 timestamp;
        bool claimed;
    }

    struct Market {
        uint256 marketId;
        string asset;
        bytes32 assetId;         // Unique asset identifier for oracle
        OracleType oracleType;
        address oracleAddress;
        bytes oracleData;        // Oracle-specific data
        uint256 startTime;
        uint256 endTime;
        uint256 settlementTime;
        bool settled;
        uint256 actualValue;     // Actual price/probability from oracle
        uint256 totalStaked;
        uint256 totalScore;
        address[] players;
        mapping(address => Prediction) predictions;
        mapping(address => uint256) rewards;
    }

    IERC20 public immutable stakeToken;
    uint256 public minStake;
    uint256 public platformFee; // basis points
    uint256 public accumulatedFees;
    uint256 public marketCounter;

    // Oracle registry
    mapping(OracleType => address) public oracleAddresses;

    mapping(uint256 => Market) public markets;

    event MarketCreated(
        uint256 indexed marketId,
        string asset,
        OracleType oracleType,
        address oracle,
        uint256 startTime,
        uint256 endTime
    );
    event PredictionPlaced(uint256 indexed marketId, address indexed player, uint256 predictedValue, uint256 stakedAmount);
    event MarketSettled(uint256 indexed marketId, uint256 actualValue, uint256 totalRewards);
    event RewardClaimed(uint256 indexed marketId, address indexed player, uint256 reward);
    event OracleRegistered(OracleType indexed oracleType, address oracle);

    constructor(
        address _stakeToken,
        uint256 _minStake,
        uint256 _platformFee
    ) {
        require(_stakeToken != address(0), "Invalid stake token");
        require(_platformFee <= 1000, "Fee too high");

        stakeToken = IERC20(_stakeToken);
        minStake = _minStake;
        platformFee = _platformFee;
    }

    /**
     * @notice Register an oracle for a specific type
     */
    function registerOracle(OracleType oracleType, address oracle) external onlyOwner {
        require(oracle != address(0), "Invalid oracle");
        oracleAddresses[oracleType] = oracle;
        emit OracleRegistered(oracleType, oracle);
    }

    /**
     * @notice Create market with specific oracle type
     * @param asset Asset description (e.g., "ETH/USD", "Will BTC hit $100k?")
     * @param assetId Asset identifier for oracle (e.g., keccak256("ETH/USD"))
     * @param oracleType Type of oracle to use
     * @param oracleData Oracle-specific configuration data
     * @param duration Market duration in seconds
     */
    function createMarket(
        string calldata asset,
        bytes32 assetId,
        OracleType oracleType,
        bytes calldata oracleData,
        uint256 duration
    ) external onlyOwner whenNotPaused returns (uint256) {
        require(bytes(asset).length > 0, "Invalid asset");
        require(duration >= 1 hours, "Duration too short");

        address oracle = oracleAddresses[oracleType];
        require(oracle != address(0), "Oracle not registered");

        // Verify oracle supports this asset
        require(
            IPriceOracle(oracle).supportsAsset(assetId),
            "Asset not supported by oracle"
        );

        marketCounter++;
        Market storage market = markets[marketCounter];

        market.marketId = marketCounter;
        market.asset = asset;
        market.assetId = assetId;
        market.oracleType = oracleType;
        market.oracleAddress = oracle;
        market.oracleData = oracleData;
        market.startTime = block.timestamp;
        market.endTime = block.timestamp + duration;
        market.settled = false;

        emit MarketCreated(
            marketCounter,
            asset,
            oracleType,
            oracle,
            market.startTime,
            market.endTime
        );

        return marketCounter;
    }

    /**
     * @notice Place prediction
     * @param marketId Market ID
     * @param predictedValue Predicted value (price, probability 0-100, etc.)
     * @param stakeAmount Tokens to stake
     */
    function predict(
        uint256 marketId,
        uint256 predictedValue,
        uint256 stakeAmount
    ) external nonReentrant whenNotPaused {
        Market storage market = markets[marketId];

        require(market.marketId != 0, "Market does not exist");
        require(block.timestamp < market.endTime, "Market closed");
        require(market.predictions[msg.sender].player == address(0), "Already predicted");
        require(predictedValue > 0, "Invalid value");
        require(stakeAmount >= minStake, "Stake too low");

        stakeToken.safeTransferFrom(msg.sender, address(this), stakeAmount);

        market.predictions[msg.sender] = Prediction({
            player: msg.sender,
            predictedValue: predictedValue,
            stakedAmount: stakeAmount,
            timestamp: block.timestamp,
            claimed: false
        });

        market.players.push(msg.sender);
        market.totalStaked += stakeAmount;

        emit PredictionPlaced(marketId, msg.sender, predictedValue, stakeAmount);
    }

    /**
     * @notice Resolve market using configured oracle
     * @param marketId Market to resolve
     */
    function resolveMarket(uint256 marketId) external nonReentrant {
        Market storage market = markets[marketId];

        require(market.marketId != 0, "Market does not exist");
        require(block.timestamp >= market.endTime, "Market not ended");
        require(!market.settled, "Already settled");
        require(market.players.length > 0, "No predictions");

        // Get value from oracle
        IPriceOracle oracle = IPriceOracle(market.oracleAddress);
        (uint256 actualValue, uint256 timestamp, uint8 confidence) = oracle.getPrice(
            market.assetId,
            market.oracleData
        );

        require(actualValue > 0, "Invalid oracle value");
        require(confidence >= 50, "Low confidence");

        market.actualValue = actualValue;
        market.settlementTime = timestamp;

        // Calculate scores
        uint256 totalScore = 0;
        for (uint256 i = 0; i < market.players.length; i++) {
            address player = market.players[i];
            Prediction memory pred = market.predictions[player];

            uint256 score = calculateScore(
                pred.predictedValue,
                actualValue,
                pred.stakedAmount
            );
            market.rewards[player] = score;
            totalScore += score;
        }

        market.totalScore = totalScore;

        // Calculate rewards
        uint256 feeAmount = (market.totalStaked * platformFee) / 10000;
        accumulatedFees += feeAmount;
        uint256 rewardPool = market.totalStaked - feeAmount;

        for (uint256 i = 0; i < market.players.length; i++) {
            address player = market.players[i];
            uint256 playerScore = market.rewards[player];

            if (totalScore > 0) {
                market.rewards[player] = (rewardPool * playerScore) / totalScore;
            } else {
                market.rewards[player] = 0;
            }
        }

        market.settled = true;

        emit MarketSettled(marketId, actualValue, rewardPool);
    }

    /**
     * @notice Calculate L2 distance score
     */
    function calculateScore(
        uint256 predicted,
        uint256 actual,
        uint256 stake
    ) public pure returns (uint256) {
        uint256 diff = predicted > actual ? predicted - actual : actual - predicted;
        uint256 distanceSquared = (diff * diff) / 1e18;
        return (stake * 1e18) / (1e18 + distanceSquared);
    }

    /**
     * @notice Claim rewards
     */
    function claimReward(uint256 marketId) external nonReentrant {
        Market storage market = markets[marketId];

        require(market.settled, "Market not settled");
        require(!market.predictions[msg.sender].claimed, "Already claimed");

        uint256 reward = market.rewards[msg.sender];
        require(reward > 0, "No reward");

        market.predictions[msg.sender].claimed = true;
        stakeToken.safeTransfer(msg.sender, reward);

        emit RewardClaimed(marketId, msg.sender, reward);
    }

    /**
     * @notice Get market info with oracle details
     */
    function getMarketInfo(uint256 marketId) external view returns (
        string memory asset,
        OracleType oracleType,
        address oracle,
        uint256 startTime,
        uint256 endTime,
        bool settled,
        uint256 actualValue,
        uint256 totalStaked,
        uint256 playersCount
    ) {
        Market storage market = markets[marketId];
        return (
            market.asset,
            market.oracleType,
            market.oracleAddress,
            market.startTime,
            market.endTime,
            market.settled,
            market.actualValue,
            market.totalStaked,
            market.players.length
        );
    }

    /**
     * @notice Get oracle metadata for a market
     */
    function getOracleMetadata(uint256 marketId) external view returns (
        string memory name,
        string memory description,
        string memory oracleTypeStr
    ) {
        Market storage market = markets[marketId];
        return IPriceOracle(market.oracleAddress).getMetadata();
    }

    // Admin functions
    function setMinStake(uint256 _minStake) external onlyOwner {
        minStake = _minStake;
    }

    function setPlatformFee(uint256 _platformFee) external onlyOwner {
        require(_platformFee <= 1000, "Fee too high");
        platformFee = _platformFee;
    }

    function withdrawFees() external onlyOwner {
        uint256 amount = accumulatedFees;
        accumulatedFees = 0;
        stakeToken.safeTransfer(owner(), amount);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }
}
