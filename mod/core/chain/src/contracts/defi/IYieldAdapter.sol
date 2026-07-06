// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IYieldAdapter
 * @dev Pluggable adapter that connects a single underlying asset to one external
 * DeFi yield venue (Aave, a mock, Compound, etc.). The vault owns the relationship:
 * it transfers the asset to the adapter and the adapter deploys it into the venue.
 *
 * This is the modularity seam — any number of adapters can be written and registered
 * as separate "strategies" in the YieldVault, giving users multiple lowfi yield options.
 */
interface IYieldAdapter {
    /// @dev The underlying asset this adapter accepts (e.g. USDC).
    function asset() external view returns (address);

    /// @dev Pull `amount` of asset from the vault (msg.sender) and deploy to the venue.
    ///      Returns the principal actually deployed (underlying decimals).
    function deposit(uint256 amount) external returns (uint256 deposited);

    /// @dev Withdraw `amount` of underlying from the venue to `to`.
    ///      Returns the amount actually withdrawn.
    function withdraw(uint256 amount, address to) external returns (uint256 withdrawn);

    /// @dev Current underlying value held = principal + accrued yield (underlying decimals).
    function totalAssets() external view returns (uint256);
}
