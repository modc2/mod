use crate::records::RecordType as MyRecordType;
use crate::store::Store;
use hickory_proto::op::{MessageType, OpCode, ResponseCode};
use hickory_proto::rr::rdata::{self, SOA, MX, SRV, CAA, TXT};
use hickory_proto::rr::{DNSClass, Name, RData, Record, RecordType};
use hickory_proto::serialize::binary::{BinDecodable, BinEncodable};
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddr};
use std::sync::Arc;
use tokio::net::{TcpListener, UdpSocket};
use tracing::{debug, info, warn};

pub async fn run(store: Arc<Store>, addr: SocketAddr, zones: Vec<String>) -> anyhow::Result<()> {
    let udp_store = store.clone();
    let tcp_store = store.clone();
    let udp_zones = zones.clone();
    let tcp_zones = zones;

    info!(%addr, "DNS server starting (UDP + TCP)");

    let udp = tokio::spawn(async move {
        if let Err(e) = run_udp(udp_store, addr, udp_zones).await {
            tracing::error!("DNS UDP error: {}", e);
        }
    });

    let tcp = tokio::spawn(async move {
        if let Err(e) = run_tcp(tcp_store, addr, tcp_zones).await {
            tracing::error!("DNS TCP error: {}", e);
        }
    });

    // Wait for both — they run forever under normal conditions
    let _ = tokio::join!(udp, tcp);
    Ok(())
}

async fn run_udp(
    store: Arc<Store>,
    addr: SocketAddr,
    zones: Vec<String>,
) -> anyhow::Result<()> {
    let socket = UdpSocket::bind(addr).await?;
    info!(addr = %addr, "DNS UDP listening");

    let mut buf = vec![0u8; 4096];
    loop {
        let (len, src) = socket.recv_from(&mut buf).await?;
        let data = buf[..len].to_vec();
        let store = store.clone();
        let zones = zones.clone();
        let socket_ref = &socket;

        // Parse and respond inline for UDP (fast path)
        match handle_query(&data, &store, &zones) {
            Ok(response) => {
                if let Err(e) = socket_ref.send_to(&response, src).await {
                    warn!(%src, "failed to send UDP response: {}", e);
                }
            }
            Err(e) => {
                debug!(%src, "bad DNS query: {}", e);
            }
        }
    }
}

async fn run_tcp(
    store: Arc<Store>,
    addr: SocketAddr,
    zones: Vec<String>,
) -> anyhow::Result<()> {
    let listener = TcpListener::bind(addr).await?;
    info!(addr = %addr, "DNS TCP listening");

    loop {
        let (mut stream, src) = listener.accept().await?;
        let store = store.clone();
        let zones = zones.clone();

        tokio::spawn(async move {
            use tokio::io::{AsyncReadExt, AsyncWriteExt};

            // TCP DNS: 2-byte length prefix
            let mut len_buf = [0u8; 2];
            if stream.read_exact(&mut len_buf).await.is_err() {
                return;
            }
            let msg_len = u16::from_be_bytes(len_buf) as usize;

            let mut data = vec![0u8; msg_len];
            if stream.read_exact(&mut data).await.is_err() {
                return;
            }

            match handle_query(&data, &store, &zones) {
                Ok(response) => {
                    let resp_len = (response.len() as u16).to_be_bytes();
                    let _ = stream.write_all(&resp_len).await;
                    let _ = stream.write_all(&response).await;
                }
                Err(e) => {
                    debug!(%src, "bad TCP DNS query: {}", e);
                }
            }
        });
    }
}

