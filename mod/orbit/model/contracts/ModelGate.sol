// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title  ModelGate
/// @notice Owner-managed whitelist gate for the unified model gateway.
///         The off-chain API verifies an EIP-191 personal-signed challenge
///         that proves the holder of an allowed address authorised a chat
///         session. The contract never holds keys — only the membership set.
/// @dev    Designed to be deployed on Base Sepolia (chainId 84532).
///         Pattern lifted from the existing claude whitelist gate but kept
///         self-contained so the model module can own its own roster.
contract ModelGate {
    // ── storage ──────────────────────────────────────────────────────
    address public owner;

    /// Membership. address => allowed
    mapping(address => bool) public allowed;

    /// Optional per-address daily quota (0 = unlimited). Off-chain enforces;
    /// contract just publishes the policy so any verifier agrees.
    mapping(address => uint64) public dailyQuota;

    /// Monotonically increasing nonce per address — the API requires the
    /// signed challenge to include the current value, then expects clients
    /// to refresh on the next session. Off-chain can call `bumpNonce` after
    /// observing abuse.
    mapping(address => uint64) public nonce;

    // ── events ───────────────────────────────────────────────────────
    event OwnerChanged(address indexed previous, address indexed next);
    event Allowed(address indexed account, bool allowed, uint64 quota);
    event NonceBumped(address indexed account, uint64 nonce);

    // ── errors ───────────────────────────────────────────────────────
    error NotOwner();
    error ZeroAddress();

    // ── modifiers ────────────────────────────────────────────────────
    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        emit OwnerChanged(address(0), initialOwner);
    }

    // ── roster ───────────────────────────────────────────────────────
    function setAllowed(address account, bool isAllowed, uint64 quota) external onlyOwner {
        if (account == address(0)) revert ZeroAddress();
        allowed[account] = isAllowed;
        dailyQuota[account] = quota;
        emit Allowed(account, isAllowed, quota);
    }

    function setAllowedBatch(address[] calldata accounts, bool isAllowed, uint64 quota) external onlyOwner {
        for (uint256 i = 0; i < accounts.length; i++) {
            address a = accounts[i];
            if (a == address(0)) revert ZeroAddress();
            allowed[a] = isAllowed;
            dailyQuota[a] = quota;
            emit Allowed(a, isAllowed, quota);
        }
    }

    function bumpNonce(address account) external onlyOwner {
        nonce[account] += 1;
        emit NonceBumped(account, nonce[account]);
    }

    function transferOwnership(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit OwnerChanged(owner, next);
        owner = next;
    }

    // ── views ────────────────────────────────────────────────────────
    /// @notice Convenience read for the API: returns (allowed, quota, nonce).
    function status(address account) external view returns (bool, uint64, uint64) {
        return (allowed[account], dailyQuota[account], nonce[account]);
    }

    /// @notice The exact human-readable challenge string the API expects to
    ///         be personal-signed. Centralising it here keeps the on- and
    ///         off-chain definitions in lockstep.
    function challenge(address account, uint64 issuedAt) external view returns (string memory) {
        return string.concat(
            "mod-model-gate v1\n",
            "address: ", _toHex(account), "\n",
            "nonce: ", _u64ToStr(nonce[account]), "\n",
            "issuedAt: ", _u64ToStr(issuedAt)
        );
    }

    // ── internals ────────────────────────────────────────────────────
    function _toHex(address a) private pure returns (string memory) {
        bytes memory s = new bytes(42);
        bytes16 hexChars = 0x30313233343536373839616263646566; // "0123456789abcdef"
        s[0] = "0"; s[1] = "x";
        for (uint256 i = 0; i < 20; i++) {
            uint8 b = uint8(uint160(a) >> (8 * (19 - i)));
            s[2 + i * 2]     = hexChars[b >> 4];
            s[2 + i * 2 + 1] = hexChars[b & 0x0f];
        }
        return string(s);
    }

    function _u64ToStr(uint64 v) private pure returns (string memory) {
        if (v == 0) return "0";
        uint64 t = v; uint256 n;
        while (t != 0) { n++; t /= 10; }
        bytes memory out = new bytes(n);
        while (v != 0) { n--; out[n] = bytes1(uint8(48 + v % 10)); v /= 10; }
        return string(out);
    }
}
