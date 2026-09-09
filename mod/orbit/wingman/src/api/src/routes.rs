use axum::{
    body::Body,
    extract::{Multipart, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde_json::{json, Value};
use crate::bridge::Bridge;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::fs;


pub type AppState = Arc<AppStateInner>;

pub struct AppStateInner {
    pub bridge: Bridge,
    pub mod_dir: PathBuf,
    pub data_dir: PathBuf,
}

// ── helpers ──────────────────────────────────────────────────────────────────

fn bridge_json(result: Result<Value, String>) -> Response {
    match result {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            let body = json!({"error": e});
            (StatusCode::BAD_GATEWAY, Json(body)).into_response()
        }
    }
}

fn is_trusted(headers: &HeaderMap, token: Option<String>) -> bool {
    // Check x-wingman-token
    if let Some(t) = token {
        if let Some(hdr) = headers.get("x-wingman-token") {
            if hdr.to_str().ok() == Some(t.as_str()) {
                return true;
            }
        }
    }
    // No X-Forwarded-For → loopback trusted
    let has_fwd = headers.contains_key("x-forwarded-for") || headers.contains_key("x-real-ip");
    !has_fwd
}

// Serve a binary file from the filesystem.
async fn serve_file(path: PathBuf, content_type: &'static str, download_name: Option<String>) -> Response {
    match fs::read(&path).await {
        Ok(data) => {
            let mut headers = axum::http::response::Builder::new()
                .status(StatusCode::OK)
                .header("content-type", content_type)
                .header("cache-control", "private, max-age=60");
            if let Some(name) = download_name {
                headers = headers.header(
                    "content-disposition",
                    format!("attachment; filename=\"{name}\""),
                );
            }
            headers.body(Body::from(data)).unwrap().into_response()
        }
        Err(e) => {
            let body = json!({"error": format!("file not found: {e}")});
            (StatusCode::NOT_FOUND, Json(body)).into_response()
        }
    }
}

// ── route handlers ────────────────────────────────────────────────────────────

pub async fn get_info(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call0_async("info").await)
}

pub async fn get_health(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call0_async("health").await)
}

pub async fn get_guide(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call0_async("guide").await)
}

pub async fn get_presets(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call0_async("presets").await)
}

pub async fn get_tools(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call0_async("tools").await)
}

// GET /token — only loopback
pub async fn get_token(headers: HeaderMap, State(s): State<AppState>) -> Response {
    let token = s.bridge.read_token();
    if !is_trusted(&headers, token.clone()) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "token is only readable from loopback"})),
        )
            .into_response();
    }
    Json(json!({"token": token})).into_response()
}

pub async fn get_sets(
    headers: HeaderMap,
    State(s): State<AppState>,
) -> Response {
    let token = s.bridge.read_token();
    if !is_trusted(&headers, token) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "listing sets needs loopback or x-wingman-token"})),
        )
            .into_response();
    }
    bridge_json(s.bridge.call_async("sets", json!({})).await)
}

pub async fn post_sets(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let name = q.get("name").cloned().or_else(|| {
        body.as_ref()
            .and_then(|b| b.get("name"))
            .and_then(|v| v.as_str())
            .map(String::from)
    });
    bridge_json(s.bridge.call_async("new", json!({ "name": name })).await)
}

pub async fn get_set(
    State(s): State<AppState>,
    Path(id): Path<String>,
) -> Response {
    bridge_json(s.bridge.call_async("get", json!({ "set": id })).await)
}

pub async fn delete_set(
    State(s): State<AppState>,
    Path(id): Path<String>,
) -> Response {
    bridge_json(s.bridge.call_async("rm", json!({ "set": id })).await)
}

