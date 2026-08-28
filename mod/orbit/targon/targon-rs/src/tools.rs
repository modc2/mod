//! The MCP tool layer: one table row per Targon Hub API endpoint, plus a few
//! composed tools (`cheapest`, `rent`) that turn the register-then-deploy dance
//! into a single call.
//!
//! Rows are data, not code: a row carries the HTTP method, the path template
//! (`{placeholders}` become required string arguments) and the query/body
//! fields, which is enough to generate the JSON Schema an MCP client sees and
//! to build the upstream request. Adding an endpoint means adding a row.

use crate::targon;
use serde_json::{json, Map, Value};

pub struct Arg {
    pub name: &'static str,
    pub ty: &'static str,
    pub required: bool,
    pub desc: &'static str,
}

const fn opt(name: &'static str, ty: &'static str, desc: &'static str) -> Arg {
    Arg { name, ty, required: false, desc }
}

const fn req(name: &'static str, ty: &'static str, desc: &'static str) -> Arg {
    Arg { name, ty, required: true, desc }
}

pub struct Spec {
    pub name: &'static str,
    pub desc: &'static str,
    pub method: &'static str,
    pub path: &'static str,
    pub query: &'static [Arg],
    pub body: &'static [Arg],
    /// No API key needed — inventory, version and health are open.
    pub public: bool,
}

const NONE: &[Arg] = &[];

const PAGE: &[Arg] = &[
    opt("limit", "integer", "Max items (default 1000)"),
    opt("cursor", "string", "Pagination cursor — next_cursor from the previous page"),
];

/// Fields of a create-workload body, reused by `create_workload` and `rent`.
const WORKLOAD_BODY: &[Arg] = &[
    req("type", "string", "RENTAL (container) or VM (confidential virtual machine)"),
    req("name", "string", "Lowercase alphanumeric and hyphens, max 32 chars"),
    req("image", "string", "Container image reference (VM: image name from vm_images)"),
    req("resource_name", "string", "Inventory resource tier, e.g. h200-small"),
    opt("envs", "array", "Environment variables: [{name, value}]. RENTAL only"),
    opt("ports", "array", "[{port: 1024-65535, protocol: TCP|UDP|SCTP, routing: PROXIED|DIRECT}]"),
    opt("commands", "array", "Command override. RENTAL only"),
    opt("args", "array", "Command arguments. RENTAL only"),
    opt("registry_auth", "object", "{server, username, password} for private images. RENTAL only"),
    opt("ssh_keys", "array", "SSH key UIDs to attach"),
    opt("volumes", "array", "[{uid, mount_path, read_only}]. RENTAL only"),
    opt("vm_config", "object", "{password} — required for type VM"),
    opt("experiments", "object", "Feature experiments: reserved-gpu, persistent-workload"),
];

