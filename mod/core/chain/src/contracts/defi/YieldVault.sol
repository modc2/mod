// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./IYieldAdapter.sol";

/// @dev Minimal view into the existing Market contract. Market.mint pulls the
/// payment token to the Treasury and mints (Market) native tokens to the caller.
interface IMarket {
    function mint(address paymentToken, uint256 paymentAmount) external returns (uint256);
}

/**
 * @title YieldVault
 * @dev Modular, low-fidelity DeFi yield aggregator.
 *
 * Users deposit a whitelisted asset (USDC/USDT/WETH/...) into one of many registered
 * STRATEGIES. Each strategy is a pluggable {IYieldAdapter} over a single asset, so the
 * owner can offer multiple lowfi yield options side by side (e.g. a conservative mock,
 * an aggressive mock, Aave, etc.) and add new ones without touching this contract.
 *
 * Yield model (decided): on `harvest`, realized profit (adapter.totalAssets above tracked
 * principal) is routed through the existing {Market} contract via `Market.mint`. Market
 * sends the profit underlying to the Treasury and mints native tokens to this vault; the
 * vault distributes those native tokens to the strategy's depositors pro-rata using a
 * MasterChef-style accumulator. Principal stays in the adapter, redeemable ~1:1.
 *
 * Shares are minted 1:1 with measured principal (no price-per-share), which removes the
 * first-depositor / donation inflation vector entirely.
 */
