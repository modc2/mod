// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title PredictionMarket
 * @dev 24-Hour Prediction Market Smart Contract
 * Users lock liquidity to predict whether an asset will go up or down in 24 hours
 */
contract PredictionMarket {
    
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
        string asset;
    }
    
    mapping(bytes32 => Position) public positions;
    mapping(address => uint256) public userBalances;
    bytes32[] public activePositions;
    bytes32[] public settledPositions;
    
    uint256 public totalLockedLiquidity;
    uint256 public rewardPoolPercentage = 95; // 95%
    
    event PositionOpened(bytes32 indexed positionId, address indexed user, uint256 amount, Direction direction, string asset);
    event PositionSettled(bytes32 indexed positionId, bool profitable, uint256 profit);
    event RewardsDistributed(string asset, uint256 totalDistributed, uint256 winners);
    event Deposit(address indexed user, uint256 amount);
    event Withdrawal(address indexed user, uint256 amount);
    
    /**
     * @dev Deposit funds to user balance
     */
    function deposit() external payable {
        require(msg.value > 0, "Amount must be positive");
        userBalances[msg.sender] += msg.value;
        emit Deposit(msg.sender, msg.value);
    }
    
    /**
     * @dev Withdraw funds from user balance
     */
    function withdraw(uint256 amount) external {
        require(amount > 0, "Amount must be positive");
        require(userBalances[msg.sender] >= amount, "Insufficient balance");
        
        userBalances[msg.sender] -= amount;
        payable(msg.sender).transfer(amount);
        emit Withdrawal(msg.sender, amount);
    }
    
    /**
     * @dev Open a new 24-hour prediction position
     */
    function openPosition(
        uint256 amount,
        Direction direction,
        uint256 currentPrice,
        string memory asset
    ) external returns (bytes32) {
        require(amount > 0, "Amount must be positive");
        require(userBalances[msg.sender] >= amount, "Insufficient balance");
        require(currentPrice > 0, "Invalid price");
        
        bytes32 positionId = keccak256(abi.encodePacked(
            msg.sender,
            block.timestamp,
            amount,
            asset
        ));
        
        userBalances[msg.sender] -= amount;
        totalLockedLiquidity += amount;
        
        positions[positionId] = Position({
            user: msg.sender,
            amount: amount,
            direction: direction,
            entryPrice: currentPrice,
            startTime: block.timestamp,
            exitTime: block.timestamp + 24 hours,
            exitPrice: 0,
            settled: false,
            asset: asset
        });
        
        activePositions.push(positionId);
        
        emit PositionOpened(positionId, msg.sender, amount, direction, asset);
        return positionId;
    }
    
    /**
     * @dev Settle a position after 24 hours
     */
    function settlePosition(bytes32 positionId, uint256 exitPrice) external {
        Position storage position = positions[positionId];
        require(position.amount > 0, "Position not found");
        require(!position.settled, "Position already settled");
        require(block.timestamp >= position.exitTime, "Position cannot be settled before exit time");
        require(exitPrice > 0, "Invalid exit price");
        
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
    
    /**
     * @dev Check if position is profitable
     */
    function isProfitable(bytes32 positionId) public view returns (bool) {
        Position memory position = positions[positionId];
        require(position.exitPrice > 0, "Position not settled");
        
        if (position.direction == Direction.UP) {
            return position.exitPrice > position.entryPrice;
        } else {
            return position.exitPrice < position.entryPrice;
        }
    }
    
    /**
     * @dev Calculate profit/loss for a position
     */
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
    
    /**
     * @dev Distribute rewards to profitable traders for a specific asset
     */
    function distributeRewards(string memory asset) external {
        bytes32[] memory relevantPositions = new bytes32[](settledPositions.length);
        uint256 count = 0;
        
        // Find settled positions for this asset
        for (uint i = 0; i < settledPositions.length; i++) {
            if (keccak256(bytes(positions[settledPositions[i]].asset)) == keccak256(bytes(asset))) {
                relevantPositions[count] = settledPositions[i];
                count++;
            }
        }
        
        require(count > 0, "No positions to distribute");
        
        uint256 totalWinningAmount = 0;
        uint256 totalLosingAmount = 0;
        uint256 winnerCount = 0;
        
        // Calculate totals
        for (uint i = 0; i < count; i++) {
            if (isProfitable(relevantPositions[i])) {
                totalWinningAmount += positions[relevantPositions[i]].amount;
                winnerCount++;
            } else {
                totalLosingAmount += positions[relevantPositions[i]].amount;
            }
        }
        
        if (winnerCount == 0) {
            // Return funds to losers if no winners
            for (uint i = 0; i < count; i++) {
                Position memory pos = positions[relevantPositions[i]];
                userBalances[pos.user] += pos.amount;
                totalLockedLiquidity -= pos.amount;
            }
            emit RewardsDistributed(asset, 0, 0);
            return;
        }
        
        uint256 rewardPool = (totalLosingAmount * rewardPoolPercentage) / 100;
        
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
        
        emit RewardsDistributed(asset, rewardPool, winnerCount);
    }
    
    /**
     * @dev Get user balance
     */
    function getBalance(address user) external view returns (uint256) {
        return userBalances[user];
    }
    
    /**
     * @dev Get active positions count
     */
    function getActivePositionsCount() external view returns (uint256) {
        return activePositions.length;
    }
    
    /**
     * @dev Get position details
     */
    function getPosition(bytes32 positionId) external view returns (
        address user,
        uint256 amount,
        Direction direction,
        uint256 entryPrice,
        uint256 exitTime,
        uint256 exitPrice,
        bool settled,
        string memory asset
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
            pos.asset
        );
    }
    
    receive() external payable {
        deposit();
    }
}