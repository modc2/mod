//! Peer audits — anyone may audit any module, and everyone can read the result.
//!
//! An audit is a review of one module at one point in time, and it is
//! content-addressed twice over:
//!
//!   base_cid  — the snapshot of the module tree that was audited
//!   cid       — the published audit report itself
//!
//! Pinning the base matters more than it looks. Without it "module X was
//! audited" is a claim about a moving target; with it, anyone can restore
//! `base_cid`, re-run the same review, and check the finding still holds. It
//! also means an audit can be read as a statement about a specific version
//! rather than about the module in general.
//!
//! The run itself reuses the fork machinery: the module is staged into the
//! auditor's own workspace from its snapshot, and the agent reviews *that*
//! copy. A non-owner auditing core/chain therefore never touches core/chain —
//! they read a restored tree inside their own sandbox. That is why auditing
//! is open to any signed-in caller while editing is not.
//!
//! Storage is a table in the jobs DB (same file, same durability as the task
//! ledger); the ledger is public, like /jobs, because an audit nobody can
//! read is not an audit.

use serde::{Deserialize, Serialize};

/// How many audits a listing returns when the caller doesn't say. The console
/// shows the previous ten for a module, which is the number this exists for.
pub const DEFAULT_LIMIT: usize = 10;
const MAX_LIMIT: usize = 200;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Audit {
    pub id: String,
    /// Module under review.
    pub module: String,
    /// Address of whoever asked for the audit ("local" in local mode).
    pub auditor: String,
    /// Unix seconds — when the audit was requested. Every listing is stamped.
    pub created_at: i64,
    pub updated_at: i64,
    /// "running" | "complete" | "failed" | "cancelled".
    pub status: String,
    /// The agent job doing the work; its stream is the live audit log.
    pub job_id: String,
    /// Snapshot CID of the tree that was audited.
    pub base_cid: Option<String>,
    /// localfs CID of the published report. Present once complete.
    pub cid: Option<String>,
    /// Auditor's own framing, if they gave one.
    pub note: String,
    /// Headline the agent ended on: "pass" | "concerns" | "fail" | "".
    pub verdict: String,
    /// 0..100, parsed from the report. -1 when the agent didn't give one.
    pub score: i64,
    pub findings: i64,
    /// First lines of the report — enough for a list row.
    pub summary: String,
    /// What the audit cost its author, in USD (metered like any other task).
    pub cost_usd: String,
}

fn db_path() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home)
        .join(".mod")
        .join(crate::credits::module_name())
        .join("claude_jobs.db")
}

fn conn() -> Option<rusqlite::Connection> {
    let c = rusqlite::Connection::open(db_path()).ok()?;
    c.execute_batch(
        "CREATE TABLE IF NOT EXISTS audits (
            id TEXT PRIMARY KEY,
            module TEXT NOT NULL,
            auditor TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            job_id TEXT NOT NULL DEFAULT '',
            base_cid TEXT,
            cid TEXT,
            note TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL DEFAULT -1,
            findings INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '',
            cost_usd TEXT NOT NULL DEFAULT '0.00'
         );
         CREATE INDEX IF NOT EXISTS idx_audits_module ON audits(module, created_at DESC);
         CREATE INDEX IF NOT EXISTS idx_audits_created ON audits(created_at DESC);",
    )
    .ok()?;
    Some(c)
}

const COLS: &str = "id, module, auditor, created_at, updated_at, status, job_id, base_cid, cid, note, verdict, score, findings, summary, cost_usd";

fn row_to_audit(row: &rusqlite::Row) -> rusqlite::Result<Audit> {
    Ok(Audit {
        id: row.get(0)?,
        module: row.get(1)?,
        auditor: row.get(2)?,
        created_at: row.get(3)?,
        updated_at: row.get(4)?,
        status: row.get(5)?,
        job_id: row.get(6)?,
        base_cid: row.get(7)?,
        cid: row.get(8)?,
        note: row.get(9)?,
        verdict: row.get(10)?,
        score: row.get(11)?,
        findings: row.get(12)?,
        summary: row.get(13)?,
        cost_usd: row.get(14)?,
    })
}

