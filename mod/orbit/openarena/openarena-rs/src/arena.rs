//! The arena itself: entering competitors, uploading tasks, running a match
//! and keeping the ratings honest.
//!
//! A match is the unit of competition — one task, N entrants, all of them
//! answering at the same time, every answer graded by the same judge against
//! the same cases. Elo only moves when at least two competitors were on the
//! board; a solo run is practice and updates pass statistics only.

use crate::judge;
use crate::players;
use crate::store::{self, Agent, Match, Outcome, Submission, Task, START_ELO};
use serde_json::{json, Value};
use std::time::Instant;

/// Rating swing of a single head-to-head, before it is shared out over the
/// other entrants in the match.
const K: f64 = 24.0;

fn s(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

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
    out.trim_matches('-').to_string()
}

// ── tasks ────────────────────────────────────────────────────────────────

/// Upload a task. `tests` is the whole grading contract, so a task without one
/// is refused rather than quietly entered as unscoreable.
pub fn create_task(spec: &Value) -> Result<Value, String> {
    let title = s(spec, "title");
    if title.is_empty() {
        return Err("a task needs a title".into());
    }
    let mode = match s(spec, "mode").as_str() {
        "" => "io".to_string(),
        m => m.to_string(),
    };
    if mode != "io" && mode != "unit" {
        return Err(format!("unknown mode `{mode}` — expected io or unit"));
    }
    let language = match s(spec, "language").as_str() {
        "" => "any".to_string(),
        l => l.to_string(),
    };
    if language != "any" && judge::runner(&language).is_none() {
        return Err(format!(
            "unsupported language `{language}` — this arena runs {:?}",
            judge::languages()
        ));
    }
    let cases = spec
        .get("tests")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if cases.is_empty() {
        return Err("a task needs at least one test case".into());
    }

    let mut body = spec.clone();
    body["mode"] = json!(mode);
    body["language"] = json!(language);
    let slug = match s(spec, "slug").as_str() {
        "" => slugify(&title),
        v => v.to_string(),
    };

    store::write(|st| {
        if st.task(&slug).is_some() {
            return Err(format!("a task with slug `{slug}` is already in the arena"));
        }
        let id = st.next("t");
        body["id"] = json!(id);
        body["slug"] = json!(slug);
        body["created"] = json!(store::now());
        let task: Task = serde_json::from_value(body.clone())
            .map_err(|e| format!("bad task: {e}"))?;
        let view = task.view(false);
        st.tasks.insert(task.id.clone(), task);
        Ok(view)
    })
}

pub fn delete_task(key: &str) -> Result<Value, String> {
    store::write(|st| {
        let id = st.task(key).map(|t| t.id.clone()).ok_or(format!("no task `{key}`"))?;
        st.tasks.remove(&id);
        Ok(json!({ "deleted": id }))
    })
}

pub fn list_tasks(filter: &Value) -> Value {
    let q = s(filter, "q").to_lowercase();
    let tag = s(filter, "tag").to_lowercase();
    let kind = s(filter, "kind").to_lowercase();
    store::read(|st| {
        let rows: Vec<Value> = st
            .task_list()
            .into_iter()
            .filter(|t| kind.is_empty() || t.kind.to_lowercase() == kind)
            .filter(|t| tag.is_empty() || t.tags.iter().any(|x| x.to_lowercase() == tag))
            .filter(|t| {
                q.is_empty()
                    || t.title.to_lowercase().contains(&q)
                    || t.slug.contains(&q)
                    || t.statement.to_lowercase().contains(&q)
            })
            .map(|t| {
                json!({
                    "id": t.id, "slug": t.slug, "title": t.title, "kind": t.kind,
                    "language": t.language, "mode": t.mode, "tags": t.tags,
                    "author": t.author, "created": t.created,
                    "tests": t.tests.len(),
                    "hidden_tests": t.tests.iter().filter(|c| c.hidden).count(),
                })
            })
            .collect();
        json!({ "count": rows.len(), "tasks": rows })
    })
}

pub fn get_task(key: &str, reveal: bool) -> Result<Value, String> {
    store::read(|st| {
        st.task(key)
            .map(|t| t.view(reveal))
            .ok_or(format!("no task `{key}`"))
    })
}

// ── competitors ──────────────────────────────────────────────────────────

