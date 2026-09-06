//! Standardized tests, pulled off the web.
//!
//! The arena grades whatever someone uploads, which makes it easy to grade
//! agents on tasks nobody else has ever run. This module is the other half:
//! fetch a published benchmark — HumanEval, MBPP, CodeContests, any dataset on
//! the HuggingFace rows API, any JSON/JSONL on the open web, or a problem page
//! scraped out of HTML — and convert it into arena tasks with the same grading
//! contract everything else here uses.
//!
//! Three ideas hold it together:
//!
//!   transport   where the bytes come from — `hf`, `json` or `html`
//!   style       how one record becomes a task — `humaneval`, `asserts`, `io`
//!               or `html`
//!   map         which field of the record feeds which part of the task
//!
//! A named source is nothing but a preset over those three, and every field of
//! a preset can be overridden per call. That is why `hf` with a map can import
//! a dataset this module has never heard of.
//!
//! Nothing is imported until asked: `bench_preview` converts and shows, and
//! only `bench_import` writes. Fetches are cached under the state directory for
//! a day, so previewing then importing hits the source once.
//!
//! Outbound fetching is a switch: `OPENARENA_BENCH=0` turns the whole thing off
//! and the tools say so. Private and loopback addresses are refused unless
//! `OPENARENA_BENCH_LOCAL=1` — an arena's importer should not be a door into
//! the network it happens to be sitting on.

use crate::arena;
use crate::store;
use serde_json::{json, Value};
use std::fs;
use std::net::{IpAddr, ToSocketAddrs};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const UA: &str = concat!("openarena-bench/", env!("CARGO_PKG_VERSION"));
/// A benchmark row can be fat (CodeContests carries hundreds of cases), a
/// benchmark file should not be a download.
const MAX_BYTES: usize = 8 * 1024 * 1024;
const CACHE_TTL: u64 = 24 * 3600;
const HF_ROWS: &str = "https://datasets-server.huggingface.co/rows";
/// The rows API refuses more than this per call, so pages are the unit.
const HF_PAGE: u64 = 100;
/// The hub itself — the half of HuggingFace that answers "which dataset?".
const HF_API: &str = "https://huggingface.co/api/datasets";
const HF_SPLITS: &str = "https://datasets-server.huggingface.co/splits";
const HF_FIRST: &str = "https://datasets-server.huggingface.co/first-rows";

// ── the catalog ──────────────────────────────────────────────────────────

pub struct Source {
    pub id: &'static str,
    pub transport: &'static str,
    pub style: &'static str,
    pub label: &'static str,
    pub home: &'static str,
    pub license: &'static str,
    pub dataset: &'static str,
    pub config: &'static str,
    pub split: &'static str,
    pub url: &'static str,
    pub language: &'static str,
    pub size: &'static str,
    pub about: &'static str,
}

const BLANK: Source = Source {
    id: "",
    transport: "hf",
    style: "humaneval",
    label: "",
    home: "",
    license: "",
    dataset: "",
    config: "default",
    split: "test",
    url: "",
    language: "python",
    size: "",
    about: "",
};

pub const SOURCES: &[Source] = &[
    Source {
        id: "humaneval",
        label: "HumanEval (OpenAI)",
        home: "https://huggingface.co/datasets/openai/openai_humaneval",
        license: "MIT",
        dataset: "openai/openai_humaneval",
        config: "openai_humaneval",
        size: "164 problems",
        about: "The standard function-completion benchmark: a signature and a docstring, \
                graded by the reference `check()` the dataset ships. Unit mode, Python.",
        ..BLANK
    },
    Source {
        id: "humanevalplus",
        label: "HumanEval+ (EvalPlus)",
        home: "https://huggingface.co/datasets/evalplus/humanevalplus",
        license: "Apache-2.0",
        dataset: "evalplus/humanevalplus",
        config: "default",
        size: "164 problems, ~80× the cases",
        about: "HumanEval with EvalPlus's much harder test inputs — the set that catches \
                solutions that only look right. Its graders import numpy, so the judge host \
                needs it installed.",
        ..BLANK
    },
    Source {
        id: "mbpp",
        transport: "json",
        style: "asserts",
        label: "MBPP, sanitized (Google Research)",
        home: "https://github.com/google-research/google-research/tree/master/mbpp",
        license: "CC-BY-4.0",
        url: "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json",
        size: "427 problems",
        about: "Mostly Basic Python Problems: one sentence of prose and three assertions. \
                Each assertion becomes its own case, so a near-miss scores partial credit.",
        ..BLANK
    },
    Source {
        id: "code_contests",
        style: "io",
        label: "CodeContests (DeepMind)",
        home: "https://huggingface.co/datasets/deepmind/code_contests",
        license: "Apache-2.0 (statements CC-BY-4.0)",
        dataset: "deepmind/code_contests",
        config: "default",
        split: "valid",
        language: "any",
        size: "117 valid / 165 test problems",
        about: "Real competitive-programming problems with public and private cases. \
                io mode, language-agnostic: the public cases are shown, the private and \
                generated ones are graded and never shown. Rows are large — import a few \
                at a time.",
        ..BLANK
    },
    Source {
        id: "hf",
        style: "humaneval",
        label: "any HuggingFace dataset (generic)",
        home: "https://huggingface.co/docs/dataset-viewer",
        license: "the dataset's own",
        size: "you choose",
        about: "Give `dataset`, `config`, `split`, a `style` and a `map`, and any public \
                dataset on the rows API imports. This is how a benchmark nobody wrote an \
                adapter for gets in.",
        ..BLANK
    },
    Source {
        id: "json",
        transport: "json",
        style: "asserts",
        label: "any JSON or JSONL URL (generic)",
        home: "",
        license: "the source's own",
        size: "you choose",
        about: "Fetch a URL that answers with a JSON array or newline-delimited JSON, then \
                map its fields onto a task. Works on a raw GitHub file, a dataset dump, or \
                anything else that publishes records.",
        ..BLANK
    },
    Source {
        id: "html",
        transport: "html",
        style: "html",
        label: "a problem page (generic scrape)",
        home: "",
        license: "the page's own — check it before you race agents on it",
        language: "any",
        size: "one task per page",
        about: "Scrape an ordinary problem page: the prose becomes the statement and the \
                <pre> blocks become sample cases, paired by the Input/Output labels around \
                them. Best-effort by construction — always preview before importing, and \
                expect the bot-blocking sites to answer 403.",
        ..BLANK
    },
];

pub fn source(id: &str) -> Option<&'static Source> {
    SOURCES.iter().find(|s| s.id == id)
}

