use serde_json::Value;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

/// Bridge to Python mod.py via subprocess.
/// Each call serialises args as JSON, writes to stdin of a Python helper,
/// reads JSON from stdout.
#[derive(Clone)]
pub struct Bridge {
    pub mod_dir: PathBuf,
}

const HELPER: &str = r#"
import sys, json, traceback
sys.path.insert(0, MOD_DIR_PLACEHOLDER)
try:
    from mod import Mod
    req = json.loads(sys.stdin.buffer.read())
    m = Mod()
    fn = getattr(m, req['fn'])
    result = fn(**req.get('args', {}))
    print(json.dumps(result, default=str))
    sys.stdout.flush()
except Exception as e:
    print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
    sys.stdout.flush()
    sys.exit(1)
"#;

impl Bridge {
    pub fn new(mod_dir: PathBuf) -> Self {
        Bridge { mod_dir }
    }

    /// Call a function on mod.Mod() with the given args dict.
    /// Returns parsed JSON Value on success.
    pub fn call(&self, fn_name: &str, args: Value) -> Result<Value, String> {
        let mod_dir_str = self.mod_dir.to_string_lossy();
        let helper = HELPER.replace("MOD_DIR_PLACEHOLDER", &format!("'{}'", mod_dir_str));

        let payload = serde_json::json!({
            "fn": fn_name,
            "args": args,
        });
        let payload_bytes = serde_json::to_vec(&payload)
            .map_err(|e| format!("serialise error: {e}"))?;

        let mut child = Command::new("python3")
            .arg("-c")
            .arg(&helper)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .current_dir(&self.mod_dir)
            .spawn()
            .map_err(|e| format!("failed to spawn python3: {e}"))?;

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(&payload_bytes)
                .map_err(|e| format!("stdin write error: {e}"))?;
        }

        let output = child
            .wait_with_output()
            .map_err(|e| format!("wait error: {e}"))?;

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        if !output.status.success() {
            let err = if !stderr.is_empty() {
                stderr.trim().to_string()
            } else {
                stdout.trim().to_string()
            };
            return Err(format!("python error ({}): {}", fn_name, err));
        }

        let text = stdout.trim();
        if text.is_empty() {
            return Ok(Value::Null);
        }

        serde_json::from_str(text)
            .map_err(|e| format!("json parse error from python ({fn_name}): {e}\nraw: {text}"))
    }

    /// Call with a flat key=string_value args map.
    pub fn call_kv(&self, fn_name: &str, pairs: &[(&str, &str)]) -> Result<Value, String> {
        let mut map = serde_json::Map::new();
        for (k, v) in pairs {
            map.insert(k.to_string(), Value::String(v.to_string()));
        }
        self.call(fn_name, Value::Object(map))
    }

    /// Call with no args.
    pub fn call0(&self, fn_name: &str) -> Result<Value, String> {
        self.call(fn_name, serde_json::json!({}))
    }

    /// Async version — runs the subprocess on tokio's blocking thread pool.
    pub async fn call_async(&self, fn_name: &'static str, args: Value) -> Result<Value, String> {
        let bridge = self.clone();
        tokio::task::spawn_blocking(move || bridge.call(fn_name, args))
            .await
            .map_err(|e| format!("spawn_blocking: {e}"))?
    }

    /// Async version with no args.
    pub async fn call0_async(&self, fn_name: &'static str) -> Result<Value, String> {
        self.call_async(fn_name, serde_json::json!({})).await
    }

    /// Read the token from ~/.mod/wingman/token.
    pub fn read_token(&self) -> Option<String> {
        let path = dirs::home_dir()?.join(".mod/wingman/token");
        std::fs::read_to_string(path).ok().map(|s| s.trim().to_string())
    }
}