pub fn enter_agent(spec: &Value) -> Result<Value, String> {
    let name = s(spec, "name");
    if name.is_empty() {
        return Err("a competitor needs a name".into());
    }
    let kind = match s(spec, "kind").as_str() {
        "" => "agent_mod".to_string(),
        k => k.to_lowercase(),
    };
    if !players::KINDS.contains(&kind.as_str()) {
        return Err(format!(
            "unknown kind `{kind}` — expected one of {:?}",
            players::KINDS
        ));
    }
    let config = spec.get("config").cloned().unwrap_or_else(|| json!({}));
    if !config.is_object() {
        return Err("config must be an object".into());
    }
    // Fail at entry, not mid-match, when a driver has nothing to call.
    match kind.as_str() {
        "http" if config.get("url").is_none() => return Err("an http competitor needs config.url".into()),
        "ap" if config.get("base").and_then(|v| v.as_str()).is_none()
            && config.get("url").is_none() =>
        {
            return Err("an ap competitor needs config.base".into())
        }
        "static" if config.get("code").is_none() && config.get("solution").is_none() => {
            return Err("a static competitor needs config.code".into())
        }
        _ => {}
    }

    store::write(|st| {
        if st.agent(&name).is_some() {
            return Err(format!("`{name}` is already in the arena"));
        }
        let id = st.next("a");
        let agent = Agent {
            id: id.clone(),
            name,
            owner: s(spec, "owner"),
            kind,
            config,
            note: s(spec, "note"),
            elo: START_ELO,
            matches: 0,
            wins: 0,
            draws: 0,
            losses: 0,
            solved: 0,
            attempts: 0,
            score_sum: 0.0,
            ms_sum: 0,
            created: store::now(),
        };
        let card = agent.card();
        st.agents.insert(id, agent);
        Ok(card)
    })
}

pub fn remove_agent(key: &str) -> Result<Value, String> {
    store::write(|st| {
        let id = st.agent(key).map(|a| a.id.clone()).ok_or(format!("no competitor `{key}`"))?;
        st.agents.remove(&id);
        Ok(json!({ "removed": id }))
    })
}

pub fn list_agents() -> Value {
    store::read(|st| {
        let rows: Vec<Value> = st.agent_list().into_iter().map(|a| a.card()).collect();
        json!({ "count": rows.len(), "agents": rows })
    })
}

pub fn leaderboard(limit: usize) -> Value {
    store::read(|st| {
        let rows: Vec<Value> = st
            .agent_list()
            .into_iter()
            .filter(|a| a.attempts > 0)
            .take(if limit == 0 { usize::MAX } else { limit })
            .enumerate()
            .map(|(i, a)| {
                let mut c = a.card();
                c["rank"] = json!(i + 1);
                c
            })
            .collect();
        json!({
            "count": rows.len(),
            "unranked": st.agents.values().filter(|a| a.attempts == 0).count(),
            "leaderboard": rows,
        })
    })
}

// ── rating ───────────────────────────────────────────────────────────────

/// Round-robin Elo over one match: every entrant plays every other, and the
/// deltas are averaged so a crowded match is not worth more than a duel.
fn elo_deltas(ratings: &[f64], scores: &[f64]) -> Vec<f64> {
    let n = ratings.len();
    if n < 2 {
        return vec![0.0; n];
    }
    let mut out = vec![0.0; n];
    for i in 0..n {
        let mut acc = 0.0;
        for j in 0..n {
            if i == j {
                continue;
            }
            let actual = if scores[i] > scores[j] {
                1.0
            } else if (scores[i] - scores[j]).abs() < f64::EPSILON {
                0.5
            } else {
                0.0
            };
            let expected = 1.0 / (1.0 + 10f64.powf((ratings[j] - ratings[i]) / 400.0));
            acc += actual - expected;
        }
        out[i] = K * acc / (n - 1) as f64;
    }
    out
}

// ── matches ──────────────────────────────────────────────────────────────

struct Answer {
    agent: Agent,
    result: Result<players::Play, String>,
    answer_ms: u64,
}

