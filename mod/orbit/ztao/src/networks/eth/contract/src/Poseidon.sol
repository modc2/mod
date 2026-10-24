// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Hades} from "./Hades.sol";

/// @title Starknet Poseidon in Solidity
/// @notice Cairo's `poseidon_hash_span` (Hades permutation, width 3, rate 2,
/// 4 + 83 + 4 rounds, x^3 s-box), over the STARK field. Every felt-level
/// rule is the one near/contract/src/poseidon.rs follows, so a hash computed here
/// equals the Cairo builtin's output: absorb two felts per permutation, pad
/// with a single 1 (then a 0 when the input length is even), return s[0].
/// The permutation is Hades.permute: unrolled Yul, constants inline.
library Poseidon {
    uint256 internal constant P = 0x800000000000011000000000000000000000000000000000000000000000001;

    /// @notice poseidon_hash_span(xs). Inputs must be < P.
    /// The input is padded with a single 1 (then a 0 if that makes the
    /// length odd) and absorbed two felts per permutation. One call site of
    /// the permutation on purpose: it is big, and inlined twice it would not
    /// fit the contract size limit.
    function hash(uint256[] memory xs) internal view returns (uint256) {
        uint256 s0;
        uint256 s1;
        uint256 s2;
        uint256 n = xs.length;
        uint256 blocks = n / 2 + 1;
        for (uint256 b = 0; b < blocks; b++) {
            uint256 i = 2 * b;
            s0 = addmod(s0, i < n ? xs[i] : 1, P);
            s1 = addmod(s1, i + 1 < n ? xs[i + 1] : (i + 1 == n ? 1 : 0), P);
            (s0, s1, s2) = Hades.permute(s0, s1, s2);
        }
        return s0;
    }

    function hash3(uint256 a, uint256 b, uint256 c) internal view returns (uint256) {
        uint256[] memory xs = new uint256[](3);
        xs[0] = a;
        xs[1] = b;
        xs[2] = c;
        return hash(xs);
    }

    function hash4(uint256 a, uint256 b, uint256 c, uint256 d) internal view returns (uint256) {
        uint256[] memory xs = new uint256[](4);
        xs[0] = a;
        xs[1] = b;
        xs[2] = c;
        xs[3] = d;
        return hash(xs);
    }

    function hash6(uint256 a, uint256 b, uint256 c, uint256 d, uint256 e, uint256 f)
        internal
        view
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
