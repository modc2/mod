//! solc driver.
//!
//! The catalog is a fixed set of contracts, so we compile all of them ONCE at
//! startup (in a blocking task) and keep the artifacts in memory. A graph
//! compile is then just "hand me the artifacts these nodes need" — instant, and
//! it means the canvas can show real bytecode sizes while you drag.

use serde::Serialize;
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Debug, Clone, Serialize)]
pub struct Artifact {
    pub contract: String,
    pub file: String,
    pub abi: serde_json::Value,
    pub bytecode: String,
    #[serde(rename = "deployedSize")]
    pub deployed_size: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct CompileResult {
    pub solc: String,
    pub version: String,
    pub artifacts: HashMap<String, Artifact>,
    pub warnings: Vec<String>,
}

/// Find a solc we can actually run. Ordered by "what the operator asked for",
/// then the svm/foundry install, then PATH.
pub fn find_solc() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("DEFI_SOLC") {
        let p = PathBuf::from(explicit);
        if p.is_file() {
            return Some(p);
        }
    }
    if let Some(home) = dirs::home_dir() {
        let svm = home.join(".local/share/svm");
        if let Ok(entries) = std::fs::read_dir(&svm) {
            let mut versions: Vec<PathBuf> = entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.is_dir())
                .collect();
            versions.sort();
            for dir in versions.iter().rev() {
                let name = dir.file_name()?.to_string_lossy().to_string();
                let bin = dir.join(format!("solc-{name}"));
                if bin.is_file() {
                    return Some(bin);
                }
            }
        }
    }
    let which = Command::new("sh").arg("-c").arg("command -v solc").output().ok()?;
    let path = String::from_utf8_lossy(&which.stdout).trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(PathBuf::from(path))
    }
}

pub fn solc_version(solc: &Path) -> String {
    Command::new(solc)
        .arg("--version")
        .output()
        .ok()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .last()
                .unwrap_or("")
                .trim()
                .to_string()
        })
        .unwrap_or_else(|| "unknown".into())
}

/// Compile every source in the catalog directory in one standard-json run, so
/// `import "./common.sol"` resolves from the in-memory source map rather than
/// the filesystem.
pub fn compile_sources(
    solc: &Path,
    sources: &HashMap<String, String>,
) -> Result<CompileResult, String> {
    let source_map: serde_json::Map<String, serde_json::Value> = sources
        .iter()
        .map(|(name, body)| {
            (name.clone(), serde_json::json!({ "content": body }))
        })
        .collect();

    let input = serde_json::json!({
        "language": "Solidity",
        "sources": source_map,
        "settings": {
            "optimizer": { "enabled": true, "runs": 200 },
            "outputSelection": { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } }
        }
    });

    let mut child = Command::new(solc)
        .arg("--standard-json")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("could not run {}: {e}", solc.display()))?;

    child
        .stdin
        .as_mut()
        .ok_or("no stdin")?
        .write_all(input.to_string().as_bytes())
        .map_err(|e| e.to_string())?;

    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let parsed: serde_json::Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("solc returned unparseable output: {e}"))?;

    let mut warnings = Vec::new();
    if let Some(errors) = parsed.get("errors").and_then(|e| e.as_array()) {
        let mut fatal = Vec::new();
        for entry in errors {
            let severity = entry.get("severity").and_then(|s| s.as_str()).unwrap_or("error");
            let message = entry
                .get("formattedMessage")
                .and_then(|m| m.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if severity == "error" {
                fatal.push(message);
            } else {
                warnings.push(message);
            }
        }
        if !fatal.is_empty() {
            return Err(fatal.join("\n\n"));
        }
    }

    let mut artifacts = HashMap::new();
    if let Some(files) = parsed.get("contracts").and_then(|c| c.as_object()) {
        for (file, contracts) in files {
            for (name, body) in contracts.as_object().into_iter().flatten() {
                let bytecode = body
                    .pointer("/evm/bytecode/object")
                    .and_then(|b| b.as_str())
                    .unwrap_or("")
                    .to_string();
                if bytecode.is_empty() {
                    continue; // interfaces and libraries
                }
                let deployed = body
                    .pointer("/evm/deployedBytecode/object")
                    .and_then(|b| b.as_str())
                    .unwrap_or("");
                artifacts.insert(
                    name.clone(),
                    Artifact {
                        contract: name.clone(),
                        file: file.clone(),
                        abi: body.get("abi").cloned().unwrap_or(serde_json::json!([])),
                        bytecode: format!("0x{bytecode}"),
                        deployed_size: deployed.len() / 2,
                    },
                );
            }
        }
    }

    Ok(CompileResult {
        solc: solc.display().to_string(),
        version: solc_version(solc),
        artifacts,
        warnings,
    })
}
