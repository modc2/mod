use hmac::{Hmac, Mac};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

const N_BITS: usize = 256;

// -- hash primitives --

pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(data);
    h.finalize().into()
}

/// Derive a deterministic 32-byte seed from any arbitrary-length text passphrase.
pub fn derive_seed(passphrase: &str) -> [u8; 32] {
    let mut mac = HmacSha256::new_from_slice(b"qbit-keygen-v1").expect("valid key");
    mac.update(passphrase.as_bytes());
    mac.finalize().into_bytes().into()
}

fn derive(seed: &[u8; 32], idx: u32, pair: u16, side: u8) -> [u8; 32] {
    let msg = format!("{}:{}:{}", idx, pair, side);
    let mut mac = HmacSha256::new_from_slice(seed).expect("valid key len");
    mac.update(msg.as_bytes());
    mac.finalize().into_bytes().into()
}

// -- lamport one-time signatures --

pub fn lamport_pk(seed: &[u8; 32], idx: u32) -> Vec<[[u8; 32]; 2]> {
    (0..N_BITS as u16)
        .map(|p| {
            [
                sha256(&derive(seed, idx, p, 0)),
                sha256(&derive(seed, idx, p, 1)),
            ]
        })
        .collect()
}

pub fn lamport_pk_hash(pk: &[[[u8; 32]; 2]]) -> [u8; 32] {
    let mut flat = Vec::with_capacity(N_BITS * 64);
    for pair in pk {
        flat.extend_from_slice(&pair[0]);
        flat.extend_from_slice(&pair[1]);
    }
    sha256(&flat)
}

pub fn lamport_sign(seed: &[u8; 32], idx: u32, msg_hash: &[u8; 32]) -> Vec<[u8; 32]> {
    (0..N_BITS as u16)
        .map(|p| {
            let bit = (msg_hash[p as usize / 8] >> (p as usize % 8)) & 1;
            derive(seed, idx, p, bit)
        })
        .collect()
}

pub fn lamport_verify(pk: &[[[u8; 32]; 2]], msg_hash: &[u8; 32], sig: &[[u8; 32]]) -> bool {
    if pk.len() != N_BITS || sig.len() != N_BITS {
        return false;
    }
    for p in 0..N_BITS {
        let bit = (msg_hash[p / 8] >> (p % 8)) & 1;
        if sha256(&sig[p]) != pk[p][bit as usize] {
            return false;
        }
    }
    true
}

// -- merkle tree --

pub struct MerkleTree {
    nodes: Vec<[u8; 32]>,
    height: usize,
}

impl MerkleTree {
    pub fn new(leaves: &[[u8; 32]]) -> Self {
        let height = if leaves.len() <= 1 {
            1
        } else {
            (leaves.len() as f64).log2().ceil() as usize
        };
        let size = 1usize << height;
        let mut nodes = vec![[0u8; 32]; 2 * size];

        for (i, leaf) in leaves.iter().enumerate() {
            nodes[size + i] = *leaf;
        }
        for i in (1..size).rev() {
            let mut buf = [0u8; 64];
            buf[..32].copy_from_slice(&nodes[2 * i]);
            buf[32..].copy_from_slice(&nodes[2 * i + 1]);
            nodes[i] = sha256(&buf);
        }

        Self { nodes, height }
    }

    pub fn root(&self) -> [u8; 32] {
        self.nodes[1]
    }

    pub fn auth_path(&self, idx: usize) -> Vec<[u8; 32]> {
        let mut path = Vec::with_capacity(self.height);
        let mut pos = (1 << self.height) + idx;
        for _ in 0..self.height {
            path.push(self.nodes[pos ^ 1]);
            pos >>= 1;
        }
        path
    }

    pub fn verify_path(
        leaf: &[u8; 32],
        idx: usize,
        path: &[[u8; 32]],
        root: &[u8; 32],
    ) -> bool {
        let mut current = *leaf;
        let mut pos = idx;
        for sibling in path {
            let mut buf = [0u8; 64];
            if pos & 1 == 0 {
                buf[..32].copy_from_slice(&current);
                buf[32..].copy_from_slice(sibling);
            } else {
                buf[..32].copy_from_slice(sibling);
                buf[32..].copy_from_slice(&current);
            }
            current = sha256(&buf);
            pos >>= 1;
        }
        current == *root
    }
}

// -- merkle signature scheme --

#[derive(Clone, Serialize, Deserialize)]
pub struct MssSignature {
    pub leaf: u32,
    pub pk: Vec<[String; 2]>,
    pub sig: Vec<String>,
    pub path: Vec<String>,
}

/// Merkle Signature Scheme key pair.
/// Private key = arbitrary-length text passphrase (or random hex).
/// Public key = hex-encoded Merkle root (text string).
pub struct KeyPair {
    seed: [u8; 32],
    passphrase: Option<String>,
    tree: MerkleTree,
    pub used: u32,
    pub capacity: u32,
}

impl KeyPair {
    /// Generate a random keypair.
    pub fn new(height: usize) -> Self {
        let mut seed = [0u8; 32];
        rand::thread_rng().fill_bytes(&mut seed);
        Self::build(seed, None, height)
    }