pub fn catalog() -> Value {
    json!({
        "enabled": enabled(),
        "cache_ttl_s": CACHE_TTL,
        "note": if enabled() { "" } else { "outbound fetching is off — set OPENARENA_BENCH=1 to allow it" },
        "styles": {
            "humaneval": "record carries a prompt, an entry_point and a `check(candidate)` grader → unit mode",
            "asserts":   "record carries prose and a list of assert lines → unit mode, one case per assertion",
            "io":        "record carries a description and input/output arrays → io mode, stdin against stdout",
            "html":      "a scraped page: prose plus <pre> sample blocks → io mode"
        },
        "sources": SOURCES.iter().map(|s| json!({
            "id": s.id, "label": s.label, "transport": s.transport, "style": s.style,
            "dataset": s.dataset, "config": s.config, "split": s.split, "url": s.url,
            "language": s.language, "size": s.size, "home": s.home, "license": s.license,
            "about": s.about,
            "generic": s.dataset.is_empty() && s.url.is_empty(),
        })).collect::<Vec<_>>(),
    })
}

// ── the fetch fence ──────────────────────────────────────────────────────

pub fn enabled() -> bool {
    !matches!(
        std::env::var("OPENARENA_BENCH").unwrap_or_default().as_str(),
        "0" | "off" | "false" | "no"
    )
}

fn local_ok() -> bool {
    matches!(
        std::env::var("OPENARENA_BENCH_LOCAL").unwrap_or_default().as_str(),
        "1" | "on" | "true" | "yes"
    )
}

fn private(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            v4.is_loopback()
                || v4.is_private()
                || v4.is_link_local()
                || v4.is_broadcast()
                || v4.is_unspecified()
                || v4.octets()[0] == 0
        }
        IpAddr::V6(v6) => {
            v6.is_loopback()
                || v6.is_unspecified()
                // unique-local fc00::/7 and link-local fe80::/10
                || (v6.segments()[0] & 0xfe00) == 0xfc00
                || (v6.segments()[0] & 0xffc0) == 0xfe80
        }
    }
}

/// http(s) only, and — unless told otherwise — nothing on this machine or this
/// network. The arena already runs other people's code; it should not also be a
/// way to ask its host what it can reach.
fn check_url(url: &str) -> Result<(), String> {
    if !enabled() {
        return Err("bench fetching is off — set OPENARENA_BENCH=1 to allow this arena to fetch from the web".into());
    }
    let rest = match url.split_once("://") {
        Some(("http", r)) | Some(("https", r)) => r,
        _ => return Err(format!("`{url}` is not an http(s) url")),
    };
    let hostport = rest.split(['/', '?', '#']).next().unwrap_or("");
    let hostport = hostport.rsplit('@').next().unwrap_or(hostport);
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) if p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() => {
            (h, p.parse().unwrap_or(443u16))
        }
        _ => (hostport, if url.starts_with("https") { 443 } else { 80 }),
    };
    let host = host.trim_matches(['[', ']']);
    if host.is_empty() {
        return Err(format!("`{url}` has no host"));
    }
    if local_ok() {
        return Ok(());
    }
    let addrs: Vec<IpAddr> = (host, port)
        .to_socket_addrs()
        .map_err(|e| format!("cannot resolve `{host}`: {e}"))?
        .map(|a| a.ip())
        .collect();
    if addrs.is_empty() {
        return Err(format!("`{host}` resolves to nothing"));
    }
    if addrs.iter().any(private) {
        return Err(format!(
            "`{host}` resolves inside this network — set OPENARENA_BENCH_LOCAL=1 if that is what you meant"
        ));
    }
    Ok(())
}

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .user_agent(UA)
            .timeout(Duration::from_secs(60))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// FNV-1a — a cache filename, not a hash anyone should trust with anything.
fn digest(s: &str) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in s.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("{h:016x}")
}

fn cache_dir() -> std::path::PathBuf {
    store::state_dir().join("bench-cache")
}

fn cached(url: &str) -> Option<String> {
    let f = cache_dir().join(format!("{}.body", digest(url)));
    let age = fs::metadata(&f)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| now().saturating_sub(d.as_secs()))?;
    if age > CACHE_TTL {
        return None;
    }
    fs::read_to_string(&f).ok()
}

fn cache_put(url: &str, body: &str) {
    let dir = cache_dir();
    if fs::create_dir_all(&dir).is_ok() {
        let _ = fs::write(dir.join(format!("{}.body", digest(url))), body);
    }
}

