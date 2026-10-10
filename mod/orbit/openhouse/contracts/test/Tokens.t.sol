// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouse} from "../OpenHouse.sol";
import {ApprovedTokens} from "../ApprovedTokens.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function warp(uint256) external;
    function deal(address, uint256) external;
}

/// A well-behaved stablecoin with configurable decimals — a USDC (6) or DAI (18).
contract MockStable {
    string public symbol;
    uint8 public decimals;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory _symbol, uint8 _decimals) {
        symbol = _symbol;
        decimals = _decimals;
    }

    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        require(balanceOf[msg.sender] >= amount, "MockStable: balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "MockStable: allowance");
        require(balanceOf[from] >= amount, "MockStable: balance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// A USDT-shaped stablecoin: transfer/transferFrom return NOTHING.
contract MockTether {
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address spender, uint256 amount) external { allowance[msg.sender][spender] = amount; }

    function transfer(address to, uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "MockTether: balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
    }

    function transferFrom(address from, address to, uint256 amount) external {
        require(allowance[from][msg.sender] >= amount, "MockTether: allowance");
        require(balanceOf[from] >= amount, "MockTether: balance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }
}

contract TokensTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    ApprovedTokens toks;
    OpenHouse oh;
    MockStable usdc;   // 6 decimals
    MockStable dai;    // 18 decimals
    MockTether usdt;   // 6 decimals, returns nothing

    address owner = address(this);
    address bank = address(0xB4A2);
    address treasury = address(0xFEE5);
    address renter = address(0x1234);
    address stranger = address(0xBAD);

    uint256 constant PRICE = 100_000e18;   // a $100k home
    uint256 constant ETH_USD = 2_000e18;   // native at $2000

    receive() external payable {}

    function setUp() public {
        usdc = new MockStable("USDC", 6);
        dai = new MockStable("DAI", 18);
        usdt = new MockTether();

        toks = new ApprovedTokens("ETH", ETH_USD);
        toks.enable(address(usdc), "USDC", 1e18);
        toks.enable(address(dai), "DAI", 1e18);
        toks.enable(address(usdt), "USDT", 1e18);

        oh = new OpenHouse("123 Test St", PRICE, address(toks), address(0), treasury, bank, 300, 8000);

        vm.deal(renter, 100 ether);
        usdc.mint(renter, 1_000_000e6);
        dai.mint(renter, 1_000_000e18);
        usdt.mint(renter, 1_000_000e6);
    }

    // ── the list ───────────────────────────────────────────────

    function test_nativeIsBornOnTheList() public view {
        require(toks.isApproved(address(0)), "native not approved");
        require(toks.count() == 4, "wrong count");
        require(toks.valueOf(address(0), 1 ether) == ETH_USD, "native value wrong");
    }

    function test_decimalsNormalize() public view {
        // A dollar is a dollar: 1 USDC (6 dec) and 1 DAI (18 dec) are both 1e18.
        require(toks.valueOf(address(usdc), 1e6) == 1e18, "usdc dollar wrong");
        require(toks.valueOf(address(dai), 1e18) == 1e18, "dai dollar wrong");
    }

    function test_onlyDeployerCuratesTheList() public {
        vm.prank(stranger);
        vm.expectRevert(bytes("Tokens: not the deployer"));
        toks.enable(address(usdc), "USDC", 1e18);
        vm.prank(stranger);
        vm.expectRevert(bytes("Tokens: not the deployer"));
        toks.disable(address(usdc));
        vm.prank(stranger);
        vm.expectRevert(bytes("Tokens: not the deployer"));
        toks.reprice(address(0), 1);
    }

    function test_disableClosesTheDoor() public {
        toks.disable(address(dai));
        require(!toks.isApproved(address(dai)), "still approved");
        require(toks.count() == 3, "list not shrunk");
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: token not approved"));
        oh.payRent(address(dai), 1e18);
    }

    function test_unlistedTokenRejected() public {
        MockStable rogue = new MockStable("RGE", 18);
        rogue.mint(renter, 10e18);
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: token not approved"));
        oh.payRent(address(rogue), 1e18);
    }

    // ── paying in stables ──────────────────────────────────────

    function test_stableRentBuysEquityByValue() public {
        vm.prank(renter);
        usdc.approve(address(oh), 1_000e6);
        vm.prank(renter);
        oh.payRent(address(usdc), 1_000e6);

        // $1000 at 3% fee, 80% credit: fee $30, credit $776, owner $194.
        require(oh.totalRentPaid() == 1_000e18, "value not booked");
        require(oh.principalPaid(renter) == 776e18, "credit wrong");
        require(oh.totalFees() == 30e18, "fee wrong");
        require(oh.totalOwnerIncome() == 194e18, "owner income wrong");
        // The tokens themselves: fee stays here, the rest reached the owner.
        require(usdc.balanceOf(address(oh)) == 30e6, "fee tokens not pooled");
        require(usdc.balanceOf(owner) == 970e6, "owner tokens wrong");
        require(oh.totalPaidIn(address(usdc)) == 1_000e6, "paidIn wrong");
    }

    function test_nativeStillWorks() public {
        vm.prank(renter);
        oh.payRent{value: 1 ether}(address(0), 1 ether);
        // 1 ETH at $2000: value 2000e18, credit 80% of net.
        require(oh.totalRentPaid() == 2_000e18, "value wrong");
        require(oh.principalPaid(renter) == 1_552e18, "credit wrong");
    }

    function test_nonReturningStableWorks() public {
        vm.prank(renter);
        usdt.approve(address(oh), 500e6);
        vm.prank(renter);
        oh.payRent(address(usdt), 500e6);
        require(oh.totalRentPaid() == 500e18, "usdt value wrong");
        require(usdt.balanceOf(address(oh)) == 15e6, "usdt fee not pooled");
    }

    function test_badCalls() public {
        // Native amount must match msg.value.
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: bad native amount"));
        oh.payRent{value: 1 ether}(address(0), 2 ether);
        // A token payment must not carry coin.
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: coin sent with a token payment"));
        oh.payRent{value: 1 ether}(address(usdc), 1e6);
    }

    function test_quoteToken() public view {
        (uint256 value, uint256 fee, uint256 credit, uint256 ownerIncome) =
            oh.quoteToken(address(usdc), 1_000e6);
        require(value == 1_000e18 && fee == 30e18 && credit == 776e18 && ownerIncome == 194e18, "quote wrong");
    }

    // ── the pool pays back in the money that came in ───────────

    function test_mixedQuarterClaimsBothTokens() public {
        vm.prank(renter);
        usdc.approve(address(oh), 1_000e6);
        vm.prank(renter);
        oh.payRent(address(usdc), 1_000e6);      // fee 30 USDC
        vm.prank(renter);
        oh.payRent{value: 1 ether}(address(0), 1 ether);   // fee 0.03 ETH

        vm.warp(block.timestamp + 90 days);
        oh.closeQuarter();

        address[] memory pool = oh.poolTokens(0);
        require(pool.length == 2, "pool tokens wrong");

        // Renter locked $776 + $1552 = $2328 of a $100k home for the quarter.
        uint256 renterUsdcBefore = usdc.balanceOf(renter);
        uint256 renterEthBefore = renter.balance;
        vm.prank(renter);
        uint256 got = oh.claim(0);
        require(got == (90e18 * 2_328) / 100_000, "claim value wrong");
        require(usdc.balanceOf(renter) - renterUsdcBefore == (30e6 * 2_328) / 100_000, "usdc share wrong");
        require(renter.balance - renterEthBefore == (0.03 ether * 2_328) / 100_000, "eth share wrong");

        // The owner's weight is the rest of the house; their claim is the rest
        // of the pool, in both tokens.
        uint256 ownerUsdcBefore = usdc.balanceOf(owner);
        oh.claim(0);
        require(usdc.balanceOf(owner) - ownerUsdcBefore == (30e6 * 97_672) / 100_000, "owner usdc wrong");
    }

    function test_sweepReturnsEveryToken() public {
        vm.prank(renter);
        usdc.approve(address(oh), 1_000e6);
        vm.prank(renter);
        oh.payRent(address(usdc), 1_000e6);
        vm.prank(renter);
        oh.payRent{value: 1 ether}(address(0), 1 ether);

        // Close q0, then let the four-quarter claim window run out unclaimed.
        for (uint256 i = 0; i < 5; i++) {
            vm.warp(block.timestamp + 90 days);
            oh.closeQuarter();
        }

        uint256 id = oh.propose(OpenHouse.OpKind.Sweep, address(0), 0, 0);
        vm.prank(bank);
        oh.approve(id);

        require(usdc.balanceOf(treasury) == 30e6, "usdc not swept");
        require(treasury.balance == 0.03 ether, "eth not swept");
        require(usdc.balanceOf(address(oh)) == 0, "usdc stranded");
        require(address(oh).balance == 0, "eth stranded");
    }

    // ── a delisting is never a confiscation ────────────────────

    function test_delistedTokenStillPaysOut() public {
        vm.prank(renter);
        dai.approve(address(oh), 1_000e18);
        vm.prank(renter);
        oh.payRent(address(dai), 1_000e18);

        toks.disable(address(dai));   // door closes for NEW payments...

        vm.warp(block.timestamp + 90 days);
        oh.closeQuarter();
        uint256 before = dai.balanceOf(renter);
        vm.prank(renter);
        oh.claim(0);                  // ...but the pool still pays the DAI out
        require(dai.balanceOf(renter) - before == (30e18 * 776) / 100_000, "dai claim failed");
    }
}