/// Run one task against N competitors. They answer concurrently — the whole
/// point of an arena — and each answer is graded on a blocking thread.
pub async fn run_match(task_key: &str, agent_keys: &[String]) -> Result<Value, String> {
    let (task, agents) = store::read(|st| {
        let task = st.task(task_key).cloned();
        let agents: Vec<Result<Agent, String>> = agent_keys
            .iter()
            .map(|k| st.agent(k).cloned().ok_or(format!("no competitor `{k}`")))
            .collect();
        (task, agents)
    });
    let task = task.ok_or(format!("no task `{task_key}`"))?;
    if agent_keys.is_empty() {
        return Err("a match needs at least one competitor".into());
    }
    let mut entrants: Vec<Agent> = Vec::new();
    for a in agents {
        entrants.push(a?);
    }
    entrants.dedup_by(|a, b| a.id == b.id);

    let started = Instant::now();

    // Every competitor gets the same brief at the same moment.
    let mut handles = Vec::new();
    for agent in entrants {
        let t = task.clone();
        handles.push(tokio::spawn(async move {
            let t0 = Instant::now();
            let result = players::play(&agent, &t).await;
            Answer {
                agent,
                result,
                answer_ms: t0.elapsed().as_millis() as u64,
            }
        }));
    }

    let mut answers: Vec<Answer> = Vec::new();
    for h in handles {
        match h.await {
            Ok(a) => answers.push(a),
            Err(e) => return Err(format!("competitor task panicked: {e}")),
        }
    }

    // Grade off the runtime: the judge spawns processes and blocks on them.
    let mut graded: Vec<(Agent, u64, Result<players::Play, String>, Vec<crate::store::CaseResult>, u64)> =
        Vec::new();
    for a in answers {
        let (cases, judge_ms, result) = match &a.result {
            Ok(play) => {
                let t = task.clone();
                let code = play.code.clone();
                let lang = play.language.clone();
                let t0 = Instant::now();
                let out = tokio::task::spawn_blocking(move || judge::grade(&t, &code, &lang))
                    .await
                    .map_err(|e| format!("judge panicked: {e}"))?;
                let ms = t0.elapsed().as_millis() as u64;
                match out {
                    Ok(cases) => (cases, ms, Ok(())),
                    Err(e) => (vec![], ms, Err(e)),
                }
            }
            Err(e) => (vec![], 0, Err(e.clone())),
        };
        let merged = match (result, a.result) {
            (Err(e), _) => Err(e),
            (Ok(()), Ok(play)) => Ok(play),
            (Ok(()), Err(e)) => Err(e),
        };
        graded.push((a.agent, a.answer_ms, merged, cases, judge_ms));
    }

    // Score first, then settle everything under one lock.
    let scores: Vec<f64> = graded
        .iter()
        .map(|(_, _, r, cases, _)| if r.is_ok() { judge::score(cases) } else { 0.0 })
        .collect();
    let ratings: Vec<f64> = graded.iter().map(|(a, _, _, _, _)| a.elo).collect();
    let rated = graded.len() > 1;
    let deltas = if rated {
        elo_deltas(&ratings, &scores)
    } else {
        vec![0.0; graded.len()]
    };
    let best = scores.iter().cloned().fold(f64::MIN, f64::max);

    let total_ms = started.elapsed().as_millis() as u64;
    let out = store::write(|st| {
        let match_id = st.next("m");
        let mut results: Vec<Outcome> = Vec::new();

        for (i, (agent, answer_ms, play, cases, judge_ms)) in graded.into_iter().enumerate() {
            let score = scores[i];
            let passed = cases.iter().filter(|c| c.passed).count();
            let total = cases.len();
            let solved = total > 0 && passed == total;

            let (language, submission_id, error) = match play {
                Ok(p) => {
                    let sid = st.next("s");
                    st.submissions.insert(
                        sid.clone(),
                        Submission {
                            id: sid.clone(),
                            task_id: task.id.clone(),
                            agent_id: agent.id.clone(),
                            match_id: match_id.clone(),
                            language: p.language.clone(),
                            code: p.code,
                            meta: p.meta,
                            created: store::now(),
                        },
                    );
                    (p.language, sid, String::new())
                }
                Err(e) => (String::new(), String::new(), e),
            };

            let elo_before = agent.elo;
            let elo_after = elo_before + deltas[i];
            if let Some(a) = st.agents.get_mut(&agent.id) {
                a.elo = elo_after;
                a.attempts += 1;
                a.score_sum += score;
                a.ms_sum += answer_ms;
                if solved {
                    a.solved += 1;
                }
                if rated {
                    a.matches += 1;
                    if score >= best && scores.iter().filter(|x| **x >= best).count() == 1 {
                        a.wins += 1;
                    } else if score >= best {
                        a.draws += 1;
                    } else {
                        a.losses += 1;
                    }
                }
            }

            results.push(Outcome {
                agent_id: agent.id.clone(),
                agent_name: agent.name.clone(),
                submission_id,
                passed,
                total,
                score,
                answer_ms,
                judge_ms,
                elo_before,
                elo_after,
                language,
                error,
                cases,
            });
        }

        results.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.answer_ms.cmp(&b.answer_ms))
        });

        let m = Match {
            id: match_id,
            task_id: task.id.clone(),
            task_title: task.title.clone(),
            created: store::now(),
            ms: total_ms,
            rated,
            results,
        };
        let full = serde_json::to_value(&m).unwrap_or_else(|_| json!({}));
        st.matches.push(m);
        full
    });

    Ok(out)
}

