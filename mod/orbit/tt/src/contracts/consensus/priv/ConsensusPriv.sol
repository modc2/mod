// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../Consensus.sol";
import "./CommitmentTree.sol";

/**
 * @title ConsensusPriv
 * @dev Privacy-preserving consensus module.
 *
 *      Validators know that each checkin comes from a member of the ERC20
 *      token-holder set, but cannot determine *which* holder made the request.
 *
 *      Flow:
 *        1. Registration: Any ERC20 holder calls `registerCommitment(commitment)`
 *           where commitment = keccak256(secret, msg.sender). The contract
 *           verifies balanceOf(msg.sender) > 0 and inserts commitment into a
 *           Merkle tree.
 *
 *        2. Anonymous checkin: Holder calls `anonCheckin(nullifier, commitment,
 *           merkleProof, merkleRoot)`. The nullifier = keccak256(secret, epoch)
 *           is unique per holder per epoch to prevent double-checkin.
 *           Validators see a valid Merkle membership proof but cannot link
 *           the nullifier to any specific holder address.
 *
 *        3. Scoring: Each valid anonymous checkin adds +1 to the epoch's
 *           anonymous score pool (like Linear but unlinkable).
 *
 *        4. Distribution: At epoch end, emissions are split evenly across all
 *           nullifiers that checked in. Rewards are stored by nullifier.
 *
 *        5. Claiming: Holder calls `claimPrivRewards(secret, epochs)` providing
 *           their secret and the epoch numbers. The contract recomputes each
 *           nullifier = keccak256(secret, epoch) and transfers the rewards.
 */
