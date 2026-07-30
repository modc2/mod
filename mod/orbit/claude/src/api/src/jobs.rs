//! Claude Job Manager — spawn and manage background Claude CLI processes

use chrono::Utc;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::{broadcast, mpsc, RwLock};
use std::collections::HashSet;
use base64::Engine;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl std::fmt::Display for JobStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JobStatus::Pending => write!(f, "pending"),
            JobStatus::Running => write!(f, "running"),
            JobStatus::Completed => write!(f, "completed"),
            JobStatus::Failed => write!(f, "failed"),
            JobStatus::Cancelled => write!(f, "cancelled"),
        }
    }
}

impl JobStatus {
    pub fn from_str(s: &str) -> Self {
        match s {
            "running" => Self::Running,
            "completed" => Self::Completed,
            "failed" => Self::Failed,
            "cancelled" => Self::Cancelled,
            _ => Self::Pending,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClaudeJob {
    pub id: String,
    pub prompt: String,
    pub model: String,
    pub work_dir: String,
    pub status: JobStatus,
    pub output: String,
    pub error: Option<String>,
    pub pid: Option<u32>,
    pub created_at: i64,
    pub updated_at: i64,
    #[serde(default)]
    pub user_address: String,
    /// localfs CID of the finished task bundle (prompt + output + metadata),
    /// registered in the store module's index — None until the task reaches
    /// a terminal state and the store bridge has run.
    #[serde(default)]
    pub cid: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImageAttachment {
    pub name: String,
    pub data: String, // base64-encoded image data (without data URL prefix)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmitRequest {
    pub prompt: String,
    #[serde(default = "default_model")]
    pub model: String,
    pub work_dir: Option<String>,
    #[serde(default)]
    pub module_name: Option<String>,
    #[serde(default)]
    pub creation_mode: Option<String>, // "new", "fork"
    #[serde(default)]
    pub fork_source: Option<String>,
    #[serde(default)]
    pub anchor_dir: Option<String>,
    #[serde(default)]
    pub images: Option<Vec<ImageAttachment>>,
    #[serde(default)]
    pub agent_type: Option<String>,
    #[serde(default)]
    pub system_prompt: Option<String>,
    /// Set by the API layer from the auth token — not from client input
    #[serde(default)]
    pub user_address: Option<String>,
}

fn default_model() -> String {
    "claude-fable-5".to_string()
}

/// Remap models the CLI can't actually run to a safe, available one.
///
/// Claude Fable 5 is now generally available, so `fable` resolves to the real
/// `claude-fable-5` model instead of falling back to Opus. Empty stays the
/// default. This is the single chokepoint every job passes through.
fn normalize_model(model: &str) -> String {
    let m = model.trim();
    let base = m.strip_suffix("[1m]").unwrap_or(m);
    match base {
        "fable" => "claude-fable-5".to_string(),
        "" => default_model(),
        _ => model.to_string(),
    }
}

/// Resolve the model for a job, expanding the special `auto` value.
///
/// `auto` (or `claude-auto`) means "let a fast model read the task and pick the
/// right tier for me": a quick Haiku classification pass buckets the prompt into
/// simple / medium / hard, which maps to Haiku 4.5 / Sonnet 5 / Opus 5. Any
/// other value passes straight through `normalize_model`. A `[1m]` (1M-context)
/// suffix on the request is preserved onto whatever tier `auto` lands on.
async fn resolve_model(requested: &str, prompt: &str, claude_bin: &str) -> String {
    let trimmed = requested.trim();
    let one_m = trimmed.ends_with("[1m]");
    let base = trimmed.strip_suffix("[1m]").unwrap_or(trimmed);
    if base != "auto" && base != "claude-auto" {
        return normalize_model(requested);
    }
    let tier = classify_complexity(prompt, claude_bin).await;
    let model = match tier.as_str() {
        "simple" => "claude-haiku-4-5",
        "hard" => "claude-opus-5",
        _ => "claude-sonnet-5", // "medium" and any fallback
    };
    if one_m {
        format!("{model}[1m]")
    } else {
        model.to_string()
    }
}

/// Ask a fast model to bucket a task's difficulty into simple|medium|hard for
/// the `auto` router. Only the head of the prompt is sent (a difficulty read
/// doesn't need the whole thing, and short == fast + cheap). Returns "medium"
/// on any timeout/error so `auto` degrades to the balanced tier, never a crash.
async fn classify_complexity(prompt: &str, claude_bin: &str) -> String {
    let head: String = prompt.chars().take(2000).collect();
    let classify_prompt = format!(
        "You are a task-complexity router for a coding agent. Read the TASK below and \
reply with EXACTLY ONE lowercase word, nothing else:\n\
- simple = trivial/mechanical (rename, typo, one-line edit, a quick factual question)\n\
- medium = normal feature work, a focused bug fix, a moderate refactor\n\
- hard = architecture, multi-file changes, tricky debugging, deep reasoning\n\n\
TASK:\n{head}"
    );
    let fut = Command::new(claude_bin)
        .arg("--print")
        .arg("--model")
        .arg("claude-haiku-4-5")
        .arg("--dangerously-skip-permissions")
        .arg(&classify_prompt)
        .stdin(std::process::Stdio::null())
        .output();
    let out = match tokio::time::timeout(std::time::Duration::from_secs(25), fut).await {
        Ok(Ok(o)) => o,
        _ => return "medium".to_string(),
    };
    let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
    // Check the strongest signals first; "simple" wins over "medium" if both
    // somehow appear, and "hard" is the deliberate upgrade signal.
    if text.contains("hard") {
        "hard".to_string()
    } else if text.contains("simple") {
        "simple".to_string()
    } else {
        "medium".to_string()
    }
}

// ── SQLite Job Store ─────────────────────────────────────────────────

struct JobStore {
    db_path: PathBuf,
}

impl JobStore {
    fn new(db_path: PathBuf) -> Self {
        let store = Self { db_path };
        store.init_db();
        store
    }

    fn conn(&self) -> Connection {
        Connection::open(&self.db_path).expect("SQLite open failed")
    }

    fn init_db(&self) {
        let conn = self.conn();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS claude_jobs (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT 'sonnet',
                work_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                output TEXT NOT NULL DEFAULT '',
                error TEXT,
                pid INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                user_address TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON claude_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON claude_jobs(created_at DESC);",
        )
        .expect("SQLite init failed");

        // Migration: add user_address column if missing (existing DBs)
        conn.execute(
            "ALTER TABLE claude_jobs ADD COLUMN user_address TEXT NOT NULL DEFAULT ''",
            [],
        )
        .ok(); // Ignore error if column already exists

        // Migration: localfs CID of the task bundle (existing DBs)
        conn.execute("ALTER TABLE claude_jobs ADD COLUMN cid TEXT", [])
            .ok();
    }

    fn insert(&self, job: &ClaudeJob) {
        let conn = self.conn();
        conn.execute(
            "INSERT INTO claude_jobs (id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address, cid)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                job.id, job.prompt, job.model, job.work_dir,
                job.status.to_string(), job.output, job.error, job.pid,
                job.created_at, job.updated_at, job.user_address, job.cid
            ],
        )
        .expect("SQLite insert failed");
    }

    fn update_status(&self, id: &str, status: &JobStatus, error: Option<&str>) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE claude_jobs SET status = ?1, error = ?2, updated_at = ?3 WHERE id = ?4",
            params![status.to_string(), error, now, id],
        )
        .ok();
    }

    fn update_pid(&self, id: &str, pid: u32) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE claude_jobs SET pid = ?1, status = 'running', updated_at = ?2 WHERE id = ?3",
            params![pid, now, id],
        )
        .ok();
    }

    fn update_cid(&self, id: &str, cid: &str) {
        let conn = self.conn();
        conn.execute(
            "UPDATE claude_jobs SET cid = ?1 WHERE id = ?2",
            params![cid, id],
        )
        .ok();
    }

    /// Terminal jobs that never got a task CID (pre-feature rows, or bridge
    /// failures) — oldest first so the backfill preserves creation order.
    fn terminal_missing_cid(&self, limit: usize) -> Vec<String> {
        let conn = self.conn();
        let mut stmt = match conn.prepare(
            "SELECT id FROM claude_jobs WHERE cid IS NULL AND status IN ('completed','failed','cancelled') ORDER BY created_at ASC LIMIT ?1",
        ) {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        stmt.query_map(params![limit as i64], |row| row.get::<_, String>(0))
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
    }

    fn append_output(&self, id: &str, text: &str) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE claude_jobs SET output = output || ?1, updated_at = ?2 WHERE id = ?3",
            params![text, now, id],
        )
        .ok();
    }

    fn get(&self, id: &str) -> Option<ClaudeJob> {
        let conn = self.conn();
        let mut stmt = conn
            .prepare("SELECT id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address, cid FROM claude_jobs WHERE id = ?1")
            .ok()?;

        stmt.query_row(params![id], |row| {
            Ok(ClaudeJob {
                id: row.get(0)?,
                prompt: row.get(1)?,
                model: row.get(2)?,
                work_dir: row.get(3)?,
                status: JobStatus::from_str(&row.get::<_, String>(4)?),
                output: row.get(5)?,
                error: row.get(6)?,
                pid: row.get(7)?,
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
                user_address: row.get::<_, String>(10).unwrap_or_default(),
                cid: row.get::<_, Option<String>>(11).unwrap_or(None),
            })
        })
        .ok()
    }

    /// Look a job up by its task-bundle CID — the replay deep link
    /// (`?replay=<cid>`) resolves through here before falling back to the
    /// localfs blob (which also serves tasks minted on other consoles).
    fn get_by_cid(&self, cid: &str) -> Option<ClaudeJob> {
        let conn = self.conn();
        let mut stmt = conn
            .prepare("SELECT id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address, cid FROM claude_jobs WHERE cid = ?1 LIMIT 1")
            .ok()?;

        stmt.query_row(params![cid], |row| {
            Ok(ClaudeJob {
                id: row.get(0)?,
                prompt: row.get(1)?,
                model: row.get(2)?,
                work_dir: row.get(3)?,
                status: JobStatus::from_str(&row.get::<_, String>(4)?),
                output: row.get(5)?,
                error: row.get(6)?,
                pid: row.get(7)?,
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
                user_address: row.get::<_, String>(10).unwrap_or_default(),
                cid: row.get::<_, Option<String>>(11).unwrap_or(None),
            })
        })
        .ok()
    }

    fn list(&self) -> Vec<ClaudeJob> {
        let conn = self.conn();
        let mut stmt = conn
            .prepare("SELECT id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address, cid FROM claude_jobs ORDER BY created_at DESC LIMIT 100")
            .unwrap();

        stmt.query_map([], |row| {
            Ok(ClaudeJob {
                id: row.get(0)?,
                prompt: row.get(1)?,
                model: row.get(2)?,
                work_dir: row.get(3)?,
                status: JobStatus::from_str(&row.get::<_, String>(4)?),
                output: row.get(5)?,
                error: row.get(6)?,
                pid: row.get(7)?,
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
                user_address: row.get::<_, String>(10).unwrap_or_default(),
                cid: row.get::<_, Option<String>>(11).unwrap_or(None),
            })
        })
        .unwrap()
        .filter_map(|r| r.ok())
        .collect()
    }

    fn delete(&self, id: &str) {
        let conn = self.conn();
        conn.execute("DELETE FROM claude_jobs WHERE id = ?1", params![id]).ok();
    }

    /// Startup reaper for jobs left `running`/`pending` by a previous api
    /// process (clean restart, crash, or OOM). Each running job's output is
    /// pumped into SQLite by an in-process reader task; when the api goes down
    /// that task dies, so nothing will ever advance the job — even though the
    /// claude child was spawned with `setsid` and may still be alive as an
    /// orphan (reparented to init, writing to a now-closed pipe). Leaving those
    /// rows `running` is exactly the "task hangs forever" symptom.
    ///
    /// So: for every stale row, if its process group is still alive, SIGKILL it
    /// (its reader is gone — it can only linger, never finish), then mark the
    /// job failed. Rows with no live process are just marked failed.
    fn mark_stale_running_as_failed(&self) {
        let conn = self.conn();
        let now = Utc::now().timestamp();

        // Collect stale rows first (can't mutate while a query stmt is live).
        let stale: Vec<(String, Option<i64>)> = {
            let mut stmt = match conn
                .prepare("SELECT id, pid FROM claude_jobs WHERE status = 'running' OR status = 'pending'")
            {
                Ok(s) => s,
                Err(_) => return,
            };
            let rows = stmt
                .query_map([], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, Option<i64>>(1)?))
                })
                .map(|it| it.filter_map(Result::ok).collect())
                .unwrap_or_default();
            rows
        };

        for (id, pid) in stale {
            let mut err = "Server restarted".to_string();
            if let Some(pid) = pid {
                if pid > 0 && pid_alive(pid as u32) {
                    // Orphaned process group with no reader — reap it so it
                    // stops squatting resources, then record why the job ended.
                    unsafe {
                        libc::kill(-(pid as i32), libc::SIGKILL);
                        libc::kill(pid as i32, libc::SIGKILL);
                    }
                    err = "Server restarted (orphaned process reaped)".to_string();
                }
            }
            conn.execute(
                "UPDATE claude_jobs SET status = 'failed', error = ?1, updated_at = ?2 WHERE id = ?3",
                params![err, now, id],
            )
            .ok();
        }
    }
}

