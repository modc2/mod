// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./IYieldAdapter.sol";

/**
 * @title MockYieldAdapter
 * @dev Self-contained "lowfi" yield venue for localhost / CI / demos. It simply
 * holds the underlying; `totalAssets` is its balance, so the owner simulates yield
 * by funding the reserve via `addYield`. Deploy several with different labels to
 * present multiple lowfi yield options (e.g. Conservative vs Aggressive).
 *
 * No external protocol needed — this is the reference implementation of {IYieldAdapter}.
 */
contract MockYieldAdapter is IYieldAdapter, Ownable {
    using SafeERC20 for IERC20;

    IERC20 public immutable underlying;
    address public immutable vault;
    string public label;

    modifier onlyVault() {
        require(msg.sender == vault, "Only vault");
        _;
    }

    constructor(address _underlying, address _vault, string memory _label) {
        require(_underlying != address(0) && _vault != address(0), "Zero addr");
        underlying = IERC20(_underlying);
        vault = _vault;
        label = _label;
    }

    function asset() external view returns (address) {
        return address(underlying);
    }

    function deposit(uint256 amount) external onlyVault returns (uint256) {
        underlying.safeTransferFrom(vault, address(this), amount);
        return amount;
    }

    function withdraw(uint256 amount, address to) external onlyVault returns (uint256) {
        underlying.safeTransfer(to, amount);
        return amount;
    }

    function totalAssets() external view returns (uint256) {
        return underlying.balanceOf(address(this));
    }

    /// @dev Owner simulates accrued yield by funding the adapter's reserve.
    function addYield(uint256 amount) external onlyOwner {
        underlying.safeTransferFrom(msg.sender, address(this), amount);
    }
}
