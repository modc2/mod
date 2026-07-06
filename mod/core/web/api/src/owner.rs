//! Owner gate for privileged catalog mutations (adding modules from GitHub).
//!
//! The front door is read-only for the public, but the owner can grow the
//! catalog by importing a GitHub repo as a new module. That action is gated by
//! a single bearer token, kept OFF the source tree (per the mod convention that
//! private auth state lives under `~/.mod/<module>/`, never in committed config).
//!
//! Resolution order for the token:
//!   1. `MOD_WEB_OWNER_TOKEN` env var (handy for ops / containers), else
//!   2. `~/.mod/web/owner.token` on disk, else
//!   3. one is generated, written to that file (0600), and logged once so the
//!      owner can copy it into the UI.

use std::io::Write;
use std::path::PathBuf;

pub struct OwnerAuth {
    token: String,
}

impl OwnerAuth {
    pub fn from_env() -> Self {
        if let Ok(tok) = std::env::var("MOD_WEB_OWNER_TOKEN") {
            let tok = tok.trim().to_string();
            if !tok.is_empty() {
                tracing::info!("owner token: loaded from MOD_WEB_OWNER_TOKEN");
                return Self { token: tok };
            }
        }

        let path = token_path();
        if let Some(p) = &path {
            if let Ok(raw) = std::fs::read_to_string(p) {
                let tok = raw.trim().to_string();
                if !tok.is_empty() {
                    tracing::info!("owner token: loaded from {}", p.display());
                    return Self { token: tok };
                }
            }
        }

        // Generate, persist, and surface a fresh token.
        let token = gen_token();
        if let Some(p) = &path {
            if let Err(e) = persist_token(p, &token) {
                tracing::warn!("could not persist owner token to {}: {e}", p.display());
            } else {
                tracing::info!(
                    "owner token: generated a new one → {}\n\n    >>> mod-web owner token: {token}\n",
                    p.display()
                );
            }
        } else {
            tracing::warn!(
                "owner token: no HOME to persist into — using an ephemeral token: {token}"
            );
        }
        Self { token }
    }

    /// Constant-ish-time check that `candidate` matches the owner token.
    pub fn check(&self, candidate: &str) -> bool {
        let a = self.token.as_bytes();
        let b = candidate.trim().as_bytes();
        if a.len() != b.len() {
            return false;
        }
        let mut diff = 0u8;
        for (x, y) in a.iter().zip(b.iter()) {
            diff |= x ^ y;
        }
        diff == 0
    }
}

fn token_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".mod/web/owner.token"))
}

/// 32 hex chars (128 bits) from the OS CSPRNG, with a degenerate fallback so a
/// missing `/dev/urandom` never panics the service.
fn gen_token() -> String {
    let mut bytes = [0u8; 16];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut bytes))
        .is_err()
    {
        // Fallback: nanosecond clock + pid mixed into the buffer.
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let pid = std::process::id() as u128;
        let seed = nanos ^ (pid << 64);
        for (i, b) in bytes.iter_mut().enumerate() {
            *b = ((seed >> (i * 8)) & 0xff) as u8;
        }
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn persist_token(path: &PathBuf, token: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(path)?;
    // Best-effort 0600 so the token isn't world-readable.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = f.set_permissions(std::fs::Permissions::from_mode(0o600));
    }
    f.write_all(token.as_bytes())?;
    f.write_all(b"\n")?;
    Ok(())
}