/// True if `pid` still names a live process. `kill(pid, 0)` sends no signal but
/// performs the existence + permission check; api runs as root, so a `0` return
/// (or `EPERM`) means the pid is alive. Only a clean ESRCH means it's gone.
fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

// ── Job Manager ──────────────────────────────────────────────────────

pub struct ClaudeJobManager {
    store: Arc<JobStore>,
    streams: Arc<RwLock<HashMap<String, broadcast::Sender<String>>>>,
    /// Live stdin writers, one per running job. The CLI runs in stream-json
    /// input mode, so pushing a user message here mid-run steers the agent —
    /// the message is injected at the next tool boundary, exactly like typing
    /// while Claude Code works. The entry is dropped when the turn's `result`
    /// event lands (which closes stdin and lets the CLI exit).
    stdin_gates: Arc<RwLock<HashMap<String, mpsc::UnboundedSender<String>>>>,
    cancelled: Arc<RwLock<HashSet<String>>>,
    claude_bin: String,
}

impl ClaudeJobManager {
    pub fn new(db_dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&db_dir).ok();
        let db_path = db_dir.join("claude_jobs.db");
        let store = JobStore::new(db_path);
        let claude_bin = which_claude().unwrap_or_else(|| "claude".to_string());

        Ok(Self {
            store: Arc::new(store),
            streams: Arc::new(RwLock::new(HashMap::new())),
            stdin_gates: Arc::new(RwLock::new(HashMap::new())),
            cancelled: Arc::new(RwLock::new(HashSet::new())),
            claude_bin,
        })
    }

    pub fn recover_stale_jobs(&self) -> Result<(), String> {
        self.store.mark_stale_running_as_failed();
        Ok(())
    }

    pub async fn submit(&self, req: SubmitRequest) -> ClaudeJob {
        let id = Uuid::new_v4().to_string();
        let now = Utc::now().timestamp();

        // Determine anchor directory
        let anchor_dir = expand_tilde(&req.anchor_dir.unwrap_or_else(|| {
            std::env::var("MOD_ANCHOR").unwrap_or_else(|_| "~/mod".to_string())
        }));

        // Handle module creation modes
        let work_dir = if let Some(module_name) = &req.module_name {
            let orbit_path = std::path::PathBuf::from(&anchor_dir).join("mod/orbit");
            let module_path = orbit_path.join(module_name);

            // If fork mode, copy from source module
            if req.creation_mode.as_deref() == Some("fork") {
                if let Some(fork_source) = &req.fork_source {
                    let source_path = orbit_path.join(fork_source);
                    if source_path.exists() {
                        // Clone will be done in the prompt preparation
                        module_path.to_string_lossy().to_string()
                    } else {
                        // Fallback to new module creation
                        module_path.to_string_lossy().to_string()
                    }
                } else {
                    module_path.to_string_lossy().to_string()
                }
            } else {
                // New module creation
                module_path.to_string_lossy().to_string()
            }
        } else {
            expand_tilde(&req.work_dir.unwrap_or_else(|| {
                std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string())
            }))
        };

        // Enhance prompt with module creation context
        let enhanced_prompt = if req.module_name.is_some() {
            let mut prompt_parts = vec![];

            if req.creation_mode.as_deref() == Some("fork") && req.fork_source.is_some() {
                prompt_parts.push(format!(
                    "Fork module '{}' from '{}' in the orbit directory. ",
                    req.module_name.as_ref().unwrap(),
                    req.fork_source.as_ref().unwrap()
                ));
                prompt_parts.push(format!(
                    "First, copy the entire '{}' module directory to '{}'. ",
                    req.fork_source.as_ref().unwrap(),
                    req.module_name.as_ref().unwrap()
                ));
            } else {
                prompt_parts.push(format!(
                    "Create a new module named '{}' in the orbit directory. ",
                    req.module_name.as_ref().unwrap()
                ));
                prompt_parts.push("Create the directory structure and a basic mod.py or anchor file. ".to_string());
            }

            prompt_parts.push(format!("Anchor directory is: {}. ", anchor_dir));
            prompt_parts.push(req.prompt.clone());
            prompt_parts.join("")
        } else {
            req.prompt.clone()
        };

        // Module-context note for EDIT jobs (work_dir = the module dir, no
        // module_name). A single prompt may also fork this module or create
        // brand-new modules — tell the agent where siblings live so it doesn't
        // treat the cwd as a hard boundary. Non-owner jobs are still confined
        // to peers/ by the uid sandbox below; the note just aims them there.
        // Delivered via --append-system-prompt, NOT the job prompt, so it
        // never shows up in the user-visible task text.
        let module_note = if req.module_name.is_none() {
            let mod_root = std::path::PathBuf::from(&anchor_dir)
                .join("mod")
                .to_string_lossy()
                .to_string();
            let orbit_root = std::path::PathBuf::from(&anchor_dir)
                .join("mod/orbit")
                .to_string_lossy()
                .to_string();
            let job_user = req.user_address.clone().unwrap_or_default();
            let peers_only = !job_user.is_empty() && !crate::auth::is_owner(&job_user);
            if work_dir.trim_end_matches('/') == mod_root && !peers_only {
                // The whole-tree "mod" selection: the job's cwd spans every
                // module, so tell the agent cross-module edits are in scope.
                Some(format!(
                    "You are working on the ENTIRE module tree at {}. Every directory under {}/orbit (community modules) and {}/core (protocol modules) is a mod — you may read and edit ANY of them in this run, and cross-module changes are expected when the request spans modules. You may also create new modules under {}/orbit (scaffold a directory with a config.json and a mod.py or src/) or fork an existing one by copying its directory to a new name and updating self-references (config.json name, ports, routes). Prefer touching only the modules the request actually needs.",
                    work_dir, mod_root, mod_root, mod_root
                ))
            } else if work_dir.starts_with(&format!("{}/", orbit_root)) {
                let create_root = if peers_only {
                    format!("{}/peers", orbit_root)
                } else {
                    orbit_root.clone()
                };
                Some(format!(
                    "You are working on the module at {}. If the request calls for it, you may also fork this module or create additional new modules in this same run — modules live under {}. To fork: copy this module's directory to a new name there, then update self-references (config.json name, ports, routes). To create a fresh module: scaffold a new directory there with a config.json and a mod.py or src/. Otherwise just edit this module in place.",
                    work_dir, create_root
                ))
            } else {
                None
            }
        } else {
            None
        };

        // Save attached images to temp dir and prepend paths to prompt
        let enhanced_prompt = if let Some(images) = &req.images {
            if !images.is_empty() {
                let img_dir = format!("/tmp/claude-jobs/{}", id);
                std::fs::create_dir_all(&img_dir).ok();

                let mut saved_paths = Vec::new();
                for img in images {
                    // Strip data URL prefix if present (e.g. "data:image/png;base64,")
                    let b64 = if let Some(pos) = img.data.find(",") {
                        &img.data[pos + 1..]
                    } else {
                        &img.data
                    };

                    if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(b64) {
                        let path = format!("{}/{}", img_dir, img.name);
                        if std::fs::write(&path, &bytes).is_ok() {
                            saved_paths.push(path);
                        }
                    }
                }

                if !saved_paths.is_empty() {
                    let paths_str = saved_paths.join(", ");
                    format!(
                        "[Attached images: {}]\n\nPlease read and analyze the attached image files above.\n\n{}",
                        paths_str, enhanced_prompt
                    )
                } else {
                    enhanced_prompt
                }
            } else {
                enhanced_prompt
            }
        } else {
            enhanced_prompt
        };

        // Resolve the claude binary and the concrete model up front. `auto`
        // triggers a fast Haiku classification pass inside resolve_model, so the
        // job row records the model that ACTUALLY ran (not the literal "auto"),
        // which is what the console badge shows.
        let claude_bin = which_claude().unwrap_or_else(|| self.claude_bin.clone());
        let model = resolve_model(&req.model, &enhanced_prompt, &claude_bin).await;

        let job = ClaudeJob {
            id: id.clone(),
            prompt: enhanced_prompt.clone(),
            model: model.clone(),
            work_dir: work_dir.clone(),
            status: JobStatus::Pending,
            output: String::new(),
            error: None,
            pid: None,
            created_at: now,
            updated_at: now,
            user_address: req.user_address.clone().unwrap_or_default(),
            cid: None,
        };

        self.store.insert(&job);

        // Create broadcast channel for live streaming
        let (tx, _) = broadcast::channel::<String>(4096);
        {
            let mut streams = self.streams.write().await;
            streams.insert(id.clone(), tx.clone());
        }

        // Stdin channel for the CLI's stream-json input: the job prompt is the
        // first message, and any mid-run guidance the user types is pushed
        // through the same gate. Registered BEFORE spawn so guidance sent
        // while the process is still starting is queued, never lost.
        let (stdin_tx, stdin_rx) = mpsc::unbounded_channel::<String>();
        stdin_tx.send(enhanced_prompt.clone()).ok();
        {
            let mut gates = self.stdin_gates.write().await;
            gates.insert(id.clone(), stdin_tx);
        }

        // Spawn the process
        let store = Arc::clone(&self.store);
        let streams = Arc::clone(&self.streams);
        let stdin_gates = Arc::clone(&self.stdin_gates);
        let cancelled = Arc::clone(&self.cancelled);
        let job_id = id.clone();
        let prompt = enhanced_prompt;
        let agent_type = req.agent_type;
        let system_prompt = req.system_prompt;

        // ── Privilege drop for peer jobs ────────────────────────────────────────
        // The CLI runs with `--dangerously-skip-permissions`; `work_dir` is only a
        // cwd, NOT a write boundary. Since the API now runs as root (pm2), a
        // malicious PEER prompt could otherwise write absolute paths into the
        // owner's modules. So any job from a peer (every authenticated non-owner,
        // incl. whitelisted users) is spawned under an unprivileged uid that has no
        // write access to the root-owned module tree — kernel-enforced containment
        // no prompt can escape. Only the OWNER (and local-mode) jobs keep running as
        // root so they can edit the orbit; peers are confined to their own root.
        let job_user = req.user_address.clone().unwrap_or_default();
        let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
        let sandbox: Option<(u32, u32)> =
            if !job_user.is_empty() && !local_mode && !crate::auth::is_owner(&job_user) {
                Some(sandbox_ids())
            } else {
                None
            };

        tokio::spawn(async move {
            run_claude_process(&job_id, &prompt, &model, &work_dir, &claude_bin, agent_type.as_deref(), system_prompt.as_deref(), module_note.as_deref(), sandbox, store, streams, stdin_gates, stdin_rx, cancelled, tx).await;
        });

        self.store.get(&id).unwrap_or(job)
    }

    pub fn list_jobs(&self) -> Vec<ClaudeJob> {
        self.store.list()
    }

    pub fn get_job(&self, id: &str) -> Option<ClaudeJob> {
        self.store.get(id)
    }

    pub fn get_job_by_cid(&self, cid: &str) -> Option<ClaudeJob> {
        self.store.get_by_cid(cid)
    }

    pub fn delete_job(&self, id: &str) {
        self.store.delete(id);
    }

    pub async fn cancel_job(&self, id: &str) -> Result<(), String> {
        let job = self.store.get(id).ok_or_else(|| format!("Job {} not found", id))?;

        // Mark as cancelled FIRST so run_claude_process won't overwrite
        {
            let mut cancelled = self.cancelled.write().await;
            cancelled.insert(id.to_string());
        }
        self.store.update_status(id, &JobStatus::Cancelled, None);

        if job.status == JobStatus::Running || job.status == JobStatus::Pending {
            if let Some(pid) = job.pid {
                #[cfg(unix)]
                {
                    unsafe {
                        // SIGTERM the process group (child was spawned with setsid)
                        libc::kill(-(pid as i32), libc::SIGTERM);
                    }
                    // Give it a moment to die gracefully
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                    unsafe {
                        // Force kill the entire process group
                        libc::kill(-(pid as i32), libc::SIGKILL);
                        // Also kill the process directly as final fallback
                        libc::kill(pid as i32, libc::SIGKILL);
                    }
                }
            }
        }

        // Notify stream subscribers
        {
            let streams = self.streams.read().await;
            if let Some(tx) = streams.get(id) {
                tx.send("[CANCELLED]\n".to_string()).ok();
            }
        }
        let mut streams = self.streams.write().await;
        streams.remove(id);
        self.stdin_gates.write().await.remove(id);
        Ok(())
    }

    /// Inject a user message into a RUNNING job — the console's "guide the
    /// task" feature. The CLI runs with stream-json input, so the message is
    /// delivered to the agent at its next tool boundary (same mechanism as
    /// typing while Claude Code works). A visible marker is appended to the
    /// job log and broadcast to live stream subscribers so the guidance shows
    /// up inline in the output, right where it was injected.
    pub async fn steer_job(&self, id: &str, message: &str) -> Result<(), String> {
        let job = self
            .store
            .get(id)
            .ok_or_else(|| format!("Job {} not found", id))?;
        if !matches!(job.status, JobStatus::Running | JobStatus::Pending) {
            return Err("Job is not running — guidance only works on an active task".to_string());
        }
        let sender = {
            let gates = self.stdin_gates.read().await;
            gates.get(id).cloned()
        }
        .ok_or_else(|| "Task is wrapping up — too late to guide it".to_string())?;
        sender
            .send(message.to_string())
            .map_err(|_| "Task stdin already closed".to_string())?;

        let marker = format!("\n\n◈ GUIDANCE ▸ {}\n\n", message.trim());
        self.store.append_output(id, &marker);
        let streams = self.streams.read().await;
        if let Some(tx) = streams.get(id) {
            tx.send(marker).ok();
        }
        Ok(())
    }

    pub async fn subscribe(&self, id: &str) -> Option<broadcast::Receiver<String>> {
        let streams = self.streams.read().await;
        streams.get(id).map(|tx| tx.subscribe())
    }

    /// Backfill task CIDs for terminal jobs that predate the store bridge
    /// (or whose bridge run failed): one slow serial pass after startup, so
    /// every past task also becomes a store object. Kill switch shared with
    /// the live path: CLAUDE_TASK_CIDS=0.
    pub fn spawn_cid_backfill(&self) {
        if !task_cids_enabled() {
            return;
        }
        let store = Arc::clone(&self.store);
        tokio::spawn(async move {
            // Let the server finish booting before shelling out to python.
            tokio::time::sleep(std::time::Duration::from_secs(30)).await;
            for id in store.terminal_missing_cid(500) {
                let s = Arc::clone(&store);
                let jid = id.clone();
                let _ = tokio::task::spawn_blocking(move || publish_task_to_store(&jid, &s)).await;
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            }
        });
    }
}

