//! The block catalog.
//!
//! A "block" is a reusable DeFi module: one Solidity contract plus the metadata
//! that makes it composable — which port types it consumes, which it provides,
//! which constructor args come from the user and which come from a wire.
//!
//! The catalog is DATA (blocks/catalog.json + sibling .sol files), not code.
//! Shipping a new block is a JSON entry and a contract; nothing here changes.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortType {
    pub label: String,
    pub color: String,
    #[serde(default)]
    pub iface: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InputPort {
    pub id: String,
    pub label: String,
    #[serde(rename = "type")]
    pub port_type: String,
    #[serde(default)]
    pub required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParamSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub param_type: String,
    pub label: String,
    #[serde(default)]
    pub default: serde_json::Value,
    /// Either a number (fixed decimals) or the name of a sibling param holding
    /// the decimals. Present means the UI shows whole units and we scale.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scale: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub help: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max: Option<u64>,
}

/// A post-deployment call emitted when an optional port is connected. This is
/// how two blocks point at each other without a constructor cycle.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WireSpec {
    /// Port id whose connection triggers this call.
    pub when: String,
    /// Arg token for the contract being called.
    pub target: String,
    /// Solidity signature, e.g. "setStrategy(address)".
    pub method: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlockSpec {
    pub id: String,
    pub contract: String,
    pub file: String,
    pub name: String,
    pub category: String,
    #[serde(default)]
    pub icon: String,
    pub summary: String,
    #[serde(default)]
    pub docs: String,
    #[serde(default)]
    pub provides: Vec<String>,
    #[serde(default)]
    pub inputs: Vec<InputPort>,
    #[serde(default)]
    pub params: Vec<ParamSpec>,
    #[serde(default)]
    pub ctor: Vec<String>,
    #[serde(default)]
    pub wires: Vec<WireSpec>,

    /// Filled in at load time — the contract source, so the UI can show it and
    /// an auditor can read exactly what they are about to deploy.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemplateNode {
    pub id: String,
    pub block: String,
    #[serde(default)]
    pub x: f64,
    #[serde(default)]
    pub y: f64,
    #[serde(default)]
    pub params: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemplateEdge {
    pub from: String,
    pub to: String,
    pub port: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Template {
    pub id: String,
    pub name: String,
    pub summary: String,
    pub nodes: Vec<TemplateNode>,
    pub edges: Vec<TemplateEdge>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Catalog {
    pub version: String,
    #[serde(default)]
    pub protocol: String,
    #[serde(rename = "portTypes")]
    pub port_types: HashMap<String, PortType>,
    pub blocks: Vec<BlockSpec>,
    #[serde(default)]
    pub templates: Vec<Template>,

    #[serde(skip)]
    pub sources: HashMap<String, String>,
    #[serde(skip)]
    pub dir: PathBuf,
}

impl Catalog {
    pub fn load(dir: &Path) -> Result<Self, String> {
        let manifest = dir.join("catalog.json");
        let raw = std::fs::read_to_string(&manifest)
            .map_err(|e| format!("catalog.json unreadable at {}: {e}", manifest.display()))?;
        let mut cat: Catalog =
            serde_json::from_str(&raw).map_err(|e| format!("catalog.json is not valid: {e}"))?;
        cat.dir = dir.to_path_buf();

        // Every .sol in the directory is a compilation unit; standard-json wants
        // them all so `import "./common.sol"` resolves without a remapping.
        for entry in std::fs::read_dir(dir).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            if path.extension().and_then(|e| e.to_str()) == Some("sol") {
                let name = path.file_name().unwrap().to_string_lossy().to_string();
                let body = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
                cat.sources.insert(name, body);
            }
        }

        for block in &mut cat.blocks {
            block.source = cat.sources.get(&block.file).cloned();
            if block.source.is_none() {
                return Err(format!("block {} references missing {}", block.id, block.file));
            }
        }
        Ok(cat)
    }

    pub fn block(&self, id: &str) -> Option<&BlockSpec> {
        self.blocks.iter().find(|b| b.id == id)
    }

    /// Catalog without contract sources — the payload the palette actually needs.
    pub fn summary(&self) -> serde_json::Value {
        let blocks: Vec<serde_json::Value> = self
            .blocks
            .iter()
            .map(|b| {
                let mut v = serde_json::to_value(b).unwrap();
                v.as_object_mut().unwrap().remove("source");
                v
            })
            .collect();
        serde_json::json!({
            "version": self.version,
            "protocol": self.protocol,
            "portTypes": self.port_types,
            "blocks": blocks,
            "templates": self.templates,
        })
    }
}
