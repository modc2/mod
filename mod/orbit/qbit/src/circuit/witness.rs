use std::collections::HashMap;

use super::gate::Circuit;

/// A witness: all wire values produced by executing a circuit.
#[derive(Debug)]
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

        // Reject any supplied key not listed as a declared input
        for name in inputs.keys() {
            if !circuit.inputs.contains(name) {
                return Err(format!(
                    "undeclared input wire '{}': not listed in circuit.inputs",
                    name
                ));
            }
        }

        let mut wires = inputs.clone();

        for (i, gate) in circuit.gates.iter().enumerate() {
            let outputs = gate
                .evaluate(&wires)
                .map_err(|e| format!("gate {}: {}", i, e))?;
            for name in outputs.keys() {
                if wires.contains_key(name) {
                    return Err(format!(
                        "gate {}: output wire '{}' collides with existing wire",
                        i, name
                    ));
                }
            }
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
            private_inputs: vec![],
        };

        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "21".into());

        let w = Witness::execute(&circuit, &inputs).unwrap();
        assert_eq!(w.wires.get("result").unwrap(), "42");
    }

    #[test]
    fn execute_rejects_output_wire_collision() {
        // Gate 0 writes "x_copy"; Gate 1 also writes "x_copy" — should error.
        let circuit = Circuit {
            name: "collision".into(),
            inputs: vec!["x".into()],
            outputs: vec!["x_copy".into()],
            gates: vec![
                Gate::Const {
                    wire: "x_copy".into(),
                    value: "1".into(),
                },
                Gate::Const {
                    wire: "x_copy".into(),
                    value: "2".into(),
                },
            ],
            private_inputs: vec![],
        };

        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "5".into());

        let err = Witness::execute(&circuit, &inputs).unwrap_err();
        assert!(
            err.contains("collides with existing wire"),
            "expected collision error, got: {}",
            err
        );
    }

    #[test]
    fn execute_rejects_gate_overwriting_input_wire() {
        // A gate that outputs the same wire name as a declared input is a collision.
        let circuit = Circuit {
            name: "input_overwrite".into(),
            inputs: vec!["x".into()],
            outputs: vec!["x".into()],
            gates: vec![Gate::Const {
                wire: "x".into(),
                value: "99".into(),
            }],
            private_inputs: vec![],
        };

        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "5".into());

        let err = Witness::execute(&circuit, &inputs).unwrap_err();
        assert!(
            err.contains("collides with existing wire"),
            "expected collision error, got: {}",
            err
        );
    }

    #[test]
    fn execute_rejects_undeclared_input_wire() {
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
            private_inputs: vec![],
        };

        let mut inputs = HashMap::new();
        inputs.insert("x".into(), "21".into());
        // "two" is an intermediate wire, not a declared input
        inputs.insert("two".into(), "99".into());

        let err = Witness::execute(&circuit, &inputs).unwrap_err();
        assert!(
            err.contains("undeclared input wire"),
            "expected undeclared input error, got: {}",
            err
        );
    }
}
