// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract ZconMerkleTree {
    uint256 public constant TREE_DEPTH = 20;
    uint256 public constant MAX_LEAVES = 2**TREE_DEPTH;
    
    bytes32[TREE_DEPTH] public zeros;
    bytes32[TREE_DEPTH] public filledSubtrees;
    bytes32 public root;
    uint256 public nextIndex = 0;
    
    mapping(bytes32 => bool) public roots;
    
    event LeafInserted(uint256 indexed index, bytes32 indexed leaf, bytes32 root);
    
    constructor() {
        bytes32 currentZero = bytes32(0);
        for (uint256 i = 0; i < TREE_DEPTH; i++) {
            zeros[i] = currentZero;
            filledSubtrees[i] = currentZero;
            currentZero = hashLeftRight(currentZero, currentZero);
        }
        root = currentZero;
        roots[root] = true;
    }
    
    function hashLeftRight(bytes32 left, bytes32 right) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(left, right));
    }
    
    function insert(bytes32 leaf) public returns (uint256) {
        require(nextIndex < MAX_LEAVES, "Tree is full");
        
        uint256 currentIndex = nextIndex;
        bytes32 currentHash = leaf;
        bytes32 left;
        bytes32 right;
        
        for (uint256 i = 0; i < TREE_DEPTH; i++) {
            if (currentIndex % 2 == 0) {
                left = currentHash;
                right = zeros[i];
                filledSubtrees[i] = currentHash;
            } else {
                left = filledSubtrees[i];
                right = currentHash;
            }
            currentHash = hashLeftRight(left, right);
            currentIndex /= 2;
        }
        
        root = currentHash;
        roots[root] = true;
        emit LeafInserted(nextIndex, leaf, root);
        nextIndex++;
        
        return nextIndex - 1;
    }
    
    function isKnownRoot(bytes32 _root) public view returns (bool) {
        return roots[_root];
    }
}