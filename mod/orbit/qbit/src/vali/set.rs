use std::collections::HashMap;

pub struct ValidatorSet {
    members: HashMap<String, String>, // name -> pub_key (text)
}

impl ValidatorSet {
    pub fn new() -> Self {
        Self {
            members: HashMap::new(),
        }
    }

    pub fn add(&mut self, name: String, pub_key: String) {
        self.members.insert(name, pub_key);
    }

    pub fn remove(&mut self, name: &str) {
        self.members.remove(name);
    }

    pub fn size(&self) -> usize {
        self.members.len()
    }

    /// 2/3 + 1 threshold
    pub fn quorum(&self) -> usize {
        (self.size() * 2 / 3) + 1
    }

    pub fn contains(&self, pub_key: &str) -> bool {
        self.members.values().any(|pk| pk == pub_key)
    }

    pub fn names(&self) -> Vec<String> {
        self.members.keys().cloned().collect()
    }
}
