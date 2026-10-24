// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Collateralised debt position — the MakerDAO shape. Lock collateral, mint a
/// stablecoin against it, pay a stability fee, get liquidated if the ratio
/// slips.
///
/// The contract IS the stablecoin (it inherits ERC20Base) rather than pointing
/// at one. That is deliberate: a CDP that could only mint a token it does not
/// control would need a two-way ownership dance the canvas cannot express, and
/// making the debt unit an `erc20` output means a PSM, an AMM or a savings rate
/// block downstream is one wire away.
contract ModCDP is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable collateral;
    IOracle public oracle;

    uint16 public minCollateralRatioBps; // e.g. 15000 = 150%
    uint16 public stabilityFeeBps;       // per year, on outstanding debt
    uint16 public liquidationPenaltyBps;
    uint256 public debtCeiling;

    /// Where accrued stability fees are minted when someone calls drawSurplus.
    address public surplusSink;
    uint256 public surplus;

    struct Position {
        uint256 collateral;
        uint256 principal;
        uint256 lastAccrual;
    }

    mapping(address => Position) public positions;
    uint256 public totalPrincipal;

    event Locked(address indexed user, uint256 amount);
    event Freed(address indexed user, uint256 amount);
    event Minted(address indexed user, uint256 amount);
    event Burned(address indexed user, uint256 amount);
    event Liquidated(address indexed user, address indexed by, uint256 repaid, uint256 seized);

    constructor(
        address collateral_,
        address oracle_,
        string memory name_,
        string memory symbol_,
        uint16 minCollateralRatioBps_,
        uint16 stabilityFeeBps_,
        uint256 debtCeiling_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(collateral_ != address(0), "NO_COLLATERAL");
        require(oracle_ != address(0), "NO_ORACLE");
        require(minCollateralRatioBps_ >= 10_100, "RATIO_TOO_LOW");
        collateral = IERC20(collateral_);
        oracle = IOracle(oracle_);
        minCollateralRatioBps = minCollateralRatioBps_;
        stabilityFeeBps = stabilityFeeBps_;
        liquidationPenaltyBps = 1_300;
        debtCeiling = debtCeiling_;
    }

    // ── reads ─────────────────────────────────────────────────────────────

    /// Debt including the stability fee that has accrued since the last touch.
    function debtOf(address user) public view returns (uint256) {
        Position memory p = positions[user];
        if (p.principal == 0) return 0;
        uint256 elapsed = block.timestamp - p.lastAccrual;
        return p.principal + (p.principal * stabilityFeeBps * elapsed) / (10_000 * 365 days);
    }

    /// Collateral value in stablecoin units, using the 1e18-scaled feed price.
    function collateralValue(address user) public view returns (uint256) {
        return (positions[user].collateral * oracle.price()) / 1e18;
    }

    /// Collateralisation in bps. type(uint256).max when there is no debt.
    function ratioBps(address user) public view returns (uint256) {
        uint256 debt = debtOf(user);
        if (debt == 0) return type(uint256).max;
        return (collateralValue(user) * 10_000) / debt;
    }

    function maxMint(address user) public view returns (uint256) {
        uint256 ceiling = (collateralValue(user) * 10_000) / minCollateralRatioBps;
        uint256 debt = debtOf(user);
        if (ceiling <= debt) return 0;
        uint256 room = ceiling - debt;
        uint256 global = debtCeiling > totalSupply ? debtCeiling - totalSupply : 0;
        return room < global ? room : global;
    }

    // ── position management ───────────────────────────────────────────────

    function _capitalise(address user) internal {
        Position storage p = positions[user];
        uint256 grown = debtOf(user);
        if (grown > p.principal) {
            uint256 fee = grown - p.principal;
            surplus += fee;
            totalPrincipal += fee;
        }
        p.principal = grown;
        p.lastAccrual = block.timestamp;
    }

    function lock(uint256 amount) external {
        require(amount > 0, "ZERO");
        _capitalise(msg.sender);
        collateral.pull(msg.sender, amount);
        positions[msg.sender].collateral += amount;
        emit Locked(msg.sender, amount);
    }

    function free(uint256 amount) external {
        _capitalise(msg.sender);
        Position storage p = positions[msg.sender];
        require(p.collateral >= amount, "INSUFFICIENT");
        p.collateral -= amount;
        require(ratioBps(msg.sender) >= minCollateralRatioBps, "UNSAFE");
        collateral.push(msg.sender, amount);
        emit Freed(msg.sender, amount);
    }

    function mint(uint256 amount) external {
        _capitalise(msg.sender);
        require(amount <= maxMint(msg.sender), "UNSAFE_OR_CAPPED");
        positions[msg.sender].principal += amount;
        totalPrincipal += amount;
        _mint(msg.sender, amount);
        emit Minted(msg.sender, amount);
    }

    /// Repay debt by burning stablecoin. Overpayment is clamped to the debt.
    function burn(uint256 amount) external {
        _capitalise(msg.sender);
        Position storage p = positions[msg.sender];
        uint256 pay = amount > p.principal ? p.principal : amount;
        _burn(msg.sender, pay);
        p.principal -= pay;
        totalPrincipal -= pay;
        emit Burned(msg.sender, pay);
    }

    /// Anyone can close an unsafe position: burn its debt, seize collateral at
    /// a discount. The penalty is what pays the keeper for showing up.
    function liquidate(address user, uint256 repayAmount) external {
        _capitalise(user);
        require(ratioBps(user) < minCollateralRatioBps, "SAFE");
        Position storage p = positions[user];
        uint256 pay = repayAmount > p.principal ? p.principal : repayAmount;
        require(pay > 0, "NO_DEBT");
        _burn(msg.sender, pay);
        p.principal -= pay;
        totalPrincipal -= pay;

        uint256 seize = (pay * 1e18 * (10_000 + liquidationPenaltyBps)) / (oracle.price() * 10_000);
        if (seize > p.collateral) seize = p.collateral;
        p.collateral -= seize;
        collateral.push(msg.sender, seize);
        emit Liquidated(user, msg.sender, pay, seize);
    }

    // ── revenue ───────────────────────────────────────────────────────────

    /// Mint the fees this system has earned to the revenue sink. Nothing else
    /// can mint: supply is either backed by collateral or by realised fees.
    function drawSurplus() external returns (uint256 amount) {
        amount = surplus;
        require(amount > 0, "NO_SURPLUS");
        address to = surplusSink == address(0) ? owner : surplusSink;
        surplus = 0;
        _mint(to, amount);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setSurplusSink(address sink) external onlyOwner {
        surplusSink = sink;
    }

    function setOracle(address oracle_) external onlyOwner {
        require(oracle_ != address(0), "NO_ORACLE");
        oracle = IOracle(oracle_);
    }

    function setRiskParams(
        uint16 minCollateralRatioBps_,
        uint16 stabilityFeeBps_,
        uint16 liquidationPenaltyBps_,
        uint256 debtCeiling_
    ) external onlyOwner {
        require(minCollateralRatioBps_ >= 10_100, "RATIO_TOO_LOW");
        require(liquidationPenaltyBps_ <= 3_000, "PENALTY");
        minCollateralRatioBps = minCollateralRatioBps_;
        stabilityFeeBps = stabilityFeeBps_;
        liquidationPenaltyBps = liquidationPenaltyBps_;
        debtCeiling = debtCeiling_;
    }
}