fn task_cids_enabled() -> bool {
    std::env::var("CLAUDE_TASK_CIDS").unwrap_or_default() != "0"
}

// ── Helpers ──────────────────────────────────────────────────────────

fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") || path == "~" {
        if let Ok(home) = std::env::var("HOME") {
            return path.replacen("~", &home, 1);
        }
    }
    path.to_string()
}

// ── Process Runner ───────────────────────────────────────────────────

/// Unprivileged (uid, gid) used to sandbox non-owner jobs. Defaults to
/// `nobody`/`nogroup` (65534), overridable via CLAUDE_SANDBOX_UID / _GID so a
/// deployment can dedicate a purpose-built user. A process running as this id
/// cannot write the root-owned module tree, which is the whole point.
fn sandbox_ids() -> (u32, u32) {
    let uid = std::env::var("CLAUDE_SANDBOX_UID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(65534);
    let gid = std::env::var("CLAUDE_SANDBOX_GID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(65534);
    (uid, gid)
}

/// Best-effort recursive chown so the dropped-privilege child can actually write
/// inside its own workspace (which we created as root). Bounded depth to avoid
/// pathological trees; failures are non-fatal (the job just may not be able to
/// write pre-existing files, never a security risk).
fn chown_tree(path: &std::path::Path, uid: u32, gid: u32, depth: usize) {
    use std::os::unix::fs::chown;
    if depth > 64 {
        return;
    }
    let _ = chown(path, Some(uid), Some(gid));
    if path.is_dir() && !path.is_symlink() {
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                chown_tree(&entry.path(), uid, gid, depth + 1);
            }
        }
    }
}

