//! System monitor — host resources + per-app usage, the "can I start another
//! app safely" view behind the HUB's SYSTEM panel.
//!
//!   GET /system — public, read-only (like /reaper):
//!     host:    memory / swap / cpu% / load / disk / uptime
//!     apps:    every module with live processes, RSS + CPU% summed per module
//!     other:   non-module host processes grouped by name (top hogs + a rest bucket)
//!
//! Attribution walks /proc directly instead of trusting `pm2 jlist` monit
//! numbers: pm2 only measures the direct child, so a prod `next start` app
//! whose real memory sits in a forked next-server worker would read near-zero.
//! A process belongs to a module when its cwd, exe, or a cmdline arg lands
//! inside {anchor}/orbit/<name> or {anchor}/core/<name> — the same matching
//! rule pm2_list uses for python `--app-dir` launches. pm2 is still consulted,
//! but only to put its friendly process names on the pids it manages.

use axum::response::IntoResponse;
use axum::Json;
use serde_json::{json, Value};
use std::collections::HashMap;

const PAGE_SIZE_KB: u64 = 4; // Linux default; fine for a monitoring readout.

/// One process at sample time: enough to attribute, weigh, and label it.
struct Snap {
    comm: String,
    state: char, // R/S/D/Z… — htop's S column
    ticks: u64,  // utime + stime
}

fn read_to_string(path: String) -> Option<String> {
    std::fs::read_to_string(path).ok()
}

/// Total and idle jiffies from the aggregate `cpu` line of /proc/stat.
fn cpu_totals() -> (u64, u64) {
    let raw = std::fs::read_to_string("/proc/stat").unwrap_or_default();
    let Some(line) = raw.lines().find(|l| l.starts_with("cpu ")) else {
        return (0, 0);
    };
    let nums: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .filter_map(|v| v.parse().ok())
        .collect();
    let total: u64 = nums.iter().sum();
    // idle + iowait
    let idle = nums.get(3).copied().unwrap_or(0) + nums.get(4).copied().unwrap_or(0);
    (total, idle)
}

/// comm + utime/stime for one pid, from /proc/<pid>/stat. The comm field is
/// parenthesized and may contain spaces, so split on the LAST ')'.
fn pid_snap(pid: i64) -> Option<Snap> {
    let raw = read_to_string(format!("/proc/{}/stat", pid))?;
    let open = raw.find('(')?;
    let close = raw.rfind(')')?;
    let comm = raw[open + 1..close].to_string();
    let rest: Vec<&str> = raw[close + 1..].split_whitespace().collect();
    // rest[0] is field 3 (state); utime/stime are fields 14/15 → rest[11]/rest[12].
    let state = rest.first().and_then(|s| s.chars().next()).unwrap_or('?');
    let utime: u64 = rest.get(11)?.parse().ok()?;
    let stime: u64 = rest.get(12)?.parse().ok()?;
    Some(Snap { comm, state, ticks: utime + stime })
}

fn pid_rss_kb(pid: i64) -> u64 {
    read_to_string(format!("/proc/{}/statm", pid))
        .and_then(|s| s.split_whitespace().nth(1).map(|v| v.to_string()))
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0)
        * PAGE_SIZE_KB
}

fn all_pids() -> Vec<i64> {
    std::fs::read_dir("/proc")
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter_map(|e| e.file_name().to_string_lossy().parse::<i64>().ok())
                .collect()
        })
        .unwrap_or_default()
}

/// `(module, category)` for a path landing inside {anchor}/orbit/<m> or
/// {anchor}/core/<m>, e.g. /root/mod/mod/orbit/claude/src/api → (claude, orbit).
pub(crate) fn module_of(path: &str, anchor: &str) -> Option<(String, String)> {
    for tree in ["orbit", "core"] {
        let prefix = format!("{}/{}/", anchor, tree);
        if let Some(rest) = path.strip_prefix(&prefix) {
            let name = rest.split('/').next().unwrap_or("");
            if !name.is_empty() {
                return Some((name.to_string(), tree.to_string()));
            }
        }
    }
    None
}

