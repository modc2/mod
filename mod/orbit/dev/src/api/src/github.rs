//! GitHub repo links — publish a module's live tree to a real GitHub repo.
//!
//! The CID-native merge requests (merge.rs) cover collaboration INSIDE the
//! orbit; this module covers the bridge OUT: the owner connects a module to
//! a GitHub repo, declares which branches the console may write, and from
//! then on the console (or an agent job driving the mod.py fns) can push
//! the module's tree to an allowed branch or merge one remote branch into
//! another — e.g. merge a dev branch into main once review is done.
//!
//! Everything is owner-gated and policy lives off-tree:
//!   ~/.mod/dev/github/<module>.json   — repo, token, branch allowlist
//!   ~/.mod/dev/github/work/<module>/  — working clone used for push/merge
//!
//! All git traffic goes through the system `git` binary with the token
//! embedded in the remote URL. git echoes that URL into stderr on failure,
//! so every error string passes through `scrub()` before leaving this file.

use crate::snapshots::{module_root_for, should_skip_dir};
use axum::{extract::Path as AxPath, http::StatusCode, response::IntoResponse, Json};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};

// ── Link records (one JSON file per module) ──────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GithubLink {
    pub module: String,
    /// "owner/name" slug on github.com (or a full https URL for other hosts).
    pub repo: String,
    /// PAT with repo write access. Optional: public repos can be read
    /// (status/branches) tokenless, but push/merge will fail without one.
    #[serde(default)]
    pub token: String,
    /// Branches the console is allowed to WRITE (push to, or merge into).
    /// Reads are unrestricted. No wildcard on purpose — name each branch.
    pub push_branches: Vec<String>,
    /// Whether branch-into-branch merges are allowed at all.
    #[serde(default)]
    pub allow_merge: bool,
    /// Remote HEAD at connect time — new branches are cut from this.
    #[serde(default)]
    pub default_branch: String,
    pub connected_by: String,
    pub connected_at: u64,
}

pub fn github_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("build").join("github")
}

fn link_path(module: &str) -> PathBuf {
    github_dir().join(format!("{module}.json"))
}

fn work_dir(module: &str) -> PathBuf {
    github_dir().join("work").join(module)
}

pub fn load_link(module: &str) -> Option<GithubLink> {
    std::fs::read_to_string(link_path(module))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

fn save_link(link: &GithubLink) -> Result<(), String> {
    let dir = github_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir github: {e}"))?;
    let json = serde_json::to_vec_pretty(link).map_err(|e| format!("link serialize: {e}"))?;
    std::fs::write(link_path(&link.module), json).map_err(|e| format!("link write: {e}"))
}

// ── Validation ───────────────────────────────────────────────────────

/// "owner/name" — the strict GitHub slug shape. Anything URL-like is
/// rejected here and must come in as a full https URL instead.
pub fn is_repo_slug(s: &str) -> bool {
    let parts: Vec<&str> = s.split('/').collect();
    parts.len() == 2
        && parts.iter().all(|p| {
            !p.is_empty()
                && p.len() <= 100
                && !p.starts_with('-')
                && !p.starts_with('.')
                && p.chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
        })
}

/// Conservative subset of git's ref-name rules: enough for real branch
/// names (incl. "feature/x"), tight enough to be shell/URL-safe.
pub fn is_valid_branch(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 120
        && !s.starts_with('-')
        && !s.starts_with('/')
        && !s.ends_with('/')
        && !s.contains("..")
        && !s.contains("//")
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '/'))
}

