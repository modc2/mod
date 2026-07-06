//! Codex Job Manager — spawn and manage background Codex CLI processes

use chrono::Utc;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::{broadcast, RwLock};
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
pub struct CodexJob {
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
    "sonnet".to_string()
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
            "CREATE TABLE IF NOT EXISTS codex_jobs (
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
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON codex_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON codex_jobs(created_at DESC);",
        )
        .expect("SQLite init failed");

        // Migration: add user_address column if missing (existing DBs)
        conn.execute(
            "ALTER TABLE codex_jobs ADD COLUMN user_address TEXT NOT NULL DEFAULT ''",
            [],
        )
        .ok(); // Ignore error if column already exists
    }

    fn insert(&self, job: &CodexJob) {
        let conn = self.conn();
        conn.execute(
            "INSERT INTO codex_jobs (id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                job.id, job.prompt, job.model, job.work_dir,
                job.status.to_string(), job.output, job.error, job.pid,
                job.created_at, job.updated_at, job.user_address
            ],
        )
        .expect("SQLite insert failed");
    }

    fn update_status(&self, id: &str, status: &JobStatus, error: Option<&str>) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE codex_jobs SET status = ?1, error = ?2, updated_at = ?3 WHERE id = ?4",
            params![status.to_string(), error, now, id],
        )
        .ok();
    }

    fn update_pid(&self, id: &str, pid: u32) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE codex_jobs SET pid = ?1, status = 'running', updated_at = ?2 WHERE id = ?3",
            params![pid, now, id],
        )
        .ok();
    }

    fn append_output(&self, id: &str, text: &str) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE codex_jobs SET output = output || ?1, updated_at = ?2 WHERE id = ?3",
            params![text, now, id],
        )
        .ok();
    }

    fn get(&self, id: &str) -> Option<CodexJob> {
        let conn = self.conn();
        let mut stmt = conn
            .prepare("SELECT id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address FROM codex_jobs WHERE id = ?1")
            .ok()?;

        stmt.query_row(params![id], |row| {
            Ok(CodexJob {
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
            })
        })
        .ok()
    }

    fn list(&self) -> Vec<CodexJob> {
        let conn = self.conn();
        let mut stmt = conn
            .prepare("SELECT id, prompt, model, work_dir, status, output, error, pid, created_at, updated_at, user_address FROM codex_jobs ORDER BY created_at DESC LIMIT 100")
            .unwrap();

        stmt.query_map([], |row| {
            Ok(CodexJob {
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
            })
        })
        .unwrap()
        .filter_map(|r| r.ok())
        .collect()
    }

    fn delete(&self, id: &str) {
        let conn = self.conn();
        conn.execute("DELETE FROM codex_jobs WHERE id = ?1", params![id]).ok();
    }

    fn mark_stale_running_as_failed(&self) {
        let conn = self.conn();
        let now = Utc::now().timestamp();
        conn.execute(
            "UPDATE codex_jobs SET status = 'failed', error = 'Server restarted', updated_at = ?1 WHERE status = 'running' OR status = 'pending'",
            params![now],
        )
        .ok();
    }
}

// ── Job Manager ──────────────────────────────────────────────────────

pub struct CodexJobManager {
    store: Arc<JobStore>,
    streams: Arc<RwLock<HashMap<String, broadcast::Sender<String>>>>,
    cancelled: Arc<RwLock<HashSet<String>>>,
    codex_bin: String,
}

impl CodexJobManager {
    pub fn new(db_dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&db_dir).ok();
        let db_path = db_dir.join("codex_jobs.db");
        let store = JobStore::new(db_path);
        let codex_bin = which_codex().unwrap_or_else(|| "codex".to_string());