/// Attribute a pid to a module by cwd, then exe, then any cmdline token —
/// mirrors pm2_list's rule so python `--app-dir <module>` launches land right.
fn attribute(pid: i64, anchor: &str) -> Option<(String, String)> {
    for link in ["cwd", "exe"] {
        if let Ok(p) = std::fs::read_link(format!("/proc/{}/{}", pid, link)) {
            if let Some(hit) = module_of(&p.to_string_lossy(), anchor) {
                return Some(hit);
            }
        }
    }
    let cmdline = std::fs::read(format!("/proc/{}/cmdline", pid)).unwrap_or_default();
    for tok in cmdline.split(|&c| c == 0) {
        if let Some(hit) = module_of(&String::from_utf8_lossy(tok), anchor) {
            return Some(hit);
        }
    }
    None
}

/// pid → pm2 process name, for friendly labels on managed processes.
fn pm2_names() -> HashMap<i64, String> {
    let mut map = HashMap::new();
    let Some(pm2) = crate::process::find_pm2() else { return map };
    let Ok(out) = std::process::Command::new(pm2).arg("jlist").output() else {
        return map;
    };
    let Ok(list) = serde_json::from_slice::<Vec<Value>>(&out.stdout) else {
        return map;
    };
    for p in &list {
        if let (Some(pid), Some(name)) = (
            p.get("pid").and_then(|v| v.as_i64()),
            p.get("name").and_then(|v| v.as_str()),
        ) {
            if pid > 0 {
                map.insert(pid, name.to_string());
            }
        }
    }
    map
}

fn disk_root_kb() -> (u64, u64) {
    let Ok(out) = std::process::Command::new("df").args(["-k", "/"]).output() else {
        return (0, 0);
    };
    let text = String::from_utf8_lossy(&out.stdout);
    let Some(line) = text.lines().nth(1) else { return (0, 0) };
    let cols: Vec<&str> = line.split_whitespace().collect();
    let total = cols.get(1).and_then(|v| v.parse().ok()).unwrap_or(0);
    let avail = cols.get(3).and_then(|v| v.parse().ok()).unwrap_or(0);
    (total, avail)
}

/// GET /system — two /proc samples ~400ms apart so CPU% is a real rate, not a
/// lifetime average. Everything else is read once on the second pass.
pub async fn status_handler() -> impl IntoResponse {
    Json(snapshot().await)
}

