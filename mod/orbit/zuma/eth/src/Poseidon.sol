// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {PoseidonRC} from "./PoseidonRC.sol";

/// @title Starknet Poseidon in Solidity
/// @notice Cairo's `poseidon_hash_span` (Hades permutation, width 3, rate 2,
/// 4 + 83 + 4 rounds, x^3 s-box), over the STARK field. Every felt-level
/// rule is the one near/src/poseidon.rs follows, so a hash computed here
/// equals the Cairo builtin's output: absorb two felts per permutation, pad
/// with a single 1 (then a 0 when the input length is even), return s[0].
/// The permutation runs in Yul with the 107 round constants read from
/// memory (PoseidonRC.load), ~12k gas each.
library Poseidon {
    uint256 internal constant P = 0x800000000000011000000000000000000000000000000000000000000000001;

    /// @dev One Hades permutation on (s0, s1, s2). `rc` is PoseidonRC.load().
    function hades(uint256 s0, uint256 s1, uint256 s2, uint256[107] memory rc)
        internal
        pure
        returns (uint256, uint256, uint256)
    {
        assembly {
            let p := 0x800000000000011000000000000000000000000000000000000000000000001
            let k := rc
            function cube(x, pp) -> y {
                y := mulmod(mulmod(x, x, pp), x, pp)
            }
            // MDS mix: (t + 2a, t - 2b, t - 3c) with t = a + b + c
            function mix(a, b, c, pp) -> a2, b2, c2 {
                let t := addmod(addmod(a, b, pp), c, pp)
                a2 := addmod(t, addmod(a, a, pp), pp)
                b2 := addmod(t, sub(pp, addmod(b, b, pp)), pp)
                c2 := addmod(t, sub(pp, addmod(addmod(c, c, pp), c, pp)), pp)
            }
            for { let i := 0 } lt(i, 4) { i := add(i, 1) } {
                s0 := cube(addmod(s0, mload(k), p), p)
                s1 := cube(addmod(s1, mload(add(k, 32)), p), p)
                s2 := cube(addmod(s2, mload(add(k, 64)), p), p)
                s0, s1, s2 := mix(s0, s1, s2, p)
                k := add(k, 96)
            }
            for { let i := 0 } lt(i, 83) { i := add(i, 1) } {
                s2 := cube(addmod(s2, mload(k), p), p)
                s0, s1, s2 := mix(s0, s1, s2, p)
                k := add(k, 32)
            }
            for { let i := 0 } lt(i, 4) { i := add(i, 1) } {
                s0 := cube(addmod(s0, mload(k), p), p)
                s1 := cube(addmod(s1, mload(add(k, 32)), p), p)
                s2 := cube(addmod(s2, mload(add(k, 64)), p), p)
                s0, s1, s2 := mix(s0, s1, s2, p)
                k := add(k, 96)
            }
        }
        return (s0, s1, s2);
    }

    /// @notice poseidon_hash_span(xs). Inputs must be < P.
    function hash(uint256[] memory xs) internal pure returns (uint256) {
        uint256[107] memory rc = PoseidonRC.load();
        uint256 s0;
        uint256 s1;
        uint256 s2;
        uint256 n = xs.length;
        uint256 i;
        while (i + 1 < n) {
            s0 = addmod(s0, xs[i], P);
            s1 = addmod(s1, xs[i + 1], P);
            (s0, s1, s2) = hades(s0, s1, s2, rc);
            i += 2;
        }
        if (i < n) {
            // odd length: the last felt is paired with the padding 1
            s0 = addmod(s0, xs[i], P);
            s1 = addmod(s1, 1, P);
        } else {
            // even length: a whole padding block (1, 0)
            s0 = addmod(s0, 1, P);
        }
        (s0,,) = hades(s0, s1, s2, rc);
        return s0;
    }

    function hash3(uint256 a, uint256 b, uint256 c) internal pure returns (uint256) {
        uint256[] memory xs = new uint256[](3);
        xs[0] = a;
        xs[1] = b;
        xs[2] = c;
        return hash(xs);
    }

    function hash4(uint256 a, uint256 b, uint256 c, uint256 d) internal pure returns (uint256) {
        uint256[] memory xs = new uint256[](4);
        xs[0] = a;
        xs[1] = b;
        xs[2] = c;
        xs[3] = d;
        return hash(xs);
    }

    function hash6(uint256 a, uint256 b, uint256 c, uint256 d, uint256 e, uint256 f)
        internal
        pure
        returns (uint256)
    {
        uint256[] memory xs = new uint256[](6);
        xs[0] = a;
        xs[1] = b;
        xs[2] = c;
        xs[3] = d;
        xs[4] = e;
        xs[5] = f;
        return hash(xs);
    }
}
