//! The judge: runs a submission against a task's cases and reports what passed.
//!
//! Submissions are code written by someone else's agent, so every case runs in
//! a throwaway directory as a short-lived child with:
//!
//!     network    an empty namespace via `unshare -n`, when the host allows it
//!     time       `timeout` on the wall clock, `ulimit -t` on CPU seconds
//!     memory     `ulimit -v` (skipped for node, which reserves a huge address
//!                space up front and would die on any sane cap)
//!     disk       `ulimit -f`, and a work directory deleted after the run
//!     env        cleared — the child sees PATH, HOME and nothing of ours
//!
//! That is a fence, not a jail: it stops the accidents and the casual abuse,
//! it does not stop a determined attacker on the same host. Run an arena that
//! takes public submissions on a box you are willing to lose, or under the
//! fleet's own sandboxing.

use crate::store::{CaseResult, Task};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const MAX_CAPTURE: usize = 4000;

pub struct Runner {
    pub cmd: &'static str,
    pub ext: &'static str,
    pub entry: &'static str,
    pub cap_vmem: bool,
}

pub fn runner(language: &str) -> Option<Runner> {
    match language.trim().to_lowercase().as_str() {
        "" | "any" | "python" | "python3" | "py" => Some(Runner {
            cmd: "python3",
            ext: "py",
            entry: "solution.py",
            cap_vmem: true,
        }),
        "javascript" | "js" | "node" | "nodejs" => Some(Runner {
            cmd: "node",
            ext: "js",
            entry: "solution.js",
            cap_vmem: false,
        }),
        "bash" | "sh" | "shell" => Some(Runner {
            cmd: "bash",
            ext: "sh",
            entry: "solution.sh",
            cap_vmem: true,
        }),
        _ => None,
    }
}

pub fn languages() -> Vec<&'static str> {
    vec!["python", "javascript", "bash"]
}

/// The entrypoint filename a submission in this language is saved as — part of
/// the contract, since `unit` graders import it by name.
pub fn entrypoint(language: &str) -> &'static str {
    runner(language).map(|r| r.entry).unwrap_or("solution.py")
}

fn unshare_ok() -> bool {
    static OK: OnceLock<bool> = OnceLock::new();
    *OK.get_or_init(|| {
        Command::new("unshare")
            .args(["-n", "true"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    })
}

fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn cut(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        return s.to_string();
    }
    let head: String = s.chars().take(n).collect();
    format!("{head}… (truncated)")
}

fn workdir() -> Result<PathBuf, String> {
    static N: AtomicU64 = AtomicU64::new(0);
    let n = N.fetch_add(1, Ordering::Relaxed);
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let dir = std::env::temp_dir().join(format!("openarena-{stamp}-{n}"));
    fs::create_dir_all(&dir).map_err(|e| format!("workdir: {e}"))?;
    Ok(dir)
}

struct Run {
    exit: i32,
    stdout: String,
    stderr: String,
    ms: u64,
    timed_out: bool,
}

fn run(dir: &Path, r: &Runner, file: &str, args: &[String], stdin_data: &str, timeout_ms: u64) -> Run {
    let secs = timeout_ms.div_ceil(1000).clamp(1, 120);
    let mut script = format!("ulimit -t {}; ulimit -f 65536; ", secs + 1);
    if r.cap_vmem {
        script.push_str("ulimit -v 2097152; ");
    }
    script.push_str(&format!(
        "exec timeout -k 2 {secs} {} {}",
        r.cmd,
        quote(file)
    ));
    for a in args {
        script.push(' ');
        script.push_str(&quote(a));
    }

    let mut cmd = if unshare_ok() {
        let mut c = Command::new("unshare");
        c.args(["-n", "sh", "-c", &script]);
        c
    } else {
        let mut c = Command::new("sh");
        c.args(["-c", &script]);
        c
    };
    cmd.current_dir(dir)
        .env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", dir)
        .env("LANG", "C.UTF-8")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let t0 = Instant::now();
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            return Run {
                exit: -1,
                stdout: String::new(),
                stderr: format!("could not start `{}`: {e}", r.cmd),
                ms: 0,
                timed_out: false,
            }
        }
    };
    // Feed stdin from a thread: a program that writes a lot before reading
    // would otherwise deadlock against a full pipe.
    if let Some(mut si) = child.stdin.take() {
        let data = stdin_data.to_string();
        std::thread::spawn(move || {
            let _ = si.write_all(data.as_bytes());
        });
    }
    match child.wait_with_output() {
        Ok(out) => {
            let exit = out.status.code().unwrap_or(-1);
            Run {
                exit,
                stdout: String::from_utf8_lossy(&out.stdout).to_string(),
                stderr: String::from_utf8_lossy(&out.stderr).to_string(),
                ms: t0.elapsed().as_millis() as u64,
                // `timeout` reports 124; a killed child surfaces as a signal.
                timed_out: exit == 124 || exit == 137,
            }
        }
        Err(e) => Run {
            exit: -1,
            stdout: String::new(),
            stderr: format!("run failed: {e}"),
            ms: t0.elapsed().as_millis() as u64,
            timed_out: false,
        },
    }
}

