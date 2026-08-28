// SPDX-License-Identifier: MIT
// Vault — deposit ETH, withdraw your own balance. No dependencies.
pragma solidity ^0.8.20;

contract Vault {
    mapping(address => uint256) public balanceOf;
    uint256 public totalDeposits;

    event Deposit(address indexed from, uint256 amount);
    event Withdrawal(address indexed to, uint256 amount);

    function deposit() public payable {
        require(msg.value > 0, "zero deposit");
        balanceOf[msg.sender] += msg.value;
        totalDeposits += msg.value;
        emit Deposit(msg.sender, msg.value);
    }

    function withdraw(uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "insufficient");
        // state first, transfer last — no reentrancy foothold
        balanceOf[msg.sender] -= amount;
        totalDeposits -= amount;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        emit Withdrawal(msg.sender, amount);
    }

    receive() external payable {
        deposit();
    }
}
