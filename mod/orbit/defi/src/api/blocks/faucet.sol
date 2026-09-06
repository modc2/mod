// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Rate-limited drip for whatever `erc20` it is wired to. Not glamorous, but
/// every testnet composition needs one and hand-rolling it every time is how
/// people end up with an unlimited-mint "faucet" in production.
contract ModFaucet is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable token;
    uint256 public dripAmount;
    uint256 public cooldown;

    mapping(address => uint256) public lastClaim;

    event Dripped(address indexed to, uint256 amount);

    constructor(address token_, uint256 dripAmount_, uint256 cooldown_, address owner_) Owned(owner_) {
        require(token_ != address(0), "NO_TOKEN");
        token = IERC20(token_);
        dripAmount = dripAmount_;
        cooldown = cooldown_;
    }

    function nextClaimAt(address user) external view returns (uint256) {
        uint256 last = lastClaim[user];
        return last == 0 ? block.timestamp : last + cooldown;
    }

    function drip() external returns (uint256) {
        require(lastClaim[msg.sender] == 0 || block.timestamp >= lastClaim[msg.sender] + cooldown, "COOLDOWN");
        uint256 balance = token.balanceOf(address(this));
        require(balance > 0, "DRY");
        uint256 amount = dripAmount > balance ? balance : dripAmount;
        lastClaim[msg.sender] = block.timestamp;
        token.push(msg.sender, amount);
        emit Dripped(msg.sender, amount);
        return amount;
    }

    function setDrip(uint256 dripAmount_, uint256 cooldown_) external onlyOwner {
        dripAmount = dripAmount_;
        cooldown = cooldown_;
    }

    function sweep(address to) external onlyOwner {
        token.push(to, token.balanceOf(address(this)));
    }
}
