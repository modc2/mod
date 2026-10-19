// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title The STARK curve in Solidity
/// @notice y^2 = x^3 + x + BETA over the STARK field (p = 2^251 + 17*2^192 + 1,
/// prime order n). The EVM has no precompile for this curve, so the group law
/// runs in Yul on `mulmod`/`addmod`: Jacobian doubling (dbl-2007-bl, a = 1)
/// and mixed addition (madd-2007-bl), exact on every input (infinity, P + P,
/// P + (-P)); the same formulas as near/src/fp.rs. Scalar multiplication is
/// Straus: two scalars share one chain of doublings, 4-bit fixed windows over
/// 16-entry affine tables. Field inversion goes through the modexp precompile
/// (Fermat), one per batch (Montgomery's trick).
///
/// Conventions shared with Cairo and the NEAR port:
///   - the point at infinity is (0, 0) in affine form (not on the curve)
///   - EC scalars are full felts: k*P depends on k mod n only, as in Cairo
library StarkCurve {
    uint256 internal constant P = 0x800000000000011000000000000000000000000000000000000000000000001;
    uint256 internal constant BETA = 0x6f21413efbe40de150e596d72f7a8c5609ad26c15c915c1f4cdfcb99cee9e89;
    uint256 internal constant GX = 0x1ef15c18599971b7beced415a40f0c7deacfd9b0d1819e03d723d8bc943cfca;
    uint256 internal constant GY = 0x5668060aa49730b7be4801df46ec62de53ecd11abe43a32873000c36e8dc1f;
    uint256 internal constant ORDER = 0x800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f;

    /// @dev x^e mod P via the modexp precompile.
    function powmod(uint256 x, uint256 e) internal view returns (uint256 r) {
        assembly {
            let m := mload(0x40)
            mstore(m, 32)
            mstore(add(m, 32), 32)
            mstore(add(m, 64), 32)
            mstore(add(m, 96), x)
            mstore(add(m, 128), e)
            mstore(add(m, 160), 0x800000000000011000000000000000000000000000000000000000000000001)
            if iszero(staticcall(gas(), 5, m, 192, m, 32)) { revert(0, 0) }
            r := mload(m)
        }
    }

    /// @dev 1/x mod P (0 for 0).
    function inv(uint256 x) internal view returns (uint256) {
        return powmod(x, P - 2);
    }

    /// @dev Euler's criterion: 1 iff x is a non-zero square mod P.
    function isSquare(uint256 x) internal view returns (bool) {
        return powmod(x, (P - 1) / 2) == 1;
    }

    /// @dev x^3 + x + BETA
    function rhs(uint256 x) internal pure returns (uint256) {
        return addmod(addmod(mulmod(mulmod(x, x, P), x, P), x, P), BETA, P);
    }

    function isOnCurve(uint256 x, uint256 y) internal pure returns (bool) {
        if (x >= P || y >= P) return false;
        return mulmod(y, y, P) == rhs(x);
    }

    /// @dev Jacobian -> affine; infinity -> (0, 0).
    function toAffine(uint256 X, uint256 Y, uint256 Z) internal view returns (uint256 x, uint256 y) {
        if (Z == 0) return (0, 0);
        uint256 zi = inv(Z);
        uint256 zi2 = mulmod(zi, zi, P);
        x = mulmod(X, zi2, P);
        y = mulmod(mulmod(Y, zi2, P), zi, P);
    }

    /// @dev [inf, Q, 2Q, ..., 15Q] in affine form as 32 words (x_i at 2i,
    /// y_i at 2i+1); entry 0 is (0, 0). One field inversion for the lot.
    function table(uint256 qx, uint256 qy) internal view returns (uint256[32] memory t) {
        // Jacobian multiples in scratch memory, then one batched inversion
        uint256[45] memory j; // 15 x (X, Y, Z)
        assembly {
            let p := 0x800000000000011000000000000000000000000000000000000000000000001
            function dbl(x1, y1, z1, pp) -> x3, y3, z3 {
                if iszero(z1) {
                    x3 := x1
                    y3 := y1
                    z3 := 0
                    leave
                }
                let xx := mulmod(x1, x1, pp)
                let yy := mulmod(y1, y1, pp)
                let yyyy := mulmod(yy, yy, pp)
                let zz := mulmod(z1, z1, pp)
                let s := addmod(x1, yy, pp)
                s := mulmod(s, s, pp)
                s := addmod(s, sub(pp, xx), pp)
                s := addmod(s, sub(pp, yyyy), pp)
                s := addmod(s, s, pp)
                let m := addmod(addmod(addmod(xx, xx, pp), xx, pp), mulmod(zz, zz, pp), pp)
                let t := addmod(mulmod(m, m, pp), sub(pp, addmod(s, s, pp)), pp)
                x3 := t
                let y8 := addmod(yyyy, yyyy, pp)
                y8 := addmod(y8, y8, pp)
                y8 := addmod(y8, y8, pp)
                y3 := addmod(mulmod(m, addmod(s, sub(pp, t), pp), pp), sub(pp, y8), pp)
                z3 := addmod(y1, z1, pp)
                z3 := mulmod(z3, z3, pp)
                z3 := addmod(z3, sub(pp, yy), pp)
                z3 := addmod(z3, sub(pp, zz), pp)
            }
            function madd(x1, y1, z1, x2, y2, pp) -> x3, y3, z3 {
                if and(iszero(x2), iszero(y2)) {
                    x3 := x1
                    y3 := y1
                    z3 := z1
                    leave
                }
                if iszero(z1) {
                    x3 := x2
                    y3 := y2
                    z3 := 1
                    leave
                }
                let z1z1 := mulmod(z1, z1, pp)
                let u2 := mulmod(x2, z1z1, pp)
                let s2 := mulmod(mulmod(y2, z1, pp), z1z1, pp)
                let h := addmod(u2, sub(pp, x1), pp)
                let r := addmod(s2, sub(pp, y1), pp)
                r := addmod(r, r, pp)
                if iszero(h) {
                    if iszero(r) {
                        x3, y3, z3 := dbl(x1, y1, z1, pp)
                        leave
                    }
                    x3 := 1
                    y3 := 1
                    z3 := 0
                    leave
                }
                let hh := mulmod(h, h, pp)
                let i := addmod(hh, hh, pp)
                i := addmod(i, i, pp)
                let jj := mulmod(h, i, pp)
                let v := mulmod(x1, i, pp)
                x3 := addmod(mulmod(r, r, pp), sub(pp, addmod(jj, addmod(v, v, pp), pp)), pp)
                let y1j := mulmod(y1, jj, pp)
                y3 := addmod(mulmod(r, addmod(v, sub(pp, x3), pp), pp), sub(pp, addmod(y1j, y1j, pp)), pp)
                z3 := addmod(z1, h, pp)
                z3 := mulmod(z3, z3, pp)
                z3 := addmod(z3, sub(pp, z1z1), pp)
                z3 := addmod(z3, sub(pp, hh), pp)
            }
            // j[0] = Q, j[1] = 2Q, j[k] = j[k-1] + Q
            mstore(j, qx)
            mstore(add(j, 32), qy)
            mstore(add(j, 64), 1)
            let x, y, z := dbl(qx, qy, 1, p)
            mstore(add(j, 96), x)
            mstore(add(j, 128), y)
            mstore(add(j, 160), z)
            for { let k := 2 } lt(k, 15) { k := add(k, 1) } {
                let o := add(j, mul(k, 96))
                x, y, z := madd(x, y, z, qx, qy, p)
                mstore(o, x)
                mstore(add(o, 32), y)
                mstore(add(o, 64), z)
            }
        }
        // batch inversion of the 15 Z's (Montgomery's trick)
        uint256[15] memory acc;
        uint256 run = 1;
        for (uint256 k = 0; k < 15; k++) {
            acc[k] = run;
            uint256 z = j[3 * k + 2];
            if (z != 0) run = mulmod(run, z, P);
        }
        uint256 invRun = inv(run);
        for (uint256 k = 15; k > 0;) {
            k--;
            uint256 z = j[3 * k + 2];
            if (z == 0) continue; // infinity: stays (0, 0)
            uint256 zi = mulmod(invRun, acc[k], P);
            invRun = mulmod(invRun, z, P);
            uint256 zi2 = mulmod(zi, zi, P);
            t[2 * (k + 1)] = mulmod(j[3 * k], zi2, P);
            t[2 * (k + 1) + 1] = mulmod(mulmod(j[3 * k + 1], zi2, P), zi, P);
        }
    }

    /// @dev a*A + b*B in Jacobian form, given the two 16-entry tables.
    /// Straus: the 63 x 4 doublings are shared by both scalars.
    function mul2(uint256 a, uint256[32] memory ta, uint256 b, uint256[32] memory tb)
        internal
        pure
        returns (uint256 X, uint256 Y, uint256 Z)
    {
        assembly {
            let p := 0x800000000000011000000000000000000000000000000000000000000000001
            function dbl(x1, y1, z1, pp) -> x3, y3, z3 {
                if iszero(z1) {
                    x3 := x1
                    y3 := y1
                    z3 := 0
                    leave
                }
                let xx := mulmod(x1, x1, pp)
                let yy := mulmod(y1, y1, pp)
                let yyyy := mulmod(yy, yy, pp)
                let zz := mulmod(z1, z1, pp)
                let s := addmod(x1, yy, pp)
                s := mulmod(s, s, pp)
                s := addmod(s, sub(pp, xx), pp)
                s := addmod(s, sub(pp, yyyy), pp)
                s := addmod(s, s, pp)
                let m := addmod(addmod(addmod(xx, xx, pp), xx, pp), mulmod(zz, zz, pp), pp)
                let t := addmod(mulmod(m, m, pp), sub(pp, addmod(s, s, pp)), pp)
                x3 := t
                let y8 := addmod(yyyy, yyyy, pp)
                y8 := addmod(y8, y8, pp)
                y8 := addmod(y8, y8, pp)
                y3 := addmod(mulmod(m, addmod(s, sub(pp, t), pp), pp), sub(pp, y8), pp)
                z3 := addmod(y1, z1, pp)
                z3 := mulmod(z3, z3, pp)
                z3 := addmod(z3, sub(pp, yy), pp)
                z3 := addmod(z3, sub(pp, zz), pp)
            }
            function madd(x1, y1, z1, x2, y2, pp) -> x3, y3, z3 {
                if and(iszero(x2), iszero(y2)) {
                    x3 := x1
                    y3 := y1
                    z3 := z1
                    leave
                }
                if iszero(z1) {
                    x3 := x2
                    y3 := y2
                    z3 := 1
                    leave
                }
                let z1z1 := mulmod(z1, z1, pp)
                let u2 := mulmod(x2, z1z1, pp)
                let s2 := mulmod(mulmod(y2, z1, pp), z1z1, pp)
                let h := addmod(u2, sub(pp, x1), pp)
                let r := addmod(s2, sub(pp, y1), pp)
                r := addmod(r, r, pp)
                if iszero(h) {
                    if iszero(r) {
                        x3, y3, z3 := dbl(x1, y1, z1, pp)
                        leave
                    }
                    x3 := 1
                    y3 := 1
                    z3 := 0
                    leave
                }
                let hh := mulmod(h, h, pp)
                let i := addmod(hh, hh, pp)
                i := addmod(i, i, pp)
                let jj := mulmod(h, i, pp)
                let v := mulmod(x1, i, pp)
                x3 := addmod(mulmod(r, r, pp), sub(pp, addmod(jj, addmod(v, v, pp), pp)), pp)
                let y1j := mulmod(y1, jj, pp)
                y3 := addmod(mulmod(r, addmod(v, sub(pp, x3), pp), pp), sub(pp, addmod(y1j, y1j, pp)), pp)
                z3 := addmod(z1, h, pp)
                z3 := mulmod(z3, z3, pp)
                z3 := addmod(z3, sub(pp, z1z1), pp)
                z3 := addmod(z3, sub(pp, hh), pp)
            }
            X := 1
            Y := 1
            Z := 0
            // top nibble first (bit 255..252 is always 0 for a felt, so the
            // window count is 64 with the first one usually empty)
            for { let i := 63 } true {} {
                let sh := mul(i, 4)
                let na := and(shr(sh, a), 15)
                let nb := and(shr(sh, b), 15)
                if na {
                    let o := add(ta, mul(na, 64))
                    X, Y, Z := madd(X, Y, Z, mload(o), mload(add(o, 32)), p)
                }
                if nb {
                    let o := add(tb, mul(nb, 64))
                    X, Y, Z := madd(X, Y, Z, mload(o), mload(add(o, 32)), p)
                }
                if iszero(i) { break }
                i := sub(i, 1)
                X, Y, Z := dbl(X, Y, Z, p)
                X, Y, Z := dbl(X, Y, Z, p)
                X, Y, Z := dbl(X, Y, Z, p)
                X, Y, Z := dbl(X, Y, Z, p)
            }
        }
    }

    /// @dev k*Q in affine form (a single scalar: the second table slot is unused).
    function mul(uint256 k, uint256 qx, uint256 qy) internal view returns (uint256, uint256) {
        uint256[32] memory t = table(qx, qy);
        (uint256 X, uint256 Y, uint256 Z) = mul2(k, t, 0, t);
        return toAffine(X, Y, Z);
    }
}
