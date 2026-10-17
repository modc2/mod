// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Fixed-rate yield strategy — the simplest thing that satisfies a `strategy`
/// port. Accrues linear interest on deposited principal and pays it out of a
/// reserve the owner tops up, so a composed protocol is testable end-to-end
/// without a live external venue.
contract ModFixedYieldStrategy is IStrategy, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable underlying;
    address public vault;
    uint16 public rateBps; // per year, on principal
    uint256 public principal;
    uint256 public lastAccrual;
    uint256 public accrued;

    event Accrued(uint256 amount, uint256 principal);

    constructor(address asset_, uint16 rateBps_, address owner_) Owned(owner_) {
        require(asset_ != address(0), "NO_ASSET");
        require(rateBps_ <= 10_000, "RATE_TOO_HIGH");
        underlying = IERC20(asset_);
        rateBps = rateBps_;
        lastAccrual = block.timestamp;
    }

    function asset() external view returns (address) {
        return address(underlying);
    }

    function totalAssets() public view returns (uint256) {
        return principal + accrued + _pending();
    }

    function _pending() internal view returns (uint256) {
        if (principal == 0 || rateBps == 0) return 0;
        uint256 elapsed = block.timestamp - lastAccrual;
        return (principal * rateBps * elapsed) / (10_000 * 365 days);
    }

    function _accrue() internal {
        uint256 pending = _pending();
        lastAccrual = block.timestamp;
        if (pending == 0) return;
        // Only recognise yield the reserve can actually pay.
        uint256 reserve = underlying.balanceOf(address(this));
        uint256 backed = reserve > principal + accrued ? reserve - principal - accrued : 0;
        uint256 realised = pending > backed ? backed : pending;
        accrued += realised;
        emit Accrued(realised, principal);
    }

    function deposit(uint256 amount) external {
        require(vault == address(0) || msg.sender == vault, "NOT_VAULT");
        _accrue();
        underlying.pull(msg.sender, amount);
        principal += amount;
    }

    function withdraw(uint256 amount) external {
        require(vault == address(0) || msg.sender == vault, "NOT_VAULT");
        _accrue();
        uint256 fromYield = amount > principal ? amount - principal : 0;
        principal = amount > principal ? 0 : principal - amount;
        accrued = fromYield > accrued ? 0 : accrued - fromYield;
        underlying.push(msg.sender, amount);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setVault(address vault_) external onlyOwner {
        vault = vault_;
    }

    function setRate(uint16 rateBps_) external onlyOwner {
        require(rateBps_ <= 10_000, "RATE_TOO_HIGH");
        _accrue();
        rateBps = rateBps_;
    }

    /// Owner funds the yield reserve.
    function fund(uint256 amount) external {
        underlying.pull(msg.sender, amount);
    }
}
