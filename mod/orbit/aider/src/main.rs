//! aider — Rust agent contract.
//! Mirrors the Python and JS counterparts. Compiled to `aider-contract`
//! and invoked by the dev FastAPI dispatcher.

use agent_base::{run_cli, AbiEntry, AbiInput, Contract};
use serde_json::{json, Value};

struct Mod;

impl Contract for Mod {
    const NAME: &'static str = "aider";
    const ICON: &'static str = "A";
    const COLOR: &'static str = "#ef4444";
    const BINARY: &'static str = "aider";
    const DEFAULT_MODEL: &'static str = "gpt-4o";
    const ENV_KEY: &'static str = "AIDER_API_KEY";
    const DESCRIPTION: &'static str = "Aider AI pair programmer (Rust contract)";

    fn abi() -> Vec<AbiEntry> {
        vec![
            AbiEntry { name: "info".into(),    kind: "view".into(), owner_only: false, inputs: vec![], doc: "Return agent info".into() },
            AbiEntry { name: "health".into(),  kind: "view".into(), owner_only: false, inputs: vec![], doc: "Probe Aider CLI".into() },
            AbiEntry { name: "version".into(), kind: "view".into(), owner_only: false, inputs: vec![], doc: "Return Aider version string".into() },
            AbiEntry { name: "submit".into(),  kind: "tx".into(),   owner_only: false,
                inputs: vec![
                    AbiInput { name: "prompt".into(), ty: "string".into() },
                    AbiInput { name: "model".into(),  ty: "string".into() },
                ],
                doc: "Queue a prompt for aider".into() },
        ]
    }

    fn call(method: &str, args: Value) -> Value {
        match method {
            "info" => json!({
                "name": Self::NAME,
                "icon": Self::ICON,
                "color": Self::COLOR,
                "binary": Self::BINARY,
                "lang": "rust",
                "description": Self::DESCRIPTION,
            }),
            "health" => json!({ "service": Self::NAME, "status": which(Self::BINARY).map(|_| "installed").unwrap_or("not-installed") }),
            "version" => json!({ "version": "0.1.0", "contract_code_hash": agent_base::code_hash() }),
            "submit" => {
                let prompt = args.get("prompt").and_then(|v| v.as_str()).unwrap_or("");
                let model  = args.get("model").and_then(|v| v.as_str()).unwrap_or(Self::DEFAULT_MODEL);
                agent_base::emit::<Self>("job_submitted", json!({ "prompt": prompt.chars().take(50).collect::<String>(), "model": model }));
                let mut state = agent_base::load_state::<Self>();
                let n = state.get("jobs_submitted").and_then(|v| v.as_i64()).unwrap_or(0) + 1;
                state["jobs_submitted"] = json!(n);
                agent_base::save_state::<Self>(&state);
                json!({ "queued": true, "prompt": prompt, "model": model, "jobs_submitted": n })
            }
            _ => json!({ "error": format!("unknown method: {}", method) }),
        }
    }
}

fn which(binary: &str) -> Option<String> {
    use std::process::Command;
    Command::new("which").arg(binary).output().ok().and_then(|o| {
        let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if s.is_empty() { None } else { Some(s) }
    })
}

fn main() { run_cli::<Mod>(); }
