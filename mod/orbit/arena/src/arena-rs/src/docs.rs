//! The documentation, as data.
//!
//! Eight markdown files, compiled in, read by three doors: the console's docs
//! tab (`GET /docs`), the `docs_*` tools, and `arena://docs/<slug>` as MCP
//! resources. One text, so what a person reads and what an agent is handed
//! cannot drift apart — which is the same rule the rest of this server keeps
//! about capabilities.
//!
//! The page about the MCP server goes one step further: its tool reference is
//! not written but generated, from `mcp::tool_list()` and `modmcp::tools_for`,
//! each time the page is read (`body`). The docs describe the server the
//! server is, and a tool added to the table is documented by being added.

use serde_json::{json, Value};

pub struct Page {
    pub slug: &'static str,
    pub title: &'static str,
    /// One line, shown in the console's contents and in `docs_pages`.
    pub summary: &'static str,
    pub body: &'static str,
}

pub const PAGES: [Page; 8] = [
    Page {
        slug: "start",
        title: "Start here",
        summary: "What the arena is, the shortest game that works, and where to go next.",
        body: include_str!("../docs/start.md"),
    },
    Page {
        slug: "upload",
        title: "Uploading",
        summary: "The reader decides the role: three containers, one id, and what a role means.",
        body: include_str!("../docs/upload.md"),
    },
    Page {
        slug: "game",
        title: "Writing a game",
        summary: "The four methods, in a Python class, a Rust class and wasm.",
        body: include_str!("../docs/game.md"),
    },
    Page {
        slug: "player",
        title: "Filling a seat",
        summary: "The seven kinds of player, their config, and how to try one first.",
        body: include_str!("../docs/player.md"),
    },
    Page {
        slug: "match",
        title: "Matches and ratings",
        summary: "One match loop, Elo per game, and why the illegal-move rate is the number.",
        body: include_str!("../docs/match.md"),
    },
    Page {
        slug: "sandbox",
        title: "The sandbox",
        summary: "What uploaded code can reach: wasm, a python subprocess, and the door out.",
        body: include_str!("../docs/sandbox.md"),
    },
    Page {
        slug: "mcp",
        title: "The MCP server",
        summary: "Connect over HTTP or stdio, the tools, the resources, one server per module.",
        body: include_str!("../docs/mcp.md"),
    },
    Page {
        slug: "api",
        title: "REST, state and the fleet",
        summary: "Every route, where the bytes live, and what this module is in the fleet.",
        body: include_str!("../docs/api.md"),
    },
];

pub const URI_PREFIX: &str = "arena://docs/";

/// The text of a page. Every door — REST, tool, resource, search — reads a
/// page through here, so the generated part is on every copy or on none.
pub fn body(p: &Page) -> String {
    if p.slug == "mcp" {
        format!("{}\n{}", p.body.trim_end(), tool_reference())
    } else {
        p.body.to_string()
    }
}

/// The MCP page's reference section, built from the tool tables themselves.
fn tool_reference() -> String {
    let mut out = String::new();
    out.push_str("## Tool reference\n\n");
    out.push_str(
        "Generated from the server's own tool table as this page is read — the \
         same `description` and `inputSchema` a client gets from `tools/list`, \
         with the arguments a tool must have in bold and the rest after them.\n\n",
    );
    out.push_str(&tool_table(&crate::mcp::tool_list()));
    out.push_str("\n## Per-module tool reference\n\n");
    out.push_str(
        "What `/m/<name>/mcp` offers, by what the module is. `about` and `source` \
         are on every one of them.\n",
    );
    for (role, title) in [("game", "A game"), ("player", "A player"), ("class", "Anything else")] {
        out.push_str(&format!("\n### {title}\n\n"));
        out.push_str(&tool_table(&crate::modmcp::tools_for(role)));
    }
    out
}

