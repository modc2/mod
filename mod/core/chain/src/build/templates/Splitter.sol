// SPDX-License-Identifier: MIT
// Splitter — ETH sent here is split between payees by weight, pull-payment style.
pragma solidity ^0.8.20;

contract Splitter {
    address[] public payees;
    uint256[] public shares;
    uint256 public totalShares;

    mapping(address => uint256) public released;
    uint256 public totalReceived;

    event PaymentReceived(address indexed from, uint256 amount);
    event PaymentReleased(address indexed to, uint256 amount);

    constructor(address[] memory payees_, uint256[] memory shares_) {
        require(payees_.length == shares_.length && payees_.length > 0, "bad payees");
        for (uint256 i = 0; i < payees_.length; i++) {
            require(payees_[i] != address(0) && shares_[i] > 0, "bad payee");
            payees.push(payees_[i]);
            shares.push(shares_[i]);
            totalShares += shares_[i];
        }
    }

    /// Everything owed to `account` that hasn't been released yet.
    function pending(address account) public view returns (uint256) {
        uint256 idx = type(uint256).max;
        for (uint256 i = 0; i < payees.length; i++) {
            if (payees[i] == account) { idx = i; break; }
        }
        if (idx == type(uint256).max) return 0;
        uint256 owed = (totalReceived * shares[idx]) / totalShares;
        return owed - released[account];
    }

    function release(address payable account) external {
        uint256 amount = pending(account);
        require(amount > 0, "nothing due");
        released[account] += amount;
        (bool ok, ) = account.call{value: amount}("");
        require(ok, "transfer failed");
        emit PaymentReleased(account, amount);
    }

    receive() external payable {
        totalReceived += msg.value;
        emit PaymentReceived(msg.sender, msg.value);
    }
}