    /// Create a keypair from any arbitrary-length text passphrase.
    pub fn from_passphrase(passphrase: &str, height: usize) -> Self {
        let seed = derive_seed(passphrase);
        Self::build(seed, Some(passphrase.to_string()), height)
    }

    fn build(seed: [u8; 32], passphrase: Option<String>, height: usize) -> Self {
        let capacity = 1u32 << height;
        let leaves: Vec<[u8; 32]> = (0..capacity)
            .map(|i| lamport_pk_hash(&lamport_pk(&seed, i)))
            .collect();
        let tree = MerkleTree::new(&leaves);
        Self {
            seed,
            passphrase,
            tree,
            used: 0,
            capacity,
        }
    }

    /// Public key as text (hex-encoded Merkle root).
    pub fn pub_key(&self) -> String {
        hex::encode(self.tree.root())
    }

    /// Private key as text — the original passphrase, or hex of the random seed.
    pub fn priv_key(&self) -> String {
        match &self.passphrase {
            Some(p) => p.clone(),
            None => hex::encode(self.seed),
        }
    }

    pub fn sign(&mut self, msg: &[u8]) -> Result<MssSignature, &'static str> {
        if self.used >= self.capacity {
            return Err("key exhausted");
        }
        let idx = self.used;
        self.used += 1;

        let msg_hash = sha256(msg);
        let pk = lamport_pk(&self.seed, idx);
        let sig = lamport_sign(&self.seed, idx, &msg_hash);
        let path = self.tree.auth_path(idx as usize);

        Ok(MssSignature {
            leaf: idx,
            pk: pk
                .iter()
                .map(|pair| [hex::encode(pair[0]), hex::encode(pair[1])])
                .collect(),
            sig: sig.iter().map(|s| hex::encode(s)).collect(),
            path: path.iter().map(|p| hex::encode(p)).collect(),
        })
    }

    /// Verify a signature against a text public key (hex-encoded root).
    pub fn verify(pub_key: &str, msg: &[u8], sig: &MssSignature) -> bool {
        let Ok(root_vec) = hex::decode(pub_key) else {
            return false;
        };
        let Ok(root) = <[u8; 32]>::try_from(root_vec.as_slice()) else {
            return false;
        };

        let msg_hash = sha256(msg);

        let pk: Vec<[[u8; 32]; 2]> = match sig
            .pk
            .iter()
            .map(|pair| {
                let mut a = [0u8; 32];
                let mut b = [0u8; 32];
                hex::decode_to_slice(&pair[0], &mut a).ok()?;
                hex::decode_to_slice(&pair[1], &mut b).ok()?;
                Some([a, b])
            })
            .collect()
        {
            Some(v) => v,
            None => return false,
        };

        let lam_sig: Vec<[u8; 32]> = match sig
            .sig
            .iter()
            .map(|s| {
                let mut arr = [0u8; 32];
                hex::decode_to_slice(s, &mut arr).ok()?;
                Some(arr)
            })
            .collect()
        {
            Some(v) => v,
            None => return false,
        };

        let path: Vec<[u8; 32]> = match sig
            .path
            .iter()
            .map(|p| {
                let mut arr = [0u8; 32];
                hex::decode_to_slice(p, &mut arr).ok()?;
                Some(arr)
            })
            .collect()
        {
            Some(v) => v,
            None => return false,
        };

        if !lamport_verify(&pk, &msg_hash, &lam_sig) {
            return false;
        }

        let pk_hash = lamport_pk_hash(&pk);
        MerkleTree::verify_path(&pk_hash, sig.leaf as usize, &path, &root)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lamport_sign_verify() {
        let seed = [42u8; 32];
        let pk = lamport_pk(&seed, 0);
        let msg = sha256(b"hello qbit");
        let sig = lamport_sign(&seed, 0, &msg);
        assert!(lamport_verify(&pk, &msg, &sig));
        assert!(!lamport_verify(&pk, &sha256(b"tampered"), &sig));
    }

    #[test]
    fn mss_roundtrip() {
        let mut kp = KeyPair::new(4);
        let root = kp.pub_key();
        let sig = kp.sign(b"test message").unwrap();
        assert!(KeyPair::verify(&root, b"test message", &sig));
        assert!(!KeyPair::verify(&root, b"wrong message", &sig));
    }

    #[test]
    fn passphrase_deterministic() {
        let kp1 = KeyPair::from_passphrase("my secret passphrase", 4);
        let kp2 = KeyPair::from_passphrase("my secret passphrase", 4);
        assert_eq!(kp1.pub_key(), kp2.pub_key());
        assert_eq!(kp1.priv_key(), "my secret passphrase");
    }

    #[test]
    fn passphrase_sign_verify() {
        let mut kp = KeyPair::from_passphrase("arbitrary length text key!!!", 4);
        let pub_key = kp.pub_key();
        let sig = kp.sign(b"hello").unwrap();
        assert!(KeyPair::verify(&pub_key, b"hello", &sig));
    }

    #[test]
    fn merkle_path_verification() {
        let leaves: Vec<[u8; 32]> = (0u8..8).map(|i| sha256(&[i])).collect();
        let tree = MerkleTree::new(&leaves);
        for i in 0..8 {
            let path = tree.auth_path(i);
            assert!(MerkleTree::verify_path(
                &leaves[i],
                i,
                &path,
                &tree.root()
            ));
        }
    }
}
