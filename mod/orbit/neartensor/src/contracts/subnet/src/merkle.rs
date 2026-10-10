use near_sdk::env;

use crate::Subnet;

/// Incremental Merkle tree (20 levels, 30-root ring buffer).
/// Port of the CommitmentTree pattern from ConsensusPriv.sol.
pub const MERKLE_LEVELS: usize = 20;
pub const ROOT_HISTORY_SIZE: usize = 30;

impl Subnet {
    /// Initialize the Merkle tree zero hashes.
    pub(crate) fn merkle_init(&mut self) {
        // Compute zero hashes: z[0] = keccak256(0x00..00), z[i] = keccak256(z[i-1] || z[i-1])
        let mut zeros: Vec<Vec<u8>> = Vec::with_capacity(MERKLE_LEVELS + 1);
        let zero_leaf = env::keccak256(&[0u8; 32]);
        zeros.push(zero_leaf);

        for i in 1..=MERKLE_LEVELS {
            let prev = &zeros[i - 1];
            let mut combined = Vec::with_capacity(64);
            combined.extend_from_slice(prev);
            combined.extend_from_slice(prev);
            zeros.push(env::keccak256(&combined));
        }

        // Compute initial root from zeros
        let initial_root = zeros[MERKLE_LEVELS].clone();

        self.merkle_zeros = zeros;
        self.merkle_filled = vec![vec![0u8; 32]; MERKLE_LEVELS];
        self.merkle_next_index = 0;
        self.merkle_roots = vec![initial_root];
        self.merkle_root_index = 0;
    }

    /// Insert a leaf into the Merkle tree and update the root.
    pub(crate) fn merkle_insert(&mut self, leaf: Vec<u8>) -> u64 {
        let max_capacity = 1u64 << MERKLE_LEVELS;
        assert!(
            self.merkle_next_index < max_capacity,
            "Merkle tree is full"
        );

        let index = self.merkle_next_index;
        self.merkle_next_index += 1;

        let mut current_hash = leaf;
        let mut current_index = index;

        for i in 0..MERKLE_LEVELS {
            if current_index % 2 == 0 {
                // Left child: store as filled subtree, pair with zero
                self.merkle_filled[i] = current_hash.clone();
                let mut combined = Vec::with_capacity(64);
                combined.extend_from_slice(&current_hash);
                combined.extend_from_slice(&self.merkle_zeros[i]);
                current_hash = env::keccak256(&combined);
            } else {
                // Right child: pair with stored left (filled) subtree
                let mut combined = Vec::with_capacity(64);
                combined.extend_from_slice(&self.merkle_filled[i]);
                combined.extend_from_slice(&current_hash);
                current_hash = env::keccak256(&combined);
            }
            current_index /= 2;
        }

        // Store new root in ring buffer
        self.merkle_root_index =
            ((self.merkle_root_index as usize + 1) % ROOT_HISTORY_SIZE) as u32;
        if (self.merkle_root_index as usize) < self.merkle_roots.len() {
            self.merkle_roots[self.merkle_root_index as usize] = current_hash;
        } else {
            self.merkle_roots.push(current_hash);
        }

        index
    }

    /// Check if a root is in the recent history.
    pub(crate) fn merkle_is_known_root(&self, root: &[u8]) -> bool {
        for stored in &self.merkle_roots {
            if stored.as_slice() == root {
                return true;
            }
        }
        false
    }

    /// Get the most recent root.
    pub(crate) fn merkle_last_root(&self) -> Vec<u8> {
        if self.merkle_roots.is_empty() {
            vec![0u8; 32]
        } else {
            self.merkle_roots[self.merkle_root_index as usize].clone()
        }
    }

    /// Verify a Merkle proof.
    pub(crate) fn merkle_verify_proof(
        &self,
        leaf: &[u8],
        proof: &[Vec<u8>],
        index: u64,
        root: &[u8],
    ) -> bool {
        assert_eq!(proof.len(), MERKLE_LEVELS, "invalid proof length");

        let mut current = leaf.to_vec();
        let mut idx = index;

        for sibling in proof {
            let mut combined = Vec::with_capacity(64);
            if idx % 2 == 0 {
                combined.extend_from_slice(&current);
                combined.extend_from_slice(sibling);
            } else {
                combined.extend_from_slice(sibling);
                combined.extend_from_slice(&current);
            }
            current = env::keccak256(&combined);
            idx /= 2;
        }

        current.as_slice() == root
    }
}
