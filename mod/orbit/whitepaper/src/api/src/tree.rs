// Off-chain Merkle tree over canonical-JSON-encoded module records.
// Matches OpenZeppelin's sorted-pair construction so on-chain MerkleProof.verify
// against the same root succeeds without any conversion.

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};
use tiny_keccak::{Hasher, Keccak};

#[derive(Serialize, Deserialize, Clone)]
pub struct Manifest {
    pub epoch: u64,
    pub root: String,
    pub count: usize,
    pub records: Vec<Value>,
    /// Layers, leaves first. Each node is lowercase hex with no `0x` prefix.
    pub layers: Vec<Vec<String>>,
}

fn keccak(input: &[u8]) -> [u8; 32] {
    let mut hasher = Keccak::v256();
    hasher.update(input);
    let mut out = [0u8; 32];
    hasher.finalize(&mut out);
    out
}

/// Canonical-JSON encoder: sorted keys, no whitespace.
/// Matches Python's `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
fn canonical_json(v: &Value) -> String {
    match v {
        Value::Object(map) => {
            let mut entries: Vec<_> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            let parts: Vec<String> = entries
                .iter()
                .map(|(k, val)| format!("{}:{}", serde_json::to_string(k).unwrap(), canonical_json(val)))
                .collect();
            format!("{{{}}}", parts.join(","))
        }
        Value::Array(arr) => {
            let parts: Vec<String> = arr.iter().map(canonical_json).collect();
            format!("[{}]", parts.join(","))
        }
        _ => serde_json::to_string(v).unwrap(),
    }
}

pub fn leaf_of(record: &Value) -> [u8; 32] {
    keccak(canonical_json(record).as_bytes())
}

fn pair(a: &[u8; 32], b: &[u8; 32]) -> [u8; 32] {
    let mut buf = [0u8; 64];
    if a <= b {
        buf[..32].copy_from_slice(a);
        buf[32..].copy_from_slice(b);
    } else {
        buf[..32].copy_from_slice(b);
        buf[32..].copy_from_slice(a);
    }
    keccak(&buf)
}

pub fn build_tree(records: Vec<Value>) -> Result<Manifest> {
    if records.is_empty() {
        return Ok(Manifest {
            epoch: now_epoch(),
            root: "0x0000000000000000000000000000000000000000000000000000000000000000".into(),
            count: 0,
            records,
            layers: vec![vec![]],
        });
    }
    let mut leaves: Vec<[u8; 32]> = records.iter().map(leaf_of).collect();
    leaves.sort();

    let mut layers: Vec<Vec<[u8; 32]>> = vec![leaves];
    while layers.last().unwrap().len() > 1 {
        let prev = layers.last().unwrap();
        let mut next = Vec::with_capacity(prev.len() / 2 + 1);
        let mut i = 0;
        while i < prev.len() {
            let a = &prev[i];
            let b = if i + 1 < prev.len() { &prev[i + 1] } else { &prev[i] };
            next.push(pair(a, b));
            i += 2;
        }
        layers.push(next);
    }
    let root = layers.last().unwrap()[0];
    let hex_layers: Vec<Vec<String>> = layers
        .iter()
        .map(|l| l.iter().map(|n| hex::encode(n)).collect())
        .collect();
    Ok(Manifest {
        epoch: now_epoch(),
        root: format!("0x{}", hex::encode(root)),
        count: records.len(),
        records,
        layers: hex_layers,
    })
}

pub fn proof_for(manifest: &Manifest, name: &str) -> Result<serde_json::Value> {
    let target = manifest
        .records
        .iter()
        .find(|r| r.get("name").and_then(|n| n.as_str()) == Some(name))
        .ok_or_else(|| anyhow!("no record named {name}"))?;
    let target_leaf = leaf_of(target);
    let leaf_hex = hex::encode(target_leaf);

    let layers: Vec<Vec<[u8; 32]>> = manifest
        .layers
        .iter()
        .map(|l| {
            l.iter()
                .map(|h| {
                    let bytes = hex::decode(h).expect("hex");
                    let mut arr = [0u8; 32];
                    arr.copy_from_slice(&bytes);
                    arr
                })
                .collect()
        })
        .collect();

    let mut idx = layers[0]
        .iter()
        .position(|l| l == &target_leaf)
        .ok_or_else(|| anyhow!("leaf missing from tree"))?;
    let mut proof: Vec<String> = vec![];
    for layer in layers.iter().take(layers.len().saturating_sub(1)) {
        let sib = idx ^ 1;
        // For odd-sized layers the unpaired node is duplicated during build,
        // so the proof must include the node itself as its own sibling.
        let sib_node = if sib < layer.len() { layer[sib] } else { layer[idx] };
        proof.push(format!("0x{}", hex::encode(sib_node)));
        idx /= 2;
    }
    Ok(serde_json::json!({
        "record": target,
        "leaf": format!("0x{leaf_hex}"),
        "proof": proof,
        "root": manifest.root,
    }))
}

pub fn verify(leaf_hex: &str, proof_hex: &[String], root_hex: &str) -> Result<bool> {
    let leaf = decode32(leaf_hex)?;
    let root = decode32(root_hex)?;
    let mut cur = leaf;
    for p in proof_hex {
        let sib = decode32(p)?;
        cur = pair(&cur, &sib);
    }
    Ok(cur == root)
}

fn decode32(s: &str) -> Result<[u8; 32]> {
    let s = s.strip_prefix("0x").unwrap_or(s);
    let bytes = hex::decode(s)?;
    if bytes.len() != 32 {
        return Err(anyhow!("expected 32 bytes, got {}", bytes.len()));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Ok(out)
}

fn now_epoch() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}