pub fn insert(a: &Audit) -> Result<(), String> {
    let c = conn().ok_or("audit store unavailable")?;
    c.execute(
        "INSERT INTO audits (id, module, auditor, created_at, updated_at, status, job_id, base_cid, cid, note, verdict, score, findings, summary, cost_usd)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15)",
        rusqlite::params![
            a.id, a.module, a.auditor, a.created_at, a.updated_at, a.status, a.job_id,
            a.base_cid, a.cid, a.note, a.verdict, a.score, a.findings, a.summary, a.cost_usd
        ],
    )
    .map_err(|e| format!("audit insert: {e}"))?;
    Ok(())
}

fn one(where_clause: &str, key: &str) -> Option<Audit> {
    let c = conn()?;
    let sql = format!("SELECT {COLS} FROM audits WHERE {where_clause} LIMIT 1");
    let found = c
        .prepare(&sql)
        .ok()?
        .query_row(rusqlite::params![key], row_to_audit)
        .ok();
    found
}

pub fn get(id: &str) -> Option<Audit> {
    one("id = ?1", id)
}

/// The audit a given job belongs to, if it is an audit run at all.
pub fn by_job(job_id: &str) -> Option<Audit> {
    one("job_id = ?1", job_id)
}

/// Newest first. `module` = None lists across every module — the global feed.
pub fn list(module: Option<&str>, limit: usize) -> Vec<Audit> {
    let limit = limit.clamp(1, MAX_LIMIT) as i64;
    let Some(c) = conn() else { return Vec::new() };
    let (sql, params): (String, Vec<Box<dyn rusqlite::ToSql>>) = match module {
        Some(m) => (
            format!("SELECT {COLS} FROM audits WHERE module = ?1 ORDER BY created_at DESC LIMIT ?2"),
            vec![Box::new(m.to_string()), Box::new(limit)],
        ),
        None => (
            format!("SELECT {COLS} FROM audits ORDER BY created_at DESC LIMIT ?1"),
            vec![Box::new(limit)],
        ),
    };
    let Ok(mut stmt) = c.prepare(&sql) else { return Vec::new() };
    let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    stmt.query_map(refs.as_slice(), row_to_audit)
        .map(|rows| rows.filter_map(|r| r.ok()).collect())
        .unwrap_or_default()
}

/// Per-module rollup for the hub: how many audits, the latest verdict, and
/// the average score across scored audits.
#[derive(Serialize)]
pub struct ModuleAuditStats {
    pub module: String,
    pub audits: i64,
    pub last_at: i64,
    pub last_verdict: String,
    pub avg_score: i64,
}

pub fn stats() -> Vec<ModuleAuditStats> {
    let Some(c) = conn() else { return Vec::new() };
    let Ok(mut stmt) = c.prepare(
        "SELECT module, COUNT(*), MAX(created_at),
                COALESCE(AVG(CASE WHEN score >= 0 THEN score END), -1)
         FROM audits WHERE status = 'complete' GROUP BY module ORDER BY 3 DESC",
    ) else {
        return Vec::new();
    };
    let mut out: Vec<ModuleAuditStats> = stmt
        .query_map([], |row| {
            Ok(ModuleAuditStats {
                module: row.get(0)?,
                audits: row.get(1)?,
                last_at: row.get(2)?,
                last_verdict: String::new(),
                avg_score: row.get::<_, f64>(3)?.round() as i64,
            })
        })
        .map(|rows| rows.filter_map(|r| r.ok()).collect())
        .unwrap_or_default();
    for s in out.iter_mut() {
        if let Some(latest) = list(Some(&s.module), 1).into_iter().next() {
            s.last_verdict = latest.verdict;
        }
    }
    out
}

fn update(a: &Audit) -> Result<(), String> {
    let c = conn().ok_or("audit store unavailable")?;
    c.execute(
        "UPDATE audits SET updated_at = ?2, status = ?3, cid = ?4, verdict = ?5,
                score = ?6, findings = ?7, summary = ?8, cost_usd = ?9
         WHERE id = ?1",
        rusqlite::params![
            a.id, a.updated_at, a.status, a.cid, a.verdict, a.score, a.findings,
            a.summary, a.cost_usd
        ],
    )
    .map_err(|e| format!("audit update: {e}"))?;
    Ok(())
}

// ── Report parsing ───────────────────────────────────────────────────

