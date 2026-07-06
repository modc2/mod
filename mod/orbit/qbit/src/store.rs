use std::collections::HashMap;

use crate::circuit::gate::Circuit;
use crate::circuit::proof::Proof;
use crate::circuit::witness::Witness;
use crate::consensus::block::{Block, Op};
use crate::consensus::round::Round;
use crate::consensus::state::State;
use crate::vali::set::ValidatorSet;
use crate::vali::validator::Validator;

pub struct ValidatorKeys {
    pub pub_key: String,
    pub priv_key: String,
}

/// Top-level put/get store backed by validator consensus + ZK circuits.
pub struct Store {
    pub state: State,
    pub vset: ValidatorSet,
    pub validators: HashMap<String, Validator>,
    pub circuits: HashMap<String, Circuit>,
    pending: Vec<Op>,
}

impl Store {
    pub fn new() -> Self {
        Self {
            state: State::new(),
            vset: ValidatorSet::new(),
            validators: HashMap::new(),
            circuits: HashMap::new(),
            pending: Vec::new(),
        }
    }

    // -- validators --

    pub fn add_validator(
        &mut self,
        name: &str,
        passphrase: Option<&str>,
        height: usize,
    ) -> ValidatorKeys {
        let v = match passphrase {
            Some(p) => Validator::from_passphrase(name.to_string(), p, height),
            None => Validator::new(name.to_string(), height),
        };
        let keys = ValidatorKeys {
            pub_key: v.pub_key(),
            priv_key: v.priv_key(),
        };
        self.vset.add(name.to_string(), keys.pub_key.clone());
        self.validators.insert(name.to_string(), v);
        keys
    }

    // -- put / get --

    pub fn put(&mut self, key: String, value: String) {
        self.pending.push(Op { key, value });
    }

    pub fn get(&self, key: &str) -> Option<String> {
        self.state.get(key).cloned()
    }

    /// Propose a block from pending ops, collect votes, commit if quorum.
    pub fn commit(&mut self) -> Result<Option<Block>, String> {
        if self.pending.is_empty() {
            return Ok(None);
        }
        if self.vset.size() == 0 {
            return Err("no validators".into());
        }

        let proposer = self.validators.keys().next().unwrap().clone();
        let block = Block::new(
            self.state.height() + 1,
            self.state.head().hash(),
            self.pending.clone(),
            proposer,
        );

        let mut round = Round::new(block.clone());
        let msg = block.hash();

        let names: Vec<String> = self.validators.keys().cloned().collect();
        for name in &names {
            let v = self.validators.get_mut(name).unwrap();
            let sig = v.sign(&msg).map_err(|e| e.to_string())?;
            let pub_key = v.pub_key();
            round.vote(&pub_key, sig, &self.vset);
        }

        if round.try_commit(&self.vset) {
            self.state.apply(block.clone())?;
            self.pending.clear();
            Ok(Some(block))
        } else {
            Err(format!(
                "quorum not reached: {}/{}",
                round.vote_count(),
                self.vset.quorum()
            ))
        }
    }

    // -- circuits --

    pub fn register_circuit(&mut self, circuit: Circuit) {
        self.circuits.insert(circuit.name.clone(), circuit);
    }

    pub fn get_circuit(&self, name: &str) -> Option<&Circuit> {
        self.circuits.get(name)
    }

    pub fn list_circuits(&self) -> Vec<String> {
        self.circuits.keys().cloned().collect()
    }

    /// Execute a circuit offchain and generate a ZK proof.
    pub fn prove(
        &self,
        name: &str,
        inputs: &HashMap<String, String>,
    ) -> Result<(HashMap<String, String>, Proof), String> {
        let circuit = self.circuits.get(name).ok_or("circuit not found")?;
        let witness = Witness::execute(circuit, inputs)?;
        let outputs = witness.outputs(circuit);
        let proof = Proof::generate(circuit, &witness)?;
        Ok((outputs, proof))
    }

    /// Verify a ZK proof against a registered or provided circuit.
    pub fn verify_proof(
        &self,
        name: Option<&str>,
        circuit: Option<&Circuit>,
        proof: &Proof,
    ) -> Result<bool, String> {
        let c = match circuit {
            Some(c) => c,
            None => {
                let n = name.ok_or("need circuit name or definition")?;
                self.circuits.get(n).ok_or("circuit not found")?
            }
        };
        proof.verify(c)
    }

    // -- info --

    pub fn height(&self) -> u64 {
        self.state.height()
    }

    pub fn snapshot(&self) -> HashMap<String, String> {
        self.state.snapshot()
    }

    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }
}
