//! The agent protocol, spoken from the arena's side — and the only place it is.
//!
//! The `agent` module defines what an agent is (`GET /agents`, a registry of
//! boxes: prompt · model · toolbox · tools · memory) and what running one
//! means (`POST /run` → a step trace). This file is the arena's whole
//! dependency on that contract, so when the contract moves there is one file
//! to move with it:
//!
//!     roster()   GET  {agent}/agents     who exists, and who asked to compete
//!     run()      POST {agent}/run        one move = one run; the answer is read
//!                                        off the trace the way the protocol says
//!     sync()     the roster mirrored into the players registry
//!     board()    the roster joined to the ratings — what the console draws
//!
//! The agent module is addressed through the fleet gateway (`/api/agent`),
//! never a port, so seating an agent wakes a module the activator slept.
//! Every call is signed with this box's own protocol token: a run spends
//! model calls and the protocol meters them per address. Runs are `free` by
//! default — an arena match costs nothing unless a seat says otherwise.

use crate::store;
use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::Duration;

/// Where the agent module answers. A player's own `base` wins, then
/// `ARENA_AGENT_BASE`, then the gateway.
pub fn base(over: Option<&str>) -> String {
    over.map(str::to_string)
        .or_else(|| std::env::var("ARENA_AGENT_BASE").ok())
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("{}/api/agent", crate::mcpout::gateway()))
}

/// The agent module's own address, read off its config.json (`urls.api`) —
/// the door used when the gateway does not answer. Waking a sleeping module
/// needs the gateway; reaching an awake one does not.
fn direct() -> String {
    let cfg = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../agent/config.json");
    std::fs::read_to_string(cfg)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.pointer("/urls/api").and_then(|u| u.as_str()).map(str::to_string))
        .map(|u| u.trim_end_matches('/').to_string())
        .unwrap_or_else(|| "http://127.0.0.1:50117".into())
}

/// GET through the gateway, then straight to the module if the gateway fails.
async fn get_json(path: &str) -> Result<(String, Value), String> {
    let mut last = String::new();
    for b in [base(None), direct()] {
        let r = client().get(format!("{b}{path}")).timeout(Duration::from_secs(10)).send().await;
        match r {
            Ok(resp) => match resp.json::<Value>().await {
                Ok(v) => return Ok((b, v)),
                Err(e) => last = format!("agent module at {b} returned non-JSON: {e}"),
            },
            Err(e) => last = format!("agent module unreachable at {b}: {e}"),
        }
    }
    Err(last)
}

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(|x| x.as_str()).unwrap_or("")
}

// ── the roster ───────────────────────────────────────────────────────────

/// One agent as the protocol describes it, cut to what a seat needs.
fn agent_of(id: &str, schema: &Value) -> Value {
    let harness = s(schema, "harness");
    json!({
        "id": id,
        "name": if s(schema, "name").is_empty() { id } else { s(schema, "name") },
        "icon": s(schema, "icon"),
        "description": s(schema, "description"),
        "model": s(schema, "model"),
        "provider": s(schema, "provider"),
        "harness": harness,
        // A harness agent runs a real CLI on the host's shell, and the
        // protocol gates that to the module owner. The arena calls as this
        // box, which is not the owner, so it would be refused every move —
        // better said on the board than scored as a wall of illegal moves.
        "playable": harness.is_empty(),
        "arena": schema.get("arena").and_then(|v| v.as_bool()).unwrap_or(false),
        "owner": s(schema, "owner"),
    })
}

/// The protocol's `schemas` map, verbatim — one schema per agent, exactly as
/// the agent module serves it. Everything else here is a reading of this.
pub async fn schemas() -> Result<serde_json::Map<String, Value>, String> {
    let (_, body) = get_json("/agents").await?;
    body.get("schemas")
        .and_then(|v| v.as_object())
        .cloned()
        .ok_or_else(|| "agent module answered /agents without `schemas`".into())
}

/// `GET /agents` — every agent the protocol knows, in its own order.
pub async fn roster() -> Result<Vec<Value>, String> {
    Ok(schemas().await?.iter().map(|(id, sc)| agent_of(id, sc)).collect())
}