// POST /photos — multipart upload
pub async fn post_photos(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    mut multipart: Multipart,
) -> Response {
    let set_ref = q.get("set").cloned();
    let tmp = match tempfile::Builder::new().prefix("wingman_upload_").tempdir() {
        Ok(d) => d,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("tmpdir: {e}")})),
            )
                .into_response()
        }
    };
    let tmp_path = tmp.path().to_string_lossy().to_string();

    // Save uploaded files to temp dir
    while let Ok(Some(field)) = multipart.next_field().await {
        let file_name = field
            .file_name()
            .map(String::from)
            .unwrap_or_else(|| "upload.jpg".to_string());
        let data = match field.bytes().await {
            Ok(b) => b,
            Err(e) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"error": format!("read field: {e}")})),
                )
                    .into_response()
            }
        };
        let dest = tmp.path().join(&file_name);
        if let Err(e) = tokio::fs::write(&dest, &data).await {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("write tmp: {e}")})),
            )
                .into_response();
        }
    }

    let args = json!({
        "set": set_ref,
        "dir": tmp_path,
    });
    let result = s.bridge.call_async("add", args).await;
    drop(tmp);
    bridge_json(result)
}

// DELETE /photos/:set/:photo
pub async fn delete_photo(
    State(s): State<AppState>,
    Path((set, photo)): Path<(String, String)>,
) -> Response {
    bridge_json(s.bridge.call_async("remove", json!({ "set": set, "photo": photo })).await)
}

// GET /audit?set=&photo=&force=
pub async fn get_audit(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let set = match q.get("set") {
        Some(s) => s.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "which set? pass set=<id|name>"})),
            )
                .into_response()
        }
    };
    let args = json!({
        "set": set,
        "photo": q.get("photo"),
        "force": q.get("force").map(|v| v != "0" && v != "false").unwrap_or(false),
    });
    bridge_json(s.bridge.call_async("audit", args).await)
}

// GET /faces?set=&photo=&threshold=
pub async fn get_faces(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let set = match q.get("set") {
        Some(s) => s.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "which set? pass set=<id|name>"})),
            )
                .into_response()
        }
    };
    let photo = match q.get("photo") {
        Some(p) => p.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "faces needs photo="})),
            )
                .into_response()
        }
    };
    let threshold: Option<f64> = q.get("threshold").and_then(|v| v.parse().ok());
    let args = json!({
        "set": set,
        "photo": photo,
        "threshold": threshold,
    });
    bridge_json(s.bridge.call_async("faces", args).await)
}

// GET /lineup?set=&n=&min_score=&allow_group=
pub async fn get_lineup(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let set = match q.get("set") {
        Some(s) => s.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "which set? pass set=<id|name>"})),
            )
                .into_response()
        }
    };
    let n: Option<u32> = q.get("n").and_then(|v| v.parse().ok());
    let min_score: Option<f64> = q.get("min_score").and_then(|v| v.parse().ok());
    let allow_group = q
        .get("allow_group")
        .map(|v| v != "0" && v != "false")
        .unwrap_or(true);
    let force = q
        .get("force")
        .map(|v| v != "0" && v != "false")
        .unwrap_or(false);
    let args = json!({
        "set": set,
        "n": n,
        "min_score": min_score,
        "allow_group": allow_group,
        "force": force,
    });
    bridge_json(s.bridge.call_async("lineup", args).await)
}

// POST /render
pub async fn post_render(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let b = body.map(|j| j.0).unwrap_or(json!({}));
    let merged = merge_query_body(q, b);
    if merged.get("set").is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "which set? pass set=<id>"})),
        )
            .into_response();
    }
    bridge_json(s.bridge.call_async("render", merged).await)
}

// POST /export
pub async fn post_export(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let b = body.map(|j| j.0).unwrap_or(json!({}));
    let merged = merge_query_body(q, b);
    if merged.get("set").is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "which set? pass set=<id>"})),
        )
            .into_response();
    }
    bridge_json(s.bridge.call_async("export", merged).await)
}

