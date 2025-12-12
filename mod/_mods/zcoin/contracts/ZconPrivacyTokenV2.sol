// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "./ZconMerkleTree.sol";

contract ZconPrivacyTokenV2 is ReentrancyGuard {
    ZconMerkleTree public merkleTree;
    
    struct Deposit {
        uint256 amount;
        address token;
        uint256 timestamp;
    }
    
    mapping(bytes32 => bool) public nullifiers;
    mapping(bytes32 => Deposit) public deposits;
    
    uint256 public constant DENOMINATION = 0.1 ether;
    uint256 public constant MERKLE_TREE_HEIGHT = 20;
    
    event DepositMade(bytes32 indexed commitment, uint256 leafIndex, uint256 timestamp);
    event WithdrawalMade(address indexed recipient, bytes32 indexed nullifier, uint256 amount);
    
    constructor() {
        merkleTree = new ZconMerkleTree();
    }
    
    function deposit(bytes32 commitment, address token, uint256 amount) external payable nonReentrant {
        require(amount > 0, "Amount must be positive");
        require(deposits[commitment].amount == 0, "Commitment already used");
        
        if (token == address(0)) {
            require(msg.value == amount, "ETH amount mismatch");
        } else {
            require(IERC20(token).transferFrom(msg.sender, address(this), amount), "Transfer failed");
        }
        
        deposits[commitment] = Deposit({
            amount: amount,
            token: token,
            timestamp: block.timestamp
        });
        
        uint256 leafIndex = merkleTree.insert(commitment);
        emit DepositMade(commitment, leafIndex, block.timestamp);
    }
    
    function withdraw(
        bytes32 nullifier,
        bytes32 root,
        address payable recipient,
        bytes32 commitment,
        bytes calldata zkProof,
        bytes32[] calldata merkleProof
    ) external nonReentrant {
        require(!nullifiers[nullifier], "Nullifier already used");
        require(merkleTree.isKnownRoot(root), "Invalid merkle root");
        require(deposits[commitment].amount > 0, "Invalid commitment");
        require(verifyMerkleProof(commitment, merkleProof, root), "Invalid merkle proof");
        require(verifyZKProof(zkProof, nullifier, commitment, recipient), "Invalid ZK proof");
        
        nullifiers[nullifier] = true;
        Deposit memory dep = deposits[commitment];
        
        if (dep.token == address(0)) {
            recipient.transfer(dep.amount);
        } else {
            require(IERC20(dep.token).transfer(recipient, dep.amount), "Transfer failed");
        }
        
        emit WithdrawalMade(recipient, nullifier, dep.amount);
    }
    
    function verifyMerkleProof(
        bytes32 leaf,
        bytes32[] calldata proof,
        bytes32 root
    ) public pure returns (bool) {
        bytes32 computedHash = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            computedHash = keccak256(abi.encodePacked(computedHash, proof[i]));
        }
        return computedHash == root;
    }
    
    function verifyZKProof(
        bytes calldata proof,
        bytes32 nullifier,
        bytes32 commitment,
        address recipient
    ) public pure returns (bool) {
        bytes32 hash = keccak256(abi.encodePacked(nullifier, commitment, recipient, proof));
        return hash != bytes32(0);
    }
    
    receive() external payable {}
}