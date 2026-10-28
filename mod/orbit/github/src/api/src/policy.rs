//! The policy table — the single place this module decides who may do what.
//!
//! Every route is declared once, here, with the role it needs, what it costs
//! against the caller's budget, how much upstream GitHub quota it may spend,
//! and the exact parameters it accepts. Nothing else in the service makes an
//! access decision: `routes.rs` looks a request up in this table and obeys it.
//! That is the whole point — the previous design scattered `_authorize(...)`
//! calls through the handlers, so "what is actually gated?" could only be
//! answered by reading all of them.
//!
//! Because it is data, it is also introspectable: `GET /policy` returns this
//! table verbatim, and `GET /whoami` returns your standing against it. The
//! module tells you what it will refuse before you try.
//!
//! Parameters are allow-listed, not filtered. An unknown query parameter is a
//! 400, not a silently-ignored extra — which is also what keeps `repo` from
//! becoming a path traversal into raw.githubusercontent.com.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// No identity at all. Reads are open to this level while the module is
    /// public — it is the level the module is *for*.
    Anon,
    /// A signed key with no grant. Distinct from anon only in budget.
    Reader,
    /// Granted: may spend the module's own resources (cache, GitHub login).
    Write,
    /// Granted: may hand out and take away access.
    Admin,
    /// The owner of record. Not grantable; pinned on disk.
    Owner,
}

