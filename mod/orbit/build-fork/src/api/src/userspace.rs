//! Per-role filesystem confinement for the build API.
//!
//! Policy tiers (single chokepoint = `resolve_path`):
//!   • OWNER  → confined to the module tree `~/mod/mod` (orbit + core). The owner
//!              (the configured address or any co-owner wallet in owners.json)
//!              edits any module but nothing outside the tree (no /etc, ~/.ssh,
//!              arbitrary host files).
//!   • GUEST EDITOR → holder of a live QR edit invite (auth::grant_edit_scope).
//!              An invite naming modules opens exactly those module dirs (plus
//!              the guest's own peer root); an "everything" invite opens the
//!              whole module tree, same surface as the owner. Access dies with
//!              the invite — expiry or revoke drops them back to PEER.
//!   • PEER   → every other authenticated address, confined to its own private
//!              root `~/.mod/peers/<addr>/`. A peer scaffolds/edits their mods at
//!              `~/.mod/peers/<addr>/<mod>/…` and can never reach the orbit.
//!   • LOCAL  → empty caller (BUILD_FORK_JOBS_LOCAL=1, trusted host CLI) → full host.
//!
//! The whitelist still governs sign-in (see auth::is_trusted) but no longer grants
//! orbit write access — whitelisted users are peers for filesystem purposes. Only
//! an explicit invite widens a guest, and only for as long as it lives.

use crate::auth;
use axum::http::HeaderMap;
use std::path::{Path, PathBuf};

pub const TOOL: &str = "build-fork";

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}

/// The module tree the owner is confined to: `~/mod/mod` (orbit + core).
pub fn mod_root() -> PathBuf {
    home().join("mod").join("mod")
}

/// A peer's private root: `~/.mod/peers/<addr>` (address lowercased).
pub fn peer_root(addr: &str) -> PathBuf {
    home().join(".mod").join("peers").join(addr.to_lowercase())
}

/// Extract the calling address from request headers. Empty string when
/// no valid token + the server is in local mode (BUILD_FORK_JOBS_LOCAL=1).
/// Returns an Err(msg) when auth is required but missing/invalid.
pub fn caller(headers: &HeaderMap) -> Result<String, String> {
    match auth::extract_address_from_headers(headers) {
        Ok(addr) => Ok(addr),
        Err(e) => {
            if std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1" {
                Ok(String::new())
            } else {
                Err(e)
            }
        }
    }
}

pub fn is_owner(addr: &str) -> bool {
    // Delegates to auth so co-owner wallets (owners.json) get the owner's
    // filesystem surface too, not just the single configured address.
    !addr.is_empty() && auth::is_owner(addr)
}

/// Confine `raw` within `base`. Accepts `~/…`, absolute, or relative paths; the
/// resulting path must stay inside `base`. We reject any `..` component up front,
/// then canonicalize the longest existing ancestor (resolving symlinks) and
/// re-append the not-yet-existing tail — so a new file under `base` is allowed
/// while a symlink or `..` that escapes `base` is refused.
fn confine(base: &Path, raw: &str) -> Result<PathBuf, String> {
    let home = home();
    let expanded: PathBuf = if raw == "~" {
        home
    } else if let Some(rest) = raw.strip_prefix("~/") {
        home.join(rest)
    } else {
        let p = Path::new(raw);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            base.join(raw)
        }
    };

    // No `..` escape hatch.
    if expanded.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
        return Err("path may not contain '..'".to_string());
    }

    // Canonicalize the longest existing prefix, then re-append the missing tail.
    let mut existing = expanded.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while !existing.exists() {
        match existing.file_name() {
            Some(n) => tail.push(n.to_os_string()),
            None => break,
        }
        if !existing.pop() {
            break;
        }
    }
    let mut real = std::fs::canonicalize(&existing).unwrap_or(existing);
    for c in tail.iter().rev() {
        real.push(c);
    }

    let base_real = std::fs::canonicalize(base).unwrap_or_else(|_| base.to_path_buf());
    if real == base_real || real.starts_with(&base_real) {
        Ok(real)
    } else {
        Err(format!("path escapes your allowed root ({})", base_real.display()))
    }
}

/// The module dirs a live QR invite opens for `addr`, most-specific first.
/// Empty when the caller holds no invite; the whole module tree for an
/// "everything" invite. These are extra roots on top of the peer root — a
/// guest editor keeps their own workspace either way.
pub fn invited_roots(caller_addr: &str) -> Vec<PathBuf> {
    match auth::grant_edit_scope(caller_addr) {
        Some(auth::EditScope::All) => vec![mod_root()],
        Some(auth::EditScope::Modules(list)) => list
            .iter()
            .filter_map(|m| crate::snapshots::module_root_for(m))
            .collect(),
        None => Vec::new(),
    }
}