// GET /img/:set/:photo  (thumbnail)
pub async fn get_thumb(
    State(s): State<AppState>,
    Path((set, photo)): Path<(String, String)>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let w = q.get("w").and_then(|v| v.parse::<u32>().ok()).unwrap_or(320);
    // Python writes JPEG bytes directly to stdout (binary)
    // Use % formatting placeholder to avoid Rust format! brace conflicts
    let mod_dir = s.mod_dir.to_string_lossy().to_string();
    let helper = format!(
        "import sys\nsys.path.insert(0, {mod_dir_q})\nimport engine as E\ndata = E.thumb({set_q}, {photo_q}, w={w})\nsys.stdout.buffer.write(data)\nsys.stdout.buffer.flush()\n",
        mod_dir_q = serde_json::to_string(&mod_dir).unwrap_or_default(),
        set_q = serde_json::to_string(&set).unwrap_or_default(),
        photo_q = serde_json::to_string(&photo).unwrap_or_default(),
        w = w,
    );
    let helper_owned = helper.clone();
    let result = tokio::task::spawn_blocking(move || run_python_raw(&helper_owned))
        .await
        .map_err(|e| format!("spawn_blocking: {e}"))
        .and_then(|r| r);
    match result {
        Ok(bytes) => axum::http::Response::builder()
            .status(200)
            .header("content-type", "image/jpeg")
            .header("cache-control", "private, max-age=3600")
            .body(Body::from(bytes))
            .unwrap()
            .into_response(),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({"error": e}))).into_response(),
    }
}

// GET /img/:set/:photo/:preset  (rendered image)
pub async fn get_rendered(
    State(s): State<AppState>,
    Path((set, photo, preset)): Path<(String, String, String)>,
) -> Response {
    // Path: ~/.mod/wingman/sets/:set/out/:photo_:preset.jpg
    let path = s
        .data_dir
        .join("sets")
        .join(&set)
        .join("out")
        .join(format!("{}_{}.jpg", photo, preset));
    serve_file(path, "image/jpeg", None).await
}

// GET /download/:set/:preset.zip
pub async fn get_download(
    State(s): State<AppState>,
    Path((set, preset_zip)): Path<(String, String)>,
) -> Response {
    let preset = if preset_zip.ends_with(".zip") {
        preset_zip[..preset_zip.len() - 4].to_string()
    } else {
        preset_zip.clone()
    };
    let path = s
        .data_dir
        .join("sets")
        .join(&set)
        .join(format!("wingman-{}.zip", preset));
    serve_file(path, "application/zip", Some(format!("wingman-{preset}.zip"))).await
}

// POST /mcp
pub async fn post_mcp(
    State(s): State<AppState>,
    body: Json<Value>,
) -> Response {
    let mod_dir = s.mod_dir.to_string_lossy().to_string();
    let helper = format!(
        "import sys, json\nsys.path.insert(0, {mod_dir_q})\nimport mcp\nreq = json.loads(sys.stdin.buffer.read())\nresult = mcp.handle(req)\nif result is None:\n    print('')\nelse:\n    print(json.dumps(result, default=str) if not isinstance(result, (bytes, str)) else (result if isinstance(result, str) else result.decode()))\nsys.stdout.flush()\n",
        mod_dir_q = serde_json::to_string(&mod_dir).unwrap_or_default(),
    );
    let payload = serde_json::to_vec(&body.0).unwrap_or_default();
    let result = tokio::task::spawn_blocking(move || run_python_inline_stdin(&helper, &payload))
        .await
        .unwrap_or_else(|e| Err(format!("spawn_blocking: {e}")));
    match result {
        Ok(v) => Json(v).into_response(),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({"error": e}))).into_response(),
    }
}

// GET /read?set=
pub async fn get_read(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let set = match q.get("set") {
        Some(s) => s.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "which set? pass set=<id>"})),
            )
                .into_response()
        }
    };
    bridge_json(s.bridge.call_async("read", json!({ "set": set, "force": false })).await)
}

