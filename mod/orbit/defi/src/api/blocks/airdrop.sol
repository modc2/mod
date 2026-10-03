// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Merkle distributor — how a token actually reaches thousands of addresses
/// without thousands of transfers. Publish one 32-byte root, let each claimant
/// pay their own gas to prove membership, and sweep whatever is unclaimed when
/// the window closes.
contract ModMerkleAirdrop is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable token;
    bytes32 public merkleRoot;
    uint256 public deadline;
    uint256 public totalClaimed;
    uint256 public claimCount;

    mapping(uint256 => uint256) internal claimedBitmap;

    event Claimed(uint256 indexed index, address indexed account, uint256 amount);
    event RootUpdated(bytes32 root);
    event Swept(address indexed to, uint256 amount);

    constructor(address token_, bytes32 merkleRoot_, uint256 deadline_, address owner_) Owned(owner_) {
        require(token_ != address(0), "NO_TOKEN");
        token = IERC20(token_);
        merkleRoot = merkleRoot_;
        deadline = deadline_;
    }

    /// Claims are tracked one bit each — 256 claimants per storage slot.
    function isClaimed(uint256 index) public view returns (bool) {
        uint256 word = claimedBitmap[index / 256];
        return word & (1 << (index % 256)) != 0;
    }

    function leaf(uint256 index, address account, uint256 amount) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(index, account, amount));
    }

    function verify(bytes32[] calldata proof, bytes32 node) public view returns (bool) {
        bytes32 computed = node;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 sibling = proof[i];
            computed = computed <= sibling
                ? keccak256(abi.encodePacked(computed, sibling))
                : keccak256(abi.encodePacked(sibling, computed));
        }
        return computed == merkleRoot;
    }

    function claim(uint256 index, address account, uint256 amount, bytes32[] calldata proof) external {
        require(deadline == 0 || block.timestamp <= deadline, "WINDOW_CLOSED");
        require(!isClaimed(index), "ALREADY_CLAIMED");
        require(verify(proof, leaf(index, account, amount)), "BAD_PROOF");
        claimedBitmap[index / 256] |= 1 << (index % 256);
        totalClaimed += amount;
        claimCount += 1;
        token.push(account, amount);
        emit Claimed(index, account, amount);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function fund(uint256 amount) external {
        token.pull(msg.sender, amount);
    }

    /// A root can only be replaced before anyone has claimed against it.
    function setRoot(bytes32 root, uint256 deadline_) external onlyOwner {
        require(claimCount == 0, "CLAIMS_STARTED");
        merkleRoot = root;
        deadline = deadline_;
        emit RootUpdated(root);
    }

    function sweep(address to) external onlyOwner returns (uint256 amount) {
        require(deadline != 0 && block.timestamp > deadline, "WINDOW_OPEN");
        amount = token.balanceOf(address(this));
        token.push(to, amount);
        emit Swept(to, amount);
    }
}
