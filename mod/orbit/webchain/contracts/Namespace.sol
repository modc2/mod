// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title Namespace
 * @dev Staketime-priority global namespace for the mod webchain.
 *
 * A *name* (e.g. "foo") resolves to a content pointer (a CID in the localfs
 * store) and is held by whichever address has the highest *staketime weight*
 * locked to it. Weight is the purest reading of "staketime":
 *
 *      weight = amount * lockBlocks          (tokens x time)
 *
 * Priority is absolute (PREEMPTION): a claimant who posts a strictly higher
 * weight than the current holder takes the name immediately, and the previous
 * holder's stake is refunded to them in full (they lose the name, not their
 * tokens). The holder cannot withdraw their own stake until the lock elapses,
 * so weight is always backed by really-locked tokens.
 *
 * Subdomains (e.g. "blog.foo") are PARENT-DELEGATED: only the holder of "foo"
 * may mint names ending in ".foo". Subdomains carry no independent stake; they
 * live and die with the parent and are re-pointable by the parent holder.
 *
 * This contract is intentionally self-contained (its own staking) so it can be
 * deployed against any ERC20 stake token (e.g. the chain module's NativeToken).
 */

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract Namespace {
    struct Name {
        address holder;      // current owner (zero == unclaimed)
        uint256 weight;      // staketime weight = amount * lockBlocks
        uint256 amount;      // tokens locked (0 for subdomains)
        uint256 lockBlocks;  // lock duration in blocks
        uint256 startBlock;  // block the active stake began
        string  cid;         // content pointer (localfs/IPFS CID)
        bool    isSub;       // true for delegated subdomains
        bytes32 parent;      // parent name hash (0x0 for top-level)
    }

    IERC20 public immutable stakeToken;

    mapping(bytes32 => Name) public names;        // nameHash => Name
    mapping(address => bytes32[]) public heldBy;  // holder => name hashes (append-only log)

    event Claimed(bytes32 indexed nameHash, string name, address indexed holder,
                  address indexed preempted, uint256 weight, uint256 amount, uint256 lockBlocks);
    event Released(bytes32 indexed nameHash, address indexed holder, uint256 refund);
    event ContentSet(bytes32 indexed nameHash, address indexed holder, string cid);
    event SubMinted(bytes32 indexed nameHash, bytes32 indexed parent, string name, address indexed holder, string cid);

    constructor(address _stakeToken) {
        stakeToken = IERC20(_stakeToken);
    }

    function hash(string memory name) public pure returns (bytes32) {
        return keccak256(bytes(name));
    }

    // ---------------------------------------------------------------------
    // Top-level claims (staketime preemption)
    // ---------------------------------------------------------------------

    /**
     * @dev Claim or preempt a top-level name by locking `amount` tokens for
     *      `lockBlocks`. Succeeds only if the resulting weight strictly exceeds
     *      the current holder's weight. The preempted holder is refunded.
     */
    function claim(string calldata name, uint256 amount, uint256 lockBlocks)
        external
        returns (uint256 weight)
    {
        require(amount > 0 && lockBlocks > 0, "stake required");
        weight = amount * lockBlocks;

        bytes32 h = hash(name);
        Name storage n = names[h];
        require(!n.isSub, "name is a subdomain");
        require(weight > n.weight, "weight too low to preempt");

        // Pull the new stake in first.
        require(stakeToken.transferFrom(msg.sender, address(this), amount), "stake transfer failed");

        // Refund the preempted holder their locked tokens (name, not tokens).
        address prev = n.holder;
        uint256 prevAmount = n.amount;
        if (prev != address(0) && prevAmount > 0) {
            require(stakeToken.transfer(prev, prevAmount), "refund failed");
        }

        n.holder = msg.sender;
        n.weight = weight;
        n.amount = amount;
        n.lockBlocks = lockBlocks;
        n.startBlock = block.number;
        // cid is preserved across preemption? No — a new holder starts blank.
        n.cid = "";
        n.isSub = false;
        n.parent = bytes32(0);
        heldBy[msg.sender].push(h);

        emit Claimed(h, name, msg.sender, prev, weight, amount, lockBlocks);
    }

    /**
     * @dev Voluntarily give up a name and reclaim the stake. Only after the
     *      lock has elapsed (so posted weight was always honest).
     */
    function release(string calldata name) external {
        bytes32 h = hash(name);
        Name storage n = names[h];
        require(n.holder == msg.sender, "not holder");
        require(!n.isSub, "subdomains have no stake");
        require(block.number >= n.startBlock + n.lockBlocks, "still locked");

        uint256 refund = n.amount;
        delete names[h];
        if (refund > 0) {
            require(stakeToken.transfer(msg.sender, refund), "refund failed");
        }
        emit Released(h, msg.sender, refund);
    }

    // ---------------------------------------------------------------------
    // Content + subdomains
    // ---------------------------------------------------------------------

    /** @dev Point a held name at a content CID. */
    function setContent(string calldata name, string calldata cid) external {
        bytes32 h = hash(name);
        Name storage n = names[h];
        require(n.holder == msg.sender, "not holder");
        n.cid = cid;
        emit ContentSet(h, msg.sender, cid);
    }

    /**
     * @dev Mint/repoint a subdomain `label`.`parent` (e.g. "blog","foo").
     *      Only the parent holder may do this; the subdomain inherits the
     *      parent holder and carries no independent stake.
     */
    function mintSub(string calldata parent, string calldata label, string calldata cid) external {
        bytes32 ph = hash(parent);
        Name storage p = names[ph];
        require(p.holder == msg.sender, "not parent holder");
        require(!p.isSub, "cannot nest under a subdomain");

        string memory full = string(abi.encodePacked(label, ".", parent));
        bytes32 fh = hash(full);
        Name storage s = names[fh];
        require(s.holder == address(0) || s.parent == ph, "name taken");

        s.holder = msg.sender;
        s.cid = cid;
        s.isSub = true;
        s.parent = ph;
        if (s.weight == 0) heldBy[msg.sender].push(fh);
        emit SubMinted(fh, ph, full, msg.sender, cid);
    }

    // ---------------------------------------------------------------------
    // Resolution (views)
    // ---------------------------------------------------------------------

    function resolve(string calldata name)
        external view
        returns (address holder, string memory cid, uint256 weight, bool isSub, bytes32 parent)
    {
        Name storage n = names[hash(name)];
        return (n.holder, n.cid, n.weight, n.isSub, n.parent);
    }

    function weightOf(string calldata name) external view returns (uint256) {
        return names[hash(name)].weight;
    }

    function holderOf(string calldata name) external view returns (address) {
        return names[hash(name)].holder;
    }

    /** @dev Minimum stake required to preempt a name right now (current weight + 1 wei-block). */
    function preemptThreshold(string calldata name) external view returns (uint256) {
        return names[hash(name)].weight;
    }
}