/// The /system payload as a plain Value, so other views (the PROVIDERS panel's
/// /providers merge) can reuse the same attribution pass.
pub async fn snapshot() -> Value {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let anchor = format!("{}/mod/mod", home);

    // ── sample 1 ──
    let (total0, idle0) = cpu_totals();
    let mut first: HashMap<i64, u64> = HashMap::new();
    for pid in all_pids() {
        if let Some(s) = pid_snap(pid) {
            first.insert(pid, s.ticks);
        }
    }

    tokio::time::sleep(std::time::Duration::from_millis(400)).await;

    // ── sample 2 + everything else ──
    let (total1, idle1) = cpu_totals();
    let d_total = total1.saturating_sub(total0).max(1) as f64;
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    // top-style: 100% == one full core busy.
    let pct_of = |d_ticks: u64| (d_ticks as f64 / d_total) * cores as f64 * 100.0;

    let labels = pm2_names();
    let mut ports_of: HashMap<i64, Vec<u16>> = HashMap::new();
    for (pid, port) in crate::process::all_listen_pid_ports() {
        ports_of.entry(pid).or_default().push(port);
    }

    // module → (category, procs)
    let mut modules: HashMap<String, (String, Vec<Value>)> = HashMap::new();
    // non-module: label → (rss_kb, cpu, count)
    let mut other: HashMap<String, (u64, f64, u64)> = HashMap::new();

    for pid in all_pids() {
        let Some(snap) = pid_snap(pid) else { continue };
        let rss_kb = pid_rss_kb(pid);
        if rss_kb == 0 {
            continue; // kernel threads
        }
        let cpu = first
            .get(&pid)
            .map(|t0| pct_of(snap.ticks.saturating_sub(*t0)))
            .unwrap_or(0.0);
        match attribute(pid, &anchor) {
            Some((name, category)) => {
                let label = labels.get(&pid).cloned().unwrap_or(snap.comm);
                let entry = modules.entry(name).or_insert_with(|| (category, Vec::new()));
                entry.1.push(json!({
                    "pid": pid,
                    "label": label,
                    "mem_mb": rss_kb / 1024,
                    "cpu_pct": (cpu * 10.0).round() / 10.0,
                    "ports": ports_of.get(&pid).cloned().unwrap_or_default(),
                }));
            }
            None => {
                let label = labels.get(&pid).cloned().unwrap_or(snap.comm);
                let e = other.entry(label).or_insert((0, 0.0, 0));
                e.0 += rss_kb;
                e.1 += cpu;
                e.2 += 1;
            }
        }
    }

    let mut apps: Vec<Value> = modules
        .into_iter()
        .map(|(name, (category, mut procs))| {
            procs.sort_by_key(|p| std::cmp::Reverse(p["mem_mb"].as_u64().unwrap_or(0)));
            let mem_mb: u64 = procs.iter().filter_map(|p| p["mem_mb"].as_u64()).sum();
            let cpu_pct: f64 = procs.iter().filter_map(|p| p["cpu_pct"].as_f64()).sum();
            json!({
                "module": name,
                "category": category,
                "mem_mb": mem_mb,
                "cpu_pct": (cpu_pct * 10.0).round() / 10.0,
                "procs": procs,
            })
        })
        .collect();
    apps.sort_by_key(|a| std::cmp::Reverse(a["mem_mb"].as_u64().unwrap_or(0)));

    // Host processes outside the module trees: name the top hogs, fold the
    // long tail into one "other" row so the list stays scannable but the
    // total still accounts for the whole box (no silently missing memory).
    let mut others: Vec<(String, u64, f64, u64)> =
        other.into_iter().map(|(l, (m, c, n))| (l, m, c, n)).collect();
    others.sort_by_key(|o| std::cmp::Reverse(o.1));
    const TOP: usize = 12;
    let tail_mem: u64 = others.iter().skip(TOP).map(|o| o.1).sum();
    let tail_count: u64 = others.iter().skip(TOP).map(|o| o.3).sum();
    let mut other_rows: Vec<Value> = others
        .into_iter()
        .take(TOP)
        .map(|(label, rss_kb, cpu, count)| {
            json!({
                "label": label,
                "mem_mb": rss_kb / 1024,
                "cpu_pct": (cpu * 10.0).round() / 10.0,
                "count": count,
            })
        })
        .collect();
    if tail_count > 0 {
        other_rows.push(json!({
            "label": "other",
            "mem_mb": tail_mem / 1024,
            "cpu_pct": 0.0,
            "count": tail_count,
        }));
    }

    let meminfo = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let mem_kb = |key: &str| {
        meminfo
            .lines()
            .find(|l| l.starts_with(key))
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(0)
    };
    let total_mb = mem_kb("MemTotal:") / 1024;
    let available_mb = mem_kb("MemAvailable:") / 1024;
    let swap_total_mb = mem_kb("SwapTotal:") / 1024;
    let swap_free_mb = mem_kb("SwapFree:") / 1024;

    let loadavg = std::fs::read_to_string("/proc/loadavg").unwrap_or_default();
    let loads: Vec<f64> = loadavg
        .split_whitespace()
        .take(3)
        .filter_map(|v| v.parse().ok())
        .collect();
    let uptime_secs = std::fs::read_to_string("/proc/uptime")
        .ok()
        .and_then(|s| s.split_whitespace().next().map(|v| v.to_string()))
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(0.0) as u64;
    let (disk_total_kb, disk_avail_kb) = disk_root_kb();

    let host_cpu_pct = {
        let d_idle = idle1.saturating_sub(idle0) as f64;
        (((d_total - d_idle) / d_total) * 1000.0).round() / 10.0
    };

    json!({
        "host": {
            "mem": {
                "total_mb": total_mb,
                "available_mb": available_mb,
                "used_mb": total_mb.saturating_sub(available_mb),
                "swap_total_mb": swap_total_mb,
                "swap_used_mb": swap_total_mb.saturating_sub(swap_free_mb),
            },
            "cpu": {
                "cores": cores,
                "pct": host_cpu_pct,
                "load1": loads.first().copied().unwrap_or(0.0),
                "load5": loads.get(1).copied().unwrap_or(0.0),
                "load15": loads.get(2).copied().unwrap_or(0.0),
            },
            "disk": {
                "total_mb": disk_total_kb / 1024,
                "available_mb": disk_avail_kb / 1024,
            },
            "uptime_secs": uptime_secs,
        },
        "apps": apps,
        "other": other_rows,
    })
}

