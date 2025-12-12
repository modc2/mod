// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Pool.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Factory.sol";
import "@uniswap/v3-periphery/contracts/interfaces/ISwapRouter.sol";
import "@uniswap/v3-periphery/contracts/interfaces/IQuoter.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title UltimatePredictionMarket
 * @dev Super Modular Uniswap V3 Prediction Market with:
 * - USD profit calculation
 * - Treasury allocation (N% locked for protocol)
 * - Token minting on profitable trades
 * - Token burning on losing trades
 * - Full Uniswap V3 integration
 */
contract UltimatePredictionMarket is ERC20, Ownable {
    
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
        uint256 usdValueAtEntry;
    }
    
    // Core state
    mapping(bytes32 => Position) public positions;
    mapping(address => uint256) public userBalances;
    mapping(address => uint256) public userTokenBalances;
    bytes32[] public activePositions;
    bytes32[] public settledPositions;
    
    // Uniswap V3 integration
    IUniswapV3Factory public immutable uniswapFactory;
    ISwapRouter public immutable swapRouter;
    IQuoter public immutable quoter;
    
    // Treasury and tokenomics - MODULAR CONFIG
    address public treasury;
    uint256 public treasuryPercentage = 10; // N% to treasury
    uint256 public totalLockedLiquidity;
    uint256 public rewardPoolPercentage = 90;
    uint256 public predictionDuration = 24 hours;
    uint256 public treasuryBalance;
    
    // USD calculation
    address public usdToken; // USDC or other stablecoin
    uint256 public tokenMintRatio = 1000; // Tokens per $1 profit
    uint256 public tokenBurnRatio = 1000; // Tokens per $1 loss
    
    // Events
    event PositionOpened(bytes32 indexed positionId, address indexed user, uint256 amount, Direction direction, address token0, address token1, uint256 usdValue);
    event PositionSettled(bytes32 indexed positionId, bool profitable, uint256 profitUSD, uint256 lossUSD);
    event TokensMinted(address indexed user, uint256 amount, uint256 profitUSD);
    event TokensBurned(address indexed user, uint256 amount, uint256 lossUSD);
    event TreasuryDeposit(uint256 amount, uint256 usdValue);
    event RewardsDistributed(address token0, address token1, uint256 totalDistributed, uint256 treasuryAmount, uint256 winners);
    event Deposit(address indexed user, uint256 amount);
    event Withdrawal(address indexed user, uint256 amount);
    event ConfigUpdated(string param, uint256 value);
    
    constructor(
        address _uniswapFactory,
        address _swapRouter,
        address _quoter,
        address _treasury,
        address _usdToken,
        string memory _tokenName,
        string memory _tokenSymbol
    ) ERC20(_tokenName, _tokenSymbol) Ownable(msg.sender) {
        uniswapFactory = IUniswapV3Factory(_uniswapFactory);
        swapRouter = ISwapRouter(_swapRouter);
        quoter = IQuoter(_quoter);
        treasury = _treasury;
        usdToken = _usdToken;
    }
    
    // ============ MODULAR CONFIGURATION ============
    
    function setTreasuryPercentage(uint256 _percentage) external onlyOwner {
        require(_percentage <= 50, "Treasury percentage too high");
        treasuryPercentage = _percentage;
        rewardPoolPercentage = 100 - _percentage;
        emit ConfigUpdated("treasuryPercentage", _percentage);
    }
    
    function setTreasury(address _treasury) external onlyOwner {
        require(_treasury != address(0), "Invalid treasury address");
        treasury = _treasury;
    }
    
    function setPredictionDuration(uint256 _duration) external onlyOwner {
        require(_duration > 0, "Invalid duration");
        predictionDuration = _duration;
        emit ConfigUpdated("predictionDuration", _duration);
    }
    
    function setUsdToken(address _usdToken) external onlyOwner {
        require(_usdToken != address(0), "Invalid USD token");
        usdToken = _usdToken;
    }
    
    function setTokenMintRatio(uint256 _ratio) external onlyOwner {
        tokenMintRatio = _ratio;
        emit ConfigUpdated("tokenMintRatio", _ratio);
    }
    
    function setTokenBurnRatio(uint256 _ratio) external onlyOwner {
        tokenBurnRatio = _ratio;
        emit ConfigUpdated("tokenBurnRatio", _ratio);
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
        
        uint256 price = uint256(sqrtPriceX96) * uint256(sqrtPriceX96) * 1e18 >> (96 * 2);
        return price;
    }
    
    function getUSDValue(address token, uint256 amount) public view returns (uint256) {
        if (token == usdToken) return amount;
        
        address poolAddress = uniswapFactory.getPool(token, usdToken, 3000);
        if (poolAddress == address(0)) return 0;
        
        IUniswapV3Pool pool = IUniswapV3Pool(poolAddress);
        (uint160 sqrtPriceX96,,,,,,) = pool.slot0();
        
        uint256 price = uint256(sqrtPriceX96) * uint256(sqrtPriceX96) * 1e18 >> (96 * 2);
        return (amount * price) / 1e18;
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
        uint256 usdValue = getUSDValue(token0, amount);
        
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
            poolFee: poolFee,
            usdValueAtEntry: usdValue
        });
        
        activePositions.push(positionId);
        
        emit PositionOpened(positionId, msg.sender, amount, direction, token0, token1, usdValue);
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
        uint256 profitUSD = 0;
        uint256 lossUSD = 0;
        
        // MINT TOKENS FOR PROFIT, BURN FOR LOSS
        if (profitable) {
            profitUSD = calculateProfitUSD(positionId);
            if (profitUSD > 0) {
                uint256 tokensToMint = (profitUSD * tokenMintRatio) / 1e18;
                _mint(position.user, tokensToMint);
                userTokenBalances[position.user] += tokensToMint;
                emit TokensMinted(position.user, tokensToMint, profitUSD);
            }
        } else {
            lossUSD = calculateLossUSD(positionId);
            if (lossUSD > 0) {
                uint256 tokensToBurn = (lossUSD * tokenBurnRatio) / 1e18;
                uint256 userBalance = balanceOf(position.user);
                if (userBalance > 0) {
                    uint256 burnAmount = tokensToBurn > userBalance ? userBalance : tokensToBurn;
                    _burn(position.user, burnAmount);
                    userTokenBalances[position.user] = userTokenBalances[position.user] > burnAmount ? 
                        userTokenBalances[position.user] - burnAmount : 0;
                    emit TokensBurned(position.user, burnAmount, lossUSD);
                }
            }
        }
        
        emit PositionSettled(positionId, profitable, profitUSD, lossUSD);
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
    
    function calculateProfitUSD(bytes32 positionId) public view returns (uint256) {
        Position memory position = positions[positionId];
        require(position.exitPrice > 0, "Position not settled");
        
        if (!isProfitable(positionId)) return 0;
        
        int256 priceChange = int256(position.exitPrice) - int256(position.entryPrice);
        if (position.direction == Direction.DOWN) {
            priceChange = -priceChange;
        }
        
        if (priceChange <= 0) return 0;
        
        uint256 profitInToken = (position.amount * uint256(priceChange)) / position.entryPrice;
        return getUSDValue(position.token0, profitInToken);
    }
    
    function calculateLossUSD(bytes32 positionId) public view returns (uint256) {
        Position memory position = positions[positionId];
        require(position.exitPrice > 0, "Position not settled");
        
        if (isProfitable(positionId)) return 0;
        
        int256 priceChange = int256(position.exitPrice) - int256(position.entryPrice);
        if (position.direction == Direction.DOWN) {
            priceChange = -priceChange;
        }
        
        if (priceChange >= 0) return 0;
        
        uint256 lossInToken = (position.amount * uint256(-priceChange)) / position.entryPrice;
        return getUSDValue(position.token0, lossInToken);
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
            emit RewardsDistributed(token0, token1, 0, 0, 0);
            return;
        }
        
        // LOCK N% TO TREASURY
        uint256 treasuryCut = (totalLosingAmount * treasuryPercentage) / 100;
        uint256 rewardPool = (totalLosingAmount * rewardPoolPercentage) / 100;
        
        // Send to treasury
        if (treasuryCut > 0) {
            treasuryBalance += treasuryCut;
            uint256 treasuryUSD = getUSDValue(token0, treasuryCut);
            emit TreasuryDeposit(treasuryCut, treasuryUSD);
        }
        
        // Distribute to winners
        for (uint i = 0; i < count; i++) {
            if (isProfitable(relevantPositions[i])) {
                Position memory pos = positions[relevantPositions[i]];
                uint256 rewardShare = (pos.amount * rewardPool) / totalWinningAmount;
                uint256 totalReturn = pos.amount + rewardShare;
                
                userBalances[pos.user] += totalReturn;
                totalLockedLiquidity -= pos.amount;
            }
        }
        
        emit RewardsDistributed(token0, token1, rewardPool, treasuryCut, winnerCount);
    }
    
    function withdrawTreasury() external onlyOwner {
        require(treasuryBalance > 0, "No treasury balance");
        uint256 amount = treasuryBalance;
        treasuryBalance = 0;
        payable(treasury).transfer(amount);
    }
    
    // ============ VIEW FUNCTIONS ============
    
    function getBalance(address user) external view returns (uint256) {
        return userBalances[user];
    }
    
    function getTokenBalance(address user) external view returns (uint256) {
        return balanceOf(user);
    }
    
    function getTreasuryBalance() external view returns (uint256) {
        return treasuryBalance;
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
        address token1,
        uint256 usdValueAtEntry
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
            pos.token1,
            pos.usdValueAtEntry
        );
    }
    
    receive() external payable {
        deposit();
    }
}