// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Governance block. Outputs an `owner` port — wire it into any other block's
/// owner slot and every privileged call on that block has to sit in the queue
/// for `delay` seconds first.
contract ModTimelock {
    address public admin;
    uint256 public delay;

    mapping(bytes32 => uint256) public queuedAt;

    event Queued(bytes32 indexed id, address target, uint256 value, bytes data, uint256 eta);
    event Executed(bytes32 indexed id, address target);
    event Cancelled(bytes32 indexed id);
    event AdminChanged(address indexed from, address indexed to);

    constructor(uint256 delay_, address admin_) {
        require(delay_ <= 30 days, "DELAY_TOO_LONG");
        delay = delay_;
        admin = admin_ == address(0) ? msg.sender : admin_;
    }

    modifier onlyAdmin() {
        require(msg.sender == admin, "NOT_ADMIN");
        _;
    }

    function hash(address target, uint256 value, bytes calldata data, bytes32 salt) public pure returns (bytes32) {
        return keccak256(abi.encode(target, value, data, salt));
    }

    function queue(address target, uint256 value, bytes calldata data, bytes32 salt) external onlyAdmin returns (bytes32 id) {
        id = hash(target, value, data, salt);
        require(queuedAt[id] == 0, "QUEUED");
        uint256 eta = block.timestamp + delay;
        queuedAt[id] = eta;
        emit Queued(id, target, value, data, eta);
    }

    function execute(address target, uint256 value, bytes calldata data, bytes32 salt) external payable onlyAdmin returns (bytes memory) {
        bytes32 id = hash(target, value, data, salt);
        uint256 eta = queuedAt[id];
        require(eta != 0, "NOT_QUEUED");
        require(block.timestamp >= eta, "TOO_EARLY");
        delete queuedAt[id];
        (bool ok, bytes memory ret) = target.call{value: value}(data);
        require(ok, "CALL_FAILED");
        emit Executed(id, target);
        return ret;
    }

    function cancel(bytes32 id) external onlyAdmin {
        require(queuedAt[id] != 0, "NOT_QUEUED");
        delete queuedAt[id];
        emit Cancelled(id);
    }

    /// Only reachable through the queue — the timelock governs itself.
    function setDelay(uint256 delay_) external {
        require(msg.sender == address(this), "TIMELOCK_ONLY");
        require(delay_ <= 30 days, "DELAY_TOO_LONG");
        delay = delay_;
    }

    function setAdmin(address admin_) external onlyAdmin {
        emit AdminChanged(admin, admin_);
        admin = admin_;
    }

    receive() external payable {}
}
