// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {OHX} from "./OHX.sol";

/// @notice A STARK fact registry (StarkWare's SHARP publishes Cairo program
/// facts as keccak256(programHash ‖ keccak256(outputs))). Optional door.
interface IFactRegistry {
    function isValid(bytes32 fact) external view returns (bool);
}

/// @title OpenHouseProofs — proof of rent paid, without the tenant.
///
/// The bank sees the payment. The chain only needs to know THAT property P
/// was paid for period T. The bank runs proof/src/lib.cairo over the private
/// facts (tenant, salt, amount) and publishes six public outputs:
///
///   [version, propertyId, period, rentDue, commitment, nullifier]
///
/// commitment = Poseidon(tenant, salt) — opens only to someone holding the
/// salt; nullifier = Poseidon(commitment, propertyId, period) — one proof
/// books one month once. This contract never runs Cairo. It checks a FACT,
/// keccak256(programHash ‖ keccak256(outputs)), carried one of two ways:
///
///   submit(outputs, sig)        the bank signed the fact (ECDSA, personal_sign)
///   submitPQ(outputs, key, sig) the bank signed the fact with OHX-KECCAK, a
///                               hash-based post-quantum signature (OHX.sol)
///   submitFact(outputs)         a STARK of the same program landed in a fact
///                               registry — same fact, same outputs, no new ABI
///
/// Post-quantum: ECDSA falls to Shor; keccak and STARKs do not. The owner can
/// call requirePQ() — one way, no undo — after which the ECDSA door is shut
/// and only the hash-based doors (submitPQ, submitFact) book rent.
///
/// Both doors pin the program hash, require the attested rent due to cover
/// the property's, refuse a spent nullifier and a period already proven.
/// Nothing about the tenant is stored, emitted or needed.
contract OpenHouseProofs {
    uint256 public constant VERSION = 1;
    uint256 public constant N_OUTPUTS = 6;
    uint8 public constant KIND_ATTESTED = 1;
    uint8 public constant KIND_STARK = 2;
    uint8 public constant KIND_PQ = 3;

    address public owner;
    bytes32 public programHash;
    IFactRegistry public registry;
    /// Once true, never false: the ECDSA door is closed for good.
    bool public pqRequired;

    struct Bank { string name; bool active; uint64 since; }
    /// A bank's post-quantum key, keyed by OHX.keyId(pubSeed, root).
    struct PQBank { string name; bool active; uint64 since; bytes32 pubSeed; bytes32 root; }
    struct Property { address owner; uint256 rentDue; bool exists; string label; }
    struct Receipt {
        bytes32 commitment;
        bytes32 nullifier;
        bytes32 fact;
        address bank;      // the signer, or the registry for STARK facts
        uint256 rentDue;   // as attested
        uint64 at;
        uint8 kind;
        bytes32 pqKey;     // the OHX key id for KIND_PQ, else 0
    }

    mapping(address => Bank) public banks;
    mapping(uint256 => Property) public properties;
    mapping(bytes32 => bool) public spent;
    mapping(bytes32 => PQBank) public pqBanks;
    /// Every one-time leaf a PQ key has spent here. A leaf signs once; a
    /// second signature from the same leaf is refused even if it verifies.
    mapping(bytes32 => mapping(uint256 => bool)) public leafUsed;
    mapping(uint256 => mapping(uint256 => Receipt)) private _receipts;
    uint256 public count;

    event OwnerSet(address owner);
    event ProgramHashSet(bytes32 programHash);
    event RegistrySet(address registry);
    event BankSet(address indexed bank, string name, bool active);
    event PQBankSet(bytes32 indexed key, bytes32 pubSeed, bytes32 root, string name, bool active);
    event PQRequired();
    event PQLeafSpent(bytes32 indexed key, uint256 leaf);
    event PropertyRegistered(uint256 indexed id, address owner, uint256 rentDue, string label);
    event RentDueSet(uint256 indexed id, uint256 rentDue);
    event RentProven(
        uint256 indexed propertyId, uint256 indexed period,
        bytes32 commitment, bytes32 nullifier, bytes32 fact, address bank, uint8 kind
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(address _owner, bytes32 _programHash, address _registry) {
        require(_owner != address(0), "owner required");
        owner = _owner;
        programHash = _programHash;
        registry = IFactRegistry(_registry);
        emit OwnerSet(_owner);
        emit ProgramHashSet(_programHash);
        emit RegistrySet(_registry);
    }

    // ── admin ──────────────────────────────────────────────────

    function transferOwner(address next) external onlyOwner {
        require(next != address(0), "owner required");
        owner = next;
        emit OwnerSet(next);
    }

    function setProgramHash(bytes32 h) external onlyOwner {
        programHash = h;
        emit ProgramHashSet(h);
    }

    function setRegistry(address r) external onlyOwner {
        registry = IFactRegistry(r);
        emit RegistrySet(r);
    }

    function setBank(address bank, string calldata name, bool active) external onlyOwner {
        require(bank != address(0), "bank required");
        Bank storage b = banks[bank];
        b.name = name;
        b.active = active;
        if (b.since == 0) b.since = uint64(block.timestamp);
        emit BankSet(bank, name, active);
    }

    /// Seat (or deactivate) a bank's OHX-KECCAK key. Returns its id.
    function setPQBank(bytes32 pubSeed, bytes32 root, string calldata name, bool active)
        external onlyOwner returns (bytes32 key)
    {
        require(pubSeed != 0 && root != 0, "pq key required");
        key = OHX.keyId(pubSeed, root);
        PQBank storage b = pqBanks[key];
        b.name = name;
        b.active = active;
        b.pubSeed = pubSeed;
        b.root = root;
        if (b.since == 0) b.since = uint64(block.timestamp);
        emit PQBankSet(key, pubSeed, root, name, active);
    }

    /// Q-day switch. One way: after this the ECDSA door never opens again,
    /// even for a forged owner key, because there is no setter back.
    function requirePQ() external onlyOwner {
        require(!pqRequired, "already required");
        pqRequired = true;
        emit PQRequired();
    }

    function registerProperty(uint256 id, address pOwner, uint256 rentDue, string calldata label)
        external onlyOwner
    {
        require(!properties[id].exists, "property exists");
        require(rentDue > 0, "rentDue required");
        properties[id] = Property(pOwner, rentDue, true, label);
        emit PropertyRegistered(id, pOwner, rentDue, label);
    }

    /// The protocol owner or the property's own owner may re-price it.
    function setRentDue(uint256 id, uint256 rentDue) external {
        Property storage p = properties[id];
        require(p.exists, "unknown property");
        require(msg.sender == owner || msg.sender == p.owner, "not owner");
        require(rentDue > 0, "rentDue required");
        p.rentDue = rentDue;
        emit RentDueSet(id, rentDue);
    }

    // ── the fact ───────────────────────────────────────────────

    /// keccak256(programHash ‖ keccak256(outputs)) — each output a 32-byte word.
    function fact(uint256[] calldata outputs) public view returns (bytes32) {
        return keccak256(abi.encodePacked(programHash, keccak256(abi.encodePacked(outputs))));
    }

    /// Every rule but the signature, as a reason ("" = fine). A view so a
    /// relayer can ask before spending gas.
    function check(uint256[] calldata o) public view returns (string memory) {
        if (o.length != N_OUTPUTS) return "wrong output count";
        if (o[0] != VERSION) return "wrong statement version";
        Property storage p = properties[o[1]];
        if (!p.exists) return "unknown property";
        if (o[3] < p.rentDue) return "attested rent due below the property's";
        if (o[4] == 0) return "empty commitment";
        if (spent[bytes32(o[5])]) return "nullifier already used";
        if (_receipts[o[1]][o[2]].at != 0) return "period already proven";
        return "";
    }

    /// personal_sign over the 32-byte fact: who signed it (0 if malformed).
    function recover(bytes32 f, bytes calldata sig) public pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", f));
        return ecrecover(digest, v, r, s);
    }

    /// Dry run of submit(): never reverts, says why it would.
    function verify(uint256[] calldata o, bytes calldata sig)
        external view returns (bool ok, string memory why, bytes32 f, address signer)
    {
        why = check(o);
        f = fact(o);
        signer = recover(f, sig);
        if (bytes(why).length == 0 && pqRequired) why = "classical door closed: post-quantum signature required";
        if (bytes(why).length == 0 && !banks[signer].active) why = "signer is not an active bank";
        ok = bytes(why).length == 0;
    }

    // ── the two doors ──────────────────────────────────────────

    /// The bank attested: it ran the program and signed the fact.
    function submit(uint256[] calldata o, bytes calldata sig) external returns (bytes32 f) {
        require(!pqRequired, "classical door closed: post-quantum signature required");
        string memory why = check(o);
        require(bytes(why).length == 0, why);
        f = fact(o);
        address bank = recover(f, sig);
        require(banks[bank].active, "signer is not an active bank");
        _record(o, f, bank, KIND_ATTESTED, 0);
    }

    /// Every PQ rule as a reason ("" = fine). A view, never reverts.
    function verifyPQ(uint256[] calldata o, bytes32 key, bytes calldata sig)
        public view returns (bool ok, string memory why, bytes32 f)
    {
        why = check(o);
        f = fact(o);
        if (bytes(why).length == 0) why = _pqWhy(f, key, sig);
        ok = bytes(why).length == 0;
    }

    function _pqWhy(bytes32 f, bytes32 key, bytes calldata sig) internal view returns (string memory) {
        PQBank storage b = pqBanks[key];
        if (!b.active) return "not an active post-quantum bank key";
        uint256 leaf = OHX.leafOf(sig);
        if (leaf >= OHX.CAPACITY) return "malformed post-quantum signature";
        if (leafUsed[key][leaf]) return "one-time leaf already used";
        if (!OHX.verify(b.pubSeed, b.root, f, sig)) return "bad post-quantum signature";
        return "";
    }

    /// The bank attested with a hash-based signature — survives a quantum
    /// computer. The leaf is burned so the one-time key can never sign twice here.
    function submitPQ(uint256[] calldata o, bytes32 key, bytes calldata sig) external returns (bytes32 f) {
        string memory why = check(o);
        require(bytes(why).length == 0, why);
        f = fact(o);
        why = _pqWhy(f, key, sig);
        require(bytes(why).length == 0, why);
        uint256 leaf = OHX.leafOf(sig);
        leafUsed[key][leaf] = true;
        emit PQLeafSpent(key, leaf);
        _record(o, f, address(0), KIND_PQ, key);
    }

    /// A STARK of the same program was verified on-chain: the registry
    /// holds the fact. No bank signature needed — the proof is the bank.
    function submitFact(uint256[] calldata o) external returns (bytes32 f) {
        require(address(registry) != address(0), "no fact registry");
        string memory why = check(o);
        require(bytes(why).length == 0, why);
        f = fact(o);
        require(registry.isValid(f), "fact not proven");
        _record(o, f, address(registry), KIND_STARK, 0);
    }

    function _record(uint256[] calldata o, bytes32 f, address bank, uint8 kind, bytes32 pqKey) internal {
        bytes32 nullifier = bytes32(o[5]);
        spent[nullifier] = true;
        _receipts[o[1]][o[2]] = Receipt(bytes32(o[4]), nullifier, f, bank, o[3], uint64(block.timestamp), kind, pqKey);
        count += 1;
        emit RentProven(o[1], o[2], bytes32(o[4]), nullifier, f, bank, kind);
    }

    // ── reads ──────────────────────────────────────────────────

    function receipt(uint256 propertyId, uint256 period) external view returns (Receipt memory) {
        return _receipts[propertyId][period];
    }

    function paid(uint256 propertyId, uint256 period) external view returns (bool) {
        return _receipts[propertyId][period].at != 0;
    }
}
