// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouseTrust} from "./OpenHouseTrust.sol";

/// @title OpenHouseFactory — where a group becomes a borrower
/// @notice Anyone can form. Nobody can finance themselves.
///
///         A formation is the cheap half: a name, a house, a list of people who
///         intend to buy it together, and the address of the bank they are
///         asking to lend. It costs one transaction and commits no one. It is a
///         public, timestamped record that this set of individuals agreed on
///         these terms before any money existed — which is the thing groups
///         usually cannot prove to a lender.
///
///         Underwriting is the other half, and it is not on-chain. The named
///         bank reads the formation, does what banks do, and either walks away
///         or calls `underwrite`, which is what actually deploys the trust. The
///         bank sets the oracle, the servicer and the price at that moment; the
///         founders' terms are carried through unchanged, so the deal that gets
///         deployed is the deal that was posted. A formation the bank never
///         touches simply stays a piece of paper.
contract OpenHouseFactory {
    enum State { Proposed, Underwritten, Declined, Withdrawn }

    struct Formation {
        address   sponsor;        // who filed it
        address   bank;           // the lender being asked
        address   asset;          // the USD stablecoin the deal is denominated in
        string    property;
        string    name;           // share token name
        string    symbol;
        bytes32   deedRef;
        OpenHouseTrust.Basis basis;
        uint256   feeBps;
        uint256   advanceRateBps;
        uint64    filedAt;
        State     state;
        address   trust;          // set on underwriting
        address[] founders;
    }

    Formation[] private _formations;

    /// Every trust this factory has deployed, and the index back to its paper.
    address[] public trusts;
    mapping(address => uint256) public formationOfTrust;

    mapping(address => uint256[]) private _bySponsor;
    mapping(address => uint256[]) private _byBank;
    mapping(address => uint256[]) private _byFounder;

    event FormationFiled(
        uint256 indexed id, address indexed sponsor, address indexed bank,
        string property, uint256 founders
    );
    event FormationWithdrawn(uint256 indexed id);
    event FormationDeclined(uint256 indexed id, string reason);
    event Underwritten(uint256 indexed id, address indexed trust, address indexed bank);

    /// @notice File a formation. Permissionless, and intentionally so — the
    ///         gate on this deal is the bank's balance sheet, not this
    ///         contract's guest list.
    /// @param founders the people who intend to buy together. They are admitted
    ///        to the trust the moment the bank deploys it — the bank underwrote
    ///        this list, not some other one.
    /// What a group posts. Everything except the underwriting.
    struct Filing {
        address bank;
        address asset;
        string  property;
        string  name;
        string  symbol;
        bytes32 deedRef;
        OpenHouseTrust.Basis basis;
        uint256 feeBps;
        uint256 advanceRateBps;
        address[] founders;
    }

    function file(Filing calldata p) external returns (uint256 id) {
        require(p.bank != address(0), "Factory: zero bank");
        require(p.asset != address(0), "Factory: zero asset");
        require(p.founders.length > 0, "Factory: no founders");
        require(p.feeBps <= 500, "Factory: fee out of band");
        require(p.advanceRateBps <= 2000, "Factory: advance rate out of band");

        id = _formations.length;
        _formations.push();
        Formation storage f = _formations[id];
        f.sponsor = msg.sender;
        f.bank = p.bank;
        f.asset = p.asset;
        f.property = p.property;
        f.name = p.name;
        f.symbol = p.symbol;
        f.deedRef = p.deedRef;
        f.basis = p.basis;
        f.feeBps = p.feeBps;
        f.advanceRateBps = p.advanceRateBps;
        f.filedAt = uint64(block.timestamp);
        f.state = State.Proposed;
        for (uint256 i = 0; i < p.founders.length; i++) {
            require(p.founders[i] != address(0), "Factory: zero founder");
            f.founders.push(p.founders[i]);
            _byFounder[p.founders[i]].push(id);
        }

        _bySponsor[msg.sender].push(id);
        _byBank[p.bank].push(id);
        emit FormationFiled(id, msg.sender, p.bank, p.property, p.founders.length);
    }

    /// @notice The bank underwrites: deploys the trust, admits exactly the
    ///         founders that were filed, activates it against its own oracle,
    ///         and keeps the controlling seat. Everything after this point is
    ///         governed by OpenHouseTrust, where the bank holds the levers.
    function underwrite(uint256 id, address oracle, address servicer, uint256 purchasePrice)
        external returns (address trust)
    {
        Formation storage f = _formations[id];
        require(f.state == State.Proposed, "Factory: not open");
        require(msg.sender == f.bank, "Factory: not the bank");

        // The bank is the bank from the first opcode: the factory never holds
        // the controlling seat, not even for one transaction. Founders and the
        // feed go in with the constructor, so the trust is born live, admitted
        // to exactly the filed list, and answering to the lender that signed.
        OpenHouseTrust t = new OpenHouseTrust(OpenHouseTrust.Init({
            name: f.name,
            symbol: f.symbol,
            property: f.property,
            deedRef: f.deedRef,
            bank: msg.sender,
            sponsor: f.sponsor,
            asset: f.asset,
            basis: f.basis,
            feeBps: f.feeBps,
            advanceRateBps: f.advanceRateBps,
            oracle: oracle,
            servicer: servicer,
            purchasePrice: purchasePrice,
            founders: f.founders
        }));

        f.state = State.Underwritten;
        f.trust = address(t);
        trusts.push(address(t));
        formationOfTrust[address(t)] = id;

        emit Underwritten(id, address(t), msg.sender);
        return address(t);
    }

    /// @notice The bank says no, on the record. A declined formation can be
    ///         re-filed at another lender; the refusal stays visible.
    function decline(uint256 id, string calldata reason) external {
        Formation storage f = _formations[id];
        require(f.state == State.Proposed, "Factory: not open");
        require(msg.sender == f.bank, "Factory: not the bank");
        f.state = State.Declined;
        emit FormationDeclined(id, reason);
    }

    /// @notice The group pulls its own filing.
    function withdrawFiling(uint256 id) external {
        Formation storage f = _formations[id];
        require(f.state == State.Proposed, "Factory: not open");
        require(msg.sender == f.sponsor, "Factory: not the sponsor");
        f.state = State.Withdrawn;
        emit FormationWithdrawn(id);
    }

    // ─────────────────────────────────────────── Views ─────

    function formationCount() external view returns (uint256) { return _formations.length; }
    function trustCount() external view returns (uint256) { return trusts.length; }

    function formation(uint256 id) external view returns (Formation memory) {
        return _formations[id];
    }

    function stateOf(uint256 id) external view returns (State) { return _formations[id].state; }
    function trustOf(uint256 id) external view returns (address) { return _formations[id].trust; }

    function foundersOf(uint256 id) external view returns (address[] memory) {
        return _formations[id].founders;
    }

    function bySponsor(address who) external view returns (uint256[] memory) { return _bySponsor[who]; }
    function byBank(address who) external view returns (uint256[] memory) { return _byBank[who]; }
    function byFounder(address who) external view returns (uint256[] memory) { return _byFounder[who]; }
}