/// True when `path` lies inside a module dir the caller's live invite named.
/// Job spawn asks this to decide whether a non-owner run may work on the real
/// module tree or must be confined to the sandbox uid.
pub fn is_invited_path(caller_addr: &str, path: &str) -> bool {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        return false;
    }
    invited_roots(caller_addr)
        .iter()
        .any(|root| confine(root, path).is_ok())
}

/// Resolve a user-facing path under the caller's role-confined root.
///   - owner  → `~/mod/mod` (orbit + core module tree)
///   - guest  → the modules their live invite named, else their peer root
///   - peer   → `~/.mod/peers/<addr>`
///   - local  → host (empty caller, trusted CLI)
pub fn resolve_path(caller_addr: &str, raw_path: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() {
        // Local/trusted CLI: full host, keep `~` expansion.
        let home = home();
        return Ok(PathBuf::from(raw_path.replacen('~', &home.to_string_lossy(), 1)));
    }
    if is_owner(caller_addr) {
        return confine(&mod_root(), raw_path);
    }
    // Peer: confine to their private root (created on demand).
    let base = peer_root(caller_addr);
    let _ = std::fs::create_dir_all(&base);
    let peer_err = match confine(&base, raw_path) {
        Ok(p) => return Ok(p),
        Err(e) => e,
    };
    // Outside their workspace — but a live invite may still open this path,
    // and only inside the module dirs it named. A relative path was already
    // joined to the peer root above, so only `~/…` and absolute paths can
    // reach here: an invite never reinterprets a relative path.
    for root in invited_roots(caller_addr) {
        if let Ok(p) = confine(&root, raw_path) {
            return Ok(p);
        }
    }
    Err(peer_err)
}

/// Ensure the calling user's root exists; returns it. Owner → module tree,
/// peer → their private root, local → host home.
pub fn ensure_workspace(caller_addr: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() {
        return Ok(home());
    }
    if is_owner(caller_addr) {
        return Ok(mod_root());
    }
    let base = peer_root(caller_addr);
    std::fs::create_dir_all(&base).map_err(|e| format!("mkdir {}: {e}", base.display()))?;
    Ok(base)
}

/// Jobs DB dir. Owner/local share the legacy `~/.mod/build-fork`; each peer gets a
/// per-peer DB under their private root so job views stay isolated.
pub fn jobs_db_path(caller_addr: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        return Ok(home().join(".mod").join("build-fork"));
    }
    let root = peer_root(caller_addr).join(".jobs");
    std::fs::create_dir_all(&root).map_err(|e| format!("mkdir {}: {e}", root.display()))?;
    Ok(root)
}