// ── /system/htop — the admin's raw hardware readout ─────────────────────────

/// (total, idle) jiffies for each cpuN line — feeds the per-core meters.
fn per_core_totals() -> Vec<(u64, u64)> {
    let raw = std::fs::read_to_string("/proc/stat").unwrap_or_default();
    raw.lines()
        .filter(|l| l.starts_with("cpu") && l.as_bytes().get(3).is_some_and(|c| c.is_ascii_digit()))
        .map(|line| {
            let nums: Vec<u64> = line
                .split_whitespace()
                .skip(1)
                .filter_map(|v| v.parse().ok())
                .collect();
            let total: u64 = nums.iter().sum();
            let idle = nums.get(3).copied().unwrap_or(0) + nums.get(4).copied().unwrap_or(0);
            (total, idle)
        })
        .collect()
}

/// Real uid of a pid, for htop's USER column.
fn pid_uid(pid: i64) -> Option<u32> {
    read_to_string(format!("/proc/{}/status", pid))?
        .lines()
        .find(|l| l.starts_with("Uid:"))
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|v| v.parse().ok())
}

/// uid → login name from /etc/passwd; unknown uids fall back to the number.
fn usernames() -> HashMap<u32, String> {
    let mut map = HashMap::new();
    for line in std::fs::read_to_string("/etc/passwd").unwrap_or_default().lines() {
        let mut cols = line.split(':');
        let (Some(name), Some(_), Some(uid)) = (cols.next(), cols.next(), cols.next()) else {
            continue;
        };
        if let Ok(uid) = uid.parse::<u32>() {
            map.insert(uid, name.to_string());
        }
    }
    map
}

/// Full command line (NUL-joined argv), falling back to htop's [comm] form.
/// Capped so one giant argv can't bloat the payload.
fn pid_command(pid: i64, comm: &str) -> String {
    let raw = std::fs::read(format!("/proc/{}/cmdline", pid)).unwrap_or_default();
    let joined = raw
        .split(|&c| c == 0)
        .filter(|t| !t.is_empty())
        .map(|t| String::from_utf8_lossy(t).into_owned())
        .collect::<Vec<_>>()
        .join(" ");
    let cmd = if joined.is_empty() { format!("[{}]", comm) } else { joined };
    cmd.chars().take(240).collect()
}

