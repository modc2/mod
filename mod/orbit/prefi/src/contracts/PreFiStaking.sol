// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title PreFiStaking - Lock PREFI for staketime, claim weekly treasury distributions
/// @notice staketime = amount * lockDuration. Weekly epoch rewards split by staketime weight.
contract PreFiStaking is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    struct Stake {
        address staker;
        uint256 amount;       // PREFI locked
        uint256 lockEnd;      // timestamp when lock expires
        uint256 staketime;    // amount * duration (weight for claims)
        uint256 startEpoch;   // epoch when stake was created
        bool withdrawn;
    }

    IERC20 public prefiToken;
    IERC20 public usdc;
    address public vault;

    uint256 public epochLength = 1 weeks;
    uint256 public genesisTime;
    uint256 public totalStaketime;
    uint256 public totalStaked; // total PREFI currently locked

    mapping(uint256 => Stake) public stakes;
    uint256 public nextStakeId;
    mapping(address => uint256[]) public userStakeIds;

    // Epoch tracking — use first deposit's staketime snapshot, accumulate rewards
    mapping(uint256 => uint256) public epochRewards;
    mapping(uint256 => uint256) public epochStaketime;
    mapping(uint256 => bool) public epochInitialized; // prevents overwriting staketime snapshot

    mapping(address => mapping(uint256 => bool)) public claimed;

    event Locked(uint256 indexed stakeId, address indexed staker, uint256 amount, uint256 duration, uint256 staketime);
    event Unlocked(uint256 indexed stakeId, address indexed staker, uint256 amount);
    event LockExtended(uint256 indexed stakeId, uint256 addedDuration, uint256 newStaketime);
    event RewardsDeposited(uint256 indexed epoch, uint256 amount, uint256 totalStaketime);
    event Claimed(address indexed staker, uint256 indexed epoch, uint256 amount);

    constructor(address _prefiToken, address _usdc, address _vault) {
        prefiToken = IERC20(_prefiToken);
        usdc = IERC20(_usdc);
        vault = _vault;
        genesisTime = block.timestamp;
    }

    function currentEpoch() public view returns (uint256) {
        return (block.timestamp - genesisTime) / epochLength;
    }

    // ── Staking ──────────────────────────────────────────────────────

    function lock(uint256 amount, uint256 duration) external nonReentrant returns (uint256 stakeId) {
        require(amount > 0, "zero amount");
        require(duration >= 1 weeks, "min 1 week");
        require(duration <= 52 weeks, "max 52 weeks");

        prefiToken.safeTransferFrom(msg.sender, address(this), amount);

        uint256 staketime = amount * duration;
        totalStaketime += staketime;
        totalStaked += amount;

        stakeId = nextStakeId++;
        stakes[stakeId] = Stake({
            staker: msg.sender,
            amount: amount,
            lockEnd: block.timestamp + duration,
            staketime: staketime,
            startEpoch: currentEpoch(),
            withdrawn: false
        });
        userStakeIds[msg.sender].push(stakeId);

        emit Locked(stakeId, msg.sender, amount, duration, staketime);
    }

    /// @notice Extend lock duration on an existing stake (increases staketime weight)
    function extendLock(uint256 stakeId, uint256 addedDuration) external nonReentrant {
        Stake storage s = stakes[stakeId];
        require(s.staker == msg.sender, "not your stake");
        require(!s.withdrawn, "already withdrawn");
        require(addedDuration >= 1 weeks, "min 1 week extension");

        uint256 newEnd = s.lockEnd + addedDuration;
        require(newEnd <= block.timestamp + 52 weeks, "exceeds 52 week max");

        uint256 addedStaketime = s.amount * addedDuration;
        s.lockEnd = newEnd;
        s.staketime += addedStaketime;
        totalStaketime += addedStaketime;

        emit LockExtended(stakeId, addedDuration, s.staketime);
    }

    function unlock(uint256 stakeId) external nonReentrant {
        Stake storage s = stakes[stakeId];
        require(s.staker == msg.sender, "not your stake");
        require(!s.withdrawn, "already withdrawn");
        require(block.timestamp >= s.lockEnd, "still locked");

        s.withdrawn = true;
        totalStaketime -= s.staketime;
        totalStaked -= s.amount;

        prefiToken.safeTransfer(msg.sender, s.amount);
        emit Unlocked(stakeId, msg.sender, s.amount);
    }

    // ── Rewards ──────────────────────────────────────────────────────

    /// @notice Deposit USDC rewards for the current epoch
    function depositRewards(uint256 amount) external {
        require(msg.sender == vault || msg.sender == owner(), "not authorized");
        require(amount > 0, "zero amount");
        require(totalStaketime > 0, "no stakers");

        usdc.safeTransferFrom(msg.sender, address(this), amount);

        uint256 epoch = currentEpoch();
        epochRewards[epoch] += amount;

        // Snapshot staketime only on first deposit of epoch
        if (!epochInitialized[epoch]) {
            epochStaketime[epoch] = totalStaketime;
            epochInitialized[epoch] = true;
        }

        emit RewardsDeposited(epoch, amount, totalStaketime);
    }

    /// @notice Claim share of epoch rewards based on staketime
    function claim(uint256 epoch) external nonReentrant {
        require(epoch < currentEpoch(), "epoch not ended");
        require(!claimed[msg.sender][epoch], "already claimed");
        require(epochRewards[epoch] > 0, "no rewards");

        uint256 userSt = _getUserStaketimeForEpoch(msg.sender, epoch);
        require(userSt > 0, "no staketime");

        uint256 epochSt = epochStaketime[epoch];
        require(epochSt > 0, "no staketime snapshot");

        uint256 share = (epochRewards[epoch] * userSt) / epochSt;
        require(share > 0, "zero share");

        claimed[msg.sender][epoch] = true;
        usdc.safeTransfer(msg.sender, share);

        emit Claimed(msg.sender, epoch, share);
    }

    // ── Views ────────────────────────────────────────────────────────

    function getUserStaketime(address user) external view returns (uint256 total) {
        uint256[] storage ids = userStakeIds[user];
        for (uint256 i = 0; i < ids.length; i++) {
            Stake storage s = stakes[ids[i]];
            if (!s.withdrawn) {
                total += s.staketime;
            }
        }
    }

    function getUserStakes(address user) external view returns (uint256[] memory) {
        return userStakeIds[user];
    }

    function getStake(uint256 stakeId) external view returns (
        address staker, uint256 amount, uint256 lockEnd,
        uint256 staketime, uint256 startEpoch, bool withdrawn
    ) {
        Stake storage s = stakes[stakeId];
        return (s.staker, s.amount, s.lockEnd, s.staketime, s.startEpoch, s.withdrawn);
    }

    /// @notice Check how much a user can claim for a given epoch
    function claimable(address user, uint256 epoch) external view returns (uint256) {
        if (epoch >= currentEpoch()) return 0;
        if (claimed[user][epoch]) return 0;
        if (epochRewards[epoch] == 0) return 0;

        uint256 userSt = _getUserStaketimeForEpoch(user, epoch);
        uint256 epochSt = epochStaketime[epoch];
        if (userSt == 0 || epochSt == 0) return 0;

        return (epochRewards[epoch] * userSt) / epochSt;
    }

    function _getUserStaketimeForEpoch(address user, uint256 epoch) internal view returns (uint256 total) {
        uint256 epochStart = genesisTime + (epoch * epochLength);

        uint256[] storage ids = userStakeIds[user];
        for (uint256 i = 0; i < ids.length; i++) {
            Stake storage s = stakes[ids[i]];
            // Active during epoch: started before/during, lock hadn't expired before epoch started
            if (s.startEpoch <= epoch && s.lockEnd >= epochStart && !s.withdrawn) {
                total += s.staketime;
            }
        }
    }

    // ── Admin ────────────────────────────────────────────────────────

    function setVault(address _vault) external onlyOwner {
        vault = _vault;
    }
}
