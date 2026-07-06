//! Tool executors — the media-producing actions the chat orchestrator can
//! call. Each takes the resolved Venice key (BYOK or backend) plus the tool
//! arguments the model emitted, runs the corresponding Venice endpoint, stores
//! the resulting bytes in the MediaStore, and returns a `MediaOut` descriptor.

use base64::Engine;
use serde::Serialize;
use serde_json::{json, Value};

use crate::media::{kind_for, MediaStore};

const VENICE_BASE: &str = "https://api.venice.ai/api/v1";

#[derive(Serialize, Clone)]
pub struct MediaOut {
    pub media_id: String,
    pub kind: &'static str,   // "image" | "video"
    pub mime: &'static str,
    pub url: String,          // "/media/<id>" (client prefixes /api/venice)
    pub tool: &'static str,
    pub prompt: String,
}

impl MediaOut {
    fn new(store: &MediaStore, bytes: &[u8], ext: &str, tool: &'static str, prompt: String) -> Result<Self, String> {
        let id = store.save(bytes, ext).map_err(|e| format!("save media: {e}"))?;
        Ok(MediaOut {
            kind: kind_for(&id),
            mime: crate::media::mime_for(&id),
            url: format!("/media/{id}"),
            media_id: id,
            tool,
            prompt,
        })
    }
}

fn arg_str(args: &Value, key: &str) -> Option<String> {
    args.get(key).and_then(|v| v.as_str()).map(|s| s.to_string())
}

/// POST /image/generate → JSON `{ images: [base64] }`.
pub async fn generate_image(
    http: &reqwest::Client,
    key: &str,
    store: &MediaStore,
    args: &Value,
) -> Result<MediaOut, String> {
    let prompt = arg_str(args, "prompt").ok_or("generate_image needs a prompt")?;
    let model = arg_str(args, "model").unwrap_or_else(|| "venice-sd35".into());
    let mut body = json!({
        "model": model,
        "prompt": prompt,
        "format": "webp",
        "safe_mode": false,
    });
    if let Some(ar) = arg_str(args, "aspect_ratio") {
        body["aspect_ratio"] = json!(ar);
    } else {
        body["width"] = json!(args.get("width").and_then(|v| v.as_u64()).unwrap_or(1024));
        body["height"] = json!(args.get("height").and_then(|v| v.as_u64()).unwrap_or(1024));
    }
    if let Some(np) = arg_str(args, "negative_prompt") {
        body["negative_prompt"] = json!(np);
    }

    let resp = http
        .post(format!("{VENICE_BASE}/image/generate"))
        .bearer_auth(key)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("image/generate: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("read image/generate: {e}"))?;
    if !status.is_success() {
        return Err(format!("image/generate {}: {}", status, truncate(&text, 300)));
    }
    let v: Value = serde_json::from_str(&text).map_err(|e| format!("image/generate json: {e}"))?;
    let b64 = v
        .get("images")
        .and_then(|a| a.as_array())
        .and_then(|a| a.first())
        .and_then(|s| s.as_str())
        .ok_or("image/generate: no image returned")?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("decode image: {e}"))?;
    MediaOut::new(store, &bytes, "webp", "generate_image", prompt)
}

/// POST /image/edit → binary image bytes.
pub async fn edit_image(
    http: &reqwest::Client,
    key: &str,
    store: &MediaStore,
    args: &Value,
) -> Result<MediaOut, String> {
    let prompt = arg_str(args, "prompt").ok_or("edit_image needs a prompt")?;
    let image_id = arg_str(args, "image_id").ok_or("edit_image needs image_id")?;
    let (b64, _mime) = store
        .read_b64(&image_id)
        .ok_or_else(|| format!("edit_image: unknown image_id {image_id}"))?;
    let mut body = json!({
        "prompt": prompt,
        "image": b64,
        "output_format": "png",
        "safe_mode": false,
    });
    if let Some(model) = arg_str(args, "model") {
        body["model"] = json!(model);
    }
    let bytes = post_binary(http, key, "/image/edit", &body).await?;
    MediaOut::new(store, &bytes, "png", "edit_image", prompt)
}

/// POST /image/upscale → binary PNG.
pub async fn upscale_image(
    http: &reqwest::Client,
    key: &str,
    store: &MediaStore,
    args: &Value,
) -> Result<MediaOut, String> {
    let image_id = arg_str(args, "image_id").ok_or("upscale_image needs image_id")?;
    let (b64, _mime) = store
        .read_b64(&image_id)
        .ok_or_else(|| format!("upscale_image: unknown image_id {image_id}"))?;
    let scale = args.get("scale").and_then(|v| v.as_u64()).unwrap_or(2).clamp(1, 4);
    let enhance = args.get("enhance").and_then(|v| v.as_bool()).unwrap_or(false);
    let body = json!({ "image": b64, "scale": scale, "enhance": enhance });
    let bytes = post_binary(http, key, "/image/upscale", &body).await?;
    MediaOut::new(store, &bytes, "png", "upscale_image", format!("upscale x{scale}"))
}