/// One markdown table of tools: name, arguments, and the first sentence of
/// what it does. Everything that could break a table cell is taken out.
fn tool_table(tools: &Value) -> String {
    let mut out = String::from("| tool | arguments | what it does |\n|---|---|---|\n");
    for t in tools.as_array().map(|a| a.as_slice()).unwrap_or_default() {
        let name = t["name"].as_str().unwrap_or("");
        let required: Vec<&str> = t["inputSchema"]["required"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        let mut args: Vec<String> = Vec::new();
        if let Some(props) = t["inputSchema"]["properties"].as_object() {
            // Required first, then the rest — each group alphabetical.
            let mut names: Vec<&String> = props.keys().collect();
            names.sort_by_key(|n| !required.contains(&n.as_str()));
            for n in names {
                let ty = props[n]["type"].as_str().unwrap_or("");
                let shown = if ty.is_empty() { n.clone() } else { format!("{n}: {ty}") };
                args.push(if required.contains(&n.as_str()) {
                    format!("**{shown}**")
                } else {
                    shown
                });
            }
        }
        let args = if args.is_empty() { "—".to_string() } else { args.join(", ") };
        let what = first_sentence(t["description"].as_str().unwrap_or(""));
        out.push_str(&format!("| `{name}` | {args} | {what} |\n"));
    }
    out
}

/// The first sentence of a description, on one line, with the pipe escaped so
/// it survives a table cell.
fn first_sentence(text: &str) -> String {
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut end = flat.len();
    let bytes = flat.as_bytes();
    for (i, w) in bytes.windows(2).enumerate() {
        // A sentence ends at ". " — never inside `a.b`, which has no space.
        if w == b". " {
            end = i + 1;
            break;
        }
    }
    flat[..end].replace('|', "\\|")
}

pub fn find(slug: &str) -> Option<&'static Page> {
    let want = slug.trim().trim_start_matches(URI_PREFIX).trim_matches('/').to_lowercase();
    let want = want.strip_suffix(".md").unwrap_or(&want).to_string();
    PAGES
        .iter()
        .find(|p| p.slug == want)
        .or_else(|| PAGES.iter().find(|p| p.title.to_lowercase() == want))
        .or_else(|| PAGES.iter().find(|p| p.slug.starts_with(&want) && !want.is_empty()))
}

fn slugs() -> Vec<&'static str> {
    PAGES.iter().map(|p| p.slug).collect()
}

fn card(p: &Page) -> Value {
    json!({
        "slug": p.slug,
        "title": p.title,
        "summary": p.summary,
        "words": body(p).split_whitespace().count(),
        "resource": format!("{URI_PREFIX}{}", p.slug),
    })
}

/// The contents. Order is the reading order, which is why it is a list.
pub fn index() -> Value {
    json!({
        "count": PAGES.len(),
        "pages": PAGES.iter().map(card).collect::<Vec<_>>(),
        "read": "GET /docs/:slug, the docs_page tool, or the resource arena://docs/:slug",
    })
}

/// One page, as markdown.
pub fn page(args: &Value) -> Result<Value, String> {
    let slug = args
        .get("slug")
        .or_else(|| args.get("page"))
        .and_then(|v| v.as_str())
        .unwrap_or("start");
    let p = find(slug).ok_or_else(|| {
        format!("no doc page `{slug}` — there are {}: {}", PAGES.len(), slugs().join(", "))
    })?;
    let mut v = card(p);
    v["markdown"] = json!(body(p));
    Ok(v)
}

/// The headings of one page, which is the fastest way to see what is on it.
fn sections(p: &'static Page) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    let mut heading = p.title.to_string();
    let mut buf: Vec<&str> = Vec::new();
    let text = body(p);
    for line in text.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            if !buf.is_empty() {
                out.push((heading.clone(), buf.join("\n")));
                buf.clear();
            }
            heading = h.trim().to_string();
        } else if !line.starts_with("# ") {
            buf.push(line);
        }
    }
    if !buf.is_empty() {
        out.push((heading, buf.join("\n")));
    }
    out
}

/// Free text over the docs, scored by section rather than by page — the answer
/// to "where does it say that" is a heading, not a file.
pub fn search(args: &Value) -> Value {
    let q = args.get("q").or_else(|| args.get("query")).and_then(|v| v.as_str()).unwrap_or("");
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(8).clamp(1, 40) as usize;
    let terms: Vec<String> = q.to_lowercase().split_whitespace().map(String::from).collect();
    if terms.is_empty() {
        return json!({ "query": q, "count": 0, "hits": [], "note": "pass q" });
    }

    let mut hits: Vec<(usize, Value)> = Vec::new();
    for p in PAGES.iter() {
        for (heading, text) in sections(p) {
            let hay = format!("{heading}\n{text}").to_lowercase();
            let mut score = 0usize;
            for t in &terms {
                let n = hay.matches(t.as_str()).count();
                if n == 0 {
                    continue;
                }
                // A hit in the heading is worth more than a hit in the body,
                // and a page whose title says it is worth more still.
                score += n
                    + 4 * usize::from(heading.to_lowercase().contains(t.as_str()))
                    + 4 * usize::from(p.title.to_lowercase().contains(t.as_str()));
            }
            if score == 0 {
                continue;
            }
            hits.push((
                score,
                json!({
                    "slug": p.slug,
                    "title": p.title,
                    "heading": heading,
                    "score": score,
                    "snippet": snippet(&text, &terms),
                    "resource": format!("{URI_PREFIX}{}", p.slug),
                }),
            ));
        }
    }
    hits.sort_by(|a, b| b.0.cmp(&a.0));
    hits.truncate(limit);
    json!({
        "query": q,
        "count": hits.len(),
        "hits": hits.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
    })
}

