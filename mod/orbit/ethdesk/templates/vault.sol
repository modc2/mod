// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title TimeVault — ETH you cannot spend until a date you set.
/// @notice Deposits are per-account and each has its own unlock time; a later
/// deposit can extend its own lock but never shorten it. Useful as a vesting
/// stub, a commitment device, or a deploy that demonstrates time in Solidity
/// without a scheduler.
contract TimeVault {
    struct Lock {
        uint256 amount;
        uint64 unlockAt;
    }

    mapping(address => Lock) public locks;
    uint256 public totalLocked;

    event Deposited(address indexed who, uint256 amount, uint64 unlockAt);
    event Withdrawn(address indexed who, uint256 amount);

    error StillLocked(uint64 unlockAt);
    error NothingHere();
    error WouldShortenLock();

    /// @param secondsFromNow how long this deposit stays put
    function deposit(uint64 secondsFromNow) external payable {
        if (msg.value == 0) revert NothingHere();
        Lock storage lock = locks[msg.sender];
        uint64 unlockAt = uint64(block.timestamp) + secondsFromNow;
        if (lock.amount > 0 && unlockAt < lock.unlockAt) revert WouldShortenLock();
        lock.amount += msg.value;
        lock.unlockAt = unlockAt;
        totalLocked += msg.value;
        emit Deposited(msg.sender, msg.value, unlockAt);
    }

    function withdraw() external {
        Lock storage lock = locks[msg.sender];
        if (lock.amount == 0) revert NothingHere();
        if (block.timestamp < lock.unlockAt) revert StillLocked(lock.unlockAt);
        uint256 amount = lock.amount;
        lock.amount = 0;
        totalLocked -= amount;
        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        require(ok, "withdraw failed");
        emit Withdrawn(msg.sender, amount);
    }

    function secondsLeft(address who) external view returns (uint256) {
        Lock memory lock = locks[who];
        if (lock.amount == 0 || block.timestamp >= lock.unlockAt) return 0;
        return lock.unlockAt - block.timestamp;
    }
}
