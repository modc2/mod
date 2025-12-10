// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract PredictionMarket is ReentrancyGuard, Ownable {
    struct Market {
        address pairAddress;
        uint256 targetPrice;
        uint256 expiryTimestamp;
        uint256 totalYesStake;
        uint256 totalNoStake;
        bool isSettled;
        bool outcome;
        uint256 finalPrice;
    }

    struct Prediction {
        address user;
        uint256 amount;
        bool predictYes;
        bool claimed;
    }

    IERC20 public immutable stakingToken;
    uint256 public marketCounter;
    
    mapping(uint256 => Market) public markets;
    mapping(uint256 => mapping(address => Prediction[])) public predictions;

    event MarketCreated(uint256 indexed marketId, address pairAddress, uint256 targetPrice, uint256 expiryTimestamp);
    event PredictionPlaced(uint256 indexed marketId, address indexed user, uint256 amount, bool predictYes);
    event MarketSettled(uint256 indexed marketId, uint256 finalPrice, bool outcome);
    event WinningsClaimed(uint256 indexed marketId, address indexed user, uint256 amount);

    constructor(address _stakingToken) {
        stakingToken = IERC20(_stakingToken);
    }

    function createMarket(
        address _pairAddress,
        uint256 _targetPrice,
        uint256 _expiryTimestamp
    ) external onlyOwner returns (uint256) {
        require(_expiryTimestamp > block.timestamp, "Expiry must be in future");
        
        uint256 marketId = marketCounter++;
        markets[marketId] = Market({
            pairAddress: _pairAddress,
            targetPrice: _targetPrice,
            expiryTimestamp: _expiryTimestamp,
            totalYesStake: 0,
            totalNoStake: 0,
            isSettled: false,
            outcome: false,
            finalPrice: 0
        });

        emit MarketCreated(marketId, _pairAddress, _targetPrice, _expiryTimestamp);
        return marketId;
    }

    function placePrediction(
        uint256 _marketId,
        uint256 _amount,
        bool _predictYes
    ) external nonReentrant {
        Market storage market = markets[_marketId];
        require(!market.isSettled, "Market already settled");
        require(block.timestamp < market.expiryTimestamp, "Market expired");
        require(_amount > 0, "Amount must be greater than 0");

        require(
            stakingToken.transferFrom(msg.sender, address(this), _amount),
            "Transfer failed"
        );

        predictions[_marketId][msg.sender].push(Prediction({
            user: msg.sender,
            amount: _amount,
            predictYes: _predictYes,
            claimed: false
        }));

        if (_predictYes) {
            market.totalYesStake += _amount;
        } else {
            market.totalNoStake += _amount;
        }

        emit PredictionPlaced(_marketId, msg.sender, _amount, _predictYes);
    }

    function settleMarket(uint256 _marketId, uint256 _finalPrice) external onlyOwner {
        Market storage market = markets[_marketId];
        require(!market.isSettled, "Already settled");
        require(block.timestamp >= market.expiryTimestamp, "Market not expired");

        market.finalPrice = _finalPrice;
        market.outcome = _finalPrice >= market.targetPrice;
        market.isSettled = true;

        emit MarketSettled(_marketId, _finalPrice, market.outcome);
    }

    function claimWinnings(uint256 _marketId) external nonReentrant {
        Market storage market = markets[_marketId];
        require(market.isSettled, "Market not settled");

        Prediction[] storage userPredictions = predictions[_marketId][msg.sender];
        uint256 totalPayout = 0;

        for (uint256 i = 0; i < userPredictions.length; i++) {
            Prediction storage pred = userPredictions[i];
            
            if (!pred.claimed && pred.predictYes == market.outcome) {
                uint256 totalPool = market.totalYesStake + market.totalNoStake;
                uint256 winningPool = market.outcome ? market.totalYesStake : market.totalNoStake;
                uint256 payout = (pred.amount * totalPool) / winningPool;
                
                pred.claimed = true;
                totalPayout += payout;
            }
        }

        require(totalPayout > 0, "No winnings to claim");
        require(stakingToken.transfer(msg.sender, totalPayout), "Transfer failed");

        emit WinningsClaimed(_marketId, msg.sender, totalPayout);
    }

    function getMarket(uint256 _marketId) external view returns (Market memory) {
        return markets[_marketId];
    }

    function getUserPredictions(uint256 _marketId, address _user) external view returns (Prediction[] memory) {
        return predictions[_marketId][_user];
    }
}