// POST /read
pub async fn post_read(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let b = body.map(|j| j.0).unwrap_or(json!({}));
    let merged = merge_query_body(q, b);
    if merged.get("set").is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "which set? pass set=<id>"})),
        )
            .into_response();
    }
    bridge_json(s.bridge.call_async("read", merged).await)
}

// GET /venice
pub async fn get_venice(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call_async("venice", json!({})).await)
}

// POST /venice
pub async fn post_venice(
    headers: HeaderMap,
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let token = s.bridge.read_token();
    if !is_trusted(&headers, token) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "changing venice settings needs loopback or x-wingman-token"})),
        )
            .into_response();
    }
    let b = body.map(|j| j.0).unwrap_or(json!({}));
    let merged = merge_query_body(q, b);
    bridge_json(s.bridge.call_async("venice", merged).await)
}

// GET /venice/models
pub async fn get_venice_models(State(s): State<AppState>) -> Response {
    bridge_json(s.bridge.call_async("venice", json!({ "models": true })).await)
}

// POST /venice/key
pub async fn post_venice_key(
    headers: HeaderMap,
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
    body: Option<Json<Value>>,
) -> Response {
    let token = s.bridge.read_token();
    if !is_trusted(&headers, token) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "the venice key is set from this box only — loopback or x-wingman-token"})),
        )
            .into_response();
    }
    let b = body.map(|j| j.0).unwrap_or(json!({}));
    let merged = merge_query_body(q, b);
    let key = merged.get("key").and_then(|v| v.as_str()).unwrap_or("").to_string();
    bridge_json(s.bridge.call_async("venice_key", json!({ "key": key })).await)
}

// DELETE /venice/key
pub async fn delete_venice_key(
    headers: HeaderMap,
    State(s): State<AppState>,
) -> Response {
    let token = s.bridge.read_token();
    if !is_trusted(&headers, token) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "the venice key is set from this box only — loopback or x-wingman-token"})),
        )
            .into_response();
    }
    bridge_json(s.bridge.call_async("venice_key", json!({ "forget": true })).await)
}

// ── utility ────────────────────────────────────────────────────────────────

fn merge_query_body(q: HashMap<String, String>, b: Value) -> Value {
    let mut map = serde_json::Map::new();
    // Body wins over query
    for (k, v) in q {
        map.insert(k, Value::String(v));
    }
    if let Value::Object(bmap) = b {
        for (k, v) in bmap {
            map.insert(k, v);
        }
    }
    Value::Object(map)
}

fn run_python_inline(code: &str) -> Result<Value, String> {
    let output = std::process::Command::new("python3")
        .arg("-c")
        .arg(code)
        .output()
        .map_err(|e| format!("spawn python3: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if !output.status.success() {
        return Err(if !stderr.is_empty() {
            stderr.trim().to_string()
        } else {
            stdout.trim().to_string()
        });
    }

    let text = stdout.trim();
    if text.is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(text).map_err(|e| format!("json parse: {e}\nraw: {text}"))
}

fn run_python_inline_stdin(code: &str, stdin_data: &[u8]) -> Result<Value, String> {
    use std::io::Write;
    let mut child = std::process::Command::new("python3")
        .arg("-c")
        .arg(code)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn python3: {e}"))?;
    if let Some(mut si) = child.stdin.take() {
        si.write_all(stdin_data).ok();
    }
    let output = child
        .wait_with_output()
        .map_err(|e| format!("wait: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        return Err(if !stderr.is_empty() {
            stderr.trim().to_string()
        } else {
            stdout.trim().to_string()
        });
    }
    let text = stdout.trim();
    if text.is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(text).map_err(|e| format!("json parse: {e}\nraw: {text}"))
}

fn run_python_raw(code: &str) -> Result<Vec<u8>, String> {
    let output = std::process::Command::new("python3")
        .arg("-c")
        .arg(code)
        .output()
        .map_err(|e| format!("spawn python3: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(stderr.trim().to_string());
    }
    Ok(output.stdout)
}