#[rustfmt::skip]
pub const SPECS: &[Spec] = &[
    // ── open endpoints ───────────────────────────────────────────
    Spec { name: "inventory", desc: "List compute tiers on the Targon (Bittensor SN4) network with hourly price and live availability. No API key needed.",
        method: "GET", path: "/tha/v2/inventory", public: true, body: NONE,
        query: &[opt("type", "string", "Filter by resource type: rental, storage or vm"),
                 opt("gpu", "boolean", "true = GPU resources only")] },
    Spec { name: "version", desc: "Targon Hub API version, git hash and build date. No API key needed.",
        method: "GET", path: "/tha/v2/version", public: true, query: NONE, body: NONE },
    Spec { name: "health", desc: "Targon Hub API liveness probe. No API key needed.",
        method: "GET", path: "/tha/v2/healthz", public: true, query: NONE, body: NONE },
    Spec { name: "readiness", desc: "Targon Hub API readiness probe. No API key needed.",
        method: "GET", path: "/tha/v2/readyz", public: true, query: NONE, body: NONE },

    // ── workloads ────────────────────────────────────────────────
    Spec { name: "list_workloads", desc: "List your workloads (rentals and VMs).",
        method: "GET", path: "/tha/v2/workloads", public: false, body: NONE,
        query: &[opt("limit", "integer", "Max items"), opt("cursor", "string", "Pagination cursor"),
                 opt("type", "string", "Filter by RENTAL or VM"),
                 opt("status", "string", "Filter by status: registered, provisioning, running, error, suspended, deleted"),
                 opt("name", "string", "Filter by name")] },
    Spec { name: "get_workload", desc: "Full workload record: envs, ports, commands, args, ssh keys, volumes, state.",
        method: "GET", path: "/tha/v2/workloads/{workload_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "create_workload", desc: "Register a workload. This only saves the config (state: registered) — call deploy_workload to provision it, or use `rent` to do both.",
        method: "POST", path: "/tha/v2/workloads", public: false, query: NONE, body: WORKLOAD_BODY },
    Spec { name: "update_workload", desc: "Update a workload. Changing a running workload triggers an automatic redeploy.",
        method: "PATCH", path: "/tha/v2/workloads/{workload_uid}", public: false, query: NONE,
        body: &[opt("name", "string", "New name"), opt("image", "string", "New image"),
                opt("envs", "array", "Environment variables"), opt("ports", "array", "Port mappings"),
                opt("commands", "array", "Command override"), opt("args", "array", "Command arguments"),
                opt("registry_auth", "object", "Private registry credentials"),
                opt("ssh_keys", "array", "SSH key UIDs"), opt("volumes", "array", "Volume mounts"),
                opt("experiments", "object", "Feature experiments")] },
    Spec { name: "delete_workload", desc: "Tear down a workload's runtime and soft-delete it. Billing stops.",
        method: "DELETE", path: "/tha/v2/workloads/{workload_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "deploy_workload", desc: "Provision a registered workload. Checks credits and inventory availability.",
        method: "POST", path: "/tha/v2/workloads/{workload_uid}/deploy", public: false, query: NONE, body: NONE },
    Spec { name: "suspend_workload", desc: "Suspend a RENTAL and release its runtime resources (config is kept). Not supported for VM.",
        method: "POST", path: "/tha/v2/workloads/{workload_uid}/suspend", public: false, query: NONE, body: NONE },
    Spec { name: "reboot_workload", desc: "Reboot a VM workload.",
        method: "POST", path: "/tha/v2/workloads/{workload_uid}/reboot", public: false, query: NONE, body: NONE },
    Spec { name: "workload_state", desc: "Current status, access URLs, public IP, SSH port and replica counts.",
        method: "GET", path: "/tha/v2/workloads/{workload_uid}/state", public: false, query: NONE, body: NONE },
    Spec { name: "workload_events", desc: "Lifecycle events for a workload — the place to look when a deploy fails.",
        method: "GET", path: "/tha/v2/workloads/{workload_uid}/events", public: false, query: PAGE, body: NONE },
    Spec { name: "workload_logs", desc: "Container (or VM serial/qemu) logs. Returns {output}.",
        method: "GET", path: "/tha/v2/workloads/{workload_uid}/logs", public: false, body: NONE,
        query: &[opt("since", "string", "RFC 3339 timestamp"), opt("tail", "integer", "Number of recent lines"),
                 opt("previous", "boolean", "Logs from the previous container instance"),
                 opt("type", "string", "VM only: serial or qemu")] },
    Spec { name: "vm_images", desc: "VM images available for type VM workloads.",
        method: "GET", path: "/tha/v2/workloads/vm-images", public: false, query: NONE, body: NONE },
    Spec { name: "verify_workload", desc: "Verify a workload's attestation digest (SHA256). Returns {verified}.",
        method: "POST", path: "/tha/v2/workloads/verify", public: false, query: NONE,
        body: &[req("uid", "string", "Workload UID"), req("digest", "string", "SHA256 digest")] },
    Spec { name: "attach_volume", desc: "Attach a volume to a RENTAL at a mount path.",
        method: "PUT", path: "/tha/v2/workloads/{workload_uid}/volumes/{volume_uid}", public: false, query: NONE,
        body: &[req("mount_path", "string", "Absolute path inside the container"),
                opt("read_only", "boolean", "Mount read-only (default false)")] },
    Spec { name: "detach_volume", desc: "Detach a volume from a workload.",
        method: "DELETE", path: "/tha/v2/workloads/{workload_uid}/volumes/{volume_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "attach_ssh_key", desc: "Attach an SSH key to a workload.",
        method: "PUT", path: "/tha/v2/workloads/{workload_uid}/ssh-keys/{ssh_key_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "detach_ssh_key", desc: "Detach an SSH key from a workload.",
        method: "DELETE", path: "/tha/v2/workloads/{workload_uid}/ssh-keys/{ssh_key_uid}", public: false, query: NONE, body: NONE },

    // ── volumes ──────────────────────────────────────────────────
    Spec { name: "create_volume", desc: "Create a persistent volume. It is provisioned when first attached to a deployed RENTAL.",
        method: "POST", path: "/tha/v2/volumes", public: false, query: NONE,
        body: &[req("name", "string", "Volume name"), req("size_in_mb", "integer", "Size in megabytes"),
                req("resource_name", "string", "Compute resource to provision on, e.g. storage-rentals")] },
    Spec { name: "list_volumes", desc: "List your volumes with size, cost and attachment.",
        method: "GET", path: "/tha/v2/volumes", public: false, body: NONE,
        query: &[opt("limit", "integer", "Max items"), opt("cursor", "string", "Pagination cursor"),
                 opt("workload_uid", "string", "Only volumes attached to this workload")] },
    Spec { name: "get_volume", desc: "Get one volume by UID.",
        method: "GET", path: "/tha/v2/volumes/{volume_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "volume_state", desc: "Current status of a volume.",
        method: "GET", path: "/tha/v2/volumes/{volume_uid}/state", public: false, query: NONE, body: NONE },
    Spec { name: "volume_events", desc: "Lifecycle and billing events for a volume.",
        method: "GET", path: "/tha/v2/volumes/{volume_uid}/events", public: false, query: PAGE, body: NONE },
    Spec { name: "update_volume", desc: "Rename a volume.",
        method: "PATCH", path: "/tha/v2/volumes/{volume_uid}", public: false, query: NONE,
        body: &[req("name", "string", "New volume name")] },
    Spec { name: "delete_volume", desc: "Delete a volume.",
        method: "DELETE", path: "/tha/v2/volumes/{volume_uid}", public: false, query: NONE, body: NONE },

    // ── ssh keys ─────────────────────────────────────────────────
    Spec { name: "create_ssh_key", desc: "Register a public SSH key for workload access.",
        method: "POST", path: "/tha/v2/ssh-keys", public: false, query: NONE,
        body: &[req("name", "string", "Key name"), req("ssh_key", "string", "Public key contents, e.g. ssh-ed25519 AAAA...")] },
    Spec { name: "list_ssh_keys", desc: "List your registered SSH keys.",
        method: "GET", path: "/tha/v2/ssh-keys", public: false, query: PAGE, body: NONE },
    Spec { name: "get_ssh_key", desc: "Get one SSH key by UID.",
        method: "GET", path: "/tha/v2/ssh-keys/{ssh_key_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "update_ssh_key", desc: "Rename an SSH key.",
        method: "PATCH", path: "/tha/v2/ssh-keys/{ssh_key_uid}", public: false, query: NONE,
        body: &[req("name", "string", "New key name")] },
    Spec { name: "delete_ssh_key", desc: "Delete an SSH key.",
        method: "DELETE", path: "/tha/v2/ssh-keys/{ssh_key_uid}", public: false, query: NONE, body: NONE },

    // ── templates ────────────────────────────────────────────────
    Spec { name: "create_template", desc: "Save a reusable workload config. manifest is a full create_workload body and manifest.type must equal type.",
        method: "POST", path: "/tha/v2/templates", public: false, query: NONE,
        body: &[req("name", "string", "Template name"), opt("description", "string", "Description"),
                req("type", "string", "Workload type, e.g. RENTAL"),
                req("manifest", "object", "Full create-workload body")] },
    Spec { name: "list_templates", desc: "List workload templates, public or your own.",
        method: "GET", path: "/tha/v2/templates", public: false, body: NONE,
        query: &[opt("limit", "integer", "Max items"), opt("cursor", "string", "Pagination cursor"),
                 opt("type", "string", "Filter by workload type"),
                 opt("visibility", "string", "public or private")] },
    Spec { name: "get_template", desc: "Get one template by UID.",
        method: "GET", path: "/tha/v2/templates/{template_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "update_template", desc: "Update a template's name, description or manifest.",
        method: "PATCH", path: "/tha/v2/templates/{template_uid}", public: false, query: NONE,
        body: &[opt("name", "string", "New name"), opt("description", "string", "New description"),
                opt("manifest", "object", "New manifest")] },
    Spec { name: "delete_template", desc: "Delete a template.",
        method: "DELETE", path: "/tha/v2/templates/{template_uid}", public: false, query: NONE, body: NONE },

    // ── account ──────────────────────────────────────────────────
    Spec { name: "wallet", desc: "Your Bittensor SS58 wallet address on Targon.",
        method: "GET", path: "/tha/v2/me/wallet", public: false, query: NONE, body: NONE },
    Spec { name: "credits", desc: "Your credit balance — deploys are refused when it runs out.",
        method: "GET", path: "/tha/v2/me/credits", public: false, query: NONE, body: NONE },
    Spec { name: "list_api_keys", desc: "List your Targon API keys.",
        method: "GET", path: "/tha/v2/me/api-keys", public: false, query: PAGE, body: NONE },
    Spec { name: "create_api_key", desc: "Create a Targon API key. The raw key is returned once, in key_raw.",
        method: "POST", path: "/tha/v2/me/api-keys", public: false, query: NONE,
        body: &[req("name", "string", "Key name")] },
    Spec { name: "update_api_key", desc: "Rename an API key.",
        method: "PATCH", path: "/tha/v2/me/api-keys/{key_uid}", public: false, query: NONE,
        body: &[req("name", "string", "New key name")] },
    Spec { name: "delete_api_key", desc: "Delete an API key.",
        method: "DELETE", path: "/tha/v2/me/api-keys/{key_uid}", public: false, query: NONE, body: NONE },
    Spec { name: "roll_api_key", desc: "Rotate an API key's secret; returns the new key_raw.",
        method: "POST", path: "/tha/v2/me/api-keys/{key_uid}:roll", public: false, query: NONE, body: NONE },

    // ── image builds ─────────────────────────────────────────────
    Spec { name: "build_image", desc: "Build a container image through the Heim build service. Returns the build log or a cached {type: cached, image_id}.",
        method: "POST", path: "/tha/v2/heim/build", public: false, query: NONE,
        body: &[opt("dockerfile", "string", "Dockerfile contents"), opt("files", "object", "Context files"),
                opt("repo", "string", "Git repository URL"), opt("ref", "string", "Git ref"),
                opt("image_id", "string", "Target image ID"), opt("tag", "string", "Image tag"),
                opt("build_args", "object", "Docker build args"), opt("labels", "object", "Image labels"),
                opt("secrets", "array", "Build secrets"), opt("ssh", "array", "SSH mount configs"),
                opt("cache_from", "array", "Cache sources"), opt("cache_to", "array", "Cache destinations"),
                opt("no_cache", "boolean", "Disable the build cache")] },
];

