//! In-memory hub state: user registry + discovered fleet + probe cache.
//! User entries shadow a fleet entry with the same id; the disabled set hides
//! any server from aggregation without deleting it.

use crate::store::{self, PeerHub, Probe, ServerEntry};
use crate::{fleet, upstream};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio::sync::RwLock;

pub struct AppState {
    pub user: RwLock<Vec<ServerEntry>>,
    pub disabled: RwLock<HashSet<String>>,
    pub fleet: RwLock<HashMap<String, ServerEntry>>,
    /// Mods caught serving MCP by the live sweep rather than by declaration.
    pub swept: RwLock<HashMap<String, ServerEntry>>,
    pub probes: RwLock<HashMap<String, Probe>>,
    pub started_at: u64,
    pub swept_at: RwLock<u64>,
    /// Other hubs known by URL (hubs.json) and the last probe of every hub.
    pub peers: RwLock<Vec<PeerHub>>,
    pub hubs_cache: RwLock<Option<(u64, Vec<crate::hubs::Hub>)>>,
}

impl AppState {
    pub fn load() -> Arc<Self> {
        let hub = store::load_hub();
        Arc::new(Self {
            user: RwLock::new(hub.servers),
            disabled: RwLock::new(hub.disabled),
            fleet: RwLock::new(fleet::discover()),
            swept: RwLock::new(hub.swept.into_iter().map(|s| (s.id.clone(), s)).collect()),
            probes: RwLock::new(store::load_probes()),
            started_at: store::now(),
            swept_at: RwLock::new(0),
            peers: RwLock::new(store::load_hubs()),
            hubs_cache: RwLock::new(None),
        })
    }

    pub async fn persist(&self) {
        let hub = store::HubFile {
            servers: self.user.read().await.clone(),
            disabled: self.disabled.read().await.clone(),
            swept: self.swept.read().await.values().cloned().collect(),
        };
        store::save_hub(&hub);
    }

    pub async fn persist_hubs(&self) {
        store::save_hubs(&self.peers.read().await.clone());
        *self.hubs_cache.write().await = None;
    }

    /// sweep ∪ fleet ∪ user — a declared endpoint outranks a swept one (it is
    /// the mod's own word on where it serves, and survives being asleep), and
    /// anything the user registered by hand outranks both.
    pub async fn all_servers(&self) -> Vec<ServerEntry> {
        let mut map = self.swept.read().await.clone();
        for (id, s) in self.fleet.read().await.iter() {
            map.insert(id.clone(), s.clone());
        }
        for s in self.user.read().await.iter() {
            map.insert(s.id.clone(), s.clone());
        }
        let mut list: Vec<ServerEntry> = map.into_values().collect();
        list.sort_by(|a, b| a.id.cmp(&b.id));
        list
    }

    pub async fn enabled_servers(&self) -> Vec<ServerEntry> {
        let disabled = self.disabled.read().await.clone();
        self.all_servers()
            .await
            .into_iter()
            .filter(|s| !disabled.contains(&s.id))
            .collect()
    }

    pub async fn get(&self, id: &str) -> Option<ServerEntry> {
        self.all_servers().await.into_iter().find(|s| s.id == id)
    }

    pub async fn set_probe(&self, id: &str, probe: Probe) {
        let snapshot = {
            let mut probes = self.probes.write().await;
            probes.insert(id.to_string(), probe);
            probes.clone()
        };
        store::save_probes(&snapshot);
    }

    /// `wake` asks the activator to start a sleeping local mod before giving up
    /// on it — what the console's "wake" button and an explicit re-probe do.
    pub async fn refresh_one_wake(&self, id: &str, wake: bool) -> Option<Probe> {
        let server = self.get(id).await?;
        let probe = upstream::probe_in(&server, upstream::default_timeout(), wake).await;
        self.set_probe(id, probe.clone()).await;
        Some(probe)
    }

    /// Knock on every port the fleet mentions and keep whatever speaks MCP.
    /// Returns the ids that answered. Servers already known by declaration or
    /// by hand are skipped — this pass exists to find the undeclared ones.
    pub async fn sweep(&self) -> Vec<String> {
        let known: HashSet<String> = self
            .fleet
            .read()
            .await
            .keys()
            .cloned()
            .chain(self.user.read().await.iter().map(|s| s.id.clone()))
            .collect();
        let candidates: Vec<ServerEntry> = fleet::sweep_candidates()
            .into_iter()
            .filter(|c| !known.contains(&c.id))
            .collect();

        // A few hundred connection attempts, most of them to a closed port:
        // fan out in slices so the whole sweep is a couple of seconds, not a
        // couple of minutes, and never let it exhaust the file-descriptor
        // budget the rest of the hub is sharing.
        let mut hits: HashMap<String, (ServerEntry, Probe)> = HashMap::new();
        for chunk in candidates.chunks(48) {
            let jobs = chunk.iter().map(|c| async move {
                let p = upstream::probe_in(c, upstream::SWEEP_TIMEOUT, false).await;
                (c.clone(), p)
            });
            for (entry, probe) in futures_util::future::join_all(jobs).await {
                if probe.ok && !hits.contains_key(&entry.id) {
                    hits.insert(entry.id.clone(), (entry, probe));
                }
            }
        }

        let found: Vec<String> = {
            let mut swept = self.swept.write().await;
            // Drop rows from earlier sweeps that no longer answer — a mod that
            // moved its port should not linger as a permanently-down card.
            swept.retain(|id, _| hits.contains_key(id));
            for (id, (entry, _)) in &hits {
                swept.insert(id.clone(), entry.clone());
            }
            let mut ids: Vec<String> = swept.keys().cloned().collect();
            ids.sort();
            ids
        };
        {
            let mut probes = self.probes.write().await;
            for (id, (_, p)) in hits {
                probes.insert(id, p);
            }
        }
        *self.swept_at.write().await = store::now();
        self.persist().await;
        store::save_probes(&self.probes.read().await.clone());
        found
    }

    /// Re-scan the fleet and re-probe every enabled server, concurrently.
    pub async fn refresh_all(&self) {
        {
            let mut f = self.fleet.write().await;
            *f = fleet::discover();
        }
        // An idle stdio server has been reaped on purpose. Re-probing it would
        // restart every installed process on a timer and, worse, replace its
        // cached tool list with "not running" — so it keeps the probe it had
        // and the next tool call starts it again.
        let mut servers = Vec::new();
        for s in self.enabled_servers().await {
            if s.is_stdio() && !crate::stdio::is_running(&s.id).await {
                continue;
            }
            servers.push(s);
        }
        let jobs = servers.into_iter().map(|s| async move {
            let p = upstream::probe(&s).await;
            (s.id, p)
        });
        let results = futures_util::future::join_all(jobs).await;
        let snapshot = {
            let mut probes = self.probes.write().await;
            for (id, p) in results {
                probes.insert(id, p);
            }
            probes.clone()
        };
        store::save_probes(&snapshot);
    }
}
