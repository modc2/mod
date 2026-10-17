// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Vote-escrow — veCRV. Lock a token for up to four years and hold a weight
/// that decays linearly to zero at unlock. Locking longer is the only way to
/// weigh more, so governance power costs time rather than a flash loan.
///
/// The weight reads like an ERC20 (balanceOf / totalSupply) but every transfer
/// path reverts: that is what makes it non-transferable, and it is also why a
/// gauge can consume it through the same `erc20` shape it uses for everything
/// else.
contract ModVoteEscrow is Owned {
    using SafeTransfer for IERC20;

    uint256 public constant WEEK = 7 days;

    IERC20 public immutable token;
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;

    uint256 public immutable maxLock;

    struct Lock {
        uint256 amount;
        uint256 end;
    }

    mapping(address => Lock) public locks;
    uint256 public totalLocked;

    /// Global decay, tracked the way Curve tracks it: a bias falling at a
    /// slope, with scheduled slope changes at each week where locks expire.
    uint256 public bias;
    uint256 public slope; // weight lost per second, 1e18 scaled
    uint256 public lastPoint;
    mapping(uint256 => uint256) public slopeChanges; // week => slope removed

    event Locked(address indexed user, uint256 amount, uint256 end);
    event Withdrawn(address indexed user, uint256 amount);

    constructor(address token_, string memory name_, string memory symbol_, uint256 maxLock_, address owner_)
        Owned(owner_)
    {
        require(token_ != address(0), "NO_TOKEN");
        require(maxLock_ >= WEEK && maxLock_ <= 4 * 365 days, "BAD_MAXLOCK");
        token = IERC20(token_);
        name = name_;
        symbol = symbol_;
        maxLock = maxLock_;
        lastPoint = (block.timestamp / WEEK) * WEEK;
    }

    // ── weights ───────────────────────────────────────────────────────────

    function balanceOf(address user) public view returns (uint256) {
        Lock memory l = locks[user];
        if (l.amount == 0 || l.end <= block.timestamp) return 0;
        return (l.amount * (l.end - block.timestamp)) / maxLock;
    }

    /// Total weight right now, walking the scheduled expiries week by week.
    function totalSupply() public view returns (uint256) {
        uint256 t = lastPoint;
        uint256 b = bias;
        uint256 s = slope;
        for (uint256 i = 0; i < 208; i++) {
            uint256 next = t + WEEK;
            if (next > block.timestamp) {
                uint256 tail = (s * (block.timestamp - t)) / 1e18;
                return b > tail ? b - tail : 0;
            }
            uint256 decay = (s * WEEK) / 1e18;
            b = b > decay ? b - decay : 0;
            uint256 removed = slopeChanges[next];
            s = removed > s ? 0 : s - removed;
            t = next;
        }
        return b;
    }

    /// Advance the stored global point. Anyone may call it; every locking
    /// action does it anyway.
    function checkpoint() public {
        uint256 t = lastPoint;
        for (uint256 i = 0; i < 208; i++) {
            uint256 next = t + WEEK;
            if (next > block.timestamp) break;
            uint256 decay = (slope * WEEK) / 1e18;
            bias = bias > decay ? bias - decay : 0;
            uint256 removed = slopeChanges[next];
            slope = removed > slope ? 0 : slope - removed;
            t = next;
        }
        uint256 tail = (slope * (block.timestamp - t)) / 1e18;
        bias = bias > tail ? bias - tail : 0;
        lastPoint = block.timestamp;
    }

    function _addWeight(uint256 amount, uint256 end) internal {
        uint256 userSlope = (amount * 1e18) / maxLock;
        bias += (amount * (end - block.timestamp)) / maxLock;
        slope += userSlope;
        slopeChanges[end] += userSlope;
    }

    function _removeWeight(Lock memory l) internal {
        if (l.amount == 0 || l.end <= block.timestamp) return;
        uint256 userSlope = (l.amount * 1e18) / maxLock;
        uint256 weight = (l.amount * (l.end - block.timestamp)) / maxLock;
        bias = bias > weight ? bias - weight : 0;
        slope = userSlope > slope ? 0 : slope - userSlope;
        slopeChanges[l.end] = slopeChanges[l.end] > userSlope ? slopeChanges[l.end] - userSlope : 0;
    }

    // ── locking ───────────────────────────────────────────────────────────

    /// Lock ends are rounded down to a week boundary, so weights across users
    /// share the same expiry grid the gauge votes on.
    function createLock(uint256 amount, uint256 unlockTime) external {
        require(amount > 0, "ZERO");
        require(locks[msg.sender].amount == 0, "EXISTING_LOCK");
        uint256 end = (unlockTime / WEEK) * WEEK;
        require(end > block.timestamp, "TOO_SHORT");
        require(end <= block.timestamp + maxLock, "TOO_LONG");
        checkpoint();
        token.pull(msg.sender, amount);
        locks[msg.sender] = Lock({amount: amount, end: end});
        totalLocked += amount;
        _addWeight(amount, end);
        emit Locked(msg.sender, amount, end);
    }

    function increaseAmount(uint256 amount) external {
        Lock storage l = locks[msg.sender];
        require(amount > 0, "ZERO");
        require(l.amount > 0 && l.end > block.timestamp, "NO_LOCK");
        checkpoint();
        _removeWeight(l);
        token.pull(msg.sender, amount);
        l.amount += amount;
        totalLocked += amount;
        _addWeight(l.amount, l.end);
        emit Locked(msg.sender, l.amount, l.end);
    }

    function increaseUnlockTime(uint256 unlockTime) external {
        Lock storage l = locks[msg.sender];
        require(l.amount > 0 && l.end > block.timestamp, "NO_LOCK");
        uint256 end = (unlockTime / WEEK) * WEEK;
        require(end > l.end, "NOT_LONGER");
        require(end <= block.timestamp + maxLock, "TOO_LONG");
        checkpoint();
        _removeWeight(l);
        l.end = end;
        _addWeight(l.amount, end);
        emit Locked(msg.sender, l.amount, end);
    }

    function withdraw() external returns (uint256 amount) {
        Lock storage l = locks[msg.sender];
        require(l.amount > 0, "NO_LOCK");
        require(block.timestamp >= l.end, "LOCKED");
        checkpoint();
        amount = l.amount;
        l.amount = 0;
        l.end = 0;
        totalLocked -= amount;
        token.push(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    // ── the non-transferable part ─────────────────────────────────────────

    function transfer(address, uint256) external pure returns (bool) {
        revert("NON_TRANSFERABLE");
    }

    function transferFrom(address, address, uint256) external pure returns (bool) {
        revert("NON_TRANSFERABLE");
    }

    function approve(address, uint256) external pure returns (bool) {
        revert("NON_TRANSFERABLE");
    }

    function allowance(address, address) external pure returns (uint256) {
        return 0;
    }
}
