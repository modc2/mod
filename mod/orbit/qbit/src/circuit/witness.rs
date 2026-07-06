use std::collections::HashMap;

use super::gate::Circuit;

/// A witness: all wire values produced by executing a circuit.
pub struct Witness {
    pub wires: HashMap<String, String>,
}

impl Witness {
    /// Execute a circuit with the given public inputs and produce a full witness.
    pub fn execute(
        circuit: &Circuit,
        inputs: &HashMap<String, String>,
    ) -> Result<Self, String> {
        // Validate all declared inputs are provided
        for name in &circuit.inputs {
            if !inputs.contains_key(name) {
                return Err(format!("missing input: {}", name));
            }
        }

        let mut wires = inputs.clone();

        for (i, gate) in circuit.gates.iter().enumerate() {
            let outputs = gate
                .evaluate(&wires)
                .map_err(|e| format!("gate {}: {}", i, e))?;
            wires.extend(outputs);
        }

        // Verify all declared outputs are set
        for name in &circuit.outputs {
            if !wires.contains_key(name) {
                return Err(format!("output wire {} not set after execution", name));
            }
        }

        Ok(Self { wires })
    }

    /// Extract only the declared output values.
    pub fn outputs(&self, circuit: &Circuit) -> HashMap<String, String> {
        circuit
            .outputs
            .iter()
            .filter_map(|k| self.wires.get(k).map(|v| (k.clone(), v.clone())))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::circuit::gate::Gate;

    #[test]
    fn execute_double_circuit() {
        let circuit = Circuit {
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
        };

        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "21".into());

        let w = Witness::execute(&circuit, &inputs).unwrap();
        assert_eq!(w.wires.get("result").unwrap(), "42");
    }
}
