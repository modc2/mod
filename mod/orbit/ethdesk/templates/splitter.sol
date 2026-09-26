// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Splitter — incoming ETH divided by fixed shares, claimed on demand.
/// @notice Pull, not push: the contract never loops over payees during a
/// receive, so one payee whose fallback reverts cannot freeze everyone else's
/// money. Each account withdraws what it is owed, whenever it likes.
contract Splitter {
    address[] public payees;
    mapping(address => uint256) public shares;
    mapping(address => uint256) public released;
    uint256 public totalShares;
    uint256 public totalReleased;

    event Received(address indexed from, uint256 amount);
    event Released(address indexed to, uint256 amount);

    error BadSetup();
    error NothingOwed();

    constructor(address[] memory _payees, uint256[] memory _shares) {
        if (_payees.length == 0 || _payees.length != _shares.length) revert BadSetup();
        for (uint256 i = 0; i < _payees.length; i++) {
            address payee = _payees[i];
            if (payee == address(0) || _shares[i] == 0 || shares[payee] != 0)
                revert BadSetup();
            payees.push(payee);
            shares[payee] = _shares[i];
            totalShares += _shares[i];
        }
    }

    receive() external payable {
        emit Received(msg.sender, msg.value);
    }

    /// @notice What `account` could withdraw right now.
    function owed(address account) public view returns (uint256) {
        if (shares[account] == 0) return 0;
        uint256 lifetime = address(this).balance + totalReleased;
        return (lifetime * shares[account]) / totalShares - released[account];
    }

    function release(address payable account) external {
        uint256 amount = owed(account);
        if (amount == 0) revert NothingOwed();
        released[account] += amount;
        totalReleased += amount;
        (bool ok, ) = account.call{value: amount}("");
        require(ok, "transfer failed");
        emit Released(account, amount);
    }

    function payeeCount() external view returns (uint256) {
        return payees.length;
    }
}