/// Grade a program the arena did not ask for — a human trying a task, or an
/// external agent pushing its own answer over MCP. Unrated: nobody was raced.
pub async fn submit(
    task_key: &str,
    code: &str,
    language: &str,
    agent_key: Option<&str>,
) -> Result<Value, String> {
    let (task, agent) = store::read(|st| {
        (
            st.task(task_key).cloned(),
            agent_key.and_then(|k| st.agent(k).cloned()),
        )
    });
    let task = task.ok_or(format!("no task `{task_key}`"))?;
    if let (Some(k), None) = (agent_key, &agent) {
        return Err(format!("no competitor `{k}`"));
    }

    let t = task.clone();
    let (c, l) = (code.to_string(), language.to_string());
    let t0 = Instant::now();
    let cases = tokio::task::spawn_blocking(move || judge::grade(&t, &c, &l))
        .await
        .map_err(|e| format!("judge panicked: {e}"))??;
    let judge_ms = t0.elapsed().as_millis() as u64;

    let score = judge::score(&cases);
    let passed = cases.iter().filter(|c| c.passed).count();
    let solved = !cases.is_empty() && passed == cases.len();
    let lang = if language.trim().is_empty() {
        task.language.clone()
    } else {
        language.to_string()
    };

    let sid = store::write(|st| {
        let sid = st.next("s");
        st.submissions.insert(
            sid.clone(),
            Submission {
                id: sid.clone(),
                task_id: task.id.clone(),
                agent_id: agent.as_ref().map(|a| a.id.clone()).unwrap_or_default(),
                match_id: String::new(),
                language: lang.clone(),
                code: code.to_string(),
                meta: json!({ "driver": "submit" }),
                created: store::now(),
            },
        );
        if let Some(a) = agent.as_ref().and_then(|a| st.agents.get_mut(&a.id)) {
            a.attempts += 1;
            a.score_sum += score;
            a.ms_sum += judge_ms;
            if solved {
                a.solved += 1;
            }
        }
        sid
    });

    Ok(json!({
        "submission_id": sid,
        "task": task.id, "task_title": task.title,
        "agent": agent.map(|a| a.id).unwrap_or_default(),
        "language": lang, "rated": false,
        "passed": passed, "total": cases.len(), "score": score, "solved": solved,
        "judge_ms": judge_ms,
        "cases": cases,
    }))
}

pub fn list_matches(limit: usize) -> Value {
    store::read(|st| {
        let n = if limit == 0 { 20 } else { limit };
        let rows: Vec<Value> = st.matches.iter().rev().take(n).map(|m| m.brief()).collect();
        json!({ "count": rows.len(), "total": st.matches.len(), "matches": rows })
    })
}

pub fn get_match(id: &str) -> Result<Value, String> {
    store::read(|st| {
        let m = st
            .matches
            .iter()
            .find(|m| m.id == id)
            .ok_or(format!("no match `{id}`"))?;
        let mut v = serde_json::to_value(m).unwrap_or_else(|_| json!({}));
        // Attach the programs — a match you can't read is a scoreboard, not a record.
        if let Some(rs) = v.get_mut("results").and_then(|r| r.as_array_mut()) {
            for r in rs.iter_mut() {
                let sid = r.get("submission_id").and_then(|s| s.as_str()).unwrap_or("");
                if let Some(sub) = st.submissions.get(sid) {
                    r["code"] = json!(sub.code);
                    r["meta"] = sub.meta.clone();
                }
            }
        }
        Ok(v)
    })
}

pub fn info() -> Value {
    store::read(|st| {
        json!({
            "name": "openarena",
            "protocol": "arena/1.0",
            "tasks": st.tasks.len(),
            "agents": st.agents.len(),
            "matches": st.matches.len(),
            "submissions": st.submissions.len(),
            "languages": judge::languages(),
            "competitor_kinds": players::KINDS,
            "modes": ["io", "unit"],
            "state": store::state_dir().display().to_string(),
        })
    })
}
