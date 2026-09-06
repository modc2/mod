// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MultiSig — n-of-m approval before anything leaves the contract.
/// @notice Owners propose a call, owners confirm it, and it executes once the
/// threshold is met. Confirmations are revocable up to execution, and the
/// executed flag is set before the call so a re-entrant callee cannot run it
/// twice.
contract MultiSig {
    address[] public owners;
    mapping(address => bool) public isOwner;
    uint256 public threshold;

    struct Transaction {
        address to;
        uint256 value;
        bytes data;
        bool executed;
        uint256 confirmations;
    }

    Transaction[] public transactions;
    mapping(uint256 => mapping(address => bool)) public confirmedBy;

    event Deposit(address indexed from, uint256 amount);
    event Proposed(uint256 indexed id, address indexed by, address to, uint256 value);
    event Confirmed(uint256 indexed id, address indexed by, uint256 count);
    event Revoked(uint256 indexed id, address indexed by);
    event Executed(uint256 indexed id, bool success, bytes result);

    error NotAnOwner();
    error NoSuchTransaction();
    error AlreadyExecuted();
    error AlreadyConfirmed();
    error NotConfirmed();
    error NotEnoughConfirmations(uint256 have, uint256 need);
    error BadSetup();

    modifier onlyOwner() {
        if (!isOwner[msg.sender]) revert NotAnOwner();
        _;
    }

    constructor(address[] memory _owners, uint256 _threshold) {
        if (_owners.length == 0 || _threshold == 0 || _threshold > _owners.length)
            revert BadSetup();
        for (uint256 i = 0; i < _owners.length; i++) {
            address candidate = _owners[i];
            if (candidate == address(0) || isOwner[candidate]) revert BadSetup();
            isOwner[candidate] = true;
            owners.push(candidate);
        }
        threshold = _threshold;
    }

    receive() external payable {
        emit Deposit(msg.sender, msg.value);
    }

    function propose(address to, uint256 value, bytes calldata data)
        external onlyOwner returns (uint256 id)
    {
        id = transactions.length;
        transactions.push(Transaction(to, value, data, false, 0));
        emit Proposed(id, msg.sender, to, value);
        confirm(id);
    }

    function confirm(uint256 id) public onlyOwner {
        if (id >= transactions.length) revert NoSuchTransaction();
        Transaction storage txn = transactions[id];
        if (txn.executed) revert AlreadyExecuted();
        if (confirmedBy[id][msg.sender]) revert AlreadyConfirmed();
        confirmedBy[id][msg.sender] = true;
        txn.confirmations += 1;
        emit Confirmed(id, msg.sender, txn.confirmations);
    }

    function revoke(uint256 id) external onlyOwner {
        if (id >= transactions.length) revert NoSuchTransaction();
        Transaction storage txn = transactions[id];
        if (txn.executed) revert AlreadyExecuted();
        if (!confirmedBy[id][msg.sender]) revert NotConfirmed();
        confirmedBy[id][msg.sender] = false;
        txn.confirmations -= 1;
        emit Revoked(id, msg.sender);
    }

    function execute(uint256 id) external onlyOwner returns (bytes memory) {
        if (id >= transactions.length) revert NoSuchTransaction();
        Transaction storage txn = transactions[id];
        if (txn.executed) revert AlreadyExecuted();
        if (txn.confirmations < threshold)
            revert NotEnoughConfirmations(txn.confirmations, threshold);
        txn.executed = true;                       // set before the call, deliberately
        (bool ok, bytes memory result) = txn.to.call{value: txn.value}(txn.data);
        emit Executed(id, ok, result);
        require(ok, "the inner call reverted");
        return result;
    }

    function ownerCount() external view returns (uint256) {
        return owners.length;
    }

    function transactionCount() external view returns (uint256) {
        return transactions.length;
    }
}
