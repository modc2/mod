use crate::records::GossipMessage;
use crate::store::Store;
use futures::StreamExt;
use libp2p::{
    gossipsub, identify, kad, mdns, noise,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, SwarmBuilder,
};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

#[derive(NetworkBehaviour)]
struct DnsBehaviour {
    kademlia: kad::Behaviour<kad::store::MemoryStore>,
    gossipsub: gossipsub::Behaviour,
    mdns: mdns::tokio::Behaviour,
    identify: identify::Behaviour,
}

pub async fn run(
    store: Arc<Store>,
    listen_port: u16,
    zones: Vec<String>,
    bootstrap_peers: Vec<String>,
    mut gossip_rx: mpsc::Receiver<GossipMessage>,
    peer_count: Arc<AtomicUsize>,
) -> anyhow::Result<()> {
    // Build swarm
    let mut swarm = SwarmBuilder::with_new_identity()
        .with_tokio()
        .with_tcp(
            tcp::Config::default(),
            noise::Config::new,
            yamux::Config::default,
        )?
        .with_behaviour(|key| {
            // Kademlia
            let peer_id = key.public().to_peer_id();
            let kad_store = kad::store::MemoryStore::new(peer_id);
            let kad_config = kad::Config::new(libp2p::StreamProtocol::new("/mod-dns/kad/1.0.0"));
            let kademlia = kad::Behaviour::with_config(peer_id, kad_store, kad_config);

            // GossipSub
            let message_id_fn = |message: &gossipsub::Message| {
                let mut s = DefaultHasher::new();
                message.data.hash(&mut s);
                message.topic.hash(&mut s);
                gossipsub::MessageId::from(s.finish().to_string())
            };
            let gossipsub_config = gossipsub::ConfigBuilder::default()
                .heartbeat_interval(Duration::from_secs(10))
                .validation_mode(gossipsub::ValidationMode::Strict)
                .message_id_fn(message_id_fn)
                .build()
                .expect("valid gossipsub config");
            let gossipsub = gossipsub::Behaviour::new(
                gossipsub::MessageAuthenticity::Signed(key.clone()),
                gossipsub_config,
            )
            .expect("valid gossipsub behaviour");

            // mDNS for local peer discovery
            let mdns = mdns::tokio::Behaviour::new(
                mdns::Config::default(),
                peer_id,
            )
            .expect("valid mdns");

            // Identify protocol
            let identify = identify::Behaviour::new(identify::Config::new(
                "/mod-dns/id/1.0.0".into(),
                key.public(),
            ));

            DnsBehaviour {
                kademlia,
                gossipsub,
                mdns,
                identify,
            }
        })?
        .with_swarm_config(|cfg| cfg.with_idle_connection_timeout(Duration::from_secs(60)))
        .build();

    let local_peer_id = *swarm.local_peer_id();
    info!(%local_peer_id, "P2P node starting");

    // Subscribe to zone topics
    for zone in &zones {
        let topic = gossipsub::IdentTopic::new(format!("dns/{}", zone));
        swarm.behaviour_mut().gossipsub.subscribe(&topic)?;
        info!(zone, "subscribed to gossip topic");
    }

    // Listen
    let listen_addr: Multiaddr = format!("/ip4/0.0.0.0/tcp/{}", listen_port).parse()?;
    swarm.listen_on(listen_addr)?;

    // Bootstrap peers
    for peer_addr in &bootstrap_peers {
        if peer_addr.is_empty() {
            continue;
        }
        match peer_addr.parse::<Multiaddr>() {
            Ok(addr) => {
                info!(%addr, "dialing bootstrap peer");
                if let Err(e) = swarm.dial(addr.clone()) {
                    warn!(%addr, "failed to dial: {}", e);
                }
            }
            Err(e) => {
                warn!(peer_addr, "invalid multiaddr: {}", e);
            }
        }
    }

    // Event loop
    loop {
        tokio::select! {
            // Outgoing gossip messages from API
            Some(msg) = gossip_rx.recv() => {
                let topic = gossipsub::IdentTopic::new(format!("dns/{}", msg.zone));
                let data = serde_json::to_vec(&msg).unwrap_or_default();
                match swarm.behaviour_mut().gossipsub.publish(topic, data) {
                    Ok(_) => debug!(zone = %msg.zone, name = %msg.record.name, "published to gossip"),
                    Err(e) => debug!(zone = %msg.zone, "gossip publish (may have no peers): {}", e),
                }
            }

            // Swarm events
            event = swarm.select_next_some() => {
                match event {
                    SwarmEvent::Behaviour(DnsBehaviourEvent::Gossipsub(
                        gossipsub::Event::Message { message, .. },
                    )) => {
                        match serde_json::from_slice::<GossipMessage>(&message.data) {
                            Ok(msg) => {
                                match store.merge(&msg.zone, &msg.record) {
                                    Ok(true) => info!(
                                        zone = %msg.zone,
                                        name = %msg.record.name,
                                        rtype = %msg.record.rtype,
                                        "merged record from peer"
                                    ),
                                    Ok(false) => debug!("record already up to date"),
                                    Err(e) => warn!("merge error: {}", e),
                                }
                            }
                            Err(e) => warn!("invalid gossip message: {}", e),
                        }
                    }

                    SwarmEvent::Behaviour(DnsBehaviourEvent::Mdns(
                        mdns::Event::Discovered(peers),
                    )) => {
                        for (peer_id, addr) in peers {
                            info!(%peer_id, %addr, "mDNS: discovered peer");
                            swarm.behaviour_mut().kademlia.add_address(&peer_id, addr);
                        }
                        update_peer_count(&swarm, &peer_count);
                    }

                    SwarmEvent::Behaviour(DnsBehaviourEvent::Mdns(
                        mdns::Event::Expired(peers),
                    )) => {
                        for (peer_id, _addr) in peers {
                            debug!(%peer_id, "mDNS: peer expired");
                        }
                        update_peer_count(&swarm, &peer_count);
                    }

                    SwarmEvent::Behaviour(DnsBehaviourEvent::Identify(
                        identify::Event::Received { peer_id, info: id_info, .. },
                    )) => {
                        debug!(%peer_id, protocol = %id_info.protocol_version, "identified peer");
                        for addr in id_info.listen_addrs {
                            swarm.behaviour_mut().kademlia.add_address(&peer_id, addr);
                        }
                        update_peer_count(&swarm, &peer_count);
                    }

                    SwarmEvent::ConnectionEstablished { peer_id, .. } => {
                        info!(%peer_id, "connected to peer");
                        update_peer_count(&swarm, &peer_count);
                    }

                    SwarmEvent::ConnectionClosed { peer_id, .. } => {
                        info!(%peer_id, "disconnected from peer");
                        update_peer_count(&swarm, &peer_count);
                    }

                    SwarmEvent::NewListenAddr { address, .. } => {
                        info!(%address, "listening on");
                    }

                    _ => {}
                }
            }
        }
    }
}

fn update_peer_count(
    swarm: &libp2p::Swarm<DnsBehaviour>,
    peer_count: &Arc<AtomicUsize>,
) {
    let count = swarm.connected_peers().count();
    peer_count.store(count, Ordering::Relaxed);
}
