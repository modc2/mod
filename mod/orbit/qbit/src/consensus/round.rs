use std::collections::HashMap;

use crate::vali::keys::{KeyPair, MssSignature};
use crate::vali::set::ValidatorSet;

use super::block::Block;

pub struct Round {
    pub block: Block,
    votes: HashMap<String, MssSignature>, // pub_key text -> sig
    committed: bool,
}

impl Round {
    pub fn new(block: Block) -> Self {
        Self {
            block,
            votes: HashMap::new(),
            committed: false,
        }
    }

    /// Cast a vote with a text public key.
    pub fn vote(
        &mut self,
        pub_key: &str,
        sig: MssSignature,
        vset: &ValidatorSet,
    ) -> bool {
        if !vset.contains(pub_key) {
            return false;
        }
        let msg = self.block.hash();
        if !KeyPair::verify(pub_key, &msg, &sig) {
            return false;
        }
        self.votes.insert(pub_key.to_string(), sig);
        true
    }

    pub fn try_commit(&mut self, vset: &ValidatorSet) -> bool {
        if self.votes.len() >= vset.quorum() {
            self.committed = true;
            return true;
        }
        false
    }

    pub fn is_committed(&self) -> bool {
        self.committed
    }

    pub fn vote_count(&self) -> usize {
        self.votes.len()
    }
}
