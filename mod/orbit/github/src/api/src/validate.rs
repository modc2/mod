//! Parameter validation, driven entirely by the policy table.
//!
//! Allow-list, not filter: a parameter the route did not declare is a 400.
//! That costs a little friction and buys two things — a caller who typos
//! `langauge=rust` is told so instead of silently getting unfiltered results,
//! and no undeclared string can reach the code that builds an upstream URL.
//!
//! `Kind::Repo` is where that matters most. It is the only parameter that
//! becomes part of a path on api.github.com and raw.githubusercontent.com, so
//! it is anchored to `owner/name` with no slashes, no dots-dots and no scheme
//! left in it. Everything downstream can then treat it as two clean segments.

use std::collections::BTreeMap;

use crate::policy::{Kind, Route};

#[derive(Debug, Clone)]
pub enum Val {
    Text(String),
    Int(i64),
    Bool(bool),
}

impl Val {
    pub fn text(&self) -> String {
        match self {
            Val::Text(s) => s.clone(),
            Val::Int(i) => i.to_string(),
            Val::Bool(b) => b.to_string(),
        }
    }
    pub fn int(&self) -> Option<i64> {
        match self {
            Val::Int(i) => Some(*i),
            Val::Text(s) => s.parse().ok(),
            Val::Bool(b) => Some(*b as i64),
        }
    }
    pub fn boolean(&self) -> bool {
        match self {
            Val::Bool(b) => *b,
            Val::Int(i) => *i != 0,
            Val::Text(s) => !matches!(s.trim().to_lowercase().as_str(), "" | "0" | "false" | "no"),
        }
    }
}

pub type Args = BTreeMap<String, Val>;

pub trait Get {
    fn s(&self, k: &str) -> Option<String>;
    fn i(&self, k: &str, default: i64) -> i64;
    fn b(&self, k: &str) -> Option<bool>;
}

impl Get for Args {
    fn s(&self, k: &str) -> Option<String> {
        self.get(k).map(|v| v.text()).filter(|s| !s.is_empty())
    }
    fn i(&self, k: &str, default: i64) -> i64 {
        self.get(k).and_then(|v| v.int()).unwrap_or(default)
    }
    fn b(&self, k: &str) -> Option<bool> {
        self.get(k).map(|v| v.boolean())
    }
}

/// `owner/name`, tolerating a full github.com URL and a trailing `.git`.
/// Returns the two segments, already safe to interpolate into a path.
pub fn split_repo(raw: &str) -> Result<(String, String), String> {
    let s = raw
        .trim()
        .trim_end_matches('/')
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_start_matches("www.")
        .trim_start_matches("github.com/")
        .trim_end_matches(".git");
    let mut parts = s.split('/');
    let (Some(owner), Some(name), None) = (parts.next(), parts.next(), parts.next()) else {
        return Err(format!("expected owner/repo, got {raw:?}"));
    };
    let ok = |seg: &str| {
        !seg.is_empty()
            && seg.len() <= 100
            && seg != "."
            && seg != ".."
            && seg.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
    };
    if !ok(owner) || !ok(name) {
        return Err(format!("expected owner/repo, got {raw:?}"));
    }
    Ok((owner.to_string(), name.to_string()))
}

fn slug_ok(s: &str) -> bool {
    s.chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '#' | '.' | '_' | '-' | ' ' | '/'))
}

/// Check `raw` against the route's declared parameters. Unknown keys, missing
/// required keys, wrong types and out-of-range values are all rejected here so
/// no handler has to re-check them.
pub fn check(route: &Route, raw: &BTreeMap<String, String>) -> Result<Args, String> {
    for k in raw.keys() {
        if !route.params.iter().any(|p| p.name == k) {
            let known: Vec<&str> = route.params.iter().map(|p| p.name).collect();
            return Err(if known.is_empty() {
                format!("{} takes no parameters, got {k:?}", route.path)
            } else {
                format!("unknown parameter {k:?} for {} — it takes {}", route.path, known.join(", "))
            });
        }
    }
    let mut out = Args::new();
    for p in route.params {
        let Some(v) = raw.get(p.name).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()) else {
            if p.required {
                return Err(format!("{} requires {:?} — {}", route.path, p.name, p.docs));
            }
            continue;
        };
        let val = match p.kind {
            Kind::Int => {
                let n: i64 = v
                    .parse()
                    .map_err(|_| format!("{:?} must be a whole number, got {v:?}", p.name))?;
                if n < p.min || n > p.max {
                    return Err(format!("{:?} must be between {} and {}, got {n}", p.name, p.min, p.max));
                }
                Val::Int(n)
            }
            Kind::Bool => Val::Bool(!matches!(v.to_lowercase().as_str(), "0" | "false" | "no")),
            Kind::Repo => {
                let (o, n) = split_repo(&v)?;
                Val::Text(format!("{o}/{n}"))
            }
            Kind::Slug => {
                if !slug_ok(&v) {
                    return Err(format!("{:?} may only contain letters, digits and +#._-", p.name));
                }
                len_check(p.name, &v, p.min, p.max)?;
                Val::Text(v)
            }
            Kind::Text => {
                if v.chars().any(|c| c.is_control() && c != '\t') {
                    return Err(format!("{:?} may not contain control characters", p.name));
                }
                len_check(p.name, &v, p.min, p.max)?;
                Val::Text(v)
            }
        };
        out.insert(p.name.to_string(), val);
    }
    Ok(out)
}

fn len_check(name: &str, v: &str, min: i64, max: i64) -> Result<(), String> {
    let n = v.chars().count() as i64;
    if n < min {
        return Err(format!("{name:?} must be at least {min} characters"));
    }
    if n > max {
        return Err(format!("{name:?} must be at most {max} characters (got {n})"));
    }
    Ok(())
}
