//! Stage 1 — the cheap half of "semantic".
//!
//! GitHub's search matches words. If the repo you want calls itself a
//! "capability based module isolation runtime" and you asked for "sandbox
//! untrusted code", it will never appear, no matter how good the ranker is.
//! So the question is tokenized, stopwords dropped, and a small hand-checked
//! lexicon turns concepts into extra lexical queries and `topic:` filters.
//!
//! Deliberately small, and exposed at `GET /expand`: this is a retrieval hint,
//! not a taxonomy, and the point is that you can read exactly what will be
//! asked rather than trusting a black box.

pub struct Plan {
    pub terms: Vec<String>,
    pub queries: Vec<String>,
    pub topics: Vec<String>,
}

static LEXICON: &[(&str, &[&str])] = &[
    ("wasm", &["webassembly", "wasm runtime", "topic:webassembly"]),
    ("webassembly", &["wasm", "wasmtime wasmer", "topic:webassembly"]),
    ("sandbox", &["sandboxing", "isolation runtime", "topic:sandbox"]),
    ("llm", &["large language model", "inference engine", "topic:llm"]),
    ("agent", &["ai agent framework", "autonomous agents", "topic:ai-agents"]),
    ("embedding", &["embeddings", "sentence transformers", "topic:embeddings"]),
    ("vector", &["vector database", "similarity search", "topic:vector-database"]),
    ("search", &["full text search", "retrieval", "topic:search"]),
    ("semantic", &["embeddings similarity", "neural search"]),
    ("p2p", &["peer to peer", "distributed network", "topic:p2p"]),
    ("blockchain", &["smart contracts", "web3", "topic:blockchain"]),
    ("crypto", &["cryptography", "encryption library", "topic:cryptography"]),
    ("db", &["database engine", "storage engine", "topic:database"]),
    ("database", &["storage engine", "query engine", "topic:database"]),
    ("queue", &["message queue", "job scheduler", "topic:message-queue"]),
    ("scraper", &["web scraping", "crawler", "topic:web-scraping"]),
    ("parser", &["parsing library", "grammar", "topic:parser"]),
    ("compiler", &["language toolchain", "codegen", "topic:compiler"]),
    ("game", &["game engine", "gamedev", "topic:gamedev"]),
    ("terminal", &["tui", "command line interface", "topic:cli"]),
    ("cli", &["command line tool", "terminal ui", "topic:cli"]),
    ("gpu", &["cuda kernels", "accelerated compute", "topic:gpu"]),
    ("ml", &["machine learning", "deep learning", "topic:machine-learning"]),
    ("api", &["rest api", "http server", "topic:api"]),
    ("auth", &["authentication", "oauth identity", "topic:authentication"]),
    ("monitoring", &["observability", "metrics tracing", "topic:monitoring"]),
    ("sync", &["synchronization", "replication", "topic:sync"]),
    ("markdown", &["markdown parser", "documentation generator"]),
    ("image", &["image processing", "computer vision", "topic:image-processing"]),
    ("audio", &["audio processing", "dsp", "topic:audio"]),
    ("video", &["video encoding", "ffmpeg", "topic:video"]),
    ("bot", &["chatbot", "automation bot", "topic:bot"]),
    ("test", &["testing framework", "test runner", "topic:testing"]),
    ("deploy", &["deployment tooling", "ci cd", "topic:devops"]),
    ("docker", &["containers", "oci images", "topic:docker"]),
    ("kubernetes", &["k8s operator", "cluster orchestration", "topic:kubernetes"]),
    ("rust", &["written in rust", "topic:rust"]),
    ("policy", &["access control", "authorization policy", "topic:authorization"]),
    ("ratelimit", &["rate limiting", "throttling", "topic:rate-limiting"]),
];

/// Words that carry no retrieval signal, so "a library that lets me…" searches
/// for the library rather than for "lets me".
static STOP: &str = "a an the and or of for to in on with without that this those these is are was
be been being it its as at by from into over under how what which who whom why when where
i me my we our you your they them their he she his her but if then than so such can could
should would may might will shall do does did done doing have has had having not no nor only
own same too very just about above below up down out off again further once here there all
any both each few more most other some like want need looking find search tool library
package framework project repo repos repository something anything way ways best good great
simple easy new using use used uses";

pub fn tokens(text: &str) -> Vec<String> {
    let stop: std::collections::HashSet<&str> = STOP.split_whitespace().collect();
    let lower = text.to_lowercase();
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in lower.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '+' | '#' | '.') {
            cur.push(c);
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out.into_iter().filter(|w| w.len() > 1 && !stop.contains(w.as_str())).collect()
}

pub fn plan(query: &str) -> Plan {
    let words = tokens(query);
    let core = if words.is_empty() {
        query.trim().to_string()
    } else {
        words.iter().take(8).cloned().collect::<Vec<_>>().join(" ")
    };
    let mut queries = vec![core];
    let mut topics: Vec<String> = Vec::new();
    for w in &words {
        if let Some((_, extras)) = LEXICON.iter().find(|(k, _)| k == w) {
            for e in *extras {
                if let Some(t) = e.strip_prefix("topic:") {
                    let t = format!("topic:{t}");
                    if !topics.contains(&t) {
                        topics.push(t);
                    }
                } else if !queries.iter().any(|q| q == e) {
                    queries.push((*e).to_string());
                }
            }
        }
    }
    // Pair the strongest expansion with the user's own words: a query that is
    // only synonyms drifts, a query that is only their words is what plain
    // GitHub search already does.
    if words.len() > 3 {
        queries.insert(1, words.iter().take(3).cloned().collect::<Vec<_>>().join(" "));
    }
    // Every duplicate is a wasted call out of ten a minute.
    let mut seen = std::collections::HashSet::new();
    queries.retain(|q| !q.trim().is_empty() && seen.insert(q.clone()));
    queries.truncate(6);
    topics.truncate(3);
    Plan { terms: words, queries, topics }
}
