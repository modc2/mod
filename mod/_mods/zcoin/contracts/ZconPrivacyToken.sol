// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract ZconPrivacyToken is ReentrancyGuard {
    struct Commitment {
        bytes32 commitment;
        uint256 amount;
        address token;
        bool spent;
    }

    mapping(bytes32 => Commitment) public commitments;
    mapping(bytes32 => bool) public nullifiers;
    
    event Deposit(bytes32 indexed commitment, address indexed token, uint256 amount);
    event Withdrawal(bytes32 indexed nullifier, address indexed token, uint256 amount);

    function deposit(address token, uint256 amount, bytes32 commitment) external nonReentrant {
        require(commitments[commitment].commitment == bytes32(0), "Commitment exists");
        require(amount > 0, "Amount must be positive");
        
        if (token == address(0)) {
            require(msg.value == amount, "ETH amount mismatch");
        } else {
            require(IERC20(token).transferFrom(msg.sender, address(this), amount), "Transfer failed");
        }
        
        commitments[commitment] = Commitment({
            commitment: commitment,
            amount: amount,
            token: token,
            spent: false
        });
        
        emit Deposit(commitment, token, amount);
    }

    function withdraw(
        bytes32 nullifier,
        bytes32 commitment,
        address recipient,
        bytes calldata zkProof
    ) external nonReentrant {
        require(!nullifiers[nullifier], "Nullifier used");
        require(commitments[commitment].commitment != bytes32(0), "Invalid commitment");
        require(!commitments[commitment].spent, "Already spent");
        require(verifyProof(zkProof, nullifier, commitment, recipient), "Invalid proof");
        
        Commitment storage comm = commitments[commitment];
        comm.spent = true;
        nullifiers[nullifier] = true;
        
        if (comm.token == address(0)) {
            payable(recipient).transfer(comm.amount);
        } else {
            require(IERC20(comm.token).transfer(recipient, comm.amount), "Transfer failed");
        }
        
        emit Withdrawal(nullifier, comm.token, comm.amount);
    }

    function verifyProof(
        bytes calldata proof,
        bytes32 nullifier,
        bytes32 commitment,
        address recipient
    ) public pure returns (bool) {
        // Simplified ZK proof verification - integrate with actual ZK library
        bytes32 hash = keccak256(abi.encodePacked(nullifier, commitment, recipient, proof));
        return hash != bytes32(0);
    }

    receive() external payable {}
}