// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title CommitmentTree
 * @dev Incremental Merkle tree for storing privacy commitments.
 *      Fixed depth of 20 levels (~1M leaves). Uses keccak256 hashing.
 *      Maintains a rolling history of roots so proofs against recent
 *      roots remain valid even after new insertions.
 */
contract CommitmentTree {
    uint256 public constant LEVELS = 20;
    uint256 public constant ROOT_HISTORY_SIZE = 30;

    // Zero values for each level (precomputed keccak chain)
    bytes32[21] public zeros;
    // Filled subtrees — one per level
    bytes32[21] public filledSubtrees;

    uint256 public nextIndex;
    bytes32[30] public roots;
    uint256 public currentRootIndex;

    constructor() {
        // Compute zero hashes for the empty tree
        bytes32 current = keccak256(abi.encodePacked(bytes32(0)));
        zeros[0] = current;
        filledSubtrees[0] = current;
        for (uint256 i = 1; i <= LEVELS; i++) {
            current = keccak256(abi.encodePacked(current, current));
            zeros[i] = current;
            filledSubtrees[i] = current;
        }
        roots[0] = current;
    }

    function insert(bytes32 leaf) external returns (uint256 index) {
        require(nextIndex < 2 ** LEVELS, "tree full");
        index = nextIndex;
        nextIndex++;

        bytes32 current = leaf;
        uint256 idx = index;

        for (uint256 i = 0; i < LEVELS; i++) {
            if (idx % 2 == 0) {
                filledSubtrees[i] = current;
                current = keccak256(abi.encodePacked(current, zeros[i]));
            } else {
                current = keccak256(abi.encodePacked(filledSubtrees[i], current));
            }
            idx >>= 1;
        }

        currentRootIndex = (currentRootIndex + 1) % ROOT_HISTORY_SIZE;
        roots[currentRootIndex] = current;
    }

    function isKnownRoot(bytes32 root) public view returns (bool) {
        if (root == bytes32(0)) return false;
        uint256 i = currentRootIndex;
        for (uint256 j = 0; j < ROOT_HISTORY_SIZE; j++) {
            if (roots[i] == root) return true;
            if (i == 0) i = ROOT_HISTORY_SIZE - 1;
            else i--;
        }
        return false;
    }

    function getLastRoot() external view returns (bytes32) {
        return roots[currentRootIndex];
    }
}
