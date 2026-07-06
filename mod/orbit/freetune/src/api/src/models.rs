//! Model registry. The source of truth is trainer/common.py (so the dropdown,
//! the trainer, and the CLI never drift). We shell out once at startup and cache
//! the JSON; if python is unavailable we fall back to a small built-in list so
//! the API still boots.

use serde_json::{json, Value};

use crate::trainer_python;

pub fn load(trainer_dir: &str) -> Value {
    match std::process::Command::new(trainer_python())
        .arg("-m")
        .arg("trainer.common")
        .arg("--models")
        .current_dir(trainer_dir)
        .output()
    {
        Ok(out) if out.status.success() => {
            if let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) {
                return v;
            }
        }
        _ => {}
    }
    fallback()
}

fn fallback() -> Value {
    json!({
        "default": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "models": [
            {"id": "Qwen/Qwen2.5-Coder-0.5B-Instruct", "label": "Qwen2.5-Coder 0.5B (Instruct)", "params": "0.5B", "cpu": "good", "note": "Default."},
            {"id": "Qwen/Qwen2.5-0.5B-Instruct", "label": "Qwen2.5 0.5B (Instruct)", "params": "0.5B", "cpu": "good", "note": "General 0.5B."},
            {"id": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "label": "Qwen2.5-Coder 1.5B (Instruct)", "params": "1.5B", "cpu": "slow", "note": "Slower on CPU."}
        ]
    })
}