/// One agent, whole: the arena's cut of it, its `schema` exactly as the agent
/// protocol serves it, and its seat's full sheet if it has one. `key` is an
/// agent id, or a custom seat's player id or name (whose config names the
/// roster agent it runs as). The agent module being down degrades this — the
/// seat still answers, with `schema: null` and the error said — rather than
/// hiding a player whose matches are all on record here.
pub async fn one(key: &str) -> Result<Value, String> {
    let (schemas, error) = match schemas().await {
        Ok(s) => (s, None),
        Err(e) => (Default::default(), Some(e)),
    };
    let seat = store::read(|st| st.player(key).cloned()).filter(|p| p.kind == "agent_mod");
    let agent_id = seat
        .as_ref()
        .map(|p| p.config.get("agent").and_then(|v| v.as_str()).unwrap_or(&p.name).to_string())
        .unwrap_or_else(|| key.to_string());
    let schema = schemas.get(&agent_id);
    if schema.is_none() && seat.is_none() {
        return Err(error.map_or_else(|| format!("no agent `{key}`"), |e| format!("no agent `{key}` seated here, and {e}")));
    }
    let mut out = schema.map(|sc| agent_of(&agent_id, sc)).unwrap_or_else(|| json!({
        "id": agent_id, "name": agent_id, "playable": false, "arena": false,
    }));
    out["agent"] = json!(agent_id);
    out["schema"] = schema.cloned().unwrap_or(Value::Null);
    if let Some(p) = &seat {
        if p.name != agent_id {
            out["id"] = json!(p.name);
            out["name"] = json!(p.name);
            out["custom"] = json!(true);
            if !p.note.is_empty() {
                out["description"] = json!(p.note);
            }
        }
        out["retired"] = json!(p.config.get("retired").and_then(|v| v.as_bool()).unwrap_or(false));
        out["player"] = crate::arena::get_player(&p.id).unwrap_or(Value::Null);
    } else {
        out["player"] = Value::Null;
    }
    let shown = out["id"].as_str().unwrap_or(key).to_string();
    out["protocol"] = json!({ "base": base(None), "roster": "GET /agents", "run": "POST /run" });
    out["pages"] = json!({
        "arena": format!("/arena/agent/{shown}"),
        "agent": format!("/agent/a/{agent_id}"),
    });
    if let Some(e) = error {
        out["error"] = json!(e);
    }
    Ok(out)
}

/// Mirror the roster into the players registry: every agent that set
/// `arena: true` and can be run by this box gets a seat named after its id.
/// Entering again updates in place and keeps the record. An agent that left
/// the roster, or became a harness, is not deleted — its matches are history
/// — but it is marked `retired` so nothing seats it again.
pub async fn sync() -> Value {
    let agents = match roster().await {
        Ok(a) => a,
        Err(e) => return json!({ "ok": false, "error": e }),
    };
    let wanted: Vec<&Value> = agents
        .iter()
        .filter(|a| a["arena"].as_bool().unwrap_or(false) && a["playable"].as_bool().unwrap_or(false))
        .collect();
    let mut seated = 0;
    for a in &wanted {
        let id = s(a, "id");
        let ok = crate::arena::enter_player(&json!({
            "name": id,
            "kind": "agent_mod",
            "note": s(a, "description"),
            "config": { "agent": id },
        }))
        .is_ok();
        seated += usize::from(ok);
    }
    let live: Vec<String> = wanted.iter().map(|a| s(a, "id").to_string()).collect();
    let retired = store::write(|st| {
        let mut n = 0;
        for p in st.players.values_mut().filter(|p| p.kind == "agent_mod") {
            let agent = p.config.get("agent").and_then(|v| v.as_str()).unwrap_or(&p.name).to_string();
            let gone = !live.contains(&agent);
            let was = p.config.get("retired").and_then(|v| v.as_bool()).unwrap_or(false);
            if gone != was {
                if !p.config.is_object() {
                    p.config = json!({});
                }
                p.config["retired"] = json!(gone);
            }
            n += usize::from(gone);
        }
        n
    });
    json!({ "ok": true, "roster": agents.len(), "seated": seated, "retired": retired })
}

