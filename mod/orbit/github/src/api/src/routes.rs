//! One door, one gate, one dispatch.
//!
//! Every request — whatever the path — lands in `handle`. It walks the same
//! seven steps in the same order and only then calls a handler:
//!
//!   1. resolve the path (the gateway may deliver `/github/x`, `/api/x` or `/x`)
//!   2. look it up in the policy table  → 404/405 before any work
//!   3. identify the caller            → verify the signature, or be anonymous
//!   4. ban check                      → refused before role or budget
//!   5. authorize                      → role ≥ the route's declared minimum
//!   6. charge the budget              → 429 with a real Retry-After
//!   7. validate the parameters        → allow-list from the same table
//!
//! and every one of those, allowed or refused, is written to the audit log.
//! Handlers below this line make no access decisions at all; if you want to
//! know what is gated, `policy.rs` is the only file to read.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::Json;

use crate::acl::{Acl, Ban, Grant};
use crate::audit;
use crate::cache::{Cache, README_TTL, SEARCH_TTL};
use crate::expand;
use crate::gh::{self, Repo};
use crate::limit::{Buckets, Governor};
use crate::policy::{self, Role};
use crate::rank::{self, Embedder};
use crate::store;
use crate::validate::{self, Args, Get};

/// Bodies larger than this are refused unread. Nothing this API accepts is
/// bigger than a PAT and an address.
const MAX_BODY: usize = 16 * 1024;

#[derive(Clone)]
pub struct AppState {
    pub cache: Arc<Cache>,
    pub gh: Arc<gh::Client>,
    pub embedder: Arc<Embedder>,
    pub buckets: Arc<Buckets>,
    pub governor: Arc<Governor>,
    pub started: f64,
    pub version: &'static str,
}

pub fn router(state: AppState) -> axum::Router {
    axum::Router::new().fallback(handle).with_state(state)
}

/// A refusal, in a shape the console can render and a human can act on.
struct Refusal {
    status: StatusCode,
    decision: &'static str,
    error: String,
    extra: serde_json::Value,
}

impl Refusal {
    fn new(status: StatusCode, decision: &'static str, error: impl Into<String>) -> Self {
        Self { status, decision, error: error.into(), extra: serde_json::json!({}) }
    }
    fn with(mut self, extra: serde_json::Value) -> Self {
        self.extra = extra;
        self
    }
}

pub async fn handle(
    State(st): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let t0 = std::time::Instant::now();
    let now = store::now();
    let path = normalize(uri.path());
    let ip = client_ip(&headers, peer);

    // CORS preflight: answered before the gate, since it carries no identity.
    if method == Method::OPTIONS {
        return (StatusCode::NO_CONTENT, cors_headers()).into_response();
    }

    // (2) Does this route exist at all?
    let Some(route) = policy::lookup(method.as_str(), &path) else {
        let status = if policy::path_exists(&path) {
            StatusCode::METHOD_NOT_ALLOWED
        } else {
            StatusCode::NOT_FOUND
        };
        let msg = if status == StatusCode::METHOD_NOT_ALLOWED {
            format!("{path} exists but not for {method} — see GET /policy")
        } else {
            format!("no such endpoint: {path} — see GET /policy")
        };
        return json(status, serde_json::json!({ "error": msg }));
    };

    // (3) Who is calling? A presented-but-bad token stays anonymous, with the
    // reason kept so /whoami can explain it instead of shrugging.
    let bearer = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.trim_start_matches("Bearer ").trim_start_matches("bearer ").trim())
        .filter(|v| !v.is_empty());
    let (address, token_error) = match bearer {
        Some(t) => match crate::auth::verify(t, now) {
            Ok(addr) => (Some(addr), None),
            Err(e) => (None, Some(e)),
        },
        None => (None, None),
    };
    let caller = crate::auth::Caller { address: address.clone(), ip: ip.clone(), token_error: token_error.clone() };
    let acl = Acl::load();
    let role = acl.role_of(caller.address.as_deref());
    let principal = caller.principal();

    let finish = |status: StatusCode, decision: &'static str, detail: Option<String>| {
        if route.path != "/health" {
            audit::append(&audit::Entry {
                t: now,
                who: principal.clone(),
                ip: ip.clone(),
                role: role.name().to_string(),
                method: method.as_str().to_string(),
                route: route.path.to_string(),
                decision: decision.to_string(),
                status: status.as_u16(),
                detail,
                ms: t0.elapsed().as_millis() as u64,
            });
        }
    };

    match gate(&st, route, &acl, role, &caller, &method, &uri, &body, now) {
        Err(r) => {
            finish(r.status, r.decision, Some(r.error.clone()));
            let mut payload = serde_json::json!({ "error": r.error, "decision": r.decision });
            if let Some(o) = r.extra.as_object() {
                for (k, v) in o {
                    payload[k] = v.clone();
                }
            }
            let mut resp = json(r.status, payload);
            if let Some(secs) = r.extra.get("retry_after").and_then(|v| v.as_u64()) {
                resp.headers_mut().insert("retry-after", secs.to_string().parse().unwrap());
            }
            resp
        }
        Ok(args) => {
            let out = dispatch(&st, route, &acl, role, &caller, args, now).await;
            match out {
                Ok(v) => {
                    finish(StatusCode::OK, "ok", None);
                    json(StatusCode::OK, v)
                }
                Err((status, msg)) => {
                    finish(status, "error", Some(msg.clone()));
                    json(status, serde_json::json!({ "error": msg, "decision": "error" }))
                }
            }
        }
    }
}

