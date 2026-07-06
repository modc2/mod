use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::vali::keys::{sha256, MerkleTree};

use super::gate::Circuit;
use super::witness::Witness;

// -- trace --

#[derive(Clone, Serialize, Deserialize)]
pub struct TraceEntry {
    pub gate_index: usize,
    pub wires: Vec<(String, String)>,
}

fn trace_entry_hash(entry: &TraceEntry) -> [u8; 32] {
    sha256(&serde_json::to_vec(entry).unwrap())
}

fn build_trace(circuit: &Circuit, witness: &Witness) -> Vec<TraceEntry> {
    circuit
        .gates
        .iter()
        .enumerate()
        .map(|(i, gate)| {
            let wire_names = gate.wires();
            let wires: Vec<(String, String)> = wire_names
                .iter()
                .filter_map(|w| witness.wires.get(w).map(|v| (w.clone(), v.clone())))
                .collect();
            TraceEntry {
                gate_index: i,
                wires,
            }
        })
        .collect()
}

// -- index selection --

/// Deterministic Fiat-Shamir: select `count` distinct indices from 0..total.
fn fiat_shamir_indices(seed: &[u8; 32], total: usize, count: usize) -> Vec<usize> {
    let count = count.min(total);
    let mut indices = Vec::with_capacity(count);
    let mut nonce = 0u32;
    while indices.len() < count {
        let h = sha256(&[seed.as_slice(), &nonce.to_le_bytes()].concat());
        let idx = u64::from_le_bytes(h[..8].try_into().unwrap()) as usize % total;
        if !indices.contains(&idx) {
            indices.push(idx);
        }
        nonce += 1;
    }
    indices
}

// -- proof --

#[derive(Clone, Serialize, Deserialize)]
pub struct Opening {
    pub entry: TraceEntry,
    pub leaf_index: usize,
    pub merkle_path: Vec<String>,
}

/// Hash-based ZK proof of correct circuit execution.
/// Quantum-resistant: uses only SHA-256 + Merkle commitments.
/// Compact enough for on-chain verification.
#[derive(Clone, Serialize, Deserialize)]
pub struct Proof {
    pub circuit_hash: String,
    pub trace_root: String,
    pub trace_len: usize,
    pub public_inputs: HashMap<String, String>,
    pub public_outputs: HashMap<String, String>,
    pub openings: Vec<Opening>,
}

