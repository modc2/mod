// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Bond depository — the OlympusDAO shape, and the cleanest answer to "how
/// does a protocol buy its own liquidity". Sell the protocol token at a
/// discount to its market price, take the payment as reserves, and vest the
/// buyer's payout over a term so the discount is not an instant arbitrage.
///
/// Price comes off the wired `oracle` — point it at the AMM pair the token
/// trades on and the bond re-prices itself every block. The discount widens
/// with debt (how much is already vesting), which is the control loop that
/// stops a bond from selling the whole treasury into one dip.
contract ModBondDepository is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable payoutToken; // what the buyer receives, vested
    IERC20 public immutable quoteToken;  // what the protocol receives, now
    IOracle public oracle;               // payout price in quote units, 1e18

    address public treasury;
    uint16 public discountBps;   // at zero debt
    uint16 public maxDebtBps;    // debt ceiling as bps of payout supply
    uint256 public vestingTerm;  // seconds
    uint256 public maxBondSize;  // payout tokens per bond

    uint256 public totalDebt;    // payout tokens owed but not yet vested
    uint256 public totalSold;

    struct Bond {
        uint256 payout;
        uint256 claimed;
        uint256 start;
        uint256 end;
    }

    mapping(address => Bond) public bonds;

    event Bonded(address indexed user, uint256 quoteIn, uint256 payout, uint256 price);
    event Redeemed(address indexed user, uint256 amount, uint256 remaining);

    constructor(
        address payoutToken_,
        address quoteToken_,
        address oracle_,
        uint16 discountBps_,
        uint256 vestingTerm_,
        address owner_
    ) Owned(owner_) {
        require(payoutToken_ != address(0) && quoteToken_ != address(0), "NO_TOKEN");
        require(oracle_ != address(0), "NO_ORACLE");
        require(discountBps_ <= 5_000, "DISCOUNT_TOO_DEEP");
        payoutToken = IERC20(payoutToken_);
        quoteToken = IERC20(quoteToken_);
        oracle = IOracle(oracle_);
        discountBps = discountBps_;
        vestingTerm = vestingTerm_;
        maxDebtBps = 2_000;
        maxBondSize = type(uint256).max;
    }

    /// Debt ratio in bps: how much of the payout supply is already vesting.
    function debtRatioBps() public view returns (uint256) {
        uint256 supply = payoutToken.totalSupply();
        if (supply == 0) return 0;
        return (totalDebt * 10_000) / supply;
    }

    /// Quote units per payout token, 1e18 scaled. Market price less the
    /// discount, and the discount shrinks as debt approaches its ceiling.
    function bondPrice() public view returns (uint256) {
        uint256 market = oracle.price();
        uint256 ratio = debtRatioBps();
        uint256 effective = maxDebtBps == 0 || ratio >= maxDebtBps
            ? 0
            : (discountBps * (maxDebtBps - ratio)) / maxDebtBps;
        return market - (market * effective) / 10_000;
    }

    function payoutFor(uint256 quoteAmount) public view returns (uint256) {
        uint256 price = bondPrice();
        if (price == 0) return 0;
        return (quoteAmount * 1e18) / price;
    }

    /// Buy a bond. `maxPrice` is the caller's slippage guard — the oracle can
    /// move between simulation and inclusion.
    function deposit(uint256 quoteAmount, uint256 maxPrice) external returns (uint256 payout) {
        require(quoteAmount > 0, "ZERO");
        uint256 price = bondPrice();
        require(price <= maxPrice, "PRICE_MOVED");
        payout = payoutFor(quoteAmount);
        require(payout > 0 && payout <= maxBondSize, "BOND_TOO_LARGE");
        require(bonds[msg.sender].payout == bonds[msg.sender].claimed, "BOND_OPEN");
        require(
            payoutToken.balanceOf(address(this)) >= totalDebt + payout,
            "INSUFFICIENT_PAYOUT_RESERVE"
        );
        require(debtRatioBps() < maxDebtBps || maxDebtBps == 0, "DEBT_CEILING");

        quoteToken.pull(msg.sender, quoteAmount);
        // Reserves go straight to the treasury; the depository only ever holds
        // the payout token it still owes.
        address to = treasury == address(0) ? owner : treasury;
        quoteToken.push(to, quoteAmount);

        bonds[msg.sender] = Bond({
            payout: payout,
            claimed: 0,
            start: block.timestamp,
            end: block.timestamp + vestingTerm
        });
        totalDebt += payout;
        totalSold += payout;
        emit Bonded(msg.sender, quoteAmount, payout, price);
    }

    function vested(address user) public view returns (uint256) {
        Bond memory b = bonds[user];
        if (b.payout == 0) return 0;
        if (block.timestamp >= b.end) return b.payout - b.claimed;
        uint256 elapsed = block.timestamp - b.start;
        uint256 total = (b.payout * elapsed) / (b.end - b.start);
        return total > b.claimed ? total - b.claimed : 0;
    }

    function redeem() external returns (uint256 amount) {
        amount = vested(msg.sender);
        require(amount > 0, "NOTHING_VESTED");
        Bond storage b = bonds[msg.sender];
        b.claimed += amount;
        totalDebt -= amount;
        payoutToken.push(msg.sender, amount);
        emit Redeemed(msg.sender, amount, b.payout - b.claimed);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    /// Stock the depository with payout tokens it can sell.
    function fund(uint256 amount) external {
        payoutToken.pull(msg.sender, amount);
    }

    function setTreasury(address treasury_) external onlyOwner {
        treasury = treasury_;
    }

    function setOracle(address oracle_) external onlyOwner {
        require(oracle_ != address(0), "NO_ORACLE");
        oracle = IOracle(oracle_);
    }

    function setTerms(
        uint16 discountBps_,
        uint256 vestingTerm_,
        uint16 maxDebtBps_,
        uint256 maxBondSize_
    ) external onlyOwner {
        require(discountBps_ <= 5_000, "DISCOUNT_TOO_DEEP");
        discountBps = discountBps_;
        vestingTerm = vestingTerm_;
        maxDebtBps = maxDebtBps_;
        maxBondSize = maxBondSize_;
    }
}