pub fn find(name: &str) -> Option<&'static Spec> {
    SPECS.iter().find(|s| s.name == name)
}

/// `{placeholder}` segments of a path template, in order.
fn path_params(path: &str) -> Vec<&str> {
    path.split('{')
        .skip(1)
        .filter_map(|seg| seg.split('}').next())
        .collect()
}

fn prop(ty: &str, desc: &str) -> Value {
    json!({ "type": ty, "description": desc })
}

pub fn input_schema(sp: &Spec) -> Value {
    let mut props = Map::new();
    let mut required: Vec<Value> = Vec::new();
    for p in path_params(sp.path) {
        props.insert(p.into(), prop("string", "Resource UID"));
        required.push(json!(p));
    }
    for a in sp.query.iter().chain(sp.body.iter()) {
        props.insert(a.name.into(), prop(a.ty, a.desc));
        if a.required {
            required.push(json!(a.name));
        }
    }
    if !sp.public {
        props.insert("api_key".into(), prop("string", "Targon API key override (defaults to TARGON_API_KEY)"));
    }
    json!({ "type": "object", "properties": props, "required": required })
}

/// Query values go on the wire as strings; keep JSON scalars readable.
fn scalar(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

pub async fn call_spec(sp: &'static Spec, args: &Value, key: &str) -> Result<Value, String> {
    let mut path = sp.path.to_string();
    for p in path_params(sp.path) {
        let v = args
            .get(p)
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("{} requires `{p}`", sp.name))?;
        path = path.replace(&format!("{{{p}}}"), v);
    }

    let mut query: Vec<(String, String)> = Vec::new();
    for a in sp.query {
        match args.get(a.name) {
            Some(v) if !v.is_null() => query.push((a.name.into(), scalar(v))),
            _ if a.required => return Err(format!("{} requires `{}`", sp.name, a.name)),
            _ => {}
        }
    }

    let mut body = Map::new();
    for a in sp.body {
        match args.get(a.name) {
            Some(v) if !v.is_null() => {
                body.insert(a.name.into(), v.clone());
            }
            _ if a.required => return Err(format!("{} requires `{}`", sp.name, a.name)),
            _ => {}
        }
    }
    let body = (!body.is_empty()).then(|| Value::Object(body));

    if !sp.public && key.is_empty() {
        return Err(format!(
            "{} needs a Targon API key — set TARGON_API_KEY, write ~/.mod/targon/api_key, or pass api_key",
            sp.name
        ));
    }

    targon::request(sp.method, &path, key, &query, body.as_ref())
        .await
        .map_err(|e| e.to_string())
}

