// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouseTrust} from "../OpenHouseTrust.sol";
import {OpenHouseFactory} from "../OpenHouseFactory.sol";
import {MortgageOracle, IMortgageOracle} from "../MortgageOracle.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function warp(uint256) external;
}

/// A minimal 6-decimal USD stablecoin, so every number in these tests reads the
/// way it would on a real deployment against USDC.
contract MockUSD {
    string public name = "Mock USD";
    string public symbol = "mUSD";
    uint8 public constant decimals = 6;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        return _move(msg.sender, to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amount, "mUSD: allowance");
        allowance[from][msg.sender] = a - amount;
        return _move(from, to, amount);
    }

    function _move(address from, address to, uint256 amount) internal returns (bool) {
        require(balanceOf[from] >= amount, "mUSD: balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract TrustTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    // The bill: $500 principal, $1000 interest, $300 escrow = $1800 a month.
    uint256 constant PRINCIPAL_DUE = 500e6;
    uint256 constant INTEREST_DUE = 1000e6;
    uint256 constant ESCROW_DUE = 300e6;
    uint256 constant DUE = PRINCIPAL_DUE + INTEREST_DUE + ESCROW_DUE;
    uint256 constant LOAN = 200_000e6;

    MockUSD usd;
    MortgageOracle oracle;
    OpenHouseFactory factory;
    OpenHouseTrust trust;

    address bank = address(0xB4);
    address r1 = address(0xB401);   // the bank's reporters
    address r2 = address(0xB402);
    address servicer = address(0x5E);
    address sponsor = address(this);
    address alice = address(0xA1);
    address bob = address(0xB0);
    address carol = address(0xCA);
    address stranger = address(0xBAD);

    function setUp() public {
        vm.warp(1_000_000);
        usd = new MockUSD();

        address[] memory reporters = new address[](2);
        reporters[0] = r1;
        reporters[1] = r2;
        vm.prank(bank);
        oracle = new MortgageOracle(bank, "Acme Servicing", keccak256("loan-1"), reporters, 2, 30 days);

        factory = new OpenHouseFactory();

        usd.mint(alice, 1_000_000e6);
        usd.mint(bob, 1_000_000e6);
        usd.mint(carol, 1_000_000e6);
        usd.mint(bank, 1_000_000e6);
        usd.mint(address(this), 1_000_000e6);
    }

    // ── helpers ────────────────────────────────────────────────

    function _file(OpenHouseTrust.Basis basis, uint256 feeBps) internal returns (uint256 id) {
        address[] memory founders = new address[](2);
        founders[0] = alice;
        founders[1] = bob;
        return factory.file(OpenHouseFactory.Filing({
            bank: bank,
            asset: address(usd),
            property: "77 Marcy Ave, Brooklyn",
            name: "77 Marcy Equity",
            symbol: "MARCY",
            deedRef: keccak256("deed"),
            basis: basis,
            feeBps: feeBps,
            advanceRateBps: 500,
            authority: address(0),
            founders: founders
        }));
    }

    function _deploy(OpenHouseTrust.Basis basis, uint256 feeBps) internal {
        uint256 id = _file(basis, feeBps);
        vm.prank(bank);
        trust = OpenHouseTrust(factory.underwrite(id, address(oracle), servicer, 400_000e6));
    }

    function _statement(uint64 period, uint256 balance) internal {
        vm.prank(r1);
        oracle.submitStatement(
            period, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            balance, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
        vm.prank(r2);
        oracle.submitStatement(
            period, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            balance, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0
        );
    }

    function _settlement(uint64 period, uint256 amount, uint256 principal, bool delinquent) internal {
        uint256 interest = amount >= principal + INTEREST_DUE ? INTEREST_DUE : 0;
        uint256 escrow = amount - principal - interest;
        vm.prank(r1);
        oracle.submitSettlement(period, amount, principal, interest, escrow, 0, delinquent);
        vm.prank(r2);
        oracle.submitSettlement(period, amount, principal, interest, escrow, 0, delinquent);
    }

    /// Pro-rata splits round down, so a holder can be short by at most a unit of
    /// 1e-6 USD per distribution. The dust stays in the contract, backed.
    function _near(uint256 got, uint256 want, string memory what) internal pure {
        require(got <= want && want - got <= 2, what);
    }

    function _contribute(address who, uint64 period, uint256 amount) internal {
        vm.prank(who);
        usd.approve(address(trust), amount);
        vm.prank(who);
        trust.contribute(period, amount);
    }

    // ═══════════════════════════════════════ Formation ═══════

    function test_bankMustUnderwriteBeforeAnythingExists() public {
        uint256 id = _file(OpenHouseTrust.Basis.Contribution, 0);
        require(factory.trustOf(id) == address(0), "trust before underwriting");

        vm.prank(stranger);
        vm.expectRevert(bytes("Factory: not the bank"));
        factory.underwrite(id, address(oracle), servicer, 400_000e6);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Factory: not the bank"));
        factory.underwrite(id, address(oracle), servicer, 400_000e6);

        vm.prank(bank);
        address t = factory.underwrite(id, address(oracle), servicer, 400_000e6);
        require(t != address(0), "no trust");
        require(factory.trustOf(id) == t, "index wrong");
    }

    /// The factory never holds the seat, not even for a transaction.
    function test_bankIsTheBankFromBirth() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        require(trust.bank() == bank, "bank is not the bank");
        require(trust.pendingBank() == address(0), "handover pending");
        require(trust.sponsor() == sponsor, "sponsor lost");
        require(uint8(trust.status()) == uint8(OpenHouseTrust.Status.Active), "not active");
        require(trust.isMember(alice), "alice not admitted");
        require(trust.isMember(bob), "bob not admitted");
        require(trust.memberCount() == 2, "roster wrong");
    }

    function test_declineIsOnTheRecord() public {
        uint256 id = _file(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(bank);
        factory.decline(id, "DTI too high");
        require(uint8(factory.stateOf(id)) == uint8(OpenHouseFactory.State.Declined), "not declined");
        vm.prank(bank);
        vm.expectRevert(bytes("Factory: not open"));
        factory.underwrite(id, address(oracle), servicer, 1);
    }

    function test_sponsorCanWithdrawFiling() public {
        uint256 id = _file(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(stranger);
        vm.expectRevert(bytes("Factory: not the sponsor"));
        factory.withdrawFiling(id);
        factory.withdrawFiling(id);
        require(uint8(factory.stateOf(id)) == uint8(OpenHouseFactory.State.Withdrawn), "not withdrawn");
    }

    // ══════════════════════════════════════════ Oracle ═══════

    function test_quorumIsRequiredToPublish() public {
        vm.prank(r1);
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0);
        require(!oracle.statementOf(1).published, "published on one signature");

        vm.prank(r2);
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0);
        require(oracle.statementOf(1).published, "not published at quorum");
        require(oracle.totalDue(1) == DUE, "wrong bill");
        require(oracle.principalBalance() == LOAN, "wrong balance");
    }

    /// Reporters that disagree by a single wei never reach quorum on either
    /// payload — the tally is per exact payload, not per period.
    function test_disagreeingReportersDoNotPublish() public {
        vm.prank(r1);
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0);
        vm.prank(r2);
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE + 1, INTEREST_DUE, ESCROW_DUE, 0);
        require(!oracle.statementOf(1).published, "published on disagreement");
    }

    function test_onlyReportersReport() public {
        vm.prank(stranger);
        vm.expectRevert(bytes("Oracle: not a reporter"));
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0);

        vm.prank(bank);   // the bank owns the feed but does not sign for it
        vm.expectRevert(bytes("Oracle: not a reporter"));
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, PRINCIPAL_DUE, INTEREST_DUE, ESCROW_DUE, 0);
    }

    function test_onlyBankAdministersTheFeed() public {
        vm.prank(stranger);
        vm.expectRevert(bytes("Oracle: not the bank"));
        oracle.setReporter(stranger, true);

        vm.prank(stranger);
        vm.expectRevert(bytes("Oracle: not the bank"));
        oracle.setFrozen(true);

        vm.prank(bank);
        oracle.setReporter(stranger, true);
        require(oracle.isReporter(stranger), "not added");
    }

    function test_aPublishedPeriodCannotBeRewritten() public {
        _statement(1, LOAN);
        vm.prank(r1);
        vm.expectRevert(bytes("Oracle: period published"));
        oracle.submitStatement(1, uint64(block.timestamp), uint64(block.timestamp + 30 days),
            LOAN, 1, 1, 1, 0);
    }

    // ═════════════════════════════ Equity is pro-rata by USD ══

    /// The whole model in one test: Alice puts in 60% of the dollars, Bob 40%,
    /// and they own 60% and 40% of the house.
    function test_equityIsProportionalToDollarsIn() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();

        _contribute(alice, 1, 1080e6);   // 60%
        _contribute(bob, 1, 720e6);      // 40%

        uint256 before = usd.balanceOf(servicer);
        trust.wire(1);
        require(usd.balanceOf(servicer) - before == DUE, "servicer not paid the bill");

        // Nothing is equity until the servicer posts it.
        require(trust.totalSupply() == 0, "minted before confirmation");
        vm.expectRevert(bytes("Trust: period not confirmed"));
        trust.settle(1, alice);

        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);

        address[] memory who = new address[](2);
        who[0] = alice;
        who[1] = bob;
        trust.settleMany(1, who);

        require(trust.balanceOf(alice) == 1080e18, "alice shares");
        require(trust.balanceOf(bob) == 720e18, "bob shares");
        require(trust.equityBps(alice) == 6000, "alice 60%");
        require(trust.equityBps(bob) == 4000, "bob 40%");
        require(trust.totalApplied() == 1800e6, "applied");
    }

    /// A third person joining later dilutes by exactly the dollars they bring
    /// and not a basis point more.
    function test_aLaterMemberDilutesOnlyByWhatTheyPay() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 900e6);
        _contribute(bob, 1, 900e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);
        require(trust.equityBps(alice) == 5000 && trust.equityBps(bob) == 5000, "not 50/50");

        vm.prank(bank);
        trust.admit(carol);

        vm.warp(block.timestamp + 30 days);
        _statement(2, LOAN - PRINCIPAL_DUE);
        trust.openPeriod();
        _contribute(carol, 2, DUE);
        trust.wire(2);
        _settlement(2, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(2);
        trust.settle(2, carol);

        // 900 : 900 : 1800 of 3600 dollars in.
        require(trust.equityBps(alice) == 2500, "alice 25%");
        require(trust.equityBps(bob) == 2500, "bob 25%");
        require(trust.equityBps(carol) == 5000, "carol 50%");
    }

    /// Under the Principal basis, only the amortized principal mints. The
    /// interest is the price of the bank's money and buys nobody any house.
    function test_principalBasisOnlyMintsTheAmortizedPart() public {
        _deploy(OpenHouseTrust.Basis.Principal, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1080e6);
        _contribute(bob, 1, 720e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);

        // $1800 paid, $500 of it principal: 300/200 split, still 60/40.
        require(trust.totalApplied() == PRINCIPAL_DUE, "applied != principal");
        require(trust.balanceOf(alice) == 300e18, "alice");
        require(trust.balanceOf(bob) == 200e18, "bob");
        require(trust.equityBps(alice) == 6000, "still 60%");
        require(trust.totalInterestPaid() == INTEREST_DUE, "interest not tracked");
    }

    function test_ownedBpsTracksThePurchasePrice() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, DUE);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        // $1800 of a $400,000 house = 45 bps.
        require(trust.ownedBps() == 45, "owned bps");
        require(trust.mortgageBalance() == LOAN, "balance from the feed");
    }

    // ══════════════════════════════════ Contribution rules ═══

    function test_overpaymentIsCappedNotTaken() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1000e6);

        uint256 before = usd.balanceOf(bob);
        _contribute(bob, 1, 5000e6);   // asks to pay 5x what is left
        require(before - usd.balanceOf(bob) == DUE - 1000e6, "took more than the shortfall");
        require(trust.roomFor(1) == 0, "period not funded");

        vm.prank(carol);
        usd.approve(address(trust), 100e6);
        vm.prank(bank);
        trust.admit(carol);
        vm.prank(carol);
        vm.expectRevert(bytes("Trust: period funded"));
        trust.contribute(1, 100e6);
    }

    function test_nonMembersCannotPayIn() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        vm.prank(stranger);
        usd.approve(address(trust), 100e6);
        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not a member"));
        trust.contribute(1, 100e6);
    }

    /// A trust that cannot see a fresh bill will not take a member's money.
    function test_staleOracleHaltsContributions() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        vm.warp(block.timestamp + 31 days);
        require(!oracle.isFresh(), "still fresh");
        vm.prank(alice);
        usd.approve(address(trust), 100e6);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: oracle stale"));
        trust.contribute(1, 100e6);
    }

    function test_frozenOracleHaltsContributions() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        vm.prank(bank);
        oracle.setFrozen(true);
        vm.prank(alice);
        usd.approve(address(trust), 100e6);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: oracle stale"));
        trust.contribute(1, 100e6);
    }

    function test_underfundedPeriodCannotWire() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 100e6);
        vm.expectRevert(bytes("Trust: period underfunded"));
        trust.wire(1);
    }

    function test_abortRefundsInFullIncludingTheFee() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 100);   // 1%
        _statement(1, LOAN);
        trust.openPeriod();
        uint256 before = usd.balanceOf(alice);
        _contribute(alice, 1, 500e6);
        require(before - usd.balanceOf(alice) == 500e6, "pull");

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.abortPeriod(1);

        vm.prank(bank);
        trust.abortPeriod(1);
        vm.prank(alice);
        trust.refund(1);
        require(usd.balanceOf(alice) == before, "not made whole");
        require(trust.rebatePool() == 0, "fee stranded");
    }

    // ═════════════════════════════ The bank's own liquidity ══

    /// The lender covering a shortfall is a loan, not a purchase: senior, in
    /// cash, and it mints the bank nothing.
    function test_bankAdvanceIsALienNotEquity() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1000e6);

        vm.prank(bank);
        usd.approve(address(trust), DUE);
        vm.prank(bank);
        trust.advance(1, DUE - 1000e6);
        require(trust.lienPrincipal() == DUE - 1000e6, "no lien");

        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);

        require(trust.balanceOf(bank) == 0, "bank holds shares");
        require(trust.equityBps(alice) == 10_000, "alice diluted by the rescue");
    }

    /// Under Principal basis the bank's advance takes its own share of the
    /// principal — members only mint against the part they funded.
    function test_advanceTakesItsShareOfPrincipalUnderPrincipalBasis() public {
        _deploy(OpenHouseTrust.Basis.Principal, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, DUE / 2);

        vm.prank(bank);
        usd.approve(address(trust), DUE);
        vm.prank(bank);
        trust.advance(1, DUE / 2);

        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);

        // Alice funded half the payment, so she mints half the principal.
        require(trust.totalApplied() == PRINCIPAL_DUE / 2, "applied");
        require(trust.balanceOf(alice) == (PRINCIPAL_DUE / 2) * 1e12, "alice shares");
    }

    function test_lienIsPaidBeforeOwners() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1000e6);
        vm.prank(bank);
        usd.approve(address(trust), DUE);
        vm.prank(bank);
        trust.advance(1, 800e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);

        uint256 bankBefore = usd.balanceOf(bank);
        usd.approve(address(trust), 1000e6);
        trust.distributeIncome(1000e6);   // rent comes in

        // 800 of it repays the lender, 200 reaches the owner.
        require(usd.balanceOf(bank) - bankBefore == 800e6, "lender not repaid first");
        require(trust.lienPrincipal() == 0, "lien outstanding");
        _near(trust.withdrawableOf(alice), 200e6, "owner short");

        uint256 aliceBefore = usd.balanceOf(alice);
        vm.prank(alice);
        trust.withdraw();
        _near(usd.balanceOf(alice) - aliceBefore, 200e6, "withdraw");
    }

    function test_advanceAccruesAtTheCappedRate() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        vm.prank(bank);
        usd.approve(address(trust), DUE);
        vm.prank(bank);
        trust.advance(1, 1000e6);
        vm.warp(block.timestamp + 365 days);
        // 5% APR on $1000 for a year.
        require(trust.lienOutstanding() == 1050e6, "accrual");

        vm.prank(bank);
        vm.expectRevert(bytes("Trust: advance rate out of band"));
        trust.setTerms(0, 2001);
    }

    // ══════════════════════════ Distributions are pro-rata ═══

    function test_incomeSplitsByShares() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1350e6);   // 75%
        _contribute(bob, 1, 450e6);      // 25%
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);

        usd.approve(address(trust), 400e6);
        trust.distributeIncome(400e6);
        _near(trust.withdrawableOf(alice), 300e6, "alice 75%");
        _near(trust.withdrawableOf(bob), 100e6, "bob 25%");
    }

    /// A transfer carries the shares forward but leaves earned income behind.
    function test_transferDoesNotStealAccruedIncome() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 900e6);
        _contribute(bob, 1, 900e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);

        usd.approve(address(trust), 200e6);
        trust.distributeIncome(200e6);

        vm.prank(alice);
        trust.transfer(bob, 900e18);
        _near(trust.withdrawableOf(alice), 100e6, "alice lost earned income");
        _near(trust.withdrawableOf(bob), 100e6, "bob gained unearned income");
    }

    /// The protocol take is not kept: it lands back on the people with money in.
    function test_theFeeComesBack() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 100);   // 1%
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 2_000e6);   // capped at the gross needed
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);

        // Nobody held a share when period 1 confirmed, so the fee waits.
        require(trust.undistributed() > 0, "fee vanished");
        trust.settle(1, alice);
        trust.flushUndistributed();
        require(trust.undistributed() == 0, "not flushed");
        require(trust.withdrawableOf(alice) > 0, "fee did not come back");
    }

    /// 5% is a ceiling in code, at both doors — the filing and the trust itself.
    function test_feeIsCappedInCode() public {
        address[] memory founders = new address[](1);
        founders[0] = alice;
        vm.expectRevert(bytes("Factory: fee out of band"));
        factory.file(OpenHouseFactory.Filing({
            bank: bank, asset: address(usd), property: "x", name: "x", symbol: "X",
            deedRef: bytes32(0), basis: OpenHouseTrust.Basis.Contribution,
            feeBps: 501, advanceRateBps: 0, authority: address(0), founders: founders
        }));

        address[] memory none = new address[](0);
        vm.expectRevert(bytes("Trust: fee out of band"));
        new OpenHouseTrust(OpenHouseTrust.Init({
            name: "x", symbol: "X", property: "x", deedRef: bytes32(0),
            bank: bank, sponsor: sponsor, asset: address(usd),
            basis: OpenHouseTrust.Basis.Contribution,
            feeBps: 501, advanceRateBps: 0,
            oracle: address(0), servicer: address(0), purchasePrice: 0,
            authority: address(0), founders: none
        }));

        _deploy(OpenHouseTrust.Basis.Contribution, 500);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: fee out of band"));
        trust.setTerms(501, 0);
    }

    // ═════════════════════════════ Bank control & its limits ══

    function test_onlyBankHoldsTheLevers() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.setPaused(true);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.admit(carol);

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.setServicer(stranger);

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.setOracle(address(oracle));

        vm.prank(stranger);
        vm.expectRevert(bytes("Trust: not the bank"));
        trust.reissue(alice, bob);
    }

    function test_bankPausesAlone() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        vm.prank(bank);
        trust.setPaused(true);

        vm.prank(alice);
        usd.approve(address(trust), 100e6);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: paused"));
        trust.contribute(1, 100e6);

        vm.prank(bank);
        trust.setPaused(false);
        _contribute(alice, 1, 100e6);
    }

    /// A swapped feed must still answer to the same bank — the numbers can
    /// never be quietly handed to somebody else.
    function test_oracleSwapMustAnswerToTheSameBank() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        address[] memory reporters = new address[](1);
        reporters[0] = stranger;
        MortgageOracle rogue = new MortgageOracle(
            stranger, "Rogue", keccak256("x"), reporters, 1, 30 days
        );
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: oracle answers to another bank"));
        trust.setOracle(address(rogue));
    }

    /// Calling the loan is gated on the feed, not on the bank's mood.
    function test_defaultRequiresTheOracleToSaySo() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: loan is current"));
        trust.declareDefault(1);

        vm.warp(block.timestamp + 31 days);   // past the due date, never settled
        vm.prank(bank);
        trust.declareDefault(1);
        require(uint8(trust.status()) == uint8(OpenHouseTrust.Status.Default), "not defaulted");

        vm.prank(bank);
        trust.cure();
        require(uint8(trust.status()) == uint8(OpenHouseTrust.Status.Active), "not cured");
    }

    function test_forceclosureNeedsADefaultFirst() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: not in default"));
        trust.foreclose();
    }

    /// Foreclosure surplus is the members', and there is nowhere else it can go.
    function test_recoverySurplusGoesToMembers() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 1800e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);

        vm.warp(block.timestamp + 31 days);
        _statement(2, LOAN - PRINCIPAL_DUE);
        vm.warp(block.timestamp + 31 days);
        vm.prank(bank);
        trust.declareDefault(2);
        vm.prank(bank);
        trust.foreclose();

        vm.prank(bank);
        usd.approve(address(trust), 5000e6);
        vm.prank(bank);
        trust.distributeRecovery(5000e6);
        _near(trust.withdrawableOf(alice), 5000e6, "surplus withheld");
    }

    /// Freezing a member stops them acting. It does not touch what they own.
    function test_freezingIsNotConfiscation() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 900e6);
        _contribute(bob, 1, 900e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);

        usd.approve(address(trust), 200e6);
        trust.distributeIncome(200e6);

        vm.prank(bank);
        trust.setFrozen(alice, true);

        require(trust.balanceOf(alice) == 900e18, "balance touched");
        require(trust.equityBps(alice) == 5000, "equity touched");
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: frozen"));
        trust.transfer(bob, 1);
        // ...but the income they already earned is still theirs to take.
        vm.prank(alice);
        _near(trust.withdraw(), 100e6, "frozen out of earned income");
    }

    /// The one bank power over shares is all-or-nothing relocation, and it
    /// cannot shrink a position or invent one.
    function test_reissueMovesTheWholePositionAndNothingElse() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, 900e6);
        _contribute(bob, 1, 900e6);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);
        trust.settle(1, bob);
        usd.approve(address(trust), 200e6);
        trust.distributeIncome(200e6);

        uint256 supply = trust.totalSupply();
        vm.prank(bank);
        trust.admit(carol);
        vm.prank(bank);
        trust.reissue(alice, carol);

        require(trust.totalSupply() == supply, "supply moved");
        require(trust.balanceOf(alice) == 0, "source not emptied");
        require(trust.balanceOf(carol) == 900e18, "destination short");
        _near(trust.withdrawableOf(carol), 100e6, "income did not follow");
        require(trust.withdrawableOf(alice) == 0, "income double counted");

        // It cannot land on somebody the bank has not cleared.
        vm.prank(bank);
        vm.expectRevert(bytes("Trust: destination not a member"));
        trust.reissue(bob, stranger);
    }

    function test_transferPolicyIsTheBanksCall() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        trust.openPeriod();
        _contribute(alice, 1, DUE);
        trust.wire(1);
        _settlement(1, DUE, PRINCIPAL_DUE, false);
        trust.confirmPeriod(1);
        trust.settle(1, alice);

        // MembersOnly by default.
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: recipient not a member"));
        trust.transfer(stranger, 1e18);

        vm.prank(bank);
        trust.setTransferPolicy(OpenHouseTrust.TransferPolicy.Locked);
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: shares are locked"));
        trust.transfer(bob, 1e18);

        vm.prank(bank);
        trust.setTransferPolicy(OpenHouseTrust.TransferPolicy.Open);
        vm.prank(alice);
        trust.transfer(stranger, 1e18);
        require(trust.balanceOf(stranger) == 1e18, "open transfer failed");
    }

    function test_bankHandoverIsTwoStep() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(bank);
        trust.nominateBank(stranger);
        require(trust.bank() == bank, "seat moved early");
        vm.prank(alice);
        vm.expectRevert(bytes("Trust: not nominated"));
        trust.acceptBank();
        vm.prank(stranger);
        trust.acceptBank();
        require(trust.bank() == stranger, "seat did not move");
    }

    function test_openMembershipIsTheBanksSwitch() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        vm.prank(carol);
        vm.expectRevert(bytes("Trust: membership is by admission"));
        trust.join();

        vm.prank(bank);
        trust.setOpenMembership(true);
        vm.prank(carol);
        trust.join();
        require(trust.isMember(carol), "join failed");
    }

    // ══════════════════════════════════════════ Discharge ════

    function test_dischargeIsPermissionlessAndCheckedAgainstTheFeed() public {
        _deploy(OpenHouseTrust.Basis.Contribution, 0);
        _statement(1, LOAN);
        vm.expectRevert(bytes("Trust: balance outstanding"));
        trust.dischargeMortgage();

        vm.warp(block.timestamp + 1 days);
        _statement(2, 0);
        vm.prank(stranger);   // anyone
        trust.dischargeMortgage();
        require(uint8(trust.status()) == uint8(OpenHouseTrust.Status.Discharged), "not discharged");
    }
}
