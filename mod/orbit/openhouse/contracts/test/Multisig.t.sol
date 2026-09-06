// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OpenHouse} from "../OpenHouse.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function warp(uint256) external;
    function deal(address, uint256) external;
}

contract MultisigTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    OpenHouse oh;
    address owner = address(this);
    address bank = address(0xB4A2);
    address renter = address(0x1234);
    address stranger = address(0xBAD);
    address newTreasury = address(0x7EE7);

    receive() external payable {}

    function setUp() public {
        oh = new OpenHouse("123 Test St", 100 ether, address(0), address(0xFEE5), bank, 300, 8000);
        vm.deal(renter, 1000 ether);
        vm.deal(stranger, 10 ether);
    }

    // ── proposals ──────────────────────────────────────────────

    function test_ownerProposesBankApproves() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetTreasury, newTreasury, 0, 0);
        require(oh.treasury() != newTreasury, "changed too early");
        vm.prank(bank);
        oh.approve(id);
        require(oh.treasury() == newTreasury, "not executed");
    }

    function test_bankProposesOwnerApproves() public {
        vm.prank(bank);
        uint256 id = oh.propose(OpenHouse.OpKind.SetTerms, address(0), 100, 5000);
        oh.approve(id);
        require(oh.platformFeeBps() == 100 && oh.rentCreditBps() == 5000, "terms not set");
    }

    function test_noSelfApprove() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetTreasury, newTreasury, 0, 0);
        vm.expectRevert(bytes("OpenHouse: cannot self-approve"));
        oh.approve(id);
    }

    function test_strangerCannotProposeApproveCancelPause() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetTreasury, newTreasury, 0, 0);
        vm.prank(stranger);
        vm.expectRevert(bytes("OpenHouse: not a seat"));
        oh.propose(OpenHouse.OpKind.TransferOwner, stranger, 0, 0);
        vm.prank(stranger);
        vm.expectRevert(bytes("OpenHouse: not a seat"));
        oh.approve(id);
        vm.prank(stranger);
        vm.expectRevert(bytes("OpenHouse: not a seat"));
        oh.cancel(id);
        vm.prank(stranger);
        vm.expectRevert(bytes("OpenHouse: not a seat"));
        oh.pause();
    }

    function test_cancelIsUndo() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetYieldVault, stranger, 0, 0);
        vm.prank(bank);
        oh.cancel(id);
        vm.prank(bank);
        vm.expectRevert(bytes("OpenHouse: cancelled"));
        oh.approve(id);
        require(oh.yieldVault() == address(0), "vault moved despite cancel");
    }

    function test_proposalExpires() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetTreasury, newTreasury, 0, 0);
        vm.warp(block.timestamp + 7 days + 1);
        vm.prank(bank);
        vm.expectRevert(bytes("OpenHouse: proposal expired"));
        oh.approve(id);
    }

    function test_noDoubleExecute() public {
        uint256 id = oh.propose(OpenHouse.OpKind.SetTreasury, newTreasury, 0, 0);
        vm.prank(bank);
        oh.approve(id);
        vm.prank(bank);
        vm.expectRevert(bytes("OpenHouse: already executed"));
        oh.approve(id);
    }

    function test_badArgsFailAtDoor() public {
        vm.expectRevert(bytes("OpenHouse: fee out of band"));
        oh.propose(OpenHouse.OpKind.SetTerms, address(0), 501, 0);
        vm.expectRevert(bytes("OpenHouse: zero address"));
        oh.propose(OpenHouse.OpKind.TransferOwner, address(0), 0, 0);
    }

    // ── seat rotation ──────────────────────────────────────────

    function test_transferOwnerNeedsBothKeys() public {
        address newOwner = address(0xA11CE);
        uint256 id = oh.propose(OpenHouse.OpKind.TransferOwner, newOwner, 0, 0);
        vm.prank(bank);
        oh.approve(id);
        require(oh.owner() == newOwner, "seat did not move");
        // Old owner lost the seat entirely.
        vm.expectRevert(bytes("OpenHouse: not a seat"));
        oh.propose(OpenHouse.OpKind.SetTerms, address(0), 0, 0);
    }

    function test_bankRotation() public {
        address newBank = address(0xB2);
        uint256 id = oh.propose(OpenHouse.OpKind.SetBank, newBank, 0, 0);
        vm.prank(bank);
        oh.approve(id);
        require(oh.bank() == newBank, "bank did not rotate");
        // The seats can never collapse into one key.
        vm.expectRevert(bytes("OpenHouse: bank would be the owner"));
        uint256 id2 = oh.propose(OpenHouse.OpKind.SetBank, owner, 0, 0);
        id2; // silence unused
    }

    // ── the brake ──────────────────────────────────────────────

    function test_pauseFreezesMoney() public {
        vm.prank(bank);
        oh.pause();
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: paused"));
        oh.payRent{value: 1 ether}();
        vm.prank(renter);
        vm.expectRevert(bytes("OpenHouse: paused"));
        oh.claim(0);
    }

    function test_unpauseNeedsBothKeys() public {
        oh.pause();
        // A seat cannot unpause alone — pause() has no inverse outside the queue.
        uint256 id = oh.propose(OpenHouse.OpKind.Unpause, address(0), 0, 0);
        require(oh.paused(), "unpaused by proposal alone");
        vm.prank(bank);
        oh.approve(id);
        require(!oh.paused(), "not unpaused");
        // Money flows again.
        vm.prank(renter);
        oh.payRent{value: 1 ether}();
    }

    // ── daily life is untouched ────────────────────────────────

    function test_rentAndCloseStillWork() public {
        vm.prank(renter);
        oh.payRent{value: 10 ether}();
        require(oh.totalRentPaid() == 10 ether, "rent not recorded");
        vm.warp(block.timestamp + 90 days);
        oh.closeQuarter(); // still permissionless
        require(oh.quarter() == 1, "quarter did not close");
    }
}
