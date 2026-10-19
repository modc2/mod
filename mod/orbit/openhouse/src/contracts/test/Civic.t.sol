// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouseTrust} from "../OpenHouseTrust.sol";
import {OpenHouseFactory} from "../OpenHouseFactory.sol";
import {MortgageOracle} from "../MortgageOracle.sol";
import {CivicRegistry} from "../CivicRegistry.sol";
import {MockUSD} from "./Trust.t.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function warp(uint256) external;
}

/// The civic seat: a government that verifies from its own servers and holds
/// the pause and the foreclosure hold on-chain. These tests are the charter's
/// guarantees — what a city gets when it adopts, and what it can never touch.
contract CivicTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 constant PRINCIPAL_DUE = 500e6;
    uint256 constant INTEREST_DUE = 1000e6;
    uint256 constant ESCROW_DUE = 300e6;
    uint256 constant DUE = PRINCIPAL_DUE + INTEREST_DUE + ESCROW_DUE;
    uint256 constant LOAN = 200_000e6;

    MockUSD usd;
    MortgageOracle oracle;
    OpenHouseFactory factory;
    OpenHouseTrust trust;
    CivicRegistry registry;

    address bank = address(0xB4);
    address r1 = address(0xB401);
    address r2 = address(0xB402);
    address servicer = address(0x5E);
    address city = address(0xC170);      // the government's server key
    address city2 = address(0xC171);     // its successor
    address alice = address(0xA1);
    address bob = address(0xB0);
    address stranger = address(0xBAD);

    function setUp() public {
        vm.warp(1_000_000);
        usd = new MockUSD();

        address[] memory reporters = new address[](2);
        reporters[0] = r1;
        reporters[1] = r2;
        oracle = new MortgageOracle(bank, "Acme Servicing", keccak256("loan-1"), reporters, 2, 30 days);

        factory = new OpenHouseFactory();
        registry = new CivicRegistry();

        usd.mint(alice, 1_000_000e6);
        usd.mint(bob, 1_000_000e6);
        usd.mint(city, 1_000_000e6);
    }

    // ── helpers ────────────────────────────────────────────────

    function _deploy(address authority) internal {
        address[] memory founders = new address[](2);
        founders[0] = alice;
        founders[1] = bob;
        uint256 id = factory.file(OpenHouseFactory.Filing({
            bank: bank,
            asset: address(usd),
            property: "77 Marcy Ave, Brooklyn",
            name: "77 Marcy Equity",
            symbol: "MARCY",
            deedRef: keccak256("deed"),
            basis: OpenHouseTrust.Basis.Contribution,
            feeBps: 0,
            advanceRateBps: 500,
            authority: authority,
            founders: founders
        }));
        vm.prank(bank);
        trust = OpenHouseTrust(factory.underwrite(id, address(oracle), servicer, 400_000e6));
    }

    function _statement(uint64 period) internal {
        vm.prank(r1);
        oracle.submitStatement(
            period, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
        vm.prank(r2);
        oracle.submitStatement(
            period, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
    }

    function _settlement(uint64 period) internal {
        vm.prank(r1);
        oracle.submitSettlement(period, DUE, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0, false);
        vm.prank(r2);
        oracle.submitSettlement(period, DUE, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0, false);
    }

    function _fund(uint64 period, address who, uint256 amount) internal {
        vm.prank(who);
        usd.approve(address(trust), amount);
        vm.prank(who);
        trust.contribute(period, amount);
    }

    /// Statement past due, nobody paid: the loan is delinquent by silence.
    function _intoDefault() internal {
        _statement(1);
        vm.warp(block.timestamp + 31 days);
        vm.prank(bank);
        trust.declareDefault(1);
    }

    // ═══════════════════════════════════════ The charter ══════

    function test_bornChartered() public {
        _deploy(city);
        require(trust.authority() == city, "authority not seated at birth");
    }

    function test_bankChartersEmptySeatOnly() public {
        _deploy(address(0));
        require(trust.authority() == address(0), "seat should be empty");

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.charterAuthority(city);

        vm.prank(bank);
        trust.charterAuthority(city);
        require(trust.authority() == city, "charter failed");

        // Once seated, the bank cannot replace the government with a friendlier one.
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: seat taken"));
        trust.charterAuthority(stranger);
    }

    function test_onlyAuthorityHoldsCivicLevers() public {
        _deploy(city);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: not the authority"));
        trust.civicSetPaused(true);

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the authority"));
        trust.setCivicHold(true);

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: not the authority"));
        trust.nominateAuthority(stranger);
    }

    // ═══════════════════════════════════ The civic pause ══════

    function test_civicPauseStopsMoneyAndBankCannotClearIt() public {
        _deploy(city);
        _statement(1);
        trust.openPeriod();

        vm.prank(city);
        trust.civicSetPaused(true);

        vm.prank(alice);
        usd.approve(address(trust), DUE);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: civic pause"));
        trust.contribute(1, DUE);

        // The bank's own pause lever touches its own flag, not the city's.
        vm.prank(bank);
        trust.setPaused(false);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: civic pause"));
        trust.contribute(1, DUE);

        vm.prank(city);
        trust.civicSetPaused(false);
        _fund(1, alice, DUE);
        require(trust.contributionOf(1, alice) == DUE, "unpause did not restore");
    }

    function test_civicPauseNeverTrapsAMembersEquity() public {
        _deploy(city);
        _statement(1);
        trust.openPeriod();
        _fund(1, alice, DUE);
        trust.wire(1);
        _settlement(1);
        trust.confirmPeriod(1);

        vm.prank(city);
        trust.civicSetPaused(true);

        // The mint still lands: a posted payment becomes equity under any flag.
        trust.settle(1, alice);
        require(trust.balanceOf(alice) > 0, "settle blocked by civic pause");

        // But shares do not move while the government's brake is on.
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: civic pause"));
        trust.transfer(bob, 1);
    }

    // ═══════════════════════════ Notice, review, and hold ═════

    function test_foreclosureWaitsForTheCity() public {
        _deploy(city);
        _intoDefault();

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: foreclosure not noticed"));
        trust.foreclose();

        vm.prank(bank);
        trust.noticeForeclosure();

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: civic review running"));
        trust.foreclose();

        vm.warp(block.timestamp + 30 days);
        vm.prank(city);
        trust.setCivicHold(true);

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: civic hold"));
        trust.foreclose();

        // The hold has no clock. Ten years on, the house is still standing.
        vm.warp(block.timestamp + 3650 days);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: civic hold"));
        trust.foreclose();

        vm.prank(city);
        trust.setCivicHold(false);
        vm.prank(bank);
        trust.foreclose();
        require(trust.status() == OpenHouseTrust.Status.Foreclosed, "not foreclosed");
    }

    function test_cureTearsUpTheNotice() public {
        _deploy(city);
        _intoDefault();
        vm.prank(bank);
        trust.noticeForeclosure();
        vm.warp(block.timestamp + 30 days);

        vm.prank(bank);
        trust.cure();
        require(trust.foreclosureNoticeAt() == 0, "notice survived the cure");

        // Back into default: the old, expired notice buys the bank nothing.
        vm.warp(block.timestamp + 31 days);
        _statement(2);
        vm.warp(block.timestamp + 31 days);
        vm.prank(bank);
        trust.declareDefault(2);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: foreclosure not noticed"));
        trust.foreclose();
    }

    function test_noAuthorityMeansTheOldDirectRemedy() public {
        _deploy(address(0));
        _intoDefault();

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: no authority to notice"));
        trust.noticeForeclosure();

        vm.prank(bank);
        trust.foreclose();
        require(trust.status() == OpenHouseTrust.Status.Foreclosed, "direct remedy broken");
    }

    // ═════════════════════════════════ Handover and resign ════

    function test_handoverIsTwoStep() public {
        _deploy(city);
        vm.prank(city);
        trust.nominateAuthority(city2);
        require(trust.authority() == city, "seat moved on nomination");

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not nominated"));
        trust.acceptAuthority();

        vm.prank(city2);
        trust.acceptAuthority();
        require(trust.authority() == city2, "handover failed");
    }

    function test_resignLiftsEveryCivicFlag() public {
        _deploy(city);
        vm.prank(city);
        trust.civicSetPaused(true);
        vm.prank(city);
        trust.setCivicHold(true);

        vm.prank(city);
        trust.resignAuthority();
        require(trust.authority() == address(0), "seat not emptied");
        require(!trust.civicPaused(), "departed city left its pause behind");
        require(!trust.civicHold(), "departed city left its hold behind");

        // The seat is genuinely open again.
        vm.prank(bank);
        trust.charterAuthority(city2);
        require(trust.authority() == city2, "recharter failed");
    }

    // ═══════════════════════════ The city-owned program ═══════

    /// A city-owned rent-to-own model is not a special mode: the city takes
    /// the bank seat (its housing fund is the lender), runs its own oracle
    /// reporters, and charters itself — or its state — as authority.
    function test_cityOwnedRentToOwn() public {
        address[] memory reporters = new address[](2);
        reporters[0] = r1;
        reporters[1] = r2;
        MortgageOracle cityOracle =
            new MortgageOracle(city, "City Housing Dept", keccak256("chp-1"), reporters, 2, 30 days);

        address[] memory founders = new address[](1);
        founders[0] = alice;
        uint256 id = factory.file(OpenHouseFactory.Filing({
            bank: city,                     // the city's housing fund is the lender
            asset: address(usd),
            property: "12 Public Way",
            name: "12 Public Way Equity",
            symbol: "PUB",
            deedRef: keccak256("deed-pub"),
            basis: OpenHouseTrust.Basis.Contribution,
            feeBps: 0,                      // a public program takes nothing
            advanceRateBps: 0,              // and rescues for free
            authority: city,                // supervising its own program
            founders: founders
        }));
        vm.prank(city);
        OpenHouseTrust t = OpenHouseTrust(factory.underwrite(id, address(cityOracle), servicer, 300_000e6));

        require(t.bank() == city && t.authority() == city, "city not in both seats");

        vm.prank(r1);
        cityOracle.submitStatement(
            1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
        vm.prank(r2);
        cityOracle.submitStatement(
            1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
        t.openPeriod();
        vm.prank(alice);
        usd.approve(address(t), DUE);
        vm.prank(alice);
        t.contribute(1, DUE);
        t.wire(1);
        vm.prank(r1);
        cityOracle.submitSettlement(1, DUE, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0, false);
        vm.prank(r2);
        cityOracle.submitSettlement(1, DUE, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0, false);
        t.confirmPeriod(1);
        t.settle(1, alice);
        require(t.balanceOf(alice) == DUE * 1e12, "resident equity did not mint");
    }

    // ═══════════════════════════════════════ The registry ═════

    function test_registryLifecycle() public {
        vm.prank(city);
        registry.register("Cleveland Housing Authority", "US-OH", "https://housing.cleveland.gov");
        require(registry.cityCount() == 1, "not listed");
        require(registry.isCity(city), "not a city");

        vm.prank(city);
        vm.expectRevert(bytes("Civic: already registered"));
        registry.register("Cleveland Again", "US-OH", "x");

        vm.prank(city);
        registry.update("Cleveland Dept of Housing", "US-OH", "https://housing.cleveland.gov/v2");
        CivicRegistry.City memory c = registry.cityOf(city);
        require(keccak256(bytes(c.name)) == keccak256(bytes("Cleveland Dept of Housing")), "update lost");

        vm.prank(city);
        registry.setActive(false);
        require(!registry.isCity(city), "inactive city still standing");
    }

    function test_endorsementsAreCheckableBothWays() public {
        _deploy(city);
        vm.prank(city);
        registry.register("Cleveland Housing Authority", "US-OH", "https://housing.cleveland.gov");

        vm.prank(stranger);
        vm.expectRevert(bytes("Civic: not registered"));
        registry.endorse(address(trust), "");

        vm.prank(city);
        registry.endorse(address(trust), "verified ledger, chartered authority");
        require(registry.isEndorsed(city, address(trust)), "endorsement missing");
        require(registry.endorsersOf(address(trust)).length == 1, "trust side missing");
        require(registry.endorsedBy(city).length == 1, "city side missing");
        require(registry.standingEndorsers(address(trust)).length == 1, "not standing");

        vm.prank(city);
        registry.revoke(address(trust), "servicer stopped reporting");
        require(!registry.isEndorsed(city, address(trust)), "revocation missing");
        // History keeps the address; standing does not.
        require(registry.endorsersOf(address(trust)).length == 1, "history rewritten");
        require(registry.standingEndorsers(address(trust)).length == 0, "still standing");
    }
}
