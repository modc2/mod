// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Perpetual futures against an oracle price, with a pool on the other side —
/// the GMX shape. Traders post margin in the settlement asset and take a
/// levered position; liquidity providers deposit the same asset, mint an LP
/// share, and are the counterparty to every open position.
///
/// The pool's edge is fees plus funding; its risk is that traders are right.
/// Funding is skew-based: whichever side is crowded pays the other, which is
/// what keeps the mark honest without an order book.
contract ModPerpMarket is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable settlement;
    IOracle public oracle;

    uint16 public openFeeBps;
    uint16 public maintenanceMarginBps; // liquidate below this, on size
    uint16 public maxLeverageBps;
    uint16 public fundingRateBps;       // per day at 100% skew
    uint16 public liquidationFeeBps;
    /// Open interest per side is capped at this share of pool liquidity.
    uint16 public utilisationCapBps;

    uint256 public poolAssets;      // LP-owned assets, net of trader margin
    uint256 public marginDeposited; // trader collateral held here

    uint256 public longOI;
    uint256 public shortOI;
    /// Cumulative funding paid per unit of size, 1e18 scaled, per side.
    uint256 public cumFundingLong;
    uint256 public cumFundingShort;
    uint256 public lastFunding;

    struct Position {
        uint256 size;       // notional in settlement units
        uint256 margin;
        uint256 entryPrice; // 1e18
        uint256 fundingIndex;
        bool isLong;
        bool open;
    }

    mapping(address => Position) public positions;

    event LiquidityAdded(address indexed to, uint256 assets, uint256 shares);
    event LiquidityRemoved(address indexed to, uint256 assets, uint256 shares);
    event Opened(address indexed user, bool isLong, uint256 size, uint256 margin, uint256 price);
    event Closed(address indexed user, int256 pnl, uint256 payout, uint256 price);
    event Liquidated(address indexed user, address indexed by, uint256 size, uint256 price);

    constructor(
        address settlement_,
        address oracle_,
        string memory name_,
        string memory symbol_,
        uint16 maxLeverageBps_,
        uint16 openFeeBps_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(settlement_ != address(0) && oracle_ != address(0), "NO_INPUT");
        require(maxLeverageBps_ >= 10_000 && maxLeverageBps_ <= 500_000, "BAD_LEVERAGE");
        require(openFeeBps_ <= 500, "FEE_TOO_HIGH");
        settlement = IERC20(settlement_);
        oracle = IOracle(oracle_);
        maxLeverageBps = maxLeverageBps_;
        openFeeBps = openFeeBps_;
        maintenanceMarginBps = 500;
        fundingRateBps = 100;
        liquidationFeeBps = 1_000;
        utilisationCapBps = 8_000;
        lastFunding = block.timestamp;
    }

    // ── funding ───────────────────────────────────────────────────────────

    /// Skew-weighted funding, accrued into a per-side index. Only the crowded
    /// side pays, in proportion to how crowded it is. It is paid to the pool
    /// rather than to the other side of the book, because here the pool *is*
    /// the other side of every position.
    function pokeFunding() public {
        uint256 elapsed = block.timestamp - lastFunding;
        if (elapsed == 0) return;
        lastFunding = block.timestamp;
        uint256 total = longOI + shortOI;
        if (total == 0 || fundingRateBps == 0) return;
        uint256 skew = longOI > shortOI ? longOI - shortOI : shortOI - longOI;
        if (skew == 0) return;
        // Rate per unit of size over the elapsed window, 1e18 scaled.
        uint256 rate = (skew * 1e18 * fundingRateBps * elapsed) / (total * 10_000 * 1 days);
        if (longOI > shortOI) cumFundingLong += rate;
        else cumFundingShort += rate;
    }

    function fundingOwed(address user) public view returns (uint256) {
        Position memory p = positions[user];
        if (!p.open) return 0;
        uint256 index = p.isLong ? cumFundingLong : cumFundingShort;
        if (index <= p.fundingIndex) return 0;
        return (p.size * (index - p.fundingIndex)) / 1e18;
    }

    // ── liquidity ─────────────────────────────────────────────────────────

    function sharePrice() public view returns (uint256) {
        if (totalSupply == 0 || poolAssets == 0) return 1e18;
        return (poolAssets * 1e18) / totalSupply;
    }

    function addLiquidity(uint256 assets) external returns (uint256 shares) {
        require(assets > 0, "ZERO");
        shares = totalSupply == 0 || poolAssets == 0 ? assets : (assets * totalSupply) / poolAssets;
        settlement.pull(msg.sender, assets);
        poolAssets += assets;
        _mint(msg.sender, shares);
        emit LiquidityAdded(msg.sender, assets, shares);
    }

    function removeLiquidity(uint256 shares) external returns (uint256 assets) {
        require(shares > 0, "ZERO");
        assets = (shares * poolAssets) / totalSupply;
        // Liquidity backing open positions cannot leave.
        require(assets <= _freeLiquidity(), "UTILISED");
        _burn(msg.sender, shares);
        poolAssets -= assets;
        settlement.push(msg.sender, assets);
        emit LiquidityRemoved(msg.sender, assets, shares);
    }

    function _freeLiquidity() internal view returns (uint256) {
        uint256 exposure = longOI > shortOI ? longOI - shortOI : shortOI - longOI;
        uint256 reserved = (exposure * 3_000) / 10_000; // headroom for a 30% move
        return poolAssets > reserved ? poolAssets - reserved : 0;
    }

    // ── trading ───────────────────────────────────────────────────────────

    function pnlOf(address user) public view returns (int256) {
        Position memory p = positions[user];
        if (!p.open) return 0;
        uint256 price = oracle.price();
        if (p.isLong) {
            return price >= p.entryPrice
                ? int256((p.size * (price - p.entryPrice)) / p.entryPrice)
                : -int256((p.size * (p.entryPrice - price)) / p.entryPrice);
        }
        return p.entryPrice >= price
            ? int256((p.size * (p.entryPrice - price)) / p.entryPrice)
            : -int256((p.size * (price - p.entryPrice)) / p.entryPrice);
    }

    /// Margin plus PnL minus funding. Zero means the position is gone.
    function equityOf(address user) public view returns (uint256) {
        Position memory p = positions[user];
        if (!p.open) return 0;
        int256 net = int256(p.margin) + pnlOf(user) - int256(fundingOwed(user));
        return net > 0 ? uint256(net) : 0;
    }

    function isLiquidatable(address user) public view returns (bool) {
        Position memory p = positions[user];
        if (!p.open) return false;
        return equityOf(user) < (p.size * maintenanceMarginBps) / 10_000;
    }

    function open(bool isLong, uint256 margin, uint16 leverageBps) external {
        require(margin > 0, "ZERO");
        require(leverageBps >= 10_000 && leverageBps <= maxLeverageBps, "LEVERAGE");
        require(!positions[msg.sender].open, "POSITION_OPEN");
        pokeFunding();

        uint256 size = (margin * leverageBps) / 10_000;
        uint256 fee = (size * openFeeBps) / 10_000;
        require(margin > fee, "FEE_EXCEEDS_MARGIN");

        uint256 sideOI = isLong ? longOI : shortOI;
        require(
            sideOI + size <= (poolAssets * utilisationCapBps) / 10_000,
            "OI_CAP"
        );

        settlement.pull(msg.sender, margin);
        uint256 net = margin - fee;
        poolAssets += fee;
        marginDeposited += net;

        positions[msg.sender] = Position({
            size: size,
            margin: net,
            entryPrice: oracle.price(),
            fundingIndex: isLong ? cumFundingLong : cumFundingShort,
            isLong: isLong,
            open: true
        });
        if (isLong) longOI += size;
        else shortOI += size;
        emit Opened(msg.sender, isLong, size, net, positions[msg.sender].entryPrice);
    }

    function close() external returns (uint256 payout) {
        Position memory p = positions[msg.sender];
        require(p.open, "NO_POSITION");
        pokeFunding();
        int256 pnl = pnlOf(msg.sender);
        uint256 funding = fundingOwed(msg.sender);

        int256 net = int256(p.margin) + pnl - int256(funding);
        payout = net > 0 ? uint256(net) : 0;
        // Profit comes out of the pool; loss stays in it.
        if (payout > p.margin) {
            uint256 owed = payout - p.margin;
            if (owed > poolAssets) owed = poolAssets;
            poolAssets -= owed;
            payout = p.margin + owed;
        } else {
            poolAssets += p.margin - payout;
        }
        marginDeposited -= p.margin;

        if (p.isLong) longOI -= p.size;
        else shortOI -= p.size;
        delete positions[msg.sender];

        if (payout > 0) settlement.push(msg.sender, payout);
        emit Closed(msg.sender, pnl, payout, oracle.price());
    }

    /// Below maintenance margin anyone may close the position; the keeper is
    /// paid out of what is left, and the pool takes the rest.
    function liquidate(address user) external {
        Position memory p = positions[user];
        require(p.open, "NO_POSITION");
        pokeFunding();
        require(isLiquidatable(user), "HEALTHY");

        uint256 equity = equityOf(user);
        uint256 keeperFee = (equity * liquidationFeeBps) / 10_000;
        poolAssets += p.margin - keeperFee;
        marginDeposited -= p.margin;

        if (p.isLong) longOI -= p.size;
        else shortOI -= p.size;
        delete positions[user];

        if (keeperFee > 0) settlement.push(msg.sender, keeperFee);
        emit Liquidated(user, msg.sender, p.size, oracle.price());
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setOracle(address oracle_) external onlyOwner {
        require(oracle_ != address(0), "NO_ORACLE");
        oracle = IOracle(oracle_);
    }

    function setRiskParams(
        uint16 maintenanceMarginBps_,
        uint16 fundingRateBps_,
        uint16 liquidationFeeBps_,
        uint16 utilisationCapBps_
    ) external onlyOwner {
        require(maintenanceMarginBps_ >= 100 && maintenanceMarginBps_ <= 5_000, "BAD_MARGIN");
        require(liquidationFeeBps_ <= 5_000 && utilisationCapBps_ <= 10_000, "BAD_PARAM");
        maintenanceMarginBps = maintenanceMarginBps_;
        fundingRateBps = fundingRateBps_;
        liquidationFeeBps = liquidationFeeBps_;
        utilisationCapBps = utilisationCapBps_;
    }
}
