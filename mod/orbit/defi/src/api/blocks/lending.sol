// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Over-collateralised borrowing against a single collateral asset, priced by
/// whatever is on the `oracle` port. Wire the oracle port to a ModFixedOracle
/// for a testnet, or to an AMM's price() for a (naive) on-chain feed.
contract ModLendingPool is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable collateral;
    IERC20 public immutable debtAsset;
    IOracle public oracle;

    uint16 public ltvBps; // max borrow / collateral value
    uint16 public liquidationBonusBps;
    uint16 public borrowRateBps; // per year

    struct Account {
        uint256 collateral;
        uint256 principal;
        uint256 lastAccrual;
    }

    mapping(address => Account) public accounts;
    uint256 public totalSupplied;

    event Supplied(address indexed user, uint256 amount);
    event Borrowed(address indexed user, uint256 amount);
    event Repaid(address indexed user, uint256 amount);
    event Liquidated(address indexed user, address indexed by, uint256 repaid, uint256 seized);

    constructor(
        address collateral_,
        address debtAsset_,
        address oracle_,
        uint16 ltvBps_,
        uint16 borrowRateBps_,
        address owner_
    ) Owned(owner_) {
        require(collateral_ != address(0) && debtAsset_ != address(0), "NO_TOKEN");
        require(ltvBps_ > 0 && ltvBps_ <= 9_000, "BAD_LTV");
        collateral = IERC20(collateral_);
        debtAsset = IERC20(debtAsset_);
        oracle = IOracle(oracle_);
        ltvBps = ltvBps_;
        borrowRateBps = borrowRateBps_;
        liquidationBonusBps = 500;
    }

    function debtOf(address user) public view returns (uint256) {
        Account memory a = accounts[user];
        if (a.principal == 0) return 0;
        uint256 elapsed = block.timestamp - a.lastAccrual;
        return a.principal + (a.principal * borrowRateBps * elapsed) / (10_000 * 365 days);
    }

    /// Collateral value denominated in the debt asset, 1e18 price scaling.
    function collateralValue(address user) public view returns (uint256) {
        return (accounts[user].collateral * oracle.price()) / 1e18;
    }

    function maxBorrow(address user) public view returns (uint256) {
        uint256 limit = (collateralValue(user) * ltvBps) / 10_000;
        uint256 debt = debtOf(user);
        return limit > debt ? limit - debt : 0;
    }

    function healthFactorBps(address user) public view returns (uint256) {
        uint256 debt = debtOf(user);
        if (debt == 0) return type(uint256).max;
        return ((collateralValue(user) * ltvBps) / 10_000) * 10_000 / debt;
    }

    function _capitalise(address user) internal {
        Account storage a = accounts[user];
        a.principal = debtOf(user);
        a.lastAccrual = block.timestamp;
    }

    /// Lenders fund the borrowable pool.
    function supply(uint256 amount) external {
        debtAsset.pull(msg.sender, amount);
        totalSupplied += amount;
        emit Supplied(msg.sender, amount);
    }

    function depositCollateral(uint256 amount) external {
        _capitalise(msg.sender);
        collateral.pull(msg.sender, amount);
        accounts[msg.sender].collateral += amount;
    }

    function withdrawCollateral(uint256 amount) external {
        _capitalise(msg.sender);
        Account storage a = accounts[msg.sender];
        require(a.collateral >= amount, "INSUFFICIENT");
        a.collateral -= amount;
        require(debtOf(msg.sender) <= (collateralValue(msg.sender) * ltvBps) / 10_000, "UNHEALTHY");
        collateral.push(msg.sender, amount);
    }

    function borrow(uint256 amount) external {
        _capitalise(msg.sender);
        require(amount <= maxBorrow(msg.sender), "OVER_LTV");
        accounts[msg.sender].principal += amount;
        debtAsset.push(msg.sender, amount);
        emit Borrowed(msg.sender, amount);
    }

    function repay(uint256 amount) external {
        _capitalise(msg.sender);
        Account storage a = accounts[msg.sender];
        uint256 pay = amount > a.principal ? a.principal : amount;
        debtAsset.pull(msg.sender, pay);
        a.principal -= pay;
        emit Repaid(msg.sender, pay);
    }

    function liquidate(address user, uint256 repayAmount) external {
        _capitalise(user);
        require(healthFactorBps(user) < 10_000, "HEALTHY");
        Account storage a = accounts[user];
        uint256 pay = repayAmount > a.principal ? a.principal : repayAmount;
        debtAsset.pull(msg.sender, pay);
        a.principal -= pay;

        uint256 seize = (pay * 1e18 * (10_000 + liquidationBonusBps)) / (oracle.price() * 10_000);
        if (seize > a.collateral) seize = a.collateral;
        a.collateral -= seize;
        collateral.push(msg.sender, seize);
        emit Liquidated(user, msg.sender, pay, seize);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setOracle(address oracle_) external onlyOwner {
        require(oracle_ != address(0), "NO_ORACLE");
        oracle = IOracle(oracle_);
    }

    function setRiskParams(uint16 ltvBps_, uint16 liquidationBonusBps_, uint16 borrowRateBps_) external onlyOwner {
        require(ltvBps_ > 0 && ltvBps_ <= 9_000, "BAD_LTV");
        require(liquidationBonusBps_ <= 3_000, "BAD_BONUS");
        ltvBps = ltvBps_;
        liquidationBonusBps = liquidationBonusBps_;
        borrowRateBps = borrowRateBps_;
    }
}