/// A HuggingFace read token, if this host has one: `HF_TOKEN`, the name the
/// hub's own tools use, or `~/.mod/openarena/hf.json` — the fleet keeps private
/// credentials off the committed config, so this reads state, never config.
/// Without one the arena still sees every public dataset; with one it also sees
/// the gated ones the caller's account has accepted.
fn hf_token() -> Option<String> {
    for k in ["HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"] {
        if let Ok(v) = std::env::var(k) {
            let v = v.trim().to_string();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    let doc: Value = fs::read_to_string(store::state_dir().join("hf.json"))
        .ok()
        .and_then(|b| serde_json::from_str(&b).ok())?;
    let t = doc
        .get("token")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .trim()
        .to_string();
    (!t.is_empty()).then_some(t)
}

/// The token goes to HuggingFace and nowhere else — `bench_import` will fetch
/// any url a caller names, and a credential must not ride along to it.
fn hf_host(url: &str) -> bool {
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or(url);
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    host == "huggingface.co" || host.ends_with(".huggingface.co")
}

async fn fetch(url: &str, refresh: bool) -> Result<String, String> {
    check_url(url)?;
    if !refresh {
        if let Some(body) = cached(url) {
            return Ok(body);
        }
    }
    let mut req = client()
        .get(url)
        .header("accept", "application/json, text/html;q=0.9, */*;q=0.8");
    if hf_host(url) {
        if let Some(t) = hf_token() {
            req = req.bearer_auth(t);
        }
    }
    let resp = req
        .send()
        .await
        .map_err(|e| format!("fetch {url}: {e}"))?;
    let status = resp.status();
    if let Some(len) = resp.content_length() {
        if len as usize > MAX_BYTES {
            return Err(format!(
                "{url} is {len} bytes — over the {MAX_BYTES} byte cap; narrow the request"
            ));
        }
    }
    let body = resp.text().await.map_err(|e| format!("read {url}: {e}"))?;
    if !status.is_success() {
        let head: String = body.chars().take(200).collect();
        return Err(format!("{url} answered {status}: {head}"));
    }
    if body.len() > MAX_BYTES {
        return Err(format!(
            "{url} returned {} bytes — over the {MAX_BYTES} byte cap",
            body.len()
        ));
    }
    cache_put(url, &body);
    Ok(body)
}

// ── the request ──────────────────────────────────────────────────────────

fn s(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

fn n(v: &Value, key: &str, default: u64) -> u64 {
    v.get(key)
        .and_then(|x| x.as_u64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(default)
}

fn flag(v: &Value, key: &str, default: bool) -> bool {
    match v.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => matches!(s.as_str(), "1" | "true" | "on" | "yes"),
        Some(Value::Number(x)) => x.as_i64().unwrap_or(0) != 0,
        _ => default,
    }
}

/// Percent-encode a query value — dataset ids carry a `/`.
fn enc(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    for b in v.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Which field feeds which part of a task, per style. Every entry can be a
/// string or a list of candidate paths — datasets disagree about names
/// (`prompt` here, `text` there) more often than about content.
fn default_map(style: &str) -> Value {
    match style {
        "asserts" => json!({
            "title": ["task_id", "name"],
            "statement": ["prompt", "text", "description"],
            "asserts": ["test_list", "tests", "asserts"],
            "imports": ["test_imports", "imports"],
        }),
        "io" => json!({
            "title": ["name", "title", "task_id"],
            "statement": ["description", "problem", "statement"],
            "inputs": ["public_tests.input", "inputs"],
            "outputs": ["public_tests.output", "outputs"],
            "hidden_inputs": ["private_tests.input", "generated_tests.input"],
            "hidden_outputs": ["private_tests.output", "generated_tests.output"],
        }),
        "html" => json!({}),
        // humaneval
        _ => json!({
            "title": ["task_id", "name"],
            "statement": ["prompt", "text"],
            "starter": ["prompt"],
            "entry_point": ["entry_point", "entrypoint"],
            "program": ["test", "check"],
        }),
    }
}

struct Spec {
    source: String,
    transport: String,
    style: String,
    url: String,
    dataset: String,
    config: String,
    split: String,
    language: String,
    author: String,
    map: Value,
    limit: usize,
    offset: usize,
    hide_after: Option<usize>,
    max_cases: usize,
    timeout_ms: u64,
    slug_prefix: String,
    title: String,
    tags: Vec<String>,
    refresh: bool,
    split_asserts: bool,
}

fn spec(args: &Value) -> Result<Spec, String> {
    let id = match s(args, "source").as_str() {
        "" => "humaneval".to_string(),
        v => v.to_lowercase(),
    };
    let src = source(&id).ok_or_else(|| {
        format!(
            "unknown source `{id}` — this arena knows {:?}",
            SOURCES.iter().map(|s| s.id).collect::<Vec<_>>()
        )
    })?;

    let pick = |key: &str, fallback: &str| match s(args, key).as_str() {
        "" => fallback.to_string(),
        v => v.to_string(),
    };
    let style = pick("style", src.style);
    if !matches!(style.as_str(), "humaneval" | "asserts" | "io" | "html") {
        return Err(format!(
            "unknown style `{style}` — expected humaneval, asserts, io or html"
        ));
    }
    let transport = pick("transport", src.transport);
    let url = pick("url", src.url);
    let dataset = pick("dataset", src.dataset);
    if transport != "hf" && url.is_empty() {
        return Err(format!("source `{id}` needs a `url` to fetch"));
    }
    if transport == "hf" && dataset.is_empty() {
        return Err(format!(
            "source `{id}` needs a `dataset` — e.g. dataset=openai/openai_humaneval"
        ));
    }

    // A caller's map is merged over the style's, not swapped for it: overriding
    // one field should not cost you the other five.
    let mut map = default_map(&style);
    if let Some(over) = args.get("map").and_then(|m| m.as_object()) {
        for (k, v) in over {
            map[k] = v.clone();
        }
    }

    let tags: Vec<String> = match args.get("tags") {
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect(),
        Some(Value::String(s)) => s
            .split(',')
            .map(|x| x.trim().to_string())
            .filter(|x| !x.is_empty())
            .collect(),
        _ => vec![],
    };

    Ok(Spec {
        source: id.clone(),
        transport,
        style,
        url,
        dataset,
        config: pick("config", src.config),
        split: pick("split", src.split),
        language: pick("language", src.language),
        author: pick("author", if src.home.is_empty() { &id } else { src.home }),
        map,
        limit: n(args, "limit", 10).clamp(1, 200) as usize,
        offset: n(args, "offset", 0) as usize,
        hide_after: args
            .get("hide_after")
            .and_then(|v| v.as_u64())
            .map(|v| v as usize),
        max_cases: n(args, "max_cases", 12).clamp(1, 64) as usize,
        timeout_ms: n(args, "timeout_ms", 10_000).clamp(1_000, 120_000),
        slug_prefix: match s(args, "slug_prefix").as_str() {
            "" => id,
            v => v.to_string(),
        },
        title: s(args, "title"),
        tags,
        refresh: flag(args, "refresh", false),
        split_asserts: flag(args, "split_asserts", true),
    })
}

// ── records ──────────────────────────────────────────────────────────────

/// `a.b` walks into nested objects; a bare name is a plain field.
fn at<'a>(rec: &'a Value, path: &str) -> Option<&'a Value> {
    let mut cur = rec;
    for part in path.split('.') {
        cur = cur.get(part)?;
    }
    Some(cur)
}

/// The first candidate path that holds something.
fn field<'a>(rec: &'a Value, map: &Value, key: &str) -> Option<&'a Value> {
    let paths: Vec<String> = match map.get(key) {
        Some(Value::String(p)) => vec![p.clone()],
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect(),
        _ => vec![],
    };
    for p in paths {
        if let Some(v) = at(rec, &p) {
            let empty = match v {
                Value::Null => true,
                Value::String(s) => s.trim().is_empty(),
                Value::Array(a) => a.is_empty(),
                _ => false,
            };
            if !empty {
                return Some(v);
            }
        }
    }
    None
}

fn text(rec: &Value, map: &Value, key: &str) -> String {
    match field(rec, map, key) {
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string().trim_matches('"').to_string(),
        None => String::new(),
    }
}

fn strings(rec: &Value, map: &Value, key: &str) -> Vec<String> {
    let mut out = vec![];
    let paths: Vec<String> = match map.get(key) {
        Some(Value::String(p)) => vec![p.clone()],
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect(),
        _ => vec![],
    };
    // Every path contributes here, not just the first: a benchmark's hidden
    // cases are often split across two fields and both of them count.
    for p in paths {
        if let Some(Value::Array(a)) = at(rec, &p) {
            out.extend(a.iter().filter_map(|v| match v {
                Value::String(s) => Some(s.clone()),
                other => Some(other.to_string()),
            }));
        }
    }
    out
}

