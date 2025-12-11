// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract ChurnStaking is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct StakeInfo {
        uint256 amount;
        uint256 startTime;
        uint256 lastClaimTime;
        uint256 multiplier; // locked multiplier in basis points (10000 = 1x)
    }

    IERC20 public stakingToken;
    mapping(address => StakeInfo) public stakes;
    
    uint256 public totalStakeTime; // sum of all stake * time
    uint256 public treasuryBalance;
    uint256 public constant MAX_MULTIPLIER = 30000; // 3x max
    uint256 public constant MIN_MULTIPLIER = 10000; // 1x min
    uint256 public constant TIME_MULTIPLIER_PERIOD = 365 days; // time to reach max multiplier
    uint256 public constant BASIS_POINTS = 10000;

    event Staked(address indexed user, uint256 amount, uint256 multiplier);
    event Unstaked(address indexed user, uint256 amount);
    event RewardsClaimed(address indexed user, uint256 amount);
    event TreasuryFunded(uint256 amount);

    constructor(address _stakingToken) {
        stakingToken = IERC20(_stakingToken);
    }

    function stake(uint256 amount, uint256 lockMultiplier) external nonReentrant {
        require(amount > 0, "Cannot stake 0");
        require(lockMultiplier >= MIN_MULTIPLIER && lockMultiplier <= MAX_MULTIPLIER, "Invalid multiplier");
        require(stakes[msg.sender].amount == 0, "Already staking, unstake first");

        stakingToken.safeTransferFrom(msg.sender, address(this), amount);

        stakes[msg.sender] = StakeInfo({
            amount: amount,
            startTime: block.timestamp,
            lastClaimTime: block.timestamp,
            multiplier: lockMultiplier
        });

        emit Staked(msg.sender, amount, lockMultiplier);
    }

    function unstake() external nonReentrant {
        StakeInfo memory stakeInfo = stakes[msg.sender];
        require(stakeInfo.amount > 0, "No stake found");

        // Claim pending rewards before unstaking
        _claimRewards(msg.sender);

        uint256 amount = stakeInfo.amount;
        delete stakes[msg.sender];

        stakingToken.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    function claimRewards() external nonReentrant {
        _claimRewards(msg.sender);
    }

    function _claimRewards(address user) internal {
        StakeInfo storage stakeInfo = stakes[user];
        require(stakeInfo.amount > 0, "No stake found");

        uint256 rewards = calculateRewards(user);
        if (rewards > 0 && treasuryBalance >= rewards) {
            stakeInfo.lastClaimTime = block.timestamp;
            treasuryBalance -= rewards;
            stakingToken.safeTransfer(user, rewards);
            emit RewardsClaimed(user, rewards);
        }
    }

    function calculateRewards(address user) public view returns (uint256) {
        StakeInfo memory stakeInfo = stakes[user];
        if (stakeInfo.amount == 0) return 0;

        uint256 timeElapsed = block.timestamp - stakeInfo.lastClaimTime;
        uint256 userStakeTime = getStakeTimeTokens(user);
        uint256 totalStakeTimeSnapshot = getTotalStakeTime();

        if (totalStakeTimeSnapshot == 0) return 0;

        // User's share of treasury based on their stake-time tokens
        uint256 userShare = (userStakeTime * BASIS_POINTS) / totalStakeTimeSnapshot;
        uint256 rewards = (treasuryBalance * userShare * timeElapsed) / (365 days * BASIS_POINTS);

        return rewards;
    }

    function getStakeTimeTokens(address user) public view returns (uint256) {
        StakeInfo memory stakeInfo = stakes[user];
        if (stakeInfo.amount == 0) return 0;

        uint256 timeMultiplier = getTimeMultiplier(user);
        uint256 effectiveStake = (stakeInfo.amount * stakeInfo.multiplier) / BASIS_POINTS;
        uint256 stakeTimeTokens = (effectiveStake * timeMultiplier) / BASIS_POINTS;

        return stakeTimeTokens;
    }

    function getTimeMultiplier(address user) public view returns (uint256) {
        StakeInfo memory stakeInfo = stakes[user];
        if (stakeInfo.amount == 0) return BASIS_POINTS;

        uint256 stakeDuration = block.timestamp - stakeInfo.startTime;
        
        // Linear curve from 1x to 2x over TIME_MULTIPLIER_PERIOD
        if (stakeDuration >= TIME_MULTIPLIER_PERIOD) {
            return 20000; // 2x after full period
        }

        uint256 additionalMultiplier = (BASIS_POINTS * stakeDuration) / TIME_MULTIPLIER_PERIOD;
        return BASIS_POINTS + additionalMultiplier;
    }

    function getTotalStakeTime() public view returns (uint256) {
        // In production, this would aggregate all users' stake-time tokens
        // For simplicity, we calculate on-demand (gas intensive, use events/indexing in prod)
        return totalStakeTime;
    }

    function fundTreasury(uint256 amount) external onlyOwner {
        stakingToken.safeTransferFrom(msg.sender, address(this), amount);
        treasuryBalance += amount;
        emit TreasuryFunded(amount);
    }

    function getStakeInfo(address user) external view returns (
        uint256 amount,
        uint256 startTime,
        uint256 lastClaimTime,
        uint256 multiplier,
        uint256 timeMultiplier,
        uint256 stakeTimeTokens,
        uint256 pendingRewards
    ) {
        StakeInfo memory stakeInfo = stakes[user];
        return (
            stakeInfo.amount,
            stakeInfo.startTime,
            stakeInfo.lastClaimTime,
            stakeInfo.multiplier,
            getTimeMultiplier(user),
            getStakeTimeTokens(user),
            calculateRewards(user)
        );
    }
}
