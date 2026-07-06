// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "./oracle/IPriceOracleAdapter.sol";
import "./score/ScoreL1.sol";

/**
 * @title AssetPredictionMarket
 * @dev Single-asset prediction market with oracle adapter and separate score calculator
 */
contract AssetPredictionMarket is ReentrancyGuard {
    
    struct Prediction {
        address player;
        uint256 predictedPrice;
        uint256 lockedAmount;
        uint256 timestamp;
    }
    
    struct Epoch {
        uint256 epochId;
        uint256 startTime;
        uint256 endTime;
        bool settled;
        uint256 actualPrice;
        uint256 totalLocked;
        mapping(address => Prediction) predictions;
        address[] players;
    }
    
    address public immutable asset;
    address public immutable oracleAdapter;
    address public immutable collateralToken;
    address public immutable scoreCalculator;
    
    mapping(uint256 => Epoch) public epochs;
    uint256 public currentEpochId;
    uint256 public epochDuration;
    
    event EpochStarted(uint256 indexed epochId, uint256 startTime, uint256 endTime);
    event PredictionPlaced(uint256 indexed epochId, address indexed player, uint256 predictedPrice, uint256 lockedAmount);
    event EpochSettled(uint256 indexed epochId, uint256 actualPrice, uint256 totalRewards);
    event RewardClaimed(uint256 indexed epochId, address indexed player, uint256 reward);
    
    constructor(
        address _asset,
        address _oracleAdapter,
        address _collateralToken,
        address _scoreCalculator,
        uint256 _epochDuration
    ) {
        require(_asset != address(0), "Invalid asset");
        require(_oracleAdapter != address(0), "Invalid oracle");
        require(_collateralToken != address(0), "Invalid collateral");
        require(_scoreCalculator != address(0), "Invalid score calculator");
        require(_epochDuration >= 1 hours, "Duration too short");
        
        asset = _asset;
        oracleAdapter = _oracleAdapter;
        collateralToken = _collateralToken;
        scoreCalculator = _scoreCalculator;
        epochDuration = _epochDuration;
        currentEpochId = 1;
        
        _startNewEpoch();
    }
    
    function placePrediction(uint256 _predictedPrice, uint256 _lockedAmount) external nonReentrant {
        Epoch storage epoch = epochs[currentEpochId];
        
        require(block.timestamp < epoch.endTime, "Epoch ended");
        require(_predictedPrice > 0, "Invalid price");
        require(_lockedAmount > 0, "Must lock tokens");
        require(epoch.predictions[msg.sender].player == address(0), "Already predicted");
        
        IERC20(collateralToken).transferFrom(msg.sender, address(this), _lockedAmount);
        
        epoch.predictions[msg.sender] = Prediction({
            player: msg.sender,
            predictedPrice: _predictedPrice,
            lockedAmount: _lockedAmount,
            timestamp: block.timestamp
        });
        
        epoch.players.push(msg.sender);
        epoch.totalLocked += _lockedAmount;
        
        emit PredictionPlaced(currentEpochId, msg.sender, _predictedPrice, _lockedAmount);
    }
    
    function settleEpoch(uint256 _epochId) external nonReentrant {
        Epoch storage epoch = epochs[_epochId];
        
        require(block.timestamp >= epoch.endTime, "Epoch not ended");
        require(!epoch.settled, "Already settled");
        require(epoch.players.length > 0, "No predictions");
        
        uint256 actualPrice = IPriceOracleAdapter(oracleAdapter).getPrice(asset);
        epoch.actualPrice = actualPrice;
        
        // Prepare arrays for batch calculation
        uint256[] memory predictedPrices = new uint256[](epoch.players.length);
        uint256[] memory lockedAmounts = new uint256[](epoch.players.length);
        
        for (uint256 i = 0; i < epoch.players.length; i++) {
            Prediction memory pred = epoch.predictions[epoch.players[i]];
            predictedPrices[i] = pred.predictedPrice;
            lockedAmounts[i] = pred.lockedAmount;
        }
        
        // Use ScoreL1 for batch scoring
        ScoreL1 calculator = ScoreL1(scoreCalculator);
        (uint256[] memory scores, uint256 totalScore) = calculator.calculateBatchScores(
            predictedPrices,
            lockedAmounts,
            actualPrice
        );
        
        uint256[] memory rewards = calculator.calculateBatchRewards(
            scores,
            totalScore,
            epoch.totalLocked
        );
        
        // Distribute rewards
        for (uint256 i = 0; i < epoch.players.length; i++) {
            if (rewards[i] > 0) {
                IERC20(collateralToken).transfer(epoch.players[i], rewards[i]);
                emit RewardClaimed(_epochId, epoch.players[i], rewards[i]);
            }
        }
        
        epoch.settled = true;
        emit EpochSettled(_epochId, actualPrice, epoch.totalLocked);
        
        if (_epochId == currentEpochId) {
            currentEpochId++;
            _startNewEpoch();
        }
    }
    
    function _startNewEpoch() internal {
        Epoch storage newEpoch = epochs[currentEpochId];
        newEpoch.epochId = currentEpochId;
        newEpoch.startTime = block.timestamp;
        newEpoch.endTime = block.timestamp + epochDuration;
        newEpoch.settled = false;
        
        emit EpochStarted(currentEpochId, newEpoch.startTime, newEpoch.endTime);
    }
    
    function getEpochInfo(uint256 _epochId) external view returns (
        uint256 epochId,
        uint256 startTime,
        uint256 endTime,
        bool settled,
        uint256 actualPrice,
        uint256 totalLocked,
        uint256 playerCount
    ) {
        Epoch storage epoch = epochs[_epochId];
        return (
            epoch.epochId,
            epoch.startTime,
            epoch.endTime,
            epoch.settled,
            epoch.actualPrice,
            epoch.totalLocked,
            epoch.players.length
        );
    }
}