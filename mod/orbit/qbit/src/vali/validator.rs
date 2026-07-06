use super::keys::{KeyPair, MssSignature};
use serde::{Deserialize, Serialize};

pub struct Validator {
    pub name: String,
    pub keys: KeyPair,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct ValidatorInfo {
    pub name: String,
    pub pub_key: String,
    pub sigs_used: u32,
    pub sigs_capacity: u32,
}

impl Validator {
    /// Create with a random keypair.
    pub fn new(name: String, height: usize) -> Self {
        Self {
            name,
            keys: KeyPair::new(height),
        }
    }

    /// Create from an arbitrary-length text passphrase.
    pub fn from_passphrase(name: String, passphrase: &str, height: usize) -> Self {
        Self {
            name,
            keys: KeyPair::from_passphrase(passphrase, height),
        }
    }

    /// Public key as text string.
    pub fn pub_key(&self) -> String {
        self.keys.pub_key()
    }

    /// Private key as text string (passphrase or hex seed).
    pub fn priv_key(&self) -> String {
        self.keys.priv_key()
    }

    pub fn sign(&mut self, msg: &[u8]) -> Result<MssSignature, &'static str> {
        self.keys.sign(msg)
    }

    pub fn info(&self) -> ValidatorInfo {
        ValidatorInfo {
            name: self.name.clone(),
            pub_key: self.pub_key(),
            sigs_used: self.keys.used,
            sigs_capacity: self.keys.capacity,
        }
    }
}
