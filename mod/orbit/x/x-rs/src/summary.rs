//! Feed digests and API cost estimates. Pure functions over X API v2 payloads
//! (`data` + `includes.users`), so the whole thing is testable offline.

use serde_json::{json, Value};
use std::collections::HashMap;

/// USD per resource on X's pay-per-use API (the default since Feb 2026):
/// post read, user read, and "owned" reads (your own home feed, lists,
/// bookmarks). Override with X_PRICE_POST_READ / X_PRICE_USER_READ /
/// X_PRICE_OWNED_READ when X changes them (developer console has the numbers).
pub fn rates() -> (f64, f64, f64) {
    let f = |k: &str, d: f64| std::env::var(k).ok().and_then(|v| v.parse().ok()).unwrap_or(d);
    (f("X_PRICE_POST_READ", 0.005), f("X_PRICE_USER_READ", 0.010), f("X_PRICE_OWNED_READ", 0.001))
}

fn cents(x: f64) -> f64 {
    (x * 10000.0).round() / 10000.0
}

/// What one read of a feed costs. `users` are profile reads we know about;
/// `max_users` is the worst case when every post brings a new author along
/// (search feeds). `owned` bills posts at the owned-read rate.
pub fn estimate(posts: u64, users: u64, max_users: u64, owned: bool) -> Value {
    let (rp, ru, ro) = rates();
    let rp = if owned { ro } else { rp };
    let usd = posts as f64 * rp + users as f64 * ru;
    let usd_max = posts as f64 * rp + max_users.max(users) as f64 * ru;
    json!({
        "posts_read": posts,
        "user_reads": users,
        "usd": cents(usd),
        "usd_max": cents(usd_max),
        "daily_for_a_month_usd": cents(usd_max * 30.0),
        "per_1000_posts_usd": cents(rp * 1000.0),
        "rates": { "post_read": rp, "user_read": ru, "owned": owned, "unit": "USD per resource" },
        "note": "X pay-per-use: credits bought up front in the developer console, no free tier. The same post/user read twice in one UTC day is billed once, so re-running today is ~free. Cap: 2M post reads/month. Rates change; override with X_PRICE_* env."
    })
}

/// How a user-typed feed maps onto an X call.
#[derive(Debug, PartialEq)]
pub enum Feed {
    /// A user's own posts.
    Timeline(String),
    /// Recent search (last 7 days), full X query syntax.
    Search(String),
    /// A list's posts (x.com/i/lists/<id>).
    List(String),
    /// The signed-in account's reverse-chronological home feed.
    Home,
    /// One thread (x.com/<h>/status/<id>): the post's conversation.
    Thread(String),
}

impl Feed {
    pub fn label(&self) -> String {
        match self {
            Feed::Timeline(h) => format!("@{h}"),
            Feed::Search(q) => format!("search: {q}"),
            Feed::List(id) => format!("list {id}"),
            Feed::Home => "home feed".into(),
            Feed::Thread(id) => format!("thread {id}"),
        }
    }
}

