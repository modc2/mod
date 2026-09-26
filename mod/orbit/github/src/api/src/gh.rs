//! Stage 2 — talking to GitHub, keylessly by default.
//!
//! Two upstreams with very different economics, and the difference is the
//! reason this module works without a login at all:
//!
//!   api.github.com          10 search calls/min anonymous. Precious. Every
//!                           call takes a token from the shared governor.
//!   raw.githubusercontent   unauthenticated AND outside the API rate limiter.
//!                           Free. Which is why the ranker can afford to read
//!                           real README text instead of guessing from titles.
//!
//! A GitHub token, when there is one, is borrowed from the `git` module's
//! store — this module never persists one. One GitHub identity per mod key,
//! kept in one place, so revoking it there revokes it here.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub const API: &str = "https://api.github.com";
pub const RAW: &str = "https://raw.githubusercontent.com";

/// The projection of a GitHub repo this module actually uses. Everything else
/// in their payload is dropped at the boundary rather than passed through.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Repo {
    pub name: String,
    pub url: String,
    pub description: String,
    pub stars: i64,
    pub forks: i64,
    pub language: Option<String>,
    pub topics: Vec<String>,
    pub license: Option<String>,
    pub pushed_at: Option<String>,
    pub created_at: Option<String>,
    pub archived: bool,
    pub owner: Option<String>,
    pub default_branch: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub open_issues: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub homepage: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<i64>,
    /// Filled in by the ranker.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub why: Option<serde_json::Value>,
}

pub fn row(r: &serde_json::Value) -> Repo {
    let s = |k: &str| r.get(k).and_then(|v| v.as_str()).map(|s| s.to_string());
    let i = |k: &str| r.get(k).and_then(|v| v.as_i64()).unwrap_or(0);
    Repo {
        name: s("full_name").unwrap_or_default(),
        url: s("html_url").unwrap_or_default(),
        description: s("description").unwrap_or_default(),
        stars: i("stargazers_count"),
        forks: i("forks_count"),
        language: s("language"),
        topics: r
            .get("topics")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
            .unwrap_or_default(),
        license: r
            .get("license")
            .and_then(|l| l.get("spdx_id"))
            .and_then(|v| v.as_str())
            .map(String::from),
        pushed_at: s("pushed_at"),
        created_at: s("created_at"),
        archived: r.get("archived").and_then(|v| v.as_bool()).unwrap_or(false),
        owner: r.get("owner").and_then(|o| o.get("login")).and_then(|v| v.as_str()).map(String::from),
        default_branch: s("default_branch").unwrap_or_else(|| "main".into()),
        open_issues: r.get("open_issues_count").and_then(|v| v.as_i64()),
        homepage: s("homepage"),
        size: r.get("size").and_then(|v| v.as_i64()),
        score: None,
        why: None,
    }
}

/// What went wrong upstream, in terms the caller can act on.
#[derive(Debug)]
pub enum GhError {
    /// GitHub said no more, or our own governor did.
    RateLimited(String),
    NotFound(String),
    Forbidden(String),
    Other(String),
}

impl std::fmt::Display for GhError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GhError::RateLimited(s) | GhError::NotFound(s) | GhError::Forbidden(s) | GhError::Other(s) => {
                write!(f, "{s}")
            }
        }
    }
}

impl GhError {
    pub fn status(&self) -> u16 {
        match self {
            GhError::RateLimited(_) => 429,
            GhError::NotFound(_) => 404,
            GhError::Forbidden(_) => 403,
            GhError::Other(_) => 502,
        }
    }
}

pub struct Client {
    http: reqwest::Client,
}

