//! Stage 3 — re-rank the pool against the *original* question.
//!
//!     score = 0.75·semantic + 0.15·topic-overlap + 0.10·popularity
//!
//! The priors are small on purpose. They break ties between repos that are
//! equally on-topic; they must never float a famous irrelevant repo above an
//! obscure exact match, because that is precisely the failure of plain GitHub
//! search that this module exists to fix. Archived repos are scored ×0.85 —
//! still findable, just not preferred.
//!
//! Two rankers, and the response always says which one actually ran:
//!
//!   tfidf   pure Rust, always available. idf is computed over the candidate
//!           pool itself — small (tens of repos) and topically tight, so a
//!           rare discriminating word scores high exactly where it should.
//!   dense   an OpenAI-shaped /embeddings endpoint, when one is configured.
//!           `src/embed/serve.py` is a local, keyless one (MiniLM on CPU); any
//!           other provider works the same way.
//!
//! With no embeddings endpoint the expansion stage is what carries the
//! meaning, so results degrade rather than break.

use std::collections::HashMap;

use crate::expand::tokens;
use crate::gh::Repo;

pub struct Embedder {
    url: Option<String>,
    model: String,
    key: Option<String>,
    http: reqwest::Client,
}

impl Embedder {
    pub fn from_env() -> Self {
        let url = std::env::var("GITHUB_EMBED_URL").ok().filter(|s| !s.trim().is_empty());
        Self {
            url,
            model: std::env::var("GITHUB_EMBED_MODEL")
                .unwrap_or_else(|_| "all-MiniLM-L6-v2".into()),
            key: std::env::var("GITHUB_EMBED_KEY").ok().filter(|s| !s.trim().is_empty()),
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap_or_default(),
        }
    }

    pub fn available(&self) -> bool {
        self.url.is_some()
    }

    pub fn label(&self) -> String {
        match &self.url {
            Some(_) => format!("dense:{}", self.model),
            None => "tfidf".into(),
        }
    }

    /// Cosine of each doc against the query, or `None` so the caller falls
    /// back. A flaky embeddings provider must not be able to fail a search.
    async fn cosines(&self, query: &str, docs: &[String]) -> Option<Vec<f64>> {
        let url = self.url.as_ref()?;
        let mut input = vec![query.to_string()];
        input.extend(docs.iter().cloned());
        let mut req = self
            .http
            .post(format!("{}/embeddings", url.trim_end_matches('/')))
            .json(&serde_json::json!({ "model": self.model, "input": input }));
        if let Some(k) = &self.key {
            req = req.bearer_auth(k);
        }
        let v: serde_json::Value = req.send().await.ok()?.json().await.ok()?;
        let data = v.get("data")?.as_array()?;
        if data.len() != docs.len() + 1 {
            return None;
        }
        let vecs: Vec<Vec<f64>> = data
            .iter()
            .map(|d| {
                d.get("embedding")
                    .and_then(|e| e.as_array())
                    .map(|a| a.iter().filter_map(|x| x.as_f64()).collect())
                    .unwrap_or_default()
            })
            .collect();
        let q = &vecs[0];
        if q.is_empty() {
            return None;
        }
        Some(vecs[1..].iter().map(|d| cosine(q, d)).collect())
    }
}

fn cosine(a: &[f64], b: &[f64]) -> f64 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let dot: f64 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f64 = a.iter().map(|x| x * x).sum::<f64>().sqrt();
    let nb: f64 = b.iter().map(|x| x * x).sum::<f64>().sqrt();
    if na == 0.0 || nb == 0.0 {
        0.0
    } else {
        dot / (na * nb)
    }
}