/// Video is async: POST /video/queue → queue_id, then poll /video/retrieve
/// until it returns `video/mp4` bytes (or times out). `progress` is called
/// with human status text on each poll so the agent can surface it over SSE.
pub async fn generate_video<F: Fn(String)>(
    http: &reqwest::Client,
    key: &str,
    store: &MediaStore,
    args: &Value,
    progress: F,
) -> Result<MediaOut, String> {
    let prompt = arg_str(args, "prompt").ok_or("generate_video needs a prompt")?;
    let image_id = arg_str(args, "image_id");
    let is_i2v = image_id.is_some();
    let model = arg_str(args, "model").unwrap_or_else(|| {
        if is_i2v {
            "wan-2-7-image-to-video".into()
        } else {
            "wan-2-7-text-to-video".into()
        }
    });
    let duration = arg_str(args, "duration").unwrap_or_else(|| "5s".into());
    let resolution = arg_str(args, "resolution").unwrap_or_else(|| "720p".into());

    let mut body = json!({
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
    });
    if let Some(ar) = arg_str(args, "aspect_ratio") {
        body["aspect_ratio"] = json!(ar);
    }
    if let Some(id) = &image_id {
        let data_url = store
            .data_url(id)
            .ok_or_else(|| format!("generate_video: unknown image_id {id}"))?;
        body["image_url"] = json!(data_url);
    }

    progress("queuing video…".into());
    let queue: Value = http
        .post(format!("{VENICE_BASE}/video/queue"))
        .bearer_auth(key)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("video/queue: {e}"))?
        .json()
        .await
        .map_err(|e| format!("video/queue json: {e}"))?;
    let queue_id = queue
        .get("queue_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("video/queue: no queue_id ({})", truncate(&queue.to_string(), 200)))?
        .to_string();

    let cap_secs: u64 = std::env::var("VENICE_VIDEO_POLL_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(300);
    let started = std::time::Instant::now();
    let retrieve_body = json!({ "model": model, "queue_id": queue_id });

    loop {
        if started.elapsed().as_secs() > cap_secs {
            return Err(format!("video still rendering after {cap_secs}s (queue_id {queue_id})"));
        }
        tokio::time::sleep(std::time::Duration::from_secs(6)).await;
        let resp = http
            .post(format!("{VENICE_BASE}/video/retrieve"))
            .bearer_auth(key)
            .json(&retrieve_body)
            .send()
            .await
            .map_err(|e| format!("video/retrieve: {e}"))?;
        let status = resp.status();
        let ct = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        if ct.starts_with("video/") {
            let bytes = resp.bytes().await.map_err(|e| format!("video bytes: {e}"))?;
            return MediaOut::new(store, &bytes, "mp4", "generate_video", prompt);
        }
        // JSON: either still PROCESSING, or COMPLETED w/ download_url (private models).
        let v: Value = resp.json().await.unwrap_or_else(|_| json!({}));
        let st = v.get("status").and_then(|s| s.as_str()).unwrap_or("");
        if st.eq_ignore_ascii_case("COMPLETED") {
            if let Some(url) = v.get("download_url").and_then(|s| s.as_str()) {
                let bytes = http
                    .get(url)
                    .send()
                    .await
                    .map_err(|e| format!("download video: {e}"))?
                    .bytes()
                    .await
                    .map_err(|e| format!("download video bytes: {e}"))?;
                return MediaOut::new(store, &bytes, "mp4", "generate_video", prompt);
            }
        }
        if !status.is_success() && st.is_empty() {
            return Err(format!("video/retrieve {}: {}", status, truncate(&v.to_string(), 200)));
        }
        let secs = started.elapsed().as_secs();
        progress(format!("rendering video… {secs}s"));
    }
}

/// POST a JSON body and return the raw response bytes, surfacing a JSON error
/// body as `Err` (these media endpoints return binary on success, JSON on fail).
async fn post_binary(
    http: &reqwest::Client,
    key: &str,
    path: &str,
    body: &Value,
) -> Result<Vec<u8>, String> {
    let resp = http
        .post(format!("{VENICE_BASE}{path}"))
        .bearer_auth(key)
        .json(body)
        .send()
        .await
        .map_err(|e| format!("{path}: {e}"))?;
    let status = resp.status();
    let ct = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    if status.is_success() && !ct.contains("application/json") {
        return Ok(resp.bytes().await.map_err(|e| format!("{path} bytes: {e}"))?.to_vec());
    }
    let text = resp.text().await.unwrap_or_default();
    Err(format!("{path} {}: {}", status, truncate(&text, 300)))
}

fn truncate(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        s.chars().take(n).collect::<String>() + "…"
    }
}
