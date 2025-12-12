// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Pool.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Factory.sol";
import "@uniswap/v3-periphery/contracts/interfaces/ISwapRouter.sol";
import "@uniswap/v3-periphery/contracts/interfaces/IQuoter.sol";

/**
 * @title UniswapV3PredictionMarket
 * @dev Modular 24-Hour Prediction Market with Uniswap V3 Integration
 */
contract UniswapV3PredictionMarket {
    
    enum Direction { UP, DOWN }
    
    struct Position {
        address user;
        uint256 amount;
        Direction direction;
        uint256 entryPrice;
        uint256 startTime;
        uint256 exitTime;
        uint256 exitPrice;
        bool settled;
        address token0;
        address token1;
        uint24 poolFee;
    }
    
    // Core state
    mapping(bytes32 => Position) public positions;
    mapping(address => uint256) public userBalances;
    bytes32[] public activePositions;
    bytes32[] public settledPositions;
    
    // Uniswap V3 integration
    IUniswapV3Factory public immutable uniswapFactory;
    ISwapRouter public immutable swapRouter;
    IQuoter public immutable quoter;
    
    // Modular configuration
    uint256 public totalLockedLiquidity;
    uint256 public rewardPoolPercentage = 95;
    uint256 public predictionDuration = 24 hours;
    address public priceOracle;
    
    // Events
    event PositionOpened(bytes32 indexed positionId, address indexed user, uint256 amount, Direction direction, address token0, address token1);
    event PositionSettled(bytes32 indexed positionId, bool profitable, uint256 profit);
    event RewardsDistributed(address token0, address token1, uint256 totalDistributed, uint256 winners);
    event Deposit(address indexed user, uint256 amount);
    event Withdrawal(address indexed user, uint256 amount);
    event ConfigUpdated(string param, uint256 value);
    
    constructor(
        address _uniswapFactory,
        address _swapRouter,
        address _quoter
    ) {
        uniswapFactory = IUniswapV3Factory(_uniswapFactory);
        swapRouter = ISwapRouter(_swapRouter);
        quoter = IQuoter(_quoter);
    }
    
    // ============ MODULAR CONFIGURATION ============
    
    function setRewardPoolPercentage(uint256 _percentage) external {
        require(_percentage <= 100, "Invalid percentage");
        rewardPoolPercentage = _percentage;
        emit ConfigUpdated("rewardPoolPercentage", _percentage);
    }
    
    function setPredictionDuration(uint256 _duration) external {
        require(_duration > 0, "Invalid duration");
        predictionDuration = _duration;
        emit ConfigUpdated("predictionDuration", _duration);
    }
    
    function setPriceOracle(address _oracle) external {
        priceOracle = _oracle;
    }
    
    // ============ UNISWAP V3 PRICE FEEDS ============
    
    function getUniswapV3Price(
        address token0,
        address token1,
        uint24 fee
    ) public view returns (uint256) {
        address poolAddress = uniswapFactory.getPool(token0, token1, fee);
        require(poolAddress != address(0), "Pool does not exist");
        
        IUniswapV3Pool pool = IUniswapV3Pool(poolAddress);
        (uint160 sqrtPriceX96,,,,,,) = pool.slot0();
        
        // Calculate price from sqrtPriceX96
        uint256 price = uint256(sqrtPriceX96) * uint256(sqrtPriceX96) * 1e18 >> (96 * 2);
        return price;
    }
    
    function getPoolAddress(
        address token0,
        address token1,
        uint24 fee
    ) public view returns (address) {
        return uniswapFactory.getPool(token0, token1, fee);
    }
    
    // ============ CORE FUNCTIONALITY ============
    
    function deposit() external payable {
        require(msg.value > 0, "Amount must be positive");
        userBalances[msg.sender] += msg.value;
        emit Deposit(msg.sender, msg.value);
    }
    
    function withdraw(uint256 amount) external {
        require(amount > 0, "Amount must be positive");
        require(userBalances[msg.sender] >= amount, "Insufficient balance");
        
        userBalances[msg.sender] -= amount;
        payable(msg.sender).transfer(amount);
        emit Withdrawal(msg.sender, amount);
    }
    
    function openPosition(
        uint256 amount,
        Direction direction,
        address token0,
        address token1,
        uint24 poolFee
    ) external returns (bytes32) {
        require(amount > 0, "Amount must be positive");
        require(userBalances[msg.sender] >= amount, "Insufficient balance");
        require(getPoolAddress(token0, token1, poolFee) != address(0), "Pool does not exist");
        
        uint256 currentPrice = getUniswapV3Price(token0, token1, poolFee);
        
        bytes32 positionId = keccak256(abi.encodePacked(
            msg.sender,
            block.timestamp,
            amount,
            token0,
            token1
        ));
        
        userBalances[msg.sender] -= amount;
        totalLockedLiquidity += amount;
        
        positions[positionId] = Position({
            user: msg.sender,
            amount: amount,
            direction: direction,
            entryPrice: currentPrice,
            startTime: block.timestamp,
            exitTime: block.timestamp + predictionDuration,
            exitPrice: 0,
            settled: false,
            token0: token0,
            token1: token1,
            poolFee: poolFee
        });
        
        activePositions.push(positionId);
        
        emit PositionOpened(positionId, msg.sender, amount, direction, token0, token1);
        return positionId;
    }
    
    function settlePosition(bytes32 positionId) external {
        Position storage position = positions[positionId];
        require(position.amount > 0, "Position not found");
        require(!position.settled, "Position already settled");
        require(block.timestamp >= position.exitTime, "Position cannot be settled before exit time");
        
        uint256 exitPrice = getUniswapV3Price(position.token0, position.token1, position.poolFee);
        position.exitPrice = exitPrice;
        position.settled = true;
        
        // Remove from active positions
        for (uint i = 0; i < activePositions.length; i++) {
            if (activePositions[i] == positionId) {
                activePositions[i] = activePositions[activePositions.length - 1];
                activePositions.pop();
                break;
            }
        }
        settledPositions.push(positionId);
        
        bool profitable = isProfitable(positionId);
        uint256 profit = calculateProfit(positionId);
        
        emit PositionSettled(positionId, profitable, profit);
    }
    
    function isProfitable(bytes32 positionId) public view returns (bool) {
        Position memory position = positions[positionId];
        require(position.exitPrice > 0, "Position not settled");
        
        if (position.direction == Direction.UP) {
            return position.exitPrice > position.entryPrice;
        } else {
            return position.exitPrice < position.entryPrice;
        }
    }
    
    function calculateProfit(bytes32 positionId) public view returns (uint256) {
        Position memory position = positions[positionId];
        require(position.exitPrice > 0, "Position not settled");
        
        int256 priceChange = int256(position.exitPrice) - int256(position.entryPrice);
        
        if (position.direction == Direction.DOWN) {
            priceChange = -priceChange;
        }
        
        if (priceChange <= 0) return 0;
        
        return (position.amount * uint256(priceChange)) / position.entryPrice;
    }
    
    function distributeRewards(address token0, address token1) external {
        bytes32[] memory relevantPositions = new bytes32[](settledPositions.length);
        uint256 count = 0;
        
        for (uint i = 0; i < settledPositions.length; i++) {
            Position memory pos = positions[settledPositions[i]];
            if (pos.token0 == token0 && pos.token1 == token1) {
                relevantPositions[count] = settledPositions[i];
                count++;
            }
        }
        
        require(count > 0, "No positions to distribute");
        
        uint256 totalWinningAmount = 0;
        uint256 totalLosingAmount = 0;
        uint256 winnerCount = 0;
        
        for (uint i = 0; i < count; i++) {
            if (isProfitable(relevantPositions[i])) {
                totalWinningAmount += positions[relevantPositions[i]].amount;
                winnerCount++;
            } else {
                totalLosingAmount += positions[relevantPositions[i]].amount;
            }
        }
        
        if (winnerCount == 0) {
            for (uint i = 0; i < count; i++) {
                Position memory pos = positions[relevantPositions[i]];
                userBalances[pos.user] += pos.amount;
                totalLockedLiquidity -= pos.amount;
            }
            emit RewardsDistributed(token0, token1, 0, 0);
            return;
        }
        
        uint256 rewardPool = (totalLosingAmount * rewardPoolPercentage) / 100;
        
        for (uint i = 0; i < count; i++) {
            if (isProfitable(relevantPositions[i])) {
                Position memory pos = positions[relevantPositions[i]];
                uint256 rewardShare = (pos.amount * rewardPool) / totalWinningAmount;
                uint256 totalReturn = pos.amount + rewardShare;
                
                userBalances[pos.user] += totalReturn;
                totalLockedLiquidity -= pos.amount;
            }
        }
        
        emit RewardsDistributed(token0, token1, rewardPool, winnerCount);
    }
    
    // ============ VIEW FUNCTIONS ============
    
    function getBalance(address user) external view returns (uint256) {
        return userBalances[user];
    }
    
    function getActivePositionsCount() external view returns (uint256) {
        return activePositions.length;
    }
    
    function getPosition(bytes32 positionId) external view returns (
        address user,
        uint256 amount,
        Direction direction,
        uint256 entryPrice,
        uint256 exitTime,
        uint256 exitPrice,
        bool settled,
        address token0,
        address token1
    ) {
        Position memory pos = positions[positionId];
        return (
            pos.user,
            pos.amount,
            pos.direction,
            pos.entryPrice,
            pos.exitTime,
            pos.exitPrice,
            pos.settled,
            pos.token0,
            pos.token1
        );
    }
    
    receive() external payable {
        deposit();
    }
}