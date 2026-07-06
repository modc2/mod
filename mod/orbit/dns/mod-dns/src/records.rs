use serde::{Deserialize, Serialize};
use std::fmt;
use std::net::{Ipv4Addr, Ipv6Addr};
use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum RecordType {
    A,
    AAAA,
    CNAME,
    MX,
    NS,
    SOA,
    TXT,
    SRV,
    CAA,
    PTR,
}

impl fmt::Display for RecordType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RecordType::A => write!(f, "A"),
            RecordType::AAAA => write!(f, "AAAA"),
            RecordType::CNAME => write!(f, "CNAME"),
            RecordType::MX => write!(f, "MX"),
            RecordType::NS => write!(f, "NS"),
            RecordType::SOA => write!(f, "SOA"),
            RecordType::TXT => write!(f, "TXT"),
            RecordType::SRV => write!(f, "SRV"),
            RecordType::CAA => write!(f, "CAA"),
            RecordType::PTR => write!(f, "PTR"),
        }
    }
}

impl FromStr for RecordType {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_uppercase().as_str() {
            "A" => Ok(RecordType::A),
            "AAAA" => Ok(RecordType::AAAA),
            "CNAME" => Ok(RecordType::CNAME),
            "MX" => Ok(RecordType::MX),
            "NS" => Ok(RecordType::NS),
            "SOA" => Ok(RecordType::SOA),
            "TXT" => Ok(RecordType::TXT),
            "SRV" => Ok(RecordType::SRV),
            "CAA" => Ok(RecordType::CAA),
            "PTR" => Ok(RecordType::PTR),
            _ => Err(format!("unknown record type: {}", s)),
        }
    }
}

/// A single DNS record with CRDT metadata for decentralized merge.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DnsRecord {
    pub name: String,
    pub rtype: RecordType,
    pub value: String,
    pub ttl: u32,
    /// Lamport timestamp for last-write-wins ordering.
    pub timestamp: u64,
    /// Node ID that last wrote this record (tiebreaker).
    pub node_id: String,
    /// Soft-delete flag.
    #[serde(default)]
    pub deleted: bool,
}

impl DnsRecord {
    pub fn new(name: String, rtype: RecordType, value: String, ttl: u32, node_id: String) -> Self {
        Self {
            name,
            rtype,
            value,
            ttl,
            timestamp: 0, // set by store
            node_id,
            deleted: false,
        }
    }

    /// Storage key: "name:RTYPE" (e.g. "www:A")
    pub fn key(&self) -> String {
        format!("{}:{}", self.name.to_lowercase(), self.rtype)
    }

    /// Returns true if self is newer than other (LWW-Register semantics).
    pub fn is_newer_than(&self, other: &DnsRecord) -> bool {
        if self.timestamp != other.timestamp {
            self.timestamp > other.timestamp
        } else {
            self.node_id > other.node_id
        }
    }

    /// Validate the record value for the given type.
    pub fn validate(&self) -> Result<(), String> {
        match self.rtype {
            RecordType::A => {
                self.value
                    .parse::<Ipv4Addr>()
                    .map_err(|e| format!("invalid A record: {}", e))?;
            }
            RecordType::AAAA => {
                self.value
                    .parse::<Ipv6Addr>()
                    .map_err(|e| format!("invalid AAAA record: {}", e))?;
            }
            RecordType::MX => {
                let parts: Vec<&str> = self.value.splitn(2, ' ').collect();
                if parts.len() != 2 {
                    return Err("MX value must be 'priority hostname'".into());
                }
                parts[0]
                    .parse::<u16>()
                    .map_err(|_| "MX priority must be a number")?;
            }
            RecordType::SRV => {
                let parts: Vec<&str> = self.value.splitn(4, ' ').collect();
                if parts.len() != 4 {
                    return Err("SRV value must be 'priority weight port target'".into());
                }
            }
            RecordType::SOA => {
                let parts: Vec<&str> = self.value.split_whitespace().collect();
                if parts.len() != 7 {
                    return Err(
                        "SOA value must be 'mname rname serial refresh retry expire minimum'"
                            .into(),
                    );
                }
            }
            RecordType::CAA => {
                let parts: Vec<&str> = self.value.splitn(3, ' ').collect();
                if parts.len() != 3 {
                    return Err("CAA value must be 'flags tag value'".into());
                }
            }
            // CNAME, NS, TXT, PTR: any string is valid
            _ => {}
        }
        Ok(())
    }
}

/// Request body for creating/updating records via API.
#[derive(Debug, Deserialize)]
pub struct RecordRequest {
    pub name: String,
    #[serde(deserialize_with = "deserialize_record_type")]
    pub rtype: RecordType,
    pub value: String,
    #[serde(default = "default_ttl")]
    pub ttl: u32,
}

fn default_ttl() -> u32 {
    300
}

fn deserialize_record_type<'de, D>(deserializer: D) -> Result<RecordType, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let s = String::deserialize(deserializer)?;
    RecordType::from_str(&s).map_err(serde::de::Error::custom)
}

/// Gossip message envelope for P2P record sync.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GossipMessage {
    pub zone: String,
    pub record: DnsRecord,
}
