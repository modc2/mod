//! Every piece of mutable state this module owns, on disk under `~/.mod/github/`.
//!
//! Nothing private is ever committed to the source tree (the fleet convention):
//! the ACL, the bans, the audit log and the cache all live in the home dir, and
//! anything that could carry a secret is written 0600. Writes go through a
//! temp-file + rename so a reader — or the Python CLI, which reads the same
//! files — never sees a half-written document.

use std::io::Write;
use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde::Serialize;

/// `~/.mod/github/<name>`, creating the directory on demand.
pub fn path(name: &str) -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    let dir = PathBuf::from(home).join(".mod/github");
    let _ = std::fs::create_dir_all(&dir);
    dir.join(name)
}

/// `~/.mod/<module>/<name>` — for reading a *sibling* module's state, which is
/// how the GitHub login stays owned by the `git` module instead of duplicated
/// here.
pub fn sibling(module: &str, name: &str) -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join(module).join(name)
}

/// Read a JSON document, or the type's default when it is missing or corrupt.
/// A damaged cache means cold, not fatal.
pub fn read<T: DeserializeOwned + Default>(p: &Path) -> T {
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Atomic write. `secret` additionally clamps the file to 0600.
pub fn write<T: Serialize>(p: &Path, value: &T, secret: bool) -> std::io::Result<()> {
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = p.with_extension(format!("{}.tmp", std::process::id()));
    {
        let mut f = std::fs::File::create(&tmp)?;
        #[cfg(unix)]
        if secret {
            use std::os::unix::fs::PermissionsExt;
            let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
        }
        f.write_all(serde_json::to_string_pretty(value)?.as_bytes())?;
        f.write_all(b"\n")?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp, p)
}

/// Seconds since the epoch.
pub fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}