/// Display a real path back to the user with their root hidden as `~/`.
/// Owner/local strip `$HOME`; peers strip their private root.
pub fn display_path(caller_addr: &str, p: &Path) -> String {
    let base = if caller_addr.is_empty() || is_owner(caller_addr) {
        home()
    } else {
        peer_root(caller_addr)
    };
    let base_real = std::fs::canonicalize(&base).unwrap_or(base);
    let s = p.to_string_lossy().to_string();
    let base_s = base_real.to_string_lossy().to_string();
    if let Some(stripped) = s.strip_prefix(&base_s) {
        let cleaned = stripped.trim_start_matches('/');
        return if cleaned.is_empty() { "~".to_string() } else { format!("~/{cleaned}") };
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    // A real, canonicalized temp dir to act as a confinement root.
    fn tmp_base(tag: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("modtest_{}_{}", std::process::id(), tag));
        std::fs::create_dir_all(&p).unwrap();
        std::fs::canonicalize(&p).unwrap()
    }

    #[test]
    fn confine_allows_relative_inside() {
        let base = tmp_base("rel");
        let r = confine(&base, "sub/file.txt").unwrap();
        assert!(r.starts_with(&base), "{r:?} not under {base:?}");
        assert!(r.ends_with("sub/file.txt"));
    }

    #[test]
    fn confine_dot_resolves_to_base() {
        let base = tmp_base("dot");
        assert_eq!(confine(&base, ".").unwrap(), base);
    }

    #[test]
    fn confine_rejects_parentdir_escape() {
        let base = tmp_base("esc");
        // Any `..` component is refused up front — the core boundary guarantee.
        assert!(confine(&base, "../outside").is_err());
        assert!(confine(&base, "a/../../b").is_err());
        assert!(confine(&base, "..").is_err());
    }

    #[test]
    fn confine_rejects_absolute_outside() {
        let base = tmp_base("abs");
        assert!(confine(&base, "/etc/passwd").is_err());
        assert!(confine(&base, "/tmp/definitely-not-under-base").is_err());
    }

    #[test]
    fn confine_allows_absolute_inside() {
        let base = tmp_base("absin");
        let inside = base.join("x").join("y");
        let r = confine(&base, inside.to_str().unwrap()).unwrap();
        assert!(r.starts_with(&base));
    }

    #[test]
    fn confine_blocks_symlink_escape() {
        // A symlink inside base pointing outside must not let writes escape.
        let base = tmp_base("sym");
        let outside = tmp_base("sym_target");
        let link = base.join("escape");
        let _ = std::fs::remove_file(&link);
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        // Resolving a path *through* the symlink escapes base → rejected.
        let r = confine(&base, "escape/pwned.txt");
        assert!(r.is_err(), "symlink escape was allowed: {r:?}");
    }

    #[test]
    fn peer_root_is_lowercased_under_mod_peers() {
        let r = peer_root("0xABCdef00");
        assert!(r.to_string_lossy().ends_with(".mod/peers/0xabcdef00"), "{r:?}");
    }

    #[test]
    fn mod_root_is_module_tree() {
        assert!(mod_root().ends_with("mod/mod"), "{:?}", mod_root());
    }

    #[test]
    fn empty_caller_is_unconfined_host() {
        // Local/trusted CLI (empty caller) keeps full-host semantics.
        let r = resolve_path("", "/etc/hosts").unwrap();
        assert_eq!(r, PathBuf::from("/etc/hosts"));
    }

    // ── Invite tier ─────────────────────────────────────────────────
    // A guest holding a live QR invite reaches the modules it named — and
    // nothing else. These drive $HOME to a temp tree, so they take the shared
    // home lock like the sudo tests do.

    const GUEST: &str = "0x00000000000000000000000000000000000000aa";

    /// A temp $HOME holding two modules and a live invite scoped to `scope`
    /// (None = an "everything" invite) redeemed by GUEST.
    fn setup_invite(scope: Option<Vec<String>>) -> crate::sudo::tempdir_guard::TempHome {
        let home = crate::sudo::tempdir_guard::TempHome::new();
        let orbit = home.path().join("mod").join("mod").join("orbit");
        for m in ["alpha", "beta"] {
            let dir = orbit.join(m).join("src");
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(orbit.join(m).join("config.json"), format!("{{\"name\":\"{m}\"}}")).unwrap();
        }
        let exp = chrono::Utc::now().timestamp() + 3600;
        let file = auth::GrantsFile {
            grants: vec![auth::Grant {
                id: "g1".into(),
                exp,
                ttl: 3600,
                key_hash: None,
                label: None,
                modules: scope.clone(),
                created: exp - 3600,
                revoked: false,
            }],
            redemptions: vec![auth::Redemption {
                address: GUEST.to_string(),
                exp,
                grant: "g1".into(),
                redeemed: exp - 3600,
                modules: scope,
            }],
        };
        auth::write_grants(&file).unwrap();
        home
    }

    #[test]
    fn scoped_invite_opens_only_its_modules() {
        let _lock = crate::home_test_lock();
        let home = setup_invite(Some(vec!["alpha".into()]));
        let orbit = home.path().join("mod").join("mod").join("orbit");
        let inside = orbit.join("alpha").join("src").join("mod.py");
        let outside = orbit.join("beta").join("src").join("mod.py");

        let r = resolve_path(GUEST, &inside.to_string_lossy()).expect("invited module refused");
        assert_eq!(r, inside);
        assert!(
            resolve_path(GUEST, &outside.to_string_lossy()).is_err(),
            "invite reached a module it never named"
        );
        assert!(is_invited_path(GUEST, &inside.to_string_lossy()));
        assert!(!is_invited_path(GUEST, &outside.to_string_lossy()));
    }

    #[test]
    fn unscoped_invite_opens_the_module_tree() {
        let _lock = crate::home_test_lock();
        let home = setup_invite(None);
        let beta = home.path().join("mod").join("mod").join("orbit").join("beta");
        assert!(resolve_path(GUEST, &beta.to_string_lossy()).is_ok());
        // Still the module tree only — never the rest of the host.
        assert!(resolve_path(GUEST, "/etc/hosts").is_err());
    }

    #[test]
    fn invite_leaves_the_peer_workspace_intact() {
        let _lock = crate::home_test_lock();
        let _home = setup_invite(Some(vec!["alpha".into()]));
        // A relative path is still the guest's own workspace — an invite adds
        // roots, it never reinterprets where "notes.txt" lives.
        let r = resolve_path(GUEST, "notes.txt").unwrap();
        assert!(r.starts_with(peer_root(GUEST)), "{r:?}");
    }

    #[test]
    fn no_invite_stays_confined_to_peers() {
        let _lock = crate::home_test_lock();
        let home = crate::sudo::tempdir_guard::TempHome::new();
        let orbit = home.path().join("mod").join("mod").join("orbit").join("alpha");
        std::fs::create_dir_all(&orbit).unwrap();
        assert!(resolve_path(GUEST, &orbit.to_string_lossy()).is_err());
        assert!(!is_invited_path(GUEST, &orbit.to_string_lossy()));
    }
}