async fn records(sp: &Spec) -> Result<(Vec<Value>, String), String> {
    match sp.transport.as_str() {
        "hf" => {
            let mut rows = vec![];
            let mut first = String::new();
            let mut taken = 0usize;
            while taken < sp.limit {
                let want = (sp.limit - taken).min(HF_PAGE as usize);
                let url = format!(
                    "{HF_ROWS}?dataset={}&config={}&split={}&offset={}&length={}",
                    enc(&sp.dataset),
                    enc(&sp.config),
                    enc(&sp.split),
                    sp.offset + taken,
                    want
                );
                if first.is_empty() {
                    first = url.clone();
                }
                let body = fetch(&url, sp.refresh).await?;
                let doc: Value = serde_json::from_str(&body)
                    .map_err(|e| format!("rows api did not answer json: {e}"))?;
                if let Some(err) = doc.get("error").and_then(|e| e.as_str()) {
                    return Err(format!("rows api: {err}"));
                }
                let page: Vec<Value> = doc
                    .get("rows")
                    .and_then(|r| r.as_array())
                    .map(|a| {
                        a.iter()
                            .map(|r| r.get("row").cloned().unwrap_or_else(|| r.clone()))
                            .collect()
                    })
                    .unwrap_or_default();
                let got = page.len();
                rows.extend(page);
                taken += got;
                if got < want {
                    break;
                }
            }
            Ok((rows, first))
        }
        "json" => {
            let body = fetch(&sp.url, sp.refresh).await?;
            let all: Vec<Value> = match serde_json::from_str::<Value>(&body) {
                Ok(Value::Array(a)) => a,
                Ok(Value::Object(o)) => {
                    // A wrapper like {"rows": [...]} or {"data": [...]}.
                    ["rows", "data", "problems", "tasks", "items"]
                        .iter()
                        .find_map(|k| o.get(*k).and_then(|v| v.as_array()).cloned())
                        .ok_or("the url returned an object with no array of records in it")?
                }
                _ => body
                    .lines()
                    .filter(|l| !l.trim().is_empty())
                    .filter_map(|l| serde_json::from_str(l).ok())
                    .collect(),
            };
            if all.is_empty() {
                return Err(format!("{} held no records", sp.url));
            }
            Ok((
                all.into_iter().skip(sp.offset).take(sp.limit).collect(),
                sp.url.clone(),
            ))
        }
        "html" => {
            let body = fetch(&sp.url, sp.refresh).await?;
            Ok((vec![scrape(&body, &sp.url)], sp.url.clone()))
        }
        other => Err(format!(
            "unknown transport `{other}` — expected hf, json or html"
        )),
    }
}

// ── scraping a page ──────────────────────────────────────────────────────

/// The nearest char boundary at or below `i`. Pages off the open web are not
/// ASCII, and slicing one mid-character panics — this is why every window into
/// raw HTML below goes through here.
fn floor_char(s: &str, i: usize) -> usize {
    let mut i = i.min(s.len());
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

fn entities(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        rest = &rest[i..];
        let end = match rest[..floor_char(rest, 12)].find(';') {
            Some(e) => e,
            None => {
                out.push('&');
                rest = &rest[1..];
                continue;
            }
        };
        let name = &rest[1..end];
        let sub = match name {
            "amp" => "&".to_string(),
            "lt" => "<".to_string(),
            "gt" => ">".to_string(),
            "quot" => "\"".to_string(),
            "apos" | "#39" => "'".to_string(),
            "nbsp" => " ".to_string(),
            "mdash" => "—".to_string(),
            "ndash" => "–".to_string(),
            "le" => "≤".to_string(),
            "ge" => "≥".to_string(),
            "times" => "×".to_string(),
            d if d.starts_with('#') => d
                .trim_start_matches("#x")
                .trim_start_matches('#')
                .parse::<u32>()
                .ok()
                .or_else(|| u32::from_str_radix(d.trim_start_matches("#x"), 16).ok())
                .and_then(char::from_u32)
                .map(String::from)
                .unwrap_or_else(|| rest[..=end].to_string()),
            _ => rest[..=end].to_string(),
        };
        out.push_str(&sub);
        rest = &rest[end + 1..];
    }
    out.push_str(rest);
    out
}

/// Drop script/style bodies, turn block tags into newlines and everything else
/// into nothing. Not a parser — a reader.
fn strip_tags(html: &str) -> String {
    let mut out = String::with_capacity(html.len() / 2);
    let mut rest = html;
    while let Some(i) = rest.find('<') {
        out.push_str(&rest[..i]);
        rest = &rest[i..];
        let lower = rest[..floor_char(rest, 16)].to_ascii_lowercase();
        let skip = ["<script", "<style", "<noscript", "<svg"]
            .iter()
            .find(|t| lower.starts_with(**t));
        if let Some(tag) = skip {
            let close = format!("</{}", &tag[1..]);
            match rest.to_ascii_lowercase().find(&close) {
                Some(e) => rest = &rest[e..],
                None => {
                    rest = "";
                    break;
                }
            }
        }
        let end = match rest.find('>') {
            Some(e) => e,
            None => break,
        };
        let tag = rest[..end].to_ascii_lowercase();
        if ["<p", "<br", "<div", "<li", "<tr", "<h1", "<h2", "<h3", "</p", "</div", "</li", "</h"]
            .iter()
            .any(|t| tag.starts_with(t))
        {
            out.push('\n');
        }
        rest = &rest[end + 1..];
    }
    out.push_str(rest);
    // Collapse the blank-line drifts that dropping markup leaves behind.
    let mut lines: Vec<String> = vec![];
    for line in entities(&out).lines() {
        let t = line.trim().to_string();
        if t.is_empty() && lines.last().map(|l: &String| l.is_empty()).unwrap_or(true) {
            continue;
        }
        lines.push(t);
    }
    lines.join("\n").trim().to_string()
}

/// Every <pre> on the page with the ~160 characters of text that led into it —
/// which is where the Input/Output label almost always is.
fn pre_blocks(html: &str) -> Vec<(String, String)> {
    let lower = html.to_ascii_lowercase();
    let mut out = vec![];
    let mut from = 0usize;
    let mut prev_end = 0usize;
    while let Some(rel) = lower[from..].find("<pre") {
        let open = from + rel;
        let body_start = match html[open..].find('>') {
            Some(e) => open + e + 1,
            None => break,
        };
        let end = match lower[body_start..].find("</pre") {
            Some(e) => body_start + e,
            None => break,
        };
        // Stop the window at the previous block: without that, one sample's
        // label bleeds onto the next and every pairing after it is wrong.
        let label_from = floor_char(html, open.saturating_sub(400)).max(prev_end);
        let label = strip_tags(&html[label_from..open]);
        let label: String = label.chars().rev().take(160).collect::<String>()
            .chars().rev().collect::<String>()
            .to_lowercase();
        // The tag's own attributes count too: judge sites label the block with
        // class="input" more often than with prose.
        let attrs = html[open..body_start].to_lowercase();
        let body = entities(&strip_tags(&html[body_start..end]));
        out.push((format!("{label} {attrs}"), body));
        from = end + 5;
        prev_end = from;
    }
    out
}

/// The text inside the first `<tag>…</tag>` on the page.
fn tag_text(html: &str, tag: &str) -> Option<String> {
    let lower = html.to_ascii_lowercase();
    let open = lower.find(&format!("<{tag}"))?;
    let body = html[open..].find('>').map(|e| open + e + 1)?;
    let close = lower[body..].find(&format!("</{tag}"))? + body;
    let t = strip_tags(&html[body..close]).trim().to_string();
    (!t.is_empty()).then_some(t)
}

/// The part of the page that is the problem, best effort. `<main>` and
/// `<article>` say so outright; failing that the prose is cut at the heading,
/// which drops the site's navigation off the front of the statement.
fn content(html: &str, _title: &str) -> String {
    let lower = html.to_ascii_lowercase();
    // The <head> is metadata that reads like prose once the tags are gone.
    let body = match lower.find("</head") {
        Some(i) => &html[i..],
        None => html,
    };
    for tag in ["main", "article"] {
        let lower = body.to_ascii_lowercase();
        if let Some(open) = lower.find(&format!("<{tag}")) {
            if let Some(close) = lower.rfind(&format!("</{tag}")) {
                if close > open {
                    let inner = strip_tags(&body[open..close]);
                    if inner.chars().count() > 200 {
                        return trim_nav(&inner);
                    }
                }
            }
        }
    }
    trim_nav(&strip_tags(body))
}

