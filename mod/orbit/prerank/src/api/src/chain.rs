//! The append-only log everything else is derived from.
//!
//! The market keeps no authoritative state of its own. It keeps a list of
//! events, each one hashed together with the hash before it, and the state
//! anyone reads is the fold of that list. Two things follow, and they are the
//! reason the design is shaped this way:
//!
//!   * Changing history changes the head. Editing event 40 of 900 changes 41
//!     through 900 too, so a head hash published yesterday is a commitment to
//!     every event that preceded it.
//!   * Anyone can recompute the state. `verify` replays the log from genesis
//!     and rebuilds the balances, the pools and the payouts from scratch — if
//!     the running server's state disagrees with the replay, the server is
//!     wrong, and it says so rather than papering over it.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::crypto::sha256_hex;
use crate::types::Event;

pub const GENESIS_PREV: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub seq: u64,
    pub prev: String,
    pub hash: String,
    pub event: Event,
}

impl Entry {
    /// The link hash: sequence, predecessor, and the event's canonical JSON.
    ///
    /// Canonical here means "what serde emits for these types" — struct
    /// fields in declaration order, maps sorted because they are all
    /// `BTreeMap`. That is enough for two builds of this binary to agree,
    /// which is what the property needs.
    pub fn compute_hash(seq: u64, prev: &str, event: &Event) -> String {
        let body = serde_json::to_string(event).expect("events are serialisable");
        sha256_hex(format!("{seq}|{prev}|{body}").as_bytes())
    }

    pub fn rehash(&self) -> String {
        Self::compute_hash(self.seq, &self.prev, &self.event)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainCheck {
    pub ok: bool,
    pub length: u64,
    pub head: String,
    /// Index of the first entry whose hash or link is wrong, if any.
    pub broken_at: Option<u64>,
    pub error: Option<String>,
}

/// The log, in memory, optionally mirrored to a file.
#[derive(Debug, Default)]
pub struct Chain {
    entries: Vec<Entry>,
    path: Option<PathBuf>,
}

impl Chain {
    pub fn in_memory() -> Self {
        Self { entries: Vec::new(), path: None }
    }

    /// Open (or create) a log on disk and read it back.
    pub fn open(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let mut chain = Self { entries: Vec::new(), path: Some(path.clone()) };
        if path.exists() {
            let file = File::open(&path)?;
            for line in BufReader::new(file).lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                // A half-written trailing line is the normal shape of a
                // process killed mid-append: stop there rather than refuse
                // to boot, and let `verify` report the truncation.
                match serde_json::from_str::<Entry>(&line) {
                    Ok(entry) => chain.entries.push(entry),
                    Err(e) => {
                        tracing::warn!(error = %e, "chain: stopping at unreadable line");
                        break;
                    }
                }
            }
        }
        Ok(chain)
    }

    pub fn len(&self) -> u64 {
        self.entries.len() as u64
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn head(&self) -> String {
        self.entries
            .last()
            .map(|e| e.hash.clone())
            .unwrap_or_else(|| GENESIS_PREV.to_string())
    }

    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }

    pub fn events(&self) -> impl Iterator<Item = &Event> {
        self.entries.iter().map(|e| &e.event)
    }

    /// Append an event, link it, and flush it to disk before returning.
    ///
    /// The flush is not optional: an event the caller was told happened but
    /// that is not on disk is exactly the inconsistency the log exists to
    /// rule out.
    pub fn append(&mut self, event: Event) -> anyhow::Result<Entry> {
        let seq = self.entries.len() as u64;
        let prev = self.head();
        let hash = Entry::compute_hash(seq, &prev, &event);
        let entry = Entry { seq, prev, hash, event };
        if let Some(path) = &self.path {
            let mut file = OpenOptions::new().create(true).append(true).open(path)?;
            writeln!(file, "{}", serde_json::to_string(&entry)?)?;
            file.flush()?;
        }
        self.entries.push(entry.clone());
        Ok(entry)
    }

    /// Recompute every hash and every link.
    pub fn check(&self) -> ChainCheck {
        let mut prev = GENESIS_PREV.to_string();
        for (i, entry) in self.entries.iter().enumerate() {
            if entry.seq != i as u64 {
                return ChainCheck {
                    ok: false,
                    length: self.len(),
                    head: self.head(),
                    broken_at: Some(i as u64),
                    error: Some(format!("entry {i} claims seq {}", entry.seq)),
                };
            }
            if entry.prev != prev {
                return ChainCheck {
                    ok: false,
                    length: self.len(),
                    head: self.head(),
                    broken_at: Some(i as u64),
                    error: Some(format!("entry {i} does not link to its predecessor")),
                };
            }
            if entry.rehash() != entry.hash {
                return ChainCheck {
                    ok: false,
                    length: self.len(),
                    head: self.head(),
                    broken_at: Some(i as u64),
                    error: Some(format!("entry {i} has been altered since it was written")),
                };
            }
            prev = entry.hash.clone();
        }
        ChainCheck {
            ok: true,
            length: self.len(),
            head: self.head(),
            broken_at: None,
            error: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Event;

    fn ev(n: u64) -> Event {
        Event::Credited {
            account: format!("0x{:040x}", n),
            amount: n as u128,
            memo: "t".into(),
            at: n as i64,
        }
    }

    #[test]
    fn a_clean_chain_checks_out_and_a_tampered_one_does_not() {
        let mut chain = Chain::in_memory();
        for n in 0..5 {
            chain.append(ev(n)).unwrap();
        }
        assert!(chain.check().ok);
        let head_before = chain.head();

        // Rewrite the amount in the middle of the log, leaving the hashes as
        // they were — the shape a tampered database would have.
        if let Event::Credited { amount, .. } = &mut chain.entries[2].event {
            *amount = 999_999;
        }
        let check = chain.check();
        assert!(!check.ok);
        assert_eq!(check.broken_at, Some(2));
        assert_eq!(chain.head(), head_before, "the stale head is what gives it away");
    }
}
