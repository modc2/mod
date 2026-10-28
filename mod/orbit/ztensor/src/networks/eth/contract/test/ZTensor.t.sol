// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ZTensor} from "../src/ZTensor.sol";
import {LSAG} from "../src/LSAG.sol";
import {Poseidon} from "../src/Poseidon.sol";
import {StarkCurve} from "../src/StarkCurve.sol";
import {Hades} from "../src/Hades.sol";
import {HadesCode} from "./HadesCode.sol";

/// @dev The signer side (keys, Hp with a real square root, LSAG.sign), test-only:
/// the same algorithm as lsag.cairo `sign`, so what it produces is what the
/// Cairo prover produces for the same randomness.
library Signer {
    uint256 constant P = StarkCurve.P;
    uint256 constant ORDER = StarkCurve.ORDER;

    /// Tonelli-Shanks. p - 1 = 2^192 * (2^59 + 17).
    function sqrt(uint256 a) internal view returns (bool ok, uint256 r) {
        if (a == 0) return (true, 0);
        if (!StarkCurve.isSquare(a)) return (false, 0);
        uint256 q = (1 << 59) + 17;
        uint256 s = 192;
        uint256 z = 2;
        while (StarkCurve.isSquare(z)) z++;
        uint256 m = s;
        uint256 c = StarkCurve.powmod(z, q);
        uint256 t = StarkCurve.powmod(a, q);
        r = StarkCurve.powmod(a, (q + 1) / 2);
        while (t != 1) {
            uint256 i = 0;
            uint256 tt = t;
            while (tt != 1) {
                tt = mulmod(tt, tt, P);
                i++;
            }
            uint256 b = c;
            for (uint256 k = 0; k < m - i - 1; k++) b = mulmod(b, b, P);
            m = i;
            c = mulmod(b, b, P);
            t = mulmod(t, c, P);
            r = mulmod(r, b, P);
        }
        return (true, r);
    }

    /// Hp(scope) the slow way (what the prover does), y = the smaller root.
    function hashToPoint(uint256 scope) internal view returns (LSAG.Point memory) {
        uint256 ctr;
        while (true) {
            uint256 x = Poseidon.hash3(LSAG.TAG_H2P, scope, ctr);
            (bool ok, uint256 y) = sqrt(StarkCurve.rhs(x));
            if (ok) {
                uint256 other = P - y;
                return LSAG.Point(x, y <= other ? y : other);
            }
            ctr++;
        }
        revert("unreachable");
    }

    function publicKey(uint256 secret) internal view returns (LSAG.Point memory p) {
        (p.x, p.y) = StarkCurve.mul(secret % ORDER, StarkCurve.GX, StarkCurve.GY);
    }

    function keyImage(uint256 secret, uint256 scope) internal view returns (LSAG.Point memory p) {
        LSAG.Point memory hp = hashToPoint(scope);
        (p.x, p.y) = StarkCurve.mul(secret % ORDER, hp.x, hp.y);
    }

    function challenge(uint256 d, uint256 lx, uint256 ly, uint256 rx, uint256 ry) internal view returns (uint256) {
        return Poseidon.hash6(LSAG.TAG_CHAL, d, lx, ly, rx, ry);
    }

    /// lsag.cairo `sign`: ring must be sorted by key id and contain secret's key.
    function sign(
        LSAG.Point[] memory ring,
        uint256 secret,
        uint256 scope,
        uint256 msg_,
        uint256 alpha,
        uint256[] memory fill
    ) internal view returns (LSAG.Signature memory sig) {
        uint256 n = ring.length;
        uint256 x = secret % ORDER;
        LSAG.Point memory me = publicKey(x);
        uint256 idx = n;
        for (uint256 i = 0; i < n; i++) {
            if (ring[i].x == me.x && ring[i].y == me.y) idx = i;
        }
        require(idx < n, "secret not in ring");
        uint256 a = alpha % ORDER;
        LSAG.Point memory hp = hashToPoint(scope);
        uint256[32] memory tg = StarkCurve.table(StarkCurve.GX, StarkCurve.GY);
        uint256[32] memory th = StarkCurve.table(hp.x, hp.y);
        LSAG.Point memory image;
        {
            (uint256 X, uint256 Y, uint256 Z) = StarkCurve.mul2(x, th, 0, th);
            (image.x, image.y) = StarkCurve.toAffine(X, Y, Z);
        }
        uint256[32] memory ti = StarkCurve.table(image.x, image.y);
        uint256 d = LSAG.digest(ring, scope, msg_, image);
        uint256[] memory cs = new uint256[](n);
        uint256[] memory ss = new uint256[](n);
        uint256 j = (idx + 1) % n;
        {
            (uint256 lx, uint256 ly, uint256 lz) = StarkCurve.mul2(a, tg, 0, tg);
            (uint256 rx, uint256 ry, uint256 rz) = StarkCurve.mul2(a, th, 0, th);
            (lx, ly) = StarkCurve.toAffine(lx, ly, lz);
            (rx, ry) = StarkCurve.toAffine(rx, ry, rz);
            cs[j] = challenge(d, lx, ly, rx, ry);
        }
        while (j != idx) {
            uint256 s = fill[j] % ORDER;
            ss[j] = s;
            uint256[32] memory tp = StarkCurve.table(ring[j].x, ring[j].y);
            (uint256 lx, uint256 ly, uint256 lz) = StarkCurve.mul2(s, tg, cs[j], tp);
            (uint256 rx, uint256 ry, uint256 rz) = StarkCurve.mul2(s, th, cs[j], ti);
            (lx, ly) = StarkCurve.toAffine(lx, ly, lz);
            (rx, ry) = StarkCurve.toAffine(rx, ry, rz);
            uint256 next = (j + 1) % n;
            cs[next] = challenge(d, lx, ly, rx, ry);
            j = next;
        }
        uint256 cx = mulmod(cs[idx] % ORDER, x, ORDER);
        ss[idx] = addmod(a, ORDER - cx, ORDER);
        sig.c0 = cs[0];
        sig.s = ss;
        sig.image = image;
        sig.hp = hp;
    }

    /// Insertion sort by key id ascending (what the contracts require).
    function sortRing(LSAG.Point[] memory ring) internal view {
        for (uint256 i = 1; i < ring.length; i++) {
            LSAG.Point memory p = ring[i];
            uint256 pid = LSAG.keyId(p);
            uint256 k = i;
            while (k > 0 && LSAG.keyId(ring[k - 1]) > pid) {
                ring[k] = ring[k - 1];
                k--;
            }
            ring[k] = p;
        }
    }
}