/// Steps 4-7. Returns the validated arguments, or the refusal to record.
#[allow(clippy::too_many_arguments)]
fn gate(
    st: &AppState,
    route: &policy::Route,
    acl: &Acl,
    role: Role,
    caller: &crate::auth::Caller,
    method: &Method,
    uri: &Uri,
    body: &Bytes,
    now: f64,
) -> Result<Args, Refusal> {
    // (4) A ban is not a low budget: it is checked first and it ends here.
    if let Some((subject, ban)) = acl.banned(caller.address.as_deref(), &caller.ip) {
        return Err(Refusal::new(
            StatusCode::FORBIDDEN,
            "banned",
            format!(
                "{subject} is banned from this module{}",
                if ban.reason.is_empty() { String::new() } else { format!(" — {}", ban.reason) }
            ),
        ));
    }

    // (5) Role. `public_read` routes drop to reader-only when the owner has
    // flipped the module private; management routes are never public anyway.
    let mut need = route.need;
    if acl.is_private() && route.public_read && need == Role::Anon {
        need = Role::Reader;
    }
    if role < need {
        let hint = match (caller.address.as_deref(), &caller.token_error) {
            (_, Some(e)) => format!("your token was rejected: {e}"),
            (None, None) => "send a signed token as `Authorization: Bearer …` \
                             (mint one with `m github/token`)"
                .into(),
            (Some(a), None) => format!("{a} has role `{}` — ask an admin for `{}`", role.name(), need.name()),
        };
        return Err(Refusal::new(
            StatusCode::from_u16(if caller.address.is_some() { 403 } else { 401 }).unwrap(),
            "unauthorized",
            format!("{} needs role `{}` — {hint}", route.path, need.name()),
        )
        .with(serde_json::json!({ "need": need.name(), "have": role.name() })));
    }

    // (6) Budget.
    let b = acl.budget(role);
    let charge = st.buckets.charge(&caller.principal(), route.cost, b.burst, b.per_minute, now);
    if !charge.ok {
        return Err(Refusal::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            format!(
                "{} costs {} and you have {:.0} left — role `{}` refills {}/min",
                route.path, route.cost, charge.remaining, role.name(), b.per_minute
            ),
        )
        .with(serde_json::json!({
            "retry_after": charge.retry_after,
            "cost": route.cost,
            "remaining": charge.remaining.round(),
            "budget": { "burst": b.burst, "per_minute": b.per_minute },
        })));
    }

    // (7) Parameters, from the query string on GET and the JSON body on POST.
    let mut raw: BTreeMap<String, String> = BTreeMap::new();
    if method == Method::GET {
        for (k, v) in form_urlencoded(uri.query().unwrap_or("")) {
            raw.insert(k, v);
        }
    } else {
        if body.len() > MAX_BODY {
            return Err(Refusal::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "invalid",
                format!("body is {} bytes; the limit is {MAX_BODY}", body.len()),
            ));
        }
        if !body.is_empty() {
            let v: serde_json::Value = serde_json::from_slice(body)
                .map_err(|e| Refusal::new(StatusCode::BAD_REQUEST, "invalid", format!("body is not JSON: {e}")))?;
            let Some(obj) = v.as_object() else {
                return Err(Refusal::new(StatusCode::BAD_REQUEST, "invalid", "body must be a JSON object"));
            };
            for (k, val) in obj {
                raw.insert(
                    k.clone(),
                    match val {
                        serde_json::Value::String(s) => s.clone(),
                        serde_json::Value::Null => continue,
                        other => other.to_string(),
                    },
                );
            }
        }
    }
    validate::check(route, &raw)
        .map_err(|e| Refusal::new(StatusCode::BAD_REQUEST, "invalid", e))
}

