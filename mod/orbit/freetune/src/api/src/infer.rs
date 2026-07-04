//! Warm inference worker pool. Each unique (model, adapter) gets ONE persistent
//! `python3 -m trainer.infer` subprocess that loads the model once and answers
//! JSON-lines requests over its stdin/stdout. This is the main efficiency win on
//! CPU: model load (seconds) happens once, not per chat turn.
//!
//! Access to a worker is serialised by a Mutex (CPU inference is sequential
//! anyway), so request/response framing is a simple write-line / read-line —
//! no id correlation needed.

use parking_lot::Mutex;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::trainer_python;

pub struct Worker {
    child: Child,
    stdin: ChildStdin,
    reader: BufReader<ChildStdout>,
    pub model: String,
    pub adapter: Option<String>,
    pub load_s: f64,
    pub pid: u32,
    pub served: u64,
}

impl Worker {
    fn spawn(
        trainer_dir: &str,
        model: &str,
        adapter: Option<&str>,
        threads: u32,
    ) -> anyhow::Result<Self> {
        let mut cmd = Command::new(trainer_python());
        cmd.arg("-m").arg("trainer.infer").arg("--model").arg(model);
        if let Some(a) = adapter {
            cmd.arg("--adapter").arg(a);
        }
        cmd.arg("--threads").arg(threads.to_string());
        cmd.current_dir(trainer_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());

        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().expect("stdin");
        let stdout = child.stdout.take().expect("stdout");
        let pid = child.id();
        let mut reader = BufReader::new(stdout);

        // First line is the readiness banner (or an error).
        let mut line = String::new();
        reader.read_line(&mut line)?;
        let banner: Value = serde_json::from_str(line.trim())
            .map_err(|_| anyhow::anyhow!("worker sent no readiness banner"))?;
        if !banner["ready"].as_bool().unwrap_or(false) {
            let err = banner["error"].as_str().unwrap_or("model failed to load");
            anyhow::bail!("{err}");
        }
        Ok(Self {
            child,
            stdin,
            reader,
            model: model.to_string(),
            adapter: adapter.map(|s| s.to_string()),
            load_s: banner["load_s"].as_f64().unwrap_or(0.0),
            pid,
            served: 0,
        })
    }

    fn request(&mut self, prompt: &str, max_new_tokens: u32, temperature: f64) -> Value {
        let req = json!({
            "prompt": prompt, "max_new_tokens": max_new_tokens, "temperature": temperature,
        });
        if writeln!(self.stdin, "{req}").and_then(|_| self.stdin.flush()).is_err() {
            return json!({"error": "worker stdin closed"});
        }
        let mut line = String::new();
        match self.reader.read_line(&mut line) {
            Ok(0) | Err(_) => json!({"error": "worker died during generation"}),
            Ok(_) => {
                self.served += 1;
                serde_json::from_str(line.trim())
                    .unwrap_or_else(|_| json!({"error": "bad worker response"}))
            }
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Clone)]
pub struct InferPool {
    trainer_dir: String,
    workers: Arc<Mutex<HashMap<String, Arc<Mutex<Worker>>>>>,
    reqs: Arc<AtomicU64>,
}

impl InferPool {
    pub fn new(trainer_dir: String) -> Self {
        Self {
            trainer_dir,
            workers: Arc::new(Mutex::new(HashMap::new())),
            reqs: Arc::new(AtomicU64::new(0)),
        }
    }

    fn key(model: &str, adapter: Option<&str>) -> String {
        format!("{model}::{}", adapter.unwrap_or("-"))
    }

    fn ensure(
        &self,
        model: &str,
        adapter: Option<&str>,
        threads: u32,
    ) -> anyhow::Result<Arc<Mutex<Worker>>> {
        let key = Self::key(model, adapter);
        if let Some(w) = self.workers.lock().get(&key) {
            return Ok(w.clone());
        }
        let w = Arc::new(Mutex::new(Worker::spawn(
            &self.trainer_dir,
            model,
            adapter,
            threads,
        )?));
        self.workers.lock().insert(key, w.clone());
        Ok(w)
    }

    /// Blocking — call from spawn_blocking.
    pub fn generate(
        &self,
        model: &str,
        adapter: Option<&str>,
        prompt: &str,
        max_new_tokens: u32,
        temperature: f64,
        threads: u32,
    ) -> Value {
        let worker = match self.ensure(model, adapter, threads) {
            Ok(w) => w,
            Err(e) => return json!({"error": format!("load failed: {e}")}),
        };
        self.reqs.fetch_add(1, Ordering::Relaxed);
        let mut w = worker.lock();
        let mut out = w.request(prompt, max_new_tokens, temperature);
        out["worker_load_s"] = json!(w.load_s);
        out["worker_served"] = json!(w.served);
        // A dead worker is poison — drop it so the next call respawns clean.
        if out.get("error").is_some() {
            drop(w);
            self.workers.lock().remove(&Self::key(model, adapter));
        }
        out
    }

    pub fn status(&self) -> Value {
        let list: Vec<Value> = self
            .workers
            .lock()
            .values()
            .map(|w| {
                let w = w.lock();
                json!({
                    "model": w.model, "adapter": w.adapter,
                    "load_s": w.load_s, "pid": w.pid, "served": w.served,
                    "rss_mb": crate::metrics::pid_rss_mb(w.pid),
                })
            })
            .collect();
        json!({"workers": list, "total_requests": self.reqs.load(Ordering::Relaxed)})
    }

    pub fn evict_all(&self) -> usize {
        let mut map = self.workers.lock();
        let n = map.len();
        map.clear(); // Drop kills each child
        n
    }
}
