// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

interface IRegistry {
    function getMod(uint256 id) external view returns (address owner, string memory name, string memory data);
    function nextModId() external view returns (uint256);
}

/**
 * @title AppStaking
 * @dev Stake BlocTime (BLOC) on apps registered in the mod protocol Registry.
 *
 * Each registered app (a Registry mod id) has its own staking pool. Anyone
 * holding BLOC can back an app by staking on it; total stake per app is the
 * curation signal. Stakes are never locked — unstaking is always allowed,
 * even if the app is later removed from the Registry (only *new* stakes
 * require the app to exist).
 *
 * Rewards: anyone (typically the app's owner) can add BLOC rewards to an
 * app's pool with reward(); they are split pro-rata among that app's stakers
 * at that moment (MasterChef-style accRewardPerShare accounting) and pulled
 * with claim().
 */
contract AppStaking {
    uint256 private constant ACC = 1e18;

    IERC20 public immutable bloc;
    IRegistry public immutable registry;

    // app id => total BLOC staked on it
    mapping(uint256 => uint256) public totalStaked;
    // app id => staker => BLOC staked
    mapping(uint256 => mapping(address => uint256)) public staked;
    // BLOC held for principal across all apps (excludes undistributed dust)
    uint256 public totalStakedAll;

    // rewards, per app
    mapping(uint256 => uint256) public accRewardPerShare; // scaled by ACC
    mapping(uint256 => uint256) public totalRewarded;
    mapping(uint256 => mapping(address => uint256)) private rewardDebt;
    mapping(uint256 => mapping(address => uint256)) private owed;

    // enumeration (append-only; amounts may be zero after full unstake)
    uint256[] private stakedAppIds;
    mapping(uint256 => bool) private appSeen;
    mapping(uint256 => address[]) private appStakers;
    mapping(uint256 => mapping(address => bool)) private stakerSeen;
    mapping(address => uint256[]) private userAppIds;
    mapping(address => mapping(uint256 => bool)) private userAppSeen;

    event Staked(uint256 indexed appId, address indexed user, uint256 amount);
    event Unstaked(uint256 indexed appId, address indexed user, uint256 amount);
    event Rewarded(uint256 indexed appId, address indexed from, uint256 amount);
    event Claimed(uint256 indexed appId, address indexed user, uint256 amount);

    uint256 private unlocked = 1;
    modifier nonReentrant() {
        require(unlocked == 1, "reentrant");
        unlocked = 0;
        _;
        unlocked = 1;
    }

    constructor(address _bloc, address _registry) {
        require(_bloc != address(0) && _registry != address(0), "zero address");
        bloc = IERC20(_bloc);
        registry = IRegistry(_registry);
    }

    // ---------------------------------------------------------------- actions

    /// @notice Stake BLOC on a registered app. Requires prior ERC20 approve.
    function stake(uint256 appId, uint256 amount) external nonReentrant {
        require(amount > 0, "amount = 0");
        (address owner, , ) = registry.getMod(appId);
        require(owner != address(0), "app not registered");

        _settle(appId, msg.sender);
        require(bloc.transferFrom(msg.sender, address(this), amount), "transfer failed");

        staked[appId][msg.sender] += amount;
        totalStaked[appId] += amount;
        totalStakedAll += amount;
        rewardDebt[appId][msg.sender] = (staked[appId][msg.sender] * accRewardPerShare[appId]) / ACC;

        if (!appSeen[appId]) { appSeen[appId] = true; stakedAppIds.push(appId); }
        if (!stakerSeen[appId][msg.sender]) { stakerSeen[appId][msg.sender] = true; appStakers[appId].push(msg.sender); }
        if (!userAppSeen[msg.sender][appId]) { userAppSeen[msg.sender][appId] = true; userAppIds[msg.sender].push(appId); }

        emit Staked(appId, msg.sender, amount);
    }

    /// @notice Unstake BLOC from an app. amount = 0 unstakes everything.
    ///         Never gated on the app still being registered.
    function unstake(uint256 appId, uint256 amount) external nonReentrant {
        uint256 balance = staked[appId][msg.sender];
        require(balance > 0, "nothing staked");
        if (amount == 0) amount = balance;
        require(amount <= balance, "exceeds stake");

        _settle(appId, msg.sender);

        staked[appId][msg.sender] = balance - amount;
        totalStaked[appId] -= amount;
        totalStakedAll -= amount;
        rewardDebt[appId][msg.sender] = (staked[appId][msg.sender] * accRewardPerShare[appId]) / ACC;

        require(bloc.transfer(msg.sender, amount), "transfer failed");
        emit Unstaked(appId, msg.sender, amount);
    }

    /// @notice Add BLOC rewards for an app, split pro-rata among its current stakers.
    function reward(uint256 appId, uint256 amount) external nonReentrant {
        require(amount > 0, "amount = 0");
        require(totalStaked[appId] > 0, "no stakers");
        require(bloc.transferFrom(msg.sender, address(this), amount), "transfer failed");

        accRewardPerShare[appId] += (amount * ACC) / totalStaked[appId];
        totalRewarded[appId] += amount;
        emit Rewarded(appId, msg.sender, amount);
    }

    /// @notice Claim accrued rewards for one app.
    function claim(uint256 appId) public nonReentrant {
        _settle(appId, msg.sender);
        uint256 amount = owed[appId][msg.sender];
        if (amount == 0) return;
        owed[appId][msg.sender] = 0;
        require(bloc.transfer(msg.sender, amount), "transfer failed");
        emit Claimed(appId, msg.sender, amount);
    }

    /// @notice Claim accrued rewards for many apps at once.
    function claimMany(uint256[] calldata appIds) external {
        for (uint256 i = 0; i < appIds.length; i++) claim(appIds[i]);
    }

    // -------------------------------------------------------------- internals

    /// @dev Move any newly-accrued rewards into owed[] and reset the debt line.
    function _settle(uint256 appId, address user) private {
        uint256 balance = staked[appId][user];
        if (balance > 0) {
            uint256 accrued = (balance * accRewardPerShare[appId]) / ACC;
            uint256 debt = rewardDebt[appId][user];
            if (accrued > debt) owed[appId][user] += accrued - debt;
        }
        rewardDebt[appId][user] = (balance * accRewardPerShare[appId]) / ACC;
    }

    // ------------------------------------------------------------------ views

    /// @notice Rewards a user could claim right now for an app.
    function earned(uint256 appId, address user) public view returns (uint256) {
        uint256 pending = owed[appId][user];
        uint256 balance = staked[appId][user];
        if (balance > 0) {
            uint256 accrued = (balance * accRewardPerShare[appId]) / ACC;
            uint256 debt = rewardDebt[appId][user];
            if (accrued > debt) pending += accrued - debt;
        }
        return pending;
    }

    /// @notice Every app id that has ever been staked on.
    function getStakedApps() external view returns (uint256[] memory) {
        return stakedAppIds;
    }

    /// @notice Current totals for a list of app ids.
    function getTotals(uint256[] calldata appIds)
        external view returns (uint256[] memory totals, uint256[] memory rewards, uint256[] memory stakerCounts)
    {
        totals = new uint256[](appIds.length);
        rewards = new uint256[](appIds.length);
        stakerCounts = new uint256[](appIds.length);
        for (uint256 i = 0; i < appIds.length; i++) {
            totals[i] = totalStaked[appIds[i]];
            rewards[i] = totalRewarded[appIds[i]];
            stakerCounts[i] = appStakers[appIds[i]].length;
        }
    }

    /// @notice The staker book for one app (addresses ever staked + live amounts).
    function getAppStakers(uint256 appId)
        external view returns (address[] memory stakers, uint256[] memory amounts)
    {
        stakers = appStakers[appId];
        amounts = new uint256[](stakers.length);
        for (uint256 i = 0; i < stakers.length; i++) amounts[i] = staked[appId][stakers[i]];
    }

    /// @notice A user's positions across every app they ever staked on.
    function getPositions(address user)
        external view returns (uint256[] memory appIds, uint256[] memory amounts, uint256[] memory claimable)
    {
        appIds = userAppIds[user];
        amounts = new uint256[](appIds.length);
        claimable = new uint256[](appIds.length);
        for (uint256 i = 0; i < appIds.length; i++) {
            amounts[i] = staked[appIds[i]][user];
            claimable[i] = earned(appIds[i], user);
        }
    }
}