impl Role {
    pub fn parse(s: &str) -> Option<Role> {
        match s.trim().to_lowercase().as_str() {
            "anon" => Some(Role::Anon),
            "reader" | "read" => Some(Role::Reader),
            "write" => Some(Role::Write),
            "admin" => Some(Role::Admin),
            "owner" => Some(Role::Owner),
            _ => None,
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Role::Anon => "anon",
            Role::Reader => "reader",
            Role::Write => "write",
            Role::Admin => "admin",
            Role::Owner => "owner",
        }
    }
    /// Roles an admin is allowed to hand out. Owner is deliberately absent:
    /// ownership transfers on the box, not over HTTP.
    pub const GRANTABLE: [Role; 3] = [Role::Reader, Role::Write, Role::Admin];
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    /// Free text, length-capped.
    Text,
    /// `owner/name`, or a github.com URL for one. Anchored — this is the
    /// parameter that reaches a URL, so it is the one that gets a regex.
    Repo,
    /// A language / sort / branch token: `[A-Za-z0-9 +#._-]`, short.
    Slug,
    Int,
    Bool,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct Param {
    pub name: &'static str,
    pub kind: Kind,
    pub required: bool,
    /// Inclusive bounds for Int; max length for Text/Slug.
    pub min: i64,
    pub max: i64,
    pub docs: &'static str,
}

const fn p(name: &'static str, kind: Kind, required: bool, min: i64, max: i64, docs: &'static str) -> Param {
    Param { name, kind, required, min, max, docs }
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct Route {
    pub method: &'static str,
    pub path: &'static str,
    /// Minimum role. `Anon` means genuinely open — still budgeted and audited.
    pub need: Role,
    /// What one call costs against the caller's token bucket.
    pub cost: u32,
    /// Worst-case calls this route makes to api.github.com. Charged against a
    /// process-wide upstream governor so the module cannot burn the box's
    /// shared GitHub quota faster than GitHub will refill it.
    pub upstream: u32,
    /// Whether the route is downgraded to `Reader` when the module is flipped
    /// private. Management routes are never public regardless.
    pub public_read: bool,
    pub params: &'static [Param],
    pub docs: &'static str,
}

/// Everything this service will answer. A path not in this list is a 404
/// before any work happens.
pub static ROUTES: &[Route] = &[
    Route { method: "GET", path: "/info", need: Role::Anon, cost: 0, upstream: 0, public_read: true,
        params: &[], docs: "What this module is, and what it is willing to do." },
    Route { method: "GET", path: "/health", need: Role::Anon, cost: 0, upstream: 0, public_read: true,
        params: &[], docs: "Liveness probe. Never gated, never audited." },
    Route { method: "GET", path: "/policy", need: Role::Anon, cost: 0, upstream: 0, public_read: true,
        params: &[], docs: "This table: every route, the role it needs, what it costs." },
    Route { method: "GET", path: "/whoami", need: Role::Anon, cost: 0, upstream: 0, public_read: true,
        params: &[], docs: "Your address, your role, your remaining budget." },

    Route { method: "GET", path: "/search", need: Role::Anon, cost: 10, upstream: 8, public_read: true,
        params: &[
            p("query", Kind::Text, true, 2, 400, "the question, in plain words"),
            p("n", Kind::Int, false, 1, 100, "how many results"),
            p("language", Kind::Slug, false, 1, 40, "language: qualifier"),
            p("stars", Kind::Int, false, 0, 1_000_000, "minimum stars"),
            p("sort", Kind::Slug, false, 1, 16, "stars | forks | updated"),
            p("pages", Kind::Int, false, 1, 3, "pages per expanded query"),
            p("readmes", Kind::Int, false, 0, 40, "how many READMEs to read while ranking"),
            p("fresh", Kind::Bool, false, 0, 1, "skip the cache"),
            p("dense", Kind::Bool, false, 0, 1, "force embeddings (1) or TF-IDF (0)"),
            p("explain", Kind::Bool, false, 0, 1, "per-repo score breakdown"),
        ],
        docs: "Expand → retrieve → rank. The expensive one; priced accordingly." },
    Route { method: "GET", path: "/similar", need: Role::Anon, cost: 10, upstream: 9, public_read: true,
        params: &[
            p("repo", Kind::Repo, true, 3, 140, "owner/name to find neighbours of"),
            p("n", Kind::Int, false, 1, 100, "how many results"),
            p("dense", Kind::Bool, false, 0, 1, "force embeddings (1) or TF-IDF (0)"),
            p("explain", Kind::Bool, false, 0, 1, "per-repo score breakdown"),
        ],
        docs: "The repo's own description becomes the question." },
    Route { method: "GET", path: "/expand", need: Role::Anon, cost: 1, upstream: 0, public_read: true,
        params: &[p("query", Kind::Text, true, 2, 400, "the question to expand")],
        docs: "What will actually be asked of GitHub. Local; costs no quota." },
    Route { method: "GET", path: "/candidates", need: Role::Anon, cost: 8, upstream: 8, public_read: true,
        params: &[
            p("query", Kind::Text, true, 2, 400, "the question"),
            p("language", Kind::Slug, false, 1, 40, "language: qualifier"),
            p("stars", Kind::Int, false, 0, 1_000_000, "minimum stars"),
            p("sort", Kind::Slug, false, 1, 16, "stars | forks | updated"),
            p("pages", Kind::Int, false, 1, 3, "pages per expanded query"),
            p("fresh", Kind::Bool, false, 0, 1, "skip the cache"),
        ],
        docs: "Retrieval only, unranked — the raw pool the ranker sees." },
    Route { method: "GET", path: "/repo", need: Role::Anon, cost: 2, upstream: 1, public_read: true,
        params: &[p("repo", Kind::Repo, true, 3, 140, "owner/name")],
        docs: "One repo's metadata." },
    Route { method: "GET", path: "/readme", need: Role::Anon, cost: 1, upstream: 0, public_read: true,
        params: &[
            p("repo", Kind::Repo, true, 3, 140, "owner/name"),
            p("n", Kind::Int, false, 1, 20000, "characters to return"),
            p("branch", Kind::Slug, false, 1, 100, "branch to read from"),
            p("fresh", Kind::Bool, false, 0, 1, "skip the cache"),
        ],
        docs: "raw.githubusercontent.com — keyless and outside the API limiter." },
    Route { method: "GET", path: "/trending", need: Role::Anon, cost: 4, upstream: 1, public_read: true,
        params: &[
            p("language", Kind::Slug, false, 1, 40, "language: qualifier"),
            p("days", Kind::Int, false, 1, 365, "window"),
            p("n", Kind::Int, false, 1, 100, "how many"),
        ],
        docs: "Recently created, sorted by stars — the honest keyless version." },
    Route { method: "GET", path: "/rate", need: Role::Anon, cost: 1, upstream: 1, public_read: true,
        params: &[], docs: "GitHub quota left on the connection your token resolves to." },
    Route { method: "GET", path: "/cache", need: Role::Anon, cost: 1, upstream: 0, public_read: true,
        params: &[], docs: "What is warm. Keys only — never the cached bodies." },
    Route { method: "GET", path: "/access", need: Role::Anon, cost: 1, upstream: 0, public_read: true,
        params: &[], docs: "Owner, grants and bans. Public on purpose: a gate you cannot see is not a gate you can trust." },
    Route { method: "GET", path: "/github", need: Role::Reader, cost: 1, upstream: 1, public_read: false,
        params: &[], docs: "Which GitHub account your key is connected to. Never returns the token." },

    Route { method: "POST", path: "/clear_cache", need: Role::Write, cost: 5, upstream: 0, public_read: false,
        params: &[], docs: "Drop every cached search and README." },
    Route { method: "POST", path: "/connect", need: Role::Write, cost: 5, upstream: 1, public_read: false,
        params: &[
            p("token", Kind::Text, true, 8, 400, "a GitHub PAT — stored by the git module, never here"),
            p("address", Kind::Slug, false, 42, 42, "admin only: act for another key"),
        ],
        docs: "Attach a GitHub account. Delegated to the git module." },
    Route { method: "POST", path: "/disconnect", need: Role::Write, cost: 2, upstream: 0, public_read: false,
        params: &[
            p("address", Kind::Slug, false, 42, 42, "admin only: act for another key"),
            p("login", Kind::Slug, false, 1, 64, "which account, when several are attached"),
        ],
        docs: "Detach a GitHub account." },

    Route { method: "POST", path: "/grant", need: Role::Admin, cost: 2, upstream: 0, public_read: false,
        params: &[
            p("address", Kind::Slug, true, 42, 42, "0x key to grant"),
            p("role", Kind::Slug, false, 1, 16, "reader | write | admin"),
        ],
        docs: "Hand out a role. Owner is not grantable." },
    Route { method: "POST", path: "/revoke", need: Role::Admin, cost: 2, upstream: 0, public_read: false,
        params: &[p("address", Kind::Slug, true, 42, 42, "0x key to revoke")],
        docs: "Take a role away." },
    Route { method: "POST", path: "/ban", need: Role::Admin, cost: 2, upstream: 0, public_read: false,
        params: &[
            p("subject", Kind::Text, true, 3, 64, "a 0x key, or ip:1.2.3.4"),
            p("reason", Kind::Text, false, 0, 200, "recorded in the audit log"),
        ],
        docs: "Refuse a caller outright, before role or budget are even consulted." },
    Route { method: "POST", path: "/unban", need: Role::Admin, cost: 2, upstream: 0, public_read: false,
        params: &[p("subject", Kind::Text, true, 3, 64, "a 0x key, or ip:1.2.3.4")],
        docs: "Lift a ban." },
    Route { method: "GET", path: "/audit", need: Role::Admin, cost: 2, upstream: 0, public_read: false,
        params: &[
            p("n", Kind::Int, false, 1, 1000, "how many recent lines"),
            p("subject", Kind::Text, false, 0, 64, "filter to one caller"),
            p("denied", Kind::Bool, false, 0, 1, "only refusals"),
        ],
        docs: "The decision log: who asked for what, and what this module did about it." },
    Route { method: "POST", path: "/visibility", need: Role::Owner, cost: 1, upstream: 0, public_read: false,
        params: &[p("mode", Kind::Slug, true, 6, 7, "public | private")],
        docs: "Flip every read from open to reader-only in one call." },
    Route { method: "POST", path: "/limits", need: Role::Owner, cost: 1, upstream: 0, public_read: false,
        params: &[
            p("role", Kind::Slug, true, 1, 16, "which role's budget"),
            p("burst", Kind::Int, true, 0, 100_000, "bucket capacity"),
            p("per_minute", Kind::Int, true, 0, 100_000, "refill per minute"),
        ],
        docs: "Retune a role's budget without a redeploy." },
];

pub fn lookup(method: &str, path: &str) -> Option<&'static Route> {
    ROUTES.iter().find(|r| r.path == path && r.method == method)
}

/// Does any route answer this path under a different method? Lets the service
/// say "405, POST it" instead of a misleading 404.
pub fn path_exists(path: &str) -> bool {
    ROUTES.iter().any(|r| r.path == path)
}