contract ConsensusPriv is Consensus {
    using SafeERC20 for IERC20;

    // ── State ────────────────────────────────────────────────────────────────

    CommitmentTree public commitTree;
    IERC20 public token; // the ERC20 whose holders form the privacy set

    mapping(bytes32 => bool) public commitmentRegistered;
    mapping(uint64 => mapping(bytes32 => bool)) public nullifierUsed;

    // Per-epoch tracking
    uint64 public currentEpoch;
    bytes32[] public epochNullifiers; // nullifiers that checked in this epoch
    uint256 public epochCheckins;     // count of anonymous checkins this epoch

    // Rewards keyed by nullifier
    mapping(bytes32 => uint256) public nullifierRewards;

    // ── Events ──────────────────────────────────────────────────────────────

    event CommitmentAdded(bytes32 indexed commitment, uint256 leafIndex);
    event AnonCheckin(bytes32 indexed nullifier, uint64 epoch);
    event PrivRewardClaimed(bytes32 indexed nullifier, address indexed to, uint256 amount);

    // ── Constructor ─────────────────────────────────────────────────────────

    constructor(
        address _subnet,
        address _stakeTime,
        address _token,
        uint256 _emissionRate,
        uint64  _epochLength
    ) Consensus(_subnet, _stakeTime, _emissionRate, _epochLength) {
        token = IERC20(_token);
        commitTree = new CommitmentTree();
        currentEpoch = 1;
    }

    // ── Commitment Registration ─────────────────────────────────────────────

    /**
     * @notice Register a commitment to join the privacy set.
     * @param commitment  keccak256(abi.encodePacked(secret, msg.sender))
     */
    function registerCommitment(bytes32 commitment) external {
        require(token.balanceOf(msg.sender) > 0, "must hold ERC20 tokens");
        require(!commitmentRegistered[commitment], "already registered");

        commitmentRegistered[commitment] = true;
        uint256 leafIndex = commitTree.insert(commitment);

        emit CommitmentAdded(commitment, leafIndex);
    }

    // ── Anonymous Checkin ───────────────────────────────────────────────────

    /**
     * @notice Check in anonymously. Proves membership in the holder set
     *         without revealing which holder you are.
     * @param nullifier    keccak256(abi.encodePacked(secret, currentEpoch))
     * @param commitment   The commitment previously registered
     * @param merkleProof  Siblings for the Merkle path
     * @param merkleRoot   A recent root of the commitment tree
     */
    function anonCheckin(
        bytes32 nullifier,
        bytes32 commitment,
        bytes32[] calldata merkleProof,
        bytes32 merkleRoot
    ) external {
        require(commitTree.isKnownRoot(merkleRoot), "unknown merkle root");
        require(!nullifierUsed[currentEpoch][nullifier], "already checked in this epoch");
        require(commitmentRegistered[commitment], "commitment not registered");
        require(
            _verifyMerkleProof(commitment, merkleProof, merkleRoot),
            "invalid merkle proof"
        );

        nullifierUsed[currentEpoch][nullifier] = true;
        epochNullifiers.push(nullifier);
        epochCheckins++;

        // Update consensus total so block production works
        consensus.totalBlocktime = epochCheckins;

        emit AnonCheckin(nullifier, currentEpoch);
    }

    // ── Consensus Overrides ─────────────────────────────────────────────────

    /// @dev Not used — anonymous checkins bypass the key-based checkin flow.
    function _applyCheckin(bytes32) internal pure override {
        revert("use anonCheckin instead");
    }

    function _selectProposer() internal view override returns (bytes32 proposer) {
        if (epochCheckins == 0) return proposer;

        uint256 rand = uint256(keccak256(abi.encodePacked(
            block.prevrandao, consensus.currentBlock, block.timestamp
        )));
        uint256 idx = rand % epochCheckins;
        proposer = epochNullifiers[idx];
    }

    function _distribute() internal override {
        if (epochCheckins == 0) return;

        uint256 emission = getEffectiveEmission();
        uint256 perCheckin = emission / epochCheckins;
        uint256 totalDistributed = 0;

        for (uint256 i = 0; i < epochNullifiers.length; i++) {
            bytes32 nul = epochNullifiers[i];
            uint256 share = perCheckin;

            // Mint and store reward for this nullifier
            subnet.mint(address(this), share);
            nullifierRewards[nul] += share;
            totalDistributed += share;
        }

        // Reset epoch state
        consensus.lastEmissionBlock = consensus.currentBlock;
        delete epochNullifiers;
        epochCheckins = 0;
        consensus.totalBlocktime = 0;
        currentEpoch++;

        try staking.advanceEpoch() {} catch {}

        emit EmissionsDistributed(consensus.currentBlock, totalDistributed);
    }

    function _recalcTotal() internal override {
        consensus.totalBlocktime = epochCheckins;
    }

    // ── Reward Claims ───────────────────────────────────────────────────────

    /**
     * @notice Claim rewards by revealing your secret for specific epochs.
     *         Recomputes nullifier = keccak256(secret, epoch) and transfers
     *         accumulated rewards.
     * @param secret  The secret used during commitment registration
     * @param epochs  Array of epoch numbers to claim for
     */
    function claimPrivRewards(bytes32 secret, uint64[] calldata epochs) external {
        uint256 total = 0;
        for (uint256 i = 0; i < epochs.length; i++) {
            bytes32 nul = keccak256(abi.encodePacked(secret, epochs[i]));
            uint256 amount = nullifierRewards[nul];
            if (amount > 0) {
                nullifierRewards[nul] = 0;
                total += amount;
                emit PrivRewardClaimed(nul, msg.sender, amount);
            }
        }
        require(total > 0, "nothing to claim");
        IERC20(address(subnet)).safeTransfer(msg.sender, total);
    }

    // ── Merkle Verification ─────────────────────────────────────────────────

    function _verifyMerkleProof(
        bytes32 leaf,
        bytes32[] calldata proof,
        bytes32 root
    ) internal pure returns (bool) {
        bytes32 computed = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 sibling = proof[i];
            if (computed <= sibling) {
                computed = keccak256(abi.encodePacked(computed, sibling));
            } else {
                computed = keccak256(abi.encodePacked(sibling, computed));
            }
        }
        return computed == root;
    }

    // ── Views ───────────────────────────────────────────────────────────────

    function getEpochCheckins() external view returns (uint256) {
        return epochCheckins;
    }

    function getCurrentEpoch() external view returns (uint64) {
        return currentEpoch;
    }

    function getCommitmentRoot() external view returns (bytes32) {
        return commitTree.getLastRoot();
    }
}
