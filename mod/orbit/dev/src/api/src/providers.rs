//! The provider registry — the heart of the gateway's modularity.
//!
//! A `Provider` is one template: an OpenAI-API-standard endpoint (`base_url`
//! exposing `/models` and `/chat/completions`), a backend-key env var, and some
//! presentation metadata. venice / openrouter / codex / claude are just seeded
//! instances; the owner can add more at runtime — they're persisted to
//! `<data_dir>/providers.json` and reloaded on boot.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// One OpenAI-compatible provider. This struct *is* the "model template".
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Provider {
    pub name: String,
    #[serde(default)]
    pub label: String,
    /// OpenAI-compatible API root, e.g. `https://openrouter.ai/api/v1`.
    pub base_url: String,
    /// Env var holding the server/backend key used for the funded fallback path.
    #[serde(default)]
    pub backend_env: String,
    #[serde(default)]
    pub default_model: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub color: String,
    /// Users may store their own key for this provider.
    #[serde(default = "default_true")]
    pub byok: bool,
    /// Seeded providers can't be deleted via the API.
    #[serde(default)]
    pub builtin: bool,
}

fn default_true() -> bool {
    true
}

impl Provider {
    /// Does the environment carry a backend key for this provider?
    pub fn has_backend(&self) -> bool {
        !self.backend_key().unwrap_or_default().is_empty()
    }

    /// The backend key from the environment, if `backend_env` names a set,
    /// non-empty variable.
    pub fn backend_key(&self) -> Option<String> {
        if self.backend_env.is_empty() {
            return None;
        }
        std::env::var(&self.backend_env)
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    }

    /// Public view (safe to serialize to clients — no secrets, only booleans).
    pub fn public(&self) -> Value {
        serde_json::json!({
            "name": self.name,
            "label": if self.label.is_empty() { self.name.clone() } else { self.label.clone() },
            "base_url": self.base_url,
            "default_model": self.default_model,
            "icon": self.icon,
            "color": self.color,
            "byok": self.byok,
            "builtin": self.builtin,
            "has_backend": self.has_backend(),
        })
    }
}

/// In-memory provider set, persisted to `<data_dir>/providers.json`.
pub struct Registry {
    path: PathBuf,
    providers: Vec<Provider>,
}

impl Registry {
    /// Load the persisted registry; if none exists, seed from `seed` and write
    /// it out. `seed` is the `providers` block from the module's config.json.
    pub fn load_or_seed(seed: Vec<Provider>) -> Self {
        let base = std::env::var("DEV_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
                PathBuf::from(home).join(".mod").join("dev")
            });
        std::fs::create_dir_all(&base).ok();
        let path = base.join("providers.json");

        let mut reg = if let Ok(raw) = std::fs::read_to_string(&path) {
            match serde_json::from_str::<Vec<Provider>>(&raw) {
                Ok(providers) => Self { path, providers },
                Err(e) => {
                    tracing::warn!("providers.json malformed ({e}); reseeding");
                    Self { path, providers: seed.clone() }
                }
            }
        } else {
            Self { path, providers: seed.clone() }
        };

        // Make sure every seeded (builtin) provider is present even if an older
        // persisted file predates it. Existing entries win (preserve user edits).
        let mut changed = false;
        for s in seed {
            if !reg.providers.iter().any(|p| p.name == s.name) {
                reg.providers.push(s);
                changed = true;
            }
        }
        if changed || !reg.path.exists() {
            reg.persist();
        }
        reg
    }

    fn persist(&self) {
        match serde_json::to_string_pretty(&self.providers) {
            Ok(s) => {
                if let Err(e) = std::fs::write(&self.path, s) {
                    tracing::error!("persist providers.json: {e}");
                }
            }
            Err(e) => tracing::error!("serialize providers: {e}"),
        }
    }

    pub fn list(&self) -> &[Provider] {
        &self.providers
    }

    pub fn names(&self) -> Vec<String> {
        self.providers.iter().map(|p| p.name.clone()).collect()
    }

    pub fn get(&self, name: &str) -> Option<Provider> {
        self.providers.iter().find(|p| p.name == name).cloned()
    }

    /// Add or replace a provider, then persist. Returns the stored provider.
    /// A new provider added at runtime is never `builtin` (so it stays deletable).
    pub fn add(&mut self, mut p: Provider) -> Result<Provider, String> {
        p.name = p.name.trim().to_lowercase();
        if p.name.is_empty()
            || !p.name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err("name must be non-empty alphanumeric (with - or _)".into());
        }
        if !(p.base_url.starts_with("http://") || p.base_url.starts_with("https://")) {
            return Err("base_url must be an http(s) URL".into());
        }
        p.base_url = p.base_url.trim_end_matches('/').to_string();
        if p.label.is_empty() {
            p.label = p.name.clone();
        }
        p.builtin = false;
        if let Some(slot) = self.providers.iter_mut().find(|x| x.name == p.name) {
            if slot.builtin {
                return Err(format!("{} is a builtin provider and can't be replaced", p.name));
            }
            *slot = p.clone();
        } else {
            self.providers.push(p.clone());
        }
        self.persist();
        Ok(p)
    }

    /// Remove a non-builtin provider. Returns whether one was removed.
    pub fn remove(&mut self, name: &str) -> Result<bool, String> {
        match self.providers.iter().find(|p| p.name == name) {
            Some(p) if p.builtin => Err(format!("{name} is a builtin provider and can't be removed")),
            Some(_) => {
                self.providers.retain(|p| p.name != name);
                self.persist();
                Ok(true)
            }
            None => Ok(false),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seed() -> Vec<Provider> {
        vec![Provider {
            name: "venice".into(),
            label: "Venice".into(),
            base_url: "https://api.venice.ai/api/v1".into(),
            backend_env: "VENICE_API_KEY".into(),
            default_model: "zai-org-glm-5".into(),
            icon: "V".into(),
            color: "#b91c1c".into(),
            byok: true,
            builtin: true,
        }]
    }

    #[test]
    fn add_and_remove_runtime_provider() {
        let dir = std::env::temp_dir().join(format!(
            "dev-prov-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::env::set_var("DEV_DATA_DIR", &dir);
        let mut reg = Registry::load_or_seed(seed());
        assert!(reg.get("venice").is_some());

        // builtin can't be removed
        assert!(reg.remove("venice").is_err());

        // add a new OpenAI-standard provider at runtime
        let groq = Provider {
            name: "groq".into(),
            label: "".into(),
            base_url: "https://api.groq.com/openai/v1/".into(),
            backend_env: "GROQ_API_KEY".into(),
            default_model: "llama-3.3-70b".into(),
            icon: "G".into(),
            color: "#f55036".into(),
            byok: true,
            builtin: false,
        };
        let stored = reg.add(groq).unwrap();
        assert_eq!(stored.label, "groq"); // defaulted from name
        assert_eq!(stored.base_url, "https://api.groq.com/openai/v1"); // trailing slash trimmed
        assert!(!stored.builtin);
        assert!(reg.remove("groq").unwrap());
        assert!(reg.get("groq").is_none());

        // bad inputs rejected
        assert!(reg.add(Provider { name: "bad name".into(), base_url: "https://x/v1".into(), ..seed()[0].clone() }).is_err());
        assert!(reg.add(Provider { name: "ok".into(), base_url: "ftp://x".into(), ..seed()[0].clone() }).is_err());
    }
}
