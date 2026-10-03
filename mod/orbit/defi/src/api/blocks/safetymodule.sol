// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Safety module — Aave's stkAAVE. Stake the governance token to backstop the
/// protocol: you earn emissions for standing there, you wait out a cooldown to
/// leave, and if the protocol takes a bad-debt hit the owner can slash up to a
/// capped fraction of the pot to cover it.
///
/// The cooldown is the whole point. Insurance you can withdraw the instant it
/// is needed is not insurance, so unstaking is a two-step: signal, wait, then
/// exit inside a short window or signal again.
contract ModSafetyModule is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable stakeToken;
    IERC20 public immutable rewardToken;

    uint256 public cooldownPeriod;
    uint256 public unstakeWindow;
    uint16 public maxSlashBps;
    address public recoveryFund; // where slashed funds go

    uint256 public rewardPerSecond;
    uint256 public periodFinish;
    uint256 public accRewardPerShare; // 1e18 scaled
    uint256 public lastUpdate;
    uint256 public totalStaked;

    mapping(address => uint256) public cooldownStart;
    mapping(address => uint256) public rewardDebt;
    mapping(address => uint256) public pendingReward;

    event Staked(address indexed user, uint256 assets, uint256 shares);
    event CooldownStarted(address indexed user, uint256 at);
    event Redeemed(address indexed user, uint256 shares, uint256 assets);
    event Slashed(uint256 amount, address indexed to, uint256 exchangeRate);
    event Claimed(address indexed user, uint256 amount);

    constructor(
        address stakeToken_,
        address rewardToken_,
        string memory name_,
        string memory symbol_,
        uint256 cooldownPeriod_,
        uint16 maxSlashBps_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(stakeToken_ != address(0) && rewardToken_ != address(0), "NO_TOKEN");
        require(maxSlashBps_ <= 5_000, "SLASH_TOO_DEEP");
        stakeToken = IERC20(stakeToken_);
        rewardToken = IERC20(rewardToken_);
        cooldownPeriod = cooldownPeriod_;
        unstakeWindow = 2 days;
        maxSlashBps = maxSlashBps_;
        lastUpdate = block.timestamp;
    }

    /// Staked assets per share, 1e18 scaled. Only a slash moves it.
    function exchangeRate() public view returns (uint256) {
        return totalSupply == 0 ? 1e18 : (totalStaked * 1e18) / totalSupply;
    }

    // ── emissions ─────────────────────────────────────────────────────────

    function _effectiveNow() internal view returns (uint256) {
        if (periodFinish == 0) return block.timestamp;
        return block.timestamp < periodFinish ? block.timestamp : periodFinish;
    }

    function _update() internal {
        uint256 nowT = _effectiveNow();
        if (nowT <= lastUpdate) return;
        if (totalSupply > 0 && rewardPerSecond > 0) {
            accRewardPerShare += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / totalSupply;
        }
        lastUpdate = nowT;
    }

    function earned(address user) public view returns (uint256) {
        uint256 acc = accRewardPerShare;
        uint256 nowT = _effectiveNow();
        if (nowT > lastUpdate && totalSupply > 0 && rewardPerSecond > 0) {
            acc += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / totalSupply;
        }
        return pendingReward[user] + ((balanceOf[user] * acc) / 1e18) - rewardDebt[user];
    }

    function _settle(address user) internal {
        pendingReward[user] += ((balanceOf[user] * accRewardPerShare) / 1e18) - rewardDebt[user];
    }

    function claim() external returns (uint256 amount) {
        _update();
        _settle(msg.sender);
        rewardDebt[msg.sender] = (balanceOf[msg.sender] * accRewardPerShare) / 1e18;
        amount = pendingReward[msg.sender];
        require(amount > 0, "NOTHING");
        uint256 available = rewardToken.balanceOf(address(this));
        if (amount > available) amount = available;
        pendingReward[msg.sender] -= amount;
        rewardToken.push(msg.sender, amount);
        emit Claimed(msg.sender, amount);
    }

    // ── staking ───────────────────────────────────────────────────────────

    function stake(uint256 assets) external returns (uint256 shares) {
        require(assets > 0, "ZERO");
        _update();
        _settle(msg.sender);
        shares = totalSupply == 0 || totalStaked == 0 ? assets : (assets * totalSupply) / totalStaked;
        stakeToken.pull(msg.sender, assets);
        totalStaked += assets;
        _mint(msg.sender, shares);
        rewardDebt[msg.sender] = (balanceOf[msg.sender] * accRewardPerShare) / 1e18;
        emit Staked(msg.sender, assets, shares);
    }

    function cooldown() external {
        require(balanceOf[msg.sender] > 0, "NOTHING_STAKED");
        cooldownStart[msg.sender] = block.timestamp;
        emit CooldownStarted(msg.sender, block.timestamp);
    }

    function redeemableAt(address user) external view returns (uint256 opens, uint256 closes) {
        uint256 start = cooldownStart[user];
        if (start == 0) return (0, 0);
        opens = start + cooldownPeriod;
        closes = opens + unstakeWindow;
    }

    function redeem(uint256 shares) external returns (uint256 assets) {
        uint256 start = cooldownStart[msg.sender];
        require(start != 0, "NO_COOLDOWN");
        require(block.timestamp >= start + cooldownPeriod, "COOLING_DOWN");
        require(block.timestamp <= start + cooldownPeriod + unstakeWindow, "WINDOW_CLOSED");
        require(shares > 0 && balanceOf[msg.sender] >= shares, "INSUFFICIENT");

        _update();
        _settle(msg.sender);
        assets = (shares * totalStaked) / totalSupply;
        _burn(msg.sender, shares);
        totalStaked -= assets;
        rewardDebt[msg.sender] = (balanceOf[msg.sender] * accRewardPerShare) / 1e18;
        if (balanceOf[msg.sender] == 0) cooldownStart[msg.sender] = 0;
        stakeToken.push(msg.sender, assets);
        emit Redeemed(msg.sender, shares, assets);
    }

    // ── the backstop ──────────────────────────────────────────────────────

    /// Cover a shortfall out of the pot. Capped, and it moves the exchange
    /// rate rather than anyone's balance, so the loss is shared exactly.
    function slash(uint256 amount) external onlyOwner returns (uint256 slashed) {
        require(totalStaked > 0, "EMPTY");
        uint256 cap = (totalStaked * maxSlashBps) / 10_000;
        slashed = amount > cap ? cap : amount;
        totalStaked -= slashed;
        stakeToken.push(recoveryFund == address(0) ? owner : recoveryFund, slashed);
        emit Slashed(slashed, recoveryFund == address(0) ? owner : recoveryFund, exchangeRate());
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function fund(uint256 amount, uint256 duration) external {
        rewardToken.pull(msg.sender, amount);
        if (duration > 0) periodFinish = block.timestamp + duration;
    }

    function setRecoveryFund(address fund_) external onlyOwner {
        recoveryFund = fund_;
    }

    function setRewardRate(uint256 rewardPerSecond_) external onlyOwner {
        _update();
        rewardPerSecond = rewardPerSecond_;
    }

    function setTerms(uint256 cooldownPeriod_, uint256 unstakeWindow_, uint16 maxSlashBps_) external onlyOwner {
        require(unstakeWindow_ >= 1 hours, "WINDOW_TOO_SHORT");
        require(maxSlashBps_ <= 5_000, "SLASH_TOO_DEEP");
        cooldownPeriod = cooldownPeriod_;
        unstakeWindow = unstakeWindow_;
        maxSlashBps = maxSlashBps_;
    }
}
