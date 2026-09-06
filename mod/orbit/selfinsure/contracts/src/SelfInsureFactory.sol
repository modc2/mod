// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {SelfInsure} from "./SelfInsure.sol";

/// @title SelfInsureFactory — anyone opens a mutual; the registry is public.
/// @notice There is no application and no underwriter. `open` deploys a fresh
///         SelfInsure with the caller as operator and lists it here so every
///         pool ever opened through this factory can be found, compared and
///         audited from one address. `healthPreset` is the template this whole
///         module exists for: the terms a community, an employer, a union or a
///         county would use to run a member-owned health mutual whose operator
///         takes 0% and whose surplus goes back to the people who paid it.
contract SelfInsureFactory {
    /// Every pool is an EIP-1167 minimal proxy onto this one audited bytecode,
    /// so verifying one implementation verifies every pool ever opened here.
    address public immutable implementation;
    address[] public pools;
    mapping(address => address[]) public poolsBy;
    mapping(address => bool) public isPool;

    event PoolOpened(address indexed pool, address indexed owner, string name, address asset,
                     uint16 feeBps, address oracle, SelfInsure.OracleMode oracleMode);

    constructor() {
        SelfInsure.Config memory empty;
        implementation = address(new SelfInsure(empty));
    }

    function open(SelfInsure.Config memory c) external returns (address pool) {
        if (c.owner == address(0)) c.owner = msg.sender;
        pool = _clone();
        SelfInsure(payable(pool)).initialize(c);
        pools.push(pool);
        poolsBy[c.owner].push(pool);
        isPool[pool] = true;
        emit PoolOpened(pool, c.owner, c.name, c.asset, c.terms.feeBps, c.oracle, c.oracleMode);
    }

    /// Open a pool on the health template in one call. `unit` is 10**decimals
    /// of the asset (1e6 for USDC, 1e18 for ETH or DAI), so the preset reads
    /// in whole dollars and scales to the asset.
    function openHealth(string calldata name, string calldata about, address asset,
                        uint256 unit, address oracle, SelfInsure.OracleMode oracleMode)
        external returns (address pool)
    {
        SelfInsure.Config memory c = SelfInsure.Config({
            name: name,
            about: bytes(about).length == 0 ? healthAbout() : about,
            asset: asset,
            owner: msg.sender,
            oracle: oracle,
            oracleMode: oracleMode,
            terms: healthPreset(unit)
        });
        pool = _clone();
        SelfInsure(payable(pool)).initialize(c);
        pools.push(pool);
        poolsBy[msg.sender].push(pool);
        isPool[pool] = true;
        emit PoolOpened(pool, msg.sender, name, asset, c.terms.feeBps, oracle, oracleMode);
    }

    /// The US health mutual template. Numbers are whole units of the asset
    /// (dollars, for a stablecoin) times `unit`; they are a starting point a
    /// pool owner tunes with setTerms, not a verdict.
    ///
    ///   premium        $400 / month     roughly a single adult's marketplace premium
    ///   coverage       $50,000 / claim  one hospitalisation, most surgeries
    ///   deductible     $250             low, because the point is to be used
    ///   annualCap      $250,000         per member per policy year
    ///   waitingPeriod  30 days          stops joining on the way to the ER
    ///   reserveFloor   $25,000          never distributed — the pool's own backstop
    ///   feeBps         0                the operator keeps NOTHING; the cap is 10%
    ///   quorum 2, threshold 66%         two adjudicators, both must agree
    ///   approvedAgentsOnly true         the pool admits who judges its claims
    function healthPreset(uint256 unit) public pure returns (SelfInsure.Terms memory t) {
        t.premium = 400 * unit;
        t.period = 30 days;
        t.coverage = 50_000 * unit;
        t.deductible = 250 * unit;
        t.annualCap = 250_000 * unit;
        t.waitingPeriod = 30 days;
        t.reserveFloor = 25_000 * unit;
        t.feeBps = 0;
        t.quorum = 2;
        t.thresholdBps = 6600;
        t.approvedAgentsOnly = true;
    }

    function healthAbout() public pure returns (string memory) {
        return "Member-owned health mutual. Covers medically necessary care billed by a "
               "licensed provider: emergency, inpatient, outpatient, surgery, diagnostics, "
               "prescriptions, maternity, mental health. A claim is the provider's itemised "
               "bill (or EOB) for the member, filed within 90 days of service; the oracle, "
               "when one is set, verifies the bill and its amount. Not covered: elective "
               "cosmetic procedures, care outside a licensed setting, costs already paid by "
               "another plan. Adjudicators judge against this text and nothing else.";
    }

    function _clone() private returns (address instance) {
        address impl = implementation;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, 0x3d602d80600a3d3981f3363d3d373d3d3d363d73000000000000000000000000)
            mstore(add(ptr, 0x14), shl(0x60, impl))
            mstore(add(ptr, 0x28), 0x5af43d82803e903d91602b57fd5bf30000000000000000000000000000000000)
            instance := create(0, ptr, 0x37)
        }
        require(instance != address(0), "clone failed");
    }

    function count() external view returns (uint256) { return pools.length; }

    function all() external view returns (address[] memory) { return pools; }
}
