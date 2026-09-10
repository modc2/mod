// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Synthetix-style reward staking. Two `erc20` ports: what you stake and what
/// you earn. Point the stake port at a vault's share token and you have a
/// yield-bearing position that also farms.
contract ModStaking is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable stakeToken;
    IERC20 public immutable rewardToken;

    uint256 public rewardPerSecond;
    uint256 public totalStaked;
    uint256 public accRewardPerShare; // 1e18 scaled
    uint256 public lastUpdate;
    uint256 public periodFinish;

    struct Position {
        uint256 amount;
        uint256 rewardDebt;
        uint256 pending;
    }

    mapping(address => Position) public positions;

    event Staked(address indexed user, uint256 amount);
    event Unstaked(address indexed user, uint256 amount);
    event Claimed(address indexed user, uint256 amount);

    constructor(
        address stakeToken_,
        address rewardToken_,
        uint256 rewardPerSecond_,
        address owner_
    ) Owned(owner_) {
        require(stakeToken_ != address(0) && rewardToken_ != address(0), "NO_TOKEN");
        stakeToken = IERC20(stakeToken_);
        rewardToken = IERC20(rewardToken_);
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
        if (totalStaked > 0 && rewardPerSecond > 0) {
            accRewardPerShare += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / totalStaked;
        }
        lastUpdate = nowT;
    }

    function earned(address user) public view returns (uint256) {
        Position memory p = positions[user];
        uint256 acc = accRewardPerShare;
        uint256 nowT = _effectiveNow();
        if (nowT > lastUpdate && totalStaked > 0 && rewardPerSecond > 0) {
            acc += ((nowT - lastUpdate) * rewardPerSecond * 1e18) / totalStaked;
        }
        return p.pending + ((p.amount * acc) / 1e18) - p.rewardDebt;
    }

    function stake(uint256 amount) external {
        require(amount > 0, "ZERO");
        _update();
        Position storage p = positions[msg.sender];
        p.pending += ((p.amount * accRewardPerShare) / 1e18) - p.rewardDebt;
        stakeToken.pull(msg.sender, amount);
        p.amount += amount;
        totalStaked += amount;
        p.rewardDebt = (p.amount * accRewardPerShare) / 1e18;
        emit Staked(msg.sender, amount);
    }

    function unstake(uint256 amount) external {
        Position storage p = positions[msg.sender];
        require(p.amount >= amount, "INSUFFICIENT");
        _update();
        p.pending += ((p.amount * accRewardPerShare) / 1e18) - p.rewardDebt;
        p.amount -= amount;
        totalStaked -= amount;
        p.rewardDebt = (p.amount * accRewardPerShare) / 1e18;
        stakeToken.push(msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    function claim() external returns (uint256 amount) {
        _update();
        Position storage p = positions[msg.sender];
        p.pending += ((p.amount * accRewardPerShare) / 1e18) - p.rewardDebt;
        p.rewardDebt = (p.amount * accRewardPerShare) / 1e18;
        amount = p.pending;
        require(amount > 0, "NOTHING");
        uint256 available = rewardToken.balanceOf(address(this));
        if (amount > available) amount = available;
        p.pending -= amount;
        rewardToken.push(msg.sender, amount);
        emit Claimed(msg.sender, amount);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setRewardRate(uint256 rewardPerSecond_) external onlyOwner {
        _update();
        rewardPerSecond = rewardPerSecond_;
    }

    /// Fund rewards and (optionally) set the end of the emission window.
    function fund(uint256 amount, uint256 duration) external {
        rewardToken.pull(msg.sender, amount);
        if (duration > 0) periodFinish = block.timestamp + duration;
    }
}