fn handle_query(
    data: &[u8],
    store: &Store,
    zones: &[String],
) -> Result<Vec<u8>, String> {
    // Parse the raw DNS message manually using hickory_proto
    let msg = hickory_proto::op::Message::from_bytes(data).map_err(|e| format!("parse error: {}", e))?;

    let mut response = hickory_proto::op::Message::new();
    response.set_id(msg.id());
    response.set_message_type(MessageType::Response);
    response.set_op_code(OpCode::Query);
    response.set_authoritative(true);
    response.set_recursion_available(false);

    if msg.op_code() != OpCode::Query {
        response.set_response_code(ResponseCode::NotImp);
        return response
            .to_bytes()
            .map_err(|e| format!("encode error: {}", e));
    }

    let queries = msg.queries();
    if queries.is_empty() {
        response.set_response_code(ResponseCode::FormErr);
        return response
            .to_bytes()
            .map_err(|e| format!("encode error: {}", e));
    }

    let query = &queries[0];
    let qname = query.name().to_string();
    let qtype = query.query_type();

    debug!(name = %qname, rtype = ?qtype, "DNS query");

    // Find which zone this query belongs to
    let zone = find_zone(&qname, zones);
    if zone.is_none() {
        response.set_response_code(ResponseCode::Refused);
        return response
            .to_bytes()
            .map_err(|e| format!("encode error: {}", e));
    }
    let zone = zone.unwrap();

    // Extract the record name (strip zone suffix)
    let record_name = extract_name(&qname, &zone);

    // Look up records
    let mut answers = Vec::new();

    if qtype == RecordType::ANY {
        // Return all record types for this name
        if let Ok(recs) = store.get_name(&zone, &record_name) {
            for rec in recs {
                if let Some(rr) = to_dns_record(&qname, &rec) {
                    answers.push(rr);
                }
            }
        }
    } else {
        // Look up specific type
        let my_type = hickory_to_my_type(qtype);
        if let Some(my_type) = my_type {
            if let Ok(Some(rec)) = store.get(&zone, &record_name, my_type) {
                if let Some(rr) = to_dns_record(&qname, &rec) {
                    answers.push(rr);
                }
            }
        }

        // If asking for A/AAAA but we have a CNAME, return that
        if answers.is_empty()
            && (qtype == RecordType::A || qtype == RecordType::AAAA)
        {
            if let Ok(Some(cname_rec)) = store.get(&zone, &record_name, MyRecordType::CNAME) {
                if let Some(rr) = to_dns_record(&qname, &cname_rec) {
                    answers.push(rr);
                }
            }
        }
    }

    if answers.is_empty() {
        response.set_response_code(ResponseCode::NXDomain);
        // Add SOA to authority section for negative caching
        if let Ok(Some(soa)) = store.get(&zone, "@", MyRecordType::SOA) {
            let zone_name = Name::from_ascii(&format!("{}.", zone)).unwrap_or_default();
            if let Some(rr) = to_dns_record(&zone_name.to_string(), &soa) {
                response.add_name_server(rr);
            }
        }
    } else {
        response.set_response_code(ResponseCode::NoError);
        for rr in answers {
            response.add_answer(rr);
        }
    }

    response
        .to_bytes()
        .map_err(|e| format!("encode error: {}", e))
}

fn find_zone(qname: &str, zones: &[String]) -> Option<String> {
    let qname_lower = qname.to_lowercase();
    // Remove trailing dot
    let qname_clean = qname_lower.trim_end_matches('.');

    for zone in zones {
        let zone_lower = zone.to_lowercase();
        if qname_clean == zone_lower || qname_clean.ends_with(&format!(".{}", zone_lower)) {
            return Some(zone_lower);
        }
    }
    None
}

fn extract_name(qname: &str, zone: &str) -> String {
    let qname_clean = qname.to_lowercase();
    let qname_clean = qname_clean.trim_end_matches('.');
    let zone_lower = zone.to_lowercase();

    if qname_clean == zone_lower {
        "@".to_string()
    } else {
        qname_clean
            .strip_suffix(&format!(".{}", zone_lower))
            .unwrap_or(qname_clean)
            .to_string()
    }
}

fn hickory_to_my_type(rt: RecordType) -> Option<MyRecordType> {
    match rt {
        RecordType::A => Some(MyRecordType::A),
        RecordType::AAAA => Some(MyRecordType::AAAA),
        RecordType::CNAME => Some(MyRecordType::CNAME),
        RecordType::MX => Some(MyRecordType::MX),
        RecordType::NS => Some(MyRecordType::NS),
        RecordType::SOA => Some(MyRecordType::SOA),
        RecordType::TXT => Some(MyRecordType::TXT),
        RecordType::SRV => Some(MyRecordType::SRV),
        RecordType::CAA => Some(MyRecordType::CAA),
        RecordType::PTR => Some(MyRecordType::PTR),
        _ => None,
    }
}

