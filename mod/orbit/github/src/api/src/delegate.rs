//! Calling the `git` module, which owns the GitHub login.
//!
//! This module deliberately does not implement a second GitHub login. `git`
//! already binds a GitHub account to a mod key and stores it off-chain in
//! `~/.mod/git/github.json` (0600) — one identity per key, in one place, so
//! revoking it there revokes it everywhere.
//!
//! The delegation runs a short Python program under the fleet's own
//! interpreter. The PAT is handed over on **stdin**, never as an argv element
//! and never inside a shell string: arguments are world-readable in
//! `/proc/*/cmdline`, and a credential that lands in the process table is a
//! credential that has leaked.

use std::io::Write;
use std::process::Stdio;

/// Run `m git/<fn>(**kwargs)` and return its JSON result.
pub fn git_call(func: &str, kwargs: serde_json::Value) -> Result<serde_json::Value, String> {
    let root = std::env::var("MOD_ROOT").unwrap_or_else(|_| "/root/mod".into());
    // The payload arrives on stdin so nothing sensitive is ever an argument.
    let program = format!(
        r#"
import json, sys, contextlib, io
sys.path.insert(0, {root:?})
payload = json.load(sys.stdin)
import mod as m
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        out = getattr(m.mod('git')(), payload['fn'])(**payload['kwargs'])
    print(json.dumps({{'ok': True, 'result': out}}, default=str))
except Exception as e:
    print(json.dumps({{'ok': False, 'error': f'{{type(e).__name__}}: {{e}}'}}))
"#
    );
    let mut child = std::process::Command::new("python3")
        .arg("-c")
        .arg(&program)
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("cannot reach the git module: {e}"))?;
    let body = serde_json::json!({ "fn": func, "kwargs": kwargs }).to_string();
    child
        .stdin
        .as_mut()
        .ok_or("cannot reach the git module: no stdin")?
        .write_all(body.as_bytes())
        .map_err(|e| format!("cannot reach the git module: {e}"))?;
    let out = child
        .wait_with_output()
        .map_err(|e| format!("git module failed: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    let last = text.lines().rev().find(|l| l.trim_start().starts_with('{')).unwrap_or("");
    let v: serde_json::Value =
        serde_json::from_str(last).map_err(|_| "git module returned no result".to_string())?;
    if v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
        Ok(v.get("result").cloned().unwrap_or(serde_json::Value::Null))
    } else {
        Err(v.get("error").and_then(|e| e.as_str()).unwrap_or("git module refused").to_string())
    }
}