async fn run_claude_process(
    job_id: &str,
    prompt: &str,
    model: &str,
    work_dir: &str,
    claude_bin: &str,
    agent_type: Option<&str>,
    system_prompt: Option<&str>,
    module_note: Option<&str>,
    sandbox: Option<(u32, u32)>,
    store: Arc<JobStore>,
    streams: Arc<RwLock<HashMap<String, broadcast::Sender<String>>>>,
    stdin_gates: Arc<RwLock<HashMap<String, mpsc::UnboundedSender<String>>>>,
    stdin_rx: mpsc::UnboundedReceiver<String>,
    cancelled: Arc<RwLock<HashSet<String>>>,
    tx: broadcast::Sender<String>,
) {
    // Ensure work_dir exists before spawning (otherwise current_dir causes ENOENT)
    if let Err(e) = std::fs::create_dir_all(work_dir) {
        let err = format!("Failed to create work_dir '{}': {}", work_dir, e);
        store.update_status(job_id, &JobStatus::Failed, Some(&err));
        tx.send(format!("[ERROR] {}\n", err)).ok();
        stdin_gates.write().await.remove(job_id);
        return;
    }

    // For a sandboxed (non-owner) job, hand its workspace to the unprivileged uid
    // so the dropped-privilege child can write there — and nowhere root-owned.
    if let Some((uid, gid)) = sandbox {
        chown_tree(std::path::Path::new(work_dir), uid, gid, 0);
    }

    // Ensure /opt/homebrew/bin is in PATH for the child process
    let child_path = {
        let current = std::env::var("PATH").unwrap_or_default();
        if current.contains("/opt/homebrew/bin") {
            current
        } else {
            format!("{}:/opt/homebrew/bin:/usr/local/bin", current)
        }
    };

    let mut cmd = Command::new(claude_bin);
    cmd.arg("--print")
        .arg("--verbose")
        .arg("--model")
        .arg(model)
        // stream-json INPUT keeps stdin open for the whole run: the prompt is
        // the first message, and mid-run guidance from POST /jobs/:id/message
        // rides the same pipe. The CLI injects late messages at the next tool
        // boundary (steering) and exits once stdin closes after the turn ends.
        .arg("--input-format")
        .arg("stream-json")
        .arg("--output-format")
        .arg("stream-json")
        .arg("--dangerously-skip-permissions");

    // Personality system prompt takes priority over agent_type; --agent still
    // composes with --append-system-prompt, so the module note can ride along
    // either way.
    if system_prompt.is_none() {
        if let Some(agent) = agent_type {
            cmd.arg("--agent").arg(agent);
        }
    }
    let mut append_sp = system_prompt.unwrap_or("").to_string();
    if let Some(note) = module_note {
        if !note.is_empty() {
            if !append_sp.is_empty() {
                append_sp.push_str("\n\n");
            }
            append_sp.push_str(note);
        }
    }
    if !append_sp.is_empty() {
        cmd.arg("--append-system-prompt").arg(&append_sp);
    }

    // The prompt is NOT an argv arg anymore — it arrives as the first
    // stream-json stdin message (already queued in stdin_rx by submit()).
    cmd.env("PATH", &child_path)
        .current_dir(work_dir)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    // Prefer the OAuth subscription token (~/.claude/.credentials.json) over
    // any inherited ANTHROPIC_API_KEY. Mirrors docker-entrypoint.sh so local
    // serve mode behaves the same as the container. HOME is forwarded
    // explicitly so the child resolves the credentials file in the right place
    // even when the parent was spawned via runuser/su without env reset.
    let home = std::env::var("HOME").unwrap_or_default();
    // Console-pasted credentials (POST /agent/auth) — the fallback when no
    // subscription credentials file exists, and the deliberate override for
    // whatever stale ANTHROPIC_API_KEY the server process inherited.
    let (stored_oauth, stored_key) = crate::auth::read_agent_auth();
    match sandbox {
        // Trusted (owner / local): prefer the OAuth subscription creds in root's
        // HOME over any inherited ANTHROPIC_API_KEY.
        None => {
            let creds_exists = !home.is_empty()
                && std::path::Path::new(&format!("{}/.claude/.credentials.json", home)).exists();
            if !home.is_empty() {
                cmd.env("HOME", &home);
            }
            if creds_exists {
                cmd.env_remove("ANTHROPIC_API_KEY");
            } else if let Some(tok) = &stored_oauth {
                cmd.env_remove("ANTHROPIC_API_KEY");
                cmd.env("CLAUDE_CODE_OAUTH_TOKEN", tok);
            } else if let Some(key) = &stored_key {
                cmd.env("ANTHROPIC_API_KEY", key);
            }
        }
        // Sandboxed (non-owner): the dropped-privilege child cannot read root's
        // 0600 credentials file, so point HOME at its own writable workspace and
        // forward the OAuth access token explicitly. The credentials file wins
        // over any inherited ANTHROPIC_API_KEY — the parent's env may carry a
        // long-dead token from whichever session originally started the server,
        // while the CLI keeps the file refreshed.
        Some(_) => {
            cmd.env("HOME", work_dir);
            let file_token = if home.is_empty() {
                None
            } else {
                std::fs::read_to_string(format!("{}/.claude/.credentials.json", home))
                    .ok()
                    .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
                    .and_then(|v| {
                        v.get("claudeAiOauth")
                            .and_then(|o| o.get("accessToken"))
                            .and_then(|t| t.as_str())
                            .map(|s| s.to_string())
                    })
            };
            if let Some(tok) = file_token.or(stored_oauth) {
                cmd.env_remove("ANTHROPIC_API_KEY");
                cmd.env("CLAUDE_CODE_OAUTH_TOKEN", tok);
            } else if let Some(key) = &stored_key {
                cmd.env("ANTHROPIC_API_KEY", key);
            }
        }
    }

    // Put child in its own process group so kill(-pid) works; for sandboxed jobs,
    // also irreversibly drop to the unprivileged uid/gid BEFORE exec so the CLI
    // can never touch root-owned files no matter what the prompt asks.
    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(move || {
            libc::setsid();
            if let Some((uid, gid)) = sandbox {
                // Drop supplementary groups, then gid, then uid — order matters:
                // once uid is dropped we can no longer change gid/groups.
                if libc::setgroups(0, std::ptr::null()) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::setgid(gid as libc::gid_t) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::setuid(uid as libc::uid_t) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                // Defense in depth: confirm privileges are truly gone.
                if libc::setuid(0) == 0 {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::PermissionDenied,
                        "privilege drop failed: still able to regain root",
                    ));
                }
            }
            Ok(())
        });
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let err = format!("Failed to spawn claude (bin='{}'): {} — work_dir='{}', PATH='{}'",
                claude_bin, e, work_dir, child_path);
            store.update_status(job_id, &JobStatus::Failed, Some(&err));
            tx.send(format!("[ERROR] {}\n", err)).ok();
            stdin_gates.write().await.remove(job_id);
            return;
        }
    };

    let pid = child.id().unwrap_or(0);
    store.update_pid(job_id, pid);

    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();

    // Stdin writer: serialize each queued text (initial prompt + any mid-run
    // guidance) as a stream-json user message. When the gate is dropped
    // (turn's `result` event, cancel, or cleanup) the channel closes; the
    // writer drains what's left, then drops stdin — EOF is what tells the CLI
    // the session is over.
    let child_stdin = child.stdin.take();
    let stdin_task = tokio::spawn(async move {
        let mut stdin = match child_stdin {
            Some(s) => s,
            None => return,
        };
        let mut rx = stdin_rx;
        while let Some(text) = rx.recv().await {
            let msg = serde_json::json!({
                "type": "user",
                "message": { "role": "user", "content": [{ "type": "text", "text": text }] }
            });
            let line = format!("{}\n", msg);
            if stdin.write_all(line.as_bytes()).await.is_err() {
                break;
            }
            let _ = stdin.flush().await;
        }
        // rx closed → drop stdin → CLI sees EOF and finishes up.
    });

    let mut stdout_reader = BufReader::new(stdout).lines();
    let mut stderr_reader = BufReader::new(stderr).lines();

    let store_out = Arc::clone(&store);
    let tx_out = tx.clone();
    let id_out = job_id.to_string();
    let gates_out = Arc::clone(&stdin_gates);

    let stdout_task = tokio::spawn(async move {
        let mut parser = StreamParser::new();
        while let Ok(Some(line)) = stdout_reader.next_line().await {
            // A `result` event ends the turn: drop the stdin gate so the
            // writer closes stdin and the CLI exits. (Guidance injected
            // mid-turn is absorbed INTO that turn — verified CLI behavior —
            // so one result means the work, guidance included, is done. A
            // message raced in just before the result still drains to stdin
            // before EOF and runs as one final turn.)
            if line.contains("\"type\":\"result\"") {
                let is_result = serde_json::from_str::<serde_json::Value>(&line)
                    .ok()
                    .and_then(|v| v.get("type").and_then(|t| t.as_str()).map(|t| t == "result"))
                    .unwrap_or(false);
                if is_result {
                    gates_out.write().await.remove(&id_out);
                }
            }
            if let Some(text) = parser.parse(&line) {
                if !text.is_empty() {
                    store_out.append_output(&id_out, &text);
                    tx_out.send(text).ok();
                }
            }
        }
        // stdout EOF = the process is gone; make sure the gate dies with it.
        gates_out.write().await.remove(&id_out);
    });

    let store_err = Arc::clone(&store);
    let tx_err = tx.clone();
    let id_err = job_id.to_string();

    let stderr_task = tokio::spawn(async move {
        while let Ok(Some(line)) = stderr_reader.next_line().await {
            let text = format!("{}\n", line);
            store_err.append_output(&id_err, &text);
            tx_err.send(text).ok();
        }
    });

    let _ = stdout_task.await;
    let _ = stderr_task.await;
    // Gate is gone by now (stdout EOF handler) — the writer drains and exits.
    let _ = stdin_task.await;

    // Wait for exit status
    let status = child.wait().await;

    // Don't overwrite status if job was already cancelled
    let was_cancelled = {
        let mut c = cancelled.write().await;
        c.remove(job_id)
    };

    if !was_cancelled {
        match status {
            Ok(exit) if exit.success() => {
                store.update_status(job_id, &JobStatus::Completed, None);
                // Every completed job that touched a module leaves a
                // content-addressed version behind: snapshot the module dir
                // into the localfs blob store and log the CID (tagged with
                // this job id so the EDIT tab can pair edit ↔ version).
                // Identical tree ⇒ identical CID ⇒ no-op edits add no record.
                if let Some((cid, module)) = snapshot_after_job(job_id, work_dir, prompt, &store) {
                    tx.send(format!("[VERSION] {} → localfs cid {}\n", module, cid)).ok();
                }
            }
            Ok(exit) => {
                let code = exit.code().unwrap_or(-1);
                store.update_status(job_id, &JobStatus::Failed, Some(&format!("Exit code: {}", code)));
            }
            Err(e) => {
                store.update_status(job_id, &JobStatus::Failed, Some(&format!("Wait error: {}", e)));
            }
        }
    }

    // Every task that reached a terminal state (completed, failed, cancelled)
    // becomes a content-addressed object: the bundle (prompt + output + meta)
    // is put into localfs and registered in the store module's index via the
    // python store bridge, so the TASKS ledger is visible in store.
    if task_cids_enabled() {
        let store_pub = Arc::clone(&store);
        let id_pub = job_id.to_string();
        let published = tokio::task::spawn_blocking(move || {
            publish_task_to_store(&id_pub, &store_pub)
        })
        .await
        .ok()
        .flatten();
        if let Some(cid) = published {
            tx.send(format!("[STORE] task → localfs cid {}\n", cid)).ok();
        }
    }

    tx.send("[DONE]\n".to_string()).ok();

    // Clean up
    stdin_gates.write().await.remove(job_id);
    let mut s = streams.write().await;
    s.remove(job_id);
}