/// Re-read the roster every few minutes, so an agent created in the agent
/// console is on the board without anyone restarting the arena.
pub fn sync_forever() {
    tokio::spawn(async {
        loop {
            let r = sync().await;
            if r["ok"].as_bool() == Some(true) && r["seated"].as_u64().unwrap_or(0) > 0 {
                println!("arena: agent protocol — {} seated, {} retired", r["seated"], r["retired"]);
            }
            tokio::time::sleep(Duration::from_secs(300)).await;
        }
    });
}

/// The roster joined to the ratings, best first: one row per agent, whether
/// it has played or not, plus every custom seat (an agent of the roster
/// entered under another name with its own overrides). This is the agents
/// tab. `retired` names the seats whose agent left the roster.
pub async fn board() -> Value {
    let (agents, error) = match roster().await {
        Ok(a) => (a, None),
        Err(e) => (Vec::new(), Some(e)),
    };
    let (rows, retired) = store::read(|st| {
        let agent_of_seat = |p: &store::Player| {
            p.config.get("agent").and_then(|v| v.as_str()).unwrap_or(&p.name).to_string()
        };
        let is_retired = |p: &store::Player| p.config.get("retired").and_then(|v| v.as_bool()).unwrap_or(false);
        let seats: Vec<&store::Player> = st.players.values().filter(|p| p.kind == "agent_mod").collect();
        let mut rows: Vec<Value> = agents
            .iter()
            .map(|a| {
                let mut row = a.clone();
                row["agent"] = a["id"].clone();
                row["player"] = seats
                    .iter()
                    .find(|p| p.name == s(a, "id"))
                    .map(|p| p.card())
                    .unwrap_or(Value::Null);
                row
            })
            .collect();
        for p in seats.iter().filter(|p| !is_retired(p)) {
            let agent = agent_of_seat(p);
            if agents.iter().any(|a| s(a, "id") == p.name) {
                continue;
            }
            let Some(base) = agents.iter().find(|a| s(a, "id") == agent) else { continue };
            let mut row = base.clone();
            row["id"] = json!(p.name);
            row["name"] = json!(p.name);
            row["agent"] = json!(agent);
            row["custom"] = json!(true);
            row["arena"] = json!(true);
            if !p.note.is_empty() {
                row["description"] = json!(p.note);
            }
            row["player"] = p.card();
            rows.push(row);
        }
        rows.sort_by(|a, b| {
            let elo = |r: &Value| r["player"]["elo"].as_f64().unwrap_or(f64::MIN);
            let n = |r: &Value| r["player"]["matches"].as_u64().unwrap_or(0);
            (n(b) > 0)
                .cmp(&(n(a) > 0))
                .then(elo(b).partial_cmp(&elo(a)).unwrap_or(std::cmp::Ordering::Equal))
                .then(s(a, "id").cmp(s(b, "id")))
        });
        let retired: Vec<String> = seats.iter().filter(|p| is_retired(p)).map(|p| p.id.clone()).collect();
        (rows, retired)
    });
    let mut out = json!({ "base": base(None), "agents": rows, "count": rows.len(), "retired": retired });
    if let Some(e) = error {
        out["error"] = json!(e);
    }
    out
}

// ── a run ────────────────────────────────────────────────────────────────

/// What one `POST /run` came back with, read the protocol's way.
pub struct Run {
    /// The answer: the `finish` step's `params.summary`, else the last
    /// `response` — never a tool's output, which is the agent's reading, not
    /// its reply.
    pub answer: String,
    pub trace: Vec<Value>,
    pub task_id: String,
    pub agent: String,
    pub usage: Value,
}

