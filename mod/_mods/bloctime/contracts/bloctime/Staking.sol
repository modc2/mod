// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title BlocTimeToken
 * @dev ERC20 token representing bloctime - minted based on staking duration
 */
contract BlocTimeToken is ERC20, Ownable {
    address public stakingContract;
    
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {}
    
    function setStakingContract(address _stakingContract) external onlyOwner {
        require(stakingContract == address(0), "Already set");
        stakingContract = _stakingContract;
    }
    
    function mint(address to, uint256 amount) external {
        require(msg.sender == stakingContract, "Only staking contract");
        _mint(to, amount);
    }
    
    function burn(address from, uint256 amount) external {
        require(msg.sender == stakingContract, "Only staking contract");
        _burn(from, amount);
    }
}

/**
 * @title BlocTimeStaking
 * @dev Stake native tokens for blocks, mint bloctime tokens based on duration multiplier
 * Distributes treasury rewards proportionally to bloctime holdings
 * Simple point-wise monotonic multiplier function
 */
contract BlocTimeStaking is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;
    
    struct Stake {
        uint256 amount;
        uint256 startBlock;
        uint256 lockBlocks;
        uint256 blocTimeBalance;
    }
    
    struct Point {
        uint256 blocks;
        uint256 multiplier; // in basis points (10000 = 1x)
    }
    
    IERC20 public nativeToken;
    BlocTimeToken public blocTimeToken;
    
    uint256 public maxLockBlocks;
    uint256 public treasuryBalance;
    uint256 public totalBlocTime;
    uint256 public distributionPercentage; // Percentage of treasury to distribute (in basis points)
    
    mapping(address => Stake) public stakes;
    Point[] public points;
    
    event Staked(address indexed user, uint256 amount, uint256 lockBlocks, uint256 blocTimeEarned);
    event Unstaked(address indexed user, uint256 amount, uint256 blocTimeReturned);
    event TreasuryFunded(uint256 amount);
    event RewardsClaimed(address indexed user, uint256 amount);
    event MaxBlocksUpdated(uint256 newMaxBlocks);
    event DistributionPercentageUpdated(uint256 newPercentage);
    event PointsSet(uint256 pointCount);
    
    constructor(
        address _nativeToken,
        string memory _blocTimeName,
        string memory _blocTimeSymbol,
        uint256 _maxLockBlocks,
        uint256 _distributionPercentage
    ) {
        require(_distributionPercentage <= 10000, "Max 100%");
        
        nativeToken = IERC20(_nativeToken);
        blocTimeToken = new BlocTimeToken(_blocTimeName, _blocTimeSymbol);
        blocTimeToken.setStakingContract(address(this));
        
        maxLockBlocks = _maxLockBlocks;
        distributionPercentage = _distributionPercentage;
        
        // Default point: 0 blocks = 1x multiplier
        points.push(Point({
            blocks: 0,
            multiplier: 10000
        }));
    }
    
    /**
     * @dev Set all points at once - enforces monotonicity
     * Points must be sorted by blocks (ascending) and multipliers must be non-decreasing
     */
    function setPoints(Point[] calldata _points) external onlyOwner {
        require(_points.length > 0, "Must provide at least one point");
        
        // Validate monotonicity
        for (uint256 i = 0; i < _points.length; i++) {
            require(_points[i].multiplier >= 10000, "Multiplier must be >= 1x");
            require(_points[i].blocks <= maxLockBlocks, "Exceeds max blocks");
            
            if (i > 0) {
                require(_points[i].blocks > _points[i-1].blocks, "Blocks must be monotonically increasing");
                require(_points[i].multiplier >= _points[i-1].multiplier, "Multiplier must be monotonically non-decreasing");
            }
        }
        
        // Clear existing points and set new ones
        delete points;
        for (uint256 i = 0; i < _points.length; i++) {
            points.push(_points[i]);
        }
        
        emit PointsSet(_points.length);
    }
    
    /**
     * @dev Get multiplier for a given block count using linear interpolation
     */
    function getMultiplier(uint256 blockCount) public view returns (uint256) {
        if (points.length == 0) {
            return 10000; // Default 1x
        }
        
        // Before first point
        if (blockCount <= points[0].blocks) {
            return points[0].multiplier;
        }
        
        // After last point
        if (blockCount >= points[points.length - 1].blocks) {
            return points[points.length - 1].multiplier;
        }
        
        // Find the two points to interpolate between
        for (uint256 i = 0; i < points.length - 1; i++) {
            if (blockCount >= points[i].blocks && blockCount <= points[i + 1].blocks) {
                return interpolate(
                    points[i].blocks,
                    points[i].multiplier,
                    points[i + 1].blocks,
                    points[i + 1].multiplier,
                    blockCount
                );
            }
        }
        
        return points[points.length - 1].multiplier;
    }
    
    /**
     * @dev Linear interpolation between two points
     */
    function interpolate(
        uint256 x0,
        uint256 y0,
        uint256 x1,
        uint256 y1,
        uint256 x
    ) internal pure returns (uint256) {
        if (x0 == x1) return y0;
        
        uint256 range = x1 - x0;
        uint256 position = x - x0;
        uint256 yRange = y1 - y0;
        
        return y0 + (yRange * position) / range;
    }
    
    /**
     * @dev Get all points
     */
    function getAllPoints() external view returns (Point[] memory) {
        return points;
    }
    
    /**
     * @dev Get number of points
     */
    function getPointCount() external view returns (uint256) {
        return points.length;
    }
    
    /**
     * @dev Set max lock blocks
     */
    function setMaxLockBlocks(uint256 _maxLockBlocks) external onlyOwner {
        maxLockBlocks = _maxLockBlocks;
        emit MaxBlocksUpdated(_maxLockBlocks);
    }
    
    /**
     * @dev Set distribution percentage
     */
    function setDistributionPercentage(uint256 _percentage) external onlyOwner {
        require(_percentage <= 10000, "Max 100%");
        distributionPercentage = _percentage;
        emit DistributionPercentageUpdated(_percentage);
    }
    
    /**
     * @dev Stake tokens for specified number of blocks
     */
    function stake(uint256 amount, uint256 lockBlocks) external nonReentrant {
        require(amount > 0, "Amount must be > 0");
        require(lockBlocks <= maxLockBlocks, "Exceeds max lock blocks");
        require(stakes[msg.sender].amount == 0, "Already staking");
        
        nativeToken.safeTransferFrom(msg.sender, address(this), amount);
        
        uint256 multiplier = getMultiplier(lockBlocks);
        uint256 blocTimeEarned = (amount * multiplier) / 10000;
        
        stakes[msg.sender] = Stake({
            amount: amount,
            startBlock: block.number,
            lockBlocks: lockBlocks,
            blocTimeBalance: blocTimeEarned
        });
        
        totalBlocTime += blocTimeEarned;
        blocTimeToken.mint(msg.sender, blocTimeEarned);
        
        emit Staked(msg.sender, amount, lockBlocks, blocTimeEarned);
    }
    
    /**
     * @dev Unstake tokens after lock period
     */
    function unstake() external nonReentrant {
        Stake storage userStake = stakes[msg.sender];
        require(userStake.amount > 0, "No active stake");
        require(block.number >= userStake.startBlock + userStake.lockBlocks, "Still locked");
        
        uint256 amount = userStake.amount;
        uint256 blocTimeBalance = userStake.blocTimeBalance;
        
        totalBlocTime -= blocTimeBalance;
        blocTimeToken.burn(msg.sender, blocTimeBalance);
        
        delete stakes[msg.sender];
        
        nativeToken.safeTransfer(msg.sender, amount);
        
        emit Unstaked(msg.sender, amount, blocTimeBalance);
    }
    
    /**
     * @dev Fund treasury for distribution (can be called by marketplace or owner)
     */
    function fundTreasury(uint256 amount) external nonReentrant {
        require(amount > 0, "Amount must be > 0");
        nativeToken.safeTransferFrom(msg.sender, address(this), amount);
        treasuryBalance += amount;
        emit TreasuryFunded(amount);
    }
    
    /**
     * @dev Calculate pending rewards for user
     */
    function pendingRewards(address user) public view returns (uint256) {
        if (totalBlocTime == 0 || treasuryBalance == 0) {
            return 0;
        }
        
        uint256 userBlocTime = blocTimeToken.balanceOf(user);
        uint256 distributionAmount = (treasuryBalance * distributionPercentage) / 10000;
        
        return (distributionAmount * userBlocTime) / totalBlocTime;
    }
    
    /**
     * @dev Claim rewards from treasury
     */
    function claimRewards() external nonReentrant {
        uint256 rewards = pendingRewards(msg.sender);
        require(rewards > 0, "No rewards available");
        require(treasuryBalance >= rewards, "Insufficient treasury");
        
        treasuryBalance -= rewards;
        nativeToken.safeTransfer(msg.sender, rewards);
        
        emit RewardsClaimed(msg.sender, rewards);
    }
    
    /**
     * @dev Get stake info for user
     */
    function getStakeInfo(address user) external view returns (
        uint256 amount,
        uint256 startBlock,
        uint256 lockBlocks,
        uint256 blocTimeBalance,
        uint256 blocksRemaining,
        uint256 rewards
    ) {
        Stake storage userStake = stakes[user];
        uint256 elapsed = block.number > userStake.startBlock ? block.number - userStake.startBlock : 0;
        uint256 remaining = userStake.lockBlocks > elapsed ? userStake.lockBlocks - elapsed : 0;
        
        return (
            userStake.amount,
            userStake.startBlock,
            userStake.lockBlocks,
            userStake.blocTimeBalance,
            remaining,
            pendingRewards(user)
        );
    }
    
    /**
     * @dev Emergency withdraw (owner only, for migrations)
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }
}