/// A site's navigation is a run of short lines; a problem statement is
/// sentences. Cut everything above the first real sentence, keeping the line
/// over it — which is usually the problem's own heading.
fn trim_nav(text: &str) -> String {
    let lines: Vec<&str> = text.lines().collect();
    match lines.iter().position(|l| l.chars().count() >= 60) {
        Some(i) if i > 0 => lines[i - 1..].join("\n"),
        _ => text.to_string(),
    }
}

/// A page → one io-mode record. Sample pairs come from the Input/Output labels
/// when the page has them and from adjacency when it does not.
fn scrape(html: &str, url: &str) -> Value {
    let title = ["h1", "title"]
        .iter()
        .find_map(|t| tag_text(html, t))
        .unwrap_or_else(|| {
            url.trim_end_matches('/')
                .rsplit('/')
                .next()
                .unwrap_or("scraped task")
                .to_string()
        });

    let blocks = pre_blocks(html);
    let mut inputs: Vec<String> = vec![];
    let mut outputs: Vec<String> = vec![];
    // "Sample Input" beats a bare "Input", which on a lot of judges labels the
    // format description rather than an example.
    let pairs = |ins: Vec<&(String, String)>, outs: Vec<&(String, String)>| {
        (!ins.is_empty() && ins.len() == outs.len()).then(|| {
            ins.iter()
                .zip(outs.iter())
                .map(|(i, o)| (i.1.clone(), o.1.clone()))
                .collect::<Vec<_>>()
        })
    };
    let by = |word: &str, not: &str| -> Vec<&(String, String)> {
        blocks
            .iter()
            .filter(|(l, _)| l.contains(word) && (not.is_empty() || !l.contains(not)))
            .collect()
    };
    let found = pairs(by("sample input", ""), by("sample output", ""))
        .or_else(|| pairs(by("入力例", ""), by("出力例", "")))
        .or_else(|| pairs(by("input", ""), by("output", "input")));
    if let Some(found) = found {
        for (i, o) in found {
            inputs.push(i);
            outputs.push(o);
        }
    } else {
        // No usable labels: take the blocks two at a time, in order.
        for pair in blocks.chunks(2) {
            if pair.len() == 2 {
                inputs.push(pair[0].1.clone());
                outputs.push(pair[1].1.clone());
            }
        }
    }

    json!({
        "name": title,
        "description": content(html, &title).chars().take(12_000).collect::<String>(),
        "inputs": inputs,
        "outputs": outputs,
        "source_url": url,
    })
}

// ── record → task ────────────────────────────────────────────────────────

fn slugify(s: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            dash = false;
        } else if !dash && !out.is_empty() {
            out.push('-');
            dash = true;
        }
    }
    out.trim_matches('-').chars().take(60).collect()
}

fn short(s: &str, n: usize) -> String {
    let one: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if one.chars().count() <= n {
        return one;
    }
    one.chars().take(n).collect::<String>().trim_end().to_string()
}

/// The contract every imported unit task states out loud, because the arena's
/// grader imports `solution.py` and a competitor that writes a script instead
/// of a function would otherwise fail for a reason nobody told it about.
const UNIT_CONTRACT: &str = "Your program is saved as `solution.py` and imported by the grader. \
Define the function at module level and return its value — do not read stdin and do not print the answer.";

fn balanced(line: &str) -> bool {
    let (mut p, mut b, mut c) = (0i32, 0i32, 0i32);
    let mut quote: Option<char> = None;
    let mut prev = '\0';
    for ch in line.chars() {
        if let Some(q) = quote {
            if ch == q && prev != '\\' {
                quote = None;
            }
        } else {
            match ch {
                '\'' | '"' => quote = Some(ch),
                '(' => p += 1,
                ')' => p -= 1,
                '[' => b += 1,
                ']' => b -= 1,
                '{' => c += 1,
                '}' => c -= 1,
                _ => {}
            }
        }
        prev = ch;
    }
    quote.is_none() && p == 0 && b == 0 && c == 0 && !line.ends_with('\\')
}

/// A `check(candidate)` grader whose body is nothing but assertions can be cut
/// into one case per assertion, which turns an all-or-nothing benchmark into a
/// score. Anything with a loop, a variable or a helper call stays whole —
/// splitting that would change what it grades.
fn split_check(test: &str) -> Option<(String, Vec<String>)> {
    let mut prelude = String::new();
    let mut body = vec![];
    let mut inside = false;
    for line in test.lines() {
        if !inside {
            if line.trim_start().starts_with("def check(") {
                inside = true;
            } else {
                prelude.push_str(line);
                prelude.push('\n');
            }
            continue;
        }
        if line.trim().is_empty() {
            continue;
        }
        let indent = line.len() - line.trim_start().len();
        if indent == 0 {
            break;
        }
        body.push(line.trim().to_string());
    }
    if body.len() < 2 || !body.iter().all(|l| l.starts_with("assert ") && balanced(l)) {
        return None;
    }
    Some((prelude.trim_end().to_string(), body))
}

fn case(name: &str, program: &str, hidden: bool) -> Value {
    json!({ "name": name, "program": program, "hidden": hidden })
}

fn io_case(name: &str, stdin: &str, expect: &str, hidden: bool) -> Value {
    json!({ "name": name, "stdin": stdin, "expect": expect, "compare": "trim", "hidden": hidden })
}

