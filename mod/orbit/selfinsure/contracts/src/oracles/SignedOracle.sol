// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ISelfInsureOracle} from "../SelfInsure.sol";

/// @title SignedOracle — real-world data, signed by someone accountable for it.
/// @notice One oracle contract serves any number of pools. A REPORTER is an
///         address the oracle's owner has named on chain: a hospital billing
///         system, a claims auditor, a Chainlink Functions consumer, a
///         pharmacy benefit feed. A reporter attests to a claim by signing
///         (pool, claimId, ok, verifiedAmount, dataHash, expiry) and anyone may
///         relay that signature here — the claimant, the pool operator, a bot.
///         A reporter that is itself a contract calls `report` directly.
///
///         `dataHash` is the keccak256 of the underlying record (the itemised
///         bill, the EOB, the procedure-price lookup) so the data behind every
///         payout can be published and checked against the chain later.
///         Everything written here is public and permanent: who attested, when,
///         to what amount, under which reporter key.
contract SignedOracle is ISelfInsureOracle {
    struct Attestation {
        bool    attested;
        bool    ok;
        uint256 verifiedAmount;
        bytes32 dataHash;
        uint64  at;
        address reporter;
        string  source;         // human label: "Mercy General billing v2", "CMS fee schedule 2026"
    }

    address public owner;
    mapping(address => bool) public reporters;
    mapping(address => string) public reporterLabel;
    address[] public reporterList;
    mapping(address => mapping(uint256 => Attestation)) private _atts;
    uint256 public attestations;

    event ReporterSet(address indexed reporter, bool allowed, string label);
    event Attested(address indexed pool, uint256 indexed claimId, address indexed reporter,
                   bool ok, uint256 verifiedAmount, bytes32 dataHash, string source);
    event OwnerChanged(address indexed from, address indexed to);

    error NotOwner();
    error NotReporter(address who);
    error Expired(uint64 expiry);
    error BadSignature();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address owner_) {
        owner = owner_ == address(0) ? msg.sender : owner_;
    }

    function setReporter(address r, bool allowed, string calldata label) external onlyOwner {
        if (allowed && !reporters[r]) reporterList.push(r);
        reporters[r] = allowed;
        reporterLabel[r] = label;
        emit ReporterSet(r, allowed, label);
    }

    function transferOwnership(address to) external onlyOwner {
        emit OwnerChanged(owner, to);
        owner = to;
    }

    /// The message a reporter signs. Bound to this chain and this oracle so a
    /// signature cannot be replayed elsewhere.
    function digest(address pool, uint256 claimId, bool ok, uint256 verifiedAmount,
                    bytes32 dataHash, uint64 expiry) public view returns (bytes32)
    {
        bytes32 inner = keccak256(abi.encode(
            block.chainid, address(this), pool, claimId, ok, verifiedAmount, dataHash, expiry));
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", inner));
    }

    /// Relay a reporter's signed attestation. Anyone can call this.
    function submit(address pool, uint256 claimId, bool ok, uint256 verifiedAmount,
                    bytes32 dataHash, uint64 expiry, string calldata source, bytes calldata sig)
        external
    {
        if (expiry != 0 && block.timestamp > expiry) revert Expired(expiry);
        address signer = _recover(digest(pool, claimId, ok, verifiedAmount, dataHash, expiry), sig);
        if (!reporters[signer]) revert NotReporter(signer);
        _write(pool, claimId, ok, verifiedAmount, dataHash, signer, source);
    }

    /// A reporter that is a contract (a Chainlink Functions consumer, say)
    /// reports directly.
    function report(address pool, uint256 claimId, bool ok, uint256 verifiedAmount,
                    bytes32 dataHash, string calldata source) external
    {
        if (!reporters[msg.sender]) revert NotReporter(msg.sender);
        _write(pool, claimId, ok, verifiedAmount, dataHash, msg.sender, source);
    }

    function _write(address pool, uint256 claimId, bool ok, uint256 amount, bytes32 h,
                    address reporter, string calldata source) private
    {
        Attestation storage a = _atts[pool][claimId];
        if (!a.attested) attestations += 1;
        a.attested = true;
        a.ok = ok;
        a.verifiedAmount = amount;
        a.dataHash = h;
        a.at = uint64(block.timestamp);
        a.reporter = reporter;
        a.source = source;
        emit Attested(pool, claimId, reporter, ok, amount, h, source);
    }

    function attestation(address pool, uint256 claimId)
        external view override
        returns (bool attested, bool ok, uint256 verifiedAmount, bytes32 dataHash, uint64 at)
    {
        Attestation storage a = _atts[pool][claimId];
        return (a.attested, a.ok, a.verifiedAmount, a.dataHash, a.at);
    }

    function attestationOf(address pool, uint256 claimId) external view returns (Attestation memory) {
        return _atts[pool][claimId];
    }

    function reporterCount() external view returns (uint256) { return reporterList.length; }

    function _recover(bytes32 h, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) revert BadSignature();
        bytes32 r; bytes32 s; uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) revert BadSignature();
        // reject signature malleability
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) revert BadSignature();
        address who = ecrecover(h, v, r, s);
        if (who == address(0)) revert BadSignature();
        return who;
    }
}
