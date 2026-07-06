// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title BlocTime
 * @dev Unified BlocTime contract - combines token and staking functionality
 * Stake native tokens for blocks, mint bloctime tokens based on duration multiplier
 */
contract BlocTime is ERC20, ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;
    
    struct Stake {
        uint256 amount;
        uint256 startBlock;
        uint256 lockBlocks;
        uint256 blocTimeBalance;
    }
    
    struct StakePosition {
        uint256 stakeId;
        uint256 amount;
        uint256 startBlock;
        uint256 lockBlocks;
        uint256 blocTimeBalance;
    }
    
    struct Point {
        uint256 blocks;
        uint256 multiplier; // in basis points (10000 = 1x)
    }

    struct Params {
        uint256 maxLockBlocks;
        uint256 distributionPercentage;
    }
    
    IERC20 public nativeToken;
    uint256 public totalBlocTime;
    uint256 public nextStakeId;
    
    mapping(address => mapping(uint256 => StakePosition)) public userStakes;
    mapping(address => uint256[]) public userStakeIds;
    mapping(address => Stake) public stakes;
    
    Point[] public points;
    Params public params;
    
    event Staked(address indexed user, uint256 stakeId, uint256 amount, uint256 lockBlocks, uint256 blocTimeEarned);
    event Unstaked(address indexed user, uint256 stakeId, uint256 amount, uint256 blocTimeReturned);
    event ParamsUpdated(Params params);
    event PointsSet(uint256 pointCount);
    event ContractSetOwnerless();

    constructor(
        address _nativeToken,
        string memory _name,
        string memory _symbol,
        uint256 _maxLockBlocks,
        uint256 _distributionPercentage
    ) ERC20(_name, _symbol) {
        require(_distributionPercentage <= 10000, "Max 100%");
        
        nativeToken = IERC20(_nativeToken);
        
        params = Params({
            distributionPercentage: _distributionPercentage,
            maxLockBlocks: _maxLockBlocks
        });
        
        // Default point: 0 blocks = 1x multiplier
        points.push(Point({
            blocks: 0,
            multiplier: 10000
        }));
    }
    
    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setPoints, setParams, emergencyWithdraw.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    /**
     * @dev Set all points at once - enforces monotonicity
     */
    function setPoints(Point[] calldata _points) external onlyOwner {
        require(_points.length > 0, "Must provide at least one point");
        
        for (uint256 i = 0; i < _points.length; i++) {
            require(_points[i].multiplier >= 10000, "Multiplier must be >= 1x");
            require(_points[i].blocks <= params.maxLockBlocks, "Exceeds max blocks");
            
            if (i > 0) {
                require(_points[i].blocks > _points[i-1].blocks, "Blocks must be monotonically increasing");
                require(_points[i].multiplier >= _points[i-1].multiplier, "Multiplier must be monotonically non-decreasing");
            }
        }
        
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
            return 10000;
        }
        
        if (blockCount <= points[0].blocks) {
            return points[0].multiplier;
        }
        
        if (blockCount >= points[points.length - 1].blocks) {
            return points[points.length - 1].multiplier;
        }
        
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
     * @dev Set params
     */
    function setParams(uint256 _maxLockBlocks, uint256 _distributionPercentage) external onlyOwner {
        require(_distributionPercentage <= 10000, "Max 100%");
        params = Params({maxLockBlocks: _maxLockBlocks, distributionPercentage: _distributionPercentage});
        emit ParamsUpdated(params);
    }
    
    /**
     * @dev Stake tokens for specified number of blocks
     */
    function stake(uint256 amount, uint256 lockBlocks) external nonReentrant {
        require(amount > 0, "Amount must be > 0");
        require(lockBlocks <= params.maxLockBlocks, "Exceeds max lock blocks");
        
        nativeToken.safeTransferFrom(msg.sender, address(this), amount);
        
        uint256 multiplier = getMultiplier(lockBlocks);
        uint256 blocTimeEarned = (amount * multiplier) / 10000;
        
        uint256 stakeId = nextStakeId++;
        
        userStakes[msg.sender][stakeId] = StakePosition({
            stakeId: stakeId,
            amount: amount,
            startBlock: block.number,
            lockBlocks: lockBlocks,
            blocTimeBalance: blocTimeEarned
        });
        
        userStakeIds[msg.sender].push(stakeId);
        
        stakes[msg.sender].amount += amount;
        stakes[msg.sender].blocTimeBalance += blocTimeEarned;
        
        totalBlocTime += blocTimeEarned;
        _mint(msg.sender, blocTimeEarned);
        
        emit Staked(msg.sender, stakeId, amount, lockBlocks, blocTimeEarned);
    }
    
    /**
     * @dev Unstake specific stake position after lock period
     */
    function unstake(uint256 stakeId) external nonReentrant {
        StakePosition storage position = userStakes[msg.sender][stakeId];
        require(position.amount > 0, "No active stake");
        require(block.number >= position.startBlock + position.lockBlocks, "Still locked");
        
        uint256 amount = position.amount;
        uint256 blocTimeBalance = position.blocTimeBalance;
        
        totalBlocTime -= blocTimeBalance;
        _burn(msg.sender, blocTimeBalance);
        
        stakes[msg.sender].amount -= amount;
        stakes[msg.sender].blocTimeBalance -= blocTimeBalance;
        
        uint256[] storage stakeIds = userStakeIds[msg.sender];
        for (uint256 i = 0; i < stakeIds.length; i++) {
            if (stakeIds[i] == stakeId) {
                stakeIds[i] = stakeIds[stakeIds.length - 1];
                stakeIds.pop();
                break;
            }
        }
        
        delete userStakes[msg.sender][stakeId];
        
        nativeToken.safeTransfer(msg.sender, amount);
        
        emit Unstaked(msg.sender, stakeId, amount, blocTimeBalance);
    }
    
    /**
     * @dev Get all stake IDs for a user
     */
    function getUserStakeIds(address user) external view returns (uint256[] memory) {
        return userStakeIds[user];
    }
    
    /**
     * @dev Get specific stake position info
     */
    function getStakePosition(address user, uint256 stakeId) external view returns (
        uint256 amount,
        uint256 startBlock,
        uint256 lockBlocks,
        uint256 blocTimeBalance,
        uint256 blocksRemaining
    ) {
        StakePosition storage position = userStakes[user][stakeId];
        uint256 elapsed = block.number > position.startBlock ? block.number - position.startBlock : 0;
        uint256 remaining = position.lockBlocks > elapsed ? position.lockBlocks - elapsed : 0;
        
        return (
            position.amount,
            position.startBlock,
            position.lockBlocks,
            position.blocTimeBalance,
            remaining
        );
    }
    
    /**
     * @dev Get stake info for user (legacy)
     */
    function getStakeInfo(address user) external view returns (
        uint256 amount,
        uint256 startBlock,
        uint256 lockBlocks,
        uint256 blocTimeBalance,
        uint256 blocksRemaining
    ) {
        Stake storage userStake = stakes[user];
        uint256 elapsed = block.number > userStake.startBlock ? block.number - userStake.startBlock : 0;
        uint256 remaining = userStake.lockBlocks > elapsed ? userStake.lockBlocks - elapsed : 0;
        
        return (
            userStake.amount,
            userStake.startBlock,
            userStake.lockBlocks,
            userStake.blocTimeBalance,
            remaining
        );
    }
    
    /**
     * @dev Emergency withdraw (owner only)
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }
}
