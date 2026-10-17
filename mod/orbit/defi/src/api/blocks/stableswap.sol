// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// StableSwap — the Curve invariant, two coins. Between a constant sum (no
/// slippage) and a constant product (never runs out), leaning on the first
/// while the pool is balanced and falling back to the second as it skews. The
/// amplification coefficient A is how hard it leans.
///
/// This is the right market block for pairs that should trade at par — a
/// stablecoin against its reserve, an LST against the asset it wraps — where a
/// constant-product pair would charge 30bps of curvature for nothing.
contract ModStableSwap is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    uint256 internal constant N = 2;

    IERC20 public immutable coin0;
    IERC20 public immutable coin1;
    /// Multipliers that lift each coin to 18 decimals, so the invariant is
    /// solved in one unit even when one side is a 6-decimal reserve.
    uint256 public immutable rate0;
    uint256 public immutable rate1;

    uint256 public amp;      // A, unscaled
    uint16 public feeBps;    // charged on the imbalanced part of a trade
    uint16 public adminFeeBps; // share of the fee kept for the protocol
    address public feeSink;

    uint256 public balance0;
    uint256 public balance1;
    uint256 public adminBalance0;
    uint256 public adminBalance1;

    event AddLiquidity(address indexed to, uint256 amount0, uint256 amount1, uint256 shares);
    event RemoveLiquidity(address indexed to, uint256 amount0, uint256 amount1, uint256 shares);
    event Exchange(address indexed to, bool zeroForOne, uint256 amountIn, uint256 amountOut);

    constructor(
        address coin0_,
        address coin1_,
        uint256 amp_,
        uint16 feeBps_,
        string memory name_,
        string memory symbol_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(coin0_ != address(0) && coin1_ != address(0), "NO_TOKEN");
        require(coin0_ != coin1_, "SAME_TOKEN");
        require(amp_ >= 1 && amp_ <= 10_000, "BAD_AMP");
        require(feeBps_ <= 100, "FEE_TOO_HIGH");
        coin0 = IERC20(coin0_);
        coin1 = IERC20(coin1_);
        rate0 = 10 ** (18 - uint256(IERC20(coin0_).decimals()));
        rate1 = 10 ** (18 - uint256(IERC20(coin1_).decimals()));
        amp = amp_;
        feeBps = feeBps_;
        adminFeeBps = 5_000;
    }

    // ── the invariant ─────────────────────────────────────────────────────

    function _xp() internal view returns (uint256 x0, uint256 x1) {
        x0 = balance0 * rate0;
        x1 = balance1 * rate1;
    }

    /// D such that the pool sits on the StableSwap curve. Newton, 255 rounds
    /// max, same as the reference implementation.
    function getD(uint256 x0, uint256 x1) public view returns (uint256) {
        uint256 s = x0 + x1;
        if (s == 0) return 0;
        uint256 d = s;
        uint256 ann = amp * N;
        for (uint256 i = 0; i < 255; i++) {
            uint256 dp = (((d * d) / (x0 * N)) * d) / (x1 * N);
            uint256 prev = d;
            d = ((ann * s + dp * N) * d) / ((ann - 1) * d + (N + 1) * dp);
            if (d > prev ? d - prev <= 1 : prev - d <= 1) return d;
        }
        return d;
    }

    /// Given one side's new normalised balance, the other side that keeps D.
    function getY(uint256 xIn, uint256 d) public view returns (uint256) {
        uint256 ann = amp * N;
        uint256 c = (((d * d) / (xIn * N)) * d) / (ann * N);
        uint256 b = xIn + d / ann;
        uint256 y = d;
        for (uint256 i = 0; i < 255; i++) {
            uint256 prev = y;
            y = (y * y + c) / (2 * y + b - d);
            if (y > prev ? y - prev <= 1 : prev - y <= 1) return y;
        }
        return y;
    }

    /// Assets backing one LP share, 1e18 scaled. The number a stable pool is
    /// actually judged on — it only ever goes up, from fees.
    function virtualPrice() external view returns (uint256) {
        if (totalSupply == 0) return 1e18;
        (uint256 x0, uint256 x1) = _xp();
        return (getD(x0, x1) * 1e18) / totalSupply;
    }

    function getDy(bool zeroForOne, uint256 amountIn) public view returns (uint256 dy, uint256 fee) {
        if (amountIn == 0 || balance0 == 0 || balance1 == 0) return (0, 0);
        (uint256 x0, uint256 x1) = _xp();
        uint256 d = getD(x0, x1);
        uint256 xIn = zeroForOne ? x0 + amountIn * rate0 : x1 + amountIn * rate1;
        uint256 y = getY(xIn, d);
        uint256 out = (zeroForOne ? x1 : x0) - y - 1; // round against the trader
        fee = (out * feeBps) / 10_000;
        dy = (out - fee) / (zeroForOne ? rate1 : rate0);
        fee = fee / (zeroForOne ? rate1 : rate0);
    }

    /// Spot price of coin0 in coin1, 1e18 scaled — an `oracle`-shaped read.
    function price() external view returns (uint256) {
        (uint256 dy, ) = getDy(true, 10 ** uint256(coin0.decimals()));
        return (dy * rate1);
    }

    // ── liquidity ─────────────────────────────────────────────────────────

    function addLiquidity(uint256 amount0, uint256 amount1, uint256 minShares)
        external
        returns (uint256 shares)
    {
        require(amount0 > 0 || amount1 > 0, "ZERO");
        (uint256 x0, uint256 x1) = _xp();
        uint256 d0 = totalSupply == 0 ? 0 : getD(x0, x1);

        if (amount0 > 0) coin0.pull(msg.sender, amount0);
        if (amount1 > 0) coin1.pull(msg.sender, amount1);
        balance0 += amount0;
        balance1 += amount1;

        (uint256 n0, uint256 n1) = _xp();
        uint256 d1 = getD(n0, n1);
        require(d1 > d0, "NO_GROWTH");
        shares = totalSupply == 0 ? d1 : (totalSupply * (d1 - d0)) / d0;
        require(shares >= minShares && shares > 0, "SLIPPAGE");
        _mint(msg.sender, shares);
        emit AddLiquidity(msg.sender, amount0, amount1, shares);
    }

    /// Balanced exit — no invariant solve needed, so it cannot be gamed.
    function removeLiquidity(uint256 shares) external returns (uint256 amount0, uint256 amount1) {
        require(shares > 0 && totalSupply > 0, "ZERO");
        amount0 = (balance0 * shares) / totalSupply;
        amount1 = (balance1 * shares) / totalSupply;
        _burn(msg.sender, shares);
        balance0 -= amount0;
        balance1 -= amount1;
        if (amount0 > 0) coin0.push(msg.sender, amount0);
        if (amount1 > 0) coin1.push(msg.sender, amount1);
        emit RemoveLiquidity(msg.sender, amount0, amount1, shares);
    }

    function exchange(bool zeroForOne, uint256 amountIn, uint256 minOut)
        external
        returns (uint256 amountOut)
    {
        uint256 fee;
        (amountOut, fee) = getDy(zeroForOne, amountIn);
        require(amountOut >= minOut && amountOut > 0, "SLIPPAGE");
        uint256 adminCut = (fee * adminFeeBps) / 10_000;
        if (zeroForOne) {
            coin0.pull(msg.sender, amountIn);
            balance0 += amountIn;
            balance1 -= amountOut + adminCut;
            adminBalance1 += adminCut;
            coin1.push(msg.sender, amountOut);
        } else {
            coin1.pull(msg.sender, amountIn);
            balance1 += amountIn;
            balance0 -= amountOut + adminCut;
            adminBalance0 += adminCut;
            coin0.push(msg.sender, amountOut);
        }
        emit Exchange(msg.sender, zeroForOne, amountIn, amountOut);
    }

    // ── revenue and wiring ────────────────────────────────────────────────

    function collectAdminFees() external returns (uint256 a0, uint256 a1) {
        address to = feeSink == address(0) ? owner : feeSink;
        a0 = adminBalance0;
        a1 = adminBalance1;
        adminBalance0 = 0;
        adminBalance1 = 0;
        if (a0 > 0) coin0.push(to, a0);
        if (a1 > 0) coin1.push(to, a1);
    }

    function setFeeSink(address sink) external onlyOwner {
        feeSink = sink;
    }

    function setParams(uint256 amp_, uint16 feeBps_, uint16 adminFeeBps_) external onlyOwner {
        require(amp_ >= 1 && amp_ <= 10_000, "BAD_AMP");
        require(feeBps_ <= 100 && adminFeeBps_ <= 10_000, "BAD_FEE");
        amp = amp_;
        feeBps = feeBps_;
        adminFeeBps = adminFeeBps_;
    }
}