pub fn parse_feed(raw: &str) -> Result<Feed, String> {
    let t = raw.trim();
    if t.is_empty() {
        return Err("summarize requires `feed` (@handle, x.com URL, #tag, search query, list URL or \"home\")".into());
    }
    let lower = t.to_lowercase();
    if matches!(lower.as_str(), "home" | "me" | "following" | "for you" | "my feed" | "timeline") {
        return Ok(Feed::Home);
    }
    if let Some(id) = lower.strip_prefix("list:").or_else(|| lower.strip_prefix("list ")) {
        let id = id.trim();
        if !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) {
            return Ok(Feed::List(id.into()));
        }
    }
    if lower.contains("x.com/") || lower.contains("twitter.com/") {
        let path = t.split("://").last().unwrap_or(t);
        let mut parts = path.splitn(2, '/');
        parts.next();
        let rest = parts.next().unwrap_or("");
        if let Some(q) = rest.split("q=").nth(1) {
            let q = q.split('&').next().unwrap_or("");
            let decoded = percent_encoding::percent_decode_str(&q.replace('+', " "))
                .decode_utf8_lossy()
                .to_string();
            if !decoded.is_empty() {
                return Ok(Feed::Search(decoded));
            }
        }
        let segs: Vec<&str> = rest.split(['?', '#']).next().unwrap_or("").split('/').filter(|s| !s.is_empty()).collect();
        let digits = |s: &&str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
        match segs.as_slice() {
            ["i", "lists", id, ..] if digits(id) => return Ok(Feed::List(id.to_string())),
            ["hashtag", tag, ..] => return Ok(Feed::Search(format!("#{tag}"))),
            ["home"] | [] => return Ok(Feed::Home),
            [_, "status", id, ..] if digits(id) => return Ok(Feed::Thread(id.to_string())),
            [h, ..] if !matches!(*h, "search" | "i" | "explore" | "notifications" | "messages" | "settings") => {
                return Ok(Feed::Timeline(h.to_string()))
            }
            _ => return Err(format!("can't tell which feed this is: {raw:?}")),
        }
    }
    let bare = t.trim_start_matches('@');
    let is_handle = !bare.is_empty()
        && bare.len() <= 15
        && bare.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
        && !t.starts_with('#')
        && (t.starts_with('@') || !t.contains(' '));
    if is_handle {
        Ok(Feed::Timeline(bare.to_string()))
    } else {
        Ok(Feed::Search(t.to_string()))
    }
}

const STOP: &[&str] = &[
    "the", "and", "for", "that", "this", "with", "you", "are", "was", "but", "not", "have", "has", "had",
    "from", "they", "his", "her", "its", "our", "their", "will", "would", "can", "could", "just", "like",
    "get", "got", "now", "new", "one", "all", "out", "about", "what", "when", "who", "how", "why", "there",
    "than", "then", "them", "been", "were", "your", "more", "some", "into", "over", "also", "only", "very",
    "much", "too", "got", "said", "says", "https", "http", "amp", "rt", "via", "don", "isn", "it's", "i'm",
    "i'll", "dont", "cant", "im", "ive", "here", "off", "any", "see", "way", "still", "even", "back",
    "going", "want", "need", "make", "good", "really", "think", "people", "time", "today", "right",
];

fn top(map: HashMap<String, u64>, n: usize) -> Vec<Value> {
    let mut v: Vec<_> = map.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    v.into_iter().take(n).map(|(k, c)| json!({ "term": k, "count": c })).collect()
}

fn metric(p: &Value, k: &str) -> u64 {
    p["public_metrics"][k].as_u64().unwrap_or(0)
}

fn engagement(p: &Value) -> u64 {
    metric(p, "like_count") + 2 * metric(p, "retweet_count") + metric(p, "reply_count") + metric(p, "quote_count")
}

