use serde::{Deserialize, Serialize};
use std::env;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub max_websites: usize,
    pub cpu_limit_percent: f32,
    pub memory_limit_mb: usize,
    pub enable_monitoring: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: "0.0.0.0".to_string(),
            port: 3000,
            max_websites: 100,
            cpu_limit_percent: 90.0,
            memory_limit_mb: 2048,
            enable_monitoring: true,
        }
    }
}

impl Config {
    pub fn load() -> anyhow::Result<Self> {
        let mut config = Config::default();

        // Env vars override defaults
        if let Ok(host) = env::var("ROUTY_HOST") {
            config.host = host;
        }
        if let Ok(port) = env::var("ROUTY_PORT") {
            if let Ok(p) = port.parse() {
                config.port = p;
            }
        }
        if let Ok(port) = env::var("PORT") {
            if let Ok(p) = port.parse() {
                config.port = p;
            }
        }

        Ok(config)
    }
}