/// Snapshot the module a completed job worked on into the localfs blob store
/// and append an "edit" version record carrying the job id. Returns
/// (cid, module_name) when a new version was recorded; None when the job ran
/// outside the module tree, the tree is unchanged, or the snapshot failed
/// (all non-fatal — the job itself already completed).
fn snapshot_after_job(
    job_id: &str,
    work_dir: &str,
    prompt: &str,
    store: &JobStore,
) -> Option<(String, String)> {
    use crate::snapshots::{
        append_version, default_store, module_for_work_dir, read_versions, snapshot_dir,
        VersionRecord,
    };
    let (module, root) = module_for_work_dir(work_dir)?;
    let blob_store = default_store();
    let (cid, _manifest) = snapshot_dir(&root, &blob_store).ok()?;
    let history = read_versions(&module);
    if history.last().map(|v| v.cid == cid).unwrap_or(false) {
        return None; // job changed nothing — same tree, same CID
    }
    let parent = history.last().map(|v| v.cid.clone());
    // The stored prompt may carry the injected "Note: you are working on…"
    // suffix and image-path preamble — keep just the human request, one line.
    const IMG_PREAMBLE_END: &str =
        "]\n\nPlease read and analyze the attached image files above.\n\n";
    let mut human = prompt;
    if human.starts_with("[Attached images: ") {
        if let Some(idx) = human.find(IMG_PREAMBLE_END) {
            human = &human[idx + IMG_PREAMBLE_END.len()..];
        }
    }
    let human = human.split("\n\nNote: you are working on").next().unwrap_or(human);
    let line = human.lines().find(|l| !l.trim().is_empty()).unwrap_or("").trim();
    let message: String = if line.chars().count() > 120 {
        format!("{}…", line.chars().take(119).collect::<String>())
    } else {
        line.to_string()
    };
    let author = store.get(job_id).map(|j| j.user_address).unwrap_or_default();
    let record = VersionRecord {
        cid: cid.clone(),
        message,
        author,
        timestamp: Utc::now().timestamp().max(0) as u64,
        parent,
        registry_cid: None,
        registry_prev: None,
        action: Some("edit".to_string()),
        job_id: Some(job_id.to_string()),
    };
    append_version(&module, record).ok()?;
    Some((cid, module))
}

