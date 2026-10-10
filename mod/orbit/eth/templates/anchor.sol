// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Anchor — put a content hash on chain and prove when you had it.
/// @notice The chain is a terrible place to store a file and an excellent place
/// to store the fact that a file existed. Anchor a CID (or any hash) here and
/// the block timestamp is a witness nobody can backdate. Content stays wherever
/// you keep it; only the claim is on chain.
contract Anchor {
    struct Record {
        address author;
        uint64 time;
        uint64 block_;
        string label;
    }

    mapping(bytes32 => Record) public records;
    mapping(address => bytes32[]) internal _byAuthor;
    uint256 public total;

    event Anchored(bytes32 indexed digest, address indexed author, string label, string cid);

    error AlreadyAnchored(address author, uint64 time);

    /// @param cid the human-readable pointer (an IPFS CID, a URL, anything)
    /// @param label a note to yourself
    function anchor(string calldata cid, string calldata label) external returns (bytes32 digest) {
        digest = keccak256(bytes(cid));
        Record storage existing = records[digest];
        if (existing.author != address(0)) revert AlreadyAnchored(existing.author, existing.time);
        records[digest] = Record(msg.sender, uint64(block.timestamp), uint64(block.number), label);
        _byAuthor[msg.sender].push(digest);
        total += 1;
        emit Anchored(digest, msg.sender, label, cid);
    }

    /// @notice Anchor a hash you computed elsewhere.
    function anchorHash(bytes32 digest, string calldata label) external {
        Record storage existing = records[digest];
        if (existing.author != address(0)) revert AlreadyAnchored(existing.author, existing.time);
        records[digest] = Record(msg.sender, uint64(block.timestamp), uint64(block.number), label);
        _byAuthor[msg.sender].push(digest);
        total += 1;
        emit Anchored(digest, msg.sender, label, "");
    }

    function verify(string calldata cid) external view
        returns (bool anchored, address author, uint64 time, string memory label)
    {
        Record memory record = records[keccak256(bytes(cid))];
        return (record.author != address(0), record.author, record.time, record.label);
    }

    function byAuthor(address author) external view returns (bytes32[] memory) {
        return _byAuthor[author];
    }
}
