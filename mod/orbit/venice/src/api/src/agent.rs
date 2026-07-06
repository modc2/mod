//! The orchestrator: one chat turn that can call media tools.
//!
//! We run a bounded tool-calling loop against a Venice text model that
//! supports function calling. The model decides whether to answer in text or
//! to call `generate_image` / `edit_image` / `upscale_image` / `generate_video`.
//! Each tool runs against Venice's media endpoints (tools.rs), stores the
//! result, and the loop feeds the outcome back so the model can chain steps
//! (e.g. generate an image, then animate it into a video).
//!
//! Progress streams to the browser as Server-Sent Events:
//!   status   {text}             — what the agent is doing right now
//!   media    {MediaOut}         — a produced image/video to render inline
//!   message  {text}             — the final assistant text
//!   error    {error}            — a fatal error
//!   done     {media:[MediaOut]} — terminal; full media list for reconciliation

use std::convert::Infallible;

use axum::response::sse::Event;
use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;

use crate::media::MediaStore;
use crate::tools;

const VENICE_BASE: &str = "https://api.venice.ai/api/v1";
const MAX_ITERS: usize = 6;

type Tx = Sender<Result<Event, Infallible>>;

fn ev(name: &str, data: Value) -> Result<Event, Infallible> {
    Ok(Event::default().event(name).data(data.to_string()))
}

async fn status(tx: &Tx, text: &str) {
    let _ = tx.send(ev("status", json!({ "text": text }))).await;
}

/// The tool schema advertised to the model (OpenAI function-calling format).
fn tool_schema() -> Value {
    json!([
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": "Generate a brand-new image from a text prompt. Returns a media_id you can reference later (e.g. to edit, upscale, or animate it).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": { "type": "string", "description": "What to depict." },
                        "model": { "type": "string", "description": "Optional image model id." },
                        "aspect_ratio": { "type": "string", "description": "e.g. '1:1', '16:9', '9:16'." },
                        "negative_prompt": { "type": "string" }
                    },
                    "required": ["prompt"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "edit_image",
                "description": "Edit / modify an existing image by instruction. image_id must be a media_id from an attachment or a previously generated image.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_id": { "type": "string", "description": "media_id of the image to edit." },
                        "prompt": { "type": "string", "description": "The edit instruction." },
                        "model": { "type": "string" }
                    },
                    "required": ["image_id", "prompt"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "upscale_image",
                "description": "Increase the resolution of an existing image. image_id must be a known media_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_id": { "type": "string" },
                        "scale": { "type": "integer", "description": "2-4x.", "minimum": 1, "maximum": 4 },
                        "enhance": { "type": "boolean" }
                    },
                    "required": ["image_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": "Create a short video. Text-to-video from a prompt, or image-to-video if you pass image_id (a known media_id) to animate that image. Rendering takes a few minutes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": { "type": "string", "description": "Motion / scene description." },
                        "image_id": { "type": "string", "description": "Optional: animate this image." },
                        "duration": { "type": "string", "description": "'5s' or '10s'." },
                        "resolution": { "type": "string", "description": "'480p','720p','1080p'." },
                        "aspect_ratio": { "type": "string" },
                        "model": { "type": "string" }
                    },
                    "required": ["prompt"]
                }
            }
        }
    ])
}

fn system_prompt(attachments: &[String]) -> String {
    let mut s = String::from(
        "You are Venice, a multimodal assistant in a single chat window. You can reply in text \
         AND create or edit media by calling tools: generate_image, edit_image, upscale_image, \
         generate_video. Use a tool whenever the user wants an image or video made or changed; \
         otherwise just answer in text. After a tool returns a media_id, you may reference that \
         media_id in a later tool call to refine it (edit, upscale, or animate into a video). \
         Keep text replies concise and describe what you created.",
    );
    if !attachments.is_empty() {
        s.push_str("\n\nThe user attached these media for you to work with (use as image_id): ");
        s.push_str(&attachments.join(", "));
        s.push('.');
    }
    s
}