impl Client {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(20))
                .user_agent("mod-github-module/0.2")
                .build()
                .unwrap_or_default(),
        }
    }

    /// The GitHub token to act with, read straight out of the `git` module's
    /// store. Returns the token only to the caller it belongs to: passing an
    /// `address` gets *that key's* account, and there is no path by which one
    /// key's token is used for another's request.
    pub fn token_for(&self, address: Option<&str>) -> Option<String> {
        #[derive(Default, Deserialize)]
        struct Accounts {
            #[serde(default)]
            accounts: BTreeMap<String, Entry>,
        }
        #[derive(Default, Deserialize)]
        struct Entry {
            #[serde(default)]
            active: Option<String>,
            #[serde(default)]
            logins: BTreeMap<String, serde_json::Value>,
            /// Pre-multi-account layout: the record itself held the token.
            #[serde(default)]
            token: Option<String>,
        }
        let addr = address?.to_lowercase();
        let store: Accounts = crate::store::read(&crate::store::sibling("git", "github.json"));
        let entry = store.accounts.iter().find(|(k, _)| k.to_lowercase() == addr).map(|(_, v)| v)?;
        if let Some(t) = &entry.token {
            return Some(t.clone());
        }
        let active = entry.active.clone()?;
        entry
            .logins
            .get(&active)
            .and_then(|v| v.get("token"))
            .and_then(|v| v.as_str())
            .map(String::from)
    }

    /// Which account is attached, without ever surfacing the credential.
    pub fn account_for(&self, address: Option<&str>) -> Option<String> {
        #[derive(Default, Deserialize)]
        struct Accounts {
            #[serde(default)]
            accounts: BTreeMap<String, serde_json::Value>,
        }
        let addr = address?.to_lowercase();
        let store: Accounts = crate::store::read(&crate::store::sibling("git", "github.json"));
        let entry = store.accounts.iter().find(|(k, _)| k.to_lowercase() == addr).map(|(_, v)| v)?;
        entry
            .get("active")
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| entry.get("login").and_then(|v| v.as_str()).map(String::from))
    }

    async fn json(
        &self,
        url: &str,
        query: &[(&str, String)],
        token: Option<&str>,
    ) -> Result<serde_json::Value, GhError> {
        let mut req = self
            .http
            .get(url)
            .query(query)
            .header("Accept", "application/vnd.github+json");
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        let r = req.send().await.map_err(|e| GhError::Other(format!("github unreachable: {e}")))?;
        let status = r.status().as_u16();
        let exhausted = r
            .headers()
            .get("x-ratelimit-remaining")
            .and_then(|v| v.to_str().ok())
            .map(|v| v == "0")
            .unwrap_or(false);
        let body = r.text().await.unwrap_or_default();
        if status >= 400 {
            let msg = serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| v.get("message").and_then(|m| m.as_str()).map(String::from))
                .unwrap_or_else(|| body.chars().take(200).collect());
            if exhausted {
                return Err(GhError::RateLimited(if token.is_some() {
                    "github rate limit reached — wait for the window to reset".into()
                } else {
                    "github rate limit reached — anonymous search is 10/min; connect an \
                     account with `m github/oauth` for 30/min"
                        .into()
                }));
            }
            return Err(match status {
                404 => GhError::NotFound(format!("github 404: {msg}")),
                401 | 403 => GhError::Forbidden(format!("github {status}: {msg}")),
                _ => GhError::Other(format!("github {status}: {msg}")),
            });
        }
        serde_json::from_str(&body).map_err(|e| GhError::Other(format!("github sent no JSON: {e}")))
    }

    pub async fn search(
        &self,
        q: &str,
        per_page: i64,
        page: i64,
        sort: Option<&str>,
        token: Option<&str>,
    ) -> Result<Vec<Repo>, GhError> {
        let mut params = vec![
            ("q", q.to_string()),
            ("per_page", per_page.to_string()),
            ("page", page.to_string()),
        ];
        if let Some(s) = sort {
            params.push(("sort", s.to_string()));
        }
        let v = self.json(&format!("{API}/search/repositories"), &params, token).await?;
        Ok(v.get("items")
            .and_then(|i| i.as_array())
            .map(|a| a.iter().map(row).collect())
            .unwrap_or_default())
    }

    pub async fn repo(&self, owner: &str, name: &str, token: Option<&str>) -> Result<Repo, GhError> {
        let v = self.json(&format!("{API}/repos/{owner}/{name}"), &[], token).await?;
        Ok(row(&v))
    }

    pub async fn rate(&self, token: Option<&str>) -> Result<serde_json::Value, GhError> {
        self.json(&format!("{API}/rate_limit"), &[], token).await
    }

    /// A README off raw.githubusercontent.com. Never sends the token: this
    /// host is public, and a credential does not belong on a request that does
    /// not need one.
    pub async fn readme(&self, owner: &str, name: &str, branch: Option<&str>) -> String {
        let branches: Vec<&str> = match branch {
            Some(b) => vec![b],
            None => vec!["main", "master"],
        };
        for br in branches {
            for file in ["README.md", "readme.md", "README.rst", "README"] {
                let url = format!("{RAW}/{owner}/{name}/{br}/{file}");
                let Ok(r) = self
                    .http
                    .get(&url)
                    .timeout(std::time::Duration::from_secs(8))
                    .send()
                    .await
                else {
                    continue;
                };
                if r.status().is_success() {
                    if let Ok(text) = r.text().await {
                        return text.chars().take(20_000).collect();
                    }
                }
            }
        }
        String::new()
    }
}