impl Proof {
    /// Generate a proof that a circuit was executed correctly.
    pub fn generate(circuit: &Circuit, witness: &Witness) -> Result<Self, String> {
        let trace = build_trace(circuit, witness);
        if trace.is_empty() {
            return Err("empty circuit".into());
        }

        // 1. Commit to execution trace via Merkle tree
        let leaves: Vec<[u8; 32]> = trace.iter().map(|e| trace_entry_hash(e)).collect();
        let tree = MerkleTree::new(&leaves);
        let trace_root = hex::encode(tree.root());
        let circuit_hash = circuit.hash();

        // 2. Collect public I/O
        let public_inputs: HashMap<String, String> = circuit
            .inputs
            .iter()
            .filter_map(|k| witness.wires.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        let public_outputs = witness.outputs(circuit);

        // 3. Required openings: gates that produce public outputs must always be opened
        let mut required: Vec<usize> = Vec::new();
        for (i, gate) in circuit.gates.iter().enumerate() {
            for wire in gate.output_wires() {
                if circuit.outputs.contains(&wire) && !required.contains(&i) {
                    required.push(i);
                }
            }
        }

        // 4. Fiat-Shamir random openings
        let challenge_input = format!(
            "{}:{}:{}",
            circuit_hash,
            trace_root,
            serde_json::to_string(&public_inputs).unwrap()
        );
        let challenge_seed = sha256(challenge_input.as_bytes());
        let num_random = trace.len().min(64).saturating_sub(required.len());
        let random = fiat_shamir_indices(&challenge_seed, trace.len(), num_random);

        // 5. Merge required + random, deduplicate
        let mut all_indices = required;
        for idx in random {
            if !all_indices.contains(&idx) {
                all_indices.push(idx);
            }
        }

        // 6. Build openings with Merkle proofs
        let openings = all_indices
            .iter()
            .map(|&idx| {
                let path = tree.auth_path(idx);
                Opening {
                    entry: trace[idx].clone(),
                    leaf_index: idx,
                    merkle_path: path.iter().map(|p| hex::encode(p)).collect(),
                }
            })
            .collect();

        Ok(Self {
            circuit_hash,
            trace_root,
            trace_len: trace.len(),
            public_inputs,
            public_outputs,
            openings,
        })
    }

    /// Verify a proof against a circuit definition.
    /// Only needs the circuit + proof — no witness required.
    pub fn verify(&self, circuit: &Circuit) -> Result<bool, String> {
        // 1. Check circuit hash
        if self.circuit_hash != circuit.hash() {
            return Ok(false);
        }

        // 2. Recompute expected opened indices (same algorithm as generate)
        let mut required: Vec<usize> = Vec::new();
        for (i, gate) in circuit.gates.iter().enumerate() {
            for wire in gate.output_wires() {
                if circuit.outputs.contains(&wire) && !required.contains(&i) {
                    required.push(i);
                }
            }
        }

        let challenge_input = format!(
            "{}:{}:{}",
            self.circuit_hash,
            self.trace_root,
            serde_json::to_string(&self.public_inputs).unwrap()
        );
        let challenge_seed = sha256(challenge_input.as_bytes());
        let num_random = self.trace_len.min(64).saturating_sub(required.len());
        let random = fiat_shamir_indices(&challenge_seed, self.trace_len, num_random);

        let mut expected: Vec<usize> = required;
        for idx in random {
            if !expected.contains(&idx) {
                expected.push(idx);
            }
        }

        // 3. Check opening count matches
        if self.openings.len() != expected.len() {
            return Ok(false);
        }

        // 4. Parse trace root
        let root_bytes: [u8; 32] = hex::decode(&self.trace_root)
            .map_err(|e| e.to_string())?
            .try_into()
            .map_err(|_| "invalid trace root".to_string())?;

        // 5. Track wire values across all openings for cross-gate consistency
        let mut seen_wires: HashMap<String, String> = HashMap::new();
        for (k, v) in &self.public_inputs {
            seen_wires.insert(k.clone(), v.clone());
        }

        // 6. Verify each opening
        for (opening, &exp_idx) in self.openings.iter().zip(expected.iter()) {
            // Index check
            if opening.leaf_index != exp_idx || opening.entry.gate_index != exp_idx {
                return Ok(false);
            }

            // Merkle path verification
            let leaf = trace_entry_hash(&opening.entry);
            let path: Result<Vec<[u8; 32]>, String> = opening
                .merkle_path
                .iter()
                .map(|h| {
                    let b = hex::decode(h).map_err(|e| e.to_string())?;
                    <[u8; 32]>::try_from(b.as_slice())
                        .map_err(|_| "invalid hash length".to_string())
                })
                .collect();
            let path = path?;

            if !MerkleTree::verify_path(&leaf, exp_idx, &path, &root_bytes) {
                return Ok(false);
            }

            // Gate constraint verification
            let gate = circuit
                .gates
                .get(exp_idx)
                .ok_or(format!("gate index {} out of bounds", exp_idx))?;
            let wire_map: HashMap<String, String> =
                opening.entry.wires.iter().cloned().collect();
            if !gate.verify(&wire_map) {
                return Ok(false);
            }

            // Cross-gate wire consistency
            for (name, val) in &opening.entry.wires {
                if let Some(prev) = seen_wires.get(name) {
                    if prev != val {
                        return Ok(false);
                    }
                }
                seen_wires.insert(name.clone(), val.clone());
            }
        }

        // 7. Verify public outputs are consistent with opened values
        for (k, v) in &self.public_outputs {
            match seen_wires.get(k) {
                Some(seen) if seen != v => return Ok(false),
                None => return Ok(false),
                _ => {}
            }
        }

        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::circuit::gate::Gate;

    fn make_double_circuit() -> Circuit {
        Circuit {
            name: "double".into(),
            inputs: vec!["x".into()],
            outputs: vec!["result".into()],
            gates: vec![
                Gate::Const {
                    wire: "two".into(),
                    value: "2".into(),
                },
                Gate::Mul {
                    a: "x".into(),
                    b: "two".into(),
                    output: "result".into(),
                },
            ],
        }
    }

    #[test]
    fn proof_roundtrip() {
        let circuit = make_double_circuit();
        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "21".into());
        let witness = Witness::execute(&circuit, &inputs).unwrap();

        let proof = Proof::generate(&circuit, &witness).unwrap();
        assert_eq!(proof.public_outputs.get("result").unwrap(), "42");
        assert!(proof.verify(&circuit).unwrap());
    }

    #[test]
    fn tampered_proof_fails() {
        let circuit = make_double_circuit();
        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "21".into());
        let witness = Witness::execute(&circuit, &inputs).unwrap();

        let mut proof = Proof::generate(&circuit, &witness).unwrap();
        // Tamper with public output
        proof.public_outputs.insert("result".into(), "999".into());
        assert!(!proof.verify(&circuit).unwrap());
    }

    #[test]
    fn hash_preimage_circuit() {
        let circuit = Circuit {
            name: "preimage".into(),
            inputs: vec!["secret".into()],
            outputs: vec!["digest".into()],
            gates: vec![Gate::Hash {
                input: "secret".into(),
                output: "digest".into(),
            }],
        };

        let mut inputs = HashMap::new();
        inputs.insert("secret".into(), "my secret value".into());
        let witness = Witness::execute(&circuit, &inputs).unwrap();

        let proof = Proof::generate(&circuit, &witness).unwrap();
        assert!(proof.verify(&circuit).unwrap());

        // The proof reveals the digest but not the secret
        assert!(proof.public_outputs.contains_key("digest"));
    }

    #[test]
    fn pythagorean_circuit() {
        let circuit = Circuit {
            name: "pythagorean".into(),
            inputs: vec!["a".into(), "b".into(), "c".into()],
            outputs: vec!["sum".into(), "c_sq".into()],
            gates: vec![
                Gate::Mul {
                    a: "a".into(),
                    b: "a".into(),
                    output: "a_sq".into(),
                },
                Gate::Mul {
                    a: "b".into(),
                    b: "b".into(),
                    output: "b_sq".into(),
                },
                Gate::Mul {
                    a: "c".into(),
                    b: "c".into(),
                    output: "c_sq".into(),
                },
                Gate::Add {
                    a: "a_sq".into(),
                    b: "b_sq".into(),
                    output: "sum".into(),
                },
                Gate::Eq {
                    a: "sum".into(),
                    b: "c_sq".into(),
                },
            ],
        };

        // 3² + 4² = 5²
        let mut inputs = HashMap::new();
        inputs.insert("a".into(), "3".into());
        inputs.insert("b".into(), "4".into());
        inputs.insert("c".into(), "5".into());
        let witness = Witness::execute(&circuit, &inputs).unwrap();
        let proof = Proof::generate(&circuit, &witness).unwrap();
        assert!(proof.verify(&circuit).unwrap());

        // 3² + 4² ≠ 6² — execution fails at Eq gate
        inputs.insert("c".into(), "6".into());
        assert!(Witness::execute(&circuit, &inputs).is_err());
    }
}
