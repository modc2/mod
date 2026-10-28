// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title OHX — a post-quantum signature Ethereum can check today.
///
/// OHX-KECCAK is a stateful hash-based signature (XMSS^MT shape: WOTS+
/// one-time keys under a two-layer Merkle hypertree) built from keccak256
/// alone. Shor's algorithm breaks ECDSA; it does not touch a hash. Byte for
/// byte the twin of pq.py and near/src/lib.rs:
///
///   n = 32, w = 16, 67 chains (64 message + 3 checksum digits)
///   H = 8 per layer, D = 2 layers → 65,536 one-time leaves per key
///   ADRS = layer<<224 | tree<<160 | type<<128 | a<<96 | b<<64 | c<<32
///   F(adrs, x) = keccak(pubSeed ‖ adrs ‖ x)
///   sig = idx (u32) ‖ [wots 67×32 ‖ auth 8×32] × 2   = 4,804 bytes
///
/// Cost: ≤ 2,010 chain hashes + 16 path hashes, roughly 400k gas with calldata.
library OHX {
    uint256 internal constant N = 32;
    uint256 internal constant W = 16;
    uint256 internal constant L = 67;
    uint256 internal constant H = 8;
    uint256 internal constant D = 2;
    uint256 internal constant CAPACITY = 1 << (H * D);
    uint256 internal constant SIG_BYTES = 4 + D * (L * N + H * N);

    uint256 internal constant T_CHAIN = 0;
    uint256 internal constant T_LEAF = 1;
    uint256 internal constant T_NODE = 2;
    uint256 internal constant T_MSG = 4;

    function adrs(uint256 layer, uint256 tree, uint256 typ, uint256 a, uint256 b, uint256 c)
        internal pure returns (uint256)
    {
        return (layer << 224) | (tree << 160) | (typ << 128) | (a << 96) | (b << 64) | (c << 32);
    }

    /// The key's on-chain identity: keccak(pubSeed ‖ root).
    function keyId(bytes32 pubSeed, bytes32 root) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(pubSeed, root));
    }

    /// The leaf a signature claims (type(uint256).max if malformed).
    function leafOf(bytes calldata sig) internal pure returns (uint256) {
        if (sig.length != SIG_BYTES) return type(uint256).max;
        return uint256(uint32(bytes4(sig[0:4])));
    }

    /// Run a WOTS chain `steps` hashes from position `start`. Hashes in the
    /// free-memory scratch without bumping the pointer — nothing allocates
    /// while it runs, so 2,000 calls cost no memory expansion.
    function _chain(bytes32 ps, bytes32 x, uint256 start, uint256 steps, uint256 base)
        private pure returns (bytes32 r)
    {
        assembly {
            let p := mload(0x40)
            mstore(p, ps)
            let end := add(start, steps)
            for { let j := start } lt(j, end) { j := add(j, 1) } {
                mstore(add(p, 32), or(base, shl(32, j)))
                mstore(add(p, 64), x)
                x := keccak256(p, 96)
            }
            r := x
        }
    }

    function _digit(bytes32 dg, uint256 i) private pure returns (uint256) {
        // nibble i of the digest, high nibble first
        return (uint256(dg) >> (252 - 4 * i)) & 15;
    }

    /// The 67 base-16 digits: 64 nibbles of the digest, then a 3-nibble checksum.
    function _digits(bytes32 dg) private pure returns (uint256[67] memory ds) {
        uint256 csum;
        for (uint256 i = 0; i < 64; i++) {
            ds[i] = _digit(dg, i);
            csum += W - 1 - ds[i];
        }
        ds[64] = (csum >> 8) & 15;
        ds[65] = (csum >> 4) & 15;
        ds[66] = csum & 15;
    }

    function _word(bytes calldata sig, uint256 at) private pure returns (bytes32 w) {
        assembly {
            w := calldataload(add(sig.offset, at))
        }
    }

    /// WOTS public key from the signature, compressed to the leaf.
    /// base = adrs(layer, tree, 0, kp, 0, 0); type and chain slots are OR'd in.
    function _leaf(bytes32 ps, bytes32 m, uint256 base, bytes calldata sig, uint256 off)
        private pure returns (bytes32)
    {
        uint256[67] memory ds = _digits(keccak256(abi.encodePacked(ps, base | (T_MSG << 128), m)));
        bytes memory pks = new bytes(64 + L * N);
        for (uint256 ch = 0; ch < L; ch++) {
            bytes32 pk = _chain(ps, _word(sig, off + ch * N), ds[ch], W - 1 - ds[ch], base | (ch << 64));
            assembly {
                mstore(add(add(pks, 96), mul(ch, 32)), pk)
            }
        }
        uint256 la = base | (T_LEAF << 128);
        assembly {
            mstore(add(pks, 32), ps)
            mstore(add(pks, 64), la)
        }
        return keccak256(pks);
    }

    /// Climb the auth path from leaf `kp` to the tree root.
    function _climb(bytes32 ps, bytes32 cur, uint256 layer, uint256 tree, uint256 kp, bytes calldata sig, uint256 off)
        private pure returns (bytes32)
    {
        for (uint256 h = 0; h < H; h++) {
            bytes32 sib = _word(sig, off + h * N);
            bool left = kp % 2 == 0;
            kp >>= 1;
            uint256 na = adrs(layer, tree, T_NODE, 0, h + 1, kp);
            cur = left
                ? keccak256(abi.encodePacked(ps, na, cur, sib))
                : keccak256(abi.encodePacked(ps, na, sib, cur));
        }
        return cur;
    }

    /// True iff `sig` signs the 32-byte `m` under (pubSeed, root). Never reverts.
    function verify(bytes32 pubSeed, bytes32 root, bytes32 m, bytes calldata sig) internal pure returns (bool) {
        if (sig.length != SIG_BYTES) return false;
        uint256 t = uint256(uint32(bytes4(sig[0:4])));
        if (t >= CAPACITY) return false;
        bytes32 node = m;
        uint256 off = 4;
        for (uint256 layer = 0; layer < D; layer++) {
            uint256 kp = t & ((1 << H) - 1);
            uint256 tree = t >> H;
            bytes32 leaf = _leaf(pubSeed, node, adrs(layer, tree, T_CHAIN, kp, 0, 0), sig, off);
            node = _climb(pubSeed, leaf, layer, tree, kp, sig, off + L * N);
            off += L * N + H * N;
            t = tree;
        }
        return node == root;
    }
}
