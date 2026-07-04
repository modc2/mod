//! System CPU / RAM sampling via /proc — cheap enough to poll on a timer
//! instead of shelling out to `top`/`ps`. The efficiency dashboard reads the
//! latest snapshot plus a short rolling history.

use parking_lot::Mutex;
use serde::Serialize;
use std::collections::VecDeque;
use std::sync::Arc;

#[derive(Clone, Serialize, Default)]
pub struct Snapshot {
    /// Whole-system CPU utilisation 0..100 (across all cores).
    pub cpu_percent: f64,
    pub cpu_cores: usize,
    pub mem_total_mb: u64,
    pub mem_used_mb: u64,
    pub mem_percent: f64,
    /// Unix seconds.
    pub at: i64,
}

#[derive(Clone)]
pub struct Metrics {
    history: Arc<Mutex<VecDeque<Snapshot>>>,
    prev_cpu: Arc<Mutex<Option<(u64, u64)>>>, // (idle, total)
    cap: usize,
}

impl Metrics {
    pub fn new() -> Self {
        Self {
            history: Arc::new(Mutex::new(VecDeque::new())),
            prev_cpu: Arc::new(Mutex::new(None)),
            cap: 120, // ~ last N samples
        }
    }

    pub fn latest(&self) -> Snapshot {
        self.history.lock().back().cloned().unwrap_or_default()
    }

    pub fn history(&self) -> Vec<Snapshot> {
        self.history.lock().iter().cloned().collect()
    }

    /// Sample once; called on a timer from main.
    pub fn sample(&self) {
        let cpu_percent = self.read_cpu();
        let (mem_total_mb, mem_used_mb) = read_mem();
        let mem_percent = if mem_total_mb > 0 {
            mem_used_mb as f64 / mem_total_mb as f64 * 100.0
        } else {
            0.0
        };
        let snap = Snapshot {
            cpu_percent,
            cpu_cores: num_cores(),
            mem_total_mb,
            mem_used_mb,
            mem_percent: round1(mem_percent),
            at: chrono::Utc::now().timestamp(),
        };
        let mut h = self.history.lock();
        h.push_back(snap);
        while h.len() > self.cap {
            h.pop_front();
        }
    }

    /// Whole-system CPU % from the delta of /proc/stat between samples.
    fn read_cpu(&self) -> f64 {
        let stat = match std::fs::read_to_string("/proc/stat") {
            Ok(s) => s,
            Err(_) => return 0.0,
        };
        let line = stat.lines().next().unwrap_or("");
        let vals: Vec<u64> = line
            .split_whitespace()
            .skip(1)
            .filter_map(|v| v.parse().ok())
            .collect();
        if vals.len() < 5 {
            return 0.0;
        }
        let idle = vals[3] + *vals.get(4).unwrap_or(&0); // idle + iowait
        let total: u64 = vals.iter().sum();
        let mut prev = self.prev_cpu.lock();
        let pct = match *prev {
            Some((pidle, ptotal)) if total > ptotal => {
                let d_idle = idle.saturating_sub(pidle) as f64;
                let d_total = (total - ptotal) as f64;
                round1((1.0 - d_idle / d_total) * 100.0)
            }
            _ => 0.0,
        };
        *prev = Some((idle, total));
        pct
    }
}

/// RSS of an arbitrary pid in MB (0 if gone) — used to report a worker's
/// footprint alongside system memory.
pub fn pid_rss_mb(pid: u32) -> u64 {
    let path = format!("/proc/{pid}/statm");
    let Ok(s) = std::fs::read_to_string(&path) else {
        return 0;
    };
    // field 2 = resident set size in pages
    let rss_pages: u64 = s
        .split_whitespace()
        .nth(1)
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    rss_pages * 4096 / 1_000_000 // 4KiB pages → MB
}

fn read_mem() -> (u64, u64) {
    let Ok(s) = std::fs::read_to_string("/proc/meminfo") else {
        return (0, 0);
    };
    let mut total = 0u64;
    let mut avail = 0u64;
    for line in s.lines() {
        if let Some(v) = line.strip_prefix("MemTotal:") {
            total = parse_kb(v);
        } else if let Some(v) = line.strip_prefix("MemAvailable:") {
            avail = parse_kb(v);
        }
    }
    let used = total.saturating_sub(avail);
    (total / 1000, used / 1000) // KB → MB
}

fn parse_kb(s: &str) -> u64 {
    s.split_whitespace()
        .next()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0)
}

fn num_cores() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}