// ── composed tools ───────────────────────────────────────────────

pub fn composed_tools() -> Value {
    json!([
        {
            "name": "cheapest",
            "description": "Pick the cheapest currently-available compute tier matching a filter — the tier picker for rent/create_workload. No API key needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gpu": { "type": "boolean", "description": "GPU tiers only (default true)" },
                    "gpu_type": { "type": "string", "description": "Substring match on GPU type, e.g. H200, B200, RTX4090" },
                    "min_gpus": { "type": "integer", "description": "Minimum GPU count" },
                    "min_memory_mb": { "type": "integer", "description": "Minimum memory in MB" },
                    "max_cost_per_hour": { "type": "number", "description": "Price ceiling in USD/hour" },
                    "type": { "type": "string", "description": "Resource type: rental (default), vm or storage" }
                }
            }
        },
        {
            "name": "rent",
            "description": "Rent a machine in one call: register the workload and deploy it. Omit resource_name to take the cheapest tier matching gpu_type/min_gpus. Returns the workload, its state and the ssh command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": { "type": "string", "description": "Lowercase alphanumeric and hyphens, max 32 chars" },
                    "image": { "type": "string", "description": "Container image (default pytorch/pytorch:latest)" },
                    "resource_name": { "type": "string", "description": "Inventory tier; omit to auto-pick the cheapest match" },
                    "type": { "type": "string", "description": "RENTAL (default) or VM" },
                    "gpu_type": { "type": "string", "description": "Used only when resource_name is omitted, e.g. H200" },
                    "min_gpus": { "type": "integer", "description": "Used only when resource_name is omitted" },
                    "max_cost_per_hour": { "type": "number", "description": "Used only when resource_name is omitted" },
                    "ssh_keys": { "type": "array", "description": "SSH key UIDs to attach" },
                    "ports": { "type": "array", "description": "[{port, protocol, routing}]" },
                    "envs": { "type": "array", "description": "[{name, value}]" },
                    "commands": { "type": "array", "description": "Command override" },
                    "args": { "type": "array", "description": "Command arguments" },
                    "volumes": { "type": "array", "description": "[{uid, mount_path, read_only}]" },
                    "vm_config": { "type": "object", "description": "{password} — required for type VM" },
                    "deploy": { "type": "boolean", "description": "Deploy after registering (default true)" },
                    "api_key": { "type": "string", "description": "Targon API key override" }
                },
                "required": ["name"]
            }
        },
        {
            "name": "workload_exec",
            "description": "Run a command inside a RENTAL workload and return its output. RENTAL only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workload_uid": { "type": "string" },
                    "command": { "description": "Command as an array of argv strings, or one string that is split on whitespace" },
                    "api_key": { "type": "string", "description": "Targon API key override" }
                },
                "required": ["workload_uid", "command"]
            }
        }
    ])
}