/// Tokens ride inside the remote URL, so refuse anything that could
/// change the URL's meaning. GitHub PATs are [A-Za-z0-9_].
fn is_valid_token(s: &str) -> bool {
    s.len() <= 255
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Replace the token with *** anywhere it appears (git echoes remote URLs
/// into stderr on failure). Applied to every string that leaves this module.
fn scrub(token: &str, text: &str) -> String {
    if token.is_empty() {
        text.to_string()
    } else {
        text.replace(token, "***")
    }
}

/// The https URL git talks to, with the token embedded when present.
fn remote_url(link: &GithubLink) -> String {
    let base = if link.repo.starts_with("https://") {
        link.repo.clone()
    } else {
        format!("https://github.com/{}.git", link.repo)
    };
    if link.token.is_empty() {
        base
    } else {
        // x-access-token works for PATs (classic and fine-grained) alike.
        base.replacen("https://", &format!("https://x-access-token:{}@", link.token), 1)
    }
}

// ── git plumbing ─────────────────────────────────────────────────────

/// Run git with args, no shell. Returns (stdout, stderr) on success,
/// scrubbed stderr on failure. `cwd=None` for clone/ls-remote.
async fn git(
    link: &GithubLink,
    cwd: Option<&Path>,
    args: &[&str],
    timeout_secs: u64,
) -> Result<(String, String), String> {
    let mut cmd = tokio::process::Command::new("git");
    cmd.env("GIT_TERMINAL_PROMPT", "0");
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    cmd.args(args);
    let out = tokio::time::timeout(std::time::Duration::from_secs(timeout_secs), cmd.output())
        .await
        .map_err(|_| format!("git {} timed out after {timeout_secs}s", args.first().unwrap_or(&"")))?
        .map_err(|e| format!("git not available: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    if out.status.success() {
        Ok((stdout, scrub(&link.token, &stderr)))
    } else {
        Err(scrub(
            &link.token,
            &format!("git {} failed: {}", args.first().unwrap_or(&""), stderr.trim()),
        ))
    }
}

/// Remote branch names + which one HEAD points at, via one ls-remote.
async fn remote_branches(link: &GithubLink) -> Result<(Vec<String>, Option<String>), String> {
    let url = remote_url(link);
    let (out, _) = git(link, None, &["ls-remote", "--symref", &url, "HEAD", "refs/heads/*"], 30).await?;
    let mut branches = Vec::new();
    let mut head = None;
    for line in out.lines() {
        // "ref: refs/heads/main\tHEAD" (symref line, first)
        if let Some(rest) = line.strip_prefix("ref: refs/heads/") {
            head = rest.split_whitespace().next().map(|s| s.to_string());
        } else if let Some(idx) = line.find("refs/heads/") {
            branches.push(line[idx + "refs/heads/".len()..].to_string());
        }
    }
    branches.sort();
    branches.dedup();
    Ok((branches, head))
}

/// Ensure the working clone exists and its origin matches the link (token
/// rotation and repo swaps both land here), then fetch all branches.
async fn ensure_clone(link: &GithubLink) -> Result<PathBuf, String> {
    let dir = work_dir(&link.module);
    let url = remote_url(link);
    if dir.join(".git").is_dir() {
        git(link, Some(&dir), &["remote", "set-url", "origin", &url], 10).await?;
    } else {
        let _ = std::fs::remove_dir_all(&dir);
        if let Some(parent) = dir.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir work: {e}"))?;
        }
        let dir_s = dir.to_string_lossy().to_string();
        git(link, None, &["clone", &url, &dir_s], 300).await?;
    }
    git(link, Some(&dir), &["fetch", "origin", "--prune"], 120).await?;
    Ok(dir)
}

/// Check out `branch` in the clone: from origin if it exists there,
/// otherwise cut fresh from origin/<default_branch>.
async fn checkout(link: &GithubLink, dir: &Path, branch: &str) -> Result<bool, String> {
    let remote_ref = format!("origin/{branch}");
    let exists = git(link, Some(dir), &["rev-parse", "--verify", "--quiet", &remote_ref], 10)
        .await
        .is_ok();
    if exists {
        git(link, Some(dir), &["checkout", "-B", branch, &remote_ref], 30).await?;
    } else {
        let base = format!("origin/{}", link.default_branch);
        git(link, Some(dir), &["checkout", "-B", branch, &base], 30)
            .await
            .map_err(|e| format!("branch '{branch}' not on remote and could not cut from {base}: {e}"))?;
    }
    Ok(exists)
}

/// Mirror the module's live tree into the clone: drop everything tracked
/// except .git, then copy using the same skip rules as snapshots (so a
/// push publishes exactly what a snapshot captures — no node_modules,
/// target/, .next, dotfiles…).
fn sync_tree(module_root: &Path, clone: &Path) -> Result<usize, String> {
    for entry in std::fs::read_dir(clone).map_err(|e| format!("read work dir: {e}"))?.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name == ".git" {
            continue;
        }
        let p = entry.path();
        if p.is_dir() {
            std::fs::remove_dir_all(&p).map_err(|e| format!("clear {name}: {e}"))?;
        } else {
            std::fs::remove_file(&p).map_err(|e| format!("clear {name}: {e}"))?;
        }
    }
    let mut count = 0usize;
    copy_tree(module_root, module_root, clone, &mut count)?;
    Ok(count)
}

fn copy_tree(root: &Path, dir: &Path, dest_root: &Path, count: &mut usize) -> Result<(), String> {
    for entry in std::fs::read_dir(dir).map_err(|e| format!("read {}: {e}", dir.display()))?.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let path = entry.path();
        let Ok(ft) = entry.file_type() else { continue };
        if ft.is_dir() {
            if should_skip_dir(&name) {
                continue;
            }
            copy_tree(root, &path, dest_root, count)?;
        } else if ft.is_file() {
            if name.starts_with('.') {
                continue;
            }
            let rel = path.strip_prefix(root).map_err(|e| format!("strip_prefix: {e}"))?;
            let dst = dest_root.join(rel);
            if let Some(parent) = dst.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
            }
            std::fs::copy(&path, &dst).map_err(|e| format!("copy {}: {e}", rel.display()))?;
            *count += 1;
        }
    }
    Ok(())
}

