// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title BlocTime
 * @dev Time-weighted staking with delegation, a weekly reward pot, and a
 *      Bitcoin-style inflation curve (halving schedule).
 *
 *      v2 — seconds-native, linear USD·seconds model:
 *      locks are measured in SECONDS against block.timestamp, and the BLOC
 *      minted for a stake is
 *
 *          bloc = usdValue(amount) * lockSeconds * multiplier(lockSeconds)
 *
 *      where usdValue converts the staked token at the owner-set
 *      `priceUsdMicro` (1e6 = $1.00 per whole token) and the multiplier curve
 *      defaults to a single flat 1x point — pure linear USD × seconds — but
 *      the owner can still shape it with `setPoints`. `secondsPerBlock` is
 *      stored on-chain so UIs can offer a blocks view of the same lock.
 *
 *      Rewards collect in a pot — inflation mints into it every epoch, and
 *      anyone can top it up with `fundPot`. Once a week, from Friday 12:00 EST
 *      onward, `distributeRewards` sweeps the entire pot out to BLOC holders
 *      pro-rata. Nothing is paid out between those windows.
 */
contract BlocTime is ERC20, ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    struct StakePosition {
        uint256 stakeId;
        uint256 amount;
        uint256 startTime;   // block.timestamp at stake
        uint256 lockSeconds;
        uint256 blocTimeBalance;
    }

    struct Point {
        uint256 lockSeconds;
        uint256 multiplier; // basis points (10000 = 1x)
    }

    struct Params {
        uint256 maxLockSeconds;
        uint256 secondsPerBlock; // display conversion only (2 on Base)
    }

    struct InflationParams {
        uint256 initialRewardPerEpoch; // tokens minted per epoch (18 decimals)
        uint256 halvingInterval;       // epochs between halvings
        uint256 minRewardPerEpoch;     // floor
        uint256 epochLength;           // SECONDS per epoch (86400 = 1 day)
        uint256 startTime;             // timestamp when inflation begins
    }

    // ── Core State ──────────────────────────────────────────────
    IERC20 public nativeToken;
    uint256 public totalBlocTime;
    uint256 public nextStakeId;

    /// @notice Owner-set price of the staked token, micro-USD per whole
    ///         token (1e6 = $1.00). Feeds the linear USD × seconds model.
    uint256 public priceUsdMicro;
    uint256 public constant PRICE_SCALE = 1e6;

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
    uint256 public constant MAX_CATCHUP_EPOCHS = 365;

    // ── Weekly Pot ──────────────────────────────────────────────
    uint256 public constant DISTRIBUTION_PERIOD = 7 days;
    // Unix time 0 was a Thursday 00:00 UTC, so every 7-day window starts on a
    // Thursday. Friday 12:00 EST (UTC-5) is 17:00 UTC — 1 day 17 hours in.
    // Pinned to EST year round: the payout is the same instant in UTC every
    // week, which is 11:00 local in New York over the summer (EDT).
    uint256 public constant DISTRIBUTION_OFFSET = 1 days + 17 hours;

    uint256 public rewardPot;               // BLOC waiting for the next payout
    uint256 public lastDistributionTime;    // window paid out last (0 = never)
    uint256 public immutable distributionStart;

    // ── Reward Accumulator (Synthetix pattern) ──────────────────
    uint256 public rewardPerTokenStored;
    mapping(address => uint256) public userRewardPerTokenPaid;
    mapping(address => uint256) public rewards;

    // ── Events ──────────────────────────────────────────────────
    event Staked(address indexed user, uint256 stakeId, uint256 amount, uint256 lockSeconds, uint256 blocTimeEarned);
    event Unstaked(address indexed user, uint256 stakeId, uint256 amount, uint256 blocTimeReturned);
    event ParamsUpdated(uint256 maxLockSeconds, uint256 secondsPerBlock);
    event PriceUpdated(uint256 priceUsdMicro);
    event PointsSet(uint256 pointCount);
    event DelegateChanged(address indexed delegator, address indexed fromDelegate, address indexed toDelegate);
    event InflationParamsUpdated(uint256 initialReward, uint256 halvingInterval, uint256 minReward, uint256 epochLength);
    event PotFunded(address indexed from, uint256 amount, uint256 potSize);
    event RewardsDistributed(uint256 indexed distributionTime, uint256 amount, uint256 minted);
    event RewardsClaimed(address indexed user, uint256 amount);

    constructor(
        address _nativeToken,
        uint256 _maxLockSeconds,
        uint256 _priceUsdMicro
    ) ERC20("BlocTime", "BLOC") Ownable(msg.sender) {
        require(_priceUsdMicro > 0, "Price > 0");
        nativeToken = IERC20(_nativeToken);
        distributionStart = block.timestamp;
        priceUsdMicro = _priceUsdMicro;
        params = Params({
            maxLockSeconds: _maxLockSeconds,
            secondsPerBlock: 2 // Base
        });
        // Default curve: one flat 1x point — the model is pure linear
        // USD × seconds until the owner shapes it.
        points.push(Point({ lockSeconds: 0, multiplier: 10000 }));
    }

    // ── Modifiers ───────────────────────────────────────────────

    modifier updateReward(address account) {
        _checkpoint(account);
        _;
    }

    /// @dev Bank what `account` has earned so far at the current accumulator.
    ///      The contract's own balance (the pot + unclaimed rewards) never
    ///      earns, so it is skipped.
    function _checkpoint(address account) internal {
        if (account == address(0) || account == address(this)) return;
        rewards[account] = earned(account);
        userRewardPerTokenPaid[account] = rewardPerTokenStored;
    }

    // ── Owner Functions ─────────────────────────────────────────

    function setPoints(Point[] calldata _points) external onlyOwner {
        require(_points.length > 0, "Need >= 1 point");
        for (uint256 i = 0; i < _points.length; i++) {
            require(_points[i].multiplier >= 10000, "Mult >= 1x");
            require(_points[i].lockSeconds <= params.maxLockSeconds, "Exceeds max");
            if (i > 0) {
                require(_points[i].lockSeconds > _points[i-1].lockSeconds, "Seconds must increase");
                require(_points[i].multiplier >= _points[i-1].multiplier, "Mult must increase");
            }
        }
        delete points;
        for (uint256 i = 0; i < _points.length; i++) {
            points.push(_points[i]);
        }
        emit PointsSet(_points.length);
    }

    function setParams(uint256 _maxLockSeconds, uint256 _secondsPerBlock) external onlyOwner {
        require(_secondsPerBlock > 0, "SPB > 0");
        params = Params({ maxLockSeconds: _maxLockSeconds, secondsPerBlock: _secondsPerBlock });
        emit ParamsUpdated(_maxLockSeconds, _secondsPerBlock);
    }

    function setPriceUsd(uint256 _priceUsdMicro) external onlyOwner {
        require(_priceUsdMicro > 0, "Price > 0");
        priceUsdMicro = _priceUsdMicro;
        emit PriceUpdated(_priceUsdMicro);
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
            startTime: block.timestamp
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

    function getMultiplier(uint256 lockSeconds) public view returns (uint256) {
        if (points.length == 0) return 10000;
        if (lockSeconds <= points[0].lockSeconds) return points[0].multiplier;
        if (lockSeconds >= points[points.length - 1].lockSeconds) return points[points.length - 1].multiplier;
        for (uint256 i = 0; i < points.length - 1; i++) {
            if (lockSeconds >= points[i].lockSeconds && lockSeconds <= points[i + 1].lockSeconds) {
                uint256 range = points[i + 1].lockSeconds - points[i].lockSeconds;
                if (range == 0) return points[i].multiplier;
                uint256 pos = lockSeconds - points[i].lockSeconds;
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

    /// @notice BLOC minted for staking `amount` for `lockSeconds`:
    ///         USD value of the stake × seconds locked × curve multiplier.
    ///         With the default flat curve this is exactly usd * seconds.
    function quoteBloc(uint256 amount, uint256 lockSeconds) public view returns (uint256) {
        uint256 usdValue = (amount * priceUsdMicro) / PRICE_SCALE; // 18-decimal USD
        return (usdValue * lockSeconds * getMultiplier(lockSeconds)) / 10000;
    }

    function stake(uint256 amount, uint256 lockSeconds) external nonReentrant updateReward(msg.sender) {
        require(amount > 0, "Amount > 0");
        require(lockSeconds <= params.maxLockSeconds, "Exceeds max lock");
        nativeToken.safeTransferFrom(msg.sender, address(this), amount);

        uint256 blocTimeEarned = quoteBloc(amount, lockSeconds);
        require(blocTimeEarned > 0, "Lock too short");
        uint256 stakeId = nextStakeId++;

        userStakes[msg.sender][stakeId] = StakePosition({
            stakeId: stakeId,
            amount: amount,
            startTime: block.timestamp,
            lockSeconds: lockSeconds,
            blocTimeBalance: blocTimeEarned
        });
        userStakeIds[msg.sender].push(stakeId);
        totalBlocTime += blocTimeEarned;
        _mint(msg.sender, blocTimeEarned);

        emit Staked(msg.sender, stakeId, amount, lockSeconds, blocTimeEarned);
    }

    function unstake(uint256 stakeId) external nonReentrant updateReward(msg.sender) {
        StakePosition storage position = userStakes[msg.sender][stakeId];
        require(position.amount > 0, "No active stake");
        require(block.timestamp >= position.startTime + position.lockSeconds, "Still locked");

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
        uint256 received = delegatedVotingPower[account];
        if (delegates[account] != address(0)) {
            // Delegated: own balance is counted at the delegate — which is
            // `received` itself when self-delegated — never here as well.
            return received;
        }
        return balanceOf(account) + received;
    }

    // ── Inflation & Rewards ─────────────────────────────────────

    function currentEpoch() public view returns (uint256) {
        if (inflationParams.epochLength == 0 || inflationParams.startTime == 0) return 0;
        if (block.timestamp < inflationParams.startTime) return 0;
        return (block.timestamp - inflationParams.startTime) / inflationParams.epochLength;
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

    /// @notice BLOC that earns a share of the pot — every holder except the
    ///         contract itself, which custodies the pot and unclaimed rewards.
    function distributableSupply() public view returns (uint256) {
        return totalSupply() - balanceOf(address(this));
    }

    /// @notice Top the pot up with BLOC. Paid out at the next weekly window.
    function fundPot(uint256 amount) external {
        require(amount > 0, "Amount > 0");
        _transfer(msg.sender, address(this), amount);
        rewardPot += amount;
        emit PotFunded(msg.sender, amount, rewardPot);
    }

    /// @dev Start of the weekly window containing `ts` — Friday 12:00 EST.
    function _windowStart(uint256 ts) internal pure returns (uint256) {
        uint256 b = (ts / DISTRIBUTION_PERIOD) * DISTRIBUTION_PERIOD + DISTRIBUTION_OFFSET;
        if (b <= ts) return b;
        return b >= DISTRIBUTION_PERIOD ? b - DISTRIBUTION_PERIOD : 0;
    }

    /// @notice The next Friday 12:00 EST at which the pot may be swept.
    function nextDistributionTime() public view returns (uint256) {
        uint256 from = lastDistributionTime == 0 ? distributionStart : lastDistributionTime;
        return _windowStart(from) + DISTRIBUTION_PERIOD;
    }

    function distributionDue() external view returns (bool) {
        return block.timestamp >= nextDistributionTime();
    }

    /// @dev Mint the inflation owed for every completed epoch into the pot.
    function _accrueInflation() internal returns (uint256 minted) {
        uint256 epoch = currentEpoch();
        if (epoch <= lastDistributionEpoch) return 0;

        uint256 startEpoch = lastDistributionEpoch + 1;
        if (epoch - startEpoch + 1 > MAX_CATCHUP_EPOCHS) {
            startEpoch = epoch - MAX_CATCHUP_EPOCHS + 1;
        }
        for (uint256 e = startEpoch; e <= epoch; e++) {
            minted += getEpochReward(e);
        }
        lastDistributionEpoch = epoch;
        if (minted > 0) {
            _mint(address(this), minted);
            rewardPot += minted;
        }
    }

    /// @notice Sweep the whole pot to BLOC holders, pro-rata. Permissionless,
    ///         but only once per week — from Friday 12:00 EST onward.
    function distributeRewards() external {
        require(block.timestamp >= nextDistributionTime(), "Not distribution time");

        // Snapshot the eligible supply before inflation lands in the pot,
        // so freshly minted rewards don't dilute the holders they're for.
        uint256 eligible = distributableSupply();
        require(eligible > 0, "No holders");

        uint256 minted = _accrueInflation();
        uint256 perToken = (rewardPot * 1e18) / eligible;
        require(perToken > 0, "Pot too small");

        // Rounding dust stays in the pot and rides along next week.
        uint256 paid = (perToken * eligible) / 1e18;
        rewardPot -= paid;
        rewardPerTokenStored += perToken;
        lastDistributionTime = _windowStart(block.timestamp);
        totalDistributed += paid;
        emit RewardsDistributed(lastDistributionTime, paid, minted);
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
        // Bank both sides at the old balance — a transfer must not hand the
        // recipient a share of rewards that accrued before they held the BLOC.
        _checkpoint(from);
        _checkpoint(to);
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
        uint256 amount, uint256 startTime, uint256 lockSeconds,
        uint256 blocTimeBalance, uint256 secondsRemaining
    ) {
        StakePosition storage position = userStakes[user][stakeId];
        uint256 elapsed = block.timestamp > position.startTime ? block.timestamp - position.startTime : 0;
        uint256 remaining = position.lockSeconds > elapsed ? position.lockSeconds - elapsed : 0;
        return (position.amount, position.startTime, position.lockSeconds, position.blocTimeBalance, remaining);
    }

    /// @notice Everything the pot card needs: current pot, the schedule, the
    ///         supply it will be split across, and inflation still to be minted.
    function getPotInfo() external view returns (
        uint256 pot, uint256 pendingInflation, uint256 eligibleSupply,
        uint256 nextTime, uint256 lastTime, bool due
    ) {
        uint256 epoch = currentEpoch();
        if (epoch > lastDistributionEpoch) {
            uint256 startEpoch = lastDistributionEpoch + 1;
            if (epoch - startEpoch + 1 > MAX_CATCHUP_EPOCHS) {
                startEpoch = epoch - MAX_CATCHUP_EPOCHS + 1;
            }
            for (uint256 e = startEpoch; e <= epoch; e++) {
                pendingInflation += getEpochReward(e);
            }
        }
        uint256 next = nextDistributionTime();
        return (
            rewardPot, pendingInflation, distributableSupply(),
            next, lastDistributionTime, block.timestamp >= next
        );
    }

    function getInflationParams() external view returns (
        uint256 initialRewardPerEpoch, uint256 halvingInterval,
        uint256 minRewardPerEpoch, uint256 epochLength, uint256 startTime
    ) {
        return (
            inflationParams.initialRewardPerEpoch, inflationParams.halvingInterval,
            inflationParams.minRewardPerEpoch, inflationParams.epochLength, inflationParams.startTime
        );
    }
}