/// The protocol's answer rule (the agent module's own `Mod.graph_answer`):
/// finish summary, else the last response. A `{"tool": "error"}` step is the
/// model call itself failing — that is an error, not a move.
pub fn answer_of(trace: &[Value]) -> Result<String, String> {
    let mut summary = String::new();
    let mut last_response = String::new();
    let mut error = String::new();
    for st in trace {
        match s(st, "tool") {
            "finish" => {
                let sm = st.get("params").map(|p| s(p, "summary")).unwrap_or("");
                if !sm.trim().is_empty() {
                    summary = sm.to_string();
                }
            }
            "response" => {
                let r = match st.get("result") {
                    Some(Value::String(x)) => x.clone(),
                    Some(v) if !v.is_null() => v.to_string(),
                    _ => String::new(),
                };
                if !r.trim().is_empty() {
                    last_response = r;
                }
            }
            "error" if error.is_empty() => error = s(st, "error").to_string(),
            _ => {}
        }
    }
    if !summary.is_empty() {
        Ok(summary)
    } else if !last_response.is_empty() {
        Ok(last_response)
    } else if !error.is_empty() {
        Err(format!("the agent's model call failed: {error}"))
    } else {
        Err("the agent finished without an answer".into())
    }
}

/// `POST /run`, signed as this box. `req` is the protocol's own request —
/// `query`, `agent_type` and whichever overrides the seat carries.
pub async fn run(base: &str, mut req: Value) -> Result<Run, String> {
    if req.get("key").and_then(|v| v.as_str()).unwrap_or("").is_empty() {
        if let Ok(tok) = crate::storelink::protocol_token().await {
            req["key"] = json!(tok);
        }
    }
    let sent = match client().post(format!("{base}/run")).json(&req).send().await {
        Ok(r) => r,
        // Only a refused connection is retried: a run that reached the
        // module may already be spending, and must not be started twice.
        Err(e) if e.is_connect() && base == crate::agentproto::base(None) => client()
            .post(format!("{}/run", direct()))
            .json(&req)
            .send()
            .await
            .map_err(|e2| format!("agent module unreachable: {e} / {e2}"))?,
        Err(e) => return Err(format!("agent module at {base} unreachable: {e}")),
    };
    let out: Value = sent
        .json()
        .await
        .map_err(|e| format!("agent module returned non-JSON: {e}"))?;
    if let Some(err) = out.get("error").and_then(|v| v.as_str()) {
        return Err(if out.get("code").and_then(|v| v.as_u64()) == Some(403) {
            format!("refused by the agent module (standing, not breakage): {err}")
        } else {
            format!("agent module: {err}")
        });
    }
    let trace: Vec<Value> = out.get("result").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let answer = answer_of(&trace)?;
    Ok(Run {
        answer,
        trace,
        task_id: s(&out, "task_id").to_string(),
        agent: s(&out, "agent_type").to_string(),
        usage: out.get("usage").cloned().unwrap_or(Value::Null),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finish_summary_wins_over_tool_output() {
        let t = vec![
            json!({"tool": "read_file", "params": {"path": "x"}, "result": "b2"}),
            json!({"tool": "response", "result": "thinking about it"}),
            json!({"tool": "finish", "params": {"summary": "e4"}}),
        ];
        assert_eq!(answer_of(&t).unwrap(), "e4");
    }

    #[test]
    fn last_response_when_no_finish_and_tool_results_ignored() {
        let t = vec![
            json!({"tool": "response", "result": "first"}),
            json!({"tool": "response", "result": "4"}),
            json!({"tool": "git", "result": "On branch dev"}),
        ];
        assert_eq!(answer_of(&t).unwrap(), "4");
    }

    #[test]
    fn model_error_is_an_error_not_a_move() {
        let t = vec![json!({"tool": "error", "error": "no key"})];
        assert!(answer_of(&t).unwrap_err().contains("no key"));
        assert!(answer_of(&[]).is_err());
    }

    #[test]
    fn harness_agents_are_not_playable() {
        let a = agent_of("claude-code", &json!({"harness": "claude", "arena": true}));
        assert_eq!(a["playable"], json!(false));
        let b = agent_of("default", &json!({"arena": true, "name": "Default"}));
        assert_eq!(b["playable"], json!(true));
        assert_eq!(b["name"], json!("Default"));
    }
}
