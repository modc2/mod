// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Peg stability module: swap a reserve asset for the protocol stablecoin at
/// par, minus a fee, in both directions. This is the block that turns "a
/// stablecoin exists" into "a stablecoin holds its peg" — arbitrage against a
/// par window is what an AMM alone cannot give you.
///
/// It swaps out of inventory rather than minting, so it is safe to point at a
/// stable it does not own: fill it with whichever side you want to defend.
contract ModPSM is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable gem;    // the reserve asset (often 6-decimal)
    IERC20 public immutable stable; // the protocol unit

    uint256 public immutable gemUnit;
    uint256 public immutable stableUnit;

    uint16 public tinBps;  // fee paying gem in
    uint16 public toutBps; // fee taking gem out
    uint256 public gemCap; // max gem this module will absorb

    uint256 public feesGem;
    uint256 public feesStable;
    address public feeSink;

    event SoldGem(address indexed user, uint256 gemIn, uint256 stableOut);
    event BoughtGem(address indexed user, uint256 stableIn, uint256 gemOut);

    constructor(
        address gem_,
        address stable_,
        uint16 tinBps_,
        uint16 toutBps_,
        uint256 gemCap_,
        address owner_
    ) Owned(owner_) {
        require(gem_ != address(0) && stable_ != address(0), "NO_TOKEN");
        require(gem_ != stable_, "SAME_TOKEN");
        require(tinBps_ <= 1_000 && toutBps_ <= 1_000, "FEE_TOO_HIGH");
        gem = IERC20(gem_);
        stable = IERC20(stable_);
        // Decimals differ across the pair far more often than not — USDC is 6,
        // every protocol unit here is 18 — so par means "same value", not
        // "same integer".
        gemUnit = 10 ** uint256(IERC20(gem_).decimals());
        stableUnit = 10 ** uint256(IERC20(stable_).decimals());
        tinBps = tinBps_;
        toutBps = toutBps_;
        gemCap = gemCap_;
    }

    function stableOutFor(uint256 gemIn) public view returns (uint256) {
        uint256 par = (gemIn * stableUnit) / gemUnit;
        return par - (par * tinBps) / 10_000;
    }

    function gemOutFor(uint256 stableIn) public view returns (uint256) {
        uint256 par = (stableIn * gemUnit) / stableUnit;
        return par - (par * toutBps) / 10_000;
    }

    /// Reserve → stable. Defends the ceiling: nobody pays above par for a unit
    /// they can mint here for par.
    function sellGem(uint256 gemIn) external returns (uint256 stableOut) {
        require(gemIn > 0, "ZERO");
        require(gemCap == 0 || gem.balanceOf(address(this)) + gemIn <= gemCap, "GEM_CAP");
        stableOut = stableOutFor(gemIn);
        require(stable.balanceOf(address(this)) >= stableOut + feesStable, "NO_STABLE");
        gem.pull(msg.sender, gemIn);
        feesStable += ((gemIn * stableUnit) / gemUnit) - stableOut;
        stable.push(msg.sender, stableOut);
        emit SoldGem(msg.sender, gemIn, stableOut);
    }

    /// Stable → reserve. Defends the floor.
    function buyGem(uint256 stableIn) external returns (uint256 gemOut) {
        require(stableIn > 0, "ZERO");
        gemOut = gemOutFor(stableIn);
        require(gem.balanceOf(address(this)) >= gemOut + feesGem, "NO_GEM");
        stable.pull(msg.sender, stableIn);
        feesGem += ((stableIn * gemUnit) / stableUnit) - gemOut;
        gem.push(msg.sender, gemOut);
        emit BoughtGem(msg.sender, stableIn, gemOut);
    }

    /// Inventory in. Either side; whoever wants the module to work funds it.
    function fill(address token, uint256 amount) external {
        require(token == address(gem) || token == address(stable), "UNKNOWN_TOKEN");
        IERC20(token).pull(msg.sender, amount);
    }

    function collectFees() external returns (uint256 gemFees, uint256 stableFees) {
        address to = feeSink == address(0) ? owner : feeSink;
        gemFees = feesGem;
        stableFees = feesStable;
        feesGem = 0;
        feesStable = 0;
        if (gemFees > 0) gem.push(to, gemFees);
        if (stableFees > 0) stable.push(to, stableFees);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setFeeSink(address sink) external onlyOwner {
        feeSink = sink;
    }

    function setFees(uint16 tinBps_, uint16 toutBps_, uint256 gemCap_) external onlyOwner {
        require(tinBps_ <= 1_000 && toutBps_ <= 1_000, "FEE_TOO_HIGH");
        tinBps = tinBps_;
        toutBps = toutBps_;
        gemCap = gemCap_;
    }

    /// Owner pulls inventory back out — the reserve is not locked in here.
    function withdraw(address token, uint256 amount, address to) external onlyOwner {
        IERC20(token).push(to, amount);
    }
}