fn now_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn err(status: StatusCode, msg: impl Into<String>) -> axum::response::Response {
    (status, Json(json!({ "error": msg.into() }))).into_response()
}

/// Public shape of a link — everything except the token itself.
fn link_json(link: &GithubLink) -> serde_json::Value {
    json!({
        "module": link.module,
        "repo": link.repo,
        "token_set": !link.token.is_empty(),
        "push_branches": link.push_branches,
        "allow_merge": link.allow_merge,
        "default_branch": link.default_branch,
        "connected_by": link.connected_by,
        "connected_at": link.connected_at,
    })
}

// ── Handlers (all owner-gated; module names come pre-validated by
//    module_root_for, which only resolves real module dirs) ───────────

/// GET /modules/:name/github — link + live remote branches.
pub async fn status(
    headers: axum::http::HeaderMap,
    AxPath(name): AxPath<String>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    let Some(link) = load_link(&name) else {
        return Json(json!({ "connected": false, "module": name })).into_response();
    };
    let (branches, head) = match remote_branches(&link).await {
        Ok(t) => t,
        Err(e) => {
            return Json(json!({
                "connected": true,
                "link": link_json(&link),
                "remote_error": e,
            }))
            .into_response();
        }
    };
    Json(json!({
        "connected": true,
        "link": link_json(&link),
        "branches": branches,
        "remote_head": head,
    }))
    .into_response()
}

#[derive(Deserialize)]
pub struct ConnectBody {
    pub repo: String,
    #[serde(default)]
    pub token: Option<String>,
    /// Branch allowlist the console may write. Defaults to none — the
    /// owner opts branches in explicitly.
    #[serde(default)]
    pub push_branches: Vec<String>,
    #[serde(default)]
    pub allow_merge: bool,
}

