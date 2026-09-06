// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {SelfInsure, ISelfInsureOracle} from "../src/SelfInsure.sol";
import {SelfInsureFactory} from "../src/SelfInsureFactory.sol";
import {SignedOracle} from "../src/oracles/SignedOracle.sol";

/// A stand-in stablecoin for the ERC-20 path.
contract MockUSD {
    string public constant name = "Mock USD";
    string public constant symbol = "mUSD";
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount; return true;
    }
    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount; balanceOf[to] += amount; return true;
    }
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount; balanceOf[to] += amount; return true;
    }
}

contract SelfInsureTest is Test {
    SelfInsureFactory factory;
    SelfInsure pool;

    address owner = makeAddr("owner");
    address alice = makeAddr("alice");
    address bob   = makeAddr("bob");
    address carol = makeAddr("carol");
    address judge1 = makeAddr("judge1");
    address judge2 = makeAddr("judge2");
    address judge3 = makeAddr("judge3");

    uint256 constant PREMIUM = 1 ether;

    function setUp() public {
        factory = new SelfInsureFactory();
        pool = SelfInsure(payable(factory.open(_config(address(0), 0, address(0), SelfInsure.OracleMode.None))));
        for (uint256 i = 0; i < 7; i++) {
            address a = [owner, alice, bob, carol, judge1, judge2, judge3][i];
            vm.deal(a, 100 ether);
        }
        _registerJudges();
    }

    function _config(address asset, uint16 feeBps, address oracle, SelfInsure.OracleMode mode)
        internal view returns (SelfInsure.Config memory c)
    {
        c.name = "Courier health mutual";
        c.about = "Medically necessary care billed by a licensed provider.";
        c.asset = asset;
        c.owner = owner;
        c.oracle = oracle;
        c.oracleMode = mode;
        c.terms = SelfInsure.Terms({
            premium: PREMIUM, period: 30 days, coverage: 5 ether, deductible: 0.1 ether,
            annualCap: 0, waitingPeriod: 0, reserveFloor: 0,
            feeBps: feeBps, quorum: 2, thresholdBps: 5000, approvedAgentsOnly: false
        });
    }

    function _registerJudges() internal {
        vm.prank(judge1); pool.registerAgent("judge one", "ai", "claude-fable-5");
        vm.prank(judge2); pool.registerAgent("judge two", "human", "");
        vm.prank(judge3); pool.registerAgent("judge three", "ai", "gpt");
    }

    function _join(address who) internal {
        vm.prank(who); pool.join{value: PREMIUM}(PREMIUM);
    }

    function _assertReconciles() internal view {
        SelfInsure.Transparency memory t = pool.transparency();
        assertTrue(t.reconciles, "held must cover pot + fees owed + rebates");
        assertEq(t.held, t.balance + (t.feesAccrued - t.feesWithdrawn) + t.rebatesUnclaimed,
                 "the contract holds exactly what it says it holds");
    }

    // ── membership and money in ─────────────────────────────

    function test_join_premium_lands_in_pool_not_house() public {
        _join(alice); _join(bob); _join(carol);
        SelfInsure.Transparency memory t = pool.transparency();
        assertEq(t.premiumsIn, 3 ether);
        assertEq(t.balance, 3 ether);
        assertEq(t.feesAccrued, 0);
        assertEq(t.operatorShareBps, 0, "operator keeps nothing by default");
        assertEq(t.memberShareBps, 10000);
        assertEq(t.members, 3);
        assertEq(pool.stakeOf(alice), 1 ether);
        assertEq(pool.totalStake(), 3 ether);
        assertTrue(pool.isCovered(alice));
        _assertReconciles();
    }

    function test_join_short_premium_reverts() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.PremiumShort.selector, 0.5 ether, PREMIUM));
        pool.join{value: 0.5 ether}(0.5 ether);
    }

    function test_eth_amount_must_match_value() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.WrongPayment.selector, 1 ether, 2 ether));
        pool.join{value: 1 ether}(2 ether);
    }

    function test_premium_extends_coverage_one_period_each() public {
        _join(alice);
        (, , , uint64 paidThrough, , , ) = pool.members(alice);
        assertEq(paidThrough, block.timestamp + 30 days);
        vm.prank(alice); pool.payPremium{value: 2 ether}(2 ether);
        (, , , paidThrough, , , ) = pool.members(alice);
        assertEq(paidThrough, block.timestamp + 90 days);
        vm.warp(block.timestamp + 91 days);
        assertFalse(pool.isCovered(alice), "lapsed");
    }

    function test_closed_pool_takes_no_new_members() public {
        vm.prank(owner); pool.setClosed(true);
        vm.prank(alice);
        vm.expectRevert(SelfInsure.PoolClosed.selector);
        pool.join{value: PREMIUM}(PREMIUM);
    }

    function test_plain_eth_is_a_donation() public {
        (bool ok, ) = address(pool).call{value: 2 ether}("");
        assertTrue(ok);
        assertEq(pool.donationsIn(), 2 ether);
        assertEq(pool.balance(), 2 ether);
        assertEq(pool.totalStake(), 0, "a donation earns no share of surplus");
        _assertReconciles();
    }

    // ── the fee: capped, noticed, published ────────────────

    function test_fee_above_cap_cannot_be_deployed() public {
        SelfInsure.Config memory c = _config(address(0), 1001, address(0), SelfInsure.OracleMode.None);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.FeeAboveCap.selector, 1001, 1000));
        factory.open(c);
    }

    function test_fee_raise_needs_seven_days_notice() public {
        vm.prank(owner); pool.proposeFee(500);
        (, , , , , , , uint16 feeBps, , , ) = pool.terms();
        assertEq(feeBps, 0, "not applied yet");
        assertEq(pool.pendingFeeBps(), 500);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.FeeNoticePending.selector, uint64(block.timestamp + 7 days)));
        pool.applyFee();
        // a member who joins during the notice pays the old fee
        _join(alice);
        assertEq(pool.feesAccrued(), 0);
        vm.warp(block.timestamp + 7 days);
        pool.applyFee();
        (, , , , , , , feeBps, , , ) = pool.terms();
        assertEq(feeBps, 500);
        _join(bob);
        assertEq(pool.feesAccrued(), 0.05 ether);
        assertEq(pool.balance(), 1.95 ether);
        SelfInsure.Transparency memory t = pool.transparency();
        assertEq(t.operatorShareBps, 250, "5% of half the premium ever paid");
        assertEq(t.memberShareBps, 9750);
        _assertReconciles();
    }

    function test_fee_cut_is_immediate_and_cap_holds() public {
        vm.startPrank(owner);
        pool.proposeFee(1000);
        vm.warp(block.timestamp + 7 days);
        pool.applyFee();
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.FeeAboveCap.selector, 1001, 1000));
        pool.proposeFee(1001);
        pool.proposeFee(0);
        vm.stopPrank();
        (, , , , , , , uint16 feeBps, , , ) = pool.terms();
        assertEq(feeBps, 0);
        assertEq(pool.pendingFeeAt(), 0);
    }

    function test_setTerms_cannot_smuggle_a_fee() public {
        (uint256 p, uint256 per, uint256 cov, uint256 ded, uint256 cap, uint256 wait, uint256 floor,
         , uint16 q, uint16 th, bool approved) = pool.terms();
        SelfInsure.Terms memory t = SelfInsure.Terms(p, per, cov, ded, cap, wait, floor, 100, q, th, approved);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.BadTerms.selector, "use proposeFee"));
        pool.setTerms(t);
    }

    function test_fees_withdrawn_are_a_public_line() public {
        vm.prank(owner); pool.proposeFee(1000);
        vm.warp(block.timestamp + 7 days);
        pool.applyFee();
        _join(alice); _join(bob);
        assertEq(pool.feesAccrued(), 0.2 ether);
        uint256 before = owner.balance;
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit SelfInsure.FeesWithdrawn(owner, 0.2 ether, 0.2 ether, 1000);
        pool.withdrawFees(owner, 0);
        assertEq(owner.balance - before, 0.2 ether);
        assertEq(pool.feesWithdrawn(), 0.2 ether);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.NotDistributable.selector, 0, 0));
        pool.withdrawFees(owner, 0);
        _assertReconciles();
    }

    function test_only_owner_touches_terms_and_fees() public {
        vm.prank(alice);
        vm.expectRevert(SelfInsure.NotOwner.selector);
        pool.proposeFee(1);
        vm.prank(alice);
        vm.expectRevert(SelfInsure.NotOwner.selector);
        pool.withdrawFees(alice, 0);
        vm.prank(alice);
        vm.expectRevert(SelfInsure.NotOwner.selector);
        pool.distribute(0);
    }

    // ── claims and adjudication ────────────────────────────

    function test_claim_settles_when_quorum_accepts_and_pays_at_once() public {
        _join(alice); _join(bob); _join(carol);
        vm.prank(alice);
        uint256 id = pool.fileClaim(2 ether, "ER visit, broken wrist", "ipfs://bill");
        assertEq(pool.openExposure(), 1.9 ether, "deductible off the top");
        vm.prank(judge1); pool.vote(id, true, "itemised bill matches the ER visit");
        SelfInsure.Claim memory c = pool.claim(id);
        assertEq(uint8(c.state), uint8(SelfInsure.ClaimState.Open), "one of two");
        uint256 before = alice.balance;
        vm.prank(judge2); pool.vote(id, true, "confirmed with the provider");
        c = pool.claim(id);
        assertEq(uint8(c.state), uint8(SelfInsure.ClaimState.Accepted));
        assertEq(c.paid, 1.9 ether);
        assertEq(c.shortfall, 0);
        assertEq(alice.balance - before, 1.9 ether);
        assertEq(pool.balance(), 1.1 ether);
        assertEq(pool.openExposure(), 0);
        assertEq(pool.stakeOf(alice), 0, "what came back as a claim is no longer stake");
        SelfInsure.Transparency memory t = pool.transparency();
        assertEq(t.lossRatioBps, 6333, "1.9 of 3 came back as claims");
        assertTrue(t.solvent);
        SelfInsure.Ballot[] memory bs = pool.ballots(id);
        assertEq(bs.length, 2);
        assertEq(bs[0].reason, "itemised bill matches the ER visit");
        (, , , , , uint32 votes, uint32 accepts, uint32 withMajority, ) = pool.agents(judge1);
        assertEq(votes, 1); assertEq(accepts, 1); assertEq(withMajority, 1);
        _assertReconciles();
    }

    function test_rejection_keeps_the_money_and_the_reasons() public {
        _join(alice); _join(bob);
        vm.prank(alice);
        uint256 id = pool.fileClaim(1 ether, "cosmetic", "");
        vm.prank(judge1); pool.vote(id, true, "looks fine");
        vm.prank(judge2); pool.vote(id, false, "elective cosmetic procedure is excluded by the pool's terms");
        // 1 of 2 = 50% ≥ 50% threshold → accepted. Push threshold: use 3 judges.
        SelfInsure.Claim memory c = pool.claim(id);
        assertEq(uint8(c.state), uint8(SelfInsure.ClaimState.Accepted));

        // a pool that needs a clear majority
        SelfInsure.Config memory cfg = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        cfg.terms.thresholdBps = 6600;
        SelfInsure strict = SelfInsure(payable(factory.open(cfg)));
        vm.prank(judge1); strict.registerAgent("j1", "ai", "");
        vm.prank(judge2); strict.registerAgent("j2", "ai", "");
        vm.prank(alice); strict.join{value: PREMIUM}(PREMIUM);
        vm.prank(alice);
        uint256 id2 = strict.fileClaim(1 ether, "cosmetic", "");
        vm.prank(judge1); strict.vote(id2, true, "ok");
        vm.prank(judge2); strict.vote(id2, false, "excluded");
        c = strict.claim(id2);
        assertEq(uint8(c.state), uint8(SelfInsure.ClaimState.Rejected));
        assertEq(strict.balance(), 1 ether, "nothing left the pool");
        assertEq(strict.openExposure(), 0);
        (, , , , , , , uint32 wm1, ) = strict.agents(judge1);
        (, , , , , , , uint32 wm2, ) = strict.agents(judge2);
        assertEq(wm1, 0); assertEq(wm2, 1, "concordance tracks who landed with the pool");
    }

    function test_vote_rules() public {
        _join(alice); _join(bob);
        vm.prank(judge1); pool.join{value: PREMIUM}(PREMIUM);
        vm.prank(judge1);
        uint256 id = pool.fileClaim(1 ether, "my own claim", "");
        vm.prank(judge1);
        vm.expectRevert(SelfInsure.OwnClaim.selector);
        pool.vote(id, true, "trust me");
        vm.prank(judge2);
        vm.expectRevert(SelfInsure.ReasonRequired.selector);
        pool.vote(id, true, "");
        vm.prank(judge2); pool.vote(id, true, "verified");
        vm.prank(judge2);
        vm.expectRevert(SelfInsure.AlreadyVoted.selector);
        pool.vote(id, false, "changed my mind");
        vm.prank(alice);
        vm.expectRevert(SelfInsure.NotAgent.selector);
        pool.vote(id, true, "I am not an adjudicator");
        vm.prank(judge3); pool.vote(id, true, "verified too");
        vm.prank(judge3);
        vm.expectRevert(SelfInsure.ClaimNotOpen.selector);
        pool.vote(id, true, "settled already");
    }

    function test_approved_agents_only() public {
        SelfInsure.Config memory cfg = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        cfg.terms.approvedAgentsOnly = true;
        cfg.terms.quorum = 1;
        SelfInsure p = SelfInsure(payable(factory.open(cfg)));
        vm.prank(judge1); p.registerAgent("j1", "ai", "");
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(alice);
        uint256 id = p.fileClaim(0.5 ether, "x", "");
        vm.prank(judge1);
        vm.expectRevert(SelfInsure.AgentNotAdmitted.selector);
        p.vote(id, true, "ok");
        vm.prank(owner); p.admitAgent(judge1, true);
        vm.prank(judge1); p.vote(id, true, "ok");
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Accepted));
    }

    function test_waiting_period_and_withdraw() public {
        SelfInsure.Config memory cfg = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        cfg.terms.waitingPeriod = 30 days;
        SelfInsure p = SelfInsure(payable(factory.open(cfg)));
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.NotCovered.selector, uint64(block.timestamp + 30 days)));
        p.fileClaim(1 ether, "too soon", "");
        vm.warp(block.timestamp + 30 days);
        vm.prank(alice);
        uint256 id = p.fileClaim(1 ether, "now", "");
        assertEq(p.openExposure(), 0.9 ether);
        vm.prank(bob);
        vm.expectRevert(SelfInsure.NotMember.selector);
        p.withdrawClaim(id);
        vm.prank(alice); p.withdrawClaim(id);
        assertEq(p.openExposure(), 0);
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Withdrawn));
    }

    function test_terms_are_frozen_at_filing() public {
        _join(alice); _join(bob); _join(carol);
        vm.prank(alice);
        uint256 id = pool.fileClaim(3 ether, "surgery", "");
        // owner slashes coverage after the claim is in — it must not apply
        (uint256 p, uint256 per, , uint256 ded, uint256 cap, uint256 wait, uint256 floor,
         uint16 fee, uint16 q, uint16 th, bool approved) = pool.terms();
        vm.prank(owner);
        pool.setTerms(SelfInsure.Terms(p, per, 0.5 ether, ded, cap, wait, floor, fee, q, th, approved));
        vm.prank(judge1); pool.vote(id, true, "ok");
        vm.prank(judge2); pool.vote(id, true, "ok");
        assertEq(pool.claim(id).paid, 2.9 ether, "judged under the 5 ether cap it was filed under");
    }

    function test_annual_cap_applies_per_policy_year() public {
        SelfInsure.Config memory cfg = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        cfg.terms.annualCap = 2 ether;
        cfg.terms.quorum = 1;
        SelfInsure p = SelfInsure(payable(factory.open(cfg)));
        vm.prank(judge1); p.registerAgent("j1", "ai", "");
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(bob); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(carol); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(alice); uint256 a = p.fileClaim(1.6 ether, "one", "");
        vm.prank(judge1); p.vote(a, true, "ok");
        assertEq(p.claim(a).paid, 1.5 ether);
        vm.prank(alice); uint256 b = p.fileClaim(1.6 ether, "two", "");
        vm.prank(judge1); p.vote(b, true, "ok");
        assertEq(p.claim(b).paid, 0.5 ether, "only what is left of the year's cap");
        vm.warp(block.timestamp + 366 days);
        vm.prank(alice); p.payPremium{value: PREMIUM}(PREMIUM);
        vm.prank(alice); uint256 c = p.fileClaim(1 ether, "next year", "");
        vm.prank(judge1); p.vote(c, true, "ok");
        assertEq(p.claim(c).paid, 0.9 ether);
    }

    // ── insolvency is recorded, queued, and paid down FIFO ─

    function test_unfunded_claim_is_owed_not_reduced() public {
        _join(alice); _join(bob);                      // pot: 2 ether
        vm.prank(alice);
        uint256 id = pool.fileClaim(5 ether, "hospital stay", "");   // payable 4.9
        vm.prank(judge1); pool.vote(id, true, "ok");
        vm.prank(judge2); pool.vote(id, true, "ok");
        SelfInsure.Claim memory c = pool.claim(id);
        assertEq(c.paid, 2 ether);
        assertEq(c.shortfall, 2.9 ether);
        assertEq(pool.unfundedOwed(), 2.9 ether);
        assertEq(pool.balance(), 0);
        uint256[] memory q = pool.unfundedQueue();
        assertEq(q.length, 1); assertEq(q[0], id);
        SelfInsure.Transparency memory t = pool.transparency();
        assertFalse(t.solvent);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.ClaimsOwed.selector, 2.9 ether));
        pool.distribute(0);

        // the next premium in pays the debt before it becomes anyone's surplus
        uint256 before = alice.balance;
        _join(carol);
        assertEq(alice.balance - before, 1 ether);
        assertEq(pool.unfundedOwed(), 1.9 ether);
        assertEq(pool.balance(), 0);
        // and a donation clears it
        vm.prank(owner); pool.donate{value: 3 ether}(3 ether);
        assertEq(pool.unfundedOwed(), 0);
        assertEq(pool.claim(id).shortfall, 0);
        assertEq(pool.claim(id).paid, 4.9 ether);
        assertEq(pool.balance(), 1.1 ether);
        assertEq(pool.unfundedQueue().length, 0);
        assertTrue(pool.transparency().solvent);
        _assertReconciles();
    }

    function test_backlog_is_oldest_first() public {
        _join(alice); _join(bob); _join(carol);      // 3 ether
        vm.prank(alice); uint256 a = pool.fileClaim(3 ether, "a", "");    // 2.9
        vm.prank(bob);   uint256 b = pool.fileClaim(2 ether, "b", "");    // 1.9
        vm.prank(judge1); pool.vote(a, true, "ok");
        vm.prank(judge2); pool.vote(a, true, "ok");       // pays 2.9, pot 0.1
        vm.prank(judge1); pool.vote(b, true, "ok");
        vm.prank(judge2); pool.vote(b, true, "ok");       // pays 0.1, owes 1.8
        assertEq(pool.claim(b).shortfall, 1.8 ether);
        vm.prank(carol); uint256 cc = pool.fileClaim(1 ether, "c", "");  // 0.9
        vm.prank(judge1); pool.vote(cc, true, "ok");
        vm.prank(judge2); pool.vote(cc, true, "ok");      // pays 0, owes 0.9
        assertEq(pool.unfundedOwed(), 2.7 ether);
        vm.prank(owner); pool.donate{value: 2 ether}(2 ether);
        assertEq(pool.claim(b).shortfall, 0, "b, the older debt, is cleared first");
        assertEq(pool.claim(cc).shortfall, 0.7 ether);
        assertEq(pool.unfundedOwed(), 0.7 ether);
        _assertReconciles();
    }

    // ── surplus goes back, pro rata, to whoever paid it ────

    function test_distribute_is_pro_rata_to_net_contribution() public {
        _join(alice); _join(bob);
        vm.prank(alice); pool.payPremium{value: 2 ether}(2 ether);   // alice 3, bob 1
        assertEq(pool.totalStake(), 4 ether);
        vm.prank(owner); pool.distribute(2 ether);
        assertEq(pool.distributed(), 2 ether);
        assertEq(pool.balance(), 2 ether);
        assertEq(pool.pendingRebate(alice), 1.5 ether);
        assertEq(pool.pendingRebate(bob), 0.5 ether);
        uint256 before = alice.balance;
        vm.prank(alice); pool.claimRebate();
        assertEq(alice.balance - before, 1.5 ether);
        assertEq(pool.pendingRebate(alice), 0);
        assertEq(pool.stakeOf(alice), 1.5 ether, "a rebate reduces stake");
        assertEq(pool.rebatesUnclaimed(), 0.5 ether);
        vm.prank(alice);
        vm.expectRevert(SelfInsure.NothingToClaim.selector);
        pool.claimRebate();
        // a second round splits on the new stakes: alice 1.5, bob 1
        vm.prank(owner); pool.distribute(1 ether);
        assertEq(pool.pendingRebate(alice), 0.6 ether);
        assertEq(pool.pendingRebate(bob), 0.9 ether);
        vm.prank(bob); pool.claimRebate();
        vm.prank(alice); pool.claimRebate();
        assertEq(pool.rebatesUnclaimed(), 0);
        assertEq(pool.distributed(), 3 ether);
        _assertReconciles();
    }

    function test_distribute_holds_back_open_claims_and_floor() public {
        SelfInsure.Config memory cfg = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        cfg.terms.reserveFloor = 1 ether;
        SelfInsure p = SelfInsure(payable(factory.open(cfg)));
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(bob); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(carol); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(alice); p.fileClaim(1.1 ether, "open", "");   // exposure 1.0
        assertEq(p.distributable(), 1 ether, "3 - 1 open - 1 floor");
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.NotDistributable.selector, 1.5 ether, 1 ether));
        p.distribute(1.5 ether);
        vm.prank(owner); p.distribute(0);
        assertEq(p.balance(), 2 ether);
    }

    function test_donors_get_no_surplus() public {
        _join(alice);
        vm.prank(bob); pool.donate{value: 3 ether}(3 ether);
        vm.prank(owner); pool.distribute(0);
        assertEq(pool.pendingRebate(alice), 4 ether, "the only member gets it all");
        assertEq(pool.pendingRebate(bob), 0);
    }

    // ── the oracle: optional, and honest about what it did ─

    uint256 constant REPORTER_PK = 0xA11CE;

    function _oraclePool(SelfInsure.OracleMode mode) internal returns (SelfInsure p, SignedOracle o) {
        o = new SignedOracle(owner);
        vm.prank(owner); o.setReporter(vm.addr(REPORTER_PK), true, "Mercy General billing");
        p = SelfInsure(payable(factory.open(_config(address(0), 0, address(o), mode))));
        vm.prank(judge1); p.registerAgent("j1", "ai", "");
        vm.prank(judge2); p.registerAgent("j2", "ai", "");
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(bob); p.join{value: PREMIUM}(PREMIUM);
        vm.prank(carol); p.join{value: PREMIUM}(PREMIUM);
    }

    function _attest(SignedOracle o, address p, uint256 id, bool ok, uint256 amount) internal {
        bytes32 h = keccak256("itemised bill #4471");
        uint64 expiry = uint64(block.timestamp + 1 days);
        bytes32 d = o.digest(p, id, ok, amount, h, expiry);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(REPORTER_PK, d);
        // anyone relays it — here, the claimant
        vm.prank(alice);
        o.submit(p, id, ok, amount, h, expiry, "Mercy General billing v2", abi.encodePacked(r, s, v));
    }

    function test_oracle_required_gates_and_caps() public {
        (SelfInsure p, SignedOracle o) = _oraclePool(SelfInsure.OracleMode.Required);
        vm.prank(alice); uint256 id = p.fileClaim(2 ether, "MRI", "");
        vm.prank(judge1); p.vote(id, true, "ok");
        vm.prank(judge2); p.vote(id, true, "ok");
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Open), "quorum met, waiting on real data");
        _attest(o, address(p), id, true, 1.2 ether);
        (bool attested, bool ok, uint256 verified, ) = p.oracleView(id);
        assertTrue(attested); assertTrue(ok); assertEq(verified, 1.2 ether);
        p.settle(id);
        assertEq(p.claim(id).paid, 1.2 ether, "the verified bill, not the asked amount");
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Accepted));
    }

    function test_oracle_required_no_overrides_votes() public {
        (SelfInsure p, SignedOracle o) = _oraclePool(SelfInsure.OracleMode.Required);
        vm.prank(alice); uint256 id = p.fileClaim(2 ether, "MRI", "");
        _attest(o, address(p), id, false, 0);
        vm.prank(judge1); p.vote(id, true, "ok");
        vm.expectEmit(true, false, false, true);
        emit SelfInsure.ClaimRejected(id, 2, 0, true);
        vm.prank(judge2); p.vote(id, true, "ok");
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Rejected));
        assertEq(p.balance(), 3 ether);
    }

    function test_oracle_automatic_settles_without_votes() public {
        (SelfInsure p, SignedOracle o) = _oraclePool(SelfInsure.OracleMode.Automatic);
        vm.prank(alice); uint256 id = p.fileClaim(2 ether, "flight delay", "");
        p.settle(id);
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Open), "nothing attested yet");
        _attest(o, address(p), id, true, 1.5 ether);
        uint256 before = alice.balance;
        p.settle(id);
        assertEq(alice.balance - before, 1.5 ether);
        assertEq(p.claim(id).accepts, 0, "no human or agent vote was needed");
    }

    function test_oracle_advisory_is_recorded_not_binding() public {
        (SelfInsure p, SignedOracle o) = _oraclePool(SelfInsure.OracleMode.Advisory);
        vm.prank(alice); uint256 id = p.fileClaim(2 ether, "MRI", "");
        _attest(o, address(p), id, false, 0.3 ether);
        vm.prank(judge1); p.vote(id, true, "the oracle's feed is stale; the bill is real");
        vm.expectEmit(true, false, false, true);
        emit SelfInsure.OracleConsulted(id, address(o), false, 0.3 ether, keccak256("itemised bill #4471"));
        vm.prank(judge2); p.vote(id, true, "agreed");
        assertEq(p.claim(id).paid, 1.9 ether, "advisory data does not cap or veto");
    }

    function test_pool_without_oracle_ignores_mode() public {
        SelfInsure p = SelfInsure(payable(factory.open(_config(address(0), 0, address(0), SelfInsure.OracleMode.Required))));
        assertEq(uint8(p.oracleMode()), uint8(SelfInsure.OracleMode.None));
    }

    function test_oracle_frozen_per_claim() public {
        (SelfInsure p, ) = _oraclePool(SelfInsure.OracleMode.Required);
        vm.prank(alice); uint256 id = p.fileClaim(2 ether, "MRI", "");
        vm.prank(owner); p.setOracle(address(0), SelfInsure.OracleMode.None);
        vm.prank(judge1); p.vote(id, true, "ok");
        vm.prank(judge2); p.vote(id, true, "ok");
        assertEq(uint8(p.claim(id).state), uint8(SelfInsure.ClaimState.Open), "filed under Required; still waits");
        vm.prank(alice); uint256 id2 = p.fileClaim(1 ether, "later", "");
        vm.prank(judge1); p.vote(id2, true, "ok");
        vm.prank(judge2); p.vote(id2, true, "ok");
        assertEq(uint8(p.claim(id2).state), uint8(SelfInsure.ClaimState.Accepted));
    }

    function test_signed_oracle_rejects_strangers_and_stale() public {
        SignedOracle o = new SignedOracle(owner);
        bytes32 h = keccak256("x");
        uint64 expiry = uint64(block.timestamp + 1);
        bytes32 d = o.digest(address(pool), 1, true, 1, h, expiry);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(REPORTER_PK, d);
        bytes memory sig = abi.encodePacked(r, s, v);
        vm.expectRevert(abi.encodeWithSelector(SignedOracle.NotReporter.selector, vm.addr(REPORTER_PK)));
        o.submit(address(pool), 1, true, 1, h, expiry, "", sig);
        vm.prank(owner); o.setReporter(vm.addr(REPORTER_PK), true, "lab");
        vm.warp(block.timestamp + 2);
        vm.expectRevert(abi.encodeWithSelector(SignedOracle.Expired.selector, expiry));
        o.submit(address(pool), 1, true, 1, h, expiry, "", sig);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(SignedOracle.NotReporter.selector, alice));
        o.report(address(pool), 1, true, 1, h, "");
        vm.prank(vm.addr(REPORTER_PK)); o.report(address(pool), 1, true, 1, h, "direct");
        (bool attested, , uint256 amt, , ) = o.attestation(address(pool), 1);
        assertTrue(attested); assertEq(amt, 1);
        assertEq(o.attestations(), 1);
    }

    // ── a stablecoin pool on the health template ───────────

    function _assertHealthTerms(SelfInsure p) internal view {
        (uint256 premium, uint256 period, uint256 coverage, uint256 ded, uint256 cap, uint256 wait,
         uint256 floor, uint16 fee, uint16 q, uint16 th, bool approved) = p.terms();
        assertEq(premium, 400e6); assertEq(period, 30 days); assertEq(coverage, 50_000e6);
        assertEq(ded, 250e6); assertEq(cap, 250_000e6); assertEq(wait, 30 days);
        assertEq(floor, 25_000e6); assertEq(fee, 0); assertEq(q, 2); assertEq(th, 6600);
        assertTrue(approved);
    }

    function _fiftyMembers(SelfInsure p, MockUSD usd) internal returns (address[] memory ms) {
        ms = new address[](50);
        for (uint256 i = 0; i < 50; i++) {
            ms[i] = address(uint160(0x1000 + i));
            usd.mint(ms[i], 400e6);
            vm.startPrank(ms[i]);
            usd.approve(address(p), 400e6);
            p.join(400e6);
            vm.stopPrank();
        }
    }

    function test_health_template_on_a_stablecoin() public {
        MockUSD usd = new MockUSD();
        vm.prank(owner);
        SelfInsure p = SelfInsure(payable(factory.openHealth(
            "Travis County health mutual", "", address(usd), 1e6, address(0), SelfInsure.OracleMode.None)));
        _assertHealthTerms(p);
        assertEq(p.owner(), owner);
        assertEq(factory.count(), 2);
        assertTrue(factory.isPool(address(p)));
        assertGt(bytes(p.about()).length, 100);

        address[] memory ms = _fiftyMembers(p, usd);
        assertEq(p.balance(), 20_000e6);
        assertEq(usd.balanceOf(address(p)), 20_000e6);
        SelfInsure.Transparency memory t = p.transparency();
        assertEq(t.held, 20_000e6);
        assertTrue(t.reconciles);
        assertEq(t.operatorShareBps, 0);

        // ETH sent to a token pool is refused
        vm.deal(ms[0], 1 ether);
        vm.prank(ms[0]);
        vm.expectRevert(abi.encodeWithSelector(SelfInsure.WrongPayment.selector, 1 ether, 0));
        p.payPremium{value: 1 ether}(400e6);

        // adjudicators are admitted by the pool
        vm.prank(judge1); p.registerAgent("claims reviewer", "human", "");
        vm.prank(judge2); p.registerAgent("bill checker", "ai", "claude-fable-5");
        vm.startPrank(owner);
        p.admitAgent(judge1, true);
        p.admitAgent(judge2, true);
        vm.stopPrank();

        vm.warp(block.timestamp + 30 days);
        vm.prank(ms[7]);
        uint256 id = p.fileClaim(12_250e6, "appendectomy, itemised bill", "ipfs://Qm...");
        assertEq(p.openExposure(), 12_000e6);
        vm.prank(judge1); p.vote(id, true, "bill matches CPT 44970 at the contracted rate");
        vm.prank(judge2); p.vote(id, true, "provider licence verified; no duplicate claim");
        assertEq(usd.balanceOf(ms[7]), 12_000e6);
        t = p.transparency();
        assertEq(t.paidOut, 12_000e6);
        assertEq(t.balance, 8_000e6);
        assertEq(t.lossRatioBps, 6000);
        assertEq(t.distributable, 0, "under the 25k floor: nothing leaves as surplus");
        assertTrue(t.reconciles);
    }

    function test_factory_registry_and_implementation() public view {
        assertEq(factory.count(), 1);
        assertEq(factory.pools(0), address(pool));
        assertEq(factory.poolsBy(owner, 0), address(pool));
        assertTrue(factory.implementation() != address(0));
        assertTrue(SelfInsure(payable(factory.implementation())).initialized(), "bare implementation is locked");
        assertTrue(pool.initialized());
    }

    function test_clone_cannot_be_reinitialised() public {
        SelfInsure.Config memory c = _config(address(0), 0, address(0), SelfInsure.OracleMode.None);
        SelfInsure impl = SelfInsure(payable(factory.implementation()));
        vm.expectRevert(SelfInsure.AlreadyInitialized.selector);
        pool.initialize(c);
        vm.expectRevert(SelfInsure.AlreadyInitialized.selector);
        impl.initialize(c);
    }

    function test_direct_deploy_without_factory() public {
        SelfInsure p = new SelfInsure(_config(address(0), 250, address(0), SelfInsure.OracleMode.None));
        assertEq(p.owner(), owner);
        (, , , , , , , uint16 fee, , , ) = p.terms();
        assertEq(fee, 250);
        vm.prank(alice); p.join{value: PREMIUM}(PREMIUM);
        assertEq(p.feesAccrued(), 0.025 ether);
        assertEq(p.operatorShareBps(), 250);
    }
}