/// GET /system/htop — per-core CPU meters + a full process table (pid, user,
/// state, cpu%, rss, command), htop-style. Owner-only, unlike the aggregated
/// public /system view: raw cmdlines can carry secrets passed as CLI args.
/// Same two-sample technique as /system so CPU% is a rate, not a lifetime avg.
pub async fn htop_handler(headers: axum::http::HeaderMap) -> axum::response::Response {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }

    // ── sample 1 ──
    let cores0 = per_core_totals();
    let (total0, _) = cpu_totals();
    let mut first: HashMap<i64, u64> = HashMap::new();
    for pid in all_pids() {
        if let Some(s) = pid_snap(pid) {
            first.insert(pid, s.ticks);
        }
    }

    tokio::time::sleep(std::time::Duration::from_millis(500)).await;

    // ── sample 2 ──
    let cores1 = per_core_totals();
    let (total1, _) = cpu_totals();
    let d_total = total1.saturating_sub(total0).max(1) as f64;
    let ncores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    // top-style: 100% == one full core busy.
    let pct_of = |d_ticks: u64| (d_ticks as f64 / d_total) * ncores as f64 * 100.0;

    let per_core: Vec<f64> = cores0
        .iter()
        .zip(cores1.iter())
        .map(|((t0, i0), (t1, i1))| {
            let dt = t1.saturating_sub(*t0).max(1) as f64;
            let di = i1.saturating_sub(*i0) as f64;
            (((dt - di) / dt) * 1000.0).round() / 10.0
        })
        .collect();
    let host_cpu_pct = {
        let busy: f64 = per_core.iter().sum();
        ((busy / per_core.len().max(1) as f64) * 10.0).round() / 10.0
    };

    let users = usernames();
    let mut running = 0u64;
    let mut tasks_total = 0u64;
    let mut procs: Vec<Value> = Vec::new();
    for pid in all_pids() {
        let Some(snap) = pid_snap(pid) else { continue };
        tasks_total += 1;
        if snap.state == 'R' {
            running += 1;
        }
        let rss_kb = pid_rss_kb(pid);
        if rss_kb == 0 {
            continue; // kernel threads
        }
        let cpu = first
            .get(&pid)
            .map(|t0| pct_of(snap.ticks.saturating_sub(*t0)))
            .unwrap_or(0.0);
        let user = pid_uid(pid)
            .map(|u| users.get(&u).cloned().unwrap_or_else(|| u.to_string()))
            .unwrap_or_else(|| "?".to_string());
        procs.push(json!({
            "pid": pid,
            "user": user,
            "state": snap.state.to_string(),
            "cpu_pct": (cpu * 10.0).round() / 10.0,
            "mem_mb": rss_kb / 1024,
            "command": pid_command(pid, &snap.comm),
        }));
    }
    let procs_total = procs.len();
    procs.sort_by(|a, b| {
        b["cpu_pct"]
            .as_f64()
            .partial_cmp(&a["cpu_pct"].as_f64())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b["mem_mb"].as_u64().cmp(&a["mem_mb"].as_u64()))
    });
    procs.truncate(60);

    let meminfo = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let mem_kb = |key: &str| {
        meminfo
            .lines()
            .find(|l| l.starts_with(key))
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(0)
    };
    let total_mb = mem_kb("MemTotal:") / 1024;
    let available_mb = mem_kb("MemAvailable:") / 1024;
    let swap_total_mb = mem_kb("SwapTotal:") / 1024;

    let loads: Vec<f64> = std::fs::read_to_string("/proc/loadavg")
        .unwrap_or_default()
        .split_whitespace()
        .take(3)
        .filter_map(|v| v.parse().ok())
        .collect();
    let uptime_secs = std::fs::read_to_string("/proc/uptime")
        .ok()
        .and_then(|s| s.split_whitespace().next().and_then(|v| v.parse::<f64>().ok()))
        .unwrap_or(0.0) as u64;
    let (disk_total_kb, disk_avail_kb) = disk_root_kb();

    Json(json!({
        "cpu": {
            "cores": ncores,
            "pct": host_cpu_pct,
            "per_core": per_core,
            "load1": loads.first().copied().unwrap_or(0.0),
            "load5": loads.get(1).copied().unwrap_or(0.0),
            "load15": loads.get(2).copied().unwrap_or(0.0),
        },
        "mem": {
            "total_mb": total_mb,
            "used_mb": total_mb.saturating_sub(available_mb),
            "available_mb": available_mb,
            "cached_mb": (mem_kb("Cached:") + mem_kb("Buffers:")) / 1024,
            "swap_total_mb": swap_total_mb,
            "swap_used_mb": swap_total_mb.saturating_sub(mem_kb("SwapFree:") / 1024),
        },
        "disk": {
            "total_mb": disk_total_kb / 1024,
            "available_mb": disk_avail_kb / 1024,
        },
        "uptime_secs": uptime_secs,
        "tasks": { "total": tasks_total, "running": running },
        "procs_total": procs_total,
        "procs": procs,
    }))
    .into_response()
}