/// Publish a finished task into localfs + the store index by shelling out to
/// the python store bridge (src/api/store_bridge.py). Builds a self-contained
/// JSON bundle (prompt, output, status, metadata, and — when the job produced
/// a module snapshot — that version's CID), pipes it to the bridge, records
/// the returned localfs CID on the job row, and returns it. Every failure is
/// non-fatal: the task itself already finished, and the startup backfill
/// retries anything left without a CID.
fn publish_task_to_store(job_id: &str, store: &JobStore) -> Option<String> {
    use std::io::Write;
    use std::process::{Command as StdCommand, Stdio};

    let job = store.get(job_id)?;
    if !matches!(
        job.status,
        JobStatus::Completed | JobStatus::Failed | JobStatus::Cancelled
    ) {
        return None;
    }
    if job.cid.is_some() {
        return job.cid;
    }

    // Pair the task with the module version its completion snapshotted, when
    // one exists — the versions log tags "edit" records with the job id.
    let module = crate::snapshots::module_for_work_dir(&job.work_dir).map(|(name, _)| name);
    let version_cid = module.as_deref().and_then(|name| {
        crate::snapshots::read_versions(name)
            .iter()
            .rev()
            .find(|v| v.job_id.as_deref() == Some(job_id))
            .map(|v| v.cid.clone())
    });

    let owner = if job.user_address.is_empty() {
        crate::auth::get_owner_address().unwrap_or_default()
    } else {
        job.user_address.clone()
    };

    let bundle = task_bundle_json(&job, module, version_cid);
    let short: String = job.id.chars().take(8).collect();
    let envelope = serde_json::json!({
        "task": bundle,
        "owner": owner,
        "key": format!("claude-task-{}.json", short),
    });

    let bridge = store_bridge_path()?;

    let mut child = StdCommand::new("python3")
        .arg(&bridge)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    child
        .stdin
        .take()?
        .write_all(envelope.to_string().as_bytes())
        .ok()?;
    let out = child.wait_with_output().ok()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    // The bridge keeps stdout to one JSON line, but parse defensively: take
    // the last line that decodes and carries a cid.
    let cid = stdout.lines().rev().find_map(|l| {
        serde_json::from_str::<serde_json::Value>(l.trim())
            .ok()?
            .get("cid")?
            .as_str()
            .map(|s| s.to_string())
    })?;
    store.update_cid(job_id, &cid);
    Some(cid)
}