fn to_task(sp: &Spec, rec: &Value, idx: usize) -> Result<Value, String> {
    let map = &sp.map;
    let raw_title = text(rec, map, "title");
    let statement_src = text(rec, map, "statement");

    let (title, statement, mode, language, mut cases) = match sp.style.as_str() {
        "humaneval" => {
            let entry = text(rec, map, "entry_point");
            let program = text(rec, map, "program");
            if entry.is_empty() || program.is_empty() {
                return Err("record has no entry_point or no grader program".into());
            }
            let mut cases = vec![];
            match (sp.split_asserts, split_check(&program)) {
                (true, Some((prelude, asserts))) => {
                    for (i, a) in asserts.iter().enumerate() {
                        let body = format!(
                            "{}\nfrom solution import {entry} as candidate\n{a}\n",
                            prelude.trim_start()
                        );
                        cases.push(case(&format!("assert {}", i + 1), &body, true));
                    }
                }
                _ => {
                    let body = format!(
                        "from solution import {entry}\n{}\n\ncheck({entry})\n",
                        program.trim_start()
                    );
                    cases.push(case("check", &body, true));
                }
            }
            let title = if raw_title.is_empty() {
                entry.clone()
            } else {
                format!("{raw_title} — {entry}")
            };
            let statement = format!(
                "Write a complete Python program that defines `{entry}`.\n{UNIT_CONTRACT}\n\n{statement_src}"
            );
            (title, statement, "unit", "python".to_string(), cases)
        }
        "asserts" => {
            let asserts = strings(rec, map, "asserts");
            if asserts.is_empty() {
                return Err("record carries no assertions to grade with".into());
            }
            let imports = strings(rec, map, "imports").join("\n");
            let cases: Vec<Value> = asserts
                .iter()
                .enumerate()
                .map(|(i, a)| {
                    let body = format!("{imports}\nfrom solution import *\n{a}\n");
                    case(&format!("assert {}", i + 1), body.trim_start(), i > 0)
                })
                .collect();
            // A record whose only name is a row number ("2") says nothing on a
            // task list, so the benchmark's own name goes in front of it.
            let title = match raw_title.as_str() {
                "" => short(&statement_src, 48),
                t if t.chars().all(|c| c.is_ascii_digit()) => format!(
                    "{} {t} — {}",
                    sp.source.to_uppercase(),
                    short(&statement_src, 40)
                ),
                t => format!("{t} — {}", short(&statement_src, 40)),
            };
            // The prose alone never names the function; the first assertion
            // does, and it is the visible case anyway.
            let statement = format!(
                "{statement_src}\n\n{UNIT_CONTRACT}\n\nIt is graded with assertions like:\n\n    {}",
                asserts[0]
            );
            (title, statement, "unit", "python".to_string(), cases)
        }
        "io" | "html" => {
            let (ins, outs) = if sp.style == "html" {
                (
                    rec.get("inputs")
                        .and_then(|v| v.as_array())
                        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                        .unwrap_or_default(),
                    rec.get("outputs")
                        .and_then(|v| v.as_array())
                        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                        .unwrap_or_default(),
                )
            } else {
                (
                    strings(rec, map, "inputs"),
                    strings(rec, map, "outputs"),
                )
            };
            let mut cases: Vec<Value> = ins
                .iter()
                .zip(outs.iter())
                .enumerate()
                .map(|(i, (a, b))| io_case(&format!("sample {}", i + 1), a, b, false))
                .collect();
            if sp.style == "io" {
                let hi = strings(rec, map, "hidden_inputs");
                let ho = strings(rec, map, "hidden_outputs");
                for (i, (a, b)) in hi.iter().zip(ho.iter()).enumerate() {
                    cases.push(io_case(&format!("hidden {}", i + 1), a, b, true));
                }
            }
            if cases.is_empty() {
                return Err("no input/output pairs found in this record".into());
            }
            let statement = if sp.style == "html" {
                let src = rec
                    .get("source_url")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                let body = rec
                    .get("description")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                format!("{body}\n\n— scraped from {src}")
            } else {
                format!(
                    "{statement_src}\n\nRead the input from stdin and write the answer to stdout."
                )
            };
            let title = if raw_title.is_empty() {
                rec.get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("scraped task")
                    .to_string()
            } else {
                raw_title.clone()
            };
            (title, statement, "io", sp.language.clone(), cases)
        }
        other => return Err(format!("unknown style `{other}`")),
    };

    // `hide_after` overrides whatever the style decided: the caller knows
    // whether their arena wants examples on the table or nothing at all.
    if let Some(k) = sp.hide_after {
        for (i, c) in cases.iter_mut().enumerate() {
            c["hidden"] = json!(i >= k);
        }
    }
    if cases.len() > sp.max_cases {
        // Keep every visible case — dropping those changes what an entrant is
        // told — and fill the rest of the budget with hidden ones.
        let mut kept: Vec<Value> = cases
            .iter()
            .filter(|c| c["hidden"] != json!(true))
            .cloned()
            .collect();
        let room = sp.max_cases.saturating_sub(kept.len());
        kept.extend(
            cases
                .into_iter()
                .filter(|c| c["hidden"] == json!(true))
                .take(room),
        );
        cases = kept;
    }
    if cases.is_empty() {
        return Err("nothing left to grade after the case limits".into());
    }

    let stem = match slugify(&raw_title).as_str() {
        "" => slugify(&format!("{title}-{}", sp.offset + idx)),
        v => v.to_string(),
    };
    // `humaneval-humaneval-0` reads like a bug. If the record already names its
    // benchmark, that name is the prefix.
    let slug = if stem.starts_with(&format!("{}-", sp.slug_prefix)) || stem == sp.slug_prefix {
        stem
    } else {
        format!("{}-{stem}", sp.slug_prefix)
    };
    let mut tags = vec![sp.source.clone(), "benchmark".to_string()];
    tags.extend(sp.tags.clone());

    let mut task = json!({
        // An override names one scraped page; a benchmark page names itself.
        "title": if sp.title.is_empty() { short(&title, 90) } else { sp.title.clone() },
        "slug": slug,
        "statement": statement.trim(),
        "language": language,
        "mode": mode,
        "tests": cases,
        "tags": tags,
        "author": sp.author,
        "timeout_ms": sp.timeout_ms,
    });
    if sp.style == "humaneval" {
        let starter = text(rec, map, "starter");
        if !starter.is_empty() {
            task["starter"] = json!(starter);
        }
    }
    Ok(task)
}

async fn convert(args: &Value) -> Result<(Spec, Vec<Value>, Vec<Value>, String), String> {
    let sp = spec(args)?;
    let (rows, url) = records(&sp).await?;
    let mut tasks = vec![];
    let mut skipped = vec![];
    for (i, rec) in rows.iter().enumerate() {
        match to_task(&sp, rec, i) {
            Ok(t) => tasks.push(t),
            Err(e) => skipped.push(json!({ "record": sp.offset + i, "reason": e })),
        }
    }
    Ok((sp, tasks, skipped, url))
}

fn card(t: &Value) -> Value {
    let cases = t["tests"].as_array().cloned().unwrap_or_default();
    json!({
        "slug": t["slug"], "title": t["title"], "mode": t["mode"], "language": t["language"],
        "cases": cases.len(),
        "hidden": cases.iter().filter(|c| c["hidden"] == json!(true)).count(),
        "statement": short(t["statement"].as_str().unwrap_or(""), 220),
    })
}

// ── the hub ──────────────────────────────────────────────────────────────
//
// The rows API above imports a dataset you can already name. This is the other
// half of the HuggingFace interface: find one. Search the hub, read a dataset's
// configs and splits, look at the columns of a real row, and infer the style
// and the field map that turn those records into arena tasks.
//
// The inference answers with the exact argument object `bench_preview` takes,
// which is the whole point — discovery ends precisely where the importer
// begins, and an agent can go from "find me a code benchmark" to graded tasks
// without a human guessing which column holds the tests.

async fn fetch_json(url: &str, refresh: bool) -> Result<Value, String> {
    let body = fetch(url, refresh).await?;
    serde_json::from_str(&body).map_err(|e| format!("{url} did not answer json: {e}"))
}

