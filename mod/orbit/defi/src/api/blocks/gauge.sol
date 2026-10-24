// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

interface IVotingEscrow {
    function balanceOf(address account) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

/// Liquidity gauge with vote-escrow boost — the second half of the Curve
/// design. Stake the LP token, earn emissions, and earn up to 2.5× more of
/// them if you also hold vote-escrowed weight.
///
/// The boost formula is Curve's: your working balance is 40% of your deposit,
/// plus 60% of the pool weighted by your share of total ve — capped at your
/// actual deposit. Without a wired escrow it degrades to a plain staking
/// gauge, everyone at 1×.
contract ModGauge is Owned {
    using SafeTransfer for IERC20;

    uint256 internal constant TOKENLESS = 40; // percent earned without any ve

    IERC20 public immutable stakeToken;
    IERC20 public immutable rewardToken;
    IVotingEscrow public escrow;

    uint256 public rewardPerSecond;
    uint256 public periodFinish;

    uint256 public totalStaked;
    uint256 public workingSupply;
    uint256 public accRewardPerShare; // over working balances, 1e18 scaled
    uint256 public lastUpdate;

    struct Position {
        uint256 amount;
        uint256 working;
        uint256 rewardDebt;
        uint256 pending;
    }

    mapping(address => Position) public positions;

    event Staked(address indexed user, uint256 amount, uint256 working);
    event Unstaked(address indexed user, uint256 amount);
    event Claimed(address indexed user, uint256 amount);
    event BoostUpdated(address indexed user, uint256 working, uint256 boostBps);

    constructor(
        address stakeToken_,
        address rewardToken_,
        address escrow_,
        uint256 rewardPerSecond_,
        address owner_
    ) Owned(owner_) {
        require(stakeToken_ != address(0) && rewardToken_ != address(0), "NO_TOKEN");
        stakeToken = IERC20(stakeToken_);
        rewardToken = IERC20(rewardToken_);
        escrow = IVotingEscrow(escrow_);
        rewardPerSecond = rewardPerSecond_;
        lastUpdate = block.timestamp;
    }

    function _effectiveNow() internal view returns (uint256) {
        if (periodFinish == 0) return block.timestamp;
        return block.timestamp < periodFinish ? block.timestamp : periodFinish;
    }

    function _update() internal {
        uint256 nowT = _effectiveNow();
        if (nowT <= lastUpdate) return;
        if (workingSupply > 0 && rewardPerSecond > 0) {
            accRewardPerShare += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / workingSupply;
        }
        lastUpdate = nowT;
    }

    /// Curve's boost: 40% of your deposit always, the other 60% pro-rata to
    /// your share of the escrow, never more than you actually staked.
    function workingBalanceFor(address user) public view returns (uint256) {
        Position memory p = positions[user];
        uint256 base = (p.amount * TOKENLESS) / 100;
        if (address(escrow) == address(0)) return p.amount;
        uint256 veTotal = escrow.totalSupply();
        if (veTotal == 0) return base;
        uint256 boosted = base + (((totalStaked * escrow.balanceOf(user)) / veTotal) * (100 - TOKENLESS)) / 100;
        return boosted > p.amount ? p.amount : boosted;
    }

    /// 10000 = 1×, 25000 = the 2.5× maximum.
    function boostBps(address user) external view returns (uint256) {
        Position memory p = positions[user];
        if (p.amount == 0) return 0;
        return (workingBalanceFor(user) * 10_000 * 100) / (p.amount * TOKENLESS);
    }

    function earned(address user) public view returns (uint256) {
        Position memory p = positions[user];
        uint256 acc = accRewardPerShare;
        uint256 nowT = _effectiveNow();
        if (nowT > lastUpdate && workingSupply > 0 && rewardPerSecond > 0) {
            acc += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / workingSupply;
        }
        return p.pending + ((p.working * acc) / 1e18) - p.rewardDebt;
    }

    function _settle(Position storage p) internal {
        p.pending += ((p.working * accRewardPerShare) / 1e18) - p.rewardDebt;
    }

    function _reboost(address user) internal {
        Position storage p = positions[user];
        uint256 next = workingBalanceFor(user);
        workingSupply = workingSupply - p.working + next;
        p.working = next;
        p.rewardDebt = (next * accRewardPerShare) / 1e18;
        emit BoostUpdated(user, next, p.amount == 0 ? 0 : (next * 10_000 * 100) / (p.amount * TOKENLESS));
    }

    function stake(uint256 amount) external {
        require(amount > 0, "ZERO");
        _update();
        Position storage p = positions[msg.sender];
        _settle(p);
        stakeToken.pull(msg.sender, amount);
        p.amount += amount;
        totalStaked += amount;
        _reboost(msg.sender);
        emit Staked(msg.sender, amount, p.working);
    }

    function unstake(uint256 amount) external {
        Position storage p = positions[msg.sender];
        require(p.amount >= amount, "INSUFFICIENT");
        _update();
        _settle(p);
        p.amount -= amount;
        totalStaked -= amount;
        _reboost(msg.sender);
        stakeToken.push(msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    /// Anyone can re-apply anyone's boost — Curve calls it kick, and it is how
    /// an expired lock stops eating emissions it no longer earns.
    function kick(address user) external {
        _update();
        Position storage p = positions[user];
        _settle(p);
        _reboost(user);
    }

    function claim() external returns (uint256 amount) {
        _update();
        Position storage p = positions[msg.sender];
        _settle(p);
        p.rewardDebt = (p.working * accRewardPerShare) / 1e18;
        amount = p.pending;
        require(amount > 0, "NOTHING");
        uint256 available = rewardToken.balanceOf(address(this));
        if (amount > available) amount = available;
        p.pending -= amount;
        rewardToken.push(msg.sender, amount);
        emit Claimed(msg.sender, amount);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function fund(uint256 amount, uint256 duration) external {
        rewardToken.pull(msg.sender, amount);
        if (duration > 0) periodFinish = block.timestamp + duration;
    }

    function setRewardRate(uint256 rewardPerSecond_) external onlyOwner {
        _update();
        rewardPerSecond = rewardPerSecond_;
    }

    function setEscrow(address escrow_) external onlyOwner {
        escrow = IVotingEscrow(escrow_);
    }
}
