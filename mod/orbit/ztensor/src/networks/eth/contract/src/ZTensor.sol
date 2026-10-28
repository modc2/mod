// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {LSAG} from "./LSAG.sol";
import {Poseidon} from "./Poseidon.sol";

/// @title ZTensor: an anonymous subnet on Ethereum
/// @notice The same subnet as starknet/contract/src/ztensor.cairo (Starknet) and
/// near/contract/src/lib.rs (NEAR), verifying the SAME ring signatures. Two public
/// sets of STARK-curve keys, MINERS and VALIDATORS; every action is an LSAG
/// ring signature over members of one role, so it proves "some member" and
/// nothing more:
///
///   submit(ring, work, sig)            key image under (subnet, epoch) = TAG
///   score(ring, tag, score, sig)       key image under (subnet, epoch, tag)
///                                      = nullifier: one score per validator
///   settle(epoch, tag)                 anyone, once the epoch closed: Yuma
///                                      consensus C and clipped rank R
///   claim(epoch, ring, recipient, sig) same scope as submit, same image:
///                                      credits emission * R / sum R
///
/// Yuma (core/yuma.py is the reference): every validator weighs 1, kappa
/// 1/2. V = validator count at the epoch's first submit; for a tag scored n
/// times, Vt = max(V, n), need = ceil(Vt/2); C = the highest s that `need`
/// validators scored at or above (0 if fewer than `need` scored it at all);
/// R = sum of min(s_v, C). claim() waits until every tag of the epoch is
/// settled.
///
/// Chain-specific bits, and nothing else differs from the Cairo contract:
///   subnet    = address(this) as a felt (160 bits, always < p)
///   epoch     = block.number / tempo
///   recipient = an address; the signed message is uint160(recipient)
///   Hp(scope) = a checked hint in the signature (no sqrt on the EVM)
///
/// No call checks msg.sender except owner admin, so a relayer posts on a
/// member's behalf (the tx sender is public on Ethereum).
contract ZTensor {
    using LSAG for LSAG.Point;

    uint8 public constant ROLE_MINER = 1;
    uint8 public constant ROLE_VALIDATOR = 2;
    /// Gas-bound, not a protocol choice: ~0.41M gas per ring member, so a
    /// ring of 32 is ~13.4M gas, inside the 2^24 per-transaction gas cap
    /// (EIP-7825). Same cap as NEAR; Starknet allows 64.
    uint32 public constant MAX_RING = 32;
    uint8 public constant MAX_SCORE = 100;
    uint256 internal constant TAG_MINE = 0x7a756d612d6d696e65; // 'zuma-mine'
    uint256 internal constant TAG_SCORE = 0x7a756d612d73636f7265; // 'zuma-score'

    address public owner;
    bool public open;
    uint64 public tempo;
    uint32 public minRing;
    uint128 public emission;

    mapping(uint256 => uint8) public roleOfId; // key_id -> role
    LSAG.Point[] internal miners;
    LSAG.Point[] internal validators;
    mapping(uint64 => mapping(uint256 => uint256)) public workOf; // (epoch, tag) -> work
    mapping(uint64 => mapping(uint256 => bool)) public submitted;
    mapping(uint64 => uint256[]) internal tags;
    mapping(uint64 => mapping(uint256 => uint128)) public scoreSum;
    mapping(uint64 => mapping(uint256 => uint32)) public scoreN;
    mapping(uint64 => uint128) public epochTotal;
    mapping(uint256 => bool) public spent; // validator nullifiers
    mapping(uint64 => mapping(uint256 => bool)) public claimed;
    mapping(address => uint128) public earned;
    // ---- Yuma ----
    mapping(uint64 => mapping(uint256 => mapping(uint8 => uint32))) public hist; // (e, tag, s) -> votes
    mapping(uint64 => mapping(uint256 => uint8)) public top; // (e, tag) -> highest score
    mapping(uint64 => uint32) public vset; // validators at the epoch's first submit
    mapping(uint64 => mapping(uint256 => uint8)) public consensus;
    mapping(uint64 => mapping(uint256 => uint128)) public rank;
    mapping(uint64 => mapping(uint256 => bool)) public settled;
    mapping(uint64 => uint128) public rankTotal;
    mapping(uint64 => uint32) public nsettled;

    event Registered(uint8 indexed role, uint256 keyId, uint32 index);
    event Submitted(uint64 indexed epoch, uint256 tag, uint256 work, uint32 ringSize);
    event Scored(uint64 indexed epoch, uint256 tag, uint8 score, uint256 nullifier, uint32 ringSize);
    event Settled(uint64 indexed epoch, uint256 tag, uint8 consensus, uint128 rank);
    event Claimed(uint64 indexed epoch, uint256 tag, address recipient, uint128 amount);

    constructor(address owner_, uint64 tempo_, uint32 minRing_, uint128 emission_) {
        require(tempo_ > 0, "tempo must be > 0");
        require(minRing_ > 0 && minRing_ <= MAX_RING, "bad min_ring");
        owner = owner_;
        tempo = tempo_;
        minRing = minRing_;
        emission = emission_;
    }

    // ---- registry ----------------------------------------------------------

    function register(uint8 role, LSAG.Point calldata key) external {
        require(role == ROLE_MINER || role == ROLE_VALIDATOR, "role is 1 or 2");
        require(open || msg.sender == owner, "registry closed");
        LSAG.Point memory k = key;
        require(k.isOnCurve(), "key not on curve");
        uint256 id = k.keyId();
        require(roleOfId[id] == 0, "key already registered");
        roleOfId[id] = role;
        LSAG.Point[] storage set = role == ROLE_MINER ? miners : validators;
        set.push(k);
        emit Registered(role, id, uint32(set.length - 1));
    }

    function setOpen(bool open_) external {
        require(msg.sender == owner, "only owner");
        open = open_;
    }

    function transferOwnership(address owner_) external {
        require(msg.sender == owner, "only owner");
        owner = owner_;
    }

    // ---- anonymous actions -------------------------------------------------

    function submit(LSAG.Point[] calldata ring, uint256 work, LSAG.Signature calldata sig)
        external
        returns (uint256 tag)
    {
        uint256 n = _checkRing(ring, ROLE_MINER);
        uint64 e = epoch();
        require(_verify(ring, mineScope(e), work, sig), "bad ring signature");
        tag = LSAG.keyId(sig.image);
        require(!submitted[e][tag], "already submitted this epoch");
        submitted[e][tag] = true;
        workOf[e][tag] = work;
        // the validator set that counts for this epoch's consensus
        if (tags[e].length == 0) vset[e] = uint32(validators.length);
        tags[e].push(tag);
        emit Submitted(e, tag, work, uint32(n));
    }

    function score(LSAG.Point[] calldata ring, uint256 tag, uint8 score_, LSAG.Signature calldata sig)
        external
        returns (uint256 nullifier)
    {
        require(score_ <= MAX_SCORE, "score is 0..100");
        uint256 n = _checkRing(ring, ROLE_VALIDATOR);
        uint64 e = epoch();
        require(submitted[e][tag], "no such tag this epoch");
        require(_verify(ring, scoreScope(e, tag), score_, sig), "bad ring signature");
        nullifier = LSAG.keyId(sig.image);
        require(!spent[nullifier], "already scored this tag");
        spent[nullifier] = true;
        scoreSum[e][tag] += score_;
        scoreN[e][tag] += 1;
        epochTotal[e] += score_;
        hist[e][tag][score_] += 1;
        if (score_ > top[e][tag]) top[e][tag] = score_;
        emit Scored(e, tag, score_, nullifier, uint32(n));
    }

    /// @notice Yuma for one tag of a closed epoch: walk the score histogram
    /// from the top down until `need` validators are covered. Anyone, once.
    function settle(uint64 epoch_, uint256 tag) external returns (uint8 c, uint128 r) {
        require(epoch_ < epoch(), "epoch still open");
        require(submitted[epoch_][tag], "no such tag this epoch");
        require(!settled[epoch_][tag], "already settled");
        uint256 n = scoreN[epoch_][tag];
        uint256 v = vset[epoch_];
        if (n > v) v = n;
        uint256 need = (v + 1) / 2;
        if (need != 0 && n >= need) {
            mapping(uint8 => uint32) storage h = hist[epoch_][tag];
            uint256 above;
            uint256 mass;
            for (uint256 s = uint256(top[epoch_][tag]) + 1; s > 0;) {
                s--;
                uint256 k = h[uint8(s)];
                above += k;
                mass += k * s;
                if (above >= need) {
                    c = uint8(s);
                    r = uint128(scoreSum[epoch_][tag] - mass + s * above);
                    break;
                }
            }
        }
        settled[epoch_][tag] = true;
        consensus[epoch_][tag] = c;
        rank[epoch_][tag] = r;
        rankTotal[epoch_] += r;
        nsettled[epoch_] += 1;
        emit Settled(epoch_, tag, c, r);
    }

    function claim(uint64 epoch_, LSAG.Point[] calldata ring, address recipient, LSAG.Signature calldata sig)
        external
        returns (uint128 amount)
    {
        require(epoch_ < epoch(), "epoch still open");
        _checkRing(ring, ROLE_MINER);
        require(_verify(ring, mineScope(epoch_), uint256(uint160(recipient)), sig), "bad ring signature");
        uint256 tag = LSAG.keyId(sig.image);
        require(submitted[epoch_][tag], "tag did not submit");
        require(nsettled[epoch_] == tags[epoch_].length, "epoch not settled");
        require(!claimed[epoch_][tag], "already claimed");
        claimed[epoch_][tag] = true;
        uint256 total = rankTotal[epoch_];
        if (total != 0) {
            amount = uint128(uint256(emission) * rank[epoch_][tag] / total);
        }
        earned[recipient] += amount;
        emit Claimed(epoch_, tag, recipient, amount);
    }

    // ---- views -------------------------------------------------------------

    function epoch() public view returns (uint64) {
        return uint64(block.number / tempo);
    }

    function subnet() public view returns (uint256) {
        return uint256(uint160(address(this)));
    }

    function mineScope(uint64 epoch_) public view returns (uint256) {
        return Poseidon.hash3(TAG_MINE, subnet(), epoch_);
    }

    function scoreScope(uint64 epoch_, uint256 tag) public view returns (uint256) {
        return Poseidon.hash4(TAG_SCORE, subnet(), epoch_, tag);
    }

    function roleOf(LSAG.Point calldata key) external view returns (uint8) {
        LSAG.Point memory k = key;
        return roleOfId[k.keyId()];
    }

    function memberCount(uint8 role) external view returns (uint256) {
        return (role == ROLE_MINER ? miners : validators).length;
    }

    function member(uint8 role, uint256 index) external view returns (LSAG.Point memory) {
        return (role == ROLE_MINER ? miners : validators)[index];
    }

    function members(uint8 role) external view returns (LSAG.Point[] memory) {
        return role == ROLE_MINER ? miners : validators;
    }

    function tally(uint64 epoch_, uint256 tag) external view returns (uint128, uint32) {
        return (scoreSum[epoch_][tag], scoreN[epoch_][tag]);
    }

    /// @notice (consensus, rank, settled) of one tag.
    function yuma(uint64 epoch_, uint256 tag) external view returns (uint8, uint128, bool) {
        return (consensus[epoch_][tag], rank[epoch_][tag], settled[epoch_][tag]);
    }

    /// @notice (validator set, rank total, tags settled) of one epoch.
    function yumaEpoch(uint64 epoch_) external view returns (uint32, uint128, uint32) {
        return (vset[epoch_], rankTotal[epoch_], nsettled[epoch_]);
    }

    function epochTags(uint64 epoch_) external view returns (uint256) {
        return tags[epoch_].length;
    }

    function tagAt(uint64 epoch_, uint256 index) external view returns (uint256) {
        return tags[epoch_][index];
    }

    function tagsOf(uint64 epoch_) external view returns (uint256[] memory) {
        return tags[epoch_];
    }

    function params() external view returns (address, uint64, uint32, uint128, bool) {
        return (owner, tempo, minRing, emission, open);
    }

    /// @notice Off-chain-style check of a signature in any scope (no state).
    function verify(LSAG.Point[] calldata ring, uint256 scope, uint256 msg_, LSAG.Signature calldata sig)
        external
        view
        returns (bool)
    {
        return _verify(ring, scope, msg_, sig);
    }

    /// @notice Stable id of a key (what rings are sorted by).
    function keyIdOf(LSAG.Point calldata key) external view returns (uint256) {
        LSAG.Point memory k = key;
        return k.keyId();
    }

    // ---- internal ----------------------------------------------------------

    function _verify(LSAG.Point[] calldata ring, uint256 scope, uint256 msg_, LSAG.Signature calldata sig)
        internal
        view
        returns (bool)
    {
        LSAG.Point[] memory r = ring;
        LSAG.Signature memory s = sig;
        return LSAG.verify(r, scope, msg_, s);
    }

    /// @dev Ring = strictly increasing key ids, all registered with `role`.
    function _checkRing(LSAG.Point[] calldata ring, uint8 role) internal view returns (uint256 n) {
        n = ring.length;
        require(n >= minRing, "ring below min_ring");
        require(n <= MAX_RING, "ring above MAX_RING");
        uint256 prev;
        for (uint256 i = 0; i < n; i++) {
            uint256 id = LSAG.keyId(ring[i]);
            require(roleOfId[id] == role, "ring has a non-member");
            require(i == 0 || id > prev, "ring not sorted/unique");
            prev = id;
        }
    }
}