/// A sample row is for reading, not for grading — a CodeContests row runs to
/// hundreds of kilobytes. Long strings and long arrays are cut to their shape.
fn clip(v: &Value) -> Value {
    match v {
        Value::String(t) => json!(short(t, 300)),
        Value::Array(a) => {
            let mut out: Vec<Value> = a.iter().take(3).map(clip).collect();
            if a.len() > 3 {
                out.push(json!(format!("… {} more", a.len() - 3)));
            }
            Value::Array(out)
        }
        Value::Object(o) => Value::Object(o.iter().map(|(k, x)| (k.clone(), clip(x))).collect()),
        other => other.clone(),
    }
}

/// The first candidate path that holds something usable in this row.
fn present<'a>(row: &Value, cands: &[&'a str]) -> Option<&'a str> {
    cands.iter().copied().find(|p| {
        matches!(at(row, p), Some(v) if !matches!(v, Value::Null)
            && !matches!(v, Value::String(s) if s.trim().is_empty())
            && !matches!(v, Value::Array(a) if a.is_empty()))
    })
}

fn is_list(row: &Value, path: &str) -> bool {
    matches!(at(row, path), Some(Value::Array(a)) if !a.is_empty())
}

const TITLE_C: &[&str] = &["task_id", "name", "title", "id", "problem_id"];
const STMT_C: &[&str] = &[
    "prompt",
    "description",
    "problem",
    "statement",
    "question",
    "instruction",
    "text",
    "content",
];

/// Which style a dataset's columns are asking for, read off one real row.
///
/// Column *names* alone lie — `test` is a grader program in HumanEval and a
/// list of assertions elsewhere — so every guess here is checked against the
/// value in the row, not just the name.
fn infer(row: &Value) -> (Option<(&'static str, Value)>, String) {
    let title = present(row, TITLE_C);
    let stmt = present(row, STMT_C);

    // humaneval — a named entry point plus a grader program to run against it.
    if let (Some(entry), Some(stmt)) = (present(row, &["entry_point", "entrypoint"]), stmt) {
        if let Some(prog) = ["test", "check", "test_code", "canonical_test"]
            .iter()
            .copied()
            .find(|p| matches!(at(row, p), Some(Value::String(s)) if s.contains("assert")))
        {
            let mut map = json!({ "statement": stmt, "starter": stmt, "entry_point": entry, "program": prog });
            if let Some(t) = title {
                map["title"] = json!(t);
            }
            return (
                Some(("humaneval", map)),
                format!("`{entry}` names the function and `{prog}` is a grader program that asserts against it"),
            );
        }
    }

    // asserts — a list of assertion lines, one case each.
    if let Some(list) = ["test_list", "asserts", "test_cases", "tests", "test"]
        .iter()
        .copied()
        .find(|p| is_list(row, p))
    {
        if let Some(stmt) = stmt {
            let mut map = json!({ "statement": stmt, "asserts": list });
            if let Some(t) = title {
                map["title"] = json!(t);
            }
            if let Some(imports) = present(row, &["test_imports", "imports", "test_setup_code"]) {
                map["imports"] = json!(imports);
            }
            return (
                Some(("asserts", map)),
                format!("`{list}` is a list of assertions — each becomes its own case, so a near-miss scores partial credit"),
            );
        }
    }

    // io — stdin against stdout, the shape competitive-programming sets use.
    let ins = present(row, &["public_tests.input", "inputs", "input", "test_input"]);
    let outs = present(row, &["public_tests.output", "outputs", "output", "test_output"]);
    if let (Some(ins), Some(outs), Some(stmt)) = (ins, outs, stmt) {
        let mut map = json!({ "statement": stmt, "inputs": ins, "outputs": outs });
        if let Some(t) = title {
            map["title"] = json!(t);
        }
        let hin = ["private_tests.input", "generated_tests.input", "hidden_inputs"]
            .iter()
            .copied()
            .filter(|p| at(row, p).is_some())
            .collect::<Vec<_>>();
        let hout = ["private_tests.output", "generated_tests.output", "hidden_outputs"]
            .iter()
            .copied()
            .filter(|p| at(row, p).is_some())
            .collect::<Vec<_>>();
        if !hin.is_empty() && !hout.is_empty() {
            map["hidden_inputs"] = json!(hin);
            map["hidden_outputs"] = json!(hout);
        }
        return (
            Some(("io", map)),
            format!("`{ins}` and `{outs}` pair up as stdin/stdout cases"),
        );
    }

    (
        None,
        match stmt {
            Some(s) => format!(
                "`{s}` reads like the statement, but no column of this row holds gradable tests — \
                 pass your own `style` and `map` to bench_preview if you know better"
            ),
            None => "no column of this row looks like a problem statement, and none holds gradable \
                     tests — this is probably not a benchmark"
                .to_string(),
        },
    )
}

/// Search the hub. Answers dataset ids, which is what every other call here
/// wants: `dataset=` on bench_preview, `dataset=` on hf_dataset.
pub async fn hf_search(args: &Value) -> Result<Value, String> {
    let q = match s(args, "query").as_str() {
        "" => s(args, "q"),
        v => v.to_string(),
    };
    let limit = n(args, "limit", 20).clamp(1, 100);
    let sort = match s(args, "sort").as_str() {
        "" | "downloads" => "downloads",
        "likes" => "likes",
        "recent" | "updated" | "lastModified" => "lastModified",
        other => {
            return Err(format!(
                "unknown sort `{other}` — expected downloads, likes or recent"
            ))
        }
    };
    let mut url = format!("{HF_API}?limit={limit}&sort={sort}&direction=-1&full=false");
    if !q.is_empty() {
        url.push_str(&format!("&search={}", enc(&q)));
    }
    let filter = s(args, "filter");
    if !filter.is_empty() {
        url.push_str(&format!("&filter={}", enc(&filter)));
    }
    let author = s(args, "author");
    if !author.is_empty() {
        url.push_str(&format!("&author={}", enc(&author)));
    }

    let doc = fetch_json(&url, flag(args, "refresh", false)).await?;
    let hits: Vec<Value> = doc
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|d| {
            let id = d.get("id").and_then(|v| v.as_str()).unwrap_or("");
            json!({
                "dataset": id,
                "home": format!("https://huggingface.co/datasets/{id}"),
                "downloads": d.get("downloads").and_then(|v| v.as_u64()).unwrap_or(0),
                "likes": d.get("likes").and_then(|v| v.as_u64()).unwrap_or(0),
                "updated": s(d, "lastModified"),
                // Gated sets answer the rows API with a 401 unless this host
                // holds a token that has accepted their terms — say so here
                // rather than let the import fail three calls later.
                "gated": !matches!(d.get("gated"), None | Some(Value::Bool(false))),
                "tags": d.get("tags").cloned().unwrap_or_else(|| json!([])),
            })
        })
        .collect();

    Ok(json!({
        "query": q,
        "sort": sort,
        "filter": filter,
        "count": hits.len(),
        "token": hf_token().is_some(),
        "results": hits,
        "next": "hf_dataset with one of these `dataset` ids — it reads the columns and works out how to import them",
    }))
}

