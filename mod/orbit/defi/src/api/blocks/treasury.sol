// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// A treasury that locks what you put in and pays it out one week at a time,
/// on BlocTime's clock, split by BlocTime weight.
///
/// Two ports do the work. `asset` is what the treasury holds and distributes —
/// a stablecoin, a vault share, whatever the position you chose pays in.
/// `weight` is the BLOC token of a BlocTime deployment: the treasury never
/// stakes it and never moves it, it only reads `balanceOf` to decide who gets
/// what share of a week's payout.
///
/// The window is copied from BlocTime.sol rather than re-derived, so a payout
/// here lands in the same instant as the one there: unix time 0 was a Thursday
/// 00:00 UTC, so every 7-day window starts on a Thursday, and Friday 12:00 EST
/// is 1 day 17 hours in. Pinned to EST year round, so the payout is the same
/// UTC instant every week (11:00 local in New York over the summer).
///
/// Two things a lock can be:
///   * returnPrincipal = false — the principal itself is the payout. It is
///     released in `termWeeks` equal weekly slices, and after the last one the
///     lock is closed and nothing comes back.
///   * returnPrincipal = true — the principal is escrowed until the term ends
///     and then withdrawable by whoever locked it. Only yield the treasury
///     earns on top is distributed. This is the "park it and share the yield"
///     shape.
/// Either way the depositor cannot take the principal back early. That is what
/// makes it a lock rather than a wallet with extra steps.
///
/// WHY A REGISTERED SET, NOT EVERY HOLDER. BlocTime can pay every holder with a
/// Synthetix accumulator because it *is* the BLOC token and can checkpoint both
/// sides of a transfer. This contract only reads that token from outside, so an
/// accumulator over totalSupply would credit whoever bought BLOC after the
/// distribution — the retroactive-reward bug BlocTime's `_update` hook exists to
/// prevent. So eligibility here is explicit: `register()` (permissionless, once,
/// anyone) puts you in the set, and a distribution snapshots `balanceOf` across
/// that set at the moment it runs. Holders outside the set earn nothing, and
/// the set splits the whole week.
contract ModBlocTimeTreasury is Owned {
    using SafeTransfer for IERC20;

    /// What the treasury holds and pays out.
    IERC20 public immutable asset;
    /// The BLOC token whose balances decide the split. Never moved by this
    /// contract — only read.
    IERC20 public immutable weight;

    // ── BlocTime's weekly window, verbatim ────────────────────────────────
    uint256 public constant DISTRIBUTION_PERIOD = 7 days;
    uint256 public constant DISTRIBUTION_OFFSET = 1 days + 17 hours;

    /// Iteration bounds. Distribution walks both sets in one transaction, so
    /// they are capped where the loop still fits comfortably in a block.
    uint256 public constant MAX_PARTICIPANTS = 256;
    uint256 public constant MAX_ACTIVE_LOCKS = 128;

    struct Lock {
        address owner;
        uint256 amount;          // principal deposited
        uint256 released;        // principal already streamed into payouts
        uint64 startTime;
        uint32 termWeeks;        // number of weekly slices, or weeks escrowed
        uint32 weeksElapsed;     // slices already taken
        bool returnPrincipal;
        bool closed;
    }

    Lock[] public locks;
    /// Ids of locks still streaming or still escrowed, for the payout loop.
    uint256[] public activeLocks;

    /// Principal this contract still owes to a lock — escrowed or unreleased.
    /// Never distributable.
    uint256 public lockedOutstanding;
    /// Credited to participants but not yet pulled. Never distributable.
    uint256 public unclaimed;

    address[] public participants;
    mapping(address => uint256) private participantAt; // 1-based; 0 = not in
    mapping(address => uint256) public claimable;
    mapping(address => uint256) public claimed;

    uint256 public immutable distributionStart;
    uint256 public lastDistributionTime; // window paid out last (0 = never)
    uint256 public totalDistributed;
    uint256 public distributions;

    event Registered(address indexed who);
    event Deregistered(address indexed who);
    event Locked(uint256 indexed id, address indexed owner, uint256 amount, uint32 termWeeks, bool returnPrincipal);
    event Funded(address indexed from, uint256 amount);
    event Distributed(uint256 indexed windowStart, uint256 amount, uint256 totalWeight, uint256 participants);
    event Claimed(address indexed who, uint256 amount);
    event Withdrawn(uint256 indexed id, address indexed owner, uint256 amount);

    constructor(address asset_, address weight_, address owner_) Owned(owner_) {
        require(asset_ != address(0), "NO_ASSET");
        require(weight_ != address(0), "NO_WEIGHT");
        asset = IERC20(asset_);
        weight = IERC20(weight_);
        distributionStart = block.timestamp;
    }

    // ── the clock ─────────────────────────────────────────────────────────

    /// Start of the weekly window containing `ts` — Friday 12:00 EST.
    function windowStart(uint256 ts) public pure returns (uint256) {
        uint256 b = (ts / DISTRIBUTION_PERIOD) * DISTRIBUTION_PERIOD + DISTRIBUTION_OFFSET;
        if (b <= ts) return b;
        return b >= DISTRIBUTION_PERIOD ? b - DISTRIBUTION_PERIOD : 0;
    }

    function nextDistributionTime() public view returns (uint256) {
        uint256 from = lastDistributionTime == 0 ? distributionStart : lastDistributionTime;
        return windowStart(from) + DISTRIBUTION_PERIOD;
    }

    function distributionDue() public view returns (bool) {
        return block.timestamp >= nextDistributionTime();
    }

    // ── eligibility ───────────────────────────────────────────────────────

    function register() external {
        _register(msg.sender);
    }

    /// Add someone else. Enrolling a holder can only ever dilute you, so this
    /// needs no permission — it is the honest direction for an open call.
    function registerFor(address who) external {
        _register(who);
    }

    function _register(address who) internal {
        require(who != address(0) && who != address(this), "BAD_ADDRESS");
        if (participantAt[who] != 0) return;
        require(participants.length < MAX_PARTICIPANTS, "FULL");
        participants.push(who);
        participantAt[who] = participants.length;
        emit Registered(who);
    }

    /// Leave the set. Anything already credited stays claimable.
    function deregister() external {
        uint256 index = participantAt[msg.sender];
        require(index != 0, "NOT_IN");
        uint256 last = participants.length - 1;
        if (index - 1 != last) {
            address moved = participants[last];
            participants[index - 1] = moved;
            participantAt[moved] = index;
        }
        participants.pop();
        participantAt[msg.sender] = 0;
        emit Deregistered(msg.sender);
    }

    function isRegistered(address who) external view returns (bool) {
        return participantAt[who] != 0;
    }

    function participantCount() external view returns (uint256) {
        return participants.length;
    }

    /// Sum of BLOC held by the registered set, right now. This is the
    /// denominator a distribution would use.
    function totalWeight() public view returns (uint256 total) {
        for (uint256 i = 0; i < participants.length; i++) {
            total += weight.balanceOf(participants[i]);
        }
    }

    // ── locking ───────────────────────────────────────────────────────────

    /// Lock `amount` of the asset for `termWeeks` weekly distributions.
    ///
    /// `returnPrincipal = false` streams the principal itself out, a slice a
    /// week. `true` escrows it whole and hands it back after the term, so only
    /// the yield on top is shared. Either way you cannot take it back early.
    function lock(uint256 amount, uint32 termWeeks, bool returnPrincipal)
        external
        returns (uint256 id)
    {
        require(amount > 0, "ZERO");
        require(termWeeks > 0 && termWeeks <= 520, "TERM");
        require(activeLocks.length < MAX_ACTIVE_LOCKS, "TOO_MANY_LOCKS");
        asset.pull(msg.sender, amount);
        id = locks.length;
        locks.push(Lock({
            owner: msg.sender,
            amount: amount,
            released: 0,
            startTime: uint64(block.timestamp),
            termWeeks: termWeeks,
            weeksElapsed: 0,
            returnPrincipal: returnPrincipal,
            closed: false
        }));
        activeLocks.push(id);
        lockedOutstanding += amount;
        emit Locked(id, msg.sender, amount, termWeeks, returnPrincipal);
    }

    /// Send the asset straight into next week's payout, with no lock and no
    /// claim on it. A plain `transfer` in does the same thing; this only exists
    /// so the deposit shows up as an event.
    function fund(uint256 amount) external {
        require(amount > 0, "ZERO");
        asset.pull(msg.sender, amount);
        emit Funded(msg.sender, amount);
    }

    function lockCount() external view returns (uint256) {
        return locks.length;
    }

    function activeLockCount() external view returns (uint256) {
        return activeLocks.length;
    }

    /// Principal a streaming lock would release into the next payout.
    function sliceOf(uint256 id) public view returns (uint256) {
        Lock storage l = locks[id];
        if (l.closed || l.returnPrincipal) return 0;
        if (l.weeksElapsed >= l.termWeeks) return 0;
        // The last slice takes the remainder, so rounding never strands dust
        // inside a lock.
        if (l.weeksElapsed + 1 == l.termWeeks) return l.amount - l.released;
        return l.amount / l.termWeeks;
    }

    /// Take this week's slice out of every streaming lock, and close any
    /// escrowed lock whose term has run out so its owner can withdraw.
    function _vest() internal returns (uint256 vested) {
        for (uint256 i = activeLocks.length; i > 0; i--) {
            uint256 id = activeLocks[i - 1];
            Lock storage l = locks[id];
            if (l.weeksElapsed < l.termWeeks) l.weeksElapsed += 1;

            if (!l.returnPrincipal) {
                uint256 slice = l.weeksElapsed == l.termWeeks
                    ? l.amount - l.released
                    : l.amount / l.termWeeks;
                l.released += slice;
                vested += slice;
                lockedOutstanding -= slice;
            }

            if (l.weeksElapsed >= l.termWeeks) {
                // A streaming lock is finished; an escrowed one is now
                // withdrawable but still owes its principal, so it keeps its
                // share of lockedOutstanding until withdraw().
                if (!l.returnPrincipal) l.closed = true;
                _dropActive(i - 1);
            }
        }
    }

    function _dropActive(uint256 index) internal {
        uint256 last = activeLocks.length - 1;
        if (index != last) activeLocks[index] = activeLocks[last];
        activeLocks.pop();
    }

    /// The asset in hand that belongs to nobody yet: yield, donations, and
    /// principal already released by a streaming lock.
    function distributable() public view returns (uint256) {
        uint256 balance = asset.balanceOf(address(this));
        uint256 spoken = lockedOutstanding + unclaimed;
        return balance > spoken ? balance - spoken : 0;
    }

    /// What `distribute()` would pay out if it ran now — surplus already in
    /// hand plus the slices this week's vesting would release.
    function pendingPayout() external view returns (uint256 total) {
        total = distributable();
        for (uint256 i = 0; i < activeLocks.length; i++) {
            total += sliceOf(activeLocks[i]);
        }
    }

    // ── the weekly payout ─────────────────────────────────────────────────

    /// Sweep the week's payout to the registered set, pro-rata by BLOC.
    /// Permissionless — anyone may call it once the window opens, and nobody
    /// can call it twice in the same week.
    function distribute() external returns (uint256 amount) {
        require(distributionDue(), "NOT_DISTRIBUTION_TIME");

        uint256 total = totalWeight();
        require(total > 0, "NO_WEIGHT_REGISTERED");

        _vest();
        amount = distributable();
        require(amount > 0, "NOTHING_TO_DISTRIBUTE");

        uint256 paid;
        for (uint256 i = 0; i < participants.length; i++) {
            address who = participants[i];
            uint256 w = weight.balanceOf(who);
            if (w == 0) continue;
            uint256 share = (amount * w) / total;
            if (share == 0) continue;
            claimable[who] += share;
            paid += share;
        }

        // Rounding dust is left in hand rather than given to whoever the loop
        // happened to reach last, so it rides along into next week.
        unclaimed += paid;
        totalDistributed += paid;
        distributions += 1;
        lastDistributionTime = windowStart(block.timestamp);
        emit Distributed(lastDistributionTime, paid, total, participants.length);
        return paid;
    }

    function claim() external returns (uint256 amount) {
        amount = claimable[msg.sender];
        require(amount > 0, "NOTHING");
        claimable[msg.sender] = 0;
        claimed[msg.sender] += amount;
        unclaimed -= amount;
        asset.push(msg.sender, amount);
        emit Claimed(msg.sender, amount);
    }

    /// Principal back, for an escrowed lock whose term has run out.
    function withdraw(uint256 id) external returns (uint256 amount) {
        Lock storage l = locks[id];
        require(l.owner == msg.sender, "NOT_YOURS");
        require(!l.closed, "CLOSED");
        require(l.returnPrincipal, "STREAMING");
        require(unlockTime(id) <= block.timestamp, "STILL_LOCKED");
        amount = l.amount - l.released;
        require(amount > 0, "NOTHING");
        l.closed = true;
        l.released = l.amount;
        lockedOutstanding -= amount;
        asset.push(msg.sender, amount);
        emit Withdrawn(id, msg.sender, amount);
    }

    /// When an escrowed lock's principal becomes withdrawable. Measured in
    /// distribution windows, not raw weeks: the term ends after the term-th
    /// payout window from the one the lock was opened in.
    function unlockTime(uint256 id) public view returns (uint256) {
        Lock storage l = locks[id];
        return windowStart(l.startTime) + (uint256(l.termWeeks) + 1) * DISTRIBUTION_PERIOD;
    }

    /// Sweep a token that is not the asset out to the owner. Deliberately
    /// cannot touch the asset: the point of the lock is that no key, including
    /// the owner's, can shorten it.
    function rescue(address token, address to) external onlyOwner returns (uint256 amount) {
        require(token != address(asset), "ASSET_IS_LOCKED");
        require(to != address(0), "BAD_ADDRESS");
        amount = IERC20(token).balanceOf(address(this));
        require(amount > 0, "NOTHING");
        IERC20(token).push(to, amount);
    }

    /// Everything a console needs about a lock in one call.
    struct LockView {
        address owner;
        uint256 amount;
        uint256 released;
        uint64 startTime;
        uint32 termWeeks;
        uint32 weeksElapsed;
        bool returnPrincipal;
        bool closed;
        uint256 unlocksAt;
        uint256 nextSlice;
    }

    function lockInfo(uint256 id) external view returns (LockView memory) {
        Lock storage l = locks[id];
        return LockView({
            owner: l.owner,
            amount: l.amount,
            released: l.released,
            startTime: l.startTime,
            termWeeks: l.termWeeks,
            weeksElapsed: l.weeksElapsed,
            returnPrincipal: l.returnPrincipal,
            closed: l.closed,
            unlocksAt: unlockTime(id),
            nextSlice: sliceOf(id)
        });
    }

    /// The whole treasury in one read, for a dashboard that would otherwise
    /// make eight calls.
    struct Summary {
        uint256 balance;          // asset held right now
        uint256 locked;           // principal still owed to locks
        uint256 owed;             // credited to holders, not yet claimed
        uint256 payoutNow;        // what distribute() would pay this week
        uint256 weightRegistered; // BLOC held by the registered set
        uint256 holders;          // size of that set
        uint256 nextAt;           // next Friday 12:00 EST, unix
        bool due;                 // the window is open right now
        uint256 paidTotal;        // distributed since deployment
        uint256 weeksPaid;        // number of distributions
    }

    function summary() external view returns (Summary memory) {
        uint256 pending = distributable();
        for (uint256 i = 0; i < activeLocks.length; i++) {
            pending += sliceOf(activeLocks[i]);
        }
        return Summary({
            balance: asset.balanceOf(address(this)),
            locked: lockedOutstanding,
            owed: unclaimed,
            payoutNow: pending,
            weightRegistered: totalWeight(),
            holders: participants.length,
            nextAt: nextDistributionTime(),
            due: distributionDue(),
            paidTotal: totalDistributed,
            weeksPaid: distributions
        });
    }
}
