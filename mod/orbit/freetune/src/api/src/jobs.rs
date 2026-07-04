//! Training job lifecycle. A job = one `python3 -m trainer.train` subprocess
//! writing `progress.json` + `train.log` into its run dir under
//! ~/.mod/freetune/runs/<id>. The API never blocks on training: it spawns,
//! tracks the pid, and reads progress/logs on demand.

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::{state_dir, trainer_python};
use crate::metrics::pid_rss_mb;

#[derive(Deserialize)]
pub struct StartJob {
    pub src: String,
    pub model: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default = "d_epochs")]
    pub epochs: f64,
    #[serde(default = "d_lr")]
    pub learning_rate: f64,
    #[serde(default = "d_r")]
    pub lora_r: u32,
    #[serde(default = "d_alpha")]
    pub lora_alpha: u32,
    #[serde(default = "d_block")]
    pub block_size: u32,
    #[serde(default = "d_batch")]
    pub batch_size: u32,
    #[serde(default = "d_accum")]
    pub grad_accum: u32,
    #[serde(default = "d_threads")]
    pub threads: u32,
    /// 0 = use the whole corpus.
    #[serde(default)]
    pub max_blocks: u32,
}

fn d_epochs() -> f64 { 1.0 }
fn d_lr() -> f64 { 2e-4 }
fn d_r() -> u32 { 8 }
fn d_alpha() -> u32 { 16 }
fn d_block() -> u32 { 512 }
fn d_batch() -> u32 { 1 }
fn d_accum() -> u32 { 8 }
fn d_threads() -> u32 { 4 }

#[derive(Serialize)]
pub struct RunSummary {
    pub id: String,
    pub name: String,
    pub model: String,
    pub src: String,
    pub status: String,
    pub running: bool,
    pub created_at: i64,
    pub progress: Value,
    pub has_adapter: bool,
}

pub struct JobManager {
    trainer_dir: String,
    children: Arc<Mutex<std::collections::HashMap<String, Child>>>,
    seq: AtomicU64,
}

impl JobManager {
    pub fn new(trainer_dir: String) -> Self {
        fs::create_dir_all(runs_root()).ok();
        Self {
            trainer_dir,
            children: Arc::new(Mutex::new(Default::default())),
            seq: AtomicU64::new(0),
        }
    }

    pub fn start(&self, job: StartJob) -> anyhow::Result<String> {
        let src = expand(&job.src);
        if !PathBuf::from(&src).is_dir() {
            anyhow::bail!("src is not a directory: {src}");
        }
        let n = self.seq.fetch_add(1, Ordering::Relaxed);
        let id = format!("{}-{:03}", chrono::Utc::now().timestamp(), n);
        let dir = runs_root().join(&id);
        fs::create_dir_all(&dir)?;

        let name = job.name.clone().unwrap_or_else(|| {
            PathBuf::from(&src)
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_else(|| "run".into())
        });

        let cfg = json!({
            "id": id, "name": name, "model": job.model, "src": src,
            "epochs": job.epochs, "learning_rate": job.learning_rate,
            "lora_r": job.lora_r, "lora_alpha": job.lora_alpha,
            "block_size": job.block_size, "batch_size": job.batch_size,
            "grad_accum": job.grad_accum, "threads": job.threads,
            "max_blocks": job.max_blocks,
            "created_at": chrono::Utc::now().timestamp(),
        });
        fs::write(dir.join("config.json"), serde_json::to_vec_pretty(&cfg)?)?;
        fs::write(
            dir.join("progress.json"),
            serde_json::to_vec(&json!({"status": "queued"}))?,
        )?;

        let log = fs::File::create(dir.join("train.log"))?;
        let log_err = log.try_clone()?;
        let child = Command::new(trainer_python())
            .arg("-m")
            .arg("trainer.train")
            .arg("--run-dir")
            .arg(&dir)
            .current_dir(&self.trainer_dir)
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(log_err))
            .spawn()?;