/// Pull the verdict block out of an audit run's output.
///
/// The prompt asks the agent to end with a fenced `AUDIT` block, but agents
/// are not parsers — so this reads loose `KEY: value` lines anywhere in the
/// tail of the output and takes the last occurrence of each. A report that
/// misses the format still lands as an audit; it just carries no verdict.
pub fn parse_report(output: &str) -> (String, i64, i64, String) {
    let mut verdict = String::new();
    let mut score: i64 = -1;
    let mut findings: i64 = 0;
    let mut summary = String::new();

    // Only scan the tail — an audit that quotes source could otherwise
    // match a "VERDICT:" that belongs to the code being reviewed.
    let tail: String = {
        let lines: Vec<&str> = output.lines().collect();
        let start = lines.len().saturating_sub(80);
        lines[start..].join("\n")
    };

    for line in tail.lines() {
        let l = line.trim().trim_start_matches(['#', '*', '-', '`', ' ']);
        let Some((key, value)) = l.split_once(':') else { continue };
        let value = value.trim().trim_matches(['*', '`', ' ']).to_string();
        if value.is_empty() {
            continue;
        }
        match key.trim().to_ascii_uppercase().as_str() {
            "VERDICT" => {
                let v = value.to_ascii_lowercase();
                verdict = if v.starts_with("pass") {
                    "pass"
                } else if v.starts_with("fail") {
                    "fail"
                } else if v.starts_with("concern") {
                    "concerns"
                } else {
                    ""
                }
                .to_string();
            }
            "SCORE" => {
                // Tolerates "82", "82/100", "82 / 100".
                if let Some(n) = value
                    .split(['/', ' '])
                    .next()
                    .and_then(|s| s.trim().parse::<i64>().ok())
                {
                    score = n.clamp(0, 100);
                }
            }
            "FINDINGS" => {
                if let Ok(n) = value.split_whitespace().next().unwrap_or("").parse::<i64>() {
                    findings = n.max(0);
                }
            }
            "SUMMARY" => summary = value.chars().take(400).collect(),
            _ => {}
        }
    }

    if summary.is_empty() {
        // No SUMMARY line: fall back to the last non-empty prose line, which
        // is usually the agent's own closing sentence.
        summary = tail
            .lines()
            .rev()
            .map(|l| l.trim())
            .find(|l| l.len() > 24 && !l.starts_with(['#', '`', '|']))
            .unwrap_or("")
            .chars()
            .take(400)
            .collect();
    }
    (verdict, score, findings, summary)
}

