// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title PreFiV3
 * @notice Prediction market with Uniswap V3 oracle integration
 * @dev L2 distance-based reward distribution system
 */
contract PreFiV3 is ReentrancyGuard, Pausable, Ownable {
    using SafeERC20 for IERC20;

    struct Prediction {
        address player;
        uint256 predictedPrice;
        uint256 stakedAmount;
        uint256 timestamp;
        bool claimed;
    }

    struct Market {
        uint256 marketId;
        string asset;
        address tokenAddress;
        uint256 startTime;
        uint256 endTime;
        uint256 settlementTime;
        bool settled;
        uint256 actualPrice;
        uint256 totalStaked;
        uint256 totalScore;
        address[] players;
        mapping(address => Prediction) predictions;
        mapping(address => uint256) rewards;
    }

    // State variables
    IERC20 public immutable stakeToken;
    address public oracle;
    uint256 public minStake;
    uint256 public platformFee; // in basis points (1% = 100)
    uint256 public accumulatedFees;
    uint256 public marketCounter;

    mapping(uint256 => Market) public markets;

    // Events
    event MarketCreated(uint256 indexed marketId, string asset, address tokenAddress, uint256 startTime, uint256 endTime);
    event PredictionPlaced(uint256 indexed marketId, address indexed player, uint256 predictedPrice, uint256 stakedAmount);
    event MarketSettled(uint256 indexed marketId, uint256 actualPrice, uint256 totalRewards);
    event RewardClaimed(uint256 indexed marketId, address indexed player, uint256 reward);
    event OracleUpdated(address indexed newOracle);
    event MinStakeUpdated(uint256 newMinStake);
    event PlatformFeeUpdated(uint256 newFee);

    constructor(
        address _stakeToken,
        address _oracle,
        uint256 _minStake,
        uint256 _platformFee
    ) {
        require(_stakeToken != address(0), "Invalid stake token");
        require(_oracle != address(0), "Invalid oracle");
        require(_platformFee <= 1000, "Fee too high"); // Max 10%

        stakeToken = IERC20(_stakeToken);
        oracle = _oracle;
        minStake = _minStake;
        platformFee = _platformFee;
    }

    /**
     * @notice Create a new prediction market
     * @param asset Asset identifier (e.g., "ETH/USD")
     * @param tokenAddress Token address for price oracle
     * @param duration Market duration in seconds
     */
    function createMarket(
        string calldata asset,
        address tokenAddress,
        uint256 duration
    ) external onlyOwner whenNotPaused returns (uint256) {
        require(bytes(asset).length > 0, "Invalid asset");
        require(tokenAddress != address(0), "Invalid token");
        require(duration >= 1 hours, "Duration too short");

        marketCounter++;
        Market storage market = markets[marketCounter];

        market.marketId = marketCounter;
        market.asset = asset;
        market.tokenAddress = tokenAddress;
        market.startTime = block.timestamp;
        market.endTime = block.timestamp + duration;
        market.settlementTime = 0;
        market.settled = false;

        emit MarketCreated(marketCounter, asset, tokenAddress, market.startTime, market.endTime);

        return marketCounter;
    }

    /**
     * @notice Place a prediction in a market
     * @param marketId Market identifier
     * @param predictedPrice Predicted price (scaled by 1e18)
     * @param stakeAmount Amount of tokens to stake
     */
    function predict(
        uint256 marketId,
        uint256 predictedPrice,
        uint256 stakeAmount
    ) external nonReentrant whenNotPaused {
        Market storage market = markets[marketId];

        require(market.marketId != 0, "Market does not exist");
        require(block.timestamp < market.endTime, "Market closed");
        require(market.predictions[msg.sender].player == address(0), "Already predicted");
        require(predictedPrice > 0, "Invalid price");
        require(stakeAmount >= minStake, "Stake too low");

        // Transfer stake tokens
        stakeToken.safeTransferFrom(msg.sender, address(this), stakeAmount);

        // Record prediction
        market.predictions[msg.sender] = Prediction({
            player: msg.sender,
            predictedPrice: predictedPrice,
            stakedAmount: stakeAmount,
            timestamp: block.timestamp,
            claimed: false
        });

        market.players.push(msg.sender);
        market.totalStaked += stakeAmount;

        emit PredictionPlaced(marketId, msg.sender, predictedPrice, stakeAmount);
    }

    /**
     * @notice Resolve market using oracle price
     * @param marketId Market to resolve
     * @param actualPrice Actual price from oracle (scaled by 1e18)
     */
    function resolveMarket(uint256 marketId, uint256 actualPrice) external nonReentrant {
        require(msg.sender == oracle || msg.sender == owner(), "Not authorized");

        Market storage market = markets[marketId];

        require(market.marketId != 0, "Market does not exist");
        require(block.timestamp >= market.endTime, "Market not ended");
        require(!market.settled, "Already settled");
        require(market.players.length > 0, "No predictions");
        require(actualPrice > 0, "Invalid price");

        market.actualPrice = actualPrice;
        market.settlementTime = block.timestamp;

        // Calculate scores using L2 distance
        uint256 totalScore = 0;

        for (uint256 i = 0; i < market.players.length; i++) {
            address player = market.players[i];
            Prediction memory pred = market.predictions[player];

            uint256 score = calculateScore(pred.predictedPrice, actualPrice, pred.stakedAmount);
            market.rewards[player] = score;
            totalScore += score;
        }

        market.totalScore = totalScore;

        // Calculate platform fee
        uint256 feeAmount = (market.totalStaked * platformFee) / 10000;
        accumulatedFees += feeAmount;

        uint256 rewardPool = market.totalStaked - feeAmount;

        // Calculate final rewards
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

        emit MarketSettled(marketId, actualPrice, rewardPool);
    }

    /**
     * @notice Calculate L2 distance score
     * @param predicted Predicted price
     * @param actual Actual price
     * @param stake Staked amount
     * @return score Calculated score
     */
    function calculateScore(
        uint256 predicted,
        uint256 actual,
        uint256 stake
    ) public pure returns (uint256 score) {
        uint256 diff = predicted > actual ? predicted - actual : actual - predicted;
        uint256 distanceSquared = (diff * diff) / 1e18; // Normalize

        // Score = stake / (1 + distance²)
        score = (stake * 1e18) / (1e18 + distanceSquared);

        return score;
    }

    /**
     * @notice Claim rewards from settled market
     * @param marketId Market identifier
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
     * @notice Get market info
     */
    function getMarketInfo(uint256 marketId) external view returns (
        string memory asset,
        address tokenAddress,
        uint256 startTime,
        uint256 endTime,
        bool settled,
        uint256 actualPrice,
        uint256 totalStaked,
        uint256 playersCount
    ) {
        Market storage market = markets[marketId];
        return (
            market.asset,
            market.tokenAddress,
            market.startTime,
            market.endTime,
            market.settled,
            market.actualPrice,
            market.totalStaked,
            market.players.length
        );
    }

    /**
     * @notice Get prediction details
     */
    function getPrediction(uint256 marketId, address player) external view returns (
        uint256 predictedPrice,
        uint256 stakedAmount,
        uint256 timestamp,
        bool claimed,
        uint256 reward
    ) {
        Market storage market = markets[marketId];
        Prediction memory pred = market.predictions[player];

        return (
            pred.predictedPrice,
            pred.stakedAmount,
            pred.timestamp,
            pred.claimed,
            market.rewards[player]
        );
    }

    // Admin functions
    function setOracle(address _oracle) external onlyOwner {
        require(_oracle != address(0), "Invalid oracle");
        oracle = _oracle;
        emit OracleUpdated(_oracle);
    }

    function setMinStake(uint256 _minStake) external onlyOwner {
        minStake = _minStake;
        emit MinStakeUpdated(_minStake);
    }

    function setPlatformFee(uint256 _platformFee) external onlyOwner {
        require(_platformFee <= 1000, "Fee too high");
        platformFee = _platformFee;
        emit PlatformFeeUpdated(_platformFee);
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
