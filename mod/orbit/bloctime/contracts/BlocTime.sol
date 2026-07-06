// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title BlocTime
 * @dev Time-weighted staking with delegation, daily reward distribution,
 *      and Bitcoin-style inflation curve (halving schedule).
 */
contract BlocTime is ERC20, ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    struct StakePosition {
        uint256 stakeId;
        uint256 amount;
        uint256 startBlock;
        uint256 lockBlocks;
        uint256 blocTimeBalance;
    }

    struct Point {
        uint256 blocks;
        uint256 multiplier; // basis points (10000 = 1x)
    }

    struct Params {
        uint256 maxLockBlocks;
        uint256 distributionPercentage;
    }

    struct InflationParams {
        uint256 initialRewardPerEpoch; // tokens minted per epoch (18 decimals)
        uint256 halvingInterval;       // epochs between halvings
        uint256 minRewardPerEpoch;     // floor
        uint256 epochLength;           // blocks per epoch (~43200 = 1 day on Base)
        uint256 startBlock;            // block when inflation begins
    }

    // ── Core State ──────────────────────────────────────────────
    IERC20 public nativeToken;
    uint256 public totalBlocTime;
    uint256 public nextStakeId;

    mapping(address => mapping(uint256 => StakePosition)) public userStakes;
    mapping(address => uint256[]) public userStakeIds;

    Point[] public points;
    Params public params;

    // ── Delegation (voting power only) ──────────────────────────
    mapping(address => address) public delegates;
    mapping(address => uint256) public delegatedVotingPower;

    // ── Inflation / Halving ─────────────────────────────────────
    InflationParams public inflationParams;
    uint256 public lastDistributionEpoch;
    uint256 public totalDistributed;

    // ── Reward Accumulator (Synthetix pattern) ──────────────────
    uint256 public rewardPerTokenStored;
    mapping(address => uint256) public userRewardPerTokenPaid;
    mapping(address => uint256) public rewards;

    // ── Events ──────────────────────────────────────────────────
    event Staked(address indexed user, uint256 stakeId, uint256 amount, uint256 lockBlocks, uint256 blocTimeEarned);
    event Unstaked(address indexed user, uint256 stakeId, uint256 amount, uint256 blocTimeReturned);
    event ParamsUpdated(uint256 maxLockBlocks, uint256 distributionPercentage);
    event PointsSet(uint256 pointCount);
    event DelegateChanged(address indexed delegator, address indexed fromDelegate, address indexed toDelegate);
    event InflationParamsUpdated(uint256 initialReward, uint256 halvingInterval, uint256 minReward, uint256 epochLength);
    event RewardsDistributed(uint256 epoch, uint256 amount);
    event RewardsClaimed(address indexed user, uint256 amount);

    constructor(
        address _nativeToken,
        uint256 _maxLockBlocks,
        uint256 _distributionPercentage
    ) ERC20("BlocTime", "BLOC") Ownable(msg.sender) {
        require(_distributionPercentage <= 10000, "Max 100%");
        nativeToken = IERC20(_nativeToken);
        params = Params({
            maxLockBlocks: _maxLockBlocks,
            distributionPercentage: _distributionPercentage
        });
        points.push(Point({ blocks: 0, multiplier: 10000 }));
    }

    // ── Modifiers ───────────────────────────────────────────────

    modifier updateReward(address account) {
        if (account != address(0)) {
            rewards[account] = earned(account);
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
        _;
    }

    // ── Owner Functions ─────────────────────────────────────────

    function setPoints(Point[] calldata _points) external onlyOwner {
        require(_points.length > 0, "Need >= 1 point");
        for (uint256 i = 0; i < _points.length; i++) {
            require(_points[i].multiplier >= 10000, "Mult >= 1x");
            require(_points[i].blocks <= params.maxLockBlocks, "Exceeds max");
            if (i > 0) {
                require(_points[i].blocks > _points[i-1].blocks, "Blocks must increase");
                require(_points[i].multiplier >= _points[i-1].multiplier, "Mult must increase");
            }
        }
        delete points;
        for (uint256 i = 0; i < _points.length; i++) {
            points.push(_points[i]);
        }
        emit PointsSet(_points.length);
    }

    function setParams(uint256 _maxLockBlocks, uint256 _distributionPercentage) external onlyOwner {
        require(_distributionPercentage <= 10000, "Max 100%");
        params = Params({ maxLockBlocks: _maxLockBlocks, distributionPercentage: _distributionPercentage });
        emit ParamsUpdated(_maxLockBlocks, _distributionPercentage);
    }

    function setInflationParams(
        uint256 _initialRewardPerEpoch,
        uint256 _halvingInterval,
        uint256 _minRewardPerEpoch,
        uint256 _epochLength
    ) external onlyOwner {
        require(_epochLength > 0, "Epoch > 0");
        require(_halvingInterval > 0, "Halving > 0");
        inflationParams = InflationParams({
            initialRewardPerEpoch: _initialRewardPerEpoch,
            halvingInterval: _halvingInterval,
            minRewardPerEpoch: _minRewardPerEpoch,
            epochLength: _epochLength,
            startBlock: block.number
        });
        lastDistributionEpoch = 0;
        emit InflationParamsUpdated(_initialRewardPerEpoch, _halvingInterval, _minRewardPerEpoch, _epochLength);
    }

    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }

    function renounceOwnership() public override onlyOwner {
        super.renounceOwnership();
    }

    // ── Multiplier Curve ────────────────────────────────────────

    function getMultiplier(uint256 blockCount) public view returns (uint256) {
        if (points.length == 0) return 10000;
        if (blockCount <= points[0].blocks) return points[0].multiplier;
        if (blockCount >= points[points.length - 1].blocks) return points[points.length - 1].multiplier;
        for (uint256 i = 0; i < points.length - 1; i++) {
            if (blockCount >= points[i].blocks && blockCount <= points[i + 1].blocks) {
                uint256 range = points[i + 1].blocks - points[i].blocks;
                if (range == 0) return points[i].multiplier;
                uint256 pos = blockCount - points[i].blocks;
                uint256 yRange = points[i + 1].multiplier - points[i].multiplier;
                return points[i].multiplier + (yRange * pos) / range;
            }
        }
        return points[points.length - 1].multiplier;
    }

    function getPoints() external view returns (Point[] memory) {
        return points;
    }

    // ── Staking ─────────────────────────────────────────────────

    function stake(uint256 amount, uint256 lockBlocks) external nonReentrant updateReward(msg.sender) {
        require(amount > 0, "Amount > 0");
        require(lockBlocks <= params.maxLockBlocks, "Exceeds max lock");
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
        totalBlocTime += blocTimeEarned;
        _mint(msg.sender, blocTimeEarned);

        emit Staked(msg.sender, stakeId, amount, lockBlocks, blocTimeEarned);
    }

    function unstake(uint256 stakeId) external nonReentrant updateReward(msg.sender) {
        StakePosition storage position = userStakes[msg.sender][stakeId];
        require(position.amount > 0, "No active stake");
        require(block.number >= position.startBlock + position.lockBlocks, "Still locked");

        uint256 amount = position.amount;
        uint256 blocTimeBalance = position.blocTimeBalance;

        totalBlocTime -= blocTimeBalance;
        _burn(msg.sender, blocTimeBalance);

        uint256[] storage sids = userStakeIds[msg.sender];
        for (uint256 i = 0; i < sids.length; i++) {
            if (sids[i] == stakeId) {
                sids[i] = sids[sids.length - 1];
                sids.pop();
                break;
            }
        }
        delete userStakes[msg.sender][stakeId];
        nativeToken.safeTransfer(msg.sender, amount);

        emit Unstaked(msg.sender, stakeId, amount, blocTimeBalance);
    }

    // ── Delegation ──────────────────────────────────────────────

    function delegate(address to) external updateReward(msg.sender) {
        require(to != address(0), "Zero address");
        address from = delegates[msg.sender];
        uint256 balance = balanceOf(msg.sender);
        if (from != address(0)) {
            delegatedVotingPower[from] -= balance;
        }
        delegates[msg.sender] = to;
        delegatedVotingPower[to] += balance;
        emit DelegateChanged(msg.sender, from, to);
    }

    function undelegate() external updateReward(msg.sender) {
        address from = delegates[msg.sender];
        require(from != address(0), "Not delegated");
        delegatedVotingPower[from] -= balanceOf(msg.sender);
        delegates[msg.sender] = address(0);
        emit DelegateChanged(msg.sender, from, address(0));
    }

    function getVotingPower(address account) external view returns (uint256) {
        uint256 own = balanceOf(account);
        uint256 received = delegatedVotingPower[account];
        address del = delegates[account];
        if (del != address(0) && del != account) {
            return received; // delegated away — own power is 0
        }
        return own + received;
    }

    // ── Inflation & Rewards ─────────────────────────────────────

    function currentEpoch() public view returns (uint256) {
        if (inflationParams.epochLength == 0 || inflationParams.startBlock == 0) return 0;
        if (block.number < inflationParams.startBlock) return 0;
        return (block.number - inflationParams.startBlock) / inflationParams.epochLength;
    }

    function getEpochReward(uint256 epoch) public view returns (uint256) {
        if (inflationParams.halvingInterval == 0) return inflationParams.initialRewardPerEpoch;
        uint256 halvings = epoch / inflationParams.halvingInterval;
        uint256 reward = inflationParams.initialRewardPerEpoch;
        for (uint256 i = 0; i < halvings && i < 64; i++) {
            reward = reward / 2;
        }
        if (reward < inflationParams.minRewardPerEpoch) return inflationParams.minRewardPerEpoch;
        return reward;
    }

    function distributeRewards() external {
        uint256 epoch = currentEpoch();
        require(epoch > lastDistributionEpoch, "Epoch not complete");
        require(totalBlocTime > 0, "No stakers");

        uint256 totalMint = 0;
        uint256 maxCatchup = 365;
        uint256 startEpoch = lastDistributionEpoch + 1;
        uint256 endEpoch = epoch;
        if (endEpoch - startEpoch + 1 > maxCatchup) {
            startEpoch = endEpoch - maxCatchup + 1;
        }
        for (uint256 e = startEpoch; e <= endEpoch; e++) {
            totalMint += getEpochReward(e);
        }
        if (totalMint > 0) {
            _mint(address(this), totalMint);
            rewardPerTokenStored += (totalMint * 1e18) / totalBlocTime;
        }
        lastDistributionEpoch = epoch;
        totalDistributed += totalMint;
        emit RewardsDistributed(epoch, totalMint);
    }

    function earned(address account) public view returns (uint256) {
        return (balanceOf(account) * (rewardPerTokenStored - userRewardPerTokenPaid[account])) / 1e18
            + rewards[account];
    }

    function claimRewards() external nonReentrant updateReward(msg.sender) {
        uint256 reward = rewards[msg.sender];
        require(reward > 0, "Nothing to claim");
        rewards[msg.sender] = 0;
        _transfer(address(this), msg.sender, reward);
        emit RewardsClaimed(msg.sender, reward);
    }

    // ── ERC20 Hook (delegation bookkeeping, OZ v5) ──────────────

    function _update(address from, address to, uint256 value) internal virtual override {
        super._update(from, to, value);
        if (from != address(0) && delegates[from] != address(0)) {
            delegatedVotingPower[delegates[from]] -= value;
        }
        if (to != address(0) && delegates[to] != address(0)) {
            delegatedVotingPower[delegates[to]] += value;
        }
    }

    // ── View Functions ──────────────────────────────────────────

    function getUserStakeIds(address user) external view returns (uint256[] memory) {
        return userStakeIds[user];
    }

    function getStakePosition(address user, uint256 stakeId) external view returns (
        uint256 amount, uint256 startBlock, uint256 lockBlocks,
        uint256 blocTimeBalance, uint256 blocksRemaining
    ) {
        StakePosition storage position = userStakes[user][stakeId];
        uint256 elapsed = block.number > position.startBlock ? block.number - position.startBlock : 0;
        uint256 remaining = position.lockBlocks > elapsed ? position.lockBlocks - elapsed : 0;
        return (position.amount, position.startBlock, position.lockBlocks, position.blocTimeBalance, remaining);
    }

    function getInflationParams() external view returns (
        uint256 initialRewardPerEpoch, uint256 halvingInterval,
        uint256 minRewardPerEpoch, uint256 epochLength, uint256 startBlock
    ) {
        return (
            inflationParams.initialRewardPerEpoch, inflationParams.halvingInterval,
            inflationParams.minRewardPerEpoch, inflationParams.epochLength, inflationParams.startBlock
        );
    }
}
