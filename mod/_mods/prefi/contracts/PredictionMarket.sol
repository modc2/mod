// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/**
 * @title PredictionMarket
 * @dev Zero-sum prediction market for asset prices with weekly settlements
 */
contract PredictionMarket is Ownable, ReentrancyGuard {
    
    struct Prediction {
        address user;
        address asset;
        uint256 predictedPrice;
        uint256 collateralAmount;
        address collateralToken;
        uint256 lockTimestamp;
        uint256 unlockTimestamp;
        bool settled;
        int256 pnl;
    }
    
    struct Asset {
        bool enabled;
        uint256 minLiquidity;
    }
    
    // Mappings
    mapping(uint256 => Prediction) public predictions;
    mapping(address => bool) public approvedCollateral;
    mapping(address => Asset) public assets;
    mapping(address => uint256) public userPoints;
    mapping(uint256 => uint256) public weeklyPools;
    
    // State variables
    uint256 public predictionCounter;
    uint256 public currentWeek;
    address public priceOracle;
    uint256 public constant WEEK = 7 days;
    uint256 public constant MAX_LOCK_PERIOD = 30 days;
    uint256 public lastSettlementTime;
    
    // Events
    event PredictionPlaced(uint256 indexed predictionId, address indexed user, address asset, uint256 predictedPrice);
    event PredictionSettled(uint256 indexed predictionId, int256 pnl, uint256 points);
    event CollateralApproved(address indexed token);
    event AssetEnabled(address indexed asset, uint256 minLiquidity);
    event WeeklySettlement(uint256 week, uint256 totalPool);
    
    constructor(address _priceOracle) {
        priceOracle = _priceOracle;
        lastSettlementTime = block.timestamp;
        currentWeek = 1;
    }
    
    /**
     * @dev Place a prediction on future asset price
     */
    function placePrediction(
        address _asset,
        uint256 _predictedPrice,
        uint256 _collateralAmount,
        address _collateralToken,
        uint256 _lockDuration
    ) external nonReentrant {
        require(assets[_asset].enabled, "Asset not enabled");
        require(approvedCollateral[_collateralToken], "Collateral not approved");
        require(_lockDuration <= MAX_LOCK_PERIOD, "Lock period too long");
        require(_lockDuration >= 1 days, "Lock period too short");
        
        // Transfer collateral
        IERC20(_collateralToken).transferFrom(msg.sender, address(this), _collateralAmount);
        
        // Create prediction
        predictions[predictionCounter] = Prediction({
            user: msg.sender,
            asset: _asset,
            predictedPrice: _predictedPrice,
            collateralAmount: _collateralAmount,
            collateralToken: _collateralToken,
            lockTimestamp: block.timestamp,
            unlockTimestamp: block.timestamp + _lockDuration,
            settled: false,
            pnl: 0
        });
        
        emit PredictionPlaced(predictionCounter, msg.sender, _asset, _predictedPrice);
        predictionCounter++;
    }
    
    /**
     * @dev Settle a prediction after unlock time
     */
    function settlePrediction(uint256 _predictionId) external nonReentrant {
        Prediction storage pred = predictions[_predictionId];
        require(!pred.settled, "Already settled");
        require(block.timestamp >= pred.unlockTimestamp, "Still locked");
        
        // Get actual price from oracle
        uint256 actualPrice = IPriceOracle(priceOracle).getPrice(pred.asset);
        
        // Calculate PnL
        int256 priceDiff = int256(actualPrice) - int256(pred.predictedPrice);
        int256 pnl = (priceDiff * int256(pred.collateralAmount)) / int256(pred.predictedPrice);
        
        pred.pnl = pnl;
        pred.settled = true;
        
        // Award points based on USD won/lost
        uint256 points = pnl > 0 ? uint256(pnl) : 0;
        userPoints[pred.user] += points;
        
        // Add to weekly pool
        weeklyPools[currentWeek] += pred.collateralAmount;
        
        emit PredictionSettled(_predictionId, pnl, points);
    }
    
    /**
     * @dev Weekly settlement - distribute pool based on performance
     */
    function weeklySettlement() external onlyOwner {
        require(block.timestamp >= lastSettlementTime + WEEK, "Too early");
        
        uint256 totalPool = weeklyPools[currentWeek];
        emit WeeklySettlement(currentWeek, totalPool);
        
        currentWeek++;
        lastSettlementTime = block.timestamp;
    }
    
    /**
     * @dev Approve collateral token
     */
    function approveCollateral(address _token) external onlyOwner {
        approvedCollateral[_token] = true;
        emit CollateralApproved(_token);
    }
    
    /**
     * @dev Enable asset for predictions
     */
    function enableAsset(address _asset, uint256 _minLiquidity) external onlyOwner {
        assets[_asset] = Asset({
            enabled: true,
            minLiquidity: _minLiquidity
        });
        emit AssetEnabled(_asset, _minLiquidity);
    }
    
    /**
     * @dev Update price oracle
     */
    function updateOracle(address _newOracle) external onlyOwner {
        priceOracle = _newOracle;
    }
}

/**
 * @dev Price Oracle Interface
 */
interface IPriceOracle {
    function getPrice(address asset) external view returns (uint256);
}
