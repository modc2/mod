use crate::vali::keys::sha256;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Circuit gate — all wires and values are text strings.
#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "op")]
pub enum Gate {
    /// Set a wire to a constant text value.
    Const { wire: String, value: String },
    /// output = sha256(input) as hex.
    Hash { input: String, output: String },
    /// output = a + b (numeric text strings).
    Add { a: String, b: String, output: String },
    /// output = a * b (numeric text strings).
    Mul { a: String, b: String, output: String },
    /// Assert two wires hold the same value.
    Eq { a: String, b: String },
    /// output = concat(a, b).
    Concat { a: String, b: String, output: String },
}

impl Gate {
    /// All wires touched by this gate (inputs + outputs).
    pub fn wires(&self) -> Vec<String> {
        match self {
            Gate::Const { wire, .. } => vec![wire.clone()],
            Gate::Hash { input, output } => vec![input.clone(), output.clone()],
            Gate::Add { a, b, output }
            | Gate::Mul { a, b, output }
            | Gate::Concat { a, b, output } => vec![a.clone(), b.clone(), output.clone()],
            Gate::Eq { a, b } => vec![a.clone(), b.clone()],
        }
    }

    /// Wires that this gate writes to.
    pub fn output_wires(&self) -> Vec<String> {
        match self {
            Gate::Const { wire, .. } => vec![wire.clone()],
            Gate::Hash { output, .. }
            | Gate::Add { output, .. }
            | Gate::Mul { output, .. }
            | Gate::Concat { output, .. } => vec![output.clone()],
            Gate::Eq { .. } => vec![],
        }
    }

    /// Execute this gate: read inputs from `wires`, return new output values.
    pub fn evaluate(&self, wires: &HashMap<String, String>) -> Result<HashMap<String, String>, String> {
        let mut out = HashMap::new();
        match self {
            Gate::Const { wire, value } => {
                out.insert(wire.clone(), value.clone());
            }
            Gate::Hash { input, output } => {
                let v = wires.get(input).ok_or(format!("{} not set", input))?;
                out.insert(output.clone(), hex::encode(sha256(v.as_bytes())));
            }
            Gate::Add { a, b, output } => {
                let va: i128 = wires
                    .get(a)
                    .ok_or(format!("{} not set", a))?
                    .parse()
                    .map_err(|_| format!("{} not numeric", a))?;
                let vb: i128 = wires
                    .get(b)
                    .ok_or(format!("{} not set", b))?
                    .parse()
                    .map_err(|_| format!("{} not numeric", b))?;
                out.insert(output.clone(), (va + vb).to_string());
            }
            Gate::Mul { a, b, output } => {
                let va: i128 = wires
                    .get(a)
                    .ok_or(format!("{} not set", a))?
                    .parse()
                    .map_err(|_| format!("{} not numeric", a))?;
                let vb: i128 = wires
                    .get(b)
                    .ok_or(format!("{} not set", b))?
                    .parse()
                    .map_err(|_| format!("{} not numeric", b))?;
                out.insert(output.clone(), (va * vb).to_string());
            }
            Gate::Eq { a, b } => {
                let va = wires.get(a).ok_or(format!("{} not set", a))?;
                let vb = wires.get(b).ok_or(format!("{} not set", b))?;
                if va != vb {
                    return Err(format!("eq failed: {}={} != {}={}", a, va, b, vb));
                }
            }
            Gate::Concat { a, b, output } => {
                let va = wires.get(a).ok_or(format!("{} not set", a))?;
                let vb = wires.get(b).ok_or(format!("{} not set", b))?;
                out.insert(output.clone(), format!("{}{}", va, vb));
            }
        }
        Ok(out)
    }

    /// Verify that a gate's constraint holds given the wire values.
    pub fn verify(&self, wires: &HashMap<String, String>) -> bool {
        match self {
            Gate::Const { wire, value } => wires.get(wire).map_or(false, |v| v == value),
            Gate::Hash { input, output } => {
                let Some(inp) = wires.get(input) else {
                    return false;
                };
                let expected = hex::encode(sha256(inp.as_bytes()));
                wires.get(output).map_or(false, |v| *v == expected)
            }
            Gate::Add { a, b, output } => {
                let va = wires.get(a).and_then(|v| v.parse::<i128>().ok());
                let vb = wires.get(b).and_then(|v| v.parse::<i128>().ok());
                match (va, vb) {
                    (Some(x), Some(y)) => {
                        wires.get(output).map_or(false, |v| *v == (x + y).to_string())
                    }
                    _ => false,
                }
            }
            Gate::Mul { a, b, output } => {
                let va = wires.get(a).and_then(|v| v.parse::<i128>().ok());
                let vb = wires.get(b).and_then(|v| v.parse::<i128>().ok());
                match (va, vb) {
                    (Some(x), Some(y)) => {
                        wires.get(output).map_or(false, |v| *v == (x * y).to_string())
                    }
                    _ => false,
                }
            }
            Gate::Eq { a, b } => match (wires.get(a), wires.get(b)) {
                (Some(va), Some(vb)) => va == vb,
                _ => false,
            },
            Gate::Concat { a, b, output } => {
                let Some(va) = wires.get(a) else {
                    return false;
                };
                let Some(vb) = wires.get(b) else {
                    return false;
                };
                wires
                    .get(output)
                    .map_or(false, |v| *v == format!("{}{}", va, vb))
            }
        }
    }
}

/// A circuit definition: named collection of gates with declared I/O wires.
#[derive(Clone, Serialize, Deserialize)]
pub struct Circuit {
    pub name: String,
    pub inputs: Vec<String>,
    pub outputs: Vec<String>,
    pub gates: Vec<Gate>,
}

impl Circuit {
    pub fn hash(&self) -> String {
        hex::encode(sha256(&serde_json::to_vec(self).unwrap()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn const_gate() {
        let g = Gate::Const {
            wire: "x".into(),
            value: "42".into(),
        };
        let out = g.evaluate(&HashMap::new()).unwrap();
        assert_eq!(out.get("x").unwrap(), "42");
    }

    #[test]
    fn add_gate() {
        let mut w = HashMap::new();
        w.insert("a".into(), "3".into());
        w.insert("b".into(), "4".into());
        let g = Gate::Add {
            a: "a".into(),
            b: "b".into(),
            output: "c".into(),
        };
        let out = g.evaluate(&w).unwrap();
        assert_eq!(out.get("c").unwrap(), "7");
    }

    #[test]
    fn hash_gate() {
        let mut w = HashMap::new();
        w.insert("in".into(), "hello".into());
        let g = Gate::Hash {
            input: "in".into(),
            output: "out".into(),
        };
        let out = g.evaluate(&w).unwrap();
        let expected = hex::encode(sha256(b"hello"));
        assert_eq!(out.get("out").unwrap(), &expected);
    }

    #[test]
    fn verify_gate() {
        let mut w = HashMap::new();
        w.insert("a".into(), "5".into());
        w.insert("b".into(), "3".into());
        w.insert("c".into(), "15".into());
        let g = Gate::Mul {
            a: "a".into(),
            b: "b".into(),
            output: "c".into(),
        };
        assert!(g.verify(&w));
        w.insert("c".into(), "16".into());
        assert!(!g.verify(&w));
    }
}