        self.children.lock().insert(id.clone(), child);
        tracing::info!(run = %id, model = %job.model, "training started");
        Ok(id)
    }

    /// Reap finished children so `running` reflects reality.
    fn reap(&self) {
        let mut map = self.children.lock();
        let done: Vec<String> = map
            .iter_mut()
            .filter_map(|(id, c)| matches!(c.try_wait(), Ok(Some(_))).then(|| id.clone()))
            .collect();
        for id in done {
            map.remove(&id);
        }
    }

    pub fn is_running(&self, id: &str) -> bool {
        self.reap();
        self.children.lock().contains_key(id)
    }

    /// pid of the live training process, if any (for RSS reporting).
    pub fn pid(&self, id: &str) -> Option<u32> {
        self.children.lock().get(id).map(|c| c.id())
    }

    pub fn stop(&self, id: &str) -> bool {
        if let Some(mut child) = self.children.lock().remove(id) {
            let _ = child.kill();
            let _ = child.wait();
            // Mark it cancelled so the UI doesn't show a frozen "training".
            let p = runs_root().join(id).join("progress.json");
            if let Some(mut cur) = read_json(&p) {
                cur["status"] = json!("cancelled");
                fs::write(&p, serde_json::to_vec(&cur).unwrap_or_default()).ok();
            }
            tracing::info!(run = %id, "training stopped");
            true
        } else {
            false
        }
    }

    pub fn progress(&self, id: &str) -> Option<Value> {
        let mut p = read_json(&runs_root().join(id).join("progress.json"))?;
        let running = self.is_running(id);
        p["running"] = json!(running);
        if let Some(pid) = self.pid(id) {
            p["worker_rss_mb"] = json!(pid_rss_mb(pid));
        }
        Some(p)
    }

    pub fn logs(&self, id: &str, tail: usize) -> String {
        let path = runs_root().join(id).join("train.log");
        let Ok(s) = fs::read_to_string(&path) else {
            return String::new();
        };
        let lines: Vec<&str> = s.lines().collect();
        let start = lines.len().saturating_sub(tail);
        lines[start..].join("\n")
    }

    pub fn list(&self) -> Vec<RunSummary> {
        self.reap();
        let mut out = vec![];
        let Ok(rd) = fs::read_dir(runs_root()) else {
            return out;
        };
        for e in rd.flatten() {
            let dir = e.path();
            if !dir.is_dir() {
                continue;
            }
            let id = e.file_name().to_string_lossy().to_string();
            let cfg = read_json(&dir.join("config.json")).unwrap_or(json!({}));
            let prog = read_json(&dir.join("progress.json")).unwrap_or(json!({}));
            out.push(RunSummary {
                id: id.clone(),
                name: cfg["name"].as_str().unwrap_or("run").to_string(),
                model: cfg["model"].as_str().unwrap_or("").to_string(),
                src: cfg["src"].as_str().unwrap_or("").to_string(),
                status: prog["status"].as_str().unwrap_or("unknown").to_string(),
                running: self.children.lock().contains_key(&id),
                created_at: cfg["created_at"].as_i64().unwrap_or(0),
                progress: prog,
                has_adapter: dir.join("adapter").is_dir(),
            });
        }
        out.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        out
    }

    pub fn adapter_dir(&self, id: &str) -> Option<String> {
        let d = runs_root().join(id).join("adapter");
        d.is_dir().then(|| d.to_string_lossy().to_string())
    }

    pub fn delete(&self, id: &str) -> bool {
        self.stop(id);
        fs::remove_dir_all(runs_root().join(id)).is_ok()
    }
}

fn runs_root() -> PathBuf {
    PathBuf::from(state_dir()).join("runs")
}

fn read_json(p: &PathBuf) -> Option<Value> {
    serde_json::from_slice(&fs::read(p).ok()?).ok()
}

fn expand(p: &str) -> String {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{home}/{rest}");
        }
    }
    p.to_string()
}
