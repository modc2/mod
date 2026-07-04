// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "./IYieldAdapter.sol";

/// @dev Minimal Aave V3 Pool surface used by this adapter.
interface IAaveV3Pool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
}

/**
 * @title AaveV3Adapter
 * @dev Real {IYieldAdapter} over Aave V3. Supplies the underlying to the Aave Pool and
 * holds the rebasing aToken; `totalAssets` is the aToken balance (principal + interest).
 * Intended for Base mainnet; exercise against a mainnet fork rather than the unit suite.
 *
 * Constructor wiring (Base): pool = Aave V3 Pool, underlying = e.g. USDC, aToken = aBasUSDC.
 */
contract AaveV3Adapter is IYieldAdapter {
    using SafeERC20 for IERC20;

    IAaveV3Pool public immutable pool;
    IERC20 public immutable underlying;
    IERC20 public immutable aToken;
    address public immutable vault;

    modifier onlyVault() {
        require(msg.sender == vault, "Only vault");
        _;
    }

    constructor(address _pool, address _underlying, address _aToken, address _vault) {
        require(
            _pool != address(0) && _underlying != address(0) &&
            _aToken != address(0) && _vault != address(0),
            "Zero addr"
        );
        pool = IAaveV3Pool(_pool);
        underlying = IERC20(_underlying);
        aToken = IERC20(_aToken);
        vault = _vault;
    }

    function asset() external view returns (address) {
        return address(underlying);
    }

    function deposit(uint256 amount) external onlyVault returns (uint256 deposited) {
        underlying.safeTransferFrom(vault, address(this), amount);
        underlying.forceApprove(address(pool), amount);
        uint256 b0 = aToken.balanceOf(address(this));
        pool.supply(address(underlying), amount, address(this), 0);
        deposited = aToken.balanceOf(address(this)) - b0;
    }

    function withdraw(uint256 amount, address to) external onlyVault returns (uint256) {
        return pool.withdraw(address(underlying), amount, to);
    }

    function totalAssets() external view returns (uint256) {
        return aToken.balanceOf(address(this));
    }
}
