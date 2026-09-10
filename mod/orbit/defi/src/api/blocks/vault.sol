// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// ERC4626-shaped yield vault. Takes an `erc20` on its asset port, issues share
/// tokens, and optionally forwards idle capital to whatever sits on its
/// `strategy` port. The share token is itself an `erc20` output, so a vault can
/// feed a staking block, an AMM pair, or another vault.
contract ModVault is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable asset;
    IStrategy public strategy;
    uint16 public performanceFeeBps;
    address public feeRecipient;

    event Deposit(address indexed caller, address indexed receiver, uint256 assets, uint256 shares);
    event Withdraw(address indexed caller, address indexed receiver, uint256 assets, uint256 shares);
    event StrategySet(address indexed strategy);
    event Harvested(uint256 gain, uint256 fee);

    constructor(
        address asset_,
        string memory name_,
        string memory symbol_,
        uint16 performanceFeeBps_,
        address owner_
    ) ERC20Base(name_, symbol_, IERC20(asset_).decimals()) Owned(owner_) {
        require(asset_ != address(0), "NO_ASSET");
        require(performanceFeeBps_ <= 5_000, "FEE_TOO_HIGH");
        asset = IERC20(asset_);
        performanceFeeBps = performanceFeeBps_;
        feeRecipient = owner_ == address(0) ? msg.sender : owner_;
    }

    /// Assets held here plus anything parked in the strategy.
    function totalAssets() public view returns (uint256) {
        uint256 idle = asset.balanceOf(address(this));
        if (address(strategy) == address(0)) return idle;
        return idle + strategy.totalAssets();
    }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        uint256 total = totalAssets();
        if (supply == 0 || total == 0) return assets;
        return (assets * supply) / total;
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply;
        if (supply == 0) return shares;
        return (shares * totalAssets()) / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        require(assets > 0, "ZERO_DEPOSIT");
        shares = convertToShares(assets);
        require(shares > 0, "ZERO_SHARES");
        asset.pull(msg.sender, assets);
        _mint(receiver, shares);
        emit Deposit(msg.sender, receiver, assets, shares);
    }

    function withdraw(uint256 shares, address receiver) external returns (uint256 assets) {
        require(shares > 0, "ZERO_SHARES");
        assets = convertToAssets(shares);
        _burn(msg.sender, shares);

        uint256 idle = asset.balanceOf(address(this));
        if (idle < assets && address(strategy) != address(0)) {
            strategy.withdraw(assets - idle);
        }
        asset.push(receiver, assets);
        emit Withdraw(msg.sender, receiver, assets, shares);
    }

    // ── wiring ────────────────────────────────────────────────────────────
    // Called once by the deployment plan when a `strategy` port is connected.

    function setStrategy(address strategy_) external onlyOwner {
        if (strategy_ != address(0)) {
            require(IStrategy(strategy_).asset() == address(asset), "ASSET_MISMATCH");
        }
        strategy = IStrategy(strategy_);
        emit StrategySet(strategy_);
    }

    function setFeeRecipient(address recipient) external onlyOwner {
        feeRecipient = recipient;
    }

    /// Push idle capital into the strategy.
    function allocate(uint256 amount) external onlyOwner {
        require(address(strategy) != address(0), "NO_STRATEGY");
        asset.approve(address(strategy), amount);
        strategy.deposit(amount);
    }

    /// Skim the performance fee off realised gains as freshly-minted shares.
    function harvest() external returns (uint256 fee) {
        uint256 gain = totalAssets();
        if (gain == 0 || performanceFeeBps == 0 || totalSupply == 0) return 0;
        uint256 owed = convertToAssets(totalSupply);
        if (gain <= owed) return 0;
        fee = ((gain - owed) * performanceFeeBps) / 10_000;
        if (fee > 0) _mint(feeRecipient, convertToShares(fee));
        emit Harvested(gain - owed, fee);
    }
}