fn to_dns_record(
    qname: &str,
    rec: &crate::records::DnsRecord,
) -> Option<Record> {
    let name = Name::from_ascii(if qname.ends_with('.') {
        qname.to_string()
    } else {
        format!("{}.", qname)
    })
    .ok()?;

    let rdata = match rec.rtype {
        MyRecordType::A => {
            let addr: Ipv4Addr = rec.value.parse().ok()?;
            RData::A(addr.into())
        }
        MyRecordType::AAAA => {
            let addr: Ipv6Addr = rec.value.parse().ok()?;
            RData::AAAA(addr.into())
        }
        MyRecordType::CNAME => {
            let target = Name::from_ascii(ensure_dot(&rec.value)).ok()?;
            RData::CNAME(rdata::CNAME(target))
        }
        MyRecordType::NS => {
            let target = Name::from_ascii(ensure_dot(&rec.value)).ok()?;
            RData::NS(rdata::NS(target))
        }
        MyRecordType::PTR => {
            let target = Name::from_ascii(ensure_dot(&rec.value)).ok()?;
            RData::PTR(rdata::PTR(target))
        }
        MyRecordType::MX => {
            let parts: Vec<&str> = rec.value.splitn(2, ' ').collect();
            if parts.len() != 2 {
                return None;
            }
            let pref: u16 = parts[0].parse().ok()?;
            let exchange = Name::from_ascii(ensure_dot(parts[1])).ok()?;
            RData::MX(MX::new(pref, exchange))
        }
        MyRecordType::TXT => {
            RData::TXT(TXT::new(vec![rec.value.clone()]))
        }
        MyRecordType::SRV => {
            let parts: Vec<&str> = rec.value.splitn(4, ' ').collect();
            if parts.len() != 4 {
                return None;
            }
            let priority: u16 = parts[0].parse().ok()?;
            let weight: u16 = parts[1].parse().ok()?;
            let port: u16 = parts[2].parse().ok()?;
            let target = Name::from_ascii(ensure_dot(parts[3])).ok()?;
            RData::SRV(SRV::new(priority, weight, port, target))
        }
        MyRecordType::SOA => {
            let parts: Vec<&str> = rec.value.split_whitespace().collect();
            if parts.len() != 7 {
                return None;
            }
            let mname = Name::from_ascii(ensure_dot(parts[0])).ok()?;
            let rname = Name::from_ascii(ensure_dot(parts[1])).ok()?;
            let serial: u32 = parts[2].parse().ok()?;
            let refresh: i32 = parts[3].parse().ok()?;
            let retry: i32 = parts[4].parse().ok()?;
            let expire: i32 = parts[5].parse().ok()?;
            let minimum: u32 = parts[6].parse().ok()?;
            RData::SOA(SOA::new(mname, rname, serial, refresh, retry, expire, minimum))
        }
        MyRecordType::CAA => {
            let parts: Vec<&str> = rec.value.splitn(3, ' ').collect();
            if parts.len() != 3 {
                return None;
            }
            let issuer_critical: bool = parts[0] != "0";
            let tag = parts[1];
            let caa_value = parts[2].trim_matches('"');
            match tag {
                "issue" | "issuewild" => {
                    let issuer_name = if caa_value.is_empty() {
                        None
                    } else {
                        Some(Name::from_ascii(ensure_dot(caa_value)).ok()?)
                    };
                    if tag == "issuewild" {
                        RData::CAA(CAA::new_issuewild(issuer_critical, issuer_name, vec![]))
                    } else {
                        RData::CAA(CAA::new_issue(issuer_critical, issuer_name, vec![]))
                    }
                }
                _ => return None, // iodef and other tags not yet supported
            }
        }
    };

    let mut record = Record::from_rdata(name, rec.ttl, rdata);
    record.set_dns_class(DNSClass::IN);
    Some(record)
}

fn ensure_dot(s: &str) -> String {
    if s.ends_with('.') {
        s.to_string()
    } else {
        format!("{}.", s)
    }
}
