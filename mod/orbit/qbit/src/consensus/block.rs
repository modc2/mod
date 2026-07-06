use crate::vali::keys::sha256;
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
pub struct Op {
    pub key: String,
    pub value: String,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Block {
    pub index: u64,
    pub prev_hash: String,
    pub ops: Vec<Op>,
    pub proposer: String,
    pub timestamp: f64,
}

impl Block {
    pub fn new(index: u64, prev_hash: [u8; 32], ops: Vec<Op>, proposer: String) -> Self {
        Self {
            index,
            prev_hash: hex::encode(prev_hash),
            ops,
            proposer,
            timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs_f64(),
        }
    }

    pub fn genesis() -> Self {
        Self {
            index: 0,
            prev_hash: hex::encode([0u8; 32]),
            ops: vec![],
            proposer: "genesis".into(),
            timestamp: 0.0,
        }
    }

    pub fn serialize(&self) -> Vec<u8> {
        serde_json::to_vec(self).unwrap()
    }

    pub fn hash(&self) -> [u8; 32] {
        sha256(&self.serialize())
    }
}