contract YieldVault is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 private constant ACC_PRECISION = 1e18;

    /// Native reward token minted by Market and distributed to depositors.
    IMarket public immutable market;
    IERC20 public immutable nativeToken;

    struct Strategy {
        IYieldAdapter adapter;       // pluggable venue connector
        address asset;               // underlying asset (== adapter.asset())
        string name;                 // human label, e.g. "USDC · Conservative"
        bool enabled;                // deposits allowed when true
        uint256 totalShares;         // == claimable principal (1:1 model)
        uint256 trackedPrincipal;    // underlying deployed via this strategy
        uint256 accRewardPerShare;   // scaled by ACC_PRECISION, in nativeToken units
    }

    struct UserInfo {
        uint256 shares;
        uint256 rewardDebt;
    }

    Strategy[] public strategies;
    mapping(uint256 => mapping(address => UserInfo)) public users; // strategyId => user => info

    event StrategyAdded(uint256 indexed id, address indexed asset, address indexed adapter, string name);
    event StrategyEnabled(uint256 indexed id, bool enabled);
    event AdapterMigrated(uint256 indexed id, address indexed oldAdapter, address indexed newAdapter, uint256 principal);
    event Deposit(uint256 indexed id, address indexed user, uint256 amount, uint256 shares);
    event Withdraw(uint256 indexed id, address indexed user, uint256 amount, uint256 shares);
    event Harvest(uint256 indexed id, uint256 profit, uint256 mintedNative, uint256 accRewardPerShare);
    event Claim(uint256 indexed id, address indexed user, uint256 reward);

    constructor(address _market, address _nativeToken) {
        require(_market != address(0) && _nativeToken != address(0), "Invalid market");
        market = IMarket(_market);
        nativeToken = IERC20(_nativeToken);
    }

    // ========== ADMIN ==========

    /// @dev Register a new yield strategy. The adapter must already report `asset`.
    function addStrategy(address asset, address adapter, string calldata name)
        external onlyOwner returns (uint256 id)
    {
        require(asset != address(0) && adapter != address(0), "Zero addr");
        require(IYieldAdapter(adapter).asset() == asset, "Adapter asset mismatch");
        id = strategies.length;
        strategies.push(Strategy({
            adapter: IYieldAdapter(adapter),
            asset: asset,
            name: name,
            enabled: true,
            totalShares: 0,
            trackedPrincipal: 0,
            accRewardPerShare: 0
        }));
        emit StrategyAdded(id, asset, adapter, name);
    }

    function setStrategyEnabled(uint256 id, bool enabled) external onlyOwner {
        _exists(id);
        strategies[id].enabled = enabled;
        emit StrategyEnabled(id, enabled);
    }

    /// @dev Migrate a strategy to a new adapter (same asset): pull all principal out of
    /// the old adapter and redeploy into the new one. Yield should be harvested first.
    function setAdapter(uint256 id, address newAdapter) external onlyOwner nonReentrant {
        _exists(id);
        Strategy storage s = strategies[id];
        require(IYieldAdapter(newAdapter).asset() == s.asset, "Adapter asset mismatch");
        address old = address(s.adapter);

        uint256 bal = s.adapter.totalAssets();
        if (bal > 0) {
            s.adapter.withdraw(bal, address(this));
            IERC20(s.asset).forceApprove(newAdapter, bal);
            IYieldAdapter(newAdapter).deposit(bal);
        }
        s.adapter = IYieldAdapter(newAdapter);
        emit AdapterMigrated(id, old, newAdapter, bal);
    }

    function pause() external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    // ========== CORE ==========

    /// @dev Deposit `amount` of a strategy's asset and receive principal shares.
    function deposit(uint256 id, uint256 amount) external nonReentrant whenNotPaused {
        _exists(id);
        require(amount > 0, "Zero amount");
        Strategy storage s = strategies[id];
        require(s.enabled, "Strategy disabled");
        UserInfo storage u = users[id][msg.sender];

        _settle(s, u, id);

        IERC20(s.asset).safeTransferFrom(msg.sender, address(this), amount);
        IERC20(s.asset).forceApprove(address(s.adapter), amount);
        uint256 before = s.adapter.totalAssets();
        s.adapter.deposit(amount);
        uint256 added = s.adapter.totalAssets() - before; // measured principal in

        s.totalShares += added;
        s.trackedPrincipal += added;
        u.shares += added;
        u.rewardDebt = (u.shares * s.accRewardPerShare) / ACC_PRECISION;

        emit Deposit(id, msg.sender, added, added);
    }

    /// @dev Burn `shares` of principal and receive the underlying asset back.
    /// Withdraw is allowed even when paused so users can always exit.
    function withdraw(uint256 id, uint256 shares) external nonReentrant {
        _exists(id);
        Strategy storage s = strategies[id];
        UserInfo storage u = users[id][msg.sender];
        require(shares > 0 && u.shares >= shares, "Bad shares");

        _settle(s, u, id);

        // CEI: mutate accounting before the external adapter call.
        s.totalShares -= shares;
        s.trackedPrincipal -= shares;
        u.shares -= shares;
        u.rewardDebt = (u.shares * s.accRewardPerShare) / ACC_PRECISION;

        uint256 got = s.adapter.withdraw(shares, msg.sender);
        emit Withdraw(id, msg.sender, got, shares);
    }

    /// @dev Realize a strategy's yield and route it into native reward tokens.
    function harvest(uint256 id) public nonReentrant whenNotPaused {
        _exists(id);
        Strategy storage s = strategies[id];
        if (s.totalShares == 0) return;

        uint256 ta = s.adapter.totalAssets();
        if (ta <= s.trackedPrincipal) return; // no profit (or transient loss)
        uint256 profit = ta - s.trackedPrincipal;

        s.adapter.withdraw(profit, address(this));
        IERC20(s.asset).forceApprove(address(market), profit);

        uint256 b0 = nativeToken.balanceOf(address(this));
        market.mint(s.asset, profit); // pulls profit -> Treasury, mints native to vault
        uint256 minted = nativeToken.balanceOf(address(this)) - b0; // net of Market fee

        s.accRewardPerShare += (minted * ACC_PRECISION) / s.totalShares;
        emit Harvest(id, profit, minted, s.accRewardPerShare);
    }

    /// @dev Claim accrued native reward tokens for a strategy.
    function claim(uint256 id) public nonReentrant {
        _exists(id);
        Strategy storage s = strategies[id];
        UserInfo storage u = users[id][msg.sender];
        _settle(s, u, id);
        u.rewardDebt = (u.shares * s.accRewardPerShare) / ACC_PRECISION;
    }

    // ========== INTERNAL ==========

    function _settle(Strategy storage s, UserInfo storage u, uint256 id) internal {
        if (u.shares == 0) return;
        uint256 pending = (u.shares * s.accRewardPerShare) / ACC_PRECISION - u.rewardDebt;
        if (pending > 0) {
            nativeToken.safeTransfer(msg.sender, pending);
            emit Claim(id, msg.sender, pending);
        }
    }

    function _exists(uint256 id) internal view {
        require(id < strategies.length, "No strategy");
    }

    // ========== VIEWS ==========

    function strategyCount() external view returns (uint256) {
        return strategies.length;
    }

    /// @dev Unclaimed native reward for a user in a strategy.
    function pendingReward(uint256 id, address user) external view returns (uint256) {
        if (id >= strategies.length) return 0;
        Strategy storage s = strategies[id];
        UserInfo storage u = users[id][user];
        if (u.shares == 0) return 0;
        return (u.shares * s.accRewardPerShare) / ACC_PRECISION - u.rewardDebt;
    }

    /// @dev Harvestable profit currently sitting in a strategy's adapter.
    function pendingProfit(uint256 id) external view returns (uint256) {
        if (id >= strategies.length) return 0;
        Strategy storage s = strategies[id];
        uint256 ta = s.adapter.totalAssets();
        return ta > s.trackedPrincipal ? ta - s.trackedPrincipal : 0;
    }

    function userShares(uint256 id, address user) external view returns (uint256) {
        if (id >= strategies.length) return 0;
        return users[id][user].shares;
    }
}