// ---------------------------------------------------------------------------
// handlers — no access decisions past this line
// ---------------------------------------------------------------------------

type Out = Result<serde_json::Value, (StatusCode, String)>;

async fn dispatch(
    st: &AppState,
    route: &policy::Route,
    acl: &Acl,
    role: Role,
    caller: &crate::auth::Caller,
    a: Args,
    now: f64,
) -> Out {
    match route.path {
        "/health" => Ok(serde_json::json!({ "ok": true, "uptime": (now - st.started).round() })),
        "/info" => Ok(info(st, acl, now)),
        "/policy" => Ok(policy_view(acl)),
        "/whoami" => Ok(whoami(st, acl, role, caller, now)),
        "/expand" => {
            let q = a.s("query").unwrap_or_default();
            let p = expand::plan(&q);
            Ok(serde_json::json!({
                "query": q, "terms": p.terms, "queries": p.queries, "topics": p.topics,
                "note": "these are the lexical queries GitHub will actually be asked",
            }))
        }
        "/candidates" => {
            let q = a.s("query").unwrap_or_default();
            let c = candidates(st, &a, &q, caller).await;
            Ok(serde_json::json!({
                "query": q, "queries": c.asked, "topics": c.topics,
                "repos": c.repos, "errors": c.errors,
                "governor": governor_view(st, now),
            }))
        }
        "/search" => search(st, &a, None, caller, now).await,
        "/similar" => {
            let name = a.s("repo").unwrap_or_default();
            let (owner, repo) = validate::split_repo(&name).map_err(bad)?;
            let token = st.gh.token_for(caller.address.as_deref());
            let seed_repo = st
                .gh
                .repo(&owner, &repo, token.as_deref())
                .await
                .map_err(|e| (StatusCode::from_u16(e.status()).unwrap(), e.to_string()))?;
            let seed = [
                seed_repo.description.clone(),
                seed_repo.topics.join(" "),
                seed_repo.language.clone().unwrap_or_default(),
            ]
            .join(" ")
            .trim()
            .to_string();
            let seed = if seed.is_empty() { repo.clone() } else { seed };
            let mut out = search(st, &a, Some(&seed), caller, now).await?;
            // The seed repo is not its own neighbour.
            if let Some(arr) = out.get_mut("results").and_then(|v| v.as_array_mut()) {
                arr.retain(|r| r.get("name").and_then(|n| n.as_str()) != Some(seed_repo.name.as_str()));
                let n = a.i("n", 15) as usize;
                arr.truncate(n);
            }
            out["seed_repo"] = serde_json::json!(seed_repo.name);
            out["query"] = serde_json::json!(seed);
            Ok(out)
        }
        "/repo" => {
            let name = a.s("repo").unwrap_or_default();
            let (owner, repo) = validate::split_repo(&name).map_err(bad)?;
            let token = st.gh.token_for(caller.address.as_deref());
            if !st.governor.take(now) {
                return Err((StatusCode::TOO_MANY_REQUESTS, governor_msg(st)));
            }
            st.gh
                .repo(&owner, &repo, token.as_deref())
                .await
                .map(|r| serde_json::to_value(r).unwrap_or_default())
                .map_err(|e| (StatusCode::from_u16(e.status()).unwrap(), e.to_string()))
        }
        "/readme" => {
            let name = a.s("repo").unwrap_or_default();
            let (owner, repo) = validate::split_repo(&name).map_err(bad)?;
            let n = a.i("n", 4000) as usize;
            let branch = a.s("branch");
            let text = readme(st, &owner, &repo, branch.as_deref(), a.b("fresh").unwrap_or(false)).await;
            Ok(serde_json::json!({
                "repo": format!("{owner}/{repo}"),
                "chars": text.chars().count().min(n),
                "readme": text.chars().take(n).collect::<String>(),
                "source": "raw.githubusercontent.com (keyless, outside the API rate limiter)",
            }))
        }
        "/trending" => {
            let days = a.i("days", 7);
            let n = a.i("n", 20);
            let since = days_ago(days);
            let mut q = format!("created:>{since}");
            if let Some(l) = a.s("language") {
                q.push_str(&format!(" language:{l}"));
            }
            if !st.governor.take(now) {
                return Err((StatusCode::TOO_MANY_REQUESTS, governor_msg(st)));
            }
            let token = st.gh.token_for(caller.address.as_deref());
            let repos = st
                .gh
                .search(&q, n, 1, Some("stars"), token.as_deref())
                .await
                .map_err(|e| (StatusCode::from_u16(e.status()).unwrap(), e.to_string()))?;
            Ok(serde_json::json!({
                "window_days": days, "language": a.s("language"), "query": q, "repos": repos,
                "note": "GitHub has no public trending API — this is 'created recently, most stars', \
                         which is the honest keyless approximation",
            }))
        }
        "/rate" => {
            if !st.governor.take(now) {
                return Err((StatusCode::TOO_MANY_REQUESTS, governor_msg(st)));
            }
            let token = st.gh.token_for(caller.address.as_deref());
            let v = st
                .gh
                .rate(token.as_deref())
                .await
                .map_err(|e| (StatusCode::from_u16(e.status()).unwrap(), e.to_string()))?;
            let pick = |k: &str| {
                let r = v.get("resources").and_then(|r| r.get(k));
                serde_json::json!({
                    "limit": r.and_then(|x| x.get("limit")),
                    "remaining": r.and_then(|x| x.get("remaining")),
                    "resets_in": r
                        .and_then(|x| x.get("reset"))
                        .and_then(|x| x.as_f64())
                        .map(|t| (t - now).max(0.0).round()),
                })
            };
            Ok(serde_json::json!({
                "core": pick("core"), "search": pick("search"),
                "authenticated": token.is_some(),
                "governor": governor_view(st, now),
            }))
        }
        "/cache" => {
            let (n, keys) = st.cache.summary(20);
            Ok(serde_json::json!({
                "entries": n, "search_ttl": SEARCH_TTL, "readme_ttl": README_TTL,
                "path": store::path("cache.json"), "warm": keys,
                "note": "keys and ages only — cached bodies are never served from here",
            }))
        }
        "/access" => Ok(access_view(acl)),
        "/github" => {
            let login = st.gh.account_for(caller.address.as_deref());
            Ok(serde_json::json!({
                "address": caller.address,
                "login": login,
                "connected": login.is_some(),
                "stored_by": "the git module (~/.mod/git/github.json, 0600)",
                "note": "searching works without this — a login only raises the rate limit",
            }))
        }
        "/clear_cache" => {
            let n = st.cache.clear();
            Ok(serde_json::json!({ "cleared": n }))
        }
        "/connect" => {
            let target = admin_target(&a, role, caller)?;
            let token = a.s("token").unwrap_or_default();
            let r = crate::delegate::git_call(
                "connect",
                serde_json::json!({ "token": token, "address": target }),
            )
            .map_err(|e| (StatusCode::BAD_GATEWAY, e))?;
            Ok(scrub(r))
        }
        "/disconnect" => {
            let target = admin_target(&a, role, caller)?;
            let r = crate::delegate::git_call(
                "disconnect",
                serde_json::json!({ "address": target, "login": a.s("login") }),
            )
            .map_err(|e| (StatusCode::BAD_GATEWAY, e))?;
            Ok(scrub(r))
        }
        "/grant" => {
            let addr = a.s("address").unwrap_or_default();
            let want = a.s("role").unwrap_or_else(|| "write".into());
            let Some(r) = Role::parse(&want).filter(|r| Role::GRANTABLE.contains(r)) else {
                return Err(bad(format!(
                    "role must be one of reader, write, admin — got {want:?}. Ownership is \
                     transferred on the box, not over HTTP"
                )));
            };
            // An admin cannot mint another admin; only the owner can.
            if r == Role::Admin && role < Role::Owner {
                return Err((
                    StatusCode::FORBIDDEN,
                    "only the owner may grant `admin`".into(),
                ));
            }
            if !addr.starts_with("0x") {
                return Err(bad("address must be a 0x key"));
            }
            let mut acl = acl.clone();
            acl.grants.insert(
                addr.clone(),
                Grant {
                    role: r.name().into(),
                    granted_at: now as i64,
                    granted_by: caller.address.clone(),
                },
            );
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        "/revoke" => {
            let addr = a.s("address").unwrap_or_default();
            let mut acl = acl.clone();
            let before = acl.grants.len();
            acl.grants.retain(|k, _| !k.eq_ignore_ascii_case(&addr));
            if acl.grants.len() == before {
                return Err((StatusCode::NOT_FOUND, format!("{addr} holds no grant here")));
            }
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        "/ban" => {
            let subject = a.s("subject").unwrap_or_default();
            if !(subject.starts_with("0x") || subject.starts_with("ip:")) {
                return Err(bad("subject must be a 0x key or ip:<addr>"));
            }
            // Guard against locking the module's own operators out.
            if acl.owner().map(|o| o.eq_ignore_ascii_case(&subject)).unwrap_or(false) {
                return Err((StatusCode::CONFLICT, "the owner cannot be banned".into()));
            }
            let mut acl = acl.clone();
            acl.bans.insert(
                subject,
                Ban {
                    reason: a.s("reason").unwrap_or_default(),
                    at: now as i64,
                    by: caller.address.clone(),
                },
            );
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        "/unban" => {
            let subject = a.s("subject").unwrap_or_default();
            let mut acl = acl.clone();
            let before = acl.bans.len();
            acl.bans.retain(|k, _| !k.eq_ignore_ascii_case(&subject));
            if acl.bans.len() == before {
                return Err((StatusCode::NOT_FOUND, format!("{subject} is not banned")));
            }
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        "/audit" => {
            let n = a.i("n", 100) as usize;
            let rows = audit::tail(n, a.s("subject").as_deref(), a.b("denied").unwrap_or(false));
            Ok(serde_json::json!({
                "entries": rows,
                "denials_last_hour": audit::denials(3600.0, now),
                "path": store::path("audit.jsonl"),
            }))
        }
        "/visibility" => {
            let mode = a.s("mode").unwrap_or_default().to_lowercase();
            if mode != "public" && mode != "private" {
                return Err(bad("mode must be `public` or `private`"));
            }
            let mut acl = acl.clone();
            acl.visibility = mode;
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        "/limits" => {
            let r = a.s("role").unwrap_or_default();
            let Some(parsed) = Role::parse(&r) else {
                return Err(bad("role must be one of anon, reader, write, admin, owner"));
            };
            let mut acl = acl.clone();
            acl.limits.insert(
                parsed.name().into(),
                crate::acl::Budget {
                    burst: a.i("burst", 60) as u32,
                    per_minute: a.i("per_minute", 30) as u32,
                },
            );
            acl.save().map_err(io_err)?;
            Ok(access_view(&acl))
        }
        other => Err((StatusCode::NOT_FOUND, format!("unrouted: {other}"))),
    }
}

/// Only an admin may act for a key other than their own; everyone else acts as
/// themselves regardless of what they put in the body.
fn admin_target(
    a: &Args,
    role: Role,
    caller: &crate::auth::Caller,
) -> Result<Option<String>, (StatusCode, String)> {
    match a.s("address") {
        Some(addr) => {
            let mine = caller.address.as_deref().map(|m| m.eq_ignore_ascii_case(&addr)).unwrap_or(false);
            if mine || role >= Role::Admin {
                Ok(Some(addr))
            } else {
                Err((
                    StatusCode::FORBIDDEN,
                    format!("only an admin may act for another key — you are {}", role.name()),
                ))
            }
        }
        None => Ok(caller.address.clone()),
    }
}

/// Strip anything credential-shaped out of a delegated result before it goes
/// back over the wire. The git module already avoids returning tokens; this is
/// the belt to that suspenders, because the cost of being wrong is a leaked PAT.
fn scrub(mut v: serde_json::Value) -> serde_json::Value {
    fn walk(v: &mut serde_json::Value) {
        match v {
            serde_json::Value::Object(o) => {
                for k in ["token", "access_token", "pat", "secret", "client_secret"] {
                    if o.contains_key(k) {
                        o.insert(k.into(), serde_json::json!("<redacted>"));
                    }
                }
                for (_, val) in o.iter_mut() {
                    walk(val);
                }
            }
            serde_json::Value::Array(a) => a.iter_mut().for_each(walk),
            _ => {}
        }
    }
    walk(&mut v);
    v
}

struct Pool {
    repos: Vec<Repo>,
    asked: Vec<String>,
    topics: Vec<String>,
    errors: Vec<String>,
}

/// Stage 2 for real: run the expanded queries, unioned, cached, and governed.
async fn candidates(st: &AppState, a: &Args, query: &str, caller: &crate::auth::Caller) -> Pool {
    let plan = expand::plan(query);
    let mut qualifiers = String::new();
    if let Some(l) = a.s("language") {
        qualifiers.push_str(&format!(" language:{l}"));
    }
    if let Some(s) = a.get("stars").and_then(|v| v.int()) {
        qualifiers.push_str(&format!(" stars:>={s}"));
    }
    let pages = a.i("pages", 1).clamp(1, 3);
    let sort = a.s("sort");
    let fresh = a.b("fresh").unwrap_or(false);
    let token = st.gh.token_for(caller.address.as_deref());

    let mut repos: Vec<Repo> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut asked = Vec::new();
    let mut errors = Vec::new();

    for (i, q) in plan.queries.iter().enumerate() {
        // The first (strongest) query also gets the best topic filter.
        let variants: Vec<String> = if i == 0 && !plan.topics.is_empty() {
            vec![q.clone(), format!("{q} {}", plan.topics[0])]
        } else {
            vec![q.clone()]
        };
        for v in variants {
            let full = format!("{v}{qualifiers}").trim().to_string();
            for page in 1..=pages {
                let key = format!("search:{full}:{:?}:40:{page}", sort);
                let hit = if fresh { None } else { st.cache.get(&key, SEARCH_TTL) };
                let items: Vec<Repo> = match hit {
                    Some(v) => serde_json::from_value(v).unwrap_or_default(),
                    None => {
                        // The upstream governor, at the only place it can
                        // honestly be applied: one token per outbound call.
                        if !st.governor.take(store::now()) {
                            errors.push(format!("{full}: {}", governor_msg(st)));
                            continue;
                        }
                        match st.gh.search(&full, 40, page, sort.as_deref(), token.as_deref()).await {
                            Ok(items) => {
                                st.cache.put(&key, serde_json::to_value(&items).unwrap_or_default());
                                items
                            }
                            Err(e) => {
                                // One starved query must not sink the search —
                                // rank whatever the earlier ones brought back.
                                errors.push(format!("{full}: {e}"));
                                vec![]
                            }
                        }
                    }
                };
                asked.push(full.clone());
                for r in items {
                    if !r.name.is_empty() && seen.insert(r.name.clone()) {
                        repos.push(r);
                    }
                }
            }
        }
    }
    Pool { repos, asked, topics: plan.topics, errors }
}

async fn readme(st: &AppState, owner: &str, name: &str, branch: Option<&str>, fresh: bool) -> String {
    let key = format!("readme:{owner}/{name}");
    if !fresh {
        if let Some(v) = st.cache.get(&key, README_TTL) {
            if let Some(s) = v.as_str() {
                return s.to_string();
            }
        }
    }
    let text = st.gh.readme(owner, name, branch).await;
    st.cache.put(&key, serde_json::json!(text));
    text
}

async fn search(
    st: &AppState,
    a: &Args,
    seed: Option<&str>,
    caller: &crate::auth::Caller,
    now: f64,
) -> Out {
    let t0 = std::time::Instant::now();
    let query = seed.map(String::from).or_else(|| a.s("query")).unwrap_or_default();
    let pool = candidates(st, a, &query, caller).await;

    // READMEs are free (raw.githubusercontent is outside the API limiter), so
    // the ranker reads real text rather than guessing from titles.
    let k = a.i("readmes", 25).clamp(0, 40) as usize;
    let mut readmes = std::collections::HashMap::new();
    let wanted: Vec<Repo> = pool.repos.iter().take(k).cloned().collect();
    let fetched = futures_join(wanted.iter().map(|r| async {
        let (o, n) = r.name.split_once('/').unwrap_or((r.name.as_str(), ""));
        (r.name.clone(), readme(st, o, n, None, false).await)
    }))
    .await;
    for (name, text) in fetched {
        readmes.insert(name, text);
    }

    let n = a.i("n", 20).clamp(1, 100) as usize;
    let ranked = rank::rank(
        &st.embedder,
        &query,
        pool.repos.clone(),
        &readmes,
        n,
        a.b("dense"),
        a.b("explain").unwrap_or(false),
    )
    .await;

    Ok(serde_json::json!({
        "query": query,
        "queries": pool.asked,
        "topics": pool.topics,
        "candidates": pool.repos.len(),
        "returned": ranked.repos.len(),
        "ranker": ranked.ranker,
        "authenticated": st.gh.token_for(caller.address.as_deref()).is_some(),
        "took": (t0.elapsed().as_millis() as f64 / 1000.0 * 100.0).round() / 100.0,
        "errors": pool.errors,
        "results": ranked.repos,
        "governor": governor_view(st, now),
    }))
}

/// Run a handful of futures concurrently. Bounded by the caller (≤40 READMEs),
/// so a plain join is the right amount of machinery.
async fn futures_join<F, T>(futs: impl Iterator<Item = F>) -> Vec<T>
where
    F: std::future::Future<Output = T>,
{
    let mut set = Vec::new();
    for f in futs {
        set.push(f);
    }
    let mut out = Vec::with_capacity(set.len());
    // Eight at a time: enough to hide the latency, few enough to be a polite
    // client of a host that is doing us a favour by being keyless.
    for chunk in set.chunks_mut(8) {
        let mut joined = Vec::new();
        for f in chunk.iter_mut() {
            joined.push(f);
        }
        for r in futures_all(joined).await {
            out.push(r);
        }
    }
    out
}

async fn futures_all<T>(futs: Vec<&mut (impl std::future::Future<Output = T> + Unpin)>) -> Vec<T> {
    let mut out = Vec::with_capacity(futs.len());
    for f in futs {
        out.push(f.await);
    }
    out
}

// --- views ------------------------------------------------------------------

fn info(st: &AppState, acl: &Acl, now: f64) -> serde_json::Value {
    let (entries, _) = st.cache.summary(0);
    serde_json::json!({
        "name": "github",
        "version": st.version,
        "description": "Semantic repo search over GitHub with no API key and no login — \
                        expand, retrieve, rank — behind a policy table that is the module's \
                        single access decision.",
        "keyless": true,
        "stages": [
            "expand → lexical queries (local, free)",
            "retrieve → public search API (governed)",
            "rank → embeddings if configured, else TF-IDF",
        ],
        "ranker": st.embedder.label(),
        "embeddings": st.embedder.available(),
        "visibility": acl.visibility,
        "owner": acl.owner(),
        "cache": { "entries": entries, "ttl": SEARCH_TTL },
        "governor": governor_view(st, now),
        "uptime": (now - st.started).round(),
        "policy": "GET /policy",
        "try": "GET /search?query=run%20untrusted%20wasm%20in%20a%20sandbox",
    })
}

fn policy_view(acl: &Acl) -> serde_json::Value {
    let routes: Vec<serde_json::Value> = policy::ROUTES
        .iter()
        .map(|r| {
            let effective = if acl.is_private() && r.public_read && r.need == Role::Anon {
                Role::Reader
            } else {
                r.need
            };
            serde_json::json!({
                "method": r.method,
                "path": r.path,
                "role": r.need.name(),
                "effective_role": effective.name(),
                "cost": r.cost,
                "github_calls_worst_case": r.upstream,
                "params": r.params,
                "docs": r.docs,
            })
        })
        .collect();
    serde_json::json!({
        "visibility": acl.visibility,
        "roles": ["anon", "reader", "write", "admin", "owner"],
        "budgets": acl.limits,
        "auth": "Authorization: Bearer <signed mod token> — `m github/token`, or sign in \
                 with a browser wallet. Verified here by recovering the signer; no session \
                 table, nothing to steal.",
        "enforcement": [
            "unknown path → 404 before any work",
            "ban → 403, checked before role and budget",
            "role < required → 401/403",
            "budget exhausted → 429 with Retry-After",
            "undeclared parameter → 400 (allow-list, not filter)",
            "every decision, allowed or refused, is appended to the audit log",
        ],
        "routes": routes,
    })
}

fn access_view(acl: &Acl) -> serde_json::Value {
    serde_json::json!({
        "owner": acl.owner(),
        "visibility": acl.visibility,
        "grants": acl.grants,
        "bans": acl.bans,
        "budgets": acl.limits,
        "note": "published on purpose — a gate you cannot inspect is not a gate you can trust",
    })
}

fn whoami(
    st: &AppState,
    acl: &Acl,
    role: Role,
    caller: &crate::auth::Caller,
    now: f64,
) -> serde_json::Value {
    let b = acl.budget(role);
    let left = st.buckets.peek(&caller.principal(), b.burst, b.per_minute, now);
    let can: Vec<&str> = policy::ROUTES
        .iter()
        .filter(|r| {
            let need = if acl.is_private() && r.public_read && r.need == Role::Anon {
                Role::Reader
            } else {
                r.need
            };
            role >= need
        })
        .map(|r| r.path)
        .collect();
    serde_json::json!({
        "address": caller.address,
        "ip": caller.ip,
        "principal": caller.principal(),
        "role": role.name(),
        "authenticated": caller.address.is_some(),
        "token_error": caller.token_error,
        "banned": acl.banned(caller.address.as_deref(), &caller.ip).map(|(s, _)| s),
        "budget": { "burst": b.burst, "per_minute": b.per_minute, "remaining": left.round() },
        "github_account": st.gh.account_for(caller.address.as_deref()),
        "can": can,
    })
}

fn governor_view(st: &AppState, now: f64) -> serde_json::Value {
    serde_json::json!({
        "github_calls_left": st.governor.remaining(now).floor(),
        "per_minute": st.governor.per_minute(),
        "why": "one process-wide budget for api.github.com, so this module cannot burn the \
                box's shared GitHub quota faster than GitHub refills it",
    })
}

fn governor_msg(st: &AppState) -> String {
    format!(
        "held back by this module's own GitHub governor ({}/min) so the box's shared quota \
         survives — retry shortly, or connect an account for a bigger upstream allowance",
        st.governor.per_minute()
    )
}

// --- plumbing ---------------------------------------------------------------

fn bad(msg: impl Into<String>) -> (StatusCode, String) {
    (StatusCode::BAD_REQUEST, msg.into())
}

fn io_err(e: std::io::Error) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, format!("could not persist the change: {e}"))
}

fn json(status: StatusCode, body: serde_json::Value) -> Response {
    (status, cors_headers(), Json(body)).into_response()
}

fn cors_headers() -> HeaderMap {
    let mut h = HeaderMap::new();
    h.insert("access-control-allow-origin", "*".parse().unwrap());
    h.insert("access-control-allow-methods", "GET,POST,OPTIONS".parse().unwrap());
    h.insert("access-control-allow-headers", "Content-Type,Authorization".parse().unwrap());
    h
}

/// The gateway can deliver the same endpoint three ways — `/api/github/search`
/// arrives stripped as `/search`, the app route keeps `/github/...`, and a
/// direct caller may use `/api/...`. All three mean the same route.
fn normalize(path: &str) -> String {
    let mut p = path.trim_end_matches('/').to_string();
    if p.is_empty() {
        p = "/".into();
    }
    for prefix in ["/github", "/api"] {
        if p == prefix {
            p = "/".into();
        } else if let Some(rest) = p.strip_prefix(&format!("{prefix}/")) {
            p = format!("/{rest}");
        }
    }
    if p == "/" {
        p = "/info".into();
    }
    p
}

/// Trust `X-Forwarded-For` only from a loopback peer — which, behind the mod
/// gateway, is the only place a proxied request can come from. A direct caller
/// cannot spoof their way out of an IP ban by setting the header themselves.
fn client_ip(headers: &HeaderMap, peer: SocketAddr) -> String {
    let peer_ip = peer.ip();
    if peer_ip.is_loopback() {
        if let Some(fwd) = headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()) {
            if let Some(first) = fwd.split(',').next().map(|s| s.trim()).filter(|s| !s.is_empty()) {
                return first.to_string();
            }
        }
    }
    peer_ip.to_string()
}

fn form_urlencoded(q: &str) -> Vec<(String, String)> {
    q.split('&')
        .filter(|s| !s.is_empty())
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
            Some((
                urlencoding::decode(k).ok()?.into_owned(),
                urlencoding::decode(v).ok()?.into_owned(),
            ))
        })
        .collect()
}

fn days_ago(days: i64) -> String {
    let secs = store::now() as i64 - days * 86_400;
    // Civil date from a unix timestamp (Howard Hinnant's algorithm) — a whole
    // date crate would be a dependency for one format string.
    let z = secs / 86_400 + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}")
}