/// Drive the full turn, emitting SSE events. Consumes `messages` (the prior
/// conversation as {role, content} objects) plus the freshly-attached media.
pub async fn run(
    http: reqwest::Client,
    key: String,
    store: MediaStore,
    model: String,
    user_messages: Vec<Value>,
    attachments: Vec<String>,
    tx: Tx,
) {
    let tools = tool_schema();
    let mut messages: Vec<Value> = Vec::with_capacity(user_messages.len() + 1);
    messages.push(json!({ "role": "system", "content": system_prompt(&attachments) }));
    messages.extend(user_messages);

    let mut produced: Vec<tools::MediaOut> = Vec::new();

    for _ in 0..MAX_ITERS {
        status(&tx, "thinking…").await;
        let req = json!({
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.7,
            "stream": false,
        });
        let resp = match http
            .post(format!("{VENICE_BASE}/chat/completions"))
            .bearer_auth(&key)
            .json(&req)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => return fail(&tx, &format!("chat request failed: {e}")).await,
        };
        let v: Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => return fail(&tx, &format!("chat decode failed: {e}")).await,
        };
        if let Some(err) = v.get("error") {
            return fail(&tx, &format!("venice error: {}", err)).await;
        }
        let message = match v.get("choices").and_then(|c| c.get(0)).and_then(|c| c.get("message")) {
            Some(m) => m.clone(),
            None => return fail(&tx, "venice returned no choices").await,
        };

        let tool_calls = message
            .get("tool_calls")
            .and_then(|t| t.as_array())
            .cloned()
            .unwrap_or_default();

        if tool_calls.is_empty() {
            // Final answer.
            let content = message
                .get("content")
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .to_string();
            let _ = tx.send(ev("message", json!({ "text": content }))).await;
            return done(&tx, &produced).await;
        }

        // Record the assistant's tool-call message verbatim for context.
        messages.push(message.clone());

        for tc in tool_calls {
            let id = tc.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
            let func = tc.get("function").cloned().unwrap_or(json!({}));
            let name = func.get("name").and_then(|n| n.as_str()).unwrap_or("").to_string();
            let args: Value = func
                .get("arguments")
                .and_then(|a| a.as_str())
                .and_then(|s| serde_json::from_str(s).ok())
                .unwrap_or(json!({}));

            status(&tx, &friendly(&name, &args)).await;

            let result = run_tool(&http, &key, &store, &name, &args, &tx).await;
            let tool_content = match result {
                Ok(out) => {
                    let _ = tx.send(ev("media", serde_json::to_value(&out).unwrap_or(json!({})))).await;
                    let summary = json!({
                        "status": "ok",
                        "media_id": out.media_id,
                        "kind": out.kind,
                        "note": format!("{} produced media_id {}", name, out.media_id),
                    });
                    produced.push(out);
                    summary.to_string()
                }
                Err(e) => {
                    let _ = tx.send(ev("status", json!({ "text": format!("{name} failed: {e}") }))).await;
                    json!({ "status": "error", "error": e }).to_string()
                }
            };
            messages.push(json!({ "role": "tool", "tool_call_id": id, "content": tool_content }));
        }
    }

    // Hit the iteration cap — summarize what we have rather than hang.
    let _ = tx
        .send(ev("message", json!({ "text": "Reached the step limit for this turn." })))
        .await;
    done(&tx, &produced).await;
}

async fn run_tool(
    http: &reqwest::Client,
    key: &str,
    store: &MediaStore,
    name: &str,
    args: &Value,
    tx: &Tx,
) -> Result<tools::MediaOut, String> {
    match name {
        "generate_image" => tools::generate_image(http, key, store, args).await,
        "edit_image" => tools::edit_image(http, key, store, args).await,
        "upscale_image" => tools::upscale_image(http, key, store, args).await,
        "generate_video" => {
            let txc = tx.clone();
            tools::generate_video(http, key, store, args, move |s| {
                let _ = txc.try_send(ev("status", json!({ "text": s })));
            })
            .await
        }
        other => Err(format!("unknown tool {other}")),
    }
}

fn friendly(name: &str, args: &Value) -> String {
    let p = args.get("prompt").and_then(|v| v.as_str()).unwrap_or("");
    let short: String = p.chars().take(48).collect();
    match name {
        "generate_image" => format!("generating image: {short}…"),
        "edit_image" => format!("editing image: {short}…"),
        "upscale_image" => "upscaling image…".into(),
        "generate_video" => format!("creating video: {short}…"),
        _ => format!("running {name}…"),
    }
}

async fn fail(tx: &Tx, msg: &str) {
    let _ = tx.send(ev("error", json!({ "error": msg }))).await;
    let _ = tx.send(ev("done", json!({ "media": [] }))).await;
}

async fn done(tx: &Tx, produced: &[tools::MediaOut]) {
    let media = serde_json::to_value(produced).unwrap_or(json!([]));
    let _ = tx.send(ev("done", json!({ "media": media }))).await;
}
