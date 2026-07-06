use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::auth;
use crate::jobs::StartJob;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        // Mod-protocol info root — a null/GET call returns module info.
        .route("/", get(info))
        .route("/health", get(health))
        .route("/info", get(info))
        // Models
        .route("/models", get(models))
        // Dataset preview (no training) — scans a dir and reports corpus stats.
        .route("/dataset/preview", post(dataset_preview))
        // Training jobs
        .route("/train", post(train_start))
        .route("/runs", get(runs_list))
        .route("/runs/:id", get(run_get).delete(run_delete))
        .route("/runs/:id/logs", get(run_logs))
        .route("/runs/:id/stop", post(run_stop))
        // Inference (warm worker pool)
        .route("/infer", post(infer))
        .route("/workers", get(workers).delete(workers_evict))
        // Efficiency: system metrics + per-task benchmark
        .route("/metrics", get(metrics))
        .route("/bench", post(bench))
}

// ── Helpers ───────────────────────────────────────────────────────────────────
fn err(code: StatusCode, msg: impl ToString) -> axum::response::Response {
    (code, Json(json!({"error": msg.to_string()}))).into_response()
}

fn token_of(h: &HeaderMap) -> Option<&str> {
    h.get("token").or_else(|| h.get("Token")).and_then(|v| v.to_str().ok())
}

/// Owner/BlocTime-holder gate for mutating endpoints (no-op unless an owner
/// is configured).
async fn guard(h: &HeaderMap) -> Result<(), axum::response::Response> {
    auth::require_owner(token_of(h))
        .await
        .map_err(|e| err(StatusCode::UNAUTHORIZED, e))
}

// ── Info / health ─────────────────────────────────────────────────────────────
async fn health() -> impl IntoResponse {
    Json(json!({"ok": true, "service": "freetune-api"}))
}

async fn info(State(s): State<AppState>) -> impl IntoResponse {
    Json(json!({
        "name": "freetune",
        "description": "CPU LoRA finetuning of Qwen over a directory of code, with an efficiency dashboard.",
        "endpoints": [
            "GET /models", "POST /dataset/preview", "POST /train",
            "GET /runs", "GET /runs/:id", "POST /runs/:id/stop", "GET /runs/:id/logs",
            "POST /infer", "GET /workers", "GET /metrics", "POST /bench",
        ],
        "models": &*s.models,
        "auth": if auth::owner_address().is_some() { "owner-gated" } else { "open" },
        "owner": auth::owner_address(),
        "authorized_via": ["owner", "bloctime_holder"],
    }))
}

async fn models(State(s): State<AppState>) -> impl IntoResponse {
    Json((*s.models).clone())
}

// ── Dataset preview ────────────────────────────────────────────────────────────
#[derive(Deserialize)]
struct PreviewReq {
    src: String,
}

async fn dataset_preview(
    State(s): State<AppState>,
    Json(req): Json<PreviewReq>,
) -> axum::response::Response {
    let dir = s.trainer_dir.clone();
    let out = tokio::task::spawn_blocking(move || {
        std::process::Command::new(crate::trainer_python())
            .arg("-m").arg("trainer.dataset")
            .arg("--src").arg(&req.src)
            .arg("--stats")
            .current_dir(&dir)
            .output()
    })
    .await;
    match out {
        Ok(Ok(o)) if o.status.success() => {
            match serde_json::from_slice::<Value>(&o.stdout) {
                Ok(v) => Json(v).into_response(),
                Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "bad dataset output"),
            }
        }
        Ok(Ok(o)) => err(
            StatusCode::BAD_REQUEST,
            String::from_utf8_lossy(&o.stderr).trim().to_string(),
        ),
        _ => err(StatusCode::INTERNAL_SERVER_ERROR, "failed to run dataset scan"),
    }
}

// ── Training ───────────────────────────────────────────────────────────────────
async fn train_start(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(job): Json<StartJob>,
) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    match s.jobs.start(job) {
        Ok(id) => Json(json!({"id": id, "status": "started"})).into_response(),
        Err(e) => err(StatusCode::BAD_REQUEST, e),
    }
}

async fn runs_list(State(s): State<AppState>) -> impl IntoResponse {
    Json(json!({"runs": s.jobs.list()}))
}

async fn run_get(State(s): State<AppState>, Path(id): Path<String>) -> axum::response::Response {
    match s.jobs.progress(&id) {
        Some(p) => Json(p).into_response(),
        None => err(StatusCode::NOT_FOUND, "no such run"),
    }
}

#[derive(Deserialize)]
struct LogQuery {
    #[serde(default = "default_tail")]
    tail: usize,
}
fn default_tail() -> usize {
    200
}

async fn run_logs(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<LogQuery>,
) -> impl IntoResponse {
    Json(json!({"id": id, "logs": s.jobs.logs(&id, q.tail)}))
}

async fn run_stop(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    Json(json!({"stopped": s.jobs.stop(&id)})).into_response()
}

async fn run_delete(
    State(s): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    Json(json!({"deleted": s.jobs.delete(&id)})).into_response()
}

