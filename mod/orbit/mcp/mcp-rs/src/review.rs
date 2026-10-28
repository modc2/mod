//! Static security reviewer: pattern-match a probe's tool inventory to surface
//! capability groups and produce a `Review` without calling any LLM.

use crate::store::{Finding, Probe, Review, ServerEntry, fnv1a, now};

/// Derive a `Review` from a probe result deterministically. Every finding has
/// `source: "static"` — agent-produced findings come from a separate pass that
/// is not part of this file.
pub fn analyze(probe: &Probe, server: &ServerEntry) -> Review {
    let mut findings: Vec<Finding> = Vec::new();
    let mut caps: Vec<String> = Vec::new();

    // Fingerprint the tool list so a later probe that adds/removes tools can be
    // detected by comparing hashes.
    let tool_names: String = probe
        .tools
        .iter()
        .filter_map(|t| t.get("name").and_then(|n| n.as_str()))
        .collect::<Vec<_>>()
        .join(",");
    let tools_hash = fnv1a(&tool_names);

    for tool in &probe.tools {
        let name = tool.get("name").and_then(|n| n.as_str()).unwrap_or("");
        let desc = tool.get("description").and_then(|d| d.as_str()).unwrap_or("");
        let text = format!("{} {}", name, desc).to_lowercase();

        // exec — arbitrary command / shell execution
        if !caps.contains(&"exec".to_string()) {
            if text.contains("exec")
                || text.contains("shell")
                || text.contains("bash")
                || text.contains("command")
                || text.contains("spawn")
                || text.contains("eval")
                || text.contains("subprocess")
                || word_in(&text, "run")
            {
                caps.push("exec".to_string());
                findings.push(Finding {
                    severity: "high".to_string(),
                    title: "Command execution capability".to_string(),
                    detail: "This server can execute arbitrary commands, shells, or subprocesses."
                        .to_string(),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }

        // filesystem-write — creating, deleting, or modifying files
        if !caps.contains(&"filesystem-write".to_string()) {
            if text.contains("write")
                || text.contains("delete")
                || text.contains("create")
                || text.contains("mkdir")
                || text.contains("chmod")
                || word_in(&text, "rm")
            {
                caps.push("filesystem-write".to_string());
                findings.push(Finding {
                    severity: "medium".to_string(),
                    title: "Filesystem write capability".to_string(),
                    detail: "This server can write, create, or delete files and directories."
                        .to_string(),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }

        // filesystem-read — reading or listing files; only flag when the tool
        // is clearly about the filesystem, not generic list/read patterns.
        if !caps.contains(&"filesystem-read".to_string()) {
            if (word_in(&text, "read") || word_in(&text, "list"))
                && (text.contains("file")
                    || text.contains("dir")
                    || text.contains("path")
                    || text.contains("folder"))
            {
                caps.push("filesystem-read".to_string());
                findings.push(Finding {
                    severity: "low".to_string(),
                    title: "Filesystem read capability".to_string(),
                    detail: "This server can read or list files and directories.".to_string(),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }

        // network — outbound HTTP or similar
        if !caps.contains(&"network".to_string()) {
            if text.contains("http")
                || text.contains("fetch")
                || text.contains("request")
                || text.contains("download")
                || word_in(&text, "curl")
            {
                caps.push("network".to_string());
                findings.push(Finding {
                    severity: "low".to_string(),
                    title: "Network request capability".to_string(),
                    detail: "This server can make outbound network requests.".to_string(),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }

        // database — SQL or query execution
        if !caps.contains(&"database".to_string()) {
            if word_in(&text, "sql")
                || text.contains("database")
                || text.contains("query")
            {
                caps.push("database".to_string());
                findings.push(Finding {
                    severity: "medium".to_string(),
                    title: "Database access capability".to_string(),
                    detail: "This server can execute database queries.".to_string(),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }

        // prompt-injection — LLM-steering text embedded in tool names or descriptions
        {
            const INJECTION_PATTERNS: &[&str] = &[
                "ignore previous",
                "ignore all",
                "disregard",
                "forget your",
                "override your",
                "new instructions",
                "you are now",
                "act as if",
                "your actual task",
                "the real instructions",
                "system prompt",
                "jailbreak",
                "[system:",
                "<instructions>",
            ];
            if let Some(&matched) = INJECTION_PATTERNS.iter().find(|&&p| text.contains(p)) {
                if !caps.contains(&"prompt-injection".to_string()) {
                    caps.push("prompt-injection".to_string());
                }
                findings.push(Finding {
                    severity: "critical".to_string(),
                    title: "Possible prompt injection in tool description".to_string(),
                    detail: format!(
                        "Tool '{}' description contains LLM-steering pattern: \"{}\".",
                        name, matched
                    ),
                    tool: name.to_string(),
                    source: "static".to_string(),
                });
            }
        }
    }

    let risk = if findings.is_empty() {
        if probe.ok { "low".to_string() } else { "unknown".to_string() }
    } else {
        max_severity(&findings)
    };

    let verdict = match risk.as_str() {
        "critical" => "reject",
        "high" => "caution",
        "medium" => "caution",
        _ => "allow",
    }
    .to_string();

    let summary = if findings.is_empty() {
        if probe.ok {
            format!(
                "No dangerous capabilities detected across {} tool{}.",
                probe.tools.len(),
                if probe.tools.len() == 1 { "" } else { "s" }
            )
        } else {
            "Server is unreachable — no tool inventory available.".to_string()
        }
    } else {
        format!(
            "{} — {} capability group{} detected: {}.",
            verdict.to_uppercase(),
            caps.len(),
            if caps.len() == 1 { "" } else { "s" },
            caps.join(", ")
        )
    };

    Review {
        url: server.location(),
        risk,
        verdict,
        summary,
        findings,
        capabilities: caps,
        tools_hash,
        tool_count: probe.tools.len() as u64,
        agent: "static".to_string(),
        agent_error: String::new(),
        checked_at: now(),
    }
}

/// True when `word` appears in `text` at a word boundary (not inside a longer
/// alphanumeric run or identifier). Prevents "rm" matching "framework" etc.
fn word_in(text: &str, word: &str) -> bool {
    let wlen = word.len();
    if wlen == 0 {
        return false;
    }
    text.match_indices(word).any(|(i, _)| {
        let before_ok = i == 0 || {
            let b = text.as_bytes()[i - 1];
            !b.is_ascii_alphanumeric() && b != b'_'
        };
        let after_ok = i + wlen >= text.len() || {
            let b = text.as_bytes()[i + wlen];
            !b.is_ascii_alphanumeric() && b != b'_'
        };
        before_ok && after_ok
    })
}

/// Highest severity present in the finding list.
fn max_severity(findings: &[Finding]) -> String {
    for sev in &["critical", "high", "medium", "low", "info"] {
        if findings.iter().any(|f| f.severity.as_str() == *sev) {
            return sev.to_string();
        }
    }
    "low".to_string()
}