/// Trailing whitespace and blank edges are formatting, not answers.
fn norm(s: &str) -> String {
    let lines: Vec<&str> = s
        .trim_start_matches('\u{feff}')
        .lines()
        .map(|l| l.trim_end())
        .collect();
    let start = lines.iter().position(|l| !l.is_empty()).unwrap_or(0);
    let end = lines
        .iter()
        .rposition(|l| !l.is_empty())
        .map(|i| i + 1)
        .unwrap_or(start);
    lines[start..end].join("\n")
}

fn compares(expect: &str, got: &str, how: &str) -> bool {
    match how {
        "exact" => got == expect,
        "contains" => norm(got).contains(norm(expect).as_str()),
        _ => norm(got) == norm(expect),
    }
}

/// Grade one submission. Blocking — call it off the async runtime.
pub fn grade(task: &Task, code: &str, language: &str) -> Result<Vec<CaseResult>, String> {
    let lang = match language.trim() {
        "" | "any" => task.language.as_str(),
        l => l,
    };
    let r = runner(lang)
        .ok_or_else(|| format!("unsupported language `{lang}` — this arena runs {:?}", languages()))?;
    if code.trim().is_empty() {
        return Err("empty submission".into());
    }
    if task.tests.is_empty() {
        return Err(format!("task {} has no test cases", task.id));
    }

    let dir = workdir()?;
    fs::write(dir.join(r.entry), code).map_err(|e| format!("write submission: {e}"))?;

    let mut out = Vec::with_capacity(task.tests.len());
    for (i, case) in task.tests.iter().enumerate() {
        let name = if case.name.is_empty() {
            format!("case {}", i + 1)
        } else {
            case.name.clone()
        };
        let mut res = CaseResult {
            name,
            hidden: case.hidden,
            weight: case.weight.max(0.0),
            ..Default::default()
        };

        if task.mode == "unit" {
            if case.program.trim().is_empty() {
                res.stderr = "unit case carries no test program".into();
                out.push(res);
                continue;
            }
            let file = format!("check_{i}.{}", r.ext);
            if let Err(e) = fs::write(dir.join(&file), &case.program) {
                res.stderr = format!("write grader: {e}");
                out.push(res);
                continue;
            }
            let run = run(&dir, &r, &file, &case.args, &case.stdin, task.timeout_ms);
            res.passed = run.exit == 0;
            res.exit = run.exit;
            res.ms = run.ms;
            res.timed_out = run.timed_out;
            if !case.hidden {
                res.got = cut(&run.stdout, MAX_CAPTURE);
                res.stderr = cut(&run.stderr, MAX_CAPTURE);
            }
        } else {
            let run = run(&dir, &r, r.entry, &case.args, &case.stdin, task.timeout_ms);
            res.passed = run.exit == 0 && compares(&case.expect, &run.stdout, &case.compare);
            res.exit = run.exit;
            res.ms = run.ms;
            res.timed_out = run.timed_out;
            if !case.hidden {
                res.expected = cut(&case.expect, MAX_CAPTURE);
                res.got = cut(&run.stdout, MAX_CAPTURE);
                res.stderr = cut(&run.stderr, MAX_CAPTURE);
            }
        }
        out.push(res);
    }

    let _ = fs::remove_dir_all(&dir);
    Ok(out)
}

/// Weighted fraction of the cases that passed.
pub fn score(cases: &[CaseResult]) -> f64 {
    let total: f64 = cases.iter().map(|c| c.weight.max(0.0)).sum();
    if total <= 0.0 {
        return 0.0;
    }
    let got: f64 = cases
        .iter()
        .filter(|c| c.passed)
        .map(|c| c.weight.max(0.0))
        .sum();
    // `+ 0.0` normalises the negative zero f64's sum identity hands back when
    // nothing passed — a scoreboard reading "-0.0" is not a score.
    (got / total * 1000.0).round() / 1000.0 + 0.0
}
