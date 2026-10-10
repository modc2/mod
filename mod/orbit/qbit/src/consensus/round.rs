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
        if self.votes.contains_key(pub_key) {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::vali::keys::KeyPair;

    #[test]
    fn duplicate_vote_rejected() {
        let block = Block::genesis();
        let mut round = Round::new(block.clone());
        let mut kp = KeyPair::new(4);
        let pub_key = kp.pub_key();
        let sig = kp.sign(&block.hash()).unwrap();

        let mut vset = ValidatorSet::new();
        vset.add("v1".into(), pub_key.clone());

        assert!(round.vote(&pub_key, sig.clone(), &vset));
        assert_eq!(round.vote_count(), 1);

        assert!(!round.vote(&pub_key, sig, &vset));
        assert_eq!(round.vote_count(), 1);
    }
}