/// The self-contained, content-addressed shape of one task — what gets put
/// into localfs and what `GET /tasks/:cid` returns. One builder so the
/// published blob and the replay endpoint can never drift apart.
pub fn task_bundle_json(
    job: &ClaudeJob,
    module: Option<String>,
    version_cid: Option<String>,
) -> serde_json::Value {
    serde_json::json!({
        "kind": "claude-task",
        "version": 1,
        "id": job.id,
        "prompt": job.prompt,
        "model": job.model,
        "work_dir": job.work_dir,
        "status": job.status.to_string(),
        "output": job.output,
        "error": job.error,
        "user_address": job.user_address,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "module": module,
        "module_version_cid": version_cid,
    })
}

/// Path to the python store bridge, or None when it isn't installed.
fn store_bridge_path() -> Option<String> {
    let bridge = std::env::var("CLAUDE_STORE_BRIDGE").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        format!("{}/mod/mod/orbit/claude/src/api/store_bridge.py", home)
    });
    if std::path::Path::new(&bridge).exists() {
        Some(bridge)
    } else {
        None
    }
}

/// Fetch a task bundle straight from localfs by CID via the store bridge —
/// the portable half of replay: a QR minted on one console resolves on any
/// console that shares (or has synced) the blob store, even when the job
/// isn't in the local ledger. Blocking — call from spawn_blocking.
pub fn fetch_task_bundle(cid: &str) -> Option<serde_json::Value> {
    use std::process::{Command as StdCommand, Stdio};
    // CIDs are base58/base32/hex tokens; refuse anything else outright.
    if cid.is_empty() || cid.len() > 128 || !cid.chars().all(|c| c.is_ascii_alphanumeric()) {
        return None;
    }
    let bridge = store_bridge_path()?;
    let out = StdCommand::new("python3")
        .arg(&bridge)
        .arg("get")
        .arg(cid)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let v = stdout
        .lines()
        .rev()
        .find_map(|l| serde_json::from_str::<serde_json::Value>(l.trim()).ok())?;
    if !v.is_object() || v.get("error").is_some() {
        return None;
    }
    Some(v)
}

