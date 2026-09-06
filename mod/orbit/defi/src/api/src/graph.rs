//! Protocol graphs: the thing the canvas edits and the thing everything else
//! consumes. A graph is nodes (block instances with params) plus edges (a wire
//! from one node's output into another node's typed input port).
//!
//! Two passes live here:
//!   validate() — is this composition legal? (types, required ports, cycles)
//!   plan()     — what transactions deploy it, in what order?
//!
//! The split matters: the canvas calls validate() on every edit for instant
//! feedback, and plan() only when you press Deploy.

use crate::catalog::{BlockSpec, Catalog};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    pub block: String,
    #[serde(default)]
    pub x: f64,
    #[serde(default)]
    pub y: f64,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub params: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Edge {
    pub from: String,
    pub to: String,
    /// Input port id on `to`.
    pub port: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Graph {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub nodes: Vec<Node>,
    #[serde(default)]
    pub edges: Vec<Edge>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Issue {
    pub level: String, // "error" | "warning"
    pub node: Option<String>,
    pub port: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub ok: bool,
    pub issues: Vec<Issue>,
    /// Constructor-dependency order. Empty when the graph does not resolve.
    pub order: Vec<String>,
    pub stats: serde_json::Value,
}

fn err(node: &str, port: Option<&str>, msg: impl Into<String>) -> Issue {
    Issue {
        level: "error".into(),
        node: Some(node.to_string()),
        port: port.map(|p| p.to_string()),
        message: msg.into(),
    }
}

fn warn(node: &str, msg: impl Into<String>) -> Issue {
    Issue {
        level: "warning".into(),
        node: Some(node.to_string()),
        port: None,
        message: msg.into(),
    }
}

/// Which node feeds which port of which node.
pub fn wiring<'a>(graph: &'a Graph) -> HashMap<(&'a str, &'a str), &'a str> {
    graph
        .edges
        .iter()
        .map(|e| ((e.to.as_str(), e.port.as_str()), e.from.as_str()))
        .collect()
}

pub fn validate(catalog: &Catalog, graph: &Graph) -> Report {
    let mut issues = Vec::new();
    let mut seen_ids: HashSet<&str> = HashSet::new();
    let mut specs: HashMap<&str, &BlockSpec> = HashMap::new();

    for node in &graph.nodes {
        if !seen_ids.insert(node.id.as_str()) {
            issues.push(err(&node.id, None, "duplicate node id"));
            continue;
        }
        match catalog.block(&node.block) {
            Some(spec) => {
                specs.insert(node.id.as_str(), spec);
            }
            None => issues.push(err(
                &node.id,
                None,
                format!("unknown block type '{}'", node.block),
            )),
        }
    }

    // Edges: endpoints exist, port exists, and the source actually provides the
    // port's type. This is the check that makes drag-and-drop safe — you cannot
    // drop a governor into an asset slot.
    let mut filled: HashSet<(&str, &str)> = HashSet::new();
    for edge in &graph.edges {
        let Some(dst) = specs.get(edge.to.as_str()) else {
            issues.push(Issue {
                level: "error".into(),
                node: Some(edge.to.clone()),
                port: Some(edge.port.clone()),
                message: format!("wire targets unknown node '{}'", edge.to),
            });
            continue;
        };
        let Some(src) = specs.get(edge.from.as_str()) else {
            issues.push(err(
                &edge.to,
                Some(&edge.port),
                format!("wire comes from unknown node '{}'", edge.from),
            ));
            continue;
        };
        let Some(port) = dst.inputs.iter().find(|p| p.id == edge.port) else {
            issues.push(err(
                &edge.to,
                Some(&edge.port),
                format!("{} has no input port '{}'", dst.name, edge.port),
            ));
            continue;
        };
        if !src.provides.contains(&port.port_type) {
            let provided = if src.provides.is_empty() {
                "nothing".to_string()
            } else {
                src.provides.join(", ")
            };
            issues.push(err(
                &edge.to,
                Some(&edge.port),
                format!(
                    "{} provides {provided} — port '{}' needs a {}",
                    src.name, port.label, port.port_type
                ),
            ));
            continue;
        }
        if edge.from == edge.to {
            issues.push(err(&edge.to, Some(&edge.port), "a block cannot wire to itself"));
            continue;
        }
        if !filled.insert((edge.to.as_str(), edge.port.as_str())) {
            issues.push(err(
                &edge.to,
                Some(&edge.port),
                format!("port '{}' is already wired", port.label),
            ));
        }
    }

    // Required ports.
    for node in &graph.nodes {
        let Some(spec) = specs.get(node.id.as_str()) else { continue };
        for port in &spec.inputs {
            if port.required && !filled.contains(&(node.id.as_str(), port.id.as_str())) {
                issues.push(err(
                    &node.id,
                    Some(&port.id),
                    format!("{} needs a {} on '{}'", spec.name, port.port_type, port.label),
                ));
            }
        }
        for (key, value) in &node.params {
            if !spec.params.iter().any(|p| &p.name == key) {
                issues.push(warn(
                    &node.id,
                    format!("param '{key}' is not declared by {} — ignored", spec.name),
                ));
                let _ = value;
            }
        }
    }

    // Constructor dependencies form the deployment DAG. Wired-after-deploy
    // ports (the `wires` list) are deliberately excluded, which is exactly what
    // lets a vault and its strategy reference each other.
    let wires = wiring(graph);
    let mut deps: HashMap<&str, Vec<&str>> = HashMap::new();
    for node in &graph.nodes {
        let Some(spec) = specs.get(node.id.as_str()) else { continue };
        let mut list = Vec::new();
        for port in &spec.inputs {
            if spec.wires.iter().any(|w| w.when == port.id) {
                continue; // resolved post-deploy
            }
            if !spec.ctor.iter().any(|t| ctor_mentions_port(t, &port.id)) {
                continue; // not a constructor arg
            }
            if let Some(src) = wires.get(&(node.id.as_str(), port.id.as_str())) {
                list.push(*src);
            }
        }
        deps.insert(node.id.as_str(), list);
    }

    let order = topo_sort(&graph.nodes, &deps);
    if order.is_none() {
        issues.push(Issue {
            level: "error".into(),
            node: None,
            port: None,
            message: "constructor wiring forms a cycle — break it with a port that wires after deployment".into(),
        });
    }

    let has_error = issues.iter().any(|i| i.level == "error");
    Report {
        ok: !has_error,
        stats: serde_json::json!({
            "nodes": graph.nodes.len(),
            "edges": graph.edges.len(),
            "categories": specs.values().map(|s| s.category.clone()).collect::<HashSet<_>>(),
        }),
        order: order.unwrap_or_default(),
        issues,
    }
}

