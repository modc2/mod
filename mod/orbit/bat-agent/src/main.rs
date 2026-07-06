//! bat-agent — cloned agent module.
use agent_base::{run_cli, AbiEntry, Contract};
use serde_json::{json, Value};

struct Mod;
impl Contract for Mod {
    const NAME: &'static str = "bat-agent";
    const BINARY: &'static str = "bat";
    const DESCRIPTION: &'static str = "bat-agent agent (auto-scaffolded)";

    fn abi() -> Vec<AbiEntry> {
        vec![
            AbiEntry { name: "info".into(),   kind: "view".into(), owner_only: false, inputs: vec![], doc: "Return info".into() },
            AbiEntry { name: "submit".into(), kind: "tx".into(),   owner_only: false, inputs: vec![], doc: "Queue a prompt".into() },
        ]
    }
    fn call(method: &str, args: Value) -> Value {
        match method {
            "info" => json!({ "name": Self::NAME, "binary": Self::BINARY, "lang": "rust" }),
            "submit" => json!({ "queued": true, "prompt": args.get("prompt") }),
            _ => json!({ "error": format!("unknown method: {}", method) }),
        }
    }
}

fn main() { run_cli::<Mod>(); }