// ── Inference ──────────────────────────────────────────────────────────────────
#[derive(Deserialize)]
struct InferReq {
    prompt: String,
    /// Either a base model id OR a run id whose adapter to load on top of its base.
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    run_id: Option<String>,
    #[serde(default = "d_max")]
    max_new_tokens: u32,
    #[serde(default = "d_temp")]
    temperature: f64,
    #[serde(default = "d_threads")]
    threads: u32,
}
fn d_max() -> u32 {
    200
}
fn d_temp() -> f64 {
    0.2
}
fn d_threads() -> u32 {
    4
}

async fn infer(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<InferReq>,
) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    // Resolve (model, adapter): a run_id loads that run's finetuned adapter on
    // its base model; otherwise use the given base model directly.
    let (model, adapter) = if let Some(rid) = &req.run_id {
        let Some(adapter) = s.jobs.adapter_dir(rid) else {
            return err(StatusCode::BAD_REQUEST, "run has no adapter yet");
        };
        // base model = the run's config model
        let base = s
            .jobs
            .list()
            .into_iter()
            .find(|r| &r.id == rid)
            .map(|r| r.model)
            .unwrap_or_default();
        if base.is_empty() {
            return err(StatusCode::BAD_REQUEST, "unknown base model for run");
        }
        (base, Some(adapter))
    } else if let Some(m) = req.model.clone() {
        (m, None)
    } else {
        return err(StatusCode::BAD_REQUEST, "provide model or run_id");
    };

    let pool = s.infer.clone();
    let res = tokio::task::spawn_blocking(move || {
        pool.generate(
            &model,
            adapter.as_deref(),
            &req.prompt,
            req.max_new_tokens,
            req.temperature,
            req.threads,
        )
    })
    .await
    .unwrap_or_else(|_| json!({"error": "inference task panicked"}));

    if res.get("error").is_some() {
        return err(StatusCode::INTERNAL_SERVER_ERROR, res["error"].as_str().unwrap_or("error"));
    }
    Json(res).into_response()
}

async fn workers(State(s): State<AppState>) -> impl IntoResponse {
    Json(s.infer.status())
}

async fn workers_evict(State(s): State<AppState>, headers: HeaderMap) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    Json(json!({"evicted": s.infer.evict_all()})).into_response()
}

// ── Efficiency ─────────────────────────────────────────────────────────────────
async fn metrics(State(s): State<AppState>) -> impl IntoResponse {
    Json(json!({
        "latest": s.metrics.latest(),
        "history": s.metrics.history(),
        "workers": s.infer.status(),
    }))
}

#[derive(Deserialize)]
struct BenchReq {
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    run_id: Option<String>,
    /// Optional custom prompts; default is a generic coding set.
    #[serde(default)]
    tasks: Option<Vec<String>>,
    #[serde(default = "d_bench_max")]
    max_new_tokens: u32,
    #[serde(default = "d_threads")]
    threads: u32,
}
fn d_bench_max() -> u32 {
    160
}

async fn bench(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<BenchReq>,
) -> axum::response::Response {
    if let Err(e) = guard(&headers).await {
        return e;
    }
    let (model, adapter) = if let Some(rid) = &req.run_id {
        let Some(adapter) = s.jobs.adapter_dir(rid) else {
            return err(StatusCode::BAD_REQUEST, "run has no adapter yet");
        };
        let base = s
            .jobs
            .list()
            .into_iter()
            .find(|r| &r.id == rid)
            .map(|r| r.model)
            .unwrap_or_default();
        (base, Some(adapter))
    } else if let Some(m) = req.model.clone() {
        (m, None)
    } else {
        return err(StatusCode::BAD_REQUEST, "provide model or run_id");
    };

    let dir = s.trainer_dir.clone();
    let res = tokio::task::spawn_blocking(move || run_bench(&dir, &model, adapter.as_deref(), &req))
        .await
        .unwrap_or_else(|_| Err("bench task panicked".to_string()));

    match res {
        Ok(v) => Json(v).into_response(),
        Err(e) => err(StatusCode::INTERNAL_SERVER_ERROR, e),
    }
}

fn run_bench(
    trainer_dir: &str,
    model: &str,
    adapter: Option<&str>,
    req: &BenchReq,
) -> Result<Value, String> {
    let mut cmd = std::process::Command::new(crate::trainer_python());
    cmd.arg("-m").arg("trainer.bench").arg("--model").arg(model);
    if let Some(a) = adapter {
        cmd.arg("--adapter").arg(a);
    }
    cmd.arg("--max-new-tokens").arg(req.max_new_tokens.to_string());
    cmd.arg("--threads").arg(req.threads.to_string());

    // Custom tasks → temp file.
    let _tmp_guard;
    if let Some(tasks) = &req.tasks {
        let path = format!("{}/.bench_tasks_{}.json", crate::state_dir(), chrono::Utc::now().timestamp_millis());
        std::fs::create_dir_all(crate::state_dir()).ok();
        std::fs::write(&path, serde_json::to_vec(tasks).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        cmd.arg("--tasks").arg(&path);
        _tmp_guard = TempFile(path);
    }

    let out = cmd
        .current_dir(trainer_dir)
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|_| "bad bench output".to_string())
}

struct TempFile(String);
impl Drop for TempFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