/// POST /modules/:name/github/connect — create or update the link.
/// Omitting `token` on an update keeps the stored one (so policy edits
/// don't require re-pasting the secret).
pub async fn connect(
    headers: axum::http::HeaderMap,
    AxPath(name): AxPath<String>,
    Json(body): Json<ConnectBody>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    if module_root_for(&name).is_none() {
        return err(StatusCode::NOT_FOUND, format!("module '{name}' not found"));
    }
    let repo = body.repo.trim().to_string();
    if !is_repo_slug(&repo) && !(repo.starts_with("https://") && repo.len() <= 300 && !repo.contains(|c: char| c.is_whitespace() || c == '@')) {
        return err(StatusCode::BAD_REQUEST, "repo must be an 'owner/name' GitHub slug or a plain https git URL");
    }
    let prev = load_link(&name);
    let token = match body.token {
        Some(t) => {
            let t = t.trim().to_string();
            if !t.is_empty() && !is_valid_token(&t) {
                return err(StatusCode::BAD_REQUEST, "token has characters that can't ride in a remote URL");
            }
            t
        }
        None => prev.as_ref().map(|l| l.token.clone()).unwrap_or_default(),
    };
    let mut push_branches: Vec<String> = Vec::new();
    for b in &body.push_branches {
        let b = b.trim();
        if b.is_empty() {
            continue;
        }
        if !is_valid_branch(b) {
            return err(StatusCode::BAD_REQUEST, format!("invalid branch name: {b}"));
        }
        if !push_branches.iter().any(|x| x == b) {
            push_branches.push(b.to_string());
        }
    }
    let caller = crate::auth::extract_address_from_headers(&headers).unwrap_or_else(|_| "local".into());
    let mut link = GithubLink {
        module: name.clone(),
        repo: repo.clone(),
        token,
        push_branches,
        allow_merge: body.allow_merge,
        default_branch: String::new(),
        connected_by: caller,
        connected_at: now_ts(),
    };
    // Prove we can actually see the repo before saving anything, and learn
    // the remote HEAD so new branches have something to cut from.
    let (branches, head) = match remote_branches(&link).await {
        Ok(t) => t,
        Err(e) => return err(StatusCode::BAD_GATEWAY, format!("could not reach repo: {e}")),
    };
    link.default_branch = head.clone().unwrap_or_else(|| "main".to_string());
    if let Err(e) = save_link(&link) {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }
    // A repo swap invalidates the old working clone.
    if prev.map(|p| p.repo != link.repo).unwrap_or(false) {
        let _ = std::fs::remove_dir_all(work_dir(&name));
    }
    Json(json!({
        "connected": true,
        "link": link_json(&link),
        "branches": branches,
        "remote_head": head,
    }))
    .into_response()
}

/// DELETE /modules/:name/github — forget the link and the working clone.
pub async fn disconnect(
    headers: axum::http::HeaderMap,
    AxPath(name): AxPath<String>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    if load_link(&name).is_none() {
        return err(StatusCode::NOT_FOUND, format!("module '{name}' has no GitHub link"));
    }
    let _ = std::fs::remove_file(link_path(&name));
    let _ = std::fs::remove_dir_all(work_dir(&name));
    Json(json!({ "connected": false, "module": name })).into_response()
}

#[derive(Deserialize)]
pub struct PushBody {
    pub branch: String,
    #[serde(default)]
    pub message: Option<String>,
}