/// The prose a term was found in, cleaned of the markdown around it — enough
/// to tell whether this is the hit you wanted without fetching the page. Lines
/// inside a fenced block are skipped: a snippet of somebody's console output
/// says nothing, and it is the sentence beside it you were looking for.
fn snippet(text: &str, terms: &[String]) -> String {
    let mut prose: Vec<String> = Vec::new();
    let mut fenced = false;
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if fenced || line.trim().is_empty() {
            continue;
        }
        let clean = line
            .trim()
            .trim_matches('|')
            .replace('|', " · ")
            .replace("**", "")
            .replace('`', "")
            .replace('#', "")
            .trim()
            .to_string();
        // A table's rule row is punctuation with a term never in it.
        if clean.is_empty() || clean.chars().all(|c| "-: ·".contains(c)) {
            continue;
        }
        prose.push(clean);
    }
    let at = prose
        .iter()
        .position(|l| {
            let low = l.to_lowercase();
            terms.iter().any(|t| low.contains(t.as_str()))
        })
        .unwrap_or(0);
    let mut s = prose
        .get(at..(at + 2).min(prose.len()))
        .unwrap_or_default()
        .join(" ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if s.chars().count() > 200 {
        s = s.chars().take(197).collect::<String>() + "…";
    }
    s
}

// ── MCP resources ────────────────────────────────────────────────────────

pub fn resource_list() -> Value {
    json!(PAGES
        .iter()
        .map(|p| json!({
            "uri": format!("{URI_PREFIX}{}", p.slug),
            "name": p.title,
            "title": p.title,
            "description": p.summary,
            "mimeType": "text/markdown",
        }))
        .collect::<Vec<_>>())
}

pub fn resource_read(uri: &str) -> Result<Value, String> {
    let p = find(uri).ok_or_else(|| format!("no resource `{uri}` — try {URI_PREFIX}start"))?;
    Ok(json!([{
        "uri": format!("{URI_PREFIX}{}", p.slug),
        "name": p.title,
        "mimeType": "text/markdown",
        "text": body(p),
    }]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_page_is_findable_by_its_own_slug_and_uri() {
        for p in PAGES.iter() {
            assert_eq!(find(p.slug).map(|f| f.slug), Some(p.slug));
            assert_eq!(find(&format!("{URI_PREFIX}{}", p.slug)).map(|f| f.slug), Some(p.slug));
            assert!(p.body.starts_with("# "), "{} has no title line", p.slug);
        }
    }

    #[test]
    fn a_page_links_only_to_pages_that_exist() {
        for p in PAGES.iter() {
            let text = body(p);
            for part in text.split("](#docs/").skip(1) {
                let slug = part.split(')').next().unwrap_or("");
                assert!(find(slug).is_some(), "{} links to a missing page: {slug}", p.slug);
            }
        }
    }

    #[test]
    fn a_snippet_is_prose_rather_than_a_console_paste() {
        let hits = search(&json!({ "q": "illegal move rate" }));
        let snip = hits["hits"][0]["snippet"].as_str().unwrap_or("");
        assert!(!snip.contains('|') && !snip.contains('`'), "unclean snippet: {snip}");
        assert!(!snip.contains("elo 12"), "snippet came out of a code block: {snip}");
    }

    #[test]
    fn the_mcp_page_documents_every_tool_the_server_has() {
        let text = body(find("mcp").unwrap());
        for t in crate::mcp::tool_list().as_array().unwrap() {
            let name = t["name"].as_str().unwrap();
            // Named in the hand-written groups *and* in the generated table.
            assert!(text.matches(&format!("`{name}`")).count() >= 2, "mcp page lacks `{name}`");
        }
        for role in ["game", "player", "class"] {
            for t in crate::modmcp::tools_for(role).as_array().unwrap() {
                let name = t["name"].as_str().unwrap();
                assert!(text.contains(&format!("| `{name}` |")), "no per-module row for {role}/{name}");
            }
        }
        // The count the prose quotes is the count the table has.
        let n = crate::mcp::tool_list().as_array().unwrap().len();
        assert_eq!(n, 31, "the mcp page says thirty-one tools; there are {n}");
        assert!(text.contains("**source: string**"), "required arguments are bold");
    }

    #[test]
    fn a_first_sentence_stops_at_the_period_and_survives_a_table() {
        assert_eq!(first_sentence("Filter by a.b or c. Then more."), "Filter by a.b or c.");
        assert_eq!(first_sentence("one\n  two"), "one two");
        assert_eq!(first_sentence("a | b"), "a \\| b");
    }

    #[test]
    fn search_finds_the_page_that_is_about_the_thing() {
        let hits = search(&json!({ "q": "illegal move rate" }));
        assert_eq!(hits["hits"][0]["slug"], "match");
        assert!(search(&json!({ "q": "stdio" }))["count"].as_u64().unwrap() > 0);
    }
}