/// TF-IDF cosine over the candidate pool.
pub fn tfidf(query_terms: &[String], docs: &[String]) -> Vec<f64> {
    let toks: Vec<Vec<String>> = docs.iter().map(|d| tokens(d)).collect();
    let mut df: HashMap<&str, f64> = HashMap::new();
    for t in &toks {
        let uniq: std::collections::HashSet<&str> = t.iter().map(|s| s.as_str()).collect();
        for w in uniq {
            *df.entry(w).or_insert(0.0) += 1.0;
        }
    }
    let n = docs.len().max(1) as f64;
    let idf = |w: &str| -> f64 {
        let c = df.get(w).copied().unwrap_or(0.0);
        (1.0 + n / (1.0 + c)).ln()
    };
    let mut qset: HashMap<&str, f64> = HashMap::new();
    for w in query_terms {
        *qset.entry(w.as_str()).or_insert(0.0) += 1.0;
    }
    let qnorm = qset
        .iter()
        .map(|(w, v)| (v * idf(w)).powi(2))
        .sum::<f64>()
        .sqrt()
        .max(f64::EPSILON);
    toks.iter()
        .map(|t| {
            let mut tf: HashMap<&str, f64> = HashMap::new();
            for w in t {
                *tf.entry(w.as_str()).or_insert(0.0) += 1.0;
            }
            let dnorm = tf
                .iter()
                .map(|(w, v)| (v * idf(w)).powi(2))
                .sum::<f64>()
                .sqrt()
                .max(f64::EPSILON);
            let dot: f64 = qset
                .iter()
                .map(|(w, qv)| qv * idf(w).powi(2) * tf.get(w).copied().unwrap_or(0.0))
                .sum();
            dot / (qnorm * dnorm)
        })
        .collect()
}

/// The document a repo is ranked as: what it calls itself, plus what its
/// README actually says.
pub fn doc(r: &Repo, readme: &str) -> String {
    let parts = [
        r.name.replace(['/', '-'], " "),
        r.description.clone(),
        r.topics.join(" "),
        r.language.clone().unwrap_or_default(),
        readme.to_string(),
    ];
    let joined = parts.iter().filter(|p| !p.is_empty()).cloned().collect::<Vec<_>>().join(" ");
    joined.chars().take(2000).collect()
}

pub struct Ranked {
    pub repos: Vec<Repo>,
    pub ranker: String,
}

pub async fn rank(
    embedder: &Embedder,
    query: &str,
    mut repos: Vec<Repo>,
    readmes: &HashMap<String, String>,
    n: usize,
    dense: Option<bool>,
    explain: bool,
) -> Ranked {
    if repos.is_empty() {
        return Ranked { repos, ranker: "tfidf".into() };
    }
    let docs: Vec<String> = repos
        .iter()
        .map(|r| doc(r, readmes.get(&r.name).map(|s| s.as_str()).unwrap_or("")))
        .collect();
    let terms = tokens(query);

    let mut ranker = "tfidf".to_string();
    let sem = match dense {
        Some(false) => tfidf(&terms, &docs),
        _ => match embedder.cosines(query, &docs).await {
            Some(v) => {
                ranker = embedder.label();
                v
            }
            None => tfidf(&terms, &docs),
        },
    };

    let termset: std::collections::HashSet<&str> = terms.iter().map(|s| s.as_str()).collect();
    for (r, s) in repos.iter_mut().zip(sem.iter()) {
        let overlap = if terms.is_empty() {
            0.0
        } else {
            r.topics
                .iter()
                .filter(|t| termset.contains(t.to_lowercase().as_str()))
                .count() as f64
                / terms.len() as f64
        };
        // ~1.0 at a million stars, so popularity nudges rather than decides.
        let pop = ((1.0 + r.stars as f64).log10() / 6.0).min(1.0);
        let mut score = 0.75 * s + 0.15 * overlap.min(1.0) + 0.10 * pop;
        if r.archived {
            score *= 0.85;
        }
        r.score = Some((score * 10_000.0).round() / 10_000.0);
        if explain {
            r.why = Some(serde_json::json!({
                "semantic": (s * 10_000.0).round() / 10_000.0,
                "topic_overlap": (overlap * 10_000.0).round() / 10_000.0,
                "popularity": (pop * 10_000.0).round() / 10_000.0,
                "readme_used": readmes.get(&r.name).map(|t| !t.is_empty()).unwrap_or(false),
                "archived_penalty": r.archived,
                "ranker": ranker,
            }));
        }
    }
    repos.sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
    repos.truncate(n);
    Ranked { repos, ranker }
}
