//! Who holds what — the access control list, on disk and reloaded per request.
//!
//! Reloading rather than caching is deliberate: `access.json` is also written
//! by the Python CLI (`m github/grant …`) and by a human with an editor. A
//! revocation that only takes effect after a restart is not a revocation, and
//! the file is a few hundred bytes.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::policy::Role;
use crate::store;

/// Where the owner of record is looked for, in order. The first two are this
/// module's own; the last is the box's, so a fresh install already has an
/// owner instead of being wide open.
const OWNER_FILES: [(&str, &str); 2] = [("github", "owner.json"), ("claude", "owner.json")];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Grant {
    pub role: String,
    #[serde(default)]
    pub granted_at: i64,
    #[serde(default)]
    pub granted_by: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ban {
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub at: i64,
    #[serde(default)]
    pub by: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Budget {
    /// Bucket capacity — the most a caller may spend in one gulp.
    pub burst: u32,
    /// Refill, per minute.
    pub per_minute: u32,
}

fn default_limits() -> BTreeMap<String, Budget> {
    // Priced against `cost` in the policy table, where a search is 10. So an
    // anonymous caller gets ~3 searches a minute and may burst 6 — which is
    // roughly what GitHub's own anonymous 10/min will actually support.
    BTreeMap::from([
        ("anon".into(), Budget { burst: 60, per_minute: 30 }),
        ("reader".into(), Budget { burst: 200, per_minute: 120 }),
        ("write".into(), Budget { burst: 600, per_minute: 400 }),
        ("admin".into(), Budget { burst: 1200, per_minute: 800 }),
        ("owner".into(), Budget { burst: 6000, per_minute: 4000 }),
    ])
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Acl {
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub grants: BTreeMap<String, Grant>,
    #[serde(default)]
    pub bans: BTreeMap<String, Ban>,
    /// "public" — reads are open to anyone. "private" — every read needs a
    /// signed key with at least `reader`.
    #[serde(default = "public")]
    pub visibility: String,
    #[serde(default = "default_limits")]
    pub limits: BTreeMap<String, Budget>,
}

fn public() -> String {
    "public".into()
}

impl Default for Acl {
    fn default() -> Self {
        Self {
            owner: None,
            grants: BTreeMap::new(),
            bans: BTreeMap::new(),
            visibility: public(),
            limits: default_limits(),
        }
    }
}

impl Acl {
    pub fn load() -> Self {
        let mut acl: Acl = store::read(&store::path("access.json"));
        if acl.limits.is_empty() {
            acl.limits = default_limits();
        }
        acl
    }

    pub fn save(&self) -> std::io::Result<()> {
        store::write(&store::path("access.json"), self, false)
    }

    /// The owner of record: this module's ACL, then the pinned owner files.
    pub fn owner(&self) -> Option<String> {
        if let Some(o) = self.owner.as_ref().filter(|o| !o.trim().is_empty()) {
            return Some(o.clone());
        }
        for (module, file) in OWNER_FILES {
            #[derive(Default, Deserialize)]
            struct OwnerRec {
                owner: Option<String>,
            }
            let rec: OwnerRec = store::read(&store::sibling(module, file));
            if let Some(o) = rec.owner.filter(|o| !o.trim().is_empty()) {
                return Some(o);
            }
        }
        None
    }

    /// A caller's role. Anonymous callers are `Anon`; a signed key with no
    /// grant is `Reader`, which differs from anonymous only in budget — being
    /// willing to prove who you are buys you rate limit, not privilege.
    pub fn role_of(&self, address: Option<&str>) -> Role {
        let Some(addr) = address else { return Role::Anon };
        let a = addr.to_lowercase();
        if self.owner().map(|o| o.to_lowercase()) == Some(a.clone()) {
            return Role::Owner;
        }
        self.grants
            .iter()
            .find(|(k, _)| k.to_lowercase() == a)
            .and_then(|(_, g)| Role::parse(&g.role))
            .unwrap_or(Role::Reader)
    }

    /// Is this caller refused outright? Checked against the key *and* the IP,
    /// before role or budget — a ban is not a low budget.
    pub fn banned(&self, address: Option<&str>, ip: &str) -> Option<(String, Ban)> {
        let mut subjects = vec![format!("ip:{ip}")];
        if let Some(a) = address {
            subjects.push(a.to_lowercase());
        }
        for s in subjects {
            if let Some((k, b)) = self.bans.iter().find(|(k, _)| k.to_lowercase() == s) {
                return Some((k.clone(), b.clone()));
            }
        }
        None
    }

    pub fn is_private(&self) -> bool {
        self.visibility.eq_ignore_ascii_case("private")
    }

    pub fn budget(&self, role: Role) -> Budget {
        self.limits
            .get(role.name())
            .cloned()
            .unwrap_or(Budget { burst: 60, per_minute: 30 })
    }
}