contract ZTensorTest is Test {
    uint64 constant TEMPO = 10;
    address owner = address(0xA11CE);
    address relayer = address(0x5E1A);
    address payee = address(0xBEEF);
    ZTensor z;
    uint256[] minerSecrets;
    uint256[] valSecrets;
    LSAG.Point[] miners;
    LSAG.Point[] vals;
    string fx;

    function sec(uint256 i) internal pure returns (uint256) {
        return 0x5eed0000 + i;
    }

    function setUp() public {
        // Hades (the Poseidon permutation) lives at a fixed CREATE2 address
        vm.etch(Hades.ADDR, HadesCode.RUNTIME);
        fx = vm.readFile("../../near/contract/tests/fixture.json");
        vm.roll(100);
        z = new ZTensor(owner, TEMPO, 3, 1000);
        for (uint256 i = 0; i < 4; i++) {
            minerSecrets.push(sec(i));
            miners.push(Signer.publicKey(sec(i)));
            valSecrets.push(sec(10 + i));
            vals.push(Signer.publicKey(sec(10 + i)));
        }
        vm.startPrank(owner);
        for (uint256 i = 0; i < 4; i++) {
            z.register(1, miners[i]);
            z.register(2, vals[i]);
        }
        vm.stopPrank();
    }

    // ---- fixture helpers ---------------------------------------------------

    function fu(string memory path) internal view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(fx, path));
    }

    function fpt(string memory path) internal view returns (LSAG.Point memory) {
        return LSAG.Point(fu(string.concat(path, ".x")), fu(string.concat(path, ".y")));
    }

    function fixtureSig() internal view returns (LSAG.Point[] memory ring, LSAG.Signature memory sig) {
        ring = new LSAG.Point[](4);
        for (uint256 i = 0; i < 4; i++) {
            ring[i] = fpt(string.concat(".sig.ring[", vm.toString(i), "]"));
        }
        string[] memory ss = vm.parseJsonStringArray(fx, ".sig.s");
        sig.s = new uint256[](ss.length);
        for (uint256 i = 0; i < ss.length; i++) sig.s[i] = vm.parseUint(ss[i]);
        sig.c0 = fu(".sig.c0");
        sig.image = fpt(".sig.image");
        // the fixture's last hash_to_point row IS the mine scope's Hp
        sig.hp = fpt(".hash_to_point[4]");
    }

    // ---- wire compatibility with Cairo -------------------------------------

    function test_poseidon_matches_cairo() public view {
        for (uint256 i = 0; i < 4; i++) {
            string memory k = string.concat(".keys[", vm.toString(i), "]");
            LSAG.Point memory p = fpt(k);
            assertEq(LSAG.keyId(p), fu(string.concat(k, ".key_id")), "key_id");
            // secret -> public key through our curve code
            uint256 secret = vm.parseJsonUint(fx, string.concat(k, ".secret"));
            LSAG.Point memory q = Signer.publicKey(secret);
            assertEq(q.x, p.x, "pubkey x");
            assertEq(q.y, p.y, "pubkey y");
        }
        uint256 scope = Poseidon.hash3(0x7a756d612d6d696e65, fu(".scope_mine.subnet"), 7);
        assertEq(scope, fu(".scope_mine.scope"), "mine scope");
        uint256 sc = Poseidon.hash4(0x7a756d612d73636f7265, fu(".score_scope.subnet"), 7, fu(".score_scope.tag"));
        assertEq(sc, fu(".score_scope.scope"), "score scope");
    }

    function test_hash_to_point_matches_cairo() public view {
        for (uint256 i = 0; i < 5; i++) {
            string memory row = string.concat(".hash_to_point[", vm.toString(i), "]");
            uint256 scope = fu(string.concat(row, ".scope"));
            LSAG.Point memory want = fpt(row);
            // the slow way (sqrt) and the hinted way agree with Cairo
            LSAG.Point memory slow = Signer.hashToPoint(scope);
            assertEq(slow.x, want.x, "Hp.x");
            assertEq(slow.y, want.y, "Hp.y");
            LSAG.Point memory hinted = LSAG.hashToPoint(scope, want);
            assertEq(hinted.x, want.x);
            assertEq(hinted.y, want.y);
        }
    }

    function test_hp_hint_is_checked() public {
        LSAG.Point memory good = fpt(".hash_to_point[0]");
        uint256 scope = fu(".hash_to_point[0].scope");
        // the other root
        vm.expectRevert(bytes("hp hint: y not the smaller root"));
        this.hp(scope, LSAG.Point(good.x, StarkCurve.P - good.y));
        // a point of another scope (its x is not on this scope's path)
        LSAG.Point memory other = fpt(".hash_to_point[1]");
        vm.expectRevert();
        this.hp(scope, other);
        // right x, wrong y
        vm.expectRevert(bytes("hp hint: not on curve"));
        this.hp(scope, LSAG.Point(good.x, 7));
    }

    function hp(uint256 scope, LSAG.Point memory hint) external view returns (LSAG.Point memory) {
        return LSAG.hashToPoint(scope, hint);
    }

    function test_cairo_signature_verifies_in_solidity() public view {
        (LSAG.Point[] memory ring, LSAG.Signature memory sig) = fixtureSig();
        uint256 scope = fu(".scope_mine.scope");
        uint256 msg_ = fu(".msg");
        assertTrue(LSAG.verify(ring, scope, msg_, sig), "cairo sig");
        assertEq(LSAG.keyId(sig.image), fu(".sig.tag"), "tag");
        // the image is signer 22's key image: linkable only by the key holder
        LSAG.Point memory img = Signer.keyImage(22, scope);
        assertEq(img.x, sig.image.x);
        assertEq(img.y, sig.image.y);
        // any change breaks it
        assertFalse(LSAG.verify(ring, scope, msg_ + 1, sig), "msg");
        sig.s[2] += 1;
        assertFalse(LSAG.verify(ring, scope, msg_, sig), "s");
        sig.s[2] -= 1;
        (ring[0], ring[1]) = (ring[1], ring[0]);
        assertFalse(LSAG.verify(ring, scope, msg_, sig), "order");
    }

    function test_solidity_signer_roundtrip_and_linkability() public view {
        LSAG.Point[] memory ring = sorted(miners);
        uint256 scope = 42;
        LSAG.Signature memory a = Signer.sign(ring, sec(1), scope, 1, 77, fill(4, 1));
        LSAG.Signature memory b = Signer.sign(ring, sec(1), scope, 2, 78, fill(4, 2));
        assertTrue(LSAG.verify(ring, scope, 1, a));
        assertTrue(LSAG.verify(ring, scope, 2, b));
        assertEq(a.image.x, b.image.x, "same key + scope = same image");
        LSAG.Signature memory c = Signer.sign(ring, sec(1), 43, 1, 79, fill(4, 3));
        assertTrue(c.image.x != a.image.x, "other scope = unlinkable");
        LSAG.Signature memory d2 = Signer.sign(ring, sec(2), scope, 1, 80, fill(4, 4));
        assertTrue(d2.image.x != a.image.x, "other key = other image");
    }

    // ---- the contract, one epoch end to end ----------------------------------

    function sorted(LSAG.Point[] storage set) internal view returns (LSAG.Point[] memory r) {
        r = new LSAG.Point[](set.length);
        for (uint256 i = 0; i < set.length; i++) r[i] = set[i];
        Signer.sortRing(r);
    }

    function fill(uint256 n, uint256 salt) internal pure returns (uint256[] memory f) {
        f = new uint256[](n);
        for (uint256 i = 0; i < n; i++) f[i] = salt * 100 + i + 1;
    }

    /// expectRevert catches the NEXT external call, and the helpers make view
    /// calls (scopes) before the write: arm it right before the write instead.
    bytes pending;

    function revertNext(bytes memory reason) internal {
        pending = reason;
    }

    function arm() internal {
        if (pending.length != 0) {
            vm.expectRevert(pending);
            delete pending;
        }
    }

    function submitAs(uint256 i, uint256 work) internal returns (uint256) {
        LSAG.Point[] memory ring = sorted(miners);
        LSAG.Signature memory s = Signer.sign(ring, sec(i), z.mineScope(z.epoch()), work, 500 + i, fill(4, i));
        arm();
        vm.prank(relayer);
        return z.submit(ring, work, s);
    }

    function scoreAs(uint256 v, uint256 tag, uint8 score) internal returns (uint256) {
        LSAG.Point[] memory ring = sorted(vals);
        LSAG.Signature memory s =
            Signer.sign(ring, sec(10 + v), z.scoreScope(z.epoch(), tag), score, 700 + v, fill(ring.length, 10 + v));
        arm();
        vm.prank(relayer);
        return z.score(ring, tag, score, s);
    }

    function claimAs(uint256 i, uint64 epoch, address to) internal returns (uint128) {
        LSAG.Point[] memory ring = sorted(miners);
        LSAG.Signature memory s =
            Signer.sign(ring, sec(i), z.mineScope(epoch), uint256(uint160(to)), 900 + i, fill(4, 50 + i));
        arm();
        vm.prank(relayer);
        return z.claim(epoch, ring, to, s);
    }

    function test_full_epoch() public {
        uint64 e = z.epoch();
        uint256 t0 = submitAs(0, 0xabc);
        uint256 t1 = submitAs(1, 0xdef);
        assertTrue(t0 != t1, "distinct tags");
        assertEq(z.epochTags(e), 2);
        // every validator scores both tags; no two nullifiers collide
        uint256[] memory nulls = new uint256[](8);
        for (uint256 v = 0; v < 4; v++) {
            nulls[2 * v] = scoreAs(v, t0, 90);
            nulls[2 * v + 1] = scoreAs(v, t1, 50);
        }
        for (uint256 a = 0; a < 8; a++) {
            for (uint256 b = a + 1; b < 8; b++) assertTrue(nulls[a] != nulls[b], "nullifiers unique");
        }
        (uint128 s0, uint32 n0) = z.tally(e, t0);
        assertEq(s0, 360);
        assertEq(n0, 4);
        assertEq(z.epochTotal(e), 560);
        // claims only after the epoch closes
        revertNext(bytes("epoch still open"));
        claimAs(0, e, payee);
        vm.roll(uint256(e + 1) * TEMPO);
        // ... and every tag is settled (Yuma)
        revertNext(bytes("epoch not settled"));
        claimAs(0, e, payee);
        settleAll(e);
        uint128 got = claimAs(0, e, payee);
        assertEq(uint256(got), uint256(1000 * 360) / 560);
        assertEq(z.earned(payee), got);
        assertTrue(z.claimed(e, t0));
        // the replay: same key, same epoch -> same tag -> rejected
        revertNext(bytes("already claimed"));
        claimAs(0, e, payee);
        // the other tag goes to another recipient
        uint128 got1 = claimAs(1, e, address(0xCAFE));
        assertEq(uint256(got1), uint256(1000 * 200) / 560);
    }

    // ---- Yuma consensus (core/yuma.py is the reference) ------------------------

    function settleAll(uint64 e) internal {
        for (uint256 i = 0; i < z.epochTags(e); i++) z.settle(e, z.tagAt(e, i));
    }

    function assertYuma(uint64 e, uint256 tag, uint8 c, uint128 r) internal view {
        (uint8 gc, uint128 gr, bool done) = z.yuma(e, tag);
        assertTrue(done, "settled");
        assertEq(gc, c, "consensus");
        assertEq(gr, r, "rank");
    }

    /// yuma.epoch({a: [90,90,90,100], b: [90], c: [80,20,60,40]}, 4, 1000):
    /// a -> (90, 360) the lone 100 clips to 90; b -> (0, 0) one of four is
    /// no consensus; c -> (60, 180). Shares 666 / 0 / 333.
    function test_yuma_clipping() public {
        uint64 e = z.epoch();
        uint256 ta = submitAs(0, 1);
        uint256 tb = submitAs(1, 2);
        uint256 tc = submitAs(2, 3);
        uint8[4] memory sa = [uint8(90), 90, 90, 100];
        uint8[4] memory sc = [uint8(80), 20, 60, 40];
        for (uint256 v = 0; v < 4; v++) {
            scoreAs(v, ta, sa[v]);
            scoreAs(v, tc, sc[v]);
        }
        scoreAs(0, tb, 90);
        assertEq(z.top(e, ta), 100);
        assertEq(z.hist(e, ta, 90), 3);
        vm.roll(uint256(e + 1) * TEMPO);
        uint256 g = gasleft();
        z.settle(e, ta);
        g -= gasleft();
        emit log_named_uint("gas settle (4 votes, C=90)", g);
        z.settle(e, tb);
        g = gasleft();
        z.settle(e, tc);
        g -= gasleft();
        emit log_named_uint("gas settle (4 votes, C=60)", g);
        assertYuma(e, ta, 90, 360);
        assertYuma(e, tb, 0, 0);
        assertYuma(e, tc, 60, 180);
        (uint32 vs, uint128 rt, uint32 ns) = z.yumaEpoch(e);
        assertEq(vs, 4);
        assertEq(rt, 540);
        assertEq(ns, 3);
        assertEq(claimAs(0, e, payee), 666);
        assertEq(claimAs(1, e, address(0xB0B)), 0);
        assertEq(claimAs(2, e, address(0xCAFE)), 333);
    }

    /// yuma.epoch({a: [90]*4, b: [70]*4, c: [50]*4}, 4, 1000): no clipping,
    /// shares 428 / 333 / 238.
    function test_yuma_shares_match_reference() public {
        uint64 e = z.epoch();
        uint256[3] memory t = [submitAs(0, 1), submitAs(1, 2), submitAs(2, 3)];
        uint8[3] memory sv = [uint8(90), 70, 50];
        for (uint256 v = 0; v < 4; v++) {
            for (uint256 i = 0; i < 3; i++) scoreAs(v, t[i], sv[i]);
        }
        vm.roll(uint256(e + 1) * TEMPO);
        settleAll(e);
        for (uint256 i = 0; i < 3; i++) assertYuma(e, t[i], sv[i], uint128(sv[i]) * 4);
        (, uint128 rt,) = z.yumaEpoch(e);
        assertEq(rt, 840);
        assertEq(claimAs(0, e, payee), 428);
        assertEq(claimAs(1, e, payee), 333);
        assertEq(claimAs(2, e, payee), 238);
        assertEq(z.earned(payee), 999);
    }

    function test_settle_rules() public {
        uint64 e = z.epoch();
        uint256 t = submitAs(0, 1);
        uint256 quiet = submitAs(1, 2); // nobody scores it
        scoreAs(0, t, 70);
        scoreAs(1, t, 70);
        vm.expectRevert(bytes("epoch still open"));
        z.settle(e, t);
        vm.roll(uint256(e + 1) * TEMPO);
        vm.expectRevert(bytes("no such tag this epoch"));
        z.settle(e, t + 1);
        z.settle(e, t);
        assertYuma(e, t, 70, 140);
        vm.expectRevert(bytes("already settled"));
        z.settle(e, t);
        // one tag left: still no claims
        revertNext(bytes("epoch not settled"));
        claimAs(0, e, payee);
        z.settle(e, quiet);
        assertYuma(e, quiet, 0, 0);
        assertEq(claimAs(0, e, payee), 1000);
        assertEq(claimAs(1, e, address(0xCAFE)), 0);
        // an epoch whose only tag got no scores: rank total 0 -> claim 0
        uint64 e2 = z.epoch();
        submitAs(2, 3);
        vm.roll(uint256(e2 + 1) * TEMPO);
        settleAll(e2);
        assertEq(claimAs(2, e2, payee), 0);
    }

    /// V is snapshotted at the epoch's first submit. Two validators join
    /// after it: V stays 4, so 2 votes reach need=2. yuma.epoch({a: [80,60],
    /// b: [100,100,100,10,10,10]}, 4, 1000): a (60,120), b (Vt = max(4, 6) = 6,
    /// need 3) (100,330); shares 266 / 733. With V = 6 a would be (0, 0).
    function test_late_validators_do_not_move_vset() public {
        uint64 e = z.epoch();
        uint256 ta = submitAs(0, 1);
        uint256 tb = submitAs(1, 2);
        vm.startPrank(owner);
        for (uint256 i = 4; i < 6; i++) {
            valSecrets.push(sec(10 + i));
            vals.push(Signer.publicKey(sec(10 + i)));
            z.register(2, vals[i]);
        }
        vm.stopPrank();
        assertEq(z.memberCount(2), 6);
        scoreAs(0, ta, 80);
        scoreAs(1, ta, 60);
        uint8[6] memory sb = [uint8(100), 100, 100, 10, 10, 10];
        for (uint256 v = 0; v < 6; v++) scoreAs(v, tb, sb[v]);
        vm.roll(uint256(e + 1) * TEMPO);
        settleAll(e);
        (uint32 vs, uint128 rt,) = z.yumaEpoch(e);
        assertEq(vs, 4, "vset snapshotted before the joins");
        assertEq(rt, 450);
        assertYuma(e, ta, 60, 120);
        assertYuma(e, tb, 100, 330);
        assertEq(claimAs(0, e, payee), 266);
        assertEq(claimAs(1, e, payee), 733);
        // the next epoch snapshots the bigger set
        uint64 e2 = z.epoch();
        submitAs(0, 3);
        (vs,,) = z.yumaEpoch(e2);
        assertEq(vs, 6);
    }

    function test_one_submission_per_miner_per_epoch() public {
        submitAs(0, 1);
        revertNext(bytes("already submitted this epoch"));
        submitAs(0, 2);
        // the next epoch gives a fresh, unlinkable tag
        uint64 e = z.epoch();
        uint256 tagA = z.tagAt(e, 0);
        vm.roll(uint256(e + 1) * TEMPO);
        uint256 tagB = submitAs(0, 3);
        assertTrue(tagA != tagB, "tags differ across epochs");
    }

    function test_one_score_per_validator_per_tag() public {
        uint256 t = submitAs(0, 1);
        scoreAs(0, t, 10);
        revertNext(bytes("already scored this tag"));
        scoreAs(0, t, 20);
        revertNext(bytes("no such tag this epoch"));
        scoreAs(1, t + 1, 20);
        revertNext(bytes("score is 0..100"));
        scoreAs(1, t, 101);
    }

    function test_ring_rules() public {
        uint64 e = z.epoch();
        LSAG.Point[] memory ring = sorted(miners);
        LSAG.Signature memory s = Signer.sign(ring, sec(0), z.mineScope(e), 1, 5, fill(4, 0));
        // unsorted
        LSAG.Point[] memory bad = new LSAG.Point[](4);
        (bad[0], bad[1], bad[2], bad[3]) = (ring[1], ring[0], ring[2], ring[3]);
        vm.expectRevert(bytes("ring not sorted/unique"));
        z.submit(bad, 1, s);
        // a validator in a miner ring
        LSAG.Point[] memory mixed = sorted(miners);
        mixed[3] = vals[0];
        Signer.sortRing(mixed);
        vm.expectRevert(bytes("ring has a non-member"));
        z.submit(mixed, 1, s);
        // below min_ring
        LSAG.Point[] memory small = new LSAG.Point[](2);
        (small[0], small[1]) = (ring[0], ring[1]);
        vm.expectRevert(bytes("ring below min_ring"));
        z.submit(small, 1, s);
        // a signature for another message
        vm.expectRevert(bytes("bad ring signature"));
        z.submit(ring, 2, s);
    }

    function test_registry() public {
        LSAG.Point memory k = Signer.publicKey(sec(99));
        vm.expectRevert(bytes("registry closed"));
        z.register(1, k);
        vm.prank(owner);
        z.setOpen(true);
        z.register(1, k);
        assertEq(z.roleOf(k), 1);
        vm.expectRevert(bytes("key already registered"));
        z.register(2, k);
        vm.expectRevert(bytes("key not on curve"));
        z.register(1, LSAG.Point(1, 2));
        assertEq(z.memberCount(1), 5);
    }

    // ---- gas by ring size ----------------------------------------------------

    function test_gas_by_ring_size() public {
        uint32[4] memory sizes = [uint32(4), 8, 16, 32];
        ZTensor big = new ZTensor(owner, TEMPO, 3, 1000);
        LSAG.Point[] memory all = new LSAG.Point[](32);
        vm.startPrank(owner);
        for (uint256 i = 0; i < 32; i++) {
            all[i] = Signer.publicKey(sec(1000 + i));
            big.register(1, all[i]);
        }
        vm.stopPrank();
        for (uint256 k = 0; k < sizes.length; k++) {
            uint256 n = sizes[k];
            LSAG.Point[] memory ring = new LSAG.Point[](n);
            for (uint256 i = 0; i < n; i++) ring[i] = all[i];
            Signer.sortRing(ring);
            // signer = the one whose key landed at slot k, any of them
            uint256 who = 1000 + k;
            LSAG.Signature memory s = Signer.sign(ring, sec(who), big.mineScope(big.epoch()), k, 31 + k, fill(n, k));
            uint256 g = gasleft();
            big.submit(ring, k, s);
            g -= gasleft();
            emit log_named_uint(string.concat("gas submit ring ", vm.toString(n)), g);
            assertLt(g, 1 << 24, "fits the EIP-7825 tx gas cap");
        }
    }
}