fn f64_of(args: &Value, key: &str) -> Option<f64> {
    args.get(key).and_then(|v| v.as_f64())
}

/// Tier names and GPU types punctuate differently ("rtx4090-small" vs
/// "NVIDIA-GeForce-RTX-4090"), so match on letters and digits only.
fn squash(s: &str) -> String {
    s.chars().filter(|c| c.is_alphanumeric()).collect::<String>().to_lowercase()
}

/// Available tiers matching the filter, cheapest first.
async fn rank_tiers(args: &Value) -> Result<Vec<Value>, String> {
    let want_gpu = args.get("gpu").and_then(|v| v.as_bool()).unwrap_or(true);
    let kind = args.get("type").and_then(|v| v.as_str()).unwrap_or("rental");
    let inv = targon::request(
        "GET",
        "/tha/v2/inventory",
        "",
        &[("type".into(), kind.into())],
        None,
    )
    .await
    .map_err(|e| e.to_string())?;

    let gpu_type = args.get("gpu_type").and_then(|v| v.as_str()).map(squash);
    let min_gpus = args.get("min_gpus").and_then(|v| v.as_u64()).unwrap_or(0);
    let min_memory = args.get("min_memory_mb").and_then(|v| v.as_u64()).unwrap_or(0);
    let max_cost = f64_of(args, "max_cost_per_hour").unwrap_or(f64::MAX);

    let mut tiers: Vec<Value> = inv
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|t| {
            let spec = t.get("spec").cloned().unwrap_or(json!({}));
            let cost = t.get("cost_per_hour").and_then(|c| c.as_f64()).unwrap_or(f64::MAX);
            let gpus = spec.get("gpu_count").and_then(|c| c.as_u64()).unwrap_or(0);
            t.get("available").and_then(|a| a.as_u64()).unwrap_or(0) > 0
                && t.get("gpu").and_then(|g| g.as_bool()).unwrap_or(false) == want_gpu
                && cost <= max_cost
                && gpus >= min_gpus
                && spec.get("memory").and_then(|m| m.as_u64()).unwrap_or(0) >= min_memory
                && gpu_type.as_ref().is_none_or(|want| {
                    let gpu = spec.get("gpu_type").and_then(|g| g.as_str()).unwrap_or("");
                    let tier = t.get("name").and_then(|n| n.as_str()).unwrap_or("");
                    squash(gpu).contains(want) || squash(tier).contains(want)
                })
        })
        .collect();
    tiers.sort_by(|a, b| {
        let c = |t: &Value| t.get("cost_per_hour").and_then(|c| c.as_f64()).unwrap_or(f64::MAX);
        c(a).partial_cmp(&c(b)).unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(tiers)
}

pub async fn call_composed(name: &str, args: &Value, key: &str) -> Result<Value, String> {
    match name {
        "cheapest" => {
            let tiers = rank_tiers(args).await?;
            match tiers.first() {
                Some(best) => Ok(json!({
                    "resource_name": best.get("name"),
                    "cost_per_hour": best.get("cost_per_hour"),
                    "available": best.get("available"),
                    "spec": best.get("spec"),
                    "match": best,
                    "alternatives": tiers.iter().skip(1).take(5).collect::<Vec<_>>(),
                })),
                None => Err("no available tier matches that filter — try `inventory` to see what is live".into()),
            }
        }

        "rent" => {
            let mut create = Map::new();
            let wl_type = args.get("type").and_then(|v| v.as_str()).unwrap_or("RENTAL");
            create.insert("type".into(), json!(wl_type));
            create.insert(
                "name".into(),
                args.get("name").cloned().ok_or("rent requires `name`")?,
            );
            create.insert(
                "image".into(),
                args.get("image").cloned().unwrap_or(json!("pytorch/pytorch:latest")),
            );
            for f in ["ssh_keys", "ports", "envs", "commands", "args", "volumes", "vm_config"] {
                if let Some(v) = args.get(f) {
                    if !v.is_null() {
                        create.insert(f.into(), v.clone());
                    }
                }
            }
            let picked = match args.get("resource_name").and_then(|v| v.as_str()) {
                Some(r) => json!(r),
                None => {
                    let mut filter = args.clone();
                    filter["type"] = json!(if wl_type == "VM" { "vm" } else { "rental" });
                    filter["gpu"] = json!(args.get("gpu").and_then(|v| v.as_bool()).unwrap_or(true));
                    let tiers = rank_tiers(&filter).await?;
                    tiers
                        .first()
                        .and_then(|t| t.get("name").cloned())
                        .ok_or("no available tier matches — pass resource_name explicitly")?
                }
            };
            create.insert("resource_name".into(), picked.clone());

            let created = targon::request("POST", "/tha/v2/workloads", key, &[], Some(&Value::Object(create)))
                .await
                .map_err(|e| e.to_string())?;
            let uid = created
                .get("uid")
                .and_then(|u| u.as_str())
                .ok_or_else(|| format!("create returned no uid: {created}"))?
                .to_string();

            if !args.get("deploy").and_then(|v| v.as_bool()).unwrap_or(true) {
                return Ok(json!({ "workload": created, "resource_name": picked, "deployed": false }));
            }
            let deployed = targon::request(
                "POST",
                &format!("/tha/v2/workloads/{uid}/deploy"),
                key,
                &[],
                None,
            )
            .await
            .map_err(|e| format!("registered {uid} but deploy failed: {e}"))?;

            Ok(json!({
                "uid": uid,
                "resource_name": picked,
                "deployed": true,
                "workload": deployed,
                "ssh": targon::ssh_command(&uid),
                "next": format!("poll workload_state {uid} until status is running"),
            }))
        }

        "workload_exec" => {
            let uid = args
                .get("workload_uid")
                .and_then(|v| v.as_str())
                .ok_or("workload_exec requires `workload_uid`")?;
            let parts: Vec<String> = match args.get("command") {
                Some(Value::Array(a)) => a.iter().map(scalar).collect(),
                Some(Value::String(s)) => s.split_whitespace().map(String::from).collect(),
                _ => return Err("workload_exec requires `command`".into()),
            };
            if parts.is_empty() {
                return Err("workload_exec requires a non-empty `command`".into());
            }
            // The API takes argv as a repeated `command` query parameter.
            let query: Vec<(String, String)> =
                parts.into_iter().map(|p| ("command".to_string(), p)).collect();
            targon::request("POST", &format!("/tha/v2/workloads/{uid}/exec"), key, &query, None)
                .await
                .map_err(|e| e.to_string())
        }

        other => Err(format!("unknown tool: {other}")),
    }
}

pub fn is_composed(name: &str) -> bool {
    matches!(name, "cheapest" | "rent" | "workload_exec")
}

/// Full MCP tool list: table rows first, then the composed tools.
pub fn tool_list() -> Value {
    let mut tools: Vec<Value> = SPECS
        .iter()
        .map(|sp| {
            json!({ "name": sp.name, "description": sp.desc, "inputSchema": input_schema(sp) })
        })
        .collect();
    if let Some(extra) = composed_tools().as_array() {
        tools.extend(extra.clone());
    }
    Value::Array(tools)
}