fn ctor_mentions_port(token: &str, port_id: &str) -> bool {
    token
        .split('|')
        .any(|alt| alt.trim() == format!("$input:{port_id}"))
}

fn topo_sort(nodes: &[Node], deps: &HashMap<&str, Vec<&str>>) -> Option<Vec<String>> {
    let mut done: HashSet<String> = HashSet::new();
    let mut order: Vec<String> = Vec::new();
    let total = nodes.len();
    while order.len() < total {
        let mut progressed = false;
        for node in nodes {
            if done.contains(&node.id) {
                continue;
            }
            let ready = deps
                .get(node.id.as_str())
                .map(|d| d.iter().all(|x| done.contains(*x)))
                .unwrap_or(true);
            if ready {
                done.insert(node.id.clone());
                order.push(node.id.clone());
                progressed = true;
            }
        }
        if !progressed {
            return None;
        }
    }
    Some(order)
}

// ── deployment planning ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Arg {
    /// A literal the wallet passes straight through.
    Value { value: serde_json::Value, r#type: String },
    /// The address of a node deployed earlier in this plan.
    Ref { node: String },
    /// The address running the deployment.
    Owner,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Step {
    Deploy {
        node: String,
        block: String,
        contract: String,
        label: String,
        abi: serde_json::Value,
        bytecode: String,
        args: Vec<Arg>,
    },
    Call {
        node: String,
        target: Arg,
        method: String,
        args: Vec<Arg>,
        note: String,
    },
}

#[derive(Debug, Clone, Serialize)]
pub struct Plan {
    pub name: String,
    pub order: Vec<String>,
    pub steps: Vec<Step>,
    pub warnings: Vec<String>,
}

/// Resolve one catalog arg token against a node's params and wiring.
fn resolve(
    token: &str,
    node: &Node,
    spec: &BlockSpec,
    wires: &HashMap<(&str, &str), &str>,
) -> Result<Arg, String> {
    for alt in token.split('|') {
        let alt = alt.trim();
        if alt == "$owner" {
            return Ok(Arg::Owner);
        }
        if alt == "$self" {
            return Ok(Arg::Ref { node: node.id.clone() });
        }
        // The null address, for optional constructor slots a block fills in
        // later through a wire (a gauge with no escrow yet, say).
        if alt == "$zero" {
            return Ok(Arg::Value {
                value: serde_json::Value::String(
                    "0x0000000000000000000000000000000000000000".into(),
                ),
                r#type: "address".into(),
            });
        }
        if let Some(port_id) = alt.strip_prefix("$input:") {
            if let Some(src) = wires.get(&(node.id.as_str(), port_id)) {
                return Ok(Arg::Ref { node: src.to_string() });
            }
            continue; // unwired — fall through to the next alternative
        }
        if let Some(param) = alt.strip_prefix("$param:") {
            let spec_param = spec
                .params
                .iter()
                .find(|p| p.name == param)
                .ok_or_else(|| format!("{} declares no param '{param}'", spec.name))?;
            let raw = node
                .params
                .get(param)
                .cloned()
                .unwrap_or_else(|| spec_param.default.clone());
            let value = scale_param(spec_param, &raw, node)?;
            return Ok(Arg::Value { value, r#type: spec_param.param_type.clone() });
        }
        return Err(format!("unrecognised arg token '{alt}'"));
    }
    Err(format!("nothing satisfies '{token}' on node {}", node.id))
}

/// Turn a human-entered value into what the constructor expects. The only real
/// work is decimal scaling: the inspector shows "1000000 tokens", the chain
/// wants 1000000 * 10^decimals.
fn scale_param(
    spec: &crate::catalog::ParamSpec,
    raw: &serde_json::Value,
    node: &Node,
) -> Result<serde_json::Value, String> {
    // "$owner" is a placeholder default for address params.
    if raw.as_str() == Some("$owner") {
        return Ok(serde_json::Value::String("$owner".into()));
    }
    let Some(scale) = &spec.scale else {
        return Ok(raw.clone());
    };
    let decimals: u32 = match scale {
        serde_json::Value::Number(n) => n.as_u64().unwrap_or(18) as u32,
        serde_json::Value::String(other_param) => node
            .params
            .get(other_param.as_str())
            .and_then(|v| v.as_u64())
            .unwrap_or(18) as u32,
        _ => 18,
    };
    let text = match raw {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Number(n) => n.to_string(),
        _ => return Err(format!("param '{}' must be a number", spec.name)),
    };
    Ok(serde_json::Value::String(scale_decimal(&text, decimals)?))
}

/// Decimal-string → base units, without pulling in a bignum crate. The values
/// here are user-entered token amounts, so string math is both sufficient and
/// exactly lossless — which f64 would not be.
pub fn scale_decimal(text: &str, decimals: u32) -> Result<String, String> {
    let text = text.trim();
    let (negative, text) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text),
    };
    if negative {
        return Err("negative amounts are not allowed".into());
    }
    let (whole, frac) = match text.split_once('.') {
        Some((w, f)) => (w, f),
        None => (text, ""),
    };
    if whole.is_empty() && frac.is_empty() {
        return Ok("0".into());
    }
    if !whole.chars().all(|c| c.is_ascii_digit()) || !frac.chars().all(|c| c.is_ascii_digit()) {
        return Err(format!("'{text}' is not a number"));
    }
    if frac.len() > decimals as usize {
        return Err(format!(
            "'{text}' has more than {decimals} decimal places"
        ));
    }
    let mut digits = String::with_capacity(whole.len() + decimals as usize);
    digits.push_str(whole);
    digits.push_str(frac);
    for _ in 0..(decimals as usize - frac.len()) {
        digits.push('0');
    }
    let trimmed = digits.trim_start_matches('0');
    Ok(if trimmed.is_empty() { "0".into() } else { trimmed.to_string() })
}

pub fn plan(
    catalog: &Catalog,
    graph: &Graph,
    compiled: &HashMap<String, crate::compile::Artifact>,
    report: &Report,
) -> Result<Plan, String> {
    if !report.ok {
        return Err("graph does not validate".into());
    }
    let wires = wiring(graph);
    let by_id: HashMap<&str, &Node> = graph.nodes.iter().map(|n| (n.id.as_str(), n)).collect();
    let mut steps = Vec::new();
    let mut warnings = Vec::new();

    for node_id in &report.order {
        let node = by_id[node_id.as_str()];
        let spec = catalog.block(&node.block).ok_or("unknown block")?;
        let artifact = compiled
            .get(&spec.contract)
            .ok_or_else(|| format!("{} was not compiled", spec.contract))?;

        let mut args = Vec::new();
        for token in &spec.ctor {
            args.push(resolve(token, node, spec, &wires)?);
        }
        steps.push(Step::Deploy {
            node: node.id.clone(),
            block: spec.id.clone(),
            contract: spec.contract.clone(),
            label: node.label.clone().unwrap_or_else(|| spec.name.clone()),
            abi: artifact.abi.clone(),
            bytecode: artifact.bytecode.clone(),
            args,
        });
    }

    // Post-deploy wiring, emitted after every deployment so any target exists.
    for node_id in &report.order {
        let node = by_id[node_id.as_str()];
        let spec = catalog.block(&node.block).ok_or("unknown block")?;
        for wire in &spec.wires {
            if !wires.contains_key(&(node.id.as_str(), wire.when.as_str())) {
                continue;
            }
            let target = resolve(&wire.target, node, spec, &wires)?;
            let mut args = Vec::new();
            for token in &wire.args {
                args.push(resolve(token, node, spec, &wires)?);
            }
            steps.push(Step::Call {
                node: node.id.clone(),
                target,
                method: wire.method.clone(),
                args,
                note: wire.note.clone().unwrap_or_else(|| wire.method.clone()),
            });
        }
    }

    if graph.nodes.len() > 12 {
        warnings.push("large protocols cost real gas — deploy to a testnet first".into());
    }

    Ok(Plan {
        name: if graph.name.is_empty() { "Untitled protocol".into() } else { graph.name.clone() },
        order: report.order.clone(),
        steps,
        warnings,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scales_whole_and_fractional_amounts() {
        assert_eq!(scale_decimal("1", 18).unwrap(), "1000000000000000000");
        assert_eq!(scale_decimal("0.001", 18).unwrap(), "1000000000000000");
        assert_eq!(scale_decimal("1000000", 6).unwrap(), "1000000000000");
        assert_eq!(scale_decimal("0", 18).unwrap(), "0");
        assert!(scale_decimal("0.1", 0).is_err());
        assert!(scale_decimal("abc", 18).is_err());
    }
}
