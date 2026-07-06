// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title BridgeableToken
 * @dev ERC20 token with owner-controlled mint/burn for custom bridging
 * Owner can mint and burn tokens to facilitate cross-chain transfers
 * This is a temporary solution until migration to native bridging
 */
contract BridgeableToken is ERC20, Ownable {

    event BridgeMint(address indexed to, uint256 amount, string bridgeId);
    event BridgeBurn(address indexed from, uint256 amount, string bridgeId);
    event BridgeMintFailed(address indexed to, uint256 amount, string bridgeId, string reason);
    event BridgeBurnFailed(address indexed from, uint256 amount, string bridgeId, string reason);
    event ContractSetOwnerless();
    event Commitment(bytes32 indexed sourceHash, address indexed evmAddress, string sourceAddress, string sourceType);
    event MintQueued(bytes32 indexed operationId, address indexed to, uint256 amount, uint256 executeAfter);
    event BurnQueued(bytes32 indexed operationId, address indexed from, uint256 amount, uint256 executeAfter);

    uint256 public constant TIMELOCK_DELAY = 24 hours;
    uint256 public immutable SUPPLY_CAP;

    struct QueuedOperation {
        address target;
        uint256 amount;
        string bridgeId;
        uint256 executeAfter;
        bool executed;
        bool isMint;
    }

    mapping(bytes32 => QueuedOperation) public queuedOperations;
    mapping(bytes32 => address) public commitments;
    mapping(address => bytes32[]) public evmCommitments;

    /**
     * @dev Constructor
     * @param name Token name
     * @param symbol Token symbol
     * @param initialSupply Initial token supply minted to deployer
     * @param supplyCap Maximum total supply (0 = unlimited)
     */
    constructor(
        string memory name,
        string memory symbol,
        uint256 initialSupply,
        uint256 supplyCap
    ) ERC20(name, symbol) {
        SUPPLY_CAP = supplyCap;
        if (initialSupply > 0) {
            require(supplyCap == 0 || initialSupply <= supplyCap, "Initial supply exceeds cap");
            _mint(msg.sender, initialSupply);
        }
    }

    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: bridgeMint, bridgeBurn, batchBridgeMint, batchBridgeBurn.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    /**
     * @dev Queue mint operation with timelock (owner only)
     * @param to Address to mint tokens to
     * @param amount Amount of tokens to mint
     * @param bridgeId Identifier for the bridge transaction
     */
    function queueMint(address to, uint256 amount, string memory bridgeId) external onlyOwner returns (bytes32) {
        require(to != address(0), "Cannot mint to zero address");
        require(amount > 0, "Amount must be greater than 0");

        bytes32 operationId = keccak256(abi.encode(to, amount, bridgeId, block.timestamp, true));
        require(queuedOperations[operationId].executeAfter == 0, "Operation already queued");

        uint256 executeAfter = block.timestamp + TIMELOCK_DELAY;
        queuedOperations[operationId] = QueuedOperation({
            target: to,
            amount: amount,
            bridgeId: bridgeId,
            executeAfter: executeAfter,
            executed: false,
            isMint: true
        });

        emit MintQueued(operationId, to, amount, executeAfter);
        return operationId;
    }

    /**
     * @dev Execute queued mint operation after timelock (anyone can call)
     * @param operationId ID of the queued operation
     */
    function executeMint(bytes32 operationId) external {
        QueuedOperation storage op = queuedOperations[operationId];
        require(op.executeAfter > 0, "Operation not queued");
        require(!op.executed, "Operation already executed");
        require(op.isMint, "Not a mint operation");
        require(block.timestamp >= op.executeAfter, "Timelock not expired");

        if (SUPPLY_CAP > 0) {
            require(totalSupply() + op.amount <= SUPPLY_CAP, "Exceeds supply cap");
        }

        op.executed = true;
        _mint(op.target, op.amount);
        emit BridgeMint(op.target, op.amount, op.bridgeId);
    }

    /**
     * @dev Mint tokens for bridging (owner only)
     * @param to Address to mint tokens to
     * @param amount Amount of tokens to mint
     * @param bridgeId Identifier for the bridge transaction
     */
    function bridgeMint(address to, uint256 amount, string memory bridgeId) external onlyOwner {
        if (to == address(0)) {
            emit BridgeMintFailed(to, amount, bridgeId, "Cannot mint to zero address");
            revert("Cannot mint to zero address");
        }
        if (amount == 0) {
            emit BridgeMintFailed(to, amount, bridgeId, "Amount must be greater than 0");
            revert("Amount must be greater than 0");
        }

        if (SUPPLY_CAP > 0) {
            if (totalSupply() + amount > SUPPLY_CAP) {
                emit BridgeMintFailed(to, amount, bridgeId, "Exceeds supply cap");
                revert("Exceeds supply cap");
            }
        }

        _mint(to, amount);
        emit BridgeMint(to, amount, bridgeId);
    }

    /**
     * @dev Queue burn operation with timelock (owner only)
     * @param from Address to burn tokens from
     * @param amount Amount of tokens to burn
     * @param bridgeId Identifier for the bridge transaction
     */
    function queueBurn(address from, uint256 amount, string memory bridgeId) external onlyOwner returns (bytes32) {
        require(from != address(0), "Cannot burn from zero address");
        require(amount > 0, "Amount must be greater than 0");

        bytes32 operationId = keccak256(abi.encode(from, amount, bridgeId, block.timestamp, false));
        require(queuedOperations[operationId].executeAfter == 0, "Operation already queued");

        uint256 executeAfter = block.timestamp + TIMELOCK_DELAY;
        queuedOperations[operationId] = QueuedOperation({
            target: from,
            amount: amount,
            bridgeId: bridgeId,
            executeAfter: executeAfter,
            executed: false,
            isMint: false
        });

        emit BurnQueued(operationId, from, amount, executeAfter);
        return operationId;
    }

    /**
     * @dev Execute queued burn operation after timelock (anyone can call)
     * @param operationId ID of the queued operation
     */
    function executeBurn(bytes32 operationId) external {
        QueuedOperation storage op = queuedOperations[operationId];
        require(op.executeAfter > 0, "Operation not queued");
        require(!op.executed, "Operation already executed");
        require(!op.isMint, "Not a burn operation");
        require(block.timestamp >= op.executeAfter, "Timelock not expired");
        require(balanceOf(op.target) >= op.amount, "Insufficient balance to burn");

        op.executed = true;
        _burn(op.target, op.amount);
        emit BridgeBurn(op.target, op.amount, op.bridgeId);
    }

    /**
     * @dev Burn tokens for bridging (owner only)
     * @param from Address to burn tokens from
     * @param amount Amount of tokens to burn
     * @param bridgeId Identifier for the bridge transaction
     */
    function bridgeBurn(address from, uint256 amount, string memory bridgeId) external onlyOwner {
        if (from == address(0)) {
            emit BridgeBurnFailed(from, amount, bridgeId, "Cannot burn from zero address");
            revert("Cannot burn from zero address");
        }
        if (amount == 0) {
            emit BridgeBurnFailed(from, amount, bridgeId, "Amount must be greater than 0");
            revert("Amount must be greater than 0");
        }
        if (balanceOf(from) < amount) {
            emit BridgeBurnFailed(from, amount, bridgeId, "Insufficient balance to burn");
            revert("Insufficient balance to burn");
        }

        _burn(from, amount);
        emit BridgeBurn(from, amount, bridgeId);
    }

    /**
     * @dev Batch mint for multiple addresses (owner only)
     * @param recipients Array of addresses to mint to
     * @param amounts Array of amounts to mint
     * @param bridgeId Identifier for the batch bridge transaction
     */
    function batchBridgeMint(
        address[] memory recipients,
        uint256[] memory amounts,
        string memory bridgeId
    ) external onlyOwner {
        require(recipients.length == amounts.length, "Arrays length mismatch");
        require(recipients.length > 0, "Empty arrays");

        uint256 totalAmount = 0;
        for (uint256 i = 0; i < recipients.length; i++) {
            if (recipients[i] == address(0)) {
                emit BridgeMintFailed(recipients[i], amounts[i], bridgeId, "Cannot mint to zero address");
                revert("Cannot mint to zero address");
            }
            if (amounts[i] == 0) {
                emit BridgeMintFailed(recipients[i], amounts[i], bridgeId, "Amount must be greater than 0");
                revert("Amount must be greater than 0");
            }
            totalAmount += amounts[i];
        }

        if (SUPPLY_CAP > 0) {
            if (totalSupply() + totalAmount > SUPPLY_CAP) {
                emit BridgeMintFailed(address(0), totalAmount, bridgeId, "Exceeds supply cap");
                revert("Exceeds supply cap");
            }
        }

        for (uint256 i = 0; i < recipients.length; i++) {
            _mint(recipients[i], amounts[i]);
            emit BridgeMint(recipients[i], amounts[i], bridgeId);
        }
    }

    /**
     * @dev Commit a source address (sr25519/solana) to an EVM address (owner only)
     * @param sourceHash keccak256 hash of the source address string
     * @param evmAddress Target EVM address
     * @param sourceAddress Original source address string
     * @param sourceType Type of source key (substrate or solana)
     */
    function commit(
        bytes32 sourceHash,
        address evmAddress,
        string memory sourceAddress,
        string memory sourceType
    ) external onlyOwner {
        require(commitments[sourceHash] == address(0), "Already committed");
        require(evmAddress != address(0), "Invalid EVM address");
        commitments[sourceHash] = evmAddress;
        evmCommitments[evmAddress].push(sourceHash);
        emit Commitment(sourceHash, evmAddress, sourceAddress, sourceType);
    }

    /**
     * @dev Get the EVM address committed to a source address hash
     */
    function getCommitment(bytes32 sourceHash) external view returns (address) {
        return commitments[sourceHash];
    }

    /**
     * @dev Get all source address hashes committed to an EVM address
     */
    function getEvmCommitments(address evmAddress) external view returns (bytes32[] memory) {
        return evmCommitments[evmAddress];
    }

    /**
     * @dev Batch burn from multiple addresses (owner only)
     * @param holders Array of addresses to burn from
     * @param amounts Array of amounts to burn
     * @param bridgeId Identifier for the batch bridge transaction
     */
    function batchBridgeBurn(
        address[] memory holders,
        uint256[] memory amounts,
        string memory bridgeId
    ) external onlyOwner {
        require(holders.length == amounts.length, "Arrays length mismatch");
        require(holders.length > 0, "Empty arrays");

        for (uint256 i = 0; i < holders.length; i++) {
            if (holders[i] == address(0)) {
                emit BridgeBurnFailed(holders[i], amounts[i], bridgeId, "Cannot burn from zero address");
                revert("Cannot burn from zero address");
            }
            if (amounts[i] == 0) {
                emit BridgeBurnFailed(holders[i], amounts[i], bridgeId, "Amount must be greater than 0");
                revert("Amount must be greater than 0");
            }
            if (balanceOf(holders[i]) < amounts[i]) {
                emit BridgeBurnFailed(holders[i], amounts[i], bridgeId, "Insufficient balance to burn");
                revert("Insufficient balance to burn");
            }

            _burn(holders[i], amounts[i]);
            emit BridgeBurn(holders[i], amounts[i], bridgeId);
        }
    }
}
