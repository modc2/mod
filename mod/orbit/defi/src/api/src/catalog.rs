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

    /// Filled in at load time from audits/<id>.json — the agent audit's verdict
    /// (risk, counts, worst finding), never the whole report. `GET
    /// /catalog/{id}/audit` is the whole report.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audit: Option<serde_json::Value>,
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
    /// Full agent audits keyed by block id (plus "common" for the shared base).
    #[serde(skip)]
    pub audits: HashMap<String, serde_json::Value>,
    #[serde(skip)]
    pub dir: PathBuf,
}

/// Severity ladder, worst first. `risk` and finding `severity` both use it.
pub const SEVERITIES: [&str; 5] = ["critical", "high", "medium", "low", "info"];

fn severity_rank(s: &str) -> usize {
    SEVERITIES.iter().position(|x| *x == s).unwrap_or(SEVERITIES.len())
}

/// The part of an audit every block carries in the catalog: the verdict, the
/// counts, and the single worst finding's title — enough to badge a block, not
/// enough to skip reading the report.
pub fn audit_summary(full: &serde_json::Value) -> serde_json::Value {
    let findings = full
        .get("findings")
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();
    let worst = findings
        .iter()
        .min_by_key(|f| severity_rank(f.get("severity").and_then(|s| s.as_str()).unwrap_or("")))
        .map(|f| {
            serde_json::json!({
                "id": f.get("id"),
                "severity": f.get("severity"),
                "title": f.get("title"),
            })
        });
    serde_json::json!({
        "risk": full.get("risk").and_then(|r| r.as_str()).unwrap_or("unknown"),
        "counts": full.get("counts").cloned().unwrap_or(serde_json::json!({})),
        "findings": findings.len(),
        "audited_at": full.get("audited_at"),
        "auditor": full.get("auditor"),
        "worst": worst,
    })
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

        // Agent audits ride alongside the sources: audits/<block id>.json. A
        // missing or malformed audit is a missing badge, never a failed boot.
        let audits_dir = dir.join("audits");
        if let Ok(entries) = std::fs::read_dir(&audits_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let id = path.file_stem().unwrap().to_string_lossy().to_string();
                match std::fs::read_to_string(&path)
                    .map_err(|e| e.to_string())
                    .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).map_err(|e| e.to_string()))
                {
                    Ok(v) => {
                        cat.audits.insert(id, v);
                    }
                    Err(e) => eprintln!("[defi] audit {} unreadable: {e}", path.display()),
                }
            }
        }

        for block in &mut cat.blocks {
            block.source = cat.sources.get(&block.file).cloned();
            if block.source.is_none() {
                return Err(format!("block {} references missing {}", block.id, block.file));
            }
            block.audit = cat.audits.get(&block.id).map(audit_summary);
        }
        Ok(cat)
    }

    /// The full agent audit of one block (or of "common", the shared base).
    pub fn audit(&self, id: &str) -> Option<&serde_json::Value> {
        self.audits.get(id)
    }

    /// Every audit's verdict on one page, with the fleet tally — what a deployer
    /// reads before picking blocks, and what the MCP tool returns with no id.
    pub fn audits_overview(&self) -> serde_json::Value {
        let mut totals: HashMap<&str, u64> = HashMap::new();
        let mut blocks = Vec::new();
        for b in &self.blocks {
            let Some(full) = self.audits.get(&b.id) else {
                blocks.push(serde_json::json!({ "id": b.id, "name": b.name, "contract": b.contract, "audited": false }));
                continue;
            };
            let summary = audit_summary(full);
            if let Some(counts) = summary.get("counts").and_then(|c| c.as_object()) {
                for sev in SEVERITIES {
                    *totals.entry(sev).or_default() += counts.get(sev).and_then(|n| n.as_u64()).unwrap_or(0);
                }
            }
            let mut v = serde_json::json!({
                "id": b.id, "name": b.name, "contract": b.contract, "category": b.category, "audited": true,
                "summary": full.get("summary"),
            });
            for (k, val) in summary.as_object().unwrap() {
                v[k] = val.clone();
            }
            blocks.push(v);
        }
        blocks.sort_by_key(|b| severity_rank(b.get("risk").and_then(|r| r.as_str()).unwrap_or("zz")));
        serde_json::json!({
            "audited": self.audits.len(),
            "blocks": blocks,
            "common": self.audits.get("common").map(|c| {
                let mut s = audit_summary(c);
                s["summary"] = c.get("summary").cloned().unwrap_or(serde_json::Value::Null);
                s["contract"] = c.get("contract").cloned().unwrap_or(serde_json::Value::Null);
                s
            }),
            "totals": totals,
            "severities": SEVERITIES,
            "note": "Agent audits of unaudited reference implementations. They reduce the unknowns; they certify nothing. Read the full report at /catalog/{id}/audit before deploying.",
        })
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

#[cfg(test)]
mod tests {
    use super::*;

    fn blocks_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("blocks")
    }

    #[test]
    fn every_block_has_a_well_formed_audit() {
        let cat = Catalog::load(&blocks_dir()).expect("catalog loads");
        assert!(cat.audits.contains_key("common"), "the shared base has its own audit");
        for b in &cat.blocks {
            let full = cat.audit(&b.id).unwrap_or_else(|| panic!("block {} has no audit", b.id));
            assert_eq!(full["block"], b.id);
            assert_eq!(full["contract"], b.contract);
            assert_eq!(full["file"], b.file);
            let risk = full["risk"].as_str().unwrap_or("");
            assert!(SEVERITIES[..4].contains(&risk), "{}: risk {risk:?}", b.id);
            let findings = full["findings"].as_array().unwrap_or_else(|| panic!("{}: findings", b.id));
            for sev in SEVERITIES {
                let n = findings.iter().filter(|f| f["severity"] == sev).count() as u64;
                assert_eq!(full["counts"][sev].as_u64().unwrap_or(0), n, "{}: counts.{sev}", b.id);
            }
            for f in findings {
                for key in ["id", "severity", "title", "where", "detail", "exploit", "recommendation"] {
                    assert!(f[key].as_str().map(|s| !s.is_empty()).unwrap_or(false), "{}: {} lacks {key}", b.id, f["id"]);
                }
            }
            // The summary the catalog carries agrees with the report.
            let s = b.audit.as_ref().expect("summary attached at load");
            assert_eq!(s["risk"], full["risk"]);
            assert_eq!(s["findings"].as_u64().unwrap(), findings.len() as u64);
            if !findings.is_empty() {
                assert!(s["worst"]["title"].is_string(), "{}: worst finding", b.id);
            }
        }
    }

    #[test]
    fn overview_is_sorted_worst_first() {
        let cat = Catalog::load(&blocks_dir()).unwrap();
        let ov = cat.audits_overview();
        let ranks: Vec<usize> = ov["blocks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|b| severity_rank(b["risk"].as_str().unwrap_or("zz")))
            .collect();
        assert!(ranks.windows(2).all(|w| w[0] <= w[1]), "{ranks:?}");
        assert_eq!(ov["audited"].as_u64().unwrap() as usize, cat.audits.len());
    }
}