/// The instructions handed to the auditing agent. Read-only by construction:
/// the agent is looking at a restored copy in the caller's own workspace, so
/// "don't change anything" is a request, not the security boundary.
pub fn audit_prompt(module: &str, note: &str) -> String {
    let focus = if note.trim().is_empty() {
        String::new()
    } else {
        format!(
            "\n\nThe person requesting this audit asked you to focus on: {}\n",
            note.trim()
        )
    };
    format!(
        "You are auditing the module `{module}`. A copy of its tree has been \
restored into this working directory from a pinned snapshot — review that copy.

This is a READ-ONLY review. Do not edit, create, delete, or run any file in \
this directory, and do not start or stop any process. Read the code and report.

Cover, in this order:
1. What the module claims to do (config.json, README, skill.md) versus what \
the code actually does.
2. Correctness — logic errors, unhandled failure paths, race conditions, \
anything that would break under normal use.
3. Security and trust — authentication and authorization gaps, secrets or \
private state committed to the tree, unvalidated input reaching the shell, \
the filesystem, or a database.
4. Protocol fit — does it follow the mod protocol conventions (config.json \
shape, endpoint/auth declarations, private state kept under ~/.mod/, ports \
that don't collide)?
5. What is good about it. An audit that only lists problems is not a useful \
review of working software.

Cite `file:line` for every finding, and be specific about the failure: what \
input or state triggers it, and what goes wrong. Say plainly when you are \
unsure rather than padding the list — a short accurate audit beats a long \
speculative one.{focus}

End your response with exactly this block, and nothing after it:

```
VERDICT: pass | concerns | fail
SCORE: <0-100>
FINDINGS: <number of distinct issues you found>
SUMMARY: <one sentence a reader can act on>
```

VERDICT is `pass` if you would be comfortable depending on this module today, \
`concerns` if it works but has issues worth fixing, and `fail` if it is broken \
or unsafe as it stands."
    )
}

/// Fold a finished audit job back into its audit record, and publish the
/// report to localfs so it has a CID of its own.
///
/// Lazy — called on read rather than from a background loop, the same way
/// merge requests reconcile. An audit whose job is still running is returned
/// unchanged.
pub fn reconcile(mut a: Audit, job: &crate::jobs::ClaudeJob) -> Audit {
    if a.status != "running" {
        return a;
    }
    let job_status = job.status.to_string();
    match job_status.as_str() {
        "completed" | "failed" | "cancelled" => {}
        _ => return a,
    }

    let (verdict, score, findings, summary) = parse_report(&job.output);
    a.verdict = verdict;
    a.score = score;
    a.findings = findings;
    a.summary = summary;
    a.status = if job_status == "completed" { "complete" } else { &job_status }.to_string();
    a.updated_at = chrono::Utc::now().timestamp();
    a.cost_usd = job.cost_usd.clone().unwrap_or_else(|| "0.00".to_string());

    // Publish the report itself. The task bundle already carries the full
    // output under the job's own CID, but an audit deserves an object that
    // says what it is — module, base, verdict — without needing the console
    // to interpret a task.
    if a.cid.is_none() && a.status == "complete" {
        a.cid = publish_report(&a, job);
    }
    let _ = update(&a);
    a
}

fn publish_report(a: &Audit, job: &crate::jobs::ClaudeJob) -> Option<String> {
    let bundle = serde_json::json!({
        "kind": "build-audit",
        "version": 1,
        "id": a.id,
        "module": a.module,
        "auditor": a.auditor,
        "created_at": a.created_at,
        "completed_at": a.updated_at,
        "base_cid": a.base_cid,
        "job_id": a.job_id,
        "job_cid": job.cid,
        "note": a.note,
        "verdict": a.verdict,
        "score": a.score,
        "findings": a.findings,
        "summary": a.summary,
        "report": job.output,
        "cost_usd": a.cost_usd,
    });
    let short: String = a.id.chars().take(8).collect();
    crate::jobs::put_blob(
        &bundle,
        &format!("build-audit-{}-{}.json", a.module, short),
        &a.auditor,
    )
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_trailing_block() {
        let out = "…lots of review…\n\n```\nVERDICT: concerns\nSCORE: 72\nFINDINGS: 4\nSUMMARY: Works, but the token check is bypassable.\n```";
        let (v, s, f, sum) = parse_report(out);
        assert_eq!(v, "concerns");
        assert_eq!(s, 72);
        assert_eq!(f, 4);
        assert!(sum.starts_with("Works, but"));
    }

    #[test]
    fn tolerates_markdown_decoration_and_score_fractions() {
        let out = "**VERDICT:** pass\n* SCORE: 91/100\n- FINDINGS: 0 issues\n## SUMMARY: Clean.";
        let (v, s, f, sum) = parse_report(out);
        assert_eq!(v, "pass");
        assert_eq!(s, 91);
        assert_eq!(f, 0);
        assert_eq!(sum, "Clean.");
    }

    #[test]
    fn a_report_without_the_block_still_yields_a_summary() {
        let out = "I read through the module and everything looks reasonable to me.";
        let (v, s, f, sum) = parse_report(out);
        assert_eq!(v, "");
        assert_eq!(s, -1);
        assert_eq!(f, 0);
        assert!(sum.contains("reasonable"));
    }

    #[test]
    fn only_the_tail_is_scanned() {
        // A quoted VERDICT far above the end must not win over the real one.
        let mut out = String::from("VERDICT: fail (this line is quoted source)\n");
        for i in 0..200 {
            out.push_str(&format!("line {i}\n"));
        }
        out.push_str("VERDICT: pass\nSCORE: 80\n");
        let (v, s, _, _) = parse_report(&out);
        assert_eq!(v, "pass");
        assert_eq!(s, 80);
    }

    #[test]
    fn score_is_clamped_to_the_scale() {
        let (_, s, _, _) = parse_report("SCORE: 4000");
        assert_eq!(s, 100);
    }

    #[test]
    fn prompt_carries_the_module_and_focus() {
        let p = audit_prompt("polymarket", "the gate logic");
        assert!(p.contains("`polymarket`"));
        assert!(p.contains("the gate logic"));
        assert!(p.contains("READ-ONLY"));
        // No focus line when no note was given.
        assert!(!audit_prompt("x", "  ").contains("asked you to focus"));
    }
}
