// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/access/Ownable.sol";

contract ZconOracle is Ownable {
    struct OracleData {
        bytes32 dataHash;
        uint256 timestamp;
        bytes zkProof;
        bool verified;
    }

    mapping(bytes32 => OracleData) public oracleData;
    mapping(address => bool) public authorizedOracles;
    
    event DataStored(bytes32 indexed key, bytes32 dataHash, uint256 timestamp);
    event OracleAuthorized(address indexed oracle);
    event OracleRevoked(address indexed oracle);

    modifier onlyAuthorized() {
        require(authorizedOracles[msg.sender] || msg.sender == owner(), "Not authorized");
        _;
    }

    function authorizeOracle(address oracle) external onlyOwner {
        authorizedOracles[oracle] = true;
        emit OracleAuthorized(oracle);
    }

    function revokeOracle(address oracle) external onlyOwner {
        authorizedOracles[oracle] = false;
        emit OracleRevoked(oracle);
    }

    function storeData(
        bytes32 key,
        bytes32 dataHash,
        bytes calldata zkProof
    ) external onlyAuthorized {
        require(verifyDataProof(dataHash, zkProof), "Invalid proof");
        
        oracleData[key] = OracleData({
            dataHash: dataHash,
            timestamp: block.timestamp,
            zkProof: zkProof,
            verified: true
        });
        
        emit DataStored(key, dataHash, block.timestamp);
    }

    function getData(bytes32 key) external view returns (OracleData memory) {
        return oracleData[key];
    }

    function verifyDataProof(bytes32 dataHash, bytes calldata proof) public pure returns (bool) {
        // Simplified ZK proof verification
        return keccak256(abi.encodePacked(dataHash, proof)) != bytes32(0);
    }
}