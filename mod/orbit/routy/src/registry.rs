use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use url::Url;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Website {
    pub name: String,
    pub target_url: String,
    pub description: Option<String>,
    pub storage_type: Option<String>,
    pub cid: Option<String>,
    pub created_at: i64,
}

#[derive(Debug, Deserialize)]
pub struct RegisterRequest {
    pub name: String,
    pub target_url: String,
    pub description: Option<String>,
    pub storage_type: Option<String>,
    pub cid: Option<String>,
    #[serde(default = "default_type")]
    pub website_type: String,
}

fn default_type() -> String {
    "app".to_string()
}

#[derive(Debug, Deserialize)]
pub struct SyncRequest {
    #[serde(default)]
    pub apps: Vec<RegisterRequest>,
    #[serde(default)]
    pub apis: Vec<RegisterRequest>,
}

pub struct WebsiteRegistry {
    apps: Arc<DashMap<String, Website>>,
    apis: Arc<DashMap<String, Website>>,
}

impl WebsiteRegistry {
    pub fn new() -> Self {
        Self {
            apps: Arc::new(DashMap::new()),
            apis: Arc::new(DashMap::new()),
        }
    }

    fn upsert(map: &DashMap<String, Website>, req: RegisterRequest) -> Result<(), String> {
        Url::parse(&req.target_url).map_err(|e| format!("Invalid URL: {}", e))?;

        if !req
            .name
            .chars()
            .all(|c| c.is_alphanumeric() || c == '-' || c == '_')
        {
            return Err("Name must be alphanumeric with hyphens/underscores only".to_string());
        }

        let website = Website {
            name: req.name.clone(),
            target_url: req.target_url,
            description: req.description,
            storage_type: req.storage_type,
            cid: req.cid,
            created_at: chrono::Utc::now().timestamp(),
        };

        map.insert(req.name, website);
        Ok(())
    }

    pub async fn register(&self, req: RegisterRequest) -> Result<(), String> {
        let map = if req.website_type == "api" {
            &self.apis
        } else {
            &self.apps
        };
        Self::upsert(map, req)
    }

    pub async fn get_app(&self, name: &str) -> Option<Website> {
        self.apps.get(name).map(|e| e.value().clone())
    }

    pub async fn get_api(&self, name: &str) -> Option<Website> {
        self.apis.get(name).map(|e| e.value().clone())
    }

    pub async fn list_apps(&self) -> Vec<Website> {
        self.apps.iter().map(|e| e.value().clone()).collect()
    }

    pub async fn list_apis(&self) -> Vec<Website> {
        self.apis.iter().map(|e| e.value().clone()).collect()
    }

    pub fn clear(&self) {
        self.apps.clear();
        self.apis.clear();
    }

    pub async fn sync(&self, req: SyncRequest) -> Result<usize, String> {
        self.clear();
        let mut count = 0;
        for app in req.apps {
            Self::upsert(&self.apps, app)?;
            count += 1;
        }
        for api in req.apis {
            Self::upsert(&self.apis, api)?;
            count += 1;
        }
        Ok(count)
    }
}

impl Default for WebsiteRegistry {
    fn default() -> Self {
        Self::new()
    }
}
