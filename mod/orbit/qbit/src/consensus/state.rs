use std::collections::HashMap;

use super::block::Block;

/// Committed state: the string→string map plus the block chain.
pub struct State {
    pub data: HashMap<String, String>,
    pub chain: Vec<Block>,
}

impl State {
    pub fn new() -> Self {
        Self {
            data: HashMap::new(),
            chain: vec![Block::genesis()],
        }
    }

    pub fn head(&self) -> &Block {
        self.chain.last().unwrap()
    }

    pub fn height(&self) -> u64 {
        (self.chain.len() - 1) as u64
    }

    /// Apply a committed block to the state.
    pub fn apply(&mut self, block: Block) -> Result<(), String> {
        let expected = self.head().hash();
        let prev: [u8; 32] = hex::decode(&block.prev_hash)
            .map_err(|e| e.to_string())?
            .try_into()
            .map_err(|_| "invalid prev_hash length".to_string())?;

        if prev != expected {
            return Err("block does not extend chain".into());
        }

        for op in &block.ops {
            self.data.insert(op.key.clone(), op.value.clone());
        }
        self.chain.push(block);
        Ok(())
    }

    pub fn get(&self, key: &str) -> Option<&String> {
        self.data.get(key)
    }

    pub fn keys(&self) -> Vec<String> {
        self.data.keys().cloned().collect()
    }

    pub fn snapshot(&self) -> HashMap<String, String> {
        self.data.clone()
    }
}
