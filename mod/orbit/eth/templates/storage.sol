// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title KeyValue — a public string map with per-key authorship.
/// @notice The "hello world" of state, useful past the tutorial: the first
/// writer of a key keeps it, so this doubles as a squatter-resistant registry
/// for pointing a name at a CID, a URL or an address.
contract KeyValue {
    mapping(string => string) private _values;
    mapping(string => address) public authorOf;
    mapping(string => uint256) public updatedAt;
    string[] public keys;

    event Set(string indexed key, string value, address indexed author);

    error NotYours(address held);

    function set(string calldata key, string calldata value) external {
        address held = authorOf[key];
        if (held == address(0)) {
            authorOf[key] = msg.sender;
            keys.push(key);
        } else if (held != msg.sender) {
            revert NotYours(held);
        }
        _values[key] = value;
        updatedAt[key] = block.timestamp;
        emit Set(key, value, msg.sender);
    }

    function get(string calldata key) external view returns (string memory) {
        return _values[key];
    }

    function count() external view returns (uint256) {
        return keys.length;
    }

    /// @notice A page of keys — cheaper than pulling the whole array into a node.
    function page(uint256 offset, uint256 limit) external view returns (string[] memory out) {
        uint256 end = offset + limit;
        if (end > keys.length) end = keys.length;
        if (offset >= end) return new string[](0);
        out = new string[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = keys[i];
    }
}
