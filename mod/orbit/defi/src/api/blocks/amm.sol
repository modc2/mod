// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Constant-product pair with LP shares. Two `erc20` ports in, one `erc20`
/// (the LP token) out — which is what lets an AMM feed a staking block to make
/// a liquidity-mining protocol out of two blocks and one wire.
contract ModAMM is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable token0;
    IERC20 public immutable token1;
    uint16 public feeBps;

    uint256 public reserve0;
    uint256 public reserve1;

    event LiquidityAdded(address indexed to, uint256 amount0, uint256 amount1, uint256 shares);
    event LiquidityRemoved(address indexed to, uint256 amount0, uint256 amount1, uint256 shares);
    event Swap(address indexed to, bool zeroForOne, uint256 amountIn, uint256 amountOut);

    constructor(
        address token0_,
        address token1_,
        uint16 feeBps_,
        string memory name_,
        string memory symbol_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(token0_ != address(0) && token1_ != address(0), "NO_TOKEN");
        require(token0_ != token1_, "SAME_TOKEN");
        require(feeBps_ <= 1_000, "FEE_TOO_HIGH");
        token0 = IERC20(token0_);
        token1 = IERC20(token1_);
        feeBps = feeBps_;
    }

    function _sqrt(uint256 y) internal pure returns (uint256 z) {
        if (y > 3) {
            z = y;
            uint256 x = y / 2 + 1;
            while (x < z) {
                z = x;
                x = (y / x + x) / 2;
            }
        } else if (y != 0) {
            z = 1;
        }
    }

    function addLiquidity(uint256 amount0, uint256 amount1, address to) external returns (uint256 shares) {
        require(amount0 > 0 && amount1 > 0, "ZERO");
        token0.pull(msg.sender, amount0);
        token1.pull(msg.sender, amount1);

        if (totalSupply == 0) {
            shares = _sqrt(amount0 * amount1);
        } else {
            uint256 s0 = (amount0 * totalSupply) / reserve0;
            uint256 s1 = (amount1 * totalSupply) / reserve1;
            shares = s0 < s1 ? s0 : s1;
        }
        require(shares > 0, "ZERO_SHARES");
        _mint(to, shares);
        reserve0 += amount0;
        reserve1 += amount1;
        emit LiquidityAdded(to, amount0, amount1, shares);
    }

    function removeLiquidity(uint256 shares, address to) external returns (uint256 amount0, uint256 amount1) {
        require(shares > 0 && totalSupply > 0, "ZERO");
        amount0 = (shares * reserve0) / totalSupply;
        amount1 = (shares * reserve1) / totalSupply;
        _burn(msg.sender, shares);
        reserve0 -= amount0;
        reserve1 -= amount1;
        token0.push(to, amount0);
        token1.push(to, amount1);
        emit LiquidityRemoved(to, amount0, amount1, shares);
    }

    function quote(bool zeroForOne, uint256 amountIn) public view returns (uint256 amountOut) {
        (uint256 rIn, uint256 rOut) = zeroForOne ? (reserve0, reserve1) : (reserve1, reserve0);
        if (rIn == 0 || rOut == 0 || amountIn == 0) return 0;
        uint256 afterFee = amountIn * (10_000 - feeBps);
        amountOut = (afterFee * rOut) / (rIn * 10_000 + afterFee);
    }

    function swap(bool zeroForOne, uint256 amountIn, uint256 minOut, address to) external returns (uint256 amountOut) {
        amountOut = quote(zeroForOne, amountIn);
        require(amountOut >= minOut && amountOut > 0, "SLIPPAGE");
        if (zeroForOne) {
            token0.pull(msg.sender, amountIn);
            token1.push(to, amountOut);
            reserve0 += amountIn;
            reserve1 -= amountOut;
        } else {
            token1.pull(msg.sender, amountIn);
            token0.push(to, amountOut);
            reserve1 += amountIn;
            reserve0 -= amountOut;
        }
        emit Swap(to, zeroForOne, amountIn, amountOut);
    }

    /// Spot price of token0 in token1, 1e18 scaled — an `oracle`-shaped read.
    function price() external view returns (uint256) {
        if (reserve0 == 0) return 0;
        return (reserve1 * 1e18) / reserve0;
    }

    function setFee(uint16 feeBps_) external onlyOwner {
        require(feeBps_ <= 1_000, "FEE_TOO_HIGH");
        feeBps = feeBps_;
    }
}