/// POST /modules/:name/github/push — publish the module's live tree as one
/// commit on an allowed branch (created from the default branch if new).
pub async fn push(
    headers: axum::http::HeaderMap,
    AxPath(name): AxPath<String>,
    Json(body): Json<PushBody>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    let Some(link) = load_link(&name) else {
        return err(StatusCode::NOT_FOUND, format!("module '{name}' has no GitHub link — connect one first"));
    };
    let Some(root) = module_root_for(&name) else {
        return err(StatusCode::NOT_FOUND, format!("module '{name}' not found"));
    };
    let branch = body.branch.trim().to_string();
    if !is_valid_branch(&branch) {
        return err(StatusCode::BAD_REQUEST, "invalid branch name");
    }
    if !link.push_branches.iter().any(|b| b == &branch) {
        return err(
            StatusCode::FORBIDDEN,
            format!("branch '{branch}' is not in the push allowlist ({})", link.push_branches.join(", ")),
        );
    }
    let dir = match ensure_clone(&link).await {
        Ok(d) => d,
        Err(e) => return err(StatusCode::BAD_GATEWAY, e),
    };
    if let Err(e) = checkout(&link, &dir, &branch).await {
        return err(StatusCode::BAD_GATEWAY, e);
    }
    let files = match sync_tree(&root, &dir) {
        Ok(n) => n,
        Err(e) => return err(StatusCode::INTERNAL_SERVER_ERROR, e),
    };
    match git(&link, Some(&dir), &["status", "--porcelain"], 30).await {
        Ok((out, _)) if out.trim().is_empty() => {
            return Json(json!({
                "pushed": false,
                "branch": branch,
                "files": files,
                "note": "remote branch already matches the live tree — nothing to push",
            }))
            .into_response();
        }
        Ok(_) => {}
        Err(e) => return err(StatusCode::INTERNAL_SERVER_ERROR, e),
    }
    let caller = crate::auth::extract_address_from_headers(&headers).unwrap_or_else(|_| "local".into());
    let message = body
        .message
        .as_deref()
        .map(str::trim)
        .filter(|m| !m.is_empty())
        .map(|m| m.to_string())
        .unwrap_or_else(|| format!("build console: publish {name}"));
    let full_message = format!("{message}\n\nPushed via the build console by {caller}");
    let commit_args = [
        "-c", "user.name=build console",
        "-c", "user.email=build@orbit",
        "commit", "-m", &full_message,
    ];
    if let Err(e) = git(&link, Some(&dir), &["add", "-A"], 60).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }
    if let Err(e) = git(&link, Some(&dir), &commit_args, 60).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }
    if let Err(e) = git(&link, Some(&dir), &["push", "origin", &branch], 300).await {
        return err(StatusCode::BAD_GATEWAY, e);
    }
    let sha = git(&link, Some(&dir), &["rev-parse", "HEAD"], 10)
        .await
        .map(|(out, _)| out.trim().to_string())
        .unwrap_or_default();
    Json(json!({
        "pushed": true,
        "branch": branch,
        "commit": sha,
        "files": files,
        "repo": link.repo,
    }))
    .into_response()
}

#[derive(Deserialize)]
pub struct MergeBody {
    /// Branch that RECEIVES the merge — must be in the push allowlist.
    pub base: String,
    /// Branch being merged in — any remote branch (a dev's branch).
    pub head: String,
}