        Ok(Self {
            store: Arc::new(store),
            streams: Arc::new(RwLock::new(HashMap::new())),
            cancelled: Arc::new(RwLock::new(HashSet::new())),
            codex_bin,
        })
    }

    pub fn recover_stale_jobs(&self) -> Result<(), String> {
        self.store.mark_stale_running_as_failed();
        Ok(())
    }

    pub async fn submit(&self, req: SubmitRequest) -> CodexJob {
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

        // Save attached images to temp dir and prepend paths to prompt
        let enhanced_prompt = if let Some(images) = &req.images {
            if !images.is_empty() {
                let img_dir = format!("/tmp/codex-jobs/{}", id);
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

        let job = CodexJob {
            id: id.clone(),
            prompt: enhanced_prompt.clone(),
            model: req.model.clone(),
            work_dir: work_dir.clone(),
            status: JobStatus::Pending,
            output: String::new(),
            error: None,
            pid: None,
            created_at: now,
            updated_at: now,
            user_address: req.user_address.clone().unwrap_or_default(),
        };

        self.store.insert(&job);

        // Create broadcast channel for live streaming
        let (tx, _) = broadcast::channel::<String>(4096);
        {
            let mut streams = self.streams.write().await;
            streams.insert(id.clone(), tx.clone());
        }

        // Spawn the process
        let store = Arc::clone(&self.store);
        let streams = Arc::clone(&self.streams);
        let cancelled = Arc::clone(&self.cancelled);
        // Re-resolve codex binary at spawn time (not just init) so it picks up
        // newly installed binaries without requiring a server restart.
        let codex_bin = which_codex().unwrap_or_else(|| self.codex_bin.clone());
        let job_id = id.clone();
        let prompt = enhanced_prompt;
        let model = req.model;
        let agent_type = req.agent_type;
        let system_prompt = req.system_prompt;

        tokio::spawn(async move {
            run_codex_process(&job_id, &prompt, &model, &work_dir, &codex_bin, agent_type.as_deref(), system_prompt.as_deref(), store, streams, cancelled, tx).await;
        });

        self.store.get(&id).unwrap_or(job)
    }

    pub fn list_jobs(&self) -> Vec<CodexJob> {
        self.store.list()
    }

    pub fn get_job(&self, id: &str) -> Option<CodexJob> {
        self.store.get(id)
    }

    pub fn delete_job(&self, id: &str) {
        self.store.delete(id);
    }

    pub async fn cancel_job(&self, id: &str) -> Result<(), String> {
        let job = self.store.get(id).ok_or_else(|| format!("Job {} not found", id))?;

        // Mark as cancelled FIRST so run_codex_process won't overwrite
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
        Ok(())
    }

    pub async fn subscribe(&self, id: &str) -> Option<broadcast::Receiver<String>> {
        let streams = self.streams.read().await;
        streams.get(id).map(|tx| tx.subscribe())
    }
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

async fn run_codex_process(
    job_id: &str,
    prompt: &str,
    model: &str,
    work_dir: &str,
    codex_bin: &str,
    agent_type: Option<&str>,
    system_prompt: Option<&str>,
    store: Arc<JobStore>,
    streams: Arc<RwLock<HashMap<String, broadcast::Sender<String>>>>,
    cancelled: Arc<RwLock<HashSet<String>>>,
    tx: broadcast::Sender<String>,
) {
    // Ensure work_dir exists before spawning (otherwise current_dir causes ENOENT)
    if let Err(e) = std::fs::create_dir_all(work_dir) {
        let err = format!("Failed to create work_dir '{}': {}", work_dir, e);
        store.update_status(job_id, &JobStatus::Failed, Some(&err));
        tx.send(format!("[ERROR] {}\n", err)).ok();
        return;
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

    let mut cmd = Command::new(codex_bin);
    cmd.arg("--print")
        .arg("--verbose")
        .arg("--model")
        .arg(model)
        .arg("--output-format")
        .arg("stream-json")
        .arg("--dangerously-skip-permissions");

    // Personality system prompt takes priority over agent_type
    if let Some(sp) = system_prompt {
        if !sp.is_empty() {
            cmd.arg("--append-system-prompt").arg(sp);
        }
    } else if let Some(agent) = agent_type {
        cmd.arg("--agent").arg(agent);
    }

    cmd.arg(prompt)
        .env("PATH", &child_path)
        .current_dir(work_dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    // Put child in its own process group so kill(-pid) works
    #[cfg(unix)]
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let err = format!("Failed to spawn codex (bin='{}'): {} — work_dir='{}', PATH='{}'",
                codex_bin, e, work_dir, child_path);
            store.update_status(job_id, &JobStatus::Failed, Some(&err));
            tx.send(format!("[ERROR] {}\n", err)).ok();
            return;
        }
    };

    let pid = child.id().unwrap_or(0);
    store.update_pid(job_id, pid);

    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();

    let mut stdout_reader = BufReader::new(stdout).lines();
    let mut stderr_reader = BufReader::new(stderr).lines();

    let store_out = Arc::clone(&store);
    let tx_out = tx.clone();
    let id_out = job_id.to_string();

    let stdout_task = tokio::spawn(async move {
        let mut parser = StreamParser::new();
        while let Ok(Some(line)) = stdout_reader.next_line().await {
            if let Some(text) = parser.parse(&line) {
                if !text.is_empty() {
                    store_out.append_output(&id_out, &text);
                    tx_out.send(text).ok();
                }
            }
        }
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

    tx.send("[DONE]\n".to_string()).ok();

    // Clean up
    let mut s = streams.write().await;
    s.remove(job_id);
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

fn which_codex() -> Option<String> {
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
        .arg("codex")
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
        "/opt/homebrew/bin/codex".to_string(),
        "/usr/local/bin/codex".to_string(),
        format!("{}/.local/bin/codex", home),
        format!("{}/.npm-global/bin/codex", home),
    ];
    for c in &candidates {
        if std::path::Path::new(c).exists() {
            return Some(c.clone());
        }
    }

    None
}
