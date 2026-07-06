//! One-shot SSH command runner. We shell out to OpenSSH because (a) it's
//! universally available on the host, (b) it side-steps Rust SSH client crate
//! churn, and (c) it gives the user identical behaviour to what they'd see
//! in a terminal.
//!
//! Password auth uses `sshpass` when present (logged + reported when missing
//! so the user knows what to install). Key auth writes the key text to a
//! permissions-restricted temp file and `ssh -i $TMP`.

use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::Stdio;

use anyhow::{bail, Result};
use rand::RngCore;
use serde::Serialize;
use tokio::process::Command;

use crate::store::Connection;

#[derive(Debug, Serialize)]
pub struct SshOutcome {
    pub ok: bool,
    pub exit: i32,
    pub stdout: String,
    pub stderr: String,
}

pub async fn run(conn: &Connection, secret: &str, command: &str) -> Result<SshOutcome> {
    let user_at_host = format!("{}@{}", conn.user, conn.host);
    let port_str = conn.port.to_string();

    let mut opts: Vec<&str> = vec![
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "BatchMode=no",
        "-o",
        "ConnectTimeout=10",
        "-p",
        &port_str,
    ];

    let (mut cmd, _tmp_key) = match conn.auth_type.as_str() {
        "password" => {
            if which("sshpass").is_none() {
                return Ok(SshOutcome {
                    ok: false,
                    exit: -1,
                    stdout: String::new(),
                    stderr:
                        "sshpass not installed on host — required for password SSH auth (brew install hudochenkov/sshpass/sshpass)"
                            .into(),
                });
            }
            let mut c = Command::new("sshpass");
            // -e reads the password from env var SSHPASS — never appears
            // in argv, never lands on disk, never on the process listing.
            c.arg("-e");
            c.arg("ssh");
            for o in &opts {
                c.arg(o);
            }
            c.arg(&user_at_host);
            c.arg(command);
            c.env("SSHPASS", secret);
            (c, None)
        }
        "key" => {
            let tmp = write_temp_key(secret)?;
            let tmp_path_string = tmp.to_string_lossy().into_owned();
            opts.push("-i");
            // We need to extend lifetime — pull the &str through a separate
            // owned String already declared above.
            let mut c = Command::new("ssh");
            for o in &opts {
                c.arg(o);
            }
            c.arg(tmp_path_string.as_str());
            c.arg(&user_at_host);
            c.arg(command);
            (c, Some(TempKey(tmp)))
        }
        other => bail!("unknown auth_type: {other}"),
    };

    cmd.stdin(Stdio::null());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let output = cmd.output().await?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let exit = output.status.code().unwrap_or(-1);
    Ok(SshOutcome {
        ok: output.status.success(),
        exit,
        stdout,
        stderr,
    })
}

fn write_temp_key(secret: &str) -> Result<PathBuf> {
    let mut name = [0u8; 8];
    rand::thread_rng().fill_bytes(&mut name);
    let path = std::env::temp_dir().join(format!("sshville-{}.key", hex::encode(name)));
    let mut f = std::fs::File::create(&path)?;
    let mut perms = f.metadata()?.permissions();
    perms.set_mode(0o600);
    std::fs::set_permissions(&path, perms)?;
    let mut s = secret.to_string();
    if !s.ends_with('\n') {
        s.push('\n');
    }
    f.write_all(s.as_bytes())?;
    f.flush()?;
    Ok(path)
}

fn which(prog: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|p| p.join(prog))
            .find(|p| p.is_file())
    })
}

/// RAII tempfile holder — removes the key file on drop even if the request
/// errors mid-flight. Stays around for the duration of the SSH call.
struct TempKey(PathBuf);
impl Drop for TempKey {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