/// Everything needed to import one dataset: its configs and splits, the columns
/// of a real row, and the style + map those columns imply.
pub async fn hf_dataset(args: &Value) -> Result<Value, String> {
    let dataset = match s(args, "dataset").as_str() {
        "" => s(args, "id"),
        v => v.to_string(),
    };
    if dataset.is_empty() {
        return Err("hf_dataset needs a `dataset` — e.g. dataset=openai/openai_humaneval".into());
    }
    let refresh = flag(args, "refresh", false);

    // The hub card is nice to have; a dataset with no card still imports, so a
    // failure here is a missing field, not a failed call.
    let card = fetch_json(&format!("{HF_API}/{}", enc(&dataset)), refresh)
        .await
        .unwrap_or(Value::Null);

    let splits_doc = fetch_json(
        &format!("{HF_SPLITS}?dataset={}", enc(&dataset)),
        refresh,
    )
    .await
    .map_err(|e| {
        format!("{e} — a dataset that is gated or private needs a token in HF_TOKEN or ~/.mod/openarena/hf.json")
    })?;
    let entries: Vec<(String, String)> = splits_doc
        .get("splits")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().map(|e| (s(e, "config"), s(e, "split"))).collect())
        .unwrap_or_default();
    if entries.is_empty() {
        return Err(format!(
            "the dataset viewer lists no splits for `{dataset}` — it may not be converted for the rows API"
        ));
    }

    // A benchmark is graded on its held-out split, so that is the one to reach
    // for; `train` is the fallback for the sets that only ship one.
    let want_config = s(args, "config");
    let want_split = s(args, "split");
    let rank = |sp: &str| match sp {
        "test" => 0,
        "validation" | "valid" => 1,
        "train" => 3,
        _ => 2,
    };
    let mut candidates: Vec<&(String, String)> = entries
        .iter()
        .filter(|(c, sp)| {
            (want_config.is_empty() || *c == want_config)
                && (want_split.is_empty() || *sp == want_split)
        })
        .collect();
    if candidates.is_empty() {
        return Err(format!(
            "`{dataset}` has no config/split matching config={want_config} split={want_split} — it has {:?}",
            entries.iter().map(|(c, sp)| format!("{c}/{sp}")).collect::<Vec<_>>()
        ));
    }
    candidates.sort_by_key(|(_, sp)| rank(sp));
    let (config, split) = candidates[0].clone();

    let first = fetch_json(
        &format!(
            "{HF_FIRST}?dataset={}&config={}&split={}",
            enc(&dataset),
            enc(&config),
            enc(&split)
        ),
        refresh,
    )
    .await?;
    let columns: Vec<Value> = first
        .get("features")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .map(|f| {
                    let t = f.get("type").cloned().unwrap_or(Value::Null);
                    json!({
                        "name": s(f, "name"),
                        "type": match t.get("dtype").and_then(|d| d.as_str()) {
                            Some(d) => d.to_string(),
                            None => s(&t, "_type"),
                        },
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let row = first
        .get("rows")
        .and_then(|r| r.as_array())
        .and_then(|a| a.first())
        .and_then(|r| r.get("row"))
        .cloned()
        .unwrap_or(Value::Null);

    let (guess, why) = infer(&row);
    let suggested = guess.as_ref().map(|(style, map)| {
        json!({
            "source": "hf",
            "dataset": dataset,
            "config": config,
            "split": split,
            "style": style,
            "map": map,
            "limit": 5,
        })
    });

    // Grouped the way a caller picks: config first, then its splits.
    let mut configs: Vec<Value> = vec![];
    for (c, sp) in &entries {
        match configs.iter_mut().find(|x| x["config"] == json!(c)) {
            Some(x) => x["splits"].as_array_mut().unwrap().push(json!(sp)),
            None => configs.push(json!({ "config": c, "splits": [sp] })),
        }
    }

    Ok(json!({
        "dataset": dataset,
        "home": format!("https://huggingface.co/datasets/{dataset}"),
        "license": card.get("cardData").and_then(|c| c.get("license")).cloned().unwrap_or(Value::Null),
        "downloads": card.get("downloads").and_then(|v| v.as_u64()).unwrap_or(0),
        "likes": card.get("likes").and_then(|v| v.as_u64()).unwrap_or(0),
        "gated": !matches!(card.get("gated"), None | Some(Value::Bool(false))),
        "tags": card.get("tags").cloned().unwrap_or_else(|| json!([])),
        "configs": configs,
        "config": config,
        "split": split,
        "columns": columns,
        "sample": clip(&row),
        "style": guess.as_ref().map(|(s, _)| json!(s)).unwrap_or(Value::Null),
        "why": why,
        "suggested": suggested,
        "next": "pass `suggested` straight to bench_preview — it is already the argument object — then bench_import when the tasks read right",
    }))
}

// ── the tools ────────────────────────────────────────────────────────────

pub fn sources_tool() -> Value {
    catalog()
}

/// Fetch and convert, and write nothing. The first task comes back in full so
/// the shape of what would land is visible before it lands.
pub async fn preview(args: &Value) -> Result<Value, String> {
    let (sp, tasks, skipped, url) = convert(args).await?;
    Ok(json!({
        "source": sp.source,
        "style": sp.style,
        "transport": sp.transport,
        "url": url,
        "offset": sp.offset,
        "count": tasks.len(),
        "skipped": skipped,
        "tasks": tasks.iter().map(card).collect::<Vec<_>>(),
        "sample": tasks.first().cloned().unwrap_or(Value::Null),
        "note": "nothing was imported — call bench_import with the same arguments to keep these",
    }))
}

/// Convert and keep. A slug already in the arena is a skip, not an error: the
/// point of `offset` is to come back for more, and coming back should be safe.
pub async fn import(args: &Value) -> Result<Value, String> {
    let (sp, tasks, mut skipped, url) = convert(args).await?;
    if flag(args, "dry_run", false) {
        return preview(args).await;
    }
    // Records read, not tasks kept: a duplicate lands in `skipped` below but
    // was already counted here, and paging past it twice would skip a record.
    let consumed = tasks.len() + skipped.len();
    let mut imported = vec![];
    let mut failed = vec![];
    for t in &tasks {
        let slug = t["slug"].as_str().unwrap_or("").to_string();
        match arena::create_task(t) {
            Ok(v) => imported.push(json!({
                "id": v["id"], "slug": v["slug"], "title": v["title"],
                "cases": v["total_tests"], "hidden": v["hidden_tests"],
            })),
            Err(e) if e.contains("already in the arena") => {
                skipped.push(json!({ "slug": slug, "reason": "already in the arena" }))
            }
            Err(e) => failed.push(json!({ "slug": slug, "error": e })),
        }
    }
    Ok(json!({
        "source": sp.source,
        "style": sp.style,
        "url": url,
        "offset": sp.offset,
        "next_offset": sp.offset + consumed,
        "imported": imported.len(),
        "tasks": imported,
        "skipped": skipped,
        "failed": failed,
    }))
}
