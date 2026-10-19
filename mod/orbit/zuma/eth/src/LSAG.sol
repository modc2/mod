// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Poseidon} from "./Poseidon.sol";
import {StarkCurve} from "./StarkCurve.sol";

/// @title LSAG over the STARK curve, the EVM port
/// @notice Scope-linkable ring signatures (Liu-Wei-Wong) exactly as
/// contracts/src/lsag.cairo defines them, so a signature the Cairo prover
/// makes verifies here unchanged:
///
///     I     = x * Hp(scope)                        key image
///     d     = poseidon('zuma-msg', scope, msg, I.x, I.y, n, ring...)
///     L_i   = s_i*G + c_i*P_i,  R_i = s_i*Hp + c_i*I
///     c_i+1 = poseidon('zuma-chal', d, L_i.x, L_i.y, R_i.x, R_i.y)
///     valid iff c_n == c_0
///
/// Hp(scope) is try-and-increment: the first ctr with poseidon('zuma-h2p',
/// scope, ctr) on the curve, y = the smaller root (Cairo's `new_from_x`).
/// Square roots are expensive on the EVM (p = 1 mod 4, 2-adicity 192), so the
/// caller passes Hp as a HINT and the contract only checks it: every earlier
/// counter is proven a non-square with Euler's criterion (one modexp each),
/// and the hinted y must be a root of the hit's rhs and the smaller one. A
/// wrong hint reverts; it can never change the point.
library LSAG {
    uint256 internal constant TAG_H2P = 0x7a756d612d683270; // 'zuma-h2p'
    uint256 internal constant TAG_MSG = 0x7a756d612d6d7367; // 'zuma-msg'
    uint256 internal constant TAG_CHAL = 0x7a756d612d6368616c; // 'zuma-chal'
    uint256 internal constant TAG_KEY = 0x7a756d612d6b6579; // 'zuma-key'
    uint256 internal constant P = StarkCurve.P;

    struct Point {
        uint256 x;
        uint256 y;
    }

    struct Signature {
        uint256 c0;
        uint256[] s;
        Point image;
        /// Hp(scope) hint (checked, see above). Get it from the prover
        /// (`hash_point`) or any off-chain sqrt.
        Point hp;
    }

    /// @notice Stable id of a public key: what membership and ring order key on.
    function keyId(Point memory p) internal pure returns (uint256) {
        return Poseidon.hash3(TAG_KEY, p.x, p.y);
    }

    function isOnCurve(Point memory p) internal pure returns (bool) {
        return StarkCurve.isOnCurve(p.x, p.y);
    }

    /// @notice Hp(scope), proven from the hint. Reverts on a bad hint.
    function hashToPoint(uint256 scope, Point memory hint) internal view returns (Point memory) {
        require(hint.y < P && hint.y <= P - hint.y, "hp hint: y not the smaller root");
        uint256 ctr;
        while (true) {
            uint256 x = Poseidon.hash3(TAG_H2P, scope, ctr);
            uint256 r = StarkCurve.rhs(x);
            if (x == hint.x) {
                require(mulmod(hint.y, hint.y, P) == r, "hp hint: not on curve");
                return hint;
            }
            // not the hint: this counter must miss the curve
            require(r != 0 && !StarkCurve.isSquare(r), "hp hint: skipped a curve point");
            unchecked {
                ctr++;
            }
        }
        revert("unreachable");
    }

    function digest(Point[] memory ring, uint256 scope, uint256 msg, Point memory image)
        internal
        pure
        returns (uint256)
    {
        uint256 n = ring.length;
        uint256[] memory xs = new uint256[](6 + 2 * n);
        xs[0] = TAG_MSG;
        xs[1] = scope;
        xs[2] = msg;
        xs[3] = image.x;
        xs[4] = image.y;
        xs[5] = n;
        for (uint256 i = 0; i < n; i++) {
            xs[6 + 2 * i] = ring[i].x;
            xs[7 + 2 * i] = ring[i].y;
        }
        return Poseidon.hash(xs);
    }

    /// @notice Verify a ring signature. Pure group math plus the hint check;
    /// no storage. Cost is linear in the ring (~0.3M gas per member).
    function verify(Point[] memory ring, uint256 scope, uint256 msg, Signature memory sig)
        internal
        view
        returns (bool)
    {
        uint256 n = ring.length;
        if (n == 0 || sig.s.length != n || !isOnCurve(sig.image)) return false;
        for (uint256 i = 0; i < n; i++) {
            if (!isOnCurve(ring[i])) return false;
        }
        Point memory hp = hashToPoint(scope, sig.hp);
        uint256[32] memory tg = StarkCurve.table(StarkCurve.GX, StarkCurve.GY);
        uint256[32] memory th = StarkCurve.table(hp.x, hp.y);
        uint256[32] memory ti = StarkCurve.table(sig.image.x, sig.image.y);
        uint256 d = digest(ring, scope, msg, sig.image);
        uint256 c = sig.c0;
        for (uint256 i = 0; i < n; i++) {
            uint256[32] memory tp = StarkCurve.table(ring[i].x, ring[i].y);
            (uint256 lx, uint256 ly, uint256 lz) = StarkCurve.mul2(sig.s[i], tg, c, tp);
            (uint256 rx, uint256 ry, uint256 rz) = StarkCurve.mul2(sig.s[i], th, c, ti);
            (lx, ly) = StarkCurve.toAffine(lx, ly, lz);
            (rx, ry) = StarkCurve.toAffine(rx, ry, rz);
            c = Poseidon.hash6(TAG_CHAL, d, lx, ly, rx, ry);
        }
        return c == sig.c0;
    }
}
