// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {OpenHouseProofs} from "../OpenHouseProofs.sol";
import {MockFactRegistry} from "../MockFactRegistry.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function sign(uint256, bytes32) external pure returns (uint8, bytes32, bytes32);
    function addr(uint256) external pure returns (address);
}

contract ProofsTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    // The cross-language vector: proofs.py, near/src/lib.rs and this file
    // all agree on it, so the fact encoding can never drift.
    bytes32 constant PH = 0x1111111111111111111111111111111111111111111111111111111111111111;
    bytes32 constant VECTOR_FACT = 0x3ed1a6f4a58595b7f7aa1d9aa1363211800b07c54f58c0267328cc38276362e3;
    address constant PY_SIGNER = 0x5050A4F4b3f9338C3472dcC01A87C76A144b3c9c;   // key 0x02..02
    bytes constant PY_SIG = hex"7f8c56164b5aa96cddd3b99ae0740024d6de7a1e8023578b44e4c4ee15e88e3c608a8e954f2e631b6fa2da0947212893e02e337e66519e32e63cf98e09ae3e841c";

    uint256 constant BANK_KEY = 0xB4A2;
    uint256 constant OTHER_KEY = 0xBAD;

    OpenHouseProofs p;
    MockFactRegistry reg;
    address bank;
    address landlord = address(0x1A4D);

    function setUp() public {
        bank = vm.addr(BANK_KEY);
        reg = new MockFactRegistry();
        p = new OpenHouseProofs(address(this), PH, address(reg));
        p.setBank(bank, "Testnet Bank", true);
        p.registerProperty(1, landlord, 1_500_000_000, "128 Maple Ave");
    }

    function _outputs(uint256 period, uint256 c, uint256 n) internal pure returns (uint256[] memory o) {
        o = new uint256[](6);
        o[0] = 1; o[1] = 1; o[2] = period; o[3] = 1_500_000_000; o[4] = c; o[5] = n;
    }

    function _sign(uint256 key, bytes32 f) internal pure returns (bytes memory) {
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", f));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    // ── the vector ─────────────────────────────────────────────

    function test_factMatchesPython() public view {
        uint256[] memory o = _outputs(202610, 0xabc, 0xdef);
        require(p.fact(o) == VECTOR_FACT, "fact drifted from proofs.py");
    }

    function test_recoversPythonSignature() public view {
        require(p.recover(VECTOR_FACT, PY_SIG) == PY_SIGNER, "python personal_sign not recovered");
    }

    function test_pythonSignedReceiptSubmits() public {
        p.setBank(PY_SIGNER, "python bank", true);
        uint256[] memory o = _outputs(202610, 0xabc, 0xdef);
        bytes32 f = p.submit(o, PY_SIG);
        require(f == VECTOR_FACT, "fact");
        require(p.paid(1, 202610), "paid");
    }

    // ── attested door ──────────────────────────────────────────

    function test_submitAttested() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        (bool ok, string memory why,, address signer) = p.verify(o, sig);
        require(ok && bytes(why).length == 0 && signer == bank, "verify view");
        p.submit(o, sig);
        require(p.paid(1, 202610), "paid");
        require(p.count() == 1, "count");
        require(p.spent(bytes32(uint256(0xd00d))), "nullifier burnt");
        OpenHouseProofs.Receipt memory r = p.receipt(1, 202610);
        require(r.commitment == bytes32(uint256(0xc0ffee)) && r.bank == bank && r.kind == 1 && r.rentDue == 1_500_000_000, "receipt");
    }

    function test_replayRefused() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        p.submit(o, sig);
        vm.expectRevert(bytes("nullifier already used"));
        p.submit(o, sig);
    }

    function test_onePeriodOneProof() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        p.submit(o, _sign(BANK_KEY, p.fact(o)));
        uint256[] memory o2 = _outputs(202610, 0xbeef, 0xfeed);
        bytes memory sig2 = _sign(BANK_KEY, p.fact(o2));
        vm.expectRevert(bytes("period already proven"));
        p.submit(o2, sig2);
    }

    function test_nextMonthIsFine() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        p.submit(o, _sign(BANK_KEY, p.fact(o)));
        uint256[] memory o2 = _outputs(202611, 0xc0ffee, 0xd00e);
        p.submit(o2, _sign(BANK_KEY, p.fact(o2)));
        require(p.paid(1, 202611) && p.count() == 2, "two months");
    }

    function test_strangerCannotAttest() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes memory sig = _sign(OTHER_KEY, p.fact(o));
        (bool ok, string memory why,,) = p.verify(o, sig);
        require(!ok && keccak256(bytes(why)) == keccak256("signer is not an active bank"), "why");
        vm.expectRevert(bytes("signer is not an active bank"));
        p.submit(o, sig);
    }

    function test_deactivatedBankRefused() public {
        p.setBank(bank, "Testnet Bank", false);
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        vm.expectRevert(bytes("signer is not an active bank"));
        p.submit(o, sig);
    }

    function test_otherProgramHashIsAnotherFact() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes32 wrongFact = keccak256(abi.encodePacked(bytes32(uint256(0x22)), keccak256(abi.encodePacked(o))));
        bytes memory sig = _sign(BANK_KEY, wrongFact);
        vm.expectRevert(bytes("signer is not an active bank"));
        p.submit(o, sig);
    }

    function test_rentDueMustCoverTheProperty() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        o[3] = 1_499_999_999;
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        vm.expectRevert(bytes("attested rent due below the property's"));
        p.submit(o, sig);
    }

    function test_overAttestedRentDueIsFine() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        o[3] = 2_000_000_000;
        p.submit(o, _sign(BANK_KEY, p.fact(o)));
        require(p.receipt(1, 202610).rentDue == 2_000_000_000, "as attested");
    }

    function test_unknownPropertyAndVersion() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        o[1] = 9;
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        vm.expectRevert(bytes("unknown property"));
        p.submit(o, sig);
        o[1] = 1; o[0] = 2;
        sig = _sign(BANK_KEY, p.fact(o));
        vm.expectRevert(bytes("wrong statement version"));
        p.submit(o, sig);
    }

    function test_wrongShapeNeverReverts() public view {
        uint256[] memory o = new uint256[](5);
        (bool ok, string memory why,,) = p.verify(o, hex"");
        require(!ok && keccak256(bytes(why)) == keccak256("wrong output count"), "shape");
    }

    // ── STARK door ─────────────────────────────────────────────

    function test_factRegistryDoor() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes32 f = p.fact(o);
        vm.expectRevert(bytes("fact not proven"));
        p.submitFact(o);
        reg.register(f);
        p.submitFact(o);
        OpenHouseProofs.Receipt memory r = p.receipt(1, 202610);
        require(r.kind == 2 && r.bank == address(reg) && r.fact == f, "stark receipt");
        // and the attested door now sees the period as taken
        vm.expectRevert(bytes("nullifier already used"));
        p.submit(o, _sign(BANK_KEY, f));
    }

    function test_noRegistryNoDoor() public {
        OpenHouseProofs q = new OpenHouseProofs(address(this), PH, address(0));
        q.registerProperty(1, landlord, 1, "x");
        uint256[] memory o = _outputs(202610, 1, 2);
        vm.expectRevert(bytes("no fact registry"));
        q.submitFact(o);
    }

    // ── admin ──────────────────────────────────────────────────

    function test_onlyOwnerAdmin() public {
        vm.prank(landlord);
        vm.expectRevert(bytes("not owner"));
        p.setBank(landlord, "me", true);
        vm.prank(landlord);
        vm.expectRevert(bytes("not owner"));
        p.setProgramHash(bytes32(0));
        vm.prank(landlord);
        vm.expectRevert(bytes("not owner"));
        p.registerProperty(2, landlord, 1, "x");
    }

    function test_propertyOwnerReprices() public {
        vm.prank(landlord);
        p.setRentDue(1, 1_600_000_000);
        (, uint256 due,,) = p.properties(1);
        require(due == 1_600_000_000, "repriced");
        vm.prank(address(0xBAD));
        vm.expectRevert(bytes("not owner"));
        p.setRentDue(1, 1);
    }

    function test_programHashRotationInvalidatesOldReceipts() public {
        uint256[] memory o = _outputs(202610, 0xc0ffee, 0xd00d);
        bytes memory sig = _sign(BANK_KEY, p.fact(o));
        p.setProgramHash(bytes32(uint256(0x22)));
        vm.expectRevert(bytes("signer is not an active bank"));
        p.submit(o, sig);
    }
}