fn clean(text: &str) -> String {
    text.split_whitespace()
        .filter(|w| !w.starts_with("http"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Build the digest from the collected posts and the `includes.users` objects.
pub fn digest(posts: &[Value], users: &[Value]) -> Value {
    let by_id: HashMap<&str, &Value> =
        users.iter().filter_map(|u| u["id"].as_str().map(|i| (i, u))).collect();
    let handle = |p: &Value| -> String {
        p["author_id"]
            .as_str()
            .and_then(|i| by_id.get(i))
            .and_then(|u| u["username"].as_str())
            .unwrap_or("?")
            .to_string()
    };

    let (mut tags, mut mentions, mut domains, mut words) =
        (HashMap::new(), HashMap::new(), HashMap::new(), HashMap::new());
    let (mut authors, mut days, mut langs): (HashMap<String, u64>, HashMap<String, u64>, HashMap<String, u64>) =
        Default::default();
    let (mut likes, mut reposts, mut replies, mut retweets, mut newest, mut oldest) =
        (0u64, 0u64, 0u64, 0u64, String::new(), String::new());

    for p in posts {
        likes += metric(p, "like_count");
        reposts += metric(p, "retweet_count");
        replies += metric(p, "reply_count");
        if p["referenced_tweets"].as_array().map_or(false, |a| a.iter().any(|r| r["type"] == "retweeted")) {
            retweets += 1;
        }
        *authors.entry(handle(p)).or_insert(0) += 1;
        if let Some(l) = p["lang"].as_str() {
            *langs.entry(l.to_string()).or_insert(0) += 1;
        }
        if let Some(c) = p["created_at"].as_str() {
            *days.entry(c.chars().take(10).collect()).or_insert(0) += 1;
            if newest.is_empty() || c > newest.as_str() {
                newest = c.into();
            }
            if oldest.is_empty() || c < oldest.as_str() {
                oldest = c.into();
            }
        }
        for t in p["entities"]["hashtags"].as_array().into_iter().flatten() {
            if let Some(t) = t["tag"].as_str() {
                *tags.entry(format!("#{}", t.to_lowercase())).or_insert(0) += 1;
            }
        }
        for m in p["entities"]["mentions"].as_array().into_iter().flatten() {
            if let Some(m) = m["username"].as_str() {
                *mentions.entry(format!("@{}", m.to_lowercase())).or_insert(0) += 1;
            }
        }
        for u in p["entities"]["urls"].as_array().into_iter().flatten() {
            let url = u["expanded_url"].as_str().or(u["url"].as_str()).unwrap_or("");
            let host = url.split("://").last().unwrap_or("").split('/').next().unwrap_or("");
            let host = host.trim_start_matches("www.");
            if !host.is_empty() && host != "x.com" && host != "twitter.com" && host != "t.co" {
                *domains.entry(host.to_lowercase()).or_insert(0) += 1;
            }
        }
        let text = clean(p["text"].as_str().unwrap_or(""));
        for w in text.split(|c: char| !(c.is_alphanumeric() || c == '\'' )) {
            let w = w.to_lowercase();
            if w.chars().count() < 4 || STOP.contains(&w.as_str()) || w.chars().all(|c| c.is_ascii_digit()) {
                continue;
            }
            *words.entry(w).or_insert(0) += 1;
        }
    }

    let mut ranked: Vec<&Value> = posts.iter().collect();
    ranked.sort_by_key(|p| std::cmp::Reverse(engagement(p)));
    let top_posts: Vec<Value> = ranked
        .iter()
        .take(5)
        .map(|p| {
            let id = p["id"].as_str().unwrap_or("");
            let h = handle(p);
            json!({
                "id": id,
                "author": h,
                "text": clean(p["text"].as_str().unwrap_or("")).chars().take(280).collect::<String>(),
                "likes": metric(p, "like_count"),
                "reposts": metric(p, "retweet_count"),
                "replies": metric(p, "reply_count"),
                "url": format!("https://x.com/{h}/status/{id}")
            })
        })
        .collect();

    let n = posts.len() as u64;
    let mut day_rows: Vec<_> = days.into_iter().collect();
    day_rows.sort();
    let top_authors = top(authors, 5);
    let top_tags = top(tags, 8);
    let top_words = top(words, 10);
    let names = |v: &[Value], k: usize| -> String {
        v.iter().take(k).filter_map(|t| t["term"].as_str()).collect::<Vec<_>>().join(", ")
    };

    let mut lines = vec![format!(
        "{n} posts from {} to {}: {likes} likes, {reposts} reposts, {replies} replies ({retweets} are reposts).",
        oldest.chars().take(10).collect::<String>(),
        newest.chars().take(10).collect::<String>()
    )];
    if !top_words.is_empty() {
        lines.push(format!("Recurring topics: {}.", names(&top_words, 6)));
    }
    if !top_tags.is_empty() {
        lines.push(format!("Hashtags: {}.", names(&top_tags, 5)));
    }
    if top_authors.len() > 1 {
        lines.push(format!("Most active: {}.", names(&top_authors, 3)));
    }
    if let Some(p) = top_posts.first() {
        lines.push(format!(
            "Top post ({} likes) by @{}: \"{}\"",
            p["likes"], p["author"].as_str().unwrap_or("?"), p["text"].as_str().unwrap_or("")
        ));
    }

    json!({
        "summary": lines.join("\n"),
        "posts": n,
        "span": { "from": oldest, "to": newest },
        "totals": { "likes": likes, "reposts": reposts, "replies": replies, "avg_likes": if n > 0 { likes as f64 / n as f64 } else { 0.0 } },
        "top_posts": top_posts,
        "topics": top_words,
        "hashtags": top_tags,
        "mentions": top(mentions, 8),
        "domains": top(domains, 8),
        "authors": top_authors,
        "languages": top(langs, 5),
        "per_day": day_rows.into_iter().map(|(d, c)| json!({ "day": d, "posts": c })).collect::<Vec<_>>()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feeds() {
        assert_eq!(parse_feed("@jack").unwrap(), Feed::Timeline("jack".into()));
        assert_eq!(parse_feed("https://x.com/elonmusk").unwrap(), Feed::Timeline("elonmusk".into()));
        assert_eq!(parse_feed("https://x.com/elonmusk/status/123?s=20").unwrap(), Feed::Thread("123".into()));
        assert_eq!(parse_feed("https://x.com/i/lists/1585430245762441216").unwrap(), Feed::List("1585430245762441216".into()));
        assert_eq!(parse_feed("list:42").unwrap(), Feed::List("42".into()));
        assert_eq!(parse_feed("https://x.com/hashtag/TAO?src=hashtag_click").unwrap(), Feed::Search("#TAO".into()));
        assert_eq!(parse_feed("home").unwrap(), Feed::Home);
        assert_eq!(parse_feed("https://x.com/home").unwrap(), Feed::Home);
        assert!(parse_feed("https://x.com/settings/account").is_err());
        assert_eq!(parse_feed("https://x.com/search?q=bittensor%20subnet&f=live").unwrap(), Feed::Search("bittensor subnet".into()));
        assert_eq!(parse_feed("#ai").unwrap(), Feed::Search("#ai".into()));
        assert_eq!(parse_feed("from:jack lang:en").unwrap(), Feed::Search("from:jack lang:en".into()));
        assert!(parse_feed("").is_err());
    }

    #[test]
    fn cost_and_digest() {
        assert_eq!(estimate(100, 1, 1, false)["usd"], json!(0.51));
        assert_eq!(estimate(100, 0, 100, false)["usd_max"], json!(1.5));
        assert_eq!(estimate(100, 0, 0, true)["usd"], json!(0.1));
        let posts = vec![
            json!({"id":"1","author_id":"9","text":"bittensor subnets rule https://t.co/x","created_at":"2026-10-20T10:00:00Z",
                   "public_metrics":{"like_count":10,"retweet_count":2,"reply_count":1,"quote_count":0},
                   "entities":{"hashtags":[{"tag":"TAO"}],"urls":[{"expanded_url":"https://www.example.com/a"}]}}),
            json!({"id":"2","author_id":"9","text":"bittensor again","created_at":"2026-10-21T10:00:00Z",
                   "public_metrics":{"like_count":1,"retweet_count":0,"reply_count":0,"quote_count":0}}),
        ];
        let d = digest(&posts, &[json!({"id":"9","username":"alice"})]);
        assert_eq!(d["posts"], 2);
        assert_eq!(d["topics"][0]["term"], "bittensor");
        assert_eq!(d["top_posts"][0]["url"], "https://x.com/alice/status/1");
        assert_eq!(d["domains"][0]["term"], "example.com");
    }
}
