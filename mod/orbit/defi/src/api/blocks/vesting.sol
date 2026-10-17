// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Linear vesting with an optional cliff. Takes an `erc20` port; usually wired
/// to the same token block that seeds an AMM, so the launch schedule and the
/// liquidity live in one diagram.
contract ModVesting is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable token;
    address public beneficiary;
    uint64 public start;
    uint64 public cliff;
    uint64 public duration;
    uint256 public released;
    bool public revocable;
    bool public revoked;

    event Released(address indexed to, uint256 amount);
    event Revoked(uint256 returned);

    constructor(
        address token_,
        address beneficiary_,
        uint64 start_,
        uint64 cliffSeconds,
        uint64 duration_,
        bool revocable_,
        address owner_
    ) Owned(owner_) {
        require(token_ != address(0) && beneficiary_ != address(0), "ZERO_ADDR");
        require(duration_ > 0, "ZERO_DURATION");
        require(cliffSeconds <= duration_, "CLIFF_GT_DURATION");
        token = IERC20(token_);
        beneficiary = beneficiary_;
        start = start_ == 0 ? uint64(block.timestamp) : start_;
        cliff = start + cliffSeconds;
        duration = duration_;
        revocable = revocable_;
    }

    function total() public view returns (uint256) {
        return token.balanceOf(address(this)) + released;
    }

    function vested() public view returns (uint256) {
        if (block.timestamp < cliff) return 0;
        if (block.timestamp >= start + duration || revoked) return total();
        return (total() * (block.timestamp - start)) / duration;
    }

    function releasable() public view returns (uint256) {
        uint256 v = vested();
        return v > released ? v - released : 0;
    }

    function release() external returns (uint256 amount) {
        amount = releasable();
        require(amount > 0, "NOTHING_VESTED");
        released += amount;
        token.push(beneficiary, amount);
        emit Released(beneficiary, amount);
    }

    function revoke() external onlyOwner {
        require(revocable && !revoked, "NOT_REVOCABLE");
        uint256 unvested = total() - vested();
        revoked = true;
        if (unvested > 0) token.push(owner, unvested);
        emit Revoked(unvested);
    }

    function setBeneficiary(address beneficiary_) external {
        require(msg.sender == beneficiary, "NOT_BENEFICIARY");
        require(beneficiary_ != address(0), "ZERO_ADDR");
        beneficiary = beneficiary_;
    }
}