/// POST /modules/:name/github/merge — merge origin/head into base and push.
/// A real `git merge` in the working clone, so the failure mode is honest:
/// conflicts abort cleanly and come back as a file list, nothing is pushed.
pub async fn merge(
    headers: axum::http::HeaderMap,
    AxPath(name): AxPath<String>,
    Json(body): Json<MergeBody>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    let Some(link) = load_link(&name) else {
        return err(StatusCode::NOT_FOUND, format!("module '{name}' has no GitHub link — connect one first"));
    };
    if !link.allow_merge {
        return err(StatusCode::FORBIDDEN, "branch merges are disabled for this link — reconnect with allow_merge");
    }
    let (base, head) = (body.base.trim().to_string(), body.head.trim().to_string());
    if !is_valid_branch(&base) || !is_valid_branch(&head) {
        return err(StatusCode::BAD_REQUEST, "invalid branch name");
    }
    if base == head {
        return err(StatusCode::BAD_REQUEST, "base and head are the same branch");
    }
    if !link.push_branches.iter().any(|b| b == &base) {
        return err(
            StatusCode::FORBIDDEN,
            format!("base '{base}' is not in the push allowlist ({})", link.push_branches.join(", ")),
        );
    }
    let dir = match ensure_clone(&link).await {
        Ok(d) => d,
        Err(e) => return err(StatusCode::BAD_GATEWAY, e),
    };
    // Both sides must already exist on the remote — a merge never invents refs.
    for b in [&base, &head] {
        let r = format!("origin/{b}");
        if git(&link, Some(&dir), &["rev-parse", "--verify", "--quiet", &r], 10).await.is_err() {
            return err(StatusCode::NOT_FOUND, format!("branch '{b}' does not exist on the remote"));
        }
    }
    if let Err(e) = git(&link, Some(&dir), &["checkout", "-B", &base, &format!("origin/{base}")], 30).await {
        return err(StatusCode::BAD_GATEWAY, e);
    }
    let caller = crate::auth::extract_address_from_headers(&headers).unwrap_or_else(|_| "local".into());
    let msg = format!("Merge {head} into {base}\n\nMerged via the build console by {caller}");
    let origin_head = format!("origin/{head}");
    let merge_args = [
        "-c", "user.name=build console",
        "-c", "user.email=build@orbit",
        "merge", "--no-ff", "--no-edit", "-m", &msg,
        &origin_head,
    ];
    if let Err(merge_err) = git(&link, Some(&dir), &merge_args, 60).await {
        // Capture the conflict set before aborting — it's the useful part.
        let conflicts = git(&link, Some(&dir), &["diff", "--name-only", "--diff-filter=U"], 10)
            .await
            .map(|(out, _)| out.lines().map(|l| l.to_string()).collect::<Vec<_>>())
            .unwrap_or_default();
        let _ = git(&link, Some(&dir), &["merge", "--abort"], 30).await;
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "merged": false,
                "error": merge_err,
                "conflicts": conflicts,
                "hint": "resolve on GitHub or push a reconciled tree to the head branch, then retry",
            })),
        )
            .into_response();
    }
    if let Err(e) = git(&link, Some(&dir), &["push", "origin", &base], 300).await {
        return err(StatusCode::BAD_GATEWAY, e);
    }
    let sha = git(&link, Some(&dir), &["rev-parse", "HEAD"], 10)
        .await
        .map(|(out, _)| out.trim().to_string())
        .unwrap_or_default();
    Json(json!({
        "merged": true,
        "base": base,
        "head": head,
        "commit": sha,
        "repo": link.repo,
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_slugs_validate() {
        assert!(is_repo_slug("octo-org/my.repo_1"));
        assert!(!is_repo_slug("octo-org"));
        assert!(!is_repo_slug("a/b/c"));
        assert!(!is_repo_slug("-evil/repo"));
        assert!(!is_repo_slug("owner/"));
        assert!(!is_repo_slug("owner/re po"));
        assert!(!is_repo_slug("https://github.com/o/r"));
    }

    #[test]
    fn branch_names_validate() {
        assert!(is_valid_branch("main"));
        assert!(is_valid_branch("feature/login-2"));
        assert!(is_valid_branch("release-1.2.x"));
        assert!(!is_valid_branch(""));
        assert!(!is_valid_branch("-rf"));
        assert!(!is_valid_branch("a..b"));
        assert!(!is_valid_branch("a b"));
        assert!(!is_valid_branch("refs//heads"));
        assert!(!is_valid_branch("branch/"));
    }

    #[test]
    fn tokens_validate() {
        assert!(is_valid_token("ghp_abc123DEF"));
        assert!(is_valid_token("github_pat_11ABC-xyz"));
        assert!(!is_valid_token("bad token"));
        assert!(!is_valid_token("evil@host/"));
    }

    #[test]
    fn scrub_hides_tokens() {
        let e = scrub("ghp_secret", "fatal: 'https://x-access-token:ghp_secret@github.com/a/b' not found");
        assert!(!e.contains("ghp_secret"));
        assert!(e.contains("***"));
        // Empty token must not blow up on replace("").
        assert_eq!(scrub("", "hello"), "hello");
    }

    #[test]
    fn remote_url_shapes() {
        let mut link = GithubLink {
            module: "m".into(),
            repo: "octo/repo".into(),
            token: String::new(),
            push_branches: vec![],
            allow_merge: false,
            default_branch: "main".into(),
            connected_by: "t".into(),
            connected_at: 0,
        };
        assert_eq!(remote_url(&link), "https://github.com/octo/repo.git");
        link.token = "tok".into();
        assert_eq!(remote_url(&link), "https://x-access-token:tok@github.com/octo/repo.git");
        link.repo = "https://gitlab.com/octo/repo.git".into();
        assert_eq!(remote_url(&link), "https://x-access-token:tok@gitlab.com/octo/repo.git");
    }

    #[test]
    fn link_json_never_carries_the_token() {
        let link = GithubLink {
            module: "m".into(),
            repo: "octo/repo".into(),
            token: "ghp_secret".into(),
            push_branches: vec!["main".into()],
            allow_merge: true,
            default_branch: "main".into(),
            connected_by: "0xabc".into(),
            connected_at: 1,
        };
        let s = serde_json::to_string(&link_json(&link)).unwrap();
        assert!(!s.contains("ghp_secret"));
        assert!(s.contains("\"token_set\":true"));
    }
}
