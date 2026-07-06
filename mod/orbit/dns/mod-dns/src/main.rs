mod api;
mod dns;
mod p2p;
mod records;
mod store;

use clap::Parser;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::AtomicUsize;
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::info;

#[derive(Parser, Debug)]
#[command(name = "mod-dns", about = "Decentralized authoritative DNS server")]
struct Args {
    /// Zones to serve (comma-separated, e.g. "modc2.com,example.org")
    #[arg(long, value_delimiter = ',')]
    zone: Vec<String>,

    /// DNS server port (UDP + TCP). Use 53 in production (requires root/CAP_NET_BIND_SERVICE).
    #[arg(long, default_value = "15353")]
    dns_port: u16,

    /// HTTP API port
    #[arg(long, default_value = "5380")]
    api_port: u16,

    /// P2P swarm port
    #[arg(long, default_value = "5381")]
    p2p_port: u16,

    /// Data directory for persistent storage
    #[arg(long, default_value = "~/.mod-dns/data")]
    data_dir: String,

    /// Bootstrap peer multiaddrs (comma-separated)
    #[arg(long, value_delimiter = ',', default_value = "")]
    bootstrap_peers: Vec<String>,

    /// Bind address
    #[arg(long, default_value = "0.0.0.0")]
    bind: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Init tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "mod_dns=info".into()),
        )
        .init();

    let args = Args::parse();

    // Expand ~ in data_dir
    let data_dir = expand_tilde(&args.data_dir);
    std::fs::create_dir_all(&data_dir)?;

    // Generate a stable node ID from the data dir (persists across restarts)
    let node_id = get_or_create_node_id(&data_dir)?;
    info!(%node_id, "node identity");

    // Open store
    let store = store::Store::open(&data_dir, node_id)?;

    // Ensure configured zones exist (create SOA if missing)
    for zone in &args.zone {
        if store.get(zone, "@", records::RecordType::SOA).map_err(|e| anyhow::anyhow!(e))?.is_none() {
            let soa = records::DnsRecord::new(
                "@".into(),
                records::RecordType::SOA,
                format!(
                    "ns1.{zone}. admin.{zone}. 1 3600 900 604800 300"
                ),
                3600,
                store.node_id.clone(),
            );
            store.put(zone, soa).map_err(|e| anyhow::anyhow!(e))?;

            let ns = records::DnsRecord::new(
                "@".into(),
                records::RecordType::NS,
                format!("ns1.{}.", zone),
                3600,
                store.node_id.clone(),
            );
            store.put(zone, ns).map_err(|e| anyhow::anyhow!(e))?;

            info!(zone, "initialized zone with SOA + NS");
        }
    }

    // Channels for API → P2P gossip
    let (gossip_tx, gossip_rx) = mpsc::channel(256);
    let peer_count = Arc::new(AtomicUsize::new(0));

    let dns_addr: SocketAddr = format!("{}:{}", args.bind, args.dns_port).parse()?;
    let api_addr: SocketAddr = format!("{}:{}", args.bind, args.api_port).parse()?;

    let dns_store = store.clone();
    let api_store = store.clone();
    let p2p_store = store.clone();

    let dns_zones = args.zone.clone();
    let api_zones = args.zone.clone();
    let p2p_zones = args.zone.clone();
    let p2p_peers = args.bootstrap_peers.clone();

    let api_peer_count = peer_count.clone();
    let p2p_peer_count = peer_count.clone();

    info!(
        zones = ?args.zone,
        dns = %dns_addr,
        api = %api_addr,
        p2p_port = args.p2p_port,
        "mod-dns starting"
    );

    // Spawn all three services
    let dns_handle = tokio::spawn(async move {
        if let Err(e) = dns::run(dns_store, dns_addr, dns_zones).await {
            tracing::error!("DNS server error: {}", e);
        }
    });

    let api_handle = tokio::spawn(async move {
        if let Err(e) = api::run(api_store, api_addr, api_zones, gossip_tx, api_peer_count).await {
            tracing::error!("API server error: {}", e);
        }
    });

    let p2p_handle = tokio::spawn(async move {
        if let Err(e) = p2p::run(
            p2p_store,
            args.p2p_port,
            p2p_zones,
            p2p_peers,
            gossip_rx,
            p2p_peer_count,
        )
        .await
        {
            tracing::error!("P2P error: {}", e);
        }
    });

    // Wait for any to finish (they shouldn't under normal operation)
    tokio::select! {
        _ = dns_handle => { tracing::error!("DNS server exited"); }
        _ = api_handle => { tracing::error!("API server exited"); }
        _ = p2p_handle => { tracing::error!("P2P layer exited"); }
        _ = tokio::signal::ctrl_c() => {
            info!("shutting down");
        }
    }

    Ok(())
}

fn expand_tilde(path: &str) -> PathBuf {
    if path.starts_with("~/") {
        if let Some(home) = dirs_home() {
            return PathBuf::from(home).join(&path[2..]);
        }
    }
    PathBuf::from(path)
}

fn dirs_home() -> Option<String> {
    std::env::var("HOME").ok()
}

fn get_or_create_node_id(data_dir: &PathBuf) -> anyhow::Result<String> {
    let id_file = data_dir.join("node_id");
    if id_file.exists() {
        Ok(std::fs::read_to_string(&id_file)?.trim().to_string())
    } else {
        let id = format!(
            "{:016x}",
            rand::random::<u64>()
        );
        std::fs::write(&id_file, &id)?;
        Ok(id)
    }
}