// ── Stateful Stream Parser ───────────────────────────────────────────

struct StreamParser {
    current_tool: Option<String>,
    current_tool_input: String,
}

impl StreamParser {
    fn new() -> Self {
        Self {
            current_tool: None,
            current_tool_input: String::new(),
        }
    }

    fn parse(&mut self, line: &str) -> Option<String> {
        let v: serde_json::Value = serde_json::from_str(line).ok()?;

        match v.get("type")?.as_str()? {
            "system" => {
                if v.get("subtype").and_then(|s| s.as_str()) == Some("init") {
                    let model = v.get("model").and_then(|m| m.as_str()).unwrap_or("unknown");
                    Some(format!("⏳ Session started ({})\n", model))
                } else {
                    None
                }
            }
            "assistant" => {
                if let Some(content) = v.pointer("/message/content") {
                    if let Some(arr) = content.as_array() {
                        let mut out = String::new();
                        for block in arr {
                            match block.get("type").and_then(|t| t.as_str()) {
                                Some("text") => {
                                    if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                                        out.push_str(t);
                                    }
                                }
                                Some("tool_use") => {
                                    let name = block.get("name").and_then(|n| n.as_str()).unwrap_or("tool");
                                    out.push_str(&format!("\n⚡ {}", name));
                                    // Extract tool input details from the complete message
                                    if let Some(input) = block.get("input") {
                                        let input_str = serde_json::to_string(input).unwrap_or_default();
                                        if let Some(detail) = self.format_tool_input(name, &input_str) {
                                            out.push_str(&detail);
                                        } else {
                                            out.push('\n');
                                        }
                                    } else {
                                        out.push('\n');
                                    }
                                }
                                _ => {}
                            }
                        }
                        if !out.is_empty() {
                            return Some(out);
                        }
                    }
                }
                None
            }
            "content_block_start" => {
                if let Some(cb) = v.get("content_block") {
                    if cb.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                        let name = cb.get("name").and_then(|n| n.as_str()).unwrap_or("tool");
                        self.current_tool = Some(name.to_string());
                        self.current_tool_input.clear();
                        return Some(format!("\n⚡ {}", name));
                    }
                }
                None
            }
            "content_block_delta" => {
                if let Some(delta) = v.get("delta") {
                    match delta.get("type").and_then(|t| t.as_str())? {
                        "text_delta" => {
                            return delta.get("text").and_then(|t| t.as_str()).map(|s| s.to_string());
                        }
                        "thinking_delta" => {
                            return delta.get("thinking").and_then(|t| t.as_str()).map(|s| format!("💭 {}", s));
                        }
                        "input_json_delta" => {
                            if let Some(json) = delta.get("partial_json").and_then(|t| t.as_str()) {
                                self.current_tool_input.push_str(json);
                            }
                            return None;
                        }
                        _ => {}
                    }
                }
                None
            }
            "content_block_stop" => {
                if let Some(tool_name) = self.current_tool.take() {
                    let input = std::mem::take(&mut self.current_tool_input);
                    return self.format_tool_input(&tool_name, &input);
                }
                None
            }
            "result" => {
                if let Some(result) = v.get("result").and_then(|r| r.as_str()) {
                    if !result.is_empty() {
                        return Some(format!("\n{}\n", result));
                    }
                }
                None
            }
            _ => None,
        }
    }

    fn format_tool_input(&self, tool: &str, input_json: &str) -> Option<String> {
        if input_json.is_empty() {
            return None;
        }
        let v: serde_json::Value = serde_json::from_str(input_json).ok()?;

        match tool {
            "Edit" => {
                let file = v.get("file_path").and_then(|f| f.as_str()).unwrap_or("?");
                let old = v.get("old_string").and_then(|s| s.as_str()).unwrap_or("");
                let new = v.get("new_string").and_then(|s| s.as_str()).unwrap_or("");
                let mut out = format!("\n┌─ EDIT: {}\n", file);
                for l in old.lines() {
                    out.push_str(&format!("│- {}\n", l));
                }
                out.push_str("│───\n");
                for l in new.lines() {
                    out.push_str(&format!("│+ {}\n", l));
                }
                out.push_str("└─\n");
                Some(out)
            }
            "Write" => {
                let file = v.get("file_path").and_then(|f| f.as_str()).unwrap_or("?");
                let content = v.get("content").and_then(|s| s.as_str()).unwrap_or("");
                let line_count = content.lines().count();
                Some(format!("\n┌─ WRITE: {} ({} lines)\n└─\n", file, line_count))
            }
            "Read" => {
                let file = v.get("file_path").and_then(|f| f.as_str()).unwrap_or("?");
                Some(format!(" {}\n", file))
            }
            "Bash" => {
                let cmd = v.get("command").and_then(|s| s.as_str()).unwrap_or("?");
                let desc = v.get("description").and_then(|s| s.as_str());
                if let Some(d) = desc {
                    Some(format!("\n$ {} # {}\n", cmd, d))
                } else {
                    Some(format!("\n$ {}\n", cmd))
                }
            }
            "Glob" => {
                let pattern = v.get("pattern").and_then(|s| s.as_str()).unwrap_or("?");
                Some(format!(" glob:{}\n", pattern))
            }
            "Grep" => {
                let pattern = v.get("pattern").and_then(|s| s.as_str()).unwrap_or("?");
                Some(format!(" grep:{}\n", pattern))
            }
            "Task" => {
                let desc = v.get("description").and_then(|s| s.as_str()).unwrap_or("subtask");
                Some(format!(" → {}\n", desc))
            }
            _ => None,
        }
    }
}

fn which_claude() -> Option<String> {
    // Try `which` first — augment PATH so it finds homebrew/npm installs
    // even when the server was started from a minimal environment.
    let extra_paths = "/opt/homebrew/bin:/usr/local/bin";
    let current_path = std::env::var("PATH").unwrap_or_default();
    let augmented_path = if current_path.is_empty() {
        extra_paths.to_string()
    } else {
        format!("{}:{}", current_path, extra_paths)
    };

    if let Some(path) = std::process::Command::new("which")
        .arg("claude")
        .env("PATH", &augmented_path)
        .output()
        .ok()
        .and_then(|out| {
            if out.status.success() {
                let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !p.is_empty() { Some(p) } else { None }
            } else {
                None
            }
        })
    {
        return Some(path);
    }

    // Fallback: check common install locations
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let candidates = [
        "/opt/homebrew/bin/claude".to_string(),
        "/usr/local/bin/claude".to_string(),
        format!("{}/.local/bin/claude", home),
        format!("{}/.npm-global/bin/claude", home),
    ];
    for c in &candidates {
        if std::path::Path::new(c).exists() {
            return Some(c.clone());
        }
    }

    None
}
