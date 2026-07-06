use std::{env, path::PathBuf};

#[derive(Debug, Clone)]
pub struct Config {
    pub port: u16,
    pub data_dir: PathBuf,
    pub snapshot_dir: PathBuf,
    pub cors_origins: Vec<String>,
    pub admin_key: Option<String>,
    #[allow(dead_code)]
    pub is_production: bool,
}

impl Config {
    pub fn load() -> anyhow::Result<Self> {
        let port: u16 = env::var("PORT")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(8840);

        let data_dir = env::var("BRIDGE_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
                PathBuf::from(home).join(".bridge")
            });

        // Snapshot dir defaults to <crate dir>/../snapshot — i.e. mod/orbit/bridge/snapshot/
        let snapshot_dir = env::var("BRIDGE_SNAPSHOT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let exe = env::current_exe().ok();
                // Walk up from the binary to find a sibling `snapshot` dir.
                if let Some(p) = exe {
                    let mut cur = p.as_path();
                    while let Some(parent) = cur.parent() {
                        let cand = parent.join("snapshot");
                        if cand.is_dir() {
                            return cand;
                        }
                        cur = parent;
                    }
                }
                // Fallback: relative to CARGO_MANIFEST_DIR when invoked via cargo run / tests.
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .map(|p| p.join("snapshot"))
                    .unwrap_or_else(|| PathBuf::from("snapshot"))
            });

        let is_production = env::var("BRIDGE_ENV").unwrap_or_default() == "production";

        let cors_origins: Vec<String> = match env::var("BRIDGE_CORS_ORIGINS") {
            Ok(v) if !v.is_empty() => v.split(',').map(|s| s.trim().to_string()).collect(),
            _ => {
                let mut o = vec![
                    "https://modc2.com".to_string(),
                    "https://bridge.modc2.com".to_string(),
                ];
                if !is_production {
                    o.push("http://localhost:3000".into());
                    o.push("http://localhost:8841".into());
                }
                o
            }
        };

        let admin_key = env::var("BRIDGE_ADMIN_KEY").ok().filter(|s| !s.is_empty());

        std::fs::create_dir_all(&data_dir)?;

        Ok(Self {
            port,
            data_dir,
            snapshot_dir,
            cors_origins,
            admin_key,
            is_production,
        })
    }

    pub fn claims_path(&self) -> PathBuf {
        self.data_dir.join("claims.json")
    }
    pub fn commitments_path(&self) -> PathBuf {
        self.data_dir.join("commitments.json")
    }
    pub fn used_sigs_path(&self) -> PathBuf {
        self.data_dir.join("used_signatures.json")
    }
    pub fn snapshot_path(&self) -> PathBuf {
        self.snapshot_dir.join("total_balances.json")
    }
